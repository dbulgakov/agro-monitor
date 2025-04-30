import os
import json
import time
import asyncio
import logging

from fastapi import APIRouter, Depends, Path, Request, HTTPException
from fastapi.responses import StreamingResponse
from azure.storage.blob.aio import BlobServiceClient

from functions.lib.helpers.env_helpers import check_environment_variables, get_required_env_vars
from functions.lib.schemas import ProgressUpdate, JobStatus, ErrorResponse

router = APIRouter(tags=["Progress", "SSE"])

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
    interval = float(os.getenv("PROGRESS_CHECK_INTERVAL_SECONDS", "2"))
    max_checks = int(os.getenv("PROGRESS_MAX_CHECKS", "3"))
    check_count = 0
    
    while True:
        if await request.is_disconnected():
            break
            
        if check_count >= max_checks:
            yield f"event: timeout\ndata: {json.dumps({'message': 'Progress check timeout'})}\n\n"
            break
            
        try:
            if await blob_client.exists():
                data = await (await blob_client.download_blob()).readall()
                upd = ProgressUpdate.model_validate_json(data)
                payload = upd.model_dump()
            else:
                if check_count == 0:
                    payload = ProgressUpdate(
                        jobId=job_id,
                        status=JobStatus.PENDING,
                        progress=0,
                        timestamp=time.time()
                    ).model_dump()
                    yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                break
        except Exception as e:
            err = ErrorResponse(message=str(e)).model_dump()
            yield f"event: error\ndata: {json.dumps(err)}\n\n"
            break

        s = json.dumps(payload)
        if s != last:
            event_type = "complete" if payload["status"] in (JobStatus.COMPLETED, JobStatus.FAILED) else "progress"
            yield f"event: {event_type}\ndata: {s}\n\n"
            last = s
            if event_type == "complete":
                break

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