import logging
import json
import os
import io
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import azure.functions as func
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core.exceptions import ResourceNotFoundError

import numpy as np
import matplotlib
matplotlib.use('Agg')  # non-interactive backend
import matplotlib.pyplot as plt

import rasterio
from pystac_client import Client
import planetary_computer
from shapely.geometry import shape, mapping
import openai

from ..shared_code.schemas import StartAnalysisPayload, ReportData, JobStatus
from ..shared_code.helpers import (
    update_job_status,
    upload_report_to_blob,
    generate_openai_recommendations,
    read_band,
    read_rgb,
    normalize_image
)
from ..shared_code import helpers

# Environment variables
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
ANALYSIS_QUEUE_NAME = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

# Initialize OpenAI client
if OPENAI_API_KEY:
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None
    logging.warning("OPENAI_API_KEY is not set. AI recommendations will be disabled.")

def main(msg: func.QueueMessage):
    logging.info(f'Python queue trigger started processing message ID: {msg.id}')
    start_time = time.time()

    job_id = None
    log_adapter = None
    try:
        body = msg.get_body().decode('utf-8')
        data = json.loads(body)
        job_id = data.get("jobId")
        payload_dict = data.get("payload")

        log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id or 'UNKNOWN'})

        if not job_id or not payload_dict:
            log_adapter.error(f"Invalid queue message format: {body}")
            return

        try:
            payload = StartAnalysisPayload.model_validate(payload_dict)
        except Exception as validation_error:
            log_adapter.error(f"Payload validation failed: {validation_error}", exc_info=True)
            return

        update_job_status(job_id, JobStatus.PROCESSING, 5, "Starting analysis")

        # --- 1. Prepare geometry & params ---
        geometry = shape(payload.area.geometry.model_dump())
        geom_mapping = mapping(geometry)
        date_str = payload.date_range
        cloud_cover = payload.max_cloud_cover
        ndvi_thresh = payload.ndvi_threshold
        crop = payload.crop_type

        update_job_status(job_id, JobStatus.PROCESSING, 10, "Searching for satellite images")

        # --- 2. STAC search ---
        catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
        search = catalog.search(
            collections=["sentinel-2-l2a"],
            intersects=geom_mapping,
            datetime=date_str,
            query={"eo:cloud_cover": {"lt": cloud_cover}}
        )
        items = list(search.items())
        if not items:
            msg_err = (f"No suitable Sentinel-2 images found for area/date ({date_str}) "
                       f"and max cloud cover ({cloud_cover}%).")
            log_adapter.error(msg_err)
            update_job_status(job_id, JobStatus.FAILED, 15, msg_err)
            return

        items.sort(key=lambda it: (it.properties.get('eo:cloud_cover', 101), it.datetime))
        selected = items[0]
        sel_date = selected.datetime.strftime('%Y-%m-%d')
        update_job_status(job_id, JobStatus.PROCESSING, 20, f"Downloading image data from {sel_date}")

        # --- 3. Download bands ---
        red_href = planetary_computer.sign(selected.assets["B04"].href)
        nir_href = planetary_computer.sign(selected.assets["B08"].href)
        rgb_href = planetary_computer.sign(selected.assets["visual"].href)

        with ThreadPoolExecutor(max_workers=3) as executor:
            red_fut   = executor.submit(read_band, job_id, red_href)
            nir_fut   = executor.submit(read_band, job_id, nir_href)
            rgb_fut   = executor.submit(read_rgb,  job_id, rgb_href)
            red, nir, rgb = red_fut.result(), nir_fut.result(), rgb_fut.result()

        update_job_status(job_id, JobStatus.PROCESSING, 40, "Calculating NDVI and generating images")

        # --- 4. NDVI & masks ---
        nir_plus_red = nir + red
        ndvi = np.full(nir.shape, np.nan, dtype=np.float32)
        valid = (nir_plus_red > 1e-9) & ~np.isnan(nir) & ~np.isnan(red)
        ndvi[valid] = (nir[valid] - red[valid]) / nir_plus_red[valid]

        rgb_norm = normalize_image(job_id, rgb)
        stress_mask = (ndvi < ndvi_thresh) & ~np.isnan(ndvi)

        # --- 5. Plotting ---
        plt.style.use('seaborn-v0_8-darkgrid')

        # RGB
        fig1, ax1 = plt.subplots(figsize=(8,8))
        ax1.imshow(rgb_norm); ax1.set_title(f'RGB Snapshot ({sel_date})'); ax1.axis('off')
        plt.tight_layout()
        buf_rgb = io.BytesIO(); plt.savefig(buf_rgb, format='png', dpi=150); plt.close(fig1)

        # NDVI
        fig2, ax2 = plt.subplots(figsize=(8,8))
        im = ax2.imshow(ndvi, vmin=-0.5, vmax=1, cmap="RdYlGn")
        ax2.set_title(f'NDVI ({sel_date})'); ax2.axis('off')
        fig2.colorbar(im, ax=ax2, fraction=0.046, pad=0.04, label="NDVI")
        plt.tight_layout()
        buf_ndvi = io.BytesIO(); plt.savefig(buf_ndvi, format='png', dpi=150); plt.close(fig2)

        # Stress mask
        fig3, ax3 = plt.subplots(figsize=(8,8))
        ax3.imshow(ndvi, vmin=-0.5, vmax=1, cmap="RdYlGn", alpha=0.6)
        viz = np.zeros((*stress_mask.shape, 4))
        viz[stress_mask] = [1, 0, 0, 0.7]
        ax3.imshow(viz)
        ax3.set_title(f'Stress Areas (NDVI < {ndvi_thresh})'); ax3.axis('off')
        plt.tight_layout()
        buf_stress = io.BytesIO(); plt.savefig(buf_stress, format='png', dpi=150); plt.close(fig3)

        update_job_status(job_id, JobStatus.PROCESSING, 70, "Uploading images")

        # --- 6. Upload images ---
        with ThreadPoolExecutor(max_workers=3) as executor:
            fut_rgb    = executor.submit(helpers.upload_image_to_blob, job_id, buf_rgb,    "satellite_rgb.png")
            fut_ndvi   = executor.submit(helpers.upload_image_to_blob, job_id, buf_ndvi,   "ndvi.png")
            fut_stress = executor.submit(helpers.upload_image_to_blob, job_id, buf_stress, "stress_mask.png")
            url_rgb, url_ndvi, url_stress = fut_rgb.result(), fut_ndvi.result(), fut_stress.result()

        update_job_status(job_id, JobStatus.PROCESSING, 85, "Generating recommendations")

        # --- 7. AI recommendations ---
        summary = generate_openai_recommendations(job_id, ndvi, stress_mask, crop)
        update_job_status(job_id, JobStatus.PROCESSING, 95, "Saving final report")

        # --- 8. Final report ---
        avg_ndvi  = float(np.nanmean(ndvi)) if np.any(~np.isnan(ndvi)) else None
        stress_pct= float((np.sum(stress_mask) / np.sum(~np.isnan(ndvi))) * 100) if np.any(~np.isnan(ndvi)) else None

        report = ReportData(
            jobId=job_id,
            status=JobStatus.COMPLETED,
            summary=summary,
            snapshotImageUrl=url_rgb,
            ndviImageUrl=url_ndvi,
            stressZoneImageUrl=url_stress,
            input_parameters=payload.model_dump(),
            processing_details={
                "image_id": selected.id,
                "image_date": sel_date,
                "image_cloud_cover": selected.properties.get('eo:cloud_cover', 'N/A'),
                "avg_ndvi": avg_ndvi,
                "stress_percentage": stress_pct
            }
        )
        upload_report_to_blob(job_id, report)
        update_job_status(job_id, JobStatus.COMPLETED, 100, "Analysis complete")

        end_time = time.time()
        log_adapter.info(f"Finished in {end_time - start_time:.2f}s")

    except Exception as e:
        logger = log_adapter or logging.getLogger(__name__)
        logger.error(f"Failed to process message: {e}", exc_info=True)
        if job_id:
            try:
                msg_err = f"Processing failed: {type(e).__name__}: {str(e)[:500]}"
                update_job_status(job_id, JobStatus.FAILED, -1, msg_err)
            except Exception as ue:
                logging.getLogger(__name__).error(
                    f"Also failed updating status for job {job_id}: {ue}"
                )
        raise
