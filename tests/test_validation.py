"""Validation / sanitisation tests, including hostile payloads."""

from __future__ import annotations

import math

import pytest

from backend import data_validation as dv


class TestSessionIds:
    def test_accepts_uuid_like_ids(self):
        assert dv.validate_session_id("s-abcdef123456") == "s-abcdef123456"
        assert dv.validate_session_id("A1b2C3d4E5f6") == "A1b2C3d4E5f6"

    @pytest.mark.parametrize("bad", ["", "short", "has space here", "semi;colon12345", None, 12345])
    def test_rejects_bad_ids(self, bad):
        with pytest.raises(dv.ValidationError):
            dv.validate_session_id(bad)


class TestTimestamps:
    def test_iso_with_z(self):
        assert dv.validate_timestamp("2026-05-01T10:00:00Z") == "2026-05-01T10:00:00+00:00"

    def test_naive_is_treated_as_utc(self):
        assert dv.validate_timestamp("2026-05-01T10:00:00") == "2026-05-01T10:00:00+00:00"

    def test_epoch_milliseconds(self):
        assert dv.validate_timestamp(1_767_000_000_000) == "2025-12-29T09:20:00+00:00"

    def test_garbage_is_rejected(self):
        assert dv.validate_timestamp("not-a-date", default_now=False) is None
        assert dv.validate_timestamp("1024-01-01T00:00:00Z", default_now=False) is None


class TestTelemetryRecord:
    def test_valid_record_passes(self):
        record = {
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "forward_lean": 12.345,
            "shoulder_tilt": 2.0,
            "ear_value": 0.28,
            "proximity_ratio": 1.02,
            "blink_rate": 14.0,
            "tracking_confidence": 0.88,
            "posture_status": "good",
        }
        clean = dv.validate_telemetry_record(record)
        assert clean["forward_lean"] == pytest.approx(12.345)
        assert clean["posture_status"] == "good"

    def test_missing_metrics_become_none(self):
        record = {
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "forward_lean": None,
            "shoulder_tilt": None,
            "ear_value": 0.3,
            "posture_status": "untracked",
        }
        clean = dv.validate_telemetry_record(record)
        assert clean["forward_lean"] is None
        assert clean["ear_value"] == pytest.approx(0.3)

    def test_nan_and_infinity_are_nulled(self):
        record = {
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "forward_lean": float("nan"),
            "shoulder_tilt": float("inf"),
            "ear_value": 0.3,
            "posture_status": "good",
        }
        clean = dv.validate_telemetry_record(record)
        assert clean["forward_lean"] is None
        assert clean["shoulder_tilt"] is None

    def test_out_of_range_is_clamped(self):
        record = {
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "forward_lean": 5000.0,
            "shoulder_tilt": -400.0,
            "ear_value": 0.3,
            "posture_status": "good",
        }
        clean = dv.validate_telemetry_record(record)
        assert clean["forward_lean"] == 90.0
        assert clean["shoulder_tilt"] == -45.0

    def test_unknown_status_becomes_untracked(self):
        record = {
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "ear_value": 0.3,
            "posture_status": "<script>alert(1)</script>",
        }
        assert dv.validate_telemetry_record(record)["posture_status"] == "untracked"

    def test_record_without_any_metric_is_dropped(self):
        record = {
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "forward_lean": None,
            "shoulder_tilt": None,
            "ear_value": None,
        }
        assert dv.validate_telemetry_record(record) is None

    def test_strings_are_not_coerced_into_numbers(self):
        record = {
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "forward_lean": "12.0",
            "ear_value": 0.3,
        }
        assert dv.validate_telemetry_record(record)["forward_lean"] is None

    def test_invalid_session_drops_record(self):
        record = {
            "session_id": "nope",
            "timestamp": "2026-05-01T10:00:00Z",
            "ear_value": 0.3,
        }
        assert dv.validate_telemetry_record(record) is None


class TestBatches:
    def test_batch_deduplicates_and_counts_rejects(self):
        base = {
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "forward_lean": 5.0,
            "ear_value": 0.3,
            "posture_status": "good",
        }
        records = [base, dict(base), {"bad": True}, "not-a-record"]
        clean, rejected = dv.validate_telemetry_batch(records)
        assert len(clean) == 1
        assert rejected == 3

    def test_batch_size_is_capped(self):
        records = [
            {
                "session_id": "s-abcdef123456",
                "timestamp": f"2026-05-01T10:{i // 60:02d}:{i % 60:02d}Z",
                "forward_lean": 5.0,
                "ear_value": 0.3,
            }
            for i in range(600)
        ]
        clean, _ = dv.validate_telemetry_batch(records)
        assert len(clean) == dv.MAX_RECORDS_PER_BATCH

    def test_non_list_payload(self):
        assert dv.validate_telemetry_batch({"records": "nope"}) == ([], 0)


