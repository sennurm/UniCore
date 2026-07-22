# Node.js / TypeScript Logging Setup

**Library:** `pino` — fastest structured logger, JSON by default.

**Install:** `npm i pino @opentelemetry/api @opentelemetry/sdk-node @opentelemetry/instrumentation-pino @opentelemetry/auto-instrumentations-node`

## Logger bootstrap (JSON + LOG_LEVEL)

```ts
import pino from "pino";

export const log = pino({
  level: process.env.LOG_LEVEL?.toLowerCase() ?? "info",
  base: { service: "checkout-api" },
  messageKey: "message",
  timestamp: () => `,"timestamp":"${new Date().toISOString()}"`,
  formatters: {
    level: (label) => ({ level: label.toUpperCase() }), // WARN/ERROR uppercase
  },
});
```

pino levels are lowercase (`trace debug info warn error fatal`); the formatter
above uppercases them to match the schema. Level is mutable at runtime via
`log.level = "debug"` if you ever need it, but the source of truth is
`LOG_LEVEL`.

## Trace context
`@opentelemetry/instrumentation-pino` auto-injects `trace_id`, `span_id`, and
`trace_flags` (canonical names — no remapping needed). Enable it in your OTel
init before requiring pino:

```ts
import { NodeSDK } from "@opentelemetry/sdk-node";
import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";

new NodeSDK({ instrumentations: [getNodeAutoInstrumentations()] }).start();
```

For manual injection (no auto-instrumentation), read the active span and omit
invalid contexts:

```ts
import { trace } from "@opentelemetry/api";

export function traceFields() {
  const ctx = trace.getActiveSpan()?.spanContext();
  if (!ctx || ctx.traceId === "0".repeat(32)) return {};
  return {
    trace_id: ctx.traceId,
    span_id: ctx.spanId,
    trace_flags: ctx.traceFlags.toString(16).padStart(2, "0"),
  };
}
```

Bind `user_id` per request via a child logger: `req.log = log.child({ user_id })`.

## Timing helper for API / DB / AI calls

```ts
import { trace } from "@opentelemetry/api";
const tracer = trace.getTracer("checkout-api");

export async function timed<T>(
  operation: string,
  fields: Record<string, unknown>,
  fn: () => Promise<T>,
): Promise<T> {
  const start = process.hrtime.bigint();
  return tracer.startActiveSpan(operation, async (span) => {
    try {
      return await fn();
    } finally {
      const duration_ms = Number((process.hrtime.bigint() - start) / 1_000_000n);
      log.info({ ...fields, duration_ms }, operation);
      span.end();
    }
  });
}

// Database
await timed("db query completed",
  { "db.system.name": "postgresql", "db.query.summary": "SELECT orders" },
  () => pool.query(sql));

// AI call
await timed("ai call completed",
  { "gen_ai.provider.name": "openai", "gen_ai.request.model": "gpt-4o" },
  () => openai.chat.completions.create({ ... }));
```

`process.hrtime.bigint()` is monotonic — correct for measuring elapsed time.

## Propagation across services
The Node auto-instrumentations wire W3C `traceparent` on `http`/`fetch`/`undici`
and popular DB drivers. Don't hand-roll the header.
