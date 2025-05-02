import os
import io
import logging
from typing import Optional
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from azure.storage.blob import BlobServiceClient, ContentSettings

from ..schemas import ReportData

# Load and validate environment configuration
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")

if not all([AZURE_STORAGE_CONNECTION_STRING, IMAGES_CONTAINER_NAME, REPORTS_CONTAINER_NAME]):
    raise ValueError("Missing one or more required environment variables: "
                     "AZURE_STORAGE_CONNECTION_STRING, IMAGES_CONTAINER_NAME, REPORTS_CONTAINER_NAME")

logger = logging.getLogger(__name__)


async def upload_image_to_blob(
    client: AsyncBlobServiceClient,
    job_id: str,
    image_buffer: io.BytesIO,
    image_name: str
) -> Optional[str]:
    log = logger.getChild(job_id)
    try:
        blob_name = f"{job_id}/{image_name}"
        blob_client = client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=blob_name)
        image_buffer.seek(0)
        await blob_client.upload_blob(
            image_buffer.read(),
            overwrite=True,
            content_settings=ContentSettings(content_type="image/png"),
        )
        log.info(f"Uploaded image to {blob_client.url}")
        return blob_client.url
    except Exception as e:
        log.error(f"Failed to upload image: {e}", exc_info=True)
        return None


async def upload_report_to_blob(
    client: AsyncBlobServiceClient,
    job_id: str,
    report_data: ReportData
) -> None:
    log = logger.getChild(job_id)
    try:
        blob_name = f"{job_id}/report.json"
        blob_client = client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=blob_name)
        report_json = report_data.model_dump(exclude_none=True)
        await blob_client.upload_blob(
            report_json.encode("utf-8"),
            overwrite=True,
            metadata={}
        )
        log.info("Uploaded final report.")
    except Exception as e:
        log.error(f"Failed to upload report: {e}", exc_info=True)
        raise


def get_sync_blob_service_client() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(
        AZURE_STORAGE_CONNECTION_STRING, 
        connection_timeout=60, 
        read_timeout=60
    )
