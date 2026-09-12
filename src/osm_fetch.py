"""Download OSM extracts (Geofabrik / Overpass) and clip to study polygon."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin

import requests
from shapely.geometry import Polygon

from .area import StudyArea

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OSM_DIR = DATA_DIR / "osm"
CACHE_DIR = DATA_DIR / "cache"

# Native tools (osmium/netconvert) often fail on non-ASCII Windows paths (e.g. OneDrive "Geomática").
SAFE_ROOT = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "OptiTraffic"
SAFE_OSM = SAFE_ROOT / "osm"

GEOFABRIK_BASE = "https://download.geofabrik.de/"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
_HTTP_HEADERS = {
    "User-Agent": "OptiTraffic/1.0 (OSM extract client; contact: local)",
    "Accept": "*/*",
}

# Country / region name (lowercase, no accents) -> Geofabrik relative path
# WITHOUT the trailing "-latest" (geofabrik_url adds "-latest.osm.pbf").
COUNTRY_EXTRACTS: dict[str, str] = {
    "costa rica": "central-america/costa-rica",
    "cr": "central-america/costa-rica",
    "panama": "central-america/panama",
    "nicaragua": "central-america/nicaragua",
    "guatemala": "central-america/guatemala",
    "honduras": "central-america/honduras",
    "el salvador": "central-america/el-salvador",
    "belize": "central-america/belize",
    "mexico": "north-america/mexico",
    "mexico city": "north-america/mexico",
    "colombia": "south-america/colombia",
    "peru": "south-america/peru",
    "chile": "south-america/chile",
    "argentina": "south-america/argentina",
    "brazil": "south-america/brazil",
    "brasil": "south-america/brazil",
    "ecuador": "south-america/ecuador",
    "bolivia": "south-america/bolivia",
    "paraguay": "south-america/paraguay",
    "uruguay": "south-america/uruguay",
    "venezuela": "south-america/venezuela",
    "spain": "europe/spain",
    "espana": "europe/spain",
    "france": "europe/france",
    "germany": "europe/germany",
    "deutschland": "europe/germany",
    "italy": "europe/italy",
    "italia": "europe/italy",
    "portugal": "europe/portugal",
    "united kingdom": "europe/great-britain",
    "uk": "europe/great-britain",
    "great britain": "europe/great-britain",
    "united states": "north-america/us",
    "usa": "north-america/us",
    "us": "north-america/us",
    "canada": "north-america/canada",
}


def ensure_dirs() -> None:
    OSM_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SAFE_OSM.mkdir(parents=True, exist_ok=True)


def to_safe_path(src: Path, dest_name: Optional[str] = None) -> Path:
    """Copy file into ASCII-only LOCALAPPDATA path for native OSM/SUMO tools."""
    ensure_dirs()
    dest = SAFE_OSM / (dest_name or src.name)
    if dest.exists() and src.exists():
        try:
            if dest.stat().st_size == src.stat().st_size and dest.stat().st_mtime >= src.stat().st_mtime:
                return dest
        except OSError:
            pass
    if src.resolve() == dest.resolve():
        return dest
    shutil.copy2(src, dest)
    return dest


def path_is_safe(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def normalize_country(country: str) -> str:
    """Lowercase, strip accents, collapse spaces."""
    text = country.strip().lower()
    # Common accented chars without depending on unidecode
    trans = str.maketrans(
        {
            "á": "a",
            "à": "a",
            "ä": "a",
            "â": "a",
            "é": "e",
            "è": "e",
            "ë": "e",
            "ê": "e",
            "í": "i",
            "ì": "i",
            "ï": "i",
            "î": "i",
            "ó": "o",
            "ò": "o",
            "ö": "o",
            "ô": "o",
            "ú": "u",
            "ù": "u",
            "ü": "u",
            "û": "u",
            "ñ": "n",
            "ç": "c",
        }
    )
    text = text.translate(trans)
    return re.sub(r"\s+", " ", text)


def listed_countries() -> list[str]:
    # Unique display names (skip short aliases)
    names = sorted({k for k in COUNTRY_EXTRACTS if len(k) > 3})
    return names


def resolve_geofabrik_path(country: str) -> str:
    key = normalize_country(country)
    if not key:
        raise FileNotFoundError(
            "País vacío: indique el país de la zona (p. ej. Costa Rica) para Geofabrik."
        )
    if key in COUNTRY_EXTRACTS:
        return COUNTRY_EXTRACTS[key]
    # Soft match only for longer names (avoid "us" matching inside other words)
    for known, rel in COUNTRY_EXTRACTS.items():
        if len(known) < 4:
            continue
        if known in key or (len(key) >= 4 and key in known):
            return rel
    sample = ", ".join(listed_countries()[:12])
    raise FileNotFoundError(
        f"País '{country}' no está mapeado a un extracto Geofabrik. "
        f"Ejemplos: {sample}… "
        "Corrija el campo País en el paso 1. Catálogo: https://download.geofabrik.de/"
    )


def geofabrik_url(country: str) -> str:
    rel = resolve_geofabrik_path(country)
    return urljoin(GEOFABRIK_BASE, f"{rel}-latest.osm.pbf")


def _looks_like_osm_pbf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(32)
        if not head or head.startswith(b"<!DOCTYPE") or head.startswith(b"<html"):
            return False
        # OSM PBF blobs start with a protobuf length prefix; require non-tiny binary
        return path.stat().st_size > 10_000 and b"<" not in head[:1]
    except OSError:
        return False


def download_extract(country: str, force: bool = False, timeout: int = 600) -> Path:
    """Download country/region PBF into ASCII-safe cache. Returns local path."""
    ensure_dirs()
    rel = resolve_geofabrik_path(country).replace("/", "__")
    dest = SAFE_OSM / f"{rel}-latest.osm.pbf"
    project_dest = OSM_DIR / dest.name

    if dest.exists() and not force and _looks_like_osm_pbf(dest):
        return dest
    if dest.exists() and force:
        try:
            dest.unlink()
        except OSError:
            pass

    url = geofabrik_url(country)
    meta = CACHE_DIR / "geofabrik_downloads.json"
    history = {}
    if meta.exists():
        try:
            history = json.loads(meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = {}

    with requests.get(url, stream=True, timeout=timeout, headers=_HTTP_HEADERS, allow_redirects=True) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        final_url = str(r.url)
        if r.status_code == 404 or final_url.rstrip("/").endswith("geofabrik.de"):
            raise FileNotFoundError(
                f"Extract Geofabrik no encontrado para '{country}': {url}. "
                "Geofabrik devolvió la página índice (URL inválida). "
                "Corrija el país. Catálogo: https://download.geofabrik.de/"
            )
        if "text/html" in ctype:
            raise FileNotFoundError(
                f"Geofabrik respondió HTML en lugar del PBF ({url} → {final_url}). "
                "El país no coincide con un dataset publicado."
            )
        r.raise_for_status()
        tmp = dest.with_suffix(".partial")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        if not _looks_like_osm_pbf(tmp):
            try:
                tmp.unlink()
            except OSError:
                pass
            raise RuntimeError(
                f"Descarga Geofabrik inválida (no es un .osm.pbf): {url}. "
                "Revise el país o intente de nuevo."
            )
        tmp.replace(dest)

    try:
        shutil.copy2(dest, project_dest)
    except OSError:
        pass

    history[str(dest.name)] = {"url": url, "ts": time.time(), "bytes": dest.stat().st_size}
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return dest


def _bbox(polygon: Polygon, pad: float = 0.001) -> Tuple[float, float, float, float]:
    minx, miny, maxx, maxy = polygon.bounds
    return (minx - pad, miny - pad, maxx + pad, maxy + pad)


def fetch_overpass_osm(polygon: Polygon, out: Path, timeout: int = 180) -> Path:
    """Download highways for the study bbox via Overpass API."""
    ensure_dirs()
    west, south, east, north = _bbox(polygon)
    query = f"""
