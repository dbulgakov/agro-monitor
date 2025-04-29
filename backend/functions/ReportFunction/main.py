# functions/ReportFunction/main.py

import logging
import json
import os
import azure.functions as func
from azure.storage.blob import BlobServiceClient, BlobClient
from azure.core.exceptions import ResourceNotFoundError
from pydantic import ValidationError

from ..shared_code.schemas import ReportData, ErrorResponse, JobStatus
from ..shared_code.helpers import check_environment_variables

# Required environment variables
REQUIRED_ENV_VARS = ["AzureWebJobsStorage", "REPORTS_CONTAINER_NAME"]

# Load config once
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")


def main(req: func.HttpRequest) -> func.HttpResponse:
    # 1) Validate env
    try:
        check_environment_variables(REQUIRED_ENV_VARS)
    except ValueError as cfg_err:
        logging.critical(f"Config error: {cfg_err}")
        resp = ErrorResponse(message="Internal server configuration error.")
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=503)

    # 2) Grab jobId from query parameter (common practice for GET)
    job_id = req.params.get("jobId")
    log = logging.LoggerAdapter(logging.getLogger(__name__), {"job_id": job_id or "NO_JOB_ID"})
    if not job_id:
        log.error("Missing jobId parameter.")
        resp = ErrorResponse(message="jobId parameter is required")
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=400)

    log.info(f"Handling report request for Job ID: {job_id}")

    # 3) Init blob client
    try:
        # Use the connection string directly
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        blob_client: BlobClient = blob_service_client.get_blob_client(
            container=REPORTS_CONTAINER_NAME, blob=f"{job_id}.json"
        )
    except ValueError as verr:
        log.error(f"Invalid connection string or configuration: {verr}")
        resp = ErrorResponse(message="Server configuration error (Storage).", details=str(verr))
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)
    except Exception as e:
        log.error(f"Blob client initialization failed: {e}", exc_info=True)
        resp = ErrorResponse(message="Failed to connect to storage.", details=str(e))
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)

    # --- Check Blob Existence and Status --- #
    try:
        if not blob_client.exists():
            log.warning(f"Report blob '{job_id}.json' not found.")
            resp = ErrorResponse(message=f"Report not found.", jobId=job_id)
            return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=404)

        # Get properties to check status metadata
        properties = blob_client.get_blob_properties()
        metadata = properties.metadata or {}
        status_str = metadata.get("jobStatus")
        progress_str = metadata.get("jobProgress", "-1")
        message_str = metadata.get("jobMessage")

        try:
            status_enum = JobStatus(status_str) if status_str else JobStatus.PENDING
        except ValueError:
            log.warning(f"Invalid status '{status_str}' in metadata, defaulting to PENDING.")
            status_enum = JobStatus.PENDING # Fallback for invalid status string

        log.info(f"Blob exists. Metadata status={status_enum.value}, progress={progress_str}")

        # ----- NOT COMPLETED ----- #
        if status_enum != JobStatus.COMPLETED:
            partial_report = ReportData(
                jobId=job_id,
                status=status_enum,
                # Add errorMessage if status is FAILED
                errorMessage=message_str if status_enum == JobStatus.FAILED else None
                # Other fields default to None
            )
            # Use 202 for PENDING/PROCESSING, 200 for FAILED (contains errorMessage)
            http_status_code = 202 if status_enum in [JobStatus.PENDING, JobStatus.PROCESSING] else 200
            log.info(f"Returning partial report (Status: {status_enum.value}), HTTP {http_status_code}")
            return func.HttpResponse(
                partial_report.model_dump_json(exclude_none=True),
                mimetype="application/json",
                status_code=http_status_code
            )

        # ----- COMPLETED: Download and Validate ----- #
        log.info("Status is COMPLETED, downloading full report content.")
        try:
            downloader = blob_client.download_blob()
            raw_content = downloader.readall()
            report_dict = json.loads(raw_content)
        except json.JSONDecodeError as je:
            log.error(f"Failed to decode JSON from blob: {je}", exc_info=True)
            resp = ErrorResponse(message="Failed to read report content (invalid JSON).", jobId=job_id, details=str(je))
            return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)
        except Exception as download_exc:
            log.error(f"Failed to download blob content: {download_exc}", exc_info=True)
            resp = ErrorResponse(message="Failed to download report content.", jobId=job_id, details=str(download_exc))
            return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)

        # Validate the downloaded content against the schema
        try:
            # Ensure status in the validated model reflects COMPLETION
            report_data = ReportData.model_validate(report_dict)
            if report_data.status != JobStatus.COMPLETED:
                log.warning(f"Report content status ('{report_data.status}') differs from metadata ('COMPLETED'). Using content status.")
                # Keep status from content, or force COMPLETED?
                # Let's trust the content but maybe log a bigger warning.
            # Ensure the main status field matches the content if validation passes
            final_report = report_data

        except ValidationError as ve:
            log.error(f"Downloaded report content failed validation: {ve}", exc_info=True)
            # Use the message expected by the test
            resp = ErrorResponse(message="Failed to validate report content", jobId=job_id, details=ve.errors())
            return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)

        # ----- SUCCESS: Return COMPLETED report ----- #
        log.info("Successfully validated completed report. Returning full data, HTTP 200.")
        return func.HttpResponse(
            final_report.model_dump_json(exclude_none=True),
            mimetype="application/json",
            status_code=200
        )

    except ResourceNotFoundError:
        # This might happen if the blob disappears between exists() and get_blob_properties()
        log.warning(f"Report blob '{job_id}.json' disappeared unexpectedly.")
        resp = ErrorResponse(message=f"Report not found.", jobId=job_id)
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=404)
    except Exception as e:
        # Catch-all for other unexpected errors (e.g., storage connection issues during access)
        log.error(f"Failed to retrieve report status or content for job {job_id}: {e}", exc_info=True)
        resp = ErrorResponse(message="Failed to retrieve report status or content.", jobId=job_id, details=str(e))
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)
