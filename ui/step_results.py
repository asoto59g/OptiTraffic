"""OptiTraffic wizard step module."""
from __future__ import annotations

import base64
import html
import sys
from pathlib import Path
from typing import Optional

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


def _download_href(data: bytes, mime: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _download_link(label: str, data: bytes, file_name: str, mime: str = "application/octet-stream") -> str:
    href = _download_href(data, mime)
    return (
        '<a class="opt-download-link" '
        f'href="{href}" download="{html.escape(file_name, quote=True)}">'
        f"{html.escape(label)}</a>"
    )


def _render_download_styles() -> None:
    st.markdown(
        """
        <style>
        .opt-download-link {
            align-items: center;
            background: #ffffff;
            border: 1px solid rgba(49, 51, 63, 0.2);
            border-radius: 0.5rem;
            color: rgb(49, 51, 63);
            display: inline-flex;
            font-weight: 400;
            justify-content: center;
            line-height: 1.4;
            min-height: 2.5rem;
            padding: 0.375rem 0.75rem;
            text-decoration: none;
            width: 100%;
        }
        .opt-download-link:hover {
            border-color: rgb(255, 75, 75);
            color: rgb(255, 75, 75);
            text-decoration: none;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_download_link(
    container,
    label: str,
    data: bytes,
    file_name: str,
    mime: str = "application/octet-stream",
) -> None:
    container.markdown(
        _download_link(label, data, file_name, mime=mime),
        unsafe_allow_html=True,
    )


def _path_or_none(value: object) -> Optional[Path]:
    if not value:
        return None
    try:
        return Path(str(value))
    except (TypeError, ValueError):
        return None


def _run_dir_has_sumo_project(run_dir: Path) -> bool:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return False
    if not (run_dir / "optitraffic.sumocfg").is_file():
        return False
    if not ((run_dir / "routes.rou.xml").is_file() or (run_dir / "trips.xml").is_file()):
        return False
    return (run_dir / "sim.net.xml").is_file() or any(run_dir.glob("*.net.xml"))


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path.absolute())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _resolve_results_run_dir(result: object, session_run_dir: object = None) -> Path:
    candidates: list[Path] = []
    session_path = _path_or_none(session_run_dir)
    if session_path is not None:
        candidates.append(session_path)

    frames_dir = _path_or_none(getattr(result, "frames_dir", None))
    if frames_dir is not None:
        candidates.append(frames_dir.parent)
    video_path = _path_or_none(getattr(result, "video_path", None))
    if video_path is not None:
        candidates.append(video_path.parent)

    candidates.extend([SAFE_RUNS / "current", ROOT / "data" / "runs" / "current"])
    candidates = _dedupe_paths(candidates)

    for candidate in candidates:
        if _run_dir_has_sumo_project(candidate):
            return candidate
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return SAFE_RUNS / "current"


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

    run_dir = _resolve_results_run_dir(result, st.session_state.get("run_dir"))
    _render_download_styles()
    c1, c2 = st.columns(2)
    csv_path = Path(run_dir) / "edges.csv"
    gj_path = Path(run_dir) / "edges_result.geojson"
    if csv_path.exists():
        _render_download_link(c1, "Descargar CSV edges", csv_path.read_bytes(), "edges.csv", "text/csv")
    if gj_path.exists():
        _render_download_link(
            c2,
            "Descargar GeoJSON resultado",
            gj_path.read_bytes(),
            "edges_result.geojson",
            "application/geo+json",
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
        _render_download_link(
            st,
            "Descargar video (MP4)",
            Path(video_path).read_bytes(),
            "simulation.mp4",
            "video/mp4",
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
        sim_run = Path(run_dir) if _run_dir_has_sumo_project(Path(run_dir)) else None
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

