import logging
import os
import azure.functions as func
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from azure.storage.queue.aio import QueueServiceClient as AsyncQueueServiceClient

from routers.analysis import router as analysis_router
from routers.report import router as report_router
from routers.progress import router as progress_router
from shared_code.queue_handler import process_analysis
from shared_code.helpers.blob import get_async_blob_service_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING must be set")
    app.state.blob_service_client = AsyncBlobServiceClient.from_connection_string(conn_str)
    app.state.queue_service_client = AsyncQueueServiceClient.from_connection_string(conn_str)
    yield
    await app.state.blob_service_client.close()
    await app.state.queue_service_client.close()

fastapi_app = FastAPI(
    title="Agro Monitor Backend API",
    description="API for retrieving and analyzing agricultural field data",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
origins = [
    "https://frontendc613cf2f.azurewebsites.net",  # Your deployed frontend
    "http://localhost:3000",                       # Local development
    # Add any other origins if needed
]

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@fastapi_app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)}
    )

fastapi_app.include_router(analysis_router, prefix="/api/analyze")
fastapi_app.include_router(report_router, prefix="/api/report")
fastapi_app.include_router(progress_router, prefix="/api/progress")

@fastapi_app.get("/", include_in_schema=False)
async def read_root():
    return {"message": "Welcome to AgroMonitor API"}

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)

@app.function_name(name="process_analysis_job")
@app.queue_trigger(
    arg_name="msg",
    queue_name="%ANALYSIS_QUEUE_NAME%",
    connection="AZURE_STORAGE_CONNECTION_STRING",
)
async def process_analysis_job(msg: func.QueueMessage):
    logger.info(f"Processing message: {msg.id}")
    try:
        content = msg.get_body().decode('utf-8')
        logger.info(f"Message content: {content}")
        
        client = get_async_blob_service_client()
        async with client:
            await process_analysis(msg, client)
    except Exception as e:
        logger.error(f"Error processing message {msg.id}: {str(e)}", exc_info=True)
        raise