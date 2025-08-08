# 21. Observability

## Overview

Observability is the ability to ask questions about a running system without shipping new code to answer them. It rests on three pillars: **metrics** (numbers over time, like CPU usage or request count), **logs** (timestamped text records of discrete events), and **traces** (the end-to-end journey of a single request through multiple services). Monitoring tells you *that* something is wrong — a dashboard turns red, an alert fires. Observability helps you figure out *why*, by letting you explore data you didn't know you'd need in advance. In a monolith, a debugger and a log file were often enough. In a distributed system with dozens of microservices, you need metrics, logs, and traces working together, correlated by IDs, to reconstruct what happened. This chapter covers the Spring Boot tools that produce and export that data.

## Micrometer

Micrometer is often described as **"SLF4J for metrics."** SLF4J gives you one logging API and lets you plug in Logback, Log4j2, or another backend without changing your code. Micrometer does the same for metrics: you instrument your code once against a vendor-neutral API, and Micrometer ships the data to Prometheus, Datadog, New Relic, CloudWatch, or any other backend via a "registry" implementation. Spring Boot Actuator uses Micrometer internally, so if you have `spring-boot-starter-actuator` on the classpath, Micrometer is already there.

The central object is the `MeterRegistry`. It is a factory and container for **meters** — the individual instruments that record data.

```java
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Service;

@Service
public class OrderService {

    private final MeterRegistry registry;

    public OrderService(MeterRegistry registry) {
        this.registry = registry;
    }

    public void placeOrder(String region) {
        // increment a counter every time this runs, tagged by region
        registry.counter("orders.placed", "region", region).increment();
    }
}
```

### Meter types

| Meter | What it measures | Example |
|---|---|---|
| `Counter` | A value that only goes up | Number of orders placed, errors thrown |
| `Gauge` | A value that goes up and down, sampled on read | Active DB connections, queue size |
| `Timer` | Duration + count of short events | HTTP request latency |
| `DistributionSummary` | Distribution of a value that isn't time | Payload size in bytes, items per order |
| `LongTaskTimer` | Duration of *in-flight* long-running tasks | A batch job that is still running right now |

The difference between `Timer` and `LongTaskTimer`: a `Timer` only records a duration once the task finishes. A `LongTaskTimer` can tell you "this job has already been running for 40 minutes" *while it is still running* — useful for catching stuck jobs.

### Tags and dimensions

A tag (or "dimension") is a key/value label attached to a meter. Instead of creating a separate metric name for every variant (`orders.placed.eu`, `orders.placed.us`), you create one metric name with a tag: `orders.placed{region="eu"}`. This lets you slice and aggregate the same metric many ways in your dashboard. But tags are also the number one way people accidentally break their metrics backend — more on that later.

```java
Timer.builder("http.client.requests")
    .tag("method", "GET")
    .tag("outcome", "SUCCESS")
    .register(registry)
    .record(() -> callDownstream());
```

### `@Timed` and `@Counted`

For simple cases, annotations save you from writing manual `MeterRegistry` code:

```java
import io.micrometer.core.annotation.Timed;
import io.micrometer.core.annotation.Counted;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class CatalogController {

    @Timed(value = "catalog.lookup", description = "Time spent looking up a product")
    @Counted(value = "catalog.lookup.count")
    @GetMapping("/products/{id}")
    public Product getProduct(String id) {
        return catalogService.find(id);
    }
}
```

These annotations do **nothing** by themselves — they need an AOP aspect registered to intercept the method call. This is a very common gotcha (see the pitfalls section).

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

### The Micrometer Observation API

Spring Boot 3 introduced the **Micrometer Observation API**, which sits a level above plain meters. An `Observation` represents "one thing happening" — a method call, an HTTP request, a database query — and a single `@Observed` annotation (or manual `Observation` call) can simultaneously:

- record a `Timer` and a `Counter` (metrics), **and**
- start and close a tracing `Span` (traces), **and**
- write structured log context.

This is the glue that unifies metrics and tracing in Spring Boot 3 — you instrument once, and both a metric and a trace span come out.

```java
import io.micrometer.observation.ObservationRegistry;
import io.micrometer.observation.annotation.Observed;
import org.springframework.stereotype.Service;

@Service
public class PaymentService {

    @Observed(name = "payment.process",
              contextualName = "processing-payment",
              lowCardinalityKeyValues = {"provider", "stripe"})
    public void process(Payment payment) {
        // business logic
    }
}
```

