"""
OCR Language utilities for converting between different OCR engine language codes.
"""
from typing import List, Optional, Set
import logging

# Language mapping from common/EasyOCR codes to Tesseract codes
LANGUAGE_MAPPING = {
    # Common ISO codes to Tesseract
    'en': 'eng',
    'ar': 'ara', 
    'fr': 'fra',
    'de': 'deu',
    'es': 'spa',
    'it': 'ita',
    'pt': 'por',
    'ru': 'rus',
    'zh': 'chi_sim',
    'zh-cn': 'chi_sim',
    'zh-tw': 'chi_tra',
    'ja': 'jpn',
    'ko': 'kor',
    'hi': 'hin',
    'th': 'tha',
    'vi': 'vie',
    'tr': 'tur',
    'pl': 'pol',
    'nl': 'nld',
    'sv': 'swe',
    'da': 'dan',
    'no': 'nor',
    'fi': 'fin',
    'cs': 'ces',
    'hu': 'hun',
    'ro': 'ron',
    'bg': 'bul',
    'hr': 'hrv',
    'sk': 'slk',
    'sl': 'slv',
    'et': 'est',
    'lv': 'lav',
    'lt': 'lit',
    'uk': 'ukr',
    'el': 'ell',
    'he': 'heb',
    'fa': 'fas',
    'ur': 'urd',
    'bn': 'ben',
    'ta': 'tam',
    'te': 'tel',
    'ml': 'mal',
    'kn': 'kan',
    'gu': 'guj',
    'pa': 'pan',
    'or': 'ori',
    'as': 'asm',
    'ne': 'nep',
    'si': 'sin',
    'my': 'mya',
    'km': 'khm',
    'lo': 'lao',
    'ka': 'kat',
    'am': 'amh',
    'is': 'isl',
    'mt': 'mlt',
    'cy': 'cym',
    'ga': 'gle',
    'gd': 'gla',
    'br': 'bre',
    'co': 'cos',
    'eu': 'eus',
    'ca': 'cat',
    'gl': 'glg',
    'oc': 'oci',
    'la': 'lat',
    'eo': 'epo',
    'vo': 'vol',
    'io': 'ido',
    'ia': 'ina',
    'ie': 'ile',
    'jbo': 'jbo',
    'tlh': 'tlh',
    # Additional common variations
    'chinese': 'chi_sim',
    'chinese-simplified': 'chi_sim',
    'chinese-traditional': 'chi_tra',
    'english': 'eng',
    'arabic': 'ara',
    'french': 'fra',
    'german': 'deu',
    'spanish': 'spa',
    'italian': 'ita',
    'portuguese': 'por',
    'russian': 'rus',
    'japanese': 'jpn',
    'korean': 'kor',
    'hindi': 'hin',
}

# Valid Tesseract language codes (for validation)
TESSERACT_CODES: Set[str] = {
    'afr', 'amh', 'ara', 'asm', 'aze', 'aze_cyrl', 'bel', 'ben', 'bod', 'bos',
    'bre', 'bul', 'cat', 'ceb', 'ces', 'chi_sim', 'chi_tra', 'chr', 'cym',
    'dan', 'deu', 'div', 'dzo', 'ell', 'eng', 'enm', 'epo', 'est', 'eus',
    'fao', 'fas', 'fin', 'fra', 'frk', 'frm', 'fry', 'gla', 'gle', 'glg',
    'grc', 'guj', 'hat', 'heb', 'hin', 'hrv', 'hun', 'hye', 'iku', 'ind',
    'isl', 'ita', 'ita_old', 'jav', 'jpn', 'kan', 'kat', 'kat_old', 'kaz',
    'khm', 'kir', 'kor', 'kur', 'lao', 'lat', 'lav', 'lit', 'ltz', 'mal',
    'mar', 'mkd', 'mlt', 'mon', 'mri', 'msa', 'mya', 'nep', 'nld', 'nor',
    'oci', 'ori', 'pan', 'pol', 'por', 'pus', 'que', 'ron', 'rus', 'san',
    'sin', 'slk', 'slv', 'snd', 'spa', 'spa_old', 'sqi', 'srp', 'srp_latn',
    'sun', 'swa', 'swe', 'syr', 'tam', 'tat', 'tel', 'tgk', 'tgl', 'tha',
    'tir', 'ton', 'tur', 'uig', 'ukr', 'urd', 'uzb', 'uzb_cyrl', 'vie',
    'yid', 'yor'
}

