# Day 27: Rate Limiting with Token Buckets

| | |
|---|---|
| 🏗️ **Project** | **ThrottleGate** — a Bucket4j token-bucket rate limiter |
| ☕ **Java & language skills** | Servlet filters/interceptors, keying by API key/IP, 429 responses, Redis-backed atomic ops |
| 🧰 **Library / tool** | Bucket4j (+ Redis for distributed limits) |
| 🗄️ **DB / distributed-systems concept** | Rate limiting algorithms — token bucket vs leaky bucket vs windows |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. What problem are we actually solving?

A service has finite capacity: a thread pool, a connection pool (**Day 9**), a database that tips over past some QPS. If one client — a buggy retry loop, a scraper, a paying customer who wrote `while(true) callApi()` — sends traffic faster than you can serve it, three bad things happen:

1. **Overload.** Queues grow (**Day 4**), latency climbs for *everyone*, and eventually you fall over. This is the failure load shedding (**Day 24**) exists to prevent — but load shedding is reactive (drop work when already hot). Rate limiting is **proactive**: cap each client *before* the system is in trouble.
2. **Unfairness.** Without per-client limits, the loudest client starves the quiet ones. A shared resource with no quota is a tragedy of the commons.
3. **Abuse / cost.** Credential-stuffing, scraping, and accidental DDoS all look like "too many requests from one identity." A rate limit is the cheapest first line of defence.

Rate limiting answers one question per request: *"Has this client used more than its allotted share in the recent past? If so, reject with **429 Too Many Requests**."* The whole game is in how you define **"recent past"** and **"allotted share"** — that is the choice of algorithm.

### 2. The four algorithms

Think of every algorithm as answering: *given the timestamps of a client's recent requests, is this new one allowed?*

#### (a) Fixed-window counter

Divide time into fixed windows (e.g. each calendar minute). Keep one integer counter per client per window. Increment on each request; reject when it exceeds the limit; reset to 0 at the window boundary.

```
limit = 10 / minute
12:00:00 ── window A ──┐ 12:01:00 ── window B ──┐
counter resets to 0 at every boundary
```

- **Pro:** trivial — a single `INCR` in Redis with a TTL. O(1) memory per client.
- **Con — the boundary burst.** A client can send 10 requests at `12:00:59` and another 10 at `12:01:00`. That's **20 requests in one second**, while every individual window stayed "under 10/min." Fixed windows allow up to **2× the limit** across a boundary. For coarse abuse protection that's fine; for protecting a fragile backend it's a real foot-gun.

#### (b) Sliding-window log

Store the **timestamp of every request** (e.g. a Redis sorted set). On each request, drop timestamps older than `now - window`, count what remains, allow iff `count < limit`.

- **Pro:** *exact*. No boundary artifact — the window genuinely slides.
- **Con:** **O(N) memory per client**, where N = limit. At 10k req/min limits across millions of clients this is expensive, and each check trims + counts the set. Accurate but heavy.

#### (c) Sliding-window counter (approximation)

A clever middle ground. Keep the current and previous fixed-window counters, and *weight* the previous window by how far you are into the current one:

```
estimate = current_count + previous_count * (1 - elapsed_fraction_of_current_window)
```

- **Pro:** O(1) memory, no hard boundary burst, very close to the log's accuracy in practice. This is what Cloudflare and many gateways actually run.
- **Con:** an approximation — it assumes traffic in the previous window was uniformly distributed, so it can be slightly off for spiky traffic. Almost always good enough.

#### (d) Token bucket — the one we'll implement

Model the limit as a **bucket of tokens**. The bucket holds at most `capacity` tokens and **refills at a steady rate** (e.g. 10 tokens per minute). Each request **takes one token**; if the bucket is empty, the request is rejected (or made to wait).

```
capacity = 10, refill = 10 tokens / minute  (≈ 1 token every 6s)

  ┌──────────┐  drip 1 token / 6s
  │ ●●●●●●●●● │ ◄─────────────
  │ ●        │
  └────┬─────┘
       │ each request removes 1 token
       ▼
   allowed if a token is available, else 429
```

