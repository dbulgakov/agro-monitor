import os
import time
import logging
from typing import Optional
from azure.storage.blob import BlobServiceClient

from ..schemas import ProgressUpdate, JobStatus

REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")

def get_job_status(client: BlobServiceClient, job_id: str) -> Optional[ProgressUpdate]:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    try:
        blob_client = client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=f"{job_id}/status.json")
        if blob_client.exists():
            data = blob_client.download_blob().readall()
            return ProgressUpdate.model_validate_json(data)
    except Exception as e:
        log_adapter.error(f"Failed to get job status: {e}", exc_info=True)
    return None

def update_job_status(client: BlobServiceClient, job_id: str, status: JobStatus, progress: int, message: Optional[str] = None, raw: Optional[str] = None):
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    try:
        blob_client = client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=f"{job_id}/status.json")
        update = ProgressUpdate(
            jobId=job_id,
            status=status,
            progress=progress,
            timestamp=time.time(),
            message=message,
        )
        content = update.model_dump_json(exclude_none=True)
        blob_client.upload_blob(content.encode('utf-8'), overwrite=True)
        log_adapter.debug(f"Updated status: {status} {progress}%")
    except Exception as e:
        log_adapter.error(f"Failed to update job status: {e}", exc_info=True)
        raise