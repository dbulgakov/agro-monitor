import asyncio
import logging
from typing import Dict, List
from shapely.geometry import shape
from pystac_client import Client
import planetary_computer
import pystac

async def get_sentinel2_urls(job_id: str, geometry: dict, date_range: str, bands: List[str] = ["nir", "red", "visual"]) -> Dict[str, str]:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    try:
        target = shape(geometry)
        catalog = await asyncio.to_thread(Client.open, "https://planetarycomputer.microsoft.com/api/stac/v1")
        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=target.bounds,
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": 25}},
            limit=5,
        )
        items = pystac.ItemCollection.from_dict(await asyncio.to_thread(search.get_all_items_as_dict))
        if not items:
            raise FileNotFoundError("No items found.")
        intersect = [it for it in items if shape(it.geometry).intersects(target)]
        if not intersect:
            raise FileNotFoundError("No intersecting items.")
        least = min(intersect, key=lambda it: it.properties.get("eo:cloud_cover", 100))
        signed = await asyncio.to_thread(planetary_computer.sign, least)
        urls = {}
        missing = []
        for b in bands:
            asset = signed.assets.get(b)
            if asset:
                urls[b] = asset.href
            else:
                missing.append(b)
        if missing:
            raise ValueError(f"Missing bands: {missing}")
        return urls
    except Exception as e:
        log_adapter.error(f"Sentinel search failed: {e}", exc_info=True)
        raise