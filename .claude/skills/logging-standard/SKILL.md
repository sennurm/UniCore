---
name: logging-standard
description: >-
  The project's mandatory standard for application logging. Use this skill
  whenever you write, review, or modify code that emits logs, or when you add or
  change an API endpoint, database query, external service call, or AI/LLM call.
  It defines the required JSON log format, the trace_id/span_id fields every log
  must carry, runtime log-level control, and how to record how long API, DB, and
  AI calls take in milliseconds. Apply it for Python, Node.js/TypeScript, Java,
  Go, and .NET. Trigger on anything about logging, log levels, structured logs,
  tracing, request tracing, correlation IDs, observability, "how do I log X",
  debug logging, or diagnosing a user-reported error from logs — even when the
  user does not name this standard explicitly.
---

# Project Logging Standard

This is the single source of truth for how this project logs. Every service,
in every language, follows it so that a single user-reported error can be traced
from the frontend request through every API hop, database query, and AI call —
by searching one `trace_id`. Consistency is the whole point: if one service logs
`traceId` and another logs `trace_id`, correlation breaks and the standard has
failed. When you write or review logging code, conform to the rules here.

## The five rules (what every log must satisfy)

1. **Structured JSON, always.** Logs are machine-parsed, not eyeballed. Every
   log line is a single JSON object with the required fields below. No
   free-form `printf`/`console.log`/`System.out` in application code.
2. **Every level is available; the active level is chosen at runtime.** Code
   emits at all severities (`TRACE`→`FATAL`). Which ones actually surface is
   controlled by the `LOG_LEVEL` environment variable at process start, so a
   developer can rerun with `LOG_LEVEL=DEBUG` to investigate without editing
   code.
3. **Correlation IDs on every line.** Every log carries `trace_id` and
   `span_id` (canonical names, see below), so logs from all layers of one
   request join up.
4. **A stable user identifier on every request-scoped log.** So a
   user-reported error ("it broke at 2:04pm for me") can be found by their id.
   This id must not be raw PII (see the PII rule).
5. **Timed operations record `duration_ms`.** Every outbound API call, database
   query, and AI/LLM call logs how long it took, in integer milliseconds, under
   the key `duration_ms`.

If you're about to violate one of these, stop and reconsider — these five are
what make the logs usable months later during an incident.

## Required JSON fields

Use exactly these keys. The canonical names are chosen so all five languages
agree; where a library defaults to a different name (notably Python's OTel
instrumentation), remap it to these.

| Field         | Required        | Meaning                                                        |
|---------------|-----------------|----------------------------------------------------------------|
| `timestamp`   | always          | ISO-8601 UTC with milliseconds, e.g. `2026-07-15T18:04:11.482Z`|
| `level`       | always          | One of `TRACE DEBUG INFO WARN ERROR FATAL`                     |
| `message`     | always          | Short human-readable event; keep variable data in fields       |
| `service`     | always          | Logical service name, e.g. `checkout-api`                      |
| `trace_id`    | when in a trace | 32 lowercase hex chars (128-bit). Omit if no valid span        |
| `span_id`     | when in a trace | 16 lowercase hex chars (64-bit). Omit if no valid span         |
| `trace_flags` | when in a trace | 2 hex chars; `01` = sampled                                    |
| `user_id`     | request-scoped  | Stable pseudonymous user id (not raw email/PII)                |
| `duration_ms` | timed ops       | Integer milliseconds for API/DB/AI operations                  |
| `error.type`  | on errors       | Exception class / error code                                   |
| `error.stack` | on errors       | Stack trace (ERROR/FATAL only)                                 |

