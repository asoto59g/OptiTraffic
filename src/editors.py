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
    reason: str = ""  # e.g. "default_ns_calle_x_avenida"


@dataclass
class TlsPlacement:
    """User-confirmed traffic light at an intersection / edge (MVP)."""

    edge_id: str
    tls_id: Optional[str] = None  # existing SUMO TLS if known
    junction_id: Optional[str] = None
    name: str = ""


def _normalize_road_name(name: str) -> str:
    return (name or "").strip().lower()


def classify_road_role(name: str, bearing_deg: Optional[float] = None) -> str:
    """
    Return 'avenida' | 'calle' | 'other'.

    Costa Rica convention (Liberia / San José centro):
    - Avenidas ≈ east–west
    - Calles ≈ north–south
    Name wins when present; otherwise use bearing (0°=N, 90°=E).
    """
    n = _normalize_road_name(name)
    if n:
        if n.startswith("avenida") or n.startswith("av.") or n.startswith("av "):
            return "avenida"
        if "avenida" in n:
            return "avenida"
        if n.startswith("calle") or "calle" in n:
            return "calle"
    if bearing_deg is None:
        return "other"
    # Fold to [0, 180): 45–135 → E–W (avenida), else N–S (calle)
    b = abs(float(bearing_deg)) % 180.0
    if 45.0 <= b <= 135.0:
        return "avenida"
    return "calle"


def edge_bearing_deg(coords: list) -> Optional[float]:
    """Bearing from first to last coordinate (lon, lat), degrees clockwise from north."""
    import math

    if not coords or len(coords) < 2:
        return None
    lon0, lat0 = float(coords[0][0]), float(coords[0][1])
    lon1, lat1 = float(coords[-1][0]), float(coords[-1][1])
    return math.degrees(math.atan2(lon1 - lon0, lat1 - lat0)) % 360.0


def annotate_road_roles(edges_geojson: dict[str, Any]) -> dict[str, Any]:
    """Add properties: bearing, road_role (avenida/calle/other), axis (EW/NS)."""
    for feat in edges_geojson.get("features", []):
        props = feat.setdefault("properties", {})
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        bearing = edge_bearing_deg(coords)
        role = classify_road_role(str(props.get("name") or ""), bearing)
        props["bearing"] = None if bearing is None else round(float(bearing), 1)
        props["road_role"] = role
        if bearing is None:
            props["axis"] = ""
        else:
            b = abs(float(bearing)) % 180.0
            props["axis"] = "EW" if 45.0 <= b <= 135.0 else "NS"
    return edges_geojson


def default_stops_ns_at_avenues(
    edges_geojson: dict[str, Any],
    junctions: list[dict[str, Any]],
    *,
    min_degree: int = 3,
    skip_junction_ids: Optional[set[str]] = None,
) -> list[StopSign]:
    """
    Default Costa Rica centro rule:
    - Avenidas (E–O) have free passage at the intersection.
    - Calles (N–S) get a stop (alto) where they meet an avenida.

    Places StopSign on N–S / calle edges that arrive at (to=) a junction that also
    has at least one E–W / avenida edge. Skips TLS junctions if provided.
    """
    skip = skip_junction_ids or set()
    annotate_road_roles(edges_geojson)

    by_id: dict[str, dict[str, Any]] = {}
    for feat in edges_geojson.get("features", []):
        props = feat.get("properties") or {}
        eid = props.get("id")
        if eid:
            by_id[str(eid)] = props

    stops: list[StopSign] = []
    seen: set[tuple[str, str]] = set()

    for j in junctions:
        jid = str(j.get("id") or "")
        if not jid or jid in skip:
            continue
        if int(j.get("degree") or 0) < min_degree:
            continue
        edge_ids = [str(e) for e in (j.get("edges") or []) if e]
        roles = []
        for eid in edge_ids:
            props = by_id.get(eid) or {}
            role = props.get("road_role") or "other"
            axis = props.get("axis") or ""
            # Treat EW other as avenida-like, NS other as calle-like when name missing
            if role == "other":
                if axis == "EW":
                    role = "avenida"
                elif axis == "NS":
                    role = "calle"
            roles.append((eid, role, props))

        has_avenida = any(r == "avenida" for _, r, _ in roles)
        if not has_avenida:
            continue

        for eid, role, props in roles:
            if role != "calle":
                continue
            # Only approaches that end at this junction (incoming)
            if str(props.get("to") or "") != jid:
                continue
            key = (eid, jid)
            if key in seen:
                continue
            seen.add(key)
            stops.append(
                StopSign(
                    edge_id=eid,
                    junction_id=jid,
                    reason="default_calle_NS_x_avenida_EW",
                )
            )
    return stops


