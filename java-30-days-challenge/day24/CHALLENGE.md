# Day 24: Resilience: Retries & Circuit Breakers

| | |
|---|---|
| 🏗️ **Project** | **ResilientClient** — a Resilience4j-wrapped client for a flaky downstream |
| ☕ **Java & language skills** | Annotation-driven resilience, fallback methods, simulating flaky downstreams, config via yml |
| 🧰 **Library / tool** | Resilience4j (@Retry, @CircuitBreaker, @TimeLimiter, @Bulkhead) |
| 🗄️ **DB / distributed-systems concept** | Resilience patterns — circuit breaker, retries with backoff+jitter, bulkheads |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. Partial failure is the normal state

On **Day 7** you accepted that a network call has *three* outcomes (success, failure, and the terrible **unknown** — the request may or may not have happened). Today we accept the next uncomfortable truth: **at any given moment, some fraction of your downstream calls are already failing or about to.**

A service that talks to one dependency 99.9% available, called 10 times per request, has a per-request success ceiling of `0.999^10 ≈ 99.0%`. Add a second dependency and a few retries and your "three nines" dependency yields a two-nines product. Availability **multiplies down the call chain**. You do not get to opt out of failure; you only get to choose how you *behave* during it.

The failure modes you must survive are not just "down":
- **Slow** — the downstream answers, but in 8 seconds. This is *worse* than down, because your threads block waiting, your connection pool (Day 9) drains, and the slowness **propagates upstream** to your own callers. A slow dependency takes down healthy services. This is **cascading failure**.
- **Overloaded** — it returns 503s or times out because it's already saturated. The *last* thing it needs is your client retrying immediately.
- **Flapping** — intermittently failing during a deploy, GC pause, or leader election.

Resilience is the engineering discipline of **containing** these failures so one sick dependency does not become your outage.

### 2. The circuit breaker state machine

The metaphor is the electrical breaker: when current is dangerous, the breaker *opens* and physically disconnects the circuit so the wiring doesn't melt. A software circuit breaker sits in front of a remote call and watches the *recent outcome history*.

```
                  failure-rate >= threshold
                  (over a sliding window)
        ┌──────────────────────────────────────────┐
        │                                            ▼
   ┌─────────┐                                  ┌─────────┐
   │ CLOSED  │                                  │  OPEN   │
   │ calls   │                                  │ calls   │
   │ pass    │                                  │ fail    │
   │ through │◄───────────┐                     │ FAST    │
   └─────────┘            │                     │ (no     │
        ▲                 │ probes succeed      │ network)│
        │                 │ (rate < threshold)  └─────────┘
        │                 │                          │
        │            ┌──────────┐                    │ after waitDurationInOpenState
        │            │HALF_OPEN │◄───────────────────┘
        │            │ allow N  │
        └────────────┤ probe    │
   probes fail       │ calls    │
   (back to OPEN)    └──────────┘
```

- **CLOSED** — normal. Calls go through. The breaker records each result into a **sliding window** (last *N* calls, or last *N* seconds). When the **failure rate** (or slow-call rate) over a *minimum number of calls* crosses a threshold, it trips to **OPEN**.
- **OPEN** — the breaker **short-circuits**. It does **not** call the downstream at all; it throws `CallNotPermittedException` instantly (which routes to your **fallback**). This is the whole point: **fail fast and give the downstream room to recover** instead of beating on it. After a configured `waitDurationInOpenState`, it moves to HALF_OPEN.
- **HALF_OPEN** — a cautious trial. It lets a small number of *probe* calls through. If they succeed (rate back under threshold) → **CLOSED** (recovered). If they fail → **OPEN** again (and the wait timer restarts). This is what prevents a "thundering herd" of all traffic slamming a downstream the instant the wait expires.

The breaker is a **failure detector with hysteresis**. It deliberately resists flapping between states.

### 3. Retry storms, backoff, and jitter — and metastable failure

