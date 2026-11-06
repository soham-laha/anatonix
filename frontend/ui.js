/**
 * Anatonix — DOM presentation layer for the monitoring component.
 *
 * All element lookups happen once; updates only touch textContent / classList /
 * widths so the live view never triggers layout thrash.  No framework, no
 * innerHTML writes with user data.
 */

import { eyeCropBox } from './posture.js';
import { LEFT_EYE, RIGHT_EYE } from './ocular.js';

const el = (id) => document.getElementById(id);

export class UI {
  constructor() {
    this.nodes = {
      root: el('anxRoot'),
      chipState: el('chipState'),
      chipStateText: el('chipStateText'),
      fps: el('fpsValue'),
      fpsChip: el('chipFps'),
      conf: el('confValue'),
      sessionTimer: el('sessionTimer'),
      audioBtn: el('btnAudio'),
      audioLabel: el('audioLabel'),
      alertBanner: el('alertBanner'),
      alertTitle: el('alertTitle'),
      alertDetail: el('alertDetail'),
      alertStamp: el('alertStamp'),
      alertDismiss: el('alertDismiss'),
      cameraLabel: el('cameraLabel'),
      stage: el('stage'),
      video: el('videoFeed'),
      poseCanvas: el('poseCanvas'),
      eyeCanvas: el('eyeCanvas'),
      chartCanvas: el('chartCanvas'),
      postureFlag: el('postureFlag'),
      hudLean: el('hudLean'),
      hudTilt: el('hudTilt'),
      hudConf: el('hudConf'),
      hudLatency: el('hudLatency'),
      standby: el('standby'),
      initBtn: el('btnInit'),
      initProgress: el('initProgress'),
      cameraError: el('cameraError'),
      pauseBtn: el('btnPause'),
      pauseLabel: el('pauseLabel'),
      stopBtn: el('btnStop'),
      calibrateBtn: el('btnCalibrate'),
      recalibrateBtn: el('btnRecalibrate'),
      eyeState: el('eyeState'),
      earValue: el('earValue'),
      blinkCount: el('blinkCount'),
      bpmValue: el('bpmValue'),
      blinkIndicator: el('blinkIndicator'),
      postureStateNote: el('postureStateNote'),
      leanReading: el('leanReading'),
      leanBar: el('leanBar'),
      tiltReading: el('tiltReading'),
      tiltBar: el('tiltBar'),
      proxReading: el('proxReading'),
      proxBar: el('proxBar'),
      alertReading: el('alertReading'),
      alertBar: el('alertBar'),
      loadBand: el('loadBand'),
      loadValue: el('loadValue'),
      qualityList: el('qualityList'),
      qualityHint: el('qualityHint'),
      qualityScore: el('qualityScore'),
      calibStatus: el('calibStatus'),
      calibPitch: el('calibPitch'),
      calibTilt: el('calibTilt'),
      calibSpan: el('calibSpan'),
      calibWhen: el('calibWhen'),
      calibBar: el('calibBar'),
      calibQuality: el('calibQuality'),
      blinkActivity: el('blinkActivity'),
      calibModal: el('calibModal'),
      calibModalTitle: el('calibTitle'),
      calibProgressBar: el('calibProgressBar'),
      calibProgressText: el('calibProgressText'),
      calibFramesText: el('calibFramesText'),
      calibStartBtn: el('btnCalibStart'),
      calibCancelBtn: el('btnCalibCancel'),
      footStatus: el('footStatus'),
      toastStack: el('toastStack'),
    };
    this.poseRenderer = new PoseRenderer(this.nodes.poseCanvas, this.nodes.video, {
      mirror: true,
    });
    this.eyeRenderer = new EyeRenderer(this.nodes.eyeCanvas);
    this._qualityRows = new Map();
  }

  /* ------------------------------------------------------------ header */
  setState(state, label, tone) {
    const { chipState, chipStateText } = this.nodes;
    if (!chipState) return;
    chipState.className = `chip is-${tone || 'idle'}`;
    chipStateText.textContent = label;
    chipState.dataset.state = state;
  }

