import asyncio
import logging
import shutil
import time
from typing import TYPE_CHECKING, Any, Optional, Union
from pathlib import Path

from fastapi.responses import FileResponse

from docling.datamodel.base_models import DocumentStream

from docling_serve.datamodel.engines import TaskStatus
from docling_serve.datamodel.requests import FileSource, HttpSource
from docling_serve.docling_conversion import convert_documents
from docling_serve.response_preparation import process_results
from docling_serve.storage import get_scratch
from docling_serve.pdf_analysis import analyze_pdf_for_force_ocr, should_analyze_file_for_force_ocr, analyze_pdf
from docling_serve.post_processing_bidi import MarkdownProcessor, BiDiProcessor

if TYPE_CHECKING:
    from docling_serve.engines.async_local.orchestrator import AsyncLocalOrchestrator

from docling_serve.arabic_correction_middleware import ArabicCorrectionMiddleware
from docling_serve.arabic_settings import ArabicCorrectionSettings

# Initialize Arabic correction settings and middleware
arabic_settings = ArabicCorrectionSettings()
arabic_middleware = ArabicCorrectionMiddleware(
    enabled=arabic_settings.enabled,
    ollama_host=arabic_settings.ollama_host,
    model_name=arabic_settings.model_name
)

# Initialize BiDi processor
bidi_processor = BiDiProcessor(enabled=True)

# Initialize AI Vision middleware
try:
    from docling_serve.settings import ai_vision_settings
    from docling_serve.ai_vision_middleware import AIVisionMiddleware
    ai_vision_middleware = AIVisionMiddleware(settings=ai_vision_settings) if ai_vision_settings else None
except ImportError as e:
    _log.warning(f"AI Vision middleware not available: {e}")
    ai_vision_middleware = None

try:
    from docling_serve.settings import ocrmypdf_settings
    from docling_serve.ocrmypdf_middleware import OCRMyPDFMiddleware
    ocrmypdf_middleware = OCRMyPDFMiddleware(settings=ocrmypdf_settings)
except ImportError as e:
    _log.warning(f"OCRMyPDF middleware not available: {e}")
    ocrmypdf_middleware = None

_log = logging.getLogger(__name__)


