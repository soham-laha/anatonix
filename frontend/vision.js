/**
 * Anatonix — computer-vision runtime.
 *
 * Responsibilities:
 *   • load the MediaPipe Tasks Vision bundle + PoseLandmarker + FaceLandmarker
 *   • own the webcam lifecycle (start / pause / stop / cleanup)
 *   • run a single requestAnimationFrame loop with a bounded inference rate
 *   • expose per-frame pose/face landmarks, FPS, latency and camera-quality data
 *
 * Everything runs locally in this browser tab.  Frames are never uploaded.
 */

export const VISION_STATE = Object.freeze({
  STANDBY: 'standby',
  INITIALIZING: 'initializing',
  READY: 'ready',
  TRACKING: 'tracking',
  LOW_CONFIDENCE: 'low_confidence',
  NO_FACE: 'no_face',
  NO_SHOULDERS: 'no_shoulders',
  PAUSED: 'paused',
  CAMERA_UNAVAILABLE: 'camera_unavailable',
  ERROR: 'error',
});

const STATE_LABELS = {
  standby: 'Standby',
  initializing: 'Initialising',
  ready: 'Camera ready',
  tracking: 'Tracking normally',
  low_confidence: 'Low tracking confidence',
  no_face: 'Face not detected',
  no_shoulders: 'Shoulders not detected',
  paused: 'Monitoring paused',
  camera_unavailable: 'Camera unavailable',
  error: 'Camera error',
};

export function stateLabel(state) {
  return STATE_LABELS[state] || state;
}

/**
 * Share of wall-clock time inference is allowed to occupy. The rest is left for
 * the browser to composite the preview and repaint the overlay; without this
 * breathing room a slow machine freezes the UI while the models run.
 */
export const INFERENCE_DUTY_LIMIT = 0.7;
/** Never schedule inference slower than this, however expensive a frame is. */
export const MIN_SCHEDULE_INTERVAL_MS = 1000 / 6;

/**
 * Minimum wall-clock gap between inference runs.
 *
 * Two constraints compete: the user's requested cap (never faster than that)
 * and the health of the page (inference must not monopolise the main thread,
 * otherwise the preview, the overlay and the eye crop cannot repaint). The
 * result therefore never lets a frame consume more than `dutyLimit` of the
 * frame budget, and never stretches the interval past `minInterval` (a 6 fps
 * floor) even on very slow machines.
 *
 * @param {{inferenceFps?:number, inferenceMs?:number, dutyLimit?:number, minInterval?:number}} options
 * @returns {number} milliseconds
 */
export function scheduleInterval({
  inferenceFps = 15,
  inferenceMs = 0,
  dutyLimit = INFERENCE_DUTY_LIMIT,
  minInterval = MIN_SCHEDULE_INTERVAL_MS,
} = {}) {
  const cap = Math.max(2, Math.min(30, Number(inferenceFps) || 15));
  const capInterval = 1000 / cap;
  const cost = Number(inferenceMs) > 0 ? Number(inferenceMs) : 0;
  const dutyInterval = cost > 0 ? cost / dutyLimit : 0;
  return Math.max(capInterval, Math.min(dutyInterval, minInterval));
}

export class CameraError extends Error {
  constructor(message, code = 'unknown') {
    super(message);
    this.name = 'CameraError';
    this.code = code;
  }
}

/**
 * Camera-quality evaluation: visibility, lighting, framing and confidence.
 * Produces human instructions rather than raw numbers.
 */
export class CameraQualityMonitor {
  constructor() {
    this.brightness = null;
    this.lastSample = 0;
    this.canvas = document.createElement('canvas');
    this.canvas.width = 64;
    this.canvas.height = 48;
    this.ctx = this.canvas.getContext('2d', { willReadFrequently: true });
  }

  /** Sample mean luminance a few times per second (cheap downscale). */
  sampleBrightness(video, nowMs) {
    if (!video || video.readyState < 2) return this.brightness;
    if (nowMs - this.lastSample < 400) return this.brightness;
    this.lastSample = nowMs;
    try {
      this.ctx.drawImage(video, 0, 0, this.canvas.width, this.canvas.height);
      const { data } = this.ctx.getImageData(0, 0, this.canvas.width, this.canvas.height);
      let sum = 0;
      for (let i = 0; i < data.length; i += 4) {
        sum += 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
      }
      this.brightness = sum / (data.length / 4);
    } catch (error) {
      this.brightness = null;
    }
    return this.brightness;
  }

