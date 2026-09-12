"""Thin wrapper: run pytest suite (no SUMO/TomTom by default)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "pytest", "-q", "-m", "not integration"],
            cwd=ROOT,
        )
    )
