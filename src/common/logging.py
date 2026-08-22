"""Structured logging setup."""
from __future__ import annotations

import logging
import sys

import structlog

from .config import get_env


# Libraries that narrate every call at INFO. Our own structured events already
# cover what matters, and at 24/7 these bury the log.
NOISY_LIBRARIES = ("httpx", "httpcore", "apscheduler", "urllib3")


def setup_logging() -> None:
    env = get_env()
    level = getattr(logging, env.mais_log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    if level > logging.DEBUG:
        for name in NOISY_LIBRARIES:
            logging.getLogger(name).setLevel(logging.WARNING)

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