class AsyncLocalWorker:
    def __init__(self, worker_id: int, orchestrator: "AsyncLocalOrchestrator"):
        self.worker_id = worker_id
        self.orchestrator = orchestrator
        
    async def loop(self):
        _log.debug(f"Starting loop for worker {self.worker_id}")
        while True:
            task_id: str = await self.orchestrator.task_queue.get()
            self.orchestrator.queue_list.remove(task_id)
            
            if task_id not in self.orchestrator.tasks:
                raise RuntimeError(f"Task {task_id} not found.")
                
            task = self.orchestrator.tasks[task_id]
            
            try:
                task.set_status(TaskStatus.STARTED)
                _log.info(f"Worker {self.worker_id} processing task {task_id}")
                
                # Notify clients about task updates
                await self.orchestrator.notify_task_subscribers(task_id)
                # Notify clients about queue updates
                await self.orchestrator.notify_queue_positions()
                
                # Define a callback function to send progress updates to the client.
                # TODO: send partial updates, e.g. when a document in the batch is done
                def run_conversion():
                    convert_sources: list[Union[str, DocumentStream]] = []
                    headers: Optional[dict[str, Any]] = None
                    
                    for source in task.sources:
                        if isinstance(source, DocumentStream):
                            convert_sources.append(source)
                        elif isinstance(source, FileSource):
                            convert_sources.append(source.to_document_stream())
                        elif isinstance(source, HttpSource):
                            convert_sources.append(str(source.url))
                            if headers is None and source.headers:
                                headers = source.headers
                    
                    # GLOBAL PDF ANALYSIS - Run before any other processing
                    pdf_analysis_performed = False
                    recommended_ocr_mode = None  # Store the recommended mode for OCRMyPDF
                    ai_vision_triggered = False
                    
                    for source in convert_sources:
                        if isinstance(source, DocumentStream) and should_analyze_file_for_force_ocr(source.name):
                            try:
                                _log.info(f"Running global PDF analysis for {source.name}")
                                # Create temporary file for full analysis
                                import tempfile
                                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_file:
                                    temp_file.write(source.stream.getvalue())
                                    temp_file.flush()
                                    temp_path = Path(temp_file.name)
                                
                                try:
                                    # Get full PDF analysis results
                                    analysis_results = analyze_pdf(temp_path)
                                    recommended_ocr_mode = analysis_results['recommended_mode']
                                    
                                    # Check if AI Vision should be triggered
                                    enable_ai_vision = getattr(task.options, 'enable_ai_vision', False)
                                    if (enable_ai_vision and
                                        ai_vision_middleware and
                                        ai_vision_middleware.enabled and
                                        recommended_ocr_mode == 'force' and
                                        ai_vision_middleware.is_supported_file(source.name)):
                                        
                                        _log.info(f"AI Vision workflow triggered for {source.name} due to force OCR recommendation")
                                        ai_vision_triggered = True
                                        
                                        # Process with AI Vision
                                        try:
                                            source.stream.seek(0)  # Reset stream position
                                            markdown_content = ai_vision_middleware.process_document(
                                                source.stream, source.name
                                            )
                                            # Create a simple response structure for AI Vision
                                            from docling_serve.response_preparation import prepare_ai_vision_response
                                            response = prepare_ai_vision_response(
                                                markdown_content=markdown_content,
                                                filename=source.name,
                                                conversion_options=task.options
                                            )
                                            _log.info(f"AI Vision processing completed for {source.name}")
                                            return response
                                        except Exception as e:
                                            _log.error(f"AI Vision processing failed for {source.name}: {e}")
                                            # Fall back to normal processing
                                            ai_vision_triggered = False
                                    
                                    # Update force_ocr based on analysis (only if not using AI Vision)
                                    if not ai_vision_triggered:
                                        should_force_ocr = True if recommended_ocr_mode == 'force' else False
                                        if should_force_ocr and not task.options.force_ocr:
                                            updated_options = task.options.model_copy(update={'force_ocr': True})
                                            task.options = updated_options
                                            _log.info(f"PDF analysis enabled force_ocr for better OCR accuracy on {source.name}")
                                    
                                    _log.info(f"PDF analysis recommends OCR mode: {recommended_ocr_mode} for {source.name}")
                                    pdf_analysis_performed = True
                                    break
                                    
                                finally:
                                    temp_path.unlink(missing_ok=True)
                                    
                            except Exception as e:
                                _log.warning(f"Failed to analyze {source.name} for force_ocr: {e}")
                    
                    if pdf_analysis_performed:
                        _log.info(f"Global PDF analysis completed for task {task_id}")
                    
                    # Add OCRMyPDF preprocessing - AFTER PDF analysis but BEFORE convert_documents
                    enable_ocrmypdf = getattr(task.options, 'enable_ocrmypdf_preprocessing', False)
                    if enable_ocrmypdf and ocrmypdf_middleware and ocrmypdf_middleware.enabled:
                        _log.info(f"Applying OCRMyPDF preprocessing for task {task_id}")
                        processed_sources = []
                        ocrmypdf_processing_performed = False
                        
                        for source in convert_sources:
                            if isinstance(source, DocumentStream):
                                try:
                                    # Get OCRMyPDF options from task options
                                    ocrmypdf_deskew = getattr(task.options, 'ocrmypdf_deskew', True)
                                    ocrmypdf_clean = getattr(task.options, 'ocrmypdf_clean', True)
                                    ocr_languages = getattr(task.options, 'ocr_lang', None)
                                    
                                    # Use the recommended mode from PDF analysis
                                    ocr_mode_to_use = recommended_ocr_mode if recommended_ocr_mode != 'skip' else 'force'
                                    
                                    # Apply preprocessing with the recommended mode
                                    processed_stream = ocrmypdf_middleware.preprocess_file(
                                        source.stream,
                                        source.name,
                                        deskew=ocrmypdf_deskew,
                                        clean=ocrmypdf_clean,
                                        ocr_languages=ocr_languages,
                                        ocr_mode=ocr_mode_to_use
                                    )
                                    
                                    # Reset stream position and create new DocumentStream
                                    processed_stream.seek(0)
                                    processed_sources.append(
                                        DocumentStream(name=source.name, stream=processed_stream)
                                    )
                                    ocrmypdf_processing_performed = True
                                    _log.info(f"OCRMyPDF preprocessing completed successfully for {source.name}")
                                    
                                except Exception as e:
                                    _log.error(f"OCRMyPDF preprocessing failed for {source.name}: {e}")
                                    processed_sources.append(source)
                            else:
                                processed_sources.append(source)
                        
                        convert_sources = processed_sources
                        
                        # IMPORTANT: Set force_ocr to False after successful OCRMyPDF preprocessing
                        # Since OCR was already performed by OCRMyPDF, we don't want docling to redo it
                        if ocrmypdf_processing_performed:
                            updated_options = task.options.model_copy(update={'force_ocr': False})
                            task.options = updated_options
                            _log.info(f"Set force_ocr=False after OCRMyPDF preprocessing to avoid redundant OCR")
                    
                    # Note: results are only an iterator->lazy evaluation
                    results = convert_documents(
                        sources=convert_sources,
                        options=task.options,  # Now has force_ocr=False if OCRMyPDF was used
                        headers=headers,
                    )
                    
                    # The real processing will happen here
                    work_dir = get_scratch() / task_id
                    response = process_results(
                        conversion_options=task.options,
                        conv_results=results,
                        work_dir=work_dir,
                    )
                    _log.info(f"Task {task_id} completed with response: {response}")
                    
                    # Apply Arabic correction if enabled and requested
                    enable_arabic_correction = getattr(task.options, 'enable_arabic_correction', False)
                    if enable_arabic_correction and arabic_middleware.enabled:
                        try:
                            _log.info(f"Applying Arabic OCR correction during async processing for task {task_id}")
                            response = arabic_middleware.process_conversion_result(response)
                        except Exception as e:
                            _log.error(f"Arabic correction failed for task {task_id}: {e}", exc_info=True)
                            # Continue without correction rather than failing the entire task
                    
                    # Apply BiDi processing if enabled and requested
                    enable_bidi_processing = getattr(task.options, 'enable_bidi_processing', False)
                    if enable_bidi_processing:
                        try:
                            _log.info(f"Applying BiDi text processing for task {task_id}")
                            response = bidi_processor.process_conversion_result(response)
                            _log.info(f"BiDi processing completed for task {task_id}")
                        except Exception as e:
                            _log.error(f"BiDi processing failed for task {task_id}: {e}", exc_info=True)
                            # Continue without BiDi processing rather than failing the entire task

                    if work_dir.exists():
                        task.scratch_dir = work_dir
                        if not isinstance(response, FileResponse):
                            _log.warning(
                                f"Task {task_id=} produced content in {work_dir=} but the response is not a file."
                            )
                            shutil.rmtree(work_dir, ignore_errors=True)
                    
                    return response
                
                start_time = time.monotonic()
                # Run in a thread
                response = await asyncio.to_thread(run_conversion)
                processing_time = time.monotonic() - start_time
                
                task.result = response
                task.sources = []
                task.options = None
                task.set_status(TaskStatus.SUCCESS)
                _log.info(
                    f"Worker {self.worker_id} completed job {task_id} "
                    f"in {processing_time:.2f} seconds"
                )
                
            except Exception as e:
                _log.error(
                    f"Worker {self.worker_id} failed to process job {task_id}: {e}"
                )
                task.set_status(TaskStatus.FAILURE)
                
            finally:
                await self.orchestrator.notify_task_subscribers(task_id)
                self.orchestrator.task_queue.task_done()
                _log.debug(f"Worker {self.worker_id} completely done with {task_id}")