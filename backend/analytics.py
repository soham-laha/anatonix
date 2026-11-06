"""Analytics over persisted telemetry.

Every number returned here is derived from rows stored in SQLite.  When there
is not enough data for a statistic the function returns ``None`` (rendered as
"insufficient data") instead of substituting a plausible looking value.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from backend.database import Database, to_iso, utcnow
from backend.logging_setup import get_logger
from config import DEFAULT_SETTINGS

log = get_logger("anatonix.analytics")

TELEMETRY_COLUMNS = [
    "record_id",
    "session_id",
    "timestamp",
    "forward_lean",
    "shoulder_tilt",
    "ear_value",
    "proximity_ratio",
    "blink_rate",
    "tracking_confidence",
    "posture_status",
]
NUMERIC_COLUMNS = [
    "forward_lean",
    "shoulder_tilt",
    "ear_value",
    "proximity_ratio",
    "blink_rate",
    "tracking_confidence",
]

#: Minimum valid samples before a statistic is considered meaningful.
MIN_SAMPLES = 30


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def telemetry_frame(
    db: Database,
    session_ids: Optional[Sequence[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> pd.DataFrame:
    rows = db.fetch_telemetry(session_ids=session_ids, since=since, until=until)
    if not rows:
        frame = pd.DataFrame({column: pd.Series(dtype="object") for column in TELEMETRY_COLUMNS})
        frame["timestamp"] = pd.Series(dtype="datetime64[ns, UTC]")
        return frame
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    for column in NUMERIC_COLUMNS:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def alerts_frame(
    db: Database,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> pd.DataFrame:
    rows = db.fetch_alerts(since=since, until=until)
    if not rows:
        return pd.DataFrame(
            columns=["alert_id", "session_id", "timestamp", "alert_type",
                     "measurement", "threshold", "message"]
        )
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    return frame


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _mean(series: pd.Series) -> Optional[float]:
    if series is None:
        return None
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.mean())


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, digits)


def blink_category(bpm: Optional[float], low: float = 8.0, reference: float = 12.0) -> Tuple[str, str]:
    """Return (category_key, label) for the Blink Rate Indicator."""
    if bpm is None:
        return "unknown", "No data"
    if bpm < low:
        return "low", "Low blink frequency"
    if bpm < reference:
        return "reduced", "Reduced blink frequency"
    return "reference", "Within reference range"


def valid_tracking_mask(frame: pd.DataFrame) -> pd.Series:
    """A sample counts as tracked when a posture metric and confidence exist."""
    if frame is None or frame.empty:
        return pd.Series(dtype=bool)
    posture_ok = frame["forward_lean"].notna() | frame["shoulder_tilt"].notna()
    if "tracking_confidence" in frame:
        confidence_ok = frame["tracking_confidence"].fillna(0.0) >= 0.3
    else:
        confidence_ok = pd.Series(True, index=frame.index)
    return (posture_ok & confidence_ok).fillna(False)


def empty_metrics() -> Dict[str, Any]:
    return {
        "samples": 0,
        "valid_samples": 0,
        "valid_seconds": 0,
        "tracking_pct": None,
        "avg_forward_lean": None,
        "max_forward_lean": None,
        "avg_shoulder_tilt": None,
        "max_shoulder_tilt": None,
        "avg_ear": None,
        "avg_blink_rate": None,
        "blink_category": "unknown",
        "blink_category_label": "No data",
        "avg_proximity": None,
        "avg_confidence": None,
        "pct_time_outside_threshold": None,
        "seconds_outside_threshold": 0,
        "posture_status_counts": {},
        "monitored_seconds": 0,
        "composite_score": None,
        "sufficient_data": False,
    }


def summarise_metrics(
    frame: pd.DataFrame,
    lean_threshold: float = DEFAULT_SETTINGS["forward_lean_threshold"],
    tilt_threshold: float = DEFAULT_SETTINGS["shoulder_tilt_threshold"],
    blink_low: float = DEFAULT_SETTINGS["blink_low_bpm"],
    blink_reference: float = DEFAULT_SETTINGS["blink_reference_bpm"],
) -> Dict[str, Any]:
    """Compute the metric bundle used by dashboards, cards and reports."""
    metrics = empty_metrics()
    if frame is None or frame.empty:
        return metrics

    total = int(len(frame))
    mask = valid_tracking_mask(frame)
    valid = int(mask.sum())

    outside = pd.Series(False, index=frame.index)
    outside |= frame["forward_lean"].fillna(-1.0) > lean_threshold
    outside |= frame["shoulder_tilt"].fillna(-1.0) > tilt_threshold
    outside = outside & mask
    seconds_outside = int(outside.sum())

    status_counts: Dict[str, int] = {}
    if "posture_status" in frame:
        status_counts = {
            str(k): int(v)
            for k, v in frame["posture_status"].fillna("untracked").value_counts().items()
        }

    avg_bpm = _mean(frame["blink_rate"])
    category, label = blink_category(avg_bpm, blink_low, blink_reference)

    metrics.update(
        {
            "samples": total,
            "valid_samples": valid,
            "valid_seconds": valid,
            "tracking_pct": _round(100.0 * valid / total, 1) if total else None,
            "avg_forward_lean": _round(_mean(frame["forward_lean"])),
            "max_forward_lean": _round(frame["forward_lean"].max()) if frame["forward_lean"].notna().any() else None,
            "avg_shoulder_tilt": _round(_mean(frame["shoulder_tilt"])),
            "max_shoulder_tilt": _round(frame["shoulder_tilt"].max()) if frame["shoulder_tilt"].notna().any() else None,
            "avg_ear": _round(_mean(frame["ear_value"]), 3),
            "avg_blink_rate": _round(avg_bpm, 1),
            "blink_category": category,
            "blink_category_label": label,
            "avg_proximity": _round(_mean(frame["proximity_ratio"]), 3),
            "avg_confidence": _round(_mean(frame["tracking_confidence"]), 3),
            "pct_time_outside_threshold": _round(100.0 * seconds_outside / valid, 1) if valid else None,
            "seconds_outside_threshold": seconds_outside,
            "posture_status_counts": status_counts,
            "monitored_seconds": total,
            "sufficient_data": valid >= MIN_SAMPLES,
        }
    )
    metrics["composite_score"] = composite_score(metrics)
    return metrics


def composite_score(metrics: Dict[str, Any]) -> Optional[float]:
    """Transparent 0-100 ergonomic composite (experimental application metric).

    Components, each normalised to 0-100 and weighted:

    * posture (35%)   = 100 - 2.0 x avg_forward_lean, floored at 0
    * symmetry (20%)  = 100 - 6.0 x avg_shoulder_tilt, floored at 0
    * threshold (25%) = 100 - pct_time_outside_threshold
    * ocular (20%)    = 100 x min(avg_blink_rate / 12, 1)

    Produced only when at least :data:`MIN_SAMPLES` valid samples exist.  This is
    an application heuristic for spotting trends; it is not a health score.
    """
    if not metrics.get("sufficient_data"):
        return None
    lean = metrics.get("avg_forward_lean")
    tilt = metrics.get("avg_shoulder_tilt")
    outside = metrics.get("pct_time_outside_threshold")
    bpm = metrics.get("avg_blink_rate")
    if None in (lean, tilt, outside, bpm):
        return None
    posture = max(0.0, 100.0 - 2.0 * lean)
    symmetry = max(0.0, 100.0 - 6.0 * tilt)
    threshold_time = max(0.0, 100.0 - outside)
    ocular = min(100.0, 100.0 * bpm / 12.0)
    score = 0.35 * posture + 0.20 * symmetry + 0.25 * threshold_time + 0.20 * ocular
    return round(max(0.0, min(100.0, score)), 1)


# --------------------------------------------------------------------------
# time-range analysis
# --------------------------------------------------------------------------
RANGE_LABELS = {
    "today": "Today",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "all": "All recorded data",
    "custom": "Custom range",
    "session": "Current session",
}


def range_bounds(
    key: str, now: Optional[datetime] = None, custom: Optional[Tuple[str, str]] = None
) -> Tuple[Optional[str], Optional[str], str]:
    """Translate a UI filter into ISO timestamp bounds."""
    now = now or utcnow()
    if key == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return to_iso(start), to_iso(now), RANGE_LABELS["today"]
    if key == "7d":
        return to_iso(now - timedelta(days=7)), to_iso(now), RANGE_LABELS["7d"]
    if key == "30d":
        return to_iso(now - timedelta(days=30)), to_iso(now), RANGE_LABELS["30d"]
    if key == "custom" and custom:
        return to_iso(custom[0]), to_iso(custom[1]), RANGE_LABELS["custom"]
    return None, to_iso(now), RANGE_LABELS["all"]


def analyse_range(
    db: Database,
    since: Optional[str] = None,
    until: Optional[str] = None,
    *,
    lean_threshold: float = DEFAULT_SETTINGS["forward_lean_threshold"],
    tilt_threshold: float = DEFAULT_SETTINGS["shoulder_tilt_threshold"],
    blink_low: float = DEFAULT_SETTINGS["blink_low_bpm"],
    blink_reference: float = DEFAULT_SETTINGS["blink_reference_bpm"],
) -> Dict[str, Any]:
    sessions = db.list_sessions(since=since, until=until)
    session_ids = [s["session_id"] for s in sessions]
    frame = (
        telemetry_frame(db, session_ids=session_ids)
        if session_ids
        else telemetry_frame(db, since=since, until=until)
    )
    alerts = alerts_frame(db, since=since, until=until)
    if not alerts.empty and session_ids:
        alerts = alerts[alerts["session_id"].isin(session_ids)]

    metrics = summarise_metrics(frame, lean_threshold, tilt_threshold, blink_low, blink_reference)
    posture_alerts = alerts[alerts["alert_type"] == "posture_alert"] if not alerts.empty else alerts
    break_alerts = alerts[alerts["alert_type"] == "break_reminder"] if not alerts.empty else alerts
    elapsed = sum(float(s.get("duration_seconds") or 0.0) for s in sessions)

    metrics.update(
        {
            "sessions": len(sessions),
            "posture_alert_count": int(len(posture_alerts)),
            "break_reminder_count": int(len(break_alerts)),
            "total_monitored_seconds": int(elapsed),
        }
    )
    return {
        "frame": frame,
        "alerts": alerts,
        "sessions": sessions,
        "metrics": metrics,
        "daily": daily_consistency(frame),
        "status_distribution": status_distribution(metrics),
    }


def daily_consistency(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-day monitored minutes and mean metrics (for the consistency chart)."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "monitored_minutes", "samples", "avg_forward_lean"])
    working = frame.copy()
    working["date"] = working["timestamp"].dt.date
    grouped = working.groupby("date").agg(
        samples=("timestamp", "count"),
        monitored_minutes=("timestamp", lambda s: round(len(s) / 60.0, 1)),
        avg_forward_lean=("forward_lean", "mean"),
        avg_shoulder_tilt=("shoulder_tilt", "mean"),
    )
    grouped = grouped.reset_index()
    for column in ("avg_forward_lean", "avg_shoulder_tilt"):
        grouped[column] = grouped[column].round(2)
    return grouped


def status_distribution(metrics: Dict[str, Any]) -> Dict[str, int]:
    counts = metrics.get("posture_status_counts") or {}
    return {
        "good": int(counts.get("good", 0)),
        "elevated": int(counts.get("elevated", 0)),
        "alert": int(counts.get("alert", 0)),
        "untracked": int(counts.get("untracked", 0)),
    }


# --------------------------------------------------------------------------
# session level
# --------------------------------------------------------------------------
def session_summary(db: Database, session_id: str, **thresholds: float) -> Dict[str, Any]:
    session = db.get_session(session_id)
    if session is None:
        return {"session": None, "metrics": empty_metrics(), "alerts": pd.DataFrame()}
    frame = telemetry_frame(db, session_ids=[session_id])
    alerts = alerts_frame(db)
    if not alerts.empty:
        alerts = alerts[alerts["session_id"] == session_id]
    metrics = summarise_metrics(frame, **thresholds)
    posture_alerts = alerts[alerts["alert_type"] == "posture_alert"] if not alerts.empty else alerts

    duration = session.get("duration_seconds")
    if duration is None:
        start = pd.to_datetime(session["start_timestamp"], utc=True, errors="coerce")
        if pd.notna(start) and not frame.empty:
            duration = float((utcnow() - start.to_pydatetime()).total_seconds())
        else:
            duration = float(metrics["monitored_seconds"])
    metrics["session_duration_seconds"] = round(float(duration or 0.0), 1)
    metrics["posture_alert_count"] = int(len(posture_alerts))
    return {
        "session": session,
        "frame": frame,
        "alerts": alerts,
        "metrics": metrics,
    }


def session_timeseries(frame: pd.DataFrame, max_points: int = 480) -> Dict[str, List[Any]]:
    """Down-sample a telemetry frame into chart-ready lists."""
    if frame is None or frame.empty:
        return {"labels": [], "forward_lean": [], "shoulder_tilt": [], "ear_value": [],
                "blink_rate": [], "proximity_ratio": [], "status": []}
    step = max(1, int(math.ceil(len(frame) / max_points)))
    sampled = frame.iloc[::step]
    return {
        "labels": [ts.strftime("%H:%M:%S") for ts in sampled["timestamp"]],
        "iso": [ts.isoformat() for ts in sampled["timestamp"]],
        "forward_lean": [None if pd.isna(v) else round(float(v), 2) for v in sampled["forward_lean"]],
        "shoulder_tilt": [None if pd.isna(v) else round(float(v), 2) for v in sampled["shoulder_tilt"]],
        "ear_value": [None if pd.isna(v) else round(float(v), 3) for v in sampled["ear_value"]],
        "blink_rate": [None if pd.isna(v) else round(float(v), 1) for v in sampled["blink_rate"]],
        "proximity_ratio": [None if pd.isna(v) else round(float(v), 3) for v in sampled["proximity_ratio"]],
        "status": list(sampled["posture_status"].fillna("untracked")),
    }


def session_history(db: Database, limit: int = 100) -> List[Dict[str, Any]]:
    """Session rows enriched with alert counts and mean metrics for tables/reports."""
    sessions = db.list_sessions(limit=limit)
    alerts = db.fetch_alerts()
    alert_counts: Dict[str, int] = {}
    for alert in alerts:
        if alert["alert_type"] == "posture_alert":
            alert_counts[alert["session_id"]] = alert_counts.get(alert["session_id"], 0) + 1

    history = []
    for session in sessions:
        frame = telemetry_frame(db, session_ids=[session["session_id"]])
        metrics = summarise_metrics(frame)
        history.append(
            {
                "session_id": session["session_id"],
                "start": session["start_timestamp"],
                "end": session["end_timestamp"],
                "duration_seconds": session.get("duration_seconds"),
                "records": int(len(frame)),
                "avg_forward_lean": metrics["avg_forward_lean"],
                "avg_shoulder_tilt": metrics["avg_shoulder_tilt"],
                "avg_blink_rate": metrics["avg_blink_rate"],
                "tracking_pct": metrics["tracking_pct"],
                "posture_alerts": alert_counts.get(session["session_id"], 0),
                "calibration_id": session.get("calibration_id"),
            }
        )
    return history


def compare_windows(
    db: Database,
    current_since: Optional[str],
    current_until: Optional[str],
    previous_since: Optional[str],
    previous_until: Optional[str],
    **thresholds: float,
) -> Dict[str, Any]:
    """Compare two periods, refusing to report deltas on thin data."""
    current = summarise_metrics(
        telemetry_frame(db, since=current_since, until=current_until), **thresholds
    )
    previous = summarise_metrics(
        telemetry_frame(db, since=previous_since, until=previous_until), **thresholds
    )

    def delta(metric: str) -> Tuple[Optional[float], bool]:
        a, b = current.get(metric), previous.get(metric)
        if a is None or b is None:
            return None, False
        if not (current["sufficient_data"] and previous["sufficient_data"]):
            return None, False
        return round(float(a) - float(b), 2), True

    lean_delta, lean_ok = delta("avg_forward_lean")
    tilt_delta, tilt_ok = delta("avg_shoulder_tilt")
    bpm_delta, bpm_ok = delta("avg_blink_rate")
    score_delta, score_ok = delta("composite_score")
    return {
        "current": current,
        "previous": previous,
        "deltas": {
            "avg_forward_lean": lean_delta,
            "avg_shoulder_tilt": tilt_delta,
            "avg_blink_rate": bpm_delta,
            "composite_score": score_delta,
        },
        "available": {
            "avg_forward_lean": lean_ok,
            "avg_shoulder_tilt": tilt_ok,
            "avg_blink_rate": bpm_ok,
            "composite_score": score_ok,
        },
    }


# --------------------------------------------------------------------------
# chart payloads for the Chart.js component
# --------------------------------------------------------------------------
def chart_payload(
    analysis: Dict[str, Any],
    *,
    lean_threshold: float = DEFAULT_SETTINGS["forward_lean_threshold"],
    tilt_threshold: float = DEFAULT_SETTINGS["shoulder_tilt_threshold"],
    blink_low: float = DEFAULT_SETTINGS["blink_low_bpm"],
    blink_reference: float = DEFAULT_SETTINGS["blink_reference_bpm"],
    animate: bool = True,
) -> Dict[str, Any]:
    """Prepare a JSON-serialisable payload for the analytics chart component."""
    frame = analysis.get("frame")
    series = session_timeseries(frame) if frame is not None else session_timeseries(pd.DataFrame())
    daily = analysis.get("daily")
    daily_payload = {"labels": [], "monitored_minutes": [], "avg_forward_lean": []}
    if daily is not None and not daily.empty:
        daily_payload = {
            "labels": [str(d) for d in daily["date"].tolist()],
            "monitored_minutes": [float(v) for v in daily["monitored_minutes"].tolist()],
            "avg_forward_lean": [
                None if pd.isna(v) else float(v) for v in daily["avg_forward_lean"].tolist()
            ],
        }
    sessions = analysis.get("sessions") or []
    duration_payload = {
        "labels": [
            str(pd.to_datetime(s["start_timestamp"], utc=True, errors="coerce").strftime("%Y-%m-%d %H:%M"))
            if s.get("start_timestamp") else "—"
            for s in reversed(sessions)
        ],
        "minutes": [
            round(float(s.get("duration_seconds") or 0.0) / 60.0, 1) for s in reversed(sessions)
        ],
    }
    return {
        "series": series,
        "daily": daily_payload,
        "durations": duration_payload,
        "distribution": analysis.get("status_distribution", {}),
        "thresholds": {
            "lean": lean_threshold,
            "tilt": tilt_threshold,
            "blinkLow": blink_low,
            "blinkReference": blink_reference,
        },
        "animate": bool(animate),
        "has_data": bool(series["labels"]),
    }
