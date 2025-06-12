import time
from typing import Dict, Any, List
from pathlib import Path
from io import BytesIO
import base64
import logging

logger = logging.getLogger(__name__)

class AIVisionMiddleware:
    """Middleware for AI Vision OCR using Ollama vision models."""
    
    def __init__(self, settings):
        logger.info("Initializing AI Vision middleware...")
        self.settings = settings
        self.enabled = settings.enabled if settings else False
        self.ollama_client = None
        
        if not settings:
            logger.warning("No AI Vision settings provided - middleware disabled")
            return
            
        logger.debug(f"AI Vision settings: enabled={self.enabled}, model={getattr(settings, 'model_name', 'unknown')}")
        
        if self.enabled:
            try:
                import ollama
                logger.debug(f"Connecting to Ollama at {settings.ollama_host}")
                self.ollama_client = ollama.Client(host=settings.ollama_host)
                
                # Test connection
                try:
                    models = self.ollama_client.list()
                    logger.debug(f"Successfully connected to Ollama, found {len(models.get('models', []))} models")
                except Exception as conn_error:
                    logger.warning(f"Ollama connection test failed: {conn_error}")
                
                logger.info(f"AI Vision middleware initialized successfully with model: {settings.model_name}")
                logger.debug(f"Configuration: max_image_size={settings.max_image_size}, "
                           f"temperature={settings.temperature}, max_tokens={settings.max_tokens}")
                           
            except ImportError:
                logger.error("Ollama package not available for AI Vision - install with: pip install ollama")
                self.enabled = False
            except Exception as e:
                logger.error(f"Failed to initialize Ollama client: {e}")
                self.enabled = False
        else:
            logger.info("AI Vision middleware initialized but disabled by configuration")

    def is_supported_file(self, filename: str) -> bool:
        """Check if file extension is supported by AI Vision."""
        if not self.settings:
            logger.debug(f"No settings available, file {filename} not supported")
            return False
            
        file_ext = Path(filename).suffix.lower()
        supported = file_ext in self.settings.supported_extensions
        
        logger.debug(f"File extension check: {filename} -> {file_ext} -> "
                    f"{'supported' if supported else 'not supported'}")
        
        if not supported:
            logger.debug(f"Supported extensions: {self.settings.supported_extensions}")
            
        return supported

    def convert_pdf_to_images(self, pdf_stream: BytesIO, filename: str) -> List:
        """Convert PDF pages to images for vision processing."""
        start_time = time.time()
        logger.info(f"Starting PDF to image conversion for {filename}")
        
        try:
            import pdf2image
            from PIL import Image
            
            # Reset stream position
            pdf_stream.seek(0)
            pdf_size = len(pdf_stream.getvalue())
            logger.debug(f"PDF size: {pdf_size / (1024*1024):.2f} MB")
            
            # Convert PDF to images
            logger.debug("Converting PDF pages to images...")
            images = pdf2image.convert_from_bytes(
                pdf_stream.getvalue(),
                dpi=200,  # Good balance between quality and size
                fmt='PIL'
            )
            
            logger.info(f"Converted {len(images)} pages from PDF")
            
            # Resize images if they're too large
            processed_images = []
            total_size_before = 0
            total_size_after = 0
            
            for i, img in enumerate(images):
                original_size = img.size
                total_size_before += img.size[0] * img.size[1]
                
                if max(img.size) > self.settings.max_image_size:
                    # Calculate new size maintaining aspect ratio
                    ratio = self.settings.max_image_size / max(img.size)
                    new_size = tuple(int(dim * ratio) for dim in img.size)
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                    
                    logger.debug(f"Page {i+1}: resized from {original_size} to {img.size} "
                               f"(ratio: {ratio:.2f})")
                else:
                    logger.debug(f"Page {i+1}: kept original size {original_size}")
                
                total_size_after += img.size[0] * img.size[1]
                processed_images.append(img)
            
            processing_time = time.time() - start_time
            compression_ratio = total_size_after / total_size_before if total_size_before > 0 else 1
            
            logger.info(f"PDF conversion completed in {processing_time:.2f}s: "
                       f"{len(processed_images)} pages, "
                       f"compression ratio: {compression_ratio:.2f}")
            
            return processed_images
            
        except ImportError as e:
            logger.error(f"Required packages not available for PDF conversion: {e}")
            logger.error("Install with: pip install pdf2image pillow")
            raise
        except Exception as e:
            logger.error(f"Failed to convert PDF to images for {filename}: {e}")
            raise

    def image_to_base64(self, image) -> str:
        """Convert PIL Image to base64 string."""
        logger.debug(f"Converting image to base64: size={image.size}, mode={image.mode}")
        
        start_time = time.time()
        buffer = BytesIO()
        
        # Convert to RGB if necessary
        if image.mode in ('RGBA', 'LA', 'P'):
            logger.debug(f"Converting image from {image.mode} to RGB")
            image = image.convert('RGB')
        
        image.save(buffer, format='JPEG', quality=self.settings.image_quality)
        buffer_size = len(buffer.getvalue())
        
        img_str = base64.b64encode(buffer.getvalue()).decode()
        
        conversion_time = time.time() - start_time
        logger.debug(f"Image conversion completed in {conversion_time:.3f}s: "
                    f"{buffer_size / 1024:.1f} KB -> {len(img_str)} chars base64")
        
        return img_str

    def process_image_with_vision(self, image, page_number: int) -> str:
        """Process a single image with the vision model."""
        logger.debug(f"Processing page {page_number} with vision model {self.settings.model_name}")
        start_time = time.time()
        
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

            logger.debug(f"Sending vision request for page {page_number}")
            
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
            processing_time = time.time() - start_time
            
            logger.info(f"Page {page_number} processed successfully in {processing_time:.2f}s: "
                       f"{len(extracted_text)} characters extracted")
            logger.debug(f"Page {page_number} content preview: {extracted_text[:100]}...")
            
            return extracted_text
            
        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"Failed to process page {page_number} with vision model after {processing_time:.2f}s: {e}")
            raise

    def process_document(self, file_stream: BytesIO, filename: str) -> str:
        """Process entire document with AI Vision and return markdown content."""
        if not self.enabled:
            logger.error("AI Vision middleware is not enabled")
            raise RuntimeError("AI Vision middleware is not enabled")
        
        if not self.is_supported_file(filename):
            logger.error(f"File type not supported by AI Vision: {filename}")
            raise ValueError(f"File type not supported by AI Vision: {filename}")
        
        logger.info(f"Starting AI Vision document processing for {filename}")
        document_start_time = time.time()
        
        try:
            # Convert PDF to images
            images = self.convert_pdf_to_images(file_stream, filename)
            total_pages = len(images)
            
            logger.info(f"Processing {total_pages} pages in batches of {self.settings.pages_per_batch}")
            
            # Process images in batches
            all_pages_content = []
            batch_count = 0
            
            for i in range(0, total_pages, self.settings.pages_per_batch):
                batch_count += 1
                batch_start_time = time.time()
                batch_end = min(i + self.settings.pages_per_batch, total_pages)
                batch_images = images[i:batch_end]
                
                logger.info(f"Processing batch {batch_count}: pages {i+1}-{batch_end} of {total_pages}")
                
                # Process each page in the batch
                for j, image in enumerate(batch_images):
                    page_number = i + j + 1
                    logger.debug(f"Processing page {page_number}/{total_pages}")
                    
                    # Retry logic for each page
                    for attempt in range(self.settings.max_retries):
                        try:
                            page_content = self.process_image_with_vision(image, page_number)
                            all_pages_content.append(page_content)
                            break
                        except Exception as e:
                            if attempt == self.settings.max_retries - 1:
                                logger.error(f"Failed to process page {page_number} after {self.settings.max_retries} attempts: {e}")
                                error_content = f"[Error processing page {page_number}: {str(e)[:100]}]"
                                all_pages_content.append(error_content)
                            else:
                                logger.warning(f"Retry {attempt + 1}/{self.settings.max_retries} for page {page_number}: {e}")
                                time.sleep(1)  # Brief delay before retry
                
                batch_time = time.time() - batch_start_time
                logger.info(f"Batch {batch_count} completed in {batch_time:.2f}s")
            
            # Combine all pages
            logger.debug("Combining all processed pages...")
            if self.settings.include_page_breaks and len(all_pages_content) > 1:
                combined_content = self.settings.page_break_marker.join(all_pages_content)
                logger.debug(f"Combined pages with page break markers: {len(combined_content)} characters")
            else:
                combined_content = "\n\n".join(all_pages_content)
                logger.debug(f"Combined pages with paragraph breaks: {len(combined_content)} characters")
            
            total_time = time.time() - document_start_time
            successful_pages = len([p for p in all_pages_content if not p.startswith("[Error")])
            
            logger.info(f"AI Vision processing completed for {filename}: "
                       f"{successful_pages}/{total_pages} pages processed successfully in {total_time:.2f}s")
            logger.info(f"Final document: {len(combined_content)} characters, "
                       f"avg {total_time/total_pages:.2f}s per page")
            
            return combined_content
            
        except Exception as e:
            total_time = time.time() - document_start_time
            logger.error(f"AI Vision processing failed for {filename} after {total_time:.2f}s: {e}")
            raise

    def validate_environment(self) -> Dict[str, Any]:
        """Validate AI Vision environment and return status information."""
        logger.info("Validating AI Vision environment...")
        
        validation_result = {
            "status": "unknown",
            "issues": [],
            "warnings": [],
            "info": []
        }
        
        if not self.enabled:
            validation_result["status"] = "disabled"
            validation_result["info"].append("AI Vision is disabled in configuration")
            logger.info("AI Vision validation: disabled by configuration")
            return validation_result
        
        try:
            # Check required packages
            logger.debug("Checking required packages...")
            try:
                import pdf2image
                import PIL
                import ollama
                validation_result["info"].append("Required packages (pdf2image, PIL, ollama) are available")
                logger.debug("All required packages are available")
            except ImportError as e:
                missing = str(e).split("'")[1] if "'" in str(e) else "unknown package"
                validation_result["issues"].append(f"Missing required package: {missing}")
                validation_result["status"] = "error"
                logger.error(f"Missing required package: {missing}")
                return validation_result
            
            # Check Ollama connectivity
            logger.debug(f"Testing Ollama connectivity to {self.settings.ollama_host}")
            try:
                start_time = time.time()
                models = self.ollama_client.list()
                connection_time = time.time() - start_time
                
                available_models = [model['name'] for model in models.get('models', [])]
                logger.debug(f"Ollama connection successful in {connection_time:.2f}s, "
                           f"found {len(available_models)} models")
                
                if self.settings.model_name in available_models:
                    validation_result["info"].append(f"Vision model '{self.settings.model_name}' is available")
                    validation_result["status"] = "healthy"
                    logger.info(f"Vision model '{self.settings.model_name}' is available and ready")
                    
                    # Test model capabilities
                    logger.debug(f"Testing model capabilities for {self.settings.model_name}")
                    try:
                        # Simple test to see if model supports vision
                        test_response = self.ollama_client.show(self.settings.model_name)
                        logger.debug(f"Model info retrieved successfully")
                    except Exception as model_test_error:
                        logger.warning(f"Could not retrieve model info: {model_test_error}")
                        validation_result["warnings"].append(f"Could not test model capabilities: {model_test_error}")
                        
                else:
                    validation_result["issues"].append(f"Vision model '{self.settings.model_name}' not found in Ollama")
                    validation_result["status"] = "error"
                    logger.error(f"Vision model '{self.settings.model_name}' not found. Available models: {available_models}")
                    
            except Exception as e:
                validation_result["issues"].append(f"Failed to connect to Ollama: {str(e)[:100]}")
                validation_result["status"] = "error"
                logger.error(f"Failed to connect to Ollama at {self.settings.ollama_host}: {e}")
                
        except Exception as e:
            validation_result["issues"].append(f"Validation error: {str(e)}")
            validation_result["status"] = "error"
            logger.error(f"Unexpected validation error: {e}")
            
        logger.info(f"AI Vision validation completed: status={validation_result['status']}")
        return validation_result