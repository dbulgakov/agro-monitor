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
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    # Set GDAL HTTP timeout using rasterio.Env
    with Env(GDAL_HTTP_TIMEOUT=60):
        with rasterio.open(href) as src:
            window = from_bounds(minx, miny, maxx, maxy, transform=src.transform)
            return src.read(band_index, window=window)


def read_and_downsample(href: str, bounds_geojson: dict, downsample: int) -> np.ndarray:
    arr = read_band_windowed(href, bounds_geojson)
    if arr.size == 0:
        raise RuntimeError(f"Empty array returned for {href}")
    if downsample > 1:
        h, w = arr.shape
        # reshape and average blocks
        arr = (
            arr[: h - (h % downsample), : w - (w % downsample)]
            .reshape(h // downsample, downsample, w // downsample, downsample)
            .mean(axis=(1, 3))
        )
    return arr


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
        
        # 6) Save report
        report = ReportData(
            jobId=job_id,
            status=JobStatus.COMPLETED,
            requestPayload=payload.model_dump(exclude_none=True),
            reportTimestamp=datetime.now(timezone.utc).isoformat(),
            ndviStatistics=stats,
            mapUrls={"ndvi": ndvi_url, "rgb": rgb_url}, # Updated to include potential None for rgb_url
            recommendations=recs,
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
