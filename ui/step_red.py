"""OptiTraffic wizard step module."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.editors import (  # noqa: E402
    NetworkEdits,
    default_stops_ns_at_avenues,
    default_street_rules_for_edges,
    merge_default_stops,
    merge_default_street_rules,
)
from src.network_build import (  # noqa: E402
    build_network,
    edges_geojson,
    list_traffic_lights,
    save_edges_geojson,
)
from src.osm_fetch import (  # noqa: E402
    MAX_GEOFABRIK_PBF_BYTES,
    SAFE_ROOT,
    geofabrik_url,
    listed_countries,
    prepare_clipped_osm,
    resolve_geofabrik_path,
)
from src.sumo_background import fetch_sumo_background  # noqa: E402
from src.sumo_env import detect_sumo  # noqa: E402
from src.wizard_state import STEPS  # noqa: E402
from ui.common import (  # noqa: E402
    _bump_map,
    go_to,
    render_study_map,
)
from src.viz import (  # noqa: E402
    build_junctions,
)


def step_red() -> None:
    st.header("2. Red OSM → SUMO")
    area = st.session_state.area
    if area is None:
        st.warning("Defina primero la zona de estudio.")
        return

    st.write(
        f"**{area.label}** · {area.area_km2:.2f} km² · "
        f"bbox `{tuple(round(x, 5) for x in area.bbox)}`"
    )

    # Mapa siempre visible (zona y/o red ya generada)
    render_study_map(
        title="Mapa de la zona / red",
        mode="plain",
        show_tls=True,
        show_direction=True,
        key_prefix="net",
    )

    st.info(
        "La red respeta **oneway** de OpenStreetMap: si OSM marca un solo sentido, "
        "SUMO solo crea ese edge dirigido. Si OSM no trae `oneway=yes`, la calle queda "
        "de doble sentido (dos edges). Regenerar la red aplica atributos OSM completos."
    )

    force = st.checkbox("Forzar re-descarga (ignorar caché local)", value=False)
    osm_source = st.radio(
        "Fuente OSM",
        options=["auto", "overpass", "geofabrik"],
        format_func=lambda k: {
            "auto": "Auto (Overpass → Geofabrik)",
            "overpass": "Solo Overpass (rápido, zona pequeña)",
            "geofabrik": "Solo Geofabrik (extracto país completo)",
        }[k],
        horizontal=True,
        key="osm_source",
    )
    if osm_source == "geofabrik":
        st.warning(
            f"Geofabrik descarga el extracto de país completo "
            f"(tope **{MAX_GEOFABRIK_PBF_BYTES / 1e6:.0f} MB**; "
            "variable `OPTITRAFFIC_MAX_PBF_MB`). Prefiera Auto/Overpass si puede."
        )
    try:
        code = getattr(area, "country_code", "") or st.session_state.get("country_code") or ""
        gf_path = resolve_geofabrik_path(area.country, country_code=code)
        gf_url = geofabrik_url(area.country, country_code=code)
        st.caption(f"Geofabrik para **{area.country}**: `{gf_path}` → {gf_url}")
    except Exception as e:
        st.warning(str(e))
        st.caption("Países con extracto mapeado: " + ", ".join(listed_countries()[:20]) + "…")

    bg_on_build = st.radio(
        "Fondo sumo-gui al generar la red",
        options=["osm", "satellite", "ninguno"],
        format_func=lambda k: {
            "osm": "OpenStreetMap",
            "satellite": "Satélite",
            "ninguno": "Omitir (se puede pedir en simulación)",
        }[k],
        horizontal=True,
        index=0,
        key="bg_on_build",
    )

    sumo = detect_sumo()
    if st.button("Descargar OSM, recortar y generar red SUMO", type="primary"):
        if not sumo.ok:
            st.error(sumo.message)
            return
        status = st.empty()
        status.info("Obteniendo OSM y generando red… el mapa de arriba permanece visible.")
        try:
            with st.spinner("Descargando / recortando OSM…"):
                extract, clipped = prepare_clipped_osm(
                    area,
                    force_download=force,
                    source=osm_source,
                )
            status.write(f"Extracto: `{extract.name}` · Recorte: `{clipped}`")
            with st.spinner("Ejecutando netconvert…"):
                net = build_network(clipped, area, sumo=sumo)
                st.session_state.net_path = net
                gj = edges_geojson(net, sumo=sumo)
                st.session_state.edges_gj = gj
                st.session_state.sim_result = None
                st.session_state.run_dir = None
                st.session_state.pop("sim_job", None)
                st.session_state.pop("background_dir", None)
                save_edges_geojson(net)
                stats = gj.get("_direction_stats") or {}
                try:
                    st.session_state.tls_list = list_traffic_lights(net, sumo=sumo)
                except Exception:
                    st.session_state.tls_list = []
                # Default CR rule: calles N–S alto × avenidas E–O libres
                try:
                    juncs = build_junctions(gj)
                    tls_jids = {
                        str(t.get("node") or t.get("id") or "")
                        for t in (st.session_state.tls_list or [])
                    }
                    suggested = default_stops_ns_at_avenues(
                        gj, juncs, min_degree=3, skip_junction_ids=tls_jids
                    )
                    edits: NetworkEdits = st.session_state.edits
                    n_added = merge_default_stops(edits, suggested, replace_defaults=True)
                    lane_defs, park_defs = default_street_rules_for_edges(gj)
                    n_lane, n_park = merge_default_street_rules(
                        edits, lane_defs, park_defs, replace_defaults=True
                    )
                    st.session_state.edits = edits
                    st.session_state._default_stops_applied = n_added
                    st.session_state._default_lanes_applied = n_lane
                    st.session_state._default_parking_applied = n_park
                except Exception:
                    st.session_state._default_stops_applied = 0
                    st.session_state._default_lanes_applied = 0
                    st.session_state._default_parking_applied = 0
                _bump_map()
            n_def = int(st.session_state.get("_default_stops_applied") or 0)
            n_lane = int(st.session_state.get("_default_lanes_applied") or 0)
            n_park = int(st.session_state.get("_default_parking_applied") or 0)
            bg_msg = ""
            if bg_on_build in ("osm", "satellite"):
                status.info(f"Descargando fondo {bg_on_build} para sumo-gui…")
                try:
                    with st.spinner(f"Teselas {bg_on_build}…"):
                        bg_dir = SAFE_ROOT / "background" / Path(net).stem
                        bg_settings = fetch_sumo_background(
                            Path(net),
                            bg_dir,
                            style=bg_on_build,  # type: ignore[arg-type]
                            max_tiles=36,
                            sumo=sumo,
                            force=bool(force),
                        )
                    if bg_settings:
                        st.session_state.background_dir = str(bg_dir)
                        bg_msg = f" · fondo {bg_on_build} OK"
                    else:
                        bg_msg = " · fondo no disponible"
                except Exception:
                    bg_msg = " · fondo falló"
            st.success(
                f"Red generada: {net} ({len(gj.get('features', []))} edges) · "
                f"sentido único={stats.get('oneway_edges', '?')} · "
                f"doble={stats.get('twoway_edges', '?')} · "
                f"altos default N–S={n_def} · "
                f"1 carril default={n_lane} · parqueo der. lleno={n_park}"
                f"{bg_msg}"
            )
            st.rerun()
        except Exception as e:
            st.error(f"OSM/red: {e}")
            return

    if st.session_state.edges_gj and st.button("Continuar a configuración"):
        go_to(STEPS[2])


