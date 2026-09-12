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
from .logging_config import get_logger

log = get_logger("osm_fetch")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OSM_DIR = DATA_DIR / "osm"
CACHE_DIR = DATA_DIR / "cache"

# Native tools (osmium/netconvert) often fail on non-ASCII Windows paths (e.g. OneDrive "Geomática").
SAFE_ROOT = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "OptiTraffic"
SAFE_OSM = SAFE_ROOT / "osm"

GEOFABRIK_BASE = "https://download.geofabrik.de/"
GEOFABRIK_INDEX_URL = "https://download.geofabrik.de/index-v1.json"
GEOFABRIK_INDEX_CACHE = CACHE_DIR / "geofabrik_index-v1.json"
GEOFABRIK_INDEX_TTL_SEC = 24 * 3600
# Soft cap for full-country PBF downloads (override with OPTITRAFFIC_MAX_PBF_MB).
MAX_GEOFABRIK_PBF_BYTES = int(os.environ.get("OPTITRAFFIC_MAX_PBF_MB", "500")) * 1024 * 1024
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


def _load_geofabrik_index(force: bool = False) -> Optional[dict]:
    """Download/cache Geofabrik index-v1.json (TTL 1 day)."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if (
            not force
            and GEOFABRIK_INDEX_CACHE.is_file()
            and (time.time() - GEOFABRIK_INDEX_CACHE.stat().st_mtime) < GEOFABRIK_INDEX_TTL_SEC
        ):
            return json.loads(GEOFABRIK_INDEX_CACHE.read_text(encoding="utf-8"))
        r = requests.get(
            GEOFABRIK_INDEX_URL,
            headers=_HTTP_HEADERS,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        GEOFABRIK_INDEX_CACHE.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception:
        log.warning("Could not load Geofabrik index", exc_info=True)
        if GEOFABRIK_INDEX_CACHE.is_file():
            try:
                return json.loads(GEOFABRIK_INDEX_CACHE.read_text(encoding="utf-8"))
            except Exception:
                log.warning("Stale Geofabrik index unreadable", exc_info=True)
        return None


def resolve_geofabrik_path_from_iso(country_code: str) -> Optional[str]:
    """
    Resolve relative extract path (without -latest.osm.pbf) from ISO alpha-2.
    Prefers the smallest country-level feature matching the ISO code.
    """
    code = (country_code or "").strip().lower()
    if len(code) != 2:
        return None
    index = _load_geofabrik_index()
    if not index:
        return None
    features = index.get("features") or []
    candidates: list[tuple[float, str]] = []
    for feat in features:
        props = feat.get("properties") or {}
        ids = props.get("ids") or {}
        iso = props.get("iso3166-1:alpha2") or props.get("ISO3166-1:alpha2")
        # index-v1 uses list under ids or properties
        codes = []
        if isinstance(iso, list):
            codes = [str(c).lower() for c in iso]
        elif iso:
            codes = [str(iso).lower()]
        if code not in codes:
            # Also check nested
            nested = ids.get("iso3166-1:alpha2") if isinstance(ids, dict) else None
            if isinstance(nested, list):
                codes = [str(c).lower() for c in nested]
            elif nested:
                codes = [str(nested).lower()]
            if code not in codes:
                continue
        pbf = props.get("urls", {}).get("pbf") if isinstance(props.get("urls"), dict) else None
        pbf = pbf or props.get("pbf")
        if not pbf:
            continue
        # urls like https://download.geofabrik.de/central-america/costa-rica-latest.osm.pbf
        rel = str(pbf).replace(GEOFABRIK_BASE, "").replace("-latest.osm.pbf", "")
        rel = rel.lstrip("/")
        # Prefer smaller extracts (country over continent): shorter path / name
        area = float(props.get("area") or props.get("extent") or 1e12)
        candidates.append((area, rel))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], len(x[1])))
    return candidates[0][1]


def resolve_geofabrik_path(country: str, country_code: str = "") -> str:
    key = normalize_country(country)
    # 1) Dynamic index via ISO (preferred)
    if country_code:
        dyn = resolve_geofabrik_path_from_iso(country_code)
        if dyn:
            return dyn
    if not key and not country_code:
        raise FileNotFoundError(
            "País vacío: indique el país de la zona (p. ej. Costa Rica) para Geofabrik."
        )
    # 2) Offline hardcoded fallback
    if key in COUNTRY_EXTRACTS:
        return COUNTRY_EXTRACTS[key]
    for known, rel in COUNTRY_EXTRACTS.items():
        if len(known) < 4:
            continue
        if known in key or (len(key) >= 4 and key in known):
            return rel
    # 3) Last resort: try matching feature name in index
    index = _load_geofabrik_index()
    if index and key:
        for feat in index.get("features") or []:
            props = feat.get("properties") or {}
            name = normalize_country(str(props.get("name") or ""))
            if name == key or (len(key) >= 4 and key in name):
                urls = props.get("urls") or {}
                pbf = urls.get("pbf") if isinstance(urls, dict) else None
                if pbf:
                    rel = str(pbf).replace(GEOFABRIK_BASE, "").replace("-latest.osm.pbf", "")
                    return rel.lstrip("/")
    sample = ", ".join(listed_countries()[:12])
    raise FileNotFoundError(
        f"País '{country}' no está mapeado a un extracto Geofabrik. "
        f"Ejemplos: {sample}… "
        "Corrija el campo País en el paso 1. Catálogo: https://download.geofabrik.de/"
    )


def geofabrik_url(country: str, country_code: str = "") -> str:
    rel = resolve_geofabrik_path(country, country_code=country_code)
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


def _atomic_publish(src: Path, dest: Path) -> None:
    """Replace dest with src; works across Windows drives (no os.replace cross-volume)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dest)
        return
    except OSError:
        pass
    # WinError 17: cannot move file to a different disk drive
    if dest.exists():
        try:
            dest.unlink()
        except OSError:
            alt = dest.with_name(dest.stem + f".new{os.getpid()}" + dest.suffix)
            shutil.copy2(src, alt)
            try:
                src.unlink()
            except OSError:
                pass
            shutil.copy2(alt, dest)
            try:
                alt.unlink()
            except OSError:
                pass
            return
    shutil.copy2(src, dest)
    try:
        src.unlink()
    except OSError:
        pass


