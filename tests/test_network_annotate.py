"""annotate_edge_directions and flow_dir unit tests."""

from src.editors import annotate_road_roles, flow_dir_from_bearing, flow_dir_label
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


def test_flow_dir_from_bearing_cardinals() -> None:
    assert flow_dir_from_bearing(0) == "SN"
    assert flow_dir_from_bearing(90) == "OE"
    assert flow_dir_from_bearing(180) == "NS"
    assert flow_dir_from_bearing(270) == "EO"
    assert flow_dir_from_bearing(None) == ""


def test_flow_dir_label() -> None:
    assert flow_dir_label("OE", "avenida") == "Avenida O→E"
    assert flow_dir_label("NS", "calle") == "Calle N→S"
    assert flow_dir_label("EO") == "E→O"


def test_annotate_road_roles_ew_and_ns() -> None:
    gj = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "ew", "name": ""},
                # lon increases → bearing ~90° → O→E
                "geometry": {"type": "LineString", "coordinates": [[-85.0, 10.0], [-84.9, 10.0]]},
            },
            {
                "type": "Feature",
                "properties": {"id": "ns", "name": ""},
                # lat decreases → bearing ~180° → N→S
                "geometry": {"type": "LineString", "coordinates": [[-85.0, 10.1], [-85.0, 10.0]]},
            },
            {
                "type": "Feature",
                "properties": {"id": "forced", "name": "", "flow_dir_user": "SN"},
                "geometry": {"type": "LineString", "coordinates": [[-85.0, 10.0], [-84.9, 10.0]]},
            },
        ],
    }
    out = annotate_road_roles(gj)
    by_id = {f["properties"]["id"]: f["properties"] for f in out["features"]}
    assert by_id["ew"]["flow_dir"] == "OE"
    assert by_id["ew"]["axis"] == "EW"
    assert by_id["ns"]["flow_dir"] == "NS"
    assert by_id["ns"]["axis"] == "NS"
    assert by_id["forced"]["flow_dir"] == "SN"
    assert by_id["forced"]["road_role"] == "calle"
    assert by_id["forced"]["axis"] == "NS"
