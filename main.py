import logging
import json
import io
import asyncio
from datetime import datetime
from typing import Optional, Dict

import numpy as np
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import azure.functions as func

from ..shared_code.schemas import StartAnalysisPayload, ReportData, JobStatus
from ..shared_code.helpers import (
    update_job_status,
    get_job_status,
    upload_image_to_blob,
    upload_report_to_blob,
    generate_openai_recommendations,
    read_band,
    read_rgb,
    normalize_image,
    get_sentinel2_urls,
)

# Pre-configure colormap and normalizer
_CMAP = plt.get_cmap('RdYlGn')
_NORM = mcolors.Normalize(vmin=-0.2, vmax=1.0)


def get_logger(job_id: str) -> logging.LoggerAdapter:
    base = logging.getLogger(__name__)
    return logging.LoggerAdapter(base, {'job_id': job_id})


async def update_status(job_id: str, status: JobStatus, progress: int, message: str):
    await update_job_status(job_id, status, progress, message)


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
    # Parallel read
    results = await asyncio.gather(
        read_band(job_id, urls['nir']),
        read_band(job_id, urls['red']),
        read_rgb(job_id, urls.get('visual', '')),  # may be optional
        return_exceptions=True,
    )
    nir, red, rgb = results

    # Check critical bands
    if isinstance(nir, Exception) or isinstance(red, Exception):
        raise RuntimeError(f"Band read error(s): {[type(r).__name__ for r in results[:2]]}")

    # Compute NDVI
    np.seterr(divide='ignore', invalid='ignore')
    nir_f = nir.astype(float)
    red_f = red.astype(float)
    ndvi = (nir_f - red_f) / (nir_f + red_f)
    ndvi = np.nan_to_num(ndvi)
    return ndvi, rgb if not isinstance(rgb, Exception) else None


def create_ndvi_buffer(ndvi: np.ndarray) -> io.BytesIO:
    buf = io.BytesIO()
    fig, ax = plt.subplots(figsize=(8, 8))
    im = ax.imshow(ndvi, cmap=_CMAP, norm=_NORM)
    ax.axis('off')
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label='NDVI')
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def create_rgb_buffer(rgb: np.ndarray) -> io.BytesIO:
    buf = io.BytesIO()
    rgb_norm = normalize_image('', rgb)
    rgb_uint8 = (np.clip(rgb_norm, 0, 1) * 255).astype(np.uint8)
    if rgb_uint8.ndim == 3 and rgb_uint8.shape[0] == 3:
        rgb_uint8 = np.transpose(rgb_uint8, (1, 2, 0))
    img = Image.fromarray(rgb_uint8, 'RGB')
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf


async def process_analysis(msg: func.QueueMessage):
    try:
        data = msg.get_json()
        job_id = data['jobId']
        payload = StartAnalysisPayload.model_validate(data['payload'])
    except:
        return

    current = await get_job_status(job_id)
    if current and current.status in {JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.FAILED}:
        return

    await update_job_status(job_id, JobStatus.PROCESSING, 10, "Acquiring data")
    urls = await get_sentinel2_urls(
        job_id,
        payload.area.geometry.model_dump(),
        payload.date_range,
        ['nir', 'red', 'visual']
    )

    await update_job_status(job_id, JobStatus.PROCESSING, 30, "Reading and computing NDVI")
    results = await asyncio.gather(
        read_band(job_id, urls['nir']),
        read_band(job_id, urls['red']),
        read_rgb(job_id, urls['visual']),
        return_exceptions=True
    )
    nir, red, rgb = results
    if isinstance(nir, Exception) or isinstance(red, Exception):
        await update_job_status(job_id, JobStatus.FAILED, -1, "Band read error")
        return

    np.seterr(divide='ignore', invalid='ignore')
    ndvi = np.nan_to_num((nir.astype(float) - red.astype(float)) / (nir.astype(float) + red.astype(float)))

    await update_job_status(job_id, JobStatus.PROCESSING, 60, "Creating visualizations")
    buf_ndvi = io.BytesIO()
    fig, ax = plt.subplots(figsize=(8, 8))
    im = ax.imshow(ndvi, cmap=_CMAP, norm=_NORM)
    ax.axis('off')
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label='NDVI')
    plt.savefig(buf_ndvi, format='png', bbox_inches='tight', dpi=150)
    plt.close(fig)
    buf_ndvi.seek(0)
    ndvi_url = await upload_image_to_blob(job_id, buf_ndvi, "ndvi_map.png")

    rgb_url = None
    if rgb is not None:
        buf_rgb = io.BytesIO()
        arr = (np.clip(normalize_image(job_id, rgb), 0, 1) * 255).astype(np.uint8)
        if arr.ndim == 3 and arr.shape[0] == 3:
            arr = np.transpose(arr, (1, 2, 0))
        Image.fromarray(arr, 'RGB').save(buf_rgb, format='PNG')
        buf_rgb.seek(0)
        rgb_url = await upload_image_to_blob(job_id, buf_rgb, "rgb_map.png")

    await update_job_status(job_id, JobStatus.PROCESSING, 80, "Generating recommendations")
    mask = ndvi < getattr(payload, 'ndvi_threshold', 0.3)
    recs = await generate_openai_recommendations(job_id, ndvi, mask, payload.crop_type)

    await update_job_status(job_id, JobStatus.PROCESSING, 95, "Finalizing report")
    finite = ndvi[np.isfinite(ndvi)]
    stats = {
        'mean': float(finite.mean()),
        'min': float(finite.min()),
        'max': float(finite.max()),
        'stress_percentage': float((mask & np.isfinite(ndvi)).sum() / finite.size * 100)
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
    await upload_report_to_blob(job_id, report)
    await update_job_status(job_id, JobStatus.COMPLETED, 100, "Analysis complete")


def main(msg: func.QueueMessage):
    asyncio.run(process_analysis(msg)) 