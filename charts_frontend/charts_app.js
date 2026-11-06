/**
 * Anatonix — Chart.js analytics renderer (Streamlit component).
 *
 * Receives an `payload` argument from Python (see backend/analytics.chart_payload)
 * and renders seven charts.  Every chart degrades to an explicit empty state
 * when the queried range contains no data — no sample numbers are invented.
 */

const RENDER_EVENT = 'streamlit:render';

const charts = {};
let payload = null;
let animate = false;

/* Palette per theme — mirrors config.THEMES so the charts sit inside the same
   visual language as the rest of the application. */
const PALETTES = {
  dark: {
    lean: '#d9d3c4',
    tilt: '#cbab74',
    ear: '#a49a87',
    bpm: '#8fb99a',
    critical: '#c0756c',
    good: '#8fb99a',
    elevated: '#cbab74',
    alert: '#c0756c',
    untracked: '#736e66',
    accent: '#d9d3c4',
    accentFill: 'rgba(217, 211, 196, 0.32)',
    leanFill: 'rgba(217, 211, 196, 0.26)',
    grid: 'rgba(234, 231, 225, 0.07)',
    border: '#2a2724',
    tick: '#736e66',
    legend: '#9c968c',
    tooltipBg: '#1c1b19',
    tooltipBorder: '#3a352f',
    tooltipText: '#eae7e1',
    sliceBorder: '#1c1b19',
  },
  light: {
    lean: '#6259ca',
    tilt: '#e0a13c',
    ear: '#9b93e8',
    bpm: '#12a171',
    critical: '#d7514a',
    good: '#12a171',
    elevated: '#e0a13c',
    alert: '#d7514a',
    untracked: '#94a1b2',
    accent: '#6259ca',
    accentFill: 'rgba(98, 89, 202, 0.30)',
    leanFill: 'rgba(98, 89, 202, 0.24)',
    grid: 'rgba(19, 26, 36, 0.07)',
    border: '#e6ebf4',
    tick: '#94a1b2',
    legend: '#5f6b7d',
    tooltipBg: '#ffffff',
    tooltipBorder: '#e6ebf4',
    tooltipText: '#131a24',
    sliceBorder: '#ffffff',
  },
};

let activeTheme = 'dark';
let COLORS = { ...PALETTES.dark };

function applyTheme(theme) {
  activeTheme = theme === 'light' ? 'light' : 'dark';
  COLORS = { ...PALETTES[activeTheme] };
  document.documentElement.dataset.theme = activeTheme;
  if (document.body) document.body.dataset.theme = activeTheme;
}

function post(type, data) {
  window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, data || {}), '*');
}

function setHeight() {
  const height = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
  post('streamlit:setFrameHeight', { height: Math.ceil(height) + 8 });
}

function baseOptions(unit = '') {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: animate ? { duration: 420 } : false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: COLORS.tooltipBg,
        borderColor: COLORS.tooltipBorder,
        borderWidth: 1,
        titleFont: { family: 'JetBrains Mono', size: 10 },
        bodyFont: { family: 'Inter', size: 11 },
        padding: 9,
        callbacks: {
          label: (ctx) => {
            const value = ctx.parsed.y;
            if (value === null || value === undefined) return `${ctx.dataset.label}: no sample`;
            return `${ctx.dataset.label}: ${typeof value === 'number' ? value.toFixed(2) : value}${unit}`;
          },
        },
      },
    },
    scales: {
      x: {
        grid: { color: COLORS.grid, drawTicks: false },
        ticks: {
          color: COLORS.tick,
          font: { family: 'JetBrains Mono', size: 9 },
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 7,
        },
        border: { color: COLORS.tooltipBorder },
      },
      y: {
        grid: { color: COLORS.grid },
        ticks: { color: COLORS.tick, font: { family: 'JetBrains Mono', size: 9 } },
        border: { color: COLORS.tooltipBorder },
        suggestedMin: 0,
      },
    },
  };
}

