"""Tests for SUMO background packaging helpers."""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from src.sumo_background import copy_background_into_project, write_gui_viewsettings


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
