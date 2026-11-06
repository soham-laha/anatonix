/**
 * Anatonix — monitoring component controller.
 *
 * Wires together: camera/vision runtime, posture + ocular engines, calibration,
 * alerting, telemetry batching and the Streamlit bridge.  Everything here runs
 * in the user's browser; the only data that leaves the tab is numeric telemetry
 * that Python persists locally in SQLite.
 */

import { StreamlitBridge, autoSize } from './bridge.js';
import { VisionEngine, VISION_STATE, stateLabel } from './vision.js';
import {
  classifyPosture,
  ema,
  forwardLean,
  eyeCropBox,
  neckLoad,
  neckLoadLabel,
  proximityRatio,
  rawPitch,
  shoulderGeometry,
  shoulderTilt,
} from './posture.js';
import { BlinkDetector, averageEAR, blinkCategory } from './ocular.js';
import { CalibrationCollector, qualityLabel } from './calibration.js';
import {
  BreakReminder,
  DEFAULT_POSTURE_SPEECH,
  PostureAlertEngine,
  SpeechEngine,
} from './alerts.js';
import { TelemetryRecorder, makeSessionId, makeUid } from './telemetry.js';
import { LiveChart } from './charts.js';
import { UI, formatClock } from './ui.js';

const INIT_STEPS = [
  'Loading vision runtime',
  'Preparing WebAssembly runtime',
  'Loading pose model',
  'Loading ocular model',
  'Requesting camera permission',
  'Starting monitoring loop',
];

const DEFAULT_SETTINGS = {
  forward_lean_threshold: 12,
  shoulder_tilt_threshold: 6,
  bad_frame_count: 20,
  recovery_frame_count: 15,
  alert_cooldown_seconds: 3.5,
  sample_interval_ms: 1000,
  inference_fps: 15,
  inference_duty: 0.7,
  smoothing_alpha: 0.25,
  ear_threshold: 0.19,
  blink_min_duration_ms: 60,
  blink_debounce_ms: 120,
  blink_low_bpm: 8,
  blink_reference_bpm: 12,
  audio_enabled: false,
  audio_volume: 0.8,
  speech_rate: 1.05,
  voice_name: '',
  break_reminders_enabled: true,
  break_interval_minutes: 30,
  break_audio: false,
  theme: 'dark',
  density: 'comfortable',
  camera_device_id: '',
  show_neck_load: true,
};

class AnatonixApp {
  constructor() {
    this.bridge = new StreamlitBridge();
    this.ui = new UI();
    this.settings = { ...DEFAULT_SETTINGS };
    this.args = {};
    this.calibration = null; // { pitch, shoulderTilt, shoulderSpan, quality, createdAt }
    this.calibrationSetAt = 0; // local timestamp of the last in-page capture
    this.lastFrame = null;     // most recent inference result, redrawn by the render loop
    this.renderHandle = null;
    this.renderMode = null;
    this.renderActive = false;
    this.lastDrawnVideoTime = -1;
    this.lastDrawAt = 0;
    this.overlayDirty = true;
    this.uncalibrated = true;

    this.sessionId = null;
    this.sessionStartedAt = null;
    this.pausedTotalMs = 0;
    this.pausedAt = null;
    this.userPaused = false;
    this.monitoring = false;

    this.alertCount = 0;
    this.lastQuality = null;
    this.lastQualityAt = 0;
    this.lastUiAt = 0;
    this.lastSessionFlushAt = 0;

    this.smoothed = { lean: null, tilt: null, ear: null, proximity: null };
    this.latest = {
      lean: null,
      tilt: null,
      ear: null,
      proximity: null,
      bpm: 0,
      confidence: 0,
      status: 'untracked',
    };

    this.blinkDetector = new BlinkDetector({
      threshold: this.settings.ear_threshold,
      minDurationMs: this.settings.blink_min_duration_ms,
      debounceMs: this.settings.blink_debounce_ms,
    });
    this.calibrationCollector = new CalibrationCollector({
      targetFrames: 45,
      sampleIntervalMs: 55,
      minVisibility: 0.5,
    });
    this.alertEngine = new PostureAlertEngine({
      leanThreshold: this.settings.forward_lean_threshold,
      tiltThreshold: this.settings.shoulder_tilt_threshold,
      badFrames: this.settings.bad_frame_count,
      recoveryFrames: this.settings.recovery_frame_count,
      cooldownSeconds: this.settings.alert_cooldown_seconds,
    });
    this.speech = new SpeechEngine({
      audioEnabled: this.settings.audio_enabled,
      volume: this.settings.audio_volume,
      rate: this.settings.speech_rate,
      voiceName: this.settings.voice_name,
    });
    this.breaks = new BreakReminder({
      intervalMinutes: this.settings.break_interval_minutes,
      enabled: this.settings.break_reminders_enabled,
    });
    this.recorder = new TelemetryRecorder({
      sampleIntervalMs: this.settings.sample_interval_ms,
      sendFn: (payload) => this.bridge.sendValue(payload),
    });
    this.chart = new LiveChart(this.ui.nodes.chartCanvas, { windowMs: 60000 });

    this.vision = new VisionEngine({
      video: this.ui.nodes.video,
      inferenceFps: this.settings.inference_fps,
      onFrame: (frame) => this.handleFrame(frame),
      onState: (state, detail) => this.handleState(state, detail),
      onError: (error) => this.handleError(error),
      onCameras: (cameras) => this.handleCameras(cameras),
    });

    this.bindEvents();
    this.bridge.onRender((args) => this.applyArgs(args));
    this.bridge.ready();
    autoSize(this.bridge, this.ui.nodes.root);

    this.applyArgs(this.bridge.args || {});
    this.renderCalibration();
    this.ui.setAudio(this.speech.enabled, this.speech.supported);
    this.ui.setControls({ cameraActive: false, paused: false });
    this.startClock();
  }

