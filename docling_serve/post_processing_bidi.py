#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import logging
from bidi.algorithm import get_display
from pathlib import Path
import tempfile
from io import BytesIO

logger = logging.getLogger(__name__)

class Line:
    """
    A single markdown line.  Flags RTL if it contains any Arabic,
    and can reverse+BiDi-reorder its content while preserving a
    leading markdown prefix.
    """
    ARABIC_RE = re.compile(r'[\u0600-\u06FF]')
    HEBREW_RE = re.compile(r'[\u0590-\u05FF]')
    DIRECTIONAL_MARKS_RE = re.compile(r'[\u200E-\u200F\u202A-\u202E\u2066-\u2069]')

    def __init__(self, raw: str):
        self.raw = raw
        self.has_arabic = bool(self.ARABIC_RE.search(raw))
        self.has_hebrew = bool(self.HEBREW_RE.search(raw))
        self.has_directional_marks = bool(self.DIRECTIONAL_MARKS_RE.search(raw))
        self.is_rtl = self.has_arabic or self.has_hebrew
        
        # Debug logging
        if self.is_rtl:
            logger.debug(f"RTL line detected: Arabic={self.has_arabic}, Hebrew={self.has_hebrew}, "
                        f"Directional marks={self.has_directional_marks}")

    def reversed(self) -> str:
        # capture markdown markers (#, >, *, -, etc.), body, newline
        m = re.match(
            r'^(?P<prefix>\s*(?:#{1,6}\s+|[-+*]\s+|>\s*))?'
            r'(?P<body>.*?)(?P<nl>\n?)$',
            self.raw
        )
        prefix = m.group('prefix') or ''
        body   = m.group('body')   or ''
        nl     = m.group('nl')     or ''

        logger.debug(f"Processing line - Prefix: '{prefix}', Body: '{body}'")
        
        # Apply full Unicode-BiDi to handle mixed runs
        bidi_fixed = get_display(body)
        
        logger.debug(f"BiDi transformation: '{body}' -> '{bidi_fixed}'")
        
        return prefix + bidi_fixed + nl


class RTLBlock:
    """Wraps consecutive RTL lines in <div dir="rtl">…</div>."""
    def __init__(self):
        self.lines = []

    def add_line(self, line: Line):
        self.lines.append(line)

    def render(self) -> str:
        logger.debug(f"Rendering RTL block with {len(self.lines)} lines")
        out = []
        for ln in self.lines:
            out.append(ln.reversed())
        return ''.join(out)


class MarkdownProcessor:
    """
    Walks a markdown document, groups RTL lines, and
    emits a new doc with LTR lines untouched and RTL
    blocks properly reversed+BiDi-wrapped.
    """
    def __init__(self, text: str, pdf_analysis_results=None):
        self.text = text
        self.pdf_analysis_results = pdf_analysis_results
        self.lines = [Line(l) for l in text.splitlines(keepends=True)]
        self.rtl_lines_count = sum(1 for line in self.lines if line.is_rtl)
        self.total_lines = len(self.lines)
        self.has_directional_marks = any(line.has_directional_marks for line in self.lines)
        
        # Check for HTML dir attributes in the content
        self.has_html_dir_tags = bool(re.search(r'dir\s*=\s*["\']?(rtl|ltr)["\']?', self.text, re.IGNORECASE))
        
        logger.debug(f"Markdown analysis: {self.total_lines} total lines, "
                    f"{self.rtl_lines_count} RTL lines, "
                    f"has directional marks: {self.has_directional_marks}, "
                    f"has HTML dir tags: {self.has_html_dir_tags}")

    def needs_processing(self) -> bool:
        """
        Determine if this markdown content needs BiDi processing.
        Now considers PDF analysis results first, then content analysis.
        """
        # First check PDF analysis results
        if self.pdf_analysis_results:
            is_tagged = self.pdf_analysis_results.get('is_tagged', False)
            text_quality = self.pdf_analysis_results.get('text_quality', 'unknown')
            recommended_mode = self.pdf_analysis_results.get('recommended_mode', 'force')
            has_text = self.pdf_analysis_results.get('has_text', False)
            
            # Skip if PDF is tagged with good quality or recommended mode is skip
            if (is_tagged and text_quality == 'good') or (recommended_mode == 'skip' and has_text):
                logger.info(f"PDF analysis indicates no BiDi processing needed: "
                           f"is_tagged={is_tagged}, text_quality={text_quality}, "
                           f"recommended_mode={recommended_mode}, has_text={has_text}")
                return False
            
            # Also skip if tagged with any quality (conservative approach)
            if is_tagged:
                logger.info(f"PDF is tagged, skipping BiDi processing regardless of quality")
                return False
        
        # Check if content already has directional markup
        if self.has_directional_marks:
            logger.info("Content already has Unicode directional marks, skipping BiDi processing")
            return False
        
        if self.has_html_dir_tags:
            logger.info("Content already has HTML dir attributes, skipping BiDi processing")
            return False
        
        # Fallback to content analysis
        needs_bidi = self.rtl_lines_count > 0
        logger.debug(f"Content-based BiDi processing needed: {needs_bidi} "
                    f"(RTL lines: {self.rtl_lines_count})")
        return needs_bidi

    def process(self) -> str:
        if not self.needs_processing():
            logger.debug("No BiDi processing needed, returning original text")
            return ''.join(line.raw for line in self.lines)
        
        logger.debug("Starting BiDi processing")
        out = []
        rtl_block = None

        for i, ln in enumerate(self.lines):
            logger.debug(f"Processing line {i+1}/{self.total_lines}: RTL={ln.is_rtl}")
            
            if ln.is_rtl:
                if rtl_block is None:
                    rtl_block = RTLBlock()
                    logger.debug("Starting new RTL block")
                rtl_block.add_line(ln)
            else:
                if rtl_block is not None:
                    logger.debug("Closing RTL block and rendering")
                    out.append(rtl_block.render())
                    rtl_block = None
                out.append(ln.raw)

        if rtl_block is not None:
            logger.debug("Rendering final RTL block")
            out.append(rtl_block.render())

        result = ''.join(out)
        logger.debug(f"BiDi processing completed, text length: {len(result)}")
        return result