Retries feel free and obviously good. They are neither. Consider 1000 clients calling a service that just got slow. Each client retries 3× **immediately on failure**. The downstream's load just **quadrupled** (1 original + 3 retries) at the *exact* moment it was already failing. The retries cause more failures, which cause more retries. The system is now in a **metastable failure state**: even after the original trigger (a deploy, a traffic spike) is gone, the **self-sustaining retry load** keeps it down. It will not recover on its own — you have to manually shed load. This is one of the most common causes of large outages.

Two fixes, used together:

**Exponential backoff** — wait longer between each attempt: `1s, 2s, 4s, 8s…` (`base * multiplier^attempt`). This gives the downstream time to drain its queue. Always **cap** the number of attempts (3–4 is typical) and the max delay.

**Jitter** — randomize the backoff. Without jitter, 1000 clients that all failed at `t=0` will all retry at *exactly* `t=1s`, then all at `t=3s` — synchronized waves of load. Backoff alone just changes *when* the herd stampedes. **Jitter spreads the retries across the interval** so load is smooth. Resilience4j does this with `randomizedWaitFactor` (it picks a delay in `[d * (1-f), d * (1+f)]`).

> Senior rule of thumb: **retry budget**. Cap total retries to a small percentage (e.g. 10%) of request volume. If you're retrying more than that, the downstream is *down*, not flaky — retrying more only hurts. (Resilience4j doesn't have a built-in retry budget; the circuit breaker is the practical substitute — once it's OPEN, retries stop entirely.)

### 4. Retry only idempotent operations — callback to Day 7

A retry *replays* a request. If the first attempt actually reached the server and succeeded — but the **response** was lost (the dreaded *unknown* outcome from Day 7) — then your "retry" is a **second execution**.

- `GET /orders/42`, `PUT /orders/42 {status: shipped}`, `DELETE /orders/42` — **idempotent**. Replay is harmless. Safe to auto-retry.
- `POST /charges {amount: 50}` — **not idempotent**. Replay = a second $50 charge. **Never blindly auto-retry.**

The Day 7 fix makes the non-idempotent operation idempotent again: attach an **idempotency key** so the server dedups replays. *Only then* is it safe to retry. The discipline: **retry policy and idempotency are a single design decision.** In `application.yml` you configure `retryExceptions` / `ignoreExceptions` precisely so you don't retry the wrong thing (e.g. never retry a `4xx` client error — it'll fail identically every time).

### 5. Bulkheads, timeouts, fallbacks

