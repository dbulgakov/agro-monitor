import logging, json
from io import BytesIO
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
import azure.functions as func
import rasterio
from rasterio.env import Env
from rasterio.mask import mask
from rasterio.warp import transform_geom
from shapely.geometry import shape, mapping
from pyproj import Geod
from azure.storage.blob import ContentSettings
from shared_code.helpers.blob import (
    get_sync_blob_service_client,
    IMAGES_CONTAINER_NAME,
    REPORTS_CONTAINER_NAME,
)
from shared_code.helpers.job_status import update_job_status
from shared_code.helpers.openai_helpers import generate_openai_recommendations
from shared_code.schemas import JobStatus, StartAnalysisPayload, ReportData

executor = ThreadPoolExecutor(max_workers=3)

BLANK_SHAPE = (512, 512)
MIN_PIX = 10
MAX_SCENES = 5
DEFAULT_DOWNSAMPLE = 1

def _blank_gray():
    return np.zeros(BLANK_SHAPE, dtype=np.float32)

def _to_png(arr, gray):
    try:
        if gray:
            if arr.dtype == bool:              # stress‑layer
                scaled = arr.astype(np.uint8) * 255
            else:                              # NDVI
                scaled = ((arr + 1) / 2 * 255).clip(0, 255).astype(np.uint8)
            img = Image.fromarray(scaled, "L")
        else:
            if arr.ndim == 2:
                arr = np.stack([arr] * 3, -1)
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
        buf = BytesIO()
        img.save(buf, "PNG", optimize=True)
        buf.seek(0)
        return buf
    except Exception as e:
        logging.warning(f"PNG encode failed: {e}")
        buf = BytesIO()
        Image.new("L" if gray else "RGB", BLANK_SHAPE).save(buf, "PNG")
        buf.seek(0)
        return buf

def _upload(client, jid, buf, name):
    try:
        blob = client.get_blob_client(IMAGES_CONTAINER_NAME, f"{jid}/{name}")
        blob.upload_blob(
            buf, overwrite=True, content_settings=ContentSettings(content_type="image/png")
        )
        return blob.url
    except Exception as e:
        logging.warning(f"Upload failed {name}: {e}")
        return ""

def _buffer_geom(geojson, meters=30):
    deg = meters / 111_320
    return mapping(shape(geojson).buffer(deg))

def _dynamic_downsample(arr):
    h, w = arr.shape[:2]
    factor = DEFAULT_DOWNSAMPLE if min(h, w) >= 2 * DEFAULT_DOWNSAMPLE else 1
    if factor == 1 or arr.size <= 1:
        return arr
    ht, wt = h - h % factor, w - w % factor
    if ht == 0 or wt == 0:
        return arr
    return arr[:ht, :wt].reshape(ht // factor, factor, wt // factor, factor).mean((1, 3))

def _ndvi(nir, red):
    with np.errstate(divide="ignore", invalid="ignore"):
        d = nir + red
        return np.where(d != 0, (nir - red) / d, 0).astype(np.float32)

def _stats(ndvi, thresh):
    m = np.isfinite(ndvi)
    if not m.any():
        return dict(mean=None, min=None, max=None, std_dev=None, stress_percentage=None)
    v = ndvi[m]
    return dict(
        mean=float(v.mean()),
        min=float(v.min()),
        max=float(v.max()),
        std_dev=float(v.std()),
        stress_percentage=float(((ndvi < thresh) & m).sum() / v.size * 100),
    )

def _read_band(href, geojson, indexes=None):
    try:
        with Env(GDAL_HTTP_TIMEOUT=180):
            with rasterio.open(href) as src:
                logging.info(f"Reading band from {href} for geometry.")
                geom = transform_geom("EPSG:4326", src.crs, geojson, precision=6)
                out, _ = mask(src, [geom], crop=True, indexes=1, filled=True)
                if indexes is None:
                    data = np.asarray(out[0])
                else:
                    out, _ = mask(src, [geom], crop=True, indexes=indexes, filled=True)
                    data = np.stack(out, axis=-1)
                if src.nodata is not None:
                    valid = np.isfinite(data) & (data != src.nodata)
                else:
                    valid = np.isfinite(data)
                valid_pixels = valid.sum()
                logging.info(f"Found {valid_pixels} valid pixels in {href}.")
                if valid_pixels < MIN_PIX:
                    logging.warning(f"Too few valid pixels ({valid_pixels}, minimum required: {MIN_PIX}) in {href}. Skipping this band.")
                    return None, None
                return data.astype(np.float32), src.crs
    except Exception as e:
        logging.warning(f"Band read failed {href}: {e}", exc_info=True)
        return None, None

def _fetch_scenes(payload):
    from pystac_client import Client
    from planetary_computer import sign

    catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    s, e = payload.date_range.split("/")
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects=payload.area.geometry.model_dump(),
        datetime=f"{s}/{e}",
        query={"eo:cloud_cover": {"lt": payload.max_cloud_cover}},
        sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        max_items=MAX_SCENES,
    )

    item_collection = search.item_collection()
    scenes = []
    scene_ids = []
    for item in item_collection:
        itm = sign(item)
        scene_ids.append(item.id)
        urls = {}
        for key, opts in {"nir": ["B08", "B08_20m"], "red": ["B04", "B04_20m"]}.items():
            for o in opts:
                if o in itm.assets:
                    urls[key] = itm.assets[o].href
                    break
        if "visual" in itm.assets:
            urls["visual"] = itm.assets["visual"].href
        scenes.append(urls)

    logging.info(f"Found {len(scenes)} scenes for job {payload.area.properties.get('jobId', 'N/A') if payload.area.properties else 'N/A'} matching criteria. Scene IDs: {scene_ids}")
    return scenes