```java
import io.micrometer.observation.Observation;
import io.micrometer.observation.ObservationRegistry;

public class ManualObservationExample {

    private final ObservationRegistry observationRegistry;

    ManualObservationExample(ObservationRegistry observationRegistry) {
        this.observationRegistry = observationRegistry;
    }

    void doWork() {
        Observation.createNotStarted("do.work", observationRegistry)
            .observe(() -> {
                // this block is timed, counted, and traced
                heavyLifting();
            });
    }
}
```

An `ObservationHandler` is a plug-in that reacts to observation start/stop events — this is exactly how `micrometer-tracing-bridge-otel` and Micrometer's metrics registries hook into the same observation without your business code knowing either exists.

## Distributed Tracing

Picture a relay race where each runner hands off a baton with a note attached: "this baton belongs to race #482, and I am leg 2 of 4." Distributed tracing works the same way. A single user request — say, "place an order" — might touch an API gateway, an order service, a payment service, and an inventory service. **Distributed tracing** stitches all of those individual hops into one connected picture.

Key vocabulary:

- **Trace**: the entire journey of one request across every service it touches. Identified by a `traceId`.
- **Span**: one unit of work within that trace — e.g., "order-service handles POST /orders" or "payment-service calls the card processor." Each span has its own `spanId`.
- **Parent span**: the span that triggered a child span. This is how the tool reconstructs the call tree (gateway → order-service → payment-service, not a flat list).
- **Trace context**: the small bundle of IDs (`traceId`, `spanId`, sampling flag) that gets passed from one service to the next so they can all agree they're part of the same trace.

### The W3C `traceparent` header

Trace context needs a standard wire format so that services written in different frameworks (or different companies) can understand each other's traces. The W3C Trace Context specification defines the `traceparent` HTTP header:

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             │  │                                │                │
             │  trace-id (32 hex chars)           parent span-id   sampled flag
          version
```

- `00` — the format version.
- `4bf92f3577b34da6a3ce929d0e0e4736` — the trace ID, shared by every span in this trace.
- `00f067aa0ba902b7` — the ID of the span that made this call (becomes the "parent" for the next hop).
- `01` — a flag saying "yes, record this trace" (sampled) vs `00` (not sampled).

Micrometer Tracing and OpenTelemetry both understand this header out of the box, so a Spring Boot service talking to a Node.js service talking to a Go service can still produce one unified trace.

### Context propagation

Propagation just means "carrying the trace context along as the request moves."

- **Over HTTP**: the `traceparent` header is added to outgoing requests automatically by instrumented `RestTemplate` / `WebClient` / `RestClient` clients, and read from incoming requests by an instrumented servlet filter.
- **Over messaging** (Kafka, RabbitMQ): there's no HTTP header, so the trace context is stuffed into message headers/metadata instead. Micrometer Tracing's Kafka/RabbitMQ instrumentation does this automatically when the relevant starter is on the classpath.
- **Across thread pools**: this is the one propagation *doesn't* happen automatically for. If you hand work off to an `ExecutorService` or a reactive scheduler without using a context-aware wrapper, the trace context can silently get lost, and the child work shows up as a brand-new, disconnected trace.

### Sampling strategies

Recording every single span for every single request is expensive at scale (storage, network, backend cost). **Sampling** decides which traces to actually keep.

| Strategy | How it works | Trade-off |
|---|---|---|
| Head-based, probabilistic | Decide at the very first span whether to sample (e.g., 10% of requests), and that decision travels with the trace | Cheap and simple, but you might sample away the 1% of requests that errored |
| Rate-limiting | Sample at most N traces per second | Predictable cost regardless of traffic spikes |
| Tail-based | Buffer all spans for a trace, decide *after* it finishes (e.g., always keep traces with errors or high latency) | Much more useful data, but needs a collector that can buffer and decide — more infrastructure |

A very common real-world setup: 100% sampling in a staging environment, 1–10% in production, plus a rule that always keeps traces that contain an error or exceed a latency threshold.

### A trace across three services

```
Client
  │
  │  GET /orders/42          traceId=abc123
  ▼
