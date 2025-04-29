import logging
import os
import io
import numpy as np
import rasterio
import openai
# Use async clients
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings # Keep sync for this
from azure.core.exceptions import ResourceNotFoundError
from typing import List, Optional
import asyncio # Import asyncio
import planetary_computer
from pystac_client import Client
import pystac
from shapely.geometry import shape
from shapely.errors import GEOSException

# Use relative import for schemas within the same package level
from .schemas import ReportData, JobStatus, JobStatusData

# --- Environment Variables --- #
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AzureWebJobsStorage")
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "reports")
IMAGES_CONTAINER_NAME = os.getenv("IMAGES_CONTAINER_NAME", "images")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
OPENAI_ASYNC_CLIENT_ENABLED = bool(os.getenv("USE_ASYNC_OPENAI_CLIENT", False)) # Flag for async client

# --- Azure Clients (Initialize once if possible, or create on demand) --- #
# Use async context manager for async client lifecycle
def get_async_blob_service_client() -> BlobServiceClient:
    """Creates an async BlobServiceClient instance."""
    if not AZURE_STORAGE_CONNECTION_STRING:
        logging.error("AzureWebJobsStorage connection string is not configured.")
        raise ValueError("AzureWebJobsStorage connection string is not configured.")
    # Return the client instance directly; manage with async with elsewhere
    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)

# Initialize OpenAI client (consider async version)
openai_client = None
openai_async_client = None # Placeholder for async client

if OPENAI_API_KEY:
    try:
        # Initialize sync client for potential sync usage or fallback
        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        logging.info("Sync OpenAI client initialized.")

        # Attempt to initialize async client if enabled/available
        if OPENAI_ASYNC_CLIENT_ENABLED:
            try:
                from openai import AsyncOpenAI
                openai_async_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
                logging.info("Async OpenAI client initialized.")
            except ImportError:
                logging.warning("Async OpenAI client requested but 'openai' async extras might be missing. Falling back to sync.")
                OPENAI_ASYNC_CLIENT_ENABLED = False # Disable if import fails
            except Exception as async_init_e:
                logging.error(f"Failed to initialize Async OpenAI client: {async_init_e}", exc_info=True)
                OPENAI_ASYNC_CLIENT_ENABLED = False # Disable on error

    except Exception as e:
        logging.error(f"Failed to initialize OpenAI client(s): {e}", exc_info=True)
else:
    logging.warning("OPENAI_API_KEY is not set. AI recommendations will be disabled.")

# --- Job Status & Blob Operations (Async) --- #

async def get_job_status(job_id: str) -> Optional[JobStatusData]:
    """Reads the job status data (status, progress, message) from blob metadata (async)."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.debug(f"Getting job status for {job_id}")
    async with get_async_blob_service_client() as blob_service_client:
        try:
            blob_client = blob_service_client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=f"{job_id}.json")
            if await blob_client.exists():
                properties = await blob_client.get_blob_properties()
                metadata = properties.metadata
                if metadata and "jobStatus" in metadata:
                    status = JobStatus(metadata.get("jobStatus", JobStatus.UNKNOWN.value))
                    progress = int(metadata.get("jobProgress", -1))
                    message = metadata.get("jobMessage")
                    log_adapter.debug(f"Found status: {status.value}, progress: {progress}")
                    return JobStatusData(status=status, progress=progress, message=message)
                else:
                    log_adapter.warning("Job blob exists but metadata or jobStatus is missing.")
                    return JobStatusData(status=JobStatus.UNKNOWN, progress=-1, message="Status metadata missing")
            else:
                log_adapter.warning("Job status blob does not exist.")
                return None # Or return UNKNOWN status
        except Exception as e:
            log_adapter.error(f"Failed to get job status: {e}", exc_info=True)
            # Decide on return value: None, UNKNOWN, or raise exception
            return JobStatusData(status=JobStatus.UNKNOWN, progress=-1, message=f"Error reading status: {e}")

async def update_job_status(job_id: str, status: JobStatus, progress: int, message: Optional[str] = None):
    """Updates the job status, progress, and optional message stored in blob metadata (async)."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Updating status (async): Status={status.value}, Progress={progress}%, Message='{message}'")
    # Use async client with async context manager
    async with get_async_blob_service_client() as blob_service_client:
        try:
            blob_client = blob_service_client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=f"{job_id}.json")

            metadata = {"jobStatus": status.value, "jobProgress": str(progress)}
            if message:
                # Ensure message encoding/length compatibility if needed
                metadata["jobMessage"] = (message[:1020] + '...') if len(message) > 1024 else message

            # Use await for async operations
            blob_exists = await blob_client.exists()

            if not blob_exists:
                initial_data = ReportData(jobId=job_id, status=status).model_dump_json(exclude_none=True)
                # Encode data to bytes for async upload
                await blob_client.upload_blob(initial_data.encode('utf-8'), overwrite=True, metadata=metadata)
                log_adapter.info(f"Created initial report blob with status {status.value}")
            else:
                # Get properties asynchronously
                properties = await blob_client.get_blob_properties()
                existing_metadata = properties.metadata
                if not existing_metadata:
                    existing_metadata = {}
                existing_metadata.update(metadata)
                # Set metadata asynchronously
                await blob_client.set_blob_metadata(metadata=existing_metadata)
                log_adapter.info(f"Updated report blob metadata")

        except Exception as e:
            # Log detailed error, consider raising specific exceptions
            log_adapter.error(f"Failed to update status metadata (async): {e}", exc_info=True)
            # Re-raise or handle appropriately (e.g., return failure status)
            raise

