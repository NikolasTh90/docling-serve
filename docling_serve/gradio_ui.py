import base64
import importlib
import json
import logging
import os
import ssl
import tempfile
import time
from pathlib import Path
from typing import Optional

import certifi
import gradio as gr
import httpx

from docling.datamodel.pipeline_options import (
    PdfBackend,
    PdfPipeline,
    TableFormerMode,
    TableStructureOptions,
)

from docling_serve.helper_functions import _to_list_of_strings
from docling_serve.settings import docling_serve_settings, uvicorn_settings

try:
    from docling_serve.settings import ocrmypdf_settings
except ImportError:
    ocrmypdf_settings = None


# ADD THESE IMPORTS for Arabic correction settings
try:
    from docling_serve.arabic_settings import ArabicCorrectionSettings
    arabic_settings = ArabicCorrectionSettings()
except ImportError:
    # Fallback if Arabic settings not available
    arabic_settings = None

from docling_serve.config import get_ocrmypdf_config


logger = logging.getLogger(__name__)

############################
# Path of static artifacts #
############################

logo_path = "https://raw.githubusercontent.com/docling-project/docling/refs/heads/main/docs/assets/logo.svg"
js_components_url = "https://unpkg.com/@docling/docling-components@0.0.7"
if (
    docling_serve_settings.static_path is not None
    and docling_serve_settings.static_path.is_dir()
):
    logo_path = str(docling_serve_settings.static_path / "logo.svg")
    js_components_url = "/static/docling-components.js"


##############################
# Head JS for web components #
##############################
head = f"""
    <script src="{js_components_url}" type="module"></script>
"""

#################
# CSS and theme #
#################

css = """
#logo {
    border-style: none;
    background: none;
    box-shadow: none;
    min-width: 80px;
}
#dark_mode_column {
    display: flex;
    align-content: flex-end;
}
#title {
    text-align: left;
    display:block;
    height: auto;
    padding-top: 5px;
    line-height: 0;
}
.title-text h1 > p, .title-text p {
    margin-top: 0px !important;
    margin-bottom: 2px !important;
}
#custom-container {
    border: 0.909091px solid;
    padding: 10px;
    border-radius: 4px;
}
#custom-container h4 {
    font-size: 14px;
}
#file_input_zone {
    height: 140px;
}

docling-img {
    gap: 1rem;
}

docling-img::part(page) {
    box-shadow: 0 0.5rem 1rem 0 rgba(0, 0, 0, 0.2);
}
"""

theme = gr.themes.Default(
    text_size="md",
    spacing_size="md",
    font=[
        gr.themes.GoogleFont("Red Hat Display"),
        "ui-sans-serif",
        "system-ui",
        "sans-serif",
    ],
    font_mono=[
        gr.themes.GoogleFont("Red Hat Mono"),
        "ui-monospace",
        "Consolas",
        "monospace",
    ],
)

#############
# Variables #
#############

gradio_output_dir = None  # Will be set by FastAPI when mounted
file_output_path = None  # Will be set when a new file is generated

#############
# Functions #
#############


def get_api_endpoint() -> str:
    protocol = "http"
    if uvicorn_settings.ssl_keyfile is not None:
        protocol = "https"
    return f"{protocol}://{docling_serve_settings.api_host}:{uvicorn_settings.port}"


def get_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    kube_sa_ca_cert_path = Path(
        "/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"
    )
    if (
        uvicorn_settings.ssl_keyfile is not None
        and ".svc." in docling_serve_settings.api_host
        and kube_sa_ca_cert_path.exists()
    ):
        ctx.load_verify_locations(cafile=kube_sa_ca_cert_path)
    return ctx


def health_check():
    response = httpx.get(f"{get_api_endpoint()}/health")
    if response.status_code == 200:
        return "Healthy"
    return "Unhealthy"
def check_arabic_correction_status():
    """Check if Arabic correction service is available using environment variables."""
    try:
        # Get configuration from environment variables with fallbacks
        if arabic_settings:
            # Use the settings class if available
            ollama_host = arabic_settings.ollama_host
            model_name = arabic_settings.model_name
            enabled = arabic_settings.enabled
        else:
            # Fallback to direct environment variable access
            ollama_host = os.getenv("DOCLING_ARABIC_OLLAMA_HOST", "http://localhost:11434")
            model_name = os.getenv("DOCLING_ARABIC_MODEL_NAME", "command-r7b-arabic")
            enabled = os.getenv("DOCLING_ARABIC_ENABLED", "true").lower() == "true"
        
        # If Arabic correction is disabled via config, return disabled status
        if not enabled:
            return "<small style='color: gray;'>⚪ Arabic correction: Disabled</small>"
        
        # Try to connect to Ollama using configured host
        import ollama
        client = ollama.Client(host=ollama_host)
        
        # Check if Ollama service is reachable
        models = client.list()
        model_names = [model['name'] for model in models.get('models', [])]
        
        # Check if the configured Arabic model is available
        if model_name in model_names:
            return f"<small style='color: green;'>✓ Arabic correction: Available ({model_name})</small>"
        else:
            return f"<small style='color: orange;'>⚠ Arabic correction: Model '{model_name}' not found</small>"
            
    except ImportError:
        return "<small style='color: red;'>✗ Arabic correction: Ollama package not installed</small>"
    except Exception as e:
        # Log the specific error for debugging
        logger.debug(f"Arabic correction status check failed: {e}")
        ollama_host = os.getenv("DOCLING_ARABIC_OLLAMA_HOST", "http://localhost:11434")
        return f"<small style='color: red;'>✗ Arabic correction: Cannot connect to {ollama_host}</small>"

def get_arabic_correction_config():
    """Get Arabic correction configuration for display."""
    if arabic_settings:
        return {
            "enabled": arabic_settings.enabled,
            "host": arabic_settings.ollama_host,
            "model": arabic_settings.model_name,
        }
    else:
        return {
            "enabled": os.getenv("DOCLING_ARABIC_ENABLED", "true").lower() == "true",
            "host": os.getenv("DOCLING_ARABIC_OLLAMA_HOST", "http://localhost:11434"),
            "model": os.getenv("DOCLING_ARABIC_MODEL_NAME", "command-r7b-arabic"),
        }
    