  /* --------------------------------------------------------------- setup */
  bindEvents() {
    const { nodes } = this.ui;
    nodes.initBtn?.addEventListener('click', () => this.initializeCamera());
    nodes.pauseBtn?.addEventListener('click', () => this.togglePause());
    nodes.stopBtn?.addEventListener('click', () => this.stopMonitoring({ userInitiated: true }));
    nodes.calibrateBtn?.addEventListener('click', () => this.openCalibration());
    nodes.recalibrateBtn?.addEventListener('click', () => this.openCalibration());
    nodes.calibStartBtn?.addEventListener('click', () => this.beginCalibrationCapture());
    nodes.calibCancelBtn?.addEventListener('click', () => this.closeCalibration());
    nodes.audioBtn?.addEventListener('click', () => this.toggleAudio());
    nodes.alertDismiss?.addEventListener('click', () => this.ui.hideAlert());

    document.addEventListener('keydown', (event) => {
      const target = event.target;
      const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA');
      if (typing) return;
      if (event.key === 'c' || event.key === 'C') {
        if (this.vision.stream) {
          event.preventDefault();
          this.openCalibration();
        }
      } else if (event.key === 'Escape') {
        if (!nodes.calibModal.classList.contains('is-hidden')) this.closeCalibration();
      }
    });

    window.addEventListener('beforeunload', () => this.shutdown('window_unload'));
    window.addEventListener('pagehide', () => this.shutdown('page_hide'));
    window.addEventListener('resize', () => {
      this.ui.poseRenderer.resize();
      this.chart.resize();
    });

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (this.monitoring && !this.userPaused) {
          this.vision.pause();
          this.flushTelemetry(true);
        }
      } else if (this.monitoring && !this.userPaused && this.vision.paused) {
        this.vision.resume();
      }
    });
  }

  applyArgs(args) {
    this.args = args || {};
    if (args.settings) {
      this.settings = { ...DEFAULT_SETTINGS, ...args.settings };
      this.configureEngines();
    }
    if (args.calibration) {
      this.calibration = args.calibration;
      this.uncalibrated = Boolean(args.calibration.uncalibrated);
      this.calibrationSetAt = 0;
    } else if (this.calibration && Date.now() - (this.calibrationSetAt || 0) < 20000) {
      // We just captured a calibration and the props we are receiving were
      // computed before it reached the database. Keep showing the local
      // baseline instead of flickering back to "not calibrated"; after 20 s the
      // server is authoritative (which is also how a deletion propagates).
    } else {
      this.calibration = null;
      this.uncalibrated = true;
    }
    this.ui.setTheme(this.settings.theme, this.settings.density);
    this.ui.setAudio(this.speech.enabled && this.speech.supported, this.speech.supported);
    this.renderCalibration();
    this.chart.setThresholds({
      lean: this.settings.forward_lean_threshold,
      tilt: this.settings.shoulder_tilt_threshold,
    });
    if (typeof args.footNote === 'string') this.ui.setFootStatus(args.footNote);
  }

  configureEngines() {
    const s = this.settings;
    this.alertEngine.configure({
      leanThreshold: s.forward_lean_threshold,
      tiltThreshold: s.shoulder_tilt_threshold,
      badFrames: s.bad_frame_count,
      recoveryFrames: s.recovery_frame_count,
      cooldownSeconds: s.alert_cooldown_seconds,
    });
    this.blinkDetector.configure({
      threshold: s.ear_threshold,
      minDurationMs: s.blink_min_duration_ms,
      debounceMs: s.blink_debounce_ms,
    });
    this.speech.configure({
      audioEnabled: s.audio_enabled,
      volume: s.audio_volume,
      rate: s.speech_rate,
      voiceName: s.voice_name,
    });
    this.breaks.configure({
      intervalMinutes: s.break_interval_minutes,
      enabled: s.break_reminders_enabled,
    });
    this.recorder.sampleIntervalMs = s.sample_interval_ms;
    this.vision.setInferenceFps(s.inference_fps);
    this.vision.setDutyLimit?.(s.inference_duty);
    // mirror_preview was previously ignored: the canvas drew its own mirrored
    // copy of the video. The video element is now the preview, so apply it here.
    const mirror = s.mirror_preview !== false;
    this.ui.poseRenderer?.setMirror(mirror);
    if (this.ui.eyeRenderer) this.ui.eyeRenderer.mirror = mirror;
  }

  startClock() {
    setInterval(() => {
      if (!this.monitoring || !this.sessionStartedAt) return;
      this.ui.setTelemetryChips({
        sessionSeconds: this.sessionDurationSeconds(),
        fps: null,
        confidence: null,
      });
    }, 1000);
  }

  sessionDurationSeconds() {
    if (!this.sessionStartedAt) return 0;
    const now = Date.now();
    const pausedNow = this.pausedAt ? now - this.pausedAt : 0;
    return (now - this.sessionStartedAt - this.pausedTotalMs - pausedNow) / 1000;
  }

  /* ------------------------------------------------------------- camera */
  async initializeCamera() {
    if (this.vision.stream && this.monitoring) return;
    this.ui.showStandby(true);
    this.ui.setInitializing(true, INIT_STEPS, 0);
    this.ui.setCameraLabel('Initialising…');

    try {
      this.ui.setInitializing(true, INIT_STEPS, 0);
      await this.vision.loadModels(
        {
          bundleUrl: this.args.vision?.bundleUrl,
          wasmBase: this.args.vision?.wasmBase,
          poseModelUrl: this.args.vision?.poseModelUrl,
          faceModelUrl: this.args.vision?.faceModelUrl,
        },
        (stage) => {
          const index = INIT_STEPS.findIndex((step) => step.startsWith(stage.split(' ')[0]));
          this.ui.setInitializing(true, INIT_STEPS, Math.max(0, index));
        }
      );
      this.ui.setInitializing(true, INIT_STEPS, 4);
      await this.vision.startCamera({
        deviceId: this.settings.camera_device_id || '',
      });
      this.ui.setInitializing(true, INIT_STEPS, 5);

      this.startSession();
      this.vision.start();
      this.ui.setInitializing(false);
      this.ui.showStandby(false);
      this.ui.setControls({ cameraActive: true, paused: false });
      this.ui.setCameraLabel(
        this.ui.nodes.cameraLabel.textContent.includes(':')
          ? this.ui.nodes.cameraLabel.textContent
          : 'Live stream · local processing'
      );
      this.chart.clear();
      this.ui.toast('Camera initialised — monitoring started.', 'ok');
    } catch (error) {
      this.handleError(error, { duringInit: true });
      this.ui.setInitializing(false);
      this.ui.showStandby(true, { error: error.message || 'Camera initialisation failed.' });
      this.ui.setCameraLabel('Camera unavailable');
    }
  }

  startSession() {
    this.sessionId = makeSessionId();
    this.sessionStartedAt = Date.now();
    this.pausedTotalMs = 0;
    this.pausedAt = null;
    this.userPaused = false;
    this.monitoring = true;
    this.alertCount = 0;
    this.smoothed = { lean: null, tilt: null, ear: null, proximity: null };
    this.blinkDetector.reset();
    this.blinkDetector.sessionStart = Date.now();
    this.alertEngine.reset();
    this.breaks.start(Date.now());
    this.startRenderLoop();
    this.recorder.startSession(this.sessionId);
    this.recorder.addEvent('monitoring_started', {
      message: 'Monitoring started',
      details: {
        calibrated: !this.uncalibrated,
        calibration_id: this.calibration?.calibrationId ?? null,
      },
    });
    this.flushTelemetry(true);
    this.ui.setFootStatus(`Session ${this.sessionId.slice(0, 10)} · recording`);
  }

  togglePause() {
    if (!this.monitoring) return;
    this.userPaused = !this.userPaused;
    if (this.userPaused) {
      this.pausedAt = Date.now();
      this.vision.pause();
      this.ui.setPostureFlag('Paused', 'warn');
      this.recorder.addEvent('monitoring_paused', { message: 'Monitoring paused by user' });
      this.flushTelemetry(true);
    } else {
      if (this.pausedAt) {
        this.pausedTotalMs += Date.now() - this.pausedAt;
        this.pausedAt = null;
      }
      this.vision.resume();
      this.recorder.addEvent('monitoring_resumed', { message: 'Monitoring resumed' });
      this.flushTelemetry(true);
    }
    this.ui.setControls({ cameraActive: true, paused: this.userPaused });
    this.ui.setFootStatus(
      this.userPaused ? 'Monitoring paused' : `Session ${this.sessionId.slice(0, 10)} · recording`
    );
  }

  async stopMonitoring({ userInitiated = true } = {}) {
    this.stopRenderLoop();
    this.lastFrame = null;
    this.vision.setFaceEnabled(true);
    if (!this.monitoring) {
      await this.vision.stopCamera();
      this.ui.showStandby(true);
      this.ui.setControls({ cameraActive: false, paused: false });
      return;
    }
    this.monitoring = false;
    this.speech.cancel();
    if (userInitiated) {
      this.recorder.addEvent('monitoring_stopped', { message: 'Monitoring stopped' });
    }
    this.flushTelemetry(true);
    await this.vision.stopCamera();
    this.monitoring = false;
    this.ui.showStandby(true);
    this.ui.setInitializing(false);
    this.ui.setControls({ cameraActive: false, paused: false });
    this.ui.setCameraLabel('Camera idle');
    this.ui.setPostureFlag('Standby', 'idle');
    this.ui.setFootStatus('Session complete — data saved locally');
    this.chart.clear();
    this.recorder.endSession();
    this.sessionStartedAt = null;
    if (userInitiated) this.ui.toast('Monitoring stopped. Session saved.', 'ok');
  }

  async shutdown(reason) {
    if (this.monitoring) {
      this.recorder.addEvent('monitoring_stopped', {
        message: `Monitoring stopped (${reason.replace('_', ' ')})`,
      });
      this.flushTelemetry(true);
      this.monitoring = false;
    }
    this.speech.cancel();
    await this.vision.stopCamera();
  }

  handleCameras(cameras) {
    if (!cameras?.length) return;
    const active = cameras.find((camera) => camera.id === this.vision.deviceId);
    this.ui.setCameraLabel(active ? active.label : `${cameras.length} camera(s) detected`);
    this.recorder.setLive({
      ...this.recorder.live,
      cameras: cameras.map((camera) => ({ id: camera.id, label: camera.label })),
      active_camera: active?.label || '',
    });
  }

  /* ------------------------------------------------------------- frames */
  handleFrame(frame) {
    const { nowMs, pose, face, ears, shoulders, confidence, frameWidth, frameHeight } = frame;
    this.lastFrame = frame;
    this.overlayDirty = true;
    const aspect = frameWidth && frameHeight ? frameWidth / frameHeight : 4 / 3;

    // ---- ocular -----------------------------------------------------
    const earRaw = face ? averageEAR(face, aspect) : null;
    const blinkEvent = this.blinkDetector.update(earRaw, Date.now());
    const bpm = this.blinkDetector.blinkRate(Date.now());
    if (blinkEvent.blink) {
      this.chart.push(nowMs, { lean: this.latest.lean, tilt: this.latest.tilt, ear: earRaw });
    }

    // ---- posture ----------------------------------------------------
    let leanRaw = null;
    let tiltRaw = null;
    let spanRaw = null;
    let proximity = null;

    if (pose && ears && shoulders) {
      const baselinePitch = this.uncalibrated ? 0 : this.calibration?.pitch ?? 0;
      const baselineTilt = this.uncalibrated ? 0 : this.calibration?.shoulderTilt ?? 0;
      const baselineSpan = this.calibration?.shoulderSpan ?? null;

      leanRaw = forwardLean([pose[7], pose[8], pose[11], pose[12]], baselinePitch);
      const geometry = shoulderGeometry(pose[11], pose[12]);
      tiltRaw = geometry ? Math.abs(geometry.tiltDegrees - baselineTilt) : null;
      spanRaw = geometry ? geometry.span : null;
      proximity = proximityRatio(spanRaw, baselineSpan);
    }

    const alpha = this.settings.smoothing_alpha;
    this.smoothed.lean = ema(this.smoothed.lean, leanRaw, alpha);
    this.smoothed.tilt = ema(this.smoothed.tilt, tiltRaw, alpha);
    this.smoothed.ear = ema(this.smoothed.ear, earRaw, 0.5);
    this.smoothed.proximity = ema(this.smoothed.proximity, proximity, 0.15);

    const lean = this.smoothed.lean;
    const tilt = this.smoothed.tilt;
    const ear = this.smoothed.ear;
    const prox = this.smoothed.proximity;

    const usable = pose && (lean !== null || tilt !== null);

    // ---- alert engine ----------------------------------------------
    const alertOutcome = this.alertEngine.update({
      lean: usable ? lean : null,
      tilt: usable ? tilt : null,
      nowMs: Date.now(),
    });
    let status = alertOutcome.status;
    if (status === 'good' && (ear === null && !face)) status = 'untracked';

    if (alertOutcome.alerts.length) {
      for (const alert of alertOutcome.alerts) {
        this.alertCount += 1;
        this.recorder.addEvent('posture_alert', {
          alert_type: alert.alertType,
          message: `${alert.title}. ${alert.advice}`,
          measurement: round(alert.measurement),
          threshold: round(alert.threshold),
          lean: round(alert.lean),
          tilt: round(alert.tilt),
        });
        this.ui.showAlert({
          title: alert.title,
          detail: `${alert.advice} Lean ${fmt(alert.lean, '°')} · Tilt ${fmt(alert.tilt, '°')}`,
          timestamp: Date.now(),
        });
        this.lastAlertAt = Date.now();
      }
      if (alertOutcome.shouldSpeak) {
        const spoken = this.speech.speak(DEFAULT_POSTURE_SPEECH);
        if (spoken) {
          this.alertEngine.markSpoken(Date.now());
          this.recorder.addEvent('tracking_degraded', {
            message: 'Spoken posture alert delivered',
            details: { audio: true },
          });
        }
      }
      this.flushTelemetry(true);
    }
    if (alertOutcome.cleared) {
      this.ui.hideAlert();
      this.ui.toast('Posture recovered.', 'ok', 2600);
    }

    // ---- camera quality (≈1 Hz) -------------------------------------
    if (nowMs - this.lastQualityAt > 1000) {
      this.lastQualityAt = nowMs;
      this.lastQuality = this.vision.quality.evaluate({
        pose,
        face,
        ears,
        shoulders,
        proximity: prox,
        nowMs,
        skipFace: Boolean(frame.faceSuppressed),
      });
      const cap = frame.fpsCap;
      if (Number.isFinite(frame.fps) && Number.isFinite(cap) && frame.fps < cap * 0.8) {
        // Explain a measured rate that is below the configured cap instead of
        // silently showing a number the user cannot reconcile.
        this.lastQuality = {
          ...this.lastQuality,
          hints: [
            ...(this.lastQuality.hints || []),
            `Inference ${frame.fps.toFixed(0)} fps of a ${cap} fps cap — ` +
              `${Math.round(frame.inferenceMs)} ms/frame (pose ${Math.round(frame.poseMs)} ms, ` +
              `face ${Math.round(frame.faceMs)} ms, ${frame.delegate} delegate). Lower the cap ` +
              `in Settings → Monitoring, or close other GPU-heavy tabs.`,
          ],
        };
      }
      this.ui.updateQuality(this.lastQuality);
    }

    // ---- calibration capture ----------------------------------------
    if (this.calibrationCollector.active && usable) {
      const pitchNow = pose ? rawPitch(midOf(pose[7], pose[8]), midOf(pose[11], pose[12])) : null;
      const geometry = pose ? shoulderGeometry(pose[11], pose[12]) : null;
      if (pitchNow !== null && geometry) {
        this.calibrationCollector.add(
          {
            pitch: pitchNow,
            shoulderTilt: geometry.tiltDegrees,
            shoulderSpan: geometry.span,
            visibility: confidence,
          },
          Date.now()
        );
        this.updateCalibrationProgress();
      }
    }

    // ---- state ------------------------------------------------------
    const state = this.deriveState({ pose, face, confidence, frame });
    this.handleState(state);

    // ---- UI (≈15 Hz; every write below is diffed inside ui.js) --------
    if (nowMs - this.lastUiAt > 66) {
      this.lastUiAt = nowMs;
      this.ui.updateHud({
        lean,
        tilt,
        confidence,
        latencyMs: frame.inferenceMs,
        leanTone: toneFor(lean, this.settings.forward_lean_threshold),
        tiltTone: toneFor(tilt, this.settings.shoulder_tilt_threshold),
      });
      this.ui.updateGauges({
        lean,
        tilt,
        proximity: prox,
        alerts: this.alertCount,
        leanThreshold: this.settings.forward_lean_threshold,
        tiltThreshold: this.settings.shoulder_tilt_threshold,
        neckLoad: this.settings.show_neck_load ? neckLoad(lean) : null,
        neckLabel: this.settings.show_neck_load ? neckLoadLabel(lean) : 'Disabled',
        state: statusLabel(status),
      });
      const category = blinkCategory(
        bpm,
        this.settings.blink_low_bpm,
        this.settings.blink_reference_bpm
      );
      const ocularPaused = Boolean(frame.faceSuppressed);
      this.ui.updateOcular({
        // While ocular inference is paused the last smoothed values are stale;
        // show "no reading" rather than a number that is not being measured.
        ear: ocularPaused ? null : ear,
        blinks: this.blinkDetector.totalBlinks,
        bpm: ocularPaused ? null : bpm,
        category,
        paused: ocularPaused,
        eyeState:
          ear === null ? 'No data' : this.blinkDetector.state === 'closed' ? 'Eyes closed' : 'Eyes open',
      });
      this.ui.updateBlinkActivity?.(this.blinkDetector.recent(12));
      this.ui.setTelemetryChips({
        fps: frame.fps,
        cap: frame.fpsCap,
        poseMs: frame.poseMs,
        faceMs: frame.faceMs,
        delegate: frame.delegate,
        confidence,
        sessionSeconds: this.sessionDurationSeconds(),
      });
      this.ui.setPostureFlag(flagLabel(status), status === 'alert' ? 'bad' : status === 'elevated' ? 'warn' : 'ok');
      this.chart.push(nowMs, { lean, tilt, ear, status });

      this.latest = {
        lean: round(lean),
        tilt: round(tilt),
        ear: round(ear, 3),
        proximity: round(prox, 3),
        bpm,
        confidence: round(confidence, 3),
        status: statusLabel(status),
      };
    }

    // ---- telemetry ---------------------------------------------------
    if (this.monitoring && !this.userPaused) {
      const stored = this.recorder.sample(nowMs, {
        forwardLean: lean,
        shoulderTilt: tilt,
        ear,
        proximity: prox,
        blinkRate: bpm,
        confidence,
        postureStatus: classifyPosture(
          usable ? lean : null,
          usable ? tilt : null,
          this.settings.forward_lean_threshold,
          this.settings.shoulder_tilt_threshold
        ),
      });
      if (stored) {
        this.recorder.setLive({
          state,
          fps: round(frame.fps, 1),
          confidence: round(confidence, 3),
          forward_lean: round(lean),
          shoulder_tilt: round(tilt),
          ear_value: round(ear, 3),
          blink_rate: bpm,
          blinks_total: this.blinkDetector.totalBlinks,
          proximity_ratio: round(prox, 3),
          posture_status: statusLabel(status),
          eye_state: this.blinkDetector.state,
          session_active: true,
          quality_issues: this.lastQuality?.issues || [],
          cameras: this.recorder.live.cameras || [],
          active_camera: this.recorder.live.active_camera || '',
        });
      }
      this.flushTelemetry();
      this.checkBreakReminder();
    }
  }

  deriveState({ pose, face, confidence, frame }) {
    if (this.userPaused) return VISION_STATE.PAUSED;
    if (!this.vision.stream) return VISION_STATE.STANDBY;
    const faceSuppressed = Boolean(frame.faceSuppressed);
    if (!pose && frame.consecutiveNoPose > 3) {
      if (faceSuppressed) return VISION_STATE.NO_SHOULDERS;
      return face ? VISION_STATE.NO_SHOULDERS : VISION_STATE.NO_FACE;
    }
    if (!faceSuppressed && !face && frame.consecutiveNoFace > 3) return VISION_STATE.NO_FACE;
    if (confidence < 0.4) return VISION_STATE.LOW_CONFIDENCE;
    return VISION_STATE.TRACKING;
  }

  handleState(state, detail) {
    if (this.state === state) return;
    this.state = state;
    const tone =
      state === VISION_STATE.TRACKING
        ? 'live'
        : state === VISION_STATE.READY
          ? 'live'
          : state === VISION_STATE.PAUSED || state === VISION_STATE.LOW_CONFIDENCE ||
              state === VISION_STATE.NO_FACE || state === VISION_STATE.NO_SHOULDERS
            ? 'warn'
            : state === VISION_STATE.ERROR || state === VISION_STATE.CAMERA_UNAVAILABLE
              ? 'bad'
              : 'idle';
    this.ui.setState(state, stateLabel(state), tone);
    if (state === VISION_STATE.CAMERA_UNAVAILABLE && this.monitoring) {
      this.recorder.addEvent('camera_error', {
        message: detail || 'Camera stream ended',
        details: { state },
      });
      this.stopMonitoring({ userInitiated: false });
    }
  }

  handleError(error, { duringInit = false } = {}) {
    const message = error?.message || String(error);
    this.ui.toast(message, 'error', 8000);
    console.error('[anatonix]', error);
    this.handleState(VISION_STATE.ERROR, message);
    this.recorder.addEvent('camera_error', {
      message,
      details: { code: error?.code || 'unknown', duringInit },
    });
    this.flushTelemetry(true);
  }

  /* --------------------------------------------------------- telemetry */
  flushTelemetry(force = false) {
    if (!this.sessionId) return;
    if (force) this.recorder.flushNow();
    else this.recorder.flush();
  }

  checkBreakReminder() {
    const due = this.breaks.update(Date.now());
    if (!due.due) return;
    this.recorder.addEvent('break_reminder', { message: due.message });
    this.ui.showAlert({
      title: 'Break reminder',
      detail: `${due.message} (snoozed 5 minutes if dismissed)`,
      timestamp: Date.now(),
      soft: true,
    });
    if (this.settings.break_audio) this.speech.speak(due.message);
    this.breaks.snooze(5);
    this.flushTelemetry(true);
  }

  /* ------------------------------------------------------------ render */
  /**
   * The preview canvas is redrawn on every animation frame (bounded by the
   * camera's own frame rate), while landmark inference may only run at
   * `inference_fps`.  Without this split the video preview would update at the
   * inference rate and look like a stuttering/flickering slideshow.
   */
  drawLatest() {
    const frame = this.lastFrame;
    const video = this.ui.nodes.video;
    if (!frame || !video || video.readyState < 2) return;
    this.overlayDirty = false;
    this.lastDrawAt = performance.now();
    this.ui.drawFrame(frame);
  }

  startRenderLoop() {
    if (this.renderActive) return;
    this.renderActive = true;
    const video = this.ui.nodes.video;

    if (video && typeof video.requestVideoFrameCallback === 'function') {
      // Best case: one callback per camera frame — the preview is as smooth as
      // the camera allows, independent of how heavy inference is.
      this.renderMode = 'video-frame';
      const onVideoFrame = () => {
        if (!this.renderActive) return;
        this.renderHandle = video.requestVideoFrameCallback(onVideoFrame);
        this.drawLatest();
      };
      this.renderHandle = video.requestVideoFrameCallback(onVideoFrame);
      return;
    }

    // Fallback: animation frame, but only paint when the picture really moved
    // (or when a new inference result needs to be drawn).
    this.renderMode = 'animation-frame';
    const tick = () => {
      if (!this.renderActive) return;
      this.renderHandle = requestAnimationFrame(tick);
      const stamp = video ? video.currentTime : -1;
      const moved = stamp !== this.lastDrawnVideoTime;
      if (!moved && !this.overlayDirty) return;
      this.lastDrawnVideoTime = stamp;
      this.drawLatest();
    };
    this.renderHandle = requestAnimationFrame(tick);
  }

  stopRenderLoop() {
    if (!this.renderActive) return;
    this.renderActive = false;
    const video = this.ui.nodes.video;
    if (this.renderMode === 'video-frame') {
      if (video && this.renderHandle) video.cancelVideoFrameCallback(this.renderHandle);
    } else if (this.renderHandle) {
      cancelAnimationFrame(this.renderHandle);
    }
    this.renderHandle = null;
    this.renderMode = null;
    this.lastDrawnVideoTime = -1;
  }

  /* -------------------------------------------------------- calibration */
  openCalibration() {
    if (!this.vision.stream) {
      this.ui.toast('Start the camera before calibrating.', 'error');
      return;
    }
    this.ui.showCalibrationModal(true);
    this.ui.setCalibrationProgress(0, 'Ready', '0 frames', false);
    this.calibrationCollector.cancel();
  }

  closeCalibration() {
    this.calibrationCollector.cancel();
    this.vision.setFaceEnabled(true);
    this.ui.showCalibrationModal(false);
  }

  beginCalibrationCapture() {
    if (!this.vision.stream) return;
    // Calibration only needs pose landmarks: switching ocular inference off for
    // the capture window roughly doubles the frame rate it can collect.
    this.vision.setFaceEnabled(false);
    this.calibrationCollector.start(Date.now());
    this.ui.setCalibrationProgress(0, 'Capturing…', '0 frames', true);
    this.ui.toast('Hold your reference posture…', 'info', 2600);
  }

  updateCalibrationProgress() {
    const collector = this.calibrationCollector;
    const frames = collector.frames.length;
    this.ui.setCalibrationProgress(
      collector.progress,
      collector.isComplete ? 'Complete' : `${Math.round(collector.progress * 100)}%`,
      `${frames} frames`,
      true
    );
    if (collector.isComplete) this.finishCalibration();
  }

  finishCalibration() {
    const result = this.calibrationCollector.finish();
    this.vision.setFaceEnabled(true);
    this.ui.showCalibrationModal(false);
    if (!result) {
      this.ui.toast(
        'Calibration failed: not enough valid frames. Keep both shoulders visible and retry.',
        'error',
        7000
      );
      this.recorder.addEvent('tracking_degraded', {
        message: 'Calibration failed: insufficient valid frames',
      });
      this.flushTelemetry(true);
      return;
    }
    this.calibration = {
      pitch: result.baseline.pitch,
      shoulderTilt: result.baseline.shoulderTilt,
      shoulderSpan: result.baseline.shoulderSpan,
      quality: result.quality,
      createdAt: new Date().toISOString(),
      frames: result.frames,
    };
    this.uncalibrated = false;
    this.calibrationSetAt = Date.now();
    // The baseline just changed, so any posture state measured against the old
    // baseline (including an active alert) is no longer meaningful.
    this.alertEngine.reset();
    this.ui.hideAlert();
    this.smoothed.lean = null;
    this.smoothed.tilt = null;
    this.renderCalibration();
    this.recorder.addEvent('calibration_completed', {
      message: `Calibration completed (${qualityLabel(result.quality)} quality)`,
      baseline: {
        pitch: result.baseline.pitch,
        shoulderTilt: result.baseline.shoulderTilt,
        shoulderSpan: result.baseline.shoulderSpan,
      },
      quality: result.quality,
      frames: result.frames,
    });
    this.flushTelemetry(true);
    this.ui.toast(
      `Calibration saved — ${qualityLabel(result.quality)} quality (${result.frames} frames).`,
      'ok',
      6000
    );
  }

  renderCalibration() {
    if (!this.calibration) {
      this.ui.updateCalibration(null);
      return;
    }
    this.ui.updateCalibration({
      ...this.calibration,
      uncalibrated: this.uncalibrated,
    });
  }

  /* ------------------------------------------------------------- audio */
  toggleAudio() {
    if (!this.speech.supported) {
      this.ui.toast('This browser does not support speech synthesis.', 'error');
      return;
    }
    this.settings.audio_enabled = !this.settings.audio_enabled;
    this.speech.configure({ audioEnabled: this.settings.audio_enabled });
    this.ui.setAudio(this.settings.audio_enabled, true);
    this.recorder.addEvent('settings_changed', {
      message: `Spoken alerts ${this.settings.audio_enabled ? 'enabled' : 'disabled'}`,
      details: { audio_enabled: this.settings.audio_enabled },
    });
    this.flushTelemetry(true);
    if (this.settings.audio_enabled) this.speech.speak('Spoken alerts enabled.');
  }
}

