# Day 29: Reactive Programming & Backpressure

| | |
|---|---|
| 🏗️ **Project** | **FluxFeed** — a WebFlux reactive streaming endpoint with backpressure |
| ☕ **Java & language skills** | Project Reactor (Mono/Flux), reactive operators, WebClient, schedulers, request(n) |
| 🧰 **Library / tool** | Spring WebFlux + Project Reactor |
| 🗄️ **DB / distributed-systems concept** | Reactive streams & backpressure (event loop vs thread-per-request) |
| 📊 **Difficulty** | Hard |

---

## Concept primer

### 1. Thread-per-request (Day 10) vs the event loop

On Day 10 you built a blocking Spring MVC controller. Under the hood, Tomcat assigns **one thread per in-flight request**. That thread is "yours" for the entire lifetime of the request — and crucially, when your handler calls the database (Day 9 pool) or a downstream HTTP service (`RestTemplate`), the thread **blocks**: it sits parked on a socket read, consuming a full OS thread stack (~512KB–1MB) while doing *nothing but waiting*.

```
Blocking model (Spring MVC / Day 10)

req1 ──► [Thread-1] ──► DB call ...waiting...waiting...waiting... ──► respond
req2 ──► [Thread-2] ──► HTTP call ...waiting............ ──► respond
req3 ──► [Thread-3] ──► waiting...
 ...
req201 ─► (no free thread) ──► QUEUED  ◄── Tomcat default maxThreads = 200
```

This is fine when work is CPU-bound or concurrency is modest. It falls apart when you have **many connections that are mostly idle/waiting** — long-lived streams, slow downstreams, chatty microservices, server-sent events, websockets. Each waiting request burns a thread. At a few hundred concurrent slow requests you exhaust the pool; everything else queues; latency explodes. The memory cost of N threads (N × ~1MB) is the real ceiling.

The **event-loop model** inverts this. A small fixed number of threads (typically `= number of CPU cores`) run an event loop. When a handler issues I/O, it **registers a callback and returns the thread to the loop** instead of blocking. When the OS signals "this socket has data" (via `epoll`/`kqueue`), the loop resumes the continuation. One thread juggles thousands of connections because no thread ever waits on I/O.

```
Event-loop model (WebFlux / Netty)

[EventLoop-1]  ──► register(req1 DB read) ─┐
               ──► register(req2 HTTP) ────┤   thread is FREE while OS waits
               ──► register(req3 ...) ─────┘
   ... epoll says "req2's socket is readable" ...
               ──► resume req2 callback ──► respond
```

This is the engine behind Nginx, Node.js, Netty, and Spring WebFlux.

### 2. The C10k problem

Coined ~1999: *how do you handle ten thousand simultaneous connections on one box?* With thread-per-connection, 10,000 threads × ~1MB stack = ~10GB of RAM doing nothing but waiting, plus brutal context-switch overhead. The event-loop answer: a handful of threads, non-blocking syscalls, O(1) readiness notification. Reactive frameworks are the application-level expression of the C10k solution. The keyword is **mechanical sympathy**: match the I/O-bound nature of network services to a model that doesn't waste a thread per wait.

### 3. The Reactive Streams spec — and why `request(n)` is the whole point

"Reactive" predates Reactor; the JVM standard is **Reactive Streams** (now `java.util.concurrent.Flow` in the JDK). It is four tiny interfaces:

```java
interface Publisher<T>     { void subscribe(Subscriber<? super T> s); }
interface Subscriber<T>    {
    void onSubscribe(Subscription s);
    void onNext(T item);
    void onError(Throwable t);
    void onComplete();
}
interface Subscription     { void request(long n); void cancel(); }
interface Processor<T,R> extends Subscriber<T>, Publisher<R> {}
```

The naive observer pattern is **push**: the producer calls `onNext` as fast as it can and the consumer drowns. Reactive Streams adds one decisive twist — `Subscription.request(n)`. The consumer says *"I am ready for n more items."* The producer is **forbidden** from emitting more than the outstanding requested amount. This turns a pure push model into a **pull-push (demand-driven)** model.