  setTelemetryChips({ fps, cap, poseMs, faceMs, delegate, confidence, sessionSeconds }) {
    if (Number.isFinite(fps)) {
      // Show the cap next to the measured rate whenever inference is the
      // bottleneck, so a "30 fps cap" is never mistaken for an achieved 30 fps.
      const measured = fps.toFixed(0);
      const limited = Number.isFinite(cap) && fps < cap * 0.85;
      setText(this.nodes.fps, limited ? `${measured}/${cap} ` : measured);
      const parts = [
        'Measured landmark-inference rate.',
        Number.isFinite(cap) ? `Cap ${cap} fps.` : '',
        Number.isFinite(poseMs) || Number.isFinite(faceMs)
          ? `Cost per frame: pose ${Math.round(poseMs || 0)} ms + face ${Math.round(faceMs || 0)} ms.`
          : '',
        delegate ? `${delegate} delegate.` : '',
        limited ? 'Inference is the bottleneck — lower the cap or close other GPU-heavy tabs.' : '',
      ];
      setTitle(this.nodes.fpsChip || this.nodes.fps, parts.filter(Boolean).join(' '));
    }
    if (Number.isFinite(confidence)) setText(this.nodes.conf, `${Math.round(confidence * 100)}%`);
    if (Number.isFinite(sessionSeconds)) {
      setText(this.nodes.sessionTimer, formatClock(sessionSeconds));
    }
  }

  setAudio(enabled, supported = true) {
    const button = this.nodes.audioBtn;
    if (!button) return;
    button.classList.toggle('btn-primary', enabled);
    button.disabled = !supported;
    button.title = supported
      ? enabled
        ? 'Spoken alerts enabled — click to mute'
        : 'Spoken alerts disabled — click to enable'
      : 'Speech synthesis is not available in this browser';
    this.nodes.audioLabel.textContent = enabled ? 'Audio on' : 'Audio off';
    const icon = button.querySelector('i');
    if (icon) icon.className = `fa-solid ${enabled ? 'fa-volume-high' : 'fa-volume-xmark'}`;
  }

  setCameraLabel(text) {
    this.nodes.cameraLabel.textContent = text;
  }

  setFootStatus(text) {
    this.nodes.footStatus.textContent = text;
  }

  /* ------------------------------------------------------------- stage */
  showStandby(show, { error = null } = {}) {
    this.nodes.standby.classList.toggle('is-hidden', !show);
    if (error) {
      this.nodes.cameraError.hidden = false;
      this.nodes.cameraError.textContent = error;
    } else {
      this.nodes.cameraError.hidden = true;
      this.nodes.cameraError.textContent = '';
    }
  }

  setInitializing(active, steps = [], progressIndex = 0) {
    const container = this.nodes.initProgress;
    this.nodes.initBtn.disabled = active;
    this.nodes.initBtn.innerHTML = active
      ? '<i class="fa-solid fa-spinner fa-spin"></i> Initialising…'
      : '<i class="fa-solid fa-power-off"></i> Initialize camera';
    if (!active) {
      container.classList.remove('is-visible');
      container.textContent = '';
      return;
    }
    container.classList.add('is-visible');
    container.textContent = '';
    steps.forEach((step, index) => {
      const row = document.createElement('div');
      row.className = `step ${index < progressIndex ? 'is-done' : index === progressIndex ? 'is-active' : ''}`;
      const bullet = document.createElement('span');
      bullet.className = 'bullet';
      const text = document.createElement('span');
      text.textContent = step;
      row.append(bullet, text);
      container.appendChild(row);
    });
  }

  setControls({ cameraActive, paused }) {
    this.nodes.pauseBtn.disabled = !cameraActive;
    this.nodes.stopBtn.disabled = !cameraActive;
    this.nodes.calibrateBtn.disabled = !cameraActive;
    this.nodes.recalibrateBtn.disabled = !cameraActive;
    this.nodes.pauseLabel.textContent = paused ? 'Resume' : 'Pause';
    const icon = this.nodes.pauseBtn.querySelector('i');
    if (icon) icon.className = `fa-solid ${paused ? 'fa-play' : 'fa-pause'}`;
  }

