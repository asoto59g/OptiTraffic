"""Download Geofabrik extracts and clip to study polygon."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin

import requests
from shapely.geometry import Polygon

from .area import StudyArea

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OSM_DIR = DATA_DIR / "osm"
CACHE_DIR = DATA_DIR / "cache"

GEOFABRIK_BASE = "https://download.geofabrik.de/"

# Country / region name (lowercase) -> relative Geofabrik path (without .osm.pbf)
# Prefer smaller extracts when possible; fall back to country.
COUNTRY_EXTRACTS: dict[str, str] = {
    "costa rica": "central-america/costa-rica",
    "panama": "central-america/panama",
    "nicaragua": "central-america/nicaragua",
    "guatemala": "central-america/guatemala",
    "honduras": "central-america/honduras",
    "el salvador": "central-america/el-salvador",
    "belize": "central-america/belize",
    "mexico": "north-america/mexico",
    "colombia": "south-america/colombia",
    "peru": "south-america/peru",
    "chile": "south-america/chile",
    "argentina": "south-america/argentina",
    "brazil": "south-america/brazil",
    "brasil": "south-america/brazil",
    "ecuador": "south-america/ecuador",
    "bolivia": "south-america/bolivia",
    "paraguay": "south-america/paraguay",
    "uruguay": "south-america/uruguay",
    "venezuela": "south-america/venezuela",
    "spain": "europe/spain",
    "españa": "europe/spain",
    "france": "europe/france",
    "germany": "europe/germany",
    "deutschland": "europe/germany",
    "italy": "europe/italy",
    "portugal": "europe/portugal",
    "united kingdom": "europe/great-britain",
    "uk": "europe/great-britain",
    "united states": "north-america/us-latest",
    "usa": "north-america/us-latest",
    "canada": "north-america/canada",
}


def ensure_dirs() -> None:
    OSM_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def normalize_country(country: str) -> str:
    return re.sub(r"\s+", " ", country.strip().lower())


def resolve_geofabrik_path(country: str) -> str:
    key = normalize_country(country)
    if key in COUNTRY_EXTRACTS:
        return COUNTRY_EXTRACTS[key]
    # Try slug
    slug = key.replace(" ", "-")
    for prefix in ("central-america", "south-america", "north-america", "europe", "asia", "africa"):
        candidate = f"{prefix}/{slug}"
        return candidate
    return f"south-america/{slug}"


def geofabrik_url(country: str) -> str:
    rel = resolve_geofabrik_path(country)
    return urljoin(GEOFABRIK_BASE, f"{rel}-latest.osm.pbf")


def download_extract(country: str, force: bool = False, timeout: int = 600) -> Path:
    """Download country/region PBF to cache. Returns local path."""
    ensure_dirs()
    rel = resolve_geofabrik_path(country).replace("/", "__")
    dest = OSM_DIR / f"{rel}-latest.osm.pbf"
    if dest.exists() and not force and dest.stat().st_size > 1000:
        return dest

    url = geofabrik_url(country)
    meta = CACHE_DIR / "geofabrik_downloads.json"
    history = {}
    if meta.exists():
        history = json.loads(meta.read_text(encoding="utf-8"))

    with requests.get(url, stream=True, timeout=timeout) as r:
        if r.status_code == 404:
            raise FileNotFoundError(
                f"Extract Geofabrik no encontrado: {url}. "
                "Use un país listado en https://download.geofabrik.de/"
            )
        r.raise_for_status()
        tmp = dest.with_suffix(".partial")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        tmp.replace(dest)

    history[str(dest.name)] = {"url": url, "ts": time.time(), "bytes": dest.stat().st_size}
    meta.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return dest


def clip_pbf_with_osmium(src: Path, polygon: Polygon, out: Path) -> Path:
    """Clip PBF to polygon bbox using pyosmium (bbox filter)."""
    import osmium

    minx, miny, maxx, maxy = polygon.bounds
    # Slight buffer to keep junctions near boundary
    pad = 0.001
    bbox = (minx - pad, miny - pad, maxx + pad, maxy + pad)

    class BBoxFilter(osmium.SimpleHandler):
        def __init__(self, writer, bbox):
            super().__init__()
            self.writer = writer
            self.bbox = bbox
            self._nodes: set[int] = set()

        def _in(self, lon: float, lat: float) -> bool:
            return self.bbox[0] <= lon <= self.bbox[2] and self.bbox[1] <= lat <= self.bbox[3]

        def node(self, n):
            if n.location.valid() and self._in(n.location.lon, n.location.lat):
                self._nodes.add(n.id)
                self.writer.add_node(n)

        def way(self, w):
            # Keep ways that reference at least one in-bbox node; osmium SimpleHandler
            # may not have full refs resolved — use bbox of way nodes if available.
            keep = False
            for nr in w.nodes:
                if nr.ref in self._nodes:
                    keep = True
                    break
                if nr.location.valid() and self._in(nr.location.lon, nr.location.lat):
                    keep = True
                    break
            if keep:
                self.writer.add_way(w)

        def relation(self, r):
            # Skip most relations; netconvert works from ways/nodes.
            pass

    out.parent.mkdir(parents=True, exist_ok=True)
    # Write as .osm XML for broader netconvert compatibility on Windows
    if out.suffix == ".pbf":
        out = out.with_suffix(".osm")

    writer = osmium.SimpleWriter(str(out))
    handler = BBoxFilter(writer, bbox)
    # locations=True so way node locations are available when possible
    handler.apply_file(str(src), locations=True)
    writer.close()
    return out


def clip_pbf_bbox_osmconvert(src: Path, polygon: Polygon, out: Path, osmconvert: Optional[Path] = None) -> Path:
    """Optional faster clip via osmconvert if available."""
    import shutil
    import subprocess

    bin_path = osmconvert or shutil.which("osmconvert")
    if not bin_path:
        raise FileNotFoundError("osmconvert no disponible")
    minx, miny, maxx, maxy = polygon.bounds
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(bin_path),
        str(src),
        f"-b={minx},{miny},{maxx},{maxy}",
        f"-o={out}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)
    return out


def prepare_clipped_osm(area: StudyArea, force_download: bool = False) -> Tuple[Path, Path]:
    """
    Download Geofabrik extract and clip to study area.
    Returns (full_extract_path, clipped_osm_path).
    """
    ensure_dirs()
    extract = download_extract(area.country, force=force_download)
    slug = re.sub(r"[^a-z0-9]+", "_", f"{area.city}_{area.country}".lower()).strip("_")
    clipped = OSM_DIR / f"{slug}_clip.osm"
    try:
        clipped = clip_pbf_with_osmium(extract, area.polygon, clipped)
    except Exception:
        # Fallback: write bbox clip attempt via osmconvert
        clipped_pbf = OSM_DIR / f"{slug}_clip.osm.pbf"
        clipped = clip_pbf_bbox_osmconvert(extract, area.polygon, clipped_pbf)
    return extract, clipped
