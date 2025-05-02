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

BLANK_SHAPE = (128, 128)
MIN_PIX = 10
MAX_SCENES = 5
DEFAULT_DOWNSAMPLE = 4

def _blank_gray():
    return np.zeros(BLANK_SHAPE, dtype=np.float32)

def _to_png(arr, gray):
    try:
        if gray:
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

def _read_band(href, geojson):
    try:
        with Env(GDAL_HTTP_TIMEOUT=60):
            with rasterio.open(href) as src:
                geom = transform_geom("EPSG:4326", src.crs, geojson, precision=6)
                out, _ = mask(src, [geom], crop=True, indexes=1, filled=True)
                data = out[0]
                if src.nodata is not None:
                    valid = np.isfinite(data) & (data != src.nodata)
                else:
                    valid = np.isfinite(data)
                if valid.sum() < MIN_PIX:
                    raise RuntimeError(f"Too few valid pixels: {valid.sum()}")
                return data, src.crs
    except Exception as e:
        logging.warning(f"Band read failed {href}: {e}")
        return _blank_gray(), None

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

    scenes = []
    for item in search.item_collection():
        itm = sign(item)
        urls = {}
        for key, opts in {"nir": ["B08", "B08_20m"], "red": ["B04", "B04_20m"]}.items():
            for o in opts:
                if o in itm.assets:
                    urls[key] = itm.assets[o].href
                    break
        if "visual" in itm.assets:
            urls["visual"] = itm.assets["visual"].href
        scenes.append(urls)
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

    metadata = {"jobId": jid, "dateRange": payload.date_range}
    update_job_status(client, jid, JobStatus.PROCESSING, 0, "Ініціалізація", raw=json.dumps(metadata))

    scenes = _fetch_scenes(payload)
    metadata["scenesFetched"] = len(scenes)
    update_job_status(client, jid, JobStatus.PROCESSING, 10, "Завантаження сцен", raw=json.dumps(metadata))

    geom = _buffer_geom(payload.area.geometry.model_dump(), meters=30)

    chosen = None
    for idx, urls in enumerate(scenes, 1):
        metadata["sceneIndex"] = idx
        update_job_status(client, jid, JobStatus.PROCESSING, 20, "Читання спектрів", raw=json.dumps(metadata))
        nir, _ = _read_band(urls.get("nir", ""), geom)
        red, _ = _read_band(urls.get("red", ""), geom)
        if nir.size > 1 and red.size > 1:
            chosen = urls
            break

    if not chosen:
        logging.error(f"No usable scenes for job {jid}")
        update_job_status(client, jid, JobStatus.FAILED, 15, "Сцени не знайдено", raw=json.dumps(metadata))
        return

    update_job_status(client, jid, JobStatus.PROCESSING, 40, "Обчислення NDVI", raw=json.dumps(metadata))
    nir, _ = _read_band(chosen["nir"], geom)
    red, _ = _read_band(chosen["red"], geom)
    nir = _dynamic_downsample(nir)
    red = _dynamic_downsample(red)
    ndvi = _ndvi(nir, red)
    thresh = payload.ndvi_threshold
    metadata["ndviThreshold"] = thresh
    stats = _stats(ndvi, thresh)
    metadata["ndviStats"] = stats

    update_job_status(client, jid, JobStatus.PROCESSING, 60, "Завантаження зображення NDVI", raw=json.dumps(metadata))
    nd_url = _upload(client, jid, _to_png(ndvi, True), "ndvi.png")

    vis_url = ""
    if "visual" in chosen:
        rgb, _ = _read_band(chosen["visual"], geom)
        rgb = _dynamic_downsample(rgb)
        if rgb.size > 1:
            update_job_status(client, jid, JobStatus.PROCESSING, 70, "Завантаження зображення RGB", raw=json.dumps(metadata))
            vis_url = _upload(client, jid, _to_png(rgb, False), "rgb.png")

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
    )

    client.get_blob_client(REPORTS_CONTAINER_NAME, f"{jid}/report.json").upload_blob(
        rpt.model_dump_json(exclude_none=True).encode(),
        overwrite=True,
    )

    metadata["completedAt"] = datetime.now(timezone.utc).isoformat()
    update_job_status(client, jid, JobStatus.COMPLETED, 100, "Завершено", raw=json.dumps(metadata))
