"""Unit tests for scripted video camera phases and spiral."""

from src.video_camera import (
    NetBounds,
    overview_boundary,
    phase_at,
    plan_camera_shot,
    spiral_center,
)


def test_phase_schedule_300() -> None:
    assert phase_at(0, segment_s=300)[0] == "overview"
    assert phase_at(299, segment_s=300)[0] == "overview"
    assert phase_at(300, segment_s=300)[0] == "vehicle_zoom"
    assert phase_at(599, segment_s=300)[0] == "vehicle_zoom"
    assert phase_at(600, segment_s=300)[0] == "spiral_mid"
    assert phase_at(900, segment_s=300)[0] == "spiral_detail"
    # Loop back to vehicle_zoom (skip overview)
    assert phase_at(1200, segment_s=300)[0] == "vehicle_zoom"
    assert phase_at(1500, segment_s=300)[0] == "spiral_mid"


def test_spiral_starts_near_center() -> None:
    b = NetBounds(0, 0, 1000, 800)
    x0, y0 = spiral_center(b, 0.0, half_w=100, half_h=100)
    assert abs(x0 - b.cx) < 1e-6
    assert abs(y0 - b.cy) < 1e-6
    x1, y1 = spiral_center(b, 1.0, half_w=100, half_h=100)
    assert (x1 - b.cx) ** 2 + (y1 - b.cy) ** 2 > 1000


def test_overview_tight_fit() -> None:
    b = NetBounds(100, 200, 500, 600)
    xmin, ymin, xmax, ymax = overview_boundary(b, margin=0.04)
    assert xmin < b.xmin
    assert xmax > b.xmax
    assert (xmax - xmin) < b.width * 1.2


def test_plan_vehicle_uses_hotspot() -> None:
    b = NetBounds(0, 0, 1000, 1000)
    shot = plan_camera_shot(350, b, hotspot=(200.0, 300.0), segment_s=300)
    assert shot.phase == "vehicle_zoom"
    assert shot.require_vehicles is True
    cx = 0.5 * (shot.xmin + shot.xmax)
    cy = 0.5 * (shot.ymin + shot.ymax)
    assert abs(cx - 200.0) < 1e-6
    assert abs(cy - 300.0) < 1e-6
