import logging
import json
import uuid
import os
import azure.functions as func
# Use async queue client
from azure.storage.queue.aio import QueueClient
# Keep sync policy for now as helper is sync
from azure.storage.queue import TextBase64EncodePolicy
from pydantic import ValidationError

# Revert to relative import for shared code within the functions package
from ..shared_code.schemas import StartAnalysisPayload, StartAnalysisResponse, ErrorResponse, JobStatus
# Keep using sync helper for now
from ..shared_code.helpers import update_job_status, check_environment_variables

# Define required variables, but check inside main
REQUIRED_ENV_VARS = [
    "AzureWebJobsStorage",
    "REPORTS_CONTAINER_NAME",
    "IMAGES_CONTAINER_NAME",
    "ANALYSIS_QUEUE_NAME"
    # OPENAI_API_KEY is optional, checked where needed
]

# Get variables - they might be None if not set, check guards against this
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
ANALYSIS_QUEUE_NAME = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests") # Default used in function.json binding

# Change to async def
async def main(req: func.HttpRequest) -> func.HttpResponse:
    # Check environment variables (sync check is okay here)
    try:
        check_environment_variables(REQUIRED_ENV_VARS)
    except ValueError as config_error:
        logging.critical(f"Configuration error: {config_error}. Function cannot proceed.")
        error_resp = ErrorResponse(message="Internal server configuration error.")
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=503 # Service Unavailable due to config
        )

    logging.info('Python HTTP trigger function processed an /analyze request.')

    try:
        # Getting JSON body is sync
        req_body = req.get_json()
    except ValueError:
        logging.error("Invalid JSON received.")
        error_resp = ErrorResponse(message="Please pass a valid JSON object in the request body")
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=400
        )

    job_id = None # Initialize for potential use in exception logging
    try:
        # Validation is sync
        payload = StartAnalysisPayload.model_validate(req_body)
        logging.info(f"Received valid analysis request for area type: {payload.area.geometry.type.value}")

        job_id = str(uuid.uuid4())
        logging.info(f"Generated Job ID: {job_id}")

        # Create initial PENDING status blob - using sync helper for now
        try:
            # This remains a sync call for now
            update_job_status(job_id, JobStatus.PENDING, 0, "Analysis request received and queued.")
            logging.info(f"Initial PENDING status set for Job ID {job_id}")
        except Exception as status_e:
             logging.error(f"Failed to set initial PENDING status for Job ID {job_id}: {status_e}", exc_info=True)
             error_resp = ErrorResponse(message="Failed to initialize analysis job status.")
             return func.HttpResponse(
                 error_resp.model_dump_json(),
                 mimetype="application/json",
                 status_code=500
             )

        queue_message = {
            "jobId": job_id,
            "payload": payload.model_dump()
        }

        # Use async queue client
        try:
            # Initialize async client
            queue_client = QueueClient.from_connection_string(
                conn_str=AZURE_STORAGE_CONNECTION_STRING,
                queue_name=ANALYSIS_QUEUE_NAME,
                message_encode_policy=TextBase64EncodePolicy() # Keep sync policy for now
            )
            # Use async context manager and await send_message
            async with queue_client:
                encoded_message = json.dumps(queue_message)
                await queue_client.send_message(encoded_message)
                logging.info(f"Successfully sent message for Job ID {job_id} to queue '{ANALYSIS_QUEUE_NAME}'.")

        except Exception as e:
            logging.error(f"Failed to send message to queue '{ANALYSIS_QUEUE_NAME}' for Job ID {job_id}: {e}", exc_info=True)
            # Update status to FAILED - using sync helper
            try:
                update_job_status(job_id, JobStatus.FAILED, -1, f"Failed to queue job: {type(e).__name__}: {str(e)[:200]}")
                logging.info(f"Updated status to FAILED for Job ID {job_id} due to queue error.")
            except Exception as status_fail_e:
                 logging.error(f"Additionally failed to update status to FAILED for Job ID {job_id}: {status_fail_e}")

            error_resp = ErrorResponse(message="Failed to queue analysis job.")
            return func.HttpResponse(
                 error_resp.model_dump_json(),
                 mimetype="application/json",
                 status_code=500
            )

        # Sync response creation
        response_data = StartAnalysisResponse(jobId=job_id)
        return func.HttpResponse(
            response_data.model_dump_json(),
            mimetype="application/json",
            status_code=202
        )

    except ValidationError as e:
        job_id_context = f"Job ID {job_id}: " if job_id else ""
        logging.error(f"{job_id_context}Request validation failed: {e}", exc_info=True)
        error_resp = ErrorResponse(message="Invalid request body", details=e.errors())
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=400
        )
    except Exception as e:
        job_id_context = f"Job ID {job_id}: " if job_id else ""
        logging.error(f"{job_id_context}An unexpected error occurred: {e}", exc_info=True)
        error_resp = ErrorResponse(message="Internal server error.")
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=500
        ) 