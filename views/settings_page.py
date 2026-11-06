"""Settings — thresholds, ocular, notifications, appearance and privacy."""

from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

import ui.kit as kit
from backend.database import Database
from backend.posture_math import neck_load_estimate
from config import DEFAULT_SETTINGS, GENERAL_DISCLAIMER, NECK_LOAD_DISCLAIMER
from views.common import save_settings


def render(db: Database, settings: Dict[str, Any]) -> None:
    kit.page_header(
        "Settings",
        "Monitoring thresholds, ocular parameters, notification behaviour, appearance and "
        "privacy controls. Every change is validated, clamped to a safe range and stored "
        "in your local database.",
        meta=["persisted in SQLite", f"{len(DEFAULT_SETTINGS)} keys", "validated on save"],
    )

    tabs = st.tabs(["Monitoring", "Ocular", "Notifications", "Appearance", "Privacy & data", "About"])

    with tabs[0]:
        _monitoring_tab(db, settings)
    with tabs[1]:
        _ocular_tab(db, settings)
    with tabs[2]:
        _notifications_tab(db, settings)
    with tabs[3]:
        _appearance_tab(db, settings)
    with tabs[4]:
        _privacy_tab(db, settings)
    with tabs[5]:
        _about_tab(db, settings)


# --------------------------------------------------------------------------
def _monitoring_tab(db: Database, settings: Dict[str, Any]) -> None:
    st.markdown(
        kit.banner(
            "Thresholds are <b>your</b> alert limits, not clinical norms. Alerts only fire after "
            "the violation persists for the configured number of frames, which prevents flicker.",
            allow_html=True,
        ),
        unsafe_allow_html=True,
    )
    left, right = st.columns(2, gap="medium")
    with left:
        kit.render_card("", "Posture thresholds", "fa-sliders")
        lean = st.slider(
            "Forward lean threshold (°)",
            min_value=2.0,
            max_value=45.0,
            value=float(settings["forward_lean_threshold"]),
            step=0.5,
            help="Relative lean above your calibrated baseline before an alert can trigger.",
            key="set_lean",
        )
        tilt = st.slider(
            "Shoulder tilt threshold (°)",
            min_value=1.0,
            max_value=25.0,
            value=float(settings["shoulder_tilt_threshold"]),
            step=0.5,
            key="set_tilt",
        )
        sensitivity = st.slider(
            "Tracking sensitivity (landmark visibility gate)",
            min_value=0.0,
            max_value=1.0,
            value=float(settings["tracking_sensitivity"]),
            step=0.05,
            help="Higher values require clearer landmarks before metrics are trusted.",
            key="set_sens",
        )
        if st.button("Save posture thresholds", type="primary", key="save_monitoring"):
            _persist(
                db,
                {
                    "forward_lean_threshold": lean,
                    "shoulder_tilt_threshold": tilt,
                    "tracking_sensitivity": sensitivity,
                },
            )

    with right:
        kit.render_card("", "Alert behaviour", "fa-bell")
        bad_frames = st.slider(
            "Consecutive violating frames before an alert",
            min_value=3,
            max_value=120,
            value=int(settings["bad_frame_count"]),
            step=1,
            key="set_bad_frames",
        )
        recovery_frames = st.slider(
            "Stable recovery frames before clearing",
            min_value=3,
            max_value=120,
            value=int(settings["recovery_frame_count"]),
            step=1,
            help="Hysteresis: an alert clears only after this many consecutive good frames.",
            key="set_recovery",
        )
        cooldown = st.slider(
            "Alert cooldown (s)",
            min_value=0.5,
            max_value=60.0,
            value=float(settings["alert_cooldown_seconds"]),
            step=0.5,
            key="set_cooldown",
        )
        if st.button("Save alert behaviour", type="primary", key="save_alert_engine"):
            _persist(
                db,
                {
                    "bad_frame_count": bad_frames,
                    "recovery_frame_count": recovery_frames,
                    "alert_cooldown_seconds": cooldown,
                },
            )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    left2, right2 = st.columns(2, gap="medium")
    with left2:
        kit.render_card("", "Camera", "fa-camera")
        cameras: List[Dict[str, str]] = st.session_state.get("anatonix_cameras") or []
        if cameras:
            labels = ["System default"] + [c.get("label", c.get("id", "camera")) for c in cameras]
            current = settings.get("camera_device_id") or ""
            index = 0
            for i, camera in enumerate(cameras):
                if camera.get("id") == current:
                    index = i + 1
            choice = st.selectbox("Camera device", labels, index=index, key="set_camera")
            device_id = "" if choice == "System default" else cameras[labels.index(choice) - 1].get("id", "")
        else:
            st.info(
                "Camera devices are detected when the Live Monitoring page initialises the "
                "camera. Open it once, then return here to choose a device."
            )
            device_id = settings.get("camera_device_id") or ""
        processing = st.select_slider(
            "Inference rate cap (fps)",
            options=[5, 8, 10, 12, 15, 20, 24, 30],
            value=int(settings["inference_fps"]),
            help="Lower values reduce CPU/GPU load. 15 fps is comfortable on laptops.",
            key="set_fps",
        )
        duty = st.slider(
            "Inference frame budget",
            0.5,
            1.0,
            float(settings["inference_duty"]),
            step=0.05,
            help=(
                "Share of wall-clock time the models are allowed to occupy. The remainder is "
                "kept free so the browser can repaint the overlay and the eye crop. Lower this "
                "if the interface feels sluggish; set it to 1.0 to give inference every "
                "millisecond (the preview may then stutter)."
            ),
            key="set_duty",
        )
        if st.button("Save camera settings", type="primary", key="save_camera"):
            _persist(
                db,
                {
                    "camera_device_id": device_id,
                    "inference_fps": processing,
                    "inference_duty": duty,
                },
            )
        st.caption(
            "The FPS cap is an upper bound: the chip shows the rate actually achieved. "
            "Camera selection is applied the next time monitoring starts."
        )

    with right2:
        kit.render_card("", "Telemetry sampling", "fa-database")
        sample_ms = st.select_slider(
            "Sampling interval (ms)",
            options=[500, 1000, 2000, 5000],
            value=int(settings["sample_interval_ms"]),
            help="1 Hz (1000 ms) is the default and gives ~3600 rows per hour.",
            key="set_sample_ms",
        )
        smoothing = st.slider(
            "Live smoothing (EMA α)",
            min_value=0.05,
            max_value=1.0,
            value=float(settings["smoothing_alpha"]),
            step=0.05,
            help="Lower values smooth the live readout more aggressively.",
            key="set_smoothing",
        )
        if st.button("Save sampling settings", type="primary", key="save_sampling"):
            _persist(db, {"sample_interval_ms": sample_ms, "smoothing_alpha": smoothing})
        st.caption(
            "Sampling changes take effect on the next telemetry flush. Stored rows keep their "
            "original timestamps, so historical analytics stay accurate."
        )


