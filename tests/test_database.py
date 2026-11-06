"""Persistence-layer tests: schema, idempotency, cascade deletes, settings."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.database import Database, utcnow


def test_schema_contains_required_tables(db):
    tables = set(db.table_names())
    assert {"sessions", "telemetry", "alerts", "calibrations", "settings"} <= tables


def test_create_and_read_session(db):
    session_id = "s-abcdef123456"
    db.create_session(session_id, "2026-01-01T10:00:00+00:00", metadata={"camera": "builtin"})
    session = db.get_session(session_id)
    assert session["session_id"] == session_id
    assert session["metadata"] == {"camera": "builtin"}
    assert session["end_timestamp"] is None


def test_ensure_session_is_idempotent(db):
    db.ensure_session("s-abcdef123456")
    db.ensure_session("s-abcdef123456")
    assert len(db.list_sessions()) == 1


def test_end_session_sets_duration(db):
    session_id = "s-duration00001"
    db.create_session(session_id, "2026-01-01T10:00:00+00:00")
    session = db.end_session(session_id, "2026-01-01T10:05:30+00:00")
    assert session["duration_seconds"] == pytest.approx(330.0)

    # Idempotent: a second close must not overwrite the original end time.
    again = db.end_session(session_id, "2026-01-01T11:00:00+00:00")
    assert again["duration_seconds"] == pytest.approx(330.0)


def test_telemetry_deduplication(db, seeded_session):
    before = db.telemetry_count(seeded_session)
    ts = db.fetch_telemetry([seeded_session])[0]["timestamp"]
    inserted = db.add_telemetry(
        [{
            "session_id": seeded_session,
            "timestamp": ts,
            "forward_lean": 99.0,
            "shoulder_tilt": 1.0,
            "ear_value": 0.3,
            "proximity_ratio": 1.0,
            "blink_rate": 12.0,
            "tracking_confidence": 0.9,
            "posture_status": "good",
        }]
    )
    assert inserted == 0
    assert db.telemetry_count(seeded_session) == before


def test_nulls_are_preserved_not_invented(db):
    session_id = "s-nulls0000001"
    db.create_session(session_id)
    db.add_telemetry([{
        "session_id": session_id,
        "timestamp": utcnow().isoformat(),
        "forward_lean": None,
        "shoulder_tilt": None,
        "ear_value": 0.27,
        "proximity_ratio": None,
        "blink_rate": None,
        "tracking_confidence": 0.4,
        "posture_status": "untracked",
    }])
    row = db.fetch_telemetry([session_id])[0]
    assert row["forward_lean"] is None
    assert row["shoulder_tilt"] is None
    assert row["ear_value"] == pytest.approx(0.27)


def test_alert_and_event_client_uid_dedup(db):
    session_id = "s-dedup0000001"
    db.create_session(session_id)
    first = db.add_alert(session_id, "posture_alert", 15.0, 12.0, "lean", client_uid="uid-1")
    second = db.add_alert(session_id, "posture_alert", 15.0, 12.0, "lean", client_uid="uid-1")
    assert first is not None and second is None

    e1 = db.add_event(session_id, "monitoring_started", client_uid="ev-1")
    e2 = db.add_event(session_id, "monitoring_started", client_uid="ev-1")
    assert e1 is not None and e2 is None


def test_cascade_delete_removes_dependent_rows(db, seeded_session):
    assert db.telemetry_count(seeded_session) > 0
    assert db.fetch_alerts([seeded_session])
    assert db.delete_session(seeded_session) is True
    assert db.get_session(seeded_session) is None
    assert db.telemetry_count(seeded_session) == 0
    assert db.fetch_alerts([seeded_session]) == []


def test_settings_roundtrip_and_defaults(db):
    settings = db.get_settings()
    assert settings["forward_lean_threshold"] == 12.0
    db.set_setting("forward_lean_threshold", 15.5)
    db.set_settings({"theme": "light", "audio_enabled": True})
    stored = db.get_settings()
    assert stored["forward_lean_threshold"] == 15.5
    assert stored["theme"] == "light"
    assert stored["audio_enabled"] is True

    db.reset_settings()
    assert db.get_settings()["forward_lean_threshold"] == 12.0


def test_calibration_storage(db):
    calibration_id = db.add_calibration(11.2, 1.4, 0.21, 0.86, 45)
    latest = db.latest_calibration()
    assert latest["calibration_id"] == calibration_id
    assert latest["quality_score"] == pytest.approx(0.86)
    db.clear_calibrations()
    assert db.latest_calibration() is None


def test_query_filters_and_ordering(db, seeded_session):
    sessions = db.list_sessions(since=(utcnow() - timedelta(days=1)).isoformat())
    assert [s["session_id"] for s in sessions] == [seeded_session]

    future = db.list_sessions(since=(utcnow() + timedelta(days=1)).isoformat())
    assert future == []

    window = db.fetch_telemetry(since=(utcnow() - timedelta(minutes=10)).isoformat())
    assert window and all(row["session_id"] == seeded_session for row in window)


def test_finalize_stale_sessions(db):
    session_id = "s-stale0000001"
    old_start = (utcnow() - timedelta(hours=2)).replace(microsecond=0).isoformat()
    db.create_session(session_id, old_start)
    db.add_telemetry([{
        "session_id": session_id,
        "timestamp": old_start,
        "forward_lean": 5.0,
        "shoulder_tilt": 1.0,
        "ear_value": 0.28,
        "proximity_ratio": 1.0,
        "blink_rate": 12.0,
        "tracking_confidence": 0.9,
        "posture_status": "good",
    }])
    closed = db.finalize_stale_sessions(grace_seconds=60)
    assert session_id in closed
    assert db.get_session(session_id)["end_timestamp"] is not None


def test_storage_stats(db, seeded_session):
    stats = db.storage_stats()
    assert stats["sessions"] == 1
    assert stats["telemetry_records"] == 120
    assert stats["alerts"] == 2


def test_transaction_rollback_on_error(db):
    session_id = "s-rollback0001"
    db.create_session(session_id)
    with pytest.raises(RuntimeError):
        with db.connection() as conn:
            conn.execute("INSERT INTO events (session_id, timestamp, event_type) VALUES (?,?,?)",
                         (session_id, utcnow().isoformat(), "monitoring_started"))
            raise RuntimeError("boom")
    assert db.fetch_events() == []


def test_parameterised_queries_reject_injection(db):
    session_id = "s-inject0000001"
    db.create_session(session_id)
    evil = "x'; DROP TABLE sessions;--"
    assert db.get_session(evil) is None
    assert "sessions" in db.table_names()


def test_schema_is_recreated_if_the_database_file_disappears(tmp_path):
    """Deleting data/anatonix.db while the app runs must not break the session."""
    from backend.database import Database

    db = Database(tmp_path / "live.db")
    db.init_schema()
    db.set_settings({"forward_lean_threshold": 15.0})
    assert db.get_settings()["forward_lean_threshold"] == 15.0

    # Simulate the file being removed behind the app's back.
    (tmp_path / "live.db").unlink()
    for leftover in tmp_path.glob("live.db-*"):
        leftover.unlink()

    # The next operation transparently rebuilds the schema.
    assert db.table_names(), "schema should be recreated on demand"
    assert db.get_settings()["forward_lean_threshold"] == 12.0  # defaults restored