async def upload_image_to_blob(job_id: str, image_buffer: io.BytesIO, image_name: str) -> str | None:
    """Uploads an image from a BytesIO buffer to blob storage (async) and returns its URL or None on failure."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Uploading image '{image_name}' (async)")
    async with get_async_blob_service_client() as blob_service_client:
        try:
            blob_name = f"{job_id}/{image_name}"
            blob_client = blob_service_client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=blob_name)

            image_buffer.seek(0)
            # Read buffer content for async upload
            image_data = image_buffer.getvalue()
            await blob_client.upload_blob(
                image_data, # Pass bytes
                blob_type="BlockBlob",
                overwrite=True,
                content_settings=ContentSettings(content_type='image/png') # ContentSettings is sync
            )
            log_adapter.info(f"Successfully uploaded image to {blob_client.url}")
            return blob_client.url
        except Exception as e:
            log_adapter.error(f"Failed to upload image {image_name} (async): {e}", exc_info=True)
            return None # Return None on failure

async def upload_report_to_blob(job_id: str, report_data: ReportData):
    """Uploads the final report data as JSON to blob storage (async), preserving status metadata."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Uploading final report JSON (async)")
    async with get_async_blob_service_client() as blob_service_client:
        try:
            blob_client = blob_service_client.get_blob_client(container=REPORTS_CONTAINER_NAME, blob=f"{job_id}.json")

            metadata = {"jobStatus": report_data.status.value, "jobProgress": "100"}
            try:
                # Await async property retrieval
                properties = await blob_client.get_blob_properties()
                existing_metadata = properties.metadata or {}
                existing_metadata.update(metadata)
                metadata = existing_metadata
            except ResourceNotFoundError:
                log_adapter.warning(f"Report blob didn't exist when trying to finalize (async). Creating.")
            except Exception as meta_e:
                # Catching specific exceptions is better (e.g., AzureError)
                log_adapter.warning(f"Could not get existing metadata before final upload (async): {meta_e}")

            report_json = report_data.model_dump_json(exclude_none=True)
            # Encode to bytes for async upload
            await blob_client.upload_blob(report_json.encode('utf-8'), overwrite=True, metadata=metadata)
            log_adapter.info(f"Successfully uploaded final report (async).")

        except Exception as e:
            log_adapter.error(f"Failed to upload final report (async): {e}", exc_info=True)
            raise