Add operation-specific fields using OpenTelemetry semantic-convention names so
they line up with traces (see `references/log-schema.md` for the full list):
e.g. `http.request.method`, `http.response.status_code`, `db.system.name`,
`db.query.summary`, `gen_ai.provider.name`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`.

**Canonical example** (pretty-printed here; emit as one line):

```json
{
  "timestamp": "2026-07-15T18:04:11.482Z",
  "level": "INFO",
  "message": "db query completed",
  "service": "checkout-api",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "trace_flags": "01",
  "user_id": "u_9f3c2b7a",
  "db.system.name": "postgresql",
  "db.query.summary": "SELECT orders",
  "duration_ms": 42
}
```

## Log levels and choosing them at runtime

Support the full ladder and use each for its purpose, so raising the level
during an incident actually reveals useful detail:

- **TRACE** — very fine-grained flow; per-iteration or per-field detail.
- **DEBUG** — diagnostic detail useful when investigating (inputs, decisions,
  intermediate values). Off in production by default.
- **INFO** — normal lifecycle events: request received, operation completed.
- **WARN** — recoverable/abnormal but handled (retry, fallback, deprecated path).
- **ERROR** — an operation failed; include `error.type` and `error.stack`.
- **FATAL** — the process cannot continue.

The active threshold is read from the **`LOG_LEVEL`** environment variable at
startup (default `INFO`). A developer validates or debugs by rerunning the same
build with `LOG_LEVEL=DEBUG` (or `TRACE`) — never by editing code to add or
uncomment logging. Each language's setup for reading `LOG_LEVEL` is in its
reference file. Note: do **not** use `OTEL_LOG_LEVEL` for this — that controls
the OpenTelemetry SDK's own internal logging, not your application's level.

## Trace and span IDs across every layer

We use OpenTelemetry / W3C Trace Context so IDs propagate automatically across
service boundaries and match your traces in the observability backend.

- **Field names are canonical:** `trace_id`, `span_id`, `trace_flags`. Some
  instrumentation emits different names (Python's `LoggingInstrumentor` uses
  `otelTraceID`/`otelSpanID`); remap those to the canonical names so all
  services agree. This is the single most common way correlation silently
  breaks — check it in review.
- **Cross-service propagation:** pass the W3C `traceparent` header on every
  outbound HTTP/RPC call and read it on every inbound request. Format:
  `00-<32hex trace-id>-<16hex parent/span-id>-<2hex flags>`, e.g.
  `00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`. OTel
  instrumentation does this for you; the point of review is to confirm nothing
  strips or regenerates the header mid-flow.
- **Propagate into DB and AI layers too:** open a child span around each
  database query and AI call so those logs carry the same `trace_id` and a new
  `span_id`. That's what lets you see, for one user's request, exactly which
  query or model call was slow or failed.
- **Never log all-zero IDs.** When there is no active span the context is
  invalid and the ids are all zeros (`00000000000000000000000000000000`).
  Omit `trace_id`/`span_id` in that case rather than logging zeros — zeros look
  like a real-but-broken link and pollute correlation.

## Recording how long things take (`duration_ms`)

For every outbound **API call**, **database query**, and **AI/LLM call**, wrap
the operation with a timer and log its wall-clock duration as an integer under
`duration_ms`. Measure with a monotonic clock (not wall time, which can jump).
Emit the timing log when the operation completes — success or failure — so slow
failures are visible too. Pair `duration_ms` with the relevant semantic fields
(`db.system.name`, `gen_ai.request.model`, target host, etc.) so you can tell
*what* was slow, not just *that* something was.

Each reference file includes a small reusable timing helper/decorator for its
language — prefer that over hand-rolling `start = now()` everywhere, so the
field name and units stay consistent.

## PII and cardinality guardrails

These protect both users and the logging pipeline:

- **`user_id` must be pseudonymous.** Log a stable hashed/opaque id
  (`u_9f3c2b7a`), never raw email, name, phone, or full IP. The id still lets
  you find one user's logs without storing their identity in plaintext.
- **Never log secrets or request bodies** that may contain PII, tokens, card
  numbers, prompts with personal data, etc. Redact before logging.
- **Keep high-cardinality raw data in logs, not metrics.** Raw SQL, full URLs
  with ids, and user ids are fine as log fields but must never become metric
  dimensions — they explode cardinality. Use summarized fields
  (`db.query.summary`, templated routes) alongside the raw values.

## How to apply this skill

1. Identify the language(s) of the code in play and open the matching reference
   file below. Each contains the recommended library, the logger bootstrap
   (JSON output + `LOG_LEVEL`), OTel trace-context wiring, and copy-paste timing
   helpers for API/DB/AI calls.
2. Make the code conform to the five rules and the required fields. When
   reviewing existing code, check specifically for: non-JSON logging, missing
   `trace_id`/`span_id` (or wrong field names), missing `duration_ms` on
   API/DB/AI calls, hard-coded log levels, and raw PII in `user_id`.
3. Use `references/log-schema.md` as the authority for exact field names and the
   OTel semantic-convention attributes to attach per operation type.

### Reference files

- `references/log-schema.md` — the complete field dictionary, level semantics,
  OTel attribute names for HTTP/DB/AI, and the `traceparent` format. Read this
  when you need the exact key for something.
- `references/python.md` — Python (structlog / stdlib) setup + helpers.
- `references/nodejs.md` — Node.js/TypeScript (pino) setup + helpers.
- `references/java.md` — Java (Logback + logstash encoder / Log4j2) setup + MDC.
- `references/go.md` — Go (slog / zap / zerolog) setup + helpers.
- `references/dotnet.md` — .NET (Serilog / MEL) setup + helpers.