# EasyOCR supported language codes (for reference)
EASYOCR_CODES: Set[str] = {
    'en', 'ch_sim', 'ch_tra', 'th', 'hi', 'ja', 'ko', 'vi', 'ar', 'bg', 'hr',
    'cs', 'da', 'nl', 'et', 'fi', 'fr', 'de', 'el', 'hu', 'is', 'it', 'lv',
    'lt', 'mt', 'no', 'pl', 'pt', 'ro', 'sk', 'sl', 'es', 'sv', 'tr', 'uk',
    'cy', 'ga', 'gd', 'la', 'ml', 'ne', 'sa', 'si', 'ta', 'te', 'kn', 'gu',
    'pa', 'bn', 'as', 'or', 'ur', 'fa', 'he', 'my', 'lo', 'km', 'ka', 'am',
    'ti', 'mn', 'bo', 'dz', 'fo', 'gl', 'eu', 'ca', 'oc', 'br', 'co', 'io',
    'ia', 'ie', 'eo', 'vo', 'jbo', 'tlh'
}


def convert_to_tesseract_codes(
    ocr_languages: Optional[List[str]], 
    logger: Optional[logging.Logger] = None
) -> List[str]:
    """
    Convert language codes from EasyOCR/common format to Tesseract format.
    
    Args:
        ocr_languages: List of language codes in various formats
        logger: Optional logger for debug/warning messages
        
    Returns:
        List of valid Tesseract language codes
    """
    if not ocr_languages:
        return []
    
    if logger is None:
        logger = logging.getLogger(__name__)
        
    converted_languages = []
    
    for lang in ocr_languages:
        lang = lang.lower().strip()
        
        # Skip empty strings
        if not lang:
            continue
            
        # Check if it's already a valid Tesseract code
        if lang in TESSERACT_CODES:
            converted_languages.append(lang)
            logger.debug(f"Language '{lang}' already in Tesseract format")
            continue
            
        # Try to convert from common format to Tesseract
        if lang in LANGUAGE_MAPPING:
            tesseract_lang = LANGUAGE_MAPPING[lang]
            converted_languages.append(tesseract_lang)
            logger.debug(f"Converted language '{lang}' to Tesseract format '{tesseract_lang}'")
            continue
            
        # Unknown language code - log warning but continue
        logger.warning(f"Unknown language code '{lang}' - skipping")
        
    # Remove duplicates while preserving order
    unique_languages = []
    for lang in converted_languages:
        if lang not in unique_languages:
            unique_languages.append(lang)
            
    logger.info(f"Final Tesseract language codes: {unique_languages}")
    return unique_languages


def format_for_ocrmypdf(tesseract_languages: List[str]) -> str:
    """
    Format Tesseract language codes for OCRMyPDF ('+' separated string).
    
    Args:
        tesseract_languages: List of Tesseract language codes
        
    Returns:
        String formatted for OCRMyPDF language parameter
    """
    return '+'.join(tesseract_languages) if tesseract_languages else ''


def validate_language_codes(
    languages: List[str], 
    target_format: str = 'tesseract',
    logger: Optional[logging.Logger] = None
) -> List[str]:
    """
    Validate language codes against a specific OCR engine format.
    
    Args:
        languages: List of language codes to validate
        target_format: Target OCR engine format ('tesseract', 'easyocr')
        logger: Optional logger for validation messages
        
    Returns:
        List of valid language codes for the target format
    """
    if not languages:
        return []
    
    if logger is None:
        logger = logging.getLogger(__name__)
    
    if target_format.lower() == 'tesseract':
        valid_codes = TESSERACT_CODES
    elif target_format.lower() == 'easyocr':
        valid_codes = EASYOCR_CODES
    else:
        logger.warning(f"Unknown target format '{target_format}', defaulting to Tesseract")
        valid_codes = TESSERACT_CODES
    
    valid_languages = []
    for lang in languages:
        lang = lang.lower().strip()
        if lang in valid_codes:
            valid_languages.append(lang)
        else:
            logger.warning(f"Invalid {target_format} language code: '{lang}'")
    
    return valid_languages


def get_supported_languages(engine: str = 'tesseract') -> Set[str]:
    """
    Get supported language codes for a specific OCR engine.
    
    Args:
        engine: OCR engine name ('tesseract', 'easyocr')
        
    Returns:
        Set of supported language codes
    """
    if engine.lower() == 'tesseract':
        return TESSERACT_CODES.copy()
    elif engine.lower() == 'easyocr':
        return EASYOCR_CODES.copy()
    else:
        return set()