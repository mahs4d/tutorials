# 15. Logging

## Overview

Logging is how your application tells you what it is doing, without you having to attach a debugger. In production, logs are often the *only* window you have into a running system, so getting logging right matters as much as getting business logic right. Spring Boot ships with a sensible logging setup out of the box, but interviewers love to probe whether you understand what is happening underneath: which library is actually writing the logs, how levels and configuration work, and how to trace a single request across multiple services. This chapter walks through the SLF4J/Logback stack, log levels, structured (JSON) logging, MDC, correlation IDs, and request logging, then closes with a list of pitfalls that show up constantly in code reviews.

## SLF4J

**SLF4J** (Simple Logging Facade for Java) is not a logging library — it is an **API** (a facade) that your code depends on. The actual work of writing log lines to a file, console, or network socket is done by an **implementation**, such as Logback or Log4j2. This split matters because it means your application code never has to change if the team decides to swap the underlying logging engine.

- **Facade** = SLF4J (`org.slf4j.Logger`, `org.slf4j.LoggerFactory`). This is what you code against.
- **Implementation** = Logback (Spring Boot's default), Log4j2, or java.util.logging (JUL) with a bridge.

| Concept | Role | Example |
|---|---|---|
| Facade (API) | Defines `Logger`, `LoggerFactory`, log methods | SLF4J |
| Implementation (engine) | Actually writes/formats/routes log lines | Logback, Log4j2 |
| Bridge | Redirects other logging APIs (JUL, Commons Logging) into SLF4J | `jul-to-slf4j` |

Spring Boot's starter (`spring-boot-starter-logging`) pulls in SLF4J + Logback by default, so in a typical project you get this for free.

### Getting a Logger

The classic, manual way:

```java
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    public void placeOrder(String orderId) {
        log.info("Placing order {}", orderId);
    }
}
```

With **Lombok**, the `@Slf4j` annotation generates that exact `private static final Logger log = ...` field for you at compile time, so you can skip the boilerplate:

```java
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

@Slf4j
@Service
public class OrderService {

    public void placeOrder(String orderId) {
        log.info("Placing order {}", orderId);
    }
}
```

| Approach | Pros | Cons |
|---|---|---|
| Manual `LoggerFactory.getLogger(X.class)` | Explicit, no extra dependency | One extra line per class, easy to copy-paste the wrong class name |
| Lombok `@Slf4j` | Zero boilerplate, always uses the correct class | Requires Lombok on the classpath and annotation processing enabled |

### Parameterized Logging vs String Concatenation

This is one of the most common interview questions about logging. Compare:

```java
// Bad: string concatenation
log.debug("Processing user " + userId + " with status " + status);

// Good: parameterized logging
log.debug("Processing user {} with status {}", userId, status);
```

Why parameterized logging wins:

- **Performance**: with concatenation, the `String` is built *every time*, even if the DEBUG level is disabled. With `{}` placeholders, SLF4J checks whether the level is enabled *before* doing any argument formatting — if DEBUG is off, the string is never built.
- **Readability**: the message template stays short and clear.
- **Safety**: no risk of a `NullPointerException` from string concatenation (`null + "text"` is actually safe in Java, but building complex chained calls is not).
- **Structured tooling**: log aggregators can more easily parse a template with placeholders than an arbitrary concatenated string.

You can pass multiple placeholders, and the last argument can be a `Throwable` to log a stack trace:

```java
try {
    paymentClient.charge(orderId);
} catch (PaymentException ex) {
    log.error("Payment failed for order {}", orderId, ex);
}
```

Note that `ex` is the **last** argument and is *not* one of the `{}` placeholders — SLF4J detects that the last argument is a `Throwable` and prints its stack trace automatically.

## Logback

**Logback** is the default logging implementation used by Spring Boot. It reads its configuration from `logback-spring.xml` (Spring-aware) or plain `logback.xml` (loaded before Spring context, so it cannot use `<springProfile>` or `<springProperty>`). Always prefer `logback-spring.xml` in a Spring Boot project.

Key building blocks:

- **Appender**: destination for log output (console, file, network socket, etc.).
- **Encoder/Layout**: formats each log event into text (or JSON).
- **Logger**: a named channel (usually matching a package or class) with its own level.
- **Root logger**: the fallback logger used when no more specific logger matches.

### Full `logback-spring.xml` Example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>

    <!-- Pull values from application.yml / environment -->
    <springProperty scope="context" name="appName" source="spring.application.name"/>

    <property name="LOG_DIR" value="logs"/>

    <!-- Console appender: human-readable pattern -->
    <appender name="CONSOLE" class="ch.qos.logback.core.ConsoleAppender">
        <encoder class="ch.qos.logback.classic.encoder.PatternLayoutEncoder">
            <pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} [%X{requestId}] - %msg%n</pattern>
        </encoder>
    </appender>

    <!-- Rolling file appender: one file per day, gzip old ones, keep 30 days -->
    <appender name="FILE" class="ch.qos.logback.core.rolling.RollingFileAppender">
        <file>${LOG_DIR}/${appName}.log</file>
        <rollingPolicy class="ch.qos.logback.core.rolling.TimeBasedRollingPolicy">
            <fileNamePattern>${LOG_DIR}/${appName}.%d{yyyy-MM-dd}.%i.log.gz</fileNamePattern>
            <maxFileSize>100MB</maxFileSize>
            <maxHistory>30</maxHistory>
            <totalSizeCap>3GB</totalSizeCap>
        </rollingPolicy>
        <encoder class="ch.qos.logback.classic.encoder.PatternLayoutEncoder">
            <pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} [%X{requestId}] - %msg%n</pattern>
        </encoder>
    </appender>

    <!-- Different behavior per Spring profile -->
    <springProfile name="local | dev">
        <root level="DEBUG">
            <appender-ref ref="CONSOLE"/>
        </root>
    </springProfile>

    <springProfile name="prod">
        <root level="INFO">
            <appender-ref ref="CONSOLE"/>
            <appender-ref ref="FILE"/>
        </root>
    </springProfile>

    <!-- Fine-tune noisy libraries regardless of profile -->
    <logger name="org.hibernate.SQL" level="WARN"/>
    <logger name="com.example.orders" level="DEBUG"/>

