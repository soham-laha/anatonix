/**
 * Node unit tests for the browser-side geometry, ocular, calibration and alert
 * logic.  Run with:  node --test frontend/tests/
 *
 * These modules are DOM-free by design, so the same code the browser runs can be
 * verified here without a headless browser.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import * as posture from '../posture.js';
import {
  BlinkDetector,
  LEFT_EYE,
  RIGHT_EYE,
  averageEAR,
  blinkCategory,
  eyeAspectRatio,
} from '../ocular.js';
import { CalibrationCollector, qualityLabel, summariseFrames } from '../calibration.js';
import { BreakReminder, HysteresisLatch, PostureAlertEngine } from '../alerts.js';
import { scheduleInterval, INFERENCE_DUTY_LIMIT, MIN_SCHEDULE_INTERVAL_MS } from '../vision.js';

const p = (x, y, z = 0, visibility = 1) => ({ x, y, z, visibility });

/* ------------------------------------------------------------------ posture */
test('midpoint averages coordinates and keeps minimum visibility', () => {
  const mid = posture.midpoint(p(0.2, 0.4, -0.1, 0.8), p(0.4, 0.6, -0.3, 0.9));
  assert.equal(Number(mid.x.toFixed(3)), 0.3);
  assert.equal(Number(mid.y.toFixed(3)), 0.5);
  assert.equal(Number(mid.z.toFixed(3)), -0.2);
  assert.equal(mid.visibility, 0.8);
});

test('upright head produces ~0 lean, forward head produces large lean', () => {
  const upright = [p(0.45, 0.2), p(0.55, 0.2), p(0.4, 0.55), p(0.6, 0.55)];
  assert.ok(posture.forwardLean(upright) < 0.01);

  const forward = [p(0.45, 0.2, -0.2), p(0.55, 0.2, -0.2), p(0.4, 0.55), p(0.6, 0.55)];
  assert.ok(posture.forwardLean(forward) > 15);
});

test('lean behind the baseline clamps to zero', () => {
  const back = [p(0.45, 0.2, 0.1), p(0.55, 0.2, 0.1), p(0.4, 0.55), p(0.6, 0.55)];
  assert.equal(posture.forwardLean(back), 0);
});

test('baseline subtraction is exact', () => {
  const points = [p(0.45, 0.2, -0.05), p(0.55, 0.2, -0.05), p(0.4, 0.55), p(0.6, 0.55)];
  const raw = posture.rawPitch(
    posture.midpoint(points[0], points[1]),
    posture.midpoint(points[2], points[3])
  );
  assert.ok(Math.abs(posture.forwardLean(points, raw)) < 1e-9);
  assert.ok(Math.abs(posture.forwardLean(points, raw - 5) - 5) < 1e-9);
});

test('missing landmarks yield null rather than fabricated numbers', () => {
  assert.equal(posture.forwardLean([p(0, 0), p(0, 0)]), null);
  assert.equal(posture.forwardLean([p(0, 0), p(0, 0), null, p(0, 0)]), null);
  assert.equal(posture.forwardLean([p(0, 0), { x: NaN, y: 0 }, p(0, 0), p(0, 0)]), null);
});

test('shoulder tilt sign and orientation normalisation', () => {
  const level = posture.shoulderGeometry(p(0.4, 0.5), p(0.6, 0.5));
  assert.ok(Math.abs(level.tiltDegrees) < 1e-9);
  assert.ok(Math.abs(level.span - 0.2) < 1e-9);

  const rightLower = posture.shoulderGeometry(p(0.4, 0.45), p(0.6, 0.55));
  assert.ok(rightLower.tiltDegrees > 0);
  assert.equal(rightLower.orientationFlipped, false);

  const frontFacing = posture.shoulderGeometry(p(0.6, 0.45), p(0.4, 0.5));
  assert.equal(frontFacing.orientationFlipped, true);
  assert.ok(frontFacing.tiltDegrees < 0);
  assert.ok(frontFacing.tiltDegrees > -90 && frontFacing.tiltDegrees < 90);
});

test('shoulder tilt relative to baseline is absolute', () => {
  const left = p(0.4, 0.45);
  const right = p(0.6, 0.55);
  const base = posture.shoulderGeometry(left, right).tiltDegrees;
  assert.ok(Math.abs(posture.shoulderTilt(left, right, base)) < 1e-9);
});

