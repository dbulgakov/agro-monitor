import os
import json
import asyncio
import logging

from fastapi import APIRouter, Depends, Path, Request, HTTPException
from fastapi.responses import StreamingResponse
from azure.storage.blob.aio import BlobServiceClient

from shared_code.schemas import ProgressUpdate, JobStatus, ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Progress", "SSE"])

AZURE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
REPORTS_CONTAINER = os.getenv("REPORTS_CONTAINER_NAME")
PROGRESS_INTERVAL = float(os.getenv("PROGRESS_CHECK_INTERVAL_SECONDS", "5"))
PROGRESS_MAX_CHECKS = int(os.getenv("PROGRESS_MAX_CHECKS", "60"))

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

async def progress_event_generator(job_id: str, request: Request):
    blob_client = await get_blob_client(job_id)
    last_payload_str = None
    check_count = 0

    logger.info("Starting progress monitoring for job %s", job_id)

    # Initial payload if blob doesn't exist
    if not await blob_client.exists():
        initial = {
            "progress": 0,
            "statusMessage": "Analysis request received",
            "isComplete": False,
        }
        payload_str = json.dumps(initial)
        yield f"data: {payload_str}\n\n"
        last_payload_str = payload_str

    while True:
        if await request.is_disconnected():
            logger.info("Client disconnected for job %s", job_id)
            break

        if check_count >= PROGRESS_MAX_CHECKS:
            timeout = {
                "progress": 100,
                "statusMessage": f"Timeout after {PROGRESS_INTERVAL * PROGRESS_MAX_CHECKS:.0f}s",
                "isComplete": True,
            }
            timeout_str = json.dumps(timeout)
            yield f"data: {timeout_str}\n\n"
            break

        try:
            payload = None
            if await blob_client.exists():
                blob_bytes = await (await blob_client.download_blob()).readall()
                update = ProgressUpdate.model_validate_json(blob_bytes)
                payload = {
                    "progress": update.progress,
                    "statusMessage": update.message,
                    "isComplete": update.status in (JobStatus.COMPLETED, JobStatus.FAILED),
                }
            elif last_payload_str is None:
                payload = {
                    "progress": 0,
                    "statusMessage": "Analysis request received",
                    "isComplete": False,
                }

            if payload is not None:
                payload_str = json.dumps(payload)
                if payload_str != last_payload_str:
                    yield f"data: {payload_str}\n\n"
                    last_payload_str = payload_str
                if payload.get("isComplete"):
                    logger.info("Job %s complete: %s", job_id, payload["statusMessage"])
                    break

        except Exception as err:
            logger.exception("Error checking progress for job %s", job_id)
            error = {
                "progress": 100,
                "statusMessage": f"Error: {err}",
                "isComplete": True,
            }
            error_str = json.dumps(error)
            yield f"data: {error_str}\n\n"
            break

        check_count += 1
        await asyncio.sleep(PROGRESS_INTERVAL)

    logger.info("Stopping progress monitoring for job %s", job_id)

@router.get(
    "/{job_id}",
    operation_id="subscribeProgress",
    responses={200: {"description": "SSE stream", "content": {"text/event-stream": {}}}},
)
async def stream_progress(
    request: Request,
    job_id: str = Path(..., description="Task ID for progress tracking"),
    _: None = Depends(lambda: None),
):
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        progress_event_generator(job_id, request),
        media_type="text/event-stream",
        headers=headers,
    )
