import os
import logging

from fastapi import APIRouter, Depends, Path, HTTPException
from fastapi.responses import JSONResponse
from azure.storage.blob.aio import BlobServiceClient

from shared_code.schemas import ProgressUpdate, JobStatus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Progress"])

AZURE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
REPORTS_CONTAINER = os.getenv("REPORTS_CONTAINER_NAME")

async def get_blob_client(job_id: str):
    if not AZURE_CONN_STR or not REPORTS_CONTAINER:
        logger.error("Missing required environment variables for Azure Blob Storage")
        raise HTTPException(status_code=503, detail="Storage configuration error")
    try:
        service = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
        return service.get_blob_client(container=REPORTS_CONTAINER, blob=f"{job_id}/status.json")
    except Exception as e:
        logger.error("Storage client init failed: %s", e)
        raise HTTPException(status_code=503, detail="Storage configuration error")

@router.get("/{job_id}", operation_id="getProgress")
async def get_progress(
    job_id: str = Path(..., description="Task ID for progress tracking"),
    _: None = Depends(lambda: None),
):
    """Return the current progress for the specified job.

    The endpoint is designed for polling from the client every few seconds.
    If the job has not been created yet, it returns an initial *pending* state
    with `progress=0`.
    """

    try:
        blob_client = await get_blob_client(job_id)

        if await blob_client.exists():
            blob_bytes = await (await blob_client.download_blob()).readall()
            update = ProgressUpdate.model_validate_json(blob_bytes)
            payload = {
                "progress": update.progress,
                "statusMessage": update.message,
                "isComplete": update.status in (JobStatus.COMPLETED, JobStatus.FAILED),
            }
        else:
            payload = {
                "progress": 0,
                "statusMessage": "Початок аналізу",
                "isComplete": False,
            }

        return JSONResponse(status_code=200, content=payload)

    except HTTPException:
        raise  # Re-raise to allow FastAPI to handle as intended
    except Exception as err:
        logger.exception("Failed to retrieve progress for job %s", job_id)
        return JSONResponse(
            status_code=500,
            content={
                "message": f"Error retrieving job progress: {err}",
            },
        )
