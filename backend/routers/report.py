import os
import json
import logging
from fastapi import APIRouter, HTTPException

# Adjust imports based on your project structure
try:
    from ..utils.azure_storage import get_blob_service_client
except ImportError:
    # Fallback for potential different structure or direct run
    from utils.azure_storage import get_blob_service_client

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Config --- #
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
PROGRESS_CONTAINER_NAME = os.getenv("PROGRESS_CONTAINER_NAME", "job-status")

@router.get("/{jobId}", tags=["Reports"]) # Path relative to prefix in app.py
async def get_analysis_report(jobId: str):
    logger.info(f"Fetching report for job: {jobId}")
    try:
        blob_service_client = await get_blob_service_client()
        if not blob_service_client:
            raise HTTPException(status_code=503, detail="Storage service unavailable.")

        async with blob_service_client:
            container_client = blob_service_client.get_container_client(REPORTS_CONTAINER_NAME)
            blob_client = container_client.get_blob_client(f"{jobId}_report.json") # Example naming

            if not await blob_client.exists():
                 # Check progress status first?
                 progress_container_client = blob_service_client.get_container_client(PROGRESS_CONTAINER_NAME)
                 progress_blob_client = progress_container_client.get_blob_client(f"{jobId}.json")
                 if await progress_blob_client.exists():
                     raise HTTPException(status_code=404, detail=f"Report for job {jobId} not generated yet.")
                 else:
                     raise HTTPException(status_code=404, detail=f"Job {jobId} not found.")

            download_stream = await blob_client.download_blob()
            report_data = json.loads(await download_stream.readall())
            return report_data # Assumes report is stored as JSON

    except HTTPException as http_exc:
        raise http_exc # Re-raise specific HTTP exceptions
    except Exception as e:
        logger.error(f"Failed to fetch report for job {jobId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve analysis report.") 