test('proximity ratio and degenerate guards', () => {
  assert.equal(posture.proximityRatio(0.2, 0.2), 1);
  assert.equal(posture.proximityRatio(0.25, 0.2), 1.25);
  assert.equal(posture.proximityRatio(0.2, 0), null);
  assert.equal(posture.proximityRatio(null, 0.2), null);
});

test('neck load mapping matches the documented table', () => {
  assert.equal(posture.neckLoad(0), 10);
  assert.equal(posture.neckLoad(8), 10);
  assert.equal(posture.neckLoad(8.1), 20);
  assert.equal(posture.neckLoad(15), 20);
  assert.equal(posture.neckLoad(15.1), 32);
  assert.equal(posture.neckLoad(25.1), 49);
  assert.equal(posture.neckLoad(null), null);
  assert.equal(posture.neckLoadLabel(9), '8–15°');
});

test('per-frame classification', () => {
  assert.equal(posture.classifyPosture(4, 2, 12, 6), 'good');
  assert.equal(posture.classifyPosture(13, 2, 12, 6), 'elevated');
  assert.equal(posture.classifyPosture(2, 7, 12, 6), 'elevated');
  assert.equal(posture.classifyPosture(null, null, 12, 6), 'untracked');
});

test('EMA smoothing behaves and ignores missing samples', () => {
  let v = null;
  for (let i = 0; i < 200; i += 1) v = posture.ema(v, 20, 0.25);
  assert.ok(Math.abs(v - 20) < 1e-6);
  assert.equal(posture.ema(12, null, 0.3), 12);
  assert.equal(posture.ema(null, 7.5, 0.3), 7.5);
});

test('eye crop box stays inside the frame and tracks the face', () => {
  const landmarks = [];
  landmarks[33] = { x: 0.45, y: 0.30 };
  landmarks[263] = { x: 0.55, y: 0.31 };
  landmarks[168] = { x: 0.50, y: 0.33 };
  const box = posture.eyeCropBox(landmarks, 640, 480, 1.4);
  assert.ok(box.sw > 0 && box.sh > 0);
  assert.ok(box.sx >= 0 && box.sy >= 0);
  assert.ok(box.sx + box.sw <= 640 + 1e-6);
  assert.ok(box.sy + box.sh <= 480 + 1e-6);

  // face pushed to the left edge must clamp, not go negative
  const edge = [];
  edge[33] = { x: 0.02, y: 0.3 };
  edge[263] = { x: 0.08, y: 0.3 };
  const edgeBox = posture.eyeCropBox(edge, 640, 480, 1.6);
  assert.ok(edgeBox.sx >= 0);
});

/* ------------------------------------------------------------------- ocular */
function eyeLandmarks(eye, openness) {
  const landmarks = [];
  const width = 0.06;
  landmarks[eye.outer] = { x: 0.40, y: 0.30 };
  landmarks[eye.inner] = { x: 0.40 + width, y: 0.30 };
  const gap = 0.03 * openness;
  landmarks[eye.upper1] = { x: 0.40 + width * 0.33, y: 0.30 - gap };
  landmarks[eye.upper2] = { x: 0.40 + width * 0.66, y: 0.30 - gap };
  landmarks[eye.lower1] = { x: 0.40 + width * 0.33, y: 0.30 + gap };
  landmarks[eye.lower2] = { x: 0.40 + width * 0.66, y: 0.30 + gap };
  return landmarks;
}

test('EAR is ~1 for a square-ish open eye and ~0 when closed', () => {
  const open = eyeAspectRatio(eyeLandmarks(LEFT_EYE, 1), LEFT_EYE, 1);
  const closed = eyeAspectRatio(eyeLandmarks(LEFT_EYE, 0.02), LEFT_EYE, 1);
  assert.ok(open > 0.3);
  assert.ok(closed < 0.05);
  assert.ok(closed < open);
});

