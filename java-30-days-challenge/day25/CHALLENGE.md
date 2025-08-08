# Day 25: Observability: Metrics & Tracing

| | |
|---|---|
| 🏗️ **Project** | **MetricsLab** — a Micrometer/Prometheus-instrumented service |
| ☕ **Java & language skills** | Micrometer instrumentation (Counter/Timer/Gauge), @Timed, Actuator config, MDC trace ids |
| 🧰 **Library / tool** | Micrometer + Spring Boot Actuator + Prometheus (& Grafana) |
| 🗄️ **DB / distributed-systems concept** | Observability — metrics/logs/traces, RED/USE, percentiles, distributed tracing |
| 📊 **Difficulty** | Medium |

---

## Concept primer

Back on **Day 9** you exposed a few Actuator endpoints and looked at JVM/pool metrics, and on **Days 15 & 24** you emitted cache hit/miss stats and circuit-breaker state. Those were all *metrics*. Today we zoom out: how do you actually *operate* a distributed system, know it's healthy, and debug it at 3am when one downstream is slow?

### The three pillars

| Pillar  | Question it answers | Cost / cardinality | Example |
|---------|--------------------|--------------------|---------|
| **Metrics** | "Is the system healthy *right now*? What's the trend?" | Cheap, aggregated, low cardinality | "p99 order-create latency is 800ms, up from 200ms" |
| **Logs**    | "What *exactly* happened for this one request/event?" | Expensive at volume, high cardinality | "Order 42 failed: payment declined (code 51)" |
| **Traces**  | "*Where* did the time go across services?" | Medium; usually **sampled** | "Request spent 700ms in `payment-svc`, 50ms in `inventory-svc`" |

They are complementary. Metrics tell you *that* something is wrong and *when*; traces tell you *where*; logs tell you *why*. The senior skill is moving between them fast — and that requires **correlation** (a shared `traceId` stitching a metric spike → a trace → the relevant log lines). We wire that up today.

### What to measure: RED and USE

You can't instrument everything, and a wall of meaningless graphs is worse than none. Two complementary recipes:

- **RED** (for *request-driven services* — your REST/Kafka consumers):
  - **R**ate — requests per second.
  - **E**rrors — failed requests per second (and as a ratio).
  - **D**uration — latency distribution (a histogram, *not* an average).
- **USE** (for *resources* — CPU, memory, connection pools, thread pools, disk):
  - **U**tilization — % of time the resource was busy.
  - **S**aturation — how much queued/extra work is waiting (e.g. pending pool acquisitions from Day 9, Kafka consumer lag from Day 18).
  - **E**rrors — error events for that resource.

For an HTTP endpoint, Spring already gives you RED via the `http.server.requests` `Timer` (rate, error tags, and a duration histogram). For *your business* (orders created, payments declined) you add custom meters — which is most of today's coding.

### Why averages lie — percentiles and histograms

Suppose 99 requests take 10ms and 1 request takes 10,000ms. The **mean** is ~110ms — a number experienced by *zero* of your users. The 1% who hit 10s are the ones who rage-quit, retry (amplifying load), and open tickets. Averages hide the **tail**, and the tail is where outages live.

So you measure **percentiles**:
- **p50** (median) — the typical experience.
- **p95 / p99** — the unlucky-but-not-rare experience; what your SLOs target.
- **p99.9 / max** — the worst case; matters because one slow dependency in a fan-out of 100 calls makes the *whole* request slow (tail amplification).

To compute percentiles *server-side and aggregatably*, Micrometer/Prometheus use **histograms**: latency is bucketed (≤5ms, ≤10ms, ≤25ms, …), each bucket is a counter, and `histogram_quantile()` interpolates a percentile from the buckets. This is mergeable across instances — you can compute a fleet-wide p99, which you **cannot** do by averaging per-instance p99s (a classic rookie mistake). Micrometer exposes this via `publishPercentileHistogram()`.

> Two distinct things, often confused: **client-side percentiles** (`publishPercentiles(0.95, 0.99)`) are computed *in the JVM* and cannot be re-aggregated across instances. **Histogram buckets** (`publishPercentileHistogram()`) ship raw buckets so Prometheus aggregates correctly. For multi-instance services, prefer histograms.

### The cardinality trap

