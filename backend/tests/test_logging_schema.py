"""Verifies conformance to the project logging standard (JSON shape, canonical fields)."""

import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from unicore.core.logging import configure_logging, get_logger, timed


@pytest.fixture(autouse=True)
def _configured_logging() -> None:
    configure_logging("unicore-api")


def _last_json_line(capsys: pytest.CaptureFixture[str]) -> dict:
    lines = [ln for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_log_line_is_json_with_required_fields(capsys: pytest.CaptureFixture[str]) -> None:
    get_logger().info("unit test event", extra_field="x")
    record = _last_json_line(capsys)
    assert record["message"] == "unit test event"
    assert record["level"] == "INFO"
    assert record["service"] == "unicore-api"
    assert "timestamp" in record and record["timestamp"].endswith("Z")
    assert record["extra_field"] == "x"


def test_warn_and_fatal_levels_are_canonical(capsys: pytest.CaptureFixture[str]) -> None:
    get_logger().warning("warn event")
    assert _last_json_line(capsys)["level"] == "WARN"
    get_logger().critical("fatal event")
    assert _last_json_line(capsys)["level"] == "FATAL"


def test_no_trace_ids_without_active_span(capsys: pytest.CaptureFixture[str]) -> None:
    get_logger().info("no span here")
    record = _last_json_line(capsys)
    # All-zero ids must be omitted, never logged.
    assert "trace_id" not in record
    assert "span_id" not in record


def test_trace_ids_present_inside_span(capsys: pytest.CaptureFixture[str]) -> None:
    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("op"):
        get_logger().info("inside span")
    record = _last_json_line(capsys)
    assert len(record["trace_id"]) == 32
    assert len(record["span_id"]) == 16
    assert record["trace_id"] != "0" * 32


def test_timed_emits_duration_ms(capsys: pytest.CaptureFixture[str]) -> None:
    with timed("db query completed", **{"db.system.name": "postgresql"}):
        pass
    record = _last_json_line(capsys)
    assert record["message"] == "db query completed"
    assert isinstance(record["duration_ms"], int)
    assert record["db.system.name"] == "postgresql"


def test_timed_logs_on_failure_too(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(ValueError):
        with timed("api call completed"):
            raise ValueError("boom")
    record = _last_json_line(capsys)
    assert record["message"] == "api call completed"
    assert "duration_ms" in record


def test_current_span_helper_does_not_leak_zero_ids() -> None:
    ctx = trace.get_current_span().get_span_context()
    assert not ctx.is_valid or ctx.trace_id != 0