</configuration>
```

What each piece does:

- `<springProperty>` reads a value out of the Spring `Environment` (e.g., `application.yml`) so Logback config can stay in sync with app config.
- `<springProfile name="...">` activates a block only when that Spring profile is active — this is only possible in `logback-spring.xml`, not `logback.xml`.
- `RollingFileAppender` + `TimeBasedRollingPolicy` rotates files daily and caps total disk usage with `totalSizeCap`, preventing unbounded log growth.

### `application.yml` Equivalents

For simple cases, Spring Boot lets you skip a custom `logback-spring.xml` entirely and configure logging straight from `application.yml`:

```yaml
logging:
  level:
    root: INFO
    org.springframework.web: DEBUG
    com.example.orders: DEBUG
    org.hibernate.SQL: WARN
  pattern:
    console: "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} [%X{requestId}] - %msg%n"
    file: "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"
  file:
    name: logs/app.log
  logback:
    rollingpolicy:
      max-file-size: 100MB
      max-history: 30
      total-size-cap: 3GB
```

| Setting | Purpose |
|---|---|
| `logging.level.<logger-name>` | Set the level for a package/class; `root` is the fallback |
| `logging.pattern.console` / `logging.pattern.file` | Override the default log line format |
| `logging.file.name` | Enables file logging at this path (adds a rolling file appender automatically) |
| `logging.logback.rollingpolicy.*` | Tune rotation size/history/cap when using the default file appender |

Use `application.yml` for simple level tweaks; drop down to `logback-spring.xml` when you need multiple appenders, custom encoders, or JSON output.

## Log Levels

Log levels let you control *how much* gets written without changing code. From least to most severe:

| Level | Meaning | When to use |
|---|---|---|
| `TRACE` | Extremely fine-grained detail | Step-by-step internals, loop iterations — almost never enabled in production |
| `DEBUG` | Diagnostic detail useful while developing/troubleshooting | Variable values, branch decisions, "entering method X with args Y" |
| `INFO` | Notable business/application events | Application startup, a request completed, a scheduled job ran |
| `WARN` | Something unexpected happened but the app recovered | Retried a failed call, used a fallback/default, deprecated API usage |
| `ERROR` | An operation failed and needs attention | Uncaught exceptions, failed external calls with no fallback, data corruption |

Rules of thumb:

- Production defaults to `INFO` or `WARN` for most packages; `DEBUG`/`TRACE` are turned on temporarily to diagnose an issue.
- `ERROR` should be reserved for things that actually need a human to look at them — not for expected, handled situations like "user entered a wrong password" (that is normal application flow, usually `WARN` or even `INFO`).
- Each level is cumulative: setting a logger to `WARN` means `WARN` and `ERROR` are shown; `DEBUG` and below are hidden.

### Changing Levels at Runtime with Actuator

Spring Boot Actuator exposes a `/actuator/loggers` endpoint that lets you view and change log levels **without redeploying**. First, enable it:

```yaml
management:
  endpoints:
    web:
      exposure:
        include: loggers