  setPostureFlag(text, tone) {
    const flag = this.nodes.postureFlag;
    flag.textContent = text;
    flag.classList.toggle('is-warn', tone === 'warn');
    flag.classList.toggle('is-bad', tone === 'bad');
  }

  updateHud({ lean, tilt, confidence, latencyMs, leanTone, tiltTone }) {
    const { hudLean, hudTilt, hudConf, hudLatency } = this.nodes;
    hudLean.textContent = formatNumber(lean, 1, '°');
    hudTilt.textContent = formatNumber(tilt, 1, '°');
    hudLean.className = `val ${toneClass(leanTone)}`;
    hudTilt.className = `val ${toneClass(tiltTone)}`;
    hudConf.textContent = Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : '--';
    hudConf.className = `val ${confidence >= 0.5 ? 'is-good' : confidence > 0.25 ? 'is-warn' : 'is-bad'}`;
    hudLatency.textContent = Number.isFinite(latencyMs) ? latencyMs.toFixed(0) : '--';
  }

  /* ------------------------------------------------------------ ocular */
  updateOcular({ ear, blinks, bpm, category, eyeState, paused = false }) {
    setText(this.nodes.earValue, Number.isFinite(ear) ? ear.toFixed(3) : '--');
    setText(this.nodes.blinkCount, String(blinks ?? 0));
    setText(this.nodes.bpmValue, Number.isFinite(bpm) ? bpm.toFixed(1) : '--');
    setText(
      this.nodes.blinkIndicator,
      paused ? 'Paused for calibration' : category?.label ?? 'No data'
    );
    this.nodes.blinkIndicator.className = `value ${toneClass(paused ? 'dim' : category?.tone)}`;
    const pill = this.nodes.eyeState;
    setText(pill, paused ? 'Paused' : eyeState ?? 'No data');
    pill.classList.toggle('is-open', !paused && eyeState === 'Eyes open');
    pill.classList.toggle('is-closed', !paused && eyeState === 'Eyes closed');
  }

  /** Recent blink activity strip: one tick per blink, dimmer as it ages. */
  updateBlinkActivity(recent = []) {
    const host = this.nodes.blinkActivity;
    if (!host) return;
    const slots = 12;
    if (host.childElementCount !== slots) {
      host.textContent = '';
      for (let i = 0; i < slots; i += 1) host.appendChild(document.createElement('span'));
    }
    const children = host.children;
    for (let i = 0; i < slots; i += 1) {
      const node = children[i];
      const entry = recent[recent.length - slots + i];
      if (!entry) {
        node.className = '';
        continue;
      }
      node.className = entry.ageMs < 15000 ? 'is-active' : 'is-recent';
      node.title = `Blink ${Math.round(entry.ageMs / 1000)}s ago`;
    }
  }

  /* ------------------------------------------------------------ gauges */
  updateGauges({ lean, tilt, proximity, alerts, leanThreshold, tiltThreshold, neckLoad, neckLabel, state }) {
    setReading(this.nodes.leanReading, this.nodes.leanBar, lean, {
      unit: '°',
      digits: 1,
      max: Math.max(30, (leanThreshold || 12) * 2.5),
      warnAt: leanThreshold || 12,
      badAt: (leanThreshold || 12) * 1.6,
    });
    setReading(this.nodes.tiltReading, this.nodes.tiltBar, tilt, {
      unit: '°',
      digits: 1,
      max: Math.max(15, (tiltThreshold || 6) * 3),
      warnAt: tiltThreshold || 6,
      badAt: (tiltThreshold || 6) * 1.8,
    });
    const proximityTone = !Number.isFinite(proximity)
      ? 'idle'
      : proximity > 1.35 || proximity < 0.62
        ? 'warn'
        : 'good';
    this.nodes.proxReading.textContent = Number.isFinite(proximity) ? proximity.toFixed(2) : '--';
    this.nodes.proxReading.className = `reading ${toneClass(proximityTone)}`;
    this.nodes.proxBar.style.width = Number.isFinite(proximity)
      ? `${Math.min(100, Math.max(0, ((proximity - 0.5) / 1.2) * 100))}%`
      : '0%';
    this.nodes.proxBar.className = toneClass(proximityTone);

    this.nodes.alertReading.textContent = String(alerts ?? 0);
    this.nodes.alertReading.className = `reading ${alerts > 0 ? 'is-warn' : 'is-good'}`;
    this.nodes.alertBar.style.width = `${Math.min(100, (alerts ?? 0) * 12)}%`;
    this.nodes.alertBar.className = alerts > 3 ? 'is-warn' : 'is-good';

    this.nodes.loadValue.textContent = Number.isFinite(neckLoad) ? String(neckLoad) : '--';
    this.nodes.loadBand.textContent = neckLabel || '--';
    this.nodes.postureStateNote.textContent = state || '--';
  }

