import logging
import os
import json
import azure.functions as func
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueServiceClient
from azure.core.exceptions import ServiceRequestError, ClientAuthenticationError

from ..shared_code.helpers import check_environment_variables

# Define required variables, but check inside main
REQUIRED_ENV_VARS = [
    "AzureWebJobsStorage", 
    "REPORTS_CONTAINER_NAME", 
    "IMAGES_CONTAINER_NAME",
    "ANALYSIS_QUEUE_NAME"
]

# Get variables - they might be None if not set, check guards against this
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
ANALYSIS_QUEUE_NAME = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")

def main(req: func.HttpRequest) -> func.HttpResponse:
    # Check environment variables at the beginning of the function execution
    try:
        check_environment_variables(REQUIRED_ENV_VARS)
    except ValueError as config_error:
        logging.critical(f"Configuration error during health check: {config_error}.")
        # Return unhealthy status immediately if config is missing
        health_status = {
            "status": "unhealthy", 
            "checks": { "configuration": f"failed: {config_error}" }
        }
        return func.HttpResponse(
            json.dumps(health_status),
            mimetype="application/json",
            status_code=503 # Service Unavailable
        )

    logging.info('Python HTTP trigger function processed /healthz request.')
    
    health_status = {"status": "healthy", "checks": {}}
    overall_healthy = True
    status_code = 200

    # Check Blob Storage (Reports Container)
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(REPORTS_CONTAINER_NAME)
        container_client.get_container_properties() 
        health_status["checks"]["blob_storage_reports"] = "connected"
    except (ServiceRequestError, ClientAuthenticationError) as e:
        logging.error(f"Health Check: Blob Storage (Reports) connection failed: {e}")
        health_status["checks"]["blob_storage_reports"] = f"failed: {type(e).__name__}"
        overall_healthy = False
    except Exception as e: 
        logging.warning(f"Health Check: Blob Storage (Reports) check issue (e.g., container '{REPORTS_CONTAINER_NAME}' might not exist): {e}")
        health_status["checks"]["blob_storage_reports"] = f"connected (warning: {type(e).__name__})"

    # Check Blob Storage (Images Container)
    try:
        # Reuse client if already created
        if 'blob_service_client' not in locals():
            blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(IMAGES_CONTAINER_NAME)
        container_client.get_container_properties() 
        health_status["checks"]["blob_storage_images"] = "connected"
    except (ServiceRequestError, ClientAuthenticationError) as e:
        logging.error(f"Health Check: Blob Storage (Images) connection failed: {e}")
        health_status["checks"]["blob_storage_images"] = f"failed: {type(e).__name__}"
        overall_healthy = False
    except Exception as e: 
        logging.warning(f"Health Check: Blob Storage (Images) check issue (e.g., container '{IMAGES_CONTAINER_NAME}' might not exist): {e}")
        health_status["checks"]["blob_storage_images"] = f"connected (warning: {type(e).__name__})"

    # Check Queue Storage
    try:
        queue_service_client = QueueServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        queue_client = queue_service_client.get_queue_client(ANALYSIS_QUEUE_NAME)
        queue_client.get_queue_properties() 
        health_status["checks"]["queue_storage"] = "connected"
    except (ServiceRequestError, ClientAuthenticationError) as e:
        logging.error(f"Health Check: Queue Storage connection failed: {e}")
        health_status["checks"]["queue_storage"] = f"failed: {type(e).__name__}"
        overall_healthy = False
    except Exception as e:
        logging.warning(f"Health Check: Queue Storage check issue (e.g., queue '{ANALYSIS_QUEUE_NAME}' might not exist): {e}")
        health_status["checks"]["queue_storage"] = f"connected (warning: {type(e).__name__})"

    if not overall_healthy:
        health_status["status"] = "unhealthy"
        status_code = 503 # Service Unavailable

    return func.HttpResponse(
        json.dumps(health_status),
        mimetype="application/json",
        status_code=status_code
    ) 