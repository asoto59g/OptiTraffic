"""Run SUMO via TraCI and collect edge congestion KPIs."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .traffic_params import CITY_MAX_SPEED_MS, CONGESTION_SPEED_MS, desired_speed_ms
from .osm_fetch import SAFE_ROOT, path_is_safe, to_safe_path
from .sumo_env import SumoEnv, detect_sumo, ensure_sumolib_on_path
from .logging_config import get_logger
from .video_camera import (
    DEFAULT_SEGMENT_S,
    apply_camera_shot,
    load_net_bounds_from_cfg,
    plan_camera_shot,
)

log = get_logger("simulate")

SAFE_RUNS = SAFE_ROOT / "runs"


@dataclass
class EdgeKPI:
    edge_id: str
    mean_speed: float = 0.0
    max_occupancy: float = 0.0
    mean_occupancy: float = 0.0
    waiting_time: float = 0.0
    sample_count: int = 0
    congested: bool = False


@dataclass
class SimResult:
    duration_s: int
    vehicle_steps: int
    total_waiting: float
    mean_speed: float
    pct_edges_congested: float
    edges: dict[str, EdgeKPI] = field(default_factory=dict)
    tomtom_correlation: Optional[float] = None
    video_path: Optional[str] = None
    frames_dir: Optional[str] = None
    frames_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SimResult":
        edges_raw = data.get("edges") or {}
        edges: dict[str, EdgeKPI] = {}
        for eid, ev in edges_raw.items():
            if isinstance(ev, EdgeKPI):
                edges[str(eid)] = ev
            elif isinstance(ev, dict):
                edges[str(eid)] = EdgeKPI(**ev)
        return cls(
            duration_s=int(data.get("duration_s") or 0),
            vehicle_steps=int(data.get("vehicle_steps") or 0),
            total_waiting=float(data.get("total_waiting") or 0.0),
            mean_speed=float(data.get("mean_speed") or 0.0),
            pct_edges_congested=float(data.get("pct_edges_congested") or 0.0),
            edges=edges,
            tomtom_correlation=data.get("tomtom_correlation"),
            video_path=data.get("video_path"),
            frames_dir=data.get("frames_dir"),
            frames_count=int(data.get("frames_count") or 0),
        )


def write_sim_progress(path: Path, **fields: Any) -> None:
    """Atomic-ish progress JSON for background TraCI jobs (Streamlit polls this)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": time.time(), **fields}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_sim_progress(path: Path) -> Optional[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_sumocfg(
    cfg_path: Path,
    net_path: Path,
    routes_path: Path,
    additional_files: Optional[list[Path]] = None,
    begin: int = 0,
    end: int = 1800,
    gui_settings_file: Optional[Path] = None,
) -> Path:
    # Native SUMO on Windows fails with non-ASCII paths (OneDrive "Geomática")
    net_safe = net_path if path_is_safe(net_path) else to_safe_path(net_path)
    routes_safe = routes_path if path_is_safe(routes_path) else to_safe_path(routes_path)
    add_safe: list[Path] = []
    if additional_files:
        for p in additional_files:
            add_safe.append(p if path_is_safe(p) else to_safe_path(p))
    gui_safe: Optional[Path] = None
    if gui_settings_file and Path(gui_settings_file).is_file():
        gui_safe = (
            gui_settings_file
            if path_is_safe(gui_settings_file)
            else to_safe_path(gui_settings_file)
        )

    if not path_is_safe(cfg_path):
        SAFE_RUNS.mkdir(parents=True, exist_ok=True)
        cfg_path = SAFE_RUNS / cfg_path.name

    root = ET.Element("configuration")
    inp = ET.SubElement(root, "input")
    ET.SubElement(inp, "net-file", value=str(net_safe).replace("\\", "/"))
    ET.SubElement(inp, "route-files", value=str(routes_safe).replace("\\", "/"))
    if add_safe:
        joined = ",".join(str(p).replace("\\", "/") for p in add_safe)
        ET.SubElement(inp, "additional-files", value=joined)
    time_el = ET.SubElement(root, "time")
    ET.SubElement(time_el, "begin", value=str(begin))
    ET.SubElement(time_el, "end", value=str(end))
    proc = ET.SubElement(root, "processing")
    ET.SubElement(proc, "time-to-teleport", value="120")
    ET.SubElement(proc, "collision.action", value="warn")
    ET.SubElement(proc, "ignore-junction-blocker", value="0")
    if gui_safe is not None:
        gui = ET.SubElement(root, "gui_only")
        try:
            gui_val = str(Path(gui_safe).resolve().relative_to(Path(cfg_path).resolve().parent))
        except ValueError:
            gui_val = str(gui_safe)
        ET.SubElement(
            gui,
            "gui-settings-file",
            value=gui_val.replace("\\", "/"),
        )

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(cfg_path, encoding="utf-8", xml_declaration=True)
    return cfg_path


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = sum((x - mx) ** 2 for x in xs) ** 0.5
    deny = sum((y - my) ** 2 for y in ys) ** 0.5
    if denx == 0 or deny == 0:
        return None
    return num / (denx * deny)


