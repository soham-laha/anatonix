"""Reference geometry for the biomechanical posture engine.

This module is the auditable Python implementation of the mathematics that the
browser performs in ``frontend/posture.js``.  Keeping a second, testable
implementation lets the project verify the formulas independently of the
browser and lets the report generator recompute derived values from stored
telemetry (for example when a calibration baseline changed mid-session).

All inputs are MediaPipe landmarks expressed as dictionaries with ``x``, ``y``,
``z`` (normalised image coordinates) and ``visibility`` (0..1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Dict, Iterable, List, Optional, Sequence

from config import NECK_LOAD_TABLE

Landmark = Dict[str, float]


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def landmark(x: float, y: float, z: float = 0.0, visibility: float = 1.0) -> Landmark:
    return {"x": float(x), "y": float(y), "z": float(z), "visibility": float(visibility)}


def midpoint(a: Landmark, b: Landmark) -> Landmark:
    return landmark(
        (a["x"] + b["x"]) / 2.0,
        (a["y"] + b["y"]) / 2.0,
        (a.get("z", 0.0) + b.get("z", 0.0)) / 2.0,
        min(a.get("visibility", 1.0), b.get("visibility", 1.0)),
    )


def forward_lean(points: Sequence[Landmark], baseline_pitch: float = 0.0) -> Optional[float]:
    """Relative forward-head angle in degrees.

    ``points`` = [left ear, right ear, left shoulder, right shoulder].

    Raw pitch is the angle between the ear-to-shoulder vector projected on the
    depth/vertical plane:

        pitch = atan2((z_shoulder - z_ear) * 100, max((y_shoulder - y_ear) * 100, 1))

    * ``y_shoulder - y_ear`` is the vertical ear-to-shoulder distance (always
      positive when the subject is upright and both landmarks are visible).
    * ``z_shoulder - z_ear`` is positive when the ears are *closer* to the
      camera than the shoulders, i.e. the head is displaced forward.
    * The 100x scaling converts normalised units to a comparable magnitude so
      that a normalised ``atan2`` ratio yields a usable number of degrees.  It
      does not make the result metric.

    The returned value is the calibrated offset ``max(0, raw - baseline)``:
    leaning *back* past the baseline is reported as 0 because only forward
    displacement is being monitored.
    """
    if len(points) != 4 or any(p is None for p in points):
        return None
    ears = midpoint(points[0], points[1])
    shoulders = midpoint(points[2], points[3])

    vertical = (shoulders["y"] - ears["y"]) * 100.0
    depth = (shoulders.get("z", 0.0) - ears.get("z", 0.0)) * 100.0
    if not math.isfinite(vertical) or not math.isfinite(depth):
        return None
    raw = math.degrees(math.atan2(depth, max(vertical, 1.0)))
    return max(0.0, raw - baseline_pitch)


def raw_pitch(points: Sequence[Landmark]) -> Optional[float]:
    """Calibration-independent pitch value (used to establish the baseline)."""
    if len(points) != 4 or any(p is None for p in points):
        return None
    ears = midpoint(points[0], points[1])
    shoulders = midpoint(points[2], points[3])
    vertical = (shoulders["y"] - ears["y"]) * 100.0
    depth = (shoulders.get("z", 0.0) - ears.get("z", 0.0)) * 100.0
    if not math.isfinite(vertical) or not math.isfinite(depth):
        return None
    return math.degrees(math.atan2(depth, max(vertical, 1.0)))


@dataclass(frozen=True)
class ShoulderGeometry:
    tilt_degrees: float
    span: float
    orientation_flipped: bool


def shoulder_geometry(left: Landmark, right: Landmark) -> Optional[ShoulderGeometry]:
    """Signed shoulder-line inclination plus the normalised shoulder span.

    The vector is normalised to point from the subject's left shoulder towards
    their right shoulder.  With a front-facing webcam the subject's left
    shoulder appears on the *right* half of the un-mirrored frame, so raw
    ``dx = x_right - x_left`` is usually negative; the vector is negated in
    that case (``orientation_flipped``) so the returned angle always lives in
    (-90, 90) and has a stable meaning:

        tilt > 0  ->  the subject's right shoulder sits lower in the frame
        tilt < 0  ->  the subject's left shoulder sits lower in the frame

    The span (shoulder width in normalised units) is unaffected by the flip and
    is used only as a *relative* camera-distance indicator.
    """
    if left is None or right is None:
        return None
    dx = right["x"] - left["x"]
    dy = right["y"] - left["y"]
    if not all(math.isfinite(v) for v in (dx, dy)):
        return None
    span = math.hypot(dx, dy)
    if span < 1e-6:
        return None
    flipped = dx < 0
    if flipped:
        dx, dy = -dx, -dy
    return ShoulderGeometry(
        tilt_degrees=math.degrees(math.atan2(dy, dx)),
        span=span,
        orientation_flipped=flipped,
    )


def shoulder_tilt(left: Landmark, right: Landmark, baseline_tilt: float = 0.0) -> Optional[float]:
    """Absolute deviation of shoulder tilt from the calibrated baseline."""
    geom = shoulder_geometry(left, right)
    if geom is None:
        return None
    return abs(geom.tilt_degrees - baseline_tilt)


def shoulder_span(left: Landmark, right: Landmark) -> Optional[float]:
    geom = shoulder_geometry(left, right)
    return geom.span if geom else None


def proximity_ratio(current_span: Optional[float], baseline_span: Optional[float]) -> Optional[float]:
    """Relative camera-distance indicator (1.0 = calibration distance)."""
    if not current_span or not baseline_span or baseline_span <= 1e-6:
        return None
    ratio = current_span / baseline_span
    if not math.isfinite(ratio) or ratio <= 0:
        return None
    return ratio


def neck_load_estimate(forward_lean_degrees: Optional[float]) -> Optional[int]:
    """Illustrative neck-load mapping (educational estimate only)."""
    if forward_lean_degrees is None:
        return None
    for upper, pounds, _label in NECK_LOAD_TABLE:
        if forward_lean_degrees <= upper:
            return pounds
    return NECK_LOAD_TABLE[-1][1]


def neck_load_label(forward_lean_degrees: Optional[float]) -> str:
    if forward_lean_degrees is None:
        return "No data"
    for upper, _pounds, label in NECK_LOAD_TABLE:
        if forward_lean_degrees <= upper:
            return label
    return NECK_LOAD_TABLE[-1][2]


def visibility(*points: Optional[Landmark]) -> float:
    vals = [p.get("visibility", 1.0) for p in points if p is not None]
    return float(mean(vals)) if vals else 0.0


def classify_posture(
    lean: Optional[float],
    tilt: Optional[float],
    lean_threshold: float,
    tilt_threshold: float,
) -> str:
    """Coarse per-frame classification persisted with telemetry."""
    if lean is None and tilt is None:
        return "untracked"
    lean_bad = lean is not None and lean > lean_threshold
    tilt_bad = tilt is not None and tilt > tilt_threshold
    if lean_bad or tilt_bad:
        # "alert" is reserved for sustained violations; per-frame excursions
        # above threshold are "elevated".
        return "elevated"
    return "good"


def ema(previous: Optional[float], current: Optional[float], alpha: float) -> Optional[float]:
    """Exponential moving average used to smooth live metrics."""
    if current is None:
        return previous
    if previous is None:
        return current
    alpha = max(0.01, min(1.0, alpha))
    return previous + alpha * (current - previous)


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------
@dataclass
class CalibrationResult:
    baseline_pitch: float
    baseline_shoulder_tilt: float
    baseline_shoulder_span: float
    quality_score: float
    frames_used: int
    frames_rejected: int
    pitch_spread: float
    tilt_spread: float

    def as_dict(self) -> Dict[str, float]:
        return {
            "baseline_pitch": round(self.baseline_pitch, 3),
            "baseline_shoulder_tilt": round(self.baseline_shoulder_tilt, 3),
            "baseline_shoulder_span": round(self.baseline_shoulder_span, 5),
            "quality_score": round(self.quality_score, 3),
            "frames_used": self.frames_used,
            "frames_rejected": self.frames_rejected,
        }


def average_baseline(frames: Iterable[Dict[str, float]], min_visibility: float = 0.5) -> Optional[CalibrationResult]:
    """Average multi-frame calibration samples.

    Each frame is ``{"pitch", "shoulderTilt", "shoulderSpan", "visibility"}``.
    Frames whose visibility is below ``min_visibility`` or that lack any metric
    are rejected.  Quality combines mean visibility with the temporal stability
    of the samples (a shaky calibration scores lower).
    """
    pitches: List[float] = []
    tilts: List[float] = []
    spans: List[float] = []
    vis: List[float] = []
    rejected = 0
    for frame in frames:
        if not isinstance(frame, dict):
            rejected += 1
            continue
        p = frame.get("pitch")
        t = frame.get("shoulderTilt")
        s = frame.get("shoulderSpan")
        v = frame.get("visibility", 1.0)
        numbers = [p, t, s, v]
        if any(n is None or not isinstance(n, (int, float)) for n in numbers):
            rejected += 1
            continue
        if any(not math.isfinite(float(n)) for n in numbers):
            rejected += 1
            continue
        if float(v) < min_visibility or float(s) <= 1e-6:
            rejected += 1
            continue
        pitches.append(float(p))
        tilts.append(float(t))
        spans.append(float(s))
        vis.append(float(v))

    if len(pitches) < 5:
        return None

    pitch_mean = mean(pitches)
    tilt_mean = mean(tilts)
    span_mean = mean(spans)
    pitch_spread = pstdev(pitches) if len(pitches) > 1 else 0.0
    tilt_spread = pstdev(tilts) if len(tilts) > 1 else 0.0
    mean_vis = mean(vis)

    stability = max(0.0, 1.0 - min(1.0, (pitch_spread / 6.0 + tilt_spread / 3.0) / 2.0))
    quality = max(0.0, min(1.0, 0.55 * mean_vis + 0.45 * stability))

    return CalibrationResult(
        baseline_pitch=pitch_mean,
        baseline_shoulder_tilt=tilt_mean,
        baseline_shoulder_span=span_mean,
        quality_score=quality,
        frames_used=len(pitches),
        frames_rejected=rejected,
        pitch_spread=pitch_spread,
        tilt_spread=tilt_spread,
    )


def quality_label(score: Optional[float]) -> str:
    if score is None:
        return "Not calibrated"
    if score >= 0.8:
        return "High"
    if score >= 0.6:
        return "Moderate"
    if score >= 0.4:
        return "Fair"
    return "Poor"