```

Check the current level of a logger:

```bash
curl http://localhost:8080/actuator/loggers/com.example.orders
```

```json
{
  "effectiveLevel": "INFO"
}
```

Change it on the fly (great for debugging a live production issue for five minutes):

```bash
curl -X POST http://localhost:8080/actuator/loggers/com.example.orders \
  -H "Content-Type: application/json" \
  -d '{"configuredLevel": "DEBUG"}'
```

This is a huge operational win: you can turn on `DEBUG` for one misbehaving package, capture the extra detail, and turn it back off — all without a restart.

## Structured Logging

**Structured logging** means each log line is emitted as a well-defined data format (usually JSON) instead of free-form text. Structured logs are far easier for log aggregators (ELK, Loki, Datadog, Splunk) to parse, filter, and query, because every field (`timestamp`, `level`, `message`, `logger`, MDC values) is a named property instead of buried inside a sentence.

Plain text log line:

```
2026-08-07 10:15:32.101 [http-nio-8080-exec-1] INFO  c.e.orders.OrderService - Order placed orderId=A123
```

Equivalent structured (JSON) log line:

```json
{
  "@timestamp": "2026-08-07T10:15:32.101Z",
  "log.level": "INFO",
  "message": "Order placed",
  "log.logger": "com.example.orders.OrderService",
  "orderId": "A123",
  "service.name": "order-service"
}
```

### Option 1: Spring Boot 3.4+ Built-in Structured Logging

Since Spring Boot 3.4, structured JSON logging is built in — no extra dependency needed. Just set a format:

```properties
# ECS (Elastic Common Schema) format
logging.structured.format.console=ecs

# Or write structured JSON to the file instead of/as well as console
logging.structured.format.file=ecs
```

Supported built-in formats:

| Format value | Target system |
|---|---|
| `ecs` | Elastic Common Schema (Elasticsearch/Kibana) |
| `gelf` | Graylog Extended Log Format (Graylog) |
| `logstash` | Logstash JSON format |

```yaml
logging:
  structured:
    format:
      console: ecs
  level:
    root: INFO
```

This is the simplest path for new Spring Boot 3.4+ projects — flip one property and every log line becomes structured JSON, including MDC context automatically.

### Option 2: `logstash-logback-encoder`

For Spring Boot versions before 3.4, or when you need finer control over the JSON shape, use the popular `logstash-logback-encoder` library with Logback directly.

Add the dependency:

```xml
<dependency>
    <groupId>net.logstash.logback</groupId>
    <artifactId>logstash-logback-encoder</artifactId>
    <version>7.4</version>
