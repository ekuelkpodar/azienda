"""Structured logging: structlog with JSON output, request-id, DLP redaction.

Every log record passes through ``redact_event`` BEFORE serialization, so a
secret can never reach a sink because a developer logged the wrong dict.
"""
from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

import structlog

from app.core import redact
from app.core.config import Settings


def _redact_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact.redact_mapping(event_dict)


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _redact_processor,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if settings.log_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stdout, force=True)


def new_request_id() -> str:
    return uuid.uuid4().hex


def bind_request(request_id: str, tenant_id: str | None = None,
                 user_id: str | None = None) -> None:
    structlog.contextvars.bind_contextvars(request_id=request_id,
                                           tenant_id=tenant_id or "-",
                                           user_id=user_id or "-")


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str) -> Any:  # structlog ships no type stubs
    return structlog.get_logger(name)
