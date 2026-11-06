"""Report generation tests (CSV + PDF) and session-manager integration."""

from __future__ import annotations

import csv
import io

import pytest

from backend import report_generator as rg
from backend.database import utcnow
from backend.session_manager import SessionManager


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def test_csv_contains_header_and_rows(db, seeded_session, tmp_path):
    data = rg.build_csv(db, seeded_session).decode("utf-8")
    rows = list(csv.reader(io.StringIO(data)))
    assert rows[0] == [header for _, header in rg.CSV_COLUMNS]
    assert len(rows) == 121  # header + 120 samples
    assert rows[1][1] != ""  # forward lean populated


def test_csv_export_writes_timestamped_file(db, seeded_session, tmp_path):
    path = rg.export_csv(db, seeded_session, out_dir=tmp_path)
    assert path.exists()
    assert path.name.startswith("anatonix_telemetry_")
    assert path.suffix == ".csv"


def test_csv_handles_session_without_data(db, tmp_path):
    session_id = "s-empty0000001"
    db.create_session(session_id)
    path = rg.export_csv(db, session_id, out_dir=tmp_path)
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert len(rows) == 1  # header only


def test_range_csv_export(db, seeded_session, tmp_path):
    path = rg.export_range_csv(db, None, utcnow().isoformat(), out_dir=tmp_path)
    assert path.exists()
    assert "anatonix_telemetry_range_" in path.name


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def test_pdf_generation(db, seeded_session, tmp_path):
    path = rg.build_pdf(db, seeded_session, out_dir=tmp_path)
    assert path.exists()
    payload = path.read_bytes()
    assert payload[:4] == b"%PDF"
    assert len(payload) > 5000


def test_pdf_for_empty_session(db, tmp_path):
    session_id = "s-empty0000002"
    db.create_session(session_id)
    db.end_session(session_id)
    path = rg.build_pdf(db, session_id, out_dir=tmp_path)
    assert path.exists() and path.read_bytes()[:4] == b"%PDF"


def test_cleanup_exports_keeps_newest(db, seeded_session, tmp_path):
    for _ in range(5):
        rg.export_csv(db, seeded_session, out_dir=tmp_path)
    removed = rg.cleanup_exports(max_files=2, out_dir=tmp_path)
    assert removed == 3
    assert len(list(tmp_path.glob("*.csv"))) == 2


# --------------------------------------------------------------------------
# Session manager
# --------------------------------------------------------------------------
def _payload(session_id: str, n: int = 3, base_ts: str | None = None):
    from datetime import timedelta

    from backend.database import parse_iso

    start = parse_iso(base_ts) or utcnow()
    return {
        "seq": n,
        "records": [
            {
                "session_id": session_id,
                "timestamp": (start + timedelta(seconds=i)).isoformat(),
                "forward_lean": 8.0 + i,
                "shoulder_tilt": 1.0,
                "ear_value": 0.28,
                "proximity_ratio": 1.0,
                "blink_rate": 13.0,
                "tracking_confidence": 0.9,
                "posture_status": "good",
            }
            for i in range(n)
        ],
        "events": [],
    }


def test_process_payload_creates_session_and_records(db):
    manager = SessionManager(db)
    session_id = "s-manager000001"
    result = manager.process_payload(_payload(session_id))
    assert result.records_inserted == 3
    assert db.get_session(session_id) is not None
    assert db.telemetry_count(session_id) == 3


def test_process_payload_is_idempotent(db):
    manager = SessionManager(db)
    session_id = "s-manager000002"
    payload = _payload(session_id)
    manager.process_payload(payload)
    again = manager.process_payload(payload)
    assert again.records_inserted == 0
    assert db.telemetry_count(session_id) == 3


def test_posture_alert_is_persisted_once(db):
    manager = SessionManager(db)
    session_id = "s-manager000003"
    payload = _payload(session_id, n=1)
    payload["events"] = [{
        "type": "posture_alert",
        "session_id": session_id,
        "uid": "ev-posture-1",
        "alert_type": "forward_lean",
        "measurement": 19.4,
        "threshold": 12.0,
        "message": "Forward lean detected",
    }]
    first = manager.process_payload(payload)
    second = manager.process_payload(payload)
    assert len(first.alerts_added) == 1
    assert second.alerts_added == []
    alerts = db.fetch_alerts([session_id])
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "posture_alert"


def test_calibration_event_stores_baseline(db):
    manager = SessionManager(db)
    session_id = "s-manager000004"
    payload = _payload(session_id, n=1)
    payload["events"] = [{
        "type": "calibration_completed",
        "session_id": session_id,
        "uid": "ev-cal-1",
        "baseline": {"pitch": 10.5, "shoulderTilt": 1.1, "shoulderSpan": 0.21},
        "quality": 0.84,
        "frames": 40,
    }]
    result = manager.process_payload(payload)
    assert result.calibration_id is not None
    latest = db.latest_calibration()
    assert latest["baseline_pitch"] == pytest.approx(10.5)


def test_invalid_calibration_event_is_rejected(db):
    manager = SessionManager(db)
    payload = _payload("s-manager000005", n=1)
    payload["events"] = [{
        "type": "calibration_completed",
        "uid": "ev-cal-bad",
        "baseline": {"pitch": 10.5, "shoulderTilt": 1.1, "shoulderSpan": 0},
        "quality": 0.84,
        "frames": 40,
    }]
    result = manager.process_payload(payload)
    assert result.calibration_id is None
    assert db.latest_calibration() is None


def test_monitoring_stopped_closes_session(db):
    manager = SessionManager(db)
    session_id = "s-manager000006"
    payload = _payload(session_id, n=1)
    payload["events"] = [{"type": "monitoring_stopped", "session_id": session_id, "uid": "ev-stop-1"}]
    result = manager.process_payload(payload)
    assert result.session_ended == session_id
    assert db.get_session(session_id)["end_timestamp"] is not None


def test_break_reminder_recorded(db):
    manager = SessionManager(db)
    session_id = "s-manager000007"
    payload = _payload(session_id, n=1)
    payload["events"] = [{
        "type": "break_reminder",
        "session_id": session_id,
        "uid": "ev-break-1",
        "message": "Consider taking a short movement break.",
    }]
    manager.process_payload(payload)
    alerts = db.fetch_alerts([session_id])
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "break_reminder"


def test_garbage_payload_does_not_raise(db):
    manager = SessionManager(db)
    result = manager.process_payload({"records": "nonsense", "events": [None, 42]})
    assert result.records_inserted == 0
    assert result.errors == []


def test_start_and_end_session_api(db):
    manager = SessionManager(db)
    session = manager.start_session(metadata={"note": "manual"})
    session_id = session["session_id"]
    assert session["start_timestamp"]
    ended = manager.end_session(session_id)
    assert ended["end_timestamp"] is not None
    events = [e["event_type"] for e in db.fetch_events(10)]
    assert "monitoring_started" in events
