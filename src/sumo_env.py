"""SUMO installation detection and helper paths."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load project .env so SUMO_HOME works without a system-wide variable.
_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")


@dataclass
class SumoEnv:
    home: Optional[Path]
    sumo_bin: Optional[Path]
    netconvert_bin: Optional[Path]
    tools_dir: Optional[Path]
    ok: bool
    message: str


def _candidate_homes() -> list[Path]:
    homes: list[Path] = []
    env = os.environ.get("SUMO_HOME", "").strip().strip('"')
    if env:
        homes.append(Path(env))
    # Common Windows install locations
    for base in (
        Path(r"C:\Program Files (x86)\Eclipse\Sumo"),
        Path(r"C:\Program Files\Eclipse\Sumo"),
        Path(r"C:\Sumo"),
        Path.home() / "Sumo",
    ):
        homes.append(base)
    return homes


def _find_bin(name: str, home: Optional[Path]) -> Optional[Path]:
    which = shutil.which(name)
    if which:
        return Path(which)
    if home:
        for sub in ("bin", "bin64", ""):
            candidate = home / sub / (name + (".exe" if os.name == "nt" else ""))
            if candidate.is_file():
                return candidate
    return None


def ensure_sumolib_on_path(home: Optional[Path]) -> bool:
    """Add SUMO tools to sys.path so `import sumolib` / `import traci` work."""
    if home is None:
        return False
    tools = home / "tools"
    if tools.is_dir() and str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import sumolib  # noqa: F401
        import traci  # noqa: F401

        return True
    except ImportError:
        return False


def detect_sumo() -> SumoEnv:
    home: Optional[Path] = None
    for candidate in _candidate_homes():
        if candidate.is_dir() and ((candidate / "tools").is_dir() or (candidate / "bin").is_dir()):
            home = candidate
            break

    if home and not os.environ.get("SUMO_HOME"):
        os.environ["SUMO_HOME"] = str(home)

    sumo_bin = _find_bin("sumo", home)
    netconvert_bin = _find_bin("netconvert", home)
    tools_dir = (home / "tools") if home and (home / "tools").is_dir() else None
    libs_ok = ensure_sumolib_on_path(home)

    if sumo_bin and netconvert_bin and libs_ok:
        return SumoEnv(
            home=home,
            sumo_bin=sumo_bin,
            netconvert_bin=netconvert_bin,
            tools_dir=tools_dir,
            ok=True,
            message=f"SUMO listo ({sumo_bin})",
        )

    missing = []
    if not sumo_bin:
        missing.append("sumo")
    if not netconvert_bin:
        missing.append("netconvert")
    if not libs_ok:
        missing.append("sumolib/traci (SUMO_HOME/tools)")
    return SumoEnv(
        home=home,
        sumo_bin=sumo_bin,
        netconvert_bin=netconvert_bin,
        tools_dir=tools_dir,
        ok=False,
        message="SUMO incompleto: falta " + ", ".join(missing) + ". Instale SUMO y defina SUMO_HOME.",
    )


def run_cmd(args: list[str], cwd: Optional[Path] = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