- **Burst handling — this is the key feature.** Because tokens *accumulate up to capacity*, a client that's been idle can spend the full bucket in a burst (10 requests instantly), then is throttled down to the steady refill rate. This matches how real clients behave (bursty, then quiet) and is forgiving without being exploitable: long-run throughput is bounded by the refill rate, short-run burst is bounded by capacity. You tune the two **independently** (`capacity` = burst size, `refillRate` = sustained rate).
- **Cost:** O(1) state per client — just `(tokenCount, lastRefillTimestamp)`. Cheap to store, cheap to update atomically. That's why it distributes well.

#### (e) Leaky bucket — the close cousin

A queue that **drains (leaks) at a constant rate**. Requests enter the queue; if it's full they're dropped. Output rate is perfectly smooth.

- **Token bucket vs leaky bucket:** they're duals. **Token bucket allows bursts** (tokens piled up while idle); **leaky bucket smooths them out** (output is always the leak rate, no matter how bursty the input). Leaky bucket is what you want for **traffic shaping** — feeding a downstream that demands a steady rate (e.g. a fixed-throughput legacy system). Token bucket is what you want for **rate limiting an API**, where occasional bursts are fine and you only care about sustained rate. We pick token bucket here because APIs want burst tolerance.

> **GCRA** (Generic Cell Rate Algorithm), used by Redis's `redis-cell` module, is a clever O(1) formulation that's equivalent to a leaky bucket but stores only a single timestamp (the "theoretical arrival time"). Mentioned in the deep-dive — same family.

**Summary table**

| Algorithm | Memory/client | Burst behaviour | Accuracy | Typical use |
|---|---|---|---|---|
| Fixed window | O(1) | 2× at boundaries | poor near edges | coarse abuse cap |
| Sliding log | O(limit) | none | exact | low-volume, precise |
| Sliding counter | O(1) | none | ~exact | high-volume gateways |
| **Token bucket** | **O(1)** | **bounded by capacity** | exact (rate) | **API rate limiting** |
| Leaky bucket / GCRA | O(1) | smoothed away | exact | traffic shaping |

### 3. The distributed rate-limiting problem

Here's the trap that mirrors **Day 16**'s caching trap. You run **3 instances** behind a load balancer, each with an in-JVM Bucket4j bucket for client `K`:

```
limit intended: 10 / min for client K
        ┌─ node A: bucket K = 10 ─┐
client K┼─ node B: bucket K = 10 ─┤  ← three independent buckets!
        └─ node C: bucket K = 10 ─┘
   effective limit experienced by K ≈ 30 / min
```

Each node only counts the requests *it* saw. A client spreading traffic across nodes (which the load balancer does *for* it) gets **N× the limit**, where N = instance count — and the limit silently drifts as you autoscale. Local counters **cannot coordinate**; the count is shared mutable state, so it must live in **one shared place**: Redis.

The hard part is **atomicity**. "Read token count, decide, write new count" is a read-modify-write race. Two nodes that read `1 token` concurrently both think they may proceed → you've over-served. The fix is to make the whole check-and-decrement **atomic on the Redis side** — a **Lua script** (`EVAL`) that Redis runs single-threaded (Day 16) as one indivisible operation. Bucket4j's Redis backend ships exactly this: the bucket state is a value in Redis and every consume is an atomic Lua call. You write almost no Redis code — you just swap the bucket's backing store.

### 4. Returning 429 correctly — and *why* the headers matter

Rejecting is necessary but not sufficient; **how** you reject determines whether well-behaved clients hammer you harder or back off gracefully.

- **`429 Too Many Requests`** (RFC 6585) — the correct status. Not 503 (that's "server broken/overloaded", retry soon), not 403 (that's "forbidden, don't retry"). 429 says *"your request was fine, you're just going too fast."*
- **`Retry-After: <seconds>`** — tells the client *how long to wait* before retrying. Without it, a client retries immediately, the retry also fails, and you've turned a rate limit into a busy-loop amplifier. With it, SDKs sleep the right amount. This is the single most important header.
- **`RateLimit-Limit` / `RateLimit-Remaining` / `RateLimit-Reset`** (the IETF `RateLimit` header fields, formerly `X-RateLimit-*`) — sent on **every** response (200s too), so clients can *self-pace before* hitting the wall. `Remaining` lets a good client slow down at 1 token left instead of getting surprised by a 429. This is cooperative backpressure: you're handing the client the information to shape its own traffic (**Day 4**).

