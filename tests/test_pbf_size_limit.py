"""PBF download size guard unit tests."""

import pytest

from src.osm_fetch import MAX_GEOFABRIK_PBF_BYTES, download_extract


def test_max_pbf_constant_positive() -> None:
    assert MAX_GEOFABRIK_PBF_BYTES >= 50 * 1024 * 1024


def test_download_aborts_on_content_length(monkeypatch, tmp_path) -> None:
    class FakeResp:
        status_code = 200
        url = "https://download.geofabrik.de/central-america/costa-rica-latest.osm.pbf"
        headers = {"Content-Type": "application/octet-stream", "Content-Length": str(900_000_000)}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size=0):
            yield b"x" * 10

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("src.osm_fetch.resolve_geofabrik_path", lambda *a, **k: "central-america/costa-rica")
    monkeypatch.setattr("src.osm_fetch.geofabrik_url", lambda *a, **k: FakeResp.url)
    monkeypatch.setattr("src.osm_fetch.SAFE_OSM", tmp_path)
    monkeypatch.setattr("src.osm_fetch.OSM_DIR", tmp_path)
    monkeypatch.setattr("src.osm_fetch.CACHE_DIR", tmp_path)
    monkeypatch.setattr("src.osm_fetch.ensure_dirs", lambda: None)
    monkeypatch.setattr("src.osm_fetch.requests.get", lambda *a, **k: FakeResp())

    with pytest.raises(RuntimeError, match="demasiado grande"):
        download_extract("Costa Rica", max_bytes=500 * 1024 * 1024)