</dependency>
```

Configure it as an encoder in `logback-spring.xml`:

```xml
<appender name="JSON_CONSOLE" class="ch.qos.logback.core.ConsoleAppender">
    <encoder class="net.logstash.logback.encoder.LogstashEncoder">
        <includeMdcKeyName>requestId</includeMdcKeyName>
        <includeMdcKeyName>traceId</includeMdcKeyName>
        <customFields>{"service":"order-service"}</customFields>
    </encoder>
</appender>

<root level="INFO">
    <appender-ref ref="JSON_CONSOLE"/>
</root>
```

Both approaches produce machine-parseable JSON; the built-in Boot 3.4+ option is easier to set up, while `logstash-logback-encoder` gives more control (custom fields, provider ordering, older Boot versions).

## MDC

**MDC** (Mapped Diagnostic Context) is a key-value map, tied to the **current thread**, that Logback/SLF4J automatically attaches to every log line printed from that thread. It is the standard way to add "context" — like a request ID or a user ID — to *every* log statement in a request without having to pass that value into every method call manually.

Basic usage:

```java
import org.slf4j.MDC;

MDC.put("userId", "u-42");
try {
    log.info("Starting checkout");   // this line automatically includes userId=u-42
    checkoutService.process();
} finally {
    MDC.remove("userId");            // always clean up
}
```

Reference `%X{key}` in your Logback pattern to print MDC values:

```xml
<pattern>%d{HH:mm:ss.SSS} [%thread] %-5level %logger{36} [userId=%X{userId}] - %msg%n</pattern>
```

### Why `try`/`finally` Is Mandatory

MDC data lives in a `ThreadLocal`. If you forget to remove a key, and the thread is later reused (which is exactly what happens in a thread pool, e.g., Tomcat's request-handling threads or an `@Async` executor), the *next*, unrelated request handled by that same thread will incorrectly inherit the old MDC values. This is a subtle, hard-to-reproduce bug — always pair `MDC.put` with a `finally` block that calls `MDC.remove` (or `MDC.clear()`).

```java
public void handle(String requestId) {
    MDC.put("requestId", requestId);
    try {
        doWork();
    } finally {
        MDC.remove("requestId"); // prevents leaking into the next request on this thread
    }
}
```

### Propagation into Thread Pools and `@Async`

MDC does **not** automatically cross thread boundaries. If a request thread puts `requestId` into MDC and then hands work off to an `@Async` method or an `ExecutorService`, the new worker thread starts with an empty MDC — unless you explicitly copy it over.

Spring lets you plug in a `TaskDecorator` that wraps each submitted task, copying the parent thread's MDC into the worker thread and cleaning it up afterward:

```java
import org.slf4j.MDC;
import org.springframework.core.task.TaskDecorator;

public class MdcTaskDecorator implements TaskDecorator {

    @Override
    public Runnable decorate(Runnable runnable) {
        Map<String, String> contextMap = MDC.getCopyOfContextMap();
        return () -> {
            try {
                if (contextMap != null) {
                    MDC.setContextMap(contextMap);
                }
                runnable.run();
            } finally {
                MDC.clear();
            }
        };
    }
}
```

Wire it into the async executor:

```java
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

@Configuration
@EnableAsync
public class AsyncConfig {

    @Bean
    public ThreadPoolTaskExecutor taskExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(4);
        executor.setMaxPoolSize(8);
        executor.setTaskDecorator(new MdcTaskDecorator());
        executor.initialize();
        return executor;
    }
}
```

Without this decorator, logs from `@Async` methods will simply be missing the `requestId`/correlation data — a very common source of "why doesn't this log have my request ID?" confusion.

## Correlation IDs

A **correlation ID** (also called a request ID or trace ID) is a unique identifier attached to a single request and carried through every log line, and ideally every downstream service call, so you can filter your log aggregator by that one ID and see the *entire* story of one request — even across microservices.

### A Custom Correlation ID Filter

A common pattern: read an incoming `X-Request-Id` header if present (so an upstream gateway's ID is preserved), otherwise generate a new UUID, store it in MDC for the duration of the request, and echo it back in the response header.

```java
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;

