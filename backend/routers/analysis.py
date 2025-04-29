import json
import os
import uuid

from azure.storage.queue.aio import QueueClient
from fastapi import APIRouter, Depends, HTTPException, status

from functions.lib.helpers.job_status import update_job_status
from functions.lib.helpers.env_helpers import check_environment_variables, get_required_env_vars
from functions.lib.schemas import StartAnalysisResponse, StartAnalysisPayload, JobStatus, ErrorResponse

router = APIRouter(tags=["Analysis"])

async def get_queue_client():
    conn = os.getenv("AzureWebJobsStorage")
    name = os.getenv("ANALYSIS_QUEUE_NAME")
    if not conn or not name:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue configuration error")
    return QueueClient.from_connection_string(conn, queue_name=name)

@router.post(
    "",
    response_model=StartAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="startAnalysis",
    responses={
        202: {"description": "Задача принята в обработку", "model": StartAnalysisResponse},
        400: {"description": "Неверные параметры запроса", "model": ErrorResponse},
        500: {"description": "Внутренняя ошибка сервера", "model": ErrorResponse},
    },
)
async def start_analysis(
    payload: StartAnalysisPayload,
    _: None = Depends(lambda: check_environment_variables(get_required_env_vars("start_analysis"))),
    queue: QueueClient = Depends(get_queue_client)
):
    job_id = str(uuid.uuid4())
    await update_job_status(job_id, JobStatus.PENDING, 0, "Analysis request received")
    message = json.dumps({"jobId": job_id, "payload": payload.model_dump()})
    async with queue:
        await queue.send_message(message)
    return StartAnalysisResponse(jobId=job_id)