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
        
        band_map = {
            'nir': 'B08',
            'red': 'B04',
            'visual': 'visual'  # Keep visual as is for now, may need TCI later
        }

        for requested_band in bands:
            asset_key = band_map.get(requested_band)
            if not asset_key:
                log_adapter.warning(f"Requested band '{requested_band}' not in known map, skipping.")
                missing.append(f"{requested_band} (unknown)")
                continue
                
            asset = signed.assets.get(asset_key)
            if asset:
                urls[requested_band] = asset.href # Store URL using the requested name
            else:
                missing.append(requested_band) # Report missing using requested name
        if missing:
            raise ValueError(f"Missing bands: {missing}")
        return urls
    except Exception as e:
        log_adapter.error(f"Sentinel search failed: {e}", exc_info=True)
        raise