[out:xml][timeout:{timeout}];
(
  way["highway"]({south},{west},{north},{east});
  node(w);
);
out body;
""".strip()

    last_err: Optional[Exception] = None
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                r = requests.post(
                    url,
                    data={"data": query},
                    timeout=timeout + 30,
                    headers=_HTTP_HEADERS,
                )
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                if b"<osm" not in r.content[:500] and b"<?xml" not in r.content[:200]:
                    raise RuntimeError(f"Respuesta Overpass inesperada desde {url}")
                out.parent.mkdir(parents=True, exist_ok=True)
                # Always write to safe path first
                safe_out = SAFE_OSM / out.name if not path_is_safe(out) else out
                safe_out.parent.mkdir(parents=True, exist_ok=True)
                safe_out.write_bytes(r.content)
                if safe_out.stat().st_size < 500:
                    raise RuntimeError("Overpass devolvió un OSM casi vacío")
                if safe_out != out:
                    try:
                        out.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(safe_out, out)
                    except OSError:
                        return safe_out
                    return out
                return safe_out
            except Exception as e:
                last_err = e
                continue
        time.sleep(3)
    raise RuntimeError(f"Overpass falló: {last_err}")


def clip_pbf_with_osmium(src: Path, polygon: Polygon, out: Path) -> Path:
    """Two-pass bbox clip with pyosmium (requires ASCII-safe paths on Windows)."""
    import osmium

    src_safe = src if path_is_safe(src) else to_safe_path(src)
    out_safe = out if path_is_safe(out) else (SAFE_OSM / out.name)
    west, south, east, north = _bbox(polygon)

    def _in(lon: float, lat: float) -> bool:
        return west <= lon <= east and south <= lat <= north

    class Pass1(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.keep_ways: set[int] = set()
            self.need_nodes: set[int] = set()

        def way(self, w):
            keep = False
            refs = []
            for nr in w.nodes:
                refs.append(nr.ref)
                try:
                    if nr.location.valid() and _in(nr.location.lon, nr.location.lat):
                        keep = True
                except Exception:
                    pass
            if keep:
                self.keep_ways.add(w.id)
                self.need_nodes.update(refs)

    class Pass2(osmium.SimpleHandler):
        def __init__(self, writer, keep_ways, need_nodes):
            super().__init__()
            self.writer = writer
            self.keep_ways = keep_ways
            self.need_nodes = need_nodes

        def node(self, n):
            if n.id in self.need_nodes:
                self.writer.add_node(n)

        def way(self, w):
            if w.id in self.keep_ways:
                self.writer.add_way(w)

    out_safe.parent.mkdir(parents=True, exist_ok=True)
    if out_safe.suffix.lower() == ".pbf":
        out_safe = out_safe.with_suffix(".osm")

    p1 = Pass1()
    p1.apply_file(str(src_safe), locations=True)
    if not p1.keep_ways:
        raise RuntimeError("osmium: ningún way en el bbox")

    writer = osmium.SimpleWriter(str(out_safe))
    try:
        p2 = Pass2(writer, p1.keep_ways, p1.need_nodes)
        p2.apply_file(str(src_safe), locations=True)
    finally:
        writer.close()

    if not out_safe.exists() or out_safe.stat().st_size < 200:
        raise RuntimeError("osmium produjo un archivo vacío")

    if out_safe != out:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_safe, out)
            return out
        except OSError:
            return out_safe
    return out_safe


def clip_pbf_bbox_osmconvert(src: Path, polygon: Polygon, out: Path, osmconvert: Optional[Path] = None) -> Path:
    import subprocess

    bin_path = osmconvert or shutil.which("osmconvert")
    if not bin_path:
        raise FileNotFoundError("osmconvert no disponible")
    src_safe = src if path_is_safe(src) else to_safe_path(src)
    out_safe = out if path_is_safe(out) else (SAFE_OSM / out.name)
    west, south, east, north = _bbox(polygon, pad=0.0)
    out_safe.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(bin_path),
        str(src_safe),
        f"-b={west},{south},{east},{north}",
        f"-o={out_safe}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)
    return out_safe


def prepare_clipped_osm(
    area: StudyArea,
    force_download: bool = False,
    source: str = "auto",
) -> Tuple[Path, Path]:
    """
    Obtain OSM for the study area (ASCII-safe paths for native tools).

    source:
      - auto: Overpass first, then Geofabrik+osmium, then osmconvert
      - overpass: only Overpass
      - geofabrik: only Geofabrik (+ osmium / osmconvert)

    Returns (source_path, clipped_osm_path).
    """
    ensure_dirs()
    slug = re.sub(r"[^a-z0-9]+", "_", f"{area.city}_{area.country}".lower()).strip("_")
    clipped = SAFE_OSM / f"{slug}_clip.osm"
    errors: list[str] = []
    source = (source or "auto").lower().strip()

    # 1) Overpass
    if source in ("auto", "overpass"):
        try:
            if force_download or not clipped.exists() or clipped.stat().st_size < 500:
                clipped = fetch_overpass_osm(area.polygon, clipped)
            return clipped, clipped
        except Exception as e:
            errors.append(f"Overpass: {e}")
            if source == "overpass":
                raise RuntimeError(
                    "Overpass falló y la fuente forzada es solo Overpass.\n" + str(e)
                ) from e

    # 2) Geofabrik + osmium
    extract: Optional[Path] = None
    if source in ("auto", "geofabrik"):
        try:
            extract = download_extract(area.country, force=force_download)
            legacy = OSM_DIR / extract.name
            if legacy.exists() and (not extract.exists() or extract.stat().st_size < legacy.stat().st_size):
                extract = to_safe_path(legacy, extract.name)
            clipped = clip_pbf_with_osmium(extract, area.polygon, clipped)
            return extract, clipped
        except Exception as e:
            errors.append(f"Geofabrik/osmium: {e}")
            if extract is None:
                try:
                    rel = resolve_geofabrik_path(area.country).replace("/", "__")
                    extract = SAFE_OSM / f"{rel}-latest.osm.pbf"
                except Exception:
                    extract = None

        # 3) osmconvert
        try:
            if extract is None or not extract.exists():
                extract = download_extract(area.country, force=force_download)
            clipped_pbf = SAFE_OSM / f"{slug}_clip.osm.pbf"
            clipped = clip_pbf_bbox_osmconvert(extract, area.polygon, clipped_pbf)
            return extract, clipped
        except Exception as e:
            errors.append(f"osmconvert: {e}")

    raise RuntimeError(
        "No se pudo obtener OSM de la zona. Detalles:\n- " + "\n- ".join(errors)
    )
