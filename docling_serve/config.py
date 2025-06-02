import os
def get_ocrmypdf_config():
    """Get OCRMyPDF middleware configuration."""
    return {
        "enabled": os.getenv("DOCLING_SERVE_ENABLE_OCRMYPDF", "false").lower() == "true"
    }