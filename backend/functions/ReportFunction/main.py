import logging
import json
import os
import azure.functions as func
from azure.storage.blob import BlobServiceClient, BlobClient
from azure.core.exceptions import ResourceNotFoundError
from pydantic import ValidationError

# Revert to relative import for shared code within the functions package
from ..shared_code.schemas import ReportData, ErrorResponse, JobStatus

# Environment variable for Azure Storage connection string
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
# Blob container name where reports are stored
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")

# Remove Blueprint
# bp = func.Blueprint()

# @bp.route(route="report/{jobId}", methods=["GET"])
def main(req: func.HttpRequest) -> func.HttpResponse:
    job_id = req.route_params.get('jobId')
    logging.info(f'Python HTTP trigger function processed a /report/{job_id} request.')

    if not job_id:
        error_resp = ErrorResponse(message="Please provide a jobId in the path.")
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=400
        )

    if not AZURE_STORAGE_CONNECTION_STRING:
        logging.error("AzureWebJobsStorage connection string is not set.")
        error_resp = ErrorResponse(message="Internal server configuration error.")
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=500
        )

    try:
        # Use context manager for service client if possible, or ensure close
        # For sync client, direct instantiation is common in Functions
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        blob_client: BlobClient = blob_service_client.get_blob_client(
            container=REPORTS_CONTAINER_NAME,
            blob=f"{job_id}.json"
        )

        # Check if the report blob exists
        if not blob_client.exists():
            logging.warning(f"Report blob not found for Job ID: {job_id}")
            error_resp = ErrorResponse(message=f"Report not found for Job ID: {job_id}")
            return func.HttpResponse(
                 error_resp.model_dump_json(),
                 mimetype="application/json",
                 status_code=404
            )

        # Check status from metadata *before* downloading potentially large data
        status = None
        try:
            properties = blob_client.get_blob_properties()
            metadata = properties.metadata or {}
            status = metadata.get("jobStatus")
            logging.info(f"Job {job_id} metadata status: {status}")
        except ResourceNotFoundError:
             # Should have been caught by blob_client.exists(), but handle defensively
             logging.warning(f"Report blob not found (ResourceNotFoundError) for Job ID: {job_id} after exists() check.")
             error_resp = ErrorResponse(message=f"Report not found for Job ID: {job_id}")
             return func.HttpResponse(error_resp.model_dump_json(), mimetype="application/json", status_code=404)
        except Exception as meta_e:
            logging.warning(f"Could not read metadata for job {job_id}, proceeding to download content: {meta_e}")
            # If metadata fails, we might still try to read the blob content

        # If status is not COMPLETED, return a specific response
        if status != JobStatus.COMPLETED.value:
            try:
                status_enum = JobStatus(status) if status in JobStatus.__members__ else JobStatus.PENDING
            except ValueError:
                status_enum = JobStatus.PENDING # Default if invalid status string

            current_state = ReportData(jobId=job_id, status=status_enum)
            logging.info(f"Report for Job ID {job_id} is not yet COMPLETED (Status: {status}). Returning current state.")
            return func.HttpResponse(
                current_state.model_dump_json(exclude_none=True),
                mimetype="application/json",
                status_code=200 # Return 200 OK, client must check the status field
            )

        # Download and parse the full report data only if COMPLETED
        logging.info(f"Job {job_id} status is COMPLETED, downloading report content.")
        blob_data = blob_client.download_blob().readall()
        report_content = json.loads(blob_data)

        # Validate the stored data against the Pydantic model
        try:
            report_data = ReportData.model_validate(report_content)
            # Ensure status in the body matches metadata (metadata is more current)
            report_data.status = JobStatus.COMPLETED
        except ValidationError as val_e:
            logging.error(f"Stored report data for job {job_id} failed validation: {val_e}", exc_info=True)
            error_resp = ErrorResponse(message=f"Failed to parse completed report data for job {job_id}", details=val_e.errors())
            return func.HttpResponse(
                error_resp.model_dump_json(),
                mimetype="application/json",
                status_code=500
            )

        return func.HttpResponse(
            report_data.model_dump_json(exclude_none=True),
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        logging.error(f"An error occurred while fetching report for Job ID {job_id}: {e}", exc_info=True)
        error_resp = ErrorResponse(message="Internal server error while fetching report.")
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=500
        ) 