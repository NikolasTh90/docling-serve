#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import logging
from bidi.algorithm import get_display

logger = logging.getLogger(__name__)

class Line:
    """
    A single markdown line.  Flags RTL if it contains any Arabic,
    and can reverse+BiDi-reorder its content while preserving a
    leading markdown prefix.
    """
    ARABIC_RE = re.compile(r'[\u0600-\u06FF]')

    def __init__(self, raw: str):
        self.raw = raw
        self.is_rtl = bool(self.ARABIC_RE.search(raw))

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

        # 1) reverse the mirrored input
        # rev = body[::-1]
        # 2) apply full Unicode-BiDi to handle mixed runs
        bidi_fixed = get_display(body)

        return prefix + bidi_fixed + nl


class RTLBlock:
    """Wraps consecutive RTL lines in <div dir="rtl">…</div>."""
    def __init__(self):
        self.lines = []

    def add_line(self, line: Line):
        self.lines.append(line)

    def render(self) -> str:
        return self.lines
        out = ['<div dir="rtl">\n']
        for ln in self.lines:
            out.append(ln.reversed())
        out.append('</div>\n')
        return ''.join(out)


class MarkdownProcessor:
    """
    Walks a markdown document, groups RTL lines, and
    emits a new doc with LTR lines untouched and RTL
    blocks properly reversed+BiDi-wrapped.
    """
    def __init__(self, text: str):
        self.lines = [Line(l) for l in text.splitlines(keepends=True)]

    def process(self) -> str:
        out = []
        rtl_block = None

        for ln in self.lines:
            if ln.is_rtl:
                if rtl_block is None:
                    rtl_block = RTLBlock()
                rtl_block.add_line(ln)
            else:
                if rtl_block is not None:
                    out.append(rtl_block.render())
                    rtl_block = None
                out.append(ln.raw)

        if rtl_block is not None:
            out.append(rtl_block.render())

        return ''.join(out)


class BiDiProcessor:
    """BiDi text processor for conversion results."""
    
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.logger = logging.getLogger(__name__)

    def _process_document_dict(self, document_dict):
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
                    processor = MarkdownProcessor(original_markdown)
                    processed_markdown = processor.process()
                    
                    # Only update if processing actually changed something
                    if processed_markdown != original_markdown:
                        document_dict["md_content"] = processed_markdown
                        bidi_applied = 1
                        self.logger.debug("BiDi processing applied to markdown content")
                    else:
                        self.logger.debug("No RTL content detected, markdown unchanged")
                        
                except Exception as e:
                    self.logger.error(f"Error processing markdown content: {e}")
        
        return document_dict, bidi_applied

    def _process_document_response(self, document):
        """Process a document response object and apply BiDi processing."""
        if hasattr(document, '__dict__'):
            # Convert object to dict, process, then update object
            doc_dict = document.__dict__.copy()
            processed_dict, bidi_applied = self._process_document_dict(doc_dict)
            
            # Update the original object
            for key, value in processed_dict.items():
                setattr(document, key, value)
                
            return document, bidi_applied
        else:
            self.logger.warning(f"Document object has no __dict__ attribute: {type(document)}")
            return document, 0

    def process_conversion_result(self, result):
        """Process and enhance conversion result with BiDi processing."""
        if not self.enabled:
            self.logger.debug("BiDi processing disabled, returning original result")
            return result
        
        self.logger.info("Starting BiDi processing of conversion result")
        self.logger.debug(f"Result type: {type(result)}")
        
        documents_processed = 0
        bidi_applications = 0
        
        try:
            # Handle response object with document attribute
            if hasattr(result, 'document') and result.document is not None:
                self.logger.debug("Processing single document from result.document attribute")
                corrected_document, doc_bidi = self._process_document_response(result.document)
                
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
                    corrected_doc, doc_bidi = self._process_document_response(doc)
                    corrected_documents.append(corrected_doc)
                    bidi_applications += doc_bidi
                    documents_processed += 1
                
                result.documents = corrected_documents
                
            # Handle dictionary-style result
            elif isinstance(result, dict):
                self.logger.debug("Processing dictionary-style result")
                
                if "document" in result:
                    self.logger.debug("Processing single document from dictionary")
                    result["document"], doc_bidi = self._process_document_dict(result["document"])
                    documents_processed = 1
                    bidi_applications = doc_bidi
                    
                elif "documents" in result and isinstance(result["documents"], list):
                    doc_count = len(result["documents"])
                    self.logger.debug(f"Processing {doc_count} documents from dictionary")
                    
                    processed_docs = []
                    for i, doc in enumerate(result["documents"]):
                        self.logger.debug(f"Processing document {i+1}/{doc_count}")
                        processed_doc, doc_bidi = self._process_document_dict(doc)
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
                        response_data["document"], doc_bidi = self._process_document_dict(response_data["document"])
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
    # src = input("Enter markdown content: ")
    src = """
## يكالهتسالا ليومتلا طباوضي
            """
    print("source: ", src)
    result = MarkdownProcessor(src).process()
    print("result: ",result)


if __name__ == '__main__':
    main()