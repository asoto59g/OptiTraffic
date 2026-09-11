"""Run SUMO via TraCI and collect edge congestion KPIs."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .sumo_env import SumoEnv, detect_sumo, ensure_sumolib_on_path


@dataclass
class EdgeKPI:
    edge_id: str
    mean_speed: float = 0.0
    max_occupancy: float = 0.0
    mean_occupancy: float = 0.0
    waiting_time: float = 0.0
    sample_count: int = 0
    congested: bool = False


@dataclass
class SimResult:
    duration_s: int
    vehicle_steps: int
    total_waiting: float
    mean_speed: float
    pct_edges_congested: float
    edges: dict[str, EdgeKPI] = field(default_factory=dict)
    tomtom_correlation: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def write_sumocfg(
    cfg_path: Path,
    net_path: Path,
    routes_path: Path,
    additional_files: Optional[list[Path]] = None,
    begin: int = 0,
    end: int = 1800,
) -> Path:
    root = ET.Element("configuration")
    inp = ET.SubElement(root, "input")
    ET.SubElement(inp, "net-file", value=str(net_path).replace("\\", "/"))
    ET.SubElement(inp, "route-files", value=str(routes_path).replace("\\", "/"))
    if additional_files:
        joined = ",".join(str(p).replace("\\", "/") for p in additional_files)
        ET.SubElement(inp, "additional-files", value=joined)
    time = ET.SubElement(root, "time")
    ET.SubElement(time, "begin", value=str(begin))
    ET.SubElement(time, "end", value=str(end))
    proc = ET.SubElement(root, "processing")
    ET.SubElement(proc, "time-to-teleport", value="120")
    ET.SubElement(proc, "collision.action", value="warn")
    # Encourage keep-clear / not blocking junctions
    ET.SubElement(proc, "ignore-junction-blocker", value="0")

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(cfg_path, encoding="utf-8", xml_declaration=True)
    return cfg_path


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = sum((x - mx) ** 2 for x in xs) ** 0.5
    deny = sum((y - my) ** 2 for y in ys) ** 0.5
    if denx == 0 or deny == 0:
        return None
    return num / (denx * deny)


def run_simulation(
    cfg_path: Path,
    sumo: Optional[SumoEnv] = None,
    step_length: float = 1.0,
    speed_cong_threshold: float = 3.0,
    edge_levels: Optional[dict[str, float]] = None,
) -> SimResult:
    sumo = sumo or detect_sumo()
    if not sumo.ok or not sumo.sumo_bin:
        raise RuntimeError(sumo.message)
    ensure_sumolib_on_path(sumo.home)
    import traci

    cmd = [str(sumo.sumo_bin), "-c", str(cfg_path), "--start", "--quit-on-end", "true"]
    traci.start(cmd)

    edge_acc: dict[str, dict[str, float]] = {}
    vehicle_steps = 0
    total_waiting = 0.0
    speed_samples = 0.0
    speed_sum = 0.0
    end = int(traci.simulation.getEndTime()) if hasattr(traci.simulation, "getEndTime") else 1800

    try:
        while traci.simulation.getMinExpectedNumber() > 0 or traci.simulation.getTime() < end:
            traci.simulationStep()
            t = traci.simulation.getTime()
            if t > end:
                break
            veh_ids = traci.vehicle.getIDList()
            vehicle_steps += len(veh_ids)
            for vid in veh_ids:
                total_waiting += traci.vehicle.getWaitingTime(vid)
                speed_sum += traci.vehicle.getSpeed(vid)
                speed_samples += 1
                eid = traci.vehicle.getRoadID(vid)
                if eid.startswith(":"):
                    continue
                acc = edge_acc.setdefault(
                    eid, {"speed": 0.0, "occ": 0.0, "wait": 0.0, "n": 0.0, "max_occ": 0.0}
                )
                acc["speed"] += traci.vehicle.getSpeed(vid)
                acc["wait"] += traci.vehicle.getWaitingTime(vid)
                acc["n"] += 1

            # Sample occupancy every 10s
            if int(t) % 10 == 0:
                for eid in list(edge_acc.keys()):
                    try:
                        occ = traci.edge.getLastStepOccupancy(eid)
                    except traci.TraCIException:
                        continue
                    edge_acc[eid]["occ"] += occ
                    edge_acc[eid]["max_occ"] = max(edge_acc[eid]["max_occ"], occ)
    finally:
        traci.close()

    edges: dict[str, EdgeKPI] = {}
    congested = 0
    for eid, acc in edge_acc.items():
        n = max(1.0, acc["n"])
        mean_speed = acc["speed"] / n
        mean_occ = acc["occ"] / max(1.0, n / 10.0)
        kpi = EdgeKPI(
            edge_id=eid,
            mean_speed=mean_speed,
            max_occupancy=acc["max_occ"],
            mean_occupancy=mean_occ,
            waiting_time=acc["wait"],
            sample_count=int(n),
            congested=mean_speed < speed_cong_threshold and n > 5,
        )
        if kpi.congested:
            congested += 1
        edges[eid] = kpi

    corr = None
    if edge_levels:
        xs, ys = [], []
        for eid, level in edge_levels.items():
            if eid in edges:
                # Compare congestion: (1 - tomtom_level) vs (low speed)
                xs.append(1.0 - float(level))
                ys.append(max(0.0, 1.0 - edges[eid].mean_speed / 13.9))
        corr = _pearson(xs, ys)

    mean_speed = (speed_sum / speed_samples) if speed_samples else 0.0
    pct = (100.0 * congested / len(edges)) if edges else 0.0
    return SimResult(
        duration_s=int(end),
        vehicle_steps=vehicle_steps,
        total_waiting=total_waiting,
        mean_speed=mean_speed,
        pct_edges_congested=pct,
        edges=edges,
        tomtom_correlation=corr,
    )


def export_edge_csv(result: SimResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "edge_id",
                "mean_speed",
                "max_occupancy",
                "mean_occupancy",
                "waiting_time",
                "sample_count",
                "congested",
            ]
        )
        for e in result.edges.values():
            w.writerow(
                [
                    e.edge_id,
                    f"{e.mean_speed:.3f}",
                    f"{e.max_occupancy:.3f}",
                    f"{e.mean_occupancy:.3f}",
                    f"{e.waiting_time:.1f}",
                    e.sample_count,
                    int(e.congested),
                ]
            )
    return path


def export_edge_geojson(edges_gj: dict[str, Any], result: SimResult, path: Path) -> Path:
    feats = []
    for feat in edges_gj.get("features", []):
        eid = feat["properties"]["id"]
        props = dict(feat["properties"])
        kpi = result.edges.get(eid)
        if kpi:
            props.update(
                {
                    "mean_speed": kpi.mean_speed,
                    "congested": kpi.congested,
                    "max_occupancy": kpi.max_occupancy,
                    "waiting_time": kpi.waiting_time,
                }
            )
        feats.append({"type": "Feature", "properties": props, "geometry": feat["geometry"]})
    out = {"type": "FeatureCollection", "features": feats}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    return path
