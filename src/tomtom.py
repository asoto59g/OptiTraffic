"""TomTom Traffic Flow Vector Tiles client and edge matching."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

import requests
from dotenv import load_dotenv
from shapely.geometry import LineString, shape

_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _ROOT / ".env"
DATA_DIR = _ROOT / "data"
CACHE_DIR = DATA_DIR / "cache" / "tomtom"
USAGE_FILE = DATA_DIR / "cache" / "tomtom_usage.json"

# Freemium guidance (vector flow tiles ~200k/month)
MONTHLY_SOFT_LIMIT = 200_000
CACHE_TTL_SEC = 90
DEFAULT_ZOOM = 15


@dataclass
class TrafficSegment:
    geometry: LineString
    traffic_level: float  # 0..1 relative speed (1 = free flow)
    road_class: Optional[str] = None


def load_api_key() -> Optional[str]:
    # Always load project .env (Streamlit CWD may differ); override empty shell vars.
    load_dotenv(_ENV_FILE, override=True)
    key = os.environ.get("TOMTOM_API_KEY", "").strip().strip('"').strip("'")
    return key or None


def _usage_load() -> dict[str, Any]:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if USAGE_FILE.exists():
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    return {"month": "", "count": 0}


def _usage_inc(n: int = 1) -> int:
    from datetime import datetime

    u = _usage_load()
    month = datetime.utcnow().strftime("%Y-%m")
    if u.get("month") != month:
        u = {"month": month, "count": 0}
    u["count"] = int(u.get("count", 0)) + n
    USAGE_FILE.write_text(json.dumps(u), encoding="utf-8")
    return u["count"]


def usage_count() -> Tuple[int, int]:
    u = _usage_load()
    from datetime import datetime

    month = datetime.utcnow().strftime("%Y-%m")
    if u.get("month") != month:
        return 0, MONTHLY_SOFT_LIMIT
    return int(u.get("count", 0)), MONTHLY_SOFT_LIMIT


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> Tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tiles_for_bbox(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float, zoom: int
) -> list[Tuple[int, int]]:
    x0, y1 = lonlat_to_tile(min_lon, min_lat, zoom)
    x1, y0 = lonlat_to_tile(max_lon, max_lat, zoom)
    tiles = []
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            tiles.append((x, y))
    return tiles


def fetch_flow_tile(
    zoom: int,
    x: int,
    y: int,
    api_key: str,
    traffic_type: str = "relative",
    use_cache: bool = True,
) -> bytes:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{traffic_type}_{zoom}_{x}_{y}.pbf"
    if use_cache and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < CACHE_TTL_SEC:
            return cache_path.read_bytes()

    url = (
        f"https://api.tomtom.com/traffic/map/4/tile/flow/{traffic_type}/"
        f"{zoom}/{x}/{y}.pbf?key={api_key}"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    _usage_inc(1)
    cache_path.write_bytes(r.content)
    return r.content


def decode_flow_tile(pbf_bytes: bytes) -> list[TrafficSegment]:
    import mapbox_vector_tile

    tile = mapbox_vector_tile.decode(pbf_bytes)
    segments: list[TrafficSegment] = []
    for layer_name, layer in tile.items():
        for feat in layer.get("features", []):
            props = feat.get("properties") or {}
            level = props.get("traffic_level")
            if level is None:
                continue
            try:
                level_f = float(level)
            except (TypeError, ValueError):
                continue
            geom = feat.get("geometry")
            if not geom:
                continue
            # MVT decode may already be in tile-local coords; library often returns
            # GeoJSON-like with coordinates already projected depending on version.
            try:
                g = shape(geom)
            except Exception:
                continue
            if g.is_empty:
                continue
            if g.geom_type == "LineString":
                segments.append(
                    TrafficSegment(
                        geometry=g,
                        traffic_level=level_f,
                        road_class=str(props.get("road_type") or props.get("roadTypes") or ""),
                    )
                )
            elif g.geom_type == "MultiLineString":
                for part in g.geoms:
                    segments.append(
                        TrafficSegment(
                            geometry=part,
                            traffic_level=level_f,
                            road_class=str(props.get("road_type") or ""),
                        )
                    )
    return segments


def _tile_local_to_lonlat(coords, zoom: int, x: int, y: int, extent: int = 4096):
    """Convert MVT local coords to WGS84 if still in tile space."""
    n = 2.0**zoom

    def tx(px, py):
        lon = (x + px / extent) / n * 360.0 - 180.0
        merc = 1 - 2 * (y + py / extent) / n
        lat = math.degrees(math.atan(math.sinh(math.pi * merc)))
        return (lon, lat)

    return [tx(c[0], c[1]) for c in coords]


def fetch_traffic_for_bbox(
    bbox: Tuple[float, float, float, float],
    api_key: Optional[str] = None,
    zoom: int = DEFAULT_ZOOM,
) -> list[TrafficSegment]:
    api_key = api_key or load_api_key()
    if not api_key:
        raise RuntimeError("TOMTOM_API_KEY no configurada. Copie .env.example a .env")

    minx, miny, maxx, maxy = bbox
    tiles = tiles_for_bbox(minx, miny, maxx, maxy, zoom)
    # Cap tiles for freemium safety
    if len(tiles) > 64:
        tiles = tiles[:64]

    all_segs: list[TrafficSegment] = []
    for x, y in tiles:
        raw = fetch_flow_tile(zoom, x, y, api_key)
        segs = decode_flow_tile(raw)
        # Heuristic: if coords look like tile-local (0..4096), reproject
        fixed: list[TrafficSegment] = []
        for s in segs:
            coords = list(s.geometry.coords)
            if coords and max(abs(c[0]) for c in coords) > 180:
                ll = _tile_local_to_lonlat(coords, zoom, x, y)
                fixed.append(
                    TrafficSegment(
                        geometry=LineString(ll),
                        traffic_level=s.traffic_level,
                        road_class=s.road_class,
                    )
                )
            else:
                fixed.append(s)
        all_segs.extend(fixed)
    return all_segs


def match_traffic_to_edges(
    edges_geojson: dict[str, Any],
    segments: list[TrafficSegment],
    max_dist_deg: float = 0.00035,
) -> dict[str, float]:
    """
    Map edge_id -> traffic_level (0..1).
    Uses nearest segment by midpoint distance.
    """
    if not segments:
        return {}

    result: dict[str, float] = {}
    for feat in edges_geojson.get("features", []):
        eid = feat["properties"]["id"]
        geom = shape(feat["geometry"])
        if geom.is_empty:
            continue
        mid = geom.interpolate(0.5, normalized=True)
        best = None
        best_d = 1e9
        for seg in segments:
            d = mid.distance(seg.geometry)
            if d < best_d:
                best_d = d
                best = seg
        if best is not None and best_d <= max_dist_deg:
            result[eid] = best.traffic_level
    return result


def traffic_level_to_demand_factor(level: float, base: float = 1.0) -> float:
    """Low relative speed => higher demand factor."""
    congestion = max(0.0, min(1.0, 1.0 - float(level)))
    return base * (0.5 + 1.5 * congestion)


def save_snapshot(path: Path, edge_levels: dict[str, float]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": time.time(), "edges": edge_levels}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_snapshot(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in data.get("edges", {}).items()}


def segments_to_geojson(segments: list[TrafficSegment]) -> dict[str, Any]:
    feats = []
    for i, s in enumerate(segments):
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "id": i,
                    "traffic_level": s.traffic_level,
                    "road_class": s.road_class or "",
                },
                "geometry": json.loads(json.dumps(s.geometry.__geo_interface__)),
            }
        )
    return {"type": "FeatureCollection", "features": feats}
