"""Download-link helpers for result artifacts."""

from types import SimpleNamespace

from ui import step_results
from ui.step_results import _download_href, _download_link


def test_download_href_encodes_binary_payload() -> None:
    assert _download_href(b"abc", "text/plain") == "data:text/plain;base64,YWJj"


def test_download_link_escapes_label_and_filename() -> None:
    link = _download_link("<CSV>", b"ok", 'edges"bad.csv', "text/csv")

    assert "data:text/csv;base64,b2s=" in link
    assert "&lt;CSV&gt;" in link
    assert 'download="edges&quot;bad.csv"' in link
    assert "<CSV>" not in link


def _write_run_dir(run_dir, *, with_net: bool) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "optitraffic.sumocfg").write_text("<configuration />", encoding="utf-8")
    (run_dir / "routes.rou.xml").write_text("<routes />", encoding="utf-8")
    if with_net:
        (run_dir / "sim.net.xml").write_text("<net />", encoding="utf-8")


def test_resolve_results_run_dir_skips_legacy_run_without_local_net(monkeypatch, tmp_path) -> None:
    legacy_root = tmp_path / "repo"
    safe_runs = tmp_path / "safe" / "runs"
    legacy_run = legacy_root / "data" / "runs" / "current"
    safe_run = safe_runs / "current"
    _write_run_dir(legacy_run, with_net=False)
    _write_run_dir(safe_run, with_net=True)

    monkeypatch.setattr(step_results, "ROOT", legacy_root)
    monkeypatch.setattr(step_results, "SAFE_RUNS", safe_runs)

    got = step_results._resolve_results_run_dir(
        SimpleNamespace(),
        session_run_dir=legacy_run,
    )

    assert got == safe_run
