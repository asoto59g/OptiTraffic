"""Geofabrik index resolution unit tests (no network)."""

from src.osm_fetch import COUNTRY_EXTRACTS, resolve_geofabrik_path, resolve_geofabrik_path_from_iso


def test_fallback_costa_rica() -> None:
    assert resolve_geofabrik_path("Costa Rica") == COUNTRY_EXTRACTS["costa rica"]


def test_iso_resolver_uses_fixture(monkeypatch) -> None:
    index = {
        "features": [
            {
                "properties": {
                    "name": "Costa Rica",
                    "iso3166-1:alpha2": ["CR"],
                    "area": 50_000,
                    "urls": {
                        "pbf": "https://download.geofabrik.de/central-america/costa-rica-latest.osm.pbf"
                    },
                }
            },
            {
                "properties": {
                    "name": "Central America",
                    "iso3166-1:alpha2": ["CR", "PA"],
                    "area": 500_000,
                    "urls": {
                        "pbf": "https://download.geofabrik.de/central-america-latest.osm.pbf"
                    },
                }
            },
        ]
    }

    monkeypatch.setattr("src.osm_fetch._load_geofabrik_index", lambda force=False: index)
    assert resolve_geofabrik_path_from_iso("cr") == "central-america/costa-rica"
    assert resolve_geofabrik_path("X", country_code="cr") == "central-america/costa-rica"