def _ocular_tab(db: Database, settings: Dict[str, Any]) -> None:
    st.markdown(
        kit.banner(
            "Blink categories are configurable monitoring bands. Blink frequency alone cannot "
            "diagnose dry-eye disease or any other condition.",
            icon="fa-eye",
            allow_html=True,
        ),
        unsafe_allow_html=True,
    )
    left, right = st.columns(2, gap="medium")
    with left:
        kit.render_card("", "Blink detection", "fa-eye")
        ear = st.slider(
            "Closed-eye EAR threshold",
            min_value=0.05,
            max_value=0.35,
            value=float(settings["ear_threshold"]),
            step=0.005,
            format="%.3f",
            help="Eye aspect ratio below this value counts as closed.",
            key="set_ear",
        )
        min_duration = st.slider(
            "Minimum blink duration (ms)",
            min_value=10,
            max_value=300,
            value=int(settings["blink_min_duration_ms"]),
            step=5,
            help="Closures shorter than this are treated as sensor noise.",
            key="set_blink_min",
        )
        debounce = st.slider(
            "Blink debounce (ms)",
            min_value=50,
            max_value=600,
            value=int(settings["blink_debounce_ms"]),
            step=10,
            help="Suppresses duplicate events from micro-oscillation around the threshold.",
            key="set_blink_debounce",
        )
        if st.button("Save ocular detection", type="primary", key="save_ocular"):
            _persist(
                db,
                {
                    "ear_threshold": ear,
                    "blink_min_duration_ms": min_duration,
                    "blink_debounce_ms": debounce,
                },
            )
    with right:
        kit.render_card("", "Blink rate categories", "fa-gauge")
        low = st.slider(
            "Low-frequency band upper limit (bpm)",
            min_value=1.0,
            max_value=20.0,
            value=float(settings["blink_low_bpm"]),
            step=0.5,
            key="set_blink_low",
        )
        reference = st.slider(
            "Reference-range floor (bpm)",
            min_value=2.0,
            max_value=40.0,
            value=float(settings["blink_reference_bpm"]),
            step=0.5,
            key="set_blink_ref",
        )
        if reference <= low:
            st.warning("The reference floor must be greater than the low-frequency limit.")
        if st.button("Save blink categories", type="primary", key="save_blink_cats"):
            _persist(db, {"blink_low_bpm": low, "blink_reference_bpm": max(reference, low + 0.5)})
        st.markdown(
            kit.kv_rows(
                [
                    (f"below {low:.1f} bpm", "Low blink frequency", "warn"),
                    (f"{low:.1f}–{reference:.1f} bpm", "Reduced blink frequency", "warn"),
                    (f"{reference:.1f} bpm or above", "Within reference range", "good"),
                ]
            ),
            unsafe_allow_html=True,
        )


