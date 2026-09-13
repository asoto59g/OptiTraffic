"""Scripted sumo-gui camera for OptiTraffic video recording.

Guion (cada toma = ``segment_s`` segundos de simulación, default 300):

1. Overview — polígono/red completo, lo más cerca posible (encuadre ajustado).
2. Vehicle zoom — acercamiento hasta distinguir vehículos (hotspot de tráfico).
3. Spiral mid — misma escala que (2), recorrido en espiral sobre todo el polígono.
4. Spiral detail — misma espiral más baja (menor área, más detalle).
5. Volver a (2) y repetir el ciclo 2→3→4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

PhaseName = Literal["overview", "vehicle_zoom", "spiral_mid", "spiral_detail"]

DEFAULT_SEGMENT_S = 300.0
# Half-extents (m) for vehicle-readable and detail zooms
VEHICLE_HALF_M = 200.0
DETAIL_HALF_M = 100.0
SPIRAL_TURNS = 3.5


@dataclass(frozen=True)
class NetBounds:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def cx(self) -> float:
        return 0.5 * (self.xmin + self.xmax)

    @property
    def cy(self) -> float:
        return 0.5 * (self.ymin + self.ymax)

    @property
    def width(self) -> float:
        return max(1.0, self.xmax - self.xmin)

    @property
    def height(self) -> float:
        return max(1.0, self.ymax - self.ymin)


def phase_at(
    sim_t: float,
    *,
    segment_s: float = DEFAULT_SEGMENT_S,
) -> tuple[PhaseName, float]:
    """
    Return (phase_name, progress_in_phase) with progress in [0, 1).

    Shot 1 once, then loop shots 2–4.
    """
    seg = max(1.0, float(segment_s))
    t = max(0.0, float(sim_t))
    if t < seg:
        return "overview", min(0.999, t / seg)
    cycle = (t - seg) % (3.0 * seg)
    if cycle < seg:
        return "vehicle_zoom", cycle / seg
    if cycle < 2.0 * seg:
        return "spiral_mid", (cycle - seg) / seg
    return "spiral_detail", (cycle - 2.0 * seg) / seg


def spiral_center(
    bounds: NetBounds,
    progress: float,
    *,
    half_w: float,
    half_h: float,
    turns: float = SPIRAL_TURNS,
) -> tuple[float, float]:
    """
    Archimedean spiral from network center outward.

    ``progress`` in [0, 1]; center stays inset so the view mostly covers the net.
    """
    p = max(0.0, min(1.0, float(progress)))
    theta = p * float(turns) * 2.0 * math.pi
    # Max radius so the camera window stays mostly over the network
    max_r_x = max(20.0, 0.5 * bounds.width - half_w)
    max_r_y = max(20.0, 0.5 * bounds.height - half_h)
    max_r = min(max_r_x, max_r_y)
    if max_r < 30.0:
        # Tiny network: still spiral a bit inside the bbox
        max_r = 0.35 * min(bounds.width, bounds.height)
    r = p * max_r
    return bounds.cx + r * math.cos(theta), bounds.cy + r * math.sin(theta)


def overview_boundary(bounds: NetBounds, margin: float = 0.04) -> tuple[float, float, float, float]:
    """Tight fit of the full network (small margin)."""
    m = max(0.0, float(margin))
    pad_x = max(15.0, bounds.width * m)
    pad_y = max(15.0, bounds.height * m)
    return (
        bounds.xmin - pad_x,
        bounds.ymin - pad_y,
        bounds.xmax + pad_x,
        bounds.ymax + pad_y,
    )


def view_boundary_from_center(
    cx: float,
    cy: float,
    half: float,
) -> tuple[float, float, float, float]:
    h = max(40.0, float(half))
    return cx - h, cy - h, cx + h, cy + h


@dataclass
class CameraShot:
    """One TraCI setBoundary request."""

    xmin: float
    ymin: float
    xmax: float
    ymax: float
    phase: PhaseName
    require_vehicles: bool


def plan_camera_shot(
    sim_t: float,
    bounds: NetBounds,
    *,
    hotspot: Optional[Tuple[float, float]] = None,
    segment_s: float = DEFAULT_SEGMENT_S,
    vehicle_half_m: float = VEHICLE_HALF_M,
    detail_half_m: float = DETAIL_HALF_M,
) -> CameraShot:
    phase, progress = phase_at(sim_t, segment_s=segment_s)

    if phase == "overview":
        xmin, ymin, xmax, ymax = overview_boundary(bounds)
        return CameraShot(xmin, ymin, xmax, ymax, phase, require_vehicles=False)

    if phase == "vehicle_zoom":
        if hotspot is not None:
            cx, cy = hotspot
        else:
            cx, cy = bounds.cx, bounds.cy
        xmin, ymin, xmax, ymax = view_boundary_from_center(cx, cy, vehicle_half_m)
        return CameraShot(xmin, ymin, xmax, ymax, phase, require_vehicles=True)

    half = vehicle_half_m if phase == "spiral_mid" else detail_half_m
    cx, cy = spiral_center(bounds, progress, half_w=half, half_h=half)
    xmin, ymin, xmax, ymax = view_boundary_from_center(cx, cy, half)
    return CameraShot(xmin, ymin, xmax, ymax, phase, require_vehicles=False)


def apply_camera_shot(shot: CameraShot, view_id: str = "View #0") -> bool:
    try:
        import traci
    except Exception:
        return False
    try:
        traci.gui.setBoundary(view_id, shot.xmin, shot.ymin, shot.xmax, shot.ymax)
        return True
    except Exception:
        return False


def load_net_bounds_from_cfg(cfg_path) -> Optional[NetBounds]:
    """Read network bbox from sumocfg → net-file via sumolib."""
    from pathlib import Path
    import xml.etree.ElementTree as ET

    try:
        import sumolib
    except Exception:
        return None
    try:
        tree = ET.parse(Path(cfg_path))
        net_el = tree.find("./input/net-file")
        if net_el is None or not net_el.get("value"):
            return None
        net_path = Path(net_el.get("value", ""))
        if not net_path.is_file():
            # Relative to cfg dir
            net_path = Path(cfg_path).resolve().parent / net_el.get("value", "")
        if not net_path.is_file():
            return None
        net = sumolib.net.readNet(str(net_path))
        xmin, ymin, xmax, ymax = net.getBoundary()
        return NetBounds(float(xmin), float(ymin), float(xmax), float(ymax))
    except Exception:
        return None