def _close_traci(label: str = "optitraffic") -> None:
    """Force-close any active TraCI connection (avoids 'already active')."""
    try:
        import traci
    except ImportError:
        return
    for lab in (label, "default"):
        try:
            traci.switch(lab)
            traci.close(wait=False)
        except Exception:
            log.debug("_close_traci: no active connection for label %r", lab, exc_info=True)
    try:
        traci.close(wait=False)
    except Exception:
        log.debug("_close_traci: default close no-op", exc_info=True)
    time.sleep(0.3)


def _sumo_stderr_probe(sumo_bin: Path, cfg_path: Path) -> str:
    """Run SUMO briefly without TraCI to capture the real load error."""
    import subprocess

    try:
        r = subprocess.run(
            [
                str(sumo_bin),
                "-c",
                str(cfg_path),
                "--begin",
                "0",
                "--end",
                "1",
                "--no-step-log",
                "true",
                "--no-warnings",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        return str(e)
    err = (r.stderr or "").strip()
    out = (r.stdout or "").strip()
    msg = err or out
    if not msg:
        msg = f"SUMO exit code {r.returncode}"
    # Keep message short for UI
    lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
    return " | ".join(lines[:6])


def find_ffmpeg() -> Optional[Path]:
    which = shutil.which("ffmpeg")
    return Path(which) if which else None


def encode_frames_to_mp4(
    frames_dir: Path,
    out_path: Path,
    *,
    fps: float = 5.0,
) -> Path:
    """Assemble frame_XXXXXX.png into an H.264 MP4 via ffmpeg."""
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise FileNotFoundError(
            "ffmpeg no está en PATH. Instálelo o use solo los PNG en la carpeta de frames."
        )
    frames = sorted(frames_dir.glob("frame_*.png"))
    if not frames:
        raise FileNotFoundError(f"No hay frames PNG en {frames_dir}")
    pattern = str(frames_dir / "frame_%06d.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        try:
            out_path.unlink()
        except OSError:
            pass
    # libx264 + yuv420p requires even width/height (window grabs are often odd).
    cmd = [
        str(ffmpeg),
        "-y",
        "-framerate",
        str(fps),
        "-i",
        pattern,
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 1000:
        if out_path.exists() and out_path.stat().st_size < 1000:
            try:
                out_path.unlink()
            except OSError:
                pass
        raise RuntimeError(
            "ffmpeg falló al crear el video:\n" + (r.stderr or r.stdout or "sin detalle")[:800]
        )
    return out_path


def _traci_sumo_pid(label: str) -> Optional[int]:
    """PID of the sumo/sumo-gui process started by TraCI (if available)."""
    try:
        import traci

        conn = traci.getConnection(label)
    except Exception:
        return None
    # TraCI Connection stores Popen as `_process` (not `_sumoProcess`).
    for attr in ("_process", "process", "_sumoProcess", "sumoProcess"):
        proc = getattr(conn, attr, None)
        if proc is not None and getattr(proc, "pid", None):
            try:
                return int(proc.pid)
            except (TypeError, ValueError):
                return None
    try:
        from traci import connection as traci_connection

        for _lab, c in getattr(traci_connection, "_connections", {}).items():
            proc = getattr(c, "_process", None)
            if proc is not None and getattr(proc, "pid", None):
                return int(proc.pid)
    except Exception:
        pass
    return None


def _is_sumo_gui_window_title(title: str) -> bool:
    """True only for real sumo-gui windows — never Streamlit/browser/IDE."""
    low = (title or "").lower()
    if not low:
        return False
    deny = (
        "chrome",
        "msedge",
        "firefox",
        "streamlit",
        "localhost",
        "cursor",
        "visual studio",
        "code -",
        "powershell",
        "cmd.exe",
        "explorer",
    )
    if any(d in low for d in deny):
        return False
    if ".sumocfg" in low:
        return True
    # Typical title: "… - SUMO 1.27.1"
    if "sumo" in low and any(ch.isdigit() for ch in low):
        return True
    return False


def _hwnd_title(hwnd: int) -> str:
    import ctypes

    user32 = ctypes.windll.user32
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return (buf.value or "").strip()


def _find_sumo_gui_hwnd(pid: Optional[int] = None) -> Optional[int]:
    """Find sumo-gui window by TraCI PID first, then strict title (Windows)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    by_pid: list[tuple[int, int]] = []
    by_title: list[tuple[int, int]] = []

    def _cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _hwnd_title(hwnd)
        if not title:
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w = int(rect.right) - int(rect.left)
        h = int(rect.bottom) - int(rect.top)
        if w < 200 or h < 150:
            return True
        area = w * h
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if pid and int(proc_id.value) == int(pid):
            by_pid.append((area, int(hwnd)))
        elif _is_sumo_gui_window_title(title):
            by_title.append((area, int(hwnd)))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    pool = by_pid or by_title
    if not pool:
        return None
    pool.sort(reverse=True)
    return pool[0][1]


def _write_record_gui_settings(path: Path) -> Path:
    """GUI settings for recording: readable vehicles without cartoon sizing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<viewsettings>
    <scheme name="real world">
        <vehicles vehicle_exaggeration="2.5" vehicle_minSize="3" vehicle_constantSize="0"
                  vehicle_quality="2" showBlinker="0"/>
        <persons person_exaggeration="1" person_minSize="1" person_constantSize="0"/>
        <edges edge_exaggeration="1.0"/>
    </scheme>
    <delay value="100"/>
</viewsettings>
""",
        encoding="utf-8",
    )
    return path


def _traffic_hotspot(
    max_sample: int = 250,
    cell_m: float = 90.0,
) -> Optional[tuple[float, float]]:
    """Return (x, y) of the densest vehicle cluster, or None if no traffic."""
    try:
        import traci
    except Exception:
        return None
    try:
        vehs = list(traci.vehicle.getIDList())
    except Exception:
        return None
    if not vehs:
        return None
    if len(vehs) > max_sample:
        step = max(1, len(vehs) // max_sample)
        vehs = vehs[::step][:max_sample]
    xs: list[float] = []
    ys: list[float] = []
    for vid in vehs:
        try:
            x, y = traci.vehicle.getPosition(vid)
            xs.append(float(x))
            ys.append(float(y))
        except Exception:
            continue
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0], ys[0]

    # Grid density: center on the cell with most vehicles (true hotspot).
    inv = 1.0 / max(10.0, cell_m)
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for x, y in zip(xs, ys):
        key = (int(x * inv), int(y * inv))
        buckets.setdefault(key, []).append((x, y))
    best = max(buckets.values(), key=len)
    cx = sum(p[0] for p in best) / len(best)
    cy = sum(p[1] for p in best) / len(best)
    return cx, cy


def _focus_gui_on_traffic(
    view_id: str = "View #0",
    *,
    mode: str = "wide",
) -> bool:
    """
    Center the GUI on the densest traffic cluster.
    mode='close' → block-scale zoom; mode='wide' → multi-block overview.
    """
    try:
        import traci
    except Exception:
        return False
    hot = _traffic_hotspot()
    if hot is None:
        return False
    cx, cy = hot
    # half extents in meters (view width/height ≈ 2*half)
    if mode == "close":
        half = 220.0
    else:
        half = 560.0
    try:
        traci.gui.setBoundary(
            view_id,
            cx - half,
            cy - half,
            cx + half,
            cy + half,
        )
        return True
    except Exception:
        return False


def _zoom_gui_to_network(cfg_path: Path, view_id: str = "View #0") -> None:
    """Initial mid-scale overview (~40% of network) until traffic appears."""
    try:
        import sumolib
        import traci
    except Exception:
        return
    try:
        tree = ET.parse(cfg_path)
        net_el = tree.find("./input/net-file")
        if net_el is None or not net_el.get("value"):
            return
        net_path = Path(net_el.get("value", ""))
        if not net_path.is_file():
            return
        net = sumolib.net.readNet(str(net_path))
        xmin, ymin, xmax, ymax = net.getBoundary()
        cx = 0.5 * (xmin + xmax)
        cy = 0.5 * (ymin + ymax)
        half_w = max(250.0, (xmax - xmin) * 0.20)
        half_h = max(250.0, (ymax - ymin) * 0.20)
        traci.gui.setBoundary(
            view_id,
            cx - half_w,
            cy - half_h,
            cx + half_w,
            cy + half_h,
        )
    except Exception:
        return


def _minimize_sumo_gui(hwnd: Optional[int]) -> None:
    """Minimize sumo-gui without activating it (background recording)."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes

        # SW_SHOWMINNOACTIVE = 7
        ctypes.windll.user32.ShowWindow(int(hwnd), 7)
    except Exception:
        log.debug("No se pudo minimizar sumo-gui", exc_info=True)


def _traci_screenshot(
    dest: Path,
    *,
    width: int = 1280,
    height: int = 720,
    view_id: str = "View #0",
) -> bool:
    """
    Ask SUMO to write a screenshot on the *next* simulationStep.
    Works without bringing the window to the foreground (unlike OS ImageGrab).
    """
    try:
        import traci
    except Exception:
        return False
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        try:
            dest.unlink()
        except OSError:
            pass
    out = dest if path_is_safe(dest) else to_safe_path(dest)
    if out != dest and out.is_file():
        try:
            out.unlink()
        except OSError:
            pass
    try:
        traci.gui.screenshot(view_id, str(out).replace("\\", "/"), int(width), int(height))
        return True
    except Exception:
        log.debug("traci.gui.screenshot falló", exc_info=True)
        return False


def _wait_screenshot_file(path: Path, timeout_s: float = 2.5) -> bool:
    """Poll until TraCI-written PNG exists and looks non-empty."""
    path = Path(path)
    deadline = time.time() + max(0.2, float(timeout_s))
    while time.time() < deadline:
        try:
            if path.is_file() and path.stat().st_size > 1000:
                return True
        except OSError:
            pass
        time.sleep(0.05)
    try:
        return path.is_file() and path.stat().st_size > 1000
    except OSError:
        return False


def _capture_hwnd_png(hwnd: int, dest: Path) -> bool:
    """Grab what is on-screen in sumo-gui (ImageGrab — fallback if TraCI screenshot fails)."""
    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    left, top, right, bottom = (
        int(rect.left),
        int(rect.top),
        int(rect.right),
        int(rect.bottom),
    )
    width = right - left
    height = bottom - top
    if width < 50 or height < 50:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Raise window occasionally so grabs match the live OpenGL view.
    # Avoid every-frame SetForegroundWindow — steals focus and feels like
    # "moving the mouse closed SUMO" when the user interacts elsewhere.
    if getattr(_capture_hwnd_png, "_raise_i", 0) % 4 == 0:
        try:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.05)
    _capture_hwnd_png._raise_i = getattr(_capture_hwnd_png, "_raise_i", 0) + 1  # type: ignore[attr-defined]

    # Prefer screen grab: PrintWindow often omits / flattens OpenGL vehicles.
    try:
        from PIL import ImageGrab
    except ImportError:
        log.debug("Pillow no disponible: no se puede grabar video de sumo-gui")
        ImageGrab = None  # type: ignore[assignment]

    if ImageGrab is not None:
        try:
            try:
                img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
            except TypeError:
                img = ImageGrab.grab(bbox=(left, top, right, bottom))
            img.save(dest, format="PNG")
            if dest.exists() and dest.stat().st_size > 1000:
                return True
        except Exception:
            log.debug("ImageGrab falló para hwnd %s", hwnd, exc_info=True)

    # Fallback: PrintWindow (may miss OpenGL vehicle layer on some GPUs).
    try:
        hwnd_dc = user32.GetWindowDC(hwnd)
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
        gdi32.SelectObject(mem_dc, bmp)
        ok = bool(user32.PrintWindow(hwnd, mem_dc, 2) or user32.PrintWindow(hwnd, mem_dc, 0))
        if ok:

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD),
                    ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD),
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0
            buf = ctypes.create_string_buffer(width * height * 4)
            bits = gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bmi), 0)
            if bits:
                from PIL import Image

                img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).convert(
                    "RGB"
                )
                img.save(dest, format="PNG")
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem_dc)
                user32.ReleaseDC(hwnd, hwnd_dc)
                return dest.exists() and dest.stat().st_size > 1000
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)
    except Exception:
        log.debug("PrintWindow falló para hwnd %s", hwnd, exc_info=True)
    return False