A Micrometer meter is `name` + a set of **tag** key/value pairs. Each *unique combination* of tag values is a separate time series stored forever in your TSDB. Tagging by something **unbounded** — `userId`, `orderId`, `email`, raw URL with path params, exception message with embedded IDs — multiplies series into the millions and OOMs/bankrupts your metrics backend. This is the #1 way teams break Prometheus.

Rule: **tags must be low-cardinality and bounded** (`status=2xx|4xx|5xx`, `method=GET|POST`, `region=eu|us`, `uri=/orders/{id}` *templated*). High-cardinality identifiers belong in **logs** and **traces**, not metric tags.

### Distributed tracing & context propagation — the *why*

A single user action ("place order") may touch `order-svc → payment-svc → inventory-svc → kafka`. Metrics say "p99 is up"; they can't tell you *which hop*. A **trace** is the end-to-end story of one request, made of **spans** (one timed unit of work per service/operation), arranged as a tree by parent/child relationships.

For spans in different services to join the *same* trace, the **trace context** must travel with the request. This is **context propagation**: the caller injects headers (W3C `traceparent`, or B3 `X-B3-TraceId`/`X-B3-SpanId`) into the outbound HTTP request / Kafka record; the callee extracts them and continues the trace. Each service generates a child `spanId` but keeps the shared `traceId`.

That same `traceId` is the **correlation ID** you put into every log line. Remember the **MDC** (Mapped Diagnostic Context) work from **Day 4** — Micrometer Tracing populates the MDC with `traceId`/`spanId` automatically, so a single grep/filter pulls every log line for one request across every service. That's the magic moment: metric spike → click the exemplar → open the trace → jump to the correlated logs.

Today we instrument *one* service end-to-end. Cross-service propagation is the same mechanism repeated; Day 30's capstone ties multiple services together.

---

## Prerequisites

- The **orders app** from prior days (Spring Boot 3.x, Java 17+, Maven from Day 1).
- **Docker** (for Prometheus, as on Days 16/18/23).
- Spring Boot 3 uses **Micrometer 1.x** and **Micrometer Tracing** (the successor to Spring Cloud Sleuth). Tracing bridges to Brave (Zipkin) or OpenTelemetry.

### Maven dependencies

Add to `pom.xml`. (Versions are managed by the Spring Boot BOM you already inherit, so no explicit versions needed.)

```xml
<!-- Actuator: the /actuator/* endpoints (Day 9) -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>

<!-- Micrometer -> Prometheus registry: exposes /actuator/prometheus -->
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>

<!-- @Timed annotation support + general AOP -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-aop</artifactId>
</dependency>

<!-- Micrometer Tracing facade + Brave (Zipkin) bridge: trace/span ids -->
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-tracing-bridge-brave</artifactId>
</dependency>

<!-- Optional: ship spans to a Zipkin collector -->
<dependency>
    <groupId>io.zipkin.reporter2</groupId>
    <artifactId>zipkin-reporter-brave</artifactId>
</dependency>

<!-- Optional but recommended: link metrics histograms to traces via exemplars -->
<dependency>
    <groupId>io.prometheus</groupId>
    <artifactId>prometheus-metrics-tracer-otel</artifactId>
    <scope>runtime</scope>
</dependency>
```

> If you prefer the OpenTelemetry path instead of Brave, swap `micrometer-tracing-bridge-brave` for `micrometer-tracing-bridge-otel` + `opentelemetry-exporter-otlp`. Same Micrometer API, different wire protocol. See the senior notes.

---

## 🛠️ Project Walkthrough — MetricsLab

Roll up your sleeves: follow the numbered steps below to instrument the orders service end-to-end and watch the metrics, traces, and correlated logs light up.

---

## Step 1 — Configure Actuator to expose Prometheus & enable tracing

`src/main/resources/application.yml`:

