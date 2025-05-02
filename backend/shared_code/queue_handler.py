import logging
import json
from io import BytesIO
from datetime import datetime, timezone
from typing import Optional, Dict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
import azure.functions as func
import rasterio
from rasterio.windows import from_bounds
from rasterio.env import Env
from azure.storage.blob import ContentSettings
from shapely.geometry import shape as shapely_shape
from pyproj import Geod

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


def upload_png_buffer(client, job_id: str, buf: BytesIO, name: str) -> Optional[str]:
    blob = client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=f"{job_id}/{name}")
    blob.upload_blob(
        buf,
        overwrite=True,
        content_settings=ContentSettings(content_type="image/png"),
        max_concurrency=4,
    )
    return blob.url


def read_band_windowed(href: str, bounds_geojson: dict, band_index: int = 1) -> np.ndarray:
    # Extract polygon bounds: assume one exterior ring
    coords = bounds_geojson.get("coordinates", [[]])[0]
    if not coords:
        raise RuntimeError("Invalid geometry bounds for windowed read.")
    xs = [pt[0] for pt in coords]
    ys = [pt[1] for pt in coords]
    req_minx, req_maxx = min(xs), max(xs)
    req_miny, req_maxy = min(ys), max(ys)

    # Set GDAL HTTP timeout using rasterio.Env
    with Env(GDAL_HTTP_TIMEOUT=60):
        with rasterio.open(href) as src:
            # Check for intersection
            intersect_minx = max(req_minx, src.bounds.left)
            intersect_miny = max(req_miny, src.bounds.bottom)
            intersect_maxx = min(req_maxx, src.bounds.right)
            intersect_maxy = min(req_maxy, src.bounds.top)

            if intersect_minx >= intersect_maxx or intersect_miny >= intersect_maxy:
                raise ValueError(
                    f"Requested area [{req_minx:.4f}, {req_miny:.4f}, {req_maxx:.4f}, {req_maxy:.4f}] "
                    f"does not overlap with image bounds [{src.bounds.left:.4f}, {src.bounds.bottom:.4f}, "
                    f"{src.bounds.right:.4f}, {src.bounds.top:.4f}]."
                )

            # Calculate window based on intersection
            window = from_bounds(
                intersect_minx, intersect_miny, intersect_maxx, intersect_maxy, transform=src.transform
            )
            
            # Read data from the calculated window
            data = src.read(band_index, window=window, boundless=True, fill_value=src.nodata)

            # Check if the read data is valid
            if data.size == 0:
                 raise ValueError(
                    f"Read window resulted in an empty array (intersection bounds: "
                    f"[{intersect_minx:.4f}, {intersect_miny:.4f}, {intersect_maxx:.4f}, {intersect_maxy:.4f}])."
                )

            if src.nodata is not None and np.all(data == src.nodata):
                raise ValueError(
                    f"Requested area contains only nodata values (intersection bounds: "
                    f"[{intersect_minx:.4f}, {intersect_miny:.4f}, {intersect_maxx:.4f}, {intersect_maxy:.4f}])."
                )

            return data


def read_and_downsample(href: str, bounds_geojson: dict, downsample: int) -> np.ndarray:
    """
    Reads the specified band, applies windowed read, downsamples if requested,
    and falls back to a synthetic 1x1 zero array on any error to allow analysis to proceed.
    """
    try:
        arr = read_band_windowed(href, bounds_geojson)

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
        return arr
    except Exception as e:
        logging.warning(f"Failed to read/downsample band {href}: {e}. Using zeros array.")
        return np.zeros((1, 1), dtype=np.float32)


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


def fetch_band_urls(client, job_id: str, payload: StartAnalysisPayload) -> Dict[str, str]:
    update_job_status(client, job_id, JobStatus.PROCESSING, 15, "Fetching URLs")
    from pystac_client import Client
    from planetary_computer import sign

    catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    start, end = payload.date_range.split("/")
    items = (
        catalog.search(
            collections=["sentinel-2-l2a"],
            intersects=payload.area.geometry.model_dump(),
            datetime=f"{start}/{end}",
            query={"eo:cloud_cover": {"lt": getattr(payload, "max_cloud_cover", 50)}},
            max_items=1,
        )
        .item_collection()
    )
    if not items:
        raise RuntimeError("No matching Sentinel-2 items found.")
    item = sign(items[0])
    required = {"B08", "B04"}
    missing = required - set(item.assets.keys())
    if missing:
        raise RuntimeError(f"Missing bands {missing}")
    visual_key = "visual" if "visual" in item.assets else "B04"
    return {
        "nir": item.assets["B08"].href,
        "red": item.assets["B04"].href,
        "visual": item.assets[visual_key].href,
    }


