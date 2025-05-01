import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict
import json
import io
import os
import tempfile
import contextlib

import numpy as np
from PIL import Image
import azure.functions as func
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings
import aiohttp

from shared_code.helpers.job_status import update_job_status, get_job_status
from shared_code.helpers.openai_helpers import generate_openai_recommendations
from shared_code.helpers.raster_helpers import read_band, read_rgb
from pystac_client import Client
from planetary_computer import sign
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

def save_ndvi_to_file(ndvi: np.ndarray, file_path: str):
    scaled = ((ndvi + 1) / 2 * 255).clip(0, 255).astype(np.uint8)
    image = Image.fromarray(scaled, mode='L')
    image.save(file_path, format="PNG", optimize=True)

def save_rgb_to_file(rgb: np.ndarray, file_path: str):
    if rgb.ndim == 3 and rgb.shape[0] == 3:
        rgb = np.transpose(rgb, (1, 2, 0))
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    image = Image.fromarray(rgb, mode='RGB')
    image.save(file_path, format="PNG", optimize=True)

async def upload_image_from_file(client: BlobServiceClient, job_id: str, local_file_path: str, image_name: str) -> Optional[str]:
    log = logging.getLogger(__name__).getChild(job_id)
    try:
        blob_name = f"{job_id}/{image_name}"
        blob_client = client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=blob_name)

        log.info(f"Початок завантаження зображення з файлу: {local_file_path} до {blob_name}")
        with open(local_file_path, "rb") as data:
            await blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type="image/png"),
            )
        log.info(f"Зображення завантажено: {blob_client.url}")
        return blob_client.url
    except Exception as e:
        log.error(f"Помилка завантаження зображення з файлу {local_file_path}: {e}", exc_info=True)
        return None

async def upload_report_to_blob(client: BlobServiceClient, job_id: str, report_data: ReportData):
    log = logging.getLogger(__name__).getChild(job_id)
    try:
        blob_name = f"{job_id}/report.json"
        blob_client = client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=blob_name)
        await blob_client.upload_blob(
            report_data.model_dump_json(exclude_none=True).encode("utf-8"),
            overwrite=True,
            metadata={}
        )
        log.info("Фінальний звіт завантажено")
    except Exception as e:
        log.error(f"Помилка завантаження звіту: {e}", exc_info=True)
        raise

async def update_status(client: BlobServiceClient, job_id: str, status: JobStatus, progress: int, message: str):
    await update_job_status(client, job_id, status, progress, message)

async def validate_message(msg: func.QueueMessage) -> Optional[Dict]:
    try:
        content = msg.get_body().decode('utf-8')
        logging.info(f"Отримано повідомлення: {content}")
        data = json.loads(content)
        return {'job_id': data['jobId'], 'payload': data['payload']}
    except (json.JSONDecodeError, KeyError) as err:
        logging.error(f"Неправильне повідомлення черги: {err}")
        return None

async def fetch_band_urls(job_id: str, payload: StartAnalysisPayload) -> Dict[str, str]:
    logger = logging.getLogger(__name__).getChild(job_id)
    await update_status(get_client(), job_id, JobStatus.PROCESSING, 15, "Отримання URL знімків")

    logger.info("Пошук знімків у Planetary Computer")
    catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")

    try:
        start_date_str, end_date_str = payload.date_range.split("/")
    except ValueError:
        raise RuntimeError(f"Неправильний формат date_range: {payload.date_range}")

    datetime_range = f"{start_date_str}/{end_date_str}"
    logger.info(f"Діапазон дат: {datetime_range}")

    items = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects=payload.area.geometry.model_dump(),
        datetime=datetime_range,
        query={"eo:cloud_cover": {"lt": 50}},
        max_items=1,
    ).get_all_items()

    if not items:
        raise RuntimeError("Не знайдено відповідних знімків Sentinel-2")

    item = sign(items[0])
    logger.info("Знімок знайдено")
    visual_href = item.assets.get('visual', item.assets.get('B04')).href
    return {
        'nir': item.assets['B08'].href,
        'red': item.assets['B04'].href,
        'visual': visual_href,
    }

