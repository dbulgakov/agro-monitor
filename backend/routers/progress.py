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

async def progress_event_generator(job_id: str, request: Request):
    blob_client = await get_blob_client(job_id)
    last = None
    interval = float(os.getenv("PROGRESS_CHECK_INTERVAL_SECONDS", "5"))
    max_checks = int(os.getenv("PROGRESS_MAX_CHECKS", "60"))
    check_count = 0
    last_sent_time = time.time()
    keep_alive_interval = 30 # seconds
    
    logger.info(f"Starting progress monitoring for job {job_id}")
    
    while True:
        if await request.is_disconnected():
            logger.info(f"Client disconnected for job {job_id}")
            break
            
        if check_count >= max_checks:
            logger.warning(f"Progress check timeout for job {job_id} after {check_count} attempts")
            yield f"data: {json.dumps({'message': f'Progress check timeout after {max_checks * interval} seconds', 'error': True, 'isComplete': True})}

"
            break
            
        payload = None # Initialize payload for the current iteration
        try:
            if await blob_client.exists():
                data = await (await blob_client.download_blob()).readall()
                upd = ProgressUpdate.model_validate_json(data)
                payload = upd.model_dump()
                logger.debug(f"Progress update for job {job_id}: {payload}")
            else:
                if check_count == 0:
                    # Send initial PENDING status if blob doesn't exist yet
                    payload = ProgressUpdate(
                        jobId=job_id,
                        status=JobStatus.PENDING,
                        progress=0,
                        message="Analysis request received",
                        timestamp=time.time()
                    ).model_dump()
                    s = json.dumps(payload)
                    # Removed event tag
                    yield f"data: {s}\n\n"
                    last = s
                    last_sent_time = time.time()
                # Don't break immediately, keep checking for a while
                # break # Removed break

        except Exception as e:
            logger.error(f"Error checking progress for job {job_id}: {str(e)}")
            err = ErrorResponse(message=str(e), error=True, isComplete=True).model_dump()
            # Removed event tag
            yield f"data: {json.dumps(err)}\n\n"
            break

        if payload: # Only yield if we have a payload for this iteration
            s = json.dumps(payload)
            if s != last:
                # Removed event tag
                yield f"data: {s}\n\n"
                last = s
                last_sent_time = time.time()
                if payload["status"] in (JobStatus.COMPLETED, JobStatus.FAILED):
                    logger.info(f"Job {job_id} completed with status {payload['status']}")
                    break
        
        # Send keep-alive if needed
        if time.time() - last_sent_time > keep_alive_interval:
            yield ":keep-alive\n\n"
            last_sent_time = time.time()
            logger.debug(f"Sent keep-alive for job {job_id}")

        check_count += 1
        await asyncio.sleep(interval)

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
    return StreamingResponse(progress_event_generator(job_id, request), media_type="text/event-stream")