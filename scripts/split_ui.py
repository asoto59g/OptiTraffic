"""One-shot splitter: extract wizard steps from app.py into ui/."""
from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
app_path = root / "app.py"
lines = app_path.read_text(encoding="utf-8").splitlines(keepends=True)


def body(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end - 1])


SHARED = '''"""OptiTraffic wizard step module."""
from __future__ import annotations

import json
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
    reverse_geocode,
    reverse_geocode_full,
    validate_area,
)
from src.demand import DENSITY_SCENARIOS, generate_demand, get_density_scenario  # noqa: E402
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
    LaneOverride,
    NetworkEdits,
    ParkingConfig,
    StopSign,
    TlsPlacement,
    TlsTiming,
    default_stops_ns_at_avenues,
    merge_default_stops,
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
    _close_traci,
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
    synthetic_peak_edge_levels,
    usage_count,
)
from src.traffic_params import CITY_MAX_SPEED_KMH, CITY_MAX_SPEED_MS, VEH_LENGTH_M  # noqa: E402
from src.wizard_state import STEPS  # noqa: E402
from ui.common import (  # noqa: E402
    _bump_map,
    _guess_place_from_filename,
    go_to,
    render_study_map,
)
from src.viz import (  # noqa: E402
    add_draw_control,
    add_edges_layer,
    add_flow_gate_markers,
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
'''

COMMON = '''"""Shared Streamlit wizard helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wizard_state import STEPS, ensure_session_defaults  # noqa: E402
from src.viz import (  # noqa: E402
    add_draw_control,
    add_edges_layer,
    add_flow_gate_markers,
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

'''

ui = root / "ui"
ui.mkdir(exist_ok=True)
(ui / "common.py").write_text(COMMON + "\n" + body(414, 485), encoding="utf-8")

mapping = {
    "step_zona.py": (243, 414),
    "step_red.py": (485, 591),
    "step_config.py": (591, 1036),
    "step_tomtom.py": (1036, 1158),
    "step_sim.py": (1158, 1628),
    "step_results.py": (1628, 1720),
}
for name, (a, b) in mapping.items():
    (ui / name).write_text(SHARED + "\n\n" + body(a, b), encoding="utf-8")
    print("wrote", name)

sidebar_body = body(182, 228)
new_app = f'''"""OptiTraffic — Streamlit wizard orchestrator."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.logging_config import setup_logging  # noqa: E402
from src.scenarios import apply_scenario_to_session, list_scenarios, load_scenario  # noqa: E402
from src.sumo_env import detect_sumo  # noqa: E402
from src.tomtom import load_api_key, usage_count  # noqa: E402
from src.wizard_state import STEPS  # noqa: E402
from ui.common import go_to, init_state  # noqa: E402
from ui.step_config import step_config  # noqa: E402
from ui.step_red import step_red  # noqa: E402
from ui.step_results import step_results  # noqa: E402
from ui.step_sim import step_sim  # noqa: E402
from ui.step_tomtom import step_tomtom  # noqa: E402
from ui.step_zona import step_zona  # noqa: E402

setup_logging()

st.set_page_config(
    page_title="OptiTraffic",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded",
)


{sidebar_body}

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
'''

backup = root / "app.py.bak"
if not backup.exists():
    backup.write_text("".join(lines), encoding="utf-8")
app_path.write_text(new_app, encoding="utf-8")
print("app.py lines", len(new_app.splitlines()))
print("done")