async def read_bands(job_id: str, urls: Dict[str, str]) -> (np.ndarray, np.ndarray, Optional[np.ndarray]):
    logger = logging.getLogger(__name__).getChild(job_id)
    await update_status(get_client(), job_id, JobStatus.PROCESSING, 20, "Завантаження знімків")
    logger.info("Читання знімків NIR, RED, RGB")

    async with aiohttp.ClientSession() as session:
        nir_task = read_band(job_id, urls['nir'], session=session)
        red_task = read_band(job_id, urls['red'], session=session)
        rgb_task = read_rgb(job_id, urls.get('visual', ''), session=session)

        try:
            results = await asyncio.wait_for(
                asyncio.gather(nir_task, red_task, rgb_task, return_exceptions=True),
                timeout=120,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Читання знімків перевищило таймаут (120 сек)")
        finally:
            pass

    nir, red, rgb = results
    error_messages = []
    if isinstance(nir, Exception):
        error_messages.append(f"NIR band error: {nir}")
    if isinstance(red, Exception):
        error_messages.append(f"Red band error: {red}")
    if isinstance(rgb, Exception):
        logger.warning(f"RGB band error (non-critical): {rgb}")
        rgb = None

    if error_messages:
        raise RuntimeError(f"Помилка читання критичних смуг: {'; '.join(error_messages)}")

    return nir, red, rgb

async def read_and_compute_ndvi(job_id: str, urls: Dict[str, str]) -> (np.ndarray, Optional[np.ndarray]):
    nir, red, rgb = await read_bands(job_id, urls)
    await update_status(get_client(), job_id, JobStatus.PROCESSING, 30, "Обчислення NDVI")
    ndvi = await asyncio.to_thread(compute_ndvi, nir, red)
    del nir, red
    return ndvi, rgb

async def process_analysis(msg: func.QueueMessage, client: BlobServiceClient):
    validated = await validate_message(msg)
    if not validated:
        return
    job_id = validated['job_id']
    payload_dict = validated['payload']
    logger = logging.getLogger(__name__).getChild(job_id)

    logger.info("Початок аналізу NDVI")

    try:
        payload = StartAnalysisPayload.model_validate(payload_dict)
    except Exception as err:
        logger.error(f"Помилка перевірки навантаження: {err}", exc_info=True)
        await update_status(client, job_id, JobStatus.FAILED, -1, f"Неправильне навантаження: {err}")
        return

    current = await get_job_status(client, job_id)
    if current and current.status in {JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.FAILED}:
        logger.warning(f"Завдання {job_id} вже обробляється/завершено/з помилкою ({current.status}). Пропуск.")
        return

    await update_status(client, job_id, JobStatus.PROCESSING, 0, "Початок обробки супутникових знімків")

    temp_files_to_clean = []
    try:
        urls = await fetch_band_urls(job_id, payload)
        ndvi, rgb = await read_and_compute_ndvi(job_id, urls)

        await update_status(client, job_id, JobStatus.PROCESSING, 60, "Створення та завантаження візуалізацій")

        upload_tasks = []

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_ndvi_file:
            ndvi_temp_path = temp_ndvi_file.name
            temp_files_to_clean.append(ndvi_temp_path)
        logger.info(f"Створення тимчасового файлу для NDVI: {ndvi_temp_path}")
        await asyncio.to_thread(save_ndvi_to_file, ndvi, ndvi_temp_path)
        upload_tasks.append(upload_image_from_file(client, job_id, ndvi_temp_path, "ndvi_map.png"))

        rgb_temp_path = None
        if rgb is not None:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_rgb_file:
                rgb_temp_path = temp_rgb_file.name
                temp_files_to_clean.append(rgb_temp_path)
            logger.info(f"Створення тимчасового файлу для RGB: {rgb_temp_path}")
            await asyncio.to_thread(save_rgb_to_file, rgb, rgb_temp_path)
            upload_tasks.append(upload_image_from_file(client, job_id, rgb_temp_path, "rgb_map.png"))
        else:
            logger.warning("RGB знімок недоступний, не буде створено rgb_map.png")
            upload_tasks.append(asyncio.sleep(0, result=None))

        upload_results = await asyncio.gather(*upload_tasks)
        ndvi_url = upload_results[0]
        rgb_url = upload_results[1] if len(upload_results) > 1 else None

        await update_status(client, job_id, JobStatus.PROCESSING, 80, "Генерація рекомендацій")
        threshold = getattr(payload, 'ndvi_threshold', 0.3)
        valid_ndvi = np.isfinite(ndvi)
        mask = (ndvi < threshold) & valid_ndvi
        recommendations = await generate_openai_recommendations(job_id, ndvi, mask, payload.crop_type)

        await update_status(client, job_id, JobStatus.PROCESSING, 95, "Формування звіту")

        ndvi_valid_pixels = ndvi[valid_ndvi]
        if ndvi_valid_pixels.size > 0:
             stats = {
                 'mean': float(np.mean(ndvi_valid_pixels)),
                 'min': float(np.min(ndvi_valid_pixels)),
                 'max': float(np.max(ndvi_valid_pixels)),
                 'std_dev': float(np.std(ndvi_valid_pixels)),
                 'stress_percentage': float(mask.sum() / valid_ndvi.sum() * 100) if valid_ndvi.sum() > 0 else 0.0
             }
        else:
             logger.warning("Немає валідних NDVI пікселів для статистики.")
             stats = { 'mean': None, 'min': None, 'max': None, 'std_dev': None, 'stress_percentage': 0.0 }

        report = ReportData(
            jobId=job_id,
            status=JobStatus.COMPLETED,
            requestPayload=payload.model_dump(exclude_none=True),
            reportTimestamp=datetime.now(timezone.utc).isoformat(),
            ndviStatistics=stats,
            mapUrls={'ndvi': ndvi_url, 'rgb': rgb_url},
            recommendations=recommendations,
        )
        await upload_report_to_blob(client, job_id, report)
        await update_status(client, job_id, JobStatus.COMPLETED, 100, "Аналіз завершено успішно")
        logger.info("Аналіз завершено успішно")

    except Exception as err:
        logger.error(f"Помилка обробки завдання {job_id}: {err}", exc_info=True)
        try:
            await update_status(client, job_id, JobStatus.FAILED, -1, f"Помилка: {str(err)[:200]}")
        except Exception as status_err:
            logger.error(f"Не вдалося оновити статус на FAILED для {job_id}: {status_err}", exc_info=True)
    finally:
        logger.info(f"Очищення тимчасових файлів для {job_id}: {temp_files_to_clean}")
        for temp_path in temp_files_to_clean:
            try:
                os.remove(temp_path)
                logger.debug(f"Видалено тимчасовий файл: {temp_path}")
            except OSError as e:
                logger.error(f"Не вдалося видалити тимчасовий файл {temp_path}: {e}")
