"""Study area: geocoding, polygons, GeoJSON, area validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from geopy.geocoders import Nominatim
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import transform
import pyproj


MAX_AREA_KM2 = 25.0


@dataclass
class StudyArea:
    city: str
    country: str
    polygon: Polygon
    center: Tuple[float, float]  # lat, lon
    label: str

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


def geocode_city(city: str, country: str) -> Tuple[float, float, str]:
    """Return (lat, lon, display_name)."""
    geolocator = Nominatim(user_agent="optitraffic-mvp/0.1")
    query = f"{city}, {country}".strip(", ")
    loc = geolocator.geocode(query, exactly_one=True, timeout=20)
    if loc is None:
        raise ValueError(f"No se encontró la ubicación: {query}")
    return float(loc.latitude), float(loc.longitude), str(loc.address)


def reverse_geocode(lat: float, lon: float) -> Tuple[str, str, str]:
    """
    Return (city, country, display_name) from coordinates.
    Falls back to empty city/country if Nominatim has no match.
    """
    geolocator = Nominatim(user_agent="optitraffic-mvp/0.1")
    loc = geolocator.reverse((lat, lon), exactly_one=True, timeout=20, language="es")
    if loc is None:
        return "", "", f"{lat:.5f}, {lon:.5f}"
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
    return str(city), str(country), str(loc.address)


def rectangle_around(lat: float, lon: float, half_km: float = 0.8) -> Polygon:
    """Axis-aligned rectangle roughly half_km from center."""
    # ~111 km per degree latitude; longitude scaled by cos(lat)
    dlat = half_km / 111.0
    import math

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
        return (
            False,
            f"Área {km2:.1f} km² supera el límite MVP de {max_km2} km². "
            "Reduzca la zona de estudio.",
        )
    return True, f"Área OK: {km2:.2f} km²"


def build_study_area(
    city: str,
    country: str,
    polygon: Polygon,
    label: Optional[str] = None,
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
    )
