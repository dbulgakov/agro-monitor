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
    conn = os.getenv("AzureWebJobsStorage")
    container = os.getenv("REPORTS_CONTAINER_NAME")
    if not conn or not container:
        raise HTTPException(status_code=503, detail="Storage configuration error")
    client = BlobServiceClient.from_connection_string(conn)
    return client.get_blob_client(container=container, blob=f"{job_id}/status.json")

async def progress_event_generator(job_id: str, request: Request):
    blob_client = await get_blob_client(job_id)
    last = None
    interval = float(os.getenv("PROGRESS_CHECK_INTERVAL_SECONDS", "2"))
    while True:
        if await request.is_disconnected():
            break
        try:
            if await blob_client.exists():
                data = await (await blob_client.download_blob()).readall()
                upd = ProgressUpdate.model_validate_json(data)
                payload = upd.model_dump()
            else:
                payload = ProgressUpdate(
                    jobId=job_id,
                    status=JobStatus.PENDING,
                    progress=0,
                    timestamp=time.time()
                ).model_dump()
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

        await asyncio.sleep(interval)

@router.get(
    "/{job_id}",
    operation_id="subscribeProgress",
    responses={
        200: {
            "description": "Успешная подписка на поток событий. Поток завершается при завершении задачи или ошибке.",
            "content": {"text/event-stream": {}}
        },
        404: {"description": "Задача не найдена", "model": ErrorResponse},
        500: {"description": "Внутренняя ошибка сервера при инициализации SSE", "model": ErrorResponse},
    },
)
async def stream_progress(
    request: Request,
    job_id: str = Path(..., description="ID задачи для отслеживания прогресса"),
    _: None = Depends(lambda: check_environment_variables(get_required_env_vars("progress")))
):
    return StreamingResponse(progress_event_generator(job_id, request), media_type="text/event-stream")