```
Subscriber: onSubscribe(sub)  → sub.request(2)        "give me 2"
Publisher:  onNext(a) onNext(b)                       (stops — demand exhausted)
Subscriber: ...processes... → sub.request(2)          "ok, 2 more"
Publisher:  onNext(c) onNext(d)
            ...
Publisher:  onComplete()
```

**That negotiated demand IS backpressure.** It is the same idea as your Day 4 bounded `BlockingQueue`: when the queue was full, `put()` blocked the producer until the consumer drained it. Reactive Streams achieves the same flow control *without blocking a thread* — the producer simply doesn't get a callback to emit until demand exists. And it's the same lag pressure you saw on Day 18: a Kafka consumer pulls (`poll`) at its own pace; the broker never pushes faster than you ask. Backpressure is the universal answer to *"fast producer, slow consumer."*

### 4. Backpressure strategies — when you genuinely can't slow the source

`request(n)` works when the source is *controllable* (it can choose not to emit). But some sources are **inherently hot and unstoppable**: mouse moves, sensor readings, market ticks, a Kafka firehose. The producer emits whether you're ready or not. Reactor then forces you to pick an **overflow strategy**:

| Strategy | Operator | Behavior when consumer is too slow |
|---|---|---|
| **BUFFER** | `onBackpressureBuffer()` | Queue overflow items in memory. Safe-ish, but an unbounded buffer is an **OOM time bomb** — always bound it. |
| **DROP** | `onBackpressureDrop()` | Throw away *newest* items that don't fit. Good for telemetry where freshness doesn't matter much. |
| **LATEST** | `onBackpressureLatest()` | Keep only the **most recent** item; discard older unconsumed ones. Ideal for "current value" dashboards. |
| **ERROR** | `onBackpressureError()` | Fail fast with `MissingBackpressureException`. Surfaces the mismatch loudly. |
| **rate-limit** | `limitRate(n)` | Consumer prefetches in chunks of `n`, requesting the next batch at 75% drain — the *polite* way to apply backpressure to a cooperative source. |

`limitRate` is the everyday tool; `onBackpressure*` is the safety valve for hot sources. You'll use both below.

### 5. Cold vs hot publishers

- **Cold**: nothing happens until you `subscribe`, and **each subscriber gets its own independent execution from the start**. A `WebClient` call, `Flux.range(...)`, reading a file — every subscriber triggers a fresh request. (This is exactly the **laziness of Day 3 Streams**: a `Stream` does nothing until a terminal operation; a cold `Flux` does nothing until `subscribe`. Building the pipeline is free; running it is on-subscribe.)
- **Hot**: emits regardless of subscribers; late subscribers **miss** what already happened (or see only from now on). A `Sinks.many()`, mouse events, a shared price feed. `share()`, `publish().refCount()`, `replay()` turn cold into hot.

The mental check: *"If two people subscribe, do they each get their own run, or do they share one live stream?"* Own run = cold. Shared live = hot.

### 6. `Mono` vs `Flux`

Reactor's two `Publisher` types:
- `Mono<T>` — **0 or 1** element, then completes (or errors). The reactive `Optional<T>` / `CompletableFuture<T>`. Use for: a single DB lookup, one HTTP response, a "save" returning the saved entity.
- `Flux<T>` — **0 to N** (possibly infinite) elements. Use for: a query returning many rows, an SSE stream, a Kafka subscription.

Both are **immutable** and **lazy**. Operators (`map`, `filter`, `flatMap`) return *new* publishers describing a pipeline; nothing executes until subscription — and in WebFlux, **the framework subscribes for you** when the HTTP response is written. You almost never call `.subscribe()` yourself in a controller; doing so is usually a bug (it detaches the work from the response).

### 7. When NOT to go reactive — and the virtual-threads escape hatch