def process_analysis(msg: func.QueueMessage):
    try:
        d = json.loads(msg.get_body())
        jid = d["jobId"]
        client = get_sync_blob_service_client()
        payload = StartAnalysisPayload.model_validate(d["payload"])
    except Exception as e:
        logging.error(f"Failed to parse message: {e}")
        return

    if payload.area.properties is None:
        payload.area.properties = {}
    payload.area.properties['jobId'] = jid

    metadata = {"jobId": jid, "dateRange": payload.date_range}
    update_job_status(client, jid, JobStatus.PROCESSING, 0, "Ініціалізація", raw=json.dumps(metadata))

    try:
        input_geojson = payload.area.geometry.model_dump()
        poly = shape(input_geojson)
        geod = Geod(ellps="WGS84")
        area_m2, _ = geod.geometry_area_perimeter(poly)
        area_km2 = abs(area_m2) / 1_000_000
        metadata["areaSqKm"] = area_km2
        min_lon, min_lat, max_lon, max_lat = poly.bounds
        bounds = ((min_lat, min_lon), (max_lat, max_lon))
        metadata["imageBounds"] = bounds
        center_lon, center_lat = poly.centroid.x, poly.centroid.y
        map_center = [center_lat, center_lon]
        metadata["mapCenter"] = map_center
        if area_km2 > 1000:
            map_zoom = 8
        elif area_km2 > 100:
            map_zoom = 10
        elif area_km2 > 10:
            map_zoom = 12
        elif area_km2 > 1:
            map_zoom = 14
        else:
            map_zoom = 16
        metadata["mapZoom"] = map_zoom
        logging.info(f"Job {jid}: Calculated Area={area_km2:.2f} km², Center={map_center}, Bounds={bounds}, Zoom={map_zoom}")
    except Exception as e:
        logging.error(f"Job {jid}: Failed to calculate geographic metadata: {e}", exc_info=True)
        area_km2 = None
        bounds = None
        map_center = None
        map_zoom = None

    scenes = _fetch_scenes(payload)
    metadata["scenesFetched"] = len(scenes)
    if not scenes:
        logging.error(f"Job {jid}: No scenes found matching the criteria.")
        update_job_status(client, jid, JobStatus.FAILED, 10, "Сцени не знайдено", raw=json.dumps(metadata))
        return
    update_job_status(client, jid, JobStatus.PROCESSING, 10, "Завантаження сцен", raw=json.dumps(metadata))

    geom = _buffer_geom(payload.area.geometry.model_dump(), meters=30)

    chosen = None
    best_ndvi_std = -1 # Track the standard deviation of the best scene found so far
    best_scene_data = None # Store nir, red, and urls for the best scene

    for idx, urls in enumerate(scenes, 1):
        metadata["sceneIndex"] = idx
        update_job_status(client, jid, JobStatus.PROCESSING, 20 + int(idx / len(scenes) * 15), f"Читання спектрів (сцена {idx}/{len(scenes)})", raw=json.dumps(metadata)) # More granular progress
        
        nir_band, _ = _read_band(urls.get("nir", ""), geom)
        red_band, _ = _read_band(urls.get("red", ""), geom)

        # Check if both bands were read successfully
        if nir_band is not None and red_band is not None:
            logging.info(f"Job {jid}, Scene {idx}: Successfully read NIR and Red bands.")
            # Perform initial downsampling before calculating NDVI for this scene
            nir_ds = _dynamic_downsample(nir_band)
            red_ds = _dynamic_downsample(red_band)
            
            # Calculate NDVI for this specific scene to check its quality
            current_ndvi = _ndvi(nir_ds, red_ds)
            current_ndvi_std = np.nanstd(current_ndvi)
            
            logging.info(f"Job {jid}, Scene {idx}: Calculated NDVI std dev: {current_ndvi_std:.4f}")

            # Check if this NDVI has non-zero standard deviation (indicates variation)
            if current_ndvi_std > 0:
                # If this is the first valid scene or better than the current best, select it
                if best_scene_data is None or current_ndvi_std > best_ndvi_std:
                    logging.info(f"Job {jid}, Scene {idx}: Selecting as best scene (std dev: {current_ndvi_std:.4f}).")
                    best_ndvi_std = current_ndvi_std
                    # Store the *original* (non-downsampled) bands and URLs
                    best_scene_data = {
                        "nir": nir_band,
                        "red": red_band,
                        "urls": urls
                    }
            else:
                logging.warning(f"Job {jid}, Scene {idx}: NDVI std dev is zero, likely uniform data (e.g., all cloud/water). Skipping.")
        else:
             logging.warning(f"Job {jid}, Scene {idx}: Failed to read NIR or Red band. Skipping.")

    # After checking all scenes, proceed if a best scene was found
    if best_scene_data:
        chosen = best_scene_data["urls"]
        nir = best_scene_data["nir"] # Use the stored original band
        red = best_scene_data["red"] # Use the stored original band
        logging.info(f"Job {jid}: Proceeding with chosen scene (best NDVI std dev: {best_ndvi_std:.4f}).")
    else:
        logging.error(f"No usable scenes found for job {jid} after checking {len(scenes)} candidates.")
        update_job_status(client, jid, JobStatus.FAILED, 35, "Не знайдено придатних сцен", raw=json.dumps(metadata))
        return

    update_job_status(client, jid, JobStatus.PROCESSING, 40, "Обчислення NDVI", raw=json.dumps(metadata))
    # Final downsampling and NDVI calculation using the chosen best scene's bands
    nir = _dynamic_downsample(nir)
    red = _dynamic_downsample(red)
    ndvi = _ndvi(nir, red)
    thresh = payload.ndvi_threshold
    metadata["ndviThreshold"] = thresh
    stats = _stats(ndvi, thresh)
    metadata["ndviStats"] = stats

    # Check if stats calculation resulted in non-null values, otherwise fail
    if stats.get('mean') is None:
        logging.error(f"Job {jid}: NDVI statistics calculation failed (all NaN?). Failing job.")
        update_job_status(client, jid, JobStatus.FAILED, 50, "Помилка розрахунку статистики NDVI", raw=json.dumps(metadata))
        return

    update_job_status(client, jid, JobStatus.PROCESSING, 60, "Завантаження зображення NDVI", raw=json.dumps(metadata))
    nd_url = _upload(client, jid, _to_png(ndvi, True), "ndvi.png")

    vis_url = ""
    if "visual" in chosen:
        # Read the visual band only for the *chosen* scene
        rgb, _ = _read_band(chosen["visual"], geom, indexes=[1,2,3])
        if rgb is not None:
            rgb = _dynamic_downsample(rgb)
            update_job_status(client, jid, JobStatus.PROCESSING, 70, "Завантаження зображення RGB", raw=json.dumps(metadata))
            vis_url = _upload(client, jid, _to_png(rgb, False), "rgb.png")
        else:
             logging.warning(f"Job {jid}: Failed to read visual band for the chosen scene.")

    update_job_status(client, jid, JobStatus.PROCESSING, 80, "Завантаження зображення зон стресу", raw=json.dumps(metadata))
    st_url = _upload(client, jid, _to_png((ndvi < thresh).astype(np.float32), True), "stress.png")

    update_job_status(client, jid, JobStatus.PROCESSING, 90, "Підготовка рекомендацій", raw=json.dumps(metadata))
    try:
        recs = generate_openai_recommendations(jid, ndvi, ndvi < thresh, payload.crop_type)
        metadata["recommendationLength"] = len(recs)
    except Exception as e:
        recs = "Не вдалося згенерувати рекомендації через помилку."
        metadata["recommendationError"] = str(e)
        logging.warning(f"Recommendations failed: {e}")

    rpt = ReportData(
        jobId=jid,
        status=JobStatus.COMPLETED,
        requestPayload=payload.model_dump(exclude_none=True),
        reportTimestamp=datetime.now(timezone.utc).isoformat(),
        ndviStatistics=stats,
        mapUrls={"ndvi": nd_url, "rgb": vis_url, "stress": st_url},
        recommendations=recs,
        areaSqKm=area_km2,
        mapCenter=map_center,
        mapZoom=map_zoom,
        imageBounds=bounds,
        selectedArea=payload.area.model_dump()
    )

    client.get_blob_client(REPORTS_CONTAINER_NAME, f"{jid}/report.json").upload_blob(
        rpt.model_dump_json(exclude_none=True).encode(),
        overwrite=True,
    )

    metadata["completedAt"] = datetime.now(timezone.utc).isoformat()
    update_job_status(client, jid, JobStatus.COMPLETED, 100, "Завершено", raw=json.dumps(metadata))