def validate_arabic_correction_environment():
    """Validate Arabic correction environment configuration."""
    logger.debug("→ Enter validate_arabic_correction_environment")
    issues = []
    warnings = []
    config = get_arabic_correction_config()
    logger.debug("Config loaded: %s", config)

    # If disabled, skip all checks
    if not config["enabled"]:
        logger.debug("Arabic correction is disabled via configuration")
        return {
            "issues": [],
            "warnings": ["Arabic correction is disabled via configuration"],
            "status": "disabled",
        }

    # Check that required packages are installed
    try:
        import ollama
        import langdetect
        logger.debug("Required packages (ollama, langdetect) imported successfully")
    except ImportError as e:
        missing = str(e).split("'")[1] if "'" in str(e) else "unknown package"
        issues.append(f"Missing required package: {missing}")
        logger.debug("ImportError: %s", e)
        return {"issues": issues, "warnings": warnings, "status": "error"}

    # Check Ollama connectivity and model availability
    try:
        import requests
        health_url = f"{config['host']}/api/tags"
        logger.debug("Pinging Ollama health endpoint: %s", health_url)
        resp = requests.get(health_url, timeout=10)
        logger.debug("Health check response code: %s", resp.status_code)

        if resp.status_code != 200:
            issues.append(f"Ollama service at {config['host']} returned {resp.status_code}")
            logger.debug("Non-200 health check, aborting model list check")
        else:
            try:
                client = ollama.Client(host=config["host"])
                raw = client.list()
                logger.debug("Raw client.list() output: %r", raw)

                # normalize into a list of model-entry objects
                if isinstance(raw, dict) and "models" in raw:
                    entries = raw["models"]
                elif hasattr(raw, "models"):
                    entries = raw.models
                elif isinstance(raw, tuple) and len(raw) == 2 and raw[0] == "models":
                    entries = raw[1]
                else:
                    entries = raw
                logger.debug("Normalized entries: %r", entries)

                available_models = []
                for e in entries:
                    # first look for `.model`, then `.name`, else fall back to str()
                    name = getattr(e, "model", None) or getattr(e, "name", None) or str(e)
                    available_models.append(name)
                logger.debug("Available models list: %r", available_models)

                present = config["model"] in available_models
                logger.debug("Is configured model '%s' present? %s", config["model"], present)
                if not present:
                    issues.append(f"Required model '{config['model']}' not found in Ollama")
                    for m in available_models:
                        logger.info("Ollama available model: %s", m)
                else:
                    # quick smoke-test of the model
                    try:
                        test = client.chat(
                            model=config["model"],
                            messages=[{"role": "user", "content": "test"}],
                            options={"max_tokens": 1},
                        )
                        logger.debug("Model smoke-test result: %r", test)
                        if not test:
                            warnings.append(f"Model '{config['model']}' loaded but did not respond")
                    except Exception as e:
                        warnings.append(f"Model '{config['model']}' test failed: {str(e)[:100]}")
                        logger.debug("Exception during smoke-test: %s", e)
            except Exception as e:
                issues.append(f"Failed to check models in Ollama: {str(e)[:100]}")
                logger.debug("Exception listing models: %s", e)

    except requests.exceptions.Timeout:
        issues.append(f"Timeout connecting to Ollama at {config['host']} (>10s)")
        logger.debug("Requests Timeout connecting to %s", config['host'])
    except requests.exceptions.ConnectionError:
        issues.append(f"Cannot connect to Ollama at {config['host']} - is Ollama running?")
        logger.debug("ConnectionError connecting to %s", config['host'])
    except Exception as e:
        issues.append(f"Error checking Ollama connectivity: {str(e)[:100]}")
        logger.debug("Unexpected exception during Ollama connectivity: %s", e)

    # Check that langdetect can detect Arabic
    try:
        from langdetect import detect
        detected = detect("هذا نص تجريبي")
        logger.debug("langdetect.detect returned: %s", detected)
        if detected != "ar":
            warnings.append(f"Language detection test failed (detected: {detected})")
    except Exception as e:
        warnings.append(f"Language detection test failed: {str(e)[:50]}")
        logger.debug("Exception during language detect test: %s", e)

    # Derive overall status
    status = "error" if issues else "warning" if warnings else "healthy"
    logger.debug("Final status: %s; issues: %s; warnings: %s", status, issues, warnings)

    return {"issues": issues, "warnings": warnings, "status": status}

def get_arabic_correction_status_with_validation():
    """Get Arabic correction status with detailed validation."""
    validation_result = validate_arabic_correction_environment()
    config = get_arabic_correction_config()
    
    if validation_result["status"] == "disabled":
        return "<small style='color: gray;'>⚪ Arabic correction: Disabled in configuration</small>"
    
    elif validation_result["status"] == "error":
        error_details = "; ".join(validation_result["issues"][:2])  # Show first 2 issues
        return f"<small style='color: red;'>✗ Arabic correction: {error_details}</small>"
    
    elif validation_result["status"] == "warning":
        warning_details = "; ".join(validation_result["warnings"][:1])  # Show first warning
        return f"<small style='color: orange;'>⚠ Arabic correction: {warning_details}</small>"
    
    else:
        return f"<small style='color: green;'>✓ Arabic correction: Ready ({config['model']})</small>"

