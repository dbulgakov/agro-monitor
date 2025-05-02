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
MIN_PIX = 1             # Minimum valid pixels required after potential downsampling
MAX_SCENES = 1          # Fetch only the scene with the lowest cloud cover initially
# DEFAULT_DOWNSAMPLE = 1 # Removed, replaced by _auto_factor


def _auto_factor(area_km2: float) -> int:
    """Calculate downsampling factor targeting ~6 million pixels."""
    # Target side length in pixels assuming 10m resolution
    side_m = (area_km2 * 1e6) ** 0.5
    px_10m = side_m / 10
    # Calculate factor needed to get side length close to sqrt(6M) ~ 2450px
    factor = px_10m / 2450
    return max(1, int(round(factor)))

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

# Removed _dynamic_downsample function

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

def _read_band(href, geojson, indexes=None, factor=1, min_pix=MIN_PIX):
    """Reads band(s) from a COG, applying masking, cropping, and downsampling."""
    try:
        # Add COG optimizations and ensure AWS requester pays is enabled
        with Env(GDAL_HTTP_TIMEOUT=180, CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif', AWS_REQUEST_PAYER='requester'):
            with rasterio.open(href) as src:
                logging.info(f"Reading band(s) from {href} for geometry (downsample factor: {factor}).")
                geom = transform_geom("EPSG:4326", src.crs, geojson, precision=6)

                # Determine indexes to read
                read_indexes = [1] if indexes is None else indexes

                # Perform mask and crop ONLY
                out_image, out_transform = mask(
                    src,
                    [geom],
                    crop=True,
                    indexes=read_indexes,
                    filled=True, # Keep True if nodata fill is desired
                    nodata=src.nodata, # Pass nodata for proper handling
                )

                # Check for valid pixels *after* masking/cropping but *before* downsampling
                if src.nodata is not None:
                    if np.issubdtype(out_image.dtype, np.floating):
                        # Use np.isclose for floating point nodata comparison
                        valid = np.isfinite(out_image) & ~np.isclose(out_image, src.nodata)
                    else:
                        valid = out_image != src.nodata
                else:
                    valid = np.isfinite(out_image) # Check only for NaN/inf if no nodata

                # Count valid pixels in the cropped image (across all bands if multiple)
                valid_pixels = valid.sum()
                logging.info(f"Found {valid_pixels} valid pixels in cropped {href}. Required: {min_pix}")

                if valid_pixels < min_pix:
                    logging.warning(f"Too few valid pixels ({valid_pixels}) in cropped {href}. Skipping this band.")
                    return None, None

                # --- Apply downsampling AFTER masking ---
                if factor > 1:
                    logging.debug(f"Applying downsampling factor {factor} to shape {out_image.shape}")
                    # Shape from mask is (bands, height, width)
                    bands, h, w = out_image.shape
                    # Calculate target dimensions, ensuring they are at least 1
                    ht = max(1, h // factor)
                    wt = max(1, w // factor)
                    # Trim edges that don't fit the factor
                    trimmed_h = ht * factor
                    trimmed_w = wt * factor

                    if trimmed_h == 0 or trimmed_w == 0:
                        logging.warning(f"Downsample factor {factor} too large for cropped image size ({h}x{w}), returning original.")
                        data = out_image # Return the original cropped image
                    else:
                        # Reshape and calculate mean for downsampling
                        try:
                            trimmed_data = out_image[:, :trimmed_h, :trimmed_w]
                            reshaped_data = trimmed_data.reshape(bands, ht, factor, wt, factor)
                            # Calculate mean across the factor axes (2 and 4), handling potential NaNs/nodata
                            # Use nanmean if data is float, otherwise regular mean
                            if np.issubdtype(reshaped_data.dtype, np.floating):
                                data_downsampled = np.nanmean(reshaped_data, axis=(2, 4))
                            else:
                                data_downsampled = reshaped_data.mean(axis=(2, 4))

                            logging.debug(f"Downsampled shape: {data_downsampled.shape}")
                            data = data_downsampled
                        except ValueError as ve:
                            logging.warning(f"Error during numpy reshape/mean for downsampling (factor={factor}, shape={out_image.shape}): {ve}. Returning original cropped data.")
                            data = out_image # Fallback to original cropped image
                else:
                     data = out_image # No downsampling needed

                # --- Final data preparation ---
                # Handle single vs multiple band output
                if data.shape[0] == 1:
                    # If single band, remove the first dimension -> (h, w)
                    final_data = data[0]
                else:
                    # If multiple bands, move bands axis to the end -> (h, w, bands)
                    final_data = np.moveaxis(data, 0, -1)

                # Return data as float32 and original CRS
                return final_data.astype(np.float32), src.crs
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
        max_items=MAX_SCENES, # Use the updated constant
    )

    item_collection = search.item_collection()
    scenes = []
    scene_ids = []
    scene_metadata = {} # Store metadata like ID along with URLs
    for item in item_collection:
        itm = sign(item)
        scene_id = item.id
        scene_ids.append(scene_id)
        urls = {"id": scene_id} # Include scene ID
        for key, opts in {"nir": ["B08", "B08_20m"], "red": ["B04", "B04_20m"]}.items():
            for o in opts:
                if o in itm.assets:
                    urls[key] = itm.assets[o].href
                    break
        if "visual" in itm.assets:
            urls["visual"] = itm.assets["visual"].href
        scenes.append(urls)
        # Store additional useful metadata if needed, e.g., cloud cover
        scene_metadata[scene_id] = {
            "cloud_cover": item.properties.get("eo:cloud_cover")
        }

    job_id = payload.area.properties.get('jobId', 'N/A') if payload.area.properties else 'N/A'
    logging.info(f"Found {len(scenes)} scenes for job {job_id} matching criteria. Scene IDs: {scene_ids}")
    # Return both the list of URLs (including IDs) and the metadata
    return scenes, scene_metadata

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

    factor = 1 # Default factor if area calculation fails
    area_km2 = None
    bounds = None
    map_center = None
    map_zoom = None
    try:
        input_geojson = payload.area.geometry.model_dump()
        poly = shape(input_geojson)
        # Calculate area more directly using pyproj.Geod
        geod = Geod(ellps="WGS84")
        area_m2, _ = geod.geometry_area_perimeter(poly)
        area_km2 = abs(area_m2) / 1_000_000
        factor = _auto_factor(area_km2) # Calculate downsampling factor based on area
        metadata["areaSqKm"] = area_km2
        metadata["downsampleFactor"] = factor
        min_lon, min_lat, max_lon, max_lat = poly.bounds
        bounds = ((min_lat, min_lon), (max_lat, max_lon))
        metadata["imageBounds"] = bounds
        center_lon, center_lat = poly.centroid.x, poly.centroid.y
        map_center = [center_lat, center_lon]
        metadata["mapCenter"] = map_center
        # Simplified map zoom calculation
        if area_km2 > 1000: map_zoom = 8
        elif area_km2 > 100: map_zoom = 10
        elif area_km2 > 10: map_zoom = 12
        elif area_km2 > 1: map_zoom = 14
        else: map_zoom = 16
        metadata["mapZoom"] = map_zoom
        logging.info(f"Job {jid}: Calculated Area={area_km2:.2f} km², Factor={factor}, Center={map_center}, Bounds={bounds}, Zoom={map_zoom}")
    except Exception as e:
        logging.error(f"Job {jid}: Failed to calculate geographic metadata: {e}", exc_info=True)
        # Keep default factor=1 if calculation fails
        metadata["downsampleFactor"] = factor # Log the default factor being used

    # Fetch scenes (MAX_SCENES=1) and their metadata
    scenes, scene_meta = _fetch_scenes(payload)
    metadata["scenesFetched"] = len(scenes)
    if not scenes:
        logging.error(f"Job {jid}: No scenes found matching the criteria.")
        update_job_status(client, jid, JobStatus.FAILED, 10, "Сцени не знайдено", raw=json.dumps(metadata))
        return

    # Since MAX_SCENES = 1, we take the first (and only) scene
    chosen_scene_urls = scenes[0]
    scene_id = chosen_scene_urls.get("id", "N/A") # Get scene ID from fetched data
    metadata["sceneId"] = scene_id
    metadata["sceneCloudCover"] = scene_meta.get(scene_id, {}).get("cloud_cover", "N/A")

    # Update status before reading bands
    update_job_status(client, jid, JobStatus.PROCESSING, 10, f"Обробка сцени {scene_id}", raw=json.dumps(metadata))

    geom_buf = _buffer_geom(payload.area.geometry.model_dump(), meters=30) # Use buffered geometry for reads

    # Update status for reading bands
    update_job_status(client, jid, JobStatus.PROCESSING, 20, "Читання спектрів", raw=json.dumps(metadata))

    # Read NIR and Red bands using the calculated factor and min_pix setting
    nir_url = chosen_scene_urls.get("nir")
    red_url = chosen_scene_urls.get("red")

    if not nir_url or not red_url:
        logging.error(f"Job {jid}: Missing NIR ('{nir_url}') or Red ('{red_url}') band URL in scene {scene_id}.")
        update_job_status(client, jid, JobStatus.FAILED, 25, "Відсутні URL спектрів", raw=json.dumps(metadata))
        return

    nir, nir_crs = _read_band(nir_url, geom_buf, factor=factor, min_pix=MIN_PIX)
    red, red_crs = _read_band(red_url, geom_buf, factor=factor, min_pix=MIN_PIX)

    # Check if *both* essential bands were read successfully
    if nir is None or red is None:
        missing_bands = []
        if nir is None: missing_bands.append("NIR")
        if red is None: missing_bands.append("Red")
        logging.error(f"Job {jid}: Failed to read essential band(s): {', '.join(missing_bands)} from scene {scene_id}. Aborting.")
        # Use a distinct status code/message for band read failure
        update_job_status(client, jid, JobStatus.FAILED, 30, f"Помилка читання спектрів ({', '.join(missing_bands)})", raw=json.dumps(metadata))
        return

    # --- Removed scene selection loop ---
    # Logic now proceeds directly with the fetched scene if bands are readable

    # Update status before NDVI calculation
    update_job_status(client, jid, JobStatus.PROCESSING, 40, "Обчислення NDVI", raw=json.dumps(metadata))
    # Data is already downsampled in _read_band if factor > 1
    ndvi = _ndvi(nir, red)
    thresh = payload.ndvi_threshold
    metadata["ndviThreshold"] = thresh
    stats = _stats(ndvi, thresh)
    metadata["ndviStats"] = stats

    # Check if stats calculation resulted in non-null values, otherwise fail
    if stats.get('mean') is None:
        logging.error(f"Job {jid}: NDVI statistics calculation failed (all NaN?). Failing job.")
        # Use a distinct status code/message for stats failure
        update_job_status(client, jid, JobStatus.FAILED, 50, "Помилка розрахунку статистики NDVI", raw=json.dumps(metadata))
        return

    # Update status before NDVI image upload
    update_job_status(client, jid, JobStatus.PROCESSING, 60, "Завантаження зображення NDVI", raw=json.dumps(metadata))
    nd_url = _upload(client, jid, _to_png(ndvi, True), "ndvi.png")

    vis_url = ""
    visual_url = chosen_scene_urls.get("visual")
    if visual_url:
        # Update status before reading visual band
        update_job_status(client, jid, JobStatus.PROCESSING, 70, "Читання RGB", raw=json.dumps(metadata))
        # Read the visual band for the chosen scene with the same factor and min_pix
        rgb, rgb_crs = _read_band(visual_url, geom_buf, indexes=[1,2,3], factor=factor, min_pix=MIN_PIX)
        if rgb is not None:
            # No _dynamic_downsample needed here
            # Update status before RGB image upload
            update_job_status(client, jid, JobStatus.PROCESSING, 75, "Завантаження зображення RGB", raw=json.dumps(metadata))
            vis_url = _upload(client, jid, _to_png(rgb, False), "rgb.png")
        else:
             logging.warning(f"Job {jid}: Failed to read visual band ({visual_url}) for the chosen scene {scene_id}.")
             # Update status slightly to indicate visual part skipped/failed
             update_job_status(client, jid, JobStatus.PROCESSING, 78, "Пропуск RGB (помилка читання)", raw=json.dumps(metadata))
    else:
        logging.info(f"Job {jid}: No visual band URL found for scene {scene_id}. Skipping RGB image.")
        # Update status slightly to indicate visual part skipped
        update_job_status(client, jid, JobStatus.PROCESSING, 78, "Пропуск RGB (немає URL)", raw=json.dumps(metadata))


    # Update status before stress layer processing
    update_job_status(client, jid, JobStatus.PROCESSING, 80, "Обробка зон стресу", raw=json.dumps(metadata))
    # Calculate stress layer from the final (potentially downsampled) NDVI
    stress_layer = (ndvi < thresh) # Boolean array
    st_url = _upload(client, jid, _to_png(stress_layer, True), "stress.png") # Pass boolean directly to _to_png

    # Update status before recommendations
    update_job_status(client, jid, JobStatus.PROCESSING, 90, "Підготовка рекомендацій", raw=json.dumps(metadata))
    recs = "Рекомендації не вдалося згенерувати." # Default message
    try:
        # Pass the calculated boolean stress layer to recommendations
        recs = generate_openai_recommendations(jid, ndvi, stress_layer, payload.crop_type)
        metadata["recommendationGenerated"] = True
        metadata["recommendationLength"] = len(recs) if isinstance(recs, str) else 0
    except Exception as e:
        recs = "Не вдалося згенерувати рекомендації через помилку."
        metadata["recommendationGenerated"] = False
        metadata["recommendationError"] = str(e)
        logging.warning(f"Recommendations failed for job {jid}: {e}", exc_info=True) # Log with traceback

    # Final report creation
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
        selectedArea=payload.area.model_dump(),
        # Include key processing metadata in the report
        processingMetadata={
            "sceneId": scene_id,
            "sceneCloudCover": metadata.get("sceneCloudCover"),
            "downsampleFactor": factor,
            "ndviThreshold": thresh,
            "recommendationGenerated": metadata.get("recommendationGenerated", False)
        }
    )

    # Upload the final report
    try:
        report_blob = client.get_blob_client(REPORTS_CONTAINER_NAME, f"{jid}/report.json")
        report_blob.upload_blob(
            rpt.model_dump_json(indent=2, exclude_none=True).encode('utf-8'), # Use indent for readability
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json") # Set content type
        )
        logging.info(f"Job {jid}: Successfully uploaded report.json")
    except Exception as e:
        logging.error(f"Job {jid}: Failed to upload report.json: {e}", exc_info=True)
        # Update status to FAILED if report upload fails, as this is critical
        update_job_status(client, jid, JobStatus.FAILED, 95, "Помилка збереження звіту", raw=json.dumps(metadata))
        return # Stop processing if report can't be saved

    # Final status update to COMPLETED
    metadata["completedAt"] = datetime.now(timezone.utc).isoformat()
    update_job_status(client, jid, JobStatus.COMPLETED, 100, "Завершено", raw=json.dumps(metadata))
    logging.info(f"Job {jid}: Processing completed successfully.")
