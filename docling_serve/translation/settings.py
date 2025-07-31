from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Set, Optional
import sys
import time
import logging

logger = logging.getLogger("docling.translate")

class TranslationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCLING_TRANSLATE_",
        env_file=".env",
        extra="allow"
    ) 
    
    enabled: bool = False
    libretranslate_host: str = "https://libretranslate.com"
    api_key: Optional[str] = None
    timeout: int = 60
    min_confidence: float = 0.9
    max_text_length: int = 30000  # Prevent API timeouts and excessive costs