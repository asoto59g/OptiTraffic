"""match_traffic_to_edges regression tests."""

from shapely.geometry import LineString

from src.tomtom import TrafficSegment, match_traffic_to_edges


def test_match_nearest_segment() -> None:
    edges = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "e1"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-84.08, 9.93], [-84.07, 9.93]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": "e2"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-84.10, 9.95], [-84.09, 9.95]],
                },
            },
        ],
    }
    segs = [
        TrafficSegment(LineString([(-84.08, 9.9301), (-84.07, 9.9301)]), 0.4),
        TrafficSegment(LineString([(-84.10, 9.9501), (-84.09, 9.9501)]), 0.8),
    ]
    levels = match_traffic_to_edges(edges, segs, max_dist_deg=0.01)
    assert levels["e1"] == 0.4
    assert levels["e2"] == 0.8


def test_match_empty_segments() -> None:
    assert match_traffic_to_edges({"features": []}, []) == {}
