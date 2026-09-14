"""Unit tests for scripted video camera phases and spiral."""

from src.video_camera import (
    CLOSE_VIEW_M,
    OVERVIEW_VIEW_M,
    NetBounds,
    overview_boundary,
    phase_at,
    plan_camera_shot,
    spiral_center,
)


def test_phase_schedule_200() -> None:
    assert phase_at(0, segment_s=200)[0] == "overview"
    assert phase_at(199, segment_s=200)[0] == "overview"
    assert phase_at(200, segment_s=200)[0] == "vehicle_zoom"
    assert phase_at(399, segment_s=200)[0] == "vehicle_zoom"
    assert phase_at(400, segment_s=200)[0] == "spiral_mid"
    assert phase_at(600, segment_s=200)[0] == "spiral_detail"
    # Loop back to vehicle_zoom (skip overview)
    assert phase_at(800, segment_s=200)[0] == "vehicle_zoom"
    assert phase_at(1000, segment_s=200)[0] == "spiral_mid"


def test_spiral_starts_near_center() -> None:
    b = NetBounds(0, 0, 1000, 800)
    x0, y0 = spiral_center(b, 0.0, half_w=100, half_h=100)
    assert abs(x0 - b.cx) < 1e-6
    assert abs(y0 - b.cy) < 1e-6
    x1, y1 = spiral_center(b, 1.0, half_w=100, half_h=100)
    assert (x1 - b.cx) ** 2 + (y1 - b.cy) ** 2 > 1000


def test_overview_is_2km_square() -> None:
    b = NetBounds(0, 0, 5000, 4000)
    xmin, ymin, xmax, ymax = overview_boundary(b)
    assert abs((xmax - xmin) - OVERVIEW_VIEW_M) < 1e-6
    assert abs((ymax - ymin) - OVERVIEW_VIEW_M) < 1e-6
    assert abs(0.5 * (xmin + xmax) - b.cx) < 1e-6
    assert abs(0.5 * (ymin + ymax) - b.cy) < 1e-6


def test_plan_close_views_are_400m() -> None:
    b = NetBounds(0, 0, 5000, 5000)
    zoom = plan_camera_shot(250, b, hotspot=(2000.0, 2500.0), segment_s=200)
    assert zoom.phase == "vehicle_zoom"
    assert abs((zoom.xmax - zoom.xmin) - CLOSE_VIEW_M) < 1e-6
    assert abs((zoom.ymax - zoom.ymin) - CLOSE_VIEW_M) < 1e-6

    spiral = plan_camera_shot(450, b, segment_s=200)
    assert spiral.phase == "spiral_mid"
    assert abs((spiral.xmax - spiral.xmin) - CLOSE_VIEW_M) < 1e-6
