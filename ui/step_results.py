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
from src.simulate import SAFE_RUNS  # noqa: E402
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

    video_path = getattr(result, "video_path", None)
    frames_dir = getattr(result, "frames_dir", None) or str(Path(run_dir) / "frames")
    frames_count = int(getattr(result, "frames_count", 0) or 0)
    disk_mp4 = Path(run_dir) / "simulation.mp4"
    if (not video_path or not Path(str(video_path)).exists() or Path(str(video_path)).stat().st_size < 1000) and disk_mp4.exists() and disk_mp4.stat().st_size > 1000:
        video_path = str(disk_mp4)
    if frames_count <= 0:
        frames_count = len(list(Path(frames_dir).glob("frame_*.png"))) if Path(frames_dir).exists() else 0

    st.markdown("**Video / capturas**")
    mp4_ok = bool(video_path and Path(video_path).exists() and Path(video_path).stat().st_size > 1000)
    frames_path_obj = Path(frames_dir) if frames_dir else Path(run_dir) / "frames"

    if not mp4_ok and frames_count > 0 and frames_path_obj.is_dir():
        if st.button("Generar MP4 desde frames (ffmpeg)", key="reencode_mp4"):
            try:
                from src.simulate import encode_frames_to_mp4

                out = encode_frames_to_mp4(frames_path_obj, Path(run_dir) / "simulation.mp4", fps=5.0)
                st.session_state.sim_result.video_path = str(out)  # type: ignore[union-attr]
                st.success(f"MP4 listo ({out.stat().st_size // 1024} KB)")
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo ensamblar el MP4: {e}")

    if mp4_ok:
        st.download_button(
            "Descargar video (MP4)",
            Path(video_path).read_bytes(),
            file_name="simulation.mp4",
            mime="video/mp4",
            key="dl_sim_mp4",
        )
        st.caption(f"`{video_path}` · {frames_count} frames · {Path(video_path).stat().st_size // 1024} KB")
    elif frames_count > 0:
        st.info(
            f"Hay **{frames_count}** PNG en `{frames_dir}` "
            "(el MP4 falló antes: suele ser resolución impar; use el botón de arriba)."
        )
        st.caption(f"Carpeta: `{frames_dir}`")
    else:
        st.warning(
            "No se generó video ni frames. La carpeta esperada es "
            f"`{Path(run_dir) / 'frames'}` (suele estar en "
            "`%LOCALAPPDATA%\\OptiTraffic\\runs\\current\\`). "
            "Vuelva a simular con **Grabar video** y deje sumo-gui visible."
        )
        detail = getattr(result, "_corr_detail", None)
        if detail and "video:" in str(detail):
            st.caption(str(detail))

    name = st.text_input("Nombre del escenario", value=f"{st.session_state.city}_mvp")
    overwrite = st.checkbox("Sobrescribir si existe el mismo nombre", value=False, key="res_overwrite")
    st.caption(
        "Al guardar se incluye el **proyecto SUMO completo** (`sumo/optitraffic.sumocfg` + red, "
        "rutas y additionals) listo para abrir en sumo-gui."
    )
    if st.button("Guardar escenario (zona + config + resultados + SUMO)", type="primary"):
        extras = [p for p in (csv_path, gj_path) if p.exists()]
        if video_path and Path(video_path).exists():
            extras.append(Path(video_path))
        sim_run = Path(run_dir) if run_dir else None
        if sim_run is None or not (sim_run / "optitraffic.sumocfg").exists():
            # Prefer the ASCII-safe run used by TraCI/SUMO
            candidate = SAFE_RUNS / "current"
            if (candidate / "optitraffic.sumocfg").exists():
                sim_run = candidate
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
                "video_path": video_path,
                "frames_count": frames_count,
            },
            extra_files=extras or None,
            overwrite=overwrite,
            run_dir=sim_run,
        )
        st.session_state.scenario_folder = str(folder)
        sumo_cfg = folder / "sumo" / "optitraffic.sumocfg"
        if sumo_cfg.is_file():
            st.success(
                f"Guardado en `{folder}` · Abra sumo-gui con `{sumo_cfg}`"
            )
        else:
            st.success(f"Guardado en `{folder}`")
            st.warning(
                "No se encontró una corrida SUMO para empaquetar. "
                "Ejecute de nuevo el paso 5 y vuelva a guardar."
            )

