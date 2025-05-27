from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class ArabicCorrectionSettings(BaseSettings):
    """Settings for Arabic OCR correction feature."""
    
    model_config = SettingsConfigDict(
        env_prefix="DOCLING_ARABIC_",
        env_file=".env",
        extra="allow"
    )
    
    enabled: bool = True
    ollama_host: str = "http://localhost:11434"
    model_name: str = "command-r7b-arabic"
    timeout: int = 30
    enable_remote_services: bool = False