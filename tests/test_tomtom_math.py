"""TomTom tile math unit tests."""

from src.tomtom import lonlat_to_tile, tiles_for_bbox, traffic_level_to_demand_factor


def test_lonlat_to_tile() -> None:
    x, y = lonlat_to_tile(-84.08, 9.93, 15)
    assert isinstance(x, int) and isinstance(y, int)


def test_tiles_for_bbox() -> None:
    tiles = tiles_for_bbox(-84.09, 9.92, -84.07, 9.94, 15)
    assert len(tiles) >= 1


def test_demand_factor() -> None:
    assert traffic_level_to_demand_factor(0.0) > traffic_level_to_demand_factor(1.0)
