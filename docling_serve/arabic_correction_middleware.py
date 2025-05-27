import logging
import os
from datetime import datetime
from typing import Dict, Any
from pathlib import Path
from langdetect import detect
from ollama import Client as OllamaClient

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class ArabicCorrectionMiddleware:
    def __init__(self, enabled: bool = True, ollama_host: str = "http://localhost:11434", model_name: str = "command-r7b-arabic"):
        self.enabled = enabled
        self.ollama_client = OllamaClient(host=ollama_host) if enabled else None
        self.model_name = model_name
        self.system_prompt = (
            "You are an OCR error correction specialist. Given text extracted from "
            "scanned Arabic documents, your job is to correct only the garbled, "
            "misspelled, or non-detectable Arabic words caused by OCR errors, "
            "without making any changes to the document content, order, or meaning. "
            "Do not add, rephrase, or remove text. Output only the corrected text."
        )
        
        # Setup dedicated loggers for before/after text
        self._setup_correction_loggers()
        
        # Main logger
        self.logger = logging.getLogger(__name__)
        
        # Log initialization
        self.logger.info(f"ArabicCorrectionMiddleware initialized - Enabled: {enabled}, Host: {ollama_host}, Model: {model_name}")

    def _setup_correction_loggers(self):
        """Setup separate loggers for before and after text correction."""
        # Create logs directory if it doesn't exist
        log_dir = Path("logs/arabic_correction")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup before text logger
        self.before_logger = logging.getLogger("arabic_correction.before")
        self.before_logger.setLevel(logging.INFO)
        self.before_logger.handlers.clear()  # Clear any existing handlers
        
        before_handler = logging.FileHandler(
            log_dir / f"before_correction_{datetime.now().strftime('%Y%m%d')}.log",
            encoding='utf-8'
        )
        before_formatter = logging.Formatter(
            '%(asctime)s | LENGTH:%(message_length)d | %(message)s'
        )
        before_handler.setFormatter(before_formatter)
        self.before_logger.addHandler(before_handler)
        self.before_logger.propagate = False
        
        # Setup after text logger
        self.after_logger = logging.getLogger("arabic_correction.after")
        self.after_logger.setLevel(logging.INFO)
        self.after_logger.handlers.clear()  # Clear any existing handlers
        
        after_handler = logging.FileHandler(
            log_dir / f"after_correction_{datetime.now().strftime('%Y%m%d')}.log",
            encoding='utf-8'
        )
        after_formatter = logging.Formatter(
            '%(asctime)s | LENGTH:%(message_length)d | MODIFIED:%(was_modified)s | %(message)s'
        )
        after_handler.setFormatter(after_formatter)
        self.after_logger.addHandler(after_handler)
        self.after_logger.propagate = False

    def should_correct_text(self, text: str) -> bool:
        """Determine if text should be corrected (Arabic detection)."""
        self.logger.debug(f"Checking if text should be corrected - Length: {len(text) if text else 0}")
        
        if not self.enabled:
            self.logger.debug("Arabic correction is disabled")
            return False
            
        if not text:
            self.logger.debug("Text is empty")
            return False
            
        if len(text.strip()) < 10:
            self.logger.debug(f"Text too short for correction: {len(text.strip())} characters")
            return False
            
        try:
            clean_text = " ".join(text.split()[:100])
            self.logger.debug(f"Detecting language for text sample: {clean_text[:50]}...")
            
            detected_lang = detect(clean_text)
            is_arabic = detected_lang == "ar"
            
            self.logger.info(f"Language detection result: {detected_lang} (is_arabic: {is_arabic})")
            return is_arabic
            
        except Exception as e:
            self.logger.error(f"Language detection error: {e}")
            return False

    def correct_arabic_text(self, text: str) -> str:
        """Correct Arabic OCR errors using Ollama LLM."""
        if not self.enabled or not self.ollama_client:
            self.logger.warning("Arabic correction is disabled or client not available")
            return text

        text_length = len(text)
        text_preview = text[:100] + "..." if len(text) > 100 else text
        
        self.logger.info(f"Starting Arabic text correction - Length: {text_length} chars")
        self.logger.debug(f"Text preview: {text_preview}")
        
        # Log before correction
        self.before_logger.info(text, extra={'message_length': text_length})
        
        try:
            self.logger.debug(f"Sending request to Ollama model: {self.model_name}")
            
            response = self.ollama_client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": text}
                ]
            )
            
            corrected_text = response['message']['content'].strip()
            corrected_length = len(corrected_text)
            was_modified = text != corrected_text
            
            self.logger.info(f"Arabic correction completed - Original: {text_length} chars, "
                           f"Corrected: {corrected_length} chars, Modified: {was_modified}")
            
            if was_modified:
                self.logger.debug(f"Text was modified during correction")
                # Calculate rough change percentage
                change_ratio = abs(corrected_length - text_length) / text_length * 100
                self.logger.info(f"Text length change: {change_ratio:.1f}%")
            else:
                self.logger.debug("Text was not modified during correction")
            
            # Log after correction
            self.after_logger.info(corrected_text, extra={
                'message_length': corrected_length,
                'was_modified': was_modified
            })
            
            return corrected_text
            
        except Exception as e:
            self.logger.error(f"Arabic correction failed: {e}", exc_info=True)
            
            # Log failed correction attempt
            self.after_logger.error(f"CORRECTION_FAILED: {str(e)}", extra={
                'message_length': text_length,
                'was_modified': False
            })
            
            return text

    def process_conversion_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Process and enhance conversion result with Arabic correction."""
        if not self.enabled:
            self.logger.debug("Arabic correction disabled, returning original result")
            return result
        
        self.logger.info("Starting Arabic correction processing of conversion result")
        
        enhanced_result = result.copy()
        documents_processed = 0
        corrections_applied = 0
        
        # Process document(s) in the result
        if "document" in result:
            self.logger.debug("Processing single document")
            enhanced_result["document"], doc_corrections = self._process_document(result["document"])
            documents_processed = 1
            corrections_applied = doc_corrections
            
        elif "documents" in result and isinstance(result["documents"], list):
            doc_count = len(result["documents"])
            self.logger.debug(f"Processing {doc_count} documents")
            
            processed_docs = []
            for i, doc in enumerate(result["documents"]):
                self.logger.debug(f"Processing document {i+1}/{doc_count}")
                processed_doc, doc_corrections = self._process_document(doc)
                processed_docs.append(processed_doc)
                corrections_applied += doc_corrections
                documents_processed += 1
            
            enhanced_result["documents"] = processed_docs
        
        self.logger.info(f"Arabic correction processing completed - Documents: {documents_processed}, "
                        f"Corrections applied: {corrections_applied}")
        
        return enhanced_result

    def _process_document(self, document: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
        """Process individual document for Arabic correction."""
        self.logger.debug("Processing individual document for Arabic correction")
        
        corrected_doc = document.copy()
        corrections_count = 0
        
        # Correct text_content if it's Arabic
        if "text_content" in document and document["text_content"]:
            text_content = document["text_content"]
            self.logger.debug(f"Checking text_content for Arabic correction - Length: {len(text_content)}")
            
            if self.should_correct_text(text_content):
                self.logger.info("Applying Arabic OCR correction to text_content")
                original_content = text_content
                corrected_content = self.correct_arabic_text(text_content)
                corrected_doc["text_content"] = corrected_content
                
                if original_content != corrected_content:
                    corrections_count += 1
                    self.logger.info("text_content was successfully corrected")
                else:
                    self.logger.debug("text_content was not modified after correction")
            else:
                self.logger.debug("text_content does not require Arabic correction")
        
        # Correct md_content if it's Arabic
        if "md_content" in document and document["md_content"]:
            md_content = document["md_content"]
            self.logger.debug(f"Checking md_content for Arabic correction - Length: {len(md_content)}")
            
            if self.should_correct_text(md_content):
                self.logger.info("Applying Arabic OCR correction to md_content")
                original_content = md_content
                corrected_content = self.correct_arabic_text(md_content)
                corrected_doc["md_content"] = corrected_content
                
                if original_content != corrected_content:
                    corrections_count += 1
                    self.logger.info("md_content was successfully corrected")
                else:
                    self.logger.debug("md_content was not modified after correction")
            else:
                self.logger.debug("md_content does not require Arabic correction")
        
        self.logger.debug(f"Document processing completed - Corrections applied: {corrections_count}")
        return corrected_doc, corrections_count