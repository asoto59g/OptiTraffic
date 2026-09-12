"""Scenario IoU unit tests."""

from shapely.geometry import box

from src.scenarios import polygon_iou


def test_iou_identical() -> None:
    a = box(0, 0, 1, 1)
    assert polygon_iou(a, a) == 1.0


def test_iou_disjoint() -> None:
    assert polygon_iou(box(0, 0, 1, 1), box(2, 2, 3, 3)) == 0.0


def test_iou_overlap() -> None:
    a = box(0, 0, 2, 2)
    b = box(1, 1, 3, 3)
    v = polygon_iou(a, b)
    assert 0.1 < v < 0.5