> Why expose `Remaining` on success responses at all? Because the best rate limit is the one the client never hits — give SDKs the data to throttle themselves and you shed less load and generate fewer angry support tickets.

### 5. Where this sits relative to Day 24

On **Day 24** you used Resilience4j's `RateLimiter`. It's a **fixed-window** limiter: "N permits per refresh period," threads that can't get a permit **block** up to a timeout, then fail. It's designed to protect a *downstream dependency* from *your own* outbound calls (a client-side throttle), in-process only.

Bucket4j is a **token bucket** designed to protect *your* API from *inbound* per-client traffic, supports bursts, and — critically — has a **distributed (Redis) backend**. Rule of thumb:

- **Resilience4j RateLimiter** → throttle *outbound* calls to a fragile dependency, single instance, blocking semantics.
- **Bucket4j** → per-client *inbound* API quotas, burst-friendly, distributed across the fleet.

---

## Prerequisites

- The Spring Boot orders service from **Days 9–15** (a controller exposing the orders API).
- A running Redis for the distributed step — reuse the Docker Redis from **Day 16**:
  ```bash
  docker run -d --name day27-redis -p 6379:6379 redis:7
  ```

### Maven dependencies

```xml
<!-- Core token-bucket engine: the in-process limiter -->
<dependency>
    <groupId>com.bucket4j</groupId>
    <artifactId>bucket4j_jdk17-core</artifactId>
    <version>8.14.0</version>
</dependency>

<!-- Distributed backend over Redis (Lettuce). Pulls in the Lua glue. -->
<dependency>
    <groupId>com.bucket4j</groupId>
    <artifactId>bucket4j_jdk17-lettuce</artifactId>
    <version>8.14.0</version>
</dependency>

<!-- From Day 16 — Lettuce client + RedisClient bean -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-redis</artifactId>
</dependency>
```

> Bucket4j 8.x publishes JDK-versioned artifacts (`bucket4j_jdk17-*`). On JDK 11 use `bucket4j_jdk11-*`. Older guides reference plain `bucket4j-core` (7.x) — fine too, but the API below targets 8.x.

---

## 🛠️ Project Walkthrough — ThrottleGate

Roll up your sleeves: from here on you build the limiter step by step, run it, and watch the 200 → 429 transition for real.

---

## Step 1 — An in-process token bucket per API key

We rate-limit by **API key** (read from an `X-API-Key` header), falling back to the client IP for anonymous traffic. Each key gets its own bucket: **capacity 10, refill 10 tokens / minute** (so a fresh/idle key may burst 10 requests, then is metered to ~1 every 6s).

First, a small factory that builds the bucket configuration and caches one `Bucket` per key in memory.

```java
package com.example.orders.ratelimit;

import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import io.github.bucket4j.BucketConfiguration;
import io.github.bucket4j.Refill;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

/**
 * Builds and caches one token bucket per client key (in-process variant).
 *
 * Bandwidth: capacity 10 (the burst size) refilled "greedily" at 10 tokens
 * per minute (the sustained rate). Greedy refill drips tokens continuously
 * (~1 every 6s) rather than dumping 10 at the top of each minute — that drip
 * is what makes a token bucket smoother than a fixed window.
 */
@Component
public class RateLimitConfig {

    private static final long CAPACITY = 10;
    private static final Duration WINDOW = Duration.ofMinutes(1);

    private final ConcurrentHashMap<String, Bucket> cache = new ConcurrentHashMap<>();

    /** The reusable bucket spec — also used by the distributed variant in Step 3. */
    public Supplier<BucketConfiguration> configuration() {
        Bandwidth limit = Bandwidth.classic(
                CAPACITY,
                Refill.greedy(CAPACITY, WINDOW));   // 10 tokens / minute, dripped
        return () -> BucketConfiguration.builder()
                .addLimit(limit)
                .build();
    }

    public long capacity() {
        return CAPACITY;
    }

    /** Resolve (or lazily create) the in-memory bucket for this key. */
    public Bucket resolveBucket(String key) {
        return cache.computeIfAbsent(key, k ->
                Bucket.builder()
                        .addLimit(Bandwidth.classic(CAPACITY, Refill.greedy(CAPACITY, WINDOW)))
                        .build());
    }
}
```

