"""Live Monitoring — the real-time computer-vision view."""

from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit as st

import ui.kit as kit
from backend.analytics import telemetry_frame
from backend.database import Database
from components.monitoring_component import monitoring_component
from views.common import (
    active_session,
    calibration_payload,
    camera_status,
    process_component_payload,
    session_metrics,
    vision_config,
)


def render(db: Database, settings: Dict[str, Any]) -> None:
    camera = camera_status()
    session = active_session(db)
    calibration = db.latest_calibration()

    kit.page_header(
        "Live Monitoring",
        "Real-time biomechanical and ocular tracking. All inference happens in your browser; "
        "only numeric telemetry reaches this application.",
        meta=[
            camera["label"],
            f"{settings['inference_fps']} fps inference cap",
            f"{settings['sample_interval_ms']} ms sampling",
            "1 Hz telemetry",
        ],
    )

    # ------------------------------------------------------------- notices
    if not calibration:
        st.markdown(
            kit.banner(
                "<b>No baseline calibration yet.</b> Posture angles are reported as raw "
                "values until you calibrate. Press <code>C</code> or use the Calibrate button "
                "while sitting in your reference posture.",
                tone="warn",
                icon="fa-crosshairs",
                allow_html=True,
            ),
            unsafe_allow_html=True,
        )

    # ----------------------------------------------------------- component
    payload = monitoring_component(
        settings=settings,
        calibration=calibration_payload(db),
        vision=vision_config(),
        foot_note=(
            f"Forward lean limit {settings['forward_lean_threshold']:.0f}° · "
            f"tilt limit {settings['shoulder_tilt_threshold']:.0f}° · "
            f"sample {settings['sample_interval_ms']} ms"
        ),
        key="anatonix_monitoring",
    )
    result = process_component_payload(db, payload)

    if result and result.errors:
        for error in result.errors:
            st.markdown(
                kit.banner(f"Telemetry issue: {error}", tone="bad", icon="fa-triangle-exclamation"),
                unsafe_allow_html=True,
            )

    # -------------------------------------------------------- session panel
    session_id = st.session_state.get("anatonix_session_id") or (session or {}).get("session_id")
    if session_id:
        _session_panel(db, session_id, settings)
    else:
        st.markdown(
            kit.empty_state(
                "Session not started",
                "Press “Initialize camera” in the panel above. Anatonix records 1 Hz telemetry "
                "into your local SQLite database while monitoring is active.",
                icon="fa-circle-play",
            ),
            unsafe_allow_html=True,
        )

    _how_it_works()


def _session_panel(db: Database, session_id: str, settings: Dict[str, Any]) -> None:
    metrics = session_metrics(db, session_id, settings)
    session = db.get_session(session_id) or {}
    frame = metrics.get("frame")
    samples = int(metrics.get("samples") or 0)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    cols = st.columns(5, gap="small")
    with cols[0]:
        st.markdown(
            kit.metric_card(
                "Samples stored",
                f"{samples:,}",
                hint="1 Hz valid telemetry",
                tone="accent" if samples else "",
                icon="fa-database",
            ),
            unsafe_allow_html=True,
        )
    with cols[1]:
        lean = metrics.get("avg_forward_lean")
        st.markdown(
            kit.metric_card(
                "Average lean",
                kit.fmt_number(lean, 1),
                "°",
                hint=f"limit {settings['forward_lean_threshold']:.0f}°",
                tone=kit.tone_for(lean, settings["forward_lean_threshold"], settings["forward_lean_threshold"] * 1.6),
                icon="fa-angles-down",
            ),
            unsafe_allow_html=True,
        )
    with cols[2]:
        tilt = metrics.get("avg_shoulder_tilt")
        st.markdown(
            kit.metric_card(
                "Average tilt",
                kit.fmt_number(tilt, 1),
                "°",
                hint=f"limit {settings['shoulder_tilt_threshold']:.0f}°",
                tone=kit.tone_for(tilt, settings["shoulder_tilt_threshold"], settings["shoulder_tilt_threshold"] * 1.8),
                icon="fa-arrows-left-right",
            ),
            unsafe_allow_html=True,
        )
    with cols[3]:
        st.markdown(
            kit.metric_card(
                "Blink rate",
                kit.fmt_number(metrics.get("avg_blink_rate"), 1),
                "bpm",
                hint=metrics.get("blink_category_label", "No data"),
                icon="fa-eye",
            ),
            unsafe_allow_html=True,
        )
    with cols[4]:
        st.markdown(
            kit.metric_card(
                "Posture alerts",
                str(metrics.get("posture_alert_count", 0)),
                hint="recorded this session",
                tone="warn" if metrics.get("posture_alert_count") else "",
                icon="fa-bell",
            ),
            unsafe_allow_html=True,
        )

    left, right = st.columns([1.4, 1], gap="medium")
    with left:
        kit.render_card(
            _recent_samples_table(frame),
            "Stored samples",
            "fa-table-list",
            note="most recent 8",
        )
    with right:
        alerts = metrics.get("alerts")
        body = ""
        if alerts is None or len(alerts) == 0:
            body = kit.empty_state(
                "No alerts yet", "Posture alerts and break reminders will be listed here.",
                icon="fa-shield-heart",
            )
        else:
            rows = []
            for _, alert in alerts.sort_values("timestamp", ascending=False).head(8).iterrows():
                label = str(alert.get("alert_type", "")).replace("_", " ").title()
                rows.append(
                    (
                        label,
                        f"{kit.local_clock(alert['timestamp'].isoformat())} · "
                        f"{kit.fmt_number(alert.get('measurement'), 1)}",
                        "warn",
                    )
                )
            body = kit.kv_rows(rows)
        kit.render_card(body, "Alerts this session", "fa-bell", note=f"{len(alerts) if alerts is not None else 0} events")

    _session_scope_note(session)