test('EAR averages both eyes and tolerates one missing eye', () => {
  const both = eyeLandmarks(LEFT_EYE, 1);
  const right = eyeLandmarks(RIGHT_EYE, 1);
  const merged = both.slice();
  Object.entries(RIGHT_EYE).forEach(([key, index]) => {
    merged[index] = right[index];
  });
  const avg = averageEAR(merged, 1);
  assert.ok(Math.abs(avg - eyeAspectRatio(merged, LEFT_EYE, 1)) < 1e-9);

  const onlyLeft = eyeLandmarks(LEFT_EYE, 1);
  assert.ok(averageEAR(onlyLeft, 1) > 0.3);
});

test('EAR returns null for missing or degenerate geometry', () => {
  assert.equal(eyeAspectRatio(null, LEFT_EYE, 1), null);
  assert.equal(eyeAspectRatio([], LEFT_EYE, 1), null);
  const degenerate = eyeLandmarks(LEFT_EYE, 1);
  degenerate[LEFT_EYE.inner] = { ...degenerate[LEFT_EYE.outer] };
  assert.equal(eyeAspectRatio(degenerate, LEFT_EYE, 1), null);
});

test('blink detection counts a normal blink exactly once', () => {
  const detector = new BlinkDetector({ threshold: 0.19, minDurationMs: 60, debounceMs: 120 });
  let now = 1000;
  const open = 0.32;
  const closed = 0.10;

  for (let i = 0; i < 5; i += 1) detector.update(open, (now += 40));
  assert.equal(detector.totalBlinks, 0);

  for (let i = 0; i < 3; i += 1) detector.update(closed, (now += 40)); // ~120 ms closed
  const reopened = detector.update(open, (now += 40));
  assert.equal(reopened.blink, true);
  assert.equal(detector.totalBlinks, 1);
  assert.equal(detector.state, 'open');
});

test('a prolonged closure is not counted as repeated blinks', () => {
  const detector = new BlinkDetector({ threshold: 0.19, minDurationMs: 60, maxClosedMs: 1200 });
  let now = 1000;
  detector.update(0.32, (now += 40));
  let prolonged = false;
  for (let i = 0; i < 60; i += 1) {
    // 2.4 s of closed eyes
    const result = detector.update(0.05, (now += 40));
    prolonged = prolonged || result.prolonged;
  }
  assert.equal(prolonged, true);
  const reopened = detector.update(0.32, (now += 40));
  assert.equal(reopened.blink, false);
  assert.equal(detector.totalBlinks, 0);
});

test('micro-oscillation around the threshold is debounced', () => {
  const detector = new BlinkDetector({ threshold: 0.19, minDurationMs: 0, debounceMs: 300 });
  let now = 1000;
  detector.update(0.30, (now += 40));
  detector.update(0.05, (now += 40));
  const first = detector.update(0.30, (now += 40));
  detector.update(0.05, (now += 40));
  const second = detector.update(0.30, (now += 40)); // only 80 ms later
  assert.equal(first.blink, true);
  assert.equal(second.blink, false);
  assert.equal(detector.totalBlinks, 1);
});

test('rolling blink rate extrapolates while the window fills', () => {
  const detector = new BlinkDetector({ threshold: 0.19, minDurationMs: 0, debounceMs: 0 });
  let now = 0;
  detector.update(0.3, 0);
  // six blinks in 30 s -> 12 bpm
  for (let i = 0; i < 6; i += 1) {
    now += 5000;
    detector.update(0.05, now);
    detector.update(0.30, (now += 40));
  }
  const bpm = detector.blinkRate(now);
  assert.ok(bpm >= 10 && bpm <= 14, `expected ~12 bpm, received ${bpm}`);
});

test('blink rate windows prune old events', () => {
  const detector = new BlinkDetector({ threshold: 0.19, minDurationMs: 0, debounceMs: 0 });
  let now = 0;
  detector.update(0.3, 0);
  for (let i = 0; i < 10; i += 1) {
    now += 2000;
    detector.update(0.05, now);
    detector.update(0.30, (now += 40));
  }
  const early = detector.windowCount(now);
  now += 61000;
  const later = detector.windowCount(now);
  assert.equal(early, 10);
  assert.equal(later, 0);
});

test('blink categories follow the configured bands', () => {
  assert.equal(blinkCategory(4).key, 'low');
  assert.equal(blinkCategory(9).key, 'reduced');
  assert.equal(blinkCategory(15).key, 'reference');
  assert.equal(blinkCategory(null).key, 'unknown');
  assert.equal(blinkCategory(9, 10, 14).key, 'low');
});

