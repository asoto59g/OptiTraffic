"""Background simulation job cleanup tests."""

from ui.step_sim import _clear_previous_run_outputs, _consume_sim_job


def test_consume_sim_job_replaces_existing_consumed_file(tmp_path) -> None:
    job_path = tmp_path / "sim_job.json"
    consumed_path = tmp_path / "sim_job_consumed.json"
    job_path.write_text('{"job": "new"}', encoding="utf-8")
    consumed_path.write_text('{"job": "old"}', encoding="utf-8")

    _consume_sim_job(tmp_path)

    assert not job_path.exists()
    assert consumed_path.read_text(encoding="utf-8") == '{"job": "new"}'


def test_clear_previous_run_outputs_removes_stale_reused_files(tmp_path) -> None:
    for name in ("simulation.mp4", "optitraffic.sumocfg", "sim_result.json"):
        (tmp_path / name).write_bytes(b"stale")
    (tmp_path / "sim_job_consumed_123.json").write_bytes(b"stale")
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame_000001.png").write_bytes(b"stale")
    background = tmp_path / "background"
    background.mkdir()
    (background / "tile2151_3850.png").write_bytes(b"stale")

    _clear_previous_run_outputs(tmp_path)

    assert not (tmp_path / "simulation.mp4").exists()
    assert not (tmp_path / "optitraffic.sumocfg").exists()
    assert not (tmp_path / "sim_result.json").exists()
    assert not (tmp_path / "sim_job_consumed_123.json").exists()
    assert not frames.exists()
    assert not background.exists()
