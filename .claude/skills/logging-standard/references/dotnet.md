# .NET Logging Setup

**Library:** Serilog with `CompactJsonFormatter`, or `Microsoft.Extensions.Logging`
(MEL) + the OpenTelemetry logging exporter. Field rules are identical.

**Install:** `dotnet add package Serilog.AspNetCore` ·
`Serilog.Formatting.Compact` · `Serilog.Enrichers.Span` ·
`OpenTelemetry.Extensions.Hosting` · `OpenTelemetry.Instrumentation.AspNetCore`

## Logger bootstrap (JSON + LOG_LEVEL)

```csharp
using Serilog;
using Serilog.Events;
using Serilog.Formatting.Compact;

var level = Environment.GetEnvironmentVariable("LOG_LEVEL") switch
{
    "TRACE" or "DEBUG" => LogEventLevel.Debug,
    "WARN"             => LogEventLevel.Warning,
    "ERROR" or "FATAL" => LogEventLevel.Error,
    _                  => LogEventLevel.Information,
};

Log.Logger = new LoggerConfiguration()
    .MinimumLevel.Is(level)
    .Enrich.FromLogContext()
    .Enrich.WithSpan()                                   // adds TraceId / SpanId
    .Enrich.WithProperty("service", "checkout-api")
    .WriteTo.Console(new CompactJsonFormatter())
    .CreateLogger();
```

`Serilog.Enrichers.Span` attaches `TraceId`/`SpanId` from `Activity.Current`.
Normalize casing to the canonical `trace_id`/`span_id` at your log pipeline (or
configure the enricher's property names) so all services match. With MEL +
OpenTelemetry, LogRecords auto-attach `TraceId`, `SpanId`, `TraceFlags` from
`Activity.Current`.

For MEL, control level via env: `Logging__LogLevel__Default=Debug`.

## Trace context (manual, when needed)
Only emit when there's a valid Activity, so you never log all-zero ids:

```csharp
var act = Activity.Current;
if (act is not null && act.TraceId != default)
{
    // TraceId.ToHexString() => 32 hex, SpanId.ToHexString() => 16 hex
    LogContext.PushProperty("trace_id", act.TraceId.ToHexString());
    LogContext.PushProperty("span_id", act.SpanId.ToHexString());
    LogContext.PushProperty("trace_flags", ((int)act.ActivityTraceFlags).ToString("x2"));
}
LogContext.PushProperty("user_id", "u_9f3c2b7a");
```

## Timing helper for API / DB / AI calls

```csharp
using System.Diagnostics;

public static class Timed
{
    private static readonly ActivitySource Source = new("checkout-api");

    public static async Task<T> RunAsync<T>(
        string operation,
        (string Key, object Value)[] fields,
        Func<Task<T>> body)
    {
        var sw = Stopwatch.StartNew();               // monotonic
        using var activity = Source.StartActivity(operation);
        try
        {
            return await body();
        }
        finally
        {
            using (LogContext.PushProperty("duration_ms", sw.ElapsedMilliseconds))
            {
                foreach (var (k, v) in fields) LogContext.PushProperty(k, v);
                Log.Information(operation);
            }
        }
    }
}

// DB:
// await Timed.RunAsync("db query completed",
//   new[] {("db.system.name",(object)"postgresql"),("db.query.summary","SELECT orders")},
//   () => db.QueryAsync(sql));
// AI:
// await Timed.RunAsync("ai call completed",
//   new[] {("gen_ai.provider.name",(object)"openai"),("gen_ai.request.model","gpt-4o")},
//   () => client.GetChatCompletionAsync(...));
```

`Stopwatch` is monotonic — the right tool for durations.

## Propagation across services
`OpenTelemetry.Instrumentation.AspNetCore` + `.Http` inject and extract the W3C
`traceparent` header automatically across service and DB boundaries.