  /**
   * @returns {{checks:Array, issues:Array, hints:Array, lighting:number|null}}
   */
  evaluate({ pose, face, ears, shoulders, proximity, nowMs, skipFace = false }) {
    const checks = [];
    const issues = [];
    const hints = [];

    // `skipFace` is used while ocular inference is paused for calibration: the
    // absence of face landmarks is deliberate, not a camera-quality problem.
    if (!skipFace) {
      const faceVisible = Boolean(face && face.length > 200);
      checks.push({
        key: 'face',
        label: 'Face visibility',
        ok: faceVisible,
        detail: faceVisible ? 'Face landmarks tracked' : 'No face landmarks',
      });
      if (!faceVisible) {
        issues.push('face_not_detected');
        hints.push('Centre your face in the frame and check the lighting.');
      }
    }

    const shoulderVisibility = ears && shoulders
      ? Math.min(...shoulders.map((p) => (p ? p.visibility ?? 1 : 0)))
      : 0;
    const shouldersVisible = Boolean(shoulders) && shoulderVisibility >= 0.5;
    checks.push({
      key: 'shoulders',
      label: 'Shoulder visibility',
      ok: shouldersVisible,
      detail: shouldersVisible
        ? `Both shoulders tracked (${Math.round(shoulderVisibility * 100)}%)`
        : 'Shoulders not reliably visible',
    });
    if (!shouldersVisible) {
      issues.push('shoulders_not_detected');
      hints.push('Keep both shoulders visible — move the camera back or lower it.');
    }

    const lighting = this.brightness;
    const lightingOk = lighting !== null && lighting >= 45 && lighting <= 225;
    const lightingDetail =
      lighting === null
        ? 'Lighting not sampled'
        : lighting < 45
          ? `Low light (${Math.round(lighting)}/255)`
          : lighting > 225
            ? `Over-exposed (${Math.round(lighting)}/255)`
            : `Lighting adequate (${Math.round(lighting)}/255)`;
    checks.push({ key: 'lighting', label: 'Lighting adequacy', ok: lightingOk, detail: lightingDetail });
    if (lighting !== null && lighting < 45) {
      issues.push('low_light');
      hints.push('Improve lighting — add a light source in front of you.');
    } else if (lighting !== null && lighting > 225) {
      issues.push('over_exposed');
      hints.push('Reduce backlight or glare behind you.');
    }

    // Framing: ears should sit in the upper part of the frame, inside the edges.
    let framingOk = false;
    let framingDetail = 'Framing not evaluated';
    if (ears) {
      const earsY = ears.map((p) => p?.y ?? 1);
      const earsX = ears.map((p) => p?.x ?? 0.5);
      const insideX = earsX.every((x) => x > 0.1 && x < 0.9);
      const headroomOk = earsY.every((y) => y > 0.06 && y < 0.62);
      framingOk = insideX && headroomOk;
      if (!insideX) {
        framingDetail = 'Head near the frame edge';
        hints.push('Position the camera closer to eye level and face it directly.');
      } else if (!headroomOk) {
        framingDetail = 'Head too high or too low in frame';
        hints.push('Position the camera closer to eye level.');
      } else {
        framingDetail = 'Framing within tolerance';
      }
    }
    checks.push({ key: 'framing', label: 'Camera framing', ok: framingOk, detail: framingDetail });

    if (proximity !== null && proximity > 1.35) {
      issues.push('too_close');
      hints.push('Move slightly backward — your shoulders fill the frame.');
    } else if (proximity !== null && proximity < 0.62) {
      issues.push('too_far');
      hints.push('Move a little closer to the camera.');
    }

    const confidence = pose ? confidenceOf(pose, [7, 8, 11, 12]) : 0;
    const confidenceOk = confidence >= 0.5;
    checks.push({
      key: 'confidence',
      label: 'Landmark confidence',
      ok: confidenceOk,
      detail: `${Math.round(confidence * 100)}% mean visibility`,
    });
    if (!confidenceOk && pose) {
      issues.push('low_confidence');
      hints.push('Hold still for a moment so tracking can re-acquire.');
    }

    return { checks, issues, hints: [...new Set(hints)].slice(0, 3), lighting };
  }
}