class TestEvents:
    def test_known_event_is_cleaned(self):
        event = dv.validate_event({
            "type": "posture_alert",
            "session_id": "s-abcdef123456",
            "timestamp": "2026-05-01T10:00:00Z",
            "message": "Forward lean detected",
            "measurement": 18.2,
            "threshold": 12.0,
            "alert_type": "forward_lean",
            "uid": "ev-123456",
        })
        assert event["type"] == "posture_alert"
        assert event["uid"] == "ev-123456"
        assert event["measurement"] == pytest.approx(18.2)

    def test_unknown_event_type_is_dropped(self):
        assert dv.validate_event({"type": "drop_tables"}) is None

    def test_long_message_is_truncated(self):
        event = dv.validate_event({"type": "camera_error", "message": "x" * 5000})
        assert len(event["message"]) == 400

    def test_invalid_uid_is_dropped(self):
        event = dv.validate_event({"type": "camera_error", "uid": "bad uid!!"})
        assert event["uid"] is None


class TestComponentPayload:
    def test_full_payload(self):
        payload = {
            "seq": "12",
            "records": [{
                "session_id": "s-abcdef123456",
                "timestamp": "2026-05-01T10:00:00Z",
                "forward_lean": 9.0,
                "ear_value": 0.3,
                "posture_status": "good",
            }],
            "events": [{"type": "monitoring_started", "session_id": "s-abcdef123456"}],
            "live": {"state": "tracking", "fps": 29.5, "quality_issues": ["a", "b", "c", "d", "e", "f", "g"]},
        }
        clean = dv.validate_component_payload(payload)
        assert clean["seq"] == 12
        assert len(clean["records"]) == 1
        assert len(clean["events"]) == 1
        assert len(clean["live"]["quality_issues"]) == 6

    def test_none_payload(self):
        clean = dv.validate_component_payload(None)
        assert clean["records"] == [] and clean["events"] == []

    def test_live_nan_is_nulled(self):
        clean = dv.validate_component_payload({"live": {"fps": math.nan}})
        assert clean["live"]["fps"] is None


class TestSettingsValidation:
    def test_clamping(self):
        clean, _ = dv.validate_settings({"forward_lean_threshold": 999, "ear_threshold": 0.0001})
        assert clean["forward_lean_threshold"] == 45.0
        assert clean["ear_threshold"] == 0.05

    def test_bad_type_is_reported_not_raised(self):
        clean, warnings = dv.validate_settings({"forward_lean_threshold": "abc"})
        assert "forward_lean_threshold" not in clean
        assert warnings

    def test_cross_field_consistency(self):
        clean, warnings = dv.validate_settings({"blink_low_bpm": 10.0, "blink_reference_bpm": 9.0})
        assert clean["blink_reference_bpm"] > clean["blink_low_bpm"]
        assert warnings

    def test_theme_and_density_enums(self):
        clean, _ = dv.validate_settings({"theme": "neon", "density": "spacious"})
        assert clean["theme"] == "dark"
        assert clean["density"] == "comfortable"


class TestCalibrationPayload:
    def test_valid(self):
        clean = dv.validate_calibration_event({
            "baseline": {"pitch": 11.5, "shoulderTilt": 1.2, "shoulderSpan": 0.22},
            "quality": 0.88,
            "frames": 45,
        })
        assert clean["baseline_pitch"] == pytest.approx(11.5)
        assert clean["frames_collected"] == 45

    def test_zero_span_rejected(self):
        assert dv.validate_calibration_event({
            "baseline": {"pitch": 11.5, "shoulderTilt": 1.2, "shoulderSpan": 0.0},
            "quality": 0.8,
            "frames": 30,
        }) is None

    def test_out_of_range_quality_rejected(self):
        assert dv.validate_calibration_event({
            "baseline": {"pitch": 11.5, "shoulderTilt": 1.2, "shoulderSpan": 0.2},
            "quality": 1.7,
            "frames": 30,
        }) is None


class TestMisc:
    def test_sanitise_text_strips_control_characters(self):
        assert dv.sanitise_text("clean\x00 text\x07", 20) == "clean text"

    def test_date_range_is_ordered(self):
        start, end, warnings = dv.validate_date_range("2026-05-02T00:00:00Z", "2026-05-01T00:00:00Z")
        assert start < end and warnings

    def test_iter_chunks(self):
        chunks = list(dv.iter_chunks(range(10), 4))
        assert [len(c) for c in chunks] == [4, 4, 2]
