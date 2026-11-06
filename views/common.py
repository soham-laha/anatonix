"""Shared plumbing for the Streamlit views."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import streamlit as st

from backend.analytics import alerts_frame, summarise_metrics, telemetry_frame
from backend.database import Database, parse_iso, utcnow
from backend.data_validation import validate_settings
from backend.logging_setup import get_logger
from backend.session_manager import ProcessResult, SessionManager
from config import (
    APP_VERSION,
    DEFAULT_SETTINGS,
    FACE_MODEL_URL,
    POSE_MODEL_URL,
    TASKS_VISION_BUNDLE,
    TASKS_VISION_WASM,
)

log = get_logger("anatonix.views")

SETTINGS_VERSION_KEY = "anatonix_settings_version"


# --------------------------------------------------------------------------
# services
# --------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_database() -> Database:
    """One Database instance per process (connections stay per-operation)."""
    db = Database()
    db.finalize_stale_sessions(grace_seconds=120)
    return db


def get_manager(db: Database) -> SessionManager:
    return SessionManager(db)


def load_settings(db: Database) -> Dict[str, Any]:
    stored = db.get_settings()
    merged = {**DEFAULT_SETTINGS, **stored}
    return merged


def save_settings(db: Database, values: Dict[str, Any]) -> List[str]:
    """Validate, persist and invalidate the cached settings snapshot."""
    clean, warnings = validate_settings(values)
    if clean:
        db.set_settings(clean)
        log.info("Settings updated: %s", ", ".join(sorted(clean)))
    st.session_state[SETTINGS_VERSION_KEY] = utcnow().timestamp()
    return warnings


def calibration_payload(db: Database) -> Optional[Dict[str, Any]]:
    calibration = db.latest_calibration()
    if not calibration:
        return None
    return {
        "pitch": float(calibration["baseline_pitch"]),
        "shoulderTilt": float(calibration["baseline_shoulder_tilt"]),
        "shoulderSpan": float(calibration["baseline_shoulder_span"]),
        "quality": float(calibration["quality_score"]),
        "createdAt": calibration["created_at"],
        "calibrationId": calibration["calibration_id"],
        "uncalibrated": False,
    }


def vision_config() -> Dict[str, str]:
    return {
        "bundleUrl": TASKS_VISION_BUNDLE,
        "wasmBase": TASKS_VISION_WASM,
        "poseModelUrl": POSE_MODEL_URL,
        "faceModelUrl": FACE_MODEL_URL,
    }


def process_component_payload(db: Database, value: Any) -> Optional[ProcessResult]:
    """Persist a telemetry batch coming back from the browser component."""
    if not isinstance(value, dict):
        return None
    manager = get_manager(db)
    result = manager.process_payload(value)
    live = value.get("live") or {}
    if live:
        st.session_state["anatonix_live"] = live
        cameras = live.get("cameras")
        if isinstance(cameras, list) and cameras:
            st.session_state["anatonix_cameras"] = cameras
    session_id = value.get("session_id")
    if session_id:
        st.session_state["anatonix_session_id"] = session_id
    if result.changed:
        st.session_state["anatonix_last_sync"] = utcnow().isoformat()
    return result


# --------------------------------------------------------------------------
# read helpers
# --------------------------------------------------------------------------
def active_session(db: Database) -> Optional[Dict[str, Any]]:
    """Return the session that is currently receiving telemetry, if any."""
    session = db.active_session()
    if not session:
        return None
    last_ts = _last_telemetry_ts(db, session["session_id"])
    if last_ts is None:
        started = parse_iso(session["start_timestamp"])
        if started and (utcnow() - started) < timedelta(seconds=120):
            return session
        return None
    if (utcnow() - last_ts) <= timedelta(seconds=90):
        return session
    return None


def _last_telemetry_ts(db: Database, session_id: str) -> Optional[datetime]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT MAX(timestamp) AS ts FROM telemetry WHERE session_id = ?", (session_id,)
        ).fetchone()
    return parse_iso(row["ts"]) if row and row["ts"] else None


def latest_session(db: Database) -> Optional[Dict[str, Any]]:
    sessions = db.list_sessions(limit=1)
    return sessions[0] if sessions else None


def session_metrics(db: Database, session_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    frame = telemetry_frame(db, session_ids=[session_id])
    metrics = summarise_metrics(
        frame,
        lean_threshold=float(settings["forward_lean_threshold"]),
        tilt_threshold=float(settings["shoulder_tilt_threshold"]),
        blink_low=float(settings["blink_low_bpm"]),
        blink_reference=float(settings["blink_reference_bpm"]),
    )
    alerts = alerts_frame(db)
    if not alerts.empty:
        alerts = alerts[alerts["session_id"] == session_id]
    posture_alerts = alerts[alerts["alert_type"] == "posture_alert"] if not alerts.empty else alerts
    metrics["posture_alert_count"] = int(len(posture_alerts))
    metrics["frame"] = frame
    metrics["alerts"] = alerts
    return metrics


def session_duration_seconds(db: Database, session: Dict[str, Any]) -> Optional[float]:
    if not session:
        return None
    if session.get("duration_seconds") is not None:
        return float(session["duration_seconds"])
    start = parse_iso(session.get("start_timestamp"))
    if not start:
        return None
    last_ts = _last_telemetry_ts(db, session["session_id"])
    end = last_ts or utcnow()
    return max(0.0, (end - start).total_seconds())


def system_status(db: Database) -> Dict[str, str]:
    """Compact status summary used by the sidebar and dashboard."""
    active = active_session(db)
    if active:
        return {
            "tone": "live",
            "label": "Monitoring active",
            "detail": f"Session started {short_time(active['start_timestamp'])}",
        }
    last = latest_session(db)
    if last:
        return {
            "tone": "",
            "label": "Idle · data stored",
            "detail": f"Last session {short_date(last['start_timestamp'])}",
        }
    return {"tone": "", "label": "No sessions yet", "detail": "Open Live Monitoring to begin"}


def camera_status() -> Dict[str, str]:
    live = st.session_state.get("anatonix_live") or {}
    state = str(live.get("state") or "standby")
    mapping = {
        "tracking": ("live", "Camera tracking"),
        "ready": ("live", "Camera ready"),
        "initializing": ("warn", "Camera initialising"),
        "low_confidence": ("warn", "Low confidence"),
        "no_face": ("warn", "Face not detected"),
        "no_shoulders": ("warn", "Shoulders not detected"),
        "paused": ("warn", "Monitoring paused"),
        "camera_unavailable": ("bad", "Camera unavailable"),
        "error": ("bad", "Camera error"),
    }
    tone, label = mapping.get(state, ("", "Camera idle"))
    return {"tone": tone, "label": label}


def short_time(value: Any) -> str:
    parsed = parse_iso(value) if isinstance(value, str) else None
    if parsed is None:
        return "—"
    return parsed.astimezone().strftime("%H:%M")


def short_date(value: Any) -> str:
    parsed = parse_iso(value) if isinstance(value, str) else None
    if parsed is None:
        return "—"
    local = parsed.astimezone()
    today = datetime.now(timezone.utc).astimezone().date()
    if local.date() == today:
        return f"today {local.strftime('%H:%M')}"
    return local.strftime("%d %b · %H:%M")


def require_data(value: Any, message: str) -> bool:
    """Render a muted note and return False when a value is missing."""
    if value is None:
        st.markdown(
            f'<div style="font-size:11.5px;color:var(--anx-dim);margin-top:-4px">{message}</div>',
            unsafe_allow_html=True,
        )
        return False
    return True


def version_footer_items(db: Database) -> Dict[str, str]:
    stats = db.storage_stats()
    return {
        "version": APP_VERSION,
        "sessions": str(stats["sessions"]),
        "records": f"{stats['telemetry_records']:,}",
        "db": f"{stats['db_size_bytes'] / 1024:.0f} KB",
    }
