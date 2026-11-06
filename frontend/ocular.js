/**
 * Anatonix — ocular (blink) engine.
 *
 * Eye Aspect Ratio (EAR) from six landmarks per eye, a debounced blink state
 * machine, and a rolling 60-second blink-rate window.
 *
 * Nothing in this module is a medical measurement: blink frequency alone cannot
 * diagnose dry-eye disease or any other condition.
 */

/** Six-point eye geometry (MediaPipe face-mesh indices). */
export const LEFT_EYE = Object.freeze({
  outer: 33,
  inner: 133,
  upper1: 160,
  upper2: 158,
  lower1: 144,
  lower2: 153,
});

export const RIGHT_EYE = Object.freeze({
  outer: 362,
  inner: 263,
  upper1: 385,
  upper2: 387,
  lower1: 380,
  lower2: 373,
});

const POINT_KEYS = ['outer', 'inner', 'upper1', 'upper2', 'lower1', 'lower2'];

function distance(a, b, aspect) {
  const dx = (a.x - b.x) * aspect;
  const dy = a.y - b.y;
  return Math.hypot(dx, dy);
}

/**
 * Eye aspect ratio.
 *   EAR = (|upper1-lower1| + |upper2-lower2|) / (2 * |outer-inner|)
 * `aspect` = frameWidth / frameHeight so that normalised x/y are comparable.
 * Returns null when any landmark is missing or the geometry is degenerate.
 */
export function eyeAspectRatio(landmarks, eye = LEFT_EYE, aspect = 4 / 3) {
  if (!landmarks) return null;
  const points = {};
  for (const key of POINT_KEYS) {
    const index = eye[key];
    const point = landmarks[index];
    if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
    points[key] = point;
  }
  const horizontal = distance(points.outer, points.inner, aspect);
  if (!Number.isFinite(horizontal) || horizontal < 1e-6) return null;
  const vertical =
    distance(points.upper1, points.lower1, aspect) +
    distance(points.upper2, points.lower2, aspect);
  return vertical / (2 * horizontal);
}

/** Mean EAR of both eyes, or the single available eye if one is occluded. */
export function averageEAR(landmarks, aspect = 4 / 3) {
  const left = eyeAspectRatio(landmarks, LEFT_EYE, aspect);
  const right = eyeAspectRatio(landmarks, RIGHT_EYE, aspect);
  if (left === null && right === null) return null;
  if (left === null) return right;
  if (right === null) return left;
  return (left + right) / 2;
}

/**
 * Blink state machine.
 *
 * - The eye is "closed" once EAR drops below `threshold`.
 * - A blink is counted when the eye re-opens after being closed for between
 *   `minDurationMs` and `maxClosedMs`.
 * - Closures longer than `maxClosedMs` (eyes shut, looking away, walking off)
 *   are reported as `prolonged` and are *not* counted as blinks — this is what
 *   stops one long closure from registering as many blinks.
 * - A `debounceMs` guard stops micro-oscillation around the threshold from
 *   producing duplicate events.
 */
export class BlinkDetector {
  constructor(options = {}) {
    this.threshold = options.threshold ?? 0.19;
    this.reopenFactor = options.reopenFactor ?? 1.08;
    this.minDurationMs = options.minDurationMs ?? 60;
    this.maxClosedMs = options.maxClosedMs ?? 1200;
    this.debounceMs = options.debounceMs ?? 120;
    this.windowMs = options.windowMs ?? 60000;
    this.reset();
  }

  reset() {
    this.state = 'open';
    this.closedSince = null;
    this.lastBlinkAt = null;
    this.lastStateAt = null;
    this.blinkTimestamps = [];
    this.totalBlinks = 0;
    this.lastEvent = null;
    this.sessionStart = null;
  }

  configure(options = {}) {
    if (options.threshold !== undefined) this.threshold = options.threshold;
    if (options.minDurationMs !== undefined) this.minDurationMs = options.minDurationMs;
    if (options.debounceMs !== undefined) this.debounceMs = options.debounceMs;
  }

  /**
   * Feed one EAR sample.
   * @returns {{state:string, ear:number|null, blink:boolean, prolonged:boolean}}
   */
  update(ear, nowMs = Date.now()) {
    if (this.sessionStart === null) this.sessionStart = nowMs;
    const result = {
      state: this.state,
      ear,
      blink: false,
      prolonged: false,
      closedDurationMs: 0,
    };
    if (ear === null || ear === undefined || !Number.isFinite(ear)) {
      return result;
    }

    if (this.state === 'open' && ear < this.threshold) {
      this.state = 'closed';
      this.closedSince = nowMs;
      this.lastStateAt = nowMs;
      result.state = 'closed';
      return result;
    }

    if (this.state === 'closed') {
      const closedFor = nowMs - (this.closedSince ?? nowMs);
      result.closedDurationMs = closedFor;
      const reopenThreshold = this.threshold * this.reopenFactor;

      if (ear >= reopenThreshold) {
        this.state = 'open';
        this.lastStateAt = nowMs;
        const duration = closedFor;
        const debounced =
          this.lastBlinkAt !== null && nowMs - this.lastBlinkAt < this.debounceMs;

        if (duration >= this.minDurationMs && duration <= this.maxClosedMs && !debounced) {
          this.totalBlinks += 1;
          this.lastBlinkAt = nowMs;
          this.blinkTimestamps.push(nowMs);
          result.blink = true;
          result.state = 'open';
        } else if (duration > this.maxClosedMs) {
          result.prolonged = true;
        }
      } else if (closedFor > this.maxClosedMs) {
        result.prolonged = true;
      }
    }

    this._prune(nowMs);
    return result;
  }

  _prune(nowMs) {
    const cutoff = nowMs - this.windowMs;
    let index = 0;
    while (index < this.blinkTimestamps.length && this.blinkTimestamps[index] < cutoff) {
      index += 1;
    }
    if (index > 0) this.blinkTimestamps.splice(0, index);
  }

  /** Blinks in the rolling 60 s window (or an extrapolation if younger). */
  blinkRate(nowMs = Date.now()) {
    this._prune(nowMs);
    const elapsed = this.sessionStart === null ? 0 : nowMs - this.sessionStart;
    if (elapsed <= 0) return 0;
    if (elapsed < this.windowMs) {
      const observed = this.blinkTimestamps.length;
      return Math.round((observed / elapsed) * this.windowMs * 10) / 10;
    }
    return this.blinkTimestamps.length;
  }

  /** Blinks counted inside the window without extrapolation. */
  windowCount(nowMs = null) {
    if (Number.isFinite(nowMs)) this._prune(nowMs);
    return this.blinkTimestamps.length;
  }

  recent(max = 12, nowMs = Date.now()) {
    return this.blinkTimestamps.slice(-max).map((ts) => ({
      timestamp: ts,
      ageMs: nowMs - ts,
    }));
  }
}

/**
 * Configurable monitoring categories for the Blink Rate Indicator.
 * These are comfort/awareness bands — explicitly not medical thresholds.
 */
export function blinkCategory(bpm, low = 8, reference = 12) {
  if (bpm === null || bpm === undefined || !Number.isFinite(bpm)) {
    return { key: 'unknown', label: 'No data', tone: 'idle' };
  }
  if (bpm < low) return { key: 'low', label: 'Low blink frequency', tone: 'bad' };
  if (bpm < reference) return { key: 'reduced', label: 'Reduced blink frequency', tone: 'warn' };
  return { key: 'reference', label: 'Within reference range', tone: 'good' };
}
