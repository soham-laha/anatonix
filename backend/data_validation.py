"""Validation and sanitisation for every value that crosses a trust boundary.

The browser is an untrusted source of telemetry: it can send ``NaN``, missing
fields, impossible angles or oversized payloads.  Everything entering the
Python backend passes through the helpers in this module, which either repair
(coerce/clamp) or reject the value *and say why*.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import POSTURE_STATUSES, SETTING_BOUNDS

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")

MAX_RECORDS_PER_BATCH = 400
MAX_EVENTS_PER_BATCH = 120
ALLOWED_EVENT_TYPES = {
    "monitoring_started",
    "monitoring_paused",
    "monitoring_resumed",
    "monitoring_stopped",
    "calibration_completed",
    "calibration_reset",
    "posture_alert",
    "break_reminder",
    "camera_error",
    "tracking_degraded",
    "report_generated",
    "session_recovered",
    "settings_changed",
}

TELEMETRY_RANGES = {
    # (min, max, nullable)
    "forward_lean": (-90.0, 90.0, True),
    "shoulder_tilt": (-45.0, 45.0, True),
    "ear_value": (0.0, 1.0, True),
    "proximity_ratio": (0.05, 20.0, True),
    "blink_rate": (0.0, 240.0, True),
    "tracking_confidence": (0.0, 1.0, True),
}


class ValidationError(ValueError):
    """Raised when a payload cannot be repaired and must be rejected."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value: Any) -> Optional[float]:
    if not _is_number(value):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def validate_session_id(session_id: Any) -> str:
    if not isinstance(session_id, str):
        raise ValidationError("session_id must be a string")
    candidate = session_id.strip()
    if not SESSION_ID_RE.match(candidate):
        raise ValidationError(f"invalid session_id: {session_id!r}")
    return candidate


def validate_timestamp(value: Any, *, default_now: bool = True) -> Optional[str]:
    """Return a normalised ISO-8601 UTC timestamp or None when unusable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # milliseconds since epoch (JavaScript Date.now()) or seconds.
        seconds = float(value) / (1000.0 if float(value) > 1e11 else 1.0)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).replace(
                microsecond=0
            ).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed.year < 2000 or parsed.year > 2100:
            return None
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    if default_now:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return None


def clamp_setting(key: str, value: Any) -> Any:
    """Coerce a user-supplied setting into its documented type and range."""
    bounds = SETTING_BOUNDS.get(key)
    if bounds is None:
        # Non-numeric settings: passthrough with light type repair.
        if key in {"theme", "density"} and isinstance(value, str):
            allowed = {"dark", "light"} if key == "theme" else {"comfortable", "compact"}
            return value if value in allowed else ("dark" if key == "theme" else "comfortable")
        if key in {"mirror_preview", "chart_animations", "show_neck_load",
                   "audio_enabled", "break_reminders_enabled", "break_audio"}:
            return bool(value)
        if key in {"camera_device_id", "voice_name"}:
            return str(value or "")[:128]
        return value

    number = _finite(value)
    if number is None:
        raise ValidationError(f"{key} must be a finite number")
    low, high = bounds
    return max(low, min(high, number))


def validate_settings(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Validate a settings mapping, returning (clean_values, warnings)."""
    clean: Dict[str, Any] = {}
    warnings: List[str] = []
    for key, value in values.items():
        try:
            clean[key] = clamp_setting(key, value)
        except ValidationError as exc:
            warnings.append(str(exc))
    # Cross-field consistency: reference category must exceed low category.
    low = clean.get("blink_low_bpm")
    reference = clean.get("blink_reference_bpm")
    if low is not None and reference is not None and reference <= low:
        clean["blink_reference_bpm"] = min(40.0, low + 1.0)
        warnings.append("blink_reference_bpm raised above blink_low_bpm")
    return clean, warnings


def validate_telemetry_record(record: Any) -> Optional[Dict[str, Any]]:
    """Validate one browser telemetry record.

    Returns a cleaned record, or ``None`` when the record is structurally
    unusable.  Individual out-of-range metrics become ``None`` (stored as SQL
    NULL) rather than fabricated values.
    """
    if not isinstance(record, dict):
        return None
    try:
        session_id = validate_session_id(record.get("session_id"))
    except ValidationError:
        return None
    timestamp = validate_timestamp(record.get("timestamp"), default_now=False)
    if timestamp is None:
        return None

    clean: Dict[str, Any] = {"session_id": session_id, "timestamp": timestamp}
    for field, (low, high, nullable) in TELEMETRY_RANGES.items():
        raw = record.get(field)
        number = _finite(raw)
        if number is None:
            if not nullable:
                return None
            clean[field] = None
            continue
        clean[field] = round(max(low, min(high, number)), 4)

    status = record.get("posture_status")
    clean["posture_status"] = status if status in POSTURE_STATUSES else "untracked"

    # A record with no posture metric at all is not worth persisting unless it
    # still carries ocular data (e.g. shoulders out of frame but face visible).
    if all(clean[f] is None for f in ("forward_lean", "shoulder_tilt", "ear_value")):
        return None
    return clean