# --- OpenAI Recommendations (Consider Async) --- #
async def generate_openai_recommendations(job_id: str, ndvi_data: np.ndarray, stress_mask: np.ndarray, crop_type: str) -> str:
    """Generates textual recommendations based on NDVI analysis using OpenAI (async preferred)."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Generating OpenAI recommendations for crop: {crop_type} (async attempt)")

    # Check if any client is available
    if not openai_client and not openai_async_client:
        log_adapter.warning("OpenAI client not available. Skipping recommendations.")
        return "AI recommendations disabled: OpenAI API key not configured or client init failed."

    try:
        # Data processing remains sync (numpy is sync)
        valid_ndvi = ndvi_data[~np.isnan(ndvi_data)]
        if valid_ndvi.size == 0:
            log_adapter.warning("No valid NDVI data for OpenAI recommendations.")
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

        recommendation = ""
        if OPENAI_ASYNC_CLIENT_ENABLED and openai_async_client:
            log_adapter.info(f"Sending prompt to Async OpenAI model {OPENAI_MODEL}...")
            response = await openai_async_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=150
            )
            recommendation = response.choices[0].message.content.strip()
            log_adapter.info("Received recommendations from Async OpenAI.")
        elif openai_client:
            log_adapter.info(f"Sending prompt to Sync OpenAI model {OPENAI_MODEL} via thread...")
            # Run sync client in a separate thread to avoid blocking event loop
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=150
            )
            recommendation = response.choices[0].message.content.strip()
            log_adapter.info("Received recommendations from Sync OpenAI via thread.")
        else:
            # Should not happen based on initial check, but as a fallback
            log_adapter.error("No OpenAI client available for recommendation.")
            return "Failed to generate AI recommendations: No client configured."

        return recommendation

    except Exception as e:
        log_adapter.error(f"OpenAI API call failed: {e}", exc_info=True)
        return "Failed to generate AI recommendations due to an error."

# --- Planetary Computer Data Fetching (Wrap sync IO in thread) --- #
# These functions perform CPU-bound and network I/O using sync libraries (requests/rasterio)
# Wrap the blocking calls in asyncio.to_thread

# Helper function for sync rasterio operations
def _sync_read_band(url: str) -> np.ndarray:
    with rasterio.open(url) as src:
        return src.read(1).astype("float32")

async def read_band(job_id: str, url: str) -> np.ndarray:
    """Reads a single band from a signed URL using rasterio in a thread."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.debug(f"Opening rasterio URL via thread: {url[:100]}...")
    try:
        # Run the sync function in a separate thread
        data = await asyncio.to_thread(_sync_read_band, url)
        log_adapter.debug(f"Finished reading band via thread. Shape: {data.shape}")
        return data
    except Exception as e:
        log_adapter.error(f"Failed to read band from URL via thread (starts {url[:50]}...): {e}")
        raise

# Helper function for sync rasterio RGB operations
def _sync_read_rgb(url: str) -> np.ndarray:
    with rasterio.open(url) as src:
        img = src.read([1, 2, 3])
        img = np.transpose(img, (1, 2, 0))
        return img.astype("float32")