```yaml
spring:
  application:
    name: orders-svc          # becomes a common tag -> distinguishes services in Prometheus/Grafana

management:
  endpoints:
    web:
      exposure:
        include: health, info, metrics, prometheus   # add prometheus to what we exposed on Day 9
  endpoint:
    health:
      show-details: when_authorized
  metrics:
    tags:
      application: ${spring.application.name}   # common tag on every meter
    distribution:
      # Turn http.server.requests into a real histogram so PromQL can compute p99 fleet-wide
      percentiles-histogram:
        http.server.requests: true
        orders.create.latency: true             # our custom timer (defined below)
      # Optional client-side percentiles (per-instance, NOT aggregatable across instances)
      percentiles:
        http.server.requests: 0.5, 0.95, 0.99
      # Sane SLO buckets so histogram_quantile interpolates well near our targets
      slo:
        http.server.requests: 50ms, 100ms, 200ms, 400ms, 800ms, 1s, 2s
  tracing:
    sampling:
      probability: 1.0        # sample 100% in dev; lower in prod (e.g. 0.1) to control cost
  zipkin:
    tracing:
      endpoint: http://localhost:9411/api/v2/spans   # only needed if running a Zipkin collector
```

> `percentiles-histogram: true` ships Prometheus `_bucket` series. `slo:` (a.k.a. service-level-objective boundaries) adds explicit bucket edges around the latencies you care about, so quantile interpolation is accurate near your targets instead of guessing between coarse default buckets.

---

## Step 2 — Custom Counter and Timer with tags

We'll instrument the order-creation flow: a **Counter** for orders (tagged by outcome) and a **Timer** wrapping the service call (so we get a duration histogram for p99).

`OrderMetrics.java` — a small holder so meters are created once and reused (creating a meter per call is fine — Micrometer dedups by name+tags — but pre-registering is cleaner and lets you set distribution config in code):

```java
package com.example.orders.observability;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

import java.time.Duration;

@Component
public class OrderMetrics {

    private final MeterRegistry registry;
    private final Timer createTimer;

    public OrderMetrics(MeterRegistry registry) {
        this.registry = registry;

        // A Timer records counts + total time + a latency distribution.
        this.createTimer = Timer.builder("orders.create.latency")
                .description("Time taken to create an order")
                .tag("module", "orders")
                .publishPercentileHistogram()                 // ship _bucket series for PromQL p99
                .serviceLevelObjectives(                       // explicit SLO buckets
                        Duration.ofMillis(50), Duration.ofMillis(100),
                        Duration.ofMillis(200), Duration.ofMillis(500),
                        Duration.ofSeconds(1))
                .register(registry);
    }

    /** Counter, dimensioned by a LOW-cardinality outcome tag. */
    public void countOrder(String outcome) {            // outcome in {created, rejected, failed}
        Counter.builder("orders.created")
                .description("Total number of order-create attempts by outcome")
                .tag("outcome", outcome)                 // bounded set -> safe cardinality
                .register(registry)
                .increment();
    }

    public Timer createTimer() {
        return createTimer;
    }
}
```

> Note the **cardinality discipline**: we tag by `outcome` (3 values), never by `orderId` or `customerId`.

---

## Step 3 — Use the meters in the service

`OrderService.java`:

```java
package com.example.orders.service;

import com.example.orders.observability.OrderMetrics;
import io.micrometer.core.annotation.Timed;
import io.micrometer.core.instrument.Timer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

@Service
public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    private final OrderMetrics metrics;

    public OrderService(OrderMetrics metrics) {
        this.metrics = metrics;
    }

    /**
     * @Timed auto-creates a Timer named "orders.service.process" with method/exception/status tags.
     * Requires a TimedAspect bean (Step 4). Great for "just time this method".
     */
    @Timed(value = "orders.service.process", description = "process() execution time",
           histogram = true, extraTags = {"module", "orders"})
    public Order createOrder(CreateOrderRequest req) {
        // Timer.record(...) gives explicit control + lets us tag the timer by outcome if desired.
        Timer.Sample sample = Timer.start();   // start manual timing
        try {
            Order order = doCreate(req);        // ... business logic: persist, reserve stock, etc.
            metrics.countOrder("created");
            log.info("Order created id={} total={}", order.getId(), order.getTotal());
            return order;
        } catch (BusinessRejectedException e) {
            metrics.countOrder("rejected");
            log.warn("Order rejected: {}", e.getMessage());
            throw e;
        } catch (RuntimeException e) {
            metrics.countOrder("failed");
            log.error("Order failed", e);       // traceId/spanId auto-added to this line (Step 6)
            throw e;
        } finally {
            // Stop the manual Timer regardless of outcome.
            sample.stop(metrics.createTimer());
        }
    }

    private Order doCreate(CreateOrderRequest req) {
        // simulate work / downstream calls
        return new Order(/* ... */);
    }
}
```

