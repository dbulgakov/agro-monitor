import os
import json
import time
import asyncio
import logging

import azure.functions as func
from azure.storage.blob import BlobServiceClient

from ..shared_code.schemas import ProgressUpdate, ErrorResponse, JobStatus
from ..shared_code.helpers import check_environment_variables

# Required env vars
REQUIRED_ENV_VARS = [
    "AzureWebJobsStorage",
    "REPORTS_CONTAINER_NAME"
]

# Config from environment
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
PROGRESS_CHECK_INTERVAL_SECONDS = float(
    os.getenv("PROGRESS_CHECK_INTERVAL_SECONDS", "2.0")
)

# Pre-init sync client (main uses async)
_blob_service_client = None
if AZURE_STORAGE_CONNECTION_STRING:
    try:
        _blob_service_client = BlobServiceClient.from_connection_string(
            AZURE_STORAGE_CONNECTION_STRING
        )
    except Exception as e:
        logging.error(f"Failed to init BlobServiceClient at module load: {e}")
        _blob_service_client = None


async def progress_generator(job_id: str):
    """Async generator yielding SSE progress events by polling status.json."""
    log = logging.LoggerAdapter(logging.getLogger(__name__), {"job_id": job_id})
    log.info("Starting SSE progress stream.")

    status_blob_path = f"{job_id}/status.json"
    last_payload_str = None
    first_emit = False

    try:
        # Use async client inside generator
        async with BlobServiceClient.from_connection_string(
            AZURE_STORAGE_CONNECTION_STRING
        ) as async_service:
            blob_client = async_service.get_blob_client(
                container=REPORTS_CONTAINER_NAME,
                blob=status_blob_path
            )

            while True:
                payload = None
                status_value = JobStatus.PENDING.value

                try:
                    if await blob_client.exists():
                        downloader = await blob_client.download_blob()
                        raw = await downloader.readall()
                        data = json.loads(raw)

                        # Validate via Pydantic
                        validated = ProgressUpdate.model_validate(data).model_dump()
                        status_value = validated.get("status", JobStatus.PENDING.value)
                        payload = validated
                        log.debug(f"Read status={status_value}, progress={validated.get('progress')}")
                    else:
                        # Blob not yet there → still pending
                        payload = {
                            "jobId": job_id,
                            "status": JobStatus.PENDING.value,
                            "progress": 0,
                            "timestamp": time.time()
                        }
                        log.debug("No status blob yet, emitting initial PENDING if not already.")

                except json.JSONDecodeError as je:
                    log.error(f"JSON decode error for {status_blob_path}: {je}")
                    fail = {
                        "jobId": job_id,
                        "status": JobStatus.FAILED.value,
                        "progress": -1,
                        "message": "Error reading status blob",
                        "timestamp": time.time()
                    }
                    yield f"event: complete\ndata: {json.dumps(fail)}\n\n"
                    log.info("Stream closed due to JSON decode failure.")
                    return

                except Exception as ex:
                    log.error(f"Error checking progress blob {status_blob_path}: {ex}", exc_info=True)
                    # Emit an error event and terminate
                    err_msg = f"Error streaming job progress: {type(ex).__name__}: {ex}"
                    error_payload = ProgressUpdate(
                        jobId=job_id,
                        status=JobStatus.FAILED,
                        progress=-1,
                        message=err_msg,
                        timestamp=time.time()
                    ).model_dump()
                    yield f"event: error\ndata: {json.dumps(error_payload)}\n\n"
                    log.info("Error event emitted, closing SSE stream.")
                    return

                # Only emit on first pass or when payload changes
                payload_str = json.dumps(payload)
                if not first_emit or payload_str != last_payload_str:
                    event_type = "progress"
                    if payload["status"] in (
                        JobStatus.COMPLETED.value,
                        JobStatus.FAILED.value
                    ):
                        event_type = "complete"

                    yield f"event: {event_type}\ndata: {payload_str}\n\n"
                    log.info(f"Sent SSE update: event={event_type}, status={payload['status']}")
                    last_payload_str = payload_str
                    first_emit = True

                    # If we're done, break
                    if event_type == "complete":
                        log.info("Job finished, closing SSE stream.")
                        return

                await asyncio.sleep(PROGRESS_CHECK_INTERVAL_SECONDS)

    except Exception as setup_ex:
        # Failures during client setup
        log = logging.LoggerAdapter(logging.getLogger(__name__), {"job_id": job_id})
        log.error(f"Unhandled init error in progress_generator: {setup_ex}", exc_info=True)
        err = ErrorResponse(
            message=f"Error streaming job progress: {type(setup_ex).__name__}: {setup_ex}"
        )
        yield f"event: error\ndata: {err.model_dump_json()}\n\n"
        # no explicit return needed; generator will exit


async def main(req: func.HttpRequest) -> func.HttpResponse:
    # Validate env
    try:
        check_environment_variables(REQUIRED_ENV_VARS)
    except ValueError as ve:
        logging.critical(f"Config error: {ve}")
        err = ErrorResponse(message="Internal server configuration error.")
        return func.HttpResponse(
            err.model_dump_json(),
            status_code=503,
            mimetype="application/json"
        )

    job_id = req.route_params.get("jobId")
    log = logging.LoggerAdapter(logging.getLogger(__name__), {"job_id": job_id or "NO_JOB_ID"})
    if not job_id:
        log.error("Missing jobId path parameter.")
        err = ErrorResponse(message="Please provide a jobId in the path")
        return func.HttpResponse(
            err.model_dump_json(),
            status_code=400,
            mimetype="application/json"
        )

    log.info("Accepting SSE /progress connection")

    if not _blob_service_client:
        log.error("BlobServiceClient unavailable; cannot stream progress.")
        err = ErrorResponse(message="Server configuration error preventing status updates.")
        return func.HttpResponse(
            err.model_dump_json(),
            status_code=500,
            mimetype="application/json"
        )

    # Return the SSE stream
    event_stream = progress_generator(job_id)
    return func.HttpResponse(
        body=event_stream,
        status_code=200,
        mimetype="text/event-stream",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )
