#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Anatonix launcher — creates a local virtual environment on first run,
# installs the pinned dependencies, then starts Streamlit.
#
#   ./run.sh              # normal start (http://localhost:8501)
#   ./run.sh --port 8700  # any extra flags are forwarded to `streamlit run`
#
# Safe to re-run: the environment is only created/updated when needed.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${ANATONIX_VENV:-.venv}"
STAMP="$VENV_DIR/.requirements.sha256"

# --- 1. make sure we can create a virtual environment -----------------------
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "error: '$PYTHON_BIN' not found. Install Python 3.10+ first." >&2
  exit 1
fi

# --- 2. create the environment ---------------------------------------------
if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "==> Creating virtual environment in $VENV_DIR"
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null; then
    echo "error: could not create a virtual environment." >&2
    echo "       On Arch install the venv module:  sudo pacman -S python" >&2
    echo "       or bootstrap pip:                  sudo pacman -S python-pip" >&2
    exit 1
  fi
fi

# --- 3. install / update dependencies --------------------------------------
# requirements.txt is hashed so repeated launches skip the pip step.
WANT_STAMP="$(sha256sum requirements.txt | cut -d' ' -f1)"
HAVE_STAMP="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$WANT_STAMP" != "$HAVE_STAMP" ] || ! "$VENV_DIR/bin/python" -c "import streamlit" >/dev/null 2>&1; then
  echo "==> Installing dependencies (one-time, ~1 minute)"
  "$VENV_DIR/bin/python" -m pip install --upgrade --quiet pip
  "$VENV_DIR/bin/python" -m pip install --quiet -r requirements.txt
  echo "$WANT_STAMP" > "$STAMP"
  echo "==> Dependencies ready"
fi

# --- 4. launch -------------------------------------------------------------
# `./run.sh --port 8700` is friendlier than `--server.port 8700`, so translate it.
PORT="8501"
if [ "${1:-}" = "--port" ] && [ -n "${2:-}" ]; then
  PORT="$2"
  shift 2
fi

echo
echo "==> Anatonix starting on http://localhost:$PORT"
echo "    Open that address in Chrome/Edge/Firefox and allow camera access."
echo "    MediaPipe models are downloaded on first use (internet needed once)."
echo "    Press Ctrl+C to stop."
echo

exec "$VENV_DIR/bin/python" -m streamlit run app.py \
  --server.address localhost \
  --server.port "$PORT" \
  --browser.gatherUsageStats false \
  "$@"
