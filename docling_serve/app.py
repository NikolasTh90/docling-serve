import asyncio
import importlib.metadata
import logging
import shutil
import time
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from docling.datamodel.base_models import DocumentStream

from docling_serve.datamodel.callback import (
    ProgressCallbackRequest,
    ProgressCallbackResponse,
)
from docling_serve.datamodel.convert import ConvertDocumentsOptions
from docling_serve.datamodel.requests import (
    ConvertDocumentFileSourcesRequest,
    ConvertDocumentHttpSourcesRequest,
    ConvertDocumentsRequest,
)
from docling_serve.datamodel.responses import (
    ClearResponse,
    ConvertDocumentResponse,
    HealthCheckResponse,
    MessageKind,
    TaskStatusResponse,
    WebsocketMessage,
)
from docling_serve.datamodel.task import Task, TaskSource
from docling_serve.docling_conversion import _get_converter_from_hash
from docling_serve.engines.async_orchestrator import (
    BaseAsyncOrchestrator,
    ProgressInvalid,
)
from docling_serve.engines.async_orchestrator_factory import get_async_orchestrator
from docling_serve.engines.base_orchestrator import TaskNotFoundError
from docling_serve.helper_functions import FormDepends
from docling_serve.settings import docling_serve_settings
from docling_serve.storage import get_scratch




# Set up custom logging as we'll be intermixes with FastAPI/Uvicorn's logging
class ColoredLogFormatter(logging.Formatter):
    COLOR_CODES = {
        logging.DEBUG: "\033[94m",  # Blue
        logging.INFO: "\033[92m",  # Green
        logging.WARNING: "\033[93m",  # Yellow
        logging.ERROR: "\033[91m",  # Red
        logging.CRITICAL: "\033[95m",  # Magenta
    }
    RESET_CODE = "\033[0m"

    def format(self, record):
        color = self.COLOR_CODES.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname}{self.RESET_CODE}"
        return super().format(record)


