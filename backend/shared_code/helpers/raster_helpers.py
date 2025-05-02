import logging
import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.enums import Resampling # Import Resampling
import requests # Use requests instead of aiohttp
from requests.exceptions import RequestException, Timeout
from typing import Optional

from .blob import get_async_blob_service_client  # not used here but included

# Define timeout settings for HTTP requests (in seconds)
REQUESTS_CONNECT_TIMEOUT = 10
REQUESTS_READ_TIMEOUT = 60 # Timeout for reading the response

DOWNSCALE_FACTOR = 2 # Define the downscale factor globally or pass as arg
RESAMPLING_METHOD = Resampling.bilinear # Choose resampling method

def _download_data(url: str, log_adapter: logging.LoggerAdapter) -> bytes:
    log_adapter.info(f"Початок завантаження: {url[:100]}...")
    try:
        response = requests.get(url, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT), stream=True)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.content
        log_adapter.info(f"Завантажено {len(data)} байт з {url[:100]}...")
        return data
    except Timeout:
        log_adapter.error(f"Таймаут ({REQUESTS_READ_TIMEOUT}s) при завантаженні {url[:100]}...")
        raise RuntimeError(f"Timeout downloading {url[:100]}...")
    except RequestException as e:
        status_code = e.response.status_code if e.response is not None else "N/A"
        log_adapter.error(f"Помилка HTTP {status_code} або мережі при завантаженні {url[:100]}...: {e}")
        raise RuntimeError(f"HTTP or Network error downloading {url[:100]}... Status: {status_code}") from e
    except Exception as e:
        log_adapter.error(f"Невідома помилка при завантаженні {url[:100]}...: {e}", exc_info=True)
        raise RuntimeError(f"Unknown error downloading {url[:100]}...") from e

def _sync_read_band_from_bytes(data: bytes, downscale_factor: int) -> np.ndarray:
    with MemoryFile(data) as memfile:
        with memfile.open() as dataset:
            # Calculate output shape
            out_height = dataset.height // downscale_factor
            out_width = dataset.width // downscale_factor
            # Read data with downsampling
            band_data = dataset.read(
                1,
                out_shape=(dataset.count, out_height, out_width),
                resampling=RESAMPLING_METHOD
            )
            return band_data.astype(np.float32)

def read_band(job_id: str, url: str) -> np.ndarray:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    try:
        band_data = _download_data(url, log_adapter)
        # Pass downscale factor
        return _sync_read_band_from_bytes(band_data, DOWNSCALE_FACTOR)
    except Exception as e:
        # Log error specific to band reading after download/processing
        log_adapter.error(f"Помилка читання або обробки даних каналу з {url[:100]}...: {e}", exc_info=True)
        # Re-raise as a runtime error to be caught by the caller
        raise RuntimeError(f"Error reading or processing band data from {url[:100]}...") from e

def _sync_read_rgb_from_bytes(data: bytes, downscale_factor: int) -> np.ndarray:
    with MemoryFile(data) as memfile:
        with memfile.open() as dataset:
            # Calculate output shape
            out_height = dataset.height // downscale_factor
            out_width = dataset.width // downscale_factor

            if dataset.count >= 3:
                # Read RGB bands with downsampling
                img = dataset.read(
                    [1, 2, 3],
                    out_shape=(dataset.count, out_height, out_width),
                    resampling=RESAMPLING_METHOD
                )
            else:
                logging.warning(f"Очікувалось >= 3 канали для RGB, знайдено {dataset.count}. Читання першого каналу з downsampling та тиражування.")
                # Read band 1 with downsampling
                band1 = dataset.read(
                    1,
                    out_shape=(dataset.count, out_height, out_width),
                    resampling=RESAMPLING_METHOD
                )
                # Stack along the channel axis (axis=0 for rasterio)
                img = np.stack([band1, band1, band1], axis=0)
            return img.astype(np.float32)

def read_rgb(job_id: str, url: str) -> Optional[np.ndarray]:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    if not url:
        log_adapter.warning("Немає URL для RGB знімку, пропуск читання.")
        return None

    try:
        rgb_data = _download_data(url, log_adapter)
        # Pass downscale factor
        rgb_array = _sync_read_rgb_from_bytes(rgb_data, DOWNSCALE_FACTOR)

        # Transpose from (bands, height, width) to (height, width, bands) expected by PIL/downstream
        if rgb_array.ndim == 3 and rgb_array.shape[0] in [1, 3]: # Handles both RGB and adapted grayscale
            return np.transpose(rgb_array, (1, 2, 0))
        else:
            log_adapter.error(f"Неочікувана форма масиву RGB після читання: {rgb_array.shape}")
            return None # Cannot proceed with unexpected shape

    except RuntimeError as e:
        # Handle download errors specifically (already logged in _download_data)
        log_adapter.warning(f"Не вдалося завантажити або обробити RGB з {url[:100]}..., обробка продовжиться без нього. Помилка: {e}")
        return None
    except Exception as e:
        log_adapter.error(f"Неочікувана помилка обробки RGB даних з {url[:100]}...: {e}", exc_info=True)
        # Return None if processing fails, as RGB is often optional
        return None