┌───────────────────────────── gateway-service ─────────────────────────────┐
│ span: gateway-handle-request          [0ms ─────────────────── 180ms]     │
│   │                                                                       │
│   │ traceparent: 00-abc123-span01-01                                     │
│   ▼                                                                       │
│  ┌─────────────────────── order-service ───────────────────────────┐     │
│  │ span: order-lookup            [10ms ───────────── 150ms]         │     │
│  │   │                                                              │     │
│  │   │ traceparent: 00-abc123-span02-01                             │     │
│  │   ▼                                                              │     │
│  │  ┌───────────── payment-service ─────────────┐                  │     │
│  │  │ span: charge-card   [20ms ────── 100ms]    │                  │     │
│  │  └─────────────────────────────────────────────┘                  │     │
│  └────────────────────────────────────────────────────────────────┘     │
└───────────────────────────────────────────────────────────────────────────┘
```

One `traceId` (`abc123`), three spans, each with a parent-child relationship. A tracing UI turns this into a "waterfall" view so you can see exactly which hop was slow.

## OpenTelemetry

**OpenTelemetry (OTel)** is a vendor-neutral, CNCF-hosted standard for producing and exporting observability data — metrics, traces, and logs. It has four pieces worth knowing:

- **Specification**: the rules — what a trace looks like, what a span attribute is, how context propagates.
- **SDKs**: language libraries (Java, Go, Python, …) that implement the spec so applications can produce the data.
- **Collector**: a standalone process that receives telemetry, can transform/filter/batch it, and forwards it to one or more backends.
- **OTLP** (OpenTelemetry Protocol): the wire format everything speaks, so the SDK, the Collector, and the backend all agree on how data is packaged.

Think of OTel as the "universal power adapter" for observability: instrument once, and plug into Zipkin, Jaeger, Prometheus, Datadog, or anything else that speaks OTLP, without rewriting instrumentation.

### Two ways to get OTel into a Spring Boot app

**1. The Micrometer bridge (recommended for Spring Boot).** You keep writing Micrometer/Observation API code, and a "bridge" dependency translates that into OpenTelemetry's data model under the hood.

```xml
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-tracing-bridge-otel</artifactId>
</dependency>
<dependency>
    <groupId>io.opentelemetry</groupId>
    <artifactId>opentelemetry-exporter-otlp</artifactId>
</dependency>
```

```yaml
management:
  tracing:
    sampling:
      probability: 0.1        # sample 10% of traces in production
  otlp:
    tracing:
      endpoint: http://otel-collector:4318/v1/traces
```

**2. The OpenTelemetry Java agent (auto-instrumentation).** A `-javaagent` you attach at JVM startup with zero code changes. It bytecode-instruments common libraries (Spring MVC, JDBC drivers, Kafka clients, etc.) for you.

```bash
java -javaagent:opentelemetry-javaagent.jar \
     -Dotel.service.name=order-service \
     -Dotel.exporter.otlp.endpoint=http://otel-collector:4318 \
     -jar order-service.jar
```

| | Micrometer bridge | OTel Java agent |
|---|---|---|
| Setup | Add dependencies, use Spring config | Attach a `-javaagent` flag, no code changes |
| Fits Spring idioms | Yes — same `Observation`/`MeterRegistry` API you already use | It's separate from your Spring code |
| Custom spans | Easy, via `@Observed` / `Observation` | Needs OTel's own SDK API calls |
| Typical use case | You're already invested in Spring Boot + Micrometer | Polyglot shops, or "just get tracing working today" |

### The Collector's role

Rather than every service pushing telemetry directly to Zipkin, Jaeger, Prometheus, *and* your APM vendor, each service sends OTLP data to one nearby **OpenTelemetry Collector**. The Collector then batches, filters, redacts sensitive fields, does tail-based sampling, and fans the data out to as many backends as you want — all without touching application code again.

```
Spring Boot app ──OTLP──▶ OTel Collector ──┬──▶ Jaeger (traces)
                                             ├──▶ Prometheus (metrics)
                                             └──▶ Datadog / vendor backend
```

## Zipkin

Zipkin is one of the original open-source distributed tracing systems (originally from Twitter). It stores traces and gives you a UI to search and visualize them.

Spring apps traditionally exported to Zipkin via the "Brave" tracer:

```xml
<dependency>
    <groupId>io.zipkin.reporter2</groupId>
    <artifactId>zipkin-reporter-brave</artifactId>
</dependency>
```

```yaml
management:
  tracing:
    sampling:
      probability: 1.0
  zipkin:
    tracing:
      endpoint: http://localhost:9411/api/v2/spans