def get_detailed_arabic_correction_info():
    """Get detailed Arabic correction information for the UI."""
    validation_result = validate_arabic_correction_environment()
    config = get_arabic_correction_config()
    
    info_html = f"""
    <div style='font-size: 12px; margin-top: 10px;'>
    <strong>Arabic OCR Correction Status:</strong><br>
    <strong>Host:</strong> {config['host']}<br>
    <strong>Model:</strong> {config['model']}<br>
    <strong>Enabled:</strong> {config['enabled']}<br>
    """
    
    if validation_result["issues"]:
        info_html += "<br><strong style='color: red;'>Issues:</strong><br>"
        for issue in validation_result["issues"]:
            info_html += f"• {issue}<br>"
    
    if validation_result["warnings"]:
        info_html += "<br><strong style='color: orange;'>Warnings:</strong><br>"
        for warning in validation_result["warnings"]:
            info_html += f"• {warning}<br>"
    
    if validation_result["status"] == "healthy":
        info_html += "<br><strong style='color: green;'>✓ All checks passed</strong><br>"
    
    # Add configuration help
    info_html += """
    <br><strong>Configuration:</strong><br>
    Set these environment variables:<br>
    • <code>DOCLING_ARABIC_ENABLED=true</code><br>
    • <code>DOCLING_ARABIC_OLLAMA_HOST=http://localhost:11434</code><br>
    • <code>DOCLING_ARABIC_MODEL_NAME=command-r7b-arabic</code><br>
    </div>
    """
    
    return info_html

def test_arabic_correction_connection():
    """Test Arabic correction connection and return user-friendly message."""
    try:
        validation_result = validate_arabic_correction_environment()
        
        if validation_result["status"] == "healthy":
            return "✅ Arabic correction is working properly!"
        elif validation_result["status"] == "disabled":
            return "ℹ️ Arabic correction is disabled in configuration"
        elif validation_result["status"] == "warning":
            warnings = "; ".join(validation_result["warnings"])
            return f"⚠️ Arabic correction has warnings: {warnings}"
        else:
            issues = "; ".join(validation_result["issues"])
            return f"❌ Arabic correction has issues: {issues}"
            
    except Exception as e:
        return f"❌ Error testing Arabic correction: {str(e)}"

def get_ocrmypdf_status_with_validation():
    """Get OCRMyPDF status with validation."""
    try:
        import ocrmypdf
        
        # Use new settings system if available, fallback to legacy config
        if ocrmypdf_settings:
            enabled = ocrmypdf_settings.enabled
        else:
            config = get_ocrmypdf_config()
            enabled = config["enabled"]
            
        if enabled:
            return '<span style="color: green;">✅ OCRMyPDF Available & Enabled</span>'
        else:
            return '<span style="color: orange;">⚠️ OCRMyPDF Available but Disabled</span>'
    except ImportError:
        return '<span style="color: red;">❌ OCRMyPDF Not Installed</span>'
    except Exception as e:
        return f'<span style="color: red;">❌ OCRMyPDF Error: {str(e)}</span>'

def get_detailed_ocrmypdf_info():
    """Get detailed OCRMyPDF configuration information."""
    try:
        import ocrmypdf
        
        if ocrmypdf_settings:
            info_html = f"""
            <div style="font-size: 12px; color: #666;">
                <strong>OCRMyPDF Configuration:</strong><br/>
                • Enabled: {'Yes' if ocrmypdf_settings.enabled else 'No'}<br/>
                • Version: {ocrmypdf.__version__}<br/>
                • Deskew: {ocrmypdf_settings.deskew}<br/>
                • Clean: {ocrmypdf_settings.clean}<br/>
                • Oversample: {ocrmypdf_settings.oversample}<br/>
                • Timeout: {ocrmypdf_settings.timeout}s<br/>
                • Max File Size: {ocrmypdf_settings.max_file_size_mb}MB<br/>
                • Fail on Error: {ocrmypdf_settings.fail_on_error}<br/>
                • Fallback on Failure: {ocrmypdf_settings.fallback_on_failure}<br/>
                <br/>
                <strong>Environment Variables:</strong><br/>
                • DOCLING_OCRMYPDF_ENABLED: {ocrmypdf_settings.enabled}<br/>
                • DOCLING_OCRMYPDF_DESKEW: {ocrmypdf_settings.deskew}<br/>
                • DOCLING_OCRMYPDF_CLEAN: {ocrmypdf_settings.clean}<br/>
                • DOCLING_OCRMYPDF_TIMEOUT: {ocrmypdf_settings.timeout}<br/>
                • DOCLING_OCRMYPDF_MAX_FILE_SIZE_MB: {ocrmypdf_settings.max_file_size_mb}<br/>
            </div>
            """
        else:
            # Fallback to legacy config
            config = get_ocrmypdf_config()
            info_html = f"""
            <div style="font-size: 12px; color: #666;">
                <strong>OCRMyPDF Configuration (Legacy):</strong><br/>
                • Enabled: {'Yes' if config['enabled'] else 'No'}<br/>
                • Version: {ocrmypdf.__version__}<br/>
                • Deskew: {'Yes' if config.get('deskew', True) else 'No'}<br/>
                • Clean: {'Yes' if config.get('clean', True) else 'No'}<br/>
                • Timeout: {config.get('timeout', 300)}s<br/>
                • Mode: Accuracy-oriented preprocessing<br/>
                <br/>
                <em>Note: Using legacy configuration. Consider upgrading to settings-based config.</em>
            </div>
            """
        return info_html
    except ImportError:
        return '<div style="color: red;">OCRMyPDF package not installed. Install with: pip install ocrmypdf</div>'
    except Exception as e:
        return f'<div style="color: red;">Error: {str(e)}</div>'

def test_ocrmypdf_connection():
    """Test OCRMyPDF functionality."""
    try:
        import ocrmypdf
        import tempfile
        from pathlib import Path
        
        # Create a minimal test to verify OCRMyPDF works
        test_result = "✅ OCRMyPDF is properly installed and functional"
        
        # Check configuration status
        if ocrmypdf_settings:
            if not ocrmypdf_settings.enabled:
                test_result += "\n⚠️  Note: OCRMyPDF is disabled in configuration (DOCLING_OCRMYPDF_ENABLED=false)"
            else:
                test_result += f"\n✅ OCRMyPDF is enabled with timeout: {ocrmypdf_settings.timeout}s"
                test_result += f"\n✅ Max file size: {ocrmypdf_settings.max_file_size_mb}MB"
        else:
            # Fallback to legacy config
            config = get_ocrmypdf_config()
            if not config["enabled"]:
                test_result += "\n⚠️  Note: OCRMyPDF is disabled in legacy configuration"
            else:
                test_result += "\n✅ OCRMyPDF is enabled (legacy configuration)"
            
        return test_result
        
    except ImportError:
        return "❌ OCRMyPDF package not installed. Please install with: pip install ocrmypdf"
    except Exception as e:
        return f"❌ OCRMyPDF test failed: {str(e)}"

