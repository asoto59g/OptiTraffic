"""
Pilot checklist for San José (Costa Rica) — run after SUMO + TomTom key are ready.

Usage:
  python scripts/pilot_sanjose.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.area import build_study_area, rectangle_around  # noqa: E402
from src.editors import NetworkEdits, TlsTiming  # noqa: E402
from src.scenarios import save_scenario  # noqa: E402
from src.simulate import EdgeKPI, SimResult, export_edge_csv, export_edge_geojson  # noqa: E402
from src.sumo_env import detect_sumo  # noqa: E402
from src.tomtom import load_api_key  # noqa: E402


def main() -> None:
    print("=== OptiTraffic pilot: San José ===")
    sumo = detect_sumo()
    print("SUMO:", sumo.message)
    print("TomTom key:", "yes" if load_api_key() else "NO (.env)")

    # ~0.8 km half-box around downtown SJ (~2.5 km²)
    poly = rectangle_around(9.932, -84.080, half_km=0.8)
    area = build_study_area("San José", "Costa Rica", poly)
    print(f"Area: {area.area_km2:.2f} km²  bbox={area.bbox}")

    edits = NetworkEdits(tls_default=TlsTiming(45, 5, 45))

    # Synthetic KPIs export path (works without SUMO) to validate export pipeline
    demo_edges = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "demo_e1", "name": "Av Central", "lanes": 1},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-84.081, 9.932], [-84.079, 9.932]],
                },
            }
        ],
    }
    result = SimResult(
        duration_s=600,
        vehicle_steps=100,
        total_waiting=50.0,
        mean_speed=8.5,
        pct_edges_congested=0.0,
        edges={
            "demo_e1": EdgeKPI(
                edge_id="demo_e1", mean_speed=8.5, sample_count=10, congested=False
            )
        },
    )
    out = ROOT / "data" / "runs" / "piloto_export"
    csv_p = export_edge_csv(result, out / "edges.csv")
    gj_p = export_edge_geojson(demo_edges, result, out / "edges_result.geojson")
    folder = save_scenario(
        "piloto_san_jose",
        area,
        edits,
        edge_levels={},
        result_summary={"note": "pre-sumo scaffold", "mean_speed": result.mean_speed},
        extra_files=[csv_p, gj_p],
    )
    print("Exports:", csv_p, gj_p)
    print("Scenario:", folder)
    print(
        json.dumps(
            {
                "next": [
                    "Install SUMO and set SUMO_HOME",
                    "Set TOMTOM_API_KEY in .env",
                    "streamlit run app.py",
                    "Step 1: San José / Costa Rica + confirm rectangle",
                    "Step 2: build network from Geofabrik",
                    "Steps 3-6: configure, TomTom, simulate, export",
                ]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
