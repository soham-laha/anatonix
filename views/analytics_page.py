"""Analytics — range filters, Chart.js visualisations and computed insights."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

import streamlit as st

import ui.kit as kit
from backend import analytics
from backend.database import Database, to_iso, utcnow
from components.charts_component import charts_component
from views.common import active_session, latest_session


RANGE_OPTIONS = {
    "Current session": "session",
    "Last 7 days": "7d",
    "Last 30 days": "30d",
    "Custom range": "custom",
    "All recorded data": "all",
}


def render(db: Database, settings: Dict[str, Any]) -> None:
    kit.page_header(
        "Analytics",
        "Session, weekly and monthly analysis computed from stored telemetry. Ranges without "
        "sufficient data are reported as insufficient rather than estimated.",
        meta=["Chart.js", "1 Hz samples", f"{db.telemetry_count():,} records stored"],
    )

    key, custom = _range_controls(db)
    since, until, label = _resolve_bounds(db, key, custom)
    session_ids = None
    if key == "session":
        session = active_session(db) or latest_session(db)
        if session is None:
            st.markdown(
                kit.empty_state(
                    "No session available",
                    "Record a session from Live Monitoring to analyse it here.",
                    icon="fa-chart-line",
                ),
                unsafe_allow_html=True,
            )
            return
        session_ids = [session["session_id"]]
        since = session["start_timestamp"]
        until = session.get("end_timestamp") or to_iso(utcnow())
        label = f"Session {session['session_id'][:10]}"

    analysis = analytics.analyse_range(
        db,
        since=since,
        until=until,
        lean_threshold=float(settings["forward_lean_threshold"]),
        tilt_threshold=float(settings["shoulder_tilt_threshold"]),
        blink_low=float(settings["blink_low_bpm"]),
        blink_reference=float(settings["blink_reference_bpm"]),
    )
    if session_ids:
        frame = analytics.telemetry_frame(db, session_ids=session_ids)
        analysis["frame"] = frame
        metrics = analytics.summarise_metrics(
            frame,
            float(settings["forward_lean_threshold"]),
            float(settings["shoulder_tilt_threshold"]),
            float(settings["blink_low_bpm"]),
            float(settings["blink_reference_bpm"]),
        )
        alerts = analysis["alerts"]
        posture_alerts = alerts[alerts["alert_type"] == "posture_alert"] if not alerts.empty else alerts
        metrics["posture_alert_count"] = int(len(posture_alerts))
        metrics["sessions"] = 1
        metrics["total_monitored_seconds"] = int(sum(s.get("duration_seconds") or 0 for s in analysis["sessions"]))
        analysis["metrics"] = metrics
        analysis["status_distribution"] = analytics.status_distribution(metrics)
        analysis["daily"] = analytics.daily_consistency(frame)

    metrics = analysis["metrics"]
    _insight_row(metrics, settings, label)

    if not metrics["samples"]:
        st.markdown(
            kit.empty_state(
                "No telemetry in this range",
                "Charts populate as soon as a monitoring session records samples inside the "
                "selected window.",
                icon="fa-chart-simple",
            ),
            unsafe_allow_html=True,
        )
        return

    payload = analytics.chart_payload(
        analysis,
        lean_threshold=float(settings["forward_lean_threshold"]),
        tilt_threshold=float(settings["shoulder_tilt_threshold"]),
        blink_low=float(settings["blink_low_bpm"]),
        blink_reference=float(settings["blink_reference_bpm"]),
        animate=bool(settings.get("chart_animations", True)),
    )
    charts_component(payload, key=f"charts_{key}", theme=settings.get("theme", "dark"))

    _comparison_section(db, key, settings)
    _composite_explanation(metrics)
    _threshold_section(db, since, until, settings)

    kit.disclaimer_footer(
        "Blinks alone cannot diagnose dry-eye disease; posture angles are geometric estimates "
        "from a webcam, not clinical measurements."
    )


# --------------------------------------------------------------------------
def _range_controls(db: Database) -> Tuple[str, Optional[Tuple[date, date]]]:
    labels = list(RANGE_OPTIONS.keys())
    cols = st.columns([2.2, 1.6, 1, 1], gap="small")
    with cols[0]:
        selection = st.radio(
            "Time range",
            labels,
            index=1,
            horizontal=True,
            label_visibility="collapsed",
            key="analytics_range",
        )
    custom = None
    if RANGE_OPTIONS[selection] == "custom":
        with cols[1]:
            picked = st.date_input(
                "Custom range",
                value=(date.today() - timedelta(days=14), date.today()),
                label_visibility="collapsed",
                key="analytics_custom",
            )
        if isinstance(picked, tuple) and len(picked) == 2:
            custom = picked
    with cols[3]:
        if st.button("Refresh data", width="stretch", key="analytics_refresh"):
            st.rerun()
    return RANGE_OPTIONS[selection], custom


def _resolve_bounds(db: Database, key: str, custom) -> Tuple[Optional[str], Optional[str], str]:
    if custom:
        since = to_iso(custom[0].isoformat() + "T00:00:00+00:00")
        until = to_iso(custom[1].isoformat() + "T23:59:59+00:00")
        return since, until, "Custom range"
    return analytics.range_bounds(key)


def _insight_row(metrics: Dict[str, Any], settings: Dict[str, Any], label: str) -> None:
    cols = st.columns(6, gap="small")
    lean = metrics.get("avg_forward_lean")
    tilt = metrics.get("avg_shoulder_tilt")
    bpm = metrics.get("avg_blink_rate")
    tracking = metrics.get("tracking_pct")
    outside = metrics.get("pct_time_outside_threshold")

    with cols[0]:
        st.markdown(
            kit.metric_card(
                "Average forward lean",
                kit.fmt_number(lean, 1),
                "°",
                hint=f"limit {settings['forward_lean_threshold']:.0f}°",
                tone=kit.tone_for(lean, settings["forward_lean_threshold"], settings["forward_lean_threshold"] * 1.6),
                icon="fa-angles-down",
            ),
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.markdown(
            kit.metric_card(
                "Average shoulder tilt",
                kit.fmt_number(tilt, 1),
                "°",
                hint=f"limit {settings['shoulder_tilt_threshold']:.0f}°",
                tone=kit.tone_for(tilt, settings["shoulder_tilt_threshold"], settings["shoulder_tilt_threshold"] * 1.8),
                icon="fa-arrows-left-right",
            ),
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(
            kit.metric_card(
                "Valid tracking time",
                kit.fmt_number(tracking, 1),
                "%",
                hint=f"{metrics.get('valid_samples', 0):,} valid samples",
                tone=kit.tone_for(tracking, 70, 40, invert=True) if tracking is not None else "",
                icon="fa-signal",
            ),
            unsafe_allow_html=True,
        )
    with cols[3]:
        st.markdown(
            kit.metric_card(
                "Beyond thresholds",
                kit.fmt_number(outside, 1),
                "%",
                hint=f"{kit.duration(metrics.get('seconds_outside_threshold'))} total",
                tone=kit.tone_for(outside, 20, 40) if outside is not None else "",
                icon="fa-triangle-exclamation",
            ),
            unsafe_allow_html=True,
        )
    with cols[4]:
        st.markdown(
            kit.metric_card(
                "Average blink rate",
                kit.fmt_number(bpm, 1),
                "bpm",
                hint=metrics.get("blink_category_label", "No data"),
                icon="fa-eye",
            ),
            unsafe_allow_html=True,
        )
    with cols[5]:
        score = metrics.get("composite_score")
        st.markdown(
            kit.metric_card(
                "Ergonomic composite",
                f"{score:.0f}" if score is not None else "—",
                "/100",
                hint="experimental · see notes" if score is not None else "needs 30+ samples",
                tone="accent" if score is not None else "",
                icon="fa-gauge-simple-high",
            ),
            unsafe_allow_html=True,
        )


def _comparison_section(db: Database, key: str, settings: Dict[str, Any]) -> None:
    if key not in {"7d", "30d", "all"}:
        return
    days = 7 if key == "7d" else 30
    now = utcnow()
    current_since = to_iso(now - timedelta(days=days))
    previous_since = to_iso(now - timedelta(days=days * 2))
    previous_until = to_iso(now - timedelta(days=days))
    comparison = analytics.compare_windows(
        db,
        current_since,
        to_iso(now),
        previous_since,
        previous_until,
        lean_threshold=float(settings["forward_lean_threshold"]),
        tilt_threshold=float(settings["shoulder_tilt_threshold"]),
        blink_low=float(settings["blink_low_bpm"]),
        blink_reference=float(settings["blink_reference_bpm"]),
    )
    deltas = comparison["deltas"]
    available = comparison["available"]

    def render_delta(metric: str, label: str, unit: str, invert: bool) -> str:
        value = deltas.get(metric)
        if value is None or not available.get(metric):
            return kit.metric_card(label, "—", unit, hint="insufficient data to compare")
        tone = "good" if (value < 0) == invert else ("warn" if value else "")
        arrow = "▲" if value > 0 else "▼" if value < 0 else "■"
        return kit.metric_card(
            label,
            f"{arrow} {abs(value):.1f}",
            unit,
            hint=f"vs previous {days} days",
            tone=tone,
        )

    st.markdown(
        '<div class="anx-card-title" style="margin:18px 0 8px">'
        '<i class="fa-solid fa-code-compare"></i>Comparison with the previous period</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(4, gap="small")
    with cols[0]:
        st.markdown(render_delta("avg_forward_lean", "Forward lean delta", "°", invert=True),
                    unsafe_allow_html=True)
    with cols[1]:
        st.markdown(render_delta("avg_shoulder_tilt", "Shoulder tilt delta", "°", invert=True),
                    unsafe_allow_html=True)
    with cols[2]:
        st.markdown(render_delta("avg_blink_rate", "Blink rate delta", "bpm", invert=False),
                    unsafe_allow_html=True)
    with cols[3]:
        st.markdown(render_delta("composite_score", "Composite score delta", "", invert=False),
                    unsafe_allow_html=True)


def _composite_explanation(metrics: Dict[str, Any]) -> None:
    score = metrics.get("composite_score")
    with st.expander("How the ergonomic composite score is calculated", expanded=False):
        st.markdown(
            f"""
