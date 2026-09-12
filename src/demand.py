"""Traffic demand generation calibrated with TomTom edge factors."""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .flow_gates import FlowGate, partition_gates
from .sumo_env import SumoEnv, detect_sumo, run_cmd
from .tomtom import traffic_level_to_demand_factor
from .traffic_params import (
    CITY_MAX_SPEED_MS,
    MIN_GAP_M,
    VEH_ACCEL,
    VEH_DECEL,
    VEH_LENGTH_M,
    VEH_SIGMA,
    VEH_TAU,
    desired_speed_ms,
    spatial_flow_cap_vph,
)


@dataclass(frozen=True)
class DensityScenario:
    """Urban corridor volume bands (veh/h). SUMO edges are one-way → use per_dir_*."""

    key: str
    label: str
    per_dir_min: int
    per_dir_max: int
    per_dir_default: int
    both_min: int
    both_max: int

    @property
    def caption(self) -> str:
        return (
            f"{self.label}: {self.per_dir_min}–{self.per_dir_max} veh/h por sentido "
            f"(≈ {self.both_min}–{self.both_max} ambos sentidos)"
        )


# Topes de densidad urbana (referencia de corredor).
DENSITY_SCENARIOS: dict[str, DensityScenario] = {
    "bajo": DensityScenario(
        key="bajo",
        label="Bajo",
        per_dir_min=150,
        per_dir_max=200,
        per_dir_default=175,
        both_min=300,
        both_max=400,
    ),
    "medio": DensityScenario(
        key="medio",
        label="Medio",
        per_dir_min=250,
        per_dir_max=350,
        per_dir_default=300,
        both_min=500,
        both_max=700,
    ),
    "alto": DensityScenario(
        key="alto",
        label="Alto",
        per_dir_min=400,
        per_dir_max=500,
        per_dir_default=450,
        both_min=800,
        both_max=1000,
    ),
}


def get_density_scenario(key: str) -> DensityScenario:
    return DENSITY_SCENARIOS.get(key, DENSITY_SCENARIOS["medio"])


def _edge_meta_from_geojson(edges_gj: Optional[dict]) -> dict[str, tuple[float, float]]:
    """id -> (length_m, lanes)."""
    out: dict[str, tuple[float, float]] = {}
    if not edges_gj:
        return out
    for feat in edges_gj.get("features", []):
        props = feat.get("properties") or {}
        eid = props.get("id")
        if not eid:
            continue
        try:
            length = float(props.get("length") or 50.0)
        except (TypeError, ValueError):
            length = 50.0
        try:
            lanes = float(props.get("lanes") or 1.0)
        except (TypeError, ValueError):
            lanes = 1.0
        out[str(eid)] = (max(5.0, length), max(1.0, lanes))
    return out


def _append_vtype(root: ET.Element, city_max_speed_ms: float) -> None:
    ET.SubElement(
        root,
        "vType",
        id="car",
        accel=str(VEH_ACCEL),
        decel=str(VEH_DECEL),
        sigma=str(VEH_SIGMA),
        tau=str(VEH_TAU),
        length=str(VEH_LENGTH_M),
        minGap=str(MIN_GAP_M),
        maxSpeed=f"{city_max_speed_ms:.3f}",
        speedFactor="normc(0.90,0.10,0.70,1.00)",
        jmIgnoreKeepClearTime="0",
    )


def _cap_rate(
    rate: float,
    eid: str,
    *,
    scenario_cap: Optional[float],
    meta: dict[str, tuple[float, float]],
    edge_levels: dict[str, float],
    city_max_speed_ms: float,
) -> float:
    if scenario_cap is not None:
        rate = min(rate, scenario_cap)
    length_m, lanes = meta.get(eid, (60.0, 1.0))
    level = edge_levels.get(eid)
    phys_cap = spatial_flow_cap_vph(
        edge_length_m=length_m,
        lanes=lanes,
        vmax_ms=city_max_speed_ms,
        traffic_level=level,
    )
    return min(rate, phys_cap)


