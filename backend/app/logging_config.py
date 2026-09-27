"""
Structured logging setup. Emits JSON-ish key=value lines so logs are easy
to grep and to ship to a log aggregator later. Never logs secret values --
call `redact()` on anything that might contain a token/key before logging it.
"""
from __future__ import annotations

import logging
import sys
from .config import get_settings

_SECRET_MARKERS = ("token", "api_key", "apikey", "authorization", "secret", "password")


def redact(value: str) -> str:
    if not value:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-2:]}"


class _SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return msg


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _SafeFormatter("%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s")
    )
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
