"""WizardState unit tests."""

from src.wizard_state import STEPS, WizardState, ensure_session_defaults


def test_request_nav_and_apply() -> None:
    ws = WizardState()
    ws.request_nav(STEPS[2])
    assert ws._pending_nav == STEPS[2]
    ws.apply_pending()
    assert ws.step == STEPS[2]
    assert ws.nav_step == STEPS[2]
    assert ws._pending_nav is None


def test_clear_network() -> None:
    ws = WizardState(
        edge_levels={"a": 1.0},
        flow_gates=[{"x": 1}],
        sim_result={"ok": True},
        run_dir="runs/current",
    )
    ws.clear_network()
    assert ws.edge_levels == {}
    assert ws.flow_gates == []
    assert ws.sim_result is None
    assert ws.run_dir is None


def test_ensure_session_defaults() -> None:
    session: dict = {}
    ensure_session_defaults(session)
    assert session["step"] == STEPS[0]
    assert "edits" in session
