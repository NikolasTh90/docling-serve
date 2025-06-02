import tempfile
import logging
from pathlib import Path
from io import BytesIO
from typing import List, Optional
import ocrmypdf
from docling.datamodel.base_models import DocumentStream

from .ocr_language_utils import convert_to_tesseract_codes, format_for_ocrmypdf
# Import settings with proper fallback
try:
    from .settings import ocrmypdf_settings
except ImportError:
    ocrmypdf_settings = None

class OCRMyPDFMiddleware:
    def __init__(self, settings=None):
        # Use provided settings or try to import from settings module
        if settings is not None:
            self.settings = settings
        else:
            try:
                from .settings import ocrmypdf_settings
                self.settings = ocrmypdf_settings
            except (ImportError, AttributeError):
                self.settings = None
        
        self.enabled = self.settings.enabled if self.settings else False
        self.logger = logging.getLogger(__name__)
        
        # Log configuration on initialization
        if self.settings:
            self.logger.info(f"OCRMyPDF Middleware initialized - Enabled: {self.enabled}")
            if self.enabled:
                self.logger.debug(f"OCRMyPDF Settings - Deskew: {self.settings.deskew}, Clean: {self.settings.clean}")
        else:
            self.logger.warning("OCRMyPDF settings not available - middleware disabled")
        
    def should_preprocess_file(self, filename: str) -> bool:
        """Check if file should be preprocessed with OCRMyPDF."""
        if not self.enabled or not self.settings:
            return False
            
        # Check file extension
        file_ext = Path(filename).suffix.lower()
        return file_ext in self.settings.supported_extensions
        
    def preprocess_file(
        self, 
        file_stream: BytesIO, 
        filename: str,
        deskew: Optional[bool] = None,
        clean: Optional[bool] = None,
        ocr_languages: Optional[List[str]] = None,
    ) -> BytesIO:
        """Preprocess PDF file with OCRMyPDF for improved OCR accuracy."""
        if not self.should_preprocess_file(filename):
            return file_stream
            
        if not self.settings:
            self.logger.warning("OCRMyPDF settings not available, skipping preprocessing")
            return file_stream
            
        # Check file size
        file_size_mb = len(file_stream.getvalue()) / (1024 * 1024)
        if file_size_mb > self.settings.max_file_size_mb:
            self.logger.warning(f"File {filename} ({file_size_mb:.1f}MB) exceeds max size ({self.settings.max_file_size_mb}MB)")
            return file_stream
            
        try:
            self.logger.info(f"Preprocessing {filename} with OCRMyPDF (size: {file_size_mb:.1f}MB)")
            
            # Use settings values or parameter overrides
            use_deskew = deskew if deskew is not None else self.settings.deskew
            use_clean = clean if clean is not None else self.settings.clean
            use_remove_background = self.settings.remove_background
            use_redo_ocr = self.settings.redo_ocr
            
            # Handle OCRMyPDF parameter conflicts
            # redo_ocr is incompatible with deskew, clean-final, and remove_background
            if use_redo_ocr and (use_deskew or use_clean or use_remove_background):
                self.logger.warning("redo_ocr conflicts with deskew/clean/remove_background. Prioritizing preprocessing options.")
                use_redo_ocr = False  # Disable redo_ocr to allow preprocessing
            
            # Convert language codes to Tesseract format
            tesseract_languages = convert_to_tesseract_codes(ocr_languages, self.logger)
            
            # Create temporary files
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as input_temp:
                input_temp.write(file_stream.getvalue())
                input_temp.flush()
                input_path = Path(input_temp.name)
                
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as output_temp:
                output_path = Path(output_temp.name)
                
            try:
                # Configure OCRMyPDF using settings with valid parameters only
                ocrmypdf_args = {
                    'input_file': input_path,
                    'output_file': output_path,
                    'deskew': use_deskew,
                    'clean': use_clean,
                    'optimize': self.settings.optimize,
                    'color_conversion_strategy': self.settings.color_conversion_strategy,
                    'oversample': self.settings.oversample,
                    # 'remove_background': use_remove_background,
                    'force_ocr': self.settings.force_ocr,
                    'skip_text': self.settings.skip_text,
                    'redo_ocr': use_redo_ocr,
                    'progress_bar': self.settings.progress_bar,
                }
                
                # Add language specification if provided
                if tesseract_languages:
                    ocrmypdf_args['language'] = tesseract_languages  # Pass as list
                    self.logger.info(f"Using OCRMyPDF with languages: {tesseract_languages}")
                
                # Log the final configuration for debugging
                self.logger.debug(f"OCRMyPDF config: deskew={use_deskew}, clean={use_clean}, "
                                f"remove_background={use_remove_background}, redo_ocr={use_redo_ocr}, "
                                f"force_ocr={self.settings.force_ocr}")
                
                # Run OCRMyPDF with timeout
                import signal
                def timeout_handler(signum, frame):
                    raise TimeoutError("OCRMyPDF processing timed out")
                
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(self.settings.timeout)
                
                try:
                    result = ocrmypdf.ocr(**ocrmypdf_args)
                    self.logger.debug(f"OCRMyPDF result: {result}")
                finally:
                    signal.alarm(0)  # Cancel the alarm
                
                # Read the processed file
                with open(output_path, 'rb') as f:
                    processed_content = f.read()
                    
                self.logger.info(f"Successfully preprocessed {filename} with OCRMyPDF")
                return BytesIO(processed_content)
                
            finally:
                # Cleanup temporary files
                input_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
                
        except Exception as e:
            self.logger.error(f"OCRMyPDF preprocessing failed for {filename}: {e}")
            
            if self.settings.fail_on_error:
                raise
            elif self.settings.fallback_on_failure:
                self.logger.info(f"Falling back to original file for {filename}")
                return file_stream
            else:
                raise
            
    def preprocess_document_streams(
        self, 
        file_sources: List[DocumentStream],
        enable_preprocessing: bool = False,
        deskew: Optional[bool] = None,
        clean: Optional[bool] = None,
        ocr_languages: Optional[List[str]] = None,
    ) -> List[DocumentStream]:
        """Preprocess multiple DocumentStream objects."""
        if not enable_preprocessing or not self.enabled or not self.settings:
            return file_sources
            
        processed_sources = []
        for source in file_sources:
            try:
                processed_stream = self.preprocess_file(
                    source.stream, 
                    source.name,
                    deskew=deskew,
                    clean=clean,
                    ocr_languages=ocr_languages
                )
                # Reset stream position
                processed_stream.seek(0)
                processed_sources.append(
                    DocumentStream(name=source.name, stream=processed_stream)
                )
            except Exception as e:
                self.logger.error(f"Failed to preprocess {source.name}: {e}")
                
                if self.settings.fail_on_error:
                    raise
                else:
                    # Use original source if preprocessing fails
                    processed_sources.append(source)
                
        return processed_sources