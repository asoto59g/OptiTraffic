"""Run SUMO via TraCI and collect edge congestion KPIs."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .traffic_params import CITY_MAX_SPEED_MS, CONGESTION_SPEED_MS, desired_speed_ms
from .osm_fetch import SAFE_ROOT, path_is_safe, to_safe_path
from .sumo_env import SumoEnv, detect_sumo, ensure_sumolib_on_path

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


def write_sumocfg(
    cfg_path: Path,
    net_path: Path,
    routes_path: Path,
    additional_files: Optional[list[Path]] = None,
    begin: int = 0,
    end: int = 1800,
) -> Path:
    # Native SUMO on Windows fails with non-ASCII paths (OneDrive "Geomática")
    net_safe = net_path if path_is_safe(net_path) else to_safe_path(net_path)
    routes_safe = routes_path if path_is_safe(routes_path) else to_safe_path(routes_path)
    add_safe: list[Path] = []
    if additional_files:
        for p in additional_files:
            add_safe.append(p if path_is_safe(p) else to_safe_path(p))

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
            pass
    try:
        traci.close(wait=False)
    except Exception:
        pass
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
    pattern = str(frames_dir / "frame_%06d.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ffmpeg),
        "-y",
        "-framerate",
        str(fps),
        "-i",
        pattern,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            "ffmpeg falló al crear el video:\n" + (r.stderr or r.stdout or "sin detalle")[:800]
        )
    return out_path


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
) -> SimResult:
    sumo = sumo or detect_sumo()
    if not sumo.ok or not sumo.sumo_bin:
        raise RuntimeError(sumo.message)
    ensure_sumolib_on_path(sumo.home)
    import traci

    label = "optitraffic"
    _close_traci(label)

    cfg_safe = cfg_path if path_is_safe(cfg_path) else to_safe_path(cfg_path)

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

    cmd = [
        str(bin_path),
        "-c",
        str(cfg_safe),
        "--start",
        "--quit-on-end",
        "true",
        "--no-warnings",
        "true",
        "--default.speeddev",
        "0.1",
    ]
    if use_gui:
        cmd.extend(["--window-size", f"{screenshot_size[0]},{screenshot_size[1]}"])

    frames_path: Optional[Path] = None
    frame_i = 0
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
    except Exception:
        _close_traci(label)
        time.sleep(0.5)
        try:
            traci.start(cmd, label=label)
        except Exception as e2:
            detail = _sumo_stderr_probe(bin_path, cfg_safe)
            raise RuntimeError(
                f"No se pudo iniciar SUMO/TraCI: {e2}. Detalle SUMO: {detail}"
            ) from e2

    traci.switch(label)

    if edge_levels:
        for eid, level in edge_levels.items():
            try:
                traci.edge.setMaxSpeed(eid, desired_speed_ms(level, CITY_MAX_SPEED_MS))
            except traci.TraCIException:
                continue
    else:
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

    if use_gui:
        try:
            traci.gui.setSchema("View #0", "real world")
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
    tracked = False

    try:
        end = float(traci.simulation.getEndTime())
    except Exception:
        end = 1800.0
    if end <= 0 or end > 1e7:
        end = 1800.0
    if warmup >= end * 0.85:
        warmup = max(0.0, end * 0.2)

    try:
        t = 0.0
        while t < end:
            if use_gui and frames_path is not None and t + 1e-9 >= next_shot_at:
                shot = frames_path / f"frame_{frame_i:06d}.png"
                try:
                    traci.gui.screenshot(
                        "View #0",
                        str(shot).replace("\\", "/"),
                        int(screenshot_size[0]),
                        int(screenshot_size[1]),
                    )
                    frame_i += 1
                    next_shot_at = t + every
                except traci.TraCIException:
                    next_shot_at = t + every

            traci.simulationStep()
            t = float(traci.simulation.getTime())

            if use_gui and not tracked:
                try:
                    vehs = traci.vehicle.getIDList()
                    if vehs:
                        traci.gui.trackVehicle("View #0", vehs[0])
                        tracked = True
                except traci.TraCIException:
                    pass

            if t < warmup:
                continue

            veh_ids = traci.vehicle.getIDList()
            vehicle_steps += len(veh_ids)
            for vid in veh_ids:
                try:
                    total_waiting += traci.vehicle.getWaitingTime(vid)
                    speed_sum += traci.vehicle.getSpeed(vid)
                    speed_samples += 1
                    eid = traci.vehicle.getRoadID(vid)
                except traci.TraCIException:
                    continue
                if not eid or eid.startswith(":"):
                    continue
                acc = edge_acc.setdefault(
                    eid, {"speed": 0.0, "occ": 0.0, "wait": 0.0, "n": 0.0, "max_occ": 0.0}
                )
                try:
                    acc["speed"] += traci.vehicle.getSpeed(vid)
                    acc["wait"] += traci.vehicle.getWaitingTime(vid)
                except traci.TraCIException:
                    pass
                acc["n"] += 1

            if int(t) % 10 == 0:
                for eid in list(edge_acc.keys()):
                    try:
                        occ = traci.edge.getLastStepOccupancy(eid)
                    except traci.TraCIException:
                        continue
                    edge_acc[eid]["occ"] += occ
                    edge_acc[eid]["max_occ"] = max(edge_acc[eid]["max_occ"], occ)

        if use_gui and frame_i > 0:
            try:
                traci.simulationStep()
            except traci.TraCIException:
                pass
    finally:
        try:
            traci.close(wait=False)
        except Exception:
            pass
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
            video_note = f"; video: {e}"

    result = SimResult(
        duration_s=int(end),
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