export function confidenceOf(landmarks, indices) {
  if (!landmarks) return 0;
  const values = indices
    .map((index) => landmarks[index])
    .filter(Boolean)
    .map((point) => (Number.isFinite(point.visibility) ? point.visibility : 1));
  if (!values.length) return 0;
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

export class VisionEngine {
  constructor(options = {}) {
    this.onFrame = options.onFrame || (() => {});
    this.onState = options.onState || (() => {});
    this.onError = options.onError || (() => {});
    this.onProgress = options.onProgress || (() => {});
    this.onCameras = options.onCameras || (() => {});

    this.video = options.video || document.createElement('video');
    this.video.playsInline = true;
    this.video.muted = true;
    this.video.autoplay = true;

    this.state = VISION_STATE.STANDBY;
    this.models = null;
    this.stream = null;
    this.rafHandle = null;
    this.paused = false;
    this.running = false;
    this.inferenceFps = options.inferenceFps || 15;
    this.lastInferenceAt = 0;
    this.lastTimestamp = 0;
    this.fpsSmoothed = 0;
    this.inferenceMs = 0;
    this.poseMs = 0;
    this.faceMs = 0;
    this.delegate = 'unknown'; // which inference backend actually initialised
    this.faceEnabled = true;   // ocular inference can be paused (calibration)
    this.dutyLimit = INFERENCE_DUTY_LIMIT;
    this.errorStreak = 0;
    this.consecutiveNoPose = 0;
    this.consecutiveNoFace = 0;
    this.quality = new CameraQualityMonitor();
    this.deviceId = options.deviceId || '';
    this.modelUrls = null;
    this._loop = this._loop.bind(this);
  }

  setState(state, detail = '') {
    if (this.state === state && !detail) return;
    this.state = state;
    try {
      this.onState(state, detail);
    } catch (error) {
      console.error('[anatonix] state handler failed', error);
    }
  }

  /**
   * Create a landmarker, falling back from the GPU delegate to CPU instead of
   * failing outright. Records which backend ended up being used so the UI can
   * explain why inference is slow.
   */
  async _createLandmarker(fileset, factory, options) {
    try {
      const landmarker = await factory.createFromOptions(fileset, options);
      this.delegate = 'GPU';
      return landmarker;
    } catch (gpuError) {
      console.warn('[anatonix] GPU delegate unavailable, falling back to CPU', gpuError);
      this.delegate = 'CPU';
      const cpuOptions = {
        ...options,
        baseOptions: { ...options.baseOptions, delegate: 'CPU' },
      };
      return factory.createFromOptions(fileset, cpuOptions);
    }
  }

  /* ------------------------------------------------------------ models */
  async loadModels(urls, progress = () => {}) {
    if (this.models) return this.models;
    this.modelUrls = urls;
    progress('Loading vision runtime');
    const vision = await import(/* webpackIgnore: true */ urls.bundleUrl);
    if (!vision?.PoseLandmarker || !vision?.FaceLandmarker || !vision?.FilesetResolver) {
      throw new CameraError('Vision runtime loaded but landmarkers are missing', 'bundle_invalid');
    }
    progress('Preparing WebAssembly runtime');
    const fileset = await vision.FilesetResolver.forVisionTasks(urls.wasmBase);

    progress('Loading pose model');
    const pose = await this._createLandmarker(fileset, vision.PoseLandmarker, {
      baseOptions: { modelAssetPath: urls.poseModelUrl, delegate: 'GPU' },
      runningMode: 'VIDEO',
      numPoses: 1,
      minPoseDetectionConfidence: 0.5,
      minPosePresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
      outputSegmentationMasks: false,
    });

    progress('Loading ocular model');
    const face = await this._createLandmarker(fileset, vision.FaceLandmarker, {
      baseOptions: { modelAssetPath: urls.faceModelUrl, delegate: 'GPU' },
      runningMode: 'VIDEO',
      numFaces: 1,
      minFaceDetectionConfidence: 0.5,
      minFacePresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
      outputFaceBlendshapes: false,
      outputFacialTransformationMatrixes: false,
    });

    this.models = { pose, face, vision };
    progress('Models ready');
    return this.models;
  }

  /* ------------------------------------------------------------ camera */
  async listCameras() {
    if (!navigator.mediaDevices?.enumerateDevices) return [];
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const cameras = devices
        .filter((device) => device.kind === 'videoinput')
        .map((device, index) => ({
          id: device.deviceId,
          label: device.label || `Camera ${index + 1}`,
        }));
      this.onCameras(cameras);
      return cameras;
    } catch (error) {
      return [];
    }
  }

  async startCamera({ deviceId = '', width = 640, height = 480 } = {}) {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new CameraError(
        'This browser does not expose camera access. Use a recent Chrome, Edge or Firefox build over https or localhost.',
        'unsupported'
      );
    }
    await this.stopCamera();
    this.setState(VISION_STATE.INITIALIZING, 'Requesting camera permission');

    const constraints = {
      audio: false,
      video: {
        width: { ideal: width },
        height: { ideal: height },
        frameRate: { ideal: 30, max: 30 },
      },
    };
    if (deviceId) constraints.video.deviceId = { exact: deviceId };

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (error) {
      const code =
        error.name === 'NotAllowedError'
          ? 'permission_denied'
          : error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError'
            ? 'no_camera'
            : error.name === 'NotReadableError'
              ? 'busy'
              : 'unknown';
      throw new CameraError(describeCameraError(code, error), code);
    }

    this.stream = stream;
    const [track] = stream.getVideoTracks();
    if (track) {
      const settings = track.getSettings ? track.getSettings() : {};
      this.deviceId = settings.deviceId || deviceId || '';
      track.addEventListener('ended', () => {
        this.setState(VISION_STATE.CAMERA_UNAVAILABLE, 'Camera stream ended');
      });
    }

    this.video.srcObject = stream;
    await this.video.play().catch((error) => {
      throw new CameraError(`Unable to start the video element: ${error.message}`, 'play_failed');
    });

    await this.listCameras();
    this.setState(VISION_STATE.READY, 'Camera stream active');
    return stream;
  }

  async stopCamera() {
    this.running = false;
    if (this.rafHandle !== null) {
      cancelAnimationFrame(this.rafHandle);
      this.rafHandle = null;
    }
    if (this.stream) {
      for (const track of this.stream.getTracks()) {
        try {
          track.stop();
        } catch (error) {
          /* ignore */
        }
      }
      this.stream = null;
    }
    try {
      this.video.pause();
    } catch (error) {
      /* ignore */
    }
    if (this.video.srcObject) this.video.srcObject = null;
    this.fpsSmoothed = 0;
    this.setState(VISION_STATE.STANDBY, 'Camera released');
  }

  pause() {
    this.paused = true;
    this.setState(VISION_STATE.PAUSED, 'Monitoring paused');
  }

  resume() {
    if (!this.stream) return false;
    this.paused = false;
    this.setState(VISION_STATE.READY, 'Monitoring resumed');
    return true;
  }

  setInferenceFps(fps) {
    this.inferenceFps = Math.max(2, Math.min(30, Number(fps) || 15));
  }

  /**
   * Share of wall-clock time inference may occupy (0.5 – 1.0). Lower values
   * keep the interface responsive on slow machines; 1.0 removes the headroom
   * entirely and lets inference use every available millisecond.
   */
  setDutyLimit(limit) {
    const value = Number(limit);
    this.dutyLimit = Number.isFinite(value)
      ? Math.max(0.5, Math.min(1, value))
      : INFERENCE_DUTY_LIMIT;
  }

  /**
   * Ocular inference is the expensive half of the pipeline. Calibration only
   * needs pose landmarks, so the capture window runs pose-only to double the
   * usable frame rate (and therefore the calibration quality).
   */
  setFaceEnabled(enabled) {
    this.faceEnabled = Boolean(enabled);
    if (!this.faceEnabled) this.consecutiveNoFace = 0;
  }

  start() {
    if (this.running) return;
    if (!this.stream) {
      this.onError(new CameraError('Camera is not running', 'not_started'));
      return;
    }
    this.running = true;
    this.errorStreak = 0;
    this.rafHandle = requestAnimationFrame(this._loop);
  }

  /* -------------------------------------------------------------- loop */
  _loop() {
    if (!this.running) return;
    this.rafHandle = requestAnimationFrame(this._loop);
    const now = performance.now();

    if (this.paused) return;
    if (this.video.readyState < 2 || !this.video.videoWidth) return;

    // Schedule the next inference: respect the configured cap, but also leave
    // at least (1 - duty) of the frame budget free for painting.
    const interval = scheduleInterval({
      inferenceFps: this.inferenceFps,
      inferenceMs: this.inferenceMs,
      dutyLimit: this.dutyLimit,
    });
    if (now - this.lastInferenceAt < interval) return;
    const sincePrev = this.lastInferenceAt ? now - this.lastInferenceAt : interval;
    this.lastInferenceAt = now;

    let poseResult = null;
    let faceResult = null;
    const started = performance.now();
    let poseMs = 0;
    try {
      let ts = Math.round(now);
      if (ts <= this.lastTimestamp) ts = this.lastTimestamp + 1;
      this.lastTimestamp = ts;
      const poseStarted = performance.now();
      poseResult = this.models?.pose?.detectForVideo(this.video, ts) || null;
      poseMs = performance.now() - poseStarted;
      if (this.faceEnabled) {
        const faceStarted = performance.now();
        faceResult = this.models?.face?.detectForVideo(this.video, ts) || null;
        this.faceMs = this.faceMs ? this.faceMs * 0.8 + (performance.now() - faceStarted) * 0.2 : performance.now() - faceStarted;
      }
      this.poseMs = this.poseMs ? this.poseMs * 0.8 + poseMs * 0.2 : poseMs;
      this.errorStreak = 0;
    } catch (error) {
      this.errorStreak += 1;
      if (this.errorStreak === 3) {
        this.onError(new CameraError(`Inference failed: ${error.message}`, 'inference_failed'));
      }
      return;
    }

    const elapsed = performance.now() - started;
    this.inferenceMs = this.inferenceMs ? this.inferenceMs * 0.8 + elapsed * 0.2 : elapsed;
    // Report the rate actually achieved (wall clock between inference runs),
    // which is what the user sees — not the requested cap.
    const measuredFps = sincePrev > 0 ? 1000 / sincePrev : this.fpsSmoothed;
    this.fpsSmoothed = this.fpsSmoothed
      ? this.fpsSmoothed * 0.85 + measuredFps * 0.15
      : measuredFps;

    const pose = poseResult?.landmarks?.[0] || null;
    const face = faceResult?.faceLandmarks?.[0] || null;

    this.consecutiveNoPose = pose ? 0 : this.consecutiveNoPose + 1;
    if (this.faceEnabled) {
      this.consecutiveNoFace = face ? 0 : this.consecutiveNoFace + 1;
    }

    const ears = pose ? [pose[7], pose[8]] : null;
    const shoulders = pose ? [pose[11], pose[12]] : null;
    const confidence = pose ? confidenceOf(pose, [7, 8, 11, 12]) : 0;
    this.quality.sampleBrightness(this.video, now);

    try {
      this.onFrame({
        nowMs: now,
        pose,
        face,
        ears,
        shoulders,
        confidence,
        fps: this.fpsSmoothed,
        fpsCap: this.inferenceFps,
        inferenceMs: this.inferenceMs,
        poseMs: this.poseMs,
        faceMs: this.faceMs,
        delegate: this.delegate,
        faceSuppressed: !this.faceEnabled,
        frameWidth: this.video.videoWidth,
        frameHeight: this.video.videoHeight,
        brightness: this.quality.brightness,
        consecutiveNoPose: this.consecutiveNoPose,
        consecutiveNoFace: this.consecutiveNoFace,
      });
    } catch (error) {
      console.error('[anatonix] frame handler failed', error);
    }
  }
}

export function describeCameraError(code, error) {
  switch (code) {
    case 'permission_denied':
      return 'Camera permission was denied. Click the camera icon in the address bar, allow access, then retry.';
    case 'no_camera':
      return 'No camera was found. Connect a webcam and restart monitoring.';
    case 'busy':
      return 'The camera is busy in another application. Close it (or another browser tab) and retry.';
    case 'unsupported':
      return 'This browser cannot access the camera. Use a recent Chrome, Edge or Firefox over https or localhost.';
    default:
      return `Camera initialisation failed: ${error?.message || 'unknown error'}`;
  }
}
