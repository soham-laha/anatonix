"""Small HTML/CSS component kit for the Streamlit shell.

Streamlit renders these strings with ``unsafe_allow_html=True``; every value is
escaped with :func:`html.escape` unless the caller explicitly passes pre-built
markup, so user-controlled text (session ids, camera labels, alert messages)
cannot inject HTML.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import streamlit as st

from config import APP_NAME, APP_VERSION, GENERAL_DISCLAIMER


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def card(
    body: str,
    title: Optional[str] = None,
    icon: Optional[str] = None,
    note: Optional[str] = None,
    *,
    raw: bool = True,
) -> str:
    """Wrap pre-built markup in the standard Anatonix card shell."""
    head = ""
    if title or note:
        head = (
            '<div class="anx-card-head">'
            f'<span class="anx-card-title"><i class="fa-solid {icon or "fa-circle"}"></i>{esc(title or "")}</span>'
            f'<span class="anx-card-note">{esc(note or "")}</span>'
            "</div>"
        )
    # A heading-only card is used as a section header (the Streamlit widgets it
    # introduces live outside the HTML block), so it is drawn as a header bar
    # rather than as an empty card.
    modifier = "" if (body or "").strip() else " anx-card--bar"
    return f'<div class="anx-card{modifier}">{head}{body}</div>'


def metric_card(
    label: str,
    value: str,
    unit: str = "",
    hint: str = "",
    tone: str = "",
    icon: str = "fa-chart-simple",
) -> str:
    tone_class = f" tone-{tone}" if tone else ""
    # A placeholder value means "no data"; showing a unit next to it would imply a measurement.
    unit_html = f'<span class="unit">{esc(unit)}</span>' if unit and value not in ("—", "") else ""
    hint_html = f'<div class="hint">{esc(hint)}</div>' if hint else ""
    return (
        f'<div class="anx-metric{tone_class}">'
        f'<div class="label"><i class="fa-solid {icon}"></i>{esc(label)}</div>'
        f'<div class="value">{esc(value)}{unit_html}</div>'
        f"{hint_html}</div>"
    )


def pill(label: str, tone: str = "idle") -> str:
    return f'<span class="anx-pill {tone}"><span class="dot"></span>{esc(label)}</span>'


def kv_rows(rows: Sequence[Sequence[Any]]) -> str:
    """rows: [(key, value, tone)] where tone is '', 'good', 'warn', 'bad', 'dim'."""
    out = ['<div class="anx-kv">']
    for row in rows:
        key = row[0]
        value = row[1]
        tone = row[2] if len(row) > 2 else ""
        value_html = value if isinstance(value, SafeHtml) else esc(value)
        tone_class = f" {tone}" if tone else ""
        out.append(f'<div class="row"><span class="k">{esc(key)}</span><span class="v{tone_class}">{value_html}</span></div>')
    out.append("</div>")
    return "".join(out)


class SafeHtml(str):
    """Marker type: text that is already escaped/sanitised markup."""


def bar(value: Optional[float], tone: str = "") -> str:
    if value is None:
        return '<div class="anx-bar"><span style="width:0%"></span></div>'
    pct = max(0.0, min(100.0, float(value)))
    return f'<div class="anx-bar"><span class="{tone}" style="width:{pct:.0f}%"></span></div>'


def empty_state(title: str, detail: str, icon: str = "fa-satellite-dish") -> str:
    return (
        '<div class="anx-empty">'
        f'<i class="fa-solid {icon}"></i>'
        f"<strong>{esc(title)}</strong>"
        f"<span>{esc(detail)}</span>"
        "</div>"
    )


def banner(message: str, tone: str = "info", icon: str = "fa-circle-info", allow_html: bool = False) -> str:
    payload = message if allow_html else esc(message)
    return f'<div class="anx-banner {tone}"><i class="fa-solid {icon}"></i><div>{payload}</div></div>'


def activity_feed(items: Iterable[Dict[str, Any]]) -> str:
    rows: List[str] = []
    icons = {
        "monitoring_started": ("fa-play", "good"),
        "monitoring_stopped": ("fa-stop", ""),
        "monitoring_paused": ("fa-pause", "warn"),
        "monitoring_resumed": ("fa-play", "good"),
        "calibration_completed": ("fa-crosshairs", "purple"),
        "calibration_reset": ("fa-rotate-left", "warn"),
        "posture_alert": ("fa-triangle-exclamation", "warn"),
        "break_reminder": ("fa-mug-hot", "warn"),
        "report_generated": ("fa-file-arrow-down", ""),
        "camera_error": ("fa-plug-circle-xmark", "bad"),
        "tracking_degraded": ("fa-signal", "warn"),
        "session_recovered": ("fa-clock-rotate-left", "warn"),
        "settings_changed": ("fa-sliders", ""),
    }
    for item in items:
        event_type = item.get("event_type", "")
        icon, tone = icons.get(event_type, ("fa-circle-dot", ""))
        rows.append(
            '<div class="row">'
            f'<div class="icon {tone}"><i class="fa-solid {icon}"></i></div>'
            '<div class="body">'
            f'<div class="title">{esc(item.get("message") or event_type.replace("_", " ").title())}</div>'
            f'<div class="sub">{esc(item.get("session_id") or "—")}</div>'
            "</div>"
            f'<div class="time">{esc(local_time(item.get("timestamp")))}</div>'
            "</div>"
        )
    if not rows:
        return empty_state(
            "No activity yet",
            "Start monitoring from the Live Monitoring page and session events will appear here.",
            icon="fa-clock",
        )
    return f'<div class="anx-activity">{"".join(rows)}</div>'


# --------------------------------------------------------------------------
# page furniture
# --------------------------------------------------------------------------
def page_header(title: str, subtitle: str, meta: Optional[Sequence[str]] = None) -> None:
    meta_parts = []
    for index, item in enumerate(meta or []):
        if index:
            meta_parts.append('<span class="sep">/</span>')
        meta_parts.append(f"<span>{esc(item)}</span>")
    meta_html = f'<div class="meta">{"".join(meta_parts)}</div>' if meta_parts else ""
    st.markdown(
        f'<div class="anx-page-head"><div class="titles"><h1>{esc(title)}</h1>'
        f"<p>{esc(subtitle)}</p></div>{meta_html}</div>",
        unsafe_allow_html=True,
    )


def render_card(
    body: str,
    title: Optional[str] = None,
    icon: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    st.markdown(card(body, title, icon, note), unsafe_allow_html=True)


def table(
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    numeric: Sequence[int] = (),
    bars: Optional[Dict[int, float]] = None,
    max_height: int = 420,
    empty: str = "No rows.",
) -> str:
    """Render a token-styled table.

    Streamlit's ``st.dataframe`` paints on a canvas with the server theme, which
    cannot follow the in-app light/dark switch; this helper keeps tables inside
    the same design system in both themes.

    ``numeric`` columns are right-aligned and set in the mono face, ``bars`` maps
    a column index to a 0-100 value rendered as a compact progress bar.
    """
    bars = bars or {}
    head = "".join(
        f'<th class="{"num" if index in numeric or index in bars else ""}">{esc(name)}</th>'
        for index, name in enumerate(columns)
    )
    body_rows = list(rows)
    if not body_rows:
        cells = "".join(f"<td>{esc(empty) if i == 0 else ''}</td>" for i in range(len(columns)))
        body = f'<tr class="is-empty">{cells}</tr>'
    else:
        chunks = []
        for row in body_rows:
            cells = []
            for index, value in enumerate(row):
                if index in bars:
                    # A missing value must not be drawn as a full bar — an empty
                    # track plus an em dash keeps "no data" visually distinct.
                    if value is None:
                        pct, label = 0.0, "—"
                    else:
                        pct = max(0.0, min(100.0, float(value)))
                        label = f"{pct:.1f}%"
                    cells.append(
                        '<td class="num bar-cell">'
                        f'<span class="anx-bar"><span style="width:{pct:.1f}%"></span></span>'
                        f"<em>{esc(label)}</em></td>"
                    )
                else:
                    cls = "num" if index in numeric else ""
                    cells.append(f'<td class="{cls}">{esc(value)}</td>')
            chunks.append("<tr>" + "".join(cells) + "</tr>")
        body = "".join(chunks)
    return (
        f'<div class="anx-table-wrap" style="max-height:{max_height}px">'
        f'<table class="anx-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
        "</div>"
    )


def section_label(text: str, note: str = "") -> None:
    st.markdown(
        f'<div class="anx-card-title" style="margin:16px 0 8px"><i class="fa-solid fa-minus"></i>{esc(text)}'
        f'<span class="anx-card-note" style="margin-left:10px">{esc(note)}</span></div>',
        unsafe_allow_html=True,
    )


def disclaimer_footer(extra: str = "") -> None:
    st.markdown(
        banner(
            f"<b>Limitations.</b> {GENERAL_DISCLAIMER} {extra}",
            tone="info",
            icon="fa-scale-balanced",
            allow_html=True,
        ),
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------
def local_time(value: Any, with_seconds: bool = True) -> str:
    parsed = _parse(value)
    if parsed is None:
        return "—"
    fmt = "%d %b %Y · %H:%M:%S" if with_seconds else "%d %b %Y · %H:%M"
    return parsed.astimezone().strftime(fmt)


def local_date(value: Any) -> str:
    parsed = _parse(value)
    if parsed is None:
        return "—"
    return parsed.astimezone().strftime("%d %b %Y")


def local_clock(value: Any) -> str:
    parsed = _parse(value)
    if parsed is None:
        return "—"
    return parsed.astimezone().strftime("%H:%M:%S")


def _parse(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return "—"
    total = int(round(value))
    if total < 60:
        return f"{total}s"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def fmt_number(
    value: Optional[float], digits: int = 1, unit: str = "", fallback: str = "—"
) -> str:
    if value is None:
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:  # NaN
        return fallback
    return f"{number:.{digits}f}{unit}"


def tone_for(value: Optional[float], warn: float, bad: float, invert: bool = False) -> str:
    if value is None:
        return "idle"
    if invert:
        if value <= bad:
            return "bad"
        if value <= warn:
            return "warn"
        return "good"
    if value >= bad:
        return "bad"
    if value >= warn:
        return "warn"
    return "good"


def app_footer() -> None:
    st.markdown(
        '<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;'
        'margin-top:22px;padding-top:12px;border-top:1px solid var(--anx-border);'
        'font-family:var(--anx-mono);font-size:10px;letter-spacing:0.08em;text-transform:uppercase;'
        'color:var(--anx-dim)">'
        f'<span><i class="fa-solid fa-shield-halved" style="color:var(--anx-positive)"></i> '
        "Local processing · no imagery stored · no biometric templates</span>"
        f"<span>{APP_NAME} v{APP_VERSION} · numeric telemetry only</span>"
        "</div>",
        unsafe_allow_html=True,
    )
