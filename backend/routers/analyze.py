import os
import json
import uuid
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Assuming shared models and utils are accessible
# Adjust imports based on your project structure
try:
    from ..utils.azure_storage import get_queue_service_client, get_blob_service_client
    # If models are defined elsewhere, import them:
    # from ..models import AnalyzeRequest, AnalyzeResponse, JobProgress 
except ImportError:
    # Fallback for potential different structure or direct run
    from utils.azure_storage import get_queue_service_client, get_blob_service_client
    # Define models here if not imported
    class AnalyzeRequest(BaseModel):
        area: dict # GeoJSON Feature
        date_range: str
        ndvi_threshold: float
        max_cloud_cover: int
        crop_type: str
        frequency: str

    class AnalyzeResponse(BaseModel):
        jobId: str

    class JobProgress(BaseModel):
        progress: int
        statusMessage: str
        isComplete: bool
        details: dict | None = None


router = APIRouter()
logger = logging.getLogger(__name__)

# --- Config (can be centralized) ---
ANALYSIS_QUEUE_NAME = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")
PROGRESS_CONTAINER_NAME = os.getenv("PROGRESS_CONTAINER_NAME", "job-status")

@router.post("/", response_model=AnalyzeResponse, tags=["Analysis"]) # Path relative to prefix in app.py
async def start_field_analysis(payload: AnalyzeRequest):
    logger.info(f"Received analysis request via router for area type: {payload.area.get('geometry',{}).get('type')}")
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    logger.info(f"Generated Job ID: {job_id}")

    queue_message = {"jobId": job_id, "payload": payload.model_dump()}

    # 1. Update initial job status
    try:
        blob_service_client = await get_blob_service_client()
        if not blob_service_client:
            raise HTTPException(status_code=503, detail="Storage service unavailable.")

        async with blob_service_client:
            container_client = blob_service_client.get_container_client(PROGRESS_CONTAINER_NAME)
            blob_client = container_client.get_blob_client(f"{job_id}.json")
            initial_status = JobProgress(progress=0, statusMessage="Pending", isComplete=False)
            await blob_client.upload_blob(initial_status.model_dump_json(), overwrite=True)
            logger.info(f"Initial status set for job {job_id}")

    except Exception as e:
        logger.error(f"Failed to set initial status for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to initialize job status.")

    # 2. Send message to Azure Storage Queue
    try:
        queue_service_client = await get_queue_service_client()
        if not queue_service_client:
            raise HTTPException(status_code=503, detail="Queue service unavailable.")

        async with queue_service_client:
            queue_client = queue_service_client.get_queue_client(ANALYSIS_QUEUE_NAME)
            await queue_client.send_message(json.dumps(queue_message))
            logger.info(f"Job {job_id} submitted to queue '{ANALYSIS_QUEUE_NAME}'.")
            return AnalyzeResponse(jobId=job_id)

    except Exception as e:
        logger.error(f"Failed to submit job {job_id} to queue: {e}", exc_info=True)
        # Consider updating status to FAILED here
        raise HTTPException(status_code=500, detail="Failed to start analysis job.") 