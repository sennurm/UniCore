"""Project logging standard: structured JSON, canonical trace fields, LOG_LEVEL at runtime.

Every log line is one JSON object carrying timestamp / level / message / service,
trace_id + span_id + trace_flags when a valid span is active (all-zero ids are
omitted, never logged), user_id when request-scoped (bound via contextvars by the
auth layer), and duration_ms on timed operations (see `timed`).
"""

import logging as stdlib_logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from opentelemetry import trace
from structlog.typing import EventDict

_LEVELS = {
    "TRACE": stdlib_logging.DEBUG,  # stdlib has no TRACE; surfaces with DEBUG
    "DEBUG": stdlib_logging.DEBUG,
    "INFO": stdlib_logging.INFO,
    "WARN": stdlib_logging.WARNING,
    "WARNING": stdlib_logging.WARNING,
    "ERROR": stdlib_logging.ERROR,
    "FATAL": stdlib_logging.CRITICAL,
}


def _add_trace_context(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    ctx = trace.get_current_span().get_span_context()
    if ctx and ctx.is_valid:  # skip no-span/all-zero contexts entirely
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
        event_dict["trace_flags"] = format(ctx.trace_flags, "02x")
    return event_dict


def _canonical_level(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    level = event_dict.get("level", "")
    event_dict["level"] = {"warning": "WARN", "critical": "FATAL"}.get(level, level.upper())
    return event_dict


def _event_to_message(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    event_dict["message"] = event_dict.pop("event", "")
    return event_dict


def configure_logging(service: str) -> None:
    """Configure structlog per the project logging standard; level from LOG_LEVEL."""
    level = _LEVELS.get(os.environ.get("LOG_LEVEL", "INFO").upper(), stdlib_logging.INFO)
    stdlib_logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_trace_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            _canonical_level,
            _event_to_message,
            structlog.processors.JSONRenderer(),
        ],
    )
    structlog.contextvars.clear_contextvars()
    get_logger().info("logging configured", service=service)


def get_logger(**initial_values: Any) -> structlog.stdlib.BoundLogger:
    settings_service = os.environ.get("UNICORE_SERVICE_NAME", "unicore-api")
    return structlog.get_logger(service=settings_service, **initial_values)


_tracer = trace.get_tracer("unicore")


@contextmanager
def timed(operation: str, **fields: Any) -> Iterator[None]:
    """Wrap an outbound API / DB / AI call: child span + duration_ms log, success or failure."""
    start = time.monotonic()
    with _tracer.start_as_current_span(operation):
        try:
            yield
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            get_logger().info(operation, duration_ms=duration_ms, **fields)
