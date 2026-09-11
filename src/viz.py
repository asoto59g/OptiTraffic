"""Folium map helpers for OptiTraffic."""

from __future__ import annotations

from typing import Any, Optional, Tuple

import folium
from folium.plugins import Draw
from shapely.geometry import mapping, shape


def base_map(center: Tuple[float, float], zoom: int = 14) -> folium.Map:
    lat, lon = center
    m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles="OpenStreetMap")
    return m


def add_draw_control(m: folium.Map) -> folium.Map:
    Draw(
        export=True,
        filename="study_area.geojson",
        position="topleft",
        draw_options={
            "polyline": False,
            "circle": False,
            "circlemarker": False,
            "marker": False,
            "polygon": True,
            "rectangle": True,
        },
        edit_options={"edit": True},
    ).add_to(m)
    return m


def add_polygon(m: folium.Map, polygon, name: str = "Área") -> folium.Map:
    folium.GeoJson(
        mapping(polygon),
        name=name,
        style_function=lambda _: {
            "color": "#0B6E4F",
            "weight": 2,
            "fillOpacity": 0.1,
        },
    ).add_to(m)
    return m


def _speed_color(level: Optional[float], mode: str = "tomtom") -> str:
    """tomtom: 1=green free flow; sim: use mean_speed fraction."""
    if level is None:
        return "#888888"
    if mode == "tomtom":
        # traffic_level 0..1
        v = float(level)
    else:
        v = max(0.0, min(1.0, float(level) / 13.9))
    if v >= 0.75:
        return "#2ecc71"
    if v >= 0.5:
        return "#f1c40f"
    if v >= 0.25:
        return "#e67e22"
    return "#e74c3c"


def add_edges_layer(
    m: folium.Map,
    edges_geojson: dict[str, Any],
    value_by_id: Optional[dict[str, float]] = None,
    mode: str = "plain",
    name: str = "Red vial",
    weight: int = 3,
) -> folium.Map:
    def style(feat):
        eid = feat["properties"].get("id")
        if mode == "plain" or not value_by_id:
            color = "#3498db"
        elif mode == "tomtom":
            color = _speed_color(value_by_id.get(eid), "tomtom")
        elif mode == "sim":
            color = _speed_color(value_by_id.get(eid), "sim")
        else:
            congested = value_by_id.get(eid)
            color = "#e74c3c" if congested else "#2ecc71"
        return {"color": color, "weight": weight, "opacity": 0.85}

    folium.GeoJson(
        edges_geojson,
        name=name,
        style_function=style,
        tooltip=folium.GeoJsonTooltip(
            fields=[f for f in ("id", "name", "lanes") if True],
            aliases=["Edge", "Nombre", "Carriles"],
        ),
    ).add_to(m)
    return m


def add_tls_markers(m: folium.Map, tls_list: list[dict[str, Any]]) -> folium.Map:
    for t in tls_list:
        if t.get("lat") is None or t.get("lon") is None:
            continue
        folium.CircleMarker(
            location=[t["lat"], t["lon"]],
            radius=5,
            color="#8e44ad",
            fill=True,
            fill_opacity=0.9,
            popup=f"Semáforo {t['id']}",
        ).add_to(m)
    return m


def extract_drawn_geojson(folium_output: Optional[dict]) -> Optional[dict]:
    """Parse last drawn feature from streamlit-folium return value."""
    if not folium_output:
        return None
    # streamlit-folium may return all_drawings or last_active_drawing
    last = folium_output.get("last_active_drawing")
    if last:
        return last
    drawings = folium_output.get("all_drawings") or []
    if drawings:
        return drawings[-1]
    return None
