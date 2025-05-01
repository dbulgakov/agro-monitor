import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict
import json

import numpy as np
import azure.functions as func
from azure.storage.blob.aio import BlobServiceClient

from shared_code.helpers.blob import (
    upload_image_to_blob,
    upload_report_to_blob,
    get_async_blob_service_client,
)
from shared_code.helpers.image_helpers import create_ndvi_buffer, create_rgb_buffer
from shared_code.helpers.job_status import update_job_status, get_job_status
from shared_code.helpers.openai_helpers import generate_openai_recommendations
from shared_code.helpers.raster_helpers import read_band, read_rgb
from shared_code.helpers.sentinel_helpers import get_sentinel2_urls
from shared_code.schemas import JobStatus, StartAnalysisPayload, ReportData

_blob_client: Optional[BlobServiceClient] = None

def get_client() -> BlobServiceClient:
    global _blob_client
    if _blob_client is None:
        _blob_client = get_async_blob_service_client()
    return _blob_client


def compute_ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    np.seterr(divide='ignore', invalid='ignore')
    nir_f = nir.astype(float)
    red_f = red.astype(float)
    ndvi_raw = (nir_f - red_f) / (nir_f + red_f)
    return np.nan_to_num(ndvi_raw)

async def read_bands(job_id: str, urls: Dict[str, str]) -> (np.ndarray, np.ndarray, Optional[np.ndarray]):
    nir_task = read_band(job_id, urls['nir'])
    red_task = read_band(job_id, urls['red'])
    rgb_task = read_rgb(job_id, urls.get('visual', ''))
    results = await asyncio.gather(nir_task, red_task, rgb_task, return_exceptions=True)
    nir, red, rgb = results
    if isinstance(nir, Exception) or isinstance(red, Exception):
        raise RuntimeError(f"Помилка читання смуг: {[type(r).__name__ for r in results[:2]]}")
    rgb = None if isinstance(rgb, Exception) else rgb
    return nir, red, rgb

async def read_and_compute_ndvi(job_id: str, urls: Dict[str, str]) -> (np.ndarray, Optional[np.ndarray]):
    nir, red, rgb = await read_bands(job_id, urls)
    ndvi = await asyncio.to_thread(compute_ndvi, nir, red)
    return ndvi, rgb

async def update_status(
    client: BlobServiceClient,
    job_id: str,
    status: JobStatus,
    progress: int,
    message: str,
):
    await update_job_status(client, job_id, status, progress, message)

async def validate_message(msg: func.QueueMessage) -> Optional[Dict]:
    try:
        content = msg.get_body().decode('utf-8')
        logging.info(f"Validating message content: {content}")
        data = json.loads(content)
        return {'job_id': data['jobId'], 'payload': data['payload']}
    except (json.JSONDecodeError, KeyError) as err:
        logging.error(f"Неправильне повідомлення черги: {err}")
        return None

async def fetch_band_urls(job_id: str, payload: StartAnalysisPayload) -> Dict[str, str]:
    return await get_sentinel2_urls(
        job_id=job_id,
        geometry=payload.area.geometry.model_dump(),
        date_range=payload.date_range,
        bands=['nir', 'red', 'visual'],
    )

async def process_analysis(msg: func.QueueMessage, client: BlobServiceClient):
    validated = await validate_message(msg)
    if not validated:
        return
    job_id = validated['job_id']
    payload_dict = validated['payload']
    logger = logging.getLogger(__name__).getChild(job_id)

    # валідую payload
    try:
        payload = StartAnalysisPayload.model_validate(payload_dict)
    except Exception as err:
        logger.error(f"Помилка перевірки навантаження: {err}")
        await update_status(client, job_id, JobStatus.FAILED, -1, f"Неправильне навантаження: {err}")
        return

    # Пропускаємо, якщо job вже обробляється або завершено
    current = await get_job_status(client, job_id)
    if current and current.status in {JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.FAILED}:
        logger.info(f"Skipping; status is {current.status}")
        return

    # Початковий статус виконання
    await update_status(client, job_id, JobStatus.PROCESSING, 0, "Початок обробки")

    try:
        await update_status(client, job_id, JobStatus.PROCESSING, 10, "Отримання даних")
        urls = await fetch_band_urls(job_id, payload)

        await update_status(client, job_id, JobStatus.PROCESSING, 30, "Читання даних та обчислення NDVI")
        ndvi, rgb = await read_and_compute_ndvi(job_id, urls)

        await update_status(client, job_id, JobStatus.PROCESSING, 60, "Створення та завантаження візуалізацій")
        ndvi_buf = create_ndvi_buffer(ndvi)
        tasks = [upload_image_to_blob(client, job_id, ndvi_buf, "ndvi_map.png")]
        if rgb is not None:
            rgb_buf = create_rgb_buffer(rgb)
            tasks.append(upload_image_to_blob(client, job_id, rgb_buf, "rgb_map.png"))
        ndvi_url, *rest = await asyncio.gather(*tasks)
        rgb_url = rest[0] if rest else None

        await update_status(client, job_id, JobStatus.PROCESSING, 80, "Генерація рекомендацій")
        threshold = getattr(payload, 'ndvi_threshold', 0.3)
        mask = ndvi < threshold
        recs = await generate_openai_recommendations(job_id, ndvi, mask, payload.crop_type)

        await update_status(client, job_id, JobStatus.PROCESSING, 95, "Фіналізація звіту")
        stats = {
            'mean': float(np.mean(ndvi)),
            'min': float(np.min(ndvi)),
            'max': float(np.max(ndvi)),
            'stress_percentage': (
                float((mask & np.isfinite(ndvi)).sum() / np.isfinite(ndvi).sum() * 100)
            ),
        }
        report = ReportData(
            jobId=job_id,
            status=JobStatus.COMPLETED,
            requestPayload=payload.model_dump(),
            reportTimestamp=datetime.now(timezone.utc).isoformat(),
            ndviStatistics=stats,
            mapUrls={'ndvi': ndvi_url, 'rgb': rgb_url},
            recommendations=recs,
        )
        await upload_report_to_blob(client, job_id, report)
        await update_status(client, job_id, JobStatus.COMPLETED, 100, "Analysis complete")
        logger.info("Job completed successfully.")

    except Exception as err:
        logger.error(f"Помилка обробки: {err}", exc_info=True)
        await update_status(client, job_id, JobStatus.FAILED, -1, f"Помилка: {err}")
