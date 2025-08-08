# 14. Spring Boot Actuator

## Overview

Spring Boot Actuator adds production-ready features to your application with almost no code. Once you add the dependency, your app exposes HTTP (or JMX) endpoints that report health, metrics, configuration, and internal state. Think of it as a built-in dashboard for operators: is the app alive, is the database reachable, how much memory is used, how many requests failed in the last minute? Without Actuator, you would have to hand-roll all of this — a `/health` endpoint, custom metrics collection, log-level toggles — for every service you write. With it, you get a consistent, well-tested foundation that plugs straight into Kubernetes probes, Prometheus, and Grafana. This chapter walks through the endpoints, the metrics library behind them (Micrometer), and how to wire everything into a real observability stack, plus the mistakes that get flagged in almost every code review.

## Health Endpoints

The **health endpoint** (`/actuator/health`) answers one question: "is this application working?" It aggregates individual **health indicators** — small checks for the database, disk space, message brokers, custom business logic — into one overall `UP` or `DOWN` status.

Add the starter dependency first:

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
```

By default, only `/actuator/health` and `/actuator/info` are exposed over HTTP. A basic response looks like this:

```json
{
  "status": "UP"
}
```

### Showing details

By default, Actuator hides the breakdown of individual checks (to avoid leaking internal details to random callers). You can turn details on:

```yaml
management:
  endpoint:
    health:
      show-details: always      # never | when-authorized | always
      show-components: always
```

With details on, a healthy app with a database and disk-space check looks like:

```json
{
  "status": "UP",
  "components": {
    "db": {
      "status": "UP",
      "details": {
        "database": "PostgreSQL",
        "validationQuery": "isValid()"
      }
    },
    "diskSpace": {
      "status": "UP",
      "details": {
        "total": 250685575168,
        "free": 116607946752,
        "threshold": 10485760,
        "exists": true
      }
    }
  }
}
```

### Health groups

**Health groups** let you expose a subset of indicators under a different path — this is exactly how Kubernetes liveness and readiness probes work in Spring Boot.

```yaml
management:
  endpoint:
    health:
      probes:
        enabled: true
      group:
        liveness:
          include: livenessState
        readiness:
          include: readinessState, db
```

This automatically creates two extra paths:

| Path | Purpose |
|---|---|
| `/actuator/health/liveness` | "Is the JVM process alive and not deadlocked?" Kubernetes restarts the pod if this fails. |
| `/actuator/health/readiness` | "Is the app ready to receive traffic?" Kubernetes removes the pod from the load balancer if this fails, but does **not** restart it. |

A matching Kubernetes deployment snippet:

```yaml
livenessProbe:
  httpGet:
    path: /actuator/health/liveness
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /actuator/health/readiness
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 5
```

**Liveness vs readiness in one sentence each:**

- Liveness: "Should this pod be killed and replaced?" Only fail this for unrecoverable states (deadlock, corrupted internal state).
- Readiness: "Should this pod receive traffic right now?" Fail this for temporary problems (database briefly unreachable, cache warming up) — the pod stays alive and can recover.

## Metrics

**Metrics** are numbers about your running application, collected over time: request counts, latencies, JVM memory, thread pool usage, database connection pool stats. Actuator exposes them through `/actuator/metrics`, backed by a library called Micrometer (covered in its own section below).

List all available metric names:

```bash
curl http://localhost:8080/actuator/metrics
```

```json
{
  "names": [
    "http.server.requests",
    "jvm.memory.used",
    "jvm.gc.pause",
    "process.cpu.usage",
    "hikaricp.connections.active",
    "logback.events"
  ]
}
```

Drill into one metric, optionally filtered by tag:

```bash
curl "http://localhost:8080/actuator/metrics/http.server.requests?tag=uri:/orders&tag=status:200"
```

```json
{
  "name": "http.server.requests",
  "measurements": [
    { "statistic": "COUNT", "value": 42 },
    { "statistic": "TOTAL_TIME", "value": 3.21 },
    { "statistic": "MAX", "value": 0.18 }
  ],
  "availableTags": [
    { "tag": "exception", "values": ["None"] },
    { "tag": "method", "values": ["GET"] }
  ]
}
```

## Info Endpoint

The **info endpoint** (`/actuator/info`) is a static-ish JSON blob for "what build is this, and what version?" It is empty by default — you decide what goes in it.

```properties
info.app.name=order-service
info.app.description=Handles order placement and fulfillment
management.info.env.enabled=true
```

To auto-populate build and git details, add the Maven/Gradle plugin that generates `build-info.properties` and `git.properties`:

```xml
<plugin>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-maven-plugin</artifactId>
    <executions>
        <execution>
            <goals>
                <goal>build-info</goal>
            </goals>
        </execution>
    </executions>
