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

    # 2) Grab jobId
    job_id = req.route_params.get("jobId")
    log = logging.LoggerAdapter(logging.getLogger(__name__), {"job_id": job_id or "NO_JOB_ID"})
    if not job_id:
        log.error("Missing jobId in path.")
        resp = ErrorResponse(message="Please provide a job ID in the URL path")
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=400)

    log.info("Handling report request")

    # 3) Init blob client
    try:
        client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    except Exception as e:
        log.error(f"Blob client init failed: {e}")
        resp = ErrorResponse(message="Server configuration error.")
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)

    blob_name = f"{job_id}.json"
    blob: BlobClient = client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=blob_name)

    # ----- NOT FOUND -----
    if not blob.exists():
        log.warning(f"Report blob '{blob_name}' not found.")
        resp = ErrorResponse(message=f"Report not found for Job ID: {job_id}")
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=404)

    # 4) Read metadata
    status = None
    metadata = {}
    meta_err = False
    try:
        props = blob.get_blob_properties()
        metadata = props.metadata or {}
        status = metadata.get("jobStatus")
        log.info(f"Metadata jobStatus={status}")
    except ResourceNotFoundError:
        log.warning("Blob disappeared during property fetch.")
        resp = ErrorResponse(message=f"Report for Job ID '{job_id}' disappeared unexpectedly.")
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=404)
    except Exception as e:
        log.warning(f"Metadata read failed, defaulting to PENDING: {e}")
        meta_err = True

    # ----- NOT COMPLETED -----
    if status != JobStatus.COMPLETED.value:
        try:
            status_enum = JobStatus(status)
        except Exception:
            status_enum = JobStatus.PENDING
        partial = ReportData(
            jobId=job_id,
            status=status_enum,
            summary=metadata.get("jobMessage")
        )
        code = 202 if meta_err else 200
        log.info(f"Returning partial ({status_enum}), HTTP {code}")
        return func.HttpResponse(
            partial.model_dump_json(exclude_none=True),
            mimetype="application/json",
            status_code=code
        )

    # ----- COMPLETED -----
    log.info("Status=COMPLETED, downloading full report")
    try:
        raw = blob.download_blob().readall()
        body = json.loads(raw)
    except json.JSONDecodeError as je:
        log.error(f"JSON decode failed: {je}")
        resp = ErrorResponse(message="Failed to read completed report data.")
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)
    except Exception as de:
        log.error(f"Download failed: {de}")
        resp = ErrorResponse(message="Failed to retrieve completed report data.")
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)

    # 5) Validate payload
    try:
        report = ReportData.model_validate(body)
        report.status = JobStatus.COMPLETED
    except ValidationError as ve:
        log.error(f"Validation error: {ve}", exc_info=True)
        resp = ErrorResponse(message="Failed to parse completed report data.", details=ve.errors())
        return func.HttpResponse(resp.model_dump_json(), mimetype="application/json", status_code=500)

    log.info("Returning final report, HTTP 200")
    return func.HttpResponse(report.model_dump_json(exclude_none=True), mimetype="application/json", status_code=200)