  updateQuality(quality) {
    const list = this.nodes.qualityList;
    if (!list) return;
    if (!quality || !quality.checks) {
      list.textContent = '';
      this.nodes.qualityScore.textContent = '--';
      return;
    }
    const okCount = quality.checks.filter((c) => c.ok).length;
    setText(this.nodes.qualityScore, `${okCount}/${quality.checks.length} checks`);
    // Drop rows for checks that are not part of this evaluation (for example
    // the face check while ocular inference is paused for calibration).
    const activeKeys = new Set(quality.checks.map((check) => check.key));
    for (const [key, row] of this._qualityRows) {
      if (!activeKeys.has(key)) {
        row.row.remove();
        this._qualityRows.delete(key);
      }
    }
    for (const check of quality.checks) {
      let row = this._qualityRows.get(check.key);
      if (!row) {
        row = document.createElement('div');
        row.className = 'quality-row';
        const icon = document.createElement('span');
        icon.className = 'icon';
        const label = document.createElement('span');
        label.className = 'label-text';
        row.append(icon, label);
        list.appendChild(row);
        this._qualityRows.set(check.key, { row, icon, label });
      }
      const { row: rowEl, icon, label } = this._qualityRows.get(check.key);
      rowEl.className = `quality-row ${check.ok ? 'is-ok' : check.detail?.includes('not') || !check.ok ? 'is-warn' : 'is-ok'}`;
      icon.className = `icon fa-solid ${check.ok ? 'fa-circle-check' : 'fa-circle-exclamation'}`;
      setText(label, check.detail || check.label);
    }
    const hint = this.nodes.qualityHint;
    if (quality.hints && quality.hints.length) {
      hint.classList.remove('is-empty');
      hint.textContent = quality.hints[0];
    } else {
      hint.classList.add('is-empty');
      hint.textContent = '';
    }
  }

  updateCalibration(calibration) {
    const has = calibration && Number.isFinite(calibration.pitch);
    this.nodes.calibStatus.textContent = has
      ? calibration.uncalibrated
        ? 'Raw (uncalibrated)'
        : 'Calibrated'
      : 'Not calibrated';
    this.nodes.calibPitch.textContent = has ? `${calibration.pitch.toFixed(1)}°` : '--';
    this.nodes.calibTilt.textContent = has ? `${calibration.shoulderTilt.toFixed(1)}°` : '--';
    this.nodes.calibSpan.textContent = has ? calibration.shoulderSpan.toFixed(3) : '--';
    this.nodes.calibWhen.textContent = calibration?.createdAt
      ? formatTimestamp(calibration.createdAt)
      : 'never';
    const quality = has ? calibration.quality : null;
    this.nodes.calibBar.style.width = Number.isFinite(quality) ? `${Math.round(quality * 100)}%` : '0%';
    this.nodes.calibQuality.textContent = Number.isFinite(quality)
      ? `${Math.round(quality * 100)}%`
      : '--';
  }

  /* ------------------------------------------------------------- alerts */
  showAlert({ title, detail, timestamp, soft = false }) {
    const banner = this.nodes.alertBanner;
    banner.hidden = false;
    banner.classList.toggle('is-soft', soft);
    this.nodes.alertTitle.textContent = title;
    this.nodes.alertDetail.innerHTML = '';
    const span = document.createElement('span');
    span.textContent = detail;
    this.nodes.alertDetail.appendChild(span);
    this.nodes.alertStamp.textContent = formatTime(timestamp);
  }

