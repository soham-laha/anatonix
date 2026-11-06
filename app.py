"""Anatonix — Intelligent Biomechanical & Ocular Monitoring Platform.

Run with:
    python -m streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import streamlit as st

import ui.kit as kit
from backend.logging_setup import configure_logging, get_logger
from config import (
    FONTS,
    FONT_IMPORT_URL,
    ICON_IMPORT_URL,
    theme_css_variables,
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    BASE_DIR,
)
from views import analytics_page, dashboard, monitoring, reports, settings_page
from views.common import (
    camera_status,
    get_database,
    load_settings,
    system_status,
    version_footer_items,
)

configure_logging()
log = get_logger("anatonix.app")

st.set_page_config(
    page_title=f"{APP_NAME} | Intelligent Biomechanical & Ocular Monitoring",
    page_icon=str(BASE_DIR / "assets" / "icons" / "favicon.svg"),
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"about": f"{APP_NAME} v{APP_VERSION} — local-first ergonomic monitoring."},
)

PAGES = {
    "dashboard": ("Dashboard", "fa-table-columns", dashboard.render, "Overview of status, summary and activity"),
    "monitoring": ("Live Monitoring", "fa-video", monitoring.render, "Real-time camera, posture and ocular tracking"),
    "analytics": ("Analytics", "fa-chart-line", analytics_page.render, "Session, weekly and monthly analysis"),
    "reports": ("Reports", "fa-file-lines", reports.render, "Session history, CSV and PDF exports"),
    "settings": ("Settings", "fa-sliders", settings_page.render, "Calibration, thresholds, notifications, privacy"),
}

LOGO_SVG = (
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" '
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round">'
    '<path d="M3 12h3.2l1.6-4.2 2.6 9 2.2-6.4 1.7 3.6H21" />'
    '<circle cx="12" cy="3.2" r="1.2" fill="currentColor" stroke="none" /></svg>'
)


# --------------------------------------------------------------------------
def inject_theme() -> None:
    """Load the shell stylesheet and emit the active palette as CSS variables."""
    css_path = BASE_DIR / "ui" / "theme.css"
    try:
        css = css_path.read_text(encoding="utf-8")
    except OSError:
        log.warning("Theme stylesheet missing at %s", css_path)
        return

    theme = st.session_state.get("anatonix_theme", "dark")
    tokens = theme_css_variables(theme, "anx")
    fonts = FONTS
    font_block = (
        ":root {\n"
        f"  --anx-display: {fonts['display']};\n"
        f"  --anx-body: {fonts['body']};\n"
        f"  --anx-mono: {fonts['mono']};\n"
        "}"
    )
    # @import must lead the sheet; local fallbacks are declared in config.FONTS.
    imports = f"@import url('{FONT_IMPORT_URL}');\n@import url('{ICON_IMPORT_URL}');\n"
    st.markdown(
        f"<style>{imports}{font_block}\n{tokens}\n{css}</style>",
        unsafe_allow_html=True,
    )


def sidebar(page: str, db, settings: Dict) -> None:
    status = system_status(db)
    camera = camera_status()
    footer = version_footer_items(db)

    with st.sidebar:
        st.markdown(
            '<div class="anx-side-brand">'
            f'<div class="anx-side-logo">{LOGO_SVG}</div>'
            '<div><span class="name">Anatonix</span>'
            f'<span class="tag">{APP_TAGLINE}</span></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="anx-side-status"><span class="dot {status["tone"]}"></span>'
            f'<span>{kit.esc(status["label"])}</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="anx-side-status" style="margin-top:-6px"><span class="dot {camera["tone"]}"></span>'
            f'<span>{kit.esc(camera["label"])}</span></div>',
            unsafe_allow_html=True,
        )

        links = []
        for key, (label, icon, _render, hint) in PAGES.items():
            active = " is-active" if key == page else ""
            badge = ""
            if key == "reports":
                badge = f'<span class="badge">{footer["sessions"]}</span>'
            elif key == "monitoring" and camera["tone"] == "live":
                badge = '<span class="badge">live</span>'
            links.append(
                f'<a class="anx-nav-link{active}" href="?page={key}" target="_self" '
                f'title="{kit.esc(hint)}"><i class="fa-solid {icon}"></i>'
                f'<span>{kit.esc(label)}</span>{badge}</a>'
            )
        st.markdown(f'<nav class="anx-nav">{"".join(links)}</nav>', unsafe_allow_html=True)

        st.markdown(
            '<div class="anx-side-foot">'
            f'<div class="row"><span>Version</span><span>v{footer["version"]}</span></div>'
            f'<div class="row"><span>Sessions</span><span>{footer["sessions"]}</span></div>'
            f'<div class="row"><span>Records</span><span>{footer["records"]}</span></div>'
            f'<div class="row"><span>Database</span><span>{footer["db"]}</span></div>'
            '<div class="row"><span>Processing</span><span>local</span></div>'
            "</div>",
            unsafe_allow_html=True,
        )

        with st.expander("Session shortcuts", expanded=False):
            st.caption(
                "Calibrate baseline: press **C** on the Live Monitoring page. "
                "Close the calibration dialog with **Esc**. Stop monitoring with the Stop button "
                "to release the camera immediately."
            )
            if st.button("Open Live Monitoring", use_container_width=True, key="side_open_monitoring"):
                st.query_params["page"] = "monitoring"
                st.rerun()


def main() -> None:
    page_key = st.query_params.get("page", "dashboard")
    if page_key not in PAGES:
        page_key = "dashboard"

    db = get_database()
    settings = load_settings(db)
    st.session_state["anatonix_theme"] = settings.get("theme", "dark")

    inject_theme()
    sidebar(page_key, db, settings)

    label, _icon, renderer, _hint = PAGES[page_key]
    try:
        renderer(db, settings)
    except Exception as exc:  # pragma: no cover - user-visible failure path
        log.exception("Page %s failed to render", page_key)
        st.error(f"The {label} page could not be rendered: {exc}")
        st.exception(exc)
        return

    kit.app_footer()


if __name__ == "__main__":
    main()
