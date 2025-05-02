import os
import logging

from fastapi import APIRouter, Depends, Path, HTTPException
from fastapi.responses import JSONResponse

# Import necessary items from shared code
from shared_code.schemas import ProgressUpdate, JobStatus
from shared_code.helpers.blob import get_sync_blob_service_client, REPORTS_CONTAINER_NAME

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Progress"])

# AZURE_CONN_STR and REPORTS_CONTAINER checks are now handled in shared_code.helpers.blob
# AZURE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
# REPORTS_CONTAINER = os.getenv("REPORTS_CONTAINER_NAME")

# Remove the local get_blob_client function
# async def get_blob_client(job_id: str):
#     if not AZURE_CONN_STR or not REPORTS_CONTAINER:
#         logger.error("Missing required environment variables for Azure Blob Storage")
#         raise HTTPException(status_code=503, detail="Storage configuration error")
#     try:
#         service = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
#         return service.get_blob_client(container=REPORTS_CONTAINER, blob=f"{job_id}/status.json")
#     except Exception as e:
#         logger.error("Storage client init failed: %s", e)
#         raise HTTPException(status_code=503, detail="Storage configuration error")

@router.get("/{job_id}", operation_id="getProgress")
async def get_progress(
    job_id: str = Path(..., description="Task ID for progress tracking"),
    # Remove the unused dependency if it served no purpose
    # _: None = Depends(lambda: None),
):
    """Return the current progress for the specified job.

    The endpoint is designed for polling from the client every few seconds.
    If the job has not been created yet, it returns an initial *pending* state
    with `progress=0`.
    """

    try:
        # Get the sync service client using the shared helper
        service_client = get_sync_blob_service_client()
        # Get the specific blob client for the status file
        blob_client = service_client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=f"{job_id}/status.json")

        if blob_client.exists():
            blob_bytes = blob_client.download_blob().readall()
            update = ProgressUpdate.model_validate_json(blob_bytes)
            payload = {
                "progress": update.progress,
                "statusMessage": update.message,
                "isComplete": update.status in (JobStatus.COMPLETED, JobStatus.FAILED),
                "status": update.status.value,
            }
        else:
            payload = {
                "progress": 0,
                "statusMessage": "Початок аналізу",
                "isComplete": False,
                "status": JobStatus.PENDING.value,
            }

        return JSONResponse(status_code=200, content=payload)

    except HTTPException:
        raise  # Re-raise known HTTP exceptions
    except Exception as err:
        # Log the exception with stack trace for better debugging
        logger.exception("Failed to retrieve progress for job %s: %s", job_id, err)
        # Return a generic 500 error
        raise HTTPException(status_code=500, detail="Error retrieving job progress")