Two styles shown deliberately:
- `@Timed` — declarative, zero boilerplate, ideal for "time this method." Needs the aspect bean below.
- `Timer.Sample` + `timer.record()` — imperative, gives you the `finally` hook and freedom to choose which timer/tags to stop into.

There's also a third (functional) style: `timer.record(() -> doCreate(req))`.

---

## Step 4 — Enable `@Timed` and `@Counted` aspects

`MetricsConfig.java`:

```java
package com.example.orders.observability;

import io.micrometer.core.aop.CountedAspect;
import io.micrometer.core.aop.TimedAspect;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class MetricsConfig {

    @Bean
    public TimedAspect timedAspect(MeterRegistry registry) {
        return new TimedAspect(registry);    // makes @Timed work (needs spring-boot-starter-aop)
    }

    @Bean
    public CountedAspect countedAspect(MeterRegistry registry) {
        return new CountedAspect(registry);  // makes @Counted work
    }
}
```

> A `Gauge` (the third meter type) is for an instantaneous, fluctuating value you *observe* rather than increment — e.g. queue depth from Day 4 or pool active-connections from Day 9. Register it once, bound to a supplier:
> ```java
> Gauge.builder("orders.queue.depth", orderQueue, Queue::size)
>      .description("Pending orders in the in-memory queue")
>      .register(registry);
> ```
> Micrometer polls the supplier at scrape time. A `DistributionSummary` is like a Timer but for non-time quantities (e.g. order amount in dollars) — same percentile/histogram machinery.

---

## Step 5 — Run Prometheus in Docker and scrape the app

`prometheus.yml`:

```yaml
global:
  scrape_interval: 5s          # scrape every 5s (dev-friendly; prod is usually 15-30s)
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'orders-svc'
    metrics_path: '/actuator/prometheus'    # the endpoint Micrometer exposes
    static_configs:
      # host.docker.internal lets the container reach the app on the host (Linux: add extra_hosts)
      - targets: ['host.docker.internal:8080']
        labels:
          env: 'local'
```

`docker-compose.yml` (Prometheus + optional Grafana + optional Zipkin):

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"   # needed on Linux for host.docker.internal

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    depends_on:
      - prometheus

  zipkin:
    image: openzipkin/zipkin:latest
    container_name: zipkin
    ports:
      - "9411:9411"                            # where Micrometer Tracing ships spans
```

Run it:

```bash
# 1. start the app
./mvnw spring-boot:run

# 2. start the observability stack
docker compose up -d

# 3. generate some traffic (vary it so percentiles are interesting)
for i in $(seq 1 200); do
  curl -s -X POST localhost:8080/orders \
    -H 'Content-Type: application/json' \
    -d '{"sku":"ABC","qty":'"$((RANDOM % 5 + 1))"'}' > /dev/null
done

# 4. confirm the raw metrics endpoint
curl -s localhost:8080/actuator/prometheus | grep -E 'orders_(created|create_latency)'
```

What to look at:
- **`http://localhost:8080/actuator/prometheus`** — raw Prometheus exposition text.
- **`http://localhost:9090/targets`** — Prometheus should show `orders-svc` as **UP**.
- **`http://localhost:9090/graph`** — run the PromQL below.
- **`http://localhost:9411`** — Zipkin trace UI (if enabled).
- **`http://localhost:3000`** — Grafana; add Prometheus (`http://prometheus:9090`) as a data source.

### Expected output (`/actuator/prometheus`)

