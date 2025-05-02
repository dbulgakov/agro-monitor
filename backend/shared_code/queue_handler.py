import logging
import json
from io import BytesIO
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
import azure.functions as func
import rasterio
from rasterio.env import Env
from rasterio.mask import mask
from rasterio.warp import transform_geom
from azure.storage.blob import ContentSettings
from shapely.geometry import shape as shapely_shape
from pyproj import Geod
import rasterio.warp # Import warp for transform

from shared_code.helpers.blob import get_sync_blob_service_client, IMAGES_CONTAINER_NAME, REPORTS_CONTAINER_NAME
from shared_code.helpers.job_status import update_job_status, get_job_status
from shared_code.helpers.openai_helpers import generate_openai_recommendations
from shared_code.schemas import JobStatus, StartAnalysisPayload, ReportData

# Thread pool for any concurrent file reads
executor = ThreadPoolExecutor(max_workers=6)


def render_ndvi_png(ndvi: np.ndarray, downsample: int = 1) -> BytesIO:
    # Guard against empty arrays
    if ndvi.size == 0:
        raise RuntimeError("Empty NDVI array; cannot render PNG.")
    # Scale NDVI from [-1,1] to [0,255]
    scaled = ((ndvi + 1) / 2 * 255).clip(0, 255).astype(np.uint8)
    image = Image.fromarray(scaled, mode="L")
    if downsample > 1:
        w, h = image.size
        image = image.resize((w // downsample, h // downsample), Image.Resampling.NEAREST)
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def render_rgb_png(rgb: np.ndarray, downsample: int = 1) -> BytesIO:
    if rgb.size == 0:
        raise RuntimeError("Empty RGB array; cannot render PNG.")
    
    # Handle synthetic 1x1 array from fallback
    if rgb.shape == (1, 1):
        logging.warning("Rendering synthetic 1x1 black pixel for RGB map.")
        # Create a 1x1x3 black pixel array
        rgb_uint8 = np.zeros((1, 1, 3), dtype=np.uint8)
    else:
        # Ensure array is 3D (H, W, C) for RGB
        if rgb.ndim == 2: # If grayscale, duplicate channels
            rgb = np.stack([rgb]*3, axis=-1)
        elif rgb.ndim == 3 and rgb.shape[0] == 3: # If channel-first (C, H, W), transpose
             rgb = np.transpose(rgb, (1, 2, 0))
        elif rgb.ndim != 3 or rgb.shape[-1] != 3:
             raise ValueError(f"Unexpected array shape for RGB: {rgb.shape}")
        
        rgb_uint8 = np.clip(rgb, 0, 255).astype(np.uint8)

    image = Image.fromarray(rgb_uint8, mode="RGB")
    if downsample > 1:
        w, h = image.size
        image = image.resize((w // downsample, h // downsample), Image.Resampling.LANCZOS)
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def render_stress_zone_png(stress_mask: np.ndarray, downsample: int = 1) -> BytesIO:
    """Render a boolean stress mask as a black (no stress) and white (stress) PNG."""
    if stress_mask.size == 0:
        # Return empty buffer if input is empty
        logging.warning("Empty stress mask; cannot render PNG.")
        return BytesIO()
        
    # Convert boolean mask to uint8 (True=255, False=0)
    img_array = (stress_mask * 255).astype(np.uint8)
    image = Image.fromarray(img_array, mode="L")
    
    if downsample > 1:
        w, h = image.size
        if w > 0 and h > 0: # Ensure image is not empty before resizing
            image = image.resize((w // downsample, h // downsample), Image.Resampling.NEAREST)
        else:
            logging.warning("Cannot downsample empty stress mask image.")
            # Return empty buffer if resize results in zero dimensions
            return BytesIO()
            
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def upload_png_buffer(client, job_id: str, buf: BytesIO, name: str) -> Optional[str]:
    blob = client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=f"{job_id}/{name}")
    blob.upload_blob(
        buf,
        overwrite=True,
        content_settings=ContentSettings(content_type="image/png"),
        max_concurrency=4,
    )
    return blob.url


def read_band_windowed(href: str, bounds_geojson: dict, band_index: int = 1) -> Tuple[np.ndarray, rasterio.Affine, rasterio.crs.CRS]:
    """Read a raster band intersecting the given AOI.

    1.  Reprojects the AOI (assumed to be EPSG:4326) into the raster CRS.
    2.  Uses ``rasterio.mask.mask`` to pull only the pixels that fall inside the AOI.
    3.  Performs a few sanity-checks to be sure we did not get an empty or all-nodata array.

    Returns:
        Tuple[np.ndarray, rasterio.Affine, rasterio.crs.CRS]: The masked data array, its affine transform, and its CRS.
    """

    # Fast-fail on obviously wrong input
    if not bounds_geojson or "coordinates" not in bounds_geojson:
        raise RuntimeError("Invalid AOI geometry passed to read_band_windowed")

    with Env(GDAL_HTTP_TIMEOUT=60):
        with rasterio.open(href) as src:
            # Re-project AOI from WGS84 into the raster's CRS so that the units match (e.g. UTM metres)
            try:
                geom_proj = transform_geom("EPSG:4326", src.crs, bounds_geojson, precision=6)
            except Exception as e:
                raise RuntimeError(f"Could not reproject AOI to raster CRS: {e}")

            # Quickly verify intersection in projected coordinates before attempting to read
            xs = [pt[0] for pt in geom_proj["coordinates"][0]]
            ys = [pt[1] for pt in geom_proj["coordinates"][0]]
            if (
                max(xs) < src.bounds.left
                or min(xs) > src.bounds.right
                or max(ys) < src.bounds.bottom
                or min(ys) > src.bounds.top
            ):
                raise RuntimeError("AOI completely outside raster bounds — skipping scene")

            # ``mask`` returns an array in shape (bands, rows, cols)
            try:
                out_image, out_transform = mask(src, [geom_proj], crop=True, indexes=band_index, filled=True)
            except ValueError as e:
                # Raised when shapes do not overlap raster
                raise RuntimeError(f"AOI does not intersect raster: {e}")

            data = out_image[0]  # first (and only) band requested

            # Sanity checks
            if data.size == 0:
                raise RuntimeError("Masked read returned an empty array")

            if src.nodata is not None and np.all(data == src.nodata):
                raise RuntimeError("Masked read is entirely nodata")

            return data, out_transform, src.crs


def read_and_downsample(href: str, bounds_geojson: dict, downsample: int) -> Tuple[np.ndarray, Optional[rasterio.Affine], Optional[rasterio.crs.CRS]]:
    """
    Reads the specified band, applies windowed read, downsamples if requested,
    and falls back to a synthetic 1x1 zero array on any error to allow analysis to proceed.

    Returns:
        Tuple[np.ndarray, Optional[rasterio.Affine], Optional[rasterio.crs.CRS]]: 
            The data array, its transform (or None on error), and its CRS (or None on error).
    """
    try:
        arr, transform, crs = read_band_windowed(href, bounds_geojson)

        # Perform downsampling if required
        if downsample > 1 and arr.size > 0:
            h, w = arr.shape
            h_trim = h - (h % downsample)
            w_trim = w - (w % downsample)
            if h_trim > 0 and w_trim > 0:
                arr = (
                    arr[:h_trim, :w_trim]
                    .reshape(h_trim // downsample, downsample, w_trim // downsample, downsample)
                    .mean(axis=(1, 3))
                )
        # Validate non-empty result
        if arr.size == 0:
            raise RuntimeError("No valid data after read and downsampling")
        return arr, transform, crs
    except Exception as e:
        logging.warning(f"Failed to read/downsample band {href}: {e}. Using zeros array.")
        return np.zeros((1, 1), dtype=np.float32), None, None


def validate_message(msg: func.QueueMessage) -> Optional[Dict]:
    try:
        content = msg.get_body().decode("utf-8")
        data = json.loads(content)
        if "jobId" not in data or "payload" not in data:
            raise ValueError()
        return {"job_id": data["jobId"], "payload": data["payload"]}
    except Exception:
        logging.error("Invalid queue message")
        return None


def fetch_band_urls(client, job_id: str, payload: StartAnalysisPayload) -> Dict[str, Optional[str]]:
    update_job_status(client, job_id, JobStatus.PROCESSING, 15, "Fetching URLs")
    from pystac_client import Client
    from planetary_computer import sign

    catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    start, end = payload.date_range.split("/")
    
    # Fetch multiple items (e.g., 5) and sort by cloud cover
    MAX_ITEMS_TO_FETCH = 5
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects=payload.area.geometry.model_dump(),
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": getattr(payload, "max_cloud_cover", 50)}},
        sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}], # Sort by cloud cover ascending
        max_items=MAX_ITEMS_TO_FETCH,
    )
    items = search.item_collection()

    if not items:
        raise RuntimeError(f"No matching Sentinel-2 items found within {MAX_ITEMS_TO_FETCH} items and cloud cover < {getattr(payload, 'max_cloud_cover', 50)}%")

    # Select the best item (first one, since sorted by cloud cover)
    logging.info(f"Found {len(items)} items. Selecting best (lowest cloud cover): {items[0].id}")
    item = sign(items[0])

    # Prefer lower-resolution (20 m) assets to reduce data volume. Fall back to 10 m/full-res.
    candidate_keys = {
        "nir": ["B08_20m", "B08_10m", "B08"],
        "red": ["B04_20m", "B04_10m", "B04"],
    }

    selected = {}
    for k, options in candidate_keys.items():
        for opt in options:
            if opt in item.assets:
                selected[k] = item.assets[opt].href
                break
        else:
            raise RuntimeError(f"Missing required asset for {k}: tried {options}")

    # Visual (RGB) asset is optional – choose best available.
    visual_opts = ["visual", "B04_20m", "B04"]
    visual_href = None
    for opt in visual_opts:
        if opt in item.assets:
            visual_href = item.assets[opt].href
            break

    selected["visual"] = visual_href # Can be None if not found
    return selected


def compute_ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        d = nir + red
        ndvi = np.where(d != 0, (nir - red) / d, 0)
    return ndvi.astype(np.float32)


def get_bounds_from_transform(shape: tuple, transform: rasterio.Affine, src_crs: rasterio.crs.CRS) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Calculate image bounds in EPSG:4326 from shape, transform, and CRS."""
    try:
        height, width = shape
        # Get corners in source CRS
        corners = [(0, 0), (0, width), (height, width), (height, 0)]
        xs, ys = rasterio.transform.xy(transform, [p[0] for p in corners], [p[1] for p in corners])
        
        # Transform corners to WGS84
        lons, lats = rasterio.warp.transform(src_crs, 'EPSG:4326', xs, ys)
        
        # Calculate min/max lat/lon
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        
        # Return ((min_lat, min_lon), (max_lat, max_lon))
        return ((min_lat, min_lon), (max_lat, max_lon))
    except Exception as e:
        logging.warning(f"Could not calculate bounds from transform: {e}")
        return None


def process_analysis(msg: func.QueueMessage):
    info = validate_message(msg)
    if not info:
        return
    job_id = info["job_id"]
    # Obtain client here
    client = get_sync_blob_service_client()
    # Validate payload
    try:
        payload = StartAnalysisPayload.model_validate(info["payload"])
    except Exception:
        update_job_status(client, job_id, JobStatus.FAILED, -1, "Invalid payload")
        return
    # Avoid re-processing
    status_data = get_job_status(client, job_id)
    if status_data and status_data.status in {JobStatus.PROCESSING, JobStatus.COMPLETED}:
        logging.info(f"Skipping already processed job {job_id} with status {status_data.status}")
        return
    # Start analysis
    update_job_status(client, job_id, JobStatus.PROCESSING, 0, "Starting analysis")
    try:
        # 1) Fetch URLs
        update_job_status(client, job_id, JobStatus.PROCESSING, 10, "Fetching band URLs from STAC")
        urls = fetch_band_urls(client, job_id, payload)
        update_job_status(client, job_id, JobStatus.PROCESSING, 20, "Band URLs fetched")
        
        # 2) Read + downsample
        bounds_geojson = payload.area.geometry.model_dump()
        # Use a consistent downsample factor
        DOWNSAMPLE_FACTOR = 4 
        update_job_status(client, job_id, JobStatus.PROCESSING, 30, "Reading NIR band")
        nir, nir_transform, nir_crs = read_and_downsample(urls["nir"], bounds_geojson, downsample=DOWNSAMPLE_FACTOR)
        update_job_status(client, job_id, JobStatus.PROCESSING, 40, "Reading RED band")
        red, _, _ = read_and_downsample(urls["red"], bounds_geojson, downsample=DOWNSAMPLE_FACTOR)
        
        update_job_status(client, job_id, JobStatus.PROCESSING, 50, "Calculating NDVI")
        ndvi = compute_ndvi(nir, red)
        update_job_status(client, job_id, JobStatus.PROCESSING, 55, "NDVI calculated")

        # Calculate image bounds based on the read NIR band (assuming others align)
        image_bounds_wgs84 = None
        if nir.shape != (1, 1) and nir_transform and nir_crs: # Check if read was successful
            image_bounds_wgs84 = get_bounds_from_transform(nir.shape, nir_transform, nir_crs)

        rgb_arr = None
        rgb_url = None
        if urls.get("visual"):
            update_job_status(client, job_id, JobStatus.PROCESSING, 60, "Reading VISUAL band")
            rgb_arr, _, _ = read_and_downsample(urls["visual"], bounds_geojson, downsample=DOWNSAMPLE_FACTOR)
            update_job_status(client, job_id, JobStatus.PROCESSING, 65, "Visual band read")
            # Only generate RGB URL if read was likely successful (not just zeros)
            if rgb_arr.shape != (1, 1): 
                update_job_status(client, job_id, JobStatus.PROCESSING, 80, "Rendering and uploading RGB map")
                rgb_buf = render_rgb_png(rgb_arr, downsample=1) # No extra downsample here if already done
                rgb_url = upload_png_buffer(client, job_id, rgb_buf, "rgb_map.png")
                update_job_status(client, job_id, JobStatus.PROCESSING, 85, "RGB map uploaded")
            else:
                 logging.warning("Skipping RGB map upload due to failed read (zeros array).")

        # 3) Render + upload NDVI map (always attempt)
        update_job_status(client, job_id, JobStatus.PROCESSING, 70, "Rendering and uploading NDVI map")
        ndvi_buf = render_ndvi_png(ndvi, downsample=1) # No extra downsample here
        ndvi_url = upload_png_buffer(client, job_id, ndvi_buf, "ndvi_map.png")
        update_job_status(client, job_id, JobStatus.PROCESSING, 75, "NDVI map uploaded")
        
        # Calculate the definitive stress mask for statistics AND image generation
        thresh = getattr(payload, "ndvi_threshold", 0.3)
        mask = np.isfinite(ndvi)
        stress_mask_bool = (ndvi < thresh) & mask
        
        # Render and upload stress zone map
        stress_zone_url = None
        update_job_status(client, job_id, JobStatus.PROCESSING, 86, "Rendering and uploading Stress Zone map")
        try:
            stress_buf = render_stress_zone_png(stress_mask_bool, downsample=1)
            if stress_buf.getbuffer().nbytes > 0: # Check if buffer is not empty
                 stress_zone_url = upload_png_buffer(client, job_id, stress_buf, "stress_zone_map.png")
                 update_job_status(client, job_id, JobStatus.PROCESSING, 88, "Stress Zone map uploaded")
            else:
                 logging.warning("Skipping stress zone map upload due to empty buffer.")
        except Exception as img_e:
            logging.warning(f"Failed to render/upload stress zone map: {img_e}")
            
        # 4) Recommendations
        update_job_status(client, job_id, JobStatus.PROCESSING, 90, "Generating recommendations")
        if ndvi.size <= 1:
            recs = "Недостатньо даних для аналізу NDVI на заданій ділянці."
        else:
            # Use the already calculated stress_mask_bool
            try:
                recs = generate_openai_recommendations(job_id, ndvi, stress_mask_bool, payload.crop_type)
            except Exception as e:
                logging.warning(f"Failed to generate AI recommendations: {e}")
                recs = "Не вдалося згенерувати рекомендації через помилку."
        
        update_job_status(client, job_id, JobStatus.PROCESSING, 95, "Recommendations generated")

        # 5) Stats
        valid_ndvi = ndvi[mask]
        if valid_ndvi.size > 0:
            num_valid = valid_ndvi.size
            num_stressed = stress_mask_bool.sum() # Sum the boolean mask
            stats = {
                "mean": float(valid_ndvi.mean()),
                "min": float(valid_ndvi.min()),
                "max": float(valid_ndvi.max()),
                "std_dev": float(valid_ndvi.std()),
                "stress_percentage": float(num_stressed / num_valid * 100) if num_valid > 0 else 0.0,
            }
        else:
            stats = {"mean": None, "min": None, "max": None, "std_dev": None, "stress_percentage": 0.0}
        
        # NEW: compute area (km^2) and centroid for map center
        area_sq_km = None
        map_center = None
        try:
            geom_dict = payload.area.geometry.model_dump()
            geom = shapely_shape(geom_dict)
            if geom.is_valid and not geom.is_empty and geom.geom_type == "Polygon":
                geod = Geod(ellps="WGS84")
                # pyproj returns negative area depending on winding order
                area, _ = geod.geometry_area_perimeter(geom)
                area_sq_km = abs(area) / 1_000_000  # m^2 to km^2
                lon, lat = geom.centroid.x, geom.centroid.y
                map_center = [lat, lon]
        except Exception as e:
            logging.warning(f"Could not compute area/centroid: {e}")
        
        # 6) Save report (updated)
        report = ReportData(
            jobId=job_id,
            status=JobStatus.COMPLETED,
            requestPayload=payload.model_dump(exclude_none=True),
            reportTimestamp=datetime.now(timezone.utc).isoformat(),
            ndviStatistics=stats, # Contains stress_percentage
            mapUrls={"ndvi": ndvi_url, "rgb": rgb_url, "stress": stress_zone_url}, # Added stress URL
            recommendations=recs,
            selectedArea=payload.area.model_dump(exclude_none=True),
            areaSqKm=area_sq_km,
            mapCenter=map_center,
            mapZoom=13, # Keep default or calculate dynamically?
            imageBounds=image_bounds_wgs84 # Add calculated bounds
        )
        report_blob = client.get_blob_client(
            container=REPORTS_CONTAINER_NAME, blob=f"{job_id}/report.json"
        )
        report_blob.upload_blob(
            report.model_dump_json(exclude_none=True).encode("utf-8"), overwrite=True
        )
        update_job_status(client, job_id, JobStatus.COMPLETED, 100, "Analysis completed successfully")
    except Exception as e:
        error_message = str(e)
        logging.error(f"Error processing job {job_id}: {error_message}", exc_info=True)
        # Ensure status is updated even on failure
        update_job_status(client, job_id, JobStatus.FAILED, -1, f"Failed: {error_message[:100]}") # Truncate long errors

        # Attempt to save a failure report
        try:
            request_payload_dict = info.get("payload", {})
            failure_report = ReportData(
                jobId=job_id,
                status=JobStatus.FAILED,
                requestPayload=request_payload_dict, # Use original payload if possible
                reportTimestamp=datetime.now(timezone.utc).isoformat(),
                errorMessage=error_message # Store the error message here
                # Other fields default to None
            )
            report_blob = client.get_blob_client(
                container=REPORTS_CONTAINER_NAME, blob=f"{job_id}/report.json"
            )
            # Use model_dump_json for consistency and exclude_none
            report_blob.upload_blob(
                failure_report.model_dump_json(exclude_none=True).encode("utf-8"), 
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json") # Add content type
            )
            logging.info(f"Failure report saved for job {job_id}")
        except Exception as report_e:
            logging.error(f"Could not save failure report for job {job_id}: {report_e}", exc_info=True)

        return
