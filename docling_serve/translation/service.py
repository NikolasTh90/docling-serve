import requests
import logging
from typing import Dict, Optional, Tuple
from .settings import TranslationSettings

logger = logging.getLogger(__name__)

class TranslationService:
    def __init__(self, settings: TranslationSettings):
        self.settings = settings
        self.base_url = settings.libretranslate_host.rstrip('/')
        
    def detect_language(self, text: str) -> Optional[Tuple[str, float]]:
        """Detect language of markdown text."""
        if not self.settings.enabled:
            return None
            
        try:
            # Use first 1000 chars for detection
            sample_text = text[:1000]
            
            payload = {"q": sample_text}
            if self.settings.api_key:
                payload["api_key"] = self.settings.api_key
                
            response = requests.post(
                f"{self.base_url}/detect",
                json=payload,
                timeout=self.settings.timeout
            )
            response.raise_for_status()
            
            result = response.json()
            if result and len(result) > 0:
                language = result[0].get("language")
                confidence = result[0].get("confidence", 0.0)
                
                if confidence >= self.settings.min_confidence:
                    logger.info(f"Detected language: {language} (confidence: {confidence:.2f})")
                    return language, confidence
                    
        except Exception as e:
            logger.error(f"Language detection failed: {e}")
            
        return None
    
    def translate_markdown(self, md_content: str, source_lang: str, target_lang: str) -> Optional[str]:
        """Translate markdown content while preserving format."""
        if not self.settings.enabled or source_lang == target_lang:
            return None
            
        try:
            payload = {
                "q": md_content,
                "source": source_lang,
                "target": target_lang,
                "format": "text"
            }
            if self.settings.api_key:
                payload["api_key"] = self.settings.api_key
                
            response = requests.post(
                f"{self.base_url}/translate",
                json=payload,
                timeout=self.settings.timeout
            )
            response.raise_for_status()
            
            result = response.json()
            return result.get("translatedText")
            
        except Exception as e:
            logger.error(f"Translation failed from {source_lang} to {target_lang}: {e}")
            return None
    
    def translate_to_all_languages(self, md_content: str) -> Dict[str, str]:
        """Detect language and translate to all target languages."""
        translations = {}
        
        # Detect source language
        detection = self.detect_language(md_content)
        if not detection:
            return translations
            
        source_lang, confidence = detection
        translations["detected_language"] = source_lang
        translations["confidence"] = confidence
        
        # Translate to all target languages except source
        target_languages = self.settings.target_languages - {source_lang}
        
        for target_lang in target_languages:
            translated = self.translate_markdown(md_content, source_lang, target_lang)
            if translated:
                translations[target_lang] = translated
                
        return translations