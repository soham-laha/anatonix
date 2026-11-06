"""Central configuration for Anatonix.

Every tunable default, design token and filesystem path used by the
application lives here so that behaviour can be inspected and audited in a
single place.

Coordinate conventions used across the project
----------------------------------------------
* MediaPipe returns normalised image coordinates:
  ``x`` in [0, 1] left -> right of the *input* frame,
  ``y`` in [0, 1] top -> bottom of the *input* frame,
  ``z`` roughly depth relative to the hips/midpoint, negative = closer to camera.
* The webcam feed is *mirrored* for display only (``scale(-1, 1)``).
  All mathematics is performed on the un-mirrored MediaPipe coordinates, so
  "left shoulder" always means the subject's own left shoulder.
* Angles are expressed in degrees.  ``forward_lean`` is a *relative* metric:
  it is the deviation of the ear-over-shoulder geometry from the calibrated
  baseline, clamped at 0 (leaning back is not counted as forward lean).
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = BASE_DIR / "exports"
ASSET_DIR = BASE_DIR / "assets"
FRONTEND_DIR = BASE_DIR / "frontend"
CHARTS_FRONTEND_DIR = BASE_DIR / "charts_frontend"
LOG_DIR = DATA_DIR / "logs"

for _path in (DATA_DIR, EXPORT_DIR, LOG_DIR):
    _path.mkdir(parents=True, exist_ok=True)

# Allow tests / power users to relocate the database.
DB_PATH = Path(os.environ.get("ANATONIX_DB", DATA_DIR / "anatonix.db"))
LOG_PATH = Path(os.environ.get("ANATONIX_LOG", LOG_DIR / "anatonix.log"))

# --------------------------------------------------------------------------
# Application identity
# --------------------------------------------------------------------------
APP_NAME = "Anatonix"
APP_TAGLINE = "Intelligent Biomechanical &amp; Ocular Monitoring"
APP_VERSION = "1.1.0"
APP_BUILD = "2026.11"

# --------------------------------------------------------------------------
# Design system (used by ui/theme.css and the component stylesheet)
# --------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Design system
# ---------------------------------------------------------------------------
# Two complete, hand-tuned palettes.  "light" follows a bright analytics
# dashboard language (white cards on a cool grey page, deep navy navigation,
# indigo accent); "dark" follows a warm minimal terminal language (near-black
# surfaces, hairline borders, sand/off-white type).
#
# Every colour used anywhere in the UI comes from one of these tokens, so the
# whole application (Streamlit chrome + the monitoring component + charts)
# switches together.

THEMES: Dict[str, Dict[str, str]] = {
    "light": {
        "bg": "#f4f6fb",
        "bg_soft": "#eef1f8",
        "sidebar": "#1b2230",
        "sidebar_text": "#e8ecf4",
        "sidebar_muted": "#94a0b4",
        "sidebar_active": "#262f40",
        "card": "#ffffff",
        "card_alt": "#f7f9fd",
        "elevated": "#eef2fa",
        "border": "#e6ebf4",
        "border_strong": "#d5deec",
        "text": "#131a24",
        "text_muted": "#5f6b7d",
        "text_dim": "#94a1b2",
        "accent": "#6259ca",
        "accent_hover": "#5148b8",
        "accent_ink": "#ffffff",
        "accent_soft": "#efedfb",
        "positive": "#12a171",
        "warning": "#d98a10",
        "critical": "#d7514a",
        "neutral": "#7f8a9b",
        "shadow_card": "0 1px 2px rgba(18, 28, 45, 0.05), 0 12px 28px -20px rgba(18, 28, 45, 0.22)",
        "shadow_pop": "0 18px 44px -22px rgba(18, 28, 45, 0.35)",
        "grid_line": "rgba(19, 26, 36, 0.06)",
        "chart_1": "#6259ca",
        "chart_2": "#9b93e8",
        "chart_3": "#12a171",
        "chart_4": "#e0a13c",
        "chart_5": "#d7514a",
        "video_backdrop": "#0b1220",
        "overlay": "rgba(255, 255, 255, 0.86)",
        "overlay_border": "rgba(19, 26, 36, 0.08)",
    },
    "dark": {
        "bg": "#121110",
        "bg_soft": "#171614",
        "sidebar": "#161514",
        "sidebar_text": "#ece9e2",
        "sidebar_muted": "#8f8a80",
        "sidebar_active": "#232120",
        "card": "#1c1b19",
        "card_alt": "#211f1d",
        "elevated": "#262421",
        "border": "#2a2724",
        "border_strong": "#3a352f",
        "text": "#eae7e1",
        "text_muted": "#9c968c",
        "text_dim": "#736e66",
        "accent": "#d9d3c4",
        "accent_hover": "#eae5d9",
        "accent_ink": "#17160f",
        "accent_soft": "#2b2822",
        "positive": "#8fb99a",
        "warning": "#cbab74",
        "critical": "#c0756c",
        "neutral": "#8d877c",
        "shadow_card": "0 1px 0 rgba(255, 255, 255, 0.03) inset, 0 14px 30px -24px rgba(0, 0, 0, 0.9)",
        "shadow_pop": "0 24px 60px -30px rgba(0, 0, 0, 0.95)",
        "grid_line": "rgba(234, 231, 225, 0.06)",
        "chart_1": "#d9d3c4",
        "chart_2": "#a49a87",
        "chart_3": "#8fb99a",
        "chart_4": "#cbab74",
        "chart_5": "#c0756c",
        "video_backdrop": "#0a0a09",
        "overlay": "rgba(22, 21, 20, 0.86)",
        "overlay_border": "rgba(234, 231, 225, 0.10)",
    },
}

# Shared geometry and typography (identical in both themes).
TOKENS = {
    "radius": "16px",
    "radius_sm": "11px",
    "radius_xs": "8px",
    "gap": "16px",
    "pad": "20px",
}

FONTS = {
    "display": "'Inter', 'Segoe UI', sans-serif",
    "body": "'Inter', 'Segoe UI', sans-serif",
    "mono": "'JetBrains Mono', 'SFMono-Regular', Consolas, monospace",
}

FONT_IMPORT_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Inter:wght@300;400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500;700"
    "&display=swap"
)
ICON_IMPORT_URL = (
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css"
)


def theme_tokens(theme: str = "dark") -> Dict[str, str]:
    """Palette for ``theme`` with the shared geometry merged in."""
    palette = THEMES.get(theme if theme in THEMES else "dark", THEMES["dark"])
    return {**TOKENS, **palette}


def theme_css_variables(theme: str = "dark", prefix: str = "anx") -> str:
    """Render the palette as a ``:root { --token: value }`` CSS block."""
    tokens = theme_tokens(theme)
    lines = [f"  --{prefix}-{name.replace('_', '-')}: {value};" for name, value in tokens.items()]
    return ":root {\n" + "\n".join(lines) + "\n}"


# Backwards-compatible aliases (used by the PDF report and small helpers).
COLORS = {**THEMES["light"]}



# --------------------------------------------------------------------------
# Computer-vision runtime
# --------------------------------------------------------------------------
TASKS_VISION_VERSION = "0.10.14"
TASKS_VISION_BASE = f"https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@{TASKS_VISION_VERSION}"
TASKS_VISION_BUNDLE = f"{TASKS_VISION_BASE}/vision_bundle.mjs"
TASKS_VISION_WASM = f"{TASKS_VISION_BASE}/wasm"
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
)
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

# MediaPipe landmark indices used by the biomechanical engine.
LM_LEFT_EAR = 7
LM_RIGHT_EAR = 8
LM_LEFT_SHOULDER = 11
LM_RIGHT_SHOULDER = 12
LM_LEFT_HIP = 23
LM_RIGHT_HIP = 24
LM_NOSE = 0

# Face mesh landmark indices.
FACE_LEFT_EYE = {"outer": 33, "inner": 133, "top1": 160, "top2": 158, "bot1": 144, "bot2": 153}
FACE_RIGHT_EYE = {"outer": 263, "inner": 362, "top1": 385, "top2": 387, "bot1": 380, "bot2": 373}
FACE_EYE_CROP_LEFT = 33
FACE_EYE_CROP_RIGHT = 263
FACE_EYE_CROP_NOSE = 168

# --------------------------------------------------------------------------
# Default user settings (persisted in the SQLite `settings` table)
# --------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    # --- monitoring -------------------------------------------------------
    "forward_lean_threshold": 12.0,      # degrees above baseline
    "shoulder_tilt_threshold": 6.0,      # degrees
    "bad_frame_count": 20,               # consecutive frames required to alert
    "recovery_frame_count": 15,          # consecutive good frames to clear
    "tracking_sensitivity": 0.5,         # 0..1 -> landmark visibility gate
    "alert_cooldown_seconds": 3.5,
    "sample_interval_ms": 1000,          # telemetry sampling (1 Hz)
    "inference_fps": 15,
    "inference_duty": 0.7,                 # model inference cap
    "camera_device_id": "",              # "" = default camera
    "mirror_preview": True,
    "smoothing_alpha": 0.25,             # EMA factor for live metrics
    # --- ocular -----------------------------------------------------------
    "ear_threshold": 0.19,
    "blink_min_duration_ms": 60,         # ignore ultra-short noise
    "blink_debounce_ms": 120,            # ignore double events
    "blink_low_bpm": 8.0,                # below -> low indicator
    "blink_reference_bpm": 12.0,         # at/above -> reference range
    # --- notifications ----------------------------------------------------
    "audio_enabled": False,
    "audio_volume": 0.8,
    "speech_rate": 1.05,
    "voice_name": "",
    "break_reminders_enabled": True,
    "break_interval_minutes": 30,
    "break_audio": False,
    # --- appearance -------------------------------------------------------
    "theme": "dark",
    "density": "comfortable",
    "chart_animations": True,
    "show_neck_load": True,
    # --- privacy / data ---------------------------------------------------
    "telemetry_retention_days": 180,
}

SETTING_BOUNDS = {
    "forward_lean_threshold": (2.0, 45.0),
    "shoulder_tilt_threshold": (1.0, 25.0),
    "bad_frame_count": (3, 200),
    "recovery_frame_count": (3, 200),
    "tracking_sensitivity": (0.0, 1.0),
    "alert_cooldown_seconds": (0.5, 60.0),
    "sample_interval_ms": (500, 10000),
    "inference_fps": (2, 30),
    "inference_duty": (0.5, 1.0),
    "smoothing_alpha": (0.05, 1.0),
    "ear_threshold": (0.05, 0.35),
    "blink_min_duration_ms": (10, 500),
    "blink_debounce_ms": (50, 1000),
    "blink_low_bpm": (1.0, 20.0),
    "blink_reference_bpm": (2.0, 40.0),
    "audio_volume": (0.0, 1.0),
    "speech_rate": (0.5, 2.0),
    "break_interval_minutes": (5, 180),
    "telemetry_retention_days": (1, 3650),
}

# --------------------------------------------------------------------------
# Illustrative neck-load mapping (educational only)
# --------------------------------------------------------------------------
NECK_LOAD_TABLE = (
    (8.0, 10, "Up to 8\u00b0"),
    (15.0, 20, "Above 8\u00b0"),
    (25.0, 32, "Above 15\u00b0"),
    (float("inf"), 49, "Above 25\u00b0"),
)

NECK_LOAD_DISCLAIMER = (
    "Illustrative Neck-Load Estimate \u2014 these values are simplified educational "
    "estimates derived from a widely circulated posture illustration. They are not "
    "measurements of spinal force or cervical loading and have no clinical validity."
)

GENERAL_DISCLAIMER = (
    "Anatonix is a self-monitoring aid. Its webcam-derived estimates are not "
    "clinically validated, are not medical devices, and cannot diagnose any "
    "condition. Consult a qualified professional for medical concerns."
)

# Colours here are resolved per render (see `theme_tokens`) so the same label
# works in both themes; the tuples carry token names, not hex values.
BLINK_CATEGORY_LABELS = {
    "low": ("Low blink frequency", "warning"),
    "reduced": ("Reduced blink frequency", "warning"),
    "reference": ("Within reference range", "positive"),
}

# Category for the posture-status distribution chart.
POSTURE_STATUSES = ("good", "elevated", "alert", "untracked")

CHART_PALETTE = {
    "lean": "chart_1",
    "tilt": "chart_4",
    "ear": "chart_2",
    "bpm": "chart_3",
    "sessions": "chart_1",
    "alert": "chart_5",
}


def chart_colors(theme: str = "dark") -> Dict[str, str]:
    """Resolve :data:`CHART_PALETTE` token names against a theme palette."""
    tokens = theme_tokens(theme)
    return {name: tokens.get(token, tokens["accent"]) for name, token in CHART_PALETTE.items()}


def resolve_db_path() -> Path:
    """Return the active database path (re-read at call time for tests)."""
    return Path(os.environ.get("ANATONIX_DB", DB_PATH))


def resolve_log_path() -> Path:
    return Path(os.environ.get("ANATONIX_LOG", LOG_PATH))