/* -------------------------------------------------------------- calibration */
function sample(pitch, tilt, span, visibility = 0.9) {
  return { pitch, shoulderTilt: tilt, shoulderSpan: span, visibility };
}

test('calibration averages stable frames with high quality', () => {
  const frames = Array.from({ length: 40 }, (_, i) => sample(11 + (i % 2 ? 0.1 : -0.1), 1.4, 0.21));
  const result = summariseFrames(frames);
  assert.ok(result);
  assert.ok(Math.abs(result.baseline.pitch - 11) < 0.2);
  assert.ok(result.quality > 0.8);
  assert.equal(result.frames, 40);
});

test('unstable or low-visibility frames reduce calibration quality', () => {
  const stable = summariseFrames(Array.from({ length: 40 }, () => sample(11, 1, 0.2)));
  const shaky = summariseFrames(
    Array.from({ length: 40 }, (_, i) => sample(11 + (i % 2 ? 6 : -6), 1 + (i % 2 ? 3 : -3), 0.2))
  );
  assert.ok(shaky.quality < stable.quality);
});

test('calibration rejects insufficient frames', () => {
  assert.equal(summariseFrames(Array.from({ length: 4 }, () => sample(11, 1, 0.2))), null);
  assert.equal(summariseFrames([]), null);
  const thin = summariseFrames(Array.from({ length: 30 }, () => sample(11, 1, 0.2, 0.2)));
  assert.equal(thin, null);
});

test('calibration collector throttles samples and tracks progress', () => {
  const collector = new CalibrationCollector({ targetFrames: 10, sampleIntervalMs: 50 });
  collector.start(0);
  let now = 0;
  let stored = 0;
  for (let i = 0; i < 40; i += 1) {
    now += 20;
    if (collector.add(sample(11, 1, 0.2), now) === true) stored += 1;
  }
  assert.ok(stored <= 20 && stored > 5);
  collector.cancel();
  assert.equal(collector.frames.length, 0);
});

test('quality labels', () => {
  assert.equal(qualityLabel(0.9), 'High');
  assert.equal(qualityLabel(0.65), 'Moderate');
  assert.equal(qualityLabel(null), 'Not calibrated');
});

/* ------------------------------------------------------------------- alerts */
test('hysteresis requires sustained bad frames and a stable recovery', () => {
  const latch = new HysteresisLatch({ badFrames: 5, recoveryFrames: 3 });
  for (let i = 0; i < 4; i += 1) assert.equal(latch.update(true).active, false);
  assert.equal(latch.update(true).activated, true);
  assert.equal(latch.active, true);

  assert.equal(latch.update(false).cleared, false);
  assert.equal(latch.update(false).cleared, false);
  assert.equal(latch.update(false).cleared, true);
  assert.equal(latch.active, false);
});

test('brief excursions never trigger an alert (no flicker)', () => {
  const latch = new HysteresisLatch({ badFrames: 20, recoveryFrames: 15 });
  for (let cycle = 0; cycle < 10; cycle += 1) {
    for (let i = 0; i < 5; i += 1) latch.update(true);
    for (let i = 0; i < 5; i += 1) latch.update(false);
  }
  assert.equal(latch.active, false);
});

test('alert engine triggers, respects cooldown and recovers', () => {
  const engine = new PostureAlertEngine({
    leanThreshold: 12,
    tiltThreshold: 6,
    badFrames: 5,
    recoveryFrames: 3,
    cooldownSeconds: 3.5,
  });
  let now = 0;
  let fired = 0;
  for (let i = 0; i < 6; i += 1) {
    now += 100;
    const outcome = engine.update({ lean: 20, tilt: 2, nowMs: now });
    fired += outcome.alerts.length;
  }
  assert.equal(fired, 1);
  assert.equal(engine.status, 'alert');

  // recovery
  let cleared = false;
  for (let i = 0; i < 4; i += 1) {
    now += 100;
    cleared = engine.update({ lean: 3, tilt: 1, nowMs: now }).cleared || cleared;
  }
  assert.equal(cleared, true);
  assert.equal(engine.status, 'good');
});

