"""Streamlit wrapper for the Chart.js analytics component.

Data flows *into* the component as a JSON-serialisable payload produced by
:func:`backend.analytics.chart_payload`; charts are rendered locally with the
vendored Chart.js build so the analytics view has no CDN dependency.
"""

from __future__ import annotations

from typing import Any, Dict

import streamlit.components.v1 as components

from config import CHARTS_FRONTEND_DIR
from backend.logging_setup import get_logger

log = get_logger("anatonix.charts_component")

_component = None


def _get_component():
    global _component
    if _component is None:
        if not (CHARTS_FRONTEND_DIR / "index.html").exists():
            raise FileNotFoundError(f"Charts component missing at {CHARTS_FRONTEND_DIR}")
        _component = components.declare_component(
            "anatonix_charts", path=str(CHARTS_FRONTEND_DIR)
        )
        log.info("Charts component registered from %s", CHARTS_FRONTEND_DIR)
    return _component


def charts_component(payload: Dict[str, Any], key: str, theme: str = "dark") -> Any:
    return _get_component()(payload=payload, theme=theme, key=key, default=None)
