"""OptiTraffic — Streamlit wizard orchestrator."""

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

    st.sidebar.radio("Pasos", STEPS, key="nav_step")
    if st.session_state.nav_step != st.session_state.step:
        st.session_state.step = st.session_state.nav_step

    st.sidebar.divider()
    st.sidebar.subheader("Escenarios guardados")
    scenarios = list_scenarios()
    if scenarios:
        labels = [p.name for p in scenarios]
        pick = st.sidebar.selectbox(
            "Cargar configuración", ["—"] + labels, key="sidebar_scen_pick"
        )
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