```

Modern setups instead point Zipkin's own OTLP-compatible collector endpoint, or send OTLP to a Collector that forwards to Zipkin — this avoids depending on Zipkin's own reporter library at all.

Running Zipkin locally with Docker is a one-liner:

```bash
docker run -d -p 9411:9411 openzipkin/zipkin
```

Then open `http://localhost:9411`. The Zipkin UI lets you:

- search traces by service name, operation name, tags, or duration
- see a waterfall/timeline view of every span in a trace
- see a dependency graph of which services call which

## Jaeger

Jaeger (originally from Uber, now a CNCF graduated project) solves the same problem as Zipkin: store and visualize distributed traces. It has native OTLP ingestion, so a Spring Boot app using the Micrometer OTel bridge can point straight at Jaeger with no translation layer needed.

```bash
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 -p 4318:4318 \
  jaegertracing/all-in-one:latest
```

```yaml
management:
  otlp:
    tracing:
      endpoint: http://localhost:4318/v1/traces
```

Open `http://localhost:16686` for the UI — trace search, waterfall views, and a service dependency graph, very similar in spirit to Zipkin's.

### Zipkin vs Jaeger

| | Zipkin | Jaeger |
|---|---|---|
| Origin | Twitter | Uber |
| Native protocol | Zipkin format (v1/v2 spans) | OTLP-native |
| Storage backends | In-memory, MySQL, Cassandra, Elasticsearch | Cassandra, Elasticsearch, or Badger (embedded) |
| CNCF status | Not a CNCF project | CNCF graduated project |
| Typical fit today | Simpler, smaller setups, legacy Brave-based apps | Larger setups, cloud-native/Kubernetes environments, teams standardizing on OTel |
| UI features | Trace search, dependency graph | Trace search, dependency graph, trace comparison |

In practice, both are increasingly used purely as a UI + storage layer behind an OpenTelemetry Collector, so the "which one" question matters less than it used to.

## Health Monitoring

Actuator's `/actuator/health` endpoint is the classic entry point, but "health" means different things depending on who's asking.

- **Liveness**: "Is this process alive, or is it deadlocked/crashed and needs to be killed and restarted?" Kubernetes uses this to decide when to restart a pod.
- **Readiness**: "Is this instance currently able to serve traffic?" A perfectly alive JVM might still not be ready — e.g., it's still warming up a cache or its database connection pool isn't up yet. Kubernetes uses this to decide whether to route traffic to a pod.

```yaml
management:
  endpoint:
    health:
      probes:
        enabled: true
  health:
    livenessstate:
      enabled: true
    readinessstate:
      enabled: true
```

```json
GET /actuator/health/readiness

{
  "status": "UP",
  "components": {
    "db": { "status": "UP" },
    "diskSpace": { "status": "UP" }
  }
}
```

