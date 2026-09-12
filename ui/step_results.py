"""OptiTraffic wizard step module."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.scenarios import (  # noqa: E402
    save_scenario,
)
from ui.common import (  # noqa: E402
    render_study_map,
)


def step_results() -> None:
    st.header("6. Resultados")
    result = st.session_state.sim_result
    if result is None:
        st.warning("Aún no hay resultados de simulación.")
        if st.session_state.edges_gj:
            render_study_map(
                title="Mapa de red",
                mode="tomtom" if st.session_state.edge_levels else "plain",
                value_by_id=st.session_state.edge_levels or None,
                key_prefix="res_empty",
            )
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Velocidad media (m/s)", f"{result.mean_speed:.2f}")
    k2.metric("% edges congestión", f"{result.pct_edges_congested:.1f}%")
    k3.metric("Espera acumulada (s)", f"{result.total_waiting:.0f}")
    corr = result.tomtom_correlation
    k4.metric("Corr. vs TomTom", f"{corr:.2f}" if corr is not None else "n/d")
    warmup_used = getattr(result, "_warmup_s", None)
    n_gates = len(st.session_state.get("flow_gates") or [])
    if warmup_used or n_gates:
        bits = []
        if n_gates:
            bits.append(f"{n_gates} puertas entrada/salida")
        if warmup_used:
            bits.append(f"warmup {int(warmup_used)}s")
        bits.append(f"duración {result.duration_s}s")
        st.caption(" · ".join(bits))
    detail = getattr(result, "_corr_detail", None)
    n_tt = len(st.session_state.edge_levels or {})
    if corr is None:
        if n_tt == 0:
            st.caption(
                "Corr. n/d: no hay calibración TomTom. En el paso 4 pulse "
                "Obtener flujo TomTom y calibrar edges y luego simule de nuevo."
            )
        elif detail:
            st.caption(f"Corr. n/d: {detail}. Hacen falta >=3 edges con TomTom y trafico simulado.")
        else:
            st.caption("Corr. n/d: no se pudo calcular la correlacion con TomTom.")
    elif detail:
        st.caption(f"Correlacion Pearson congestion TomTom vs simulacion ({detail}).")

    speeds = {eid: kpi.mean_speed for eid, kpi in result.edges.items()}
    render_study_map(
        title="Mapa de congestión simulada",
        mode="sim",
        value_by_id=speeds,
        show_tls=True,
        height=500,
        key_prefix="res",
    )

    run_dir = st.session_state.run_dir or (ROOT / "data" / "runs" / "current")
    c1, c2 = st.columns(2)
    csv_path = Path(run_dir) / "edges.csv"
    gj_path = Path(run_dir) / "edges_result.geojson"
    if csv_path.exists():
        c1.download_button("Descargar CSV edges", csv_path.read_bytes(), file_name="edges.csv")
    if gj_path.exists():
        c2.download_button(
            "Descargar GeoJSON resultado",
            gj_path.read_bytes(),
            file_name="edges_result.geojson",
        )

    name = st.text_input("Nombre del escenario", value=f"{st.session_state.city}_mvp")
    overwrite = st.checkbox("Sobrescribir si existe el mismo nombre", value=False, key="res_overwrite")
    if st.button("Guardar escenario (zona + config + resultados)", type="primary"):
        folder = save_scenario(
            name,
            st.session_state.area,
            st.session_state.edits,
            net_path=st.session_state.net_path,
            edge_levels=st.session_state.edge_levels,
            edges_gj=st.session_state.edges_gj,
            tls_list=st.session_state.tls_list or [],
            flow_gates=st.session_state.get("flow_gates") or [],
            result_summary={
                "mean_speed": result.mean_speed,
                "pct_edges_congested": result.pct_edges_congested,
                "tomtom_correlation": result.tomtom_correlation,
            },
            extra_files=[csv_path, gj_path] if csv_path.exists() else None,
            overwrite=overwrite,
        )
        st.session_state.scenario_folder = str(folder)
        st.success(f"Guardado en {folder}")


