"""Unit tests for the posture / ocular reference mathematics."""

from __future__ import annotations

import math

import pytest

from backend import posture_math as pm


def lm(x, y, z=0.0, visibility=1.0):
    return pm.landmark(x, y, z, visibility)


class TestPrimitives:
    def test_midpoint(self):
        result = pm.midpoint(lm(0.2, 0.4, -0.1, 0.8), lm(0.4, 0.6, -0.3, 0.9))
        assert result["x"] == pytest.approx(0.3)
        assert result["y"] == pytest.approx(0.5)
        assert result["z"] == pytest.approx(-0.2)
        assert result["visibility"] == pytest.approx(0.8)

    def test_ema_smoothing_converges(self):
        value = None
        for _ in range(200):
            value = pm.ema(value, 20.0, 0.25)
        assert value == pytest.approx(20.0, abs=1e-6)

    def test_ema_handles_missing_current(self):
        assert pm.ema(12.0, None, 0.3) == 12.0
        assert pm.ema(None, None, 0.3) is None
        assert pm.ema(None, 7.5, 0.3) == 7.5


class TestForwardLean:
    def test_upright_head_has_near_zero_lean(self):
        points = [lm(0.45, 0.20, 0.0), lm(0.55, 0.20, 0.0),
                  lm(0.40, 0.55, 0.0), lm(0.60, 0.55, 0.0)]
        assert pm.forward_lean(points) == pytest.approx(0.0, abs=0.01)

    def test_head_forward_of_shoulders_is_positive(self):
        # ears closer to the camera (negative z) than shoulders -> forward lean
        points = [lm(0.45, 0.20, -0.20), lm(0.55, 0.20, -0.20),
                  lm(0.40, 0.55, 0.0), lm(0.60, 0.55, 0.0)]
        assert pm.forward_lean(points) > 15.0

    def test_head_behind_shoulders_is_clamped_to_zero(self):
        points = [lm(0.45, 0.20, 0.10), lm(0.55, 0.20, 0.10),
                  lm(0.40, 0.55, 0.0), lm(0.60, 0.55, 0.0)]
        assert pm.forward_lean(points) == 0.0

    def test_baseline_is_subtracted(self):
        points = [lm(0.45, 0.20, -0.05), lm(0.55, 0.20, -0.05),
                  lm(0.40, 0.55, 0.0), lm(0.60, 0.55, 0.0)]
        raw = pm.raw_pitch(points)
        assert pm.forward_lean(points, baseline_pitch=raw) == pytest.approx(0.0, abs=1e-6)
        assert pm.forward_lean(points, baseline_pitch=raw - 5.0) == pytest.approx(5.0, abs=1e-6)

    def test_missing_landmarks_return_none(self):
        assert pm.forward_lean([lm(0, 0), lm(0, 0)]) is None
        assert pm.forward_lean([lm(0, 0), lm(0, 0), None, lm(0, 0)]) is None


class TestShoulderGeometry:
    def test_level_shoulders(self):
        geom = pm.shoulder_geometry(lm(0.4, 0.5), lm(0.6, 0.5))
        assert geom.tilt_degrees == pytest.approx(0.0, abs=1e-6)
        assert geom.span == pytest.approx(0.2, abs=1e-6)

    def test_right_shoulder_lower_is_positive(self):
        geom = pm.shoulder_geometry(lm(0.4, 0.45), lm(0.6, 0.55))
        assert geom.tilt_degrees == pytest.approx(math.degrees(math.atan2(0.10, 0.20)))
        assert geom.tilt_degrees > 0

    def test_left_shoulder_lower_is_negative(self):
        geom = pm.shoulder_geometry(lm(0.4, 0.55), lm(0.6, 0.45))
        assert geom.tilt_degrees < 0

    def test_front_facing_camera_orientation_is_normalised(self):
        """Subject's left shoulder usually has the larger x in a front-facing feed."""
        geom = pm.shoulder_geometry(lm(0.6, 0.45), lm(0.4, 0.50))
        assert geom.orientation_flipped is True
        # right shoulder (x=0.4) is lower in the frame than the left (y=0.45)
        assert geom.tilt_degrees < 0
        assert -90 < geom.tilt_degrees < 90

    def test_absolute_tilt_uses_baseline(self):
        left, right = lm(0.4, 0.45), lm(0.6, 0.55)
        base = pm.shoulder_geometry(left, right).tilt_degrees
        assert pm.shoulder_tilt(left, right, baseline_tilt=base) == pytest.approx(0.0, abs=1e-9)

    def test_degenerate_span(self):
        assert pm.shoulder_geometry(lm(0.5, 0.5), lm(0.5, 0.5)) is None