def download_extract(
    country: str,
    force: bool = False,
    timeout: int = 600,
    country_code: str = "",
    max_bytes: Optional[int] = None,
) -> Path:
    """Download country/region PBF into ASCII-safe cache. Returns local path.

    Aborts if Content-Length or streamed size exceeds max_bytes
    (default MAX_GEOFABRIK_PBF_BYTES / OPTITRAFFIC_MAX_PBF_MB).
    """
    ensure_dirs()
    limit = MAX_GEOFABRIK_PBF_BYTES if max_bytes is None else int(max_bytes)
    rel = resolve_geofabrik_path(country, country_code=country_code).replace("/", "__")
    dest = SAFE_OSM / f"{rel}-latest.osm.pbf"
    project_dest = OSM_DIR / dest.name

    if dest.exists() and not force and _looks_like_osm_pbf(dest):
        if dest.stat().st_size > limit:
            raise RuntimeError(
                f"Extracto local `{dest.name}` ({dest.stat().st_size / 1e6:.0f} MB) "
                f"supera el tope de {limit / 1e6:.0f} MB. Use Overpass o suba "
                f"OPTITRAFFIC_MAX_PBF_MB."
            )
        return dest
    if dest.exists() and force:
        try:
            dest.unlink()
        except OSError:
            pass

    url = geofabrik_url(country, country_code=country_code)
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
        cl_raw = r.headers.get("Content-Length")
        if cl_raw:
            try:
                content_len = int(cl_raw)
            except ValueError:
                content_len = 0
            if content_len > limit:
                raise RuntimeError(
                    f"Extracto Geofabrik demasiado grande "
                    f"({content_len / 1e6:.0f} MB > tope {limit / 1e6:.0f} MB) "
                    f"para '{country}'. Prefiera fuente Overpass/auto, o defina "
                    f"OPTITRAFFIC_MAX_PBF_MB. URL: {url}"
                )
        tmp = dest.with_suffix(".partial")
        written = 0
        try:
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > limit:
                        raise RuntimeError(
                            f"Descarga Geofabrik abortada: superó "
                            f"{limit / 1e6:.0f} MB al escribir `{dest.name}`. "
                            "Prefiera Overpass/auto o suba OPTITRAFFIC_MAX_PBF_MB."
                        )
                    f.write(chunk)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
        if not _looks_like_osm_pbf(tmp):
            try:
                tmp.unlink()
            except OSError:
                pass
            raise RuntimeError(
                f"Descarga Geofabrik inválida (no es un .osm.pbf): {url}. "
                "Revise el país o intente de nuevo."
            )
        _atomic_publish(tmp, dest)

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