logging.basicConfig(
    level=logging.INFO,  # Set the logging level
    format="%(levelname)s:\t%(asctime)s - %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

# Override the formatter with the custom ColoredLogFormatter
root_logger = logging.getLogger()  # Get the root logger
for handler in root_logger.handlers:  # Iterate through existing handlers
    if handler.formatter:
        handler.setFormatter(ColoredLogFormatter(handler.formatter._fmt))

_log = logging.getLogger(__name__)

# Initialize middleware instances with proper error handling
try:
    from docling_serve.settings import ocrmypdf_settings
    from docling_serve.ocrmypdf_middleware import OCRMyPDFMiddleware
    ocrmypdf_middleware = OCRMyPDFMiddleware(settings=ocrmypdf_settings)
except ImportError as e:
    _log.warning(f"OCRMyPDF middleware not available: {e}")
    ocrmypdf_middleware = None

try:
    from docling_serve.settings import arabic_correction_settings  
    from docling_serve.arabic_correction_middleware import ArabicCorrectionMiddleware
    arabic_middleware = ArabicCorrectionMiddleware(
        enabled=arabic_correction_settings.enabled if arabic_correction_settings else False,
        ollama_host=arabic_correction_settings.ollama_host if arabic_correction_settings else "http://localhost:11434",
        model_name=arabic_correction_settings.model_name if arabic_correction_settings else "command-r7b-arabic"
    )
except ImportError as e:
    _log.warning(f"Arabic correction middleware not available: {e}")
    arabic_middleware = None


# Context manager to initialize and clean up the lifespan of the FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    orchestrator = get_async_orchestrator()
    scratch_dir = get_scratch()

    # Warm up processing cache
    await orchestrator.warm_up_caches()

    # Start the background queue processor
    queue_task = asyncio.create_task(orchestrator.process_queue())

    yield

    # Cancel the background queue processor on shutdown
    queue_task.cancel()
    try:
        await queue_task
    except asyncio.CancelledError:
        _log.info("Queue processor cancelled.")

    # Remove scratch directory in case it was a tempfile
    if docling_serve_settings.scratch_path is not None:
        shutil.rmtree(scratch_dir, ignore_errors=True)


##################################
# App creation and configuration #
##################################


def create_app():  # noqa: C901
    try:
        version = importlib.metadata.version("docling_serve")
    except importlib.metadata.PackageNotFoundError:
        _log.warning("Unable to get docling_serve version, falling back to 0.0.0")

        version = "0.0.0"

    # Initialize Arabic correction middleware
    try:
        if arabic_correction_settings:
            arabic_middleware = ArabicCorrectionMiddleware(
                enabled=arabic_correction_settings.enabled,
                ollama_host=arabic_correction_settings.ollama_host,
                model_name=arabic_correction_settings.model_name
            )
            _log.info(f"Arabic correction middleware initialized: enabled={arabic_correction_settings.enabled}")
        else:
            arabic_middleware = ArabicCorrectionMiddleware(enabled=False)
            _log.info("Arabic correction middleware disabled: settings not available")
    except Exception as e:
        _log.warning(f"Failed to initialize Arabic correction middleware: {e}")
        arabic_middleware = ArabicCorrectionMiddleware(enabled=False)

    try:
        from docling_serve.ai_vision_middleware import AIVisionMiddleware
        from docling_serve.settings import ai_vision_settings
        ai_vision_middleware = AIVisionMiddleware(ai_vision_settings)
        _log.info(f"AI Vision middleware initialized: enabled={ai_vision_middleware.enabled}")
    except ImportError as e:
        _log.warning(f"AI Vision middleware not available: {e}")
        ai_vision_middleware = None

    offline_docs_assets = False
    if (
        docling_serve_settings.static_path is not None
        and (docling_serve_settings.static_path).is_dir()
    ):
        offline_docs_assets = True
        _log.info("Found static assets.")

    app = FastAPI(
        title="Docling Serve",
        docs_url=None if offline_docs_assets else "/docs",
        redoc_url=None if offline_docs_assets else "/redocs",
        lifespan=lifespan,
        version=version,
    )

    origins = docling_serve_settings.cors_origins
    methods = docling_serve_settings.cors_methods
    headers = docling_serve_settings.cors_headers

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=methods,
        allow_headers=headers,
    )

    # Mount the Gradio app
    if docling_serve_settings.enable_ui:
        try:
            import gradio as gr

            from docling_serve.gradio_ui import ui as gradio_ui

            tmp_output_dir = get_scratch() / "gradio"
            tmp_output_dir.mkdir(exist_ok=True, parents=True)
            gradio_ui.gradio_output_dir = tmp_output_dir
            app = gr.mount_gradio_app(
                app,
                gradio_ui,
                path="/ui",
                allowed_paths=["./logo.png", tmp_output_dir],
                root_path="/ui",
            )
        except ImportError:
            _log.warning(
                "Docling Serve enable_ui is activated, but gradio is not installed. "
                "Install it with `pip install docling-serve[ui]` "
                "or `pip install gradio`"
            )

    #############################
    # Offline assets definition #
    #############################
    if offline_docs_assets:
        app.mount(
            "/static",
            StaticFiles(directory=docling_serve_settings.static_path),
            name="static",
        )

        @app.get("/docs", include_in_schema=False)
        async def custom_swagger_ui_html():
            return get_swagger_ui_html(
                openapi_url=app.openapi_url,
                title=app.title + " - Swagger UI",
                oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
                swagger_js_url="/static/swagger-ui-bundle.js",
                swagger_css_url="/static/swagger-ui.css",
            )

        @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
        async def swagger_ui_redirect():
            return get_swagger_ui_oauth2_redirect_html()

        @app.get("/redoc", include_in_schema=False)
        async def redoc_html():
            return get_redoc_html(
                openapi_url=app.openapi_url,
                title=app.title + " - ReDoc",
                redoc_js_url="/static/redoc.standalone.js",
            )

    ########################
    # Async / Sync helpers #
    ########################

    async def _enque_source(
        orchestrator: BaseAsyncOrchestrator,
        conversion_request: ConvertDocumentsRequest,
    ) -> Task:
        
        # Handle different request types
        if hasattr(conversion_request, 'http_sources'):
            # This is a ConvertDocumentsRequest with HTTP sources
            sources_info = f"{len(conversion_request.http_sources or [])} HTTP sources"
            _log.info(f"Received source conversion request with {sources_info}")
            
            # Get options from the request
            options = conversion_request.options or ConvertDocumentsOptions()
            
            # Create task sources from HTTP sources
            file_sources: list[TaskSource] = []
            
            # Process HTTP sources
            if conversion_request.http_sources:
                for http_source in conversion_request.http_sources:
                    file_sources.append(http_source)
                    
        elif hasattr(conversion_request, 'file_sources'):
            # This is a ConvertDocumentFileSourcesRequest with file sources (base64)
            sources_info = f"{len(conversion_request.file_sources or [])} file sources"
            _log.info(f"Received source conversion request with {sources_info}")
            
            # Get options from the request
            options = conversion_request.options or ConvertDocumentsOptions()
            
            # Create task sources from file sources (base64)
            file_sources: list[TaskSource] = []
            
            # Process base64 file sources
            if conversion_request.file_sources:
                for file_source in conversion_request.file_sources:
                    file_sources.append(file_source)
        else:
            # Fallback - try to get any sources available
            _log.warning("Unknown request type in _enque_source")
            options = getattr(conversion_request, 'options', None) or ConvertDocumentsOptions()
            file_sources = []

        # Remove all OCRMyPDF preprocessing logic - it will be handled by the worker
        # Just log if OCRMyPDF is requested
        enable_ocrmypdf = getattr(options, 'enable_ocrmypdf_preprocessing', False)
        if enable_ocrmypdf:
            _log.info("OCRMyPDF preprocessing will be applied by background worker")
        elif enable_ocrmypdf:
            _log.warning("OCRMyPDF preprocessing requested but middleware not available")

        task = await orchestrator.enqueue(sources=file_sources, options=options)
        return task

    async def _enque_file(
        orchestrator: BaseAsyncOrchestrator,
        files: list[UploadFile],
        options: ConvertDocumentsOptions,
    ) -> Task:
        _log.info(f"Received {len(files)} files for processing.")

        # Load the uploaded files to Docling DocumentStream
        file_sources: list[TaskSource] = []
        for i, file in enumerate(files):
            buf = BytesIO(file.file.read())
            suffix = "" if len(file_sources) == 1 else f"_{i}"
            name = file.filename if file.filename else f"file{suffix}.pdf"
            file_sources.append(DocumentStream(name=name, stream=buf))

        # Remove all OCRMyPDF preprocessing logic - it will be handled by the worker
        # Just log if OCRMyPDF is requested
        enable_ocrmypdf = getattr(options, 'enable_ocrmypdf_preprocessing', False)
        if enable_ocrmypdf:
            _log.info("OCRMyPDF preprocessing will be applied by background worker")

        task = await orchestrator.enqueue(sources=file_sources, options=options)
        return task

    async def _wait_task_complete(
        orchestrator: BaseAsyncOrchestrator, task_id: str
    ) -> bool:
        start_time = time.monotonic()
        while True:
            task = await orchestrator.task_status(task_id=task_id)
            if task.is_completed():
                return True
            await asyncio.sleep(5)
            elapsed_time = time.monotonic() - start_time
            if elapsed_time > docling_serve_settings.max_sync_wait:
                return False

    #############################
    # API Endpoints definitions #
    #############################

    # Favicon
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        logo_url = "https://raw.githubusercontent.com/docling-project/docling/refs/heads/main/docs/assets/logo.svg"
        if offline_docs_assets:
            logo_url = "/static/logo.svg"
        response = RedirectResponse(url=logo_url)
        return response

    @app.get("/health")
    def health() -> HealthCheckResponse:
        return HealthCheckResponse()

    # API readiness compatibility for OpenShift AI Workbench
    @app.get("/api", include_in_schema=False)
    def api_check() -> HealthCheckResponse:
        return HealthCheckResponse()

    # Convert a document from URL(s)
    @app.post(
        "/v1alpha/convert/source",
        response_model=ConvertDocumentResponse,
        responses={
            200: {
                "content": {"application/zip": {}},
                # "description": "Return the JSON item or an image.",
            }
        },
    )
    async def process_url(
        background_tasks: BackgroundTasks,
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        conversion_request: ConvertDocumentsRequest,
    ):
        task = await _enque_source(
            orchestrator=orchestrator, conversion_request=conversion_request
        )
        success = await _wait_task_complete(
            orchestrator=orchestrator, task_id=task.task_id
        )

        if not success:
            # TODO: abort task!
            return HTTPException(
                status_code=504,
                detail=f"Conversion is taking too long. The maximum wait time is configure as DOCLING_SERVE_MAX_SYNC_WAIT={docling_serve_settings.max_sync_wait}.",
            )

        result = await orchestrator.task_result(
            task_id=task.task_id, background_tasks=background_tasks
        )
        if result is None:
            raise HTTPException(
                status_code=404,
                detail="Task result not found. Please wait for a completion status.",
            )
        
        # Apply Arabic correction if enabled and requested
        if hasattr(conversion_request, 'options') and conversion_request.options:
            enable_arabic_correction = getattr(conversion_request.options, 'enable_arabic_correction', False)
            if enable_arabic_correction and arabic_middleware.enabled:
                _log.info("Applying Arabic OCR correction to sync source conversion result")
                result = arabic_middleware.process_conversion_result(result)

        return result

    # Convert a document from file(s)
    @app.post(
        "/v1alpha/convert/file",
        response_model=ConvertDocumentResponse,
        responses={
            200: {
                "content": {"application/zip": {}},
            }
        },
    )
    async def process_file(
        background_tasks: BackgroundTasks,
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        files: list[UploadFile],
        options: Annotated[
            ConvertDocumentsOptions, FormDepends(ConvertDocumentsOptions)
        ],
    ):
        task = await _enque_file(
            orchestrator=orchestrator, files=files, options=options
        )
        success = await _wait_task_complete(
            orchestrator=orchestrator, task_id=task.task_id
        )

        if not success:
            # TODO: abort task!
            return HTTPException(
                status_code=504,
                detail=f"Conversion is taking too long. The maximum wait time is configure as DOCLING_SERVE_MAX_SYNC_WAIT={docling_serve_settings.max_sync_wait}.",
            )

        result = await orchestrator.task_result(
            task_id=task.task_id, background_tasks=background_tasks
        )
        if result is None:
            raise HTTPException(
                status_code=404,
                detail="Task result not found. Please wait for a completion status.",
            )
        
        # Apply Arabic correction if enabled and requested
        enable_arabic_correction = getattr(options, 'enable_arabic_correction', False)
        if enable_arabic_correction and arabic_middleware.enabled:
            _log.info("Applying Arabic OCR correction to sync file conversion result")
            result = arabic_middleware.process_conversion_result(result)

        return result

    # Convert a document from URL(s) using the async api
    @app.post(
        "/v1alpha/convert/source/async",
        response_model=TaskStatusResponse,
    )
    async def process_url_async(
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        conversion_request: ConvertDocumentsRequest,
    ):
        task = await _enque_source(
            orchestrator=orchestrator, conversion_request=conversion_request
        )
        task_queue_position = await orchestrator.get_queue_position(
            task_id=task.task_id
        )
        return TaskStatusResponse(
            task_id=task.task_id,
            task_status=task.task_status,
            task_position=task_queue_position,
            task_meta=task.processing_meta,
        )

    # Convert a document from file(s) using the async api
    @app.post(
        "/v1alpha/convert/file/async",
        response_model=TaskStatusResponse,
    )
    async def process_file_async(
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        background_tasks: BackgroundTasks,
        files: list[UploadFile],
        options: Annotated[
            ConvertDocumentsOptions, FormDepends(ConvertDocumentsOptions)
        ],
    ):
        task = await _enque_file(
            orchestrator=orchestrator, files=files, options=options
        )
        task_queue_position = await orchestrator.get_queue_position(
            task_id=task.task_id
        )
        return TaskStatusResponse(
            task_id=task.task_id,
            task_status=task.task_status,
            task_position=task_queue_position,
            task_meta=task.processing_meta,
        )


    # ADD NEW ENDPOINT - Arabic correction status
    @app.get("/v1alpha/arabic/status")
    async def arabic_correction_status():
        """Get Arabic correction service status."""
        try:
            from docling_serve.gradio_ui import validate_arabic_correction_environment, get_arabic_correction_config
            
            validation_result = validate_arabic_correction_environment()
            config = get_arabic_correction_config()
            
            return {
                "enabled": config["enabled"],
                "host": config["host"],
                "model": config["model"],
                "status": validation_result["status"],
                "issues": validation_result.get("issues", []),
                "warnings": validation_result.get("warnings", [])
            }
        except Exception as e:
            return {
                "enabled": False,
                "status": "error",
                "issues": [f"Failed to check Arabic correction status: {str(e)}"]
            }

    # ADD NEW ENDPOINT - Test Arabic correction
    @app.post("/v1alpha/arabic/test")
    async def test_arabic_correction(test_text: str = "هذا نص تجريبي للاختبار"):
        """Test Arabic correction functionality."""
        if not arabic_middleware.enabled:
            raise HTTPException(
                status_code=503,
                detail="Arabic correction is disabled"
            )
        
        try:
            # Test language detection
            is_arabic = arabic_middleware.should_correct_text(test_text)
            
            if not is_arabic:
                return {
                    "success": False,
                    "message": "Text not detected as Arabic",
                    "original_text": test_text,
                    "corrected_text": test_text
                }
            
            # Test correction
            corrected_text = arabic_middleware.correct_arabic_text(test_text)
            
            return {
                "success": True,
                "message": "Arabic correction test completed",
                "original_text": test_text,
                "corrected_text": corrected_text,
                "was_modified": test_text != corrected_text
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Arabic correction test failed: {str(e)}"
            )


    # Task status poll
    @app.get(
        "/v1alpha/status/poll/{task_id}",
        response_model=TaskStatusResponse,
    )
    async def task_status_poll(
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        task_id: str,
        wait: Annotated[
            float, Query(help="Number of seconds to wait for a completed status.")
        ] = 0.0,
    ):
        try:
            task = await orchestrator.task_status(task_id=task_id, wait=wait)
            task_queue_position = await orchestrator.get_queue_position(task_id=task_id)
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        return TaskStatusResponse(
            task_id=task.task_id,
            task_status=task.task_status,
            task_position=task_queue_position,
            task_meta=task.processing_meta,
        )

    # Task status websocket
    @app.websocket(
        "/v1alpha/status/ws/{task_id}",
    )
    async def task_status_ws(
        websocket: WebSocket,
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        task_id: str,
    ):
        await websocket.accept()

        if task_id not in orchestrator.tasks:
            await websocket.send_text(
                WebsocketMessage(
                    message=MessageKind.ERROR, error="Task not found."
                ).model_dump_json()
            )
            await websocket.close()
            return

        task = orchestrator.tasks[task_id]

        # Track active WebSocket connections for this job
        orchestrator.task_subscribers[task_id].add(websocket)

        try:
            task_queue_position = await orchestrator.get_queue_position(task_id=task_id)
            task_response = TaskStatusResponse(
                task_id=task.task_id,
                task_status=task.task_status,
                task_position=task_queue_position,
                task_meta=task.processing_meta,
            )
            await websocket.send_text(
                WebsocketMessage(
                    message=MessageKind.CONNECTION, task=task_response
                ).model_dump_json()
            )
            while True:
                task_queue_position = await orchestrator.get_queue_position(
                    task_id=task_id
                )
                task_response = TaskStatusResponse(
                    task_id=task.task_id,
                    task_status=task.task_status,
                    task_position=task_queue_position,
                    task_meta=task.processing_meta,
                )
                await websocket.send_text(
                    WebsocketMessage(
                        message=MessageKind.UPDATE, task=task_response
                    ).model_dump_json()
                )
                # each client message will be interpreted as a request for update
                msg = await websocket.receive_text()
                _log.debug(f"Received message: {msg}")

        except WebSocketDisconnect:
            _log.info(f"WebSocket disconnected for job {task_id}")

        finally:
            orchestrator.task_subscribers[task_id].remove(websocket)

    # Task result
    @app.get(
        "/v1alpha/result/{task_id}",
        response_model=ConvertDocumentResponse,
        responses={
            200: {
                "content": {"application/zip": {}},
            }
        },
    )
    async def task_result(
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        background_tasks: BackgroundTasks,
        task_id: str,
    ):
        result = await orchestrator.task_result(
            task_id=task_id, background_tasks=background_tasks
        )
        if result is None:
            raise HTTPException(
                status_code=404,
                detail="Task result not found. Please wait for a completion status.",
            )
        


        return result

    # Update task progress
    @app.post(
        "/v1alpha/callback/task/progress",
        response_model=ProgressCallbackResponse,
    )
    async def callback_task_progress(
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        request: ProgressCallbackRequest,
    ):
        try:
            await orchestrator.receive_task_progress(request=request)
            return ProgressCallbackResponse(status="ack")
        except TaskNotFoundError:
            raise HTTPException(status_code=404, detail="Task not found.")
        except ProgressInvalid as err:
            raise HTTPException(
                status_code=400, detail=f"Invalid progress payload: {err}"
            )

    #### Clear requests

    # Offload models
    @app.get(
        "/v1alpha/clear/converters",
        response_model=ClearResponse,
    )
    async def clear_converters():
        _get_converter_from_hash.cache_clear()
        return ClearResponse()

    # Clean results
    @app.get(
        "/v1alpha/clear/results",
        response_model=ClearResponse,
    )
    async def clear_results(
        orchestrator: Annotated[BaseAsyncOrchestrator, Depends(get_async_orchestrator)],
        older_then: float = 3600,
    ):
        await orchestrator.clear_results(older_than=older_then)
        return ClearResponse()

    return app
