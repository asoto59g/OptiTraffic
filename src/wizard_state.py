"""Typed wizard session state for OptiTraffic Streamlit UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


STEPS = [
    "1. Zona",
    "2. Red OSM/SUMO",
    "3. Configurar red",
    "4. TomTom",
    "5. Simulación",
    "6. Resultados",
]


@dataclass
class WizardState:
    """Explicit wizard fields; syncs to/from st.session_state."""

    step: str = STEPS[0]
    nav_step: str = STEPS[0]
    area: Any = None
    net_path: Any = None
    edges_gj: Any = None
    tls_list: list = field(default_factory=list)
    edits: Any = None
    edge_levels: dict = field(default_factory=dict)
    flow_gates: list = field(default_factory=list)
    sim_gate_edge_id: Optional[str] = None
    sim_result: Any = None
    run_dir: Any = None
    center: tuple[float, float] = (9.93, -84.08)
    map_zoom: int = 14
    map_nonce: int = 0
    preview_polygon: Any = None
    city: str = "San José"
    country: str = "Costa Rica"
    country_code: str = ""
    edge_levels_source: str = ""
    scenario_name: str = ""
    scenario_folder: str = ""
    _pending_nav: Optional[str] = None
    _pending_city: Optional[str] = None
    _pending_country: Optional[str] = None

    def request_nav(self, step: str) -> None:
        self.step = step
        self._pending_nav = step

    def request_place(self, city: Optional[str] = None, country: Optional[str] = None) -> None:
        if city is not None:
            self._pending_city = city
        if country is not None:
            self._pending_country = country

    def apply_pending(self) -> None:
        if self._pending_nav is not None:
            self.step = self._pending_nav
            self.nav_step = self._pending_nav
            self._pending_nav = None
        if self._pending_city is not None:
            self.city = self._pending_city
            self._pending_city = None
        if self._pending_country is not None:
            self.country = self._pending_country
            self._pending_country = None
        if self.nav_step not in STEPS:
            self.nav_step = self.step

    def clear_network(self) -> None:
        self.net_path = None
        self.edges_gj = None
        self.tls_list = []
        self.edge_levels = {}
        self.flow_gates = []
        self.sim_gate_edge_id = None
        self.sim_result = None
        self.run_dir = None
        self.edge_levels_source = ""

    def bump_map(
        self,
        center: tuple[float, float] | None = None,
        zoom: int | None = None,
    ) -> None:
        if center is not None:
            self.center = center
        if zoom is not None:
            self.map_zoom = zoom
        self.map_nonce = int(self.map_nonce or 0) + 1

    def to_session_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "nav_step": self.nav_step,
            "area": self.area,
            "net_path": self.net_path,
            "edges_gj": self.edges_gj,
            "tls_list": self.tls_list,
            "edits": self.edits,
            "edge_levels": self.edge_levels,
            "flow_gates": self.flow_gates,
            "sim_gate_edge_id": self.sim_gate_edge_id,
            "sim_result": self.sim_result,
            "run_dir": self.run_dir,
            "center": self.center,
            "map_zoom": self.map_zoom,
            "map_nonce": self.map_nonce,
            "preview_polygon": self.preview_polygon,
            "city": self.city,
            "country": self.country,
            "country_code": self.country_code,
            "edge_levels_source": self.edge_levels_source,
            "scenario_name": self.scenario_name,
            "scenario_folder": self.scenario_folder,
        }

    def push_to_streamlit(self, session: Any) -> None:
        """Write known fields into Streamlit session_state."""
        data = self.to_session_dict()
        for k, v in data.items():
            session[k] = v
        if self._pending_nav is not None:
            session["_pending_nav"] = self._pending_nav
        if self._pending_city is not None:
            session["_pending_city"] = self._pending_city
        if self._pending_country is not None:
            session["_pending_country"] = self._pending_country

    @classmethod
    def from_streamlit(cls, session: Any) -> "WizardState":
        """Build from session_state defaults without requiring all keys."""
        from .editors import NetworkEdits

        ws = cls()
        ws.step = session.get("step", STEPS[0])
        ws.nav_step = session.get("nav_step", ws.step)
        ws.area = session.get("area")
        ws.net_path = session.get("net_path")
        ws.edges_gj = session.get("edges_gj")
        ws.tls_list = session.get("tls_list") or []
        ws.edits = session.get("edits") or NetworkEdits()
        ws.edge_levels = session.get("edge_levels") or {}
        ws.flow_gates = session.get("flow_gates") or []
        ws.sim_gate_edge_id = session.get("sim_gate_edge_id")
        ws.sim_result = session.get("sim_result")
        ws.run_dir = session.get("run_dir")
        ws.center = session.get("center") or (9.93, -84.08)
        ws.map_zoom = int(session.get("map_zoom") or 14)
        ws.map_nonce = int(session.get("map_nonce") or 0)
        ws.preview_polygon = session.get("preview_polygon")
        ws.city = session.get("city") or "San José"
        ws.country = session.get("country") or "Costa Rica"
        ws.country_code = session.get("country_code") or ""
        ws.edge_levels_source = session.get("edge_levels_source") or ""
        ws.scenario_name = session.get("scenario_name") or ""
        ws.scenario_folder = session.get("scenario_folder") or ""
        ws._pending_nav = session.get("_pending_nav")
        ws._pending_city = session.get("_pending_city")
        ws._pending_country = session.get("_pending_country")
        return ws


def ensure_session_defaults(session: Any) -> None:
    """Initialize missing Streamlit session keys (compat with legacy app)."""
    from .editors import NetworkEdits

    defaults = {
        "step": STEPS[0],
        "nav_step": STEPS[0],
        "area": None,
        "net_path": None,
        "edges_gj": None,
        "tls_list": [],
        "edits": NetworkEdits(),
        "edge_levels": {},
        "flow_gates": [],
        "sim_gate_edge_id": None,
        "sim_result": None,
        "run_dir": None,
        "center": (9.93, -84.08),
        "map_zoom": 14,
        "map_nonce": 0,
        "preview_polygon": None,
        "city": "San José",
        "country": "Costa Rica",
        "country_code": "",
    }
    for k, v in defaults.items():
        if k not in session:
            session[k] = v
