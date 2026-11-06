"""CSV and PDF report generation.

Reports are built from persisted rows only.  If a session has no telemetry the
generator still produces a valid document, but every metric is rendered as
"no data" rather than a placeholder number.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from backend import analytics
from backend.database import Database, parse_iso
from backend.logging_setup import get_logger
from config import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    EXPORT_DIR,
    GENERAL_DISCLAIMER,
    NECK_LOAD_DISCLAIMER,
)

log = get_logger("anatonix.reports")

CSV_COLUMNS = [
    ("timestamp", "timestamp"),
    ("forward_lean", "forward_lean_deg"),
    ("shoulder_tilt", "shoulder_tilt_deg"),
    ("ear_value", "ear_value"),
    ("blink_rate", "blink_rate_bpm"),
    ("proximity_ratio", "proximity_ratio"),
    ("tracking_confidence", "tracking_confidence"),
    ("posture_status", "posture_status"),
]


def _unique_path(path: Path) -> Path:
    """Never overwrite a previously exported report."""
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def _timestamp_slug(value: Optional[str]) -> str:
    parsed = parse_iso(value) or datetime.now(timezone.utc)
    return parsed.strftime("%Y%m%d-%H%M%S")


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "no data"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "no data"
    if pd.isna(number):
        return "no data"
    return f"{number:.{digits}f}{suffix}"


def _duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "no data"
    total = int(round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def build_csv(db: Database, session_id: str) -> bytes:
    frame = analytics.telemetry_frame(db, session_ids=[session_id])
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([header for _, header in CSV_COLUMNS])
    if frame.empty:
        return buffer.getvalue().encode("utf-8")

    for _, row in frame.iterrows():
        values: List[Any] = []
        for field, _header in CSV_COLUMNS:
            value = row.get(field)
            if field == "timestamp":
                values.append(pd.to_datetime(value, utc=True).isoformat())
            elif field == "posture_status":
                values.append(value if isinstance(value, str) else "untracked")
            else:
                values.append("" if value is None or pd.isna(value) else round(float(value), 4))
        writer.writerow(values)
    return buffer.getvalue().encode("utf-8")


def export_csv(db: Database, session_id: str, out_dir: Optional[Path] = None) -> Path:
    directory = Path(out_dir) if out_dir else EXPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    session = db.get_session(session_id) or {}
    filename = f"anatonix_telemetry_{_timestamp_slug(session.get('start_timestamp'))}_{session_id[:8]}.csv"
    path = _unique_path(directory / filename)
    path.write_bytes(build_csv(db, session_id))
    log.info("CSV written to %s", path)
    return path


def export_range_csv(
    db: Database,
    since: Optional[str],
    until: Optional[str],
    out_dir: Optional[Path] = None,
) -> Path:
    directory = Path(out_dir) if out_dir else EXPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    frame = analytics.telemetry_frame(db, since=since, until=until)
    path = _unique_path(
        directory / f"anatonix_telemetry_range_{_timestamp_slug(since)}_{_timestamp_slug(until)}.csv"
    )
    if frame.empty:
        path.write_text(",".join(header for _, header in CSV_COLUMNS) + "\n", encoding="utf-8")
    else:
        export = frame.copy()
        export["timestamp"] = export["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        export = export.rename(columns={key: header for key, header in CSV_COLUMNS})
        export[[header for _, header in CSV_COLUMNS]].to_csv(path, index=False)
    log.info("Range CSV written to %s", path)
    return path


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def _chart_image(frame: pd.DataFrame, path: Path) -> Optional[Path]:
    """Render a compact trend chart for the PDF (matplotlib, headless)."""
    if frame is None or frame.empty:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt

        figure, axes = plt.subplots(2, 1, figsize=(7.4, 3.6), dpi=170)
        figure.patch.set_facecolor("white")

        top = axes[0]
        top.plot(frame["timestamp"], frame["forward_lean"], color="#0E7490", linewidth=1.2,
                 label="Forward lean (\u00b0)")
        top.plot(frame["timestamp"], frame["shoulder_tilt"], color="#B45309", linewidth=1.0,
                 label="Shoulder tilt (\u00b0)")
        top.set_title("Posture metrics", fontsize=8, loc="left")
        top.legend(fontsize=6, frameon=False, ncol=2)
        top.tick_params(labelsize=6)

        bottom = axes[1]
        bottom.plot(frame["timestamp"], frame["ear_value"], color="#6D28D9", linewidth=1.0,
                    label="Eye aspect ratio")
        bottom.plot(frame["timestamp"], frame["blink_rate"], color="#047857", linewidth=1.0,
                    label="Blink rate (bpm)")
        bottom.set_title("Ocular metrics", fontsize=8, loc="left")
        bottom.legend(fontsize=6, frameon=False, ncol=2)
        bottom.tick_params(labelsize=6)

        span_seconds = (
            frame["timestamp"].iloc[-1] - frame["timestamp"].iloc[0]
        ).total_seconds() if len(frame) > 1 else 0
        for axis in axes:
            axis.grid(True, linewidth=0.3, alpha=0.4)
            axis.set_facecolor("#FAFAFA")
            if span_seconds and span_seconds < 600:
                # Short sessions need an explicit seconds interval; AutoDateLocator
                # cannot choose one for very narrow ranges.
                interval = 30 if span_seconds > 180 else 15 if span_seconds > 60 else 5
                axis.xaxis.set_major_locator(mdates.SecondLocator(interval=interval))
            else:
                axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
            for spine in ("top", "right"):
                axis.spines[spine].set_visible(False)

        figure.tight_layout()
        figure.savefig(path, format="png", facecolor="white")
        plt.close(figure)
        return path
    except Exception as exc:  # pragma: no cover - plotting failures must not kill the report
        log.warning("Trend chart rendering failed: %s", exc)
        return None


def build_pdf(
    db: Database,
    session_id: str,
    *,
    settings: Optional[Dict[str, Any]] = None,
    out_dir: Optional[Path] = None,
) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    settings = settings or db.get_settings()
    summary = analytics.session_summary(
        db,
        session_id,
        lean_threshold=float(settings["forward_lean_threshold"]),
        tilt_threshold=float(settings["shoulder_tilt_threshold"]),
        blink_low=float(settings["blink_low_bpm"]),
        blink_reference=float(settings["blink_reference_bpm"]),
    )
    session = summary["session"]
    metrics = summary["metrics"]
    frame = summary.get("frame")
    alerts = summary.get("alerts")

    directory = Path(out_dir) if out_dir else EXPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    start = (session or {}).get("start_timestamp")
    filename = f"anatonix_report_{_timestamp_slug(start)}_{session_id[:8]}.pdf"
    path = _unique_path(directory / filename)
    chart_path = _chart_image(frame if frame is not None else pd.DataFrame(), directory / f".chart_{session_id[:8]}.png")

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"{APP_NAME} session report {session_id}",
        author=APP_NAME,
    )

    styles = getSampleStyleSheet()
    accent = colors.HexColor("#0E7490")
    muted = colors.HexColor("#475569")
    heading = ParagraphStyle("ANIHeading", parent=styles["Heading2"], fontSize=12.5,
                             textColor=accent, spaceBefore=8, spaceAfter=4, fontName="Helvetica-Bold")
    body = ParagraphStyle("ANIBody", parent=styles["BodyText"], fontSize=8.6, leading=11.4,
                          textColor=colors.HexColor("#1F2937"))
    small = ParagraphStyle("ANISmall", parent=body, fontSize=7.4, leading=9.6, textColor=muted)
    title_style = ParagraphStyle("ANITitle", parent=styles["Title"], fontSize=20,
                                 textColor=colors.HexColor("#0F172A"), spaceAfter=2,
                                 fontName="Helvetica-Bold")

    story: List[Any] = []
    story.append(Paragraph(f"{APP_NAME}", title_style))
    story.append(Paragraph("Intelligent Biomechanical &amp; Ocular Monitoring &mdash; Session Report", small))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Version {APP_VERSION} &nbsp;|&nbsp; Generated {datetime.now().strftime('%d %b %Y, %H:%M')}", small))
    story.append(Spacer(1, 8))

    # --- session information ------------------------------------------------
    story.append(Paragraph("Session information", heading))
    info_rows = [
        ["Session ID", session_id],
        ["Start", _local(session.get("start_timestamp"))],
        ["End", _local(session.get("end_timestamp"))],
        ["Monitoring duration", _duration(metrics.get("session_duration_seconds"))],
        ["Telemetry samples", str(metrics.get("samples", 0))],
        ["Valid tracking time", _duration(metrics.get("valid_seconds"))],
        ["Calibration", str((session or {}).get("calibration_id") or "not calibrated")],
    ]
    story.append(_table(info_rows, accent, colors.HexColor("#F1F5F9")))

    # --- posture summary -----------------------------------------------------
    story.append(Paragraph("Posture summary", heading))
    posture_rows = [
        ["Average forward lean", _fmt(metrics.get("avg_forward_lean"), 1, "\u00b0")],
        ["Maximum forward lean", _fmt(metrics.get("max_forward_lean"), 1, "\u00b0")],
        ["Average shoulder tilt", _fmt(metrics.get("avg_shoulder_tilt"), 1, "\u00b0")],
        ["Maximum shoulder tilt", _fmt(metrics.get("max_shoulder_tilt"), 1, "\u00b0")],
        ["Time beyond thresholds", f"{_duration(metrics.get('seconds_outside_threshold'))} "
                                   f"({_fmt(metrics.get('pct_time_outside_threshold'), 1, '%')})"],
        ["Average proximity ratio", _fmt(metrics.get("avg_proximity"), 2)],
        ["Posture alerts", str(metrics.get("posture_alert_count", 0))],
    ]
    story.append(_table(posture_rows, accent, colors.HexColor("#F1F5F9")))

    # --- ocular summary ------------------------------------------------------
    story.append(Paragraph("Ocular monitoring summary", heading))
    ocular_rows = [
        ["Average eye aspect ratio (EAR)", _fmt(metrics.get("avg_ear"), 3)],
        ["Average blink rate", _fmt(metrics.get("avg_blink_rate"), 1, " bpm")],
        ["Blink rate indicator", str(metrics.get("blink_category_label"))],
        ["Tracking availability", _fmt(metrics.get("tracking_pct"), 1, "%")],
    ]
    story.append(_table(ocular_rows, accent, colors.HexColor("#F1F5F9")))

    # --- alerts --------------------------------------------------------------
    story.append(Paragraph("Alert history", heading))
    if alerts is None or len(alerts) == 0:
        story.append(Paragraph("No alerts were recorded during this session.", body))
    else:
        rows = [["Timestamp", "Type", "Measurement", "Threshold"]]
        for _, alert in alerts.sort_values("timestamp").iterrows():
            rows.append([
                _local(alert["timestamp"].isoformat() if hasattr(alert["timestamp"], "isoformat") else alert["timestamp"]),
                str(alert.get("alert_type", "")).replace("_", " ").title(),
                _fmt(alert.get("measurement"), 2),
                _fmt(alert.get("threshold"), 2),
            ])
        story.append(_table(rows, accent, colors.HexColor("#F1F5F9"), header=True, font_size=7.2))

    # --- chart ---------------------------------------------------------------
    if chart_path and Path(chart_path).exists():
        story.append(
            KeepTogether(
                [
                    Paragraph("Trend overview", heading),
                    Image(str(chart_path), width=170 * mm, height=78 * mm),
                    Paragraph(
                        "Lines are drawn from stored 1 Hz telemetry only; gaps indicate samples "
                        "where the required landmarks were not visible.",
                        small,
                    ),
                ]
            )
        )

    # --- limitations & privacy ----------------------------------------------
    story.append(Paragraph("Measurement limitations", heading))
    for text in (
        "Forward lean is a webcam-derived geometric proxy computed from ear and shoulder "
        "landmarks relative to the user's own calibration baseline. It is not a clinical "
        "cervical angle and cannot be compared between people or sessions with different "
        "camera setups.",
        "Shoulder tilt is the inclination of the shoulder line in the image plane and is "
        "affected by camera roll, seat rotation and clothing.",
        "Proximity ratio is a relative indicator derived from shoulder span. It does not "
        "measure distance in centimetres and is not a screen-distance measurement.",
        "Blink rate is computed from an eye-aspect-ratio heuristic. Blink frequency alone "
        "cannot diagnose dry-eye disease or any other condition.",
        NECK_LOAD_DISCLAIMER,
        GENERAL_DISCLAIMER,
    ):
        story.append(Paragraph(f"&bull; {text}", small))
        story.append(Spacer(1, 2))

    story.append(Paragraph("Privacy information", heading))
    story.append(Paragraph(
        "All computer vision processing ran locally in the browser on this computer. Camera "
        "frames were never transmitted, stored or analysed on a server, and no facial images, "
        "biometric templates or video were saved. Only numeric telemetry rows (angles, eye "
        "aspect ratio, blink counts, tracking confidence) were written to the local SQLite "
        "database. Exporting this report shares the derived numbers listed above, not imagery.",
        small))

    doc.build(story)

    if chart_path and Path(chart_path).exists():
        try:
            Path(chart_path).unlink()
        except OSError:  # pragma: no cover
            pass

    log.info("PDF written to %s", path)
    return path


def _table(rows: List[List[str]], accent, background, header: bool = False, font_size: float = 8.4):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    cell = ParagraphStyle(
        "cell", parent=styles["BodyText"], fontSize=font_size, leading=font_size + 2.4,
        textColor=colors.HexColor("#1F2937"),
    )
    data = [[Paragraph(str(value), cell) for value in row] for row in rows]
    table = Table(data, colWidths=[52 * mm, 118 * mm] if not header else None, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (0, -1), background),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), accent))
        style.append(("TEXTCOLOR", (0, 0), (-1, 0), colors.white))
    table.setStyle(TableStyle(style))
    return table


def _local(value: Any) -> str:
    """Render a stored UTC timestamp in the machine's local time zone."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, pd.Timestamp):
        parsed = value.to_pydatetime()
    else:
        parsed = parse_iso(str(value))
    if parsed is None:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%d %b %Y, %H:%M:%S")


def cleanup_exports(max_files: int = 60, out_dir: Optional[Path] = None) -> int:
    """Keep the exports directory bounded by removing the oldest files."""
    directory = Path(out_dir) if out_dir else EXPORT_DIR
    if not directory.exists():
        return 0
    files = sorted(
        (
            p
            for p in directory.iterdir()
            if p.is_file()
            and p.suffix.lower() in {".csv", ".pdf"}
            and p.name.startswith("anatonix_")
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for stale in files[max_files:]:
        try:
            stale.unlink()
            removed += 1
        except OSError:  # pragma: no cover
            continue
    return removed
