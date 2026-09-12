"""Nominatim cache helpers (no network)."""

from src.area import _cache_path, _read_cache, _write_cache


def test_nominatim_cache_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("src.area._CACHE_DIR", tmp_path)
    path = _cache_path("fwd", "san jose, costa rica")
    _write_cache(path, {"lat": 9.93, "lon": -84.08, "display": "SJ"})
    data = _read_cache(path)
    assert data is not None
    assert data["lat"] == 9.93
