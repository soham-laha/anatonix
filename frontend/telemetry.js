/**
 * Anatonix — telemetry recorder.
 *
 * Buffers 1 Hz telemetry samples and discrete events, then sends them to Python
 * in batches (default every 6 s, and immediately for meaningful events such as
 * calibration completion or monitoring stop).  Batching keeps Streamlit reruns
 * rare, which is what keeps the live view responsive.
 *
 * Every event carries a unique `uid` so that a re-delivered component value can
 * never be persisted twice (see backend/session_manager.py).
 */

export function makeUid(prefix = 'ev') {
  const random =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 16)
      : Math.random().toString(36).slice(2, 18);
  return `${prefix}-${random}`;
}

export function makeSessionId() {
  const random =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID().replace(/-/g, '')
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `s-${random.slice(0, 12)}`;
}

export class TelemetryRecorder {
  constructor(options = {}) {
    this.sampleIntervalMs = options.sampleIntervalMs ?? 1000;
    this.flushIntervalMs = options.flushIntervalMs ?? 6000;
    this.maxPending = options.maxPending ?? 900;
    this.sessionId = null;
    this.sequence = 0;
    this.lastSampleAt = 0;
    this.lastFlushAt = 0;
    this.pending = [];
    this.events = [];
    this.live = {};
    this.samplesSaved = 0;
    this._flushTimer = null;
    this.sendFn = options.sendFn || (() => {});
  }

  startSession(sessionId) {
    this.sessionId = sessionId;
    this.pending = [];
    this.events = [];
    this.samplesSaved = 0;
    this.lastSampleAt = 0;
    this.lastFlushAt = 0;
  }

  endSession() {
    this.sessionId = null;
    this.pending = [];
    this.events = [];
    this.live = {};
  }

  setLive(live) {
    this.live = live || {};
  }

  /**
   * Offer a live metric snapshot; stores at most one sample per interval.
   * @returns {boolean} whether a sample was stored
   */
  sample(nowMs, metrics) {
    if (!this.sessionId) return false;
    if (this.lastSampleAt && nowMs - this.lastSampleAt < this.sampleIntervalMs) return false;
    this.lastSampleAt = nowMs;
    const record = {
      session_id: this.sessionId,
      timestamp: new Date().toISOString(),
      forward_lean: finiteOrNull(metrics.forwardLean),
      shoulder_tilt: finiteOrNull(metrics.shoulderTilt),
      ear_value: finiteOrNull(metrics.ear),
      proximity_ratio: finiteOrNull(metrics.proximity),
      blink_rate: finiteOrNull(metrics.blinkRate),
      tracking_confidence: finiteOrNull(metrics.confidence),
      posture_status: metrics.postureStatus || 'untracked',
    };
    if (
      record.forward_lean === null &&
      record.shoulder_tilt === null &&
      record.ear_value === null
    ) {
      // Nothing measurable in this second: do not persist an empty row.
      return false;
    }
    this.pending.push(record);
    if (this.pending.length > this.maxPending) this.pending.shift();
    this.samplesSaved += 1;
    return true;
  }

  addEvent(type, payload = {}) {
    const event = {
      type,
      uid: payload.uid || makeUid(),
      session_id: payload.session_id ?? this.sessionId,
      timestamp: payload.timestamp || new Date().toISOString(),
      ...payload,
    };
    this.events.push(event);
    return event;
  }

  get pendingCount() {
    return this.pending.length + this.events.length;
  }

  /** Build the payload for the bridge without clearing the buffers. */
  buildPayload() {
    return {
      seq: (this.sequence += 1),
      session_id: this.sessionId,
      records: this.pending.slice(),
      events: this.events.slice(),
      live: this.live,
      sent_at: new Date().toISOString(),
    };
  }

  /**
   * Flush to Python.  The buffers are only cleared after the payload has been
   * handed to the bridge, so a thrown error keeps the data for the next attempt.
   * @returns {{sentRecords:number, sentEvents:number}|null}
   */
  flush(nowMs = Date.now(), force = false) {
    if (!this.sessionId) return null;
    if (!force && nowMs - this.lastFlushAt < this.flushIntervalMs) return null;
    if (this.pendingCount === 0) return null;

    const counts = { sentRecords: this.pending.length, sentEvents: this.events.length };
    const payload = this.buildPayload();
    try {
      this.sendFn(payload);
    } catch (error) {
      console.error('[anatonix] telemetry flush failed', error);
      return null;
    }
    this.pending = [];
    this.events = [];
    this.lastFlushAt = nowMs;
    return counts;
  }

  /** Send whatever is pending right now (used before stopping / unloading). */
  flushNow(nowMs = Date.now()) {
    return this.flush(nowMs, true);
  }
}

function finiteOrNull(value) {
  return Number.isFinite(value) ? Math.round(value * 10000) / 10000 : null;
}
