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

    # Always try to read the full report blob if it exists, regardless of status
    try:
        if report_blob.exists():
            raw = report_blob.download_blob().readall()
            rpt = ReportData.model_validate_json(raw)
            # Ensure the status from the report blob matches the status blob if both exist
            if rpt.status != status_val:
                 logging.warning(f"Status mismatch for job {job_id}: status.json says {status_val}, report.json says {rpt.status}. Returning report.json status.")
            return rpt
        # If report blob doesn't exist, handle based on status_val
        elif status_val != JobStatus.COMPLETED:
             # For non-completed jobs without a report file, return partial status
             partial = ReportData(jobId=job_id, status=status_val)
             code = status.HTTP_202_ACCEPTED if status_val in (JobStatus.PENDING, JobStatus.PROCESSING) else status.HTTP_200_OK
             # Use the model directly as response body for non-200 codes if desired, or keep in detail
             # For consistency, let's return the partial model directly with 200 for FAILED
             if status_val == JobStatus.FAILED:
                  return partial # Return the partial model directly with 200 OK
             else:
                  # For PENDING/PROCESSING, raise 202 with detail
                  raise HTTPException(status_code=status.HTTP_202_ACCEPTED, detail=partial.model_dump(exclude_none=True))
        else:
            # Status is COMPLETED but report.json is missing
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed report data is missing.")

    except ResourceNotFoundError: # Should be caught by report_blob.exists(), but just in case
         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report data not found (ResourceNotFoundError).")
    except ValidationError as ve:
        logging.error(f"Validation error reading report for job {job_id}: {ve}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error reading report data: {ve}")
    except Exception as e:
        logging.error(f"Error retrieving report for job {job_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal error retrieving report: {e}")