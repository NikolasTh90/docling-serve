import logging
import tempfile
import base64
from io import BytesIO
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class AIVisionMiddleware:
    """Middleware for AI Vision OCR using Ollama vision models."""
    
    def __init__(self, settings):
        self.settings = settings
        self.enabled = settings.enabled if settings else False
        self.ollama_client = None
        
        if self.enabled:
            try:
                import ollama
                self.ollama_client = ollama.Client(host=settings.ollama_host)
                logger.info(f"AI Vision middleware initialized with model: {settings.model_name}")
            except ImportError:
                logger.error("Ollama package not available for AI Vision")
                self.enabled = False
            except Exception as e:
                logger.error(f"Failed to initialize Ollama client: {e}")
                self.enabled = False

    def is_supported_file(self, filename: str) -> bool:
        """Check if file extension is supported by AI Vision."""
        if not self.settings:
            return False
        file_ext = Path(filename).suffix.lower()
        return file_ext in self.settings.supported_extensions

    def convert_pdf_to_images(self, pdf_stream: BytesIO, filename: str) -> List:
        """Convert PDF pages to images for vision processing."""
        try:
            import pdf2image
            from PIL import Image
            
            # Reset stream position
            pdf_stream.seek(0)
            
            # Convert PDF to images
            images = pdf2image.convert_from_bytes(
                pdf_stream.getvalue(),
                dpi=200,  # Good balance between quality and size
                fmt='PIL'
            )
            
            # Resize images if they're too large
            processed_images = []
            for img in images:
                if max(img.size) > self.settings.max_image_size:
                    # Calculate new size maintaining aspect ratio
                    ratio = self.settings.max_image_size / max(img.size)
                    new_size = tuple(int(dim * ratio) for dim in img.size)
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                processed_images.append(img)
            
            logger.info(f"Converted {len(processed_images)} pages from {filename}")
            return processed_images
            
        except ImportError as e:
            logger.error(f"Required packages not available for PDF conversion: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to convert PDF to images for {filename}: {e}")
            raise

    def image_to_base64(self, image) -> str:
        """Convert PIL Image to base64 string."""
        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=self.settings.image_quality)
        img_str = base64.b64encode(buffer.getvalue()).decode()
        return img_str

    def process_image_with_vision(self, image, page_number: int) -> str:
        """Process a single image with the vision model."""
        try:
            # Convert image to base64
            img_b64 = self.image_to_base64(image)
            
            # Prepare the prompt for OCR-like extraction
            prompt = """Please extract all text from this image exactly as it appears, preserving the original formatting, layout, and structure. 

Your task is to act like an OCR scanner and convert this image to markdown text:
- Maintain the exact text content and formatting
- Preserve headings, paragraphs, lists, and tables
- Keep the original reading order (top to bottom, left to right)
- Use proper markdown formatting for headings (# ## ###), lists (- * 1.), tables, etc.
- Include all visible text including headers, footers, page numbers, captions
- Do not add any commentary, explanations, or interpretations
- Output only the extracted text in markdown format

Extract the text now:"""

            # Call the vision model
            response = self.ollama_client.chat(
                model=self.settings.model_name,
                messages=[
                    {
                        'role': 'user',
                        'content': prompt,
                        'images': [img_b64]
                    }
                ],
                options={
                    'temperature': self.settings.temperature,
                    'num_predict': self.settings.max_tokens,
                }
            )
            
            extracted_text = response['message']['content'].strip()
            logger.debug(f"Processed page {page_number} with vision model")
            return extracted_text
            
        except Exception as e:
            logger.error(f"Failed to process page {page_number} with vision model: {e}")
            raise

    def process_document(self, file_stream: BytesIO, filename: str) -> str:
        """Process entire document with AI Vision and return markdown content."""
        if not self.enabled:
            raise RuntimeError("AI Vision middleware is not enabled")
        
        if not self.is_supported_file(filename):
            raise ValueError(f"File type not supported by AI Vision: {filename}")
        
        try:
            logger.info(f"Starting AI Vision processing for {filename}")
            
            # Convert PDF to images
            images = self.convert_pdf_to_images(file_stream, filename)
            
            # Process images in batches
            all_pages_content = []
            total_pages = len(images)
            
            for i in range(0, total_pages, self.settings.pages_per_batch):
                batch_end = min(i + self.settings.pages_per_batch, total_pages)
                batch_images = images[i:batch_end]
                
                logger.info(f"Processing pages {i+1}-{batch_end} of {total_pages}")
                
                # Process each page in the batch
                for j, image in enumerate(batch_images):
                    page_number = i + j + 1
                    
                    # Retry logic for each page
                    for attempt in range(self.settings.max_retries):
                        try:
                            page_content = self.process_image_with_vision(image, page_number)
                            all_pages_content.append(page_content)
                            break
                        except Exception as e:
                            if attempt == self.settings.max_retries - 1:
                                logger.error(f"Failed to process page {page_number} after {self.settings.max_retries} attempts: {e}")
                                all_pages_content.append(f"[Error processing page {page_number}]")
                            else:
                                logger.warning(f"Retry {attempt + 1} for page {page_number}: {e}")
            
            # Combine all pages
            if self.settings.include_page_breaks and len(all_pages_content) > 1:
                combined_content = self.settings.page_break_marker.join(all_pages_content)
            else:
                combined_content = "\n\n".join(all_pages_content)
            
            logger.info(f"AI Vision processing completed for {filename}: {len(all_pages_content)} pages processed")
            return combined_content
            
        except Exception as e:
            logger.error(f"AI Vision processing failed for {filename}: {e}")
            raise

    def validate_environment(self) -> Dict[str, Any]:
        """Validate AI Vision environment and return status information."""
        validation_result = {
            "status": "unknown",
            "issues": [],
            "warnings": [],
            "info": []
        }
        
        if not self.enabled:
            validation_result["status"] = "disabled"
            validation_result["info"].append("AI Vision is disabled in configuration")
            return validation_result
        
        try:
            # Check required packages
            try:
                import pdf2image
                import PIL
                import ollama
                validation_result["info"].append("Required packages (pdf2image, PIL, ollama) are available")
            except ImportError as e:
                missing = str(e).split("'")[1] if "'" in str(e) else "unknown package"
                validation_result["issues"].append(f"Missing required package: {missing}")
                validation_result["status"] = "error"
                return validation_result
            
            # Check Ollama connectivity
            try:
                models = self.ollama_client.list()
                available_models = [model['name'] for model in models.get('models', [])]
                
                if self.settings.model_name in available_models:
                    validation_result["info"].append(f"Vision model '{self.settings.model_name}' is available")
                    validation_result["status"] = "healthy"
                else:
                    validation_result["issues"].append(f"Vision model '{self.settings.model_name}' not found in Ollama")
                    validation_result["status"] = "error"
                    
            except Exception as e:
                validation_result["issues"].append(f"Failed to connect to Ollama: {str(e)[:100]}")
                validation_result["status"] = "error"
                
        except Exception as e:
            validation_result["issues"].append(f"Validation error: {str(e)}")
            validation_result["status"] = "error"
            
        return validation_result