def _recent_samples_table(frame) -> str:
    if frame is None or len(frame) == 0:
        return kit.empty_state(
            "No samples stored yet",
            "Telemetry is written once per second while the camera is tracking.",
            icon="fa-database",
        )
    recent = frame.tail(8).iloc[::-1]
    rows = []
    for _, row in recent.iterrows():
        rows.append(
            (
                kit.local_clock(row["timestamp"].isoformat()),
                (
                    f"{kit.fmt_number(row['forward_lean'], 1, '°')} lean · "
                    f"{kit.fmt_number(row['shoulder_tilt'], 1, '°')} tilt · "
                    f"EAR {kit.fmt_number(row['ear_value'], 3)} · "
                    f"{kit.fmt_number(row['blink_rate'], 1, ' bpm')}"
                ),
                str(row.get("posture_status") or "untracked"),
            )
        )
    body = kit.kv_rows([(f"{r[0]}", r[1], "") for r in rows])
    return body


def _session_scope_note(session: Dict[str, Any]) -> None:
    if not session:
        return
    ended = session.get("end_timestamp")
    note = (
        f"Session <code>{kit.esc(session['session_id'])}</code> started "
        f"{kit.esc(kit.local_time(session['start_timestamp']))}"
        + (f" · ended {kit.esc(kit.local_time(ended))}" if ended else " · recording")
    )
    st.markdown(
        f'<div style="font-family:var(--anx-mono);font-size:10.5px;color:#64748b;'
        f'letter-spacing:.05em;margin-top:-4px">{note}</div>',
        unsafe_allow_html=True,
    )


def _how_it_works() -> None:
    with st.expander("How the monitoring pipeline works", expanded=False):
        st.markdown(
            """
**Pipeline.** Webcam → MediaPipe Pose + Face Landmarker (WASM/GPU, in-tab) → posture and
ocular geometry → 1 Hz batching → Streamlit component bridge → SQLite on this machine.

* **Forward lean** is the ear-over-shoulder pitch relative to your calibrated baseline.
* **Shoulder tilt** is the inclination of the shoulder line, normalised for camera orientation.
* **Proximity ratio** is the shoulder span relative to your calibration span — a *relative*
  distance indicator, not a measurement in centimetres.
* **Eye aspect ratio** uses six landmarks per eye; a blink is counted only after a debounced
  open→closed→open transition.
* **Telemetry** is written once per second and only when a measurement is available; missing
  values are stored as `NULL` rather than invented.

**Privacy.** Frames never leave the tab: no uploads, no stored images, no biometric templates.
Only numeric rows (angles, EAR, blink counts, confidence) reach the database.
            """
        )
