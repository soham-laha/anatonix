"""Streamlit custom component wrapper for the Anatonix monitoring engine.

The component is a real bidirectional custom component: its static frontend
(:mod:`config.FRONTEND_DIR`) speaks the Streamlit component protocol directly
(see ``frontend/bridge.js``) and returns telemetry batches that Python persists.

`declare_component` is executed once per process; later calls reuse the same
instance so the iframe is never rebuilt during a rerun.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit.components.v1 as components

from config import FRONTEND_DIR
from backend.logging_setup import get_logger

log = get_logger("anatonix.component")

_component = None


def _get_component():
    global _component
    if _component is None:
        if not (FRONTEND_DIR / "index.html").exists():
            raise FileNotFoundError(
                f"Monitoring component frontend missing at {FRONTEND_DIR}"
            )
        _component = components.declare_component(
            "anatonix_monitoring", path=str(FRONTEND_DIR)
        )
        log.info("Monitoring component registered from %s", FRONTEND_DIR)
    return _component


def monitoring_component(
    settings: Dict[str, Any],
    calibration: Optional[Dict[str, Any]],
    vision: Dict[str, Any],
    foot_note: str = "",
    key: str = "anatonix_monitoring",
) -> Optional[Dict[str, Any]]:
    """Render the live monitoring UI.

    Returns the latest payload sent by the browser (telemetry batch + events +
    live snapshot), or ``None`` before the component has sent anything.
    """
    return _get_component()(
        settings=settings,
        calibration=calibration,
        vision=vision,
        footNote=foot_note,
        key=key,
        default=None,
    )