def log_ocrmypdf_startup_status():
    """Log OCRMyPDF status on startup for debugging."""
    try:
        import logging
        logger = logging.getLogger(__name__)
        
        if ocrmypdf_settings:
            logger.info(f"OCRMyPDF Preprocessing - Enabled: {ocrmypdf_settings.enabled}")
            logger.info(f"OCRMyPDF Preprocessing - Timeout: {ocrmypdf_settings.timeout}s")
            logger.info(f"OCRMyPDF Preprocessing - Max File Size: {ocrmypdf_settings.max_file_size_mb}MB")
            logger.info(f"OCRMyPDF Preprocessing - Fail on Error: {ocrmypdf_settings.fail_on_error}")
            logger.info(f"OCRMyPDF Preprocessing - Fallback on Failure: {ocrmypdf_settings.fallback_on_failure}")
        else:
            config = get_ocrmypdf_config()
            logger.info(f"OCRMyPDF Preprocessing (Legacy) - Enabled: {config['enabled']}")
            logger.warning("OCRMyPDF using legacy configuration. Consider upgrading to settings-based config.")
            
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error checking OCRMyPDF status on startup: {e}")

def validate_ocrmypdf_environment():
    """Validate OCRMyPDF environment and return status information."""
    validation_result = {
        "status": "unknown",
        "issues": [],
        "warnings": [],
        "info": []
    }
    
    try:
        # Check if ocrmypdf package is installed
        import ocrmypdf
        validation_result["info"].append(f"OCRMyPDF version {ocrmypdf.__version__} is installed")
        
        # Check settings availability
        if ocrmypdf_settings:
            validation_result["status"] = "enabled" if ocrmypdf_settings.enabled else "disabled"
            validation_result["info"].append("Using modern settings-based configuration")
            
            # Validate settings
            if ocrmypdf_settings.timeout <= 0:
                validation_result["issues"].append("Invalid timeout value - must be positive")
            
            if ocrmypdf_settings.max_file_size_mb <= 0:
                validation_result["issues"].append("Invalid max file size - must be positive")
                
            if not ocrmypdf_settings.supported_extensions:
                validation_result["issues"].append("No supported file extensions configured")
                
            # Warnings for common misconfigurations
            if ocrmypdf_settings.timeout < 60:
                validation_result["warnings"].append("Timeout is very short - may cause processing failures")
                
            if ocrmypdf_settings.max_file_size_mb > 500:
                validation_result["warnings"].append("Very large max file size - may cause memory issues")
                
        else:
            # Fallback to legacy config
            config = get_ocrmypdf_config()
            validation_result["status"] = "enabled" if config["enabled"] else "disabled"
            validation_result["warnings"].append("Using legacy configuration - consider upgrading")
            
    except ImportError:
        validation_result["status"] = "not_installed"
        validation_result["issues"].append("OCRMyPDF package not installed")
    except Exception as e:
        validation_result["status"] = "error"
        validation_result["issues"].append(f"Configuration error: {str(e)}")
        
    return validation_result

def set_options_visibility(x):
    return gr.Accordion("Options", open=x)


def set_outputs_visibility_direct(x, y):
    content = gr.Row(visible=x)
    file = gr.Row(visible=y)
    return content, file


def set_task_id_visibility(x):
    task_id_row = gr.Row(visible=x)
    return task_id_row


def set_outputs_visibility_process(x):
    content = gr.Row(visible=not x)
    file = gr.Row(visible=x)
    return content, file


def set_download_button_label(label_text: gr.State):
    return gr.DownloadButton(label=str(label_text), scale=1)


def clear_outputs():
    task_id_rendered = ""
    markdown_content = ""
    json_content = ""
    json_rendered_content = ""
    html_content = ""
    text_content = ""
    doctags_content = ""

    return (
        task_id_rendered,
        markdown_content,
        markdown_content,
        json_content,
        json_rendered_content,
        html_content,
        html_content,
        text_content,
        doctags_content,
    )


def clear_url_input():
    return ""


def clear_file_input():
    return None


def auto_set_return_as_file(
    url_input_value: str,
    file_input_value: Optional[list[str]],
    image_export_mode_value: str,
):
    # If more than one input source is provided, return as file
    if (
        (len(url_input_value.split(",")) > 1)
        or (file_input_value and len(file_input_value) > 1)
        or (image_export_mode_value == "referenced")
    ):
        return True
    else:
        return False


def change_ocr_lang(ocr_engine):
    if ocr_engine == "easyocr":
        return "en,fr,de,es"
    elif ocr_engine == "tesseract_cli":
        return "eng,fra,deu,spa"
    elif ocr_engine == "tesseract":
        return "eng,fra,deu,spa"
    elif ocr_engine == "rapidocr":
        return "english,chinese"


def wait_task_finish(task_id: str, return_as_file: bool):
    conversion_sucess = False
    task_finished = False
    task_status = ""
    ssl_ctx = get_ssl_context()
    while not task_finished:
        try:
            response = httpx.get(
                f"{get_api_endpoint()}/v1alpha/status/poll/{task_id}?wait=5",
                verify=ssl_ctx,
                timeout=15,
            )
            task_status = response.json()["task_status"]
            if task_status == "success":
                conversion_sucess = True
                task_finished = True

            if task_status in ("failure", "revoked"):
                conversion_sucess = False
                task_finished = True
                raise RuntimeError(f"Task failed with status {task_status!r}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Error processing file(s): {e}")
            conversion_sucess = False
            task_finished = True
            raise gr.Error(f"Error processing file(s): {e}", print_exception=False)

    if conversion_sucess:
        try:
            response = httpx.get(
                f"{get_api_endpoint()}/v1alpha/result/{task_id}",
                timeout=15,
                verify=ssl_ctx,
            )
            output = response_to_output(response, return_as_file)
            return output
        except Exception as e:
            logger.error(f"Error getting task result: {e}")

    raise gr.Error(
        f"Error getting task result, conversion finished with status: {task_status}"
    )