def _notifications_tab(db: Database, settings: Dict[str, Any]) -> None:
    left, right = st.columns(2, gap="medium")
    with left:
        kit.render_card("", "Spoken alerts", "fa-volume-high")
        audio_enabled = st.toggle(
            "Enable spoken posture alerts",
            value=bool(settings["audio_enabled"]),
            key="set_audio",
        )
        volume = st.slider("Volume", 0.0, 1.0, float(settings["audio_volume"]), 0.05, key="set_volume")
        rate = st.slider("Speech rate", 0.5, 2.0, float(settings["speech_rate"]), 0.05, key="set_rate")
        voice = st.text_input(
            "Preferred voice name (optional)",
            value=str(settings.get("voice_name") or ""),
            help="Leave empty to use the browser default voice. Voice names come from your OS.",
            key="set_voice",
        )
        if st.button("Save notification settings", type="primary", key="save_notifications"):
            _persist(
                db,
                {
                    "audio_enabled": audio_enabled,
                    "audio_volume": volume,
                    "speech_rate": rate,
                    "voice_name": voice,
                },
            )
        st.caption(
            "Speech uses the browser Web Speech API. If the browser has no voices available the "
            "toggle on the Live Monitoring page is disabled and monitoring continues silently."
        )
    with right:
        kit.render_card("", "Break reminders", "fa-mug-hot")
        breaks_enabled = st.toggle(
            "Enable break reminders",
            value=bool(settings["break_reminders_enabled"]),
            key="set_breaks",
        )
        interval = st.slider(
            "Reminder interval (minutes)",
            min_value=5,
            max_value=180,
            value=int(settings["break_interval_minutes"]),
            step=5,
            key="set_break_interval",
        )
        break_audio = st.toggle(
            "Speak break reminders",
            value=bool(settings["break_audio"]),
            key="set_break_audio",
        )
        if st.button("Save reminder settings", type="primary", key="save_breaks"):
            _persist(
                db,
                {
                    "break_reminders_enabled": breaks_enabled,
                    "break_interval_minutes": interval,
                    "break_audio": break_audio,
                },
            )
        st.caption(
            "Reminders appear as a dismissible banner; dismissing one snoozes it for five minutes."
        )