def run_simulation(
    cfg_path: Path,
    sumo: Optional[SumoEnv] = None,
    step_length: float = 1.0,
    speed_cong_threshold: float = CONGESTION_SPEED_MS,
    edge_levels: Optional[dict[str, float]] = None,
    warmup_s: float = 0.0,
    *,
    record_video: bool = False,
    record_every_s: float = 10.0,
    frames_dir: Optional[Path] = None,
    video_path: Optional[Path] = None,
    video_fps: float = 5.0,
    screenshot_size: tuple[int, int] = (1280, 720),
    progress_cb: Optional[Any] = None,
    progress_file: Optional[Path] = None,
    camera_segment_s: float = DEFAULT_SEGMENT_S,
    video_capture: str = "traci",
) -> SimResult:
    sumo = sumo or detect_sumo()
    if not sumo.ok or not sumo.sumo_bin:
        raise RuntimeError(sumo.message)
    ensure_sumolib_on_path(sumo.home)
    import traci

    label = "optitraffic"
    _close_traci(label)

    cfg_safe = cfg_path if path_is_safe(cfg_path) else to_safe_path(cfg_path)
    worker_pid = os.getpid()

    use_gui = bool(record_video)
    if use_gui:
        if not getattr(sumo, "sumo_gui_bin", None):
            raise RuntimeError(
                "Grabar video requiere sumo-gui. Instale SUMO con interfaz gráfica "
                "y verifique que `sumo-gui` esté en PATH / SUMO_HOME/bin."
            )
        bin_path = sumo.sumo_gui_bin
    else:
        bin_path = sumo.sumo_bin

    # SUMO cannot render frames with headless `sumo` — sumo-gui is required.
    # Default capture uses TraCI screenshots (works minimized / in background).
    # Optional "screen" mode grabs the OS window (needs the GUI visible).
    capture_mode = (video_capture or "traci").strip().lower()
    if capture_mode not in ("traci", "screen"):
        capture_mode = "traci"
    gui_delay_ms = "1" if capture_mode == "traci" else "100"
    cmd = [
        str(bin_path),
        "-c",
        str(cfg_safe),
        "--start",
        "true",
        "--quit-on-end",
        "true",
        "--no-warnings",
        "true",
        "--default.speeddev",
        "0.1",
    ]
    if use_gui:
        settings_dir = Path(frames_dir).parent if frames_dir else (SAFE_RUNS / "current")
        bg_decals = settings_dir / "background" / "viewsettings_decals.xml"
        if not bg_decals.is_file():
            bg_decals = settings_dir / "background" / "viewsettings_bg.xml"
        try:
            from .sumo_background import write_gui_viewsettings

            gui_settings = write_gui_viewsettings(
                settings_dir / "viewsettings_record.xml",
                decals_xml=bg_decals if bg_decals.is_file() else None,
                delay_ms=int(gui_delay_ms),
            )
        except Exception:
            log.warning("No se pudo combinar fondo con viewsettings de grabación", exc_info=True)
            gui_settings = _write_record_gui_settings(settings_dir / "viewsettings_record.xml")
        cmd.extend(
            [
                "--gui-settings-file",
                str(gui_settings),
                "--delay",
                gui_delay_ms,
                "--window-size",
                f"{int(screenshot_size[0])},{int(screenshot_size[1])}",
            ]
        )

    frames_path: Optional[Path] = None
    frame_i = 0
    gui_hwnd: Optional[int] = None
    gui_pid: Optional[int] = None
    capture_failures = 0
    if use_gui:
        frames_path = Path(frames_dir) if frames_dir else (SAFE_RUNS / "current" / "frames")
        if not path_is_safe(frames_path):
            frames_path = to_safe_path(frames_path)
        frames_path.mkdir(parents=True, exist_ok=True)
        for old in frames_path.glob("frame_*.png"):
            try:
                old.unlink()
            except OSError:
                pass

    try:
        traci.start(cmd, label=label)
    except Exception as e1:
        log.warning("traci.start primer intento falló, reintentando: %s", e1)
        _close_traci(label)
        time.sleep(0.5)
        try:
            traci.start(cmd, label=label)
        except Exception as e2:
            detail = _sumo_stderr_probe(bin_path, cfg_safe)
            log.error("traci.start reintento también falló: %s | SUMO: %s", e2, detail)
            raise RuntimeError(
                f"No se pudo iniciar SUMO/TraCI: {e2}. Detalle SUMO: {detail}"
            ) from e2

    traci.switch(label)

    if use_gui:
        gui_pid = _traci_sumo_pid(label)
        time.sleep(0.8)
        gui_hwnd = _find_sumo_gui_hwnd(gui_pid)
        # Fit view to full network tightly (guion toma 1).
        try:
            traci.simulationStep()
        except traci.TraCIException:
            pass
        _zoom_gui_to_network(cfg_safe)
        try:
            from .video_camera import overview_boundary

            nb0 = load_net_bounds_from_cfg(cfg_safe)
            if nb0 is not None:
                xmin, ymin, xmax, ymax = overview_boundary(nb0)
                try:
                    traci.gui.setBoundary("View #0", xmin, ymin, xmax, ymax)
                except Exception:
                    pass
        except Exception:
            log.debug("overview inicial falló", exc_info=True)
        try:
            traci.simulationStep()
        except traci.TraCIException:
            pass
        # Re-resolve HWND after window is fully up.
        gui_hwnd = _find_sumo_gui_hwnd(gui_pid) or gui_hwnd
        # Do NOT minimize: OpenGL + TraCI screenshot on a minimized window
        # often crashes sumo-gui on Windows ("Connection closed by SUMO").
        if capture_mode == "traci":
            log.info(
                "Video: captura TraCI (deje sumo-gui abierto; puede quedar detrás de otras ventanas)"
            )
        else:
            log.info("Video: captura de pantalla OS (deje sumo-gui visible)")

    if edge_levels:
        for eid, level in edge_levels.items():
            try:
                traci.edge.setMaxSpeed(eid, desired_speed_ms(level, CITY_MAX_SPEED_MS))
            except traci.TraCIException:
                continue
    elif not use_gui:
        try:
            for eid in traci.edge.getIDList():
                if eid.startswith(":"):
                    continue
                try:
                    lane0 = f"{eid}_0"
                    try:
                        cur = traci.lane.getMaxSpeed(lane0)
                    except traci.TraCIException:
                        cur = CITY_MAX_SPEED_MS + 1.0
                    if cur > CITY_MAX_SPEED_MS + 1e-6:
                        traci.edge.setMaxSpeed(eid, CITY_MAX_SPEED_MS)
                except traci.TraCIException:
                    continue
        except traci.TraCIException:
            pass

    edge_acc: dict[str, dict[str, float]] = {}
    vehicle_steps = 0
    total_waiting = 0.0
    speed_samples = 0.0
    speed_sum = 0.0
    warmup = max(0.0, float(warmup_s))
    every = max(1.0, float(record_every_s))
    next_shot_at = 0.0
    last_progress_t = -1e9
    camera_segment = max(30.0, float(camera_segment_s))
    net_bounds = load_net_bounds_from_cfg(cfg_safe) if use_gui else None
    last_camera_phase = ""
    active_capture_mode = capture_mode
    connection_lost = False
    t = 0.0

    def _emit_progress(sim_t: float, *, force: bool = False, message: str = "") -> None:
        nonlocal last_progress_t
        if progress_file is None and progress_cb is None:
            return
        if not force and (sim_t - last_progress_t) < 2.0:
            return
        last_progress_t = sim_t
        if progress_file is not None:
            try:
                write_sim_progress(
                    Path(progress_file),
                    status="running",
                    t=float(sim_t),
                    end=float(end),
                    frames=int(frame_i),
                    vehicle_steps=int(vehicle_steps),
                    camera_phase=last_camera_phase or "",
                    capture_mode=active_capture_mode,
                    message=message or "running",
                    pid=int(worker_pid),
                )
            except Exception:
                log.debug("write_sim_progress falló", exc_info=True)
        if progress_cb is not None:
            try:
                progress_cb(sim_t, end, frame_i)
            except Exception:
                pass

    try:
        end = float(traci.simulation.getEndTime())
    except Exception:
        log.warning("traci.simulation.getEndTime() falló, usando 1800s por defecto", exc_info=True)
        end = 1800.0
    if end <= 0 or end > 1e7:
        log.warning("getEndTime() devolvió %s fuera de rango, usando 1800s", end)
        end = 1800.0
    if warmup >= end * 0.85:
        warmup = max(0.0, end * 0.2)

    def _os_capture(sim_t: float) -> None:
        nonlocal frame_i, next_shot_at, gui_hwnd, capture_failures
        nonlocal last_camera_phase, active_capture_mode, connection_lost
        if frames_path is None or sim_t + 1e-9 < next_shot_at:
            return
        try:
            n_veh = len(traci.vehicle.getIDList())
        except Exception as e:
            if "Connection closed" in str(e) or e.__class__.__name__ == "FatalTraCIError":
                connection_lost = True
            n_veh = 0

        hot = _traffic_hotspot() if n_veh > 0 else None
        if net_bounds is None:
            if n_veh <= 0:
                return
            next_shot_at = sim_t + every
            focused = _focus_gui_on_traffic(mode="close" if n_veh > 40 else "wide")
            if not focused:
                capture_failures += 1
                return
        else:
            shot = plan_camera_shot(
                sim_t,
                net_bounds,
                hotspot=hot,
                segment_s=camera_segment,
            )
            if shot.require_vehicles and n_veh <= 0:
                return
            next_shot_at = sim_t + every
            if shot.phase != last_camera_phase:
                last_camera_phase = shot.phase
                log.info(
                    "Video cámara · fase=%s t=%.0fs (segment=%ss)",
                    shot.phase,
                    sim_t,
                    int(camera_segment),
                )
            _emit_progress(sim_t, force=True, message=f"camera:{shot.phase}")
            if not apply_camera_shot(shot):
                capture_failures += 1
                return

        shot_path = frames_path / f"frame_{frame_i:06d}.png"
        out_path = shot_path if path_is_safe(shot_path) else to_safe_path(shot_path)

        if active_capture_mode == "traci":
            queued = _traci_screenshot(
                out_path,
                width=int(screenshot_size[0]),
                height=int(screenshot_size[1]),
            )
            try:
                traci.simulationStep()
            except Exception as e:
                if "Connection closed" in str(e) or e.__class__.__name__ == "FatalTraCIError":
                    connection_lost = True
                    log.error("SUMO cerró la conexión durante captura TraCI: %s", e)
                    return
            if queued and _wait_screenshot_file(out_path, timeout_s=1.2):
                if out_path != shot_path:
                    try:
                        shutil.copy2(out_path, shot_path)
                    except OSError:
                        pass
                frame_i += 1
                _emit_progress(sim_t, force=True, message="frame_ok")
                return
            capture_failures += 1
            if capture_failures >= 3:
                active_capture_mode = "screen"
                log.warning(
                    "TraCI screenshot inestable (%d fallos); pasando a captura de pantalla",
                    capture_failures,
                )

        # Screen / fallback grab
        try:
            traci.simulationStep()
        except Exception as e:
            if "Connection closed" in str(e) or e.__class__.__name__ == "FatalTraCIError":
                connection_lost = True
                log.error("SUMO cerró la conexión durante captura: %s", e)
                return
        time.sleep(0.10)
        if gui_hwnd is None:
            gui_hwnd = _find_sumo_gui_hwnd(gui_pid)
        if not gui_hwnd:
            capture_failures += 1
            return
        title = _hwnd_title(gui_hwnd) if sys.platform == "win32" else ""
        if title and not _is_sumo_gui_window_title(title) and not gui_pid:
            capture_failures += 1
            gui_hwnd = None
            return
        if _capture_hwnd_png(gui_hwnd, shot_path):
            frame_i += 1
            _emit_progress(sim_t, force=True, message="frame_ok_screen")
        else:
            capture_failures += 1
            gui_hwnd = None

    try:
        t = 0.0
        _emit_progress(0.0, force=True, message="sim_loop_start")
        while t < end and not connection_lost:
            try:
                traci.simulationStep()
            except Exception as e:
                if "Connection closed" in str(e) or e.__class__.__name__ == "FatalTraCIError":
                    connection_lost = True
                    log.error(
                        "Connection closed by SUMO en t≈%.0fs (frames=%d). "
                        "Guardando resultados parciales. No cierre sumo-gui mientras graba.",
                        t,
                        frame_i,
                    )
                    break
                raise
            try:
                t = float(traci.simulation.getTime())
            except Exception:
                if connection_lost:
                    break
                raise

            if use_gui:
                _os_capture(t)
                if connection_lost:
                    break
                try:
                    t = float(traci.simulation.getTime())
                except Exception:
                    pass

            _emit_progress(t)

            if t < warmup:
                continue

            try:
                veh_ids = traci.vehicle.getIDList()
            except Exception as e:
                if "Connection closed" in str(e) or e.__class__.__name__ == "FatalTraCIError":
                    connection_lost = True
                    break
                raise
            vehicle_steps += len(veh_ids)
            for vid in veh_ids:
                try:
                    total_waiting += traci.vehicle.getWaitingTime(vid)
                    speed_sum += traci.vehicle.getSpeed(vid)
                    speed_samples += 1
                    eid = traci.vehicle.getRoadID(vid)
                except Exception:
                    continue
                if not eid or eid.startswith(":"):
                    continue
                acc = edge_acc.setdefault(
                    eid, {"speed": 0.0, "occ": 0.0, "wait": 0.0, "n": 0.0, "max_occ": 0.0}
                )
                try:
                    acc["speed"] += traci.vehicle.getSpeed(vid)
                    acc["wait"] += traci.vehicle.getWaitingTime(vid)
                except Exception:
                    pass
                acc["n"] += 1

            if int(t) % 10 == 0:
                for eid in list(edge_acc.keys()):
                    try:
                        occ = traci.edge.getLastStepOccupancy(eid)
                    except Exception:
                        continue
                    edge_acc[eid]["occ"] += occ
                    edge_acc[eid]["max_occ"] = max(edge_acc[eid]["max_occ"], occ)
    finally:
        try:
            traci.close(wait=False)
        except Exception:
            log.debug("traci.close(wait=False) en finally no-op", exc_info=True)
        _close_traci(label)

    edges: dict[str, EdgeKPI] = {}
    congested = 0
    for eid, acc in edge_acc.items():
        n = max(1.0, acc["n"])
        mean_speed = acc["speed"] / n
        mean_occ = acc["occ"] / max(1.0, n / 10.0)
        kpi = EdgeKPI(
            edge_id=eid,
            mean_speed=mean_speed,
            max_occupancy=acc["max_occ"],
            mean_occupancy=mean_occ,
            waiting_time=acc["wait"],
            sample_count=int(n),
            congested=mean_speed < speed_cong_threshold and n > 5,
        )
        if kpi.congested:
            congested += 1
        edges[eid] = kpi

    corr = None
    corr_detail = "sin_tomtom"
    if edge_levels:
        xs, ys = [], []
        for eid, level in edge_levels.items():
            if eid in edges:
                xs.append(1.0 - float(level))
                ys.append(max(0.0, 1.0 - edges[eid].mean_speed / CITY_MAX_SPEED_MS))
        if len(xs) < 3:
            corr_detail = f"pocos_edges_comunes ({len(xs)}; se necesitan ≥3)"
        else:
            corr = _pearson(xs, ys)
            if corr is None:
                corr_detail = "sin_varianza (TomTom o simulación casi constantes)"
            else:
                corr_detail = f"ok n={len(xs)}"
    else:
        corr_detail = "sin_tomtom (paso 4 no calibró edges)"

    mean_speed = (speed_sum / speed_samples) if speed_samples else 0.0
    pct = (100.0 * congested / len(edges)) if edges else 0.0

    out_video: Optional[str] = None
    frames_count = frame_i
    frames_dir_str = str(frames_path) if frames_path else None
    video_note = ""
    if use_gui and frames_path and frame_i > 0:
        dest_video = Path(video_path) if video_path else (frames_path.parent / "simulation.mp4")
        if not path_is_safe(dest_video):
            dest_video = to_safe_path(dest_video)
        try:
            encode_frames_to_mp4(frames_path, dest_video, fps=float(video_fps))
            out_video = str(dest_video)
        except Exception as e:
            log.warning("encode_frames_to_mp4 falló (%d frames): %s", frame_i, e)
            video_note = f"; video: {e}"
    elif use_gui and frame_i == 0:
        video_note = (
            f"; video: sin frames (ventana sumo-gui no capturable; fallos={capture_failures}). "
            "Deje la ventana abierta (no la cierre ni la minimice si usa captura TraCI)."
        )
    if connection_lost:
        video_note += (
            f"; SUMO cerró la conexión ~t={t:.0f}s "
            f"(resultados parciales, frames={frame_i})"
        )

    result = SimResult(
        duration_s=int(t) if connection_lost and t > 0 else int(end),
        vehicle_steps=vehicle_steps,
        total_waiting=total_waiting,
        mean_speed=mean_speed,
        pct_edges_congested=pct,
        edges=edges,
        tomtom_correlation=corr,
        video_path=out_video,
        frames_dir=frames_dir_str,
        frames_count=frames_count,
    )
    result._corr_detail = corr_detail + video_note  # type: ignore[attr-defined]
    result._warmup_s = warmup  # type: ignore[attr-defined]
    result._connection_lost = connection_lost  # type: ignore[attr-defined]
    return result


def export_edge_csv(result: SimResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "edge_id",
                "mean_speed",
                "max_occupancy",
                "mean_occupancy",
                "waiting_time",
                "sample_count",
                "congested",
            ]
        )
        for e in result.edges.values():
            w.writerow(
                [
                    e.edge_id,
                    f"{e.mean_speed:.3f}",
                    f"{e.max_occupancy:.3f}",
                    f"{e.mean_occupancy:.3f}",
                    f"{e.waiting_time:.1f}",
                    e.sample_count,
                    int(e.congested),
                ]
            )
    return path


def export_edge_geojson(edges_gj: dict[str, Any], result: SimResult, path: Path) -> Path:
    feats = []
    for feat in edges_gj.get("features", []):
        eid = feat["properties"]["id"]
        props = dict(feat["properties"])
        kpi = result.edges.get(eid)
        if kpi:
            props.update(
                {
                    "mean_speed": kpi.mean_speed,
                    "congested": kpi.congested,
                    "max_occupancy": kpi.max_occupancy,
                    "waiting_time": kpi.waiting_time,
                }
            )
        feats.append({"type": "Feature", "properties": props, "geometry": feat["geometry"]})
    out = {"type": "FeatureCollection", "features": feats}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    return path
