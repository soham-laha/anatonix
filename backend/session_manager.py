"""Session orchestration between the browser component and SQLite.

The browser owns real-time sampling: it knows when monitoring starts, pauses or
ends and it streams batches of 1 Hz telemetry together with discrete events.
This module is the single place where those payloads become durable rows, so
idempotency (the same component value may be re-delivered by a Streamlit
rerun) and error handling live in one auditable spot.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.database import Database, utcnow_iso
from backend.data_validation import (
    validate_calibration_event,
    validate_component_payload,
    validate_session_id,
    validate_settings,
)
from config import DEFAULT_SETTINGS
from backend.logging_setup import get_logger

log = get_logger("anatonix.session")

EVENT_MESSAGES = {
    "monitoring_started": "Monitoring started",
    "monitoring_paused": "Monitoring paused",
    "monitoring_resumed": "Monitoring resumed",
    "monitoring_stopped": "Monitoring stopped",
    "calibration_completed": "Calibration completed",
    "calibration_reset": "Calibration reset",
    "posture_alert": "Posture warning",
    "break_reminder": "Break reminder",
    "camera_error": "Camera error",
    "tracking_degraded": "Tracking degraded",
    "report_generated": "Report generated",
    "session_recovered": "Session recovered",
    "settings_changed": "Settings changed",
}


@dataclass
class ProcessResult:
    records_inserted: int = 0
    records_rejected: int = 0
    alerts_added: List[Dict[str, Any]] = field(default_factory=list)
    events_processed: int = 0
    calibration_id: Optional[int] = None
    session_ended: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.records_inserted
            or self.alerts_added
            or self.events_processed
            or self.session_ended
        )


class SessionManager:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- lifecycle ---------------------------------------------------------
    def start_session(
        self,
        session_id: Optional[str] = None,
        calibration_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sid = session_id or f"s-{uuid.uuid4().hex[:12]}"
        validate_session_id(sid)
        started = utcnow_iso()
        self.db.ensure_session(sid, started, calibration_id, metadata or {})
        self.db.add_event(sid, "monitoring_started", "Monitoring started", timestamp=started)
        log.info("Session %s started", sid)
        session = self.db.get_session(sid) or {}
        return session

    def end_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self.db.end_session(session_id)
        if session:
            log.info("Session %s ended", session_id)
        return session

    # -- payload processing -------------------------------------------------
    def process_payload(self, payload: Any, calibration_id: Optional[int] = None) -> ProcessResult:
        """Validate, de-duplicate and persist a component payload."""
        result = ProcessResult()
        clean = validate_component_payload(payload)
        result.records_rejected = clean["rejected"]

        try:
            # Sessions referenced by records *and* by alerts must exist first so
            # that the foreign keys on telemetry/alerts stay satisfied.
            referenced = {row["session_id"] for row in clean["records"]}
            referenced |= {
                event["session_id"]
                for event in clean["events"]
                if event.get("session_id") and event["type"] != "calibration_reset"
            }
            for session_id in referenced:
                self.db.ensure_session(session_id)

            for event in clean["events"]:
                self._handle_event(event, result, calibration_id)

            if clean["records"]:
                result.records_inserted = self.db.add_telemetry(clean["records"])
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Failed to persist component payload")
            result.errors.append(str(exc))
        return result

    def _handle_event(
        self,
        event: Dict[str, Any],
        result: ProcessResult,
        calibration_id: Optional[int],
    ) -> None:
        event_type = event["type"]
        session_id = event.get("session_id") or "system"
        timestamp = event.get("timestamp") or utcnow_iso()
        uid = event.get("uid") or f"ev-{uuid.uuid4().hex[:16]}"
        message = event.get("message") or EVENT_MESSAGES.get(event_type, event_type)

        if event_type == "calibration_completed":
            calibration = validate_calibration_event(event)
            if calibration is None:
                result.errors.append("Rejected invalid calibration payload")
                return
            new_id = self.db.add_calibration(
                baseline_pitch=calibration["baseline_pitch"],
                baseline_shoulder_tilt=calibration["baseline_shoulder_tilt"],
                baseline_shoulder_span=calibration["baseline_shoulder_span"],
                quality_score=calibration["quality_score"],
                frames_collected=calibration["frames_collected"],
                notes=message,
            )
            result.calibration_id = new_id
            self.db.add_event(
                session_id if session_id != "system" else None,
                "calibration_completed",
                f"Calibration completed (quality {calibration['quality_score']:.0%})",
                details=calibration,
                timestamp=timestamp,
                client_uid=uid,
            )
            result.events_processed += 1
            return

        if event_type in {"posture_alert", "break_reminder"}:
            alert_id = self.db.add_alert(
                session_id,
                event_type,
                measurement=event.get("measurement"),
                threshold=event.get("threshold"),
                message=message,
                details={
                    "alert_type": event.get("alert_type", event_type),
                    "reason": event.get("message", ""),
                    **{k: v for k, v in event.items() if k in ("lean", "tilt", "bpm")},
                },
                timestamp=timestamp,
                client_uid=uid,
            )
            if alert_id is not None:
                result.alerts_added.append(
                    {
                        "alert_id": alert_id,
                        "type": event_type,
                        "alert_type": event.get("alert_type", event_type),
                        "timestamp": timestamp,
                        "measurement": event.get("measurement"),
                        "threshold": event.get("threshold"),
                        "message": message,
                    }
                )
                self.db.add_event(
                    session_id,
                    event_type,
                    message,
                    details={"alert_type": event.get("alert_type", event_type)},
                    timestamp=timestamp,
                    client_uid=f"{uid}-ev",
                )
                result.events_processed += 1
            return

        if event_type == "monitoring_stopped":
            inserted = self.db.add_event(
                session_id, event_type, message, timestamp=timestamp, client_uid=uid
            )
            if inserted is not None:
                result.events_processed += 1
            self.db.end_session(session_id, timestamp)
            result.session_ended = session_id
            return

        if event_type == "calibration_reset":
            self.db.clear_calibrations()
            inserted = self.db.add_event(
                None, event_type, message, timestamp=timestamp, client_uid=uid
            )
            if inserted is not None:
                result.events_processed += 1
            return

        if event_type == "settings_changed":
            # The live monitoring component can toggle a small set of settings
            # (for example spoken alerts). Persist only known keys, validated
            # and clamped exactly like the Settings page.
            requested = {
                key: value
                for key, value in (event.get("details") or {}).items()
                if key in DEFAULT_SETTINGS
            }
            if requested:
                clean, warnings = validate_settings(requested)
                if clean:
                    self.db.set_settings(clean)
                for warning in warnings:
                    result.errors.append(warning)

        inserted = self.db.add_event(
            session_id if session_id != "system" else None,
            event_type,
            message,
            details=event.get("details") or {},
            timestamp=timestamp,
            client_uid=uid,
        )
        if inserted is not None:
            result.events_processed += 1

    # -- convenience --------------------------------------------------------
    def record_report(self, session_id: Optional[str], kind: str, path: str) -> None:
        self.db.add_event(
            session_id,
            "report_generated",
            f"{kind.upper()} report generated",
            details={"path": path},
        )

    def recover_abandoned(self, grace_seconds: int = 90) -> List[str]:
        return self.db.finalize_stale_sessions(grace_seconds=grace_seconds)

    def summary(self, session_id: str) -> Dict[str, Any]:
        from backend import analytics

        return analytics.session_summary(self.db, session_id)