Reactive is **not free**. The costs are real and senior engineers weigh them honestly:
- **Debuggability**: stack traces are useless (everything runs on `reactor-http-nio-3`, the logical call chain is gone). You need `Hooks.onOperatorDebug()` / `checkpoint()` / Reactor's debug agent.
- **Cognitive load**: the whole team must think in pipelines and never, ever block. One stray `jdbcTemplate.query()` on the event loop and throughput collapses for everyone.
- **Ecosystem**: your *entire* I/O path must be non-blocking — DB driver (R2DBC, not JDBC), HTTP client (WebClient), everything. One blocking JDBC call poisons the model.

Reactive pays off when you are **I/O-bound and concurrency is genuinely high**: API gateways, streaming/SSE/websocket fan-out, aggregating many slow downstreams, the C10k zone. It does **not** pay off for CPU-bound work, modest concurrency, or a simple CRUD service over JDBC.

**The Java 21 virtual threads alternative.** Virtual threads (Project Loom) give you the C10k *throughput* of the event loop while keeping the *blocking, sequential, debuggable* programming model of Day 10. A virtual thread that blocks on I/O is cheaply parked by the JVM and its carrier OS thread is freed — so you can have *millions* of them. You write plain blocking code (`jdbcTemplate`, `RestTemplate`), keep readable stack traces, and the runtime gives you the scalability. For the *many* services whose only reason to consider reactive was "we need more concurrency than 200 threads," virtual threads (`spring.threads.virtual.enabled=true` on the MVC stack) are now the **simpler, default-correct** answer. Reactive earns its keep specifically where you need **declarative streaming + backpressure composition**, not merely high connection counts. Know both; reach for reactive deliberately, not reflexively.

---

## Prerequisites

