import asyncio
import logging
import shutil
import time
from typing import TYPE_CHECKING, Any, Optional, Union

from fastapi.responses import FileResponse

from docling.datamodel.base_models import DocumentStream

from docling_serve.datamodel.engines import TaskStatus
from docling_serve.datamodel.requests import FileSource, HttpSource
from docling_serve.docling_conversion import convert_documents
from docling_serve.response_preparation import process_results
from docling_serve.storage import get_scratch

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


                    # Add OCRMyPDF preprocessing here - BEFORE convert_documents
                    enable_ocrmypdf = getattr(task.options, 'enable_ocrmypdf_preprocessing', False)
                    if enable_ocrmypdf and ocrmypdf_middleware and ocrmypdf_middleware.enabled:
                        _log.info(f"Applying OCRMyPDF preprocessing for task {task_id}")
                        
                        # Process DocumentStream sources for OCRMyPDF
                        processed_sources = []
                        for source in convert_sources:
                            if isinstance(source, DocumentStream):
                                try:
                                    # Get OCRMyPDF options from task options
                                    ocrmypdf_deskew = getattr(task.options, 'ocrmypdf_deskew', True)
                                    ocrmypdf_clean = getattr(task.options, 'ocrmypdf_clean', True)
                                    ocr_languages = getattr(task.options, 'ocr_lang', None)
                                    
                                    # Apply preprocessing
                                    processed_stream = ocrmypdf_middleware.preprocess_file(
                                        source.stream,
                                        source.name,
                                        deskew=ocrmypdf_deskew,
                                        clean=ocrmypdf_clean,
                                        ocr_languages=ocr_languages
                                    )
                                    
                                    # Reset stream position and create new DocumentStream
                                    processed_stream.seek(0)
                                    processed_sources.append(
                                        DocumentStream(name=source.name, stream=processed_stream)
                                    )
                                except Exception as e:
                                    _log.error(f"OCRMyPDF preprocessing failed for {source.name}: {e}")
                                    # Use original source if preprocessing fails
                                    processed_sources.append(source)
                            else:
                                # Non-DocumentStream sources (URLs) - keep as is
                                processed_sources.append(source)
                        
                        convert_sources = processed_sources

                    # Note: results are only an iterator->lazy evaluation
                    results = convert_documents(
                        sources=convert_sources,
                        options=task.options,
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

                    if work_dir.exists():
                        task.scratch_dir = work_dir
                        if not isinstance(response, FileResponse):
                            _log.warning(
                                f"Task {task_id=} produced content in {work_dir=} but the response is not a file."
                            )
                            shutil.rmtree(work_dir, ignore_errors=True)

                    return response

                start_time = time.monotonic()

                # Run the prediction in a thread to avoid blocking the event loop.
                # Get the current event loop
                # loop = asyncio.get_event_loop()
                # future = asyncio.run_coroutine_threadsafe(
                #     run_conversion(),
                #     loop=loop
                # )
                # response = future.result()

                # Run in a thread
                response = await asyncio.to_thread(
                    run_conversion,
                )
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
