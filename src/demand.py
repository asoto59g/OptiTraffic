"""Traffic demand generation calibrated with TomTom edge factors."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .sumo_env import SumoEnv, detect_sumo, run_cmd
from .tomtom import traffic_level_to_demand_factor


def write_trips(
    out_path: Path,
    edge_ids: list[str],
    edge_levels: Optional[dict[str, float]] = None,
    base_vehs_per_hour: float = 120.0,
    begin: int = 0,
    end: int = 1800,
    max_trips: int = 5000,
) -> Path:
    """Write trips.xml with from/to pairs weighted by TomTom congestion."""
    edge_levels = edge_levels or {}
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        id="car",
        accel="2.6",
        decel="4.5",
        sigma="0.5",
        length="4.5",
        minGap="2.5",
        maxSpeed="13.9",
        jmIgnoreKeepClearTime="0",
    )

    n = len(edge_ids)
    if n == 0:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
        return out_path

    trip_id = 0
    duration = max(1, end - begin)
    for i, eid in enumerate(edge_ids):
        level = edge_levels.get(eid)
        factor = traffic_level_to_demand_factor(level, 1.0) if level is not None else 1.0
        rate = base_vehs_per_hour * factor
        n_trips = max(1, int(rate * duration / 3600.0))
        to_edge = edge_ids[(i + max(1, n // 7)) % n]
        if to_edge == eid and n > 1:
            to_edge = edge_ids[(i + 1) % n]
        for k in range(n_trips):
            depart = begin + (k * duration) / max(1, n_trips)
            trip = ET.Element("trip", id=f"t_{trip_id}", type="car", depart=f"{depart:.1f}")
            trip.set("from", eid)
            trip.set("to", to_edge)
            trip.set("departLane", "best")
            trip.set("departSpeed", "max")
            root.append(trip)
            trip_id += 1
            if trip_id >= max_trips:
                break
        if trip_id >= max_trips:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def run_duarouter(
    net_path: Path,
    trips_path: Path,
    routes_out: Path,
    sumo: Optional[SumoEnv] = None,
) -> Path:
    sumo = sumo or detect_sumo()
    if not sumo.home:
        raise RuntimeError("SUMO_HOME requerido para duarouter")

    import shutil
    from pathlib import Path as P

    candidates = [
        sumo.home / "bin" / "duarouter.exe",
        sumo.home / "bin" / "duarouter",
        P(shutil.which("duarouter") or ""),
    ]
    duarouter = next((c for c in candidates if c and c.exists()), None)
    if duarouter is None:
        raise FileNotFoundError("duarouter no encontrado")

    args = [
        str(duarouter),
        "-n",
        str(net_path),
        "-t",
        str(trips_path),
        "-o",
        str(routes_out),
        "--ignore-errors",
        "true",
        "--repair",
        "true",
    ]
    r = run_cmd(args, timeout=600)
    if r.returncode != 0 and not routes_out.exists():
        raise RuntimeError("duarouter falló:\n" + (r.stderr or r.stdout))
    return routes_out


def generate_demand(
    net_path: Path,
    work_dir: Path,
    edge_ids: list[str],
    edge_levels: Optional[dict[str, float]] = None,
    base_vehs_per_hour: float = 120.0,
    duration_s: int = 1800,
    sumo: Optional[SumoEnv] = None,
) -> Path:
    """Create trips + routed .rou.xml. Returns routes path."""
    work_dir.mkdir(parents=True, exist_ok=True)
    trips = work_dir / "trips.xml"
    routes = work_dir / "routes.rou.xml"
    write_trips(
        trips,
        edge_ids,
        edge_levels=edge_levels,
        base_vehs_per_hour=base_vehs_per_hour,
        begin=0,
        end=duration_s,
    )
    try:
        return run_duarouter(net_path, trips, routes, sumo=sumo)
    except Exception:
        return trips
