from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class AIVisionSettings(BaseSettings):
    """Settings for AI Vision OCR feature using Ollama vision models."""
    
    model_config = SettingsConfigDict(
        env_prefix="DOCLING_AI_VISION_",
        env_file=".env",
        extra="allow"
    )
    
    enabled: bool = False
    ollama_host: str = "http://localhost:11434"
    model_name: str = "qwen2.5-vl:32b"
    timeout: int = 300  # 5 minutes default timeout
    max_retries: int = 3
    supported_extensions: List[str] = [".pdf"]
    
    # Image processing settings for vision model
    image_quality: int = 95
    max_image_size: int = 2048  # Max dimension in pixels
    pages_per_batch: int = 5   # Process pages in batches
    
    # Vision model specific settings
    temperature: float = 0.1   # Low temperature for consistent OCR-like output
    max_tokens: int = 100000    # Max tokens per response
    
    # Output formatting
    preserve_formatting: bool = True
    include_page_breaks: bool = True
    page_break_marker: str = "\n\n---\n\n"