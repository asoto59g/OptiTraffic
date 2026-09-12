"""Area / polygon unit tests."""

from src.area import (
    build_study_area,
    geodesic_area_km2,
    load_geojson_polygon,
    rectangle_around,
    validate_area,
)


def test_rectangle_and_validate() -> None:
    poly = rectangle_around(9.93, -84.08, half_km=0.5)
    ok, msg = validate_area(poly)
    assert ok, msg
    area = build_study_area("San José", "Costa Rica", poly)
    assert area.area_km2 < 5


def test_load_geojson_polygon() -> None:
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
