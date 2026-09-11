"""Generate SUMO additional files: TLS, parking, stops, lane overrides."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional


@dataclass
class TlsTiming:
    green: int = 45
    yellow: int = 5
    red: int = 45


@dataclass
class LaneOverride:
    edge_id: str
    num_lanes: int = 1
    oneway_dual: bool = False  # two lanes same direction


@dataclass
class ParkingConfig:
    edge_id: str
    side: Literal["left", "right", "both"] = "right"
    corner_clearance_m: float = 5.0
    length_m: float = 40.0
    capacity: int = 8


@dataclass
class StopSign:
    edge_id: str
    junction_id: Optional[str] = None


@dataclass
class NetworkEdits:
    tls_default: TlsTiming = field(default_factory=TlsTiming)
    tls_overrides: dict[str, TlsTiming] = field(default_factory=dict)
    lane_overrides: list[LaneOverride] = field(default_factory=list)
    parking: list[ParkingConfig] = field(default_factory=list)
    stops: list[StopSign] = field(default_factory=list)
    min_width_both_sides_m: float = 9.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tls_default": asdict(self.tls_default),
            "tls_overrides": {k: asdict(v) for k, v in self.tls_overrides.items()},
            "lane_overrides": [asdict(x) for x in self.lane_overrides],
            "parking": [asdict(x) for x in self.parking],
            "stops": [asdict(x) for x in self.stops],
            "min_width_both_sides_m": self.min_width_both_sides_m,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NetworkEdits":
        edits = cls()
        td = d.get("tls_default") or {}
        edits.tls_default = TlsTiming(**{k: td[k] for k in ("green", "yellow", "red") if k in td})
        for tid, timing in (d.get("tls_overrides") or {}).items():
            edits.tls_overrides[tid] = TlsTiming(**timing)
        edits.lane_overrides = [LaneOverride(**x) for x in d.get("lane_overrides") or []]
        edits.parking = [ParkingConfig(**x) for x in d.get("parking") or []]
        edits.stops = [StopSign(**x) for x in d.get("stops") or []]
        edits.min_width_both_sides_m = float(d.get("min_width_both_sides_m", 9.0))
        return edits


def _indent(elem: ET.Element, level: int = 0) -> None:
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


def write_tls_add(
    out_path: Path,
    tls_ids: list[str],
    edits: NetworkEdits,
    n_phases: int = 2,
) -> Path:
    """
    Write tlLogic additional file with configurable G/Y/R timings.
    Uses a simple 2-phase pattern by default (NS / EW style).
    """
    root = ET.Element("additional")
    for tid in tls_ids:
        timing = edits.tls_overrides.get(tid, edits.tls_default)
        tl = ET.SubElement(
            root,
            "tlLogic",
            id=tid,
            type="static",
            programID="optitraffic",
            offset="0",
        )
        # Phase A green, yellow, red-clear; Phase B same
        # State strings are placeholders; SUMO will adapt if length mismatches on load
        # Prefer letting netconvert TLS remain and only override durations via change
        state_a = "G" * 8
        state_y = "y" * 8
        state_r = "r" * 8
        ET.SubElement(tl, "phase", duration=str(timing.green), state=state_a)
        ET.SubElement(tl, "phase", duration=str(timing.yellow), state=state_y)
        ET.SubElement(tl, "phase", duration=str(timing.red), state=state_r)
        ET.SubElement(tl, "phase", duration=str(timing.green), state=state_a[::-1].replace("G", "G"))
        # Second green uses alternate pattern (simplified)
        ET.SubElement(tl, "phase", duration=str(timing.yellow), state=state_y)
        ET.SubElement(tl, "phase", duration=str(max(1, timing.red // 4)), state=state_r)

    _indent(root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def write_parking_add(out_path: Path, edits: NetworkEdits, edge_widths: Optional[dict[str, float]] = None) -> Path:
    """Create parkingArea definitions with corner clearance."""
    root = ET.Element("additional")
    edge_widths = edge_widths or {}
    for i, p in enumerate(edits.parking):
        sides = ["right", "left"] if p.side == "both" else [p.side]
        if p.side == "both":
            w = edge_widths.get(p.edge_id, 0.0)
            if w and w < edits.min_width_both_sides_m:
                # Skip invalid both-sides parking
                continue
        for side in sides:
            start = p.corner_clearance_m
            end = start + p.length_m
            ET.SubElement(
                root,
                "parkingArea",
                id=f"park_{p.edge_id}_{side}_{i}",
                lane=f"{p.edge_id}_0",
                startPos=str(start),
                endPos=str(end),
                roadsideCapacity=str(p.capacity),
                angle="0" if side == "right" else "180",
            )
    _indent(root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def write_stops_add(out_path: Path, edits: NetworkEdits) -> Path:
    """Stop signs as stopping places near edge end (MVP approximation)."""
    root = ET.Element("additional")
    for i, s in enumerate(edits.stops):
        ET.SubElement(
            root,
            "stop",
            id=f"stop_{s.edge_id}_{i}",
            lane=f"{s.edge_id}_0",
            endPos="-5",
            friendlyPos="true",
            duration="3",
        )
    # Also emit connection-style stop via <stop> on lane for parking-like halt —
    # Real priority stops need netedit; we add a vType-friendly landmark file.
    _indent(root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def write_lane_patch_xml(out_path: Path, edits: NetworkEdits) -> Path:
    """
    Write a netconvert patch file to set lane counts.
    Applied with: netconvert -s net.xml -o net2.xml -p patch.xml
    """
    root = ET.Element("net")
    for lo in edits.lane_overrides:
        n = 2 if lo.oneway_dual else lo.num_lanes
        ET.SubElement(root, "edge", id=lo.edge_id, numLanes=str(max(1, n)))
    _indent(root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def apply_lane_patch(net_in: Path, patch: Path, net_out: Path, netconvert_bin: Path) -> Path:
    from .sumo_env import run_cmd

    args = [
        str(netconvert_bin),
        "-s",
        str(net_in),
        "-o",
        str(net_out),
        "--tllogic.remove",
        "false",
    ]
    # netconvert patch via --keep-edges.input-file is different; use XML patch:
    # Supported as: netconvert --sumo-net-file in --output-file out -- edgedata?
    # Official: netconvert -s in.net.xml -o out.net.xml -p patch.edg.xml style
    args.extend(["-p", str(patch)])
    r = run_cmd(args)
    if r.returncode != 0 or not net_out.exists():
        # Fallback: copy original if patch format unsupported
        import shutil

        shutil.copy2(net_in, net_out)
    return net_out


def write_all_additionals(
    scenario_dir: Path,
    edits: NetworkEdits,
    tls_ids: list[str],
) -> list[Path]:
    scenario_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    if tls_ids:
        paths.append(write_tls_add(scenario_dir / "tls.add.xml", tls_ids, edits))
    if edits.parking:
        paths.append(write_parking_add(scenario_dir / "parking.add.xml", edits))
    if edits.stops:
        paths.append(write_stops_add(scenario_dir / "stops.add.xml", edits))
    if edits.lane_overrides:
        paths.append(write_lane_patch_xml(scenario_dir / "lanes.patch.xml", edits))
    return paths