def merge_default_stops(edits: NetworkEdits, suggested: list[StopSign], replace_defaults: bool = True) -> int:
    """
    Merge suggested stops into edits.
    If replace_defaults, drop previous auto defaults (reason startswith default_) then add.
    Returns number of stops added.
    """
    if replace_defaults:
        edits.stops = [s for s in edits.stops if not (s.reason or "").startswith("default_")]
    existing = {(s.edge_id, s.junction_id) for s in edits.stops}
    added = 0
    for s in suggested:
        key = (s.edge_id, s.junction_id)
        if key in existing:
            continue
        # Also skip if same edge already has any stop
        if any(x.edge_id == s.edge_id for x in edits.stops):
            continue
        edits.stops.append(s)
        existing.add(key)
        added += 1
    return added


@dataclass
class NetworkEdits:
    tls_default: TlsTiming = field(default_factory=TlsTiming)
    tls_overrides: dict[str, TlsTiming] = field(default_factory=dict)
    lane_overrides: list[LaneOverride] = field(default_factory=list)
    parking: list[ParkingConfig] = field(default_factory=list)
    stops: list[StopSign] = field(default_factory=list)
    tls_placements: list[TlsPlacement] = field(default_factory=list)
    min_width_both_sides_m: float = 9.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tls_default": asdict(self.tls_default),
            "tls_overrides": {k: asdict(v) for k, v in self.tls_overrides.items()},
            "lane_overrides": [asdict(x) for x in self.lane_overrides],
            "parking": [asdict(x) for x in self.parking],
            "stops": [asdict(x) for x in self.stops],
            "tls_placements": [asdict(x) for x in self.tls_placements],
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
        edits.stops = [StopSign(**{k: x[k] for k in ("edge_id", "junction_id", "reason") if k in x}) for x in d.get("stops") or []]
        # Ensure reason default for legacy saves
        for s in edits.stops:
            if not hasattr(s, "reason"):
                s.reason = ""

        edits.tls_placements = [TlsPlacement(**x) for x in d.get("tls_placements") or []]
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


def _phase_kind(state: str) -> str:
    """Classify a SUMO TLS phase by its state string."""
    s = state or ""
    if any(c in "yY" for c in s):
        return "yellow"
    if any(c in "Gg" for c in s):
        return "green"
    return "red"


def normalize_tls_id(tls_id: Optional[str], junction_id: Optional[str] = None) -> str:
    """Map legacy tls_j_<junction> ids to the real SUMO TLS/junction id."""
    tid = (tls_id or "").strip()
    jid = (junction_id or "").strip()
    if tid.startswith("tls_j_") and len(tid) > 6:
        return tid[6:]
    if tid.startswith("tls_") and jid:
        return jid
    if tid:
        return tid
    return jid


def collect_tls_junction_ids(edits: NetworkEdits, osm_tls_ids: Optional[list[str]] = None) -> list[str]:
    """Junction / TLS ids that must be traffic_light in the simulation net."""
    ids: list[str] = []
    for p in edits.tls_placements:
        nid = normalize_tls_id(p.tls_id, p.junction_id)
        if nid:
            ids.append(nid)
        elif p.junction_id:
            ids.append(str(p.junction_id))
    for tid in edits.tls_overrides.keys():
        nid = normalize_tls_id(tid)
        if nid:
            ids.append(nid)
    for tid in osm_tls_ids or []:
        if tid:
            ids.append(str(tid))
    # Preserve order, unique
    return list(dict.fromkeys(ids))