The composite is an **optional, experimental application metric** (currently
**{f"{score:.0f}/100" if score is not None else "unavailable"}** for this range). It is not a
clinical health score and has no diagnostic meaning. It is produced only when the range contains
at least 30 valid samples.

| Component | Weight | Formula |
|---|---|---|
| Posture | 35% | `100 − 2 × average forward lean (°)` |
| Symmetry | 20% | `100 − 6 × average shoulder tilt (°)` |
| Threshold time | 25% | `100 − % of tracked time beyond your thresholds` |
| Ocular | 20% | `100 × min(average blink rate ÷ 12, 1)` |

Each component is clamped to 0–100, then weighted and summed. Components are shown above so you
can see exactly which part of your routine drives the number.
            """
        )


def _threshold_section(db: Database, since: Optional[str], until: Optional[str],
                       settings: Dict[str, Any]) -> None:
    frame = analytics.telemetry_frame(db, since=since, until=until)
    if frame.empty:
        return
    lean_thr = float(settings["forward_lean_threshold"])
    tilt_thr = float(settings["shoulder_tilt_threshold"])
    lean_series = frame["forward_lean"].dropna()
    tilt_series = frame["shoulder_tilt"].dropna()
    if lean_series.empty and tilt_series.empty:
        return

    rows = []
    if not lean_series.empty:
        rows.append(
            (
                "Samples beyond the lean limit",
                f"{int((lean_series > lean_thr).sum()):,} of {len(lean_series):,}",
                "warn",
            )
        )
        rows.append(("Maximum forward lean", kit.fmt_number(lean_series.max(), 1, "°"), ""))
        rows.append(("95th percentile lean", kit.fmt_number(lean_series.quantile(0.95), 1, "°"), ""))
    if not tilt_series.empty:
        rows.append(
            (
                "Samples beyond the tilt limit",
                f"{int((tilt_series > tilt_thr).sum()):,} of {len(tilt_series):,}",
                "warn",
            )
        )
        rows.append(("Maximum shoulder tilt", kit.fmt_number(tilt_series.max(), 1, "°"), ""))
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    kit.render_card(
        kit.kv_rows(rows),
        "Threshold exposure",
        "fa-ruler-horizontal",
        note="computed from stored samples",
    )
