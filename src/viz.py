"""Folium map helpers for OptiTraffic."""

from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

import folium
from folium.plugins import Draw
from shapely.geometry import mapping


def base_map(center: Tuple[float, float], zoom: int = 14) -> folium.Map:
    lat, lon = center
    m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles="OpenStreetMap")
    return m


def bounds_latlon(polygon) -> list[list[float]]:
    """Return Folium fit_bounds corners [[south, west], [north, east]]."""
    minx, miny, maxx, maxy = polygon.bounds  # lon/lat
    return [[miny, minx], [maxy, maxx]]


def fit_polygon(m: folium.Map, polygon, padding: float = 0.0) -> folium.Map:
    """Zoom map to polygon extent."""
    minx, miny, maxx, maxy = polygon.bounds
    if padding:
        minx -= padding
        maxx += padding
        miny -= padding
        maxy += padding
    m.fit_bounds([[miny, minx], [maxy, maxx]])
    return m


def fit_center(m: folium.Map, center: Tuple[float, float], zoom: int = 14) -> folium.Map:
    lat, lon = center
    m.location = [lat, lon]
    m.options["zoom"] = zoom
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


def add_polygon(m: folium.Map, polygon, name: str = "Área", fit: bool = True) -> folium.Map:
    folium.GeoJson(
        mapping(polygon),
        name=name,
        style_function=lambda _: {
            "color": "#0B6E4F",
            "weight": 2,
            "fillOpacity": 0.15,
        },
    ).add_to(m)
    if fit:
        fit_polygon(m, polygon)
    return m


def _speed_color(level: Optional[float], mode: str = "tomtom") -> str:
    """tomtom: 1=green free flow; sim: use mean_speed fraction."""
    if level is None:
        return "#888888"
    if mode == "tomtom":
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


def simplify_edges_for_map(edges_geojson: dict[str, Any], max_points: int = 8) -> dict[str, Any]:
    """Lighter GeoJSON for Folium config map (fewer vertices per edge)."""
    feats = []
    for feat in edges_geojson.get("features", []):
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") == "LineString" and len(coords) > max_points:
            step = max(1, (len(coords) - 1) // (max_points - 1))
            slim = [coords[i] for i in range(0, len(coords), step)]
            if slim[-1] != coords[-1]:
                slim.append(coords[-1])
            feats.append(
                {
                    "type": "Feature",
                    "properties": feat.get("properties") or {},
                    "geometry": {"type": "LineString", "coordinates": slim},
                }
            )
        else:
            feats.append(feat)
    return {"type": "FeatureCollection", "features": feats}


def add_edges_layer(
    m: folium.Map,
    edges_geojson: dict[str, Any],
    value_by_id: Optional[dict[str, float]] = None,
    mode: str = "plain",
    name: str = "Red vial",
    weight: int = 3,
    fit: bool = False,
    color_by_id: Optional[dict[str, str]] = None,
    weight_by_id: Optional[dict[str, int]] = None,
    show_direction: bool = False,
    direction_max_arrows: int = 250,
) -> folium.Map:
    def style(feat):
        eid = feat["properties"].get("id")
        props = feat.get("properties") or {}
        if color_by_id and eid in color_by_id:
            color = color_by_id[eid]
            w = (weight_by_id or {}).get(eid, weight + 2)
            return {"color": color, "weight": w, "opacity": 0.95}
        if mode == "plain" or not value_by_id:
            # OSM one-way vs two-way visual cue
            if props.get("oneway") is True or props.get("sentido") == "unico":
                color = "#1a5276"  # darker = sentido único OSM
            else:
                color = "#85c1e9"  # lighter = doble sentido OSM
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
            fields=["id", "name", "lanes", "sentido"],
            aliases=["Edge", "Nombre", "Carriles", "Sentido OSM"],
        ),
    ).add_to(m)

    if show_direction:
        add_direction_arrows(m, edges_geojson, max_arrows=direction_max_arrows)

    if fit and edges_geojson.get("features"):
        lats: list[float] = []
        lons: list[float] = []
        for feat in edges_geojson["features"]:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if geom.get("type") == "LineString":
                for lon, lat in coords:
                    lons.append(lon)
                    lats.append(lat)
        if lats and lons:
            m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    return m


