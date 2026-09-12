"""Shared Streamlit wizard helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.network_build import annotate_edge_directions  # noqa: E402
from src.wizard_state import STEPS, ensure_session_defaults  # noqa: E402
from src.viz import (  # noqa: E402
    add_edges_layer,
    add_polygon,
    add_tls_markers,
    base_map,
    map_key,
)


def go_to(step: str) -> None:
    st.session_state.step = step
    st.session_state._pending_nav = step


def apply_pending_nav() -> None:
    pending = st.session_state.pop("_pending_nav", None)
    if pending:
        st.session_state.step = pending
        st.session_state.nav_step = pending


def apply_pending_place() -> None:
    if "_pending_city" in st.session_state:
        st.session_state.city = st.session_state.pop("_pending_city")
    if "_pending_country" in st.session_state:
        st.session_state.country = st.session_state.pop("_pending_country")


def init_state() -> None:
    ensure_session_defaults(st.session_state)
    apply_pending_nav()
    apply_pending_place()
    if st.session_state.get("nav_step") not in STEPS:
        st.session_state.nav_step = st.session_state.step


def _bump_map(center: tuple[float, float] | None = None, zoom: int | None = None) -> None:
    if center is not None:
        st.session_state.center = center
    if zoom is not None:
        st.session_state.map_zoom = zoom
    st.session_state.map_nonce = int(st.session_state.get("map_nonce", 0)) + 1


def _guess_place_from_filename(name: str) -> str:
    stem = Path(name).stem
    stem = stem.replace("_", " ").replace("-", " ").strip()
    return stem.title() if stem else ""


def render_study_map(
    *,
    title: str,
    mode: str = "plain",
    value_by_id: dict | None = None,
    show_tls: bool = False,
    show_area: bool = True,
    show_direction: bool = False,
    height: int = 480,
    key_prefix: str = "study",
) -> None:
    """Always-visible Folium map for the current study area / network."""
    area = st.session_state.area
    edges_gj = st.session_state.edges_gj
    if area is None:
        return

    if edges_gj and (edges_gj.get("features") or []) and "oneway" not in (
        (edges_gj["features"][0].get("properties") or {})
    ):
        edges_gj = annotate_edge_directions(edges_gj)
        st.session_state.edges_gj = edges_gj

    st.subheader(title)
    m = base_map(area.center, zoom=int(st.session_state.get("map_zoom", 14)))
    if edges_gj:
        add_edges_layer(
            m,
            edges_gj,
            value_by_id=value_by_id,
            mode=mode,
            name="Red / tráfico",
            fit=True,
            show_direction=show_direction and mode == "plain",
        )
        stats = edges_gj.get("_direction_stats") or {}
        if stats:
            st.caption(
                f"Sentidos OSM/SUMO: **{stats.get('oneway_edges', 0)}** un sentido · "
                f"**{stats.get('twoway_edges', 0)}** doble sentido "
                f"({stats.get('oneway_pct', 0)}% único). "
                "Los vehículos solo circulan en el sentido del edge (flechas)."
            )
    if show_area:
        add_polygon(m, area.polygon, fit=not bool(edges_gj))
    if show_tls:
        add_tls_markers(m, st.session_state.tls_list or [])

    n_edges = len((edges_gj or {}).get("features", []))
    n_vals = len(value_by_id or {})
    try:
        from streamlit_folium import st_folium

        st_folium(
            m,
            width=None,
            height=height,
            key=map_key(
                key_prefix,
                mode,
                area.center,
                n_edges,
                n_vals,
                show_direction,
                st.session_state.get("map_nonce", 0),
            ),
        )
    except ImportError:
        st.info("Instale streamlit-folium para ver el mapa.")