function hasValues(values) {
  return Array.isArray(values) && values.some((v) => v !== null && v !== undefined);
}

function render(id, config, emptyId, hasData) {
  const canvas = document.getElementById(id);
  const empty = document.getElementById(emptyId);
  if (!canvas) return;
  if (charts[id]) {
    charts[id].destroy();
    delete charts[id];
  }
  if (!hasData) {
    if (empty) empty.classList.add('show');
    return;
  }
  if (empty) empty.classList.remove('show');
  charts[id] = new window.Chart(canvas, config);
}

function lineChart(id, emptyId, labels, values, label, colour, unit, threshold) {
  const options = baseOptions(unit);
  if (Number.isFinite(threshold)) {
    options.plugins.annotation = undefined;
    options.scales.y.suggestedMax = Math.max(threshold * 1.8, 5);
    options.plugins.tooltip.callbacks.footer = (items) =>
      Number.isFinite(threshold) ? `threshold ${threshold}${unit}` : '';
  }
  render(
    id,
    {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label,
            data: values,
            borderColor: colour,
            backgroundColor: `${colour}22`,
            borderWidth: 1.8,
            pointRadius: 0,
            spanGaps: false,
            tension: 0.25,
            fill: true,
          },
          ...(Number.isFinite(threshold)
            ? [
                {
                  label: 'Threshold',
                  data: labels.map(() => threshold),
                  borderColor: COLORS.critical,
                  borderDash: [5, 5],
                  borderWidth: 1,
                  pointRadius: 0,
                  fill: false,
                },
              ]
            : []),
        ],
      },
      options,
    },
    emptyId,
    hasValues(values)
  );
}

