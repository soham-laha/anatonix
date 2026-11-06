"""Dashboard — overview of the current/last session, summaries and activity."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import streamlit as st

import ui.kit as kit
from backend.database import Database
from backend.posture_math import neck_load_estimate
from views.common import (
    active_session,
    camera_status,
    latest_session,
    session_duration_seconds,
    session_metrics,
    system_status,
)

ICONS = {
    "status": "fa-satellite-dish",
    "duration": "fa-stopwatch",
    "stability": "fa-arrows-to-dot",
    "blink": "fa-eye",
    "alerts": "fa-bell",
    "activity": "fa-wave-square",
}


def render(db: Database, settings: Dict[str, Any]) -> None:
    status = system_status(db)
    camera = camera_status()
    session = active_session(db) or latest_session(db)
    display_settings = settings

    kit.page_header(
        "Dashboard",
        "Live overview of biomechanical and ocular monitoring. Every figure below is "
        "calculated from telemetry stored in your local database.",
        meta=[
            kit.local_time(datetime.now().astimezone(), with_seconds=False),
            camera["label"],
            f"{settings['forward_lean_threshold']:.0f}° lean limit",
            f"{settings['shoulder_tilt_threshold']:.0f}° tilt limit",
        ],
    )

    metrics = session_metrics(db, session["session_id"], display_settings) if session else None
    duration = session_duration_seconds(db, session) if session else None

    # ------------------------------------------------------------ overview
    cards = st.columns(6, gap="small")
    with cards[0]:
        st.markdown(
            kit.metric_card(
                "Monitoring status",
                "Active" if status["tone"] == "live" else "Idle",
                hint=status["detail"],
                tone="good" if status["tone"] == "live" else "",
                icon=ICONS["status"],
            ),
            unsafe_allow_html=True,
        )
    with cards[1]:
        st.markdown(
            kit.metric_card(
                "Session duration",
                kit.duration(duration) if duration is not None else "—",
                hint="Active session" if status["tone"] == "live" else "Last session",
                tone="accent" if duration else "",
                icon=ICONS["duration"],
            ),
            unsafe_allow_html=True,
        )
    with cards[2]:
        if metrics and metrics["valid_samples"] >= 30:
            pct = metrics["pct_time_outside_threshold"] or 0.0
            stability = max(0.0, 100.0 - pct)
            st.markdown(
                kit.metric_card(
                    "Posture stability",
                    f"{stability:.0f}",
                    "%",
                    hint=f"{pct:.0f}% of tracked time beyond limits",
                    tone="good" if stability >= 80 else "warn" if stability >= 55 else "bad",
                    icon=ICONS["stability"],
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                kit.metric_card(
                    "Posture stability",
                    "—",
                    hint="Needs 30+ tracked samples",
                    icon=ICONS["stability"],
                ),
                unsafe_allow_html=True,
            )
    with cards[3]:
        bpm = metrics["avg_blink_rate"] if metrics else None
        st.markdown(
            kit.metric_card(
                "Blink frequency",
                f"{bpm:.1f}" if bpm is not None else "—",
                "bpm",
                hint=metrics["blink_category_label"] if metrics else "No ocular data",
                tone="accent" if bpm is not None else "",
                icon=ICONS["blink"],
            ),
            unsafe_allow_html=True,
        )
    with cards[4]:
        alerts = metrics["posture_alert_count"] if metrics else 0
        st.markdown(
            kit.metric_card(
                "Posture alerts",
                str(alerts or 0),
                hint="In the selected session",
                tone="warn" if alerts else ("good" if metrics and metrics["samples"] else ""),
                icon=ICONS["alerts"],
            ),
            unsafe_allow_html=True,
        )
    with cards[5]:
        events = db.fetch_events(6)
        last = events[0] if events else None
        st.markdown(
            kit.metric_card(
                "Recent activity",
                kit.local_clock(last["timestamp"]) if last else "—",
                hint=(last["message"] if last else "No events recorded yet"),
                icon=ICONS["activity"],
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    # ------------------------------------------------------- main columns
    left, right = st.columns([1.45, 1], gap="medium")

    with left:
        _session_summary_card(db, session, metrics, duration, settings)
        _activity_card(db)

    with right:
        _live_snapshot_card(metrics, session, camera)
        _insight_card(metrics, session, settings)

    kit.disclaimer_footer(
        "Neck-load values are illustrative, proximity is relative, and blink categories are "
        "configurable monitoring bands rather than diagnostic thresholds."
    )


# --------------------------------------------------------------------------
def _session_summary_card(db, session, metrics, duration, settings) -> None:
    if not session:
        body = kit.empty_state(
            "No monitoring sessions yet",
            "Open Live Monitoring, initialise the camera and Anatonix will start recording "
            "1 Hz telemetry into your local database.",
            icon="fa-camera-retro",
        )
        kit.render_card(body, "Session summary", "fa-clipboard-list", note="no data")
        return

    metrics = metrics or {}
    lean = metrics.get("avg_forward_lean")
    tilt = metrics.get("avg_shoulder_tilt")
    tracking = metrics.get("tracking_pct")
    rows = [
        ("Average forward lean", kit.fmt_number(lean, 1, "°"), kit.tone_for(lean, 12, 20)),
        ("Average shoulder tilt", kit.fmt_number(tilt, 1, "°"), kit.tone_for(tilt, 6, 10)),
        ("Average blink rate", kit.fmt_number(metrics.get("avg_blink_rate"), 1, " bpm"), ""),
        ("Total posture alerts", str(metrics.get("posture_alert_count", 0)), ""),
        ("Monitoring duration", kit.duration(duration), ""),
        (
            "Tracking availability",
            kit.fmt_number(tracking, 1, "%"),
            kit.tone_for(tracking, 70, 40, invert=True) if tracking is not None else "",
        ),
        ("Telemetry samples", f"{metrics.get('samples', 0):,}", ""),
        ("Neck-load estimate", f"{neck_load_estimate(lean) if lean is not None else '—'} lbs", ""),
    ]
    body = kit.kv_rows(rows)
    body += (
        '<p style="margin-top:10px;font-size:11px;color:var(--anx-dim)">'
        "Neck-load figures are an illustrative educational mapping, not a measurement of "
        "spinal force.</p>"
    )
    note = kit.local_time(session["start_timestamp"], with_seconds=False)
    kit.render_card(
        body,
        "Session summary",
        "fa-clipboard-list",
        note=f"{'live' if session.get('end_timestamp') is None else 'completed'} · {note}",
    )


def _activity_card(db) -> None:
    events = db.fetch_events(9)
    kit.render_card(
        kit.activity_feed(events),
        "Recent monitoring activity",
        "fa-timeline",
        note=f"{len(events)} events",
    )


def _live_snapshot_card(metrics, session, camera) -> None:
    live = st.session_state.get("anatonix_live") or {}
    if not live:
        body = kit.empty_state(
            "Live snapshot unavailable",
            "Open the Live Monitoring page — the current metrics appear here once the "
            "camera is running.",
            icon="fa-signal",
        )
        kit.render_card(body, "Live snapshot", "fa-wave-square", note="idle")
        return

    rows = [
        ("Tracking state", str(live.get("state", "—")), ""),
        ("Forward lean", kit.fmt_number(live.get("forward_lean"), 1, "°"), ""),
        ("Shoulder tilt", kit.fmt_number(live.get("shoulder_tilt"), 1, "°"), ""),
        ("Eye aspect ratio", kit.fmt_number(live.get("ear_value"), 3), ""),
        ("Blink rate", kit.fmt_number(live.get("blink_rate"), 1, " bpm"), ""),
        ("Blinks counted", str(live.get("blinks_total", "—")), ""),
        ("Inference rate", kit.fmt_number(live.get("fps"), 0, " fps"), ""),
        ("Landmark confidence", kit.fmt_number(
            (live.get("confidence") or 0) * 100 if live.get("confidence") is not None else None, 0, "%"
        ), ""),
    ]
    issues = live.get("quality_issues") or []
    body = kit.kv_rows(rows)
    if issues:
        body += (
            '<div style="margin-top:10px;padding:8px 10px;border-radius:8px;'
            'border:1px solid color-mix(in srgb, var(--anx-warning) 45%, transparent);'
            'background:color-mix(in srgb, var(--anx-warning) 10%, var(--anx-card));'
            'font-size:11.5px;color:var(--anx-warning)">'
            f"Camera hint: {kit.esc(' · '.join(str(i).replace('_', ' ') for i in issues))}</div>"
        )
    kit.render_card(body, "Live snapshot", "fa-wave-square", note=camera["label"])


def _insight_card(metrics, session, settings) -> None:
    if not metrics or not metrics.get("samples"):
        kit.render_card(
            kit.empty_state(
                "No insights yet",
                "Insights appear once a session has recorded measurable telemetry.",
                icon="fa-lightbulb",
            ),
            "Insights",
            "fa-lightbulb",
        )
        return

    lines = []
    lean = metrics.get("avg_forward_lean")
    tilt = metrics.get("avg_shoulder_tilt")
    pct_outside = metrics.get("pct_time_outside_threshold")
    if lean is not None:
        if lean > settings["forward_lean_threshold"]:
            lines.append(
                f"Average forward lean ({lean:.1f}°) is above your "
                f"{settings['forward_lean_threshold']:.0f}° limit — try raising the screen or "
                "easing the chair back."
            )
        else:
            lines.append(
                f"Average forward lean ({lean:.1f}°) stayed inside your "
                f"{settings['forward_lean_threshold']:.0f}° limit."
            )
    if tilt is not None and tilt > settings["shoulder_tilt_threshold"]:
        lines.append(
            f"Shoulder tilt averaged {tilt:.1f}° and crossed your "
            f"{settings['shoulder_tilt_threshold']:.0f}° limit — check chair height and arm rests."
        )
    if pct_outside is not None:
        lines.append(f"{pct_outside:.0f}% of tracked time was beyond your configured thresholds.")
    if metrics.get("blink_category") in {"low", "reduced"}:
        lines.append(
            f"Blink rate averaged {metrics['avg_blink_rate']:.1f} bpm — "
            f"{metrics['blink_category_label'].lower()}. Consider a short screen break."
        )
    score = metrics.get("composite_score")
    if score is not None:
        lines.append(
            f"Ergonomic composite score {score:.0f}/100 (experimental application metric — "
            "not a clinical health score)."
        )
    if not lines:
        lines.append("Tracking is available but no threshold excursions were recorded.")

    body = "".join(
        f'<div style="display:flex;gap:9px;padding:9px 0;'
        f'border-bottom:1px solid var(--anx-border);font-size:12.5px;color:var(--anx-muted)">'
        f'<i class="fa-solid fa-angle-right" style="color:var(--anx-accent);margin-top:3px"></i>'
        f"<span>{kit.esc(line)}</span></div>"
        for line in lines
    )
    kit.render_card(body, "Insights", "fa-lightbulb", note="derived from stored telemetry")
