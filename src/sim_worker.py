"""
Background TraCI worker: runs SUMO outside the Streamlit script cycle.

Long simulations (e.g. 7200 s) used to die when the browser/Streamlit reran
(click, widget, or even focus fights). This process owns TraCI until the end.
"""

from __future__ import annotations

import json
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

    from src.simulate import run_simulation, write_sim_progress
    from src.sumo_env import detect_sumo

    write_sim_progress(
        progress_path,
        status="running",
        t=0.0,
        end=float(job.get("end") or 0),
        frames=0,
        message="worker_started",
    )
    try:
        sumo = detect_sumo()
        result = run_simulation(
            Path(job["cfg"]),
            sumo=sumo,
            edge_levels=job.get("edge_levels") or None,
            warmup_s=float(job.get("warmup_s") or 0),
            record_video=bool(job.get("record_video")),
            record_every_s=float(job.get("record_every_s") or 30),
            frames_dir=Path(job["frames_dir"]) if job.get("frames_dir") else None,
            video_path=Path(job["video_path"]) if job.get("video_path") else None,
            video_fps=float(job.get("video_fps") or 5),
            progress_file=progress_path,
        )
        result_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False),
            encoding="utf-8",
        )
        write_sim_progress(
            progress_path,
            status="done",
            t=float(result.duration_s),
            end=float(result.duration_s),
            frames=int(result.frames_count),
            vehicle_steps=int(result.vehicle_steps),
            message="ok",
        )
        return 0
    except Exception as e:
        write_sim_progress(
            progress_path,
            status="error",
            error=str(e),
            traceback=traceback.format_exc()[-4000:],
            message="failed",
        )
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