A liveness check should almost never depend on a downstream system (a flaky database shouldn't get your app container killed). A readiness check *should* depend on things you need to actually serve requests.

### Synthetic checks

A synthetic check is a scripted, fake "user" that periodically exercises a real flow from the outside — e.g., a scheduled job that logs in and places a test order every 5 minutes. Unlike internal health checks, synthetic checks catch problems that only show up end-to-end: a broken DNS record, an expired TLS cert, a misconfigured load balancer.

### SLI / SLO / error budgets

- **SLI (Service Level Indicator)**: a measured number, e.g., "percentage of requests under 300ms" or "percentage of successful checkout requests."
- **SLO (Service Level Objective)**: the target for that indicator, e.g., "99.9% of checkout requests succeed over a rolling 30 days."
- **Error budget**: the allowed amount of failure implied by the SLO. A 99.9% SLO over 30 days allows roughly 43 minutes of full downtime (or equivalent partial degradation). Once the budget is spent, the team's priority shifts from shipping features to shoring up reliability.

### Alert on symptoms, not causes

Alert on things a user would notice — elevated error rate, high latency, failed checkouts — not on every internal wobble.

```promql
# good: alert on the symptom users feel
sum(rate(http_server_requests_seconds_count{status=~"5.."}[5m]))
  /
sum(rate(http_server_requests_seconds_count[5m])) > 0.01
```

```promql
# noisy: alerting on a "cause" metric directly, with no user impact threshold
jvm_gc_pause_seconds_max > 0.5
```

A GC pause alone might mean nothing to a user if latency stayed fine. Page on the symptom (latency/error rate), then use traces and cause-level metrics (like GC pauses) *during investigation*, not as the trigger.

## Metrics Collection

There are two fundamentally different ways metrics data gets from your app into a time-series database: the backend **pulls** it, or the app **pushes** it.

| | Pull (e.g., Prometheus) | Push (e.g., OTLP, StatsD) |
|---|---|---|
| Who initiates | The metrics server scrapes an HTTP endpoint on a timer | The app actively sends data out |
| App's job | Expose current values on `/actuator/prometheus` | Ship data to a collector/endpoint |
| Works well for | Long-lived services with a stable network address | Short-lived jobs, serverless functions, batch tasks that might finish before a scrape happens |
| Firewall/NAT friendliness | Server needs network access *to* the app | App just needs outbound access |
| Spring Boot dependency | `micrometer-registry-prometheus` | `micrometer-registry-otlp` / StatsD registry |

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health, prometheus, metrics
  metrics:
    export:
      prometheus:
        enabled: true
```

```properties
# scrape config lives on the Prometheus server, not the app, e.g. prometheus.yml
scrape_configs:
  - job_name: order-service
    metrics_path: /actuator/prometheus
    static_configs:
      - targets: ['order-service:8080']
```

### Cardinality budgets

**Cardinality** is the number of unique combinations of tag values a metric can have. `http.requests{method, status}` has low cardinality — a handful of HTTP methods times a handful of status codes. `http.requests{userId, orderId}` has *unbounded* cardinality — a new unique time series is created for every distinct user and order that ever exists. Time-series databases allocate real memory and disk per unique series, so unbounded cardinality metrics can crash or bankrupt a metrics backend. Treat cardinality like a budget: decide up front which tags are safe (bounded, known set of values — region, HTTP method, status class) and which must never become tags (user IDs, order IDs, raw request paths with path variables inlined).

### Histograms vs client-side percentiles

A **percentile** (e.g., p99 latency) answers "what's the latency that 99% of requests are faster than?" The tempting-but-wrong approach is to compute that percentile *inside each app instance* and export a single number. The problem: **percentiles cannot be mathematically averaged or combined** across instances. If instance A reports p99=100ms and instance B reports p99=120ms, there is no way to derive the fleet-wide p99 from those two numbers alone — a percentile is already a lossy summary.

The fix is to export a **histogram** — bucketed counts of how many requests fell into each latency range — and let the backend (e.g., Prometheus) compute percentiles *after* aggregating raw bucket counts across all instances.

```yaml
management:
  metrics:
    distribution:
      percentiles-histogram:
        http.server.requests: true
      minimum-expected-value:
        http.server.requests: 1ms
      maximum-expected-value:
        http.server.requests: 5s
```

```promql
histogram_quantile(0.99,
  sum(rate(http_server_requests_seconds_bucket[5m])) by (le)
)
```

### RED and USE methods

Two popular "what should I even measure" checklists:

| Method | Best for | Tracks |
|---|---|---|
| **RED** | Request-driven services (APIs) | **R**ate (requests/sec), **E**rrors (error rate), **D**uration (latency) |
| **USE** | Resources (CPU, disk, DB connections, queues) | **U**tilization (% busy), **S**aturation (how much work is queued), **E**rrors (error count) |

### Retention and downsampling

Storing every raw data point forever is expensive and mostly useless — nobody needs second-by-second CPU usage from 14 months ago. **Downsampling** rolls old, high-resolution data into coarser aggregates (e.g., 10-second resolution for the last day, 5-minute resolution after a week, hourly after a month). **Retention** policies define how long data is kept at all before being deleted. Configure both at the metrics backend level (Prometheus, Mimir, Thanos, a hosted vendor), not in application code.

## Common Code Review / Interview Pitfalls

- **High-cardinality tags** — putting `userId`, `orderId`, session IDs, or a raw un-templated URI (`/orders/12345` instead of `/orders/{id}`) into a metric tag creates a new time series per unique value and can crash or blow the budget of your metrics backend.
- **100% sampling in production** — fine in a demo or in staging, but at real traffic volumes it's expensive storage and network overhead for marginal debugging benefit; use probabilistic or tail-based sampling instead.
- **No trace ID in logs** — if your log lines don't include the current `traceId`/`spanId`, you can't jump from "I found this error in the logs" to "let me see the full trace" — always configure your logging pattern to include them (Micrometer Tracing does this via MDC automatically when configured).
- **Still using Spring Cloud Sleuth** — Sleuth is end-of-life; Micrometer Tracing (with the Brave or OTel bridge) is its replacement in Spring Boot 3. Seeing Sleuth dependencies in a Boot 3 codebase is a red flag.
- **Alerting on every metric instead of SLOs** — an alert for every dashboard graph produces alert fatigue; page on user-facing symptom breaches (error rate, latency SLO burn), not on every internal fluctuation.
- **Metrics only recorded on the success path** — e.g., incrementing a counter after a successful call but never on the `catch` block, so your error rate looks artificially low and outages go unnoticed.
- **`MeterRegistry.counter(...)`/`.timer(...)` lookups inside hot loops** — calling `registry.counter("name", "tag", value)` on every iteration re-resolves the meter from a map each time; look it up once (e.g., in a constructor or as a field) and reuse the reference.
- **Inconsistent custom metric naming** — mixing `order_count`, `orders.count`, and `OrdersCount` across a codebase makes dashboards and dimensional queries painful; agree on a naming convention (Micrometer's dot-notation, e.g., `orders.placed`) and stick to it.
- **Ignoring the observability bill** — traces, metrics, and logs all cost money to store and transmit; unbounded retention or unsampled tracing in a large fleet can quietly become one of the biggest line items in a cloud bill.
- **Using `@Timed` without registering `TimedAspect`** — the annotation is silently a no-op if there's no `TimedAspect` bean wired up; a very common "why isn't this metric showing up" interview trap.
- **Traces that stop at an async/thread-pool boundary** — handing work to a raw `ExecutorService`, `@Async` method, or a manually created thread without a context-propagating wrapper drops the trace context, so the async work appears as an unconnected, orphaned trace instead of a child span.
- **Treating liveness and readiness as the same thing** — wiring a database check into the liveness probe means a slow database can get a perfectly healthy app container killed and restarted in a loop, making the outage worse.
- **Client-side-only percentiles** — exporting a pre-computed p99 per instance instead of a histogram means you cannot correctly aggregate latency across instances; always prefer `percentiles-histogram: true` and compute quantiles at query time.
- **No correlation between logs, metrics, and traces** — collecting all three pillars but never linking them via a shared trace/span ID defeats much of the point of observability; correlation is what turns "three separate tools" into one investigative flow.

## Quick Recap

- Three pillars: **metrics** (numbers over time), **logs** (event records), **traces** (a request's journey across services). Observability lets you ask new questions later; monitoring just tells you something broke.
- **Micrometer** is the vendor-neutral metrics facade ("SLF4J for metrics"); `MeterRegistry` creates `Counter`, `Gauge`, `Timer`, `DistributionSummary`, and `LongTaskTimer` meters, tagged with dimensions.
- The **Observation API** (`ObservationRegistry`, `@Observed`) unifies metrics and tracing in one instrumentation point in Spring Boot 3.
- `@Timed`/`@Counted` need a registered `TimedAspect` bean to actually do anything.
- **Distributed tracing** links spans across services into one trace via propagated context; the **W3C `traceparent`** header is the standard wire format.
- **OpenTelemetry** is the vendor-neutral standard (spec + SDK + Collector + OTLP); use `micrometer-tracing-bridge-otel` for Spring-idiomatic instrumentation, or the OTel Java agent for zero-code auto-instrumentation.
- **Zipkin** and **Jaeger** are trace storage/UI backends; both increasingly accept OTLP directly, reducing the need for tracer-specific reporters like Brave.
- **Liveness** = "is the process alive?"; **readiness** = "can it serve traffic right now?" Don't couple liveness to downstream dependencies.
- **SLIs/SLOs/error budgets** turn "is it healthy" into a measurable, agreed target; alert on symptoms (user-facing breaches), not every internal cause metric.
- **Pull** (Prometheus scrape) vs **push** (OTLP/StatsD) are the two metrics collection models; pick push for short-lived/serverless workloads.
- Watch your **cardinality budget** — never tag metrics with user IDs, order IDs, or raw URIs.
- Export **histograms**, not client-side percentiles — percentiles can't be aggregated after the fact.
- **RED** (Rate, Errors, Duration) for services; **USE** (Utilization, Saturation, Errors) for resources.
- Sleuth is dead — Micrometer Tracing is its successor in Spring Boot 3.