  hideAlert() {
    this.nodes.alertBanner.hidden = true;
  }

  /* ------------------------------------------------------------ modal */
  showCalibrationModal(show) {
    this.nodes.calibModal.classList.toggle('is-hidden', !show);
  }

  setCalibrationProgress(progress, text, frames, capturing) {
    const pct = Math.round(Math.min(1, Math.max(0, progress)) * 100);
    const width = `${pct}%`;
    if (this.nodes.calibProgressBar.style.width !== width) {
      this.nodes.calibProgressBar.style.width = width;
    }
    setText(this.nodes.calibProgressText, text || `${pct}%`);
    setText(this.nodes.calibFramesText, frames ?? '');
    const disabled = Boolean(capturing);
    if (this.nodes.calibStartBtn.disabled !== disabled) {
      this.nodes.calibStartBtn.disabled = disabled;
      setText(this.nodes.calibStartBtn, disabled ? 'Capturing…' : 'Start capture');
    }
  }

  /* ------------------------------------------------------------ toast */
  toast(message, kind = 'info', timeout = 4200) {
    const stack = this.nodes.toastStack;
    if (!stack) return;
    const node = document.createElement('div');
    node.className = `toast ${kind === 'error' ? 'is-error' : kind === 'ok' ? 'is-ok' : ''}`;
    node.textContent = message;
    stack.appendChild(node);
    setTimeout(() => {
      node.style.opacity = '0';
      setTimeout(() => node.remove(), 220);
    }, timeout);
  }

  setTheme(theme, density) {
    const resolved = theme === 'light' ? 'light' : 'dark';
    this.nodes.root.dataset.theme = resolved;
    this.nodes.root.dataset.density = density === 'compact' ? 'compact' : 'comfortable';
    // <html> carries the theme too so page-level surfaces (body, scrollbars)
    // pick up the same palette as the component tree.
    document.documentElement.dataset.theme = resolved;
  }

  /* --------------------------------------------------------- rendering */
  drawFrame(frame) {
    this.poseRenderer.draw(frame);
    this.eyeRenderer.draw({ ...frame, video: this.nodes.video });
  }
}

function setReading(readingEl, barEl, value, options) {
  const { unit = '', digits = 1, max = 30, warnAt, badAt } = options;
  if (!Number.isFinite(value)) {
    readingEl.textContent = '--';
    readingEl.className = 'reading is-idle';
    barEl.style.width = '0%';
    barEl.className = '';
    return;
  }
  readingEl.textContent = `${value.toFixed(digits)}${unit}`;
  const tone = value >= badAt ? 'bad' : value >= warnAt ? 'warn' : 'good';
  readingEl.className = `reading ${toneClass(tone)}`;
  barEl.style.width = `${Math.min(100, Math.max(2, (value / max) * 100))}%`;
  barEl.className = toneClass(tone);
}

function toneClass(tone) {
  if (tone === 'good' || tone === 'ok') return 'is-good';
  if (tone === 'warn' || tone === 'warning') return 'is-warn';
  if (tone === 'bad' || tone === 'alert' || tone === 'critical') return 'is-bad';
  return 'is-idle';
}

export function formatNumber(value, digits = 1, suffix = '') {
  if (!Number.isFinite(value)) return '--';
  return `${value.toFixed(digits)}${suffix}`;
}

export function formatClock(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  return [hours, minutes, secs].map((v) => String(v).padStart(2, '0')).join(':');
}

