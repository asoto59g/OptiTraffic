"""Build SUMO network from clipped OSM via netconvert."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from shapely.geometry import LineString, mapping

from .area import StudyArea
from .sumo_env import SumoEnv, detect_sumo, ensure_sumolib_on_path, run_cmd

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
    out = NETS_DIR / f"{_slug(area)}.net.xml"
    args = [
        str(sumo.netconvert_bin),
        "--osm-files",
        str(osm_file),
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
    ]
    if extra_args:
        args.extend(extra_args)

    result = run_cmd(args, timeout=900)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(
            "netconvert falló:\n" + (result.stderr or result.stdout or "sin salida")
        )
    return out


def edges_geojson(net_path: Path, sumo: Optional[SumoEnv] = None) -> dict[str, Any]:
    """Export SUMO edges as GeoJSON FeatureCollection (WGS84)."""
    sumo = sumo or detect_sumo()
    if not sumo.home:
        raise RuntimeError("SUMO_HOME no definido")
    ensure_sumolib_on_path(sumo.home)
    import sumolib

    net = sumolib.net.readNet(str(net_path))
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
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": edge.getID(),
                    "name": edge.getName() or "",
                    "lanes": edge.getLaneNumber(),
                    "speed": edge.getSpeed(),
                    "length": edge.getLength(),
                    "from": edge.getFromNode().getID(),
                    "to": edge.getToNode().getID(),
                },
                "geometry": mapping(line),
            }
        )
    return {"type": "FeatureCollection", "features": features}


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
