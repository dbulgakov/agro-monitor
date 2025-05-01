import json
import os
import uuid
import logging

from azure.storage.queue.aio import QueueClient
from azure.storage.blob.aio import BlobServiceClient
from fastapi import APIRouter, Depends, HTTPException, status, Request

from shared_code.helpers.job_status import update_job_status
from shared_code.helpers.env_helpers import check_environment_variables, get_required_env_vars
from shared_code.schemas import StartAnalysisResponse, StartAnalysisPayload, JobStatus, ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Analysis"])

async def get_queue_client():
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    name = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")
    if not conn or not name:
        logger.error(f"Queue configuration error: conn={bool(conn)}, name={name}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue configuration error: Check AZURE_STORAGE_CONNECTION_STRING and ANALYSIS_QUEUE_NAME environment variables")
    return QueueClient.from_connection_string(conn, queue_name=name)

# Dependency to get BlobServiceClient from app state
async def get_blob_service_client(request: Request) -> BlobServiceClient:
    return request.app.state.blob_service_client

@router.post(
    "",
    response_model=StartAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="startAnalysis",
    responses={
        202: {"description": "Task accepted for processing", "model": StartAnalysisResponse},
        400: {"description": "Invalid request parameters", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
)
async def start_analysis(
    request: Request,
    payload: StartAnalysisPayload,
    _: None = Depends(lambda: check_environment_variables(get_required_env_vars("start_analysis"))),
    queue: QueueClient = Depends(get_queue_client),
    blob_client: BlobServiceClient = Depends(get_blob_service_client)
):
    try:
        logger.info(f"Starting analysis with payload: {payload.model_dump()}")
        job_id = str(uuid.uuid4())
        logger.info(f"Created job ID: {job_id}")
        
        await update_job_status(blob_client, job_id, JobStatus.PENDING, 0, "Analysis request received")
        logger.info(f"Updated job status for {job_id}")
        
        message = json.dumps({"jobId": job_id, "payload": payload.model_dump()})
        logger.info(f"Sending message to queue: {message}")
        
        async with queue:
            await queue.send_message(message)
        logger.info(f"Message sent successfully for job {job_id}")
        
        return StartAnalysisResponse(jobId=job_id)
    except Exception as e:
        logger.error(f"Error in start_analysis: {str(e)}", exc_info=True)
        raise