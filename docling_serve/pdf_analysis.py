from io import BytesIO
import logging
import re
from pathlib import Path
import unicodedata
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def analyze_pdf(pdf_path: Path) -> Dict[str, Any]:
    """
    Analyze PDF to determine if it needs OCR and what type of OCR to apply.
    Works with multilingual documents including non-Latin scripts.
    
    Args:
        pdf_path: Path to the PDF file to analyze
        
    Returns:
        dict: Analysis results with the following keys:
            - needs_ocr: Whether OCR is needed at all
            - has_text: Whether the PDF contains any text
            - is_tagged: Whether the PDF is tagged
            - text_quality: Assessment of text quality ('good', 'poor', 'unknown')
            - recommended_mode: Recommended OCR mode ('force', 'redo', 'skip')
    """
    result = {
        'needs_ocr': True,
        'has_text': False,
        'is_tagged': False,
        'text_quality': 'unknown',
        'recommended_mode': 'force',  # Default to force-ocr
    }
    
    try:
        # Try to use pikepdf for analysis
        try:
            from pikepdf import Pdf
            with Pdf.open(pdf_path) as pdf:
                # Check if PDF is tagged
                if hasattr(pdf.Root, 'MarkInfo') and pdf.Root.MarkInfo.get('/Marked', False):
                    result['is_tagged'] = True
                    logger.info(f"PDF is tagged, likely created from an office document")
        except ImportError:
            logger.warning("pikepdf not available, cannot check if PDF is tagged")
        
        # Text quality analysis
        pages_with_text = 0
        pages_total = 0
        text_samples = []
        poor_quality_indicators = 0
        
        # Use pdfplumber to extract and analyze text
        try:
            import pdfplumber
            
            with pdfplumber.open(pdf_path) as pdf:
                pages_total = len(pdf.pages)
                for page_num, page in enumerate(pdf.pages):
                    page_text = page.extract_text().strip()
                    
                    if len(page_text) > 0:
                        pages_with_text += 1
                        
                        # Only sample a few pages for quality analysis
                        if page_num < 5 or page_num % max(1, int(pages_total / 10)) == 0:
                            text_samples.append(page_text[:2000])  # Sample first 2000 chars
                    
            # If we found text, analyze its quality using language-agnostic approaches
            if text_samples:
                result['has_text'] = True
                text_coverage = pages_with_text / max(pages_total, 1)
                logger.info(f"PDF has text on {pages_with_text}/{pages_total} pages ({text_coverage:.1%})")
                
                # Analyze text quality using language-agnostic approaches
                for sample in text_samples:
                    # 1. Check for control characters (language-agnostic)
                    control_chars = sum(1 for c in sample if unicodedata.category(c)[0] == 'C')
                    control_ratio = control_chars / max(len(sample), 1)
                    if control_ratio > 0.03:  # More than 3% control chars
                        poor_quality_indicators += 1
                        logger.debug(f"High control character ratio: {control_ratio:.2f}")
                    
                    # 2. Check for high proportion of symbols/punctuation (language-agnostic)
                    # This works for most scripts as proper text generally has fewer symbols
                    symbols = sum(1 for c in sample if unicodedata.category(c)[0] in ('P', 'S'))
                    symbol_ratio = symbols / max(len(sample), 1)
                    if symbol_ratio > 0.30:  # More than 30% symbols/punctuation
                        poor_quality_indicators += 1
                        logger.debug(f"High symbol/punctuation ratio: {symbol_ratio:.2f}")
                    
                    # 3. Check for escape sequences (language-agnostic, common in bad OCR)
                    escape_sequences = len(re.findall(r'\\[0-9a-fA-F]{2}', sample))
                    if escape_sequences > 5:
                        poor_quality_indicators += 2  # Weight this higher
                        logger.debug(f"Found {escape_sequences} escape sequences")
                    
                    # 4. Check for space consistency (language-agnostic)
                    # Good OCR typically has consistent spacing between words/characters
                    space_pattern = re.findall(r'[ ]{1,10}', sample)
                    if space_pattern:
                        avg_space_len = sum(len(s) for s in space_pattern) / len(space_pattern)
                        # Unusual average space length or high variance indicates poor OCR
                        if avg_space_len > 2.5:
                            poor_quality_indicators += 1
                            logger.debug(f"Unusual spacing pattern: avg={avg_space_len:.2f}")
                    
                    # 5. Check for unprintable characters or replacement characters
                    # Unicode replacement character � (U+FFFD) often indicates OCR failure
                    if '\ufffd' in sample:
                        poor_quality_indicators += len(re.findall('\ufffd', sample))
                        logger.debug("Found Unicode replacement characters")
                
                # Determine text quality based on indicators
                quality_score = poor_quality_indicators / max(len(text_samples), 1)
                if quality_score > 0.8:
                    result['text_quality'] = 'poor'
                    logger.info(f"PDF contains text, but quality appears poor (score: {quality_score:.2f})")
                else:
                    result['text_quality'] = 'good'
                    logger.info(f"PDF contains text of reasonable quality (score: {quality_score:.2f})")
                
        except ImportError:
            logger.warning("pdfplumber not available, text quality analysis limited")
        except Exception as e:
            logger.warning(f"Error during text quality analysis: {e}")
        
        # Final decision making based on all factors
        if result['is_tagged'] and result['has_text'] and result['text_quality'] != 'poor':
            # Tagged PDFs with good text don't need OCR
            result['needs_ocr'] = False
            result['recommended_mode'] = 'skip'
        elif result['has_text'] and result['text_quality'] == 'poor':
            # Has text but poor quality - use force-ocr to completely redo it
            result['recommended_mode'] = 'force'
            logger.info("Poor text quality detected, recommending force-ocr")
        elif result['has_text'] and not result['is_tagged']:
            # Has reasonable text but not tagged - might benefit from redo-ocr
            result['recommended_mode'] = 'redo'
        else:
            # No text or couldn't determine - use force-ocr
            result['recommended_mode'] = 'force'
            
        logger.info(f"PDF analysis complete: {result}")
        return result
        
    except Exception as e:
        logger.error(f"Error analyzing PDF: {e}")
        # Default to force-ocr on analysis error
        return result

