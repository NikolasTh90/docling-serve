import tempfile
import logging
from pathlib import Path
from io import BytesIO
from typing import List, Optional
import ocrmypdf
from docling.datamodel.base_models import DocumentStream

from .ocr_language_utils import convert_to_tesseract_codes, format_for_ocrmypdf


class OCRMyPDFMiddleware:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.logger = logging.getLogger(__name__)
        
    def should_preprocess_file(self, filename: str) -> bool:
        """Check if file should be preprocessed with OCRMyPDF."""
        if not self.enabled:
            return False
            
        # Only process PDF files
        return filename.lower().endswith('.pdf')
        
    def preprocess_file(
        self, 
        file_stream: BytesIO, 
        filename: str,
        deskew: bool = True,
        clean: bool = True,
        ocr_languages: Optional[List[str]] = None,
    ) -> BytesIO:
        """Preprocess PDF file with OCRMyPDF for improved OCR accuracy."""
        if not self.should_preprocess_file(filename):
            return file_stream
            
        try:
            self.logger.info(f"Preprocessing {filename} with OCRMyPDF")
            
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
                # Configure OCRMyPDF for accuracy-oriented processing
                ocrmypdf_args = {
                    'input_file': input_path,
                    'output_file': output_path,
                    'deskew': deskew,
                    'clean': clean,
                    'optimize': 1,  # Light optimization
                    'color_conversion_strategy': 'RGB',
                    'oversample': 300,  # High quality oversampling
                    'remove_background': True,
                    'threshold': True,
                    'force_ocr': True,
                    'skip_text': False,  # Don't skip existing text
                    'redo_ocr': True,
                    'progress_bar': False,
                    'quiet': True
                }
                
                # Add language specification if provided
                if tesseract_languages:
                    language_string = format_for_ocrmypdf(tesseract_languages)
                    ocrmypdf_args['language'] = language_string
                    self.logger.info(f"Using OCRMyPDF with languages: {language_string}")
                
                # Run OCRMyPDF
                ocrmypdf.ocr(**ocrmypdf_args)
                
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
            # Return original file if preprocessing fails
            return file_stream
            
    def preprocess_document_streams(
        self, 
        file_sources: List[DocumentStream],
        enable_preprocessing: bool = False,
        deskew: bool = True,
        clean: bool = True,
        ocr_languages: Optional[List[str]] = None,
    ) -> List[DocumentStream]:
        """Preprocess multiple DocumentStream objects."""
        if not enable_preprocessing or not self.enabled:
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
                # Use original source if preprocessing fails
                processed_sources.append(source)
                
        return processed_sources