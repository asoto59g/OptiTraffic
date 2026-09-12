"""Demand generation unit tests."""

import tempfile
from pathlib import Path

from src.demand import write_gate_trips, write_trips
from src.flow_gates import FlowGate
from src.tomtom import traffic_level_to_demand_factor


def test_demand_factor_inverse() -> None:
    assert traffic_level_to_demand_factor(0.2) > traffic_level_to_demand_factor(0.9)


def test_write_trips() -> None:
    with tempfile.TemporaryDirectory() as td:
        trips = write_trips(
            Path(td) / "trips.xml",
            ["A", "B", "C"],
            edge_levels={"A": 0.2, "B": 0.9},
            base_vehs_per_hour=100,
            end=600,
        )
        assert trips.exists()
        assert "from=" in trips.read_text(encoding="utf-8")


def test_write_gate_trips() -> None:
    gates = [
        FlowGate("e1", "entry", 400, "A", 10.0, -85.0),
        FlowGate("e2", "exit", 300, "B", 10.1, -85.1),
    ]
    with tempfile.TemporaryDirectory() as td:
        path = write_gate_trips(
            Path(td) / "g.xml",
            gates,
            ["e1", "e2", "e3", "e4"],
            end=3600,
        )
        text = path.read_text(encoding="utf-8")
        assert text.count("<trip") > 10
        assert 'from="e1"' in text