def check_pdf_is_tagged(pdf_path: Path) -> bool:
    """
    Check if a PDF is tagged (has structural information).
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        bool: True if the PDF is tagged, False otherwise
    """
    try:
        from pikepdf import Pdf
        with Pdf.open(pdf_path) as pdf:
            if hasattr(pdf.Root, 'MarkInfo') and pdf.Root.MarkInfo.get('/Marked', False):
                return True
    except (ImportError, Exception):
        pass
    return False


def analyze_pdf_for_force_ocr(file_stream: BytesIO, filename: str) -> bool:
    """
    Analyze PDF to determine if force_ocr should be enabled for docling.
    
    Args:
        file_stream: PDF file content as BytesIO
        filename: Original filename for logging
        
    Returns:
        bool: True if force_ocr should be enabled, False otherwise
    """
    logger.info(f"Analyzing PDF {filename} to determine force_ocr setting")
    
    try:
        # Create temporary file for analysis
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_file:
            temp_file.write(file_stream.getvalue())
            temp_file.flush()
            temp_path = Path(temp_file.name)
            
        try:
            # Analyze the PDF
            analysis = analyze_pdf(temp_path)
            
            # Determine force_ocr based on analysis
            if analysis['recommended_mode'] == 'force':
                logger.info(f"PDF analysis recommends force_ocr=True for {filename}")
                return True
            elif analysis['text_quality'] == 'poor':
                logger.info(f"Poor text quality detected, setting force_ocr=True for {filename}")
                return True
            else:
                logger.info(f"PDF analysis recommends keeping force_ocr=False for {filename}")
                return False
                
        finally:
            temp_path.unlink(missing_ok=True)
            
    except Exception as e:
        logger.warning(f"Failed to analyze PDF {filename}: {e}")
        # Default to not forcing OCR on analysis failure
        return False

def should_analyze_file_for_force_ocr(filename: str) -> bool:
    """Check if file should be analyzed for force_ocr determination."""
    file_ext = Path(filename).suffix.lower()
    return file_ext == '.pdf'