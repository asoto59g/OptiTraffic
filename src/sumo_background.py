"""Download OSM / satellite background tiles for sumo-gui (via SUMO tileGet.py)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal, Optional

from .logging_config import get_logger
from .sumo_env import SumoEnv, detect_sumo, ensure_sumolib_on_path

log = get_logger("sumo_background")

BackgroundStyle = Literal["osm", "satellite"]

# tileGet.py -u shortcuts (installed SUMO)
_STYLE_TO_URL = {
    "osm": "osm",
    "satellite": "arcgis",  # Esri World Imagery — no API key
}

_USER_AGENT = "OptiTraffic/1.0 (local traffic study; background tiles for SUMO)"


def _tile_get_script(sumo: SumoEnv) -> Optional[Path]:
    if not sumo.home:
        return None
    p = sumo.home / "tools" / "tileGet.py"
    return p if p.is_file() else None


def write_gui_viewsettings(
    out_path: Path,
    *,
    decals_xml: Optional[Path] = None,
    vehicle_exaggeration: float = 2.5,
    delay_ms: int = 100,
) -> Path:
    """
    Write a viewsettings file with real-world scheme and optional background decals.
    Decal <decal .../> nodes are copied from tileGet output (same directory as tiles).
    """
    root = ET.Element("viewsettings")
    scheme = ET.SubElement(root, "scheme", name="real world")
    ET.SubElement(
        scheme,
        "vehicles",
        vehicle_exaggeration=str(vehicle_exaggeration),
        vehicle_minSize="3",
        vehicle_constantSize="0",
        vehicle_quality="2",
        showBlinker="0",
    )
    ET.SubElement(
        scheme,
        "persons",
        person_exaggeration="1",
        person_minSize="1",
        person_constantSize="0",
    )
    ET.SubElement(scheme, "edges", edge_exaggeration="1.0")
    ET.SubElement(root, "delay", value=str(int(delay_ms)))

    if decals_xml and Path(decals_xml).is_file():
        try:
            dtree = ET.parse(decals_xml)
            for decal in dtree.getroot().findall("decal"):
                root.append(decal)
        except Exception:
            log.warning("No se pudieron leer decals de %s", decals_xml, exc_info=True)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Pretty-ish: ElementTree doesn't indent by default — fine for SUMO
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def fetch_sumo_background(
    net_path: Path,
    out_dir: Path,
    *,
    style: BackgroundStyle = "osm",
    max_tiles: int = 36,
    sumo: Optional[SumoEnv] = None,
    force: bool = False,
) -> Optional[Path]:
    """
    Download map tiles for the network bbox and return path to viewsettings_bg.xml
    (with decals + vehicle scheme). Tiles live alongside that file in out_dir.

    Returns None if tileGet is missing, style invalid, or download fails.
    """
    sumo = sumo or detect_sumo()
    script = _tile_get_script(sumo)
    if script is None:
        log.warning("tileGet.py no encontrado en SUMO_HOME/tools")
        return None
    if style not in _STYLE_TO_URL:
        log.warning("Estilo de fondo desconocido: %s", style)
        return None
    net_path = Path(net_path)
    if not net_path.is_file():
        log.warning("Red inexistente para fondo: %s", net_path)
        return None

    ensure_sumolib_on_path(sumo.home)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings_out = out_dir / "viewsettings_bg.xml"
    decals_raw = out_dir / "viewsettings_decals.xml"

    # Reuse cache unless forced or style changed
    style_marker = out_dir / f".style_{style}"
    has_tiles = any(out_dir.glob("tile*.png")) or any(out_dir.glob("tile*.jpeg"))
    if not force and settings_out.is_file() and has_tiles and style_marker.is_file():
        return settings_out

    # Clear previous tiles for this folder
    for pat in ("tile*.png", "tile*.jpeg", "tile*.jpg"):
        for f in out_dir.glob(pat):
            try:
                f.unlink()
            except OSError:
                pass
    for marker in out_dir.glob(".style_*"):
        try:
            marker.unlink()
        except OSError:
            pass

    url_key = _STYLE_TO_URL[style]
    cmd = [
        sys.executable,
        str(script),
        "-n",
        str(net_path),
        "-d",
        str(out_dir),
        "-s",
        decals_raw.name,
        "-p",
        "tile",
        "-t",
        str(max(4, int(max_tiles))),
        "-u",
        url_key,
        "-a",
        _USER_AGENT,
        "-j",
        "4",
        "-l",
        "-1",
        "-f",
        "1000",
    ]
    env = os.environ.copy()
    if sumo.home:
        env["SUMO_HOME"] = str(sumo.home)
        tools = str(sumo.home / "tools")
        env["PYTHONPATH"] = tools + os.pathsep + env.get("PYTHONPATH", "")

    log.info("Descargando fondo %s con tileGet…", style)
    try:
        r = subprocess.run(
            cmd,
            cwd=str(out_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except Exception:
        log.exception("tileGet falló al lanzarse")
        return None

    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "")[:500]
        log.warning("tileGet exit=%s: %s", r.returncode, detail)
        return None

    tiles = list(out_dir.glob("tile*.png")) + list(out_dir.glob("tile*.jpeg"))
    if not tiles:
        log.warning("tileGet no produjo imágenes en %s", out_dir)
        return None

    write_gui_viewsettings(settings_out, decals_xml=decals_raw if decals_raw.is_file() else None)
    # Ensure decal paths are bare filenames (tiles sit next to viewsettings_bg.xml)
    try:
        tree = ET.parse(settings_out)
        root = tree.getroot()
        changed = False
        for decal in root.findall("decal"):
            raw = decal.get("file") or ""
            name = Path(raw).name
            if name and raw != name:
                decal.set("file", name)
                changed = True
        if changed:
            tree.write(settings_out, encoding="utf-8", xml_declaration=True)
    except Exception:
        log.debug("No se normalizaron rutas de decals", exc_info=True)

    (out_dir / f".style_{style}").write_text(style, encoding="utf-8")
    log.info("Fondo %s: %d teselas → %s", style, len(tiles), settings_out)
    return settings_out


def copy_background_into_project(
    bg_dir: Path,
    dest_dir: Path,
    *,
    subdir: str = "background",
) -> Optional[Path]:
    """
    Copy tiles + rewrite viewsettings so decal paths are relative
    (subdir/filename) for a portable sumo-gui project.
    Returns path to dest viewsettings_bg.xml or None.
    """
    bg_dir = Path(bg_dir)
    dest_dir = Path(dest_dir)
    src_settings = bg_dir / "viewsettings_bg.xml"
    if not src_settings.is_file():
        return None

    tile_dest = dest_dir / subdir
    if tile_dest.exists():
        shutil.rmtree(tile_dest, ignore_errors=True)
    tile_dest.mkdir(parents=True, exist_ok=True)

    for pat in ("tile*.png", "tile*.jpeg", "tile*.jpg", "viewsettings_decals.xml"):
        for f in bg_dir.glob(pat):
            shutil.copy2(f, tile_dest / f.name)

    tree = ET.parse(src_settings)
    root = tree.getroot()
    for decal in root.findall("decal"):
        fname = Path(decal.get("file") or "").name
        if fname:
            decal.set("file", f"{subdir}/{fname}".replace("\\", "/"))

    out_settings = dest_dir / "viewsettings_bg.xml"
    tree.write(out_settings, encoding="utf-8", xml_declaration=True)
    return out_settings