```
# HELP orders_created_total Total number of order-create attempts by outcome
# TYPE orders_created_total counter
orders_created_total{application="orders-svc",outcome="created",} 187.0
orders_created_total{application="orders-svc",outcome="rejected",} 11.0
orders_created_total{application="orders-svc",outcome="failed",} 2.0

# HELP orders_create_latency_seconds Time taken to create an order
# TYPE orders_create_latency_seconds histogram
orders_create_latency_seconds_bucket{application="orders-svc",module="orders",le="0.05",} 142.0
orders_create_latency_seconds_bucket{application="orders-svc",module="orders",le="0.1",}  171.0
orders_create_latency_seconds_bucket{application="orders-svc",module="orders",le="0.2",}  190.0
orders_create_latency_seconds_bucket{application="orders-svc",module="orders",le="0.5",}  199.0
orders_create_latency_seconds_bucket{application="orders-svc",module="orders",le="1.0",}  200.0
orders_create_latency_seconds_bucket{application="orders-svc",module="orders",le="+Inf",} 200.0
orders_create_latency_seconds_count{application="orders-svc",module="orders",} 200.0
orders_create_latency_seconds_sum{application="orders-svc",module="orders",}   18.34
```

Note the cumulative `_bucket` series (`le` = "less than or equal"), plus `_count` and `_sum`. Those three give Prometheus everything it needs for rate, average, and quantiles.

---

## Step 6 — Enable trace & span IDs in logs

Micrometer Tracing auto-instruments Spring MVC/WebClient/Kafka, creating a span per request and putting `traceId`/`spanId` into the **MDC** (the same MDC mechanism from Day 4). You just need a log pattern that prints them.

`application.yml`:

```yaml
logging:
  pattern:
    # %5p = level; the brackets carry app name + correlation ids
    level: "%5p [${spring.application.name:},%X{traceId:-},%X{spanId:-}]"
```

`%X{traceId}` reads the MDC value Micrometer Tracing populated. Now every log line during a request shows the same `traceId`:

```
2026-06-16 10:21:04.512  INFO [orders-svc,3f9a1c2b7e8d4f10,a1b2c3d4e5f60718] c.e.o.service.OrderService : Order created id=42 total=129.90
2026-06-16 10:21:04.515  WARN [orders-svc,3f9a1c2b7e8d4f10,c0ffee00d00dface] c.e.o.service.OrderService : Order rejected: out of stock
```

Same `traceId` across log lines (and, across services, across machines) = grep one ID, get the whole request story. When this app calls another service via Spring's `RestClient`/`WebClient`, Micrometer Tracing **injects** the `traceparent`/B3 headers automatically; the downstream **extracts** them and continues the trace. That's context propagation working for free.

> To create a manual span around a chunk of work:
> ```java
> Span span = tracer.nextSpan().name("reserve-inventory").start();
> try (Tracer.SpanInScope ws = tracer.withSpan(span)) {
>     inventoryClient.reserve(sku, qty);     // child span, propagated downstream
> } finally {
>     span.end();
> }
> ```

---

## PromQL examples

Run these in the Prometheus graph UI (`:9090`). All use the metrics we just emitted.

```promql
# 1. RATE — order-create attempts per second, by outcome, over the last 1m
rate(orders_created_total[1m])

# 2. ERRORS — failure ratio over the last 5m (errors / total)
sum(rate(orders_created_total{outcome=~"failed|rejected"}[5m]))
  /
sum(rate(orders_created_total[5m]))

# 3. DURATION p99 — the headline number. histogram_quantile over the bucket rate.
#    Must group by the "le" label and use a *rate* of the buckets.
histogram_quantile(
  0.99,
  sum(rate(orders_create_latency_seconds_bucket[5m])) by (le)
)

# 4. p50 vs p95 vs p99 side by side (paste each as its own query / panel)
histogram_quantile(0.50, sum(rate(orders_create_latency_seconds_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(orders_create_latency_seconds_bucket[5m])) by (le))

# 5. The lie of averages: average latency = sum / count (compare to p99 above)
rate(orders_create_latency_seconds_sum[5m])
  /
rate(orders_create_latency_seconds_count[5m])

# 6. HTTP RED for the whole app (Spring's built-in timer), p99 per URI
histogram_quantile(
  0.99,
  sum(rate(http_server_requests_seconds_bucket[5m])) by (le, uri)
)

# 7. HTTP error rate (5xx) as a fraction of all requests
sum(rate(http_server_requests_seconds_count{status=~"5.."}[5m]))
  /
sum(rate(http_server_requests_seconds_count[5m]))
```

