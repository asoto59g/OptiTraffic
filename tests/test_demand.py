"""Demand generation unit tests."""

import tempfile
import xml.etree.ElementTree as ET
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
            preload_vph=0,
        )
        text = path.read_text(encoding="utf-8")
        assert text.count("<trip") > 10
        assert 'from="e1"' in text


def test_write_gate_trips_sorted_by_depart() -> None:
    """SUMO ignores out-of-order departures; multi-entry demand must be sorted."""
    gates = [
        FlowGate("e1", "entry", 120, "A", 10.0, -85.0),
        FlowGate("e2", "entry", 120, "B", 10.1, -85.1),
        FlowGate("e3", "exit", 120, "C", 10.2, -85.2),
    ]
    with tempfile.TemporaryDirectory() as td:
        path = write_gate_trips(
            Path(td) / "g.xml",
            gates,
            ["e1", "e2", "e3", "e4", "e5"],
            end=3600,
            preload_vph=60,
            preload_fill_s=300,
        )
        deps = [
            float(t.get("depart") or 0)
            for t in ET.parse(path).getroot().findall("trip")
        ]
        assert deps == sorted(deps)
        assert deps[0] == 0.0
        assert any(d < 60 for d in deps)


def test_write_gate_trips_preload() -> None:
    gates = [
        FlowGate("e1", "entry", 50, "A", 10.0, -85.0),
        FlowGate("e2", "exit", 50, "B", 10.1, -85.1),
    ]
    with tempfile.TemporaryDirectory() as td:
        path = write_gate_trips(
            Path(td) / "g.xml",
            gates,
            ["e1", "e2", "e3", "e4", "e5"],
            end=3600,
            preload_vph=100,
            preload_fill_s=900,
            preload_frontload=0.7,
        )
        root = ET.parse(path).getroot()
        trips = root.findall("trip")
        # 100 veh/h * 1 h = 100 preload trips (+ entry trips)
        assert len(trips) >= 100
        early = sum(1 for t in trips if float(t.get("depart") or 0) < 900)
        assert early >= 50
        deps = [float(t.get("depart") or 0) for t in trips]
        assert deps == sorted(deps)