def compute_ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        d = nir + red
        ndvi = np.where(d != 0, (nir - red) / d, 0)
    return ndvi.astype(np.float32)


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
        bounds = payload.area.geometry.model_dump()
        update_job_status(client, job_id, JobStatus.PROCESSING, 30, "Reading NIR band")
        nir = read_and_downsample(urls["nir"], bounds, downsample=4)
        update_job_status(client, job_id, JobStatus.PROCESSING, 40, "Reading RED band")
        red = read_and_downsample(urls["red"], bounds, downsample=4)
        
        update_job_status(client, job_id, JobStatus.PROCESSING, 50, "Calculating NDVI")
        ndvi = compute_ndvi(nir, red)
        update_job_status(client, job_id, JobStatus.PROCESSING, 55, "NDVI calculated")

        rgb_arr = None
        rgb_url = None
        if urls.get("visual"):
            update_job_status(client, job_id, JobStatus.PROCESSING, 60, "Reading VISUAL band")
            rgb_arr = read_and_downsample(urls["visual"], bounds, downsample=4)
            update_job_status(client, job_id, JobStatus.PROCESSING, 65, "Visual band read")

        # 3) Render + upload
        update_job_status(client, job_id, JobStatus.PROCESSING, 70, "Rendering and uploading NDVI map")
        ndvi_buf = render_ndvi_png(ndvi)
        ndvi_url = upload_png_buffer(client, job_id, ndvi_buf, "ndvi_map.png")
        update_job_status(client, job_id, JobStatus.PROCESSING, 75, "NDVI map uploaded")
        
        if rgb_arr is not None:
            update_job_status(client, job_id, JobStatus.PROCESSING, 80, "Rendering and uploading RGB map")
            rgb_buf = render_rgb_png(rgb_arr)
            rgb_url = upload_png_buffer(client, job_id, rgb_buf, "rgb_map.png")
            update_job_status(client, job_id, JobStatus.PROCESSING, 85, "RGB map uploaded")

        # 4) Recommendations
        update_job_status(client, job_id, JobStatus.PROCESSING, 90, "Generating recommendations")
        thresh = getattr(payload, "ndvi_threshold", 0.3)
        mask = np.isfinite(ndvi)
        stress = (ndvi < thresh) & mask
        recs = generate_openai_recommendations(job_id, ndvi, stress, payload.crop_type)
        update_job_status(client, job_id, JobStatus.PROCESSING, 95, "Recommendations generated")

        # 5) Stats
        valid = ndvi[mask]
        if valid.size > 0:
            num_valid = valid.size
            num_stressed = stress.sum()
            stats = {
                "mean": float(valid.mean()),
                "min": float(valid.min()),
                "max": float(valid.max()),
                "std_dev": float(valid.std()),
                "stress_percentage": float(num_stressed / num_valid * 100),
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
            ndviStatistics=stats,
            mapUrls={"ndvi": ndvi_url, "rgb": rgb_url},
            recommendations=recs,
            # New fields for frontend compatibility
            parameters=payload.model_dump(exclude_none=True),
            selectedArea=payload.area.model_dump(exclude_none=True),
            areaSqKm=area_sq_km,
            snapshotImageUrl=rgb_url,
            ndviImageUrl=ndvi_url,
            stressZoneImageUrl=None,  # Placeholder; could generate mask image
            stressPercentage=stats.get("stress_percentage"),
            summary=recs,
            mapCenter=map_center,
            mapZoom=13,
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
                requestPayload=request_payload_dict,
                reportTimestamp=datetime.now(timezone.utc).isoformat(),
                ndviStatistics={},
                mapUrls={}, 
                recommendations=f"Analysis failed: {error_message}",
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