def _appearance_tab(db: Database, settings: Dict[str, Any]) -> None:
    left, right = st.columns(2, gap="medium")
    with left:
        kit.render_card("", "Theme", "fa-palette")
        theme = st.radio(
            "Colour theme",
            ["dark", "light"],
            index=0 if settings["theme"] == "dark" else 1,
            horizontal=True,
            key="set_theme",
            help="The dark instrument theme is the primary design; a light variant is provided "
                 "for bright rooms.",
        )
        density = st.radio(
            "Density",
            ["comfortable", "compact"],
            index=0 if settings["density"] == "comfortable" else 1,
            horizontal=True,
            key="set_density",
        )
        if st.button("Save appearance", type="primary", key="save_appearance"):
            _persist(db, {"theme": theme, "density": density})
            st.rerun()
    with right:
        kit.render_card("", "Charts & display", "fa-chart-line")
        animations = st.toggle(
            "Chart animations",
            value=bool(settings["chart_animations"]),
            key="set_animations",
            help="Disable on low-power machines to reduce redraw cost.",
        )
        neck = st.toggle(
            "Show illustrative neck-load estimate",
            value=bool(settings["show_neck_load"]),
            key="set_neck",
        )
        if st.button("Save display settings", type="primary", key="save_display"):
            _persist(db, {"chart_animations": animations, "show_neck_load": neck})
        st.caption(NECK_LOAD_DISCLAIMER)
    st.markdown(
        kit.banner(
            f"Example of the current mapping: at 10° forward lean the illustrative estimate is "
            f"{neck_load_estimate(10)} lbs; at 26° it is {neck_load_estimate(26)} lbs. These are "
            "educational figures only.",
            icon="fa-circle-info",
        ),
        unsafe_allow_html=True,
    )


