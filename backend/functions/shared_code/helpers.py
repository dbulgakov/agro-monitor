import logging
import os
import io
import json
import numpy as np
import rasterio
import openai
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core.exceptions import ResourceNotFoundError

# Use relative import for schemas within the same package level
from .schemas import ReportData, JobStatus

# --- Environment Variables --- #
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

# --- Azure Clients (Initialize once if possible, or create on demand) --- #
def get_blob_service_client():
    """Creates a BlobServiceClient instance."""
    if not AZURE_STORAGE_CONNECTION_STRING:
        logging.error("AzureWebJobsStorage connection string is not configured.")
        raise ValueError("AzureWebJobsStorage connection string is not configured.")
    # Consider creating this client once per application instance if performance is critical
    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)

openai_client = None
if OPENAI_API_KEY:
    try:
        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
else:
    logging.warning("OPENAI_API_KEY is not set. AI recommendations will be disabled.")

# --- Job Status & Blob Operations --- #
def update_job_status(job_id: str, status: JobStatus, progress: int, message: str = None):
    """Updates the job status, progress, and optional message stored in blob metadata."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Updating status: Status={status.value}, Progress={progress}%, Message='{message}'")
    try:
        blob_service_client = get_blob_service_client()
        blob_client = blob_service_client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=f"{job_id}.json")

        metadata = {"jobStatus": status.value, "jobProgress": str(progress)}
        if message:
            metadata["jobMessage"] = (message[:1020] + '...') if len(message) > 1024 else message

        if not blob_client.exists():
            initial_data = ReportData(jobId=job_id, status=status).model_dump_json(exclude_none=True)
            blob_client.upload_blob(initial_data, overwrite=True, metadata=metadata)
            log_adapter.info(f"Created initial report blob with status {status.value}")
        else:
            existing_metadata = blob_client.get_blob_properties().metadata
            if not existing_metadata:
                existing_metadata = {}
            existing_metadata.update(metadata)
            blob_client.set_blob_metadata(metadata=existing_metadata)
            log_adapter.info(f"Updated report blob metadata")

    except Exception as e:
        log_adapter.error(f"Failed to update status metadata: {e}", exc_info=True)

def upload_image_to_blob(job_id: str, image_buffer: io.BytesIO, image_name: str) -> str | None:
    """Uploads an image from a BytesIO buffer to blob storage and returns its URL or None on failure."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Uploading image '{image_name}'")
    try:
        blob_service_client = get_blob_service_client()
        blob_name = f"{job_id}/{image_name}"
        blob_client = blob_service_client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=blob_name)

        image_buffer.seek(0)
        blob_client.upload_blob(
            image_buffer,
            blob_type="BlockBlob",
            overwrite=True,
            content_settings=ContentSettings(content_type='image/png')
        )
        log_adapter.info(f"Successfully uploaded image to {blob_client.url}")
        return blob_client.url
    except Exception as e:
        log_adapter.error(f"Failed to upload image {image_name}: {e}", exc_info=True)
        return None # Return None on failure

