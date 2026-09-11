"""Save and load OptiTraffic scenarios."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .area import StudyArea, load_geojson_polygon, build_study_area
from .editors import NetworkEdits

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = ROOT / "scenarios"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "scenario"


def list_scenarios() -> list[Path]:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted([p for p in SCENARIOS_DIR.iterdir() if p.is_dir() and (p / "scenario.json").exists()])


def save_scenario(
    name: str,
    area: StudyArea,
    edits: NetworkEdits,
    net_path: Optional[Path] = None,
    edge_levels: Optional[dict[str, float]] = None,
    result_summary: Optional[dict[str, Any]] = None,
    extra_files: Optional[list[Path]] = None,
) -> Path:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    folder = SCENARIOS_DIR / f"{_slug(name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)

    meta = {
        "name": name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "area": area.to_geojson(),
        "edits": edits.to_dict(),
        "net_path": str(net_path) if net_path else None,
        "edge_levels": edge_levels or {},
        "result_summary": result_summary,
    }
    (folder / "scenario.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (folder / "area.geojson").write_text(json.dumps(area.to_geojson(), indent=2), encoding="utf-8")

    if net_path and Path(net_path).exists():
        shutil.copy2(net_path, folder / Path(net_path).name)
        meta["net_path"] = str(folder / Path(net_path).name)
        (folder / "scenario.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if extra_files:
        for f in extra_files:
            if f and Path(f).exists():
                shutil.copy2(f, folder / Path(f).name)

    return folder


def load_scenario(folder: Path) -> dict[str, Any]:
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
    return {
        "meta": meta,
        "area": area,
        "edits": edits,
        "folder": folder,
        "edge_levels": meta.get("edge_levels") or {},
    }