async def read_rgb(job_id: str, url: str) -> np.ndarray:
    """Reads RGB bands from a signed URL using rasterio in a thread."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.debug(f"Opening rasterio URL for RGB via thread: {url[:100]}...")
    try:
        # Run the sync function in a separate thread
        img = await asyncio.to_thread(_sync_read_rgb, url)
        log_adapter.debug(f"Finished reading RGB via thread. Shape: {img.shape}")
        return img.astype("float32")
    except Exception as e:
        log_adapter.error(f"Failed to read RGB from URL via thread (starts {url[:50]}...): {e}")
        raise

# NEW Function for fetching Sentinel-2 URLs
async def get_sentinel2_urls(job_id: str, geometry: dict, date_range: str, bands: List[str] = ["nir", "red", "visual"]) -> dict:
    """Searches Planetary Computer for Sentinel-2 L2A data and returns signed URLs for specified bands.

    Args:
        job_id: The job ID for logging.
        geometry: GeoJSON geometry dictionary for the area of interest.
        date_range: Time interval string (e.g., '2023-05-01/2023-05-10').
        bands: List of band common names to retrieve URLs for (e.g., ["nir", "red", "visual"]).

    Returns:
        A dictionary mapping band common names to their signed URLs.
        Returns empty dict or raises an error if data cannot be found or accessed.
    """
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.info(f"Searching Planetary Computer for Sentinel-2 L2A data. Date: {date_range}, Bands: {bands}")

    try:
        # Convert GeoJSON dict to Shapely object
        area_shape = shape(geometry)
        area_bounds = area_shape.bounds

        # Planetary Computer STAC API endpoint
        stac_api_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
        # Use asyncio.to_thread for sync pystac_client operations
        catalog = await asyncio.to_thread(Client.open, stac_api_url)

        log_adapter.debug(f"Searching collection 'sentinel-2-l2a' with bounds: {area_bounds}, datetime: {date_range}")

        # Define search parameters
        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=area_bounds,
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": 25}}, # Example: filter by cloud cover < 25%
            limit=10 # Limit number of items initially to find the best one
        )

        # Get items using sync method within asyncio.to_thread
        items_dict = await asyncio.to_thread(search.get_all_items_as_dict)
        items = pystac.ItemCollection.from_dict(items_dict)

        if not items:
            log_adapter.warning("No Sentinel-2 L2A items found for the specified criteria.")
            raise FileNotFoundError("No suitable Sentinel-2 L2A data found.")

        log_adapter.info(f"Found {len(items)} items. Filtering by intersection and selecting the least cloudy.")

        # Filter items that actually intersect the requested geometry
        intersecting_items = []
        target_shape = shape(geometry)
        for item in items:
            item_geom = shape(item.geometry)
            try:
                # Check for valid intersection (non-empty and valid geometry result)
                if item_geom.is_valid and target_shape.is_valid and item_geom.intersects(target_shape):
                    # Optional: Check intersection area or type if needed
                    intersecting_items.append(item)
            except GEOSException as intersect_err:
                log_adapter.warning(f"GEOS error checking intersection for item {item.id}: {intersect_err}. Skipping item.")
                continue # Skip item if intersection check fails

        if not intersecting_items:
            log_adapter.warning("No items found that intersect the specified geometry.")
            raise FileNotFoundError("No suitable Sentinel-2 L2A data found intersecting the geometry.")

        # Select the least cloudy item *from the intersecting items*
        least_cloudy_item = min(intersecting_items, key=lambda item: item.properties.get('eo:cloud_cover', 101))

        log_adapter.info(f"Selected intersecting item: {least_cloudy_item.id} with cloud cover {least_cloudy_item.properties.get('eo:cloud_cover')}%")

        # Sign the selected item's assets using the planetary_computer library
        # This operation might also be blocking, run in thread
        signed_item = await asyncio.to_thread(planetary_computer.sign, least_cloudy_item)

        band_urls = {}
        missing_bands = []
        for band_common_name in bands:
            asset = signed_item.assets.get(band_common_name)
            if asset:
                band_urls[band_common_name] = asset.href
                log_adapter.debug(f"Found URL for band '{band_common_name}': {asset.href[:100]}...")
            else:
                log_adapter.warning(f"Band '{band_common_name}' not found in assets for item {signed_item.id}")
                missing_bands.append(band_common_name)

        if missing_bands:
            # Decide how to handle missing bands: raise error, return partial results, etc.
            raise ValueError(f"Could not find URLs for required bands: {', '.join(missing_bands)}")

        log_adapter.info("Successfully retrieved signed URLs for all requested bands.")
        return band_urls

    except FileNotFoundError as fnf_err:
        log_adapter.error(f"Data not found: {fnf_err}")
        raise # Re-raise specific error
    except Exception as e:
        log_adapter.error(f"Error searching or signing Planetary Computer data: {e}", exc_info=True)
        # Raise a more generic error or specific internal error
        raise RuntimeError(f"Failed to retrieve data from Planetary Computer: {e}")

# --- Image Processing (Sync, CPU-bound) --- #
# This is CPU-bound (numpy), doesn't need async directly.
# If it becomes a bottleneck, consider multiprocessing or running in a separate worker.
def normalize_image(job_id: str, img: np.ndarray, lower: float = 2, upper: float = 98) -> np.ndarray:
    """Normalizes image data to 0-1 range based on percentiles."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'job_id': job_id})
    log_adapter.debug(f"Normalizing image...")
    valid_pixels = img[~np.isnan(img)]
    if valid_pixels.size == 0:
        log_adapter.warning(f"Attempting to normalize an image with no valid pixels.")
        return np.zeros_like(img)
    vmin, vmax = np.percentile(valid_pixels, lower, method='linear'), np.percentile(valid_pixels, upper, method='linear')
    log_adapter.debug(f"Normalization percentiles: vmin={vmin}, vmax={vmax}")
    if vmax - vmin < 1e-6:
        log_adapter.warning(f"Image normalization range too small (vmin={vmin}, vmax={vmax}). Clamping.")
        return np.ones_like(img) if vmin > 0.5 else np.zeros_like(img)

    img_normalized = (img - vmin) / (vmax - vmin)
    result = np.clip(img_normalized, 0, 1)
    log_adapter.debug(f"Finished normalizing image.")
    return result

# --- Environment Variable Check (Sync is fine) --- #
def check_environment_variables(required_vars: List[str]):
    """Checks if required environment variables are set and logs/raises error if not."""
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        error_message = f"Missing required environment variables: {', '.join(missing_vars)}"
        logging.critical(error_message)
        # Raising a ConfigurationError or similar is often better to halt execution
        raise ValueError(error_message)
    logging.info("Required environment variables check passed.") 