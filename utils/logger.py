"""Structured logging via loguru (Grafana-friendly: message + kwargs)."""

import sys
from typing import Any

from loguru import logger as _logger

from config.settings import settings


def configure_logging() -> None:
    """Remove default handler and add a structured sink once."""
    _logger.remove()
    level = settings.log.level.upper()
    _logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> "
        "{extra}",
        serialize=False,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a logger bound to a module name (optional)."""
    if name:
        return _logger.bind(module=name)
    return _logger


configure_logging()
logger = get_logger("prompt_security")
