# Java Logging Setup

**Library:** Logback + `logstash-logback-encoder` (JSON), used via SLF4J.
(Log4j2 with `JsonTemplateLayout` is an accepted alternative — same field rules.)

**Dependencies:** `ch.qos.logback:logback-classic`,
`net.logstash.logback:logstash-logback-encoder`, plus the OpenTelemetry Java
agent (attach at startup) or `io.opentelemetry.instrumentation:opentelemetry-logback-mdc-1.0`.

## Logger bootstrap (JSON + level)

`logback.xml`:

```xml
<configuration>
  <appender name="JSON" class="ch.qos.logback.core.ConsoleAppender">
    <encoder class="net.logstash.logback.encoder.LogstashEncoder">
      <fieldNames>
        <timestamp>timestamp</timestamp>
        <level>level</level>
        <message>message</message>
        <logger>[ignore]</logger>
        <thread>[ignore]</thread>
      </fieldNames>
      <customFields>{"service":"checkout-api"}</customFields>
      <!-- pull trace context out of MDC into top-level fields -->
      <includeMdcKeyName>trace_id</includeMdcKeyName>
      <includeMdcKeyName>span_id</includeMdcKeyName>
      <includeMdcKeyName>trace_flags</includeMdcKeyName>
      <includeMdcKeyName>user_id</includeMdcKeyName>
      <includeMdcKeyName>duration_ms</includeMdcKeyName>
    </encoder>
  </appender>
  <root level="${LOG_LEVEL:-INFO}">
    <appender-ref ref="JSON"/>
  </root>
</configuration>
```

`${LOG_LEVEL:-INFO}` reads the `LOG_LEVEL` env var (Logback property
substitution), defaulting to `INFO`. On Spring Boot you can instead set
`LOGGING_LEVEL_ROOT=DEBUG`.

## Trace context (MDC)
The OTel Java agent / logback-mdc instrumentation populates MDC with `trace_id`,
`span_id`, `trace_flags` (canonical names). The encoder config above lifts them
to top-level JSON fields. No manual work when the agent is attached.

If setting MDC manually, only write when the span context is valid so you never
emit all-zero ids:

```java
Span span = Span.current();
SpanContext ctx = span.getSpanContext();
if (ctx.isValid()) {
    MDC.put("trace_id", ctx.getTraceId());
    MDC.put("span_id", ctx.getSpanId());
    MDC.put("trace_flags", ctx.getTraceFlags().asHex());
}
MDC.put("user_id", "u_9f3c2b7a");
```

## Timing helper for API / DB / AI calls

```java
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.Tracer;
import org.slf4j.MDC;

public final class Timed {
  private static final Logger log = LoggerFactory.getLogger("app");

  public static <T> T run(Tracer tracer, String operation,
                          Map<String, String> fields, Callable<T> body) throws Exception {
    long start = System.nanoTime();
    Span span = tracer.spanBuilder(operation).startSpan();
    try (var scope = span.makeCurrent()) {
      return body.call();
    } finally {
      long ms = (System.nanoTime() - start) / 1_000_000;
      fields.forEach(MDC::put);
      MDC.put("duration_ms", Long.toString(ms));
      log.info(operation);
      MDC.remove("duration_ms");
      fields.keySet().forEach(MDC::remove);
      span.end();
    }
  }
}
// DB:  Timed.run(tracer, "db query completed",
//        Map.of("db.system.name","postgresql","db.query.summary","SELECT orders"), () -> jdbc.query(sql));
// AI:  Timed.run(tracer, "ai call completed",
//        Map.of("gen_ai.provider.name","anthropic","gen_ai.request.model","claude-opus-4"), () -> client.call());
```

`System.nanoTime()` is monotonic. Prefer a structured-arguments approach
(`net.logstash.logback.argument.StructuredArguments.kv`) over MDC churn if you
log many per-call fields.

## Propagation across services
The OTel Java agent auto-instruments servlet/JAX-RS/HTTP clients/JDBC and
injects the W3C `traceparent` header. Don't build it manually.
