import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict
import json
import io
import os

import numpy as np
from PIL import Image
import azure.functions as func
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings

from shared_code.helpers.job_status import update_job_status, get_job_status
from shared_code.helpers.openai_helpers import generate_openai_recommendations
from shared_code.helpers.raster_helpers import read_band, read_rgb
# from shared_code.helpers.sentinel_helpers import get_sentinel2_urls
from pystac_client import Client
from datetime import date, timedelta
from shared_code.schemas import JobStatus, StartAnalysisPayload, ReportData

AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")

_blob_client: Optional[BlobServiceClient] = None

def get_client() -> BlobServiceClient:
    global _blob_client
    if _blob_client is None:
        _blob_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    return _blob_client

def compute_ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    with np.errstate(divide='ignore', invalid='ignore'):
        denom = nir + red
        ndvi = np.where(denom != 0, (nir - red) / denom, 0)
    return ndvi.astype(np.float32)

def create_ndvi_buffer(ndvi: np.ndarray) -> io.BytesIO:
    scaled = ((ndvi + 1) / 2 * 255).clip(0, 255).astype(np.uint8)
    image = Image.fromarray(scaled, mode='L')
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf

def create_rgb_buffer(rgb: np.ndarray) -> io.BytesIO:
    if rgb.ndim == 3 and rgb.shape[0] == 3:
        rgb = np.transpose(rgb, (1, 2, 0))
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    image = Image.fromarray(rgb, mode='RGB')
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf

async def upload_image_to_blob(
    client: BlobServiceClient,
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
    client: BlobServiceClient,
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
    await update_status(get_client(), job_id, JobStatus.PROCESSING, 15, "Отримання URL знімків")

    catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")

    start_date = payload.date_range.start.strftime("%Y-%m-%d")
    end_date = payload.date_range.end.strftime("%Y-%m-%d")

    items = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects=payload.area.geometry.model_dump(),
        datetime=f"{start_date}/{end_date}",
        query={"eo:cloud_cover": {"lt": 50}},
        max_items=1,
    ).get_all_items()

    if not items:
        raise RuntimeError("Не знайдено відповідних знімків Sentinel-2")

    item = items[0]
    asset_urls = {
        'nir': item.assets['B08'].href,
        'red': item.assets['B04'].href,
        'visual': item.assets['visual'].href if 'visual' in item.assets else item.assets['B04'].href,
    }
    return asset_urls

async def read_bands(job_id: str, urls: Dict[str, str]) -> (np.ndarray, np.ndarray, Optional[np.ndarray]):
    await update_status(get_client(), job_id, JobStatus.PROCESSING, 20, "Завантаження знімків")
    nir_task = read_band(job_id, urls['nir'])
    red_task = read_band(job_id, urls['red'])
    rgb_task = read_rgb(job_id, urls.get('visual', ''))
    try:
        results = await asyncio.wait_for(
            asyncio.gather(nir_task, red_task, rgb_task, return_exceptions=True),
            timeout=60,
        )
    except asyncio.TimeoutError:
        raise RuntimeError("Зчитування знімків перевищило таймаут")

    nir, red, rgb = results
    if isinstance(nir, Exception) or isinstance(red, Exception):
        raise RuntimeError(f"Помилка читання смуг: {[type(r).__name__ for r in results[:2]]}")
    rgb = None if isinstance(rgb, Exception) else rgb
    return nir, red, rgb

async def read_and_compute_ndvi(job_id: str, urls: Dict[str, str]) -> (np.ndarray, Optional[np.ndarray]):
    nir, red, rgb = await read_bands(job_id, urls)
    await update_status(get_client(), job_id, JobStatus.PROCESSING, 30, "Обчислення NDVI")
    ndvi = await asyncio.to_thread(compute_ndvi, nir, red)
    return ndvi, rgb

async def process_analysis(msg: func.QueueMessage, client: BlobServiceClient):
    validated = await validate_message(msg)
    if not validated:
        return
    job_id = validated['job_id']
    payload_dict = validated['payload']
    logger = logging.getLogger(__name__).getChild(job_id)

    try:
        payload = StartAnalysisPayload.model_validate(payload_dict)
    except Exception as err:
        logger.error(f"Помилка перевірки навантаження: {err}")
        await update_status(client, job_id, JobStatus.FAILED, -1, f"Неправильне навантаження: {err}")
        return

    current = await get_job_status(client, job_id)
    if current and current.status in {JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.FAILED}:
        logger.info(f"Skipping; status is {current.status}")
        return

    await update_status(client, job_id, JobStatus.PROCESSING, 0, "Початок обробки")

    try:
        urls = await fetch_band_urls(job_id, payload)
        ndvi, rgb = await read_and_compute_ndvi(job_id, urls)

        await update_status(client, job_id, JobStatus.PROCESSING, 60, "Створення та завантаження візуалізацій")
        ndvi_buf = await asyncio.to_thread(create_ndvi_buffer, ndvi)
        tasks = [upload_image_to_blob(client, job_id, ndvi_buf, "ndvi_map.png")]

        if rgb is not None:
            rgb_buf = await asyncio.to_thread(create_rgb_buffer, rgb)
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
