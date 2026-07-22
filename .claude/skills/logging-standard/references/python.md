# Python Logging Setup

**Library:** `structlog` (composable processors, native JSON). If you must stay
on stdlib `logging`, use `python-json-logger` as the formatter — the rules are
the same.

**Install:** `pip install structlog opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-logging`

## Logger bootstrap (JSON + LOG_LEVEL)

```python
import logging
import os
import structlog

LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(format="%(message)s", level=getattr(logging, LEVEL, logging.INFO))

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, LEVEL, logging.INFO)),
    processors=[
        structlog.contextvars.merge_contextvars,   # request-scoped fields
        add_trace_context,                          # see below
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        rename_level_upper,                         # WARN/ERROR uppercase
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger(service="checkout-api")
```

`structlog` maps its keys to our schema: `timestamp` (TimeStamper), `level`,
`event`→ rename to `message` if you want strict parity (add a small processor),
and `service` via the bound logger.

## Trace context (remap OTel's names to canonical)

Python's `LoggingInstrumentor` emits `otelTraceID`/`otelSpanID`. Pull ids
directly from the active span and emit canonical names, skipping invalid spans:

```python
from opentelemetry import trace

def add_trace_context(logger, method_name, event_dict):
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:                       # skip all-zero/no-span
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
        event_dict["trace_flags"] = format(ctx.trace_flags, "02x")
    return event_dict
```

Set `user_id` per request (pseudonymous):

```python
structlog.contextvars.bind_contextvars(user_id="u_9f3c2b7a")
```

## Timing helper for API / DB / AI calls

```python
import time
from contextlib import contextmanager
from opentelemetry import trace

tracer = trace.get_tracer("checkout-api")

@contextmanager
def timed(operation: str, **fields):
    """Wrap a DB/API/AI call: opens a child span and logs duration_ms."""
    start = time.monotonic()
    with tracer.start_as_current_span(operation):
        try:
            yield
        finally:
            dur = int((time.monotonic() - start) * 1000)
            log.info(operation, duration_ms=dur, **fields)

# Database
with timed("db query completed", **{"db.system.name": "postgresql",
                                     "db.query.summary": "SELECT orders"}):
    rows = cursor.execute(sql)

# AI call
with timed("ai call completed", **{"gen_ai.provider.name": "anthropic",
                                    "gen_ai.request.model": "claude-opus-4"}):
    resp = client.messages.create(...)
```

Use a monotonic clock (`time.monotonic`) so timing is immune to wall-clock jumps.

## Propagation across services
Use `opentelemetry-instrumentation-{requests,httpx,fastapi,flask,...}` so the
W3C `traceparent` header is injected on outbound calls and extracted inbound
automatically. Don't build the header by hand.