def _xml_esc(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def clip_pbf_with_osmium(src: Path, polygon: Polygon, out: Path) -> Path:
    """
    Two-pass bbox clip with pyosmium.

    Writes OSM XML with plain Python I/O (no osmium.SimpleWriter) to avoid
    Windows WinError 17: \"cannot move the file to a different disk drive\".
    """
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
            # Prefer highways (netconvert passenger network); keep others only if tagged highway
            tags = {t.k: t.v for t in w.tags}
            if "highway" not in tags:
                return
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

    class Pass2Xml(osmium.SimpleHandler):
        def __init__(self, fh, keep_ways: set[int], need_nodes: set[int]):
            super().__init__()
            self.fh = fh
            self.keep_ways = keep_ways
            self.need_nodes = need_nodes
            self.n_nodes = 0
            self.n_ways = 0

        def node(self, n):
            if n.id not in self.need_nodes:
                return
            try:
                if not n.location.valid():
                    return
                lat, lon = n.location.lat, n.location.lon
            except Exception:
                return
            self.fh.write(
                f'  <node id="{n.id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"/>\n'
            )
            self.n_nodes += 1

        def way(self, w):
            if w.id not in self.keep_ways:
                return
            self.fh.write(f'  <way id="{w.id}" version="1">\n')
            for nr in w.nodes:
                self.fh.write(f'    <nd ref="{nr.ref}"/>\n')
            for t in w.tags:
                self.fh.write(
                    f'    <tag k="{_xml_esc(t.k)}" v="{_xml_esc(t.v)}"/>\n'
                )
            self.fh.write("  </way>\n")
            self.n_ways += 1

    out_safe.parent.mkdir(parents=True, exist_ok=True)
    if out_safe.suffix.lower() == ".pbf":
        out_safe = out_safe.with_suffix(".osm")

    p1 = Pass1()
    p1.apply_file(str(src_safe), locations=True)
    if not p1.keep_ways:
        raise RuntimeError("osmium: ningún highway en el bbox")

    # Always write to a unique work file, then publish (avoids locked target names).
    work = out_safe.parent / f"{out_safe.stem}.{os.getpid()}.{int(time.time())}.work.osm"
    if work.exists():
        try:
            work.unlink()
        except OSError:
            pass

    with work.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write('<osm version="0.6" generator="OptiTraffic-osmium-clip">\n')
        fh.write(
            f'  <bounds minlat="{south:.7f}" minlon="{west:.7f}" '
            f'maxlat="{north:.7f}" maxlon="{east:.7f}"/>\n'
        )
        p2 = Pass2Xml(fh, p1.keep_ways, p1.need_nodes)
        p2.apply_file(str(src_safe), locations=True)
        fh.write("</osm>\n")

    if p2.n_ways < 1 or work.stat().st_size < 200:
        try:
            work.unlink()
        except OSError:
            pass
        raise RuntimeError("osmium produjo un archivo vacío")

    try:
        _atomic_publish(work, out_safe)
        result = out_safe
    except OSError:
        # Target locked: keep the work file as the usable clip
        result = work

    if not result.exists() or result.stat().st_size < 200:
        raise RuntimeError("osmium produjo un archivo vacío")

    if result != out and path_is_safe(out):
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result, out)
            return out
        except OSError:
            return result
    return result


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
            log.warning("Overpass failed for %s", area.label, exc_info=True)
            if source == "overpass":
                raise RuntimeError(
                    "Overpass falló y la fuente forzada es solo Overpass.\n" + str(e)
                ) from e

    # 2) Geofabrik + osmium
    extract: Optional[Path] = None
    if source in ("auto", "geofabrik"):
        try:
            # Reuse existing clip when present (avoids Windows file locks / long re-clip)
            if (
                not force_download
                and clipped.exists()
                and clipped.stat().st_size > 500
            ):
                try:
                    extract = download_extract(
                        area.country,
                        force=False,
                        country_code=getattr(area, "country_code", "") or "",
                    )
                except Exception:
                    log.warning("download_extract reuse failed", exc_info=True)
                    extract = clipped
                return extract, clipped

            extract = download_extract(
                area.country,
                force=force_download,
                country_code=getattr(area, "country_code", "") or "",
            )
            legacy = OSM_DIR / extract.name
            if legacy.exists() and (not extract.exists() or extract.stat().st_size < legacy.stat().st_size):
                extract = to_safe_path(legacy, extract.name)
            clipped = clip_pbf_with_osmium(extract, area.polygon, clipped)
            return extract, clipped
        except Exception as e:
            errors.append(f"Geofabrik/osmium: {e}")
            if extract is None:
                try:
                    rel = resolve_geofabrik_path(
                        area.country,
                        country_code=getattr(area, "country_code", "") or "",
                    ).replace("/", "__")
                    extract = SAFE_OSM / f"{rel}-latest.osm.pbf"
                except Exception:
                    log.warning("resolve_geofabrik_path fallback failed", exc_info=True)
                    extract = None

        # 3) osmconvert
        try:
            if extract is None or not extract.exists():
                extract = download_extract(
                    area.country,
                    force=force_download,
                    country_code=getattr(area, "country_code", "") or "",
                )
            clipped_pbf = SAFE_OSM / f"{slug}_clip.osm.pbf"
            clipped = clip_pbf_bbox_osmconvert(extract, area.polygon, clipped_pbf)
            return extract, clipped
        except Exception as e:
            errors.append(f"osmconvert: {e}")

    raise RuntimeError(
        "No se pudo obtener OSM de la zona. Detalles:\n- " + "\n- ".join(errors)
    )