@Component
public class CorrelationIdFilter extends OncePerRequestFilter {

    private static final String HEADER_NAME = "X-Request-Id";
    private static final String MDC_KEY = "requestId";

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                     HttpServletResponse response,
                                     FilterChain filterChain) throws ServletException, IOException {
        String requestId = request.getHeader(HEADER_NAME);
        if (requestId == null || requestId.isBlank()) {
            requestId = UUID.randomUUID().toString();
        }

        MDC.put(MDC_KEY, requestId);
        response.setHeader(HEADER_NAME, requestId);

        try {
            filterChain.doFilter(request, response);
        } finally {
            MDC.remove(MDC_KEY); // critical: this thread will be reused for other requests
        }
    }
}
```

`OncePerRequestFilter` guarantees the filter runs exactly once per request even if it gets dispatched/forwarded internally, which avoids double-generating IDs.

### Micrometer Tracing: Built-in `traceId` / `spanId`

If you add Micrometer Tracing (`spring-boot-starter-actuator` + a tracing bridge like `micrometer-tracing-bridge-brave` or OpenTelemetry), Spring Boot automatically populates MDC with `traceId` and `spanId` for you — no custom filter needed for the basic case.

```xml
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-tracing-bridge-brave</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
```

Once this is on the classpath, your log pattern can reference `%X{traceId}` and `%X{spanId}` directly:

```xml
<pattern>%d{HH:mm:ss.SSS} [%thread] %-5level %logger{36} [traceId=%X{traceId}, spanId=%X{spanId}] - %msg%n</pattern>
```

| Approach | Scope | Best for |
|---|---|---|
| Custom `X-Request-Id` filter | Single logical request, simple string ID | Simple services, no distributed tracing backend |
| Micrometer Tracing `traceId`/`spanId` | Full distributed trace across services | Microservices with Zipkin/Tempo/Jaeger/OpenTelemetry backend |

In practice, many teams use both: a human-friendly `X-Request-Id` for support tickets and the tracing system's `traceId` for deep distributed-tracing analysis. The key point for an interview: correlation IDs only work if every service in the chain forwards the header downstream (e.g., when calling another service with `RestClient`/`WebClient`, propagate `X-Request-Id` onward).

## Request Logging

Request logging records metadata about incoming HTTP requests — method, URI, query string, client IP, status code, duration — which is invaluable for debugging and auditing, but it must be done carefully to avoid leaking sensitive data or drowning your logs.

### `CommonsRequestLoggingFilter`

Spring provides a ready-made filter for basic request logging:

```java
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.filter.CommonsRequestLoggingFilter;

@Configuration
public class RequestLoggingConfig {

    @Bean
    public CommonsRequestLoggingFilter requestLoggingFilter() {
        CommonsRequestLoggingFilter filter = new CommonsRequestLoggingFilter();
        filter.setIncludeQueryString(true);
        filter.setIncludeClientInfo(true);
        filter.setIncludeHeaders(false);   // headers can contain auth tokens - keep off by default
        filter.setIncludePayload(false);   // request bodies can contain sensitive data - keep off by default
        filter.setMaxPayloadLength(1000);
        filter.setAfterMessagePrefix("REQUEST DATA: ");
        return filter;
    }
}
```

This filter logs at `DEBUG` level by default under the `org.springframework.web.filter.CommonsRequestLoggingFilter` logger, so remember to enable it:

```yaml
logging:
  level:
    org.springframework.web.filter.CommonsRequestLoggingFilter: DEBUG