class BiDiProcessor:
    """BiDi text processor for conversion results."""
    
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.logger = logging.getLogger(__name__)

    def _is_document_tagged(self, content: str, pdf_analysis_results=None) -> bool:
        """
        Check if document already has directional tagging/processing.
        Now uses PDF analysis results for better decision making.
        
        Args:
            content: The document content to check
            pdf_analysis_results: Results from PDF analysis
            
        Returns:
            bool: True if content has directional tagging or comes from tagged PDF
        """
        if not content:
            return False
        
        # First check PDF analysis results if available
        if pdf_analysis_results:
            is_tagged = pdf_analysis_results.get('is_tagged', False)
            text_quality = pdf_analysis_results.get('text_quality', 'unknown')
            
            if is_tagged:
                self.logger.info("PDF analysis indicates document is tagged")
                return True
            
            if text_quality == 'good':
                self.logger.info("PDF analysis indicates good text quality, likely well-structured")
                return True
        
        self.logger.info("PDF Analysis results were not found, falling back to directional attributes recognition.")
        
        # Fallback to content analysis
        # Check for HTML directional attributes
        html_dir_pattern = re.compile(r'dir\s*=\s*["\']?(rtl|ltr)["\']?', re.IGNORECASE)
        has_html_dir = bool(html_dir_pattern.search(content))
        
        # Check for Unicode directional marks
        directional_marks_pattern = re.compile(r'[\u200E-\u200F\u202A-\u202E\u2066-\u2069]')
        has_directional_marks = bool(directional_marks_pattern.search(content))
        
        # Check for BiDi override characters
        bidi_override_pattern = re.compile(r'[\u202D-\u202E]')
        has_bidi_override = bool(bidi_override_pattern.search(content))
        
        # Check for structured content markers
        structured_content_indicators = [
            r'<[^>]+>',  # HTML tags
            r'\n#{1,6}\s+',  # Markdown headers
            r'\n\s*[-*+]\s+',  # Markdown lists
            r'\n\s*\d+\.\s+',  # Numbered lists
        ]
        
        structured_content_score = 0
        for pattern in structured_content_indicators:
            matches = len(re.findall(pattern, content))
            structured_content_score += matches
        
        has_structured_content = structured_content_score > 5
        
        is_tagged = has_html_dir or has_directional_marks or has_bidi_override or has_structured_content
        
        self.logger.debug(f"Content analysis: HTML dir={has_html_dir}, "
                         f"directional marks={has_directional_marks}, "
                         f"BiDi override={has_bidi_override}, "
                         f"structured content score={structured_content_score}, "
                         f"is_tagged={is_tagged}")
        
        return is_tagged

    def _process_document_dict(self, document_dict, task_info=None, pdf_analysis_results=None):
        """Process a document dictionary and apply BiDi processing to markdown content."""
        if not isinstance(document_dict, dict):
            self.logger.warning(f"Expected document dict, got {type(document_dict)}")
            return document_dict, 0
        
        bidi_applied = 0
        
        # Process markdown content if present
        if "md_content" in document_dict and document_dict["md_content"]:
            original_markdown = document_dict["md_content"]
            if original_markdown and isinstance(original_markdown, str):
                try:
                    self.logger.debug(f"Processing markdown content (length: {len(original_markdown)})")
                    
                    # Pass PDF analysis results to MarkdownProcessor
                    processor = MarkdownProcessor(original_markdown, pdf_analysis_results)
                    
                    # Now the processor knows about PDF analysis
                    if not processor.needs_processing():
                        self.logger.info("Document doesn't need BiDi processing (PDF analysis or content analysis)")
                        return document_dict, 0
                    
                    # Additional check using content analysis
                    if self._is_document_tagged(original_markdown, pdf_analysis_results):
                        self.logger.info("Document appears to be already tagged, skipping BiDi processing")
                        return document_dict, 0
                    
                    processed_markdown = processor.process()
                    
                    # Only update if processing actually changed something
                    if processed_markdown != original_markdown:
                        document_dict["md_content"] = processed_markdown
                        bidi_applied = 1
                        self.logger.info("BiDi processing applied to markdown content")
                        
                        # Log sample of changes for debugging
                        if self.logger.isEnabledFor(logging.DEBUG):
                            orig_preview = original_markdown[:200].replace('\n', '\\n')
                            proc_preview = processed_markdown[:200].replace('\n', '\\n')
                            self.logger.debug(f"Original: {orig_preview}")
                            self.logger.debug(f"Processed: {proc_preview}")
                    else:
                        self.logger.debug("No changes made during processing")
                        
                except Exception as e:
                    self.logger.error(f"Error processing markdown content: {e}", exc_info=True)
        else:
            self.logger.debug("No markdown content found in document")
        
        return document_dict, bidi_applied

    def _process_document_response(self, document, task_info=None, pdf_analysis_results=None):
        """Process a document response object and apply BiDi processing."""
        if hasattr(document, '__dict__'):
            # Convert object to dict, process, then update object
            doc_dict = document.__dict__.copy()
            processed_dict, bidi_applied = self._process_document_dict(
                doc_dict, task_info, pdf_analysis_results
            )
            
            # Update the original object
            for key, value in processed_dict.items():
                setattr(document, key, value)
                
            return document, bidi_applied
        else:
            self.logger.warning(f"Document object has no __dict__ attribute: {type(document)}")
            return document, 0

    def process_conversion_result(self, result, task_info=None, pdf_analysis_results=None):
        """
        Process and enhance conversion result with BiDi processing.
        
        Args:
            result: The conversion result to process
            task_info: Optional task information for enhanced analysis
            pdf_analysis_results: Results from PDF analysis for smarter decisions
        """
        if not self.enabled:
            self.logger.debug("BiDi processing disabled, returning original result")
            return result
        
        self.logger.info("Starting BiDi processing of conversion result")
        self.logger.debug(f"Result type: {type(result)}")
        
        # Log PDF analysis results for debugging
        if pdf_analysis_results:
            self.logger.info(f"PDF analysis results: {pdf_analysis_results}")
        else:
            self.logger.debug("No PDF analysis results available")
        
        documents_processed = 0
        bidi_applications = 0
        
        try:
            # Handle response object with document attribute
            if hasattr(result, 'document') and result.document is not None:
                self.logger.debug("Processing single document from result.document attribute")
                corrected_document, doc_bidi = self._process_document_response(
                    result.document, task_info, pdf_analysis_results
                )
                
                # Update the document in place
                result.document = corrected_document
                documents_processed = 1
                bidi_applications = doc_bidi
                
            # Handle response object with documents attribute (list)
            elif hasattr(result, 'documents') and result.documents is not None:
                doc_count = len(result.documents)
                self.logger.debug(f"Processing {doc_count} documents from result.documents attribute")
                
                corrected_documents = []
                for i, doc in enumerate(result.documents):
                    self.logger.debug(f"Processing document {i+1}/{doc_count}")
                    corrected_doc, doc_bidi = self._process_document_response(
                        doc, task_info, pdf_analysis_results
                    )
                    corrected_documents.append(corrected_doc)
                    bidi_applications += doc_bidi
                    documents_processed += 1
                
                result.documents = corrected_documents
                
            # Handle dictionary-style result
            elif isinstance(result, dict):
                self.logger.debug("Processing dictionary-style result")
                
                if "document" in result:
                    self.logger.debug("Processing single document from dictionary")
                    result["document"], doc_bidi = self._process_document_dict(
                        result["document"], task_info, pdf_analysis_results
                    )
                    documents_processed = 1
                    bidi_applications = doc_bidi
                    
                elif "documents" in result and isinstance(result["documents"], list):
                    doc_count = len(result["documents"])
                    self.logger.debug(f"Processing {doc_count} documents from dictionary")
                    
                    processed_docs = []
                    for i, doc in enumerate(result["documents"]):
                        self.logger.debug(f"Processing document {i+1}/{doc_count}")
                        processed_doc, doc_bidi = self._process_document_dict(
                            doc, task_info, pdf_analysis_results
                        )
                        processed_docs.append(processed_doc)
                        bidi_applications += doc_bidi
                        documents_processed += 1
                    
                    result["documents"] = processed_docs
            
            # Handle JSONResponse or similar response objects
            elif hasattr(result, 'body') or (hasattr(result, 'content') and hasattr(result, 'status_code')):
                self.logger.debug("Processing JSON response object")
                try:
                    import json
                    from fastapi.responses import JSONResponse
                    
                    # Get the response data
                    if hasattr(result, 'body'):
                        response_data = json.loads(result.body.decode('utf-8'))
                    elif callable(getattr(result, 'json', None)):
                        response_data = result.json()
                    else:
                        self.logger.warning("Could not extract JSON from response object")
                        return result
                    
                    # Process the data
                    if "document" in response_data:
                        response_data["document"], doc_bidi = self._process_document_dict(
                            response_data["document"], task_info, pdf_analysis_results
                        )
                        documents_processed = 1
                        bidi_applications = doc_bidi
                    
                    # Create new response with processed data
                    result = JSONResponse(content=response_data)
                    
                except Exception as e:
                    self.logger.error(f"Error processing JSON response: {e}")
            
            else:
                self.logger.warning(f"Unsupported result structure: {type(result)}")
        
        except Exception as e:
            self.logger.error(f"Error processing conversion result: {e}", exc_info=True)
        
        self.logger.info(f"BiDi processing completed - Documents: {documents_processed}, "
                        f"BiDi applications: {bidi_applications}")
        
        return result


