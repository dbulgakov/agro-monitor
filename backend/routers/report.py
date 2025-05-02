import os
import json
import logging

from fastapi import APIRouter, Depends, Path, HTTPException, status
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError
from pydantic import ValidationError

from shared_code.helpers.env_helpers import check_environment_variables, get_required_env_vars
from shared_code.schemas import ReportData, ErrorResponse, ProgressUpdate, JobStatus

router = APIRouter(tags=["Report"])

def get_blob_service_client():
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Storage configuration error: AZURE_STORAGE_CONNECTION_STRING environment variable is not set")
    return BlobServiceClient.from_connection_string(conn)

@router.get(
    "/{job_id}",
    response_model=ReportData,
    operation_id="getReport",
    responses={
        200: {"description": "Successfully retrieved report data", "model": ReportData},
        404: {"description": "Report not found", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
)
def get_report(
    job_id: str = Path(..., description="Task ID received when starting analysis"),
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    try:
        if report_blob.exists():
            raw = report_blob.download_blob().readall()
            rpt = ReportData.model_validate_json(raw)
            if rpt.status != status_val:
                 logging.warning(f"Status mismatch for job {job_id}: status.json says {status_val}, report.json says {rpt.status}. Returning report.json status.")
            return rpt
        elif status_val != JobStatus.COMPLETED:
             partial = ReportData(jobId=job_id, status=status_val)
             if status_val == JobStatus.FAILED:
                  return partial
             else:
                  raise HTTPException(status_code=status.HTTP_202_ACCEPTED, detail=partial.model_dump(exclude_none=True))
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed report data is missing.")

    except ResourceNotFoundError:
         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report data not found (ResourceNotFoundError).")
    except ValidationError as ve:
        logging.error(f"Validation error reading report for job {job_id}: {ve}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error reading report data: {ve}")
    except Exception as e:
        logging.error(f"Error retrieving report for job {job_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal error retrieving report: {e}")