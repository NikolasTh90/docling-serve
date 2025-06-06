from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List

class OCRMyPDFSettings(BaseSettings):
    """Settings for OCRMyPDF preprocessing middleware."""
    
    model_config = SettingsConfigDict(
        env_prefix="DOCLING_OCRMYPDF_",
        env_file=".env",
        extra="allow"
    )
    
    enabled: bool = False
    deskew: bool = True
    clean: bool = True
    optimize: int = 1
    color_conversion_strategy: Optional[str] = None
    oversample: int = 300
    remove_background: bool = False # Changed to False because it is not yet implemented
    force_ocr: bool = True
    skip_text: bool = False
    redo_ocr: bool = False  # Changed to False to avoid conflicts with preprocessing options
    progress_bar: bool = False
    timeout: int = 600  # 5 minutes timeout for OCRMyPDF processing
    
    # Language settings
    default_languages: Optional[List[str]] = None
    language_detection: bool = True
    
    # File processing settings
    max_file_size_mb: int = 200
    supported_extensions: List[str] = [".pdf"]
    
    # Error handling
    fail_on_error: bool = False
    fallback_on_failure: bool = True
    
    # Performance settings
    parallel_processing: bool = True
    use_threads: Optional[bool] = None
    max_workers: Optional[int] = None
    
    # Advanced OCRMyPDF options
    tesseract_timeout: Optional[float] = None
    clean_final: bool = False  # This can also conflict with redo_ocr

    pdf_renderer: Optional[str] = None