def process_url(
    input_sources,
    to_formats,
    image_export_mode,
    pipeline,
    ocr,
    force_ocr,
    ocr_engine,
    ocr_lang,
    pdf_backend,
    table_mode,
    abort_on_error,
    return_as_file,
    do_code_enrichment,
    do_formula_enrichment,
    do_picture_classification,
    do_picture_description,
    enable_arabic_correction=False,
    enable_ocrmypdf_preprocessing=False,
    ocrmypdf_deskew=True,
    ocrmypdf_clean=True,
):
    
    # Check if Arabic correction is globally enabled via config
    config = get_arabic_correction_config()
    final_arabic_correction = enable_arabic_correction and config["enabled"]
    parameters = {
        "http_sources": [{"url": source} for source in input_sources.split(",")],
        "options": {
            "to_formats": to_formats,
            "image_export_mode": image_export_mode,
            "pipeline": pipeline,
            "ocr": ocr,
            "force_ocr": force_ocr,
            "ocr_engine": ocr_engine,
            "ocr_lang": _to_list_of_strings(ocr_lang),
            "pdf_backend": pdf_backend,
            "table_mode": table_mode,
            "abort_on_error": abort_on_error,
            "return_as_file": return_as_file,
            "do_code_enrichment": do_code_enrichment,
            "do_formula_enrichment": do_formula_enrichment,
            "do_picture_classification": do_picture_classification,
            "do_picture_description": do_picture_description,
            "enable_arabic_correction": final_arabic_correction,
            "enable_ocrmypdf_preprocessing": enable_ocrmypdf_preprocessing,
            "ocrmypdf_deskew": ocrmypdf_deskew,
            "ocrmypdf_clean": ocrmypdf_clean,
        },
    }
    if (
        not parameters["http_sources"]
        or len(parameters["http_sources"]) == 0
        or parameters["http_sources"][0]["url"] == ""
    ):
        logger.error("No input sources provided.")
        raise gr.Error("No input sources provided.", print_exception=False)
    try:
        ssl_ctx = get_ssl_context()
        response = httpx.post(
            f"{get_api_endpoint()}/v1alpha/convert/source/async",
            json=parameters,
            verify=ssl_ctx,
            timeout=60,
        )
    except Exception as e:
        logger.error(f"Error processing URL: {e}")
        raise gr.Error(f"Error processing URL: {e}", print_exception=False)
    if response.status_code != 200:
        data = response.json()
        error_message = data.get("detail", "An unknown error occurred.")
        logger.error(f"Error processing file: {error_message}")
        raise gr.Error(f"Error processing file: {error_message}", print_exception=False)

    task_id_rendered = response.json()["task_id"]
    return task_id_rendered


def file_to_base64(file):
    with open(file.name, "rb") as f:
        encoded_string = base64.b64encode(f.read()).decode("utf-8")
    return encoded_string


def process_file(
    files,
    to_formats,
    image_export_mode,
    pipeline,
    ocr,
    force_ocr,
    ocr_engine,
    ocr_lang,
    pdf_backend,
    table_mode,
    abort_on_error,
    return_as_file,
    do_code_enrichment,
    do_formula_enrichment,
    do_picture_classification,
    do_picture_description,
    enable_arabic_correction=False,
    enable_ocrmypdf_preprocessing=False,
    ocrmypdf_deskew=True,
    ocrmypdf_clean=True,

):
    if not files or len(files) == 0:
        logger.error("No files provided.")
        raise gr.Error("No files provided.", print_exception=False)
    files_data = [
        {"base64_string": file_to_base64(file), "filename": file.name} for file in files
    ]

    # Check if Arabic correction is globally enabled via config
    config = get_arabic_correction_config()
    final_arabic_correction = enable_arabic_correction and config["enabled"]

    ocrmypdf_config = get_ocrmypdf_config()
    final_ocrmypdf_preprocessing = enable_ocrmypdf_preprocessing and ocrmypdf_config["enabled"]

    parameters = {
        "file_sources": files_data,
        "options": {
            "to_formats": to_formats,
            "image_export_mode": image_export_mode,
            "pipeline": pipeline,
            "ocr": ocr,
            "force_ocr": force_ocr,
            "ocr_engine": ocr_engine,
            "ocr_lang": _to_list_of_strings(ocr_lang),
            "pdf_backend": pdf_backend,
            "table_mode": table_mode,
            "abort_on_error": abort_on_error,
            "return_as_file": return_as_file,
            "do_code_enrichment": do_code_enrichment,
            "do_formula_enrichment": do_formula_enrichment,
            "do_picture_classification": do_picture_classification,
            "do_picture_description": do_picture_description,
            "enable_arabic_correction": final_arabic_correction,
            "enable_ocrmypdf_preprocessing": final_ocrmypdf_preprocessing,
            "ocrmypdf_deskew": ocrmypdf_deskew,
            "ocrmypdf_clean": ocrmypdf_clean,
        
        },
    }

    try:
        ssl_ctx = get_ssl_context()
        response = httpx.post(
            f"{get_api_endpoint()}/v1alpha/convert/source/async",
            json=parameters,
            verify=ssl_ctx,
            timeout=60,
        )
    except Exception as e:
        logger.error(f"Error processing file(s): {e}")
        raise gr.Error(f"Error processing file(s): {e}", print_exception=False)
    if response.status_code != 200:
        data = response.json()
        error_message = data.get("detail", "An unknown error occurred.")
        logger.error(f"Error processing file: {error_message}")
        raise gr.Error(f"Error processing file: {error_message}", print_exception=False)

    task_id_rendered = response.json()["task_id"]
    return task_id_rendered


