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
matplotlib.use('Agg') # Use non-interactive backend suitable for Azure Functions
import matplotlib.pyplot as plt
import rasterio
from pystac_client import Client
import planetary_computer
from shapely.geometry import shape, mapping
import openai

# Import schemas from shared code
from ..shared_code.schemas import StartAnalysisPayload, ReportData, JobStatus, ErrorResponse

# Import shared code
from shared_code.helpers import (
    update_job_status,
    upload_image_to_blob,
    upload_report_to_blob,
    generate_openai_recommendations,
    read_band,
    read_rgb,
    normalize_image
)

# Environment variables
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
# ... other env vars ...
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
ANALYSIS_QUEUE_NAME = os.getenv("ANALYSIS_QUEUE_NAME", "analysis-requests")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

# ... OpenAI client init ...
if OPENAI_API_KEY:
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None
    logging.warning("OPENAI_API_KEY is not set. AI recommendations will be disabled.")

# ... Helper functions (get_blob_service_client, update_job_status, etc.) ...
# These helpers remain the same, they don't need schema imports directly

# Remove Blueprint
# bp = func.Blueprint()

# @bp.queue_trigger(...)
def main(msg: func.QueueMessage):
    logging.info(f'Python queue trigger function started processing message ID: {msg.id}')
    start_time = time.time()

    job_id = None
    log_adapter = None
    try:
        message_body = msg.get_body().decode('utf-8')
        message_data = json.loads(message_body)
        job_id = message_data.get("jobId")
        payload_dict = message_data.get("payload")

        log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id or 'UNKNOWN'})

        if not job_id or not payload_dict:
            log_adapter.error(f"Received invalid message format in queue: {message_body}")
            return

        try:
            payload = StartAnalysisPayload.model_validate(payload_dict)
        except Exception as validation_error:
            log_adapter.error(f"Payload validation failed: {validation_error}", exc_info=True)
            update_job_status(job_id, JobStatus.FAILED, 0, f"Invalid input payload: {str(validation_error)[:500]}")
            return

        log_adapter.info(f"Processing for crop '{payload.crop_type}'")
        update_job_status(job_id, JobStatus.PROCESSING, 5, "Starting analysis")

        # 1. Extract parameters and prepare geometry
        log_adapter.debug(f"Parsing geometry")
        geometry = shape(payload.area.geometry.model_dump())
        geom_mapping = mapping(geometry)
        date_str = payload.date_range
        cloud_cover = payload.max_cloud_cover
        ndvi_thresh = payload.ndvi_threshold
        crop = payload.crop_type
        log_adapter.debug(f"Params: Date={date_str}, Cloud<{cloud_cover}, NDVI Thresh={ndvi_thresh}")

        update_job_status(job_id, JobStatus.PROCESSING, 10, "Searching for satellite images")

        # 2. Search Planetary Computer STAC API
        log_adapter.debug(f"Opening STAC catalog")
        catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
        log_adapter.debug(f"Performing STAC search...")
        search = catalog.search(
            collections=["sentinel-2-l2a"],
            intersects=geom_mapping,
            datetime=date_str,
            query={"eo:cloud_cover": {"lt": cloud_cover}}
        )

        items = list(search.items())
        log_adapter.info(f"Found {len(items)} potential images. Selecting best one.")
        if not items:
            error_msg = f"No suitable Sentinel-2 images found for the specified area, date range ({date_str}), and max cloud cover ({cloud_cover}%)."
            log_adapter.error(error_msg)
            update_job_status(job_id, JobStatus.FAILED, 15, error_msg)
            return

        items.sort(key=lambda item: (item.properties.get('eo:cloud_cover', 101), item.datetime), reverse=False)
        selected_item = items[0]
        selected_date = selected_item.datetime.strftime('%Y-%m-%d')
        cloud_prop = selected_item.properties.get('eo:cloud_cover', 'N/A')
        log_adapter.info(f"Selected image: {selected_item.id} from {selected_date} (Cloud cover: {cloud_prop} %)")

        update_job_status(job_id, JobStatus.PROCESSING, 20, f"Downloading image data from {selected_date}")

        log_adapter.debug(f"Signing asset URLs")
        red_href = planetary_computer.sign(selected_item.assets["B04"].href)
        nir_href = planetary_computer.sign(selected_item.assets["B08"].href)
        rgb_href = planetary_computer.sign(selected_item.assets["visual"].href)

        # 3. Download bands concurrently
        log_adapter.info(f"Starting concurrent download")
        red, nir, rgb = None, None, None
        with ThreadPoolExecutor(max_workers=3) as executor:
            red_future = executor.submit(read_band, job_id, red_href)
            nir_future = executor.submit(read_band, job_id, nir_href)
            rgb_future = executor.submit(read_rgb, job_id, rgb_href)
            red = red_future.result()
            nir = nir_future.result()
            rgb = rgb_future.result()
        log_adapter.info(f"Finished concurrent download")

        if red is None or red.size == 0 or nir is None or nir.size == 0 or rgb is None or rgb.size == 0:
             raise Exception("Failed to download valid image data for one or more required bands.")

        log_adapter.info(f"Image data downloaded. Red Shape: {red.shape}, RGB Shape: {rgb.shape}")
        update_job_status(job_id, JobStatus.PROCESSING, 40, "Calculating NDVI and generating images")

        # 4. Calculate NDVI, normalize RGB, create stress mask
        log_adapter.debug(f"Calculating NDVI")
        nir_plus_red = nir + red
        ndvi = np.full(nir.shape, np.nan, dtype=np.float32)
        valid_mask = (nir_plus_red > 1e-9) & ~np.isnan(nir) & ~np.isnan(red)
        ndvi[valid_mask] = (nir[valid_mask] - red[valid_mask]) / nir_plus_red[valid_mask]

        log_adapter.debug(f"Normalizing RGB image")
        rgb_normalized = normalize_image(job_id, rgb)
        log_adapter.debug(f"Creating stress mask (NDVI < {ndvi_thresh})")
        stress_mask = np.logical_and(ndvi < ndvi_thresh, ~np.isnan(ndvi))

        # --- Generate Plots --- #
        log_adapter.info(f"Generating analysis images")
        plt.style.use('seaborn-v0_8-darkgrid')

        log_adapter.debug(f"Generating RGB plot")
        fig_rgb, ax_rgb = plt.subplots(figsize=(8, 8))
        ax_rgb.imshow(rgb_normalized)
        ax_rgb.set_title(f'RGB Snapshot ({selected_date})')
        ax_rgb.axis('off')
        plt.tight_layout()
        rgb_buffer = io.BytesIO()
        plt.savefig(rgb_buffer, format='png', dpi=150)
        plt.close(fig_rgb)

        log_adapter.debug(f"Generating NDVI plot")
        fig_ndvi, ax_ndvi = plt.subplots(figsize=(8, 8))
        im_ndvi = ax_ndvi.imshow(ndvi, vmin=-0.5, vmax=1, cmap="RdYlGn")
        ax_ndvi.set_title(f'NDVI ({selected_date})')
        ax_ndvi.axis('off')
        fig_ndvi.colorbar(im_ndvi, ax=ax_ndvi, fraction=0.046, pad=0.04, label="NDVI")
        plt.tight_layout()
        ndvi_buffer = io.BytesIO()
        plt.savefig(ndvi_buffer, format='png', dpi=150)
        plt.close(fig_ndvi)

        log_adapter.debug(f"Generating Stress Mask plot")
        fig_stress, ax_stress = plt.subplots(figsize=(8, 8))
        ax_stress.imshow(ndvi, vmin=-0.5, vmax=1, cmap="RdYlGn", alpha=0.6)
        stress_viz = np.zeros((*stress_mask.shape, 4))
        stress_viz[stress_mask] = [1, 0, 0, 0.7]
        ax_stress.imshow(stress_viz)
        ax_stress.set_title(f'Potential Stress Areas (NDVI < {ndvi_thresh}) - {selected_date}')
        ax_stress.axis('off')
        plt.tight_layout()
        stress_buffer = io.BytesIO()
        plt.savefig(stress_buffer, format='png', dpi=150)
        plt.close(fig_stress)

        log_adapter.info(f"Finished generating analysis images")
        update_job_status(job_id, JobStatus.PROCESSING, 70, "Uploading images")

        # 5. Upload images to Blob Storage
        log_adapter.info(f"Starting concurrent image upload")
        rgb_url, ndvi_url, stress_url = None, None, None
        with ThreadPoolExecutor(max_workers=3) as executor:
            rgb_upload_future = executor.submit(upload_image_to_blob, job_id, rgb_buffer, "satellite_rgb.png")
            ndvi_upload_future = executor.submit(upload_image_to_blob, job_id, ndvi_buffer, "ndvi.png")
            stress_upload_future = executor.submit(upload_image_to_blob, job_id, stress_buffer, "stress_mask.png")
            rgb_url = rgb_upload_future.result()
            ndvi_url = ndvi_upload_future.result()
            stress_url = stress_upload_future.result()
        log_adapter.info(f"Finished concurrent image upload")

        if not rgb_url or not ndvi_url or not stress_url:
            log_adapter.warning(f"One or more images failed to upload. Report URLs might be missing.")

        update_job_status(job_id, JobStatus.PROCESSING, 85, "Generating recommendations")

        # 6. Generate recommendations using OpenAI
        summary = generate_openai_recommendations(job_id, ndvi, stress_mask, crop)

        update_job_status(job_id, JobStatus.PROCESSING, 95, "Saving final report")

        # 7. Prepare and store final report data
        log_adapter.debug(f"Preparing final report data object")
        avg_ndvi_val = float(np.nanmean(ndvi)) if np.any(~np.isnan(ndvi)) else None
        stress_perc_val = float((np.sum(stress_mask) / np.sum(~np.isnan(ndvi))) * 100) if np.any(~np.isnan(ndvi)) else None

        report_data = ReportData(
            jobId=job_id,
            status=JobStatus.COMPLETED,
            summary=summary,
            snapshotImageUrl=rgb_url,
            ndviImageUrl=ndvi_url,
            stressZoneImageUrl=stress_url,
            input_parameters=payload.model_dump(),
            processing_details={
                "image_id": selected_item.id,
                "image_date": selected_date,
                "image_cloud_cover": cloud_prop,
                "avg_ndvi": avg_ndvi_val,
                "stress_percentage": stress_perc_val
            }
        )
        upload_report_to_blob(job_id, report_data)
        update_job_status(job_id, JobStatus.COMPLETED, 100, "Analysis complete")

        end_time = time.time()
        log_adapter.info(f"Successfully processed. Total time: {end_time - start_time:.2f} seconds.")

    except Exception as e:
        logger_to_use = log_adapter if log_adapter else logging.getLogger(__name__)
        logger_to_use.error(f"Failed to process message: {e}", exc_info=True)
        if job_id:
            try:
                error_message = f"Processing failed: {type(e).__name__}: {str(e)[:500]}"
                update_job_status(job_id, JobStatus.FAILED, -1, error_message)
            except Exception as status_update_error:
                logging.getLogger(__name__).error(f"Additionally failed to update status to FAILED for job {job_id}: {status_update_error}")
        raise 