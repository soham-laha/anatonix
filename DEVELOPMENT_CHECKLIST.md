# Anatonix — development checklist

Status legend: **[x] done & verified** · **[~] implemented, verified manually** · **[ ] not done**

Everything marked **[x]** was exercised on the running application (headless Chromium with a
synthetic 15 FPS webcam feed) or by an automated test, not just written.

---

## 1. Environment

* [x] Python 3.13 virtual environment; `streamlit 1.66.1`, `pandas`, `numpy`, `reportlab`,
      `matplotlib` installed from `requirements.txt`
* [x] Node 20 used only to run the JavaScript test suite (no build step in the app)
* [x] SQLite schema created on first launch at `data/anatonix.db`
* [x] `.streamlit/config.toml` — neutral widget theme seeded from the dark palette, usage stats
      disabled (the app itself is painted entirely from `config.THEMES` via CSS)
* [x] Logging to `data/logs/anatonix.log` with rotating file handler

## 2. Backend

* [x] `backend/database.py` — schema, sessions, telemetry, alerts, calibrations, settings,
      events, exports; `INSERT OR IGNORE` on client-generated UIDs (replay-safe);
      stale-session recovery on startup; retention cleanup; per-session and bulk deletion
* [x] `backend/data_validation.py` — payload/event/calibration/settings validators with
      clamping, type coercion, NaN rejection, string sanitising
* [x] `backend/posture_math.py` — vector geometry, lean/tilt/proximity, EMA smoothing,
      neck-load mapping, degenerate-landmark guards
* [x] `backend/session_manager.py` — payload processing, session lifecycle, event routing,
      component-originated setting changes
* [x] `backend/analytics.py` — range aggregation, per-session metrics, distributions,
      previous-period comparison, composite score, insufficient-data handling
* [x] `backend/report_generator.py` — CSV writers, PDF with tables + matplotlib trend chart +
      limitations + privacy statement
* [x] `backend/logging_setup.py` — single configured logger hierarchy, no silent `except: pass`

## 3. Component / computer vision

* [x] MediaPipe **Tasks Vision 0.10.14** loaded as an ES module from jsDelivr; `pose_landmarker_lite`
      + `face_landmarker` models; GPU delegate with CPU fallback
* [x] Camera acquisition through `getUserMedia`, mirrored preview, camera released on stop
* [x] Skeleton overlay: ear→shoulder vectors, shoulder line, eye landmarks, "cover"-fit mapping
      so the overlay stays aligned at any panel aspect ratio
* [x] Ocular PIP: live eye crop on a second canvas with eye-contour overlay
* [x] Forward lean, shoulder tilt, proximity ratio, EAR, blink state machine, blink rate,
      tracking confidence, quality checks (face, shoulders, lighting, framing, visibility)
* [x] Calibration: 3 s capture, per-frame quality gating, baseline + spread, quality score
* [x] Alert engine: sustained-frames debounce, hysteresis recovery, per-type cooldown,
      dismiss/snooze, duplicate suppression
* [x] Break reminder timer with toast + optional speech
* [x] Telemetry sampler (1 Hz) with batching, replay-safe UIDs, offline retry in `bridge.js`
* [x] Graceful failure paths: camera denied, model load failure, no landmarks, no speech
      support, component bridge timeout
* [x] Keyboard shortcuts (<kbd>C</kbd> calibrate, <kbd>Esc</kbd> close, <kbd>Space</kbd> pause)

## 4. Streamlit shell

* [x] `app.py` — page config, theme injection, sidebar with live status badges and database
      stats, `?page=` navigation, error boundary per page
* [x] Dashboard — status cards, session summary, live snapshot, insights, recent activity
* [x] Live Monitoring — component mount, environment banner, session shortcuts
* [x] Analytics — range selector, 6 insight cards, 7 Chart.js charts, comparison, composite score
* [x] Reports — session history, per-session CSV/PDF, range CSV, storage stats, deletion
* [x] Settings — 6 tabs, validated + persisted server-side
* [x] `ui/kit.py` — escaped HTML card/metric/table/empty-state helpers (no injected user data)
* [x] Consistent design system across Streamlit pages and the component
* [x] **Two themes driven by one token set** — `config.THEMES['light'|'dark']` →
      `theme_css_variables()` → `ui/theme.css`, with the same palettes mirrored in
      `frontend/styles.css` (`:root[data-theme="light"]`) and `charts_frontend/charts_app.js`