def response_to_output(response, return_as_file):
    markdown_content = ""
    json_content = ""
    json_rendered_content = ""
    html_content = ""
    text_content = ""
    doctags_content = ""
    download_button = gr.DownloadButton(visible=False, label="Download Output", scale=1)
    if return_as_file:
        filename = (
            response.headers.get("Content-Disposition").split("filename=")[1].strip('"')
        )
        tmp_output_dir = Path(tempfile.mkdtemp(dir=gradio_output_dir, prefix="ui_"))
        file_output_path = f"{tmp_output_dir}/{filename}"
        # logger.info(f"Saving file to: {file_output_path}")
        with open(file_output_path, "wb") as f:
            f.write(response.content)
        download_button = gr.DownloadButton(
            visible=True, label=f"Download {filename}", scale=1, value=file_output_path
        )
    else:
        full_content = response.json()
        markdown_content = full_content.get("document").get("md_content")
        json_content = json.dumps(
            full_content.get("document").get("json_content"), indent=2
        )
        # Embed document JSON and trigger load at client via an image.
        json_rendered_content = f"""
            <docling-img id="dclimg" pagenumbers><docling-tooltip></docling-tooltip></docling-img>
            <script id="dcljson" type="application/json" onload="document.getElementById('dclimg').src = JSON.parse(document.getElementById('dcljson').textContent);">{json_content}</script>
            <img src onerror="document.getElementById('dclimg').src = JSON.parse(document.getElementById('dcljson').textContent);" />
            """
        html_content = full_content.get("document").get("html_content")
        text_content = full_content.get("document").get("text_content")
        doctags_content = full_content.get("document").get("doctags_content")
    return (
        markdown_content,
        markdown_content,
        json_content,
        json_rendered_content,
        html_content,
        html_content,
        text_content,
        doctags_content,
        download_button,
    )


def log_arabic_correction_startup_status():
    """Log Arabic correction status on startup for debugging."""
    try:
        validation_result = validate_arabic_correction_environment()
        config = get_arabic_correction_config()
        
        logger.info(f"Arabic OCR Correction - Enabled: {config['enabled']}")
        logger.info(f"Arabic OCR Correction - Host: {config['host']}")
        logger.info(f"Arabic OCR Correction - Model: {config['model']}")
        logger.info(f"Arabic OCR Correction - Status: {validation_result['status']}")
        
        if validation_result['issues']:
            for issue in validation_result['issues']:
                logger.warning(f"Arabic OCR Correction Issue: {issue}")
                
        if validation_result['warnings']:
            for warning in validation_result['warnings']:
                logger.info(f"Arabic OCR Correction Warning: {warning}")
                
    except Exception as e:
        logger.error(f"Error checking Arabic correction status on startup: {e}")

# Call this at the end of the file, before the UI definition
log_arabic_correction_startup_status()


############
# UI Setup #
############