```

### A Custom Request Logging Filter (with timing)

For more control — e.g., logging status code and duration in one line — write your own filter:

```java
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Component
public class TimingRequestLoggingFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(TimingRequestLoggingFilter.class);

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                     HttpServletResponse response,
                                     FilterChain filterChain) throws ServletException, IOException {
        long start = System.currentTimeMillis();
        try {
            filterChain.doFilter(request, response);
        } finally {
            long durationMs = System.currentTimeMillis() - start;
            log.info("{} {} -> {} ({} ms)",
                    request.getMethod(),
                    request.getRequestURI(),
                    response.getStatus(),
                    durationMs);
        }
    }
}
```

### The Danger of Logging Bodies

Logging full request/response bodies feels convenient for debugging, but it is a common source of real security incidents:

- Request bodies for login, registration, and payment endpoints often contain **passwords, tokens, card numbers, or personal data (PII)**.
- Once that data is in a log file, it usually gets shipped to a log aggregator, retained for weeks/months, and accessible to a much wider set of people than the production database itself.
- Regulations like GDPR/PCI-DSS treat logs as data at rest — logging a card number can be a compliance violation, not just a bad practice.

Guidelines:

- Keep `includePayload`/body logging **off** by default; enable it only temporarily, scoped to non-sensitive endpoints, in non-production environments.
- If you must log part of a payload, log a redacted/allow-listed subset of fields, never the raw body.
- Prefer logging identifiers (e.g., `orderId`) over the data itself — you can always look up the full record from the ID.

## Common Code Review / Interview Pitfalls

- **String concatenation in log calls** — `log.debug("user=" + user)` builds the string even when DEBUG is disabled, wasting CPU on every call.
  - ❌ `log.debug("user=" + user);`
  - ✅ `log.debug("user={}", user);`

- **Logging PII, passwords, tokens, or card numbers** — sensitive data in logs gets copied to aggregators, retained long-term, and read by more people than should ever see it; can violate GDPR/PCI-DSS.
  - ❌ `log.info("Login attempt: user={}, password={}", user, password);`
  - ✅ `log.info("Login attempt for user={}", user);`

- **Log-and-rethrow duplication** — catching an exception, logging it, then rethrowing it causes the same error to be logged multiple times up the call stack, cluttering logs and making the real failure count unclear.
  - ❌ `catch (IOException e) { log.error("Failed", e); throw e; }` (repeated at every layer)
  - ✅ Log once at the boundary that actually handles/reports the error (e.g., a global exception handler), and let inner layers just throw.

- **`e.printStackTrace()`** — writes straight to `System.err`, bypassing SLF4J entirely: no log level, no file routing, no MDC context, easily lost in production.
  - ❌ `catch (Exception e) { e.printStackTrace(); }`
  - ✅ `catch (Exception e) { log.error("Order processing failed", e); }`

- **`System.out.println` for diagnostics** — same problem as above: unstructured, unlevelled, impossible to filter or ship to log aggregation.
  - ❌ `System.out.println("Processing order " + orderId);`
  - ✅ `log.info("Processing order {}", orderId);`

- **Passing the exception as a `{}` placeholder instead of the last argument** — SLF4J only prints a stack trace when the `Throwable` is the final vararg; passing it as a placeholder just prints its `toString()`.
  - ❌ `log.error("Failed: {}", ex);` (loses the stack trace)
  - ✅ `log.error("Failed to process order {}", orderId, ex);`

- **Wrong level: INFO spam** — logging routine, high-frequency events at `INFO` drowns out genuinely important messages and inflates storage costs.
  - ❌ `log.info("Cache hit for key {}", key);` (fires thousands of times per second)
  - ✅ `log.trace("Cache hit for key {}", key);` or don't log it at all.

- **Wrong level: ERROR for expected/handled cases** — using `ERROR` for normal business outcomes (e.g., "item not found", "invalid password") triggers false alarms and alert fatigue for on-call engineers.
  - ❌ `log.error("User not found: {}", userId);` (for a normal 404 lookup)
  - ✅ `log.warn("User not found: {}", userId);` or `log.info(...)` depending on how expected it is.

- **Missing MDC cleanup causing leaked context in pooled threads** — forgetting `MDC.remove`/`clear` in a `finally` block means the next request served by that pooled thread inherits stale correlation data, producing misleading correlated logs.
  - ❌ `MDC.put("requestId", id); handle(); ` (no cleanup)
  - ✅ `MDC.put("requestId", id); try { handle(); } finally { MDC.remove("requestId"); }`

- **No correlation ID in a microservice** — without a shared request/trace ID propagated across services, debugging a failure that spans multiple services means manually correlating timestamps across separate log streams — slow and error-prone.
  - ❌ Each service logs independently with no shared ID.
  - ✅ Generate/propagate `X-Request-Id` (or use Micrometer Tracing's `traceId`) and forward it on every outbound call.

- **Unbounded log files** — writing to a single ever-growing file with no rotation or retention policy eventually fills the disk and can crash the application.
  - ❌ A `FileAppender` with no rolling policy.
  - ✅ `RollingFileAppender` with `maxHistory` and `totalSizeCap` set, as shown in the Logback config above.

- **Logging inside tight loops** — logging once per iteration of a large loop (thousands/millions of iterations) can dominate CPU time and balloon log volume.
  - ❌ `for (Order o : millionsOfOrders) { log.info("Processing {}", o.getId()); }`
  - ✅ Log a summary before/after the loop, or log only every Nth iteration / only failures: `log.info("Processed {} orders, {} failed", total, failed);`

- **Not guarding expensive log arguments** — building an expensive-to-compute argument (e.g., serializing a large object to a string) even when the log level is disabled wastes work, because SLF4J's placeholder trick only helps with formatting, not with the cost of evaluating the arguments themselves.
  - ❌ `log.debug("Payload: {}", objectMapper.writeValueAsString(bigObject));` (serializes even when DEBUG is off)
  - ✅ `if (log.isDebugEnabled()) { log.debug("Payload: {}", objectMapper.writeValueAsString(bigObject)); }`

- **Logging full stack traces at scale without sampling** — in high-throughput systems, every exception logging its full stack trace can flood storage; consider deduplication/sampling for extremely frequent, identical errors.
  - ❌ Logging a full stack trace on every retry of a flaky call that fails thousands of times per minute.
  - ✅ Log the first occurrence with the full trace, then log a rate-limited summary ("failed 500 times in the last minute") for the rest.

## Quick Recap

- **SLF4J** is the facade (API) you code against; **Logback** is Spring Boot's default implementation that actually writes the logs.
- Use `@Slf4j` (Lombok) or `LoggerFactory.getLogger(X.class)` to get a logger; always use parameterized `{}` placeholders, never string concatenation.
- Pass the `Throwable` as the **last** argument, not as a `{}` placeholder, so the full stack trace gets printed.
- Configure Logback via `logback-spring.xml` (supports `<springProfile>` and `<springProperty>`) or simpler cases via `application.yml`'s `logging.level.*` / `logging.pattern.*` / `logging.file.name`.
- Levels from quietest to loudest: `TRACE < DEBUG < INFO < WARN < ERROR`; production usually runs at `INFO`/`WARN`, with `DEBUG` toggled on temporarily via the Actuator `/actuator/loggers` endpoint.
- **Structured logging** emits JSON instead of free text; Spring Boot 3.4+ supports it natively via `logging.structured.format.console=ecs|gelf|logstash`, or use `logstash-logback-encoder` for more control.
- **MDC** attaches context (like `requestId`) to every log line on the current thread; always pair `MDC.put` with `MDC.remove` in a `finally` block to avoid leaking context in pooled threads.
- MDC does not cross thread boundaries automatically — use a `TaskDecorator` to propagate it into `@Async`/executor threads.
- **Correlation IDs** (`X-Request-Id`) tie together all log lines for one request; a custom `OncePerRequestFilter` or Micrometer Tracing's built-in `traceId`/`spanId` can provide this.
- **Request logging** (`CommonsRequestLoggingFilter` or a custom filter) is useful for auditing and debugging, but never log full request/response bodies by default — they often contain PII, passwords, or payment data.
- Rotate and cap log files (`maxHistory`, `totalSizeCap`) to avoid filling up disk; avoid logging inside tight loops or at the wrong severity level.