Now the filter. A `OncePerRequestFilter` runs before the controllers, so we reject early without burning any business logic. The crucial Bucket4j call is `tryConsumeAndReturnRemaining(1)`, which **atomically** takes a token (within the JVM here) and tells us how many remain and, if denied, how many nanoseconds until a token frees up — exactly what we need for `Retry-After`.

```java
package com.example.orders.ratelimit;

import io.github.bucket4j.Bucket;
import io.github.bucket4j.ConsumptionProbe;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.time.Duration;

@Component
@Order(1)   // run before auth/business filters
public class RateLimitFilter extends OncePerRequestFilter {

    private final RateLimitConfig config;

    public RateLimitFilter(RateLimitConfig config) {
        this.config = config;
    }

    /** Only rate-limit the orders API; let everything else through. */
    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith("/api/orders");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {

        String clientKey = resolveClientKey(request);
        Bucket bucket = config.resolveBucket(clientKey);

        // Atomically try to take 1 token. The probe tells us the outcome AND
        // the remaining tokens / nanos-to-refill in a single call (no race).
        ConsumptionProbe probe = bucket.tryConsumeAndReturnRemaining(1);

        // Informational headers on EVERY response so clients can self-pace.
        response.setHeader("RateLimit-Limit", String.valueOf(config.capacity()));
        response.setHeader("RateLimit-Remaining", String.valueOf(probe.getRemainingTokens()));

        if (probe.isConsumed()) {
            long resetSeconds = Duration.ofNanos(probe.getNanosToWaitForRefill()).getSeconds();
            response.setHeader("RateLimit-Reset", String.valueOf(resetSeconds));
            chain.doFilter(request, response);   // allowed — proceed to controller
            return;
        }

        // Denied: how long until a token is available?
        long retryAfterSeconds = Math.max(1, Duration.ofNanos(probe.getNanosToWaitForRefill()).getSeconds());
        response.setStatus(HttpServletResponse.SC_TOO_MANY_REQUESTS); // 429
        response.setHeader("Retry-After", String.valueOf(retryAfterSeconds));
        response.setHeader("RateLimit-Reset", String.valueOf(retryAfterSeconds));
        response.setContentType("application/json");
        response.getWriter().write("""
                {"error":"rate_limited","message":"Too many requests. Retry after %d seconds.","retryAfter":%d}
                """.formatted(retryAfterSeconds, retryAfterSeconds));
    }

    /** Prefer the API key; fall back to client IP for anonymous callers. */
    private String resolveClientKey(HttpServletRequest request) {
        String apiKey = request.getHeader("X-API-Key");
        if (StringUtils.hasText(apiKey)) {
            return "key:" + apiKey;
        }
        // Behind a proxy/LB, trust X-Forwarded-For only if the LB sets it reliably.
        String forwarded = request.getHeader("X-Forwarded-For");
        String ip = StringUtils.hasText(forwarded)
                ? forwarded.split(",")[0].trim()
                : request.getRemoteAddr();
        return "ip:" + ip;
    }
}
```

That's a fully working in-process per-client limiter. Restart the app and it's live on `/api/orders`.

---

## Step 2 — Drive it past the limit with curl

Send 13 requests as the same API key, faster than the bucket refills:

```bash
for i in $(seq 1 13); do
  printf "req %2d -> " "$i"
  curl -s -o /dev/null \
       -w "HTTP %{http_code}  remaining=%header{ratelimit-remaining}  retry-after=%header{retry-after}\n" \
       -H "X-API-Key: acme-prod-key" \
       http://localhost:8080/api/orders
done
```

### Expected output

The first 10 succeed (the bucket starts full = burst allowance), then it's empty and the rest are rejected with `Retry-After`:

```
req  1 -> HTTP 200  remaining=9   retry-after=
req  2 -> HTTP 200  remaining=8   retry-after=
req  3 -> HTTP 200  remaining=7   retry-after=
req  4 -> HTTP 200  remaining=6   retry-after=
req  5 -> HTTP 200  remaining=5   retry-after=
req  6 -> HTTP 200  remaining=4   retry-after=
req  7 -> HTTP 200  remaining=3   retry-after=
req  8 -> HTTP 200  remaining=2   retry-after=
req  9 -> HTTP 200  remaining=1   retry-after=
req 10 -> HTTP 200  remaining=0   retry-after=
req 11 -> HTTP 429  remaining=0   retry-after=6
req 12 -> HTTP 429  remaining=0   retry-after=5
req 13 -> HTTP 429  remaining=0   retry-after=5
```

