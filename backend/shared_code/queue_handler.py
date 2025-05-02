import logging
from datetime import datetime, timezone
from typing import Optional, Dict
import json
import io
import os
import tempfile
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from PIL import Image
import azure.functions as func
from azure.storage.blob import BlobServiceClient, ContentSettings
import requests

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
        if not AZURE_STORAGE_CONNECTION_STRING:
             raise ValueError("AZURE_STORAGE_CONNECTION_STRING must be set to initialize client")
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
    if rgb.ndim != 3 or rgb.shape[2] != 3:
         raise ValueError(f"Unexpected RGB shape for saving: {rgb.shape}. Expected (H, W, 3)")
    rgb_uint8 = np.clip(rgb, 0, 255).astype(np.uint8)
    image = Image.fromarray(rgb_uint8, mode='RGB')
    image.save(file_path, format="PNG", optimize=True)

def upload_image_from_file(client: BlobServiceClient, job_id: str, local_file_path: str, image_name: str) -> Optional[str]:
    log = logging.getLogger(__name__).getChild(job_id)
    try:
        blob_name = f"{job_id}/{image_name}"
        blob_client = client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=blob_name)

        with open(local_file_path, "rb") as data:
            blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type="image/png"),
            )
        return blob_client.url
    except Exception as e:
        log.error(f"Помилка завантаження зображення з файлу {local_file_path}: {e}", exc_info=True)
        return None

def upload_report_to_blob(client: BlobServiceClient, job_id: str, report_data: ReportData):
    log = logging.getLogger(__name__).getChild(job_id)
    try:
        blob_name = f"{job_id}/report.json"
        blob_client = client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=blob_name)
        blob_client.upload_blob(
            report_data.model_dump_json(exclude_none=True).encode("utf-8"),
            overwrite=True,
            metadata={}
        )
    except Exception as e:
        log.error(f"Помилка завантаження звіту: {e}", exc_info=True)
        raise

def update_status(client: BlobServiceClient, job_id: str, status: JobStatus, progress: int, message: str):
    update_job_status(client, job_id, status, progress, message)

def validate_message(msg: func.QueueMessage) -> Optional[Dict]:
    try:
        content = msg.get_body().decode('utf-8')
        data = json.loads(content)
        if 'jobId' not in data or 'payload' not in data:
             raise KeyError("Missing 'jobId' or 'payload' in message")
        return {'job_id': data['jobId'], 'payload': data['payload']}
    except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as err:
        logging.error(f"Неправильне повідомлення черги: {err}")
        return None

def fetch_band_urls(job_id: str, payload: StartAnalysisPayload) -> Dict[str, str]:
    logger = logging.getLogger(__name__).getChild(job_id)
    update_status(get_client(), job_id, JobStatus.PROCESSING, 15, "Отримання URL знімків")

    catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")

    try:
        start_date_str, end_date_str = payload.date_range.split("/")
    except ValueError:
        raise RuntimeError(f"Неправильний формат date_range: {payload.date_range}")

    datetime_range = f"{start_date_str}/{end_date_str}"
    cloud_cover = getattr(payload, 'max_cloud_cover', 50)

    search = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects=payload.area.geometry.model_dump(),
        datetime=datetime_range,
        query={"eo:cloud_cover": {"lt": cloud_cover}},
        max_items=1,
    )
    items = search.item_collection()

    if not items:
        raise RuntimeError(f"Не знайдено відповідних знімків Sentinel-2 для діапазону {datetime_range} та хмарності < {cloud_cover}%")

    item = sign(items[0])

    required_assets = {'B08', 'B04'}
    missing_assets = required_assets - set(item.assets.keys())
    if missing_assets:
        raise RuntimeError(f"Знайдений знімок {item.id} не має необхідних каналів: {missing_assets}")

    visual_asset_key = 'visual' if 'visual' in item.assets else 'B04'
    visual_href = item.assets[visual_asset_key].href

    return {
        'nir': item.assets['B08'].href,
        'red': item.assets['B04'].href,
        'visual': visual_href,
    }

def read_bands(job_id: str, urls: Dict[str, str]) -> (np.ndarray, np.ndarray, Optional[np.ndarray]):
    logger = logging.getLogger(__name__).getChild(job_id)
    update_status(get_client(), job_id, JobStatus.PROCESSING, 20, "Завантаження знімків")

    results = {}
    error_messages = []

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix=f'{job_id}_read') as executor:
        future_to_band = {}
        if 'nir' in urls:
            future_to_band[executor.submit(read_band, job_id, urls['nir'])] = 'nir'
        if 'red' in urls:
            future_to_band[executor.submit(read_band, job_id, urls['red'])] = 'red'
        visual_url = urls.get('visual')
        if visual_url:
            future_to_band[executor.submit(read_rgb, job_id, visual_url)] = 'rgb'

        for future in as_completed(future_to_band):
            band_name = future_to_band[future]
            try:
                results[band_name] = future.result()
            except Exception as e:
                logger.error(f"Помилка читання каналу {band_name.upper()}: {e}", exc_info=True)
                error_messages.append(f"{band_name.upper()} band error: {e}")
                results[band_name] = None

    nir = results.get('nir')
    red = results.get('red')
    rgb = results.get('rgb')

    if nir is None or red is None:
        raise RuntimeError(f"Помилка читання критичних смуг: {'; '.join(error_messages)}")

    if 'rgb' in future_to_band and rgb is None:
        logger.error("Читання RGB не повернуло даних або сталася помилка (деталі вище).")

    return nir, red, rgb