/* ------------------------------------------------------------- helpers */
function midOf(a, b) {
  if (!a || !b) return null;
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: ((a.z ?? 0) + (b.z ?? 0)) / 2 };
}

function round(value, digits = 2) {
  return Number.isFinite(value) ? Math.round(value * 10 ** digits) / 10 ** digits : null;
}

function fmt(value, suffix = '') {
  return Number.isFinite(value) ? `${value.toFixed(1)}${suffix}` : '--';
}

function toneFor(value, threshold) {
  if (!Number.isFinite(value)) return 'idle';
  if (value >= threshold * 1.6) return 'bad';
  if (value >= threshold) return 'warn';
  return 'good';
}

function statusLabel(status) {
  if (status === 'alert') return 'Alert';
  if (status === 'elevated') return 'Elevated';
  if (status === 'good') return 'Stable';
  return 'No data';
}

function flagLabel(status) {
  if (status === 'alert') return 'Posture alert';
  if (status === 'elevated') return 'Watch posture';
  if (status === 'good') return 'Posture stable';
  return 'Standby';
}

/* expose a couple of helpers for the debug console / e2e tests */
window.__anatonix = {
  app: null,
  get instance() {
    return this.app;
  },
  formatClock,
  eyeCropBox,
  makeUid,
};

try {
  window.__anatonix.app = new AnatonixApp();
} catch (error) {
  console.error('[anatonix] fatal initialisation error', error);
  const banner = document.getElementById('cameraError');
  if (banner) {
    banner.hidden = false;
    banner.textContent = `Anatonix failed to start: ${error.message}`;
  }
}
