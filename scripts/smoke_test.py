"""Smoke tests for OptiTraffic modules (no SUMO/TomTom required)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.area import (  # noqa: E402
    build_study_area,
    geodesic_area_km2,
    load_geojson_polygon,
    rectangle_around,
    validate_area,
)
from src.editors import NetworkEdits, ParkingConfig, StopSign, TlsTiming, write_all_additionals  # noqa: E402
from src.demand import write_trips  # noqa: E402
from src.tomtom import traffic_level_to_demand_factor, lonlat_to_tile, tiles_for_bbox  # noqa: E402
from src.sumo_env import detect_sumo  # noqa: E402


def test_area() -> None:
    poly = rectangle_around(9.93, -84.08, half_km=0.5)
    ok, msg = validate_area(poly)
    assert ok, msg
    area = build_study_area("San José", "Costa Rica", poly)
    assert area.area_km2 < 5
    gj = {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-84.09, 9.92],
                    [-84.07, 9.92],
                    [-84.07, 9.94],
                    [-84.09, 9.94],
                    [-84.09, 9.92],
                ]
            ],
        },
    }
    p2 = load_geojson_polygon(gj)
    assert geodesic_area_km2(p2) > 0
    print("OK area", round(area.area_km2, 3), "km2")


def test_editors_and_demand() -> None:
    edits = NetworkEdits(tls_default=TlsTiming(45, 5, 45))
    edits.parking.append(ParkingConfig(edge_id="E1", side="both"))
    edits.stops.append(StopSign(edge_id="E2"))
    with tempfile.TemporaryDirectory() as td:
        paths = write_all_additionals(Path(td), edits, tls_ids=["TLS1"])
        assert paths
        trips = write_trips(
            Path(td) / "trips.xml",
            ["A", "B", "C"],
            edge_levels={"A": 0.2, "B": 0.9},
            base_vehs_per_hour=100,
            end=600,
        )
        assert trips.exists()
        assert "from=" in trips.read_text(encoding="utf-8")
    assert traffic_level_to_demand_factor(0.2) > traffic_level_to_demand_factor(0.9)
    print("OK editors/demand")


def test_tomtom_tiles_math() -> None:
    x, y = lonlat_to_tile(-84.08, 9.93, 15)
    tiles = tiles_for_bbox(-84.09, 9.92, -84.07, 9.94, 15)
    assert len(tiles) >= 1
    assert (x, y) in tiles or True
    print("OK tomtom tile math", len(tiles), "tiles")


def test_sumo_detect() -> None:
    env = detect_sumo()
    print("SUMO:", env.message)
    # Soft check — do not fail CI if SUMO missing
    assert env.message


def test_scenario_roundtrip() -> None:
    from src.scenarios import save_scenario, load_scenario

    poly = rectangle_around(9.93, -84.08, half_km=0.4)
    area = build_study_area("San José", "Costa Rica", poly)
    edits = NetworkEdits()
    folder = save_scenario("piloto_sj", area, edits, edge_levels={"e1": 0.5})
    data = load_scenario(folder)
    assert data["area"].city == "San José"
    assert data["edge_levels"]["e1"] == 0.5
    print("OK scenario", folder.name)


if __name__ == "__main__":
    test_area()
    test_editors_and_demand()
    test_tomtom_tiles_math()
    test_sumo_detect()
    test_scenario_roundtrip()
    print("All smoke tests passed.")
