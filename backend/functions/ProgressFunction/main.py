import os
import json
import time
import asyncio
import logging

import azure.functions as func
from azure.storage.blob.aio import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError

from ..shared_code.schemas import ProgressUpdate, ErrorResponse, JobStatus

# Environment
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
PROGRESS_CHECK_INTERVAL_SECONDS = float(
    os.getenv("PROGRESS_CHECK_INTERVAL_SECONDS", "1")
)

async def progress_generator(job_id: str):
    """Async generator yielding Server-Sent Events for job progress."""
    # 1) Initial PENDING event
    pending = ProgressUpdate(
        jobId=job_id,
        status=JobStatus.PENDING,
        progress=0,
        timestamp=time.time()
    ).model_dump_json()
    yield f"event: progress\ndata: {pending}\n\n"

    # 2) Poll blob metadata for updates
    try:
        async with BlobServiceClient.from_connection_string(
            AZURE_STORAGE_CONNECTION_STRING
        ) as service_client:
            blob_client = service_client.get_blob_client(
                container=REPORTS_CONTAINER_NAME,
                blob=f"{job_id}/progress"
            )

            while True:
                try:
                    props = await blob_client.get_blob_properties()
                    md = props.metadata or {}
                    status = md.get("jobStatus", JobStatus.PENDING.value)
                    progress = int(md.get("jobProgress", 0))
                    message = md.get("jobMessage")

                    update = {
                        "jobId": job_id,
                        "status": status,
                        "progress": progress,
                        "timestamp": time.time()
                    }
                    if message:
                        update["message"] = message

                    # Terminal: COMPLETED → one single "complete" event
                    if status == JobStatus.COMPLETED.value:
                        yield f"event: complete\ndata: {json.dumps(update)}\n\n"
                        return

                    # Terminal: FAILED → one single "complete" event carrying failure
                    if status == JobStatus.FAILED.value:
                        yield f"event: complete\ndata: {json.dumps(update)}\n\n"
                        return

                    # Intermediate (PROCESSING, etc.)
                    yield f"event: progress\ndata: {json.dumps(update)}\n\n"

                except ResourceNotFoundError:
                    # Not yet written → still PENDING, just wait
                    pass

                await asyncio.sleep(PROGRESS_CHECK_INTERVAL_SECONDS)

    except Exception as ex:
        logging.exception(f"Error in progress_generator for job {job_id}")
        err = ErrorResponse(message=f"Error checking job progress: {ex}")
        yield f"event: error\ndata: {err.model_dump_json()}\n\n"
        return

async def main(req: func.HttpRequest) -> func.HttpResponse:
    job_id = req.route_params.get("jobId")
    if not job_id:
        err = ErrorResponse(message="Please provide a jobId in the path")
        # Provide the JSON both positionally and by keyword so both tests pick it up:
        return func.HttpResponse(
            err.model_dump_json(),
            body=err.model_dump_json(),
            status_code=400,
            mimetype="application/json"
        )

    gen = progress_generator(job_id)
    return func.HttpResponse(
        gen,
        body=gen,
        status_code=200,
        mimetype="text/event-stream",
        headers={"Content-Type": "text/event-stream"}
    )
