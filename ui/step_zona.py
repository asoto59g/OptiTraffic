"""OptiTraffic wizard step module."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.area import (  # noqa: E402
    MAX_AREA_KM2,
    build_study_area,
    geocode_city,
    load_geojson_polygon,
    polygon_from_bounds,
    rectangle_around,
    reverse_geocode_full,
    validate_area,
)
from src.osm_fetch import (  # noqa: E402
    listed_countries,
    normalize_country,
    resolve_geofabrik_path,
)
from src.wizard_state import STEPS  # noqa: E402
from ui.common import (  # noqa: E402
    _bump_map,
    _guess_place_from_filename,
    go_to,
)
from src.viz import (  # noqa: E402
    add_draw_control,
    add_polygon,
    base_map,
    extract_drawn_geojson,
    fit_polygon,
    map_key,
)


def _country_options() -> list[str]:
    """Title-cased country names for Geofabrik selectbox."""
    opts = sorted({c.title() for c in listed_countries() if len(c) > 3})
    # Ensure Costa Rica present with common capitalization
    if "Costa Rica" not in opts:
        opts = sorted(opts + ["Costa Rica"])
    return opts


def _sync_country_to_options(options: list[str]) -> None:
    """Map free-text / reverse-geocode country into a known select option when possible."""
    cur = str(st.session_state.get("country") or "").strip()
    if not cur:
        st.session_state.country = "Costa Rica"
        return
    if cur in options:
        return
    n = normalize_country(cur)
    match = next((o for o in options if normalize_country(o) == n), None)
    if match:
        st.session_state.country = match
    elif cur not in options:
        # Keep custom label so reverse-geocode results are not lost
        options.insert(0, cur)


def step_zona() -> None:
    st.header("1. Zona de estudio")
    st.write(
        "Indique ciudad y país, luego defina un rectángulo, dibuje un polígono "
        f"o suba un GeoJSON. Límite MVP: **{MAX_AREA_KM2} km²**."
    )

    country_opts = _country_options()
    _sync_country_to_options(country_opts)

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        city = st.text_input("Ciudad", key="city")
    with c2:
        country = st.selectbox(
            "País (Geofabrik)",
            options=country_opts,
            key="country",
            help="Lista de países con extracto conocido. Reduce errores de tipeo/acentos.",
        )
    with c3:
        if st.button("Geocodificar", width="stretch"):
            try:
                lat, lon, addr = geocode_city(city, country)
                st.session_state.preview_polygon = None
                _bump_map(center=(lat, lon), zoom=14)
                st.success(addr)
                st.rerun()
            except Exception as e:
                st.error(str(e))

    try:
        gf = resolve_geofabrik_path(
            country,
            country_code=st.session_state.get("country_code") or "",
        )
        st.caption(f"Extracto Geofabrik previsto: `{gf}-latest.osm.pbf`")
    except Exception as e:
        st.warning(f"Geofabrik: {e}")

    mode = st.radio(
        "Método de delimitación",
        ["Rectángulo alrededor del centro", "Bounds manuales", "Dibujo en mapa", "Subir GeoJSON"],
        horizontal=True,
        key="zona_mode",
    )

    polygon = None
    if mode == "Rectángulo alrededor del centro":
        half = st.slider("Media longitud (km)", 0.3, 2.5, 0.8, 0.1)
        lat, lon = st.session_state.center
        polygon = rectangle_around(lat, lon, half_km=half)
    elif mode == "Bounds manuales":
        lat, lon = st.session_state.center
        b1, b2, b3, b4 = st.columns(4)
        south = b1.number_input("Sur", value=round(lat - 0.01, 5), format="%.5f")
        west = b2.number_input("Oeste", value=round(lon - 0.01, 5), format="%.5f")
        north = b3.number_input("Norte", value=round(lat + 0.01, 5), format="%.5f")
        east = b4.number_input("Este", value=round(lon + 0.01, 5), format="%.5f")
        try:
            polygon = polygon_from_bounds(south, west, north, east)
        except ValueError as e:
            st.error(str(e))
    elif mode == "Dibujo en mapa":
        st.info("Dibuje un polígono o rectángulo con las herramientas del mapa (arriba izquierda).")
        m = base_map(st.session_state.center, zoom=int(st.session_state.get("map_zoom", 14)))
        add_draw_control(m)
        try:
            from streamlit_folium import st_folium

            out = st_folium(
                m,
                width=None,
                height=480,
                key=map_key("draw", st.session_state.center, st.session_state.map_nonce),
            )
            drawn = extract_drawn_geojson(out)
            if drawn:
                try:
                    polygon = load_geojson_polygon(drawn)
                    st.success("Polígono capturado del mapa")
                except Exception as e:
                    st.warning(f"Dibujo no válido aún: {e}")
        except ImportError:
            st.error("Instale streamlit-folium")
    else:
        up = st.file_uploader("GeoJSON (Polygon)", type=["geojson", "json"])
        if up is not None:
            file_id = f"{up.name}_{up.size}"
            try:
                polygon = load_geojson_polygon(up.getvalue())
                minx, miny, maxx, maxy = polygon.bounds
                center = ((miny + maxy) / 2, (minx + maxx) / 2)
                if st.session_state.get("geojson_file_id") != file_id:
                    st.session_state.geojson_file_id = file_id
                    st.session_state.preview_polygon = polygon
                    _bump_map(center=center, zoom=15)
                    # Defer city/country update (cannot set after text_input widgets)
                    try:
                        rcity, rcountry, addr, ccode = reverse_geocode_full(
                            center[0], center[1]
                        )
                        st.session_state._pending_city = rcity or _guess_place_from_filename(
                            up.name
                        )
                        if rcountry:
                            st.session_state._pending_country = rcountry
                        if ccode:
                            st.session_state.country_code = ccode
                        st.session_state.last_reverse_addr = addr
                    except Exception:
                        guess = _guess_place_from_filename(up.name)
                        if guess:
                            st.session_state._pending_city = guess
                    st.rerun()
                else:
                    st.session_state.preview_polygon = polygon
                    st.session_state.center = center
                if st.session_state.get("last_reverse_addr"):
                    st.caption(f"Ubicación detectada: {st.session_state.last_reverse_addr}")
            except Exception as e:
                st.error(str(e))
                polygon = st.session_state.get("preview_polygon")

    if polygon is not None:
        ok, msg = validate_area(polygon)
        if ok:
            st.success(msg)
        else:
            st.error(msg)

        minx, miny, maxx, maxy = polygon.bounds
        poly_center = ((miny + maxy) / 2, (minx + maxx) / 2)
        m2 = base_map(poly_center, zoom=int(st.session_state.get("map_zoom", 14)))
        add_polygon(m2, polygon, fit=True)
        fit_polygon(m2, polygon)
        try:
            from streamlit_folium import st_folium

            st_folium(
                m2,
                width=None,
                height=360,
                key=map_key(
                    "preview",
                    poly_center,
                    round(minx, 5),
                    round(miny, 5),
                    round(maxx, 5),
                    round(maxy, 5),
                    st.session_state.map_nonce,
                ),
            )
        except ImportError:
            st.write(polygon.bounds)

        st.write(f"Ciudad/país actuales: **{st.session_state.city}**, **{st.session_state.country}**")

        if ok and st.button("Confirmar zona", type="primary"):
            try:
                area = build_study_area(
                    st.session_state.city,
                    st.session_state.country,
                    polygon,
                    country_code=st.session_state.get("country_code") or "",
                )
                st.session_state.area = area
                st.session_state.center = area.center
                st.session_state.preview_polygon = polygon
                # Clear previous network so Liberia doesn't keep San José edges
                st.session_state.net_path = None
                st.session_state.edges_gj = None
                st.session_state.tls_list = []
                st.session_state.edge_levels = {}
                st.session_state.flow_gates = []
                st.session_state.sim_gate_edge_id = None
                st.session_state.sim_result = None
                st.session_state.run_dir = None
                st.session_state.pop("sim_job", None)
                st.session_state.pop("background_dir", None)
                st.success(f"Zona lista: {area.label} ({area.area_km2:.2f} km²)")
                go_to(STEPS[1])
            except Exception as e:
                st.error(str(e))
    elif mode != "Dibujo en mapa":
        m0 = base_map(st.session_state.center, zoom=int(st.session_state.get("map_zoom", 14)))
        try:
            from streamlit_folium import st_folium

            st_folium(
                m0,
                width=None,
                height=360,
                key=map_key("city", st.session_state.center, st.session_state.map_nonce),
            )
        except ImportError:
            pass

