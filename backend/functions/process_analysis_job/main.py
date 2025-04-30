import logging
import io
import asyncio
from datetime import datetime
from typing import Optional, Dict

import numpy as np
import azure.functions as func
from azure.storage.blob.aio import BlobServiceClient

from functions.lib.helpers.blob import upload_image_to_blob, upload_report_to_blob, get_async_blob_service_client
from functions.lib.helpers.image_helpers import create_ndvi_buffer, create_rgb_buffer
from functions.lib.helpers.job_status import update_job_status, get_job_status
from functions.lib.helpers.openai_helpers import generate_openai_recommendations
from functions.lib.helpers.raster_helpers import read_band, read_rgb
from functions.lib.helpers.sentinel_helpers import get_sentinel2_urls
from functions.lib.schemas import JobStatus, StartAnalysisPayload, ReportData

_CMAP = None  # Colormap defined in helpers
_NORM = None  # Normalizer defined in helpers

async def update_status(client: BlobServiceClient, job_id: str, status: JobStatus, progress: int, message: str):
    await update_job_status(client, job_id, status, progress, message)

async def validate_message(msg: func.QueueMessage) -> Optional[Dict]:
    try:
        data = msg.get_json()
        job_id = data['jobId']
        payload = data['payload']
        return {'job_id': job_id, 'payload': payload}
    except Exception as err:
        logging.error(f"Invalid queue message: {err}")
        return None

async def fetch_band_urls(job_id: str, payload: StartAnalysisPayload) -> Dict[str, str]:
    return await get_sentinel2_urls(
        job_id=job_id,
        geometry=payload.area.geometry.model_dump(),
        date_range=payload.date_range,
        bands=['nir', 'red', 'visual'],
    )

async def read_and_compute_ndvi(job_id: str, urls: Dict[str, str]) -> (np.ndarray, Optional[np.ndarray]):
    results = await asyncio.gather(
        read_band(job_id, urls['nir']),
        read_band(job_id, urls['red']),
        read_rgb(job_id, urls.get('visual', '')),  # optional
        return_exceptions=True,
    )
    nir, red, rgb = results

    if isinstance(nir, Exception) or isinstance(red, Exception):
        raise RuntimeError(f"Band read error(s): {[type(r).__name__ for r in results[:2]]}")

    np.seterr(divide='ignore', invalid='ignore')
    nir_f = nir.astype(float)
    red_f = red.astype(float)
    ndvi = (nir_f - red_f) / (nir_f + red_f)
    ndvi = np.nan_to_num(ndvi)
    rgb = None if isinstance(rgb, Exception) else rgb
    return ndvi, rgb

async def process_analysis(msg: func.QueueMessage):
    validated = await validate_message(msg)
    if not validated:
        return
    job_id = validated['job_id']
    payload_dict = validated['payload']
    logger = logging.getLogger(__name__).getChild(job_id)

    client = get_async_blob_service_client()
    async with client:
        try:
            payload = StartAnalysisPayload.model_validate(payload_dict)
        except Exception as err:
            logger.error(f"Payload validation error: {err}")
            await update_status(client, job_id, JobStatus.FAILED, -1, f"Invalid payload: {err}")
            return

        current = await get_job_status(client, job_id)
        if current and current.status in {JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.FAILED}:
            logger.info(f"Skipping; status is {current.status}")
            return

        try:
            await update_status(client, job_id, JobStatus.PROCESSING, 10, "Acquiring data")
            urls = await fetch_band_urls(job_id, payload)

            await update_status(client, job_id, JobStatus.PROCESSING, 30, "Reading and computing NDVI")
            ndvi, rgb = await read_and_compute_ndvi(job_id, urls)

            await update_status(client, job_id, JobStatus.PROCESSING, 60, "Creating visualizations")
            ndvi_buf = create_ndvi_buffer(ndvi)
            ndvi_url = await upload_image_to_blob(client, job_id, ndvi_buf, "ndvi_map.png")
            rgb_url = None
            if rgb is not None:
                rgb_buf = create_rgb_buffer(rgb)
                rgb_url = await upload_image_to_blob(client, job_id, rgb_buf, "rgb_map.png")

            await update_status(client, job_id, JobStatus.PROCESSING, 80, "Generating recommendations")
            threshold = getattr(payload, 'ndvi_threshold', 0.3)
            mask = ndvi < threshold
            recs = await generate_openai_recommendations(job_id, ndvi, mask, payload.crop_type)

            await update_status(client, job_id, JobStatus.PROCESSING, 95, "Finalizing report")
            stats = {
                'mean': float(np.mean(ndvi)),
                'min': float(np.min(ndvi)),
                'max': float(np.max(ndvi)),
                'stress_percentage': float((mask & np.isfinite(ndvi)).sum() / np.isfinite(ndvi).sum() * 100),
            }
            report = ReportData(
                jobId=job_id,
                status=JobStatus.COMPLETED,
                requestPayload=payload.model_dump(),
                reportTimestamp=datetime.utcnow().isoformat(),
                ndviStatistics=stats,
                mapUrls={'ndvi': ndvi_url, 'rgb': rgb_url},
                recommendations=recs,
            )
            await upload_report_to_blob(client, job_id, report)
            await update_status(client, job_id, JobStatus.COMPLETED, 100, "Analysis complete")
            logger.info("Job completed successfully.")

        except Exception as err:
            logger.error(f"Processing error: {err}", exc_info=True)
            await update_status(client, job_id, JobStatus.FAILED, -1, f"Error: {err}")


def main(msg: func.QueueMessage):
    try:
        asyncio.run(process_analysis(msg))
    except Exception as exc:
        logging.critical(f"Unhandled exception in main: {exc}", exc_info=True)
        raise