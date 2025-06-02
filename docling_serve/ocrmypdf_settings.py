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
    color_conversion_strategy: str = "RGB"
    oversample: int = 300
    remove_background: bool = True
    threshold: bool = True
    force_ocr: bool = True
    skip_text: bool = False
    redo_ocr: bool = True
    progress_bar: bool = False
    quiet: bool = True
    timeout: int = 300  # 5 minutes timeout for OCRMyPDF processing
    
    # Language settings
    default_languages: Optional[List[str]] = None
    language_detection: bool = True
    
    # File processing settings
    max_file_size_mb: int = 100
    supported_extensions: List[str] = [".pdf"]
    
    # Error handling
    fail_on_error: bool = False
    fallback_on_failure: bool = True
    
    # Performance settings
    parallel_processing: bool = True
    max_workers: Optional[int] = None