"""Reports — session history, CSV export and printable PDF reports."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

import ui.kit as kit
from backend import analytics
from backend.database import Database
from backend.report_generator import cleanup_exports, export_csv, export_range_csv, build_pdf
from backend.session_manager import SessionManager
from views.common import session_metrics


def render(db: Database, settings: Dict[str, Any]) -> None:
    sessions = db.list_sessions(limit=200)
    kit.page_header(
        "Reports",
        "Session history with downloadable CSV telemetry and printable PDF reports. "
        "Reports contain derived numeric measurements and never contain imagery.",
        meta=[f"{len(sessions)} sessions stored", "CSV + PDF export", "local files only"],
    )

    if not sessions:
        st.markdown(
            kit.empty_state(
                "No sessions recorded",
                "Once you record a session it appears here with export controls.",
                icon="fa-file-lines",
            ),
            unsafe_allow_html=True,
        )
        _export_all(db)
        return

    history = analytics.session_history(db, limit=200)
    _history_table(history)
    _session_detail(db, sessions, settings)
    _export_all(db)
    _danger_zone(db)

    kit.disclaimer_footer(
        "Exported reports contain the stored numeric estimates only — no camera frames, no "
        "facial images and no biometric templates."
    )


# --------------------------------------------------------------------------
def _history_table(history) -> None:
    frame = pd.DataFrame(history)
    if frame.empty:
        return
    # pandas represents SQL NULLs as NaN; convert so the formatters report "—".
    frame = frame.astype(object).where(pd.notnull(frame), None)

    def number(value, digits):
        return "—" if value is None else f"{float(value):.{digits}f}"

    rows = [
        (
            str(row["session_id"])[:14],
            kit.local_time(row["start"], with_seconds=False),
            kit.duration(row["duration_seconds"]),
            row["records"],
            number(row["avg_forward_lean"], 2),
            number(row["avg_shoulder_tilt"], 2),
            number(row["avg_blink_rate"], 1),
            None if row["tracking_pct"] is None else float(row["tracking_pct"]),
            row["posture_alerts"],
            "active" if row["end"] is None else "completed",
        )
        for row in frame.to_dict("records")
    ]
    kit.render_card(
        kit.table(
            [
                "Session",
                "Started",
                "Duration",
                "Samples",
                "Avg lean (°)",
                "Avg tilt (°)",
                "Blink rate (bpm)",
                "Tracking (%)",
                "Alerts",
                "State",
            ],
            rows,
            numeric=(3, 4, 5, 6, 8),
            bars={7: 100.0},
            max_height=min(420, 50 + 38 * len(rows)),
        ),
        "Session history",
        "fa-clock-rotate-left",
        note=f"{len(rows)} sessions",
    )


def _session_detail(db: Database, sessions, settings: Dict[str, Any]) -> None:
    options = {
        f"{kit.local_time(s['start_timestamp'], with_seconds=False)} · {s['session_id'][:14]}"
        + ("" if s.get("end_timestamp") else "  (active)")
        : s["session_id"]
        for s in sessions
    }
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    kit.render_card("", "Generate a report", "fa-file-arrow-down", note="select a session")
    left, mid, right = st.columns([2.2, 1, 1.2], gap="small")
    with left:
        choice = st.selectbox(
            "Session",
            list(options.keys()),
            key="report_session_choice",
            label_visibility="collapsed",
        )
    session_id = options[choice]
    metrics = session_metrics(db, session_id, settings)

    with mid:
        csv_clicked = st.button("Prepare CSV", width="stretch", key="report_prepare_csv")
    with right:
        pdf_clicked = st.button("Prepare PDF", width="stretch", key="report_prepare_pdf")

    summary_cols = st.columns(4, gap="small")
    with summary_cols[0]:
        st.markdown(
            kit.metric_card(
                "Samples",
                f"{metrics.get('samples', 0):,}",
                hint="1 Hz telemetry rows",
                icon="fa-database",
            ),
            unsafe_allow_html=True,
        )
    with summary_cols[1]:
        st.markdown(
            kit.metric_card(
                "Avg lean",
                kit.fmt_number(metrics.get("avg_forward_lean"), 1),
                "°",
                icon="fa-angles-down",
            ),
            unsafe_allow_html=True,
        )
    with summary_cols[2]:
        st.markdown(
            kit.metric_card(
                "Blink rate",
                kit.fmt_number(metrics.get("avg_blink_rate"), 1),
                "bpm",
                hint=metrics.get("blink_category_label", ""),
                icon="fa-eye",
            ),
            unsafe_allow_html=True,
        )
    with summary_cols[3]:
        st.markdown(
            kit.metric_card(
                "Alerts",
                str(metrics.get("posture_alert_count", 0)),
                hint="posture alerts",
                icon="fa-bell",
            ),
            unsafe_allow_html=True,
        )

    manager = SessionManager(db)

    if csv_clicked:
        if not metrics.get("samples"):
            st.warning("This session has no stored telemetry yet — start monitoring first.")
        else:
            path = export_csv(db, session_id)
            manager.record_report(session_id, "csv", str(path))
            st.session_state["report_csv"] = str(path)

    if pdf_clicked:
        try:
            path = build_pdf(db, session_id, settings=settings)
            manager.record_report(session_id, "pdf", str(path))
            st.session_state["report_pdf"] = str(path)
        except Exception as exc:  # pragma: no cover - surfaced to the user
            st.error(f"PDF generation failed: {exc}")

    downloads = st.columns(2, gap="small")
    csv_path = st.session_state.get("report_csv")
    pdf_path = st.session_state.get("report_pdf")
    with downloads[0]:
        if csv_path:
            try:
                data = open(csv_path, "rb").read()
                st.download_button(
                    "Download CSV",
                    data,
                    file_name=csv_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                    mime="text/csv",
                    width="stretch",
                    key="download_csv",
                )
            except OSError:
                st.info("The prepared CSV file is no longer available — prepare it again.")
        else:
            st.button("Download CSV", disabled=True, width="stretch", key="download_csv_disabled")
    with downloads[1]:
        if pdf_path:
            try:
                data = open(pdf_path, "rb").read()
                st.download_button(
                    "Download PDF",
                    data,
                    file_name=pdf_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                    mime="application/pdf",
                    width="stretch",
                    key="download_pdf",
                )
            except OSError:
                st.info("The prepared PDF file is no longer available — prepare it again.")
        else:
            st.button("Download PDF", disabled=True, width="stretch", key="download_pdf_disabled")


def _export_all(db: Database) -> None:
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    kit.render_card("", "Range export", "fa-file-csv", note="all stored telemetry in range")
    cols = st.columns([1.4, 1, 1], gap="small")
    with cols[0]:
        days = st.selectbox(
            "Range",
            ["Last 7 days", "Last 30 days", "All recorded data"],
            key="range_export_choice",
            label_visibility="collapsed",
        )
    with cols[1]:
        clicked = st.button("Prepare range CSV", width="stretch", key="prepare_range_csv")
    with cols[2]:
        if clicked:
            from datetime import timedelta

            from backend.database import to_iso, utcnow

            until = to_iso(utcnow())
            since = None
            if days == "Last 7 days":
                since = to_iso(utcnow() - timedelta(days=7))
            elif days == "Last 30 days":
                since = to_iso(utcnow() - timedelta(days=30))
            path = export_range_csv(db, since, until)
            st.session_state["range_csv"] = str(path)
            st.success("Range export prepared.")

    if st.session_state.get("range_csv"):
        path = st.session_state["range_csv"]
        try:
            data = open(path, "rb").read()
            st.download_button(
                "Download range CSV",
                data,
                file_name=path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                mime="text/csv",
                key="download_range_csv",
            )
        except OSError:
            st.info("The prepared file is no longer available.")


def _danger_zone(db: Database) -> None:
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    kit.render_card("", "Data management", "fa-broom", note="deletes are permanent")
    cleanup_exports()
    stats = db.storage_stats()
    st.markdown(
        kit.kv_rows(
            [
                ("Database", stats["db_path"], "dim"),
                ("Size", f"{stats['db_size_bytes'] / 1024:.0f} KB", ""),
                ("Telemetry rows", f"{stats['telemetry_records']:,}", ""),
                ("Alerts", f"{stats['alerts']:,}", ""),
                ("Calibrations", f"{stats['calibrations']:,}", ""),
            ]
        ),
        unsafe_allow_html=True,
    )
    with st.expander("Delete sessions"):
        sessions = db.list_sessions(limit=100)
        labels = {
            f"{kit.local_time(s['start_timestamp'], with_seconds=False)} · {s['session_id'][:14]}": s["session_id"]
            for s in sessions
        }
        if not labels:
            st.info("There are no sessions to delete.")
            return
        chosen = st.multiselect("Sessions to delete", list(labels.keys()), key="delete_sessions")
        confirm = st.checkbox("I understand this permanently removes the selected telemetry.",
                              key="confirm_delete_sessions")
        if st.button("Delete selected sessions", disabled=not (chosen and confirm), key="do_delete"):
            removed = db.delete_sessions([labels[c] for c in chosen])
            st.success(f"Deleted {removed} session(s).")
            st.rerun()
