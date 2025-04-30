import logging
import os
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

app = FastAPI(
    title="Agro Monitor Backend API",
    description="API for retrieving and analyzing agricultural field data. Includes SSE for tracking task progress.",
    version="1.0.0",
    root_path="/api",
    servers=[{"url": "/api", "description": "Local or proxy server"}],
    lifespan=lifespan,
)

app.include_router(analysis_router, prefix="/analyze")
app.include_router(report_router, prefix="/report")
app.include_router(progress_router, prefix="/progress")

@app.get("/", include_in_schema=False)
async def read_root():
    return {"message": "Welcome to AgroMonitor API v2 (Docker)"}