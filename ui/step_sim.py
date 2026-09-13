"""OptiTraffic wizard step module."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.demand import (  # noqa: E402
    DENSITY_SCENARIOS,
    generate_demand,
    get_density_scenario,
    sort_demand_xml,
)
from src.flow_gates import (  # noqa: E402
    FlowGate,
    edge_midpoint,
    gates_from_list,
    gates_to_list,
    opposite_edge_id,
    remove_gate,
    remove_gates,
    suggest_boundary_gates,
    upsert_gate,
)
from src.editors import (  # noqa: E402
    prepare_sim_network,
    write_all_additionals,
)
from src.network_build import (  # noqa: E402
    cap_network_speeds,
)
from src.simulate import (  # noqa: E402
    SAFE_RUNS,
    _close_traci,
    export_edge_csv,
    export_edge_geojson,
    find_ffmpeg,
    run_simulation,
    write_sumocfg,
)
from src.sumo_env import detect_sumo  # noqa: E402
from src.traffic_params import CITY_MAX_SPEED_KMH, CITY_MAX_SPEED_MS, VEH_LENGTH_M  # noqa: E402
from src.wizard_state import STEPS  # noqa: E402
from ui.common import (  # noqa: E402
    _bump_map,
    go_to,
)
from src.viz import (  # noqa: E402
    add_edges_layer,
    add_flow_gate_markers,
    add_polygon,
    add_tls_markers,
    base_map,
    click_latlon,
    map_key,
    nearest_edge_id,
    simplify_edges_for_map,
)


def step_sim() -> None:
    st.header("5. Simulación SUMO")
    area = st.session_state.area
    net = st.session_state.net_path
    if area is None or net is None:
        st.warning("Faltan zona o red.")
        return

    edges_gj = st.session_state.edges_gj or {}
    if not edges_gj.get("features"):
        st.warning("No hay edges. Genere la red en el paso 2.")
        return

    levels = st.session_state.edge_levels or {}
    gates = gates_from_list(st.session_state.get("flow_gates") or [])
    sel_gate = st.session_state.get("sim_gate_edge_id")

    # Color entry/exit edges; selected = yellow
    color_by_id: dict[str, str] = {}
    weight_by_id: dict[str, int] = {}
    for g in gates:
        color_by_id[g.edge_id] = "#1e8449" if g.kind == "entry" else "#d35400"
        weight_by_id[g.edge_id] = 6
    if sel_gate:
        color_by_id[str(sel_gate)] = "#f1c40f"
        weight_by_id[str(sel_gate)] = 8

    st.markdown("**Entradas / salidas de flujo**")
    st.caption(
        "Verde = entrada · Naranja = salida · **Amarillo = seleccionada** en la lista. "
        "En doble sentido se sugieren ambos sentidos (entra / sale). "
        "Puede editar o eliminar puertas inválidas."
    )

    m = base_map(area.center, zoom=int(st.session_state.get("map_zoom", 14)))
    add_polygon(m, area.polygon, fit=True)
    # Zoom to selected gate if any
    if sel_gate:
        mid = edge_midpoint(edges_gj, str(sel_gate))
        if mid:
            m.location = [mid[0], mid[1]]
            m.options["zoom"] = 17
    edges_map = simplify_edges_for_map(edges_gj, max_points=6)
    add_edges_layer(
        m,
        edges_map,
        value_by_id=levels or None,
        mode="tomtom" if levels else "plain",
        color_by_id=color_by_id or None,
        weight_by_id=weight_by_id or None,
    )
    add_flow_gate_markers(m, gates, selected_edge_id=sel_gate)
    add_tls_markers(m, st.session_state.tls_list or [])

    from streamlit_folium import st_folium

    map_out = st_folium(
        m,
        width=None,
        height=480,
        returned_objects=["last_object_clicked", "last_clicked"],
        key=map_key(
            "sim_gates",
            area.center,
            len(gates),
            sel_gate,
            st.session_state.get("map_nonce", 0),
        ),
    )
    clicked = click_latlon(map_out)
    if clicked:
        clat, clon = clicked
        hit = nearest_edge_id(edges_gj, clat, clon, max_dist_deg=0.0015)
        if hit and hit.get("id"):
            eid = str(hit["id"])
            if st.session_state.get("sim_gate_edge_id") != eid:
                st.session_state.sim_gate_edge_id = eid
                st.rerun()

    dens_key = st.radio(
        "Tope de densidad (referencia)",
        options=list(DENSITY_SCENARIOS.keys()),
        format_func=lambda k: DENSITY_SCENARIOS[k].caption,
        horizontal=True,
        index=1,
        key="density_scenario",
    )
    dens = get_density_scenario(dens_key)
    default_vph = float(dens.per_dir_default)
    # Gate editor max must cover stored values when density scenario changes
    # (e.g. suggested at Alto 450 then user switches to Medio 350).
    gate_vph_max = max(int(DENSITY_SCENARIOS["alto"].per_dir_max), 1000)

    eid = st.session_state.get("sim_gate_edge_id")
    if eid:
        ename = ""
        sentido = ""
        for feat in edges_gj.get("features", []):
            props = feat.get("properties") or {}
            if str(props.get("id")) == str(eid):
                ename = str(props.get("name") or "")
                sentido = str(props.get("sentido") or ("unico" if props.get("oneway") else "doble"))
                break
        existing = next((g for g in gates if g.edge_id == str(eid)), None)
        st.info(
            f"{'🟡 Seleccionada' if existing else 'Tramo'}: `{eid}` · "
            f"{ename or '(sin nombre)'} · sentido **{sentido or '?'}**"
        )
        init_vph = int(existing.vehs_per_hour) if existing else int(default_vph)
        vph = st.number_input(
            "Flujo (veh/h) para este punto",
            min_value=50,
            max_value=gate_vph_max,
            value=min(max(50, init_vph), gate_vph_max),
            step=25,
            key=f"gate_vph_{eid}",
        )
        b1, b2, b3, b4 = st.columns(4)
        mid = edge_midpoint(edges_gj, str(eid))
        with b1:
            if st.button("Marcar ENTRADA", type="primary", width="stretch"):
                gate = FlowGate(
                    edge_id=str(eid),
                    kind="entry",
                    vehs_per_hour=float(vph),
                    name=ename or str(eid),
                    lat=mid[0] if mid else None,
                    lon=mid[1] if mid else None,
                    sentido=sentido,
                    pair_edge_id=opposite_edge_id(str(eid)) if sentido == "doble" else "",
                )
                gates = upsert_gate(gates, gate)
                st.session_state.flow_gates = gates_to_list(gates)
                _bump_map()
                st.rerun()
        with b2:
            if st.button("Marcar SALIDA", width="stretch"):
                gate = FlowGate(
                    edge_id=str(eid),
                    kind="exit",
                    vehs_per_hour=float(vph),
                    name=ename or str(eid),
                    lat=mid[0] if mid else None,
                    lon=mid[1] if mid else None,
                    sentido=sentido,
                    pair_edge_id=opposite_edge_id(str(eid)) if sentido == "doble" else "",
                )
                gates = upsert_gate(gates, gate)
                st.session_state.flow_gates = gates_to_list(gates)
                _bump_map()
                st.rerun()
        with b3:
            if st.button("Quitar puerta", width="stretch"):
                gates = remove_gate(gates, str(eid))
                st.session_state.flow_gates = gates_to_list(gates)
                st.session_state.sim_gate_edge_id = None
                _bump_map()
                st.rerun()
        with b4:
            if st.button("Limpiar selección", width="stretch"):
                st.session_state.sim_gate_edge_id = None
                st.rerun()
        if sentido == "doble":
            opp = opposite_edge_id(str(eid))
            if st.button(
                f"También marcar sentido contrario (`{opp}`) como "
                f"{'SALIDA' if (existing and existing.kind == 'entry') or not existing else 'ENTRADA'}",
                key=f"pair_{eid}",
            ):
                other_kind = "exit"
                if existing and existing.kind == "exit":
                    other_kind = "entry"
                elif existing and existing.kind == "entry":
                    other_kind = "exit"
                else:
                    other_kind = "exit"
                omid = edge_midpoint(edges_gj, opp)
                gates = upsert_gate(
                    gates,
                    FlowGate(
                        edge_id=opp,
                        kind=other_kind,  # type: ignore[arg-type]
                        vehs_per_hour=float(vph),
                        name=f"{ename or opp} (par)",
                        lat=(omid[0] + 0.00012) if omid else None,
                        lon=(omid[1] + 0.00012) if omid else None,
                        sentido="doble",
                        pair_edge_id=str(eid),
                    ),
                )
                st.session_state.flow_gates = gates_to_list(gates)
                _bump_map()
                st.rerun()
    else:
        st.caption("Seleccione un edge en el mapa o una fila de la lista (Ver en mapa).")

    s1, s2 = st.columns(2)
    with s1:
        if st.button("Sugerir puertas en el borde", width="stretch"):
            suggested = suggest_boundary_gates(
                edges_gj,
                area.polygon,
                max_corridors=8,
                default_vph=default_vph,
            )
            if not suggested:
                st.warning("No se encontraron edges cerca del borde del polígono.")
            else:
                for g in suggested:
                    gates = upsert_gate(gates, g)
                st.session_state.flow_gates = gates_to_list(gates)
                # Select first entry so user sees yellow highlight
                first = next((g for g in suggested if g.kind == "entry"), suggested[0])
                st.session_state.sim_gate_edge_id = first.edge_id
                _bump_map()
                n_in = sum(1 for g in suggested if g.kind == "entry")
                n_out = sum(1 for g in suggested if g.kind == "exit")
                st.success(
                    f"Sugeridas {len(suggested)} puertas ({n_in} entradas · {n_out} salidas). "
                    "Revise la lista, edite o elimine las no válidas."
                )
                st.rerun()
    with s2:
        if gates and st.button("Borrar todas las puertas", width="stretch"):
            st.session_state.flow_gates = []
            st.session_state.sim_gate_edge_id = None
            _bump_map()
            st.rerun()

    if gates:
        n_in = sum(1 for g in gates if g.kind == "entry")
        n_out = sum(1 for g in gates if g.kind == "exit")
        st.write(
            f"**{len(gates)}** puertas · **{n_in}** entradas · **{n_out}** salidas · "
            f"Σ entrada ≈ **{sum(g.vehs_per_hour for g in gates if g.kind == 'entry'):.0f}** veh/h · "
            f"Σ salida ≈ **{sum(g.vehs_per_hour for g in gates if g.kind == 'exit'):.0f}** veh/h"
        )

        st.markdown("**Lista editable** — pulse *Ver* para marcar en amarillo en el mapa")
        # Bulk delete
        del_opts = {
            f"{'ENTRADA' if g.kind == 'entry' else 'SALIDA'} · {g.name or g.edge_id} · {g.edge_id}": g.edge_id
            for g in gates
        }
        to_del = st.multiselect(
            "Eliminar puertas no válidas",
            options=list(del_opts.keys()),
            key="gate_delete_multi",
            help="Seleccione una o varias y pulse Eliminar seleccionadas.",
        )
        if to_del and st.button("Eliminar seleccionadas", type="secondary"):
            ids = [del_opts[k] for k in to_del]
            gates = remove_gates(gates, ids)
            st.session_state.flow_gates = gates_to_list(gates)
            if st.session_state.get("sim_gate_edge_id") in ids:
                st.session_state.sim_gate_edge_id = None
            _bump_map()
            st.rerun()

        for g in sorted(gates, key=lambda x: (0 if x.kind == "entry" else 1, x.name or x.edge_id)):
            is_sel = str(g.edge_id) == str(sel_gate)
            prefix = "🟡 " if is_sel else ""
            with st.expander(
                f"{prefix}{'ENTRADA' if g.kind == 'entry' else 'SALIDA'} · "
                f"{g.name or g.edge_id} · {int(g.vehs_per_hour)} veh/h"
                f"{' · doble' if g.sentido == 'doble' else ''}",
                expanded=is_sel,
            ):
                c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
                with c1:
                    new_kind = st.selectbox(
                        "Tipo",
                        ["entry", "exit"],
                        index=0 if g.kind == "entry" else 1,
                        format_func=lambda k: "Entrada" if k == "entry" else "Salida",
                        key=f"edit_kind_{g.edge_id}",
                    )
                with c2:
                    edit_max = max(gate_vph_max, int(g.vehs_per_hour))
                    edit_val = min(max(50, int(g.vehs_per_hour)), edit_max)
                    # Sync widget state if scenario change left a stale out-of-range value
                    wkey = f"edit_vph_{g.edge_id}"
                    if wkey in st.session_state:
                        try:
                            cur = int(st.session_state[wkey])
                            if cur > edit_max or cur < 50:
                                st.session_state[wkey] = edit_val
                        except (TypeError, ValueError):
                            st.session_state[wkey] = edit_val
                    new_vph = st.number_input(
                        "veh/h",
                        min_value=50,
                        max_value=edit_max,
                        value=edit_val,
                        step=25,
                        key=wkey,
                    )
                with c3:
                    if st.button("Ver en mapa", key=f"see_{g.edge_id}", width="stretch"):
                        st.session_state.sim_gate_edge_id = g.edge_id
                        _bump_map()
                        st.rerun()
                with c4:
                    if st.button("Eliminar", key=f"rm_{g.edge_id}", width="stretch"):
                        gates = remove_gate(gates, g.edge_id)
                        st.session_state.flow_gates = gates_to_list(gates)
                        if st.session_state.get("sim_gate_edge_id") == g.edge_id:
                            st.session_state.sim_gate_edge_id = None
                        _bump_map()
                        st.rerun()
                st.caption(f"Edge `{g.edge_id}` · sentido {g.sentido or 'n/d'}")
                if st.button("Guardar cambios", key=f"save_{g.edge_id}"):
                    updated = FlowGate(
                        edge_id=g.edge_id,
                        kind=new_kind,  # type: ignore[arg-type]
                        vehs_per_hour=float(new_vph),
                        name=g.name,
                        lat=g.lat,
                        lon=g.lon,
                        sentido=g.sentido,
                        pair_edge_id=g.pair_edge_id,
                    )
                    gates = upsert_gate(gates, updated)
                    st.session_state.flow_gates = gates_to_list(gates)
                    st.session_state.sim_gate_edge_id = g.edge_id
                    _bump_map()
                    st.rerun()
    else:
        st.info(
            "Sin puertas: se usará demanda repartida (modo anterior). "
            "Para un flujo más real, use *Sugerir puertas* o márquelas en el mapa."
        )

    sumo = detect_sumo()
    if not sumo.ok:
        st.error(sumo.message)
        return

    st.markdown("**Tiempo de simulación**")
    st.caption(
        "Use ≥ 1 h para que la red se llene y se vean represamientos. "
        "El warmup no cuenta en los KPIs (solo estabiliza el flujo)."
    )
    duration = st.slider(
        "Duración total (s)",
        1800,
        10800,
        3600,
        300,
        key="sim_duration",
        help="1800=30 min · 3600=1 h · 7200=2 h · 10800=3 h",
    )
    warmup = st.slider(
        "Warmup / estabilización (s)",
        300,
        min(3600, max(300, int(duration * 0.5))),
        min(900, max(300, int(duration * 0.25))),
        60,
        key="sim_warmup",
        help="KPIs se miden solo después de este tiempo.",
    )

    st.info(
        f"Modelo urbano: **máx. {CITY_MAX_SPEED_KMH:.0f} km/h**, vehículo **{VEH_LENGTH_M:.0f} m** + gap. "
        f"Medición de KPIs: {int(duration - warmup)} s útiles tras {int(warmup)} s de llenado."
    )

    st.markdown("**Grabación de video**")
    gui_ok = bool(getattr(sumo, "sumo_gui_bin", None))
    ff_ok = find_ffmpeg() is not None
    if not gui_ok:
        st.caption("sumo-gui no detectado: la grabación no está disponible.")
    elif not ff_ok:
        st.caption(
            "ffmpeg no está en PATH: se guardarán PNG; el MP4 se puede generar después."
        )
    record_video = st.checkbox(
        "Grabar video (sumo-gui)",
        value=False,
        key="record_video",
        disabled=not gui_ok,
        help=(
            "Abre sumo-gui, captura pantallas cada N segundos de simulación y "
            "arma un MP4 con ffmpeg. Más lento que sumo headless. "
            "No minimice la ventana de SUMO mientras graba."
        ),
    )
    record_every = 10.0
    video_fps = 5.0
    if record_video and gui_ok:
        st.warning(
            "La grabación espera a que haya **vehículos**, enfoca el **punto con más tráfico** "
            "y alterna acercamiento/alejamiento cada **30 s** de simulación. "
            "Deje sumo-gui visible; el MP4 se arma al final. "
            "Con grabación la simulación es más lenta (~100 ms/paso)."
        )
        rc1, rc2 = st.columns(2)
        with rc1:
            record_every = float(
                st.slider(
                    "Intervalo de captura (s sim)",
                    5,
                    60,
                    10,
                    5,
                    key="record_every_s",
                    help="5–10 s suele bastar para un video corto y legible.",
                )
            )
        with rc2:
            video_fps = float(
                st.slider(
                    "FPS del MP4",
                    2,
                    15,
                    5,
                    1,
                    key="video_fps",
                    help="Cuadros por segundo al ensamblar el video.",
                )
            )
        n_est = max(1, int(duration / record_every))
        st.caption(
            f"≈ {n_est} capturas · ventana GUI 1280×720 · "
            f"{'MP4 al final' if ff_ok else 'solo PNG (sin ffmpeg)'}"
        )

    base_rate = st.slider(
        f"Densidad base (solo si no hay puertas) — {dens.label}",
        dens.per_dir_min,
        dens.per_dir_max,
        dens.per_dir_default,
        10,
        key=f"density_base_{dens_key}",
        disabled=bool(gates),
    )
    preload_vph = st.number_input(
        "Precarga de red (veh/h internos)",
        min_value=0,
        max_value=2000,
        value=100,
        step=25,
        key="preload_vph",
        disabled=not bool(gates),
        help=(
            "Con puertas: viajes internos fijos (OD aleatorio) además de las entradas. "
            "~70% sale al inicio para llenar la red rápido; el resto se reparte en el resto "
            "de la simulación. 0 = solo puertas. Sin puertas no aplica (use densidad base)."
        ),
    )
    if gates:
        st.caption(
            f"Precarga ≈ **{int(preload_vph)}** veh/h internos + Σ entradas "
            f"**{sum(g.vehs_per_hour for g in gates if g.kind == 'entry'):.0f}** veh/h. "
            f"Ventana de llenado temprano ≈ **{min(int(duration), max(300, int(duration * 0.25)))}** s "
            f"(alineada al warmup cuando sea posible)."
        )
    else:
        st.caption("Active puertas de entrada/salida para usar la precarga fija de red.")

    edge_ids = [f["properties"]["id"] for f in edges_gj.get("features", []) if f.get("properties", {}).get("id")]
    seeds = edge_ids[:: max(1, len(edge_ids) // 80)][:80] if edge_ids else []

    if st.button("Generar demanda y simular", type="primary"):
        SAFE_RUNS.mkdir(parents=True, exist_ok=True)
        run_dir = SAFE_RUNS / "current"
        run_dir.mkdir(parents=True, exist_ok=True)
        st.session_state.run_dir = run_dir
        st.session_state.density_scenario_used = dens.key
        st.session_state.density_base_rate = int(base_rate)
        st.session_state.preload_vph_used = int(preload_vph) if gates else 0
        _close_traci()
        status = st.empty()
        status.info("Preparando demanda…")
        try:
            # Bake confirmed TLS + altos into a sim net (priority_stop / --tls.set)
            status.info("Preparando red (semáforos y altos)…")
            osm_tls = [t["id"] for t in (st.session_state.tls_list or []) if t.get("id")]
            sim_net = run_dir / "sim.net.xml"
            with st.spinner("Aplicando semáforos y altos a la red…"):
                sim_net, tls_ids = prepare_sim_network(
                    Path(net),
                    sim_net,
                    st.session_state.edits,
                    osm_tls_ids=osm_tls,
                    edges_gj=edges_gj,
                    netconvert_bin=sumo.netconvert_bin,
                )
            cap_network_speeds(sim_net, max_speed_ms=CITY_MAX_SPEED_MS)
            preload_fill = float(min(int(duration), max(300, int(warmup)))) if gates else None
            with st.spinner(
                "Generando demanda OD (puertas + precarga)…"
                if gates
                else "Generando demanda calibrada…"
            ):
                routes = generate_demand(
                    sim_net,
                    run_dir,
                    edge_ids if gates else seeds,
                    edge_levels=st.session_state.edge_levels,
                    base_vehs_per_hour=float(base_rate),
                    duration_s=int(duration),
                    max_vehs_per_hour=float(dens.per_dir_max),
                    edges_gj=edges_gj,
                    sumo=sumo,
                    flow_gates=gates or None,
                    preload_vph=float(preload_vph) if gates else 0.0,
                    preload_fill_s=preload_fill,
                )
            # Belt-and-suspenders: never hand SUMO an unsorted route file
            sort_demand_xml(run_dir / "trips.xml")
            sort_demand_xml(Path(routes))


            adds = write_all_additionals(
                run_dir, st.session_state.edits, tls_ids, net_path=sim_net
            )
            add_files = [p for p in adds if p.suffix == ".xml" and "patch" not in p.name]

            cfg = write_sumocfg(
                run_dir / "optitraffic.sumocfg",
                sim_net,
                routes,
                additional_files=add_files or None,
                begin=0,
                end=int(duration),
            )
            status.info(
                f"Ejecutando SUMO ({duration}s · warmup {warmup}s"
                + (" · grabando video" if record_video else "")
                + ")… puede tardar varios minutos."
            )

            def _on_progress(t: float, end_t: float, frames: int) -> None:
                status.info(
                    f"SUMO {t:.0f}/{end_t:.0f}s"
                    + (f" · frames={frames}" if record_video else "")
                    + "…"
                )

            with st.spinner(
                "Ejecutando SUMO-GUI + capturas… (mire el reloj en sumo-gui)"
                if record_video
                else "Ejecutando SUMO (TraCI)…"
            ):
                result = run_simulation(
                    cfg,
                    sumo=sumo,
                    edge_levels=st.session_state.edge_levels or None,
                    warmup_s=float(warmup),
                    record_video=bool(record_video and gui_ok),
                    record_every_s=float(record_every),
                    frames_dir=run_dir / "frames",
                    video_path=run_dir / "simulation.mp4",
                    video_fps=float(video_fps),
                    progress_cb=_on_progress,
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
                        "preload_vph": int(preload_vph) if gates else 0,
                        "duration_s": int(duration),
                        "warmup_s": int(warmup),
                        "flow_gates": gates_to_list(gates),
                        "demand_mode": "gates" if gates else "seed",
                        "record_video": bool(record_video and gui_ok),
                        "video_path": result.video_path,
                        "frames_count": result.frames_count,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            _bump_map()
            vid_msg = ""
            if result.video_path:
                vid_msg = f" · video={Path(result.video_path).name}"
            elif result.frames_count:
                vid_msg = f" · {result.frames_count} frames PNG"
            status.success(
                f"Simulación OK · veh-steps={result.vehicle_steps} · "
                f"v_media={result.mean_speed:.2f} m/s · modo="
                f"{'puertas' if gates else 'repartido'}{vid_msg}"
            )
            go_to(STEPS[5])
        except Exception as e:
            _close_traci()
            status.error(f"Simulación: {e}")
            st.exception(e)