def add_direction_arrows(
    m: folium.Map,
    edges_geojson: dict[str, Any],
    max_arrows: int = 250,
) -> folium.Map:
    """Small mid-edge markers indicating SUMO/OSM travel direction."""
    import math

    feats = edges_geojson.get("features") or []
    step = max(1, len(feats) // max(1, max_arrows))
    for feat in feats[::step]:
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        (lon0, lat0) = coords[0]
        (lon1, lat1) = coords[-1]
        mid = coords[len(coords) // 2]
        lon, lat = float(mid[0]), float(mid[1])
        bearing = math.degrees(math.atan2(lon1 - lon0, lat1 - lat0)) % 360
        oneway = props.get("oneway") is True or props.get("sentido") == "unico"
        color = "#1a5276" if oneway else "#5dade2"
        folium.RegularPolygonMarker(
            location=[lat, lon],
            number_of_sides=3,
            radius=4 if oneway else 3,
            rotation=bearing,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.9,
            weight=1,
            tooltip=f"{'→ único' if oneway else '↔ doble'} · {props.get('id')}",
        ).add_to(m)
    return m


def add_tls_markers(
    m: folium.Map,
    tls_list: Sequence[dict[str, Any]],
    selected_id: Optional[str] = None,
) -> folium.Map:
    for t in tls_list:
        if t.get("lat") is None or t.get("lon") is None:
            continue
        tid = t["id"]
        selected = selected_id is not None and tid == selected_id
        folium.CircleMarker(
            location=[t["lat"], t["lon"]],
            radius=9 if selected else 5,
            color="#f39c12" if selected else "#8e44ad",
            fill=True,
            fill_opacity=0.95,
            popup=f"Semáforo {tid}",
            tooltip=f"TLS {tid}",
        ).add_to(m)
    return m


def click_latlon(folium_output: Optional[dict]) -> Optional[Tuple[float, float]]:
    """Extract click lat/lon from streamlit-folium return value."""
    if not folium_output:
        return None
    obj = folium_output.get("last_object_clicked") or folium_output.get("last_clicked")
    if not obj:
        return None
    lat = obj.get("lat")
    lon = obj.get("lng") if "lng" in obj else obj.get("lon")
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def build_junctions(edges_geojson: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build intersection nodes from edge endpoints (from/to + coordinates).
    Returns list of {id, lat, lon, edges: [...], degree: int}.
    """
    nodes: dict[str, dict[str, Any]] = {}
    for feat in edges_geojson.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        eid = props.get("id")
        ends = [
            (str(props.get("from") or f"anon_f_{eid}"), coords[0]),
            (str(props.get("to") or f"anon_t_{eid}"), coords[-1]),
        ]
        for nid, (lon, lat) in ends:
            if nid not in nodes:
                nodes[nid] = {
                    "id": nid,
                    "lat": float(lat),
                    "lon": float(lon),
                    "edges": [],
                }
            else:
                # average slight coordinate drift
                nodes[nid]["lat"] = (nodes[nid]["lat"] + float(lat)) / 2
                nodes[nid]["lon"] = (nodes[nid]["lon"] + float(lon)) / 2
            if eid and eid not in nodes[nid]["edges"]:
                nodes[nid]["edges"].append(eid)
    out = []
    for n in nodes.values():
        n["degree"] = len(n["edges"])
        out.append(n)
    return out


def add_junction_markers(
    m: folium.Map,
    junctions: Sequence[dict[str, Any]],
    selected_id: Optional[str] = None,
    min_degree: int = 2,
) -> folium.Map:
    """Draw intersection dots (degree>=2) so users can click cruces, not only street lines."""
    for j in junctions:
        if int(j.get("degree") or 0) < min_degree:
            continue
        jid = j["id"]
        selected = selected_id is not None and jid == selected_id
        folium.CircleMarker(
            location=[j["lat"], j["lon"]],
            radius=10 if selected else 6,
            color="#c0392b" if selected else "#2c3e50",
            fill=True,
            fill_color="#e74c3c" if selected else "#95a5a6",
            fill_opacity=0.95,
            weight=3 if selected else 2,
            popup=f"Intersección {jid} ({j.get('degree')} calles)",
            tooltip=f"Cruce {jid} · {j.get('degree')} tramos — clic para semáforo",
        ).add_to(m)
    return m


def nearest_junction(
    junctions: Sequence[dict[str, Any]],
    lat: float,
    lon: float,
    max_dist_deg: float = 0.0010,
    min_degree: int = 2,
) -> Optional[dict[str, Any]]:
    import math

    best = None
    best_d = 1e9
    for j in junctions:
        if int(j.get("degree") or 0) < min_degree:
            continue
        d = math.hypot(float(j["lat"]) - lat, float(j["lon"]) - lon)
        if d < best_d:
            best_d = d
            best = j
    if best is None or best_d > max_dist_deg:
        return None
    return dict(best)


def fit_junction(m: folium.Map, junction: dict[str, Any], pad: float = 0.00055) -> folium.Map:
    lat, lon = float(junction["lat"]), float(junction["lon"])
    m.fit_bounds([[lat - pad, lon - pad], [lat + pad, lon + pad]])
    return m


def edge_center_latlon(edges_geojson: dict[str, Any], edge_id: str) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) midpoint of an edge."""
    for feat in edges_geojson.get("features", []):
        props = feat.get("properties") or {}
        if props.get("id") != edge_id:
            continue
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 1:
            return None
        mid = coords[len(coords) // 2]
        return float(mid[1]), float(mid[0])
    return None


def fit_edge(m: folium.Map, edges_geojson: dict[str, Any], edge_id: str, pad: float = 0.00035) -> folium.Map:
    """Zoom map tightly around one edge for easier clicking."""
    for feat in edges_geojson.get("features", []):
        props = feat.get("properties") or {}
        if props.get("id") != edge_id:
            continue
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            return m
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        m.fit_bounds(
            [[min(lats) - pad, min(lons) - pad], [max(lats) + pad, max(lons) + pad]]
        )
        return m
    return m


def nearest_edge_id(
    edges_geojson: dict[str, Any],
    lat: float,
    lon: float,
    max_dist_deg: float = 0.0012,
) -> Optional[dict[str, Any]]:
    """Return properties of nearest edge to a click, or None if too far."""
    from shapely.geometry import LineString, Point

    pt = Point(lon, lat)
    best = None
    best_d = 1e9
    for feat in edges_geojson.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        line = LineString(coords)
        d = line.distance(pt)
        if d < best_d:
            best_d = d
            best = feat
    if best is None or best_d > max_dist_deg:
        return None
    return dict(best.get("properties") or {})


def nearest_tls(
    tls_list: Sequence[dict[str, Any]],
    lat: float,
    lon: float,
    max_dist_deg: float = 0.0012,
) -> Optional[dict[str, Any]]:
    import math

    best = None
    best_d = 1e9
    for t in tls_list:
        if t.get("lat") is None or t.get("lon") is None:
            continue
        d = math.hypot(float(t["lat"]) - lat, float(t["lon"]) - lon)
        if d < best_d:
            best_d = d
            best = t
    if best is None or best_d > max_dist_deg:
        return None
    return dict(best)


def extract_drawn_geojson(folium_output: Optional[dict]) -> Optional[dict]:
    """Parse last drawn feature from streamlit-folium return value."""
    if not folium_output:
        return None
    last = folium_output.get("last_active_drawing")
    if last:
        return last
    drawings = folium_output.get("all_drawings") or []
    if drawings:
        return drawings[-1]
    return None


def map_key(*parts: Any) -> str:
    """Changing Streamlit key so Folium remounts after location changes."""
    return "map_" + "_".join(
        str(p).replace(".", "p").replace("-", "m").replace(" ", "")[:28]
        for p in parts
        if p is not None
    )


def add_flow_gate_markers(
    m: folium.Map,
    gates: Sequence[Any],
    selected_edge_id: Optional[str] = None,
) -> folium.Map:
    """Markers for entry (green) / exit (orange); selected gate highlighted yellow."""
    for g in gates:
        kind = getattr(g, "kind", None) or (g.get("kind") if isinstance(g, dict) else None)
        eid = getattr(g, "edge_id", None) or (g.get("edge_id") if isinstance(g, dict) else None)
        lat = getattr(g, "lat", None) if not isinstance(g, dict) else g.get("lat")
        lon = getattr(g, "lon", None) if not isinstance(g, dict) else g.get("lon")
        vph = getattr(g, "vehs_per_hour", None) if not isinstance(g, dict) else g.get("vehs_per_hour")
        name = getattr(g, "name", None) if not isinstance(g, dict) else g.get("name")
        sentido = getattr(g, "sentido", None) if not isinstance(g, dict) else g.get("sentido")
        if lat is None or lon is None or eid is None:
            continue
        is_entry = str(kind) == "entry"
        color = "#1e8449" if is_entry else "#d35400"
        label = "ENTRADA" if is_entry else "SALIDA"
        sel = str(eid) == str(selected_edge_id)
        if sel:
            color = "#f1c40f"  # yellow when selected from list/map
        sentido_txt = f" · {sentido}" if sentido else ""
        folium.CircleMarker(
            location=[float(lat), float(lon)],
            radius=12 if sel else 7,
            color="#7d6608" if sel else ("#1e8449" if is_entry else "#d35400"),
            weight=4 if sel else 2,
            fill=True,
            fill_color=color,
            fill_opacity=0.95,
            tooltip=(
                f"{label}{sentido_txt} · {int(float(vph or 0))} veh/h · {name or eid}"
            ),
        ).add_to(m)
    return m
