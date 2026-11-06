"""Shared pytest fixtures for the Anatonix test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def db(tmp_path):
    from backend.database import Database

    return Database(tmp_path / "test_anatonix.db")


@pytest.fixture()
def seeded_session(db):
    """A session with 120 seconds of plausible telemetry and two alerts."""
    from datetime import timedelta

    from backend.database import utcnow

    start = utcnow() - timedelta(minutes=5)
    session_id = "s-test-session-0001"
    db.create_session(session_id, start.isoformat())
    rows = []
    for second in range(120):
        timestamp = (start + timedelta(seconds=second)).replace(microsecond=0).isoformat()
        rows.append(
            {
                "session_id": session_id,
                "timestamp": timestamp,
                "forward_lean": 4.0 + (second % 30) * 0.5,
                "shoulder_tilt": 1.0 + (second % 10) * 0.3,
                "ear_value": 0.28 if second % 40 else 0.12,
                "proximity_ratio": 1.0,
                "blink_rate": 14.0,
                "tracking_confidence": 0.9,
                "posture_status": "elevated" if second % 30 > 20 else "good",
            }
        )
    db.add_telemetry(rows)
    db.add_alert(session_id, "posture_alert", 18.4, 12.0, "Forward lean detected")
    db.add_alert(session_id, "break_reminder", None, None, "Consider a short break")
    db.end_session(session_id, (start + timedelta(minutes=2)).isoformat())
    return session_id