`retry-after` shrinks as the greedy refill drips the next token (~6s). Wait 6 seconds, re-run a single request, and you'll get one more `200` — the bucket earned a token back. A *different* `X-API-Key` value gets its own fresh bucket: proof the limit is **per client**, not global.

---

## Step 3 — Make it distributed with Redis

Run two instances of this app and the Step 1 limiter lets `acme-prod-key` do ~20/min (Section 3's trap). Fix it by moving bucket state into Redis. With Bucket4j you change *only the bucket factory* — the filter is untouched, because it programs against the same `Bucket` interface.

First, a Redis-backed proxy manager (uses the Lettuce client from **Day 16**):

```java
package com.example.orders.ratelimit;

import io.github.bucket4j.distributed.proxy.ClientSideConfig;
import io.github.bucket4j.redis.lettuce.cas.LettuceBasedProxyManager;
import io.lettuce.core.RedisClient;
import io.lettuce.core.codec.ByteArrayCodec;
import io.lettuce.core.codec.StringCodec;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

/**
 * Active only when ratelimit.distributed=true. Builds a ProxyManager whose
 * buckets live in Redis. Every consume becomes an atomic CAS/Lua round-trip,
 * so all app instances share one authoritative counter per key.
 */
@Configuration
@ConditionalOnProperty(name = "ratelimit.distributed", havingValue = "true")
public class DistributedRateLimitConfig {

    @Bean(destroyMethod = "shutdown")
    public RedisClient redisClient() {
        return RedisClient.create("redis://localhost:6379");
    }

    @Bean
    public LettuceBasedProxyManager<byte[]> proxyManager(RedisClient redisClient) {
        var connection = redisClient.connect(
                io.lettuce.core.codec.RedisCodec.of(ByteArrayCodec.INSTANCE, ByteArrayCodec.INSTANCE));
        return LettuceBasedProxyManager.builderFor(connection)
                .withClientSideConfig(
                        // Expire idle keys so Redis doesn't accumulate dead buckets.
                        ClientSideConfig.getDefault()
                                .withExpirationAfterWriteStrategy(
                                        io.github.bucket4j.distributed.ExpirationAfterWriteStrategy
                                                .basedOnTimeForRefillingBucketUpToMax(Duration.ofMinutes(2))))
                .build();
    }
}
```

Then make the bucket resolver prefer the distributed store when available. We adjust `RateLimitConfig` to optionally hold a `ProxyManager` and hand out Redis-backed proxy buckets:

```java
// --- additions to RateLimitConfig ---
import io.github.bucket4j.distributed.proxy.ProxyManager;
import org.springframework.beans.factory.annotation.Autowired;
import java.nio.charset.StandardCharsets;

@Autowired(required = false)   // present only in distributed mode
private ProxyManager<byte[]> proxyManager;

/** Returns a Redis-backed bucket if configured, else the in-process one. */
public Bucket resolveBucket(String key) {
    if (proxyManager != null) {
        byte[] redisKey = ("rl:" + key).getBytes(StandardCharsets.UTF_8);
        // builder() lazily creates the bucket in Redis on first touch.
        return proxyManager.builder().build(redisKey, configuration());
    }
    return cache.computeIfAbsent(key, k ->
            Bucket.builder()
                    .addLimit(Bandwidth.classic(CAPACITY, Refill.greedy(CAPACITY, WINDOW)))
                    .build());
}
```

Now run **two** instances with `--ratelimit.distributed=true --server.port=8080` and `...=8081`, point both at the same Redis, and re-run the curl loop split across ports. The 11th request is rejected **no matter which port served the first ten** — the bucket is a single Redis value, and every `tryConsume` is an atomic Lua `EVAL`, so the two instances cannot over-serve.

Inspect the live state:

```bash
docker exec -it day27-redis redis-cli --scan --pattern 'rl:*'
# rl:key:acme-prod-key
```

> **Why this is correct under concurrency:** the read-decide-write is one server-side script, and Redis executes scripts single-threaded (**Day 16**). Two instances calling `tryConsume` at the same instant are serialized by Redis — no lost update, no over-admission. This is the same atomicity primitive you'll generalise into **distributed locks on Day 28**.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Sliding-window log vs counter — accuracy vs cost.** The log is exact but O(limit) memory and trims a sorted set per request; the counter is O(1) but assumes uniform traffic in the prior window. At fleet scale (millions of keys) the memory difference decides it — almost everyone runs the counter approximation and accepts a few-percent error. Token bucket sidesteps the question: it's O(1) *and* exact on sustained rate.
- **GCRA.** If you want leaky-bucket smoothing with single-timestamp state, GCRA (e.g. Redis's `redis-cell` `CL.THROTTLE`) is the elegant choice — it returns "allowed?" plus retry-after in one atomic command. Worth knowing when token-bucket bursts are *undesirable* (you genuinely want a smooth output rate).
- **Where to enforce — gateway vs app.** Per-client limits ideally live at the **edge/API gateway** (Kong, Envoy, Spring Cloud Gateway, NGINX) so abusive traffic is rejected *before* it consumes an app thread or a DB connection (**Day 9**). In-app limiting (what we built) is still valuable as **defence in depth** and for limits that need business context (per-tenant plan tier, per-endpoint cost) the gateway doesn't know. Mature systems do both.
- **Global vs per-route vs weighted.** Real APIs layer limits: a cheap `GET` and an expensive `POST /orders` (which writes to the DB, the outbox on **Day 20**, emits to Kafka on **Day 18**) shouldn't cost the same token. Use multiple `Bandwidth` limits per bucket, or consume **N tokens** for expensive endpoints (`tryConsume(weight)`) — token bucket makes weighted/cost-based limiting natural.
- **Fairness & quotas.** Per-key buckets give *isolation* (one client can't starve others) but not *fairness under contention* of a shared backend — that's where you combine rate limiting with concurrency limits (bulkheads, Day 24) and queue admission (Day 4). Tiered quotas (free=10/min, pro=1000/min) are just per-key bucket configs looked up from the key's plan.
- **Time source & clock skew.** Distributed buckets must use a single clock. Bucket4j's Redis backend computes refill **on the Redis server**, so app-instance clock skew is irrelevant — another reason to put the math where the state is.
- **Failure mode — fail open or fail closed?** If Redis is down, does the limiter allow all traffic (fail open, prioritise availability) or reject all (fail closed, prioritise protection)? There's no universal answer; decide deliberately and wrap the Redis call so a connection error doesn't 500 every request. Often: fail open but alert loudly.

### Stretch goals

1. **Weighted / tiered limits.** Add a second, larger `Bandwidth` for "pro" keys and look up the tier from the API key. Make `POST /api/orders` consume 5 tokens and `GET` consume 1 via `tryConsume(weight)`.
2. **Resilience4j head-to-head.** Wire a Resilience4j `RateLimiter` in front of the *outbound* call your service makes to a downstream (e.g. the payment service) and write a short note in the README on why you used Bucket4j inbound and Resilience4j outbound.
3. **Verify distribution with Testcontainers.** Using **Day 23**'s Testcontainers, spin a Redis container and assert in an integration test that the 11th `tryConsume` across two `ProxyManager` instances (simulating two app nodes) returns denied — the distributed guarantee, in CI.
4. **GCRA comparison.** Add the `redis-cell` module to your Redis container and rate-limit one endpoint with `CL.THROTTLE`; compare its smooth output against Bucket4j's bursty behaviour under an identical curl loop.

### Day 28 teaser

Today's distributed bucket leaned on Redis doing one atomic thing at a time. **Day 28 — Distributed Locks** generalises that primitive: when two instances must *not* run the same critical section concurrently (process this outbox row once, run this scheduled job on one node), you need a lock that works *across* JVMs. We'll build one with Redis `SET key val NX PX` and the Redlock idea, confront the fencing-token problem (what happens when a lock holder pauses for a GC and its lease expires), and see why "I have the lock" is a lie without a fencing token. The `SETNX` preview from Day 16 finally pays off.
