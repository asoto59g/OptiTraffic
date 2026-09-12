"""Save and load OptiTraffic scenarios bound to a study polygon."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from shapely.geometry import shape

from .area import StudyArea, build_study_area, load_geojson_polygon
from .editors import NetworkEdits

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = ROOT / "scenarios"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "scenario"


def list_scenarios() -> list[Path]:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        [p for p in SCENARIOS_DIR.iterdir() if p.is_dir() and (p / "scenario.json").exists()],
        key=lambda p: (p / "scenario.json").stat().st_mtime,
        reverse=True,
    )


def polygon_iou(a, b) -> float:
    """Intersection-over-union for two shapely polygons (0–1)."""
    try:
        inter = a.intersection(b).area
        union = a.union(b).area
        if union <= 0:
            return 0.0
        return float(inter / union)
    except Exception:
        return 0.0


def scenarios_matching_area(
    area: StudyArea,
    min_iou: float = 0.85,
) -> list[tuple[Path, dict[str, Any], float]]:
    """Return [(folder, meta, iou)] for scenarios whose polygon overlaps the study area."""
    out: list[tuple[Path, dict[str, Any], float]] = []
    for folder in list_scenarios():
        try:
            meta = json.loads((folder / "scenario.json").read_text(encoding="utf-8"))
            feat = meta.get("area") or {}
            geom = feat.get("geometry")
            if not geom:
                continue
            poly = shape(geom)
            iou = polygon_iou(area.polygon, poly)
            if iou >= min_iou:
                out.append((folder, meta, iou))
        except Exception:
            continue
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def find_scenario_by_name(name: str) -> Optional[Path]:
    slug = _slug(name)
    for folder in list_scenarios():
        try:
            meta = json.loads((folder / "scenario.json").read_text(encoding="utf-8"))
            if _slug(str(meta.get("name") or "")) == slug or folder.name.startswith(slug):
                return folder
        except Exception:
            continue
    return None


def save_scenario(
    name: str,
    area: StudyArea,
    edits: NetworkEdits,
    net_path: Optional[Path] = None,
    edge_levels: Optional[dict[str, float]] = None,
    edges_gj: Optional[dict[str, Any]] = None,
    tls_list: Optional[list[dict[str, Any]]] = None,
    result_summary: Optional[dict[str, Any]] = None,
    extra_files: Optional[list[Path]] = None,
    overwrite: bool = False,
) -> Path:
    """
    Persist zone configuration tied to the study polygon.

    Stores: polygon, edits (TLS/parking/stops/lanes), optional net, edges GeoJSON,
    TLS list, TomTom levels, result summary.
    """
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)

    folder: Optional[Path] = None
    if overwrite:
        folder = find_scenario_by_name(name)
    if folder is None:
        folder = SCENARIOS_DIR / f"{_slug(name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)

    net_local: Optional[str] = None
    if net_path and Path(net_path).exists():
        dest_net = folder / Path(net_path).name
        if Path(net_path).resolve() != dest_net.resolve():
            shutil.copy2(net_path, dest_net)
        net_local = dest_net.name

    if edges_gj:
        (folder / "edges.geojson").write_text(
            json.dumps(edges_gj, ensure_ascii=False),
            encoding="utf-8",
        )

    if tls_list is not None:
        (folder / "tls_list.json").write_text(
            json.dumps(tls_list, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    meta = {
        "name": name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "updated": datetime.now().isoformat(timespec="seconds"),
        "area": area.to_geojson(),
        "edits": edits.to_dict(),
        "net_file": net_local,
        "net_path": str(folder / net_local) if net_local else (str(net_path) if net_path else None),
        "edge_levels": edge_levels or {},
        "has_edges_geojson": bool(edges_gj) or (folder / "edges.geojson").exists(),
        "has_tls_list": tls_list is not None or (folder / "tls_list.json").exists(),
        "counts": {
            "tls_placements": len(edits.tls_placements),
            "parking": len(edits.parking),
            "stops": len(edits.stops),
            "lane_overrides": len(edits.lane_overrides),
            "tls_overrides": len(edits.tls_overrides),
        },
        "result_summary": result_summary,
    }
    (folder / "scenario.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "area.geojson").write_text(
        json.dumps(area.to_geojson(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if extra_files:
        for f in extra_files:
            if f and Path(f).exists():
                shutil.copy2(f, folder / Path(f).name)

    return folder


def load_scenario(folder: Path) -> dict[str, Any]:
    folder = Path(folder)
    meta = json.loads((folder / "scenario.json").read_text(encoding="utf-8"))
    poly = load_geojson_polygon(meta["area"])
    props = meta["area"].get("properties") or {}
    area = build_study_area(
        props.get("city", ""),
        props.get("country", ""),
        poly,
        label=props.get("label"),
    )
    edits = NetworkEdits.from_dict(meta.get("edits") or {})

    net_path: Optional[Path] = None
    net_file = meta.get("net_file")
    if net_file and (folder / net_file).exists():
        net_path = folder / net_file
    elif meta.get("net_path") and Path(meta["net_path"]).exists():
        net_path = Path(meta["net_path"])

    edges_gj = None
    edges_path = folder / "edges.geojson"
    if edges_path.exists():
        edges_gj = json.loads(edges_path.read_text(encoding="utf-8"))

    tls_list: list[dict[str, Any]] = []
    tls_path = folder / "tls_list.json"
    if tls_path.exists():
        tls_list = json.loads(tls_path.read_text(encoding="utf-8"))

    return {
        "meta": meta,
        "area": area,
        "edits": edits,
        "folder": folder,
        "edge_levels": meta.get("edge_levels") or {},
        "net_path": net_path,
        "edges_gj": edges_gj,
        "tls_list": tls_list,
    }


def apply_scenario_to_session(data: dict[str, Any], session: Any) -> str:
    """
    Apply loaded scenario into a Streamlit-like session_state mapping.
    Returns suggested wizard step label key hint: 'config' | 'red' | 'zona'.
    """
    area: StudyArea = data["area"]
    session.area = area
    session.edits = data["edits"]
    session.edge_levels = data.get("edge_levels") or {}
    session.city = area.city
    session.country = area.country
    session.center = area.center
    session.preview_polygon = area.polygon
    session.sim_result = None
    session.scenario_folder = str(data.get("folder") or "")

    net_path = data.get("net_path")
    if net_path and Path(net_path).exists():
        session.net_path = Path(net_path)
    else:
        session.net_path = None

    edges_gj = data.get("edges_gj")
    if edges_gj:
        session.edges_gj = edges_gj
    elif session.net_path:
        try:
            from .network_build import edges_geojson

            session.edges_gj = edges_geojson(Path(session.net_path))
        except Exception:
            session.edges_gj = None
    else:
        session.edges_gj = None

    tls = data.get("tls_list")
    if tls:
        session.tls_list = tls
    elif session.net_path:
        try:
            from .network_build import list_traffic_lights

            session.tls_list = list_traffic_lights(Path(session.net_path))
        except Exception:
            session.tls_list = []
    else:
        session.tls_list = []

    # Clear map caches so Folium rebuilds for the loaded polygon
    session.map_nonce = int(getattr(session, "map_nonce", 0) or 0) + 1
    session._junctions_key = None
    session._edges_map_slim = None

    if session.edges_gj and (data["edits"].tls_placements or data["edits"].parking or data["edits"].stops):
        return "config"
    if session.edges_gj:
        return "red"
    return "zona"
