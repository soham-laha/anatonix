"""Logging configuration shared by the Streamlit app, backend services and tests."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from typing import Optional

from config import resolve_log_path

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-24s | %(message)s"


def configure_logging(level: int = logging.INFO, force: bool = False) -> None:
    """Configure root logging once per process (file + stderr)."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT)

    stream = logging.StreamHandler(stream=sys.stderr)
    stream.setFormatter(formatter)
    stream.setLevel(level)
    root.addHandler(stream)

    try:
        log_path = resolve_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover - read-only filesystems
        logging.getLogger(__name__).warning("File logging disabled: %s", exc)

    # Third-party noise control.
    for noisy in ("matplotlib", "PIL", "urllib3", "fontTools"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    configure_logging()
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    return logger
