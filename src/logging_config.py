"""Central logging for OptiTraffic (UI stays resilient; I/O leaves a trail)."""

from __future__ import annotations

import logging
import sys
from typing import Optional

_CONFIGURED = False
LOGGER_NAME = "optitraffic"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Idempotent root logger for the optitraffic namespace."""
    global _CONFIGURED
    log = logging.getLogger(LOGGER_NAME)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        log.addHandler(handler)
        log.setLevel(level)
        log.propagate = False
        _CONFIGURED = True
    return log


def get_logger(name: Optional[str] = None) -> logging.Logger:
    setup_logging()
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)