def ensure_tls_junctions(
    net_in: Path,
    net_out: Path,
    junction_ids: list[str],
    netconvert_bin: Path,
) -> Path:
    """Force named junctions to be controlled by traffic lights via netconvert."""
    import shutil

    from .sumo_env import run_cmd

    net_out.parent.mkdir(parents=True, exist_ok=True)
    ids = [j for j in dict.fromkeys(junction_ids) if j]
    if not ids:
        if Path(net_in).resolve() != Path(net_out).resolve():
            shutil.copy2(net_in, net_out)
        return net_out

    args = [
        str(netconvert_bin),
        "-s",
        str(net_in),
        "-o",
        str(net_out),
        "--tls.set",
        ",".join(ids),
        "--no-turnarounds.except-deadend",
        "true",
    ]
    r = run_cmd(args, timeout=900)
    if r.returncode != 0 or not net_out.is_file():
        detail = (r.stderr or r.stdout or "")[:600]
        raise RuntimeError(
            f"netconvert --tls.set falló al crear semáforos en {ids[:12]}: {detail}"
        )
    return net_out


def apply_stop_signs_to_net(
    net_in: Path,
    net_out: Path,
    stops: list[StopSign],
    *,
    edges_gj: Optional[dict[str, Any]] = None,
    netconvert_bin: Optional[Path] = None,
    skip_junction_ids: Optional[set[str]] = None,
) -> Path:
    """
    Encode real stop behaviour in the network:
    - Junctions with altos (and without TLS) → type=priority_stop
    - Stop edges get low priority; crossing avenidas get high priority
    Then rebuild with netconvert so right-of-way is recalculated.
    """
    import shutil

    if not stops:
        if Path(net_in).resolve() != Path(net_out).resolve():
            shutil.copy2(net_in, net_out)
        return net_out

    skip = {str(x) for x in (skip_junction_ids or set())}
    role_by_edge: dict[str, str] = {}
    if edges_gj:
        for feat in edges_gj.get("features", []):
            props = feat.get("properties") or {}
            eid = props.get("id")
            if eid is not None:
                role_by_edge[str(eid)] = str(props.get("road_role") or "")

    tree = ET.parse(net_in)
    root = tree.getroot()

    stop_edges = {str(s.edge_id) for s in stops if s.edge_id}
    stop_jids: set[str] = set()
    for s in stops:
        if s.junction_id:
            stop_jids.add(str(s.junction_id))
        else:
            # Infer junction = edge "to" node from net
            for edge in root.findall("edge"):
                if edge.get("id") == s.edge_id and not str(edge.get("id", "")).startswith(":"):
                    to_n = edge.get("to")
                    if to_n:
                        stop_jids.add(str(to_n))

    # Raise priority on avenidas that meet stop junctions; lower on stop approaches.
    for edge in root.findall("edge"):
        eid = edge.get("id") or ""
        if eid.startswith(":"):
            continue
        to_n = edge.get("to") or ""
        frm = edge.get("from") or ""
        touches = to_n in stop_jids or frm in stop_jids
        if not touches:
            continue
        role = role_by_edge.get(eid, "")
        if eid in stop_edges:
            edge.set("priority", "1")
        elif role == "avenida":
            edge.set("priority", "12")
        elif role == "calle":
            edge.set("priority", "2")
        else:
            # Unlabelled edge ending at stop junction: treat as minor if it is a stop edge only
            try:
                cur = int(float(edge.get("priority") or "1"))
            except ValueError:
                cur = 1
            if eid in stop_edges:
                edge.set("priority", "1")
            elif cur < 9:
                edge.set("priority", str(max(cur, 8)))

    for junc in root.findall("junction"):
        jid = junc.get("id") or ""
        if jid not in stop_jids or jid in skip:
            continue
        jtype = (junc.get("type") or "").lower()
        if jtype in ("traffic_light", "traffic_light_right_on_red", "traffic_light_unregulated"):
            continue  # TLS wins over stop
        junc.set("type", "priority_stop")

    patched = net_out.with_suffix(".stops_patch.net.xml")
    tree.write(patched, encoding="utf-8", xml_declaration=True)

    if netconvert_bin is None:
        shutil.move(str(patched), str(net_out))
        return net_out

    # Rebuild connections / right-of-way from patched priorities + junction types.
    from .sumo_env import run_cmd

    args = [
        str(netconvert_bin),
        "-s",
        str(patched),
        "-o",
        str(net_out),
        "--no-turnarounds.except-deadend",
        "true",
    ]
    r = run_cmd(args, timeout=900)
    try:
        patched.unlink(missing_ok=True)
    except OSError:
        pass
    if r.returncode != 0 or not net_out.is_file():
        detail = (r.stderr or r.stdout or "")[:600]
        raise RuntimeError(f"netconvert al aplicar altos (priority_stop) falló: {detail}")
    return net_out


