"""Structured logging via loguru with two sinks:

* **stderr** — clean, human-readable for local runs and CI live-output.
  Format: ``HH:mm:ss | LEVEL | message``.

* **JSON file** (``reports/logs/test-run.jsonl``) — one JSON object per line
  preserving every ``logger.info(..., key=value)`` kwarg under ``record.extra``.
  This is what a log shipper (Promtail/Vector/Datadog Agent) tails into Loki /
  Grafana, where you can filter by ``extra.app``, ``extra.tab``,
  ``extra.extension_id``, etc.

The terminal stays clean; the structured fields stay queryable.
"""

import sys
from pathlib import Path
from typing import Any

from loguru import logger as _logger

from config.settings import settings

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _REPO_ROOT / "reports" / "logs"
_JSON_LOG_PATH = _LOG_DIR / "test-run.jsonl"


def configure_logging() -> None:
    """Remove default handler and register the stderr + JSON file sinks once."""
    _logger.remove()
    level = settings.log.level.upper()

    _logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        serialize=False,
    )

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _logger.add(
        _JSON_LOG_PATH,
        level=level,
        serialize=True,
        rotation="10 MB",
        retention=5,
        enqueue=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a logger bound to a module name (optional)."""
    if name:
        return _logger.bind(module=name)
    return _logger


configure_logging()
logger = get_logger("prompt_security")
