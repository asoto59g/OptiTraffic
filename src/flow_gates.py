"""Entry/exit flow gates for OD demand (vehicles entering/leaving the study area)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional, Sequence

from .logging_config import get_logger

log = get_logger("flow_gates")

GateKind = Literal["entry", "exit"]


@dataclass
class FlowGate:
    edge_id: str
    kind: GateKind
    vehs_per_hour: float = 400.0
    name: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    sentido: str = ""  # "unico" | "doble" | ""
    pair_edge_id: str = ""  # opposite SUMO edge when two-way

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FlowGate":
        kind = str(d.get("kind") or "entry").lower()
        if kind not in ("entry", "exit"):
            kind = "entry"
        return cls(
            edge_id=str(d["edge_id"]),
            kind=kind,  # type: ignore[arg-type]
            vehs_per_hour=float(d.get("vehs_per_hour") or 400.0),
            name=str(d.get("name") or ""),
            lat=float(d["lat"]) if d.get("lat") is not None else None,
            lon=float(d["lon"]) if d.get("lon") is not None else None,
            sentido=str(d.get("sentido") or ""),
            pair_edge_id=str(d.get("pair_edge_id") or ""),
        )


def gates_to_list(gates: Sequence[FlowGate]) -> list[dict[str, Any]]:
    return [g.to_dict() for g in gates]


def gates_from_list(raw: Optional[Sequence[dict[str, Any]]]) -> list[FlowGate]:
    out: list[FlowGate] = []
    for item in raw or []:
        try:
            out.append(FlowGate.from_dict(item))
        except Exception:
            log.warning("Invalid flow gate payload skipped: %s", item, exc_info=True)
            continue
    return out


def upsert_gate(gates: list[FlowGate], gate: FlowGate) -> list[FlowGate]:
    """Replace same edge_id or append. An edge is either entry or exit, not both."""
    rest = [g for g in gates if g.edge_id != gate.edge_id]
    rest.append(gate)
    return rest


def remove_gate(gates: list[FlowGate], edge_id: str) -> list[FlowGate]:
    return [g for g in gates if g.edge_id != edge_id]


def remove_gates(gates: list[FlowGate], edge_ids: Sequence[str]) -> list[FlowGate]:
    drop = {str(e) for e in edge_ids}
    return [g for g in gates if g.edge_id not in drop]


def partition_gates(gates: Sequence[FlowGate]) -> tuple[list[FlowGate], list[FlowGate]]:
    entries = [g for g in gates if g.kind == "entry"]
    exits = [g for g in gates if g.kind == "exit"]
    return entries, exits


def opposite_edge_id(edge_id: str) -> str:
    s = str(edge_id)
    return s[1:] if s.startswith("-") else f"-{s}"


def undirected_key(edge_id: str) -> str:
    s = str(edge_id)
    return s[1:] if s.startswith("-") else s


def edge_midpoint(edges_gj: dict[str, Any], edge_id: str) -> Optional[tuple[float, float]]:
    """Return (lat, lon) midpoint of edge, or None."""
    feat = _edge_feature(edges_gj, edge_id)
    if not feat:
        return None
    coords = (feat.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    mid = coords[len(coords) // 2]
    return float(mid[1]), float(mid[0])


def _edge_feature(edges_gj: dict[str, Any], edge_id: str) -> Optional[dict[str, Any]]:
    for feat in edges_gj.get("features", []):
        props = feat.get("properties") or {}
        if str(props.get("id")) == str(edge_id):
            return feat
    return None


def _edge_endpoints(feat: dict[str, Any]) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
    """((lon0, lat0), (lon1, lat1)) travel direction along the edge."""
    coords = (feat.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    a = (float(coords[0][0]), float(coords[0][1]))
    b = (float(coords[-1][0]), float(coords[-1][1]))
    return a, b


def _classify_vs_centroid(
    start_lonlat: tuple[float, float],
    end_lonlat: tuple[float, float],
    centroid_lonlat: tuple[float, float],
) -> GateKind:
    """If travel goes toward the city centroid → entry; away → exit."""
    cx, cy = centroid_lonlat
    d0 = (start_lonlat[0] - cx) ** 2 + (start_lonlat[1] - cy) ** 2
    d1 = (end_lonlat[0] - cx) ** 2 + (end_lonlat[1] - cy) ** 2
    return "entry" if d1 < d0 else "exit"


def suggest_boundary_gates(
    edges_gj: dict[str, Any],
    polygon,
    *,
    max_corridors: int = 8,
    default_vph: float = 400.0,
    buffer_deg: float = 0.0012,
) -> list[FlowGate]:
    """
    Suggest entry/exit candidates near the study polygon boundary.

    - Classifies by travel direction toward/away from polygon centroid.
    - Two-way (doble sentido): both SUMO edges — one entry, one exit — with
      flow split ~50/50.
    - One-way: single gate according to direction into/out of the city.
    """
    from shapely.geometry import Point

    try:
        boundary = polygon.boundary
        c = polygon.centroid
        centroid = (float(c.x), float(c.y))
    except Exception:
        return []

    # Index features by id
    by_id: dict[str, dict[str, Any]] = {}
    for feat in edges_gj.get("features", []):
        props = feat.get("properties") or {}
        eid = props.get("id")
        if eid is not None:
            by_id[str(eid)] = feat

    # Candidate edges near boundary
    near: list[tuple[float, str]] = []
    for eid, feat in by_id.items():
        ends = _edge_endpoints(feat)
        if not ends:
            continue
        (lon0, lat0), (lon1, lat1) = ends
        mid_lon = 0.5 * (lon0 + lon1)
        mid_lat = 0.5 * (lat0 + lat1)
        try:
            d = boundary.distance(Point(mid_lon, mid_lat))
        except Exception:
            continue
        if d <= buffer_deg:
            near.append((d, eid))

    if not near:
        return []

    # Group into undirected corridors
    groups: dict[str, list[tuple[float, str]]] = {}
    for d, eid in near:
        groups.setdefault(undirected_key(eid), []).append((d, eid))

    corridors: list[tuple[float, list[FlowGate]]] = []
    for key, members in groups.items():
        members.sort(key=lambda x: x[0])
        best_d = members[0][0]
        eids = [eid for _, eid in members]
        # Prefer including the opposite if it exists in the network (even if
        # slightly farther from boundary)
        opp_candidates = []
        for eid in list(eids):
            opp = opposite_edge_id(eid)
            if opp in by_id and opp not in eids:
                opp_candidates.append(opp)
        eids = list(dict.fromkeys(eids + opp_candidates))

        feat0 = by_id[eids[0]]
        props0 = feat0.get("properties") or {}
        name = str(props0.get("name") or "")
        is_oneway = props0.get("oneway") is True or props0.get("sentido") == "unico"
        # If opposite exists in net → treat as two-way corridor
        has_pair = any(opposite_edge_id(e) in by_id for e in eids)
        if has_pair:
            is_oneway = False

        role = str(props0.get("road_role") or "").lower()
        # Prefer arterials / named roads (lower score = better)
        pref = 0.0
        if role == "avenida" or "avenida" in name.lower() or name.lower().startswith("av"):
            pref -= 0.0004
        elif not name:
            pref += 0.0003

        gates_for: list[FlowGate] = []
        if is_oneway or len(eids) == 1:
            eid = eids[0]
            feat = by_id[eid]
            ends = _edge_endpoints(feat)
            if not ends:
                continue
            kind = _classify_vs_centroid(ends[0], ends[1], centroid)
            mid = edge_midpoint(edges_gj, eid)
            # Slight offset so stacked markers are readable
            lat = mid[0] if mid else None
            lon = mid[1] if mid else None
            gates_for.append(
                FlowGate(
                    edge_id=eid,
                    kind=kind,
                    vehs_per_hour=float(default_vph),
                    name=name or eid,
                    lat=lat,
                    lon=lon,
                    sentido="unico",
                    pair_edge_id="",
                )
            )
        else:
            # Two directed edges: assign entry/exit by direction; split flow
            half = max(50.0, float(default_vph) * 0.5)
            seen_kinds: set[str] = set()
            primary = undirected_key(eids[0])
            pair_ids = [primary, opposite_edge_id(primary)]
            pair_ids = [e for e in pair_ids if e in by_id]
            if len(pair_ids) < 2:
                pair_ids = eids[:2]

            for eid in pair_ids:
                feat = by_id[eid]
                ends = _edge_endpoints(feat)
                if not ends:
                    continue
                kind = _classify_vs_centroid(ends[0], ends[1], centroid)
                # Avoid two of the same kind on a corridor
                if kind in seen_kinds and len(pair_ids) == 2:
                    kind = "exit" if kind == "entry" else "entry"
                seen_kinds.add(kind)
                mid = edge_midpoint(edges_gj, eid)
                lat = mid[0] if mid else None
                lon = mid[1] if mid else None
                # Offset exit slightly so both markers show
                if kind == "exit" and lat is not None and lon is not None:
                    lat += 0.00012
                    lon += 0.00012
                other = opposite_edge_id(eid)
                gates_for.append(
                    FlowGate(
                        edge_id=eid,
                        kind=kind,
                        vehs_per_hour=half,
                        name=f"{name or eid} ({'IN' if kind == 'entry' else 'OUT'})",
                        lat=lat,
                        lon=lon,
                        sentido="doble",
                        pair_edge_id=other if other in by_id else "",
                    )
                )

            # Ensure at least one entry and one exit on the corridor
            kinds = {g.kind for g in gates_for}
            if gates_for and kinds == {"entry"}:
                gates_for[-1].kind = "exit"
                gates_for[-1].name = gates_for[-1].name.replace("(IN)", "(OUT)")
            elif gates_for and kinds == {"exit"}:
                gates_for[0].kind = "entry"
                gates_for[0].name = gates_for[0].name.replace("(OUT)", "(IN)")

        if gates_for:
            corridors.append((best_d + pref, gates_for))

    corridors.sort(key=lambda x: x[0])
    picked: list[FlowGate] = []
    used_keys: set[str] = set()
    for _, gset in corridors:
        if len(used_keys) >= max_corridors:
            break
        key = undirected_key(gset[0].edge_id)
        if key in used_keys:
            continue
        # Spatial dedupe vs already picked corridor midpoints
        g0 = gset[0]
        too_close = False
        for p in picked:
            if p.lat is None or g0.lat is None or p.lon is None or g0.lon is None:
                continue
            if undirected_key(p.edge_id) == key:
                too_close = True
                break
            if abs(p.lat - g0.lat) < 0.0018 and abs(p.lon - g0.lon) < 0.0018:
                # Same vicinity — skip unless opposite of an existing pair
                if opposite_edge_id(g0.edge_id) != p.edge_id:
                    too_close = True
                    break
        if too_close:
            continue
        used_keys.add(key)
        picked.extend(gset)

    return picked