test('alert engine freezes when no data is available', () => {
  const engine = new PostureAlertEngine({ badFrames: 5, recoveryFrames: 3 });
  const outcome = engine.update({ lean: null, tilt: null, nowMs: 0 });
  assert.equal(outcome.status, 'untracked');
  assert.equal(outcome.alerts.length, 0);
});

test('shoulder-only violation raises a shoulder alert', () => {
  const engine = new PostureAlertEngine({ leanThreshold: 12, tiltThreshold: 6, badFrames: 2, recoveryFrames: 2 });
  let now = 0;
  let alertType = null;
  for (let i = 0; i < 4; i += 1) {
    now += 100;
    const outcome = engine.update({ lean: 2, tilt: 9, nowMs: now });
    if (outcome.alerts.length) alertType = outcome.alerts[0].alertType;
  }
  assert.equal(alertType, 'shoulder_tilt');
});

test('break reminder fires on interval, snoozes and dismisses', () => {
  const reminder = new BreakReminder({ intervalMinutes: 1, enabled: true });
  reminder.start(0);
  assert.equal(reminder.update(30000).due, false);
  assert.equal(reminder.update(61000).due, true);
  assert.equal(reminder.update(62000).due, false);

  reminder.snooze(5, 62000);
  assert.equal(reminder.update(100000).due, false);
  assert.equal(reminder.update(362000).due, true);

  reminder.dismiss(362000);
  assert.equal(reminder.update(380000).due, false);
});

test('break reminder respects the disabled flag', () => {
  const reminder = new BreakReminder({ intervalMinutes: 1, enabled: false });
  reminder.start(0);
  assert.equal(reminder.update(999999).due, false);
});

/* ------------------------------------------------------- inference budget */
test('scheduler honours the configured cap when inference is cheap', () => {
  assert.equal(Number(scheduleInterval({ inferenceFps: 30, inferenceMs: 5 }).toFixed(2)), 33.33);
  assert.equal(Number(scheduleInterval({ inferenceFps: 15, inferenceMs: 0 }).toFixed(2)), 66.67);
});

test('scheduler leaves paint headroom when inference is the bottleneck', () => {
  // 70 ms per frame: the cap (30 fps -> 33 ms) is unreachable, so the
  // interval stretches until inference uses at most 70% of the budget.
  const interval = scheduleInterval({ inferenceFps: 30, inferenceMs: 70 });
  assert.equal(Number(interval.toFixed(2)), 100);
  const duty = 70 / interval;
  assert.ok(duty <= INFERENCE_DUTY_LIMIT + 1e-9, `duty ${duty} should stay under the limit`);
  assert.ok(1 - duty >= 0.25, 'at least a quarter of the budget stays free for painting');
});

test('a lower cap still wins over the duty budget', () => {
  // 40 ms cost with a 10 fps cap: the cap interval (100 ms) is larger than the
  // duty interval (57 ms), so the user's slower setting is respected.
  assert.equal(scheduleInterval({ inferenceFps: 10, inferenceMs: 40 }), 100);
});

test('scheduler never drops below the 6 fps floor', () => {
  assert.equal(scheduleInterval({ inferenceFps: 30, inferenceMs: 3000 }), MIN_SCHEDULE_INTERVAL_MS);
  assert.equal(Number(MIN_SCHEDULE_INTERVAL_MS.toFixed(2)), 166.67);
});

test('scheduler clamps out-of-range fps values', () => {
  assert.equal(Number(scheduleInterval({ inferenceFps: 999, inferenceMs: 0 }).toFixed(2)), 33.33);
  assert.equal(scheduleInterval({ inferenceFps: 0, inferenceMs: 0 }), 1000 / 15);
});

test('duty limit of 1.0 removes the paint headroom', () => {
  // Opting out of the budget lets inference use the whole frame.
  assert.equal(Number(scheduleInterval({ inferenceFps: 30, inferenceMs: 70, dutyLimit: 1 }).toFixed(2)), 70);
});

test('a stricter duty limit throttles inference further', () => {
  const interval = scheduleInterval({ inferenceFps: 30, inferenceMs: 60, dutyLimit: 0.5 });
  assert.equal(interval, 120);
});
