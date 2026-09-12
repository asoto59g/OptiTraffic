"""annotate_edge_directions unit tests."""

from src.network_build import annotate_edge_directions


def test_oneway_and_twoway() -> None:
    gj = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "10"},
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]},
            },
            {
                "type": "Feature",
                "properties": {"id": "-10"},
                "geometry": {"type": "LineString", "coordinates": [[1, 0], [0, 0]]},
            },
            {
                "type": "Feature",
                "properties": {"id": "20"},
                "geometry": {"type": "LineString", "coordinates": [[0, 1], [1, 1]]},
            },
        ],
    }
    out = annotate_edge_directions(gj)
    by_id = {f["properties"]["id"]: f["properties"] for f in out["features"]}
    assert by_id["10"]["sentido"] == "doble"
    assert by_id["10"]["oneway"] is False
    assert by_id["20"]["sentido"] == "unico"
    assert by_id["20"]["oneway"] is True
