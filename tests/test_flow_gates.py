"""Flow gate suggestion unit tests."""

from shapely.geometry import box

from src.flow_gates import _classify_vs_centroid, suggest_boundary_gates


def test_classify_vs_centroid() -> None:
    c = (0.5, 0.5)
    assert _classify_vs_centroid((1.0, 0.5), (0.9, 0.5), c) == "entry"
    assert _classify_vs_centroid((0.9, 0.5), (1.0, 0.5), c) == "exit"


def test_suggest_boundary_gates_inout() -> None:
    feats = [
        {
            "type": "Feature",
            "properties": {
                "id": "100",
                "name": "Av Este",
                "oneway": False,
                "sentido": "doble",
                "road_role": "avenida",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[1.0, 0.5], [0.9, 0.5]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "id": "-100",
                "name": "Av Este",
                "oneway": False,
                "sentido": "doble",
                "road_role": "avenida",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[0.9, 0.5], [1.0, 0.5]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "id": "200",
                "name": "Calle Norte",
                "oneway": True,
                "sentido": "unico",
                "road_role": "calle",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[0.5, 1.0], [0.5, 0.9]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "id": "300",
                "name": "Calle Sur",
                "oneway": True,
                "sentido": "unico",
                "road_role": "calle",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[0.5, 0.1], [0.5, 0.0]],
            },
        },
    ]
    gj = {"type": "FeatureCollection", "features": feats}
    poly = box(0.2, 0.2, 0.8, 0.8)
    gates = suggest_boundary_gates(
        gj, poly, max_corridors=5, default_vph=400, buffer_deg=0.5
    )
    n_in = sum(1 for g in gates if g.kind == "entry")
    n_out = sum(1 for g in gates if g.kind == "exit")
    assert n_in >= 1 and n_out >= 1
    assert any(g.sentido == "doble" for g in gates)
