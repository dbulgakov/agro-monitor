import asyncio
import logging
import numpy as np
import rasterio

from .blob import get_async_blob_service_client  # not used here but included


def _sync_read_band(url: str) -> np.ndarray:
    with rasterio.open(url) as src:
        return src.read(1).astype('float32')

async def read_band(job_id: str, url: str) -> np.ndarray:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    try:
        return await asyncio.to_thread(_sync_read_band, url)
    except Exception as e:
        log_adapter.error(f"Failed to read band: {e}", exc_info=True)
        raise


def _sync_read_rgb(url: str) -> np.ndarray:
    with rasterio.open(url) as src:
        img = src.read([1, 2, 3])
        return np.transpose(img, (1, 2, 0)).astype('float32')

async def read_rgb(job_id: str, url: str) -> np.ndarray:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    try:
        return await asyncio.to_thread(_sync_read_rgb, url)
    except Exception as e:
        log_adapter.error(f"Failed to read RGB: {e}", exc_info=True)
        raise