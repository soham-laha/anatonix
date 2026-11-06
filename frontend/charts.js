/**
 * Anatonix — live rolling chart.
 *
 * A small, dependency-free canvas renderer for the last 60 seconds of forward
 * lean, shoulder tilt and eye aspect ratio.  Drawing is throttled to ~4 fps and
 * uses a device-pixel-ratio aware backing store so lines stay crisp.
 */

// Series colours are design tokens so the chart follows the active theme.
const SERIES = [
  { key: 'lean', label: 'Forward lean', token: 'accent', fallback: '#d9d3c4', max: 30 },
  { key: 'tilt', label: 'Shoulder tilt', token: 'warning', fallback: '#cbab74', max: 15 },
  { key: 'ear', label: 'Eye aspect ratio', token: 'purple', fallback: '#a49a87', max: 0.5 },
];

function token(name, fallback) {
  if (typeof window === 'undefined' || !document.documentElement) return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(`--anx-${name}`)
    .trim();
  return value || fallback;
}

function palette() {
  return {
    grid: token('grid-line', 'rgba(234, 231, 225, 0.06)'),
    muted: token('text-muted', '#9c968c'),
    dim: token('text-dim', '#736e66'),
    critical: token('critical', '#c0756c'),
    series: SERIES.map((s) => ({ ...s, colour: token(s.token, s.fallback) })),
  };
}

export class LiveChart {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.windowMs = options.windowMs ?? 60000;
    this.points = [];
    this.visible = { lean: true, tilt: true, ear: true };
    this.thresholds = { lean: 12, tilt: 6 };
    this.lastDraw = 0;
    this.emptyLabel = options.emptyLabel ?? 'Waiting for the first 60 seconds of tracking data';
    this.resize();
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(180, rect.width || this.canvas.clientWidth || 480);
    const height = Math.max(120, rect.height || this.canvas.clientHeight || 178);
    if (this.canvas.width !== Math.round(width * dpr)) {
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(height * dpr);
    }
    this.width = width;
    this.height = height;
    this.dpr = dpr;
    this.draw(true);
  }

  setThresholds(thresholds) {
    this.thresholds = { ...this.thresholds, ...thresholds };
  }

  toggleSeries(key) {
    if (key in this.visible) this.visible[key] = !this.visible[key];
    this.draw(true);
  }

  push(nowMs, values) {
    this.points.push({
      t: nowMs,
      lean: Number.isFinite(values.lean) ? values.lean : null,
      tilt: Number.isFinite(values.tilt) ? values.tilt : null,
      ear: Number.isFinite(values.ear) ? values.ear : null,
      status: values.status || 'good',
    });
    const cutoff = nowMs - this.windowMs;
    while (this.points.length && this.points[0].t < cutoff) this.points.shift();
    if (this.points.length > 900) this.points.splice(0, this.points.length - 900);
    if (nowMs - this.lastDraw > 250) this.draw();
  }

  clear() {
    this.points = [];
    this.draw(true);
  }

  draw(force = false) {
    if (!force && Date.now() - this.lastDraw < 200) return;
    this.lastDraw = Date.now();
    const { ctx, width, height, dpr } = this;
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 12, right: 10, bottom: 20, left: 32 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;

    const C = palette();

    // grid
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    ctx.font = '9px "JetBrains Mono", monospace';
    ctx.fillStyle = C.dim;
    for (let i = 0; i <= 4; i += 1) {
      const y = padding.top + (plotHeight * i) / 4;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(padding.left + plotWidth, y);
      ctx.stroke();
      const value = 100 - i * 25;
      ctx.fillText(`${value}%`, 4, y + 3);
    }

    // threshold guide for forward lean
    if (this.visible.lean && Number.isFinite(this.thresholds.lean)) {
      const y = padding.top + plotHeight - (this.thresholds.lean / 30) * plotHeight;
      if (y > padding.top && y < padding.top + plotHeight) {
        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = C.critical;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(padding.left + plotWidth, y);
        ctx.stroke();
        ctx.restore();
      }
    }

    if (this.points.length < 2) {
      ctx.fillStyle = C.dim;
      ctx.font = '11px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(this.emptyLabel, width / 2, height / 2 + 4);
      ctx.textAlign = 'left';
      return;
    }

    const tStart = this.points[0].t;
    const tEnd = this.points[this.points.length - 1].t;
    const span = Math.max(1000, tEnd - tStart);
    const xOf = (t) => padding.left + ((t - tStart) / span) * plotWidth;

    for (const series of C.series) {
      if (!this.visible[series.key]) continue;
      const max = series.key === 'ear' ? 0.5 : series.max;
      ctx.strokeStyle = series.colour;
      ctx.lineWidth = 1.6;
      ctx.lineJoin = 'round';
      ctx.beginPath();
      let started = false;
      for (const point of this.points) {
        const value = point[series.key];
        if (value === null || !Number.isFinite(value)) {
          started = false;
          continue;
        }
        const x = xOf(point.t);
        const y = padding.top + plotHeight - Math.min(1, Math.max(0, value / max)) * plotHeight;
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
    }

    // time labels
    ctx.fillStyle = C.muted;
    ctx.font = '9px "JetBrains Mono", monospace';
    ctx.fillText('−60s', padding.left, height - 6);
    ctx.textAlign = 'right';
    ctx.fillText('now', padding.left + plotWidth, height - 6);
    ctx.textAlign = 'left';
  }
}

export const LIVE_CHART_SERIES = SERIES;
export { palette as liveChartPalette };
