"""Tests for SUMO background packaging helpers."""

from __future__ import annotations

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from src import sumo_background as bg
from src.scenarios import package_sumo_project
from src.sumo_background import (
    background_matches_net,
    copy_background_into_project,
    fetch_sumo_background,
    write_gui_viewsettings,
)
from src.sumo_env import SumoEnv


def _write_net(path: Path, orig_boundary: str) -> None:
    root = ET.Element("net")
    ET.SubElement(
        root,
        "location",
        netOffset="0.00,0.00",
        convBoundary="0.00,0.00,100.00,100.00",
        origBoundary=orig_boundary,
        projParameter="!",
    )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def test_write_gui_viewsettings_copies_decals() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        decals = td_path / "decals.xml"
        decals.write_text(
            '<?xml version="1.0"?>\n<viewsettings>\n'
            '<decal file="tile0.png" centerX="1" centerY="2" width="10" height="10"/>\n'
            "</viewsettings>\n",
            encoding="utf-8",
        )
        out = write_gui_viewsettings(td_path / "viewsettings_bg.xml", decals_xml=decals)
        root = ET.parse(out).getroot()
        assert root.find("scheme") is not None
        d = root.find("decal")
        assert d is not None
        assert d.get("file") == "tile0.png"


def test_copy_background_rewrites_relative_paths() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        bg = td_path / "src_bg"
        bg.mkdir()
        (bg / "tile0.png").write_bytes(b"fake")
        (bg / "viewsettings_bg.xml").write_text(
            '<?xml version="1.0"?>\n<viewsettings>\n'
            '<decal file="tile0.png" centerX="0" centerY="0" width="1" height="1"/>\n'
            "</viewsettings>\n",
            encoding="utf-8",
        )
        dest = td_path / "project"
        dest.mkdir()
        out = copy_background_into_project(bg, dest, subdir="background")
        assert out is not None and out.is_file()
        assert (dest / "background" / "tile0.png").is_file()
        decal = ET.parse(out).getroot().find("decal")
        assert decal is not None
        assert decal.get("file") == "background/tile0.png"


def test_background_cache_is_bound_to_network_metadata(tmp_path) -> None:
    net = tmp_path / "london.net.xml"
    _write_net(net, "-0.15,51.49,-0.10,51.52")
    bg_dir = tmp_path / "background"
    bg_dir.mkdir()
    (bg_dir / "tile2151_3850.png").write_bytes(b"old")
    (bg_dir / "viewsettings_bg.xml").write_text("<viewsettings />", encoding="utf-8")
    (bg_dir / ".style_osm").write_text("osm", encoding="utf-8")

    assert not background_matches_net(bg_dir, net, style="osm", max_tiles=36)


def test_fetch_sumo_background_redownloads_when_network_changes(monkeypatch, tmp_path) -> None:
    old_net = tmp_path / "liberia.net.xml"
    new_net = tmp_path / "london.net.xml"
    _write_net(old_net, "-85.45,10.60,-85.41,10.64")
    _write_net(new_net, "-0.15,51.49,-0.10,51.52")

    bg_dir = tmp_path / "background"
    bg_dir.mkdir()
    (bg_dir / "tile2151_3850.png").write_bytes(b"old")
    (bg_dir / "viewsettings_bg.xml").write_text("<viewsettings />", encoding="utf-8")
    bg._write_background_meta(bg_dir, net_path=old_net, style="osm", max_tiles=36)

    monkeypatch.setattr(bg, "_tile_get_script", lambda _sumo: tmp_path / "tileGet.py")
    monkeypatch.setattr(bg, "ensure_sumolib_on_path", lambda _home: True)

    def fake_run(cmd, cwd, env, capture_output, text, timeout, check):
        out = Path(cwd)
        (out / "tile999_888.png").write_bytes(b"new")
        (out / "viewsettings_decals.xml").write_text(
            '<?xml version="1.0"?>\n<viewsettings>\n'
            '<decal file="tile999_888.png" centerX="0" centerY="0" width="1" height="1"/>\n'
            "</viewsettings>\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bg.subprocess, "run", fake_run)
    sumo = SumoEnv(
        home=tmp_path,
        sumo_bin=None,
        sumo_gui_bin=None,
        netconvert_bin=None,
        tools_dir=tmp_path,
        ok=True,
        message="ok",
    )

    settings = fetch_sumo_background(
        new_net,
        bg_dir,
        style="osm",
        max_tiles=36,
        sumo=sumo,
        force=False,
    )

    assert settings == bg_dir / "viewsettings_bg.xml"
    assert not (bg_dir / "tile2151_3850.png").exists()
    assert (bg_dir / "tile999_888.png").is_file()
    assert background_matches_net(bg_dir, new_net, style="osm", max_tiles=36)


def test_package_sumo_project_ignores_stale_background_without_metadata(tmp_path) -> None:
    run_dir = tmp_path / "run"
    dest_dir = tmp_path / "scenario" / "sumo"
    bg_dir = run_dir / "background"
    bg_dir.mkdir(parents=True)
    _write_net(run_dir / "sim.net.xml", "-0.15,51.49,-0.10,51.52")
    (run_dir / "routes.rou.xml").write_text("<routes />", encoding="utf-8")
    (bg_dir / "tile2151_3850.png").write_bytes(b"old")
    (bg_dir / "viewsettings_bg.xml").write_text("<viewsettings />", encoding="utf-8")
    (run_dir / "optitraffic.sumocfg").write_text(
        "<?xml version='1.0' encoding='utf-8'?>"
        "<configuration><input><net-file value='sim.net.xml' />"
        "<route-files value='routes.rou.xml' /></input>"
        "<gui_only><gui-settings-file value='background/viewsettings_bg.xml' />"
        "</gui_only></configuration>",
        encoding="utf-8",
    )

    cfg = package_sumo_project(run_dir, dest_dir)

    assert cfg is not None
    assert not (dest_dir / "background").exists()
    assert ET.parse(cfg).find("./gui_only/gui-settings-file") is None


def test_package_sumo_project_prefers_fallback_net_over_external_cfg_net(tmp_path) -> None:
    run_dir = tmp_path / "run"
    dest_dir = tmp_path / "scenario" / "sumo"
    run_dir.mkdir()
    stale_external_net = tmp_path / "data" / "nets" / "liberia_costa_rica.net.xml"
    stale_external_net.parent.mkdir(parents=True)
    _write_net(stale_external_net, "-85.45,10.60,-85.41,10.64")
    fallback_net = tmp_path / "london_great_britain.net.xml"
    _write_net(fallback_net, "-0.15,51.49,-0.10,51.52")
    (run_dir / "routes.rou.xml").write_text("<routes />", encoding="utf-8")
    (run_dir / "optitraffic.sumocfg").write_text(
        "<?xml version='1.0' encoding='utf-8'?>"
        "<configuration><input>"
        f"<net-file value='{stale_external_net}' />"
        "<route-files value='routes.rou.xml' />"
        "</input></configuration>",
        encoding="utf-8",
    )

    cfg = package_sumo_project(run_dir, dest_dir, fallback_net_path=fallback_net)

    assert cfg is not None
    assert (dest_dir / "london_great_britain.net.xml").is_file()
    assert not (dest_dir / "liberia_costa_rica.net.xml").exists()
    net_el = ET.parse(cfg).find("./input/net-file")
    assert net_el is not None
    assert net_el.get("value") == "london_great_britain.net.xml"
