# Log Schema & Field Dictionary

The authoritative list of field names. When in doubt about what to call
something, use the name here. Names follow OpenTelemetry semantic conventions so
logs align with traces and metrics.

## Table of contents
- [Core fields (every log)](#core-fields)
- [Trace-context fields](#trace-context-fields)
- [HTTP / API-call fields](#http--api-call-fields)
- [Database fields](#database-fields)
- [AI / LLM fields](#ai--llm-fields)
- [Error fields](#error-fields)
- [Level semantics](#level-semantics)
- [W3C traceparent format](#w3c-traceparent-format)

## Core fields
| Key         | Type   | Notes                                                       |
|-------------|--------|-------------------------------------------------------------|
| `timestamp` | string | ISO-8601 UTC, millisecond precision, `Z` suffix             |
| `level`     | string | `TRACE` `DEBUG` `INFO` `WARN` `ERROR` `FATAL`               |
| `message`   | string | Short, low-cardinality event description                    |
| `service`   | string | Logical service/app name                                    |
| `user_id`   | string | Pseudonymous stable id; request-scoped; never raw PII       |
| `duration_ms` | int  | Integer milliseconds; on API/DB/AI operations               |

Notes on `duration_ms`: OpenTelemetry has **no** standard log field for
duration (spans carry it implicitly via start/end; metrics use seconds). This
project standardizes on `duration_ms` as an integer for logs. Keep the name and
units identical everywhere.

## Trace-context fields
| Key           | Type   | Notes                                                  |
|---------------|--------|--------------------------------------------------------|
| `trace_id`    | string | 32 lowercase hex chars (128-bit). Omit if no valid span|
| `span_id`     | string | 16 lowercase hex chars (64-bit). Omit if no valid span |
| `trace_flags` | string | 2 hex chars; `01` = sampled, `00` = not sampled        |

Canonical names are `trace_id` / `span_id` / `trace_flags`. Known library
defaults that must be remapped:
- Python `LoggingInstrumentor`: emits `otelTraceID`, `otelSpanID`,
  `otelTraceSampled` → remap to the canonical names.
- pino, Java MDC, .NET MEL, Go OTLP already use the canonical names (or
  `TraceId`/`SpanId` casing you lowercase).

All-zero ids (`000…0`) mean "no active span" — do not log them; omit instead.

## HTTP / API-call fields
Use for inbound requests and outbound API calls.
| Key                          | Notes                                    |
|------------------------------|------------------------------------------|
| `http.request.method`        | `GET`, `POST`, …                         |
| `http.response.status_code`  | integer                                  |
| `url.full`                   | outbound client calls (redact secrets)   |
| `url.path`                   | inbound server requests                  |
| `server.address`             | target host                              |
| `server.port`                | target port                              |
| `network.protocol.version`   | e.g. `1.1`, `2`                          |
| `error.type`                 | on failure                               |

## Database fields
Attribute names updated in OTel semconv v1.4x (older names in parentheses —
do not use the old ones):
| Key                  | Was            | Notes                                   |
|----------------------|----------------|-----------------------------------------|
| `db.system.name`     | `db.system`    | `postgresql`, `mysql`, `redis`, …       |
| `db.operation.name`  | `db.operation` | `SELECT`, `INSERT`, …                   |
| `db.query.text`      | `db.statement` | Parameterize literals to `?`; may be PII|
| `db.query.summary`   | —              | Low-cardinality summary, e.g. `SELECT orders` |
| `db.namespace`       | —              | database/schema name                    |
| `db.collection.name` | —              | table/collection                        |

Always pair a DB log with `duration_ms`.

## AI / LLM fields
GenAI semantic conventions are still experimental and changing; pin these names
project-wide. (`gen_ai.provider.name` supersedes the older `gen_ai.system`.)
| Key                          | Notes                                    |
|------------------------------|------------------------------------------|
| `gen_ai.operation.name`      | `chat`, `text_completion`, `generate_content` |
| `gen_ai.provider.name`       | `openai`, `anthropic`, `gcp.vertex_ai`   |
| `gen_ai.request.model`       | requested model id                       |
| `gen_ai.response.model`      | model that actually served              |
| `gen_ai.request.max_tokens`  | integer                                  |
| `gen_ai.request.temperature` | float                                    |
| `gen_ai.usage.input_tokens`  | integer                                  |
| `gen_ai.usage.output_tokens` | integer                                  |
| `gen_ai.conversation.id`     | conversation/thread id                   |

Always pair an AI-call log with `duration_ms`. Do not log full prompts/responses
if they may contain user PII — log token counts and model instead, or redact.

## Error fields
| Key           | Notes                                              |
|---------------|----------------------------------------------------|
| `error.type`  | exception class or error code                      |
| `error.stack` | full stack trace; ERROR/FATAL only                 |

## Level semantics
- `TRACE` — per-iteration/per-field flow detail.
- `DEBUG` — diagnostic inputs, decisions, intermediate values (off in prod).
- `INFO` — normal lifecycle: request received, operation completed.
- `WARN` — handled anomaly: retry, fallback, deprecation.
- `ERROR` — operation failed; include `error.type` + `error.stack`.
- `FATAL` — process cannot continue.

Active threshold = `LOG_LEVEL` env var, default `INFO`.

## W3C traceparent format
Header sent on every outbound HTTP/RPC call and read on every inbound request:

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             └┬┘ └──────────────┬──────────────┘ └──────┬───────┘ └┬┘
           version=00       trace-id (32 hex)     parent/span-id   flags
                                                    (16 hex)       01=sampled
```

Four dash-separated fields. `trace-id` and `parent-id` must not be all-zero.
Optional companion header `tracestate` carries vendor `key=value` pairs. Let
OTel instrumentation manage these headers; in review, confirm nothing strips or
regenerates them between hops.
