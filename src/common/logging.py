"""Structured logging with stdout + rotating file output.

A 10 MB rotating file under ``$MAIS_DATA_DIR/logs/mais.log`` is kept (5
generations) so Windows users running under Docker can grep history without
spinning up `docker logs --tail`.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from .config import get_env


def setup_logging() -> None:
    env = get_env()
    level = getattr(logging, env.mais_log_level.upper(), logging.INFO)

    root = logging.getLogger()
    if root.handlers:
        # Already configured — avoid duplicate handlers across reloads
        return
    root.setLevel(level)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_h = logging.StreamHandler()
    stream_h.setFormatter(fmt)
    root.addHandler(stream_h)

    try:
        log_dir = Path(env.mais_data_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_h = RotatingFileHandler(
            log_dir / "mais.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_h.setFormatter(fmt)
        root.addHandler(file_h)
    except Exception:
        # Filesystem read-only or path missing — log to stdout only.
        pass

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
