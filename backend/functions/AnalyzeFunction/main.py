import logging
import json
import uuid
import os
import azure.functions as func
from azure.storage.queue import QueueClient, TextBase64EncodePolicy
from pydantic import ValidationError

from shared_code.schemas import StartAnalysisPayload, StartAnalysisResponse, ErrorResponse

AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
ANALYSIS_QUEUE_NAME = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed an /analyze request.')

    if not AZURE_STORAGE_CONNECTION_STRING:
        logging.error("AzureWebJobsStorage connection string is not set.")
        error_resp = ErrorResponse(message="Internal server configuration error.")
        return func.HttpResponse(
             error_resp.model_dump_json(),
             mimetype="application/json",
             status_code=500
        )

    try:
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
        payload = StartAnalysisPayload.model_validate(req_body)
        logging.info(f"Received valid analysis request for area type: {payload.area.geometry.type.value}")

        job_id = str(uuid.uuid4())
        logging.info(f"Generated Job ID: {job_id}")

        queue_message = {
            "jobId": job_id,
            "payload": payload.model_dump()
        }

        try:
            queue_client = QueueClient.from_connection_string(
                conn_str=AZURE_STORAGE_CONNECTION_STRING,
                queue_name=ANALYSIS_QUEUE_NAME,
                message_encode_policy=TextBase64EncodePolicy()
            )
            encoded_message = json.dumps(queue_message)
            queue_client.send_message(encoded_message)
            logging.info(f"Successfully sent message for Job ID {job_id} to queue '{ANALYSIS_QUEUE_NAME}'.")

        except Exception as e:
            logging.error(f"Failed to send message to queue '{ANALYSIS_QUEUE_NAME}' for Job ID {job_id}: {e}", exc_info=True)
            error_resp = ErrorResponse(message="Failed to queue analysis job.")
            return func.HttpResponse(
                 error_resp.model_dump_json(),
                 mimetype="application/json",
                 status_code=500
            )

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