</plugin>
```

Resulting response:

```json
{
  "app": {
    "name": "order-service",
    "description": "Handles order placement and fulfillment"
  },
  "build": {
    "artifact": "order-service",
    "version": "1.4.2",
    "time": "2026-08-01T09:12:33.123Z"
  },
  "git": {
    "branch": "main",
    "commit": { "id": "a1b2c3d", "time": "2026-08-06T14:00:00Z" }
  }
}
```

You can also add custom info programmatically with an `InfoContributor` bean — useful for dynamic values like feature-flag state.

## Custom Health Indicators

You are not limited to the built-in checks (database, disk space, Redis, etc.). Implement `HealthIndicator` to add your own — for example, checking that a downstream payment gateway is reachable.

```java
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Component
public class PaymentGatewayHealthIndicator implements HealthIndicator {

    private final RestClient restClient;

    public PaymentGatewayHealthIndicator(RestClient.Builder builder) {
        this.restClient = builder.baseUrl("https://payments.internal").build();
    }

    @Override
    public Health health() {
        try {
            restClient.get()
                    .uri("/ping")
                    .retrieve()
                    .toBodilessEntity();
            return Health.up()
                    .withDetail("gateway", "payments.internal")
                    .build();
        } catch (RestClientException ex) {
            return Health.down(ex)
                    .withDetail("gateway", "payments.internal")
                    .build();
        }
    }
}
```

Spring Boot picks up any `HealthIndicator` bean automatically and registers it under a name derived from the bean name (here, `paymentGateway`). The result then shows up in `/actuator/health`:

```json
{
  "status": "UP",
  "components": {
    "paymentGateway": {
      "status": "UP",
      "details": { "gateway": "payments.internal" }
    }
  }
}
```

For reactive (WebFlux) applications, implement `ReactiveHealthIndicator` instead, returning a `Mono<Health>`.

**Important design rule:** health checks should be fast and should not call slow or flaky downstreams directly on every request (more on this in the pitfalls section). Prefer caching the result of an expensive check and refreshing it on a schedule.

## Micrometer

**Micrometer** is a vendor-neutral metrics facade — like SLF4J, but for metrics instead of logs. You write code against Micrometer's API, and it ships the data to whichever backend you plug in (Prometheus, Datadog, CloudWatch, New Relic, ...). Spring Boot auto-configures a `MeterRegistry` bean for you.

### The four core meter types

| Type | What it measures | Example |
|---|---|---|
| `Counter` | A value that only goes up | number of orders placed |
| `Gauge` | A value that goes up and down, sampled on read | active sessions, queue size |
| `Timer` | Count + total time of short recurring events | HTTP request duration |
| `DistributionSummary` | Count + total of event sizes (not time) | payload size in bytes |

### Using them in code

```java
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.DistributionSummary;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Service;

import java.util.concurrent.atomic.AtomicInteger;

@Service
public class OrderService {

    private final Counter ordersPlaced;
    private final Timer processingTimer;
    private final DistributionSummary payloadSize;
    private final AtomicInteger activeOrders = new AtomicInteger();

    public OrderService(MeterRegistry registry) {
        this.ordersPlaced = Counter.builder("orders.placed")
                .description("Total number of orders placed")
                .tag("channel", "web")
                .register(registry);

        this.processingTimer = Timer.builder("orders.processing.time")
                .description("Time to process an order")
                .publishPercentiles(0.5, 0.95, 0.99)
                .register(registry);

        this.payloadSize = DistributionSummary.builder("orders.payload.size")
                .baseUnit("bytes")
                .register(registry);

        registry.gauge("orders.active", activeOrders);
    }

