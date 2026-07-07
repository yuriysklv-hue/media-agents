"""Структурированный логинг: консоль + data/logs/runs.log."""
from __future__ import annotations

import logging
import os

from .config import DATA_DIR

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger("media_agents")
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "runs.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(f"media_agents.{name}")
