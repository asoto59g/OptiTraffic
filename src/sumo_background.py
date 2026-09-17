"""Download OSM / satellite background tiles for sumo-gui (via SUMO tileGet.py)."""

from __future__ import annotations

import hashlib
import json
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
_CACHE_META = ".background_meta.json"
_LOCATION_ATTRS = ("netOffset", "convBoundary", "origBoundary", "projParameter")


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


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def net_background_signature(net_path: Path) -> str:
    """
    Stable cache key for the geographic extent/projection of a SUMO network.

    tileGet derives tiles from the network location/bounds. Use those attrs when
    available so derived simulation nets can share a background with their source
    net. Fall back to a file hash for older/unusual nets without <location>.
    """
    net_path = Path(net_path)
    try:
        with net_path.open("rb") as f:
            for _event, elem in ET.iterparse(f, events=("start",)):
                if elem.tag != "location":
                    continue
                values = {key: elem.get(key, "") for key in _LOCATION_ATTRS}
                if any(values.values()):
                    return "location:" + json.dumps(values, sort_keys=True)
                break
    except Exception:
        log.debug("No se pudo leer <location> de %s", net_path, exc_info=True)
    return "sha256:" + _hash_file(net_path)


def _background_meta_path(bg_dir: Path) -> Path:
    return Path(bg_dir) / _CACHE_META


def _read_background_meta(bg_dir: Path) -> dict | None:
    try:
        return json.loads(_background_meta_path(bg_dir).read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_background_meta(
    bg_dir: Path,
    *,
    net_path: Path,
    style: BackgroundStyle,
    max_tiles: int,
) -> None:
    meta = {
        "style": style,
        "max_tiles": int(max_tiles),
        "net_signature": net_background_signature(net_path),
        "net_name": Path(net_path).name,
    }
    _background_meta_path(bg_dir).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _background_has_tiles(bg_dir: Path) -> bool:
    bg_dir = Path(bg_dir)
    return any(bg_dir.glob("tile*.png")) or any(bg_dir.glob("tile*.jpeg")) or any(
        bg_dir.glob("tile*.jpg")
    )


def background_matches_net(
    bg_dir: Path,
    net_path: Path,
    *,
    style: Optional[BackgroundStyle] = None,
    max_tiles: Optional[int] = None,
) -> bool:
    """True only when cached background tiles were generated for this network."""
    bg_dir = Path(bg_dir)
    if not (bg_dir / "viewsettings_bg.xml").is_file() or not _background_has_tiles(bg_dir):
        return False
    meta = _read_background_meta(bg_dir)
    if not meta:
        return False
    if style is not None and meta.get("style") != style:
        return False
    if max_tiles is not None and int(meta.get("max_tiles") or 0) != int(max_tiles):
        return False
    try:
        return meta.get("net_signature") == net_background_signature(net_path)
    except Exception:
        return False


def _clear_background_dir(bg_dir: Path) -> None:
    bg_dir = Path(bg_dir)
    for pat in ("tile*.png", "tile*.jpeg", "tile*.jpg", ".style_*"):
        for f in bg_dir.glob(pat):
            try:
                f.unlink()
            except OSError:
                pass
    for name in ("viewsettings_bg.xml", "viewsettings_decals.xml", _CACHE_META):
        try:
            (bg_dir / name).unlink()
        except OSError:
            pass


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
    if style not in _STYLE_TO_URL:
        log.warning("Estilo de fondo desconocido: %s", style)
        return None
    net_path = Path(net_path)
    if not net_path.is_file():
        log.warning("Red inexistente para fondo: %s", net_path)
        return None

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings_out = out_dir / "viewsettings_bg.xml"
    decals_raw = out_dir / "viewsettings_decals.xml"
    tile_limit = max(4, int(max_tiles))

    # Reuse cache only when it was generated for the same network extent/style.
    if not force and background_matches_net(out_dir, net_path, style=style, max_tiles=tile_limit):
        return settings_out

    # runs/current is reused across cities; stale backgrounds must not survive.
    _clear_background_dir(out_dir)

    sumo = sumo or detect_sumo()
    script = _tile_get_script(sumo)
    if script is None:
        log.warning("tileGet.py no encontrado en SUMO_HOME/tools")
        return None

    ensure_sumolib_on_path(sumo.home)

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
        str(tile_limit),
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

    tiles = (
        list(out_dir.glob("tile*.png"))
        + list(out_dir.glob("tile*.jpeg"))
        + list(out_dir.glob("tile*.jpg"))
    )
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
    _write_background_meta(out_dir, net_path=net_path, style=style, max_tiles=tile_limit)
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

    for pat in ("tile*.png", "tile*.jpeg", "tile*.jpg", "viewsettings_decals.xml", _CACHE_META):
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