    public void placeOrder(byte[] payload) {
        activeOrders.incrementAndGet();
        processingTimer.record(() -> {
            // ... business logic ...
            payloadSize.record(payload.length);
        });
        ordersPlaced.increment();
        activeOrders.decrementAndGet();
    }
}
```

### The `@Timed` annotation

Instead of manually wrapping code in a `Timer`, annotate a method or controller endpoint. This requires a `TimedAspect` bean (register it once):

```java
import io.micrometer.core.aop.TimedAspect;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class MetricsConfig {

    @Bean
    public TimedAspect timedAspect(MeterRegistry registry) {
        return new TimedAspect(registry);
    }
}
```

```java
import io.micrometer.core.annotation.Timed;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {

    @Timed(value = "orders.lookup.time", description = "Time to look up an order")
    @GetMapping("/orders/{id}")
    public String getOrder(String id) {
        return "order-" + id;
    }
}
```

### Common tags

**Tags** are key-value labels attached to a metric so you can slice it later (by endpoint, by status code, by region). Every `http.server.requests` metric automatically gets tags like:

| Tag | Example value | Meaning |
|---|---|---|
| `method` | `GET` | HTTP method |
| `uri` | `/orders/{id}` | Route template (**not** the raw path — this matters, see pitfalls) |
| `status` | `200` | HTTP status code |
| `exception` | `None` | Exception class thrown, if any |
| `outcome` | `SUCCESS` | Coarse-grained outcome bucket |

You can add global tags to every metric (e.g. `application`, `environment`) via configuration:

```yaml
management:
  metrics:
    tags:
      application: order-service
      environment: production
```

## Prometheus

**Prometheus** is an open-source monitoring system that periodically "scrapes" (pulls) metrics from your app over HTTP, stores them as time series, and lets you query them with **PromQL**. Spring Boot integrates with it through a Micrometer registry implementation.

### 1. Add the dependency

```xml
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>
```

This automatically adds a `/actuator/prometheus` endpoint that returns metrics in Prometheus's text exposition format.

### 2. Expose the endpoint

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health, info, prometheus, metrics
  endpoint:
    health:
      probes:
        enabled: true
```

A snippet of what `/actuator/prometheus` returns:

```
# HELP http_server_requests_seconds
# TYPE http_server_requests_seconds summary
http_server_requests_seconds_count{method="GET",status="200",uri="/orders/{id}"} 128
http_server_requests_seconds_sum{method="GET",status="200",uri="/orders/{id}"} 4.532
```

### 3. Configure Prometheus to scrape it

```yaml
scrape_configs:
  - job_name: 'order-service'
    metrics_path: '/actuator/prometheus'
    scrape_interval: 15s
    static_configs:
      - targets: ['order-service:8080']
```

### 4. Example PromQL queries

Request rate over the last 5 minutes, per status code:

```promql
sum by (status) (rate(http_server_requests_seconds_count[5m]))
```

95th percentile request latency (needs histogram buckets published via `management.metrics.distribution.percentiles-histogram.http.server.requests=true`):

```promql
histogram_quantile(0.95, sum by (le) (rate(http_server_requests_seconds_bucket[5m])))
```

JVM heap usage as a percentage of max:

```promql
jvm_memory_used_bytes{area="heap"} / jvm_memory_max_bytes{area="heap"}
```

## Grafana

**Grafana** is a dashboarding tool that reads from Prometheus (or other data sources) and renders graphs. It does not collect data itself — it visualizes what Prometheus already scraped. A good Spring Boot dashboard usually has three sections:

### RED metrics (per endpoint)

RED stands for **R**ate, **E**rrors, **D**uration — the three numbers that tell you if a service is healthy from the outside.

| Metric | PromQL sketch | What it tells you |
|---|---|---|
| Rate | `rate(http_server_requests_seconds_count[5m])` | Requests per second |
| Errors | `rate(http_server_requests_seconds_count{status=~"5.."}[5m])` | Failure rate |
| Duration | `histogram_quantile(0.95, ...)` | p50/p95/p99 latency |

### JVM panels

- Heap and non-heap memory usage (`jvm_memory_used_bytes`)
- Garbage collection pause time (`jvm_gc_pause_seconds`)
- Thread count, especially blocked/waiting threads
- CPU usage (`process_cpu_usage`)

### Connection pool panels (HikariCP)

Spring Boot's default connection pool, HikariCP, publishes its own metrics — these catch database bottlenecks before they become outages:

| Metric | Meaning |
|---|---|
| `hikaricp_connections_active` | Connections currently in use |
| `hikaricp_connections_idle` | Connections sitting free in the pool |
| `hikaricp_connections_pending` | Threads waiting for a connection — non-zero for long means the pool is too small |
| `hikaricp_connections_timeout_total` | Requests that gave up waiting |