def _privacy_tab(db: Database, settings: Dict[str, Any]) -> None:
    stats = db.storage_stats()
    left, right = st.columns([1.2, 1], gap="medium")
    with left:
        kit.render_card(
            kit.kv_rows(
                [
                    ("Camera processing", "Local browser only", "good"),
                    ("Video frames stored", "None", "good"),
                    ("Facial images stored", "None", "good"),
                    ("Biometric templates", "None", "good"),
                    ("Network transmission", "None (numeric telemetry only stays local)", "good"),
                    ("Telemetry rows", f"{stats['telemetry_records']:,}", ""),
                    ("Database file", stats["db_path"], "dim"),
                ]
            ),
            "Privacy posture",
            "fa-shield-halved",
            note="verified by design",
        )
        st.markdown(
            kit.banner(
                "<b>What Anatonix stores.</b> Numeric telemetry only: timestamps, angles, eye "
                "aspect ratio, blink counts, proximity ratio, tracking confidence and posture "
                "status. Nothing that could reconstruct an image of you. Webcam frames are "
                "processed in the browser tab and discarded on the next animation frame.",
                icon="fa-lock",
                allow_html=True,
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            kit.banner(
                "<b>Limits of browser-based local processing.</b> The MediaPipe models are loaded "
                "from a CDN and run in your browser; your browser vendor and OS can still observe "
                "process activity, and any extension with camera permission could access the "
                "device. Anatonix itself never transmits frames.",
                tone="warn",
                icon="fa-triangle-exclamation",
                allow_html=True,
            ),
            unsafe_allow_html=True,
        )

    with right:
        kit.render_card("", "Data controls", "fa-broom")
        retention = st.slider(
            "Retention window (days)",
            min_value=1,
            max_value=365,
            value=int(settings["telemetry_retention_days"]),
            step=1,
            key="set_retention",
            help="Used by the 'Purge old data' action below.",
        )
        if st.button("Save retention window", key="save_retention"):
            _persist(db, {"telemetry_retention_days": retention})
            st.success("Retention window saved.")

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        confirm = st.checkbox(
            "I understand these actions permanently delete local data.",
            key="privacy_confirm",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Purge old data", disabled=not confirm, key="purge_old"):
                removed = db.purge_older_than(int(settings["telemetry_retention_days"]))
                st.success(f"Purged {removed} session(s) older than the retention window.")
                st.rerun()
            if st.button("Clear calibrations", disabled=not confirm, key="clear_calibrations"):
                count = db.clear_calibrations()
                st.success(f"Cleared {count} calibration record(s). Posture metrics are raw again.")
        with col_b:
            if st.button("Delete all sessions", disabled=not confirm, key="delete_all"):
                count = db.delete_all_sessions()
                st.success(f"Deleted {count} session(s) and their telemetry.")
                st.rerun()
            if st.button("Reset all settings", disabled=not confirm, key="reset_settings"):
                db.reset_settings()
                st.success("Settings restored to defaults.")
                st.rerun()


def _about_tab(db: Database, settings: Dict[str, Any]) -> None:
    stats = db.storage_stats()
    left, right = st.columns([1.2, 1], gap="medium")
    with left:
        kit.render_card(
            "<p>Anatonix is a desktop-first ergonomic self-monitoring instrument. It combines "
            "MediaPipe Pose and Face Landmarker inference running in your browser with a "
            "Streamlit backend that persists numeric telemetry to SQLite and derives analytics, "
            "alerts and reports from it.</p>"
            "<p>Forward lean, shoulder tilt and proximity are geometric estimates computed "
            "relative to your own calibration baseline. They are useful for spotting your own "
            "drift over time and are not clinical measurements.</p>"
            "<p><b>Measurement limitations.</b> " + GENERAL_DISCLAIMER + "</p>",
            "About Anatonix",
            "fa-circle-info",
        )
        kit.render_card(
            kit.kv_rows(
                [
                    ("Pose model", "MediaPipe Pose Landmarker (lite)", ""),
                    ("Ocular model", "MediaPipe Face Landmarker (478 landmarks)", ""),
                    ("Inference", "WebAssembly / GPU in-browser", ""),
                    ("Database", "SQLite (WAL)", ""),
                    ("Sensors", "Webcam only", ""),
                ]
            ),
            "Technical stack",
            "fa-microchip",
        )
    with right:
        kit.render_card(
            kit.kv_rows(
                [
                    ("Sessions", str(stats["sessions"]), ""),
                    ("Telemetry rows", f"{stats['telemetry_records']:,}", ""),
                    ("Alerts", str(stats["alerts"]), ""),
                    ("Calibrations", str(stats["calibrations"]), ""),
                    ("Database size", f"{stats['db_size_bytes'] / 1024:.0f} KB", ""),
                    ("First session", kit.local_time(stats["first_session"], with_seconds=False), ""),
                ]
            ),
            "Stored data",
            "fa-database",
        )
        kit.render_card(
            kit.kv_rows(
                [
                    ("Lean threshold", f"{settings['forward_lean_threshold']:.1f}°", ""),
                    ("Tilt threshold", f"{settings['shoulder_tilt_threshold']:.1f}°", ""),
                    ("EAR threshold", f"{settings['ear_threshold']:.3f}", ""),
                    ("Sampling", f"{settings['sample_interval_ms']} ms", ""),
                    ("Break interval", f"{settings['break_interval_minutes']} min", ""),
                ]
            ),
            "Active configuration",
            "fa-sliders",
        )


# --------------------------------------------------------------------------
def _persist(db: Database, values: Dict[str, Any]) -> None:
    warnings = save_settings(db, values)
    if warnings:
        for warning in warnings:
            st.warning(warning)
    else:
        st.success("Settings saved.")
    st.session_state["anatonix_settings_dirty"] = True