def validate_telemetry_batch(records: Any) -> Tuple[List[Dict[str, Any]], int]:
    if not isinstance(records, (list, tuple)):
        return [], 0
    cleaned: List[Dict[str, Any]] = []
    rejected = 0
    for record in list(records)[:MAX_RECORDS_PER_BATCH]:
        result = validate_telemetry_record(record)
        if result is None:
            rejected += 1
        else:
            cleaned.append(result)
    # De-duplicate within the batch (same session + timestamp).
    unique: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in cleaned:
        unique[(row["session_id"], row["timestamp"])] = row
    rejected += len(cleaned) - len(unique)
    return list(unique.values()), rejected


def validate_event(event: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(event, dict):
        return None
    event_type = event.get("type") or event.get("event_type")
    if event_type not in ALLOWED_EVENT_TYPES:
        return None
    session_id = event.get("session_id")
    clean_session = None
    if session_id is not None:
        try:
            clean_session = validate_session_id(session_id)
        except ValidationError:
            clean_session = None
    raw_uid = event.get("uid") or event.get("client_uid")
    uid = None
    if isinstance(raw_uid, str) and 1 <= len(raw_uid) <= 64 and re.match(r"^[A-Za-z0-9_-]+$", raw_uid):
        uid = raw_uid
    payload = {
        "type": event_type,
        "uid": uid,
        "session_id": clean_session,
        "timestamp": validate_timestamp(event.get("timestamp")),
        "message": str(event.get("message") or "")[:400],
        "details": event.get("details") if isinstance(event.get("details"), dict) else {},
    }
    for numeric in ("measurement", "threshold", "quality", "frames"):
        value = _finite(event.get(numeric))
        if value is not None:
            payload[numeric] = value
    if event.get("alert_type"):
        payload["alert_type"] = str(event["alert_type"])[:48]
    if event.get("baseline") and isinstance(event["baseline"], dict):
        payload["baseline"] = event["baseline"]
    return payload


def validate_events(events: Any) -> List[Dict[str, Any]]:
    if not isinstance(events, (list, tuple)):
        return []
    out = []
    for event in list(events)[:MAX_EVENTS_PER_BATCH]:
        cleaned = validate_event(event)
        if cleaned is not None:
            out.append(cleaned)
    return out


def validate_component_payload(payload: Any) -> Dict[str, Any]:
    """Normalise the whole component value into a predictable structure."""
    if not isinstance(payload, dict):
        return {"seq": 0, "records": [], "events": [], "rejected": 0, "live": {}}
    records, rejected = validate_telemetry_batch(payload.get("records"))
    events = validate_events(payload.get("events"))
    try:
        seq = int(payload.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    live = payload.get("live") if isinstance(payload.get("live"), dict) else {}
    live_clean = {}
    for key in (
        "state", "fps", "confidence", "forward_lean", "shoulder_tilt",
        "ear_value", "blink_rate", "blinks_total", "proximity_ratio",
        "posture_status", "eye_state", "session_active", "calibration_quality",
        "quality_issues",
    ):
        if key in live:
            value = live[key]
            if _is_number(value):
                number = _finite(value)
                live_clean[key] = number if number is not None else None
            elif isinstance(value, (str, bool)) or value is None:
                live_clean[key] = value
            elif isinstance(value, (list, tuple)):
                live_clean[key] = [str(v)[:120] for v in list(value)[:6]]
    return {"seq": seq, "records": records, "events": events, "rejected": rejected, "live": live_clean}


def validate_calibration_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    baseline = event.get("baseline") or {}
    pitch = _finite(baseline.get("pitch"))
    tilt = _finite(baseline.get("shoulderTilt"))
    span = _finite(baseline.get("shoulderSpan"))
    quality = _finite(event.get("quality"))
    if None in (pitch, tilt, span, quality):
        return None
    if span <= 0.001:
        return None
    if not -90 <= pitch <= 90 or not -45 <= tilt <= 45:
        return None
    if not 0 <= quality <= 1:
        return None
    return {
        "baseline_pitch": round(pitch, 3),
        "baseline_shoulder_tilt": round(tilt, 3),
        "baseline_shoulder_span": round(span, 5),
        "quality_score": round(quality, 3),
        "frames_collected": int(_finite(event.get("frames")) or 0),
    }


def sanitise_text(value: Any, limit: int = 200) -> str:
    """Strip control characters and trim user-provided strings for display."""
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch for ch in value if ch.isprintable() or ch == " ")
    return cleaned.strip()[:limit]


def validate_date_range(start: Any, end: Any) -> Tuple[Optional[str], Optional[str], List[str]]:
    warnings: List[str] = []
    start_iso = validate_timestamp(start, default_now=False) if start else None
    end_iso = validate_timestamp(end, default_now=False) if end else None
    if start_iso and end_iso and start_iso > end_iso:
        start_iso, end_iso = end_iso, start_iso
        warnings.append("Date range was reversed and has been corrected.")
    return start_iso, end_iso, warnings


def iter_chunks(items: Iterable[Any], size: int = 500) -> Iterable[List[Any]]:
    chunk: List[Any] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