def prepare_sim_network(
    net_in: Path,
    net_out: Path,
    edits: NetworkEdits,
    *,
    osm_tls_ids: Optional[list[str]] = None,
    edges_gj: Optional[dict[str, Any]] = None,
    netconvert_bin: Optional[Path] = None,
) -> tuple[Path, list[str]]:
    """
    Build a simulation net where:
    - confirmed / OSM junctions are real traffic lights
    - configured altos are priority_stop with correct edge priorities
    Returns (net_path, tls_ids_to_program).
    """
    import shutil
    import tempfile

    from .sumo_env import detect_sumo

    sumo = detect_sumo()
    nconv = netconvert_bin or sumo.netconvert_bin
    if not nconv:
        raise RuntimeError("netconvert no disponible para preparar semáforos/altos.")

    net_out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="optitraffic_net_"))
    try:
        step1 = work / "tls.net.xml"
        tls_jids = collect_tls_junction_ids(edits, osm_tls_ids)
        ensure_tls_junctions(net_in, step1, tls_jids, nconv)

        step2 = work / "stops.net.xml"
        apply_stop_signs_to_net(
            step1,
            step2,
            edits.stops,
            edges_gj=edges_gj,
            netconvert_bin=nconv,
            skip_junction_ids=set(tls_jids),
        )
        shutil.copy2(step2, net_out)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # Normalize override keys to real SUMO ids
    remapped: dict[str, TlsTiming] = {}
    for tid, timing in edits.tls_overrides.items():
        remapped[normalize_tls_id(tid)] = timing
    for p in edits.tls_placements:
        nid = normalize_tls_id(p.tls_id, p.junction_id)
        if nid and nid not in remapped:
            remapped[nid] = edits.tls_default
        if p.tls_id != nid and nid:
            p.tls_id = nid
    edits.tls_overrides = {k: v for k, v in remapped.items() if k}

    programs = read_tls_programs_from_net(net_out)
    tls_ids = list(dict.fromkeys([*tls_jids, *programs.keys(), *edits.tls_overrides.keys()]))
    # Bake G/Y/R into the net's programID=0 (SUMO rejects a duplicate program 0 in additionals)
    apply_tls_timings_to_net(net_out, edits, tls_ids)
    return net_out, tls_ids


def read_tls_programs_from_net(net_path: Path) -> dict[str, list[tuple[int, str]]]:
    """
    Return {tls_id: [(duration, state), ...]} from the first program of each TLS in the .net.xml.
    Phase *state* length must match the number of controlled links — inventing GGGG… crashes SUMO.
    """
    out: dict[str, list[tuple[int, str]]] = {}
    if not net_path or not Path(net_path).is_file():
        return out
    for _event, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag != "tlLogic":
            continue
        tid = elem.get("id")
        if not tid or tid in out:
            elem.clear()
            continue
        phases: list[tuple[int, str]] = []
        for p in elem.findall("phase"):
            state = p.get("state") or ""
            try:
                dur = int(float(p.get("duration") or "1"))
            except ValueError:
                dur = 1
            if state:
                phases.append((max(1, dur), state))
        if phases:
            out[tid] = phases
        elem.clear()
    return out


def _timing_for_tls(edits: NetworkEdits, tls_id: str) -> TlsTiming:
    real = normalize_tls_id(tls_id) or tls_id
    return (
        edits.tls_overrides.get(real)
        or edits.tls_overrides.get(tls_id)
        or edits.tls_default
    )


