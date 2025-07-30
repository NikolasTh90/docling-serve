from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Set, Optional

class TranslationSettings(BaseSettings):
    """Settings for LibreTranslate integration."""
    
    model_config = SettingsConfigDict(
        env_prefix="DOCLING_TRANSLATE_",
        env_file=".env",
        extra="allow"
    )
    
    enabled: bool = False
    libretranslate_host: str = "http://localhost:5000"
    api_key: Optional[str] = None
    timeout: int = 30
    target_languages: Set[str] = {"eng", "ara", "spa", "fra", "deu"}
    min_confidence: float = 0.7
    chunk_size: int = 500  # Characters per translation request