def read_and_compute_ndvi(job_id: str, urls: Dict[str, str]) -> (np.ndarray, Optional[np.ndarray]):
    logger = logging.getLogger(__name__).getChild(job_id)
    nir, red, rgb = read_bands(job_id, urls)
    update_status(get_client(), job_id, JobStatus.PROCESSING, 30, "Обчислення NDVI")
    ndvi = compute_ndvi(nir, red)
    del nir, red
    return ndvi, rgb

def process_analysis(msg: func.QueueMessage, client: BlobServiceClient):
    validated = validate_message(msg)
    if not validated:
        return

    job_id = validated['job_id']
    payload_dict = validated['payload']
    logger = logging.getLogger(__name__).getChild(job_id)

    try:
        payload = StartAnalysisPayload.model_validate(payload_dict)
    except Exception as err:
        logger.error(f"Помилка перевірки навантаження: {err}", exc_info=True)
        update_status(client, job_id, JobStatus.FAILED, -1, f"Неправильне навантаження: {str(err)[:200]}")
        return

    current = get_job_status(client, job_id)
    if current and current.status in {JobStatus.PROCESSING, JobStatus.COMPLETED}:
        return
    elif current and current.status == JobStatus.FAILED:
        pass

    update_status(client, job_id, JobStatus.PROCESSING, 0, "Початок обробки супутникових знімків")

    temp_files_to_clean = []
    try:
        urls = fetch_band_urls(job_id, payload)
        ndvi, rgb = read_and_compute_ndvi(job_id, urls)

        update_status(client, job_id, JobStatus.PROCESSING, 60, "Створення та завантаження візуалізацій")

        ndvi_url = None
        rgb_url = None
        ndvi_temp_path = None
        rgb_temp_path = None

        # Save files locally first
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_ndvi_file:
                ndvi_temp_path = temp_ndvi_file.name
                temp_files_to_clean.append(ndvi_temp_path)
            save_ndvi_to_file(ndvi, ndvi_temp_path)
        except Exception as e:
             logger.error(f"Помилка збереження NDVI до тимчасового файлу: {e}", exc_info=True)
             # If saving fails, we can't upload
             ndvi_temp_path = None

        if rgb is not None:
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_rgb_file:
                    rgb_temp_path = temp_rgb_file.name
                    temp_files_to_clean.append(rgb_temp_path)
                save_rgb_to_file(rgb, rgb_temp_path)
            except Exception as e:
                 logger.error(f"Помилка збереження RGB до тимчасового файлу: {e}", exc_info=True)
                 # If saving fails, we can't upload
                 rgb_temp_path = None

        # Upload files in parallel if they were saved successfully
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f'{job_id}_upload') as executor:
            future_to_image = {}
            if ndvi_temp_path:
                future_to_image[executor.submit(upload_image_from_file, client, job_id, ndvi_temp_path, "ndvi_map.png")] = 'ndvi'
            if rgb_temp_path:
                future_to_image[executor.submit(upload_image_from_file, client, job_id, rgb_temp_path, "rgb_map.png")] = 'rgb'

            for future in as_completed(future_to_image):
                image_name = future_to_image[future]
                try:
                    url = future.result()
                    if image_name == 'ndvi':
                        ndvi_url = url
                        if not url:
                             logger.error("Не вдалося завантажити NDVI зображення (upload_image_from_file повернув None).")
                    elif image_name == 'rgb':
                        rgb_url = url
                        if not url:
                             logger.error("Не вдалося завантажити RGB зображення (upload_image_from_file повернув None).")
                except Exception as e:
                    # upload_image_from_file already logs errors internally
                    logger.error(f"Помилка під час очікування завантаження {image_name.upper()}: {e}", exc_info=True)
                    # Ensure URLs remain None if upload future failed
                    if image_name == 'ndvi':
                        ndvi_url = None
                    elif image_name == 'rgb':
                        rgb_url = None

        update_status(client, job_id, JobStatus.PROCESSING, 80, "Генерація рекомендацій")
        threshold = getattr(payload, 'ndvi_threshold', 0.3)
        valid_ndvi = np.isfinite(ndvi)
        mask = (ndvi < threshold) & valid_ndvi
        recommendations = generate_openai_recommendations(job_id, ndvi, mask, payload.crop_type)

        update_status(client, job_id, JobStatus.PROCESSING, 95, "Формування звіту")

        ndvi_valid_pixels = ndvi[valid_ndvi]
        stats = {}
        if ndvi_valid_pixels.size > 0:
             stats = {
                 'mean': float(np.mean(ndvi_valid_pixels)),
                 'min': float(np.min(ndvi_valid_pixels)),
                 'max': float(np.max(ndvi_valid_pixels)),
                 'std_dev': float(np.std(ndvi_valid_pixels)),
                 'stress_percentage': float(mask.sum() / valid_ndvi.sum() * 100) if valid_ndvi.sum() > 0 else 0.0
             }
        else:
             logger.error("Немає валідних NDVI пікселів для статистики.")
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

        upload_report_to_blob(client, job_id, report)

        update_status(client, job_id, JobStatus.COMPLETED, 100, "Аналіз завершено успішно")

    except Exception as err:
        logger.error(f"Помилка обробки завдання {job_id}: {err}", exc_info=True)
        try:
            update_status(client, job_id, JobStatus.FAILED, -1, f"Помилка: {str(err)[:200]}")
        except Exception as status_err:
            logger.error(f"Не вдалося оновити статус на FAILED для {job_id}: {status_err}", exc_info=True)
    finally:
        for temp_path in temp_files_to_clean:
            try:
                os.remove(temp_path)
            except OSError as e:
                logger.error(f"Не вдалося видалити тимчасовий файл {temp_path}: {e}")
