"""OptiTraffic wizard step module."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tomtom import (  # noqa: E402
    fetch_traffic_for_bbox,
    load_api_key,
    match_traffic_to_edges,
    save_snapshot,
    synthetic_peak_edge_levels,
    usage_count,
)
from src.wizard_state import STEPS  # noqa: E402
from ui.common import (  # noqa: E402
    _bump_map,
    go_to,
    render_study_map,
)


def step_tomtom() -> None:
    st.header("4. Tráfico real TomTom")
    area = st.session_state.area
    if area is None or not st.session_state.edges_gj:
        st.warning("Se requieren zona y red. Genere la red en el paso 2.")
        if st.button("Ir a Red OSM/SUMO"):
            go_to(STEPS[1])
        return

    levels = st.session_state.edge_levels or {}
    # Mapa persistente: red base o congestión TomTom si ya hay datos
    render_study_map(
        title="Mapa de tráfico",
        mode="tomtom" if levels else "plain",
        value_by_id=levels or None,
        show_tls=True,
        key_prefix="tomtom",
    )

    key = load_api_key()
    used, limit = usage_count()
    if key:
        st.write(f"Uso del mes: **{used:,}** / {limit:,} tiles")
    else:
        st.warning("Sin `TOMTOM_API_KEY` en `.env`. Puede continuar con densidad sintética.")

    if levels:
        pct = 100.0 * len(levels) / max(1, len(st.session_state.edges_gj["features"]))
        src = st.session_state.get("edge_levels_source") or "tomtom"
        label = "sintética (hora pico)" if src == "synthetic" else "TomTom"
        st.metric("Edges calibrados", f"{len(levels)} ({pct:.0f}%) · {label}")
    else:
        st.caption("Aún sin calibración — el mapa muestra la red base.")

    st.caption(
        "TomTom Vector Flow **no tiene cobertura en Costa Rica** (tiles HTTP 200 pero vacíos). "
        "En Europa/EE.UU. sí hay datos. Para Liberia use calibración sintética de hora pico."
    )

    zoom = st.slider("Zoom tiles", 13, 16, 15, disabled=not bool(key))
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        fetch = st.button(
            "Obtener flujo TomTom",
            type="primary",
            disabled=not bool(key),
            width="stretch",
        )
    with col_b:
        synth = st.button(
            "Calibración sintética (hora pico)",
            width="stretch",
        )
    with col_c:
        cont = st.button(
            "Continuar a simulación →",
            width="stretch",
            type="secondary",
        )

    if synth:
        peak = st.session_state.get("peak_hour", True)
        new_levels = synthetic_peak_edge_levels(st.session_state.edges_gj, peak=peak)
        st.session_state.edge_levels = new_levels
        st.session_state.edge_levels_source = "synthetic"
        st.session_state.tomtom_segments = 0
        snap = ROOT / "data" / "cache" / "tomtom_snapshot.json"
        save_snapshot(snap, new_levels)
        _bump_map()
        st.success(
            f"Calibración sintética: {len(new_levels)} edges "
            f"({'hora pico' if peak else 'fuera de pico'}). No es tráfico TomTom real."
        )
        st.rerun()

    if fetch and key:
        st.info("Consultando TomTom… el mapa de arriba se actualizará al terminar.")
        try:
            with st.spinner("Descargando vector flow tiles y emparejando edges…"):
                segs = fetch_traffic_for_bbox(area.bbox, api_key=key, zoom=zoom)
                new_levels = match_traffic_to_edges(st.session_state.edges_gj, segs)
                st.session_state.edge_levels = new_levels
                st.session_state.edge_levels_source = "tomtom"
                st.session_state.tomtom_segments = len(segs)
                snap = ROOT / "data" / "cache" / "tomtom_snapshot.json"
                save_snapshot(snap, new_levels)
                _bump_map()
            stats = getattr(fetch_traffic_for_bbox, "last_stats", {}) or {}
            pct = 100.0 * len(new_levels) / max(1, len(st.session_state.edges_gj["features"]))
            msg = (
                f"{len(segs)} segmentos TomTom · {len(new_levels)} edges calibrados ({pct:.0f}%) · "
                f"tiles con datos {stats.get('tiles_ok', '?')}/{stats.get('tiles_total', '?')} "
                f"(vacíos {stats.get('tiles_empty', 0)})"
            )
            if stats.get("errors"):
                st.warning(
                    "Algunos tiles fallaron (timeout/red); se usó caché o se omitieron. "
                    + "; ".join(stats["errors"][:2])
                )
            st.success(msg)
            st.rerun()
        except Exception as e:
            err = str(e)
            if key:
                err = err.replace(key, "***")
            st.error(err)
            if "sin datos de tráfico" in err.lower() or "cobertura" in err.lower():
                st.info(
                    "Use el botón **Calibración sintética (hora pico)** para seguir con "
                    "demanda/velocidades relativas por avenida/calle."
                )
            else:
                st.caption(
                    "Si es timeout: revise internet/VPN/firewall hacia api.tomtom.com, "
                    "baje el zoom (p. ej. 14) e intente de nuevo. "
                    "Puede continuar a simulación sin TomTom (corr. saldrá n/d)."
                )

    if cont:
        go_to(STEPS[4])


