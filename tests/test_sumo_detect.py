"""SUMO detection — integration (skipped in default CI)."""

import pytest

from src.sumo_env import detect_sumo


@pytest.mark.integration
def test_sumo_detect() -> None:
    env = detect_sumo()
    assert env.message
