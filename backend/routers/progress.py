import os
import json
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Adjust imports based on your project structure
try:
    from ..utils.azure_storage import get_blob_service_client
    # If models are defined elsewhere, import them:
    # from ..models import JobProgress
except ImportError:
    # Fallback for potential different structure or direct run
    from utils.azure_storage import get_blob_service_client
    # Define models here if not imported
    class JobProgress(BaseModel):
        progress: int
        statusMessage: str
        isComplete: bool
        details: dict | None = None

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Config --- #
PROGRESS_CONTAINER_NAME = os.getenv("PROGRESS_CONTAINER_NAME", "job-status")

@router.get("/{jobId}", tags=["Progress"])
async def get_job_progress_sse(jobId: str, request: Request):
    logger.info(f"SSE connection requested for job: {jobId}")

    # Initial check if job exists (optional but recommended)
    blob_service_client = await get_blob_service_client()
    if not blob_service_client:
        logger.error("SSE: Storage service unavailable.")
        raise HTTPException(status_code=503, detail="Storage service unavailable.")

    try:
        async with blob_service_client:
            progress_container_client = blob_service_client.get_container_client(PROGRESS_CONTAINER_NAME)
            progress_blob_client = progress_container_client.get_blob_client(f"{jobId}.json")
            if not await progress_blob_client.exists():
                raise HTTPException(status_code=404, detail=f"Job {jobId} not found.")
    except HTTPException as e:
        raise e
    except Exception as e:
         logger.error(f"SSE: Error checking initial job status {jobId}: {e}")
         raise HTTPException(status_code=500, detail="Error checking job status.")

    async def event_stream():
        last_progress_data = None
        while True:
            if await request.is_disconnected():
                logger.info(f"SSE client disconnected for job: {jobId}")
                break

            current_progress_data = None
            try:
                blob_service_client_stream = await get_blob_service_client() # Get client again for loop
                if not blob_service_client_stream:
                    logger.error("SSE loop: Storage service unavailable. Cannot fetch progress.")
                    await asyncio.sleep(5)
                    continue

                async with blob_service_client_stream:
                    container_client = blob_service_client_stream.get_container_client(PROGRESS_CONTAINER_NAME)
                    blob_client = container_client.get_blob_client(f"{jobId}.json")
                    if await blob_client.exists():
                        download_stream = await blob_client.download_blob()
                        current_progress_raw = await download_stream.readall()
                        current_progress_data = json.loads(current_progress_raw)
                    else:
                        logger.warning(f"SSE loop: Progress blob for job {jobId} disappeared.")
                        yield f"event: error\ndata: Job progress lost\n\n"
                        break

            except Exception as e:
                logger.error(f"SSE loop: Failed to get progress for job {jobId}: {e}", exc_info=True)
                await asyncio.sleep(5)
                continue

            if current_progress_data and current_progress_data != last_progress_data:
                try:
                    # Validate data fetched from blob against Pydantic model if available
                    try: # Add validation block
                        progress_update = JobProgress.model_validate(current_progress_data)
                        yield f"data: {progress_update.model_dump_json()}\n\n"
                        last_progress_data = current_progress_data # Store raw dict for comparison
                        if progress_update.isComplete:
                            logger.info(f"SSE stream complete for job: {jobId}")
                            break
                    except Exception as validation_error: # Catch validation error specifically
                        logger.error(f"SSE loop: Invalid progress data format for job {jobId}: {validation_error}")
                        # Send an error event to the client about the data format?
                        yield f"event: error\ndata: Invalid progress data format\n\n"
                        await asyncio.sleep(2)
                        continue # Continue trying to poll
                except Exception as yield_error: # Catch errors during yield/processing
                     logger.error(f"SSE loop: Error processing/yielding data for job {jobId}: {yield_error}")
                     await asyncio.sleep(2)
                     continue

            await asyncio.sleep(2) # Poll interval

    return StreamingResponse(event_stream(), media_type="text/event-stream") 