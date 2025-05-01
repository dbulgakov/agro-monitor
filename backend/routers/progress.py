import os
import json
import time
import asyncio
import logging

from fastapi import APIRouter, Depends, Path, Request, HTTPException
from fastapi.responses import StreamingResponse
from azure.storage.blob.aio import BlobServiceClient

from shared_code.helpers.env_helpers import check_environment_variables, get_required_env_vars
from shared_code.schemas import ProgressUpdate, JobStatus, ErrorResponse

router = APIRouter(tags=["Progress", "SSE"])

logger = logging.getLogger(__name__)

async def get_blob_client(job_id: str):
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    container = os.getenv("REPORTS_CONTAINER_NAME")
    if not conn or not container:
        raise HTTPException(status_code=503, detail="Storage configuration error: Check AZURE_STORAGE_CONNECTION_STRING and REPORTS_CONTAINER_NAME environment variables")
    client = BlobServiceClient.from_connection_string(conn)
    return client.get_blob_client(container=container, blob=f"{job_id}/status.json")

def format_sse_message(data: dict) -> str:
    return f"data: {json.dumps(data)}\\n\\n"

async def progress_event_generator(job_id: str, request: Request):
    blob_client = await get_blob_client(job_id)
    last_payload_str = None
    interval = float(os.getenv("PROGRESS_CHECK_INTERVAL_SECONDS", "5"))
    max_checks = int(os.getenv("PROGRESS_MAX_CHECKS", "60"))
    check_count = 0

    logger.info(f"Starting progress monitoring for job {job_id}")

    initial_message_sent = False
    try:
        if not await blob_client.exists():
            initial_payload = {
                "progress": 0,
                "statusMessage": "Analysis request received",
                "isComplete": False
            }
            yield format_sse_message(initial_payload)
            last_payload_str = json.dumps(initial_payload)
            initial_message_sent = True
            logger.debug(f"Sent initial PENDING status for job {job_id}")

    except Exception as e:
        logger.error(f"Error checking initial blob status for job {job_id}: {str(e)}")

    while True:
        if await request.is_disconnected():
            logger.info(f"Client disconnected for job {job_id}")
            break

        if check_count >= max_checks:
            logger.warning(f"Progress check timeout for job {job_id} after {check_count} attempts")
            timeout_payload = {
                "progress": 100,
                "statusMessage": f"Progress check timeout after {max_checks * interval} seconds",
                "isComplete": True
            }
            yield format_sse_message(timeout_payload)
            break

        current_payload = None
        try:
            if await blob_client.exists():
                blob_data = await (await blob_client.download_blob()).readall()
                update = ProgressUpdate.model_validate_json(blob_data)

                is_complete = update.status in (JobStatus.COMPLETED, JobStatus.FAILED)
                current_payload = {
                    "progress": update.progress,
                    "statusMessage": update.message,
                    "isComplete": is_complete
                }
                logger.debug(f"Progress update for job {job_id}: {current_payload}")
            elif not initial_message_sent:
                initial_payload = {
                    "progress": 0,
                    "statusMessage": "Analysis request received",
                    "isComplete": False
                }
                yield format_sse_message(initial_payload)
                last_payload_str = json.dumps(initial_payload)
                initial_message_sent = True
                logger.debug(f"Sent PENDING status for job {job_id} (within loop)")
                current_payload = None

        except Exception as e:
            logger.error(f"Error checking progress for job {job_id}: {str(e)}")
            error_payload = {
                "progress": 100,
                "statusMessage": f"An error occurred: {str(e)}",
                "isComplete": True
            }
            yield format_sse_message(error_payload)
            break

        if current_payload:
            current_payload_str = json.dumps(current_payload)
            if current_payload_str != last_payload_str:
                yield format_sse_message(current_payload)
                last_payload_str = current_payload_str
                if current_payload.get("isComplete"):
                    logger.info(f"Job {job_id} ended with isComplete=True. Status message: {current_payload.get('statusMessage')}")
                    break

        check_count += 1
        await asyncio.sleep(interval)
    logger.info(f"Stopping progress monitoring for job {job_id}")

@router.get(
    "/{job_id}",
    operation_id="subscribeProgress",
    responses={
        200: {
            "description": "Successfully subscribed to event stream. Stream ends when task completes or on error.",
            "content": {"text/event-stream": {}}
        },
        404: {"description": "Task not found", "model": ErrorResponse},
        500: {"description": "Internal server error during SSE initialization", "model": ErrorResponse},
    },
)
async def stream_progress(
    request: Request,
    job_id: str = Path(..., description="Task ID for progress tracking"),
    _: None = Depends(lambda: check_environment_variables(get_required_env_vars("progress")))
):
    headers = {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
    }
    return StreamingResponse(progress_event_generator(job_id, request), media_type="text/event-stream", headers=headers)