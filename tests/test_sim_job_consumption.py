"""Background simulation job cleanup tests."""

from ui.step_sim import _consume_sim_job


def test_consume_sim_job_replaces_existing_consumed_file(tmp_path) -> None:
    job_path = tmp_path / "sim_job.json"
    consumed_path = tmp_path / "sim_job_consumed.json"
    job_path.write_text('{"job": "new"}', encoding="utf-8")
    consumed_path.write_text('{"job": "old"}', encoding="utf-8")

    _consume_sim_job(tmp_path)

    assert not job_path.exists()
    assert consumed_path.read_text(encoding="utf-8") == '{"job": "new"}'
