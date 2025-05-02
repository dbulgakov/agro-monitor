import json
import os
import uuid
import logging

# Import sync clients
from azure.storage.queue import QueueServiceClient
from azure.storage.blob import BlobServiceClient
from fastapi import APIRouter, Depends, HTTPException, status, Request

# Import sync version of update_job_status
from shared_code.helpers.job_status import update_job_status
from shared_code.helpers.env_helpers import check_environment_variables, get_required_env_vars
from shared_code.schemas import StartAnalysisResponse, StartAnalysisPayload, JobStatus, ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Analysis"])

# Dependencies can remain async, FastAPI handles it, but they just return sync clients now.
async def get_blob_service_client(request: Request) -> BlobServiceClient:
    # Returns the sync client stored in state by lifespan
    return request.app.state.blob_service_client

async def get_queue_service_client(request: Request) -> QueueServiceClient:
    # Returns the sync client stored in state by lifespan
    return request.app.state.queue_service_client

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
# Make the endpoint synchronous
def start_analysis(
    request: Request,
    payload: StartAnalysisPayload,
    _: None = Depends(lambda: check_environment_variables(get_required_env_vars("start_analysis"))),
    # Dependencies resolve correctly even for sync endpoints
    queue_service_client: QueueServiceClient = Depends(get_queue_service_client),
    blob_client: BlobServiceClient = Depends(get_blob_service_client)
):
    try:
        logger.info(f"Starting analysis with payload: {payload.model_dump()}")
        job_id = str(uuid.uuid4())
        logger.info(f"Created job ID: {job_id}")

        queue_name = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")
        # Get the sync queue client
        queue_client = queue_service_client.get_queue_client(queue_name)

        # Call sync update_job_status (no await)
        update_job_status(blob_client, job_id, JobStatus.PENDING, 0, "Початок аналізу")
        logger.info(f"Updated job status for {job_id}")

        message = json.dumps({"jobId": job_id, "payload": payload.model_dump()})
        logger.info(f"Sending message to queue: {message}")

        # Call sync send_message (no await)
        queue_client.send_message(message)
        logger.info(f"Message sent successfully for job {job_id}")

        return StartAnalysisResponse(jobId=job_id)
    except Exception as e:
        logger.error(f"Error in start_analysis: {str(e)}", exc_info=True)
        # Reraise the exception to be handled by the global exception handler
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")