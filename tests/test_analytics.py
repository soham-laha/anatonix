"""Analytics tests: aggregation, empty states, comparison guards, composite score."""

from __future__ import annotations

import pandas as pd
import pytest

from backend import analytics
from backend.database import utcnow


def test_summarise_metrics_from_seeded_session(db, seeded_session):
    frame = analytics.telemetry_frame(db, session_ids=[seeded_session])
    metrics = analytics.summarise_metrics(frame, lean_threshold=12.0, tilt_threshold=6.0)
    assert metrics["samples"] == 120
    assert metrics["valid_samples"] == 120
    assert metrics["avg_forward_lean"] == pytest.approx(frame["forward_lean"].mean(), abs=0.01)
    assert metrics["sufficient_data"] is True
    assert 0 <= metrics["composite_score"] <= 100


def test_empty_frame_yields_no_metrics():
    metrics = analytics.summarise_metrics(pd.DataFrame())
    assert metrics["samples"] == 0
    assert metrics["avg_forward_lean"] is None
    assert metrics["composite_score"] is None
    assert metrics["sufficient_data"] is False
    assert metrics["tracking_pct"] is None


def test_null_metrics_do_not_become_zero(db):
    session_id = "s-nulls0000002"
    db.create_session(session_id)
    from datetime import timedelta

    start = utcnow() - timedelta(seconds=100)
    db.add_telemetry([
        {
            "session_id": session_id,
            "timestamp": (start + timedelta(seconds=i)).isoformat(),
            "forward_lean": None,
            "shoulder_tilt": None,
            "ear_value": None,
            "proximity_ratio": None,
            "blink_rate": None,
            "tracking_confidence": 0.1,
            "posture_status": "untracked",
        }
        for i in range(50)
    ])
    metrics = analytics.summarise_metrics(analytics.telemetry_frame(db, [session_id]))
    assert metrics["samples"] == 50
    assert metrics["valid_samples"] == 0
    assert metrics["avg_forward_lean"] is None
    assert metrics["tracking_pct"] == 0.0
    assert metrics["composite_score"] is None


def test_valid_tracking_mask_requires_confidence(db, seeded_session):
    frame = analytics.telemetry_frame(db, [seeded_session])
    assert analytics.valid_tracking_mask(frame).all()
    frame.loc[0, "tracking_confidence"] = 0.1
    assert not analytics.valid_tracking_mask(frame).iloc[0]


def test_time_outside_threshold(db, seeded_session):
    frame = analytics.telemetry_frame(db, [seeded_session])
    metrics = analytics.summarise_metrics(frame, lean_threshold=5.0, tilt_threshold=6.0)
    expected = int((frame["forward_lean"] > 5.0).sum())
    assert metrics["seconds_outside_threshold"] == expected
    assert 0 < metrics["pct_time_outside_threshold"] <= 100


def test_blink_categories():
    assert analytics.blink_category(5.0)[0] == "low"
    assert analytics.blink_category(9.0)[0] == "reduced"
    assert analytics.blink_category(15.0)[0] == "reference"
    assert analytics.blink_category(None)[0] == "unknown"


def test_composite_score_components():
    base = {
        "sufficient_data": True,
        "avg_forward_lean": 0.0,
        "avg_shoulder_tilt": 0.0,
        "pct_time_outside_threshold": 0.0,
        "avg_blink_rate": 12.0,
    }
    assert analytics.composite_score(base) == pytest.approx(100.0)
    worse = dict(base, avg_forward_lean=20.0, pct_time_outside_threshold=50.0, avg_blink_rate=4.0)
    assert analytics.composite_score(worse) < analytics.composite_score(base)
    assert analytics.composite_score(dict(base, sufficient_data=False)) is None


def test_session_summary_duration_and_alerts(db, seeded_session):
    summary = analytics.session_summary(db, seeded_session)
    assert summary["session"]["session_id"] == seeded_session
    assert summary["metrics"]["session_duration_seconds"] == pytest.approx(120.0, abs=0.5)
    assert summary["metrics"]["posture_alert_count"] == 1


def test_analyse_range_7d_includes_session(db, seeded_session):
    since, until, label = analytics.range_bounds("7d")
    analysis = analytics.analyse_range(db, since, until)
    assert label == "Last 7 days"
    assert analysis["metrics"]["sessions"] == 1
    assert analysis["metrics"]["samples"] == 120
    assert analysis["metrics"]["posture_alert_count"] == 1
    assert set(analysis["status_distribution"]) == {"good", "elevated", "alert", "untracked"}


def test_analyse_range_empty_window(db):
    since, until, _ = analytics.range_bounds("7d")
    analysis = analytics.analyse_range(db, since, until)
    assert analysis["metrics"]["samples"] == 0
    assert analysis["metrics"]["avg_forward_lean"] is None
    assert analysis["daily"].empty


def test_daily_consistency(db, seeded_session):
    frame = analytics.telemetry_frame(db, [seeded_session])
    daily = analytics.daily_consistency(frame)
    assert len(daily) == 1
    assert daily.iloc[0]["monitored_minutes"] == pytest.approx(2.0, abs=0.1)


def test_session_timeseries_downsamples(db, seeded_session):
    frame = analytics.telemetry_frame(db, [seeded_session])
    series = analytics.session_timeseries(frame, max_points=10)
    assert len(series["labels"]) <= 12
    assert len(series["labels"]) == len(series["forward_lean"])
    assert series["labels"] == sorted(series["labels"])


def test_compare_windows_requires_sufficient_data(db, seeded_session):
    result = analytics.compare_windows(db, None, None, None, None)
    assert result["current"]["sufficient_data"] is True
    assert result["deltas"]["avg_forward_lean"] == pytest.approx(0.0)

    # No data in the "previous" window -> deltas must be withheld.
    empty = analytics.compare_windows(
        db,
        None,
        None,
        "2020-01-01T00:00:00+00:00",
        "2020-01-02T00:00:00+00:00",
    )
    assert empty["previous"]["sufficient_data"] is False
    assert empty["deltas"]["avg_forward_lean"] is None
    assert empty["available"]["avg_forward_lean"] is False


def test_chart_payload_shape(db, seeded_session):
    since, until, _ = analytics.range_bounds("7d")
    analysis = analytics.analyse_range(db, since, until)
    payload = analytics.chart_payload(analysis, animate=False)
    assert payload["has_data"] is True
    assert len(payload["series"]["labels"]) == len(payload["series"]["forward_lean"])
    assert payload["thresholds"]["lean"] == 12.0
    assert payload["durations"]["minutes"][0] == pytest.approx(2.0, abs=0.2)


def test_session_history_enriches_rows(db, seeded_session):
    history = analytics.session_history(db)
    assert len(history) == 1
    row = history[0]
    assert row["records"] == 120
    assert row["posture_alerts"] == 1
    assert row["avg_forward_lean"] is not None


def test_range_bounds_all_and_custom():
    since, _, label = analytics.range_bounds("all")
    assert since is None and label == "All recorded data"
    start, end, _ = analytics.range_bounds("custom", custom=("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"))
    assert start.startswith("2026-01-01") and end.startswith("2026-02-01")