- JDK 17+ (21 recommended so you can compare virtual threads).
- The Maven setup from Day 1. This is a **fresh Spring Boot project** (WebFlux and MVC don't mix in one auto-config cleanly — pick one).

### Maven dependency

The single starter swaps the entire web stack from Tomcat/servlet to Netty/reactive:

```xml
<dependencies>
    <!-- Reactive web: brings in Reactor + Netty + WebClient -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-webflux</artifactId>
    </dependency>

    <!-- Optional: reactive test support (StepVerifier, WebTestClient) -->
    <dependency>
        <groupId>io.projectreactor</groupId>
        <artifactId>reactor-test</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>
```

> Note: do **not** also include `spring-boot-starter-web`. If both are on the classpath, Spring Boot defaults back to the **blocking MVC/Tomcat** stack and silently disables the reactive server — a classic gotcha.

---

## 🛠️ Project Walkthrough — FluxFeed

Roll up your sleeves — from here on you build the live-ticker service hands-on, step by step, ending with a run and the expected output.

### Step-by-step project

We'll build a small "live ticker" service:
1. An **SSE endpoint** streaming a `Flux` of price events to clients (event-loop fan-out).
2. A **WebClient** call to a downstream service, composed with `flatMap` (non-blocking replacement for `RestTemplate`).
3. A **backpressure demo** endpoint: a fast source + a deliberately slow consumer, showing `limitRate` and `onBackpressureBuffer` in action with logged `request(n)` demand.

### Step 1 — The application + a domain record

```java
package com.example.reactive;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class ReactiveApp {
    public static void main(String[] args) {
        SpringApplication.run(ReactiveApp.class, args);
    }
}
```

```java
package com.example.reactive;

import java.time.Instant;

// A simple immutable event we'll stream.
public record PriceTick(String symbol, double price, Instant at) {}
```

### Step 2 — A reactive controller returning a `Flux` (SSE)

Contrast with Day 10: there, a handler returned `ResponseEntity<Order>` and the thread blocked until the body was ready. Here the handler returns a **`Flux`** immediately; Netty subscribes and writes each element to the socket as it arrives, holding **zero threads** between emissions.

```java
package com.example.reactive;

import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;

import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.ThreadLocalRandom;

@RestController
public class PriceStreamController {

    private static final String[] SYMBOLS = {"ACME", "GLOB", "INIT"};

    /**
     * Infinite, COLD Flux: a new tick every 500ms. Each subscriber (each curl)
     * gets its own independent stream — that's "cold". The framework subscribes
     * when it writes the HTTP response; we never call subscribe() ourselves.
     *
     * MediaType TEXT_EVENT_STREAM => the browser/curl sees Server-Sent Events.
     */
    @GetMapping(value = "/prices", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<ServerSentEvent<PriceTick>> streamPrices() {
        return Flux.interval(Duration.ofMillis(500))          // 0,1,2,... every 500ms
                .map(seq -> {
                    String symbol = SYMBOLS[(int) (seq % SYMBOLS.length)];
                    double price = 100 + ThreadLocalRandom.current().nextDouble(-5, 5);
                    return new PriceTick(symbol, Math.round(price * 100) / 100.0, Instant.now());
                })
                .map(tick -> ServerSentEvent.<PriceTick>builder()
                        .id(String.valueOf(tick.at().toEpochMilli()))
                        .event("price")
                        .data(tick)
                        .build());
    }

    /** A plain Flux<PriceTick> (JSON stream) for comparison — same engine, no SSE envelope. */
    @GetMapping(value = "/prices/json", produces = MediaType.APPLICATION_NDJSON_VALUE)
    public Flux<PriceTick> streamPricesJson() {
        return streamPrices().map(ServerSentEvent::data);
    }
}
```

Key points:
- `Flux.interval(...)` is a **hot-ish** infinite timer; combined here as a cold pipeline (each subscriber starts its own interval).
- `MediaType.TEXT_EVENT_STREAM_VALUE` makes Spring serialize each element as an SSE frame (`event:`, `id:`, `data:`).
- The handler returns in microseconds. There is no thread "stuck" producing the stream — Netty pumps elements as the timer fires.

### Step 3 — Non-blocking downstream calls with `WebClient` (replacing `RestTemplate`)

`RestTemplate` (Day 10) blocks the calling thread until the response arrives — fatal on the event loop. `WebClient` returns a `Mono`/`Flux` and never blocks. Register it as a bean:

```java
package com.example.reactive;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
public class WebClientConfig {

    @Bean
    public WebClient ratingsClient(WebClient.Builder builder) {
        // Downstream "ratings" service. Using httpbin as a stand-in you can curl.
        return builder.baseUrl("https://httpbin.org").build();
    }
}
```

Now compose: for each symbol, enrich it by calling the downstream service, **in parallel**, with `flatMap`. This is where reactive shines — fan out N non-blocking calls and merge the results without managing a thread pool by hand (contrast Day 4's manual producer/consumer).

```java
package com.example.reactive;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.time.Duration;

@RestController
public class EnrichmentController {

    private final WebClient ratingsClient;

    public EnrichmentController(WebClient ratingsClient) {
        this.ratingsClient = ratingsClient;
    }

    public record EnrichedSymbol(String symbol, String origin) {}

    /**
     * For each symbol, fire a non-blocking downstream call.
     * flatMap => the N calls run CONCURRENTLY and results merge as they arrive
     *            (order not guaranteed; use concatMap to preserve order).
     */
    @GetMapping(value = "/enrich", produces = MediaType.APPLICATION_JSON_VALUE)
    public Flux<EnrichedSymbol> enrich() {
        return Flux.just("ACME", "GLOB", "INIT")
                .flatMap(this::callRatings)               // concurrent fan-out
                .timeout(Duration.ofSeconds(5))           // never wait forever
                .onErrorResume(ex -> Flux.empty());       // degrade gracefully
    }

    private Mono<EnrichedSymbol> callRatings(String symbol) {
        return ratingsClient.get()
                .uri("/anything/{s}", symbol)
                .retrieve()
                .bodyToMono(JsonNode.class)               // non-blocking deserialization
                .map(json -> new EnrichedSymbol(symbol, json.path("origin").asText("unknown")))
                // subscribeOn moves the SUBSCRIPTION-side work to a bounded
                // elastic scheduler (relevant when wrapping a blocking lib;
                // pure WebClient is already non-blocking, shown for teaching):
                .onErrorReturn(new EnrichedSymbol(symbol, "downstream-error"));
    }
}
```

- `flatMap` subscribes to each inner `Mono` **eagerly and concurrently**, merging results — this is the "many slow downstreams aggregated cheaply" win.
- Swap `flatMap` → `concatMap` if you need the inner streams processed **in order, one at a time**.
- Swap `flatMap` → `zip` (e.g. `Mono.zip(a, b, c)`) when you need to **combine** results of several calls into one object (all must complete).
- `timeout` + `onErrorResume`/`onErrorReturn` are your reactive resilience operators (compare Day 24).

### Step 4 — The backpressure demo: fast producer, slow consumer

This is the heart of the day. We create a **fast source** (emits as fast as it can) and a **deliberately slow consumer**, then show three behaviors: (a) raw demand with `request(n)`, (b) polite `limitRate`, (c) a bounded `onBackpressureBuffer` overflow valve.

```java
package com.example.reactive;

import org.reactivestreams.Subscription;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.BaseSubscriber;
import reactor.core.publisher.Flux;
import reactor.util.concurrent.Queues;

import java.time.Duration;

@RestController
public class BackpressureController {

    /**
     * (a) MANUAL request(n): a custom Subscriber that asks for items 2 at a time,
     * sleeping between batches to simulate a slow consumer. Watch the producer
     * only emit what was requested — backpressure in its rawest form.
     *
     * Returns the controlled stream so you can also see it over HTTP.
     */
    @GetMapping(value = "/bp/manual", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> manualDemand() {
        Flux<Long> fastSource = Flux.range(1, 50)
                .map(Long::valueOf)
                .doOnRequest(n -> log("PRODUCER got request(" + n + ")"));

        // A subscriber that pulls 2 at a time. (For teaching; we ALSO return the
        // flux so the HTTP client drives demand. Run this subscriber separately
        // to watch the log, or call it from main()/a test.)
        fastSource.subscribe(new SlowSubscriber());

        return fastSource.map(i -> "item-" + i);
    }

    /**
     * (b) limitRate: the cooperative, everyday backpressure tool. The consumer
     * prefetches in windows of 5 and requests the next window at 75% drain.
     * A slow downstream (50ms each) cannot be overrun by the fast source.
     */
    @GetMapping(value = "/bp/limitrate", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> limitRate() {
        return Flux.range(1, 50)
                .doOnRequest(n -> log("limitRate -> request(" + n + ")"))
                .limitRate(5)                                  // prefetch 5, refill at 75%
                .concatMap(i -> Flux.just("item-" + i)
                        .delayElements(Duration.ofMillis(50))); // slow consumer
    }

    /**
     * (c) onBackpressureBuffer: a HOT, unstoppable source (an interval can't be
     * slowed). We bound the buffer; if the slow consumer falls behind by more
     * than 16 items, the OLDEST overflow is dropped and we log it. This is the
     * OOM-safe valve for sources you cannot ask to slow down.
     */
    @GetMapping(value = "/bp/buffer", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> bufferedHotSource() {
        return Flux.interval(Duration.ofMillis(10))            // FAST, unstoppable: 100/sec
                .onBackpressureBuffer(
                        16,                                    // bounded buffer
                        dropped -> log("DROPPED (buffer full): " + dropped),
                        reactor.core.publisher.BufferOverflowStrategy.DROP_OLDEST)
                .concatMap(i -> Flux.just("tick-" + i)
                        .delayElements(Duration.ofMillis(200))); // SLOW: 5/sec
    }

    private static void log(String msg) {
        System.out.println("[" + Thread.currentThread().getName() + "] " + msg);
    }

    /** A Subscriber that demonstrates request(n) explicitly. */
    static final class SlowSubscriber extends BaseSubscriber<Long> {
        @Override
        protected void hookOnSubscribe(Subscription subscription) {
            log("SUBSCRIBER: request(2)");
            request(2);                                        // initial demand
        }

        @Override
        protected void hookOnNext(Long value) {
            log("SUBSCRIBER consumed " + value + " (simulating slow work...)");
            try { Thread.sleep(300); } catch (InterruptedException ignored) {}
            log("SUBSCRIBER: request(2)  // ready for more");
            request(2);                                        // pull the next batch
        }

        @Override
        protected void hookOnComplete() { log("SUBSCRIBER: done"); }
    }
}
```

What each demonstrates:
- **/bp/manual** — `BaseSubscriber` overriding `hookOnSubscribe`/`hookOnNext` to call `request(2)`. The `doOnRequest` log shows the producer receiving the *exact* demand. This is Reactive Streams stripped to its essence — the consumer drives.
- **/bp/limitrate** — the production-grade tool. `limitRate(5)` caps in-flight demand; the slow `concatMap` consumer is never overwhelmed, and you'll see `request(5)` then `request(4)` (refill at 75%) batches in the log.
- **/bp/buffer** — a genuinely **hot, unstoppable** source (a 100/sec interval) feeding a 5/sec consumer. Since you can't `request(n)` your way out of a fixed-rate source, you bound a buffer and **drop the oldest** on overflow, logging each drop. The buffer never grows unbounded → no OOM.

> The Day 4 parallel: `/bp/buffer` is exactly your bounded `BlockingQueue` with a drop policy — except no thread blocks, the policy is declarative, and the demand protocol is built into the stream.

---

## How to run + curl the stream

```bash
mvn spring-boot:run
```

Stream the SSE prices (Ctrl-C to stop the infinite stream):

```bash
curl -N http://localhost:8080/prices
```

`-N` disables curl buffering so you see events live.

Aggregate downstream calls:

```bash
curl -s http://localhost:8080/enrich | jq .
```

Watch backpressure. Run these and **watch the server console** for the `request(n)` / `DROPPED` logs:

```bash
curl -N http://localhost:8080/bp/limitrate
curl -N http://localhost:8080/bp/buffer
curl -N http://localhost:8080/bp/manual
```

### Expected output

`/prices` (SSE frames, one every 500ms):

```
id:1718539200123
event:price
data:{"symbol":"ACME","price":102.34,"at":"2026-06-16T10:00:00.123Z"}

id:1718539200623
event:price
data:{"symbol":"GLOB","price":97.88,"at":"2026-06-16T10:00:00.623Z"}
...
```

`/bp/manual` — server console (note the producer only emits up to demand, the consumer pulls 2 at a time):

```
[reactor-...] SUBSCRIBER: request(2)
[reactor-...] PRODUCER got request(2)
[reactor-...] SUBSCRIBER consumed 1 (simulating slow work...)
[reactor-...] SUBSCRIBER: request(2)  // ready for more
[reactor-...] PRODUCER got request(2)
[reactor-...] SUBSCRIBER consumed 2 (simulating slow work...)
...
```

`/bp/buffer` — console shows drops as the slow consumer falls behind the 100/sec source:

```
[reactor-...] DROPPED (buffer full): 21
[reactor-...] DROPPED (buffer full): 22
...
```

while `curl` still receives a steady ~5 ticks/sec (`tick-0`, `tick-17`, ... — gaps where items were dropped). The system stays healthy under overload instead of OOM-ing: that is backpressure doing its job.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **R2DBC — the missing piece.** A reactive controller over a **blocking JDBC driver (Day 9/12) is a lie**: the moment you call JDBC, you block the event loop and lose everything. The fully reactive DB stack is **R2DBC** (`spring-boot-starter-data-r2dbc` + e.g. `r2dbc-postgresql`), exposing `ReactiveCrudRepository` returning `Mono`/`Flux`. Note: R2DBC is younger and less featureful than JPA (no lazy loading, weaker mapping). For many teams, a blocking JDBC repo run on **virtual threads** beats fighting R2DBC.
- **Never block the event loop — and how to detect it.** A single `Thread.sleep`, blocking JDBC, or `.block()` on a `reactor-http-nio-*` thread stalls *every* connection that thread serves. Add **BlockHound** (`reactor-tools` / blockhound agent) in tests — it instruments the JVM to throw if blocking code runs on a non-blocking scheduler. Treat it as a CI gate.
- **Schedulers: `subscribeOn` vs `publishOn`.** When you *must* wrap a blocking call, offload it: `Schedulers.boundedElastic()` is the pool for blocking work. `subscribeOn(scheduler)` sets where the **subscription/source** runs (affects the start of the chain, position-independent). `publishOn(scheduler)` switches the thread for **everything downstream of it** (position matters). `Schedulers.parallel()` is for CPU-bound work (sized to cores); never run blocking I/O on it.
- **Cold→hot sharing.** Use `.share()` / `.publish().refCount()` to multicast one upstream to many subscribers (e.g. one real price feed, many SSE clients) instead of N independent upstreams. `replay(n)` lets late subscribers catch the last n events.
- **Debugging.** Stack traces are worthless. Tools: `Hooks.onOperatorDebug()` (dev only — expensive), `.checkpoint("label")` at suspect points (cheap, production-safe), the **Reactor Debug Agent** (`reactor-tools`), and `.log()` to trace `onSubscribe`/`request`/`onNext`/`onComplete` signals. `StepVerifier` (from `reactor-test`) is how you unit-test a `Flux` deterministically, including virtual-time tests for `interval`/`delay` via `StepVerifier.withVirtualTime(...)`.
- **Virtual threads vs reactive — the senior call.** If your need is purely *"handle more concurrent I/O-bound requests than 200 threads,"* prefer **virtual threads** on the Day 10 MVC stack (`spring.threads.virtual.enabled=true`) — same throughput, blocking code, readable traces, full JDBC/JPA ecosystem. Reach for **reactive** when you specifically need **declarative streaming, composition, and backpressure** (SSE/websocket fan-out, merging/throttling many streams, rate-shaping). Virtual threads solve *concurrency*; reactive solves *stream composition + flow control*. They are not the same problem, and the best teams pick per-service.

### Stretch goals

1. **WebTestClient + StepVerifier.** Write a test that hits `/prices` and asserts the first 3 SSE elements with `StepVerifier`, using `withVirtualTime` so the 500ms interval doesn't make the test slow.
2. **Multicast one feed to many clients.** Replace the per-subscriber cold `Flux.interval` in `/prices` with a single shared hot source via `Sinks.many().multicast()` + `.asFlux()`, so 1,000 SSE clients share one upstream. Prove with `share()` that all clients see the *same* ticks.
3. **Go fully reactive end-to-end.** Add `spring-boot-starter-data-r2dbc` + an in-memory H2 (or Postgres via Testcontainers, Day 23) reactive repository, and stream rows straight from the DB as a `Flux` to an SSE endpoint — backpressure flowing all the way from the socket to the database cursor.
4. **Head-to-head benchmark.** Build the *same* endpoint three ways — blocking MVC (Day 10), MVC + virtual threads, and WebFlux — point a load generator (e.g. `wrk -c 5000`) at each with an artificial 100ms downstream delay, and compare threads, memory, and p99 latency. Form your own opinion about when reactive is worth it.

### Day 30 teaser — Capstone

Tomorrow you tie it all together. You'll assemble a small but real distributed system that combines the threads you've pulled across 29 days: a durable log (WAL/Kafka), the **outbox pattern (Day 20)** for reliable event publishing, **idempotency (Day 7)** on the write path, **caching (Day 15/Redis 16)**, **resilience (Day 24)** and **rate limiting (Day 27)** at the edge, **observability (Day 25)** wired through, and a choice between a **reactive (Day 29)** or **virtual-thread** front door — defended, not defaulted. The capstone is where "I learned the concept" becomes "I can build the system."
