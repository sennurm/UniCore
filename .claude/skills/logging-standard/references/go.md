# Go Logging Setup

**Library:** `log/slog` (stdlib, structured, JSON handler) as the baseline. Use
`zap` or `zerolog` on zero-allocation hot paths — the field rules are identical.

**Install (bridge):** `go get go.opentelemetry.io/contrib/bridges/otelslog go.opentelemetry.io/otel`

## Logger bootstrap (JSON + LOG_LEVEL)

```go
package logging

import (
	"log/slog"
	"os"
	"strings"
)

var levelVar = new(slog.LevelVar) // runtime-mutable

func Init() *slog.Logger {
	levelVar.Set(parseLevel(os.Getenv("LOG_LEVEL")))
	h := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: levelVar,
		ReplaceAttr: func(_ []string, a slog.Attr) slog.Attr {
			switch a.Key {
			case slog.TimeKey:
				a.Key = "timestamp"
			case slog.MessageKey:
				a.Key = "message"
			case slog.LevelKey:
				a.Value = slog.StringValue(strings.ToUpper(a.Value.String()))
			}
			return a
		},
	})
	return slog.New(h).With("service", "checkout-api")
}

func parseLevel(s string) slog.Level {
	switch strings.ToUpper(s) {
	case "DEBUG", "TRACE":
		return slog.LevelDebug
	case "WARN":
		return slog.LevelWarn
	case "ERROR", "FATAL":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
```

`slog.LevelVar` makes the threshold changeable at runtime, but `LOG_LEVEL` is the
source of truth at startup.

## Trace context
Prefer the OTel bridge `otelslog` so records auto-correlate. For explicit
control, attach canonical fields from the active span, skipping invalid contexts:

```go
import (
	"context"
	"log/slog"
	"go.opentelemetry.io/otel/trace"
)

func withTrace(ctx context.Context, l *slog.Logger) *slog.Logger {
	sc := trace.SpanContextFromContext(ctx)
	if !sc.IsValid() { // all-zero / no span → omit
		return l
	}
	return l.With(
		"trace_id", sc.TraceID().String(),   // 32 hex
		"span_id", sc.SpanID().String(),     // 16 hex
		"trace_flags", sc.TraceFlags().String(),
	)
}
```

Add `user_id` per request with `logger.With("user_id", "u_9f3c2b7a")`.

## Timing helper for API / DB / AI calls

```go
import (
	"context"
	"log/slog"
	"time"
	"go.opentelemetry.io/otel"
)

func Timed(ctx context.Context, l *slog.Logger, op string, attrs []any, fn func(context.Context) error) error {
	start := time.Now()
	ctx, span := otel.Tracer("checkout-api").Start(ctx, op)
	defer span.End()
	err := fn(ctx)
	attrs = append(attrs, "duration_ms", time.Since(start).Milliseconds())
	withTrace(ctx, l).Info(op, attrs...)
	return err
}

// DB:
// Timed(ctx, log, "db query completed",
//   []any{"db.system.name","postgresql","db.query.summary","SELECT orders"},
//   func(ctx context.Context) error { _, e := db.QueryContext(ctx, sql); return e })
// AI:
// Timed(ctx, log, "ai call completed",
//   []any{"gen_ai.provider.name","openai","gen_ai.request.model","gpt-4o"}, callModel)
```

`time.Since` uses Go's monotonic clock — correct for durations.

## Propagation across services
Use `otelhttp`/`otelgrpc` and instrumented DB drivers so the W3C `traceparent`
header flows across boundaries automatically.
