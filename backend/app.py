# app.py
# import os # Removed unused import
# import json # No longer needed here
# import asyncio # No longer needed here
# import uuid # No longer needed here
from fastapi import FastAPI #, HTTPException, Request # Removed unused imports
# from fastapi.responses import StreamingResponse # No longer needed here
# from pydantic import BaseModel # No longer needed here
import logging

# Import the async clients - Still needed if routers use utils directly, TBC
# from utils.azure_storage import get_queue_service_client, get_blob_service_client

# --- Import Routers ---
from routers import analyze, report, progress # Assuming they are in a 'routers' package

# Configure logging
logging.basicConfig(level=logging.INFO) # Adjust level as needed
# logger = logging.getLogger(__name__) # Removed unused logger

# --- Config --- #
# Config needed by routers might be better defined within them or a central config module
# ANALYSIS_QUEUE_NAME = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")
# REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
# PROGRESS_CONTAINER_NAME = os.getenv("PROGRESS_CONTAINER_NAME", "job-status") # Example name

# --- FastAPI App --- #
app = FastAPI(
    title="AgroMonitor API",
    description="API for AgroMonitor services.",
    version="0.1.0",
    # Add root_path if running behind a proxy or gateway, handled by Azure Functions?
    # root_path=os.environ.get("API_ROOT_PATH", "/api")
)

# --- API Models --- #
# Define models needed by multiple routers or keep them within router files?
# For now, keep them here if they were used by multiple original endpoints.
# Alternatively, create a backend/models.py
# class AnalyzeRequest(BaseModel):
#     # Define request model based on frontend/openapi.yaml
#     area: dict # GeoJSON Feature
#     date_range: str
#     ndvi_threshold: float
#     max_cloud_cover: int
#     crop_type: str
#     frequency: str

# class AnalyzeResponse(BaseModel):
#     jobId: str

# class JobProgress(BaseModel):
#     progress: int
#     statusMessage: str
#     isComplete: bool
#     details: dict | None = None

# --- Include Routers --- #
app.include_router(analyze.router, prefix="/analyze", tags=["Analysis"])
app.include_router(report.router, prefix="/report", tags=["Reports"])
app.include_router(progress.router, prefix="/progress", tags=["Progress"])


# --- Old Route Handlers Removed --- #
# @app.post("/analyze", response_model=AnalyzeResponse, tags=["Analysis"])
# async def start_field_analysis(payload: AnalyzeRequest):
# ... (logic moved to routers/analyze.py) ...

# @app.get("/report/{jobId}", tags=["Reports"])
# async def get_analysis_report(jobId: str):
# ... (logic moved to routers/report.py) ...

# @app.get("/progress/{jobId}", tags=["Progress"])
# async def get_job_progress_sse(jobId: str, request: Request):
# ... (logic moved to routers/progress.py) ...


# Example root endpoint (can remain or be moved)
@app.get("/", tags=["Default"])
async def read_root():
    return {"message": "Welcome to AgroMonitor API v2 (Docker)"} 