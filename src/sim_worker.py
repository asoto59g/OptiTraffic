"""
Background TraCI worker: runs SUMO outside the Streamlit script cycle.

Long simulations (e.g. 7200 s) used to die when the browser/Streamlit reran
(click, widget, or even focus fights). This process owns TraCI until the end.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("Usage: python -m src.sim_worker <job.json>", file=sys.stderr)
        return 2
    job_path = Path(argv[0])
    job = json.loads(job_path.read_text(encoding="utf-8"))
    run_dir = Path(job["run_dir"])
    progress_path = Path(job.get("progress_file") or (run_dir / "sim_progress.json"))
    result_path = Path(job.get("result_file") or (run_dir / "sim_result.json"))

    # Ensure project root on path when launched as __main__
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from src.logging_config import get_logger
    from src.simulate import run_simulation, write_sim_progress
    from src.sumo_env import detect_sumo

    log = get_logger("sim_worker")
    wanted_end = float(job.get("end") or job.get("duration") or 0)
    want_video = bool(job.get("record_video"))

    write_sim_progress(
        progress_path,
        status="running",
        t=0.0,
        end=wanted_end,
        frames=0,
        pid=os.getpid(),
        message="worker_started",
    )
    try:
        # Persist worker PID into job.json (launcher may have written Popen PID).
        try:
            job["pid"] = os.getpid()
            job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

        sumo = detect_sumo()
        common = dict(
            sumo=sumo,
            edge_levels=job.get("edge_levels") or None,
            warmup_s=float(job.get("warmup_s") or 0),
            record_every_s=float(job.get("record_every_s") or 30),
            frames_dir=Path(job["frames_dir"]) if job.get("frames_dir") else None,
            video_path=Path(job["video_path"]) if job.get("video_path") else None,
            video_fps=float(job.get("video_fps") or 5),
            camera_segment_s=float(job.get("camera_segment_s") or 200),
            video_capture=str(job.get("video_capture") or "screen"),
            progress_file=progress_path,
        )
        result = run_simulation(
            Path(job["cfg"]),
            record_video=want_video,
            **common,
        )

        # GUI + video is fragile on Windows; if SUMO closed early, finish KPIs headless.
        lost = bool(getattr(result, "_connection_lost", False))
        partial_t = float(result.duration_s or 0)
        if (
            want_video
            and lost
            and wanted_end > 0
            and partial_t < wanted_end * 0.9
        ):
            log.warning(
                "GUI/video cortó en t≈%.0fs/%s. Reintento headless (sin video) para KPIs.",
                partial_t,
                int(wanted_end),
            )
            write_sim_progress(
                progress_path,
                status="running",
                t=partial_t,
                end=wanted_end,
                frames=int(result.frames_count),
                pid=os.getpid(),
                message="retry_headless_after_gui_drop",
                camera_phase="",
                capture_mode="none",
            )
            partial_video = result.video_path
            partial_frames_dir = result.frames_dir
            partial_frames = int(result.frames_count)
            partial_note = getattr(result, "_corr_detail", "") or ""

            result = run_simulation(
                Path(job["cfg"]),
                record_video=False,
                **common,
            )
            # Keep any video/frames produced before the GUI died.
            if partial_frames > 0:
                result.frames_count = partial_frames
                result.frames_dir = partial_frames_dir
                result.video_path = partial_video
            note_extra = (
                f"; video parcial t≈{partial_t:.0f}s (frames={partial_frames}); "
                "KPIs de reintento headless"
            )
            prev = getattr(result, "_corr_detail", "") or ""
            result._corr_detail = (partial_note + " | " + prev + note_extra).strip(" |")  # type: ignore[attr-defined]

        result_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False),
            encoding="utf-8",
        )
        note = getattr(result, "_corr_detail", "") or ""
        write_sim_progress(
            progress_path,
            status="done",
            t=float(result.duration_s),
            end=wanted_end or float(result.duration_s),
            frames=int(result.frames_count),
            vehicle_steps=int(result.vehicle_steps),
            pid=os.getpid(),
            message="ok_partial" if ("parcial" in note.lower() or lost) else "ok",
            detail=note[-500:] if note else "",
        )
        return 0
    except Exception as e:
        write_sim_progress(
            progress_path,
            status="error",
            error=str(e),
            traceback=traceback.format_exc()[-4000:],
            pid=os.getpid(),
            message="failed",
        )
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
