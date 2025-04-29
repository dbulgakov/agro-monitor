import logging
import json
import azure.functions as func
from typing import Optional
import asyncio
import numpy as np
from datetime import datetime
import io # Add io
from PIL import Image # Add Pillow
import matplotlib.pyplot as plt # Add Matplotlib
import matplotlib.colors as colors # Add Matplotlib colors

# Относительные импорты для shared code
from ..shared_code.schemas import StartAnalysisPayload, ReportData, JobStatus, JobStatusData
from ..shared_code.helpers import (
    update_job_status,
    get_job_status,
    upload_image_to_blob,
    upload_report_to_blob,
    generate_openai_recommendations,
    read_band,
    read_rgb,
    normalize_image,
    get_sentinel2_urls # Import the new helper
    # Add helpers for Planetary Computer data fetching if available
)
# Импорт модели данных запроса необходим для десериализации
from ..shared_code.schemas import StartAnalysisPayload

async def main(msg: func.QueueMessage):
    """Azure Function triggered by a message in the analysis queue.
    Processes the analysis request defined in the message.
    """
    job_id = None # Initialize in case of error before getting job_id
    try:
        # 1. Get and parse message from queue
        message_body = msg.get_body().decode('utf-8')
        logging.info(f"Python Queue trigger function processed a queue item: {message_body[:500]}") # Log start of message
        message_data = json.loads(message_body)

        job_id = message_data.get("jobId")
        payload_dict = message_data.get("payload")

        if not job_id or not payload_dict:
            logging.error(f"Missing 'jobId' or 'payload' in queue message: {message_body}")
            return

        log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
        log_adapter.info(f"Received message for Job ID: {job_id}")

        # --- Idempotency Check --- 
        current_status_data = await get_job_status(job_id)
        if current_status_data:
            if current_status_data.status == JobStatus.COMPLETED:
                log_adapter.info(f"Job {job_id} is already completed. Skipping processing.")
                return
            elif current_status_data.status == JobStatus.FAILED:
                 log_adapter.warning(f"Job {job_id} previously failed. Skipping processing.")
                 # Optionally: Allow reprocessing of failed jobs based on message or config?
                 return
            elif current_status_data.status == JobStatus.PROCESSING:
                 # Basic check: If already processing, log and exit to avoid concurrent runs.
                 # More advanced: Check timestamp of last update, if too old, maybe take over?
                 log_adapter.warning(f"Job {job_id} is already marked as PROCESSING. Skipping duplicate run.")
                 return
            # If PENDING or UNKNOWN, proceed with processing.
            log_adapter.info(f"Current status is {current_status_data.status}. Proceeding with processing.")
        else:
            # If status blob doesn't exist (shouldn't happen if StartAnalysis worked), log and proceed cautiously.
            log_adapter.warning(f"Could not retrieve current status for job {job_id}. Proceeding anyway.")

        # Deserialize payload (Pydantic validation)
        try:
            payload = StartAnalysisPayload.model_validate(payload_dict)
            log_adapter.info(f"Successfully parsed payload for area: {payload.area.geometry.type.value}, crop: {payload.crop_type}")
        except Exception as p_err: # Catch Pydantic validation errors
             log_adapter.error(f"Payload validation failed: {p_err}", exc_info=True)
             await update_job_status(job_id, JobStatus.FAILED, -1, f"Invalid payload in queue message: {p_err}")
             return # Finish processing, status updated

        # -----------------------------------------------------
        # 2. Core Analysis Logic
        # -----------------------------------------------------
        await update_job_status(job_id, JobStatus.PROCESSING, 10, "Starting data acquisition")

        # --- Step 2.1: Data Acquisition (e.g., satellite images) ---
        # Logic to get image URLs (NIR, Red, RGB) based on payload
        # Use the new helper function
        try:
            log_adapter.info(f"Requesting bands: nir, red, visual")
            # Request needed bands from the helper
            image_urls = await get_sentinel2_urls(
                job_id=job_id,
                geometry=payload.area.geometry.model_dump(), # Pass geometry dict
                date_range=payload.date_range,
                bands=['nir', 'red', 'visual'] # Explicitly request needed bands
            )
            nir_url = image_urls['nir']
            red_url = image_urls['red']
            rgb_url = image_urls['visual'] # Sentinel-2 uses 'visual' asset for RGB
            log_adapter.info("Successfully obtained signed URLs for Sentinel-2 bands.")

        except FileNotFoundError as data_not_found_err:
            log_adapter.error(f"Failed to find suitable satellite data: {data_not_found_err}", exc_info=True)
            await update_job_status(job_id, JobStatus.FAILED, -1, f"Data acquisition failed: {data_not_found_err}")
            return # Stop processing if no data found
        except Exception as data_acq_err:
            log_adapter.error(f"Failed during data acquisition: {data_acq_err}", exc_info=True)
            await update_job_status(job_id, JobStatus.FAILED, -1, f"Data acquisition failed: {data_acq_err}")
            return # Stop processing on other acquisition errors

        # --- Step 2.2: Read Data and Calculate NDVI ---
        await update_job_status(job_id, JobStatus.PROCESSING, 30, "Calculating NDVI")
        nir_band, red_band, rgb_image = None, None, None # Initialize
        try:
            # Read bands concurrently using the obtained URLs, collecting results/exceptions
            log_adapter.info("Reading NIR, Red, and RGB bands...")
            results = await asyncio.gather(
                read_band(job_id, nir_url),
                read_band(job_id, red_url),
                read_rgb(job_id, rgb_url),
                return_exceptions=True # Important: Return exceptions instead of raising immediately
            )

            # Check results for exceptions
            nir_result, red_result, rgb_result = results

            read_errors = []
            if isinstance(nir_result, Exception):
                log_adapter.error(f"Failed to read NIR band: {nir_result}", exc_info=nir_result)
                read_errors.append(f"NIR read error: {type(nir_result).__name__}")
            else:
                nir_band = nir_result
                log_adapter.info("NIR band read successfully.")

            if isinstance(red_result, Exception):
                log_adapter.error(f"Failed to read Red band: {red_result}", exc_info=red_result)
                read_errors.append(f"Red read error: {type(red_result).__name__}")
            else:
                red_band = red_result
                log_adapter.info("Red band read successfully.")

            if isinstance(rgb_result, Exception):
                # Log RGB error but don't necessarily stop processing if NIR/Red are ok
                log_adapter.warning(f"Failed to read RGB bands: {rgb_result}", exc_info=rgb_result)
                # We can continue without the RGB image
                rgb_image = None # Explicitly set to None
            else:
                rgb_image = rgb_result
                log_adapter.info("RGB bands read successfully.")

            # If critical bands (NIR or Red) failed, stop processing
            if nir_band is None or red_band is None:
                error_message = f"Failed to read critical bands: {', '.join(read_errors)}"
                log_adapter.error(error_message)
                await update_job_status(job_id, JobStatus.FAILED, -1, error_message)
                return

            # NDVI Calculation (example)
            log_adapter.info("Calculating NDVI...")
            np.seterr(divide='ignore', invalid='ignore')
            ndvi = (nir_band.astype(float) - red_band.astype(float)) / (nir_band.astype(float) + red_band.astype(float))
            ndvi[np.isnan(ndvi)] = 0
            log_adapter.info("NDVI calculated.")

        except Exception as data_err:
            # Catch any other unexpected errors during this block
            log_adapter.error(f"Unexpected error during data processing: {data_err}", exc_info=True)
            await update_job_status(job_id, JobStatus.FAILED, -1, f"Data processing error: {data_err}")
            return

        # --- Step 2.3: Generate Visualizations (NDVI map, RGB) ---
        await update_job_status(job_id, JobStatus.PROCESSING, 60, "Generating visualizations")
        ndvi_map_url: Optional[str] = None
        rgb_map_url: Optional[str] = None
        try:
            # Generate and upload NDVI map (only if ndvi was calculated)
            if ndvi is not None and np.any(~np.isnan(ndvi)):
                 ndvi_map_buffer = create_ndvi_png_buffer(ndvi)
                 ndvi_map_url = await upload_image_to_blob(job_id, ndvi_map_buffer, "ndvi_map.png")
                 log_adapter.info(f"NDVI map uploaded to: {ndvi_map_url}")
            else:
                 log_adapter.warning("Skipping NDVI map generation (NDVI is None or all NaN).")

            # Normalize and upload RGB map (only if rgb_image was read successfully)
            if rgb_image is not None and np.any(~np.isnan(rgb_image)):
                 rgb_normalized = normalize_image(job_id, rgb_image)
                 rgb_map_buffer = create_rgb_png_buffer(rgb_normalized)
                 rgb_map_url = await upload_image_to_blob(job_id, rgb_map_buffer, "rgb_map.png")
                 log_adapter.info(f"RGB map uploaded to: {rgb_map_url}")
            else:
                 log_adapter.warning("Skipping RGB map generation (RGB image is None or all NaN).")

            # log_adapter.info("Visualizations generated and uploaded (PLACEHOLDER).")
            # Leave URLs empty for now
            # ndvi_map_url = "NDVI_MAP_URL_PLACEHOLDER"
            # rgb_map_url = "RGB_MAP_URL_PLACEHOLDER"

        except Exception as viz_err:
             log_adapter.error(f"Error generating or uploading visualizations: {viz_err}", exc_info=True)
             # Do not interrupt execution, report will be without images, but update status
             await update_job_status(job_id, JobStatus.PROCESSING, 75, f"Visualization error: {viz_err}. Proceeding without images.")
             # Could also add error message to the report itself

        # --- Step 2.4: Generate AI Recommendations ---
        await update_job_status(job_id, JobStatus.PROCESSING, 80, "Generating AI recommendations")
        recommendations = "N/A" # Default value
        try:
            # Example stress mask (NDVI < 0.3)
            stress_mask = ndvi < 0.3
            recommendations = await generate_openai_recommendations(job_id, ndvi, stress_mask, payload.crop_type)
            log_adapter.info("AI recommendations generated.")
        except Exception as ai_err:
            log_adapter.error(f"Error generating AI recommendations: {ai_err}", exc_info=True)
            recommendations = f"Failed to generate AI recommendations: {ai_err}"
            # Do not interrupt, record error in the report

        # --- Step 2.5: Assemble and Save Report ---
        await update_job_status(job_id, JobStatus.PROCESSING, 95, "Finalizing report")
        try:
            # Collect all data into the final report
            final_report = ReportData(
                jobId=job_id,
                status=JobStatus.COMPLETED, # Set Completed here
                requestPayload=payload.model_dump(), # Save original payload
                reportTimestamp=datetime.utcnow().isoformat(),
                ndviStatistics={ # Example statistics
                     "mean": float(np.nanmean(ndvi)) if np.any(~np.isnan(ndvi)) else None,
                     "median": float(np.nanmedian(ndvi)) if np.any(~np.isnan(ndvi)) else None,
                     "min": float(np.nanmin(ndvi)) if np.any(~np.isnan(ndvi)) else None,
                     "max": float(np.nanmax(ndvi)) if np.any(~np.isnan(ndvi)) else None,
                     "stress_percentage": float(np.sum(stress_mask) / ndvi.size * 100) if ndvi.size > 0 else 0,
                     # Add other stats as needed
                },
                mapUrls={
                    "ndvi": ndvi_map_url,
                    "rgb": rgb_map_url
                },
                recommendations=recommendations
            )

            # Upload final JSON report to Blob Storage
            await upload_report_to_blob(job_id, final_report)
            log_adapter.info("Final report uploaded successfully.")

        except Exception as report_err:
            log_adapter.error(f"Error finalizing or uploading report: {report_err}", exc_info=True)
            # If report saving failed, revert status to FAILED
            await update_job_status(job_id, JobStatus.FAILED, -1, f"Failed to save final report: {report_err}")
            return

        # Final success log
        log_adapter.info(f"Successfully processed Job ID: {job_id}")

    except Exception as e:
        # Top-level handler for unexpected errors
        job_id_context = f"Job ID {job_id}: " if job_id else "(Job ID not retrieved) "
        logging.critical(f"{job_id_context}Unhandled exception in ProcessAnalysisFunction: {e}", exc_info=True)

        # Attempt to update status to FAILED if job_id exists
        if job_id:
            try:
                # Use await since update_job_status is async
                await update_job_status(job_id, JobStatus.FAILED, -1, f"Unhandled internal error: {type(e).__name__}")
            except Exception as status_update_err:
                 logging.error(f"Job ID {job_id}: Additionally failed to update status to FAILED after unhandled exception: {status_update_err}")
        # Important! The Azure Function must raise an exception for the runtime
        # to know processing failed, allowing retry/poison queue handling.
        raise

