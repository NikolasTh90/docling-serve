# This file already exists but needs to be completed
import logging
from typing import Dict, Any, Optional
from langdetect import detect
from ollama import Client as OllamaClient

logger = logging.getLogger(__name__)

class ArabicCorrectionMiddleware:
    def __init__(self, enabled: bool = True, ollama_host: str = "http://localhost:11434", model_name: str = "command-r7b-arabic"):
        self.enabled = enabled
        self.ollama_client = OllamaClient(host=ollama_host) if enabled else None
        # CHANGE: Use parameter instead of hardcoded value
        self.model_name = model_name
        self.system_prompt = (
            "You are an OCR error correction specialist. Given text extracted from "
            "scanned Arabic documents, your job is to correct only the garbled, "
            "misspelled, or non-detectable Arabic words caused by OCR errors, "
            "without making any changes to the document content, order, or meaning. "
            "Do not add, rephrase, or remove text. Output only the corrected text."
        )
    
    def should_correct_text(self, text: str) -> bool:
        """Determine if text should be corrected (Arabic detection)."""
        if not self.enabled or not text or len(text.strip()) < 10:
            return False
            
        try:
            clean_text = " ".join(text.split()[:100])
            detected_lang = detect(clean_text)
            return detected_lang == "ar"
        except Exception as e:
            logger.error(f"Language detection error: {e}")
            return False
    
    def correct_arabic_text(self, text: str) -> str:
        """Correct Arabic OCR errors using Ollama LLM."""
        if not self.enabled or not self.ollama_client:
            return text
            
        try:
            response = self.ollama_client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": text}
                ]
            )
            return response['message']['content'].strip()
        except Exception as e:
            logger.error(f"Arabic correction failed: {e}")
            return text
    
    def process_conversion_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Process and enhance conversion result with Arabic correction."""
        if not self.enabled:
            return result
        
        enhanced_result = result.copy()
        
        # Process document(s) in the result
        if "document" in result:
            enhanced_result["document"] = self._process_document(result["document"])
        elif "documents" in result and isinstance(result["documents"], list):
            enhanced_result["documents"] = [
                self._process_document(doc) for doc in result["documents"]
            ]
        
        return enhanced_result
    
    def _process_document(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Process individual document for Arabic correction."""
        corrected_doc = document.copy()
        
        # Correct text_content if it's Arabic
        if "text_content" in document and document["text_content"]:
            text_content = document["text_content"]
            if self.should_correct_text(text_content):
                logger.info("Applying Arabic OCR correction to text_content")
                corrected_doc["text_content"] = self.correct_arabic_text(text_content)
        
        # Correct md_content if it's Arabic
        if "md_content" in document and document["md_content"]:
            md_content = document["md_content"]
            if self.should_correct_text(md_content):
                logger.info("Applying Arabic OCR correction to md_content")
                corrected_doc["md_content"] = self.correct_arabic_text(md_content)
        
        return corrected_doc