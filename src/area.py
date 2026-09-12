"""Study area: geocoding, polygons, GeoJSON, area validation."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

from geopy.geocoders import Nominatim
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import transform
import pyproj

from .logging_config import get_logger

log = get_logger("area")

MAX_AREA_KM2 = 25.0
_USER_AGENT = "OptiTraffic/0.2 (local traffic simulator; respectful Nominatim use)"
_MIN_INTERVAL_SEC = 1.1
_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "nominatim"
_RATE_FILE = _CACHE_DIR / "_last_request.json"


@dataclass
class StudyArea:
    city: str
    country: str
    polygon: Polygon
    center: Tuple[float, float]  # lat, lon
    label: str
    country_code: str = ""

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """minx, miny, maxx, maxy (lon, lat)."""
        return self.polygon.bounds

    @property
    def area_km2(self) -> float:
        return geodesic_area_km2(self.polygon)

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "properties": {
                "city": self.city,
                "country": self.country,
                "country_code": self.country_code,
                "label": self.label,
                "area_km2": round(self.area_km2, 3),
            },
            "geometry": mapping(self.polygon),
        }


def geodesic_area_km2(polygon: Polygon) -> float:
    """Approximate area in km² using equal-area projection."""
    minx, miny, maxx, maxy = polygon.bounds
    lon0 = (minx + maxx) / 2
    lat0 = (miny + maxy) / 2
    proj = pyproj.Proj(proj="aea", lat_1=miny, lat_2=maxy, lat_0=lat0, lon_0=lon0)
    transformer = pyproj.Transformer.from_proj(pyproj.Proj("EPSG:4326"), proj, always_xy=True)

    def _t(x, y, z=None):
        return transformer.transform(x, y)

    projected = transform(_t, polygon)
    return abs(projected.area) / 1_000_000.0


def _cache_path(kind: str, key: str) -> Path:
    digest = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()[:40]
    return _CACHE_DIR / f"{kind}_{digest}.json"


def _read_cache(path: Path) -> Optional[dict[str, Any]]:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Nominatim cache read failed: %s", path, exc_info=True)
    return None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.warning("Nominatim cache write failed: %s", path, exc_info=True)


def _respect_rate_limit() -> None:
    """Enforce Nominatim usage policy (~1 req/s)."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        last = 0.0
        if _RATE_FILE.is_file():
            try:
                last = float(json.loads(_RATE_FILE.read_text(encoding="utf-8")).get("ts") or 0)
            except Exception:
                last = 0.0
        wait = _MIN_INTERVAL_SEC - (now - last)
        if wait > 0:
            time.sleep(wait)
        _RATE_FILE.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    except Exception:
        log.warning("Nominatim rate-limit bookkeeping failed", exc_info=True)
        time.sleep(_MIN_INTERVAL_SEC)


def _geolocator() -> Nominatim:
    return Nominatim(user_agent=_USER_AGENT)


def geocode_city(city: str, country: str) -> Tuple[float, float, str]:
    """Return (lat, lon, display_name). Uses disk cache + rate limit."""
    query = f"{city}, {country}".strip(", ")
    cpath = _cache_path("fwd", query.lower())
    cached = _read_cache(cpath)
    if cached and "lat" in cached and "lon" in cached:
        return float(cached["lat"]), float(cached["lon"]), str(cached.get("display") or query)

    _respect_rate_limit()
    loc = _geolocator().geocode(query, exactly_one=True, timeout=20)
    if loc is None:
        raise ValueError(f"No se encontró la ubicación: {query}")
    lat, lon, display = float(loc.latitude), float(loc.longitude), str(loc.address)
    _write_cache(cpath, {"lat": lat, "lon": lon, "display": display, "query": query})
    return lat, lon, display


def reverse_geocode(lat: float, lon: float) -> Tuple[str, str, str]:
    """
    Return (city, country, display_name) from coordinates.
    Falls back to empty city/country if Nominatim has no match.
    """
    city, country, display, _code = reverse_geocode_full(lat, lon)
    return city, country, display


def reverse_geocode_full(lat: float, lon: float) -> Tuple[str, str, str, str]:
    """Return (city, country, display_name, country_code ISO-3166-1 alpha-2)."""
    key = f"{lat:.5f},{lon:.5f}"
    cpath = _cache_path("rev", key)
    cached = _read_cache(cpath)
    if cached:
        return (
            str(cached.get("city") or ""),
            str(cached.get("country") or ""),
            str(cached.get("display") or key),
            str(cached.get("country_code") or "").lower(),
        )

    _respect_rate_limit()
    loc = _geolocator().reverse((lat, lon), exactly_one=True, timeout=20, language="es")
    if loc is None:
        return "", "", f"{lat:.5f}, {lon:.5f}", ""
    raw = loc.raw.get("address") or {}
    city = (
        raw.get("city")
        or raw.get("town")
        or raw.get("village")
        or raw.get("municipality")
        or raw.get("county")
        or raw.get("state_district")
        or ""
    )
    country = raw.get("country") or ""
    code = str(raw.get("country_code") or "").lower()
    display = str(loc.address)
    _write_cache(
        cpath,
        {
            "city": city,
            "country": country,
            "display": display,
            "country_code": code,
            "lat": lat,
            "lon": lon,
        },
    )
    return str(city), str(country), display, code


def rectangle_around(lat: float, lon: float, half_km: float = 0.8) -> Polygon:
    """Axis-aligned rectangle roughly half_km from center."""
    dlat = half_km / 111.0
    dlon = half_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    return box(lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def polygon_from_bounds(south: float, west: float, north: float, east: float) -> Polygon:
    if south >= north or west >= east:
        raise ValueError("Bounds inválidos: south < north y west < east")
    return box(west, south, east, north)


def load_geojson_polygon(raw: str | bytes | dict) -> Polygon:
    if isinstance(raw, (str, bytes)):
        data = json.loads(raw)
    else:
        data = raw

    if data.get("type") == "FeatureCollection":
        feats = data.get("features") or []
        if not feats:
            raise ValueError("GeoJSON FeatureCollection vacío")
        geom = feats[0]["geometry"]
    elif data.get("type") == "Feature":
        geom = data["geometry"]
    else:
        geom = data

    g = shape(geom)
    if g.geom_type == "Polygon":
        return g
    if g.geom_type == "MultiPolygon":
        return max(g.geoms, key=lambda x: x.area)
    raise ValueError(f"Se esperaba Polygon/MultiPolygon, llegó {g.geom_type}")


def validate_area(polygon: Polygon, max_km2: float = MAX_AREA_KM2) -> Tuple[bool, str]:
    km2 = geodesic_area_km2(polygon)
    if km2 <= 0:
        return False, "El polígono tiene área cero."
    if km2 > max_km2:
        return False, f"Área {km2:.1f} km² supera el máximo de {max_km2:.0f} km²."
    return True, f"{km2:.2f} km²"


def build_study_area(
    city: str,
    country: str,
    polygon: Polygon,
    label: Optional[str] = None,
    country_code: str = "",
) -> StudyArea:
    ok, msg = validate_area(polygon)
    if not ok:
        raise ValueError(msg)
    minx, miny, maxx, maxy = polygon.bounds
    center = ((miny + maxy) / 2, (minx + maxx) / 2)
    return StudyArea(
        city=city.strip(),
        country=country.strip(),
        polygon=polygon,
        center=center,
        label=label or f"{city}, {country}",
        country_code=(country_code or "").lower(),
    )