function renderAll() {
  if (!payload || typeof window.Chart === 'undefined') {
    if (typeof window.Chart === 'undefined') {
      document.querySelectorAll('.empty').forEach((node) => {
        node.textContent = 'Chart.js failed to load — charts unavailable.';
        node.classList.add('show');
      });
    }
    return;
  }
  const series = payload.series || {};
  const labels = series.labels || [];
  const thresholds = payload.thresholds || {};
  animate = Boolean(payload.animate);

  document.getElementById('leanNote').textContent = `${labels.length} samples`;
  document.getElementById('tiltNote').textContent = `limit ${thresholds.tilt}°`;
  document.getElementById('earNote').textContent = labels.length ? '' : '';
  document.getElementById('bpmNote').textContent = `reference ${thresholds.blinkReference} bpm`;

  lineChart('leanChart', 'leanEmpty', labels, series.forward_lean || [], 'Forward lean', COLORS.lean, '°', thresholds.lean);
  lineChart('tiltChart', 'tiltEmpty', labels, series.shoulder_tilt || [], 'Shoulder tilt', COLORS.tilt, '°', thresholds.tilt);
  lineChart('earChart', 'earEmpty', labels, series.ear_value || [], 'EAR', COLORS.ear, '', null);
  lineChart('bpmChart', 'bpmEmpty', labels, series.blink_rate || [], 'Blink rate', COLORS.bpm, ' bpm', thresholds.blinkReference);

  const distribution = payload.distribution || {};
  const distValues = [
    distribution.good || 0,
    distribution.elevated || 0,
    distribution.alert || 0,
    distribution.untracked || 0,
  ];
  render(
    'statusChart',
    {
      type: 'doughnut',
      data: {
        labels: ['Stable', 'Elevated', 'Alert', 'Untracked'],
        datasets: [
          {
            data: distValues,
            backgroundColor: [COLORS.good, COLORS.elevated, COLORS.alert, COLORS.untracked],
            borderColor: COLORS.sliceBorder,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: animate ? { duration: 420 } : false,
        cutout: '62%',
        plugins: {
          legend: {
            position: 'right',
            labels: {
              color: COLORS.legend,
              font: { family: 'Inter', size: 10 },
              boxWidth: 9,
              boxHeight: 9,
              usePointStyle: true,
            },
          },
          tooltip: {
            backgroundColor: COLORS.tooltipBg,
            borderColor: COLORS.tooltipBorder,
            borderWidth: 1,
            callbacks: {
              label: (ctx) => {
                const total = ctx.dataset.data.reduce((a, b) => a + b, 0) || 1;
                const pct = ((ctx.parsed / total) * 100).toFixed(1);
                return `${ctx.label}: ${ctx.parsed}s (${pct}%)`;
              },
            },
          },
        },
      },
    },
    'statusEmpty',
    distValues.some((v) => v > 0)
  );

  const durations = payload.durations || { labels: [], minutes: [] };
  render(
    'durationChart',
    {
      type: 'bar',
      data: {
        labels: durations.labels || [],
        datasets: [
          {
            label: 'Minutes',
            data: durations.minutes || [],
            backgroundColor: COLORS.accentFill,
            borderColor: COLORS.accent,
            borderWidth: 1,
            borderRadius: 4,
          },
        ],
      },
      options: (() => {
        const options = baseOptions(' min');
        options.scales.y.title = {
          display: true,
          text: 'minutes',
          color: COLORS.tick,
          font: { family: 'JetBrains Mono', size: 9 },
        };
        options.plugins.legend.display = false;
        return options;
      })(),
    },
    'durationEmpty',
    hasValues(durations.minutes)
  );

  const daily = payload.daily || { labels: [], monitored_minutes: [] };
  render(
    'consistencyChart',
    {
      type: 'bar',
      data: {
        labels: daily.labels || [],
        datasets: [
          {
            label: 'Monitored minutes',
            data: daily.monitored_minutes || [],
            backgroundColor: COLORS.leanFill,
            borderColor: COLORS.lean,
            borderWidth: 1,
            borderRadius: 4,
          },
          {
            label: 'Mean forward lean (°)',
            data: daily.avg_forward_lean || [],
            type: 'line',
            borderColor: COLORS.tilt,
            backgroundColor: 'transparent',
            borderWidth: 1.6,
            pointRadius: 2,
            yAxisID: 'y1',
            tension: 0.25,
          },
        ],
      },
      options: (() => {
        const options = baseOptions(' min');
        options.plugins.legend = {
          display: true,
          labels: {
            color: COLORS.legend,
            font: { family: 'Inter', size: 10 },
            boxWidth: 10,
            usePointStyle: true,
          },
        };
        options.scales.y1 = {
          position: 'right',
          grid: { drawOnChartArea: false },
          ticks: { color: COLORS.tick, font: { family: 'JetBrains Mono', size: 9 } },
          title: {
            display: true,
            text: 'degrees',
            color: COLORS.tick,
            font: { family: 'JetBrains Mono', size: 9 },
          },
        };
        return options;
      })(),
    },
    'consistencyEmpty',
    hasValues(daily.monitored_minutes)
  );

  applyToggles();
  setHeight();
}

function applyToggles() {
  document.querySelectorAll('[data-toggle]').forEach((checkbox) => {
    const key = checkbox.dataset.toggle;
    const panel = document.querySelector(`[data-panel="${key}"]`);
    if (!panel) return;
    panel.classList.toggle('hidden', !checkbox.checked);
  });
  // distribution panel stays visible; it has no toggle.
  setHeight();
}

const toggleHandler = (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  applyToggles();
};
document.addEventListener('change', toggleHandler);

window.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || data.type !== RENDER_EVENT) return;
  const args = data.args || {};
  payload = args.payload || null;
  applyTheme(args.theme || activeTheme);
  renderAll();
});

window.addEventListener('resize', () => {
  Object.values(charts).forEach((chart) => chart.resize());
  setHeight();
});

applyTheme('dark');
post('streamlit:componentReady', { apiVersion: 1 });
setTimeout(setHeight, 60);
setTimeout(setHeight, 500);