def upload_report_to_blob(job_id: str, report_data: ReportData):
    """Uploads the final report data as JSON to blob storage, preserving status metadata."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Uploading final report JSON")
    try:
        blob_service_client = get_blob_service_client()
        blob_client = blob_service_client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=f"{job_id}.json")

        metadata = {"jobStatus": report_data.status.value, "jobProgress": "100"}
        try:
            properties = blob_client.get_blob_properties()
            existing_metadata = properties.metadata or {}
            existing_metadata.update(metadata)
            metadata = existing_metadata
        except ResourceNotFoundError:
            log_adapter.warning(f"Report blob didn't exist when trying to finalize. Creating.")
        except Exception as meta_e:
            log_adapter.warning(f"Could not get existing metadata before final upload: {meta_e}")

        report_json = report_data.model_dump_json(exclude_none=True)
        blob_client.upload_blob(report_json, overwrite=True, metadata=metadata)
        log_adapter.info(f"Successfully uploaded final report.")

    except Exception as e:
        log_adapter.error(f"Failed to upload final report: {e}", exc_info=True)
        raise

# --- OpenAI Recommendations --- #
def generate_openai_recommendations(job_id: str, ndvi_data: np.ndarray, stress_mask: np.ndarray, crop_type: str) -> str:
    """Generates textual recommendations based on NDVI analysis using OpenAI."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Generating OpenAI recommendations for crop: {crop_type}")
    if not openai_client:
        log_adapter.warning(f"OpenAI client not available. Skipping recommendations.")
        return "AI recommendations disabled: OpenAI API key not configured."

    try:
        valid_ndvi = ndvi_data[~np.isnan(ndvi_data)]
        if valid_ndvi.size == 0:
            log_adapter.warning(f"No valid NDVI data for OpenAI recommendations.")
            return "AI recommendations unavailable: No valid NDVI data."

        avg_ndvi = np.mean(valid_ndvi)
        median_ndvi = np.median(valid_ndvi)
        min_ndvi = np.min(valid_ndvi)
        max_ndvi = np.max(valid_ndvi)
        stress_percentage = (np.sum(stress_mask) / valid_ndvi.size) * 100 if valid_ndvi.size > 0 else 0

        prompt = (
            f"Analyze the following satellite NDVI data for a field intended for {crop_type}. "
            f"The NDVI values range from -1 (lowest vegetation health/density) to +1 (highest). "
            f"A stress mask indicates areas where NDVI is below a threshold (e.g., < 0.3), suggesting potential issues.\n\n"
            f"Key statistics:\n"
            f"- Average NDVI: {avg_ndvi:.3f}\n"
            f"- Median NDVI: {median_ndvi:.3f}\n"
            f"- Min NDVI: {min_ndvi:.3f}\n"
            f"- Max NDVI: {max_ndvi:.3f}\n"
            f"- Approximate area showing stress: {stress_percentage:.1f}%\n\n"
            f"Based on these statistics, provide:\n"
            f"1. A brief, overall assessment of the field's vegetation health (e.g., uniform, variable, stressed).\n"
            f"2. Concise, actionable recommendations for the farmer, considering it's a {crop_type} field. "
            f"Focus on potential causes for low NDVI areas (if any) and suggest next steps (e.g., ground truthing, soil sampling, irrigation/fertilizer adjustments). "
            f"Keep the language practical and easy to understand for a farmer.\n"
            f"Limit the total response to about 3-4 sentences."
        )

        log_adapter.info(f"Sending prompt to OpenAI model {OPENAI_MODEL}...")
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=150
        )
        recommendation = response.choices[0].message.content.strip()
        log_adapter.info(f"Received recommendations from OpenAI.")
        return recommendation

    except Exception as e:
        log_adapter.error(f"OpenAI API call failed: {e}", exc_info=True)
        return "Failed to generate AI recommendations due to an error."

# --- Planetary Computer Data Fetching --- #
def read_band(job_id: str, url: str) -> np.ndarray:
    """Reads a single band from a signed URL using rasterio."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.debug(f"Opening rasterio URL: {url[:100]}...")
    try:
        with rasterio.open(url) as src:
            log_adapter.debug(f"Reading band 1...")
            data = src.read(1).astype("float32")
            log_adapter.debug(f"Finished reading band. Shape: {data.shape}")
            return data
    except Exception as e:
        log_adapter.error(f"Failed to read band from URL (starts {url[:50]}...): {e}")
        raise

def read_rgb(job_id: str, url: str) -> np.ndarray:
    """Reads RGB bands from a signed URL using rasterio."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.debug(f"Opening rasterio URL for RGB: {url[:100]}...")
    try:
        with rasterio.open(url) as src:
            log_adapter.debug(f"Reading bands 1, 2, 3...")
            img = src.read([1, 2, 3])
            log_adapter.debug(f"Transposing image...")
            img = np.transpose(img, (1, 2, 0))
            log_adapter.debug(f"Finished reading RGB. Shape: {img.shape}")
            return img.astype("float32")
    except Exception as e:
        log_adapter.error(f"Failed to read RGB from URL (starts {url[:50]}...): {e}")
        raise

# --- Image Processing --- #
def normalize_image(job_id: str, img: np.ndarray, lower: float = 2, upper: float = 98) -> np.ndarray:
    """Normalizes image data to 0-1 range based on percentiles."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.debug(f"Normalizing image...")
    valid_pixels = img[~np.isnan(img)]
    if valid_pixels.size == 0:
        log_adapter.warning(f"Attempting to normalize an image with no valid pixels.")
        return np.zeros_like(img)
    vmin, vmax = np.percentile(valid_pixels, lower), np.percentile(valid_pixels, upper)
    log_adapter.debug(f"Normalization percentiles: vmin={vmin}, vmax={vmax}")
    if vmax - vmin < 1e-6:
        log_adapter.warning(f"Image normalization range too small (vmin={vmin}, vmax={vmax}). Clamping.")
        return np.ones_like(img) if vmin > 0.5 else np.zeros_like(img)

    img_normalized = (img - vmin) / (vmax - vmin)
    result = np.clip(img_normalized, 0, 1)
    log_adapter.debug(f"Finished normalizing image.")
    return result 