def _pick_weighted(ids: list[str], weights: list[float], rng: random.Random) -> str:
    if not ids:
        raise ValueError("empty pick list")
    if len(ids) == 1:
        return ids[0]
    total = sum(weights) or float(len(weights))
    r = rng.random() * total
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if r <= acc:
            return ids[i]
    return ids[-1]


def write_trips(
    out_path: Path,
    edge_ids: list[str],
    edge_levels: Optional[dict[str, float]] = None,
    base_vehs_per_hour: float = 300.0,
    begin: int = 0,
    end: int = 1800,
    max_vehs_per_hour: Optional[float] = None,
    max_trips: int = 20000,
    edges_gj: Optional[dict] = None,
    city_max_speed_ms: float = CITY_MAX_SPEED_MS,
) -> Path:
    """Write trips.xml with from/to pairs weighted by TomTom congestion.

    Rates are per SUMO edge (one direction). TomTom scales demand but rates are
    hard-capped by scenario tope AND physical space (veh length 5 m + gap, 40 km/h).
    """
    edge_levels = edge_levels or {}
    scenario_cap = float(max_vehs_per_hour) if max_vehs_per_hour is not None else None
    meta = _edge_meta_from_geojson(edges_gj)
    root = ET.Element("routes")
    _append_vtype(root, city_max_speed_ms)

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
        rate = _cap_rate(
            float(base_vehs_per_hour) * factor,
            eid,
            scenario_cap=scenario_cap,
            meta=meta,
            edge_levels=edge_levels,
            city_max_speed_ms=city_max_speed_ms,
        )

        n_trips = max(1, int(rate * duration / 3600.0))
        to_edge = edge_ids[(i + max(1, n // 7)) % n]
        if to_edge == eid and n > 1:
            to_edge = edge_ids[(i + 1) % n]

        v_des = desired_speed_ms(level, city_max_speed_ms)
        speed_factor = max(0.15, min(1.0, v_des / city_max_speed_ms))

        for k in range(n_trips):
            depart = begin + (k * duration) / max(1, n_trips)
            trip = ET.Element(
                "trip",
                id=f"t_{trip_id}",
                type="car",
                depart=f"{depart:.1f}",
            )
            trip.set("from", eid)
            trip.set("to", to_edge)
            trip.set("departLane", "best")
            trip.set("departSpeed", "avg")
            trip.set("speedFactor", f"{speed_factor:.3f}")
            root.append(trip)
            trip_id += 1
            if trip_id >= max_trips:
                break
        if trip_id >= max_trips:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def write_gate_trips(
    out_path: Path,
    gates: Sequence[FlowGate],
    all_edge_ids: list[str],
    edge_levels: Optional[dict[str, float]] = None,
    begin: int = 0,
    end: int = 3600,
    max_vehs_per_hour: Optional[float] = None,
    max_trips: int = 40000,
    edges_gj: Optional[dict] = None,
    city_max_speed_ms: float = CITY_MAX_SPEED_MS,
    internal_fraction: float = 0.12,
    base_internal_vph: float = 80.0,
    seed: int = 42,
) -> Path:
    """
    OD demand from entry → exit gates (plus a small internal background).

    - Entries inject vehicles at their veh/h into the network.
    - Destinations prefer exits (weighted by exit veh/h); if no exits, random
      distant edges.
    - Exits alone: origins sampled from network → each exit.
    """
    edge_levels = edge_levels or {}
    scenario_cap = float(max_vehs_per_hour) if max_vehs_per_hour is not None else None
    meta = _edge_meta_from_geojson(edges_gj)
    entries, exits = partition_gates(gates)
    rng = random.Random(seed)
    root = ET.Element("routes")
    _append_vtype(root, city_max_speed_ms)

    duration = max(1, end - begin)
    trip_id = 0
    gate_edge_ids = {g.edge_id for g in gates}
    pool = [e for e in all_edge_ids if e not in gate_edge_ids] or list(all_edge_ids)

    def _speed_factor_for(eid: str) -> float:
        level = edge_levels.get(eid)
        v_des = desired_speed_ms(level, city_max_speed_ms)
        return max(0.15, min(1.0, v_des / city_max_speed_ms))

    def _add_trip(frm: str, to: str, depart: float) -> None:
        nonlocal trip_id
        if frm == to:
            return
        trip = ET.Element(
            "trip",
            id=f"g_{trip_id}",
            type="car",
            depart=f"{depart:.1f}",
        )
        trip.set("from", frm)
        trip.set("to", to)
        trip.set("departLane", "best")
        trip.set("departSpeed", "avg")
        trip.set("speedFactor", f"{_speed_factor_for(frm):.3f}")
        root.append(trip)
        trip_id += 1

    exit_ids = [g.edge_id for g in exits]
    exit_w = [max(1.0, float(g.vehs_per_hour)) for g in exits]

    # Entry → exit (or internal pool)
    for ent in entries:
        rate = _cap_rate(
            float(ent.vehs_per_hour),
            ent.edge_id,
            scenario_cap=scenario_cap,
            meta=meta,
            edge_levels=edge_levels,
            city_max_speed_ms=city_max_speed_ms,
        )
        n_trips = max(1, int(rate * duration / 3600.0))
        for k in range(n_trips):
            depart = begin + (k * duration) / max(1, n_trips)
            if exit_ids:
                to = _pick_weighted(exit_ids, exit_w, rng)
            else:
                to = rng.choice(pool)
            _add_trip(ent.edge_id, to, depart)
            if trip_id >= max_trips:
                break
        if trip_id >= max_trips:
            break

    # Exit-only: pull from internal pool toward exits
    if exits and not entries and trip_id < max_trips:
        for ex in exits:
            rate = _cap_rate(
                float(ex.vehs_per_hour),
                ex.edge_id,
                scenario_cap=scenario_cap,
                meta=meta,
                edge_levels=edge_levels,
                city_max_speed_ms=city_max_speed_ms,
            )
            n_trips = max(1, int(rate * duration / 3600.0))
            for k in range(n_trips):
                depart = begin + (k * duration) / max(1, n_trips)
                frm = rng.choice(pool)
                _add_trip(frm, ex.edge_id, depart)
                if trip_id >= max_trips:
                    break
            if trip_id >= max_trips:
                break

    # Light internal background so the grid is not empty between corridors
    if pool and internal_fraction > 0 and trip_id < max_trips:
        n_int = max(
            0,
            int(base_internal_vph * internal_fraction * duration / 3600.0 * max(1, len(entries) + len(exits))),
        )
        n_int = min(n_int, max(0, max_trips - trip_id))
        for k in range(n_int):
            frm = rng.choice(pool)
            to = rng.choice(pool)
            depart = begin + (k * duration) / max(1, n_int)
            _add_trip(frm, to, depart)

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

    # Prefer destinations that keep routing honest: duarouter will not traverse
    # against OSM one-way (SUMO edges are directed). Avoid inventing reverse edges.
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
        "--remove-loops",
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
    base_vehs_per_hour: float = 300.0,
    duration_s: int = 1800,
    max_vehs_per_hour: Optional[float] = None,
    edges_gj: Optional[dict] = None,
    sumo: Optional[SumoEnv] = None,
    flow_gates: Optional[Sequence[FlowGate]] = None,
) -> Path:
    """Create trips + routed .rou.xml. Returns routes path.

    If flow_gates is non-empty, uses entry/exit OD demand; otherwise legacy
    seed-edge demand.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    trips = work_dir / "trips.xml"
    routes = work_dir / "routes.rou.xml"
    gates = list(flow_gates or [])
    if gates:
        write_gate_trips(
            trips,
            gates,
            edge_ids,
            edge_levels=edge_levels,
            begin=0,
            end=duration_s,
            max_vehs_per_hour=max_vehs_per_hour,
            edges_gj=edges_gj,
        )
    else:
        write_trips(
            trips,
            edge_ids,
            edge_levels=edge_levels,
            base_vehs_per_hour=base_vehs_per_hour,
            begin=0,
            end=duration_s,
            max_vehs_per_hour=max_vehs_per_hour,
            edges_gj=edges_gj,
        )
    try:
        return run_duarouter(net_path, trips, routes, sumo=sumo)
    except Exception:
        return trips
