"""Build SUMO network from clipped OSM via netconvert."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from shapely.geometry import LineString, mapping

from .area import StudyArea
from .logging_config import get_logger
from .sumo_env import SumoEnv, detect_sumo, ensure_sumolib_on_path, run_cmd

log = get_logger("network_build")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
NETS_DIR = DATA_DIR / "nets"


def _slug(area: StudyArea) -> str:
    return re.sub(r"[^a-z0-9]+", "_", f"{area.city}_{area.country}".lower()).strip("_")


def build_network(
    osm_file: Path,
    area: StudyArea,
    sumo: Optional[SumoEnv] = None,
    extra_args: Optional[list[str]] = None,
) -> Path:
    """Run netconvert and return path to .net.xml."""
    sumo = sumo or detect_sumo()
    if not sumo.ok or not sumo.netconvert_bin:
        raise RuntimeError(sumo.message)

    NETS_DIR.mkdir(parents=True, exist_ok=True)
    # Native netconvert fails on non-ASCII paths (OneDrive "Geomática") — use safe dir.
    from .osm_fetch import SAFE_ROOT, path_is_safe, to_safe_path

    safe_nets = SAFE_ROOT / "nets"
    safe_nets.mkdir(parents=True, exist_ok=True)
    out = safe_nets / f"{_slug(area)}.net.xml"
    osm_for_nc = osm_file if path_is_safe(osm_file) else to_safe_path(osm_file)

    minx, miny, maxx, maxy = area.bbox
    args = [
        str(sumo.netconvert_bin),
        "--osm-files",
        str(osm_for_nc),
        "-o",
        str(out),
        "--geometry.remove",
        "true",
        "--ramps.guess",
        "true",
        "--junctions.join",
        "true",
        "--tls.guess-signals",
        "true",
        "--tls.discard-simple",
        "false",
        "--tls.join",
        "true",
        "--keep-edges.by-vclass",
        "passenger",
        "--remove-edges.isolated",
        "true",
        "--output.street-names",
        "true",
        "--proj.utm",
        "true",
        # Keep OSM tags / one-way: never invent reverse carriageways for oneway=yes
        "--osm.all-attributes",
        "true",
        "--keep-edges.in-geo-boundary",
        f"{minx},{miny},{maxx},{maxy}",
    ]
    if extra_args:
        args.extend(extra_args)

    result = run_cmd(args, timeout=900)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(
            "netconvert falló:\n" + (result.stderr or result.stdout or "sin salida")
        )

    # Municipal speed cap (40 km/h) — OSM often imports higher highway speeds.
    from .traffic_params import CITY_MAX_SPEED_MS

    cap_network_speeds(out, max_speed_ms=CITY_MAX_SPEED_MS)

    # Mirror into project data/nets when possible
    project_out = NETS_DIR / out.name
    try:
        import shutil

        shutil.copy2(out, project_out)
        return project_out
    except OSError:
        return out


def cap_network_speeds(net_path: Path, max_speed_ms: float = 11.111) -> Path:
    """Clamp every lane speed attribute in a .net.xml to max_speed_ms."""
    import xml.etree.ElementTree as ET

    tree = ET.parse(net_path)
    root = tree.getroot()
    changed = 0
    for lane in root.iter("lane"):
        raw = lane.get("speed")
        if raw is None:
            continue
        try:
            spd = float(raw)
        except ValueError:
            continue
        if spd > max_speed_ms + 1e-6:
            lane.set("speed", f"{max_speed_ms:.3f}")
            changed += 1
    if changed:
        tree.write(net_path, encoding="utf-8", xml_declaration=True)
    return net_path


def annotate_edge_directions(edges_geojson: dict[str, Any]) -> dict[str, Any]:
    """
    Mark each edge with oneway=True when SUMO has no opposite edge id.
    OSM oneway=yes ⇒ only one directed edge; two-way ⇒ id and -id.
    """
    ids = {
        str((f.get("properties") or {}).get("id"))
        for f in edges_geojson.get("features", [])
        if (f.get("properties") or {}).get("id") is not None
    }

    def _opposite(eid: str) -> str:
        return eid[1:] if eid.startswith("-") else f"-{eid}"

    n_one = 0
    n_two = 0
    for feat in edges_geojson.get("features", []):
        props = feat.setdefault("properties", {})
        eid = str(props.get("id") or "")
        if not eid:
            continue
        is_oneway = _opposite(eid) not in ids
        props["oneway"] = bool(is_oneway)
        props["sentido"] = "unico" if is_oneway else "doble"
        if is_oneway:
            n_one += 1
        else:
            n_two += 1
    edges_geojson["_direction_stats"] = {
        "oneway_edges": n_one,
        "twoway_edges": n_two,
        "oneway_pct": round(100.0 * n_one / max(1, n_one + n_two), 1),
    }
    return edges_geojson


def edges_geojson(net_path: Path, sumo: Optional[SumoEnv] = None) -> dict[str, Any]:
    """Export SUMO edges as GeoJSON FeatureCollection (WGS84)."""
    sumo = sumo or detect_sumo()
    if not sumo.home:
        raise RuntimeError("SUMO_HOME no definido")
    ensure_sumolib_on_path(sumo.home)
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    id_set = {e.getID() for e in net.getEdges() if e.getFunction() != "internal"}
    features = []
    for edge in net.getEdges():
        if edge.getFunction() == "internal":
            continue
        shape = edge.getShape()
        if not shape or len(shape) < 2:
            continue
        # SUMO stores x,y in network projection; convert to lon/lat
        coords = []
        for x, y in shape:
            lon, lat = net.convertXY2LonLat(x, y)
            coords.append((lon, lat))
        line = LineString(coords)
        eid = edge.getID()
        opp = eid[1:] if eid.startswith("-") else f"-{eid}"
        is_oneway = opp not in id_set
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": eid,
                    "name": edge.getName() or "",
                    "lanes": edge.getLaneNumber(),
                    "speed": edge.getSpeed(),
                    "length": edge.getLength(),
                    "from": edge.getFromNode().getID(),
                    "to": edge.getToNode().getID(),
                    "oneway": is_oneway,
                    "sentido": "unico" if is_oneway else "doble",
                },
                "geometry": mapping(line),
            }
        )
    gj = {"type": "FeatureCollection", "features": features}
    return annotate_edge_directions(gj)


def list_traffic_lights(net_path: Path, sumo: Optional[SumoEnv] = None) -> list[dict[str, Any]]:
    sumo = sumo or detect_sumo()
    ensure_sumolib_on_path(sumo.home)
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    tls_list = []
    raw = net.getTrafficLights()
    for item in raw:
        tid = item if isinstance(item, str) else item.getID()
        lon = lat = None
        node = tid
        try:
            jn = net.getNode(tid)
            x, y = jn.getCoord()
            lon, lat = net.convertXY2LonLat(x, y)
        except Exception:
            log.warning("TLS coord lookup failed for %s", tid, exc_info=True)
            # Try matching TLS id to a junction that controls traffic lights
            for jn in net.getNodes():
                if jn.getType() == "traffic_light" and (jn.getID() == tid or tid.startswith(jn.getID())):
                    x, y = jn.getCoord()
                    lon, lat = net.convertXY2LonLat(x, y)
                    node = jn.getID()
                    break
        tls_list.append({"id": tid, "node": node, "lon": lon, "lat": lat, "n_edges": 0})
    return tls_list


def save_edges_geojson(net_path: Path, out: Optional[Path] = None) -> Path:
    gj = edges_geojson(net_path)
    out = out or net_path.with_suffix(".edges.geojson")
    out.write_text(json.dumps(gj), encoding="utf-8")
    return out
