import asyncio
import concurrent.futures
import logging
import time
from typing import Dict, Optional, Tuple, Any, List
from urllib import request
from libretranslatepy import LibreTranslateAPI
from .settings import TranslationSettings

logger = logging.getLogger(__name__)

# Monkey patch urllib.request to add User-Agent header for LibreTranslate compatibility
# Some LibreTranslate servers (like RunPod instances) require a User-Agent header
_original_request_init = request.Request.__init__

def _patched_request_init(self, url, data=None, headers=None, origin_req_host=None, unverifiable=False, method=None):
    """Patched Request.__init__ that ensures User-Agent header is present."""
    if headers is None:
        headers = {}
    elif not isinstance(headers, dict):
        # Convert headers to dict if it's another type
        headers = dict(headers) if hasattr(headers, 'items') else {}
    
    # Add User-Agent if not present
    if 'User-Agent' not in headers and 'user-agent' not in headers:
        headers['User-Agent'] = 'Mozilla/5.0 (compatible; DoclingServe/1.0; LibreTranslate Client)'
    
    # Call original constructor
    _original_request_init(self, url, data, headers, origin_req_host, unverifiable, method)

# Apply the patch
request.Request.__init__ = _patched_request_init

class TranslationService:
    def __init__(self, settings: TranslationSettings):
        self.settings = settings
        self.api = LibreTranslateAPI(
            url=settings.libretranslate_host,
            api_key=settings.api_key
        )
        
    def detect_language(self, text: str) -> Optional[Tuple[str, float]]:
        """Detect language of markdown text."""
        if not self.settings.enabled:
            return None
            
        try:
            # Use first 1000 chars for detection, skip markdown headers and formatting
            sample_text = self._clean_text_for_detection(text[:10000])
            
            result = self.api.detect(sample_text)
            
            if result and len(result) > 0:
                language = result[0].get("language")
                confidence = result[0].get("confidence", 0.0)
                
                if confidence >= self.settings.min_confidence:
                    logger.info(f"Detected language: {language} (confidence: {confidence:.2f})")
                    return language, confidence
                else:
                    logger.warning(f"Language detection confidence {confidence:.2f} below threshold {self.settings.min_confidence}")
                    
        except Exception as e:
            logger.error(f"Language detection failed: {e}")
            
        return None
    
    def _clean_text_for_detection(self, text: str) -> str:
        """Clean markdown text for better language detection."""
        import re
        
        # Remove markdown headers
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
        # Remove markdown links
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # Remove markdown emphasis
        text = re.sub(r'[*_]{1,2}([^*_]+)[*_]{1,2}', r'\1', text)
        # Remove code blocks
        text = re.sub(r'```[^`]*```', '', text, flags=re.DOTALL)
        text = re.sub(r'`[^`]+`', '', text)
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def _split_text_into_chunks(self, text: str, max_words: int = 500) -> List[str]:
        """Split text into chunks of approximately max_words, preserving sentence boundaries."""
        words = text.split()
        if len(words) <= max_words:
            return [text]
            
        chunks = []
        current_chunk = []
        word_count = 0

        for word in words:
            current_chunk.append(word)
            word_count += 1

            # Check if we should end this chunk
            if word_count >= max_words:
                # Try to find a sentence boundary
                chunk_text = ' '.join(current_chunk)

                # Look for sentence endings in the last part of the chunk
                last_sentences = chunk_text.split('. ')
                if len(last_sentences) > 1:
                    # Keep all but the last incomplete sentence
                    complete_chunk = '. '.join(last_sentences[:-1]) + '.'
                    chunks.append(complete_chunk)

                    # Start next chunk with the incomplete sentence
                    remaining_words = last_sentences[-1].split()
                    current_chunk = remaining_words
                    word_count = len(remaining_words)
                else:
                    # No sentence boundary found, split at word boundary
                    chunks.append(chunk_text)
                    current_chunk = []
                    word_count = 0

        # Add remaining words as final chunk
        if current_chunk:
            chunks.append(' '.join(current_chunk))

        return chunks

    def _translate_chunk(self, chunk: str, source_lang: str, target_lang: str, chunk_index: int) -> Tuple[int, Optional[str]]:
        """Translate a single chunk and return with its index."""
        try:
            translated = self.api.translate(chunk, source_lang, target_lang)
            logger.debug(f"Translated chunk {chunk_index}: {len(chunk)} chars -> {len(translated) if translated else 0} chars")
            return chunk_index, translated
        except Exception as e:
            logger.error(f"Failed to translate chunk {chunk_index}: {e}")
            return chunk_index, None
                
    def translate_markdown_parallel(self, md_content: str, source_lang: str, target_lang: str) -> Optional[str]:
        """Translate markdown content in parallel chunks."""
        if not self.settings.enabled or source_lang == target_lang:
            return None
            
        # Check text length limit
        if len(md_content) > self.settings.max_text_length:
            logger.warning(f"Text length {len(md_content)} exceeds limit {self.settings.max_text_length}, truncating")
            md_content = md_content[:self.settings.max_text_length]
            
        start_time = time.time()
        try:
            # Split text into chunks
            chunks = self._split_text_into_chunks(md_content, max_words=500)
            logger.info(f"Split text into {len(chunks)} chunks for parallel translation")

            if len(chunks) == 1:
                # Single chunk, use regular translation
                return self.translate_markdown(md_content, source_lang, target_lang)

            # Translate chunks in parallel using ThreadPoolExecutor
            translated_chunks = [None] * len(chunks)

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(chunks))) as executor:
                # Submit all translation tasks
                future_to_index = {
                    executor.submit(self._translate_chunk, chunk, source_lang, target_lang, i): i
                    for i, chunk in enumerate(chunks)
                }

                # Collect results as they complete
                for future in concurrent.futures.as_completed(future_to_index):
                    chunk_index, translated_text = future.result()
                    if translated_text:
                        translated_chunks[chunk_index] = translated_text
                    else:
                        logger.error(f"Translation failed for chunk {chunk_index}")
                        # Use original chunk as fallback
                        translated_chunks[chunk_index] = chunks[chunk_index]

            # Recombine translated chunks
            final_translation = ' '.join(chunk for chunk in translated_chunks if chunk)

            # Log translation performance
            end_time = time.time()
            translation_time = end_time - start_time
            words = len(md_content.split())
            wps = words / translation_time if translation_time > 0 else 0

            logger.info(f"Parallel translation {source_lang}→{target_lang}: {translation_time:.2f}s, {words} words, {wps:.1f} words/sec, {len(chunks)} chunks")

            return final_translation if final_translation.strip() else None
        except Exception as e:
            end_time = time.time()
            translation_time = end_time - start_time
            logger.error(f"Parallel translation failed from {source_lang} to {target_lang} after {translation_time:.2f}s: {e}")
            return None

    def translate_markdown(self, md_content: str, source_lang: str, target_lang: str) -> Optional[str]:
        """Translate markdown content. Uses parallel chunking for large texts."""
        if not self.settings.enabled or source_lang == target_lang:
            return None

        # Single chunk translation for smaller texts
        if len(md_content) > self.settings.max_text_length:
            logger.warning(f"Text length {len(md_content)} exceeds limit {self.settings.max_text_length}, truncating")
            md_content = md_content[:self.settings.max_text_length]
        # Use parallel translation for texts with more than 500 words
        word_count = len(md_content.split())
        if word_count > 500:
            return self.translate_markdown_parallel(md_content, source_lang, target_lang)

        start_time = time.time()
        try:
            translated_text = self.api.translate(md_content, source_lang, target_lang)
            
            # Log translation performance
            end_time = time.time()
            translation_time = end_time - start_time
            
            if translated_text:
                words = len(md_content.split())
                wps = words / translation_time if translation_time > 0 else 0
                logger.info(f"Translation {source_lang}→{target_lang}: {translation_time:.2f}s, {words} words, {wps:.1f} words/sec")
            else:
                logger.warning(f"Translation {source_lang}→{target_lang} returned empty result after {translation_time:.2f}s")
                
            return translated_text
        except Exception as e:
            end_time = time.time()
            translation_time = end_time - start_time
            logger.error(f"Translation failed from {source_lang} to {target_lang} after {translation_time:.2f}s: {e}")
            return None
    
    def get_supported_languages(self) -> List[str]:
        """Get list of supported languages from LibreTranslate API."""
        try:
            languages = self.api.languages()
            return [lang["code"] for lang in languages]
        except Exception as e:
            logger.error(f"Failed to get supported languages: {e}")
            return []

    def get_translation_target_languages(self, ocr_languages: Optional[List[str]] = None) -> List[str]:
        """Get target languages for translation based on OCR languages or settings."""
        if ocr_languages:
            return ocr_languages
        return self.settings.target_languages

    def translate_to_all_languages(self, md_content: str, ocr_languages: Optional[List[str]] = None) -> Dict[str, str]:
        """Detect language and translate to OCR target languages."""
        translations = {}
        
        # Get target languages from OCR settings
        target_languages = self.get_translation_target_languages(ocr_languages)
        if not target_languages:
            logger.info("No valid translation target languages - skipping translation")
            return translations
        
        # Detect source language
        detection = self.detect_language(md_content)
        if not detection:
            logger.warning("Could not detect language for translation - skipping translation")
            return translations
            
        source_lang, confidence = detection
        translations["detected_language"] = source_lang
        translations["confidence"] = confidence
        
        # Translate to target languages except source (avoid self-translation)
        target_languages_set = set(target_languages) - {source_lang}
        
        if not target_languages_set:
            logger.info(f"Source language '{source_lang}' matches all target languages - no translation needed")
            return translations
        
        for target_lang in target_languages_set:
            try:
                translated = self.translate_markdown(md_content, source_lang, target_lang)
                if translated:
                    translations[target_lang] = translated
                    logger.info(f"Successfully translated to {target_lang}")
                else:
                    logger.warning(f"Translation to {target_lang} returned empty result")
            except Exception as e:
                logger.error(f"Failed to translate to {target_lang}: {e}")
                
        return translations
    
    def process_conversion_result(self, response: Any, ocr_languages: Optional[List[str]] = None) -> Any:
        """Process conversion result and append translations to markdown content."""
        if not self.settings.enabled:
            return response
            
        try:
            # Handle different response types
            if hasattr(response, 'document') and hasattr(response.document, 'md_content'):
                # Single document response
                md_content = response.document.md_content
                if md_content:
                    enhanced_md = self._append_translations(md_content, ocr_languages)
                    response.document.md_content = enhanced_md
                    
            elif hasattr(response, 'documents'):
                # Multiple documents response
                for doc in response.documents:
                    if hasattr(doc, 'md_content') and doc.md_content:
                        enhanced_md = self._append_translations(doc.md_content, ocr_languages)
                        doc.md_content = enhanced_md
                        
            elif isinstance(response, dict) and 'document' in response:
                # Dictionary format response
                doc = response['document']
                if 'md_content' in doc and doc['md_content']:
                    enhanced_md = self._append_translations(doc['md_content'], ocr_languages)
                    doc['md_content'] = enhanced_md
                    
            else:
                logger.warning(f"Unknown response format for translation: {type(response)}")
        except Exception as e:
            logger.error(f"Error processing conversion result for translation: {e}")
        return response
    
    def _append_translations(self, original_md: str, ocr_languages: Optional[List[str]] = None) -> str:
        """Append translations to original markdown content."""
        try:
            translations = self.translate_to_all_languages(original_md, ocr_languages)
            
            if not translations or len(translations) <= 2:  # Only metadata, no actual translations
                logger.info("No translations generated - returning original content")
                return original_md
                
            enhanced_md = original_md
            detected_lang = translations.get("detected_language", "unknown")
            
            # Add translation header (without confidence)
            enhanced_md += f"\n\n---\n\n# Auto-Generated Translations\n\n"
            enhanced_md += f"*Source language detected: {detected_lang}*\n\n"
            
            # Language code to full name mapping (LibreTranslate two-letter codes)
            lang_names = {
                "en": "English",
                "ar": "Arabic",
                "el": "Greek",
                "es": "Spanish", 
                "fr": "French",
                "de": "German",
                "it": "Italian",
                "pt": "Portuguese",
                "ru": "Russian",
                "zh": "Chinese",
                "ja": "Japanese",
                "ko": "Korean",
                "nl": "Dutch",
                "pl": "Polish",
                "tr": "Turkish",
                "vi": "Vietnamese",
                "hi": "Hindi"
            }
            
            # Add each translation
            for lang_code, translated_text in translations.items():
                if lang_code not in ["detected_language", "confidence"]:
                    lang_name = lang_names.get(lang_code, lang_code.upper())
                    enhanced_md += f"## {lang_name} Translation\n\n"
                    enhanced_md += f"{translated_text}\n\n"
                    
            logger.info(f"Successfully appended {len(translations) - 2} translations")
            return enhanced_md
            
        except Exception as e:
            logger.error(f"Error appending translations: {e}")
            return original_md
