"""TLS program override + stop-sign net encoding tests."""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from src.editors import (
    NetworkEdits,
    StopSign,
    TlsTiming,
    apply_stop_signs_to_net,
    apply_tls_timings_to_net,
    normalize_tls_id,
    write_stops_add,
    write_tls_add,
)


def test_normalize_tls_id_strips_legacy_prefix() -> None:
    assert normalize_tls_id("tls_j_42", "42") == "42"
    assert normalize_tls_id("tls_edgeA", "99") == "99"
    assert normalize_tls_id("cluster_1", None) == "cluster_1"
    assert normalize_tls_id(None, "7") == "7"


def test_resolve_tls_junction_ids_maps_joined_clusters() -> None:
    from src.editors import resolve_tls_junction_ids

    net_xml = """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <junction id="100" type="priority" x="0" y="0" incLanes="" intLanes="" shape=""/>
  <junction id="joinedS_3086_cluster_107867_4090401565_1680893657" type="priority" x="1" y="1" incLanes="" intLanes="" shape=""/>
  <junction id=":internal_1" type="internal" x="2" y="2" incLanes="" intLanes="" shape=""/>
</net>
"""
    with tempfile.TemporaryDirectory() as td:
        net = Path(td) / "n.net.xml"
        net.write_text(net_xml, encoding="utf-8")
        resolved, skipped = resolve_tls_junction_ids(
            net,
            ["100", "4090401565", "999999", "joinedS_3086_cluster_107867_4090401565_1680893657"],
        )
        assert "100" in resolved
        assert "joinedS_3086_cluster_107867_4090401565_1680893657" in resolved
        assert "999999" in skipped
        assert "4090401565" not in resolved  # remapped into joined id
        assert any("4090401565" in j for j in resolved)


def test_apply_tls_timings_patches_net_program_0() -> None:
    net_xml = """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <tlLogic id="42" type="static" programID="0" offset="0">
    <phase duration="31" state="GG"/>
    <phase duration="4" state="yy"/>
    <phase duration="31" state="rr"/>
  </tlLogic>
</net>
"""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        net = td_path / "n.net.xml"
        net.write_text(net_xml, encoding="utf-8")
        edits = NetworkEdits(tls_default=TlsTiming(green=20, yellow=3, red=25))
        edits.tls_overrides["tls_j_42"] = TlsTiming(green=20, yellow=3, red=25)
        n = apply_tls_timings_to_net(net, edits, ["tls_j_42"])
        assert n == 1
        tl = ET.parse(net).getroot().find("tlLogic")
        assert tl is not None
        phases = [(p.get("duration"), p.get("state")) for p in tl.findall("phase")]
        assert phases == [("20", "GG"), ("3", "yy"), ("25", "rr")]


def test_write_tls_add_does_not_duplicate_program_0() -> None:
    net_xml = """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <tlLogic id="42" type="static" programID="0" offset="0">
    <phase duration="31" state="GG"/>
    <phase duration="4" state="yy"/>
    <phase duration="31" state="rr"/>
  </tlLogic>
</net>
"""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        net = td_path / "n.net.xml"
        net.write_text(net_xml, encoding="utf-8")
        edits = NetworkEdits(tls_default=TlsTiming(green=18, yellow=2, red=22))
        out = write_tls_add(td_path / "tls.add.xml", ["42"], edits, net_path=net)
        text = out.read_text(encoding="utf-8")
        assert "<tlLogic" not in text
        # Timings still applied in the net
        phases = [
            (p.get("duration"), p.get("state"))
            for p in ET.parse(net).getroot().find("tlLogic").findall("phase")
        ]
        assert phases == [("18", "GG"), ("2", "yy"), ("22", "rr")]


def test_write_stops_add_is_documentation_only() -> None:
    edits = NetworkEdits()
    edits.stops.append(StopSign(edge_id="e1", junction_id="j1"))
    with tempfile.TemporaryDirectory() as td:
        out = write_stops_add(Path(td) / "stops.add.xml", edits)
        text = out.read_text(encoding="utf-8")
        assert "<stop " not in text
        assert "priority_stop" in text
        assert "e1" in text


def test_apply_stop_signs_sets_priority_stop() -> None:
    net_xml = """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <edge id="minor" from="A" to="J" priority="1"/>
  <edge id="major" from="B" to="J" priority="9"/>
  <junction id="J" type="priority" x="0" y="0" incLanes="" intLanes="" shape=""/>
</net>
"""
    edges_gj = {
        "features": [
            {"properties": {"id": "minor", "road_role": "calle"}},
            {"properties": {"id": "major", "road_role": "avenida"}},
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        net_in = td_path / "in.net.xml"
        net_out = td_path / "out.net.xml"
        net_in.write_text(net_xml, encoding="utf-8")
        apply_stop_signs_to_net(
            net_in,
            net_out,
            [StopSign(edge_id="minor", junction_id="J")],
            edges_gj=edges_gj,
            netconvert_bin=None,
        )
        root = ET.parse(net_out).getroot()
        junc = root.find("junction")
        assert junc is not None
        assert junc.get("type") == "priority_stop"
        by_id = {e.get("id"): e for e in root.findall("edge")}
        assert by_id["minor"].get("priority") == "1"
        assert by_id["major"].get("priority") == "12"