A common starting point is to import a pre-built community dashboard (e.g. "JVM (Micrometer)" dashboard ID `4701` on grafana.com) and add a HikariCP row on top.

## Tracing

**Tracing** (distributed tracing) follows a single request as it hops across services, so you can see where time was spent — this call spent 200ms in the order service, then 150ms waiting on the payment service, then 5ms in the database. Each hop is a **span**; all spans for one request share a **trace ID**.

Spring Boot 3 replaced the older **Spring Cloud Sleuth** project (now end-of-life) with **Micrometer Tracing**, which plays the same "facade" role for tracing that Micrometer plays for metrics. You pick a **bridge** dependency depending on which tracer implementation you want underneath:

```xml
<!-- Core tracing facade -->
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-tracing</artifactId>
</dependency>

<!-- Option A: OpenTelemetry bridge (recommended for new projects) -->
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-tracing-bridge-otel</artifactId>
</dependency>
<dependency>
    <groupId>io.opentelemetry</groupId>
    <artifactId>opentelemetry-exporter-zipkin</artifactId>
</dependency>

<!-- Option B: Brave bridge (Zipkin's native tracer) -->
<!--
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-tracing-bridge-brave</artifactId>
</dependency>
<dependency>
    <groupId>io.zipkin.reporter2</groupId>
    <artifactId>zipkin-reporter-brave</artifactId>
</dependency>
-->
```