def main():
    # Configure logging for testing
    logging.basicConfig(level=logging.DEBUG)
    
    # Test case 1: RTL content that needs processing
    src1 = """## يكالهتسالا ليومتلا طباوضي
This is mixed content with Arabic text.
"""
    print("=== Test 1: RTL content (no PDF analysis) ===")
    print("Source:", repr(src1))
    result1 = MarkdownProcessor(src1).process()
    print("Result:", repr(result1))
    
    # Test case 2: RTL content with tagged PDF analysis
    print("\n=== Test 2: RTL content with tagged PDF analysis ===")
    pdf_analysis_tagged = {
        'is_tagged': True,
        'text_quality': 'good',
        'has_text': True,
        'recommended_mode': 'skip'
    }
    processor_tagged = MarkdownProcessor(src1, pdf_analysis_tagged)
    print("Source:", repr(src1))
    print("PDF Analysis:", pdf_analysis_tagged)
    print("Needs processing:", processor_tagged.needs_processing())
    result2 = processor_tagged.process()
    print("Result:", repr(result2))
    
    # Test case 3: RTL content with untagged PDF analysis
    print("\n=== Test 3: RTL content with untagged PDF analysis ===")
    pdf_analysis_untagged = {
        'is_tagged': False,
        'text_quality': 'poor',
        'has_text': True,
        'recommended_mode': 'force'
    }
    processor_untagged = MarkdownProcessor(src1, pdf_analysis_untagged)
    print("Source:", repr(src1))
    print("PDF Analysis:", pdf_analysis_untagged)
    print("Needs processing:", processor_untagged.needs_processing())
    result3 = processor_untagged.process()
    print("Result:", repr(result3))
    
    # Test case 4: Already tagged content
    src4 = """<div dir="rtl">يكالهتسالا ليومتلا طباوضي</div>
This content is already tagged.
"""
    print("\n=== Test 4: Already tagged content ===")
    print("Source:", repr(src4))
    result4 = MarkdownProcessor(src4).process()
    print("Result:", repr(result4))
    
    # Test case 5: BiDi processor with mock response
    print("\n=== Test 5: BiDi processor with mock response ===")
    processor = BiDiProcessor()
    
    mock_response = {
        "document": {
            "md_content": src1
        }
    }
    
    # Test with tagged PDF analysis
    result5_tagged = processor.process_conversion_result(
        mock_response.copy(), 
        pdf_analysis_results=pdf_analysis_tagged
    )
    print("Tagged PDF result:", result5_tagged["document"]["md_content"][:100])
    
    # Test with untagged PDF analysis  
    result5_untagged = processor.process_conversion_result(
        mock_response.copy(), 
        pdf_analysis_results=pdf_analysis_untagged
    )
    print("Untagged PDF result:", result5_untagged["document"]["md_content"][:100])


if __name__ == '__main__':
    main()