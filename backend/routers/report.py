import os
import json
import logging

from fastapi import APIRouter, Depends, Path, HTTPException, status
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError
from pydantic import ValidationError

from functions.lib.helpers.env_helpers import check_environment_variables, get_required_env_vars
from functions.lib.schemas import ReportData, ErrorResponse, ProgressUpdate, JobStatus

router = APIRouter(tags=["Report"])

def get_blob_service_client():
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Storage configuration error")
    return BlobServiceClient.from_connection_string(conn)

@router.get(
    "/{job_id}",
    response_model=ReportData,
    operation_id="getReport",
    responses={
        200: {"description": "Успешный ответ с данными отчета", "model": ReportData},
        404: {"description": "Отчет не найден", "model": ErrorResponse},
        500: {"description": "Внутренняя ошибка сервера", "model": ErrorResponse},
    },
)
def get_report(
    job_id: str = Path(..., description="ID задачи, полученный при запуске анализа"),
    _: None = Depends(lambda: check_environment_variables(get_required_env_vars("report"))),
    client: BlobServiceClient = Depends(get_blob_service_client)
):
    container = os.getenv("REPORTS_CONTAINER_NAME")
    status_blob = client.get_blob_client(container=container, blob=f"{job_id}/status.json")
    report_blob = client.get_blob_client(container=container, blob=f"{job_id}/report.json")
    try:
        data = status_blob.download_blob().readall()
        upd = ProgressUpdate.model_validate_json(data)
        status_val = upd.status
    except (ResourceNotFoundError, ValidationError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    if status_val != JobStatus.COMPLETED:
        partial = ReportData(jobId=job_id, status=status_val)
        code = status.HTTP_202_ACCEPTED if status_val in (JobStatus.PENDING, JobStatus.PROCESSING) else status.HTTP_200_OK
        raise HTTPException(status_code=code, detail=partial.model_dump(exclude_none=True))

    try:
        raw = report_blob.download_blob().readall()
        rpt = ReportData.model_validate_json(raw)
        return rpt
    except ResourceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed report not found")
    except ValidationError as ve:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))