| Bridge | Underlying tracer | Typical export target |
|---|---|---|
| `micrometer-tracing-bridge-otel` | OpenTelemetry | OTLP collector, Tempo, Jaeger, Zipkin |
| `micrometer-tracing-bridge-brave` | Brave (Zipkin's tracer) | Zipkin |

### Sampling

Tracing every single request is expensive (storage and CPU overhead), so most setups only trace a percentage — this is **sampling**.

```yaml
management:
  tracing:
    sampling:
      probability: 0.1   # trace 10% of requests
  zipkin:
    tracing:
      endpoint: http://zipkin:9411/api/v2/spans
```

`1.0` means "trace everything" — fine for local development or low-traffic services, risky for high-throughput production traffic (see pitfalls).

### Manual spans with `@Observed`

Spring Boot's `@Observed` annotation creates both a metric and a trace span for a method in one shot, using Micrometer's `Observation` API under the hood.

```java
import io.micrometer.observation.ObservationRegistry;
import io.micrometer.observation.annotation.Observed;
import io.micrometer.observation.aop.ObservedAspect;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.stereotype.Service;

@Configuration
class ObservationConfig {

    @Bean
    ObservedAspect observedAspect(ObservationRegistry registry) {
        return new ObservedAspect(registry);
    }
}

@Service
class ShippingService {

    @Observed(name = "shipping.calculate",
              contextualName = "calculate-shipping-cost",
              lowCardinalityKeyValues = {"carrier", "ups"})
    public double calculateCost(String destination) {
        // ... business logic ...
        return 12.50;
    }
}
```

Every span carries a trace ID and span ID, which get injected into logs automatically (Spring Boot adds `traceId` and `spanId` to the MDC), making it easy to jump from a log line straight into the matching trace in your tracing backend.

## Common Code Review / Interview Pitfalls

- **`management.endpoints.web.exposure.include=*` in production.** This exposes every Actuator endpoint, including `env`, `heapdump`, and `shutdown`, to anyone who can reach the app. Fix: explicitly list only what you need (`health, info, metrics, prometheus`), and never include `*` outside local development.
- **Actuator running on the same port as the app, with no security.** If `/actuator/**` shares the app's public port and isn't protected, an attacker can read internal state or trigger a shutdown. Fix: run management endpoints on a separate port (`management.server.port`) that is only reachable inside the cluster, and/or secure them with Spring Security rules.
- **`show-details: always` on a public endpoint.** This leaks internal architecture (database vendor, disk paths, downstream hostnames) to any caller. Fix: use `when-authorized` so only authenticated/admin callers see details, or restrict the endpoint's network exposure entirely.
- **`/actuator/heapdump` and `/actuator/env` left exposed.** A heap dump can contain passwords, session tokens, and PII sitting in memory; `env` can print raw environment variables including secrets not properly masked. Fix: never expose these on public-facing endpoints; if needed for debugging, require strong authentication and pull them through a secure internal channel only.
- **High-cardinality tags blowing up the metrics registry.** Tagging a metric with a raw user ID, order ID, or an un-templated URL (`/orders/12345` instead of `/orders/{id}`) creates a new time series per unique value — this can crash Prometheus or your metrics backend (a phenomenon called a "cardinality explosion"). Fix: only tag with low-cardinality values (status code, method, route template, region); use logs or traces, not metric tags, for per-request identifiers.
- **Health checks that call slow downstreams synchronously, cascading failures.** If your health check makes a live network call to a flaky downstream on every hit, a slow downstream makes your own health check slow or timeout, which can make an orchestrator kill an otherwise-healthy pod. Fix: keep health checks fast and local where possible; for downstream checks, cache the result and refresh asynchronously on a schedule rather than checking inline.
- **Confusing liveness with readiness.** Failing liveness on a temporary condition (e.g. database briefly down) causes Kubernetes to restart the pod repeatedly, which does not fix the database and just adds churn. Fix: put transient dependency checks in the readiness group only; reserve liveness for truly unrecoverable states like deadlocks.
- **100% trace sampling in production.** `management.tracing.sampling.probability: 1.0` under real traffic generates enormous span volume, straining the collector and increasing storage cost, sometimes without much extra debugging value. Fix: sample a small percentage (often 1-10%) in production, and consider tail-based sampling or temporarily raising the rate only while investigating an incident.
- **Assuming Sleuth still applies.** Spring Cloud Sleuth is end-of-life for Spring Boot 3; code and StackOverflow answers referencing `spring-cloud-starter-sleuth` are outdated. Fix: use Micrometer Tracing with an OTel or Brave bridge instead.
- **Forgetting to add a registry dependency and expecting `/actuator/prometheus` to exist.** Actuator's metrics collection (Micrometer core) is separate from any specific backend exporter; without `micrometer-registry-prometheus` on the classpath, the Prometheus endpoint simply does not appear, and there's no error to point at why. Fix: add the exporter dependency matching your backend, and confirm the endpoint is both registered and included in `exposure.include`.
- **Treating `/actuator/metrics` as the production integration point.** It's a human-browsable JSON API, not built for continuous scraping at scale. Fix: use the `/actuator/prometheus` (or equivalent) endpoint for automated collection; use `/actuator/metrics` only for ad hoc debugging.
- **No authentication on Actuator endpoints that mutate state.** Endpoints like `/actuator/shutdown` or `/actuator/loggers` (which can change log levels, or even restart parts of the app) are disabled by default but sometimes get force-enabled without adding access control. Fix: keep write-capable endpoints disabled unless truly needed, and require authentication when they are enabled.
- **Ignoring `management.endpoint.health.show-components` vs `show-details`.** Teams sometimes assume disabling one hides all internal information, when the other still leaks component names or statuses. Fix: understand that `show-components` controls whether the breakdown by component appears at all, while `show-details` controls whether each component's `details` map is populated — check both settings together.

## Quick Recap

- Actuator gives you production endpoints (health, metrics, info, and more) with almost no code.
- `/actuator/health` aggregates `HealthIndicator` beans into one `UP`/`DOWN` status; use health groups to expose `/liveness` and `/readiness` for Kubernetes.
- Liveness = "restart me if I fail this." Readiness = "stop sending me traffic if I fail this, but don't restart."
- Write custom checks by implementing `HealthIndicator` (or `ReactiveHealthIndicator` for WebFlux); keep them fast, cache slow downstream checks.
- `/actuator/info` is a static blob you populate yourself, often with build/git info from the Maven/Gradle plugin.
- Micrometer is the vendor-neutral metrics facade behind Actuator; core meter types are `Counter`, `Gauge`, `Timer`, and `DistributionSummary`.
- `@Timed` and `@Observed` add metrics/tracing to a method with one annotation, backed by `TimedAspect` / `ObservedAspect` beans.
- Never tag metrics with high-cardinality values like user IDs or raw URLs — use route templates and low-cardinality dimensions.
- Prometheus scrapes `/actuator/prometheus` (needs `micrometer-registry-prometheus`); query the data with PromQL; only expose the endpoints you actually need via `management.endpoints.web.exposure.include`.
- Grafana visualizes what Prometheus collected — build dashboards around RED metrics, JVM health, and HikariCP pool stats.
- Spring Boot 3 uses Micrometer Tracing (not Sleuth, which is EOL) with an OTel or Brave bridge; control cost with `management.tracing.sampling.probability`.
- Lock down Actuator in production: separate management port, authentication, no `*` exposure, no `show-details: always` on public endpoints.
