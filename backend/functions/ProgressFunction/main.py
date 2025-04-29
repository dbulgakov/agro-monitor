import logging
import json
import asyncio
import time
import os
import azure.functions as func
from azure.storage.blob.aio import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError

# Revert to relative import for shared code within the functions package
from ..shared_code.schemas import ErrorResponse, ProgressUpdate, JobStatus

# Environment variable for Azure Storage connection string
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
# Blob container name where status/progress is tracked
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
PROGRESS_CHECK_INTERVAL_SECONDS = 5 # How often to check for progress updates

# Remove Blueprint
# bp = func.Blueprint()

async def progress_generator(job_id: str):
    """Async generator to yield SSE progress updates."""
    # Use adapter for logs within this specific job's stream
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})

    last_status_str = None
    last_progress_val = -1
    initial_pending_sent = False

    if not AZURE_STORAGE_CONNECTION_STRING:
        log_adapter.error("AzureWebJobsStorage connection string is not set for progress generator.")
        error_data = ErrorResponse(message="Internal server configuration error.")
        yield f"event: error\ndata: {error_data.model_dump_json()}\n\n"
        return

    try:
        # Use context manager for async client
        async with BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING) as blob_service_client:
            blob_client = blob_service_client.get_blob_client(
                container=REPORTS_CONTAINER_NAME,
                blob=f"{job_id}.json"
            )

            while True:
                current_status = JobStatus.PENDING
                current_progress = 0
                status_message = None
                is_terminal = False
                update_detected = False

                try:
                    properties = await blob_client.get_blob_properties()
                    metadata = properties.metadata or {}
                    current_status_str = metadata.get("jobStatus", JobStatus.PENDING.value)
                    try:
                        current_status = JobStatus(current_status_str)
                    except ValueError:
                        log_adapter.warning(f"Invalid status '{current_status_str}' in metadata, defaulting to FAILED.")
                        current_status = JobStatus.FAILED # Treat unknown status as failed

                    current_progress = int(metadata.get("jobProgress", "0"))
                    status_message = metadata.get("jobMessage")
                    is_terminal = current_status in [JobStatus.COMPLETED, JobStatus.FAILED]

                    # Detect change
                    if current_status.value != last_status_str or current_progress != last_progress_val:
                        update_detected = True
                        last_status_str = current_status.value
                        last_progress_val = current_progress
                        initial_pending_sent = True # Mark as sent if we get any real status
                        log_adapter.info(f"Detected progress change: Status={current_status.value}, Progress={current_progress}%")

                except ResourceNotFoundError:
                    # Blob doesn't exist yet, job is pending
                    current_status = JobStatus.PENDING
                    current_progress = 0
                    is_terminal = False
                    if not initial_pending_sent:
                        log_adapter.info("Blob not found, sending initial PENDING status.")
                        update_detected = True # Send initial pending update
                        initial_pending_sent = True
                        last_status_str = JobStatus.PENDING.value
                        last_progress_val = 0
                    else:
                        log_adapter.debug("Blob not found, but initial PENDING sent. Waiting...")

                except Exception as e:
                    log_adapter.error(f"Error checking progress: {e}", exc_info=True)
                    error_data = ErrorResponse(message="Error checking job progress.")
                    yield f"event: error\ndata: {error_data.model_dump_json()}\n\n"
                    break # Stop streaming on error

                # Send update if detected
                if update_detected:
                    progress_data = ProgressUpdate(
                        jobId=job_id,
                        status=current_status,
                        progress=current_progress,
                        message=status_message,
                        timestamp=time.time()
                    )
                    yield f"event: progress\ndata: {progress_data.model_dump_json(exclude_none=True)}\n\n"

                # If terminal state reached, send final confirmation and stop
                if is_terminal:
                    log_adapter.info(f"Job reached terminal state: {current_status.value}. Stopping progress stream.")
                    final_data = ProgressUpdate(
                        jobId=job_id,
                        status=current_status,
                        progress=current_progress,
                        message=status_message,
                        timestamp=time.time()
                    )
                    # Send final update as 'complete' event type
                    yield f"event: complete\ndata: {final_data.model_dump_json(exclude_none=True)}\n\n"
                    break # Stop streaming

                # Wait before checking again
                await asyncio.sleep(PROGRESS_CHECK_INTERVAL_SECONDS)

    except Exception as e:
        log_adapter.error(f"Unhandled exception in progress generator: {e}", exc_info=True)
        error_data = ErrorResponse(message="Internal server error during progress streaming.")
        try:
            yield f"event: error\ndata: {error_data.model_dump_json()}\n\n"
        except Exception as yield_e:
            log_adapter.error(f"Failed to yield final error message: {yield_e}")
    finally:
        log_adapter.info(f"Progress stream generator finished.")
        # Async client context manager handles closing

# @bp.route(route="progress/{jobId}", methods=["GET"])
def main(req: func.HttpRequest) -> func.HttpResponse:
    job_id = req.route_params.get('jobId')
    # Use adapter for initial request log
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id or 'UNKNOWN'})
    log_adapter.info(f'Python HTTP trigger function processed a /progress SSE request.')

    if not job_id:
        error_resp = ErrorResponse(message="Please provide a jobId in the path.")
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=400
        )

    headers = {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive'
    }

    # Return streaming response using the async generator
    return func.HttpResponse(
        body=progress_generator(job_id),
        status_code=200,
        headers=headers,
        mimetype='text/event-stream'
    ) 