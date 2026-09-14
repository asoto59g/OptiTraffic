"""Scripted sumo-gui camera for OptiTraffic video recording.

Guion (cada toma = ``segment_s`` segundos de simulación, default 200):

1. Overview — vista general fija **2×2 km** centrada en el polígono/red.
2. Vehicle zoom — acercamiento **400×400 m** al hotspot de tráfico.
3. Spiral mid — recorrido en espiral **400×400 m** sobre todo el polígono.
4. Spiral detail — segunda pasada en espiral **400×400 m** (más vueltas / cobertura).
5. Volver a (2) y repetir el ciclo 2→3→4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

PhaseName = Literal["overview", "vehicle_zoom", "spiral_mid", "spiral_detail"]

DEFAULT_SEGMENT_S = 200.0

# Fixed view sizes (full width/height of the camera window in meters).
OVERVIEW_VIEW_M = 2000.0  # 2 km × 2 km
CLOSE_VIEW_M = 400.0  # 400 m × 400 m

OVERVIEW_HALF_M = OVERVIEW_VIEW_M / 2.0  # 1000 m
CLOSE_HALF_M = CLOSE_VIEW_M / 2.0  # 200 m

# Back-compat aliases used by callers / tests
VEHICLE_HALF_M = CLOSE_HALF_M
DETAIL_HALF_M = CLOSE_HALF_M

SPIRAL_TURNS_MID = 3.5
SPIRAL_TURNS_DETAIL = 5.5


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
    turns: float = SPIRAL_TURNS_MID,
) -> tuple[float, float]:
    """
    Archimedean spiral from network center outward across the polygon.

    ``progress`` in [0, 1]; center travels so a CLOSE_VIEW window sweeps the net.
    """
    p = max(0.0, min(1.0, float(progress)))
    theta = p * float(turns) * 2.0 * math.pi
    # Allow the 400×400 window to reach the polygon edges (center inset by half).
    max_r_x = max(20.0, 0.5 * bounds.width - half_w)
    max_r_y = max(20.0, 0.5 * bounds.height - half_h)
    max_r = max(max_r_x, max_r_y)  # cover the longer axis of the study area
    if max_r < 30.0:
        max_r = 0.35 * min(bounds.width, bounds.height)
    r = p * max_r
    return bounds.cx + r * math.cos(theta), bounds.cy + r * math.sin(theta)


def overview_boundary(
    bounds: NetBounds,
    *,
    view_m: float = OVERVIEW_VIEW_M,
) -> tuple[float, float, float, float]:
    """Fixed square overview (default 2×2 km) centered on the network/polygon."""
    half = max(100.0, float(view_m) / 2.0)
    return view_boundary_from_center(bounds.cx, bounds.cy, half)


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
    overview_view_m: float = OVERVIEW_VIEW_M,
    close_view_m: float = CLOSE_VIEW_M,
    vehicle_half_m: Optional[float] = None,
    detail_half_m: Optional[float] = None,
) -> CameraShot:
    """
    Plan the next camera frame.

    ``vehicle_half_m`` / ``detail_half_m`` override half-extents when set;
    otherwise close views use ``close_view_m`` (400 m → half 200 m).
    """
    phase, progress = phase_at(sim_t, segment_s=segment_s)
    close_half = (
        float(vehicle_half_m)
        if vehicle_half_m is not None
        else max(40.0, float(close_view_m) / 2.0)
    )
    detail_half = (
        float(detail_half_m)
        if detail_half_m is not None
        else close_half
    )

    if phase == "overview":
        xmin, ymin, xmax, ymax = overview_boundary(bounds, view_m=overview_view_m)
        return CameraShot(xmin, ymin, xmax, ymax, phase, require_vehicles=False)

    if phase == "vehicle_zoom":
        if hotspot is not None:
            cx, cy = hotspot
        else:
            cx, cy = bounds.cx, bounds.cy
        xmin, ymin, xmax, ymax = view_boundary_from_center(cx, cy, close_half)
        return CameraShot(xmin, ymin, xmax, ymax, phase, require_vehicles=True)

    half = close_half if phase == "spiral_mid" else detail_half
    turns = SPIRAL_TURNS_MID if phase == "spiral_mid" else SPIRAL_TURNS_DETAIL
    cx, cy = spiral_center(bounds, progress, half_w=half, half_h=half, turns=turns)
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