# --- Helper Functions (Placeholders) ---
# Replace with actual implementations

def create_ndvi_png_buffer(ndvi_array: np.ndarray) -> io.BytesIO:
    """ Converts NDVI array to PNG buffer. (Example with matplotlib) """
    logger = logging.getLogger(__name__) # Get logger
    logger.info("Generating NDVI PNG buffer...")
    buf = io.BytesIO()
    # Ensure input is float for color mapping
    ndvi_float = ndvi_array.astype(float)
    cmap = plt.get_cmap('RdYlGn') # Colormap for NDVI
    norm = colors.Normalize(vmin=-0.2, vmax=1.0) # Adjust vmin/vmax based on expected NDVI range
    fig, ax = plt.subplots(figsize=(8, 8)) # Control figure size
    im = ax.imshow(ndvi_float, cmap=cmap, norm=norm)
    ax.set_title("NDVI Map")
    ax.axis('off') # Turn off axes
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="NDVI") # Add colorbar with label
    # Save figure to buffer
    try:
        plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.1, dpi=150) # Adjust dpi/padding
        logger.info("NDVI PNG buffer generated.")
    except Exception as e:
        logger.error(f"Failed to save NDVI map to buffer: {e}", exc_info=True)
        raise # Re-raise the exception
    finally:
        plt.close(fig) # Close figure to free memory

    buf.seek(0)
    return buf

def create_rgb_png_buffer(rgb_array_normalized: np.ndarray) -> io.BytesIO:
    """ Converts normalized RGB array to PNG buffer. (Example with Pillow) """
    logger = logging.getLogger(__name__) # Get logger
    logger.info("Generating RGB PNG buffer...")
    buf = io.BytesIO()
    try:
        # Ensure data is uint8 [0, 255]
        # Clip just in case normalization produced values slightly outside [0, 1]
        rgb_clipped = np.clip(rgb_array_normalized, 0, 1)
        rgb_uint8 = (rgb_clipped * 255).astype(np.uint8)
        img = Image.fromarray(rgb_uint8, 'RGB')
        img.save(buf, format='PNG')
        logger.info("RGB PNG buffer generated.")
    except Exception as e:
        logger.error(f"Failed to create or save RGB image buffer: {e}", exc_info=True)
        raise # Re-raise the exception

    buf.seek(0)
    return buf 