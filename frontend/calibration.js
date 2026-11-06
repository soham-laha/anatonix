/**
 * Anatonix — multi-frame baseline calibration.
 *
 * The user holds an upright, comfortable reference posture for roughly three
 * seconds.  Every processed frame is offered to the collector; frames with poor
 * landmark visibility are rejected, and the accepted frames are averaged into
 * the baseline used by the posture engine.
 */

export const CALIBRATION_DEFAULTS = Object.freeze({
  targetFrames: 45,
  maxFrames: 240,
  minVisibility: 0.5,
  minFramesRequired: 15,
});

export function mean(values) {
  if (!values.length) return null;
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

export function stdDev(values) {
  if (values.length < 2) return 0;
  const m = mean(values);
  const variance = values.reduce((sum, v) => sum + (v - m) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

/**
 * Average a set of calibration samples.
 * Each sample: { pitch, shoulderTilt, shoulderSpan, visibility }.
 * Returns null when fewer than `minFramesRequired` usable samples exist.
 */
export function summariseFrames(frames, options = {}) {
  const { minVisibility, minFramesRequired } = { ...CALIBRATION_DEFAULTS, ...options };
  const pitches = [];
  const tilts = [];
  const spans = [];
  const visibilities = [];
  let rejected = 0;

  for (const frame of frames || []) {
    if (!frame) {
      rejected += 1;
      continue;
    }
    const values = [frame.pitch, frame.shoulderTilt, frame.shoulderSpan];
    const allFinite = values.every((v) => Number.isFinite(v));
    const visibility = Number.isFinite(frame.visibility) ? frame.visibility : 1;
    if (!allFinite || frame.shoulderSpan <= 1e-6 || visibility < minVisibility) {
      rejected += 1;
      continue;
    }
    pitches.push(frame.pitch);
    tilts.push(frame.shoulderTilt);
    spans.push(frame.shoulderSpan);
    visibilities.push(visibility);
  }

  if (pitches.length < minFramesRequired) return null;

  const pitchSpread = stdDev(pitches);
  const tiltSpread = stdDev(tilts);
  const meanVisibility = mean(visibilities);
  const stability = Math.max(
    0,
    1 - Math.min(1, (pitchSpread / 6 + tiltSpread / 3) / 2)
  );
  const quality = Math.max(0, Math.min(1, 0.55 * meanVisibility + 0.45 * stability));

  return {
    baseline: {
      pitch: mean(pitches),
      shoulderTilt: mean(tilts),
      shoulderSpan: mean(spans),
    },
    quality,
    frames: pitches.length,
    rejected,
    pitchSpread,
    tiltSpread,
    meanVisibility,
  };
}

export class CalibrationCollector {
  constructor(options = {}) {
    this.options = { ...CALIBRATION_DEFAULTS, ...options };
    this.frames = [];
    this.active = false;
    this.startedAt = null;
    this.lastSampleAt = null;
  }

  start(nowMs = Date.now()) {
    this.frames = [];
    this.active = true;
    this.startedAt = nowMs;
    this.lastSampleAt = null;
  }

  cancel() {
    this.active = false;
    this.frames = [];
    this.startedAt = null;
  }

  /**
   * Offer a frame.  Samples are throttled to `sampleIntervalMs` so a 60 fps
   * loop does not fill the buffer instantly, and the collector stops itself
   * once `maxFrames` usable samples have been stored.
   * @returns {boolean} whether the frame was stored
   */
  add(frame, nowMs = Date.now()) {
    if (!this.active) return false;
    const interval = this.options.sampleIntervalMs ?? 60;
    if (this.lastSampleAt !== null && nowMs - this.lastSampleAt < interval) return false;
    if (this.frames.length >= this.options.maxFrames) return false;
    if (!frame) return false;
    if (!Number.isFinite(frame.pitch) || !Number.isFinite(frame.shoulderTilt) ||
        !Number.isFinite(frame.shoulderSpan)) {
      return false;
    }
    if ((frame.visibility ?? 1) < this.options.minVisibility) return false;
    this.frames.push({ ...frame, at: nowMs });
    this.lastSampleAt = nowMs;
    return true;
  }

  get progress() {
    if (!this.options.targetFrames) return 0;
    return Math.min(1, this.frames.length / this.options.targetFrames);
  }

  get isComplete() {
    return this.frames.length >= this.options.targetFrames;
  }

  result() {
    return summariseFrames(this.frames, this.options);
  }

  finish() {
    const result = this.result();
    this.active = false;
    return result;
  }
}

export function qualityLabel(score) {
  if (score === null || score === undefined || !Number.isFinite(score)) return 'Not calibrated';
  if (score >= 0.8) return 'High';
  if (score >= 0.6) return 'Moderate';
  if (score >= 0.4) return 'Fair';
  return 'Poor';
}