def apply_tls_timings_to_net(
    net_path: Path,
    edits: NetworkEdits,
    tls_ids: Optional[list[str]] = None,
) -> int:
    """
    Rewrite phase durations on existing tlLogic programID=0 inside the .net.xml.
    Keeps phase states (link count). Returns number of TLS programs updated.
    """
    if not net_path or not Path(net_path).is_file():
        return 0

    want: Optional[set[str]] = None
    if tls_ids is not None:
        want = {normalize_tls_id(t) or t for t in tls_ids if t}
        want |= set(edits.tls_overrides.keys())

    tree = ET.parse(net_path)
    root = tree.getroot()
    updated = 0
    seen: set[str] = set()
    for tl in root.findall("tlLogic"):
        tid = tl.get("id") or ""
        if not tid or tid in seen:
            continue
        pid = tl.get("programID") or "0"
        if pid not in ("0", ""):
            # Only patch the active/default program
            continue
        if want is not None and tid not in want and normalize_tls_id(tid) not in want:
            continue
        seen.add(tid)
        timing = _timing_for_tls(edits, tid)
        phases = list(tl.findall("phase"))
        if not phases:
            continue
        for p in phases:
            state = p.get("state") or ""
            kind = _phase_kind(state)
            if kind == "green":
                p.set("duration", str(max(1, int(timing.green))))
            elif kind == "yellow":
                p.set("duration", str(max(1, int(timing.yellow))))
            else:
                p.set("duration", str(max(1, int(timing.red))))
        updated += 1

    if updated:
        tree.write(net_path, encoding="utf-8", xml_declaration=True)
    return updated


def write_tls_add(
    out_path: Path,
    tls_ids: list[str],
    edits: NetworkEdits,
    net_path: Optional[Path] = None,
) -> Path:
    """
    Document TLS timings. When net_path is set, timings are already baked into
    sim.net.xml via apply_tls_timings_to_net — do NOT emit another programID=0
    (SUMO errors: \"Another logic with id … and programID '0' exists\").
    """
    root = ET.Element("additional")
    if net_path and Path(net_path).is_file():
        n = apply_tls_timings_to_net(Path(net_path), edits, tls_ids)
        ids = ", ".join(sorted({normalize_tls_id(t) or t for t in tls_ids if t})[:30])
        root.append(
            ET.Comment(
                f"TLS timings applied in net-file ({n} programs). "
                f"Ids: {ids}. No duplicate tlLogic here (avoids programID clash)."
            )
        )
    else:
        # Offline / no net: cannot invent valid phase states — comment only
        root.append(
            ET.Comment(
                "No net-file supplied; TLS timings must be applied via "
                "prepare_sim_network / apply_tls_timings_to_net."
            )
        )

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
    """
    Document configured altos. Real stop behaviour is applied in the .net.xml
    via prepare_sim_network / apply_stop_signs_to_net (priority_stop + priorities).
    Do not emit fake lane <stop duration=…> landmarks — they do not enforce ROW.
    """
    root = ET.Element("additional")
    n = len(edits.stops)
    ids = ", ".join(
        f"{s.edge_id}" + (f"@{s.junction_id}" if s.junction_id else "")
        for s in edits.stops[:40]
    )
    more = f" … (+{n - 40})" if n > 40 else ""
    root.append(
        ET.Comment(
            f"Altos aplicados en la red (priority_stop): {n}. "
            f"Edges: {ids}{more}"
            if n
            else "Sin altos configurados."
        )
    )
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
    net_path: Optional[Path] = None,
) -> list[Path]:
    scenario_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    # Include user-confirmed placements (real junction / TLS id, not tls_j_* aliases)
    placement_ids = []
    for p in edits.tls_placements:
        tid = normalize_tls_id(p.tls_id, p.junction_id) or (p.junction_id or "")
        if not tid:
            continue
        if p.tls_id != tid:
            p.tls_id = tid
        placement_ids.append(tid)
        if tid not in edits.tls_overrides:
            edits.tls_overrides[tid] = edits.tls_default
    # Remap legacy override keys
    remapped: dict[str, TlsTiming] = {}
    for tid, timing in edits.tls_overrides.items():
        remapped[normalize_tls_id(tid) or tid] = timing
    edits.tls_overrides = {k: v for k, v in remapped.items() if k}

    all_tls = list(
        dict.fromkeys(
            [
                *(normalize_tls_id(t) or t for t in tls_ids),
                *placement_ids,
                *edits.tls_overrides.keys(),
            ]
        )
    )
    if all_tls:
        paths.append(
            write_tls_add(scenario_dir / "tls.add.xml", all_tls, edits, net_path=net_path)
        )
    if edits.parking:
        paths.append(write_parking_add(scenario_dir / "parking.add.xml", edits))
    if edits.stops:
        paths.append(write_stops_add(scenario_dir / "stops.add.xml", edits))
    if edits.lane_overrides:
        paths.append(write_lane_patch_xml(scenario_dir / "lanes.patch.xml", edits))
    return paths
