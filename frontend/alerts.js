/**
 * Anatonix — alert engine.
 *
 * Three cooperating pieces:
 *   HysteresisLatch  – separate trigger/recovery frame counts (no flicker).
 *   PostureAlertEngine – turns live metrics into user-facing alerts with a
 *                        spoken-audio cooldown and duplicate suppression.
 *   BreakReminder    – interval timer with snooze/dismiss bookkeeping.
 *   SpeechEngine     – Web Speech API wrapper with graceful degradation.
 */

export class HysteresisLatch {
  constructor({ badFrames = 20, recoveryFrames = 15 } = {}) {
    this.badFramesRequired = Math.max(1, badFrames);
    this.recoveryFramesRequired = Math.max(1, recoveryFrames);
    this.reset();
  }

  reset() {
    this.active = false;
    this.badCount = 0;
    this.goodCount = 0;
  }

  configure({ badFrames, recoveryFrames }) {
    if (Number.isFinite(badFrames)) this.badFramesRequired = Math.max(1, badFrames);
    if (Number.isFinite(recoveryFrames)) {
      this.recoveryFramesRequired = Math.max(1, recoveryFrames);
    }
  }

  /**
   * @param {boolean} isBad value for this frame
   * @returns {{active:boolean, activated:boolean, cleared:boolean, badCount:number}}
   */
  update(isBad) {
    let activated = false;
    let cleared = false;

    if (isBad) {
      this.badCount += 1;
      this.goodCount = 0;
      if (!this.active && this.badCount >= this.badFramesRequired) {
        this.active = true;
        activated = true;
      }
    } else {
      this.goodCount += 1;
      this.badCount = 0;
      if (this.active && this.goodCount >= this.recoveryFramesRequired) {
        this.active = false;
        cleared = true;
      }
    }
    return { active: this.active, activated, cleared, badCount: this.badCount };
  }
}

const REASON_TEXT = {
  forward_lean: {
    title: 'Forward lean detected',
    advice: 'Consider easing your head back over your shoulders and adjusting your sitting position.',
  },
  shoulder_tilt: {
    title: 'Shoulder asymmetry detected',
    advice: 'Level your shoulders, relax your arms and check that your chair and desk are even.',
  },
  combined: {
    title: 'Posture drift detected',
    advice: 'Sit back, square your shoulders and reset your position.',
  },
};

export class PostureAlertEngine {
  constructor(options = {}) {
    this.leanThreshold = options.leanThreshold ?? 12;
    this.tiltThreshold = options.tiltThreshold ?? 6;
    this.cooldownMs = (options.cooldownSeconds ?? 3.5) * 1000;
    this.leanLatch = new HysteresisLatch({
      badFrames: options.badFrames ?? 20,
      recoveryFrames: options.recoveryFrames ?? 15,
    });
    this.tiltLatch = new HysteresisLatch({
      badFrames: options.badFrames ?? 20,
      recoveryFrames: options.recoveryFrames ?? 15,
    });
    this.lastAnnouncedAt = Number.NEGATIVE_INFINITY;
    this.lastSpokenAt = Number.NEGATIVE_INFINITY;
    this.status = 'idle';
    this.lastAlert = null;
  }

  configure(options = {}) {
    if (Number.isFinite(options.leanThreshold)) this.leanThreshold = options.leanThreshold;
    if (Number.isFinite(options.tiltThreshold)) this.tiltThreshold = options.tiltThreshold;
    if (Number.isFinite(options.cooldownSeconds)) this.cooldownMs = options.cooldownSeconds * 1000;
    this.leanLatch.configure({
      badFrames: options.badFrames,
      recoveryFrames: options.recoveryFrames,
    });
    this.tiltLatch.configure({
      badFrames: options.badFrames,
      recoveryFrames: options.recoveryFrames,
    });
  }

  reset() {
    this.leanLatch.reset();
    this.tiltLatch.reset();
    this.status = 'idle';
    this.lastAlert = null;
  }

  /**
   * Feed one processed frame.
   * @returns {{status:string, alerts:Array, shouldSpeak:boolean, cleared:boolean}}
   */
  update({ lean, tilt, nowMs = Date.now() }) {
    const out = { status: this.status, alerts: [], shouldSpeak: false, cleared: false };

    if (lean === null && tilt === null) {
      // No data: freeze state instead of fabricating either extreme.
      this.status = 'untracked';
      out.status = this.status;
      return out;
    }

    const leanBad = lean !== null && lean > this.leanThreshold;
    const tiltBad = tilt !== null && tilt > this.tiltThreshold;
    const leanState = this.leanLatch.update(leanBad);
    const tiltState = this.tiltLatch.update(tiltBad);

    let status = 'good';
    if (leanState.active || tiltState.active) status = 'alert';
    else if (leanBad || tiltBad) status = 'elevated';

    if (leanState.cleared || tiltState.cleared) out.cleared = true;
    this.status = status;
    out.status = status;

    const justTriggered = leanState.activated || tiltState.activated;
    if (justTriggered && nowMs - this.lastAnnouncedAt >= this.cooldownMs) {
      const type = leanState.active && tiltState.active
        ? 'combined'
        : leanState.active
          ? 'forward_lean'
          : 'shoulder_tilt';
      const reason = REASON_TEXT[type];
      const alert = {
        type: 'posture_alert',
        alertType: type,
        title: reason.title,
        advice: reason.advice,
        measurement: type === 'forward_lean' ? lean : type === 'shoulder_tilt' ? tilt : Math.max(lean ?? 0, tilt ?? 0),
        threshold: type === 'forward_lean' ? this.leanThreshold : this.tiltThreshold,
        lean,
        tilt,
        timestamp: nowMs,
      };
      out.alerts.push(alert);
      out.shouldSpeak = true;
      this.lastAnnouncedAt = nowMs;
      this.lastAlert = alert;
    }
    return out;
  }

