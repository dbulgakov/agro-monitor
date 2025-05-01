import asyncio
import logging
import numpy as np
import rasterio
from rasterio.io import MemoryFile
import aiohttp
from typing import Optional

from .blob import get_async_blob_service_client  # not used here but included

# Define timeout settings for HTTP requests (in seconds)
AIOHTTP_CONNECT_TIMEOUT = 10
AIOHTTP_TOTAL_TIMEOUT = 60 # Total time for the whole request including connection

async def _download_data(session: aiohttp.ClientSession, url: str, log_adapter: logging.LoggerAdapter) -> bytes:
    log_adapter.info(f"Початок завантаження: {url[:100]}...") # Log truncated URL
    try:
        # Use ClientTimeout for finer control
        timeout = aiohttp.ClientTimeout(total=AIOHTTP_TOTAL_TIMEOUT, connect=AIOHTTP_CONNECT_TIMEOUT)
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            data = await response.read()
            log_adapter.info(f"Завантажено {len(data)} байт з {url[:100]}...")
            return data
    except aiohttp.ClientResponseError as e:
        log_adapter.error(f"HTTP помилка {e.status} при завантаженні {url[:100]}...: {e.message}")
        raise RuntimeError(f"HTTP error {e.status} downloading {url[:100]}...") from e
    except asyncio.TimeoutError:
        log_adapter.error(f"Таймаут ({AIOHTTP_TOTAL_TIMEOUT}s) при завантаженні {url[:100]}...")
        raise RuntimeError(f"Timeout downloading {url[:100]}...")
    except aiohttp.ClientError as e:
        log_adapter.error(f"Помилка клієнта aiohttp при завантаженні {url[:100]}...: {e}")
        raise RuntimeError(f"aiohttp client error downloading {url[:100]}...") from e
    except Exception as e:
        log_adapter.error(f"Невідома помилка при завантаженні {url[:100]}...: {e}", exc_info=True)
        raise RuntimeError(f"Unknown error downloading {url[:100]}...") from e

def _sync_read_band_from_bytes(data: bytes) -> np.ndarray:
    # This part still runs in a thread because rasterio processing can be CPU intensive
    with MemoryFile(data) as memfile:
        with memfile.open() as dataset:
            return dataset.read(1).astype(np.float32)

async def read_band(job_id: str, url: str) -> np.ndarray:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    async with aiohttp.ClientSession() as session:
        band_data = await _download_data(session, url, log_adapter)
    try:
        # Run rasterio reading in a separate thread
        return await asyncio.to_thread(_sync_read_band_from_bytes, band_data)
    except Exception as e:
        log_adapter.error(f"Помилка обробки даних каналу (після завантаження) з {url[:100]}...: {e}", exc_info=True)
        raise RuntimeError(f"Error processing band data from {url[:100]}...") from e

def _sync_read_rgb_from_bytes(data: bytes) -> np.ndarray:
    # This part still runs in a thread
    with MemoryFile(data) as memfile:
        with memfile.open() as dataset:
            # Ensure we read 3 bands for RGB
            if dataset.count >= 3:
                # Read first three bands (assuming R=1, G=2, B=3 or similar)
                img = dataset.read([1, 2, 3])
                # Rasterio reads as (bands, height, width), transpose to (height, width, bands)
                return img.astype(np.float32) # Keep as float for potential intermediate calcs
            else:
                # Handle cases where the visual asset might not have 3 bands (e.g., grayscale)
                logging.warning(f"Очікувалось >= 3 канали для RGB, знайдено {dataset.count}. Повернення першого каналу.")
                # Return single band repeated 3 times to mimic RGB structure, or handle differently
                band1 = dataset.read(1)
                # Stack the single band three times along a new axis (axis=0)
                # and ensure it's float32
                img_gray_rgb = np.stack([band1, band1, band1], axis=0).astype(np.float32)
                return img_gray_rgb

async def read_rgb(job_id: str, url: str) -> Optional[np.ndarray]: # Return Optional
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    if not url:
        log_adapter.warning("Немає URL для RGB знімку, пропуск читання.")
        return None # Return None if no URL is provided

    async with aiohttp.ClientSession() as session:
        try:
            rgb_data = await _download_data(session, url, log_adapter)
        except RuntimeError:
             # Logged in _download_data, return None if download fails
             log_adapter.warning(f"Не вдалося завантажити RGB з {url[:100]}..., обробка продовжиться без нього.")
             return None

    try:
        # Run rasterio reading in a separate thread
        rgb_array = await asyncio.to_thread(_sync_read_rgb_from_bytes, rgb_data)
        # Transpose from (bands, height, width) to (height, width, bands) expected by PIL
        # Do transposition here instead of _sync function to keep raw read data simpler
        if rgb_array.ndim == 3 and rgb_array.shape[0] == 3:
             return np.transpose(rgb_array, (1, 2, 0))
        else:
             # This case might occur if _sync_read_rgb_from_bytes adapted grayscale
             # Ensure shape is (height, width, bands) even for grayscale adapted
             if rgb_array.ndim == 3 and rgb_array.shape[0] == 1: # Single band returned
                  temp_rgb = np.stack([rgb_array[0]]*3, axis=-1) # Create (H, W, 3)
                  return temp_rgb
             elif rgb_array.ndim == 2: # Pure grayscale returned somehow?
                  temp_rgb = np.stack([rgb_array]*3, axis=-1) # Create (H, W, 3)
                  return temp_rgb
             else:
                  log_adapter.error(f"Неочікувана форма масиву RGB після читання: {rgb_array.shape}")
                  return None # Cannot proceed with unexpected shape

    except Exception as e:
        log_adapter.error(f"Помилка обробки RGB даних (після завантаження) з {url[:100]}...: {e}", exc_info=True)
        # Return None if processing fails, as RGB is often optional
        return None