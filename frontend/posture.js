/**
 * Anatonix — biomechanical posture mathematics (browser reference).
 *
 * Pure functions only: no DOM, no MediaPipe, no side effects.  This mirrors
 * `backend/posture_math.py`; both implementations are covered by tests so the
 * formulas cannot drift silently.
 *
 * Coordinate convention
 * ---------------------
 * MediaPipe returns x,y in [0,1] relative to the *un-mirrored* input frame and
 * z in roughly the same scale (negative = closer to the camera).  The preview is
 * mirrored for the user's comfort but all maths uses un-mirrored coordinates.
 */

export const RAD_TO_DEG = 180 / Math.PI;

/** Clamp a number into a range. */
export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

/** Finite-number guard used before any geometry. */
export function isFinitePoint(point) {
  return (
    point &&
    Number.isFinite(point.x) &&
    Number.isFinite(point.y) &&
    Number.isFinite(point.z ?? 0)
  );
}

export function midpoint(a, b) {
  if (!isFinitePoint(a) || !isFinitePoint(b)) return null;
  return {
    x: (a.x + b.x) / 2,
    y: (a.y + b.y) / 2,
    z: ((a.z ?? 0) + (b.z ?? 0)) / 2,
    visibility: Math.min(a.visibility ?? 1, b.visibility ?? 1),
  };
}

/**
 * Raw (calibration independent) cervical pitch proxy in degrees.
 * pitch = atan2((z_shoulder - z_ear) * 100, max((y_shoulder - y_ear) * 100, 1))
 * Positive => ears displaced toward the camera relative to the shoulders.
 */
export function rawPitch(earMid, shoulderMid) {
  if (!isFinitePoint(earMid) || !isFinitePoint(shoulderMid)) return null;
  const vertical = (shoulderMid.y - earMid.y) * 100;
  const depth = ((shoulderMid.z ?? 0) - (earMid.z ?? 0)) * 100;
  if (!Number.isFinite(vertical) || !Number.isFinite(depth)) return null;
  return Math.atan2(depth, Math.max(vertical, 1.0)) * RAD_TO_DEG;
}

/**
 * Relative forward lean (degrees) above the calibrated baseline, clamped at 0.
 * `points` = [leftEar, rightEar, leftShoulder, rightShoulder].
 */
export function forwardLean(points, baselinePitch = 0) {
  if (!Array.isArray(points) || points.length !== 4) return null;
  const earMid = midpoint(points[0], points[1]);
  const shoulderMid = midpoint(points[2], points[3]);
  const raw = rawPitch(earMid, shoulderMid);
  if (raw === null) return null;
  return Math.max(0, raw - baselinePitch);
}

/**
 * Shoulder-line geometry.  The vector is normalised to point from the subject's
 * left shoulder towards their right shoulder so the angle is stable across
 * camera orientations (see backend/posture_math.py for the full rationale).
 */
export function shoulderGeometry(left, right) {
  if (!isFinitePoint(left) || !isFinitePoint(right)) return null;
  let dx = right.x - left.x;
  let dy = right.y - left.y;
  const span = Math.hypot(dx, dy);
  if (!Number.isFinite(span) || span < 1e-6) return null;
  const orientationFlipped = dx < 0;
  if (orientationFlipped) {
    dx = -dx;
    dy = -dy;
  }
  return {
    tiltDegrees: Math.atan2(dy, dx) * RAD_TO_DEG,
    span,
    orientationFlipped,
  };
}

/** Absolute deviation of shoulder tilt from the calibrated baseline. */
export function shoulderTilt(left, right, baselineTilt = 0) {
  const geometry = shoulderGeometry(left, right);
  if (!geometry) return null;
  return Math.abs(geometry.tiltDegrees - baselineTilt);
}

export function shoulderSpan(left, right) {
  const geometry = shoulderGeometry(left, right);
  return geometry ? geometry.span : null;
}

/** Relative camera distance indicator (1.0 == calibration distance). */
export function proximityRatio(currentSpan, baselineSpan) {
  if (!currentSpan || !baselineSpan || baselineSpan <= 1e-6) return null;
  const ratio = currentSpan / baselineSpan;
  return Number.isFinite(ratio) && ratio > 0 ? ratio : null;
}

const NECK_LOAD_TABLE = [
  { limit: 8, pounds: 10, label: 'Up to 8°' },
  { limit: 15, pounds: 20, label: '8–15°' },
  { limit: 25, pounds: 32, label: '15–25°' },
  { limit: Infinity, pounds: 49, label: 'Above 25°' },
];

/** Illustrative neck-load mapping — educational estimate, not a measurement. */
export function neckLoad(leanDegrees) {
  if (leanDegrees === null || leanDegrees === undefined || !Number.isFinite(leanDegrees)) {
    return null;
  }
  for (const row of NECK_LOAD_TABLE) {
    if (leanDegrees <= row.limit) return row.pounds;
  }
  return 49;
}

export function neckLoadLabel(leanDegrees) {
  if (leanDegrees === null || leanDegrees === undefined || !Number.isFinite(leanDegrees)) {
    return 'No data';
  }
  for (const row of NECK_LOAD_TABLE) {
    if (leanDegrees <= row.limit) return row.label;
  }
  return 'Above 25°';
}

export function neckLoadTable() {
  return NECK_LOAD_TABLE.map((row) => ({ ...row }));
}

/** Per-frame coarse classification persisted with telemetry. */
export function classifyPosture(lean, tilt, leanThreshold, tiltThreshold) {
  if (lean === null && tilt === null) return 'untracked';
  const leanBad = lean !== null && lean > leanThreshold;
  const tiltBad = tilt !== null && tilt > tiltThreshold;
  return leanBad || tiltBad ? 'elevated' : 'good';
}

/** Exponential moving average for live smoothing. */
export function ema(previous, current, alpha) {
  if (current === null || current === undefined || !Number.isFinite(current)) return previous;
  if (previous === null || previous === undefined || !Number.isFinite(previous)) return current;
  const a = clamp(alpha, 0.01, 1);
  return previous + a * (current - previous);
}

/** Mean visibility of the provided landmarks (missing entries ignored). */
export function visibilityOf(...points) {
  const values = points
    .filter((p) => p && Number.isFinite(p.visibility ?? NaN))
    .map((p) => p.visibility);
  if (!values.length) return 0;
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

/** Per-eye crop box (un-mirrored input coordinates) used by the ocular PIP. */
export function eyeCropBox(landmarks, frameWidth, frameHeight, padding = 1.35) {
  const outer = landmarks?.[33];
  const inner = landmarks?.[263];
  const nose = landmarks?.[168];
  if (!outer || !inner) return null;
  const cx = ((outer.x + inner.x) / 2) * frameWidth;
  const cy =
    ((outer.y + inner.y + (nose?.y ?? (outer.y + inner.y) / 2)) / (nose ? 3 : 2)) * frameHeight;
  const eyeWidth = Math.abs(inner.x - outer.x) * frameWidth;
  const eyeHeight = Math.abs(inner.y - outer.y) * frameHeight;
  const width = Math.max(eyeWidth * padding, 60);
  const height = Math.max(width * 0.42, eyeHeight * 2.6, 34);
  let sx = cx - width / 2;
  let sy = cy - height / 2;
  sx = clamp(sx, 0, Math.max(0, frameWidth - 4));
  sy = clamp(sy, 0, Math.max(0, frameHeight - 4));
  const w = Math.min(width, frameWidth - sx);
  const h = Math.min(height, frameHeight - sy);
  if (w <= 2 || h <= 2) return null;
  return { sx, sy, sw: w, sh: h, cx, cy };
}
