import logging
import os
import azure.functions as func
from fastapi import FastAPI
from contextlib import asynccontextmanager
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from azure.storage.queue.aio import QueueServiceClient as AsyncQueueServiceClient

from routers.analysis import router as analysis_router
from routers.report import router as report_router
from routers.progress import router as progress_router

logging.basicConfig(level=logging.INFO)

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

fastapi_app.include_router(analysis_router, prefix="/api/analyze")
fastapi_app.include_router(report_router, prefix="/api/report")
fastapi_app.include_router(progress_router, prefix="/api/progress")

@fastapi_app.get("/", include_in_schema=False)
async def read_root():
    return {"message": "Welcome to AgroMonitor API"}

# Wrap FastAPI app with Azure Functions
app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)