class TestProximityAndLoad:
    def test_proximity_ratio(self):
        assert pm.proximity_ratio(0.20, 0.20) == pytest.approx(1.0)
        assert pm.proximity_ratio(0.25, 0.20) == pytest.approx(1.25)
        assert pm.proximity_ratio(0.20, None) is None
        assert pm.proximity_ratio(0.20, 0.0) is None

    @pytest.mark.parametrize(
        "lean,expected",
        [(0.0, 10), (8.0, 10), (8.1, 20), (15.0, 20), (15.1, 32), (25.0, 32), (25.1, 49), (60.0, 49)],
    )
    def test_neck_load_mapping(self, lean, expected):
        assert pm.neck_load_estimate(lean) == expected

    def test_neck_load_none(self):
        assert pm.neck_load_estimate(None) is None


class TestClassification:
    def test_good_posture(self):
        assert pm.classify_posture(4.0, 2.0, 12.0, 6.0) == "good"

    def test_elevated_when_either_threshold_exceeded(self):
        assert pm.classify_posture(13.0, 2.0, 12.0, 6.0) == "elevated"
        assert pm.classify_posture(2.0, 7.0, 12.0, 6.0) == "elevated"

    def test_untracked(self):
        assert pm.classify_posture(None, None, 12.0, 6.0) == "untracked"


class TestCalibration:
    def _frames(self, count=30, pitch=12.0, tilt=1.5, span=0.2, visibility=0.9, noise=0.0):
        return [
            {
                "pitch": pitch + (noise if i % 2 else -noise),
                "shoulderTilt": tilt,
                "shoulderSpan": span,
                "visibility": visibility,
            }
            for i in range(count)
        ]

    def test_average_of_stable_frames_has_high_quality(self):
        result = pm.average_baseline(self._frames())
        assert result is not None
        assert result.baseline_pitch == pytest.approx(12.0)
        assert result.baseline_shoulder_tilt == pytest.approx(1.5)
        assert result.baseline_shoulder_span == pytest.approx(0.2)
        assert result.quality_score > 0.8
        assert result.frames_used == 30

    def test_unstable_frames_lower_quality(self):
        stable = pm.average_baseline(self._frames())
        noisy = pm.average_baseline(self._frames(noise=5.0))
        assert noisy is not None and stable is not None
        assert noisy.quality_score < stable.quality_score

    def test_low_visibility_frames_are_rejected(self):
        frames = self._frames(10) + self._frames(20, visibility=0.2)
        result = pm.average_baseline(frames)
        assert result is not None
        assert result.frames_used == 10
        assert result.frames_rejected == 20

    def test_insufficient_frames_returns_none(self):
        assert pm.average_baseline(self._frames(4)) is None

    def test_invalid_numbers_are_rejected(self):
        frames = self._frames(10)
        frames.append({"pitch": float("nan"), "shoulderTilt": 1.0, "shoulderSpan": 0.2, "visibility": 0.9})
        frames.append({"pitch": 10.0, "shoulderTilt": None, "shoulderSpan": 0.2, "visibility": 0.9})
        result = pm.average_baseline(frames)
        assert result is not None
        assert result.frames_rejected == 2

    def test_quality_label(self):
        assert pm.quality_label(0.9) == "High"
        assert pm.quality_label(0.65) == "Moderate"
        assert pm.quality_label(None) == "Not calibrated"


class TestVisibility:
    def test_mean_visibility_ignores_missing(self):
        assert pm.visibility(lm(0, 0, 0, 0.8), None, lm(0, 0, 0, 0.6)) == pytest.approx(0.7)
        assert pm.visibility(None) == 0.0
