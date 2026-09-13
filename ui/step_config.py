"""OptiTraffic wizard step module."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.editors import (  # noqa: E402
    LaneOverride,
    NetworkEdits,
    ParkingConfig,
    StopSign,
    TlsPlacement,
    TlsTiming,
    default_stops_ns_at_avenues,
    default_street_rules_for_edges,
    merge_default_stops,
    merge_default_street_rules,
    write_all_additionals,
)
from src.network_build import (  # noqa: E402
    annotate_edge_directions,
)
from src.scenarios import (  # noqa: E402
    apply_scenario_to_session,
    load_scenario,
    save_scenario,
    scenarios_matching_area,
)
from src.wizard_state import STEPS  # noqa: E402
from ui.common import (  # noqa: E402
    go_to,
)
from src.viz import (  # noqa: E402
    add_edges_layer,
    add_junction_markers,
    add_tls_markers,
    base_map,
    build_junctions,
    click_latlon,
    edge_center_latlon,
    fit_edge,
    fit_junction,
    map_key,
    nearest_edge_id,
    nearest_junction,
    nearest_tls,
    simplify_edges_for_map,
)


def step_config() -> None:
    st.header("3. Configuración vial")
    st.info(
        "Para **semáforos**: clic en el **punto gris del cruce** (intersección). "
        "Para parqueo/alto/carriles: clic en la **línea** de la calle. "
        "Luego pulse **Confirmar** debajo del mapa.\n\n"
        "**Regla por defecto (CR centro):** avenidas E–O con vía libre; "
        "calles N–S con **alto** al cruzar avenidas (se aplica al generar la red)."
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
                width="stretch",
                disabled=not can_tls,
            ):
                linked = st.session_state.selected_tls_id
                edge_for_place = eid or ((st.session_state.selected_junction or {}).get("edges") or [""])[0]
                # SUMO TLS id == junction id after --tls.set; avoid tls_j_* aliases
                tid = linked or (str(jid) if jid else f"tls_{edge_for_place}")
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
                # Drop legacy tls_j_* override keys for this junction
                if jid:
                    edits.tls_overrides.pop(f"tls_j_{jid}", None)
                edits.tls_overrides[tid] = edits.tls_default
                st.session_state.edits = edits
                st.success("Semáforo confirmado en la intersección.")
                st.rerun()
        with a2:
            if st.button("✅ Confirmar alto", width="stretch", disabled=not eid):
                if not any(s.edge_id == eid for s in edits.stops):
                    edits.stops.append(StopSign(edge_id=eid, junction_id=jid))
                st.session_state.edits = edits
                st.rerun()
        with a3:
            if st.button("✅ Confirmar parqueo", width="stretch", disabled=not eid):
                length_m = 40.0
                for feat in edges_gj.get("features", []):
                    props = feat.get("properties") or {}
                    if str(props.get("id")) == str(eid):
                        try:
                            length_m = float(props.get("length") or 40.0)
                        except (TypeError, ValueError):
                            length_m = 40.0
                        break
                clearance = 5.0
                usable = max(10.0, length_m - 2.0 * clearance)
                edits.parking = [p for p in edits.parking if p.edge_id != eid]
                edits.parking.append(
                    ParkingConfig(
                        edge_id=eid,
                        side="right",
                        corner_clearance_m=clearance,
                        length_m=round(usable, 1),
                        capacity=max(1, int(usable / 5.0)),
                        reason="",
                    )
                )
                st.session_state.edits = edits
                st.rerun()
        with a4:
            if st.button("🗑️ Quitar selección", width="stretch", disabled=not (eid or jid)):
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
        if st.button("Aplicar carriles al tramo", width="stretch", disabled=not eid):
            edits.lane_overrides = [x for x in edits.lane_overrides if x.edge_id != eid]
            edits.lane_overrides.append(
                LaneOverride(edge_id=eid, num_lanes=int(nlanes), oneway_dual=dual)
            )
            st.session_state.edits = edits
            st.rerun()
    with x2:
        if st.button("Parqueo detallado al tramo", width="stretch", disabled=not eid):
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

    n_auto = sum(1 for s in edits.stops if (s.reason or "").startswith("default_"))
    st.caption(
        f"Altos: **{len(edits.stops)}** total · **{n_auto}** por regla default "
        f"(calle N–S × avenida E–O). En la simulación se aplican como `priority_stop` "
        f"(avenidas con prioridad; calles con alto deben detenerse)."
    )
    n_lane_def = sum(1 for x in edits.lane_overrides if (x.reason or "").startswith("default_"))
    n_park_def = sum(1 for p in edits.parking if (p.reason or "").startswith("default_"))
    st.caption(
        f"Calles: **{n_lane_def}** con 1 carril default · **{n_park_def}** con parqueo "
        f"derecho lleno (default). Los cambios manuales del tramo no se sobrescriben."
    )
    if st.button("Reaplicar regla default (calles N-S con alto)"):
        tls_jids = {
            str(t.get("node") or t.get("id") or "")
            for t in (st.session_state.tls_list or [])
        }
        for p in edits.tls_placements:
            if p.junction_id:
                tls_jids.add(str(p.junction_id))
        suggested = default_stops_ns_at_avenues(
            edges_gj, junctions, min_degree=3, skip_junction_ids=tls_jids
        )
        added = merge_default_stops(edits, suggested, replace_defaults=True)
        st.session_state.edits = edits
        st.success(f"Regla aplicada: {added} altos en calles N-S (cruces con avenida).")
        st.rerun()
    if st.button("Reaplicar default: 1 carril + parqueo derecho lleno"):
        lane_defs, park_defs = default_street_rules_for_edges(edges_gj)
        n_lane, n_park = merge_default_street_rules(
            edits, lane_defs, park_defs, replace_defaults=True
        )
        st.session_state.edits = edits
        st.success(
            f"Default aplicado: {n_lane} edges a 1 carril · {n_park} parqueos derechos llenos."
        )
        st.rerun()

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
        if st.button("💾 Guardar configuración de zona", type="primary", width="stretch"):
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
                    flow_gates=st.session_state.get("flow_gates") or [],
                    overwrite=overwrite,
                )
                st.session_state.scenario_name = scen_name.strip()
                st.session_state.scenario_folder = str(folder)
                st.success(f"Configuración guardada en `{folder.name}` (ligada al polígono).")
    with g2:
        if st.button("Continuar a TomTom", width="stretch"):
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


