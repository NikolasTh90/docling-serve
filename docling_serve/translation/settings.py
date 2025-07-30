from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Set, Optional

class TranslationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCLING_TRANSLATE_",
        env_file=".env",
        extra="allow"
    )
    
    enabled: bool = False
    libretranslate_host: str = "https://libretranslate.com"
    api_key: Optional[str] = None
    timeout: int = 30
    target_languages: Set[str] = {"eng", "ara", "spa", "fra", "deu"}
    min_confidence: float = 0.7