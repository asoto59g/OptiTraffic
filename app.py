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
    validate_area,
)
from src.demand import generate_demand  # noqa: E402
from src.editors import (  # noqa: E402
    LaneOverride,
    NetworkEdits,
    ParkingConfig,
    StopSign,
    TlsTiming,
    write_all_additionals,
)
from src.network_build import build_network, edges_geojson, list_traffic_lights, save_edges_geojson  # noqa: E402
from src.osm_fetch import prepare_clipped_osm  # noqa: E402
from src.scenarios import list_scenarios, load_scenario, save_scenario  # noqa: E402
from src.simulate import (  # noqa: E402
    export_edge_csv,
    export_edge_geojson,
    run_simulation,
    write_sumocfg,
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
from src.viz import (  # noqa: E402
    add_draw_control,
    add_edges_layer,
    add_polygon,
    add_tls_markers,
    base_map,
    extract_drawn_geojson,
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


def init_state() -> None:
    defaults = {
        "step": STEPS[0],
        "area": None,
        "net_path": None,
        "edges_gj": None,
        "tls_list": [],
        "edits": NetworkEdits(),
        "edge_levels": {},
        "sim_result": None,
        "run_dir": None,
        "center": (9.93, -84.08),
        "city": "San José",
        "country": "Costa Rica",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


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

    st.session_state.step = st.sidebar.radio("Pasos", STEPS, index=STEPS.index(st.session_state.step))

    st.sidebar.divider()
    st.sidebar.subheader("Escenarios")
    scenarios = list_scenarios()
    if scenarios:
        labels = [p.name for p in scenarios]
        pick = st.sidebar.selectbox("Cargar", ["—"] + labels)
        if pick != "—" and st.sidebar.button("Abrir escenario"):
            data = load_scenario(next(p for p in scenarios if p.name == pick))
            st.session_state.area = data["area"]
            st.session_state.edits = data["edits"]
            st.session_state.edge_levels = data["edge_levels"]
            st.session_state.city = data["area"].city
            st.session_state.country = data["area"].country
            st.session_state.center = data["area"].center
            net = data["meta"].get("net_path")
            if net and Path(net).exists():
                st.session_state.net_path = Path(net)
                try:
                    st.session_state.edges_gj = edges_geojson(Path(net))
                except Exception:
                    pass
            st.sidebar.success("Escenario cargado")
            st.rerun()


def step_zona() -> None:
    st.header("1. Zona de estudio")
    st.write(
        "Indique ciudad y país, luego defina un rectángulo, dibuje un polígono "
        f"o suba un GeoJSON. Límite MVP: **{MAX_AREA_KM2} km²**."
    )

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        city = st.text_input("Ciudad", value=st.session_state.city)
    with c2:
        country = st.text_input("País", value=st.session_state.country)
    with c3:
        if st.button("Geocodificar", use_container_width=True):
            try:
                lat, lon, addr = geocode_city(city, country)
                st.session_state.center = (lat, lon)
                st.session_state.city = city
                st.session_state.country = country
                st.success(addr)
            except Exception as e:
                st.error(str(e))

    mode = st.radio(
        "Método de delimitación",
        ["Rectángulo alrededor del centro", "Bounds manuales", "Dibujo en mapa", "Subir GeoJSON"],
        horizontal=True,
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
        m = base_map(st.session_state.center)
        add_draw_control(m)
        try:
            from streamlit_folium import st_folium

            out = st_folium(m, width=None, height=480, key="draw_map")
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
        if up:
            try:
                polygon = load_geojson_polygon(up.read())
            except Exception as e:
                st.error(str(e))

    if polygon is not None:
        ok, msg = validate_area(polygon)
        if ok:
            st.success(msg)
        else:
            st.error(msg)

        m2 = base_map(st.session_state.center)
        add_polygon(m2, polygon)
        try:
            from streamlit_folium import st_folium

            st_folium(m2, width=None, height=360, key="preview_area")
        except ImportError:
            st.write(polygon.bounds)

        if ok and st.button("Confirmar zona", type="primary"):
            try:
                area = build_study_area(city, country, polygon)
                st.session_state.area = area
                st.session_state.city = city
                st.session_state.country = country
                st.session_state.center = area.center
                st.session_state.step = STEPS[1]
                st.success(f"Zona lista: {area.label} ({area.area_km2:.2f} km²)")
                st.rerun()
            except Exception as e:
                st.error(str(e))


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
    force = st.checkbox("Forzar re-descarga Geofabrik", value=False)

    sumo = detect_sumo()
    if st.button("Descargar OSM, recortar y generar red SUMO", type="primary"):
        if not sumo.ok:
            st.error(sumo.message)
            return
        with st.spinner("Descargando extracto Geofabrik (puede tardar)…"):
            try:
                extract, clipped = prepare_clipped_osm(area, force_download=force)
                st.write(f"Extracto: `{extract.name}`")
                st.write(f"Recorte: `{clipped}`")
            except Exception as e:
                st.error(f"OSM: {e}")
                return
        with st.spinner("Ejecutando netconvert…"):
            try:
                net = build_network(clipped, area, sumo=sumo)
                st.session_state.net_path = net
                gj = edges_geojson(net, sumo=sumo)
                st.session_state.edges_gj = gj
                save_edges_geojson(net)
                try:
                    st.session_state.tls_list = list_traffic_lights(net, sumo=sumo)
                except Exception:
                    st.session_state.tls_list = []
                st.success(f"Red generada: {net} ({len(gj.get('features', []))} edges)")
            except Exception as e:
                st.error(str(e))
                return

    if st.session_state.edges_gj:
        m = base_map(area.center)
        add_edges_layer(m, st.session_state.edges_gj, name="Edges SUMO")
        add_polygon(m, area.polygon)
        add_tls_markers(m, st.session_state.tls_list or [])
        try:
            from streamlit_folium import st_folium

            st_folium(m, width=None, height=480, key="net_map")
        except ImportError:
            st.json({"n_edges": len(st.session_state.edges_gj["features"])})

        if st.button("Continuar a configuración"):
            st.session_state.step = STEPS[2]
            st.rerun()


def step_config() -> None:
    st.header("3. Configuración vial")
    edits: NetworkEdits = st.session_state.edits

    st.subheader("Semáforos (default)")
    g, y, r = st.columns(3)
    green = g.number_input("Verde (s)", 5, 180, edits.tls_default.green)
    yellow = y.number_input("Amarillo (s)", 1, 15, edits.tls_default.yellow)
    red = r.number_input("Rojo (s)", 5, 180, edits.tls_default.red)
    edits.tls_default = TlsTiming(green=int(green), yellow=int(yellow), red=int(red))

    tls_ids = [t["id"] for t in (st.session_state.tls_list or [])]
    st.caption(f"Semáforos detectados en red: {len(tls_ids)}")
    if tls_ids:
        sel = st.multiselect("Aplicar override a TLS", tls_ids)
        if sel:
            og, oy, or_ = st.columns(3)
            ogv = og.number_input("Override verde", 5, 180, green, key="og")
            oyv = oy.number_input("Override amarillo", 1, 15, yellow, key="oy")
            orv = or_.number_input("Override rojo", 5, 180, red, key="or")
            for tid in sel:
                edits.tls_overrides[tid] = TlsTiming(int(ogv), int(oyv), int(orv))

    st.subheader("Carriles / sentidos")
    st.caption("Por defecto: 1 carril (un vehículo de ancho). Marque dual para 2 carriles mismo sentido.")
    edge_ids = []
    if st.session_state.edges_gj:
        edge_ids = [f["properties"]["id"] for f in st.session_state.edges_gj["features"]]
    sel_edges = st.multiselect("Edges a ajustar", edge_ids[:500] if edge_ids else [])
    dual = st.checkbox("Dos vías en un solo sentido (2 carriles)", value=False)
    nlanes = st.number_input("Número de carriles", 1, 4, 1)
    if st.button("Aplicar carriles a selección") and sel_edges:
        for eid in sel_edges:
            edits.lane_overrides = [x for x in edits.lane_overrides if x.edge_id != eid]
            edits.lane_overrides.append(
                LaneOverride(edge_id=eid, num_lanes=int(nlanes), oneway_dual=dual)
            )
        st.success(f"Actualizados {len(sel_edges)} edges")

    st.subheader("Parqueo")
    park_edge = st.selectbox("Edge para parqueo", ["—"] + (edge_ids[:500] if edge_ids else []))
    side = st.selectbox("Lado", ["right", "left", "both"])
    clearance = st.number_input("Distancia legal esquina (m)", 3.0, 20.0, 5.0)
    plen = st.number_input("Longitud zona (m)", 10.0, 200.0, 40.0)
    cap = st.number_input("Capacidad", 1, 50, 8)
    if st.button("Agregar parqueo") and park_edge != "—":
        edits.parking.append(
            ParkingConfig(
                edge_id=park_edge,
                side=side,  # type: ignore
                corner_clearance_m=float(clearance),
                length_m=float(plen),
                capacity=int(cap),
            )
        )
        st.success("Parqueo agregado")
    if edits.parking:
        st.write([p.__dict__ for p in edits.parking])

    st.subheader("Señales de alto")
    stop_edge = st.selectbox("Edge con alto", ["—"] + (edge_ids[:500] if edge_ids else []), key="stop_sel")
    if st.button("Agregar alto") and stop_edge != "—":
        edits.stops.append(StopSign(edge_id=stop_edge))
        st.success("Alto agregado")
    if edits.stops:
        st.write([s.__dict__ for s in edits.stops])

    st.session_state.edits = edits

    if st.session_state.net_path and st.button("Generar archivos SUMO adicionales", type="primary"):
        run_dir = ROOT / "data" / "runs" / "current"
        run_dir.mkdir(parents=True, exist_ok=True)
        paths = write_all_additionals(run_dir, edits, tls_ids)
        st.session_state.run_dir = run_dir
        st.success("Generados: " + ", ".join(p.name for p in paths) if paths else "Sin archivos (nada configurado)")

    if st.button("Continuar a TomTom"):
        st.session_state.step = STEPS[3]
        st.rerun()


def step_tomtom() -> None:
    st.header("4. Tráfico real TomTom")
    area = st.session_state.area
    if area is None or not st.session_state.edges_gj:
        st.warning("Se requieren zona y red.")
        return

    key = load_api_key()
    if not key:
        st.error("Configure `TOMTOM_API_KEY` en `.env` (copie `.env.example`).")
        st.info("Sin TomTom puede continuar con densidad sintética en el siguiente paso.")
        if st.button("Continuar sin TomTom"):
            st.session_state.edge_levels = {}
            st.session_state.step = STEPS[4]
            st.rerun()
        return

    used, limit = usage_count()
    st.write(f"Uso del mes: **{used:,}** / {limit:,} tiles")

    zoom = st.slider("Zoom tiles", 13, 16, 15)
    if st.button("Obtener flujo TomTom y calibrar edges", type="primary"):
        with st.spinner("Descargando vector flow tiles…"):
            try:
                segs = fetch_traffic_for_bbox(area.bbox, api_key=key, zoom=zoom)
                levels = match_traffic_to_edges(st.session_state.edges_gj, segs)
                st.session_state.edge_levels = levels
                snap = ROOT / "data" / "cache" / "tomtom_snapshot.json"
                save_snapshot(snap, levels)
                pct = 100.0 * len(levels) / max(1, len(st.session_state.edges_gj["features"]))
                st.success(
                    f"{len(segs)} segmentos TomTom · {len(levels)} edges calibrados ({pct:.0f}%)"
                )
                m = base_map(area.center)
                add_edges_layer(
                    m,
                    st.session_state.edges_gj,
                    value_by_id=levels,
                    mode="tomtom",
                    name="Congestión TomTom",
                )
                try:
                    from streamlit_folium import st_folium

                    st_folium(m, width=None, height=480, key="tomtom_map")
                except ImportError:
                    st.write(segments_to_geojson(segs[:20]))
            except Exception as e:
                st.error(str(e))

    if st.session_state.edge_levels:
        st.metric("Edges calibrados", len(st.session_state.edge_levels))
        if st.button("Continuar a simulación"):
            st.session_state.step = STEPS[4]
            st.rerun()


def step_sim() -> None:
    st.header("5. Simulación SUMO")
    area = st.session_state.area
    net = st.session_state.net_path
    if area is None or net is None:
        st.warning("Faltan zona o red.")
        return

    sumo = detect_sumo()
    if not sumo.ok:
        st.error(sumo.message)
        return

    duration = st.slider("Duración simulada (s)", 300, 3600, 1800, 300)
    base_rate = st.slider("Densidad base (veh/h por edge semilla)", 20, 400, 120, 10)

    edges_gj = st.session_state.edges_gj or {}
    edge_ids = [f["properties"]["id"] for f in edges_gj.get("features", [])]
    # Limit seeds for performance
    seeds = edge_ids[:: max(1, len(edge_ids) // 80)][:80] if edge_ids else []

    if st.button("Generar demanda y simular", type="primary"):
        run_dir = ROOT / "data" / "runs" / "current"
        run_dir.mkdir(parents=True, exist_ok=True)
        st.session_state.run_dir = run_dir
        with st.spinner("Generando demanda calibrada…"):
            try:
                routes = generate_demand(
                    Path(net),
                    run_dir,
                    seeds,
                    edge_levels=st.session_state.edge_levels,
                    base_vehs_per_hour=float(base_rate),
                    duration_s=int(duration),
                    sumo=sumo,
                )
            except Exception as e:
                st.error(f"Demanda: {e}")
                return

        tls_ids = [t["id"] for t in (st.session_state.tls_list or [])]
        adds = write_all_additionals(run_dir, st.session_state.edits, tls_ids)
        add_files = [p for p in adds if p.suffix == ".xml" and "patch" not in p.name]

        cfg = write_sumocfg(
            run_dir / "optitraffic.sumocfg",
            Path(net),
            routes,
            additional_files=add_files or None,
            begin=0,
            end=int(duration),
        )
        with st.spinner("Ejecutando SUMO (TraCI)…"):
            try:
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
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                st.success("Simulación completada")
                st.session_state.step = STEPS[5]
                st.rerun()
            except Exception as e:
                st.error(f"Simulación: {e}")


def step_results() -> None:
    st.header("6. Resultados")
    result = st.session_state.sim_result
    area = st.session_state.area
    if result is None:
        st.warning("Aún no hay resultados de simulación.")
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Velocidad media (m/s)", f"{result.mean_speed:.2f}")
    k2.metric("% edges congestión", f"{result.pct_edges_congested:.1f}%")
    k3.metric("Espera acumulada (s)", f"{result.total_waiting:.0f}")
    corr = result.tomtom_correlation
    k4.metric("Corr. vs TomTom", f"{corr:.2f}" if corr is not None else "n/d")

    speeds = {eid: kpi.mean_speed for eid, kpi in result.edges.items()}
    if area and st.session_state.edges_gj:
        m = base_map(area.center)
        add_edges_layer(
            m,
            st.session_state.edges_gj,
            value_by_id=speeds,
            mode="sim",
            name="Congestión simulada",
        )
        try:
            from streamlit_folium import st_folium

            st_folium(m, width=None, height=500, key="res_map")
        except ImportError:
            pass

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
    if st.button("Guardar escenario", type="primary"):
        folder = save_scenario(
            name,
            area,
            st.session_state.edits,
            net_path=st.session_state.net_path,
            edge_levels=st.session_state.edge_levels,
            result_summary={
                "mean_speed": result.mean_speed,
                "pct_edges_congested": result.pct_edges_congested,
                "tomtom_correlation": result.tomtom_correlation,
            },
            extra_files=[csv_path, gj_path] if csv_path.exists() else None,
        )
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