Run query 3 and query 5 side by side: watch the average sit comfortably low while p99 spikes during the slow tail. That contrast *is* the lesson.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **OpenTelemetry (OTel) is the convergence point.** Brave/Zipkin is the simplest bridge, but the industry standard is now OTel: a vendor-neutral API + SDK + the **OTLP** wire protocol, plus the **OTel Collector** (a sidecar/daemon that receives, batches, transforms, and fans out telemetry to any backend). For new systems, prefer `micrometer-tracing-bridge-otel` + `opentelemetry-exporter-otlp` and push to a Collector. Micrometer's **Observation API** (`ObservationRegistry`) lets you emit a metric *and* a span from a single instrumentation point — the future-proof way to instrument.

- **Exemplars link metrics → traces.** With the Prometheus exemplar support enabled, each histogram bucket can carry an example `traceId` for a request that landed in it. In Grafana you click the dot on the p99 latency graph and jump straight to the exact slow trace. This collapses the "metric spike → which request?" gap that used to require log spelunking. This is the single highest-leverage observability upgrade once basics work.

- **SLI / SLO / error budgets.** An **SLI** is a measured indicator (e.g. "fraction of order-creates < 500ms"). An **SLO** is the target (e.g. "99.5% over 30 days"). The **error budget** is `1 - SLO` (0.5% may be slow/failed). Budgets turn reliability into a number you can *spend*: budget remaining → ship features; budget exhausted → freeze and fix. Compute SLIs directly from the histograms above (`good events / total events`), and alert on **burn rate** (how fast you're consuming the budget) rather than instantaneous thresholds — far fewer false pages.

- **High-cardinality dangers (revisited, because it bites everyone).** Every unique tag-value combination is a stored time series with memory + indexing cost. `userId`/`orderId`/`sessionId`/unbounded exception messages/un-templated URLs are cardinality bombs. Spring templates URIs (`/orders/{id}`) for exactly this reason — never tag with the raw path. Put high-cardinality detail in **logs and trace attributes**, which are designed for it. If you must explore high-cardinality dimensions, that's a job for a tracing/wide-events backend (Honeycomb-style), not Prometheus.

- **Log sampling & cost.** Logs are the most expensive pillar at scale. Strategies: sample debug/info logs (keep all errors), log structured **JSON** (so fields are queryable, not regex-parsed), keep the `traceId` on every line for correlation, and consider **trace-based sampling** — keep all logs/spans for a sampled trace, drop the rest — so you keep coherent stories rather than random fragments. Tracing itself is sampled (`sampling.probability`) for the same cost reason; **tail-based sampling** in the OTel Collector lets you keep 100% of *interesting* traces (errors, slow ones) while sampling the boring majority.

- **Push vs pull.** Prometheus *pulls* (scrapes), which makes target health observable (a down target is an alert). For short-lived jobs that die before a scrape, use the **Pushgateway** or OTLP push. Know which model your component needs.

---

### Stretch goals

1. **Import a Grafana dashboard.** Add Prometheus as a data source, then build (or import) a RED dashboard for `orders-svc`: rate, error %, and a p50/p95/p99 latency panel from the histogram. Save the JSON to the repo.
2. **Wire exemplars end-to-end.** Enable Prometheus exemplar storage (`--enable-feature=exemplar-storage`), add the OTel exemplar sampler dep, run Zipkin, and click from a p99 latency point in Grafana into the matching trace.
3. **Define an SLO + burn-rate alert.** Add a Prometheus alerting rule for "p99 order-create latency > 500ms for 5m" or a multi-window error-budget burn-rate alert, and trigger it with a `sleep` injected into `doCreate`.
4. **Add a `DistributionSummary` and a `Gauge`.** Record order *amount* as a `DistributionSummary` (p99 order value) and expose live queue depth (Day 4) as a `Gauge`; verify both in PromQL.
5. **Switch to the OTel bridge.** Replace the Brave bridge with `micrometer-tracing-bridge-otel` + OTLP, run an OpenTelemetry Collector in Docker, and fan traces out to Jaeger.

---

### Day 26 teaser

You can now *see* the system. **Day 26: Security** — we lock it down: authentication vs authorization, Spring Security filter chains, JWT/OAuth2 resource servers, password hashing (BCrypt), and the principle of least privilege. And yes — we'll make sure those shiny new `/actuator/prometheus` and `/actuator/metrics` endpoints aren't wide open to the internet, because an unauthenticated metrics endpoint is an information-disclosure gift to attackers.