- **Bulkhead** (from ships' compartments): cap the number of *concurrent* calls allowed to a dependency. If the dependency goes slow, only that many of your threads can be stuck on it; the rest of your app keeps serving. It **isolates the blast radius** so one slow dependency can't consume your entire thread pool (Day 9's pool exhaustion, contained).
- **TimeLimiter / timeout**: a call that never returns is the worst failure. A timeout converts "hangs forever" into "fails in 2s" — which the breaker can then *count* and the retry can react to. **Without timeouts, the breaker can't protect you from slowness, only from errors.**
- **Fallback / graceful degradation**: when the call fails (or is short-circuited), return *something useful* — a cached value (Day 15), a default, an empty list, a "try again later" — instead of a 500. The art of resilience is **degrading**, not collapsing.

### Order of the decorators (this matters)

When you stack annotations, Resilience4j applies them in a fixed order (outer → inner):

```
Retry( CircuitBreaker( RateLimiter( TimeLimiter( Bulkhead( yourCall ) ) ) ) )
```

Read it inside-out per attempt: a single attempt is bulkhead-guarded, time-limited, then the breaker records its outcome — and **Retry wraps the breaker**, so each retry attempt is itself counted by the breaker. The fallback is the outermost catch of all. This is the sensible default; just know that **Retry is outermost**, so once the breaker is OPEN, retries see `CallNotPermittedException` and stop fast.

---

## Prerequisites

- JDK 17+ and Maven (Day 1).
- Familiarity with Spring Boot annotations and AOP proxies (Day 8 — these annotations only work on Spring-managed beans called *through the proxy*; a self-invocation inside the same class bypasses them, exactly like `@Transactional`).
- Day 7's idempotency mindset.

### Maven dependencies

```xml
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <!-- Resilience4j annotations are AOP-driven -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-aop</artifactId>
    </dependency>
    <!-- The headline dependency: pulls in resilience4j core + Spring integration -->
    <dependency>
        <groupId>io.github.resilience4j</groupId>
        <artifactId>resilience4j-spring-boot3</artifactId>
        <version>2.2.0</version>
    </dependency>
    <!-- Exposes /actuator/circuitbreakers + Micrometer metrics (ties to Day 25) -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-actuator</artifactId>
    </dependency>
</dependencies>
```

> `resilience4j-spring-boot3` already brings `resilience4j-circuitbreaker`, `-retry`, `-timelimiter`, `-bulkhead`, `-ratelimiter` transitively. You only add the one artifact.

### `src/main/resources/application.yml`

```yaml
spring:
  application:
    name: resilience-lab

server:
  port: 8080

# Expose breaker internals so you can watch state transitions
management:
  endpoints:
    web:
      exposure:
        include: health, circuitbreakers, circuitbreakerevents, retries, metrics
  endpoint:
    health:
      show-details: always
  health:
    circuitbreakers:
      enabled: true

resilience4j:
  circuitbreaker:
    configs:
      default:
        sliding-window-type: COUNT_BASED   # last N calls (TIME_BASED = last N seconds)
        sliding-window-size: 10            # judge over the last 10 calls
        minimum-number-of-calls: 5         # don't judge until we have >=5 samples
        failure-rate-threshold: 50         # trip OPEN at >=50% failures
        slow-call-rate-threshold: 50       # OR >=50% slow calls
        slow-call-duration-threshold: 2s   # a call slower than 2s counts as "slow"
        wait-duration-in-open-state: 5s    # stay OPEN this long before probing
        permitted-number-of-calls-in-half-open-state: 3
        automatic-transition-from-open-to-half-open-enabled: true
        # register the marker exception below so OPEN short-circuits don't pollute health
        record-exceptions:
          - java.io.IOException
          - java.util.concurrent.TimeoutException
          - org.example.resilience.DownstreamUnavailableException
    instances:
      flaky:
        base-config: default

  retry:
    configs:
      default:
        max-attempts: 4
        wait-duration: 500ms
        enable-exponential-backoff: true
        exponential-backoff-multiplier: 2     # 0.5s, 1s, 2s ...
        enable-randomized-wait: true
        randomized-wait-factor: 0.5           # JITTER: +/-50% of the computed delay
        retry-exceptions:
          - java.io.IOException
          - java.util.concurrent.TimeoutException
          - org.example.resilience.DownstreamUnavailableException
        ignore-exceptions:
          - org.example.resilience.NonRetryableException   # e.g. a 4xx / business error
    instances:
      flaky:
        base-config: default

  timelimiter:
    configs:
      default:
        timeout-duration: 2s
        cancel-running-future: true
    instances:
      flaky:
        base-config: default

  bulkhead:
    instances:
      flaky:
        max-concurrent-calls: 10      # at most 10 concurrent calls into the downstream
        max-wait-duration: 0          # if full, reject immediately (don't queue)
```

---

## 🛠️ Project Walkthrough — ResilientClient

Roll up your sleeves: build the resilient client end to end, then run it and watch the breaker trip OPEN and recover.

### Step 1 — Marker exceptions (so the policies target the *right* failures)

```java
package org.example.resilience;

/** A transient, retryable failure — the downstream is flaky/overloaded. */
public class DownstreamUnavailableException extends RuntimeException {
    public DownstreamUnavailableException(String message) { super(message); }
}

/** A permanent failure (bad request / business rule) — retrying is pointless. */
public class NonRetryableException extends RuntimeException {
    public NonRetryableException(String message) { super(message); }
}
```

Splitting these is the *whole game* for retries: you wire `DownstreamUnavailableException` into `retry-exceptions` and `NonRetryableException` into `ignore-exceptions`. Retrying a deterministic failure just wastes 4 attempts before failing identically.

### Step 2 — The deliberately flaky / slow downstream simulator

A second controller in the same app stands in for a remote service. It fails randomly and sometimes stalls — exactly the partial-failure behaviour from the primer.

```java
package org.example.resilience;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Simulates an unreliable downstream "quotes" service.
 * Knobs let the driver flip it between healthy and sick at runtime.
 */
@RestController
public class FlakyDownstreamController {

    /** When true, the downstream is "sick": high failure + latency. */
    private final AtomicBoolean sick = new AtomicBoolean(false);

    @GetMapping("/downstream/quote")
    public String quote(@RequestParam(defaultValue = "ACME") String symbol) {
        boolean isSick = sick.get();
        double failProb = isSick ? 0.8 : 0.1;     // 80% vs 10% error rate
        int slowProb    = isSick ? 70  : 5;        // % chance of a slow response

        if (ThreadLocalRandom.current().nextInt(100) < slowProb) {
            sleep(3000); // 3s > our 2s timeout -> counts as a slow/failed call
        }
        if (ThreadLocalRandom.current().nextDouble() < failProb) {
            throw new RuntimeException("downstream 503 for " + symbol);
        }
        double price = 100 + ThreadLocalRandom.current().nextDouble() * 10;
        return String.format("{\"symbol\":\"%s\",\"price\":%.2f}", symbol, price);
    }

    @GetMapping("/downstream/sick")
    public String setSick(@RequestParam boolean value) {
        sick.set(value);
        return "downstream sick=" + value;
    }

    private static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
```

### Step 3 — The resilient client service (the heart of today)

This is the bean that *calls* the downstream and is wrapped by every Resilience4j policy. Because `@TimeLimiter` needs to interrupt a blocking call, the protected method returns a `CompletableFuture` and runs on a separate thread.

```java
package org.example.resilience;

import io.github.resilience4j.bulkhead.annotation.Bulkhead;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.github.resilience4j.retry.annotation.Retry;
import io.github.resilience4j.timelimiter.annotation.TimeLimiter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.time.Duration;
import java.util.concurrent.CompletableFuture;

@Service
public class QuoteClient {

    private static final Logger log = LoggerFactory.getLogger(QuoteClient.class);
    private static final String NAME = "flaky"; // matches the yaml instance names

    private final RestTemplate http;

    public QuoteClient(RestTemplateBuilder builder) {
        // Connect/read timeouts are your FIRST line of defence — never rely on JVM defaults (infinite).
        this.http = builder
                .setConnectTimeout(Duration.ofSeconds(1))
                .setReadTimeout(Duration.ofSeconds(5)) // > timelimiter so the limiter wins
                .build();
    }

    /**
     * Decorator order (outer->inner): Retry( CircuitBreaker( TimeLimiter( Bulkhead( call ) ) ) ).
     * The fallback is named once; Spring picks the overload whose last arg matches the thrown type.
     */
    @Retry(name = NAME)                                  // backoff + jitter, from yaml
    @CircuitBreaker(name = NAME, fallbackMethod = "fallback")
    @TimeLimiter(name = NAME)                            // 2s budget per attempt
    @Bulkhead(name = NAME)                               // cap concurrency
    public CompletableFuture<String> getQuote(String symbol) {
        return CompletableFuture.supplyAsync(() -> {
            log.info("--> calling downstream for {}", symbol);
            try {
                String body = http.getForObject(
                        "http://localhost:8080/downstream/quote?symbol={s}", String.class, symbol);
                log.info("<-- downstream OK: {}", body);
                return body;
            } catch (RestClientException e) {
                // Translate transport errors into our RETRYABLE marker so the policies fire.
                throw new DownstreamUnavailableException("call failed: " + e.getMessage());
            }
        });
    }

    /**
     * Fallback for transient/short-circuited failures -> graceful degradation.
     * Triggered by DownstreamUnavailableException, TimeoutException, and (crucially)
     * CallNotPermittedException when the breaker is OPEN.
     */
    @SuppressWarnings("unused")
    private CompletableFuture<String> fallback(String symbol, Throwable t) {
        if (t instanceof CallNotPermittedException) {
            log.warn("BREAKER OPEN — short-circuited {}, serving cached/degraded value", symbol);
        } else {
            log.warn("FALLBACK for {} due to {}: {}", symbol,
                    t.getClass().getSimpleName(), t.getMessage());
        }
        // Serve a stale/cached/default answer instead of failing the caller (Day 15 vibes).
        return CompletableFuture.completedFuture(
                String.format("{\"symbol\":\"%s\",\"price\":null,\"stale\":true}", symbol));
    }
}
```

A few load-bearing details:
- **Fallback signature** = the same parameters as the protected method **plus a trailing `Throwable`**, and the **same return type** (`CompletableFuture<String>`). If the signature is wrong you get a runtime `NoSuchMethodException` — a classic first-time gotcha.
- The fallback catches **`CallNotPermittedException`** — that's the exception thrown when the breaker is OPEN. This is the seam where "circuit breaker tripped" turns into "user sees a graceful degraded response".
- The HTTP read timeout (5s) is set **higher** than the `TimeLimiter` (2s) on purpose: we want the *Resilience4j* timeout to be the one that fires and gets counted, not a silent socket timeout.

### Step 4 — A thin REST facade (so you can curl it)

```java
package org.example.resilience;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.concurrent.CompletableFuture;

@RestController
public class QuoteController {

    private final QuoteClient client;

    public QuoteController(QuoteClient client) { this.client = client; }

    @GetMapping("/quote")
    public CompletableFuture<String> quote(@RequestParam(defaultValue = "ACME") String symbol) {
        return client.getQuote(symbol);
    }
}
```

### Step 5 — A load driver that makes the breaker trip

Runs on startup: warms up healthy, flips the downstream **sick**, fires a burst (breaker trips OPEN), then flips it **healthy** and keeps calling (breaker probes in HALF_OPEN and recovers to CLOSED). It reads the breaker's live state so you can watch the machine move.

```java
package org.example.resilience;

import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class LoadDriver implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(LoadDriver.class);

    private final QuoteClient client;
    private final CircuitBreakerRegistry registry;
    private final RestTemplate admin = new RestTemplate();

    public LoadDriver(QuoteClient client, CircuitBreakerRegistry registry) {
        this.client = client;
        this.registry = registry;
    }

    @Override
    public void run(String... args) throws Exception {
        var breaker = registry.circuitBreaker("flaky");
        breaker.getEventPublisher().onStateTransition(e ->
                log.info("####### BREAKER {} -> {}",
                        e.getStateTransition().getFromState(),
                        e.getStateTransition().getToState()));

        Thread.sleep(1500); // let the web server bind

        log.info("===== PHASE 1: healthy downstream =====");
        fire(10);

        log.info("===== PHASE 2: downstream goes SICK (breaker should OPEN) =====");
        admin.getForObject("http://localhost:8080/downstream/sick?value=true", String.class);
        fire(20);

        log.info("===== PHASE 3: downstream recovers; wait past openState; observe HALF_OPEN -> CLOSED =====");
        admin.getForObject("http://localhost:8080/downstream/sick?value=false", String.class);
        Thread.sleep(6000); // > wait-duration-in-open-state (5s) -> auto HALF_OPEN
        fire(15);

        log.info("===== DONE. Final breaker state: {} =====", breaker.getState());
    }

    private void fire(int n) {
        for (int i = 0; i < n; i++) {
            try {
                String r = client.getQuote("ACME").join();
                log.info("[{}] state={} result={}", i, currentState(), r);
            } catch (Exception e) {
                log.info("[{}] state={} ERROR={}", i, currentState(), e.getMessage());
            }
            sleep(300);
        }
    }

    private String currentState() { return registry.circuitBreaker("flaky").getState().name(); }
    private static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
```

### Step 6 — App entry point

```java
package org.example.resilience;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class ResilienceApplication {
    public static void main(String[] args) {
        SpringApplication.run(ResilienceApplication.class, args);
    }
}
```

### How to run

```bash
mvn spring-boot:run
```

The `LoadDriver` runs automatically. You can also poke it by hand in another terminal:

```bash
# A single resilient call
curl 'http://localhost:8080/quote?symbol=ACME'

# Make the downstream sick / healthy on demand
curl 'http://localhost:8080/downstream/sick?value=true'
curl 'http://localhost:8080/downstream/sick?value=false'

# Watch the breaker's live state and recent transitions (Actuator)
curl -s localhost:8080/actuator/circuitbreakers | jq
curl -s localhost:8080/actuator/circuitbreakerevents/flaky | jq '.circuitBreakerEvents[-8:]'
```

### Expected output (abridged)

```
===== PHASE 1: healthy downstream =====
--> calling downstream for ACME
<-- downstream OK: {"symbol":"ACME","price":104.21}
[0] state=CLOSED result={"symbol":"ACME","price":104.21}
... mostly OK, the rare failure is silently swallowed by a retry ...

===== PHASE 2: downstream goes SICK (breaker should OPEN) =====
--> calling downstream for ACME
DownstreamUnavailableException: call failed: 500 ...
# Retry kicks in WITH BACKOFF+JITTER -- note the growing, jittered gaps:
--> calling downstream for ACME      (≈ +0.5s ± jitter)
--> calling downstream for ACME      (≈ +1.0s ± jitter)
--> calling downstream for ACME      (≈ +2.0s ± jitter)
FALLBACK for ACME due to DownstreamUnavailableException: call failed ...
[3] state=CLOSED result={"symbol":"ACME","price":null,"stale":true}
...
####### BREAKER CLOSED -> OPEN              <-- failure rate crossed 50%
[7] state=OPEN ...
BREAKER OPEN — short-circuited ACME, serving cached/degraded value
[8] state=OPEN result={"symbol":"ACME","price":null,"stale":true}
# Notice: NO "--> calling downstream" lines now. The breaker is failing FAST,
# not touching the network. The sick downstream gets breathing room.

===== PHASE 3: downstream recovers ... =====
####### BREAKER OPEN -> HALF_OPEN           <-- after 5s, auto-probe
--> calling downstream for ACME             <-- a probe call gets through
<-- downstream OK: {"symbol":"ACME","price":101.88}
####### BREAKER HALF_OPEN -> CLOSED         <-- probes succeeded, fully recovered
[5] state=CLOSED result={"symbol":"ACME","price":102.04}

===== DONE. Final breaker state: CLOSED =====
```

The three things to *see and internalise*:
1. **Retries back off with jitter** — the `--> calling` lines in Phase 2 grow apart by ~0.5s, 1s, 2s with randomness, never synchronized hammering.
2. **When OPEN, the downstream calls stop entirely** — fail-fast protects the sick service. The fallback serves a degraded answer so your callers never see a 500.
3. **Recovery is automatic and cautious** — OPEN → (5s) → HALF_OPEN → probe → CLOSED. No human intervention, no thundering herd.

---

## 🚀 Going Deeper & Next Steps

### Senior-level notes

- **Metastable failures.** The scariest outages aren't "X went down" — they're systems stuck *down* by their own retry/load feedback loop after the trigger is long gone. The breaker is your circuit-level **load shedder**: once OPEN it removes the self-sustaining load, which is precisely what lets the downstream escape the metastable state. Read Bronson et al., *"Metastable Failures in Distributed Systems"* (HotOS '21) — it reframes everything above.
- **Load shedding vs. backpressure (callback Day 4).** A breaker sheds load *at the client*. The server should *also* shed (return 503 / `Retry-After` early when its queue is full) so it never accepts work it can't finish. Backpressure (Day 4) and circuit breaking are the same idea applied at different ends of the pipe.
- **Hedged requests.** For read-only, idempotent calls with strict tail-latency goals, instead of waiting for a timeout you fire a *second* request after the p95 latency and take whichever returns first. It trades extra load for lower tail latency — Google's "The Tail at Scale" technique. Only safe for idempotent ops (Day 7 again).
- **Breaker tuning is empirical.** `failure-rate-threshold` too low → breaker flaps on normal noise; too high → it never protects you. `sliding-window-size` and `minimum-number-of-calls` must reflect real traffic (a COUNT window of 10 is meaningless at 5000 rps — prefer `TIME_BASED`). `wait-duration-in-open-state` should roughly match how long the downstream needs to recover (often a deploy or GC pause). Always **separate breaker instances per downstream** — one sick dependency shouldn't open the breaker for a healthy one.
- **Per-downstream bulkheads** mean a slow dependency A can't starve calls to healthy dependency B. Without bulkheads, one slow downstream eats the shared thread pool and you cascade (Day 9 pool exhaustion, distributed-systems edition).
- **Don't retry inside a breaker that's already counting your retries.** Be deliberate about the decorator order; a Retry *inside* the breaker can hide failures from it. Default order (Retry outermost) is usually what you want.
- **Idempotency is the gate, always.** Re-read Day 7 before enabling retries on anything that mutates state. The single most expensive resilience bug is an over-eager retry on a non-idempotent write.

### Observability of breakers — bridge to Day 25

A breaker you can't *see* is a liability — you find out it's been OPEN for an hour from angry users. `spring-boot-starter-actuator` already wired Resilience4j into **Micrometer**, exposing metrics like `resilience4j_circuitbreaker_state`, `..._calls{kind="failed|successful|not_permitted"}`, and `resilience4j_retry_calls`. On **Day 25 (Observability)** you'll scrape these with **Prometheus**, graph state transitions and failure rates in **Grafana**, and alert on "breaker OPEN > 1 min". The rule: **every resilience primitive must emit a metric and, ideally, a trace span** so an OPEN breaker shows up as a clearly-attributed, degraded-but-alive signal — not a silent mystery.

### Stretch goals

1. **Prove the retry/idempotency rule, painfully.** Add a `POST /charge` to the downstream that increments a counter, wrap it with `@Retry`, and watch a single client request produce **multiple charges** when the response is dropped. Then fix it with a Day 7 idempotency key and confirm the count stays at 1. This is the lesson that sticks.
2. **TIME_BASED window + slow-call breaker.** Switch the breaker to `sliding-window-type: TIME_BASED` and drive *only slowness* (no errors) — set `slow-call-duration-threshold` low and make the downstream sleep. Watch the breaker trip on the **slow-call rate** alone. Slowness, not errors, is what kills real systems.
3. **Bulkhead saturation.** Lower `max-concurrent-calls` to 2, fire 20 concurrent requests at a slow downstream, and observe `BulkheadFullException` / immediate rejection while the rest of the app stays responsive. Compare to *no* bulkhead, where every thread blocks.
4. **Programmatic config + events sink.** Replace the YAML with a `CircuitBreakerConfig`/`RetryConfig` bean built in Java, register an `onStateTransition` and `onRetry` listener, and push every transition to a log/metric. This is how you'd build a self-documenting resilience layer.

### Day 25 teaser

You built breakers and retries today — but a resilience layer you can't observe is a black box that *will* surprise you. **Day 25: Observability** wires this app to **Micrometer + Prometheus + Grafana** and adds **distributed tracing** (OpenTelemetry), so a tripped breaker, a retry storm, or a creeping p99 becomes a graph and an alert instead of a 3 a.m. mystery. The three pillars — **metrics, logs, traces** — turn "it feels slow" into "downstream `quotes` breaker has been OPEN for 90s, here's the trace."