with gr.Blocks(
    head=head,
    css=css,
    theme=theme,
    title="Docling Serve",
    delete_cache=(3600, 3600),  # Delete all files older than 1 hour every hour
) as ui:
    # Constants stored in states to be able to pass them as inputs to functions
    processing_text = gr.State("Processing your document(s), please wait...")
    true_bool = gr.State(True)
    false_bool = gr.State(False)

    # Banner
    with gr.Row(elem_id="check_health"):
        # Logo
        with gr.Column(scale=1, min_width=90):
            try:
                gr.Image(
                    logo_path,
                    height=80,
                    width=80,
                    show_download_button=False,
                    show_label=False,
                    show_fullscreen_button=False,
                    container=False,
                    elem_id="logo",
                    scale=0,
                )
            except Exception:
                logger.warning("Logo not found.")

        # Title
        with gr.Column(scale=1, min_width=200):
            gr.Markdown(
                f"# Docling Serve \n(docling version: "
                f"{importlib.metadata.version('docling')})",
                elem_id="title",
                elem_classes=["title-text"],
            )
        # Dark mode button
        with gr.Column(scale=16, elem_id="dark_mode_column"):
            dark_mode_btn = gr.Button("Dark/Light Mode", scale=0)
            dark_mode_btn.click(
                None,
                None,
                None,
                js="""() => {
                    if (document.querySelectorAll('.dark').length) {
                        document.querySelectorAll('.dark').forEach(
                        el => el.classList.remove('dark')
                        );
                    } else {
                        document.querySelector('body').classList.add('dark');
                    }
                }""",
                show_api=False,
            )

    # URL Processing Tab
    with gr.Tab("Convert URL"):
        with gr.Row():
            with gr.Column(scale=4):
                url_input = gr.Textbox(
                    label="URL Input Source",
                    placeholder="https://arxiv.org/pdf/2501.17887",
                )
            with gr.Column(scale=1):
                url_process_btn = gr.Button("Process URL", scale=1)
                url_reset_btn = gr.Button("Reset", scale=1)

    # File Processing Tab
    with gr.Tab("Convert File"):
        with gr.Row():
            with gr.Column(scale=4):
                file_input = gr.File(
                    elem_id="file_input_zone",
                    label="Upload File",
                    file_types=[
                        ".pdf",
                        ".docx",
                        ".pptx",
                        ".html",
                        ".xlsx",
                        ".json",
                        ".asciidoc",
                        ".txt",
                        ".md",
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".gif",
                    ],
                    file_count="multiple",
                    scale=4,
                )
            with gr.Column(scale=1):
                file_process_btn = gr.Button("Process File", scale=1)
                file_reset_btn = gr.Button("Reset", scale=1)

    # Options
    with gr.Accordion("Options") as options:
        with gr.Row():
            with gr.Column(scale=1):
                to_formats = gr.CheckboxGroup(
                    [
                        ("Docling (JSON)", "json"),
                        ("Markdown", "md"),
                        ("HTML", "html"),
                        ("Plain Text", "text"),
                        ("Doc Tags", "doctags"),
                    ],
                    label="To Formats",
                    value=["json", "md"],
                )
            with gr.Column(scale=1):
                image_export_mode = gr.Radio(
                    [
                        ("Embedded", "embedded"),
                        ("Placeholder", "placeholder"),
                        ("Referenced", "referenced"),
                    ],
                    label="Image Export Mode",
                    value="embedded",
                )
        with gr.Row():
            with gr.Column(scale=1, min_width=200):
                pipeline = gr.Radio(
                    [(v.value.capitalize(), v.value) for v in PdfPipeline],
                    label="Pipeline type",
                    value=PdfPipeline.STANDARD.value,
                )
        with gr.Row():
            with gr.Column(scale=1, min_width=200):
                ocr = gr.Checkbox(label="Enable OCR", value=True)
                force_ocr = gr.Checkbox(label="Force OCR", value=False)
            with gr.Column(scale=1):
                ocr_engine = gr.Radio(
                    [
                        ("EasyOCR", "easyocr"),
                        ("Tesseract", "tesseract"),
                        ("RapidOCR", "rapidocr"),
                    ],
                    label="OCR Engine",
                    value="easyocr",
                )
            with gr.Column(scale=1, min_width=200):
                ocr_lang = gr.Textbox(
                    label="OCR Language (beware of the format)", value="en,fr,de,es"
                )
            ocr_engine.change(change_ocr_lang, inputs=[ocr_engine], outputs=[ocr_lang])
        with gr.Row():
            with gr.Column(scale=4):
                pdf_backend = gr.Radio(
                    [v.value for v in PdfBackend],
                    label="PDF Backend",
                    value=PdfBackend.DLPARSE_V4.value,
                )
            with gr.Column(scale=2):
                table_mode = gr.Radio(
                    [(v.value.capitalize(), v.value) for v in TableFormerMode],
                    label="Table Mode",
                    value=TableStructureOptions().mode.value,
                )
            with gr.Column(scale=1):
                abort_on_error = gr.Checkbox(label="Abort on Error", value=False)
                return_as_file = gr.Checkbox(label="Return as File", value=False)
        with gr.Row():
            with gr.Column():
                do_code_enrichment = gr.Checkbox(
                    label="Enable code enrichment", value=False
                )
                do_formula_enrichment = gr.Checkbox(
                    label="Enable formula enrichment", value=False
                )
            with gr.Column():
                do_picture_classification = gr.Checkbox(
                    label="Enable picture classification", value=False
                )
                do_picture_description = gr.Checkbox(
                    label="Enable picture description", value=False
                )

        # Enhanced Arabic correction section with validation
        with gr.Row():
            with gr.Column():
                enable_arabic_correction = gr.Checkbox(
                    label="Enable Arabic OCR Correction",
                    value=False,
                    info="Automatically detect and correct Arabic OCR errors using LLM"
                )
            with gr.Column():
                # Dynamic status with validation
                arabic_correction_status = gr.HTML(
                    value=get_arabic_correction_status_with_validation(),
                    visible=True
                )
        
        # Detailed configuration and validation info
        with gr.Accordion("Arabic Correction Configuration & Status", open=False):
            with gr.Row():
                with gr.Column(scale=2):
                    arabic_detailed_info = gr.HTML(
                        value=get_detailed_arabic_correction_info(),
                        visible=True
                    )
                with gr.Column(scale=1):
                    test_arabic_btn = gr.Button(
                        "Test Connection",
                        variant="secondary",
                        scale=1
                    )
                    test_arabic_result = gr.Textbox(
                        label="Test Result",
                        interactive=False,
                        visible=False
                    )

        # OCRMyPDF preprocessing section
        with gr.Row():
            with gr.Column():
                enable_ocrmypdf_preprocessing = gr.Checkbox(
                    label="Enable OCRMyPDF Preprocessing",
                    value=False,
                    info="Preprocess PDFs with OCRMyPDF for improved OCR accuracy"
                )
            with gr.Column():
                # OCRMyPDF status
                ocrmypdf_status = gr.HTML(
                    value=get_ocrmypdf_status_with_validation(),
                    visible=True
                )

        # OCRMyPDF detailed options
        with gr.Accordion("OCRMyPDF Preprocessing Options", open=False):
            with gr.Row():
                with gr.Column():
                    ocrmypdf_deskew = gr.Checkbox(
                        label="Apply Deskewing",
                        value=True,
                        info="Correct rotated pages during preprocessing"
                    )
                    ocrmypdf_clean = gr.Checkbox(
                        label="Apply Page Cleaning", 
                        value=True,
                        info="Remove artifacts and improve image quality"
                    )
                with gr.Column():
                    ocrmypdf_detailed_info = gr.HTML(
                        value=get_detailed_ocrmypdf_info(),
                        visible=True
                    )
                    test_ocrmypdf_btn = gr.Button(
                        "Test OCRMyPDF",
                        variant="secondary",
                        scale=1
                    )
                    test_ocrmypdf_result = gr.Textbox(
                        label="Test Result",
                        interactive=False,
                        visible=False
                    )

    # Task id output
    with gr.Row(visible=False) as task_id_output:
        task_id_rendered = gr.Textbox(label="Task id", interactive=False)

    # Document output
    with gr.Row(visible=False) as content_output:
        with gr.Tab("Docling (JSON)"):
            output_json = gr.Code(language="json", wrap_lines=True, show_label=False)
        with gr.Tab("Docling-Rendered"):
            output_json_rendered = gr.HTML(label="Response")
        with gr.Tab("Markdown"):
            output_markdown = gr.Code(
                language="markdown", wrap_lines=True, show_label=False
            )
        with gr.Tab("Markdown-Rendered"):
            output_markdown_rendered = gr.Markdown(label="Response")
        with gr.Tab("HTML"):
            output_html = gr.Code(language="html", wrap_lines=True, show_label=False)
        with gr.Tab("HTML-Rendered"):
            output_html_rendered = gr.HTML(label="Response")
        with gr.Tab("Text"):
            output_text = gr.Code(wrap_lines=True, show_label=False)
        with gr.Tab("DocTags"):
            output_doctags = gr.Code(wrap_lines=True, show_label=False)

    # File download output
    with gr.Row(visible=False) as file_output:
        download_file_btn = gr.DownloadButton(label="Placeholder", scale=1)

    ##############
    # UI Actions #
    ##############

    # Handle Return as File
    url_input.change(
        auto_set_return_as_file,
        inputs=[url_input, file_input, image_export_mode],
        outputs=[return_as_file],
    )
    file_input.change(
        auto_set_return_as_file,
        inputs=[url_input, file_input, image_export_mode],
        outputs=[return_as_file],
    )
    image_export_mode.change(
        auto_set_return_as_file,
        inputs=[url_input, file_input, image_export_mode],
        outputs=[return_as_file],
    )

    # Arabic correction test button
    test_arabic_btn.click(
        fn=test_arabic_correction_connection,
        inputs=[],
        outputs=[test_arabic_result]
    ).then(
        fn=lambda x: gr.Textbox(visible=True),
        inputs=[test_arabic_result],
        outputs=[test_arabic_result]
    )
    
    # Auto-refresh Arabic status every 30 seconds
    arabic_correction_status.change(
        fn=get_arabic_correction_status_with_validation,
        inputs=[],
        outputs=[arabic_correction_status],
    )

    # OCRMyPDF test button  
    test_ocrmypdf_btn.click(
        fn=test_ocrmypdf_connection,
        inputs=[],
        outputs=[test_ocrmypdf_result]
    ).then(
        fn=lambda x: gr.Textbox(visible=True),
        inputs=[test_ocrmypdf_result], 
        outputs=[test_ocrmypdf_result]
    )

    # URL processing
    url_process_btn.click(
        set_options_visibility, inputs=[false_bool], outputs=[options]
    ).then(
        set_download_button_label, inputs=[processing_text], outputs=[download_file_btn]
    ).then(
        clear_outputs,
        inputs=None,
        outputs=[
            task_id_rendered,
            output_markdown,
            output_markdown_rendered,
            output_json,
            output_json_rendered,
            output_html,
            output_html_rendered,
            output_text,
            output_doctags,
        ],
    ).then(
        set_task_id_visibility,
        inputs=[true_bool],
        outputs=[task_id_output],
    ).then(
        process_url,
        inputs=[
            url_input,
            to_formats,
            image_export_mode,
            pipeline,
            ocr,
            force_ocr,
            ocr_engine,
            ocr_lang,
            pdf_backend,
            table_mode,
            abort_on_error,
            return_as_file,
            do_code_enrichment,
            do_formula_enrichment,
            do_picture_classification,
            do_picture_description,
            enable_arabic_correction,
            enable_ocrmypdf_preprocessing,
            ocrmypdf_deskew,
            ocrmypdf_clean,
            
        ],
        outputs=[
            task_id_rendered,
        ],
    ).then(
        set_outputs_visibility_process,
        inputs=[return_as_file],
        outputs=[content_output, file_output],
    ).then(
        wait_task_finish,
        inputs=[task_id_rendered, return_as_file],
        outputs=[
            output_markdown,
            output_markdown_rendered,
            output_json,
            output_json_rendered,
            output_html,
            output_html_rendered,
            output_text,
            output_doctags,
            download_file_btn,
        ],
    )

    url_reset_btn.click(
        clear_outputs,
        inputs=None,
        outputs=[
            output_markdown,
            output_markdown_rendered,
            output_json,
            output_json_rendered,
            output_html,
            output_html_rendered,
            output_text,
            output_doctags,
        ],
    ).then(set_options_visibility, inputs=[true_bool], outputs=[options]).then(
        set_outputs_visibility_direct,
        inputs=[false_bool, false_bool],
        outputs=[content_output, file_output],
    ).then(set_task_id_visibility, inputs=[false_bool], outputs=[task_id_output]).then(
        clear_url_input, inputs=None, outputs=[url_input]
    )

    # File processing
    file_process_btn.click(
        set_options_visibility, inputs=[false_bool], outputs=[options]
    ).then(
        set_download_button_label, inputs=[processing_text], outputs=[download_file_btn]
    ).then(
        clear_outputs,
        inputs=None,
        outputs=[
            task_id_rendered,
            output_markdown,
            output_markdown_rendered,
            output_json,
            output_json_rendered,
            output_html,
            output_html_rendered,
            output_text,
            output_doctags,
        ],
    ).then(
        set_task_id_visibility,
        inputs=[true_bool],
        outputs=[task_id_output],
    ).then(
        process_file,
        inputs=[
            file_input,
            to_formats,
            image_export_mode,
            pipeline,
            ocr,
            force_ocr,
            ocr_engine,
            ocr_lang,
            pdf_backend,
            table_mode,
            abort_on_error,
            return_as_file,
            do_code_enrichment,
            do_formula_enrichment,
            do_picture_classification,
            do_picture_description,
            enable_arabic_correction,
            enable_ocrmypdf_preprocessing,
            ocrmypdf_deskew,
            ocrmypdf_clean,
        ],
        outputs=[
            task_id_rendered,
        ],
    ).then(
        set_outputs_visibility_process,
        inputs=[return_as_file],
        outputs=[content_output, file_output],
    ).then(
        wait_task_finish,
        inputs=[task_id_rendered, return_as_file],
        outputs=[
            output_markdown,
            output_markdown_rendered,
            output_json,
            output_json_rendered,
            output_html,
            output_html_rendered,
            output_text,
            output_doctags,
            download_file_btn,
        ],
    )

    file_reset_btn.click(
        clear_outputs,
        inputs=None,
        outputs=[
            output_markdown,
            output_markdown_rendered,
            output_json,
            output_json_rendered,
            output_html,
            output_html_rendered,
            output_text,
            output_doctags,
        ],
    ).then(set_options_visibility, inputs=[true_bool], outputs=[options]).then(
        set_outputs_visibility_direct,
        inputs=[false_bool, false_bool],
        outputs=[content_output, file_output],
    ).then(set_task_id_visibility, inputs=[false_bool], outputs=[task_id_output]).then(
        clear_file_input, inputs=None, outputs=[file_input]
    )