* [x] Light theme: white cards on a cool grey canvas, navy navigation rail, indigo accent,
      soft shadows; dark theme: near-black canvas, hairline borders, warm sand accent,
      desaturated chart ramp
* [x] Theme choice persisted in SQLite (`settings.theme`), applied to the shell, the monitoring
      iframe (`data-theme` on `<html>` + `#anxRoot`) and the analytics charts on the same render
* [x] Sliders, radios, toggles, selectboxes, tabs, alerts, buttons, disabled states and scrollbars
      re-pointed at the tokens (Streamlit's own theme is fixed at server start)
* [x] `ui.kit.table` replaces `st.dataframe` for the session history so the table (including its
      progress bar) is themed in both modes instead of being locked to the server theme
* [x] Camera-stage overlays (standby copy, HUD badges, posture flag, ocular caption) use a
      dedicated always-light `--anx-on-video-*` token group, because the video backdrop stays dark
      in both themes
* [x] Canvas renderers (skeleton overlay, ocular crop, live sparkline) read their colours from the
      live tokens at draw time, so they re-theme without a reload

## 5. Tests

* [x] `python -m pytest` → **133 passed** (121 + 12 design-system contract tests in
      `tests/test_theme.py`: palette parity, token → CSS emission, chart colours per theme,
      `kit.table` escaping and missing-value handling)
* [x] `node --test frontend/tests/` → **40 passed** (includes 7 inference-scheduler tests)
* [x] Page smoke test across all five pages: no exceptions, no console errors
* [x] Live resilience check: `data/anatonix.db` deleted while the server was running → schema
      recreated on the next request, settings/dashboard still render
* [x] `tools/browser_smoke_test.py` — 19/19 checkpoints pass against a live server
      (shipped in the repo so the browser run is reproducible)

## 6. End-to-end verification (headless Chromium, synthetic webcam)

* [x] Component iframe mounts and completes the Streamlit handshake
* [x] Camera permission → live video → models load → landmarks tracked
* [x] Live metrics update (`Tracking normally`, lean, EAR, blinks, bpm, timer, confidence)
* [x] Quality rows report real values (`Face landmarks tracked`, `Both shoulders tracked (100%)`,
      `Lighting adequate (126/255)`, `Framing within tolerance`, `100% mean visibility`)
* [x] Calibration dialog: progress 7→67 %, 45 frames captured, baseline `38.6°`, quality `88 %`
* [x] Post-calibration lean drops to ~1° (relative to baseline)
* [x] Alerts fire and are persisted (`posture_alert`, measurement ≈ 38°, threshold 12°)
* [x] Pause → `Monitoring paused` / button becomes `Resume`; resume → `Tracking normally`
* [x] Keyboard <kbd>C</kbd> opens the calibration dialog, <kbd>Esc</kbd> closes it
* [x] Stop → standby overlay returns, camera tracks `ended`, session closed with duration
* [x] SQLite after a full cycle: session row with duration, 269 telemetry rows (0 NULLs in the
      measured columns), alerts, calibration (quality 0.882, 45 frames), events
* [x] Analytics renders 7 charts from stored telemetry, with thresholds, distribution and
      session durations
* [x] Reports: session history (real per-session averages), CSV 8,015 B, PDF 78,294 B (2 pages),
      range CSV 23,692 B — all downloaded successfully
* [x] Settings: slider change → save → `settings` row updated to 15.0 → value survives reload
* [x] Break reminder fires (forced timer) → toast shown → `break_reminder` event stored
* [x] Theme switch verified live: `settings.theme` → `light` re-renders the shell, the monitoring
      iframe (`data-theme=light`, card background `rgb(255,255,255)`) and the Chart.js palettes
      without restarting the server
* [x] All six documentation screenshots regenerated in the new design, plus
      `07-dashboard-light.png` for the light theme

## 7. Bugs found during verification and fixed

| # | Symptom | Fix |
| --- | --- | --- |
| 1 | `render_card() got an unexpected keyword argument 'raw'` on Settings → About | removed the kwarg (signature never had it) |
| 2 | Calibration "Start capture" button unclickable — modal centred on the whole iframe and fell outside the viewport | moved `#calibModal` into `#stage`; `.modal-backdrop` uses `position: absolute; inset: 0`, modal width `min(470px, 100%)`, toast stack anchored inside the card |
| 3 | Camera panel filled the viewport and pushed the telemetry column below the fold | grid breakpoint lowered (980 px), stage `max-height: 468px`, landmark/video mapping switched to "cover" fit so nothing distorts |
| 4 | Ocular PIP always showed "Ocular crop inactive" | `drawFrame()` did not pass the `<video>` element to the eye renderer |
| 5 | `Reports` page crashed: `cannot convert float NaN to integer` | `kit.duration()` handles NaN/inf; pandas `NaN` normalised to `None` before formatting |
| 6 | Alert banner permanently visible with "Forward lean detected" even with zero alerts | `.alert-banner` sets `display: flex`, which overrode the `hidden` attribute; added `.alert-banner[hidden] { display: none; }` |
| 7 | Audio toggle reverted to "Audio off" after the next Streamlit rerun | component-initiated `settings_changed` events now persist validated settings server-side, so the next `streamlit:render` carries the new value |
| 8 | Alerts measured against the old baseline right after calibration | calibration completion resets the alert engine, hides the banner and clears the smooth-value cache |
| 9 | PDF page 1 ended with an orphaned "Trend overview" heading; alert types printed as `posture_alert` | heading + chart wrapped in `KeepTogether`, chart height reduced; alert types title-cased |
| 10 | matplotlib `AutoDateLocator` warning for short sessions | explicit `SecondLocator` interval for spans under 10 minutes |
| 11 | Streamlit deprecation warnings for `use_container_width` | replaced with `width="stretch"` (requires Streamlit ≥ 1.49, pinned in `requirements.txt`) |
| 12 | Stale-Streamlit-module confusion during development | the dev server runs with `--server.fileWatcherType none`; restart after edits |
| 13 | Right after calibration the panel briefly reverted to "Not calibrated" (props were computed before the calibration reached SQLite) | a locally captured baseline is kept for up to 20 s until server props catch up; server stays authoritative afterwards, so deletions still propagate |
| 14 | Camera preview stuttered/flickered badly (worst during calibration), and the FPS chip showed ~14 while the cap said 30 | root cause: the preview was a canvas repainted from JS, so every repaint waited for inference; plus blurred `backdrop-filter` overlays on top of it. Fix: the preview is now the native `<video>` element (browser compositing), the overlay canvas is transparent and only carries landmarks, all `backdrop-filter`s were removed, and the render loop uses `requestVideoFrameCallback` |
| 15 | Inference could monopolise the main thread (~100 % duty on slower machines), starving the overlay and eye crop | added a frame budget: inference may use at most 70 % of wall-clock time, with a 6 fps floor; extracted as `scheduleInterval()` and unit-tested (5 tests) |
| 16 | FPS chip reported the requested cap rather than the achieved rate, and had no explanation | chip now shows `measured / cap` with a tooltip listing pose/face milliseconds and the delegate; the camera-quality card adds a hint explaining the bottleneck |
| 17 | Calibration collected frames at the full pose+face cost, and pausing ocular inference falsely reported "No face landmarks" / blanked the eye crop | calibration capture now runs pose-only (~2× frame rate), the face quality check is skipped while suppressed, and the ocular card + eye crop show an explicit "paused for calibration" state |
| 18 | `mirror_preview` setting had no effect (the canvas always mirrored its own copy) | now applied to both the preview video element and the overlay renderer |
| 19 | Deleting `data/anatonix.db` while the app is running crashed every page with `no such table: settings` until a restart | `Database.connection()` now detects a missing schema and recreates it transparently; covered by `test_schema_is_recreated_if_the_database_file_disappears` and verified live (file deleted under a running server, pages kept working) |
| 20 | Retheme regression: `frontend/ui.js` still referenced the removed `ACCENT` / `CRITICAL` constants, so the eye crop threw on every draw (`ocular crop canvas painted — 0 px`) | colour constants replaced by a `palette()` helper that reads the live tokens; caught by `tools/browser_smoke_test.py` (17/19) and re-verified at 19/19 |
| 21 | Standby copy, HUD badges and the ocular caption were unreadable in light mode (dark text over the dark video stage) | dedicated always-light `--anx-on-video-*` tokens for anything drawn over the camera |
| 22 | Streamlit's slider/radio/toggle/selectbox/tabs kept the server theme, so they clashed with the in-app switch (invisible slider thumbs, white expander on the navy sidebar, dark selectboxes on a light page) | react-aria + baseweb selectors re-pointed at the tokens; the tab underline indicator (`.react-aria-SelectionIndicator`) is hidden in favour of a segmented control |
| 23 | `st.dataframe` paints on a canvas with the server theme, so the session history could not follow the light/dark switch (and its progress bar stayed indigo) | replaced by `ui.kit.table`, a token-styled table with the same columns, mono numerics and an inline progress bar |
| 24 | `ui.kit.table` drew a **full** progress bar for a `NULL` tracking value, i.e. it implied 100 % for missing data | missing values now render an empty track plus `—`; covered by `test_table_handles_a_missing_bar_value` |
| 25 | The brand mark disappeared in light mode: the sidebar's `*` colour rule (higher specificity) repainted the SVG `currentColor` near-white on a near-white tile | explicit accent colour on `.anx-side-logo` / its `svg` |
| 26 | Disabled `st.download_button`s inherited the dark theme's 40 %-alpha text and were illegible on the light canvas | disabled buttons get an explicit theme-aware surface, border and muted label |

## 7b. Clean-machine install

* [x] `run.sh` verified from a fresh copy of the project: creates `.venv`, installs
      `requirements.txt` (25 s), starts Streamlit on the requested port, creates the database,
      dashboard renders — all without a pre-existing environment
* [x] Performance pass verified in a headless browser: preview is the native video element
      (fills the stage, `object-fit: cover`), overlay pixels align with the subject, no
      `backdrop-filter` anywhere, calibration collects ~2× the frames in the same wall-clock
      window, and the ocular card reports "Paused for calibration" while suppressed
* [x] `python -m venv` + `pip install -r requirements.txt` verified in an empty venv
      (`streamlit 1.66.1`, `pandas 2.3.3`, `numpy 2.5.4`, `reportlab 4.5.1`, `matplotlib 3.11.2`)
* [x] Arch/Manjaro "No module named streamlit" case documented (system Python is externally
      managed; use `./run.sh`)

## 8. Documentation

* [x] `README.md` — features, architecture, installation, usage, measurement definitions,
      full settings reference, privacy, testing, troubleshooting, limitations
* [x] Deviation note about the single-file brief, the legacy MediaPipe API and the hardcoded
      interpreter path
* [x] Screenshots in `docs/screenshots/`
* [x] `requirements.txt`, `pytest.ini`, `.gitignore`
* [x] This checklist
* [x] Design-system section in the README: token flow diagram, the two palettes side by side,
      overlay/number/tables notes, and the light-theme dashboard screenshot

## 9. Known gaps / deliberate decisions

* [ ] **No browser test in CI.** The end-to-end run ships as
      `tools/browser_smoke_test.py` and is documented, but it needs a browser download and a
      sample video, so it is not wired into CI. The fast suites in `tests/` cover the logic.
* [ ] **MediaPipe models are not vendored** (≈ 12 MB each). They are fetched from Google Cloud
      Storage on first use and cached by the browser; offline first-run is therefore not
      supported for the vision layer (Chart.js *is* vendored).
* [ ] **Blink detection under-reports at very low inference FPS** (e.g. 1–2 FPS in software
      rendering): a 120 ms eye closure can fall entirely between two processed frames. Expected
      behaviour at the normal 15 FPS cap; documented in the README.
* [ ] **Streamlit's own widget theme is fixed at server start** (it cannot follow the in-app
      switch). Everything visible is re-pointed at the tokens in `ui/theme.css`; the one canvas
      surface that could not be restyled — `st.dataframe` — was replaced by `ui.kit.table`, and
      the remaining internals (tooltips, code blocks, spinners) are close enough in both palettes
      to read correctly.
* [ ] **Single-user, single-camera design.** No multi-user accounts, no remote sync — that is a
      privacy decision, not an oversight.
* [ ] Sessions that are killed hard (browser closed mid-monitoring) are closed on the next app
      start by the stale-session recovery pass, with their last telemetry timestamp as the end.
