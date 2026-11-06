"""Design-system contract tests.

The light and dark palettes are consumed by three surfaces (the Streamlit
shell, the monitoring component and the Chart.js views), so the token names
themselves are part of the API: a missing key silently falls back to a stale
hard-coded colour somewhere in the UI.
"""

from __future__ import annotations

import re

import pytest

import config
from config import CHART_PALETTE, THEMES, TOKENS, chart_colors, theme_css_variables, theme_tokens
import ui.kit as kit

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
RGBA = re.compile(r"^rgba?\([^)]+\)$")


def test_both_themes_define_the_same_tokens():
    light, dark = set(THEMES["light"]), set(THEMES["dark"])
    assert light == dark, f"token drift: {light ^ dark}"


def test_every_colour_token_is_a_valid_colour():
    for name, palette in THEMES.items():
        for key, value in palette.items():
            if key.startswith("shadow"):
                continue
            assert HEX.match(value) or RGBA.match(value), f"{name}.{key} = {value!r}"


def test_theme_tokens_merge_geometry():
    tokens = theme_tokens("light")
    assert tokens["radius"] == TOKENS["radius"]
    assert tokens["accent"] == THEMES["light"]["accent"]


def test_unknown_theme_falls_back_to_dark():
    assert theme_tokens("solarized")["bg"] == THEMES["dark"]["bg"]


def test_theme_css_variables_emits_every_token():
    css = theme_css_variables("light", "anx")
    assert css.startswith(":root {")
    for key in list(TOKENS) + list(THEMES["light"]):
        assert f"--anx-{key.replace('_', '-')}:" in css


def test_chart_palette_resolves_per_theme():
    for theme in THEMES:
        resolved = chart_colors(theme)
        assert set(resolved) == set(CHART_PALETTE)
        for value in resolved.values():
            assert HEX.match(value)


def test_chart_series_colours_differ_between_themes():
    light, dark = chart_colors("light"), chart_colors("dark")
    assert light["lean"] != dark["lean"]


def test_heading_only_card_renders_as_a_section_bar():
    assert "anx-card--bar" in kit.card("", "Session history", "fa-clock")
    assert "anx-card--bar" not in kit.card("<p>body</p>", "Session history", "fa-clock")


def test_table_escapes_cell_content():
    markup = kit.table(["Name"], [["<script>alert(1)</script>"]])
    assert "&lt;script&gt;" in markup
    assert "<script>" not in markup


def test_table_renders_progress_bar_for_bar_columns():
    markup = kit.table(["Session", "Tracking (%)"], [("s-1", 82.5)], numeric=(0,), bars={1: 100.0})
    assert 'style="width:82.5%"' in markup
    assert "82.5%" in markup


def test_table_handles_a_missing_bar_value():
    markup = kit.table(["Session", "Tracking (%)"], [("s-1", None)], bars={1: 100.0})
    assert 'style="width:0.0%"' in markup
    assert "—" in markup


def test_table_empty_state_does_not_invent_rows():
    markup = kit.table(["A", "B"], [], empty="No sessions recorded")
    assert "No sessions recorded" in markup
    assert "is-empty" in markup
