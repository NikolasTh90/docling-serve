import os
from docling_serve.settings import ocrmypdf_settings, arabic_correction_settings

def get_ocrmypdf_config():
    """Get OCRMyPDF middleware configuration (legacy function for backward compatibility)."""
    if ocrmypdf_settings:
        return {
            "enabled": ocrmypdf_settings.enabled,
            "deskew": ocrmypdf_settings.deskew,
            "clean": ocrmypdf_settings.clean,
            "timeout": ocrmypdf_settings.timeout,
        }
    else:
        # Fallback to environment variables
        return {
            "enabled": os.getenv("DOCLING_OCRMYPDF_ENABLED", "false").lower() == "true",
            "deskew": os.getenv("DOCLING_OCRMYPDF_DESKEW", "true").lower() == "true",
            "clean": os.getenv("DOCLING_OCRMYPDF_CLEAN", "true").lower() == "true",
            "timeout": int(os.getenv("DOCLING_OCRMYPDF_TIMEOUT", "300")),
        }

def get_arabic_correction_config():
    """Get Arabic correction configuration for display."""
    if arabic_correction_settings:
        return {
            "enabled": arabic_correction_settings.enabled,
            "host": arabic_correction_settings.ollama_host,
            "model": arabic_correction_settings.model_name,
        }
    else:
        return {
            "enabled": os.getenv("DOCLING_ARABIC_ENABLED", "true").lower() == "true",
            "host": os.getenv("DOCLING_ARABIC_OLLAMA_HOST", "http://localhost:11434"),
            "model": os.getenv("DOCLING_ARABIC_MODEL_NAME", "command-r7b-arabic"),
        }