  /** Whether a new spoken alert is allowed by the audio cooldown. */
  canSpeak(nowMs = Date.now()) {
    return nowMs - this.lastSpokenAt >= this.cooldownMs;
  }

  markSpoken(nowMs = Date.now()) {
    this.lastSpokenAt = nowMs;
  }
}

export const BREAK_MESSAGES = [
  'Consider taking a short movement break.',
  'Look away from the screen for a while.',
  'Change your sitting position.',
  'Stand up, stretch and reset your shoulders.',
];

export class BreakReminder {
  constructor({ intervalMinutes = 30, enabled = true, messages = BREAK_MESSAGES } = {}) {
    this.intervalMs = intervalMinutes * 60 * 1000;
    this.enabled = enabled;
    this.messages = messages;
    this.startedAt = null;
    this.lastFiredAt = null;
    this.snoozeUntil = null;
    this.firedCount = 0;
  }

  configure({ intervalMinutes, enabled }) {
    if (Number.isFinite(intervalMinutes)) this.intervalMs = intervalMinutes * 60 * 1000;
    if (typeof enabled === 'boolean') this.enabled = enabled;
  }

  start(nowMs = Date.now()) {
    this.startedAt = nowMs;
    this.lastFiredAt = null;
    this.snoozeUntil = null;
  }

  stop() {
    this.startedAt = null;
  }

  /** @returns {{due:boolean, message?:string}} */
  update(nowMs = Date.now()) {
    if (!this.enabled || this.startedAt === null) return { due: false };
    if (this.snoozeUntil !== null) {
      if (nowMs < this.snoozeUntil) return { due: false };
      // A snooze that has elapsed fires immediately.
      this.snoozeUntil = null;
      return this._fire(nowMs);
    }
    const reference = this.lastFiredAt ?? this.startedAt;
    if (nowMs - reference >= this.intervalMs) return this._fire(nowMs);
    return { due: false };
  }

  _fire(nowMs) {
    const message = this.messages[this.firedCount % this.messages.length];
    this.lastFiredAt = nowMs;
    this.firedCount += 1;
    return { due: true, message };
  }

  snooze(minutes = 5, nowMs = Date.now()) {
    this.snoozeUntil = nowMs + minutes * 60 * 1000;
  }

  dismiss(nowMs = Date.now()) {
    this.lastFiredAt = nowMs;
    this.snoozeUntil = null;
  }

  get nextDueInMs() {
    const reference = this.lastFiredAt ?? this.startedAt;
    if (reference === null) return null;
    if (this.snoozeUntil !== null) return Math.max(0, this.snoozeUntil - Date.now());
    return Math.max(0, this.intervalMs - (Date.now() - reference));
  }
}

/**
 * Web Speech API wrapper.  Silently degrades (and reports why) when the browser
 * does not support speech synthesis, so monitoring keeps running.
 */
export class SpeechEngine {
  constructor(settings = {}) {
    this.supported = typeof window !== 'undefined' && 'speechSynthesis' in window;
    this.enabled = Boolean(settings.audioEnabled);
    this.volume = settings.volume ?? 0.8;
    this.rate = settings.rate ?? 1.05;
    this.pitch = settings.pitch ?? 1.05;
    this.voiceName = settings.voiceName ?? '';
    this.lastSpokenAt = 0;
    this.minIntervalMs = 2500;
    this.voices = [];
    if (this.supported) this._loadVoices();
  }

  _loadVoices() {
    try {
      this.voices = window.speechSynthesis.getVoices() || [];
      if (!this.voices.length) {
        window.speechSynthesis.addEventListener('voiceschanged', () => {
          this.voices = window.speechSynthesis.getVoices() || [];
        });
      }
    } catch (error) {
      this.voices = [];
    }
  }

  configure(settings = {}) {
    if (typeof settings.audioEnabled === 'boolean') this.enabled = settings.audioEnabled;
    if (Number.isFinite(settings.volume)) this.volume = settings.volume;
    if (Number.isFinite(settings.rate)) this.rate = settings.rate;
    if (typeof settings.voiceName === 'string') this.voiceName = settings.voiceName;
  }

  /** @returns {boolean} whether speech was actually queued */
  speak(text, nowMs = Date.now()) {
    if (!this.supported || !this.enabled || !text) return false;
    if (nowMs - this.lastSpokenAt < this.minIntervalMs) return false;
    try {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = Math.max(0.5, Math.min(2, this.rate));
      utterance.volume = Math.max(0, Math.min(1, this.volume));
      utterance.pitch = this.pitch;
      const voice = this.voices.find((v) => v.name === this.voiceName);
      if (voice) utterance.voice = voice;
      utterance.lang = voice?.lang || 'en-US';
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
      this.lastSpokenAt = nowMs;
      return true;
    } catch (error) {
      return false;
    }
  }

  cancel() {
    if (!this.supported) return;
    try {
      window.speechSynthesis.cancel();
    } catch (error) {
      /* ignore */
    }
  }

  listVoices() {
    return this.voices.map((v) => ({ name: v.name, lang: v.lang, default: v.default }));
  }
}

export const DEFAULT_POSTURE_SPEECH = 'Sit back and check your posture.';
