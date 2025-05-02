import os
import io
import logging
from typing import Optional
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from azure.storage.blob import BlobServiceClient, ContentSettings

from ..schemas import ReportData

AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")


async def upload_image_to_blob(
    client: AsyncBlobServiceClient,
    job_id: str,
    image_buffer: io.BytesIO,
    image_name: str
) -> Optional[str]:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    try:
        blob_name = f"{job_id}/{image_name}"
        blob_client = client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=blob_name)
        image_buffer.seek(0)
        data = image_buffer.getvalue()
        await blob_client.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type="image/png"),
        )
        log_adapter.info(f"Uploaded image to {blob_client.url}")
        return blob_client.url
    except Exception as e:
        log_adapter.error(f"Failed to upload image: {e}", exc_info=True)
        return None


async def upload_report_to_blob(
    client: AsyncBlobServiceClient,
    job_id: str,
    report_data: ReportData
):
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    try:
        blob_name = f"{job_id}/report.json"
        blob_client = client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=blob_name)
        report_json = report_data.model_dump_json(exclude_none=True)
        await blob_client.upload_blob(
            report_json.encode("utf-8"),
            overwrite=True,
            metadata={}
        )
        log_adapter.info("Uploaded final report.")
    except Exception as e:
        log_adapter.error(f"Failed to upload report: {e}", exc_info=True)
        raise


def get_async_blob_service_client() -> AsyncBlobServiceClient:
    if not AZURE_STORAGE_CONNECTION_STRING:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING environment variable is not set")
    return AsyncBlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)

def get_sync_blob_service_client() -> BlobServiceClient:
    if not AZURE_STORAGE_CONNECTION_STRING:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING environment variable is not set")
    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
