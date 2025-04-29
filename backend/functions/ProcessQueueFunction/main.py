import logging
import json
import os
import io
import time
from datetime import datetime
import asyncio

import azure.functions as func

import numpy as np
import matplotlib
matplotlib.use('Agg')  # non-interactive backend
import matplotlib.pyplot as plt

import rasterio
from shapely.geometry import shape, mapping

from ..shared_code.schemas import StartAnalysisPayload, ReportData, JobStatus
from ..shared_code.helpers import (
    update_job_status,
    upload_report_to_blob,
    generate_openai_recommendations,
    read_band,
    read_rgb,
    normalize_image,
    check_environment_variables,
    upload_image_to_blob,
    get_sentinel2_urls
)

# Define required variables, but check inside main
REQUIRED_ENV_VARS = [
    "AzureWebJobsStorage", 
    "REPORTS_CONTAINER_NAME", 
    "IMAGES_CONTAINER_NAME", 
    "ANALYSIS_QUEUE_NAME",
    "OPENAI_API_KEY" # Required for this function's core logic
]

# Get variables - they might be None if not set, check guards against this
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
ANALYSIS_QUEUE_NAME = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

# Initialize OpenAI client (can remain at module level, guarded by check in main)
if OPENAI_API_KEY:
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None 

async def main(msg: func.QueueMessage):
    try:
        check_environment_variables(REQUIRED_ENV_VARS)
    except ValueError as config_error:
        logging.critical(f"Configuration error: {config_error}. Function cannot proceed.")
        raise

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
            log_adapter.info(f"Validated payload for job {job_id}")
        except Exception as validation_error:
            log_adapter.error(f"Payload validation failed: {validation_error}", exc_info=True)
            try:
                await update_job_status(job_id, JobStatus.FAILED, -1, f"Payload validation failed: {validation_error}")
            except Exception as status_update_error:
                log_adapter.error(f"Failed to update status to FAILED after validation error: {status_update_error}")
            return

        await update_job_status(job_id, JobStatus.PROCESSING, 5, "Starting analysis")

        geometry = shape(payload.area.geometry.model_dump())
        geom_mapping = mapping(geometry)
        date_str = payload.date_range
        cloud_cover = payload.max_cloud_cover
        ndvi_thresh = payload.ndvi_threshold
        crop = payload.crop_type

        await update_job_status(job_id, JobStatus.PROCESSING, 10, "Searching for satellite images")

        try:
            band_urls = await get_sentinel2_urls(job_id, geometry, date_str)
            red_href = band_urls.get("red")
            nir_href = band_urls.get("nir")
            rgb_href = band_urls.get("visual")

            if not all([red_href, nir_href, rgb_href]):
                raise ValueError(f"Missing required band URLs: red={bool(red_href)}, nir={bool(nir_href)}, visual={bool(rgb_href)}")

            sel_date = "Selected Date"
            log_adapter.info(f"Retrieved band URLs for date: {sel_date}")

        except (FileNotFoundError, ValueError, RuntimeError) as stac_error:
            msg_err = f"Failed to find/get Sentinel-2 data: {stac_error}"
            log_adapter.error(msg_err)
            await update_job_status(job_id, JobStatus.FAILED, 15, msg_err)
            return

        await update_job_status(job_id, JobStatus.PROCESSING, 20, f"Downloading image data from {sel_date}")

        try:
            log_adapter.info("Starting concurrent band downloads...")
            red_task = asyncio.create_task(read_band(job_id, red_href))
            nir_task = asyncio.create_task(read_band(job_id, nir_href))
            rgb_task = asyncio.create_task(read_rgb(job_id, rgb_href))

            red, nir, rgb = await asyncio.gather(red_task, nir_task, rgb_task)
            log_adapter.info("Finished concurrent band downloads.")
        except rasterio.RasterioIOError as rio_error:
            msg_err = f"Failed reading image data: {rio_error}"
            log_adapter.error(msg_err, exc_info=True)
            # Let outer exception handler update status and re-raise
            raise

        await update_job_status(job_id, JobStatus.PROCESSING, 40, "Calculating NDVI and generating images")

        nir_plus_red = nir + red
        ndvi = np.full(nir.shape, np.nan, dtype=np.float32)
        valid = (nir_plus_red > 1e-9) & ~np.isnan(nir) & ~np.isnan(red)
        ndvi[valid] = (nir[valid] - red[valid]) / nir_plus_red[valid]
        log_adapter.info("NDVI calculation complete.")

        rgb_norm = normalize_image(job_id, rgb)
        log_adapter.info("RGB normalization complete.")

        stress_mask = (ndvi < ndvi_thresh) & ~np.isnan(ndvi)
        log_adapter.info("Stress mask created.")

        log_adapter.info("Generating plot images...")
        plt.style.use('seaborn-v0_8-darkgrid')

        fig1, ax1 = plt.subplots(figsize=(8,8))
        ax1.imshow(rgb_norm); ax1.set_title(f'RGB Snapshot ({sel_date})'); ax1.axis('off')
        plt.tight_layout()
        buf_rgb = io.BytesIO(); plt.savefig(buf_rgb, format='png', dpi=150); plt.close(fig1)

        fig2, ax2 = plt.subplots(figsize=(8,8))
        im = ax2.imshow(ndvi, vmin=-0.5, vmax=1, cmap="RdYlGn")
        ax2.set_title(f'NDVI ({sel_date})'); ax2.axis('off')
        fig2.colorbar(im, ax=ax2, fraction=0.046, pad=0.04, label="NDVI")
        plt.tight_layout()
        buf_ndvi = io.BytesIO(); plt.savefig(buf_ndvi, format='png', dpi=150); plt.close(fig2)

        fig3, ax3 = plt.subplots(figsize=(8,8))
        ax3.imshow(ndvi, vmin=-0.5, vmax=1, cmap="RdYlGn", alpha=0.6)
        viz = np.zeros((*stress_mask.shape, 4))
        viz[stress_mask] = [1, 0, 0, 0.7]
        ax3.imshow(viz)
        ax3.set_title(f'Stress Areas (NDVI < {ndvi_thresh})'); ax3.axis('off')
        plt.tight_layout()
        buf_stress = io.BytesIO(); plt.savefig(buf_stress, format='png', dpi=150); plt.close(fig3)
        log_adapter.info("Plot image generation complete.")

        await update_job_status(job_id, JobStatus.PROCESSING, 70, "Uploading images")

        try:
            upload_rgb_task = asyncio.create_task(upload_image_to_blob(job_id, buf_rgb, "satellite_rgb.png"))
            upload_ndvi_task = asyncio.create_task(upload_image_to_blob(job_id, buf_ndvi, "ndvi.png"))
            upload_stress_task = asyncio.create_task(upload_image_to_blob(job_id, buf_stress, "stress_mask.png"))

            url_rgb, url_ndvi, url_stress = await asyncio.gather(
                upload_rgb_task, upload_ndvi_task, upload_stress_task
            )
            log_adapter.info("Finished concurrent image uploads.")
        except Exception as upload_error:
            msg_err = f"Failed during image upload: {upload_error}"
            log_adapter.error(msg_err, exc_info=True)
            await update_job_status(job_id, JobStatus.FAILED, 75, msg_err)
            raise

        await update_job_status(job_id, JobStatus.PROCESSING, 85, "Generating recommendations")

        summary = await generate_openai_recommendations(job_id, ndvi, stress_mask, crop)
        if "Failed to generate AI recommendations" in summary:
            log_adapter.warning("AI recommendation generation failed, but proceeding.")

        await update_job_status(job_id, JobStatus.PROCESSING, 95, "Saving final report")

        avg_ndvi  = float(np.nanmean(ndvi)) if np.any(~np.isnan(ndvi)) else None
        stress_pct= float((np.sum(stress_mask) / np.sum(~np.isnan(ndvi))) * 100) if np.any(~np.isnan(ndvi)) else None
        report = ReportData(
            jobId=job_id,
            status=JobStatus.COMPLETED,
            summary=summary,
            requestPayload=payload.model_dump(),
            reportTimestamp=datetime.utcnow().isoformat() + "Z",
            ndviStatistics={"mean": avg_ndvi, "stress_percentage": stress_pct},
            mapUrls={"satellite_rgb": url_rgb, "ndvi": url_ndvi, "stress_mask": url_stress},
            recommendations=summary,
            errorMessage=None
        )

        log_adapter.info("Uploading final report...")
        await upload_report_to_blob(job_id, report)
        log_adapter.info("Final report uploaded.")

        await update_job_status(job_id, JobStatus.COMPLETED, 100, "Analysis complete")

        end_time = time.time()
        log_adapter.info(f"Finished in {end_time - start_time:.2f}s")

    except Exception as e:
        logger = log_adapter or logging.getLogger(__name__)
        logger.error(f"Failed to process message: {e}", exc_info=True)
        if job_id:
            try:
                msg_err = f"Processing failed: {type(e).__name__}: {str(e)[:500]}"
                await update_job_status(job_id, JobStatus.FAILED, -1, msg_err)
            except Exception as ue:
                logging.getLogger(__name__).error(
                    f"Also failed updating status for job {job_id}: {ue}"
                )
        raise