export function formatTime(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--:--';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function formatTimestamp(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return 'never';
  return date.toLocaleString([], {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/* ==========================================================================
   Canvas renderers
   ========================================================================== */

/** Read a design token from the live theme (falls back to the dark palette). */
function token(name, fallback) {
  if (typeof window === 'undefined' || !document.documentElement) return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(`--anx-${name}`)
    .trim();
  return value || fallback;
}

const palette = () => ({
  accent: token('accent', '#d9d3c4'),
  accentFill: `color-mix(in srgb, ${token('accent', '#d9d3c4')} 26%, transparent)`,
  critical: token('critical', '#c0756c'),
  warning: token('warning', '#cbab74'),
  positive: token('positive', '#8fb99a'),
  purple: token('purple', '#a49a87'),
  text: token('text', '#eae7e1'),
  muted: token('text-muted', '#9c968c'),
  dim: token('text-dim', '#736e66'),
  videoBg: token('video-bg', '#0a0a09'),
});

/**
 * Write text/attributes only when they actually change.  The monitoring loop
 * runs many times per second and unconditional writes force needless style
 * recalculation and repaints over the live canvas.
 */
export function setText(node, value) {
  if (!node) return;
  const next = value == null ? '' : String(value);
  if (node.textContent !== next) node.textContent = next;
}

export function setTitle(node, value) {
  if (!node || !value) return;
  if (node.getAttribute('title') !== value) node.setAttribute('title', value);
}

export class PoseRenderer {
  constructor(canvas, video, options = {}) {
    this.canvas = canvas;
    this.video = video;
    this.ctx = canvas.getContext('2d');
    this.mirror = options.mirror !== false;
    this.resize();
  }

  /** Mirror the preview <video> and the overlay together. */
  setMirror(mirror) {
    this.mirror = Boolean(mirror);
    const stage = this.canvas?.closest('.stage');
    if (stage) stage.classList.toggle('is-unmirrored', !this.mirror);
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(240, rect.width || this.canvas.clientWidth || 640);
    const height = Math.max(180, rect.height || this.canvas.clientHeight || 480);
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
    this.width = width;
    this.height = height;
    this.dpr = dpr;
  }

  clear() {
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.width, this.height);
  }


  /**
   * Map the video onto the canvas with "cover" semantics so the preview never
   * distorts when the panel's aspect ratio differs from the camera's.
   */
  _fit(frameWidth, frameHeight) {
    const scale = Math.max(this.width / frameWidth, this.height / frameHeight);
    const drawWidth = frameWidth * scale;
    const drawHeight = frameHeight * scale;
    return {
      scale,
      dx: (this.width - drawWidth) / 2,
      dy: (this.height - drawHeight) / 2,
      drawWidth,
      drawHeight,
    };
  }

  draw({ pose, face, ears, shoulders, frameWidth, frameHeight, status }) {
    const { ctx, width, height } = this;
    if (!width || !height) return;
    this.clear();
    const C = palette();

    const fit = frameWidth && frameHeight ? this._fit(frameWidth, frameHeight) : null;
    // The video frames themselves are painted by the browser (see #videoFeed);
    // this canvas only carries the landmark overlay, so it stays transparent.
    const map = fit || { dx: 0, dy: 0, drawWidth: width, drawHeight: height, scale: 1 };
    const xOf = (point) =>
      map.dx + (this.mirror ? (1 - point.x) : point.x) * map.drawWidth;
    const yOf = (point) => map.dy + point.y * map.drawHeight;

    if (face && face.length > 400) {
      ctx.fillStyle = C.accent;
      for (const index of [33, 133, 362, 263, 168, 1, 61, 291]) {
        const point = face[index];
        if (!point) continue;
        ctx.beginPath();
        ctx.arc(xOf(point), yOf(point), 1.4, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    if (!pose) return;

    const jointColour = status === 'alert' ? C.critical : status === 'elevated' ? C.warning : C.positive;

    // shoulder alignment line
    const leftShoulder = pose[11];
    const rightShoulder = pose[12];
    if (leftShoulder && rightShoulder) {
      const xs = [xOf(leftShoulder), xOf(rightShoulder)];
      const ys = [yOf(leftShoulder), yOf(rightShoulder)];
      const ext = 0.12 * width;
      const angle = Math.atan2(ys[1] - ys[0], xs[1] - xs[0]);
      ctx.strokeStyle = C.positive;
      ctx.lineWidth = 1.6;
      ctx.setLineDash([7, 5]);
      ctx.beginPath();
      ctx.moveTo(xs[0] - Math.cos(angle) * ext, ys[0] - Math.sin(angle) * ext);
      ctx.lineTo(xs[1] + Math.cos(angle) * ext, ys[1] + Math.sin(angle) * ext);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // ear-to-shoulder reference vector
    const leftEar = pose[7];
    const rightEar = pose[8];
    if (leftEar && rightEar && leftShoulder && rightShoulder) {
      const earX = (xOf(leftEar) + xOf(rightEar)) / 2;
      const earY = (yOf(leftEar) + yOf(rightEar)) / 2;
      const shoulderX = (xOf(leftShoulder) + xOf(rightShoulder)) / 2;
      const shoulderY = (yOf(leftShoulder) + yOf(rightShoulder)) / 2;

      // the lean vector is the primary overlay cue: neutral when calm,
      // critical when the posture engine raises an alert
      const vectorColour = status === 'alert' ? C.critical : C.accent;
      ctx.strokeStyle = vectorColour;
      ctx.lineWidth = 2;
      ctx.shadowColor = vectorColour;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      ctx.moveTo(shoulderX, shoulderY);
      ctx.lineTo(earX, earY);
      ctx.stroke();
      ctx.shadowBlur = 0;

      // vertical reference through the shoulder midpoint
      ctx.strokeStyle = C.muted;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(shoulderX, shoulderY);
      ctx.lineTo(shoulderX, earY);
      ctx.stroke();
      ctx.setLineDash([]);

      // landmarks
      for (const [x, y, colour] of [
        [earX, earY, C.purple],
        [shoulderX, shoulderY, jointColour],
      ]) {
        ctx.fillStyle = colour;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = C.videoBg;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // ear / shoulder dots
      for (const point of [leftEar, rightEar, leftShoulder, rightShoulder]) {
        ctx.fillStyle = jointColour;
        ctx.beginPath();
        ctx.arc(xOf(point), yOf(point), 2.6, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // upper-arm hint
    if (pose[13] && pose[14]) {
      ctx.strokeStyle = C.dim;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(xOf(pose[11]), yOf(pose[11]));
      ctx.lineTo(xOf(pose[13]), yOf(pose[13]));
      ctx.moveTo(xOf(pose[12]), yOf(pose[12]));
      ctx.lineTo(xOf(pose[14]), yOf(pose[14]));
      ctx.stroke();
    }
  }
}

export class EyeRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.lastBox = null;
  }

  draw({ face, frameWidth, frameHeight, video, faceSuppressed }) {
    const { ctx, canvas } = this;
    const C = palette();
    const source = video;
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!face || !frameWidth || !frameHeight || !source || source.readyState < 2) {
      ctx.fillStyle = C.videoBg;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = C.muted;
      ctx.font = '12px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(
        faceSuppressed ? 'Ocular paused for calibration' : 'Ocular crop inactive',
        canvas.width / 2,
        canvas.height / 2
      );
      ctx.textAlign = 'left';
      return;
    }

    const box = eyeCropBox(face, frameWidth, frameHeight, 1.45);
    if (!box) {
      ctx.fillStyle = C.videoBg;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      return;
    }
    this.lastBox = box;

    ctx.save();
    // mirror the crop horizontally so it matches the mirrored main preview
    ctx.translate(canvas.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(
      source,
      box.sx, box.sy, box.sw, box.sh,
      0, 0, canvas.width, canvas.height
    );
    ctx.restore();

    // eye outline markers in crop space
    const toCanvas = (point) => ({
      x: canvas.width - ((point.x * frameWidth - box.sx) / box.sw) * canvas.width,
      y: ((point.y * frameHeight - box.sy) / box.sh) * canvas.height,
    });

    ctx.strokeStyle = C.accent;
    ctx.lineWidth = 1.1;
    for (const eye of [LEFT_EYE, RIGHT_EYE]) {
      const points = [eye.outer, eye.upper1, eye.inner, eye.lower1].map((i) => face[i]);
      if (points.some((p) => !p)) continue;
      ctx.beginPath();
      points.forEach((point, index) => {
        const { x, y } = toCanvas(point);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
      ctx.stroke();
    }
  }
}
