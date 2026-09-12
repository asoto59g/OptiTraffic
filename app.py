"""OptiTraffic — Streamlit wizard: zona → red OSM/SUMO → config → TomTom → simulación."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.area import (  # noqa: E402
    MAX_AREA_KM2,
    build_study_area,
    geocode_city,
    load_geojson_polygon,
    polygon_from_bounds,
    rectangle_around,
    reverse_geocode,
    validate_area,
)
from src.demand import DENSITY_SCENARIOS, generate_demand, get_density_scenario  # noqa: E402
from src.editors import (  # noqa: E402
    LaneOverride,
    NetworkEdits,
    ParkingConfig,
    StopSign,
    TlsPlacement,
    TlsTiming,
    write_all_additionals,
)
from src.network_build import (  # noqa: E402
    annotate_edge_directions,
    build_network,
    cap_network_speeds,
    edges_geojson,
    list_traffic_lights,
    save_edges_geojson,
)
from src.osm_fetch import (  # noqa: E402
    geofabrik_url,
    listed_countries,
    prepare_clipped_osm,
    resolve_geofabrik_path,
)
from src.scenarios import (  # noqa: E402
    apply_scenario_to_session,
    list_scenarios,
    load_scenario,
    save_scenario,
    scenarios_matching_area,
)
from src.simulate import (  # noqa: E402
    SAFE_RUNS,
    export_edge_csv,
    export_edge_geojson,
    run_simulation,
    write_sumocfg,
    _close_traci,
)
from src.sumo_env import detect_sumo  # noqa: E402
from src.tomtom import (  # noqa: E402
    fetch_traffic_for_bbox,
    load_api_key,
    match_traffic_to_edges,
    save_snapshot,
    segments_to_geojson,
    usage_count,
)
from src.traffic_params import CITY_MAX_SPEED_KMH, CITY_MAX_SPEED_MS, VEH_LENGTH_M  # noqa: E402
from src.viz import (  # noqa: E402
    add_draw_control,
    add_edges_layer,
    add_junction_markers,
    add_polygon,
    add_tls_markers,
    base_map,
    build_junctions,
    click_latlon,
    edge_center_latlon,
    extract_drawn_geojson,
    fit_edge,
    fit_junction,
    fit_polygon,
    map_key,
    nearest_edge_id,
    nearest_junction,
    nearest_tls,
    simplify_edges_for_map,
)

st.set_page_config(
    page_title="OptiTraffic",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded",
)

STEPS = [
    "1. Zona",
    "2. Red OSM/SUMO",
    "3. Configuración",
    "4. TomTom",
    "5. Simulación",
    "6. Resultados",
]


def go_to(step: str) -> None:
    """Navigate wizard. Sync sidebar radio on next run (never after widget exists)."""
    if step not in STEPS:
        return
    st.session_state.step = step
    st.session_state._pending_nav = step
    st.rerun()


def apply_pending_nav() -> None:
    """Must run BEFORE sidebar radio(key='nav_step') is created."""
    pending = st.session_state.pop("_pending_nav", None)
    if pending in STEPS:
        st.session_state.step = pending
        st.session_state.nav_step = pending


def apply_pending_place() -> None:
    """Apply deferred city/country before text_input widgets exist."""
    if "_pending_city" in st.session_state:
        st.session_state.city = st.session_state.pop("_pending_city")
    if "_pending_country" in st.session_state:
        st.session_state.country = st.session_state.pop("_pending_country")


def init_state() -> None:
    defaults = {
        "step": STEPS[0],
        "nav_step": STEPS[0],
        "area": None,
        "net_path": None,
        "edges_gj": None,
        "tls_list": [],
        "edits": NetworkEdits(),
        "edge_levels": {},
        "sim_result": None,
        "run_dir": None,
        "center": (9.93, -84.08),
        "map_zoom": 14,
        "map_nonce": 0,
        "preview_polygon": None,
        "city": "San José",
        "country": "Costa Rica",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    apply_pending_nav()
    apply_pending_place()
    if st.session_state.get("nav_step") not in STEPS:
        st.session_state.nav_step = st.session_state.step


def sidebar() -> None:
    st.sidebar.title("OptiTraffic")
    st.sidebar.caption("Simulador urbano · Streamlit + SUMO + TomTom")

    sumo = detect_sumo()
    if sumo.ok:
        st.sidebar.success(sumo.message)
    else:
        st.sidebar.error(sumo.message)
        st.sidebar.markdown(
            "Instale [SUMO](https://eclipse.dev/sumo/) y defina `SUMO_HOME`. "
            "Vea el README."
        )

    used, limit = usage_count()
    key = load_api_key()
    if key:
        st.sidebar.info(f"TomTom: {used:,} / {limit:,} tiles (mes)")
    else:
        st.sidebar.warning("Sin TOMTOM_API_KEY (.env)")

    # nav_step already synced via apply_pending_nav() before this widget
    st.sidebar.radio("Pasos", STEPS, key="nav_step")
    if st.session_state.nav_step != st.session_state.step:
        st.session_state.step = st.session_state.nav_step

    st.sidebar.divider()
    st.sidebar.subheader("Escenarios guardados")
    scenarios = list_scenarios()
    if scenarios:
        labels = [p.name for p in scenarios]
        pick = st.sidebar.selectbox("Cargar configuración", ["—"] + labels, key="sidebar_scen_pick")
        if pick != "—" and st.sidebar.button("Abrir escenario", key="sidebar_scen_open"):
            data = load_scenario(next(p for p in scenarios if p.name == pick))
            hint = apply_scenario_to_session(data, st.session_state)
            st.sidebar.success(f"Cargado: {data['meta'].get('name', pick)}")
            if hint == "config":
                go_to(STEPS[2])
            elif hint == "red":
                go_to(STEPS[1])
            else:
                go_to(STEPS[0])
    else:
        st.sidebar.caption("Aún no hay escenarios. Guarde desde Configuración o Resultados.")


def _bump_map(center: tuple[float, float] | None = None, zoom: int | None = None) -> None:
    """Force Folium remount after city / polygon changes."""
    if center is not None:
        st.session_state.center = center
    if zoom is not None:
        st.session_state.map_zoom = zoom
    st.session_state.map_nonce = int(st.session_state.get("map_nonce", 0)) + 1


def _guess_place_from_filename(name: str) -> str:
    stem = Path(name).stem
    stem = stem.replace("_", " ").replace("-", " ").strip()
    return stem


def step_zona() -> None:
    st.header("1. Zona de estudio")
    st.write(
        "Indique ciudad y país, luego defina un rectángulo, dibuje un polígono "
        f"o suba un GeoJSON. Límite MVP: **{MAX_AREA_KM2} km²**."
    )

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        city = st.text_input("Ciudad", key="city")
    with c2:
        country = st.text_input("País", key="country")
    with c3:
        if st.button("Geocodificar", use_container_width=True):
            try:
                lat, lon, addr = geocode_city(city, country)
                st.session_state.preview_polygon = None
                _bump_map(center=(lat, lon), zoom=14)
                st.success(addr)
                st.rerun()
            except Exception as e:
                st.error(str(e))

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
                        rcity, rcountry, addr = reverse_geocode(center[0], center[1])
                        st.session_state._pending_city = rcity or _guess_place_from_filename(up.name)
                        if rcountry:
                            st.session_state._pending_country = rcountry
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
                )
                st.session_state.area = area
                st.session_state.center = area.center
                st.session_state.preview_polygon = polygon
                # Clear previous network so Liberia doesn't keep San José edges
                st.session_state.net_path = None
                st.session_state.edges_gj = None
                st.session_state.tls_list = []
                st.session_state.edge_levels = {}
                st.session_state.sim_result = None
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
    try:
        gf_path = resolve_geofabrik_path(area.country)
        gf_url = geofabrik_url(area.country)
        st.caption(f"Geofabrik para **{area.country}**: `{gf_path}` → {gf_url}")
    except Exception as e:
        st.warning(str(e))
        st.caption("Países con extracto mapeado: " + ", ".join(listed_countries()[:20]) + "…")

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
                save_edges_geojson(net)
                stats = gj.get("_direction_stats") or {}
                try:
                    st.session_state.tls_list = list_traffic_lights(net, sumo=sumo)
                except Exception:
                    st.session_state.tls_list = []
                _bump_map()
            st.success(
                f"Red generada: {net} ({len(gj.get('features', []))} edges) · "
                f"sentido único={stats.get('oneway_edges', '?')} · "
                f"doble={stats.get('twoway_edges', '?')}"
            )
            st.rerun()
        except Exception as e:
            st.error(f"OSM/red: {e}")
            return

    if st.session_state.edges_gj and st.button("Continuar a configuración"):
        go_to(STEPS[2])


def step_config() -> None:
    st.header("3. Configuración vial")
    st.info(
        "Para **semáforos**: clic en el **punto gris del cruce** (intersección). "
        "Para parqueo/alto/carriles: clic en la **línea** de la calle. "
        "Luego pulse **Confirmar** debajo del mapa."
    )
    edits: NetworkEdits = st.session_state.edits
    for k, v in {
        "selected_edge_id": None,
        "selected_edge_name": "",
        "selected_tls_id": None,
        "selected_junction_id": None,
        "selected_junction": None,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v

    area = st.session_state.area
    edges_gj = st.session_state.edges_gj
    if area is None or not edges_gj:
        st.warning("Genere primero la red en el paso 2.")
        if st.button("Ir a Red OSM/SUMO"):
            go_to(STEPS[1])
        return

    if edges_gj and "oneway" not in ((edges_gj.get("features") or [{}])[0].get("properties") or {}):
        edges_gj = annotate_edge_directions(edges_gj)
        st.session_state.edges_gj = edges_gj
        st.session_state._junctions_key = None  # refresh slim map cache

    junctions_key = (
        f"junc_{len(edges_gj.get('features', []))}_{st.session_state.get('map_nonce', 0)}"
    )
    if st.session_state.get("_junctions_key") != junctions_key:
        st.session_state._junctions_key = junctions_key
        st.session_state._junctions_cache = build_junctions(edges_gj)
        st.session_state._edges_map_slim = simplify_edges_for_map(edges_gj, max_points=6)
    junctions = st.session_state._junctions_cache
    edges_map = st.session_state.get("_edges_map_slim") or edges_gj

    color_by_id: dict[str, str] = {}
    weight_by_id: dict[str, int] = {}
    for p in edits.parking:
        color_by_id[p.edge_id] = "#27ae60"
        weight_by_id[p.edge_id] = 5
    for s in edits.stops:
        color_by_id[s.edge_id] = "#e67e22"
        weight_by_id[s.edge_id] = 5
    for lo in edits.lane_overrides:
        if lo.edge_id not in color_by_id:
            color_by_id[lo.edge_id] = "#9b59b6"
            weight_by_id[lo.edge_id] = 5
    for p in edits.tls_placements:
        color_by_id[p.edge_id] = "#f1c40f"
        weight_by_id[p.edge_id] = 6
        if p.junction_id:
            for j in junctions:
                if j["id"] == p.junction_id:
                    for e2 in j.get("edges") or []:
                        color_by_id[e2] = "#f1c40f"
                        weight_by_id[e2] = 6

    sel = st.session_state.selected_edge_id
    sel_j = st.session_state.selected_junction_id
    if sel_j:
        for j in junctions:
            if j["id"] == sel_j:
                for e2 in j.get("edges") or []:
                    color_by_id[e2] = "#e74c3c"
                    weight_by_id[e2] = 7
    elif sel:
        color_by_id[sel] = "#e74c3c"
        weight_by_id[sel] = 7

    st.subheader("Mapa interactivo")
    st.caption(
        "La lentitud al confirmar no depende de Geofabrik: el mapa Folium se reconstruye en local. "
        "Se usa una versión simplificada de la red para acelerar."
    )
    zoom_cfg = st.slider(
        "Zoom del mapa de configuración",
        min_value=15,
        max_value=19,
        value=int(st.session_state.get("config_zoom", 17)),
        help="Suba el zoom si cuesta hacer clic en el cruce",
    )
    st.session_state.config_zoom = zoom_cfg

    cleg1, cleg2, cleg3, cleg4, cleg5, cleg6 = st.columns(6)
    cleg1.markdown("⚫ Cruce")
    cleg2.markdown("🔴 Seleccionado")
    cleg3.markdown("🟡 Semáforo OK")
    cleg4.markdown("🟢 Parqueo")
    cleg5.markdown("🟠 Alto")
    cleg6.markdown("▲ Sentido OSM")

    map_center = area.center
    if sel_j and st.session_state.selected_junction:
        map_center = (
            float(st.session_state.selected_junction["lat"]),
            float(st.session_state.selected_junction["lon"]),
        )
    elif sel:
        mid = edge_center_latlon(edges_gj, sel)
        if mid:
            map_center = mid

    m = base_map(map_center, zoom=zoom_cfg)
    add_edges_layer(
        m,
        edges_map,
        mode="plain",
        fit=False,
        color_by_id=color_by_id,
        weight_by_id=weight_by_id,
        weight=3,
        show_direction=True,
        direction_max_arrows=180,
    )
    add_junction_markers(m, junctions, selected_id=sel_j, min_degree=2)
    add_tls_markers(m, st.session_state.tls_list or [], selected_id=st.session_state.selected_tls_id)
    if sel_j and st.session_state.selected_junction:
        fit_junction(m, st.session_state.selected_junction, pad=0.0006)
    elif sel:
        fit_edge(m, edges_gj, sel, pad=0.00045)

    # Remount only when selection/zoom/style changes — not on every widget tick.
    style_sig = (
        len(edits.parking),
        len(edits.stops),
        len(edits.lane_overrides),
        len(edits.tls_placements),
    )
    try:
        from streamlit_folium import st_folium

        map_out = st_folium(
            m,
            width=None,
            height=480,
            returned_objects=["last_object_clicked", "last_clicked"],
            key=map_key(
                "cfg_click_v2",
                area.center,
                sel,
                sel_j,
                zoom_cfg,
                style_sig,
            ),
        )
    except ImportError:
        map_out = None
        st.error("Instale streamlit-folium")

    clicked = click_latlon(map_out)
    if clicked:
        clat, clon = clicked
        changed = False
        j_hit = nearest_junction(junctions, clat, clon, max_dist_deg=0.0012, min_degree=2)
        if j_hit:
            if st.session_state.selected_junction_id != j_hit["id"]:
                st.session_state.selected_junction_id = j_hit["id"]
                st.session_state.selected_junction = j_hit
                edges_at = j_hit.get("edges") or []
                if edges_at:
                    st.session_state.selected_edge_id = edges_at[0]
                    st.session_state.selected_edge_name = ""
                    for feat in edges_gj.get("features", []):
                        if feat.get("properties", {}).get("id") == edges_at[0]:
                            st.session_state.selected_edge_name = feat["properties"].get("name") or ""
                            break
                changed = True
            near_tls = nearest_tls(st.session_state.tls_list or [], clat, clon, max_dist_deg=0.0010)
            if near_tls and st.session_state.selected_tls_id != near_tls["id"]:
                st.session_state.selected_tls_id = near_tls["id"]
                changed = True
        else:
            edge_hit = nearest_edge_id(edges_map, clat, clon)
            if edge_hit:
                eid = edge_hit.get("id")
                if eid and (
                    eid != st.session_state.selected_edge_id
                    or st.session_state.selected_junction_id is not None
                ):
                    st.session_state.selected_edge_id = eid
                    st.session_state.selected_edge_name = edge_hit.get("name") or ""
                    st.session_state.selected_junction_id = None
                    st.session_state.selected_junction = None
                    changed = True
        if changed:
            st.rerun()

    eid = st.session_state.selected_edge_id
    ename = st.session_state.selected_edge_name or "(sin nombre)"
    jid = st.session_state.selected_junction_id
    st.subheader("1) Confirmar selección")
    if not eid and not jid and not st.session_state.selected_tls_id:
        st.warning(
            "Haga clic en un **punto gris del cruce** para semáforo, o en una **calle** para parqueo/alto."
        )
    else:
        if jid:
            deg = (st.session_state.selected_junction or {}).get("degree", "?")
            st.success(f"Intersección seleccionada: `{jid}` ({deg} tramos) — lista para semáforo")
        elif eid:
            st.success(f"Calle seleccionada: **{ename}** · `{eid}`")
        if st.session_state.selected_tls_id:
            st.info(f"Semáforo OSM detectado cerca: `{st.session_state.selected_tls_id}`")

        st.markdown("**¿Qué desea confirmar en este punto?**")
        a1, a2, a3, a4 = st.columns(4)
        with a1:
            can_tls = bool(jid or eid)
            if st.button(
                "✅ Confirmar semáforo aquí",
                type="primary",
                use_container_width=True,
                disabled=not can_tls,
            ):
                linked = st.session_state.selected_tls_id
                edge_for_place = eid or ((st.session_state.selected_junction or {}).get("edges") or [""])[0]
                tid = linked or (f"tls_j_{jid}" if jid else f"tls_{edge_for_place}")
                edits.tls_placements = [
                    t
                    for t in edits.tls_placements
                    if t.edge_id != edge_for_place and t.junction_id != jid
                ]
                edits.tls_placements.append(
                    TlsPlacement(
                        edge_id=edge_for_place,
                        tls_id=tid,
                        junction_id=jid,
                        name=f"Cruce {jid}" if jid else ename,
                    )
                )
                edits.tls_overrides[tid] = edits.tls_default
                st.session_state.edits = edits
                st.success("Semáforo confirmado en la intersección.")
                st.rerun()
        with a2:
            if st.button("✅ Confirmar alto", use_container_width=True, disabled=not eid):
                if not any(s.edge_id == eid for s in edits.stops):
                    edits.stops.append(StopSign(edge_id=eid, junction_id=jid))
                st.session_state.edits = edits
                st.rerun()
        with a3:
            if st.button("✅ Confirmar parqueo", use_container_width=True, disabled=not eid):
                edits.parking = [p for p in edits.parking if p.edge_id != eid]
                edits.parking.append(
                    ParkingConfig(
                        edge_id=eid,
                        side="right",
                        corner_clearance_m=5.0,
                        length_m=40.0,
                        capacity=8,
                    )
                )
                st.session_state.edits = edits
                st.rerun()
        with a4:
            if st.button("🗑️ Quitar selección", use_container_width=True, disabled=not (eid or jid)):
                if eid:
                    edits.lane_overrides = [x for x in edits.lane_overrides if x.edge_id != eid]
                    edits.parking = [p for p in edits.parking if p.edge_id != eid]
                    edits.stops = [s for s in edits.stops if s.edge_id != eid]
                edits.tls_placements = [
                    t
                    for t in edits.tls_placements
                    if t.edge_id != eid and (not jid or t.junction_id != jid)
                ]
                st.session_state.edits = edits
                st.rerun()

    st.subheader("2) Tiempos de semáforo")
    g, y, r = st.columns(3)
    green = g.number_input("Verde (s)", 5, 180, edits.tls_default.green)
    yellow = y.number_input("Amarillo (s)", 1, 15, edits.tls_default.yellow)
    red = r.number_input("Rojo (s)", 5, 180, edits.tls_default.red)
    edits.tls_default = TlsTiming(green=int(green), yellow=int(yellow), red=int(red))

    target_tls = st.session_state.selected_tls_id
    if not target_tls and (eid or jid):
        for p in edits.tls_placements:
            if (jid and p.junction_id == jid) or (eid and p.edge_id == eid):
                target_tls = p.tls_id or f"tls_{p.edge_id}"
                break

    if target_tls:
        st.caption(f"Aplicar tiempos a: `{target_tls}`")
        if st.button("Aplicar estos tiempos al semáforo confirmado", type="primary"):
            edits.tls_overrides[target_tls] = TlsTiming(int(green), int(yellow), int(red))
            st.session_state.edits = edits
            st.success(f"Tiempos aplicados a {target_tls}")
            st.rerun()
    else:
        st.caption("Primero confirme un semáforo con el botón de arriba.")

    st.subheader("3) Opciones extra del tramo (opcional)")
    dual = st.checkbox(
        "2 carriles en el MISMO sentido OSM (no abre el sentido contrario)",
        value=False,
    )
    nlanes = st.number_input("Número de carriles", 1, 4, 2 if dual else 1)
    side = st.selectbox("Lado de parqueo", ["right", "left", "both"])
    clearance = st.number_input("Distancia legal esquina (m)", 3.0, 20.0, 5.0)
    plen = st.number_input("Longitud zona parqueo (m)", 10.0, 200.0, 40.0)
    cap = st.number_input("Capacidad parqueo", 1, 50, 8)

    x1, x2 = st.columns(2)
    with x1:
        if st.button("Aplicar carriles al tramo", use_container_width=True, disabled=not eid):
            edits.lane_overrides = [x for x in edits.lane_overrides if x.edge_id != eid]
            edits.lane_overrides.append(
                LaneOverride(edge_id=eid, num_lanes=int(nlanes), oneway_dual=dual)
            )
            st.session_state.edits = edits
            st.rerun()
    with x2:
        if st.button("Parqueo detallado al tramo", use_container_width=True, disabled=not eid):
            edits.parking = [p for p in edits.parking if p.edge_id != eid]
            edits.parking.append(
                ParkingConfig(
                    edge_id=eid,
                    side=side,  # type: ignore
                    corner_clearance_m=float(clearance),
                    length_m=float(plen),
                    capacity=int(cap),
                )
            )
            st.session_state.edits = edits
            st.rerun()

    with st.expander("Resumen de ediciones", expanded=False):
        st.write("Semáforos confirmados", [t.__dict__ for t in edits.tls_placements])
        st.write("Parqueos", [p.__dict__ for p in edits.parking])
        st.write("Altos", [s.__dict__ for s in edits.stops])
        st.write("Carriles", [x.__dict__ for x in edits.lane_overrides])
        st.write("TLS tiempos", {k: v.__dict__ for k, v in edits.tls_overrides.items()})

    st.session_state.edits = edits

    tls_ids = [t["id"] for t in (st.session_state.tls_list or [])]
    if st.session_state.net_path and st.button("Generar archivos SUMO adicionales"):
        run_dir = ROOT / "data" / "runs" / "current"
        run_dir.mkdir(parents=True, exist_ok=True)
        paths = write_all_additionals(
            run_dir, edits, tls_ids, net_path=Path(st.session_state.net_path)
        )
        st.session_state.run_dir = run_dir
        st.success(
            "Generados: " + ", ".join(p.name for p in paths) if paths else "Sin archivos (nada configurado)"
        )

    st.divider()
    st.subheader("4) Guardar / cargar configuración de esta zona")
    st.caption(
        "La configuración (semáforos, altos, parqueos, carriles y tiempos) queda asociada al "
        "**polígono de estudio**. Puede recuperarla en corridas posteriores desde aquí o la barra lateral."
    )

    default_name = f"{area.city}_{area.country}_config".replace(" ", "_")
    scen_name = st.text_input(
        "Nombre de la configuración",
        value=st.session_state.get("scenario_name") or default_name,
        key="cfg_scenario_name",
    )
    overwrite = st.checkbox(
        "Sobrescribir si ya existe un escenario con el mismo nombre",
        value=True,
        key="cfg_scen_overwrite",
    )

    n_tls = len(edits.tls_placements)
    n_park = len(edits.parking)
    n_stop = len(edits.stops)
    st.write(
        f"Se guardará: **{n_tls}** semáforos · **{n_park}** parqueos · **{n_stop}** altos · "
        f"polígono **{area.label}** ({area.area_km2:.2f} km²)"
    )

    g1, g2 = st.columns(2)
    with g1:
        if st.button("💾 Guardar configuración de zona", type="primary", use_container_width=True):
            if not scen_name.strip():
                st.error("Indique un nombre.")
            else:
                folder = save_scenario(
                    scen_name.strip(),
                    area,
                    edits,
                    net_path=st.session_state.net_path,
                    edge_levels=st.session_state.edge_levels or {},
                    edges_gj=st.session_state.edges_gj,
                    tls_list=st.session_state.tls_list or [],
                    overwrite=overwrite,
                )
                st.session_state.scenario_name = scen_name.strip()
                st.session_state.scenario_folder = str(folder)
                st.success(f"Configuración guardada en `{folder.name}` (ligada al polígono).")
    with g2:
        if st.button("Continuar a TomTom", use_container_width=True):
            go_to(STEPS[3])

    matches = scenarios_matching_area(area, min_iou=0.85)
    if matches:
        st.markdown("**Configuraciones ya guardadas para este (o casi el mismo) polígono:**")
        opts = {
            f"{m.get('name', folder.name)} · {folder.name} (IoU {iou:.0%})": folder
            for folder, m, iou in matches
        }
        pick = st.selectbox("Cargar en esta zona", ["—"] + list(opts.keys()), key="cfg_match_pick")
        if pick != "—" and st.button("Cargar configuración seleccionada", key="cfg_match_load"):
            data = load_scenario(opts[pick])
            apply_scenario_to_session(data, st.session_state)
            st.success(f"Cargada: {data['meta'].get('name')}")
            st.rerun()
    else:
        st.caption("No hay configuraciones previas que coincidan con este polígono.")


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
        st.metric("Edges calibrados", f"{len(levels)} ({pct:.0f}%)")
    else:
        st.caption("Aún sin calibración TomTom — el mapa muestra la red base.")

    zoom = st.slider("Zoom tiles", 13, 16, 15, disabled=not bool(key))
    col_a, col_b = st.columns(2)
    with col_a:
        fetch = st.button(
            "Obtener flujo TomTom y calibrar edges",
            type="primary",
            disabled=not bool(key),
            use_container_width=True,
        )
    with col_b:
        cont = st.button(
            "Continuar a simulación →",
            use_container_width=True,
            type="secondary",
        )

    if fetch and key:
        st.info("Consultando TomTom… el mapa de arriba se actualizará al terminar.")
        try:
            with st.spinner("Descargando vector flow tiles y emparejando edges…"):
                segs = fetch_traffic_for_bbox(area.bbox, api_key=key, zoom=zoom)
                new_levels = match_traffic_to_edges(st.session_state.edges_gj, segs)
                st.session_state.edge_levels = new_levels
                st.session_state.tomtom_segments = len(segs)
                snap = ROOT / "data" / "cache" / "tomtom_snapshot.json"
                save_snapshot(snap, new_levels)
                _bump_map()
            pct = 100.0 * len(new_levels) / max(1, len(st.session_state.edges_gj["features"]))
            st.success(
                f"{len(segs)} segmentos TomTom · {len(new_levels)} edges calibrados ({pct:.0f}%)"
            )
            st.rerun()
        except Exception as e:
            st.error(str(e))

    if cont:
        go_to(STEPS[4])


def step_sim() -> None:
    st.header("5. Simulación SUMO")
    area = st.session_state.area
    net = st.session_state.net_path
    if area is None or net is None:
        st.warning("Faltan zona o red.")
        return

    levels = st.session_state.edge_levels or {}
    render_study_map(
        title="Mapa de demanda / red",
        mode="tomtom" if levels else "plain",
        value_by_id=levels or None,
        show_tls=True,
        key_prefix="sim",
    )

    sumo = detect_sumo()
    if not sumo.ok:
        st.error(sumo.message)
        return

    duration = st.slider("Duración simulada (s)", 300, 3600, 600, 300)

    st.info(
        f"Modelo urbano: **máx. {CITY_MAX_SPEED_KMH:.0f} km/h**, vehículo **{VEH_LENGTH_M:.0f} m** + gap. "
        "En hora pico TomTom baja la velocidad permitida del tramo y la demanda se limita "
        "por capacidad física (~Greenshields), no solo por el escenario Bajo/Medio/Alto."
    )

    st.markdown("**Densidad de demanda (veh/h)**")
    st.caption(
        "Los edges SUMO son por sentido. TomTom escala dentro del tope del escenario y del "
        "espacio vial (largo del tramo / 5 m+gap)."
    )
    dens_key = st.radio(
        "Escenario",
        options=list(DENSITY_SCENARIOS.keys()),
        format_func=lambda k: DENSITY_SCENARIOS[k].caption,
        horizontal=True,
        index=1,  # Medio por defecto
        key="density_scenario",
    )
    dens = get_density_scenario(dens_key)
    st.dataframe(
        {
            "Escenario": ["Bajo", "Medio", "Alto"],
            "Ambos sentidos": ["300–400", "500–700", "800–1.000"],
            "Por sentido (tope)": ["150–200", "250–350", "400–500"],
        },
        hide_index=True,
        use_container_width=True,
    )
    base_rate = st.slider(
        f"Densidad base por sentido ({dens.label})",
        dens.per_dir_min,
        dens.per_dir_max,
        dens.per_dir_default,
        10,
        key=f"density_base_{dens_key}",
        help=(
            f"Tope del escenario: {dens.per_dir_max} veh/h por sentido "
            f"(≈ {dens.both_max} ambos sentidos)."
        ),
    )

    edges_gj = st.session_state.edges_gj or {}
    edge_ids = [f["properties"]["id"] for f in edges_gj.get("features", [])]
    seeds = edge_ids[:: max(1, len(edge_ids) // 80)][:80] if edge_ids else []

    if st.button("Generar demanda y simular", type="primary"):
        # Use ASCII-safe run dir (OneDrive accents break SUMO/TraCI on Windows)
        SAFE_RUNS.mkdir(parents=True, exist_ok=True)
        run_dir = SAFE_RUNS / "current"
        run_dir.mkdir(parents=True, exist_ok=True)
        st.session_state.run_dir = run_dir
        st.session_state.density_scenario_used = dens.key
        st.session_state.density_base_rate = int(base_rate)
        _close_traci()
        status = st.empty()
        status.info("Preparando demanda…")
        try:
            # Enforce 40 km/h on existing nets built before this change
            cap_network_speeds(Path(net), max_speed_ms=CITY_MAX_SPEED_MS)
            with st.spinner("Generando demanda calibrada…"):
                routes = generate_demand(
                    Path(net),
                    run_dir,
                    seeds,
                    edge_levels=st.session_state.edge_levels,
                    base_vehs_per_hour=float(base_rate),
                    duration_s=int(duration),
                    max_vehs_per_hour=float(dens.per_dir_max),
                    edges_gj=edges_gj,
                    sumo=sumo,
                )

            tls_ids = [t["id"] for t in (st.session_state.tls_list or [])]
            adds = write_all_additionals(
                run_dir, st.session_state.edits, tls_ids, net_path=Path(net)
            )
            add_files = [p for p in adds if p.suffix == ".xml" and "patch" not in p.name]

            cfg = write_sumocfg(
                run_dir / "optitraffic.sumocfg",
                Path(net),
                routes,
                additional_files=add_files or None,
                begin=0,
                end=int(duration),
            )
            status.info(f"Ejecutando SUMO ({duration}s simulados)…")
            with st.spinner("Ejecutando SUMO (TraCI)…"):
                result = run_simulation(
                    cfg,
                    sumo=sumo,
                    edge_levels=st.session_state.edge_levels or None,
                )
            st.session_state.sim_result = result
            export_edge_csv(result, run_dir / "edges.csv")
            export_edge_geojson(edges_gj, result, run_dir / "edges_result.geojson")
            (run_dir / "kpis.json").write_text(
                json.dumps(
                    {
                        "mean_speed": result.mean_speed,
                        "pct_edges_congested": result.pct_edges_congested,
                        "total_waiting": result.total_waiting,
                        "tomtom_correlation": result.tomtom_correlation,
                        "vehicle_steps": result.vehicle_steps,
                        "density_scenario": dens.key,
                        "density_base_veh_h": int(base_rate),
                        "density_cap_veh_h": dens.per_dir_max,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            _bump_map()
            status.success(
                f"Simulación OK · veh-steps={result.vehicle_steps} · "
                f"v_media={result.mean_speed:.2f} m/s"
            )
            go_to(STEPS[5])
        except Exception as e:
            _close_traci()
            status.error(f"Simulación: {e}")
            st.exception(e)

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


def main() -> None:
    init_state()
    sidebar()
    step = st.session_state.step
    if step == STEPS[0]:
        step_zona()
    elif step == STEPS[1]:
        step_red()
    elif step == STEPS[2]:
        step_config()
    elif step == STEPS[3]:
        step_tomtom()
    elif step == STEPS[4]:
        step_sim()
    else:
        step_results()


if __name__ == "__main__":
    main()
