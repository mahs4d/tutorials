# Day 16: Distributed Caching with Redis

| | |
|---|---|
| 🏗️ **Project** | **RedisCache** — a shared Redis cache-aside layer |
| ☕ **Java & language skills** | RedisTemplate, serialization, Docker for deps, cache-aside coding |
| 🧰 **Library / tool** | Spring Data Redis (Lettuce) |
| 🗄️ **DB / distributed-systems concept** | Distributed/shared cache, cache-aside & dual-write consistency |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. Why the Day 15 cache stops working at scale

On Day 15 you put a Caffeine cache *inside the JVM heap*. For a single process that is the fastest cache that exists: a `ConcurrentHashMap` lookup, nanoseconds, no network. The catch is in the word *inside*.

Picture the production reality: you run **three** instances of the service behind a load balancer (for throughput and HA). Each instance has its **own private heap**, therefore its **own private Caffeine cache**. Now:

```
            ┌──────── load balancer ────────┐
            │              │                 │
        ┌───▼───┐      ┌───▼───┐         ┌───▼───┐
        │ node A│      │ node B│         │ node C│
        │ cache │      │ cache │         │ cache │
        │ {7:$9}│      │ {7:$9}│         │ {7:$9}│
        └───┬───┘      └───┬───┘         └───┬───┘
            └──────────────┴─────────────────┘
                           │
                      ┌────▼────┐
                      │   DB    │  product 7 price = $9
                      └─────────┘
```

A request to **update product 7's price to $5** lands on node A. Node A writes `$5` to the DB and evicts `7` from **its** cache. But nodes B and C never heard about it — they keep serving the stale `$9` from their local caches until their TTL expires. You now have **read-your-own-writes violations** that depend on *which node the load balancer happened to pick*. That is the worst kind of bug: non-deterministic, environment-dependent, invisible on a single-node laptop.

The fix is structural: move the cache **out of the process** into a service every node shares. That service is Redis.

```
   node A ──┐
   node B ──┼──►  Redis  ◄──── one shared copy: {7:$5}
   node C ──┘     (+ DB behind it)
```

One copy, one source of cache truth. An eviction by node A is seen by B and C on their next read. The cost is that every cache hit is now a **network round-trip** (~0.1–1 ms on a LAN) instead of a heap lookup (~100 ns). That trade — correctness across nodes for a sub-millisecond network hop — is almost always worth it, and Day 16's *Going deeper* section shows how **two-tier caching** wins back the nanoseconds without losing correctness.

> **Redis is not just a cache.** Redis ("REmote DIctionary Server") is a *data-structure server*: keys map to strings, hashes, lists, sets, sorted sets, streams, bitmaps, HyperLogLogs, geospatial indexes. We use the string/hash subset today, but the same instance underpins **distributed locks (Day 28)**, **rate limiting (Day 27)**, and Kafka-like streams. Treat it as a Swiss-army networked data structure, not a dumb key-value bag.

### 2. Cache-aside vs read-through / write-through / write-behind

These are the four canonical caching topologies. The distinction is **who talks to the database** and **when**.

**Cache-aside (lazy loading)** — *the application* owns the logic. This is what you'll implement manually today.

```
read:   v = cache.get(k)
        if v == null:                 # miss
            v = db.load(k)
            cache.set(k, v, ttl)
        return v

write:  db.save(k, v)
        cache.evict(k)                # invalidate, do NOT update
```

- Pros: simple, resilient (if Redis is down, you can still hit the DB), only caches data that's actually read.
- Cons: first read after a miss/eviction is slow (the "cold" read); application code is responsible for consistency.

**Read-through** — the *cache* sits in front of the DB and loads on a miss itself; the app only ever talks to the cache. Spring's `@Cacheable` is effectively read-through *from the app's point of view* (the app calls a method; the cache abstraction handles miss → load → store).

**Write-through** — every write goes to the cache, which **synchronously** writes to the DB before returning. Cache and DB are always consistent, but every write pays both latencies.

**Write-behind (write-back)** — writes go to the cache and return immediately; the cache flushes to the DB **asynchronously** in batches. Fast writes, great for high-write workloads, but you risk **data loss** if the cache dies before flushing, and you've created a second dual-write problem of your own.

| Pattern | Who loads DB | Write path | Risk |
|---|---|---|---|
| Cache-aside | App | DB then evict cache | Stale on race; cold reads |
| Read-through | Cache | (often paired w/ write-through) | Cache is critical path |
| Write-through | Cache (sync) | cache+DB synchronously | Slow writes |
| Write-behind | Cache (async) | cache now, DB later | Data loss window |

For a typical read-heavy Spring service, **cache-aside is the default and the right default**. We implement it explicitly first (so you understand every step) and then show the annotation-driven equivalent.

### 3. TTL and invalidation

A cache entry can become wrong the instant the underlying row changes. Two complementary defenses:

1. **TTL (time-to-live):** every entry expires after N seconds. This bounds staleness *unconditionally* — even if your invalidation logic has a bug, the worst-case stale window is the TTL. **Always set a TTL.** A cache without TTL is a memory leak with extra steps.
2. **Explicit invalidation:** on a write, proactively remove the affected key so the next read reloads fresh data.

The senior rule for invalidation: **delete, don't update.** When you change a row, *evict* the cache key rather than writing the new value into the cache. Why? Because writing the new value into the cache re-introduces a dual-write race (below), and because the freshly written value might itself be stale by the time it lands. Deleting is idempotent and forces the next reader to load the authoritative value from the DB.

### 4. The dual-write consistency problem

The unavoidable truth: **the DB and the cache are two separate systems, and you cannot atomically write to both.** Any time you touch both, there is a window where they can diverge. Consider the naive "update the cache on write" approach with two concurrent operations on key 7:

```
Reader R (cache miss, slow):                Writer W (update price to $5):
  t0  read DB  -> $9 (old value)
                                              t1  write DB -> $5
                                              t2  set cache[7] = $5
  t3  set cache[7] = $9   <-- STALE WRITE WINS
```

R loaded the old value at t0, got descheduled, and writes it into the cache *after* W's update — the cache is now permanently `$9` (stale) until TTL. This is why **write-through-into-cache-on-read is dangerous**, and why we prefer **evict over update**:

- **Mitigation 1 — delete instead of update on writes.** On a write, `db.save()` then `cache.delete(k)`. The next reader misses and reloads from the DB. The stale-write race still exists in a narrow form (reader's late `set` after a writer's `delete`), which is why you also need:
- **Mitigation 2 — always set a TTL.** It caps the stale window deterministically. Belt and suspenders.
- **Mitigation 3 — order: DB first, then cache.** If the DB write fails, you never touched the cache; the cache stays *consistently old* (safe, will self-heal at TTL) rather than *inconsistently new*.
- **Mitigation 4 — for stronger guarantees, decouple via the log.** The bulletproof fix is to derive cache invalidations from the DB's change stream (the **Outbox pattern, Day 20**, or CDC) so the invalidation is part of the same transaction as the write. That's beyond today, but know it exists — the dual-write problem is *the* recurring theme of this course (you'll meet it again in **2PC / Day 17** and **Outbox / Day 20**).

There is no way to make a cache + DB perfectly consistent without either distributed transactions (slow, fragile) or a single source of truth feeding both. Cache-aside + delete + TTL is the pragmatic 99.9% answer.

---

## Prerequisites

You're continuing the Spring Boot + JPA service from **Days 9–15**, with a `Product` entity and a `ProductRepository`. You'll need a running Redis.

### Run Redis (one line)

```bash
docker run -d --name redis-day16 -p 6379:6379 redis:7-alpine
```

That pulls the official image and exposes Redis on `localhost:6379`. To stop/remove later: `docker stop redis-day16 && docker rm redis-day16`. (No persistence configured — perfect for a cache. We'll containerize more deliberately on Days 18 and 23.)

Sanity check:

```bash
docker exec -it redis-day16 redis-cli ping
# -> PONG
```

### Maven dependency

Add the starter to `pom.xml` (Lettuce comes transitively; no extra client dep needed):

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-redis</artifactId>
</dependency>
```

### Configuration (`application.yml`)

```yaml
spring:
  data:
    redis:
      host: localhost
      port: 6379
      timeout: 2s            # fail fast if Redis is unreachable
      lettuce:
        pool:
          max-active: 16     # bounded connection pool (recall pooling, Day 9)
          max-idle: 8
          min-idle: 2
```

Spring Boot autoconfigures a `LettuceConnectionFactory` from these properties. We override the templates' serializers below.

---

## 🛠️ Project Walkthrough — RedisCache

Follow these steps hands-on: wire up Redis, implement cache-aside by hand and via annotations, then run the service and inspect the keys live.

### The domain (recap from prior days)

```java
package com.example.catalog.product;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import java.io.Serializable;
import java.math.BigDecimal;

@Entity
public class Product implements Serializable {  // Serializable: lets ANY serializer work

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String name;
    private BigDecimal price;

    protected Product() { }  // JPA needs a no-arg ctor

    public Product(String name, BigDecimal price) {
        this.name = name;
        this.price = price;
    }

    public Long getId() { return id; }
    public String getName() { return name; }
    public BigDecimal getPrice() { return price; }
    public void setName(String name) { this.name = name; }
    public void setPrice(BigDecimal price) { this.price = price; }

    @Override public String toString() {
        return "Product{id=%d, name='%s', price=%s}".formatted(id, name, price);
    }
}
```

```java
package com.example.catalog.product;

import org.springframework.data.jpa.repository.JpaRepository;

public interface ProductRepository extends JpaRepository<Product, Long> { }
```

---

### Step 1 — Configure `RedisTemplate` with proper serializers

The single biggest footgun in Spring Data Redis is **the default serializer is `JdkSerializationRedisSerializer`**, which writes opaque Java-serialized binary blobs. They're unreadable in `redis-cli`, brittle across class-version changes, and a security liability. We replace it with **string keys + JSON values**.

```java
package com.example.catalog.config;

import com.example.catalog.product.Product;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.serializer.GenericJackson2JsonRedisSerializer;
import org.springframework.data.redis.serializer.StringRedisSerializer;

@Configuration
public class RedisConfig {

    /**
     * A RedisTemplate keyed by String, valued by Product, serialized as JSON.
     * - Keys: human-readable strings  -> "product:7"
     * - Values: JSON                  -> {"@class":"...Product","id":7,...}
     * GenericJackson2JsonRedisSerializer embeds the @class type hint so it can
     * deserialize back into the concrete type without us specifying it.
     */
    @Bean
    public RedisTemplate<String, Product> productRedisTemplate(RedisConnectionFactory cf) {
        RedisTemplate<String, Product> template = new RedisTemplate<>();
        template.setConnectionFactory(cf);

        StringRedisSerializer keySerializer = new StringRedisSerializer();
        GenericJackson2JsonRedisSerializer valueSerializer =
                new GenericJackson2JsonRedisSerializer();

        // Keys AND hash-keys as plain strings
        template.setKeySerializer(keySerializer);
        template.setHashKeySerializer(keySerializer);
        // Values AND hash-values as JSON
        template.setValueSerializer(valueSerializer);
        template.setHashValueSerializer(valueSerializer);

        template.afterPropertiesSet();
        return template;
    }
}
```

`StringRedisTemplate` is autoconfigured for you (string keys + string values) and is handy for counters/flags — we'll reuse it for the stampede lock preview. The `@class` hint added by `GenericJackson2JsonRedisSerializer` is what lets it round-trip a `Product` without you passing `Product.class` on every read.

---

### Step 2 — Manual cache-aside service with `RedisTemplate`

This is the heart of the day: cache-aside written out by hand so every step is explicit.

```java
package com.example.catalog.product;

import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.util.Optional;

@Service
public class ProductCacheAsideService {

    private static final String KEY_PREFIX = "product:";
    private static final Duration TTL = Duration.ofMinutes(10);  // bounds staleness

    private final ProductRepository repository;
    private final RedisTemplate<String, Product> redis;
    private final ValueOperations<String, Product> ops;

    public ProductCacheAsideService(ProductRepository repository,
                                    RedisTemplate<String, Product> productRedisTemplate) {
        this.repository = repository;
        this.redis = productRedisTemplate;
        this.ops = productRedisTemplate.opsForValue();
    }

    private String key(Long id) {
        return KEY_PREFIX + id;
    }

    /**
     * READ — classic cache-aside:
     *   1. try cache
     *   2. on miss, load from DB
     *   3. populate cache with a TTL
     *   4. return
     */
    public Optional<Product> getById(Long id) {
        String k = key(id);

        Product cached = ops.get(k);             // 1. cache lookup (network hop)
        if (cached != null) {
            return Optional.of(cached);          //    HIT
        }

        // 2. MISS -> authoritative load from the database
        Optional<Product> fromDb = repository.findById(id);

        // 3. populate cache only if found (don't cache null here; see note below)
        fromDb.ifPresent(p -> ops.set(k, p, TTL));

        return fromDb;                            // 4. return
    }

    /**
     * WRITE — DB first, then EVICT (delete, do not update) the cache.
     * Ordering matters: if the DB write throws, we never touched the cache,
     * so the cache stays consistently-old and self-heals at TTL.
     */
    @Transactional
    public Product updatePrice(Long id, java.math.BigDecimal newPrice) {
        Product p = repository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("No product " + id));
        p.setPrice(newPrice);
        Product saved = repository.save(p);       // 1. DB is the source of truth

        redis.delete(key(id));                    // 2. invalidate, DON'T set
        return saved;
    }

    @Transactional
    public Product create(Product product) {
        Product saved = repository.save(product);
        // No cache write on create: let the first reader lazily populate it.
        return saved;
    }
}
```

Two senior notes baked into this code:
- **We evict, we don't update** (`redis.delete`, not `ops.set`) on write — see dual-write mitigation 1.
- **We don't cache the negative (`null`) result** for a missing id here. Caching misses ("negative caching") defends against repeated lookups of non-existent keys, but a naive null-cache can mask a just-created row. If you do negative-cache, use a *short* TTL and a tombstone sentinel. (Stretch goal below.)

---

### Step 3 — The `@Cacheable` / `@CacheEvict` variant backed by Redis

The same behavior, declaratively, via Spring's cache abstraction. First, configure a `RedisCacheManager` so the abstraction stores entries *in Redis* (instead of the default in-memory `ConcurrentMapCache`, which would re-create the Day-15 per-node problem!).

```java
package com.example.catalog.config;

import org.springframework.cache.annotation.EnableCaching;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.cache.RedisCacheConfiguration;
import org.springframework.data.redis.cache.RedisCacheManager;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.serializer.GenericJackson2JsonRedisSerializer;
import org.springframework.data.redis.serializer.RedisSerializationContext;

import java.time.Duration;

@Configuration
@EnableCaching   // turns on @Cacheable / @CacheEvict / @CachePut processing
public class CacheConfig {

    @Bean
    public RedisCacheManager cacheManager(RedisConnectionFactory cf) {
        RedisCacheConfiguration defaults = RedisCacheConfiguration.defaultCacheConfig()
                .entryTtl(Duration.ofMinutes(10))            // global default TTL
                .disableCachingNullValues()                  // don't store nulls
                .serializeValuesWith(RedisSerializationContext.SerializationPair
                        .fromSerializer(new GenericJackson2JsonRedisSerializer()));

        return RedisCacheManager.builder(cf)
                .cacheDefaults(defaults)
                // per-cache override: "products" gets a 5-minute TTL
                .withCacheConfiguration("products",
                        defaults.entryTtl(Duration.ofMinutes(5)))
                .build();
    }
}
```

Now the service becomes almost annotations-only:

```java
package com.example.catalog.product;

import org.springframework.cache.annotation.CacheEvict;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;

@Service
public class ProductAnnotatedService {

    private final ProductRepository repository;

    public ProductAnnotatedService(ProductRepository repository) {
        this.repository = repository;
    }

    /**
     * Read-through from the app's perspective: on a miss Spring runs the method
     * body, then stores the return value under key "products::<id>" in Redis.
     * On a hit the method body is SKIPPED entirely.
     */
    @Cacheable(cacheNames = "products", key = "#id")
    public Product getById(Long id) {
        // This line only runs on a cache miss.
        return repository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("No product " + id));
    }

    /**
     * On write, evict the key so the next read reloads. beforeInvocation=false
     * (default) means eviction happens only if the method returns normally.
     */
    @CacheEvict(cacheNames = "products", key = "#id")
    @Transactional
    public Product updatePrice(Long id, BigDecimal newPrice) {
        Product p = repository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("No product " + id));
        p.setPrice(newPrice);
        return repository.save(p);
    }
}
```

How they compare:

| | Manual `RedisTemplate` | `@Cacheable` + `RedisCacheManager` |
|---|---|---|
| Control | Total (custom keys, conditional caching, partial loads) | Convention-driven |
| Boilerplate | More | Almost none |
| Key naming | You choose (`product:7`) | `<cacheName>::<key>` → `products::7` |
| Self-invocation | N/A | **Breaks** — calling an annotated method from within the same bean bypasses the proxy |
| Best for | Hot paths, complex logic, non-trivial keys | The 80% of straightforward read methods |

> **Gotcha:** `@Cacheable` works via a Spring AOP proxy. If `ProductAnnotatedService.someOther()` calls `this.getById(id)` *internally*, the call doesn't go through the proxy and **the cache is bypassed**. This is the #1 "my cache isn't working" bug. Either call across beans, or self-inject the proxy.

---

### Step 4 — Run and inspect with `redis-cli`

Expose both services through a controller (or call from a test/`CommandLineRunner`). Assume product id `7` exists with price `9.00`.

1. **First read (cold) — expect a DB hit and a cache populate:**
   ```bash
   curl http://localhost:8080/products/7
   # -> {"id":7,"name":"Widget","price":9.00}
   ```
2. **Inspect Redis.** Manual cache-aside uses `product:7`; the annotated path uses `products::7`:
   ```bash
   docker exec -it redis-day16 redis-cli

   127.0.0.1:6379> KEYS *
   1) "product:7"
   2) "products::7"

   127.0.0.1:6379> GET product:7
   "{\"@class\":\"com.example.catalog.product.Product\",\"id\":7,\"name\":\"Widget\",\"price\":9.00}"

   127.0.0.1:6379> TTL product:7
   (integer) 593          # seconds remaining, counting down from 600

   127.0.0.1:6379> TYPE product:7
   string
   ```
   Because we used `StringRedisSerializer` + JSON, the value is **human-readable** — that's the payoff for not using the JDK serializer.
3. **Second read (warm) — the DB is NOT touched.** Add a `log.info("DB load {}", id)` in the repository path; it should print only on the cold read.
4. **Update the price, then watch the key vanish:**
   ```bash
   curl -X PUT http://localhost:8080/products/7/price -d '5.00' -H 'Content-Type: application/json'

   127.0.0.1:6379> EXISTS product:7
   (integer) 0            # evicted!
   ```
5. **Read again — cold again, repopulated with the new value:**
   ```bash
   curl http://localhost:8080/products/7
   # -> {"id":7,"name":"Widget","price":5.00}
   127.0.0.1:6379> GET product:7   # now contains 5.00
   ```

**Expected behavior summary:** cold read → DB + populate; warm read → Redis only; update → DB + evict; next read → cold again with fresh value. Run two app instances on different ports against the *same* Redis and confirm an update on instance A is immediately reflected on instance B — the exact failure mode that the Day-15 cache could not handle.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Redis is single-threaded (for command execution).** A single core processes commands one at a time, serially, from an event loop. This is a *feature*: every command is atomic with respect to others — no locks, no races inside Redis. It also means a single **slow command blocks everything** (`KEYS *` on a big DB, a huge `MGET`, a Lua script in a tight loop). Never run `KEYS` in production; use `SCAN`. Modern Redis offloads I/O to extra threads, but the command logic is still serial.

- **Eviction policies (`maxmemory` + `maxmemory-policy`).** A cache must be allowed to forget. Set a memory ceiling and a policy, otherwise Redis grows until the OOM killer reaps it:
  ```
  maxmemory 512mb
  maxmemory-policy allkeys-lru
  ```
  Common policies: `allkeys-lru` (evict least-recently-used across all keys — the right default for a pure cache), `allkeys-lfu` (least-*frequently*-used; recall the LRU vs LFU discussion from **Day 15**), `volatile-ttl` (evict keys with the nearest expiry), `noeviction` (reject writes when full — correct for a datastore, wrong for a cache). Note Redis's LRU/LFU are *approximate* (sampled) to stay O(1).

- **Hot keys.** One wildly popular key (a celebrity product, a homepage config) funnels all its traffic to the single Redis shard that owns it, creating a hotspot that no amount of horizontal scaling relieves. Mitigations: re-introduce a small **local near-cache** for just the hottest keys (two-tier, below), or **key splitting** (`product:7#0..N` with random fan-out). Consistent hashing (**Day 22**) governs *which* shard owns a key.

- **Cache stampede / thundering herd.** When a hot key expires, *every* concurrent reader misses at once and stampedes the DB with identical reloads. Fixes: (a) **request coalescing** — let one loader populate while others wait; (b) **probabilistic early expiration** — refresh slightly before TTL; (c) a **mutex lock** so only one node rebuilds the entry. The lock is a preview of **Day 28**:
  ```java
  // Only ONE node wins the lock and rebuilds; others briefly back off.
  Boolean gotLock = stringRedisTemplate.opsForValue()
          .setIfAbsent("lock:product:7", "1", Duration.ofSeconds(5));  // SET NX EX
  if (Boolean.TRUE.equals(gotLock)) {
      try { /* load from DB, set cache */ }
      finally { stringRedisTemplate.delete("lock:product:7"); }
  } else {
      /* spin-wait briefly, then read the now-populated cache */
  }
  ```
  `setIfAbsent` is Redis `SET key val NX EX` — atomic test-and-set, the primitive behind distributed locks. We'll do the *correct, fenced* version (with token validation and Redlock caveats) on Day 28; the above is intentionally simplified.

- **Two-tier / near-cache (L1 + L2).** Combine Day 15 and Day 16: a tiny **Caffeine** cache (L1, nanoseconds, per-node) in front of the **Redis** cache (L2, shared). Reads check L1 → L2 → DB. This recovers in-process speed for the hottest keys while keeping Redis as the shared source. The cost is that L1 can be briefly stale across nodes again, so use a **short L1 TTL** and/or a Redis **pub/sub invalidation** channel (or Redis 6+ **client-side caching / tracking**) to push evictions to every node's L1. This is exactly how high-end services squeeze out latency.

- **Serialization is a contract.** Your JSON values are a wire format other services and future code versions must read. Avoid Java-serialization (versioning hell, RCE risk). Be deliberate about adding/removing fields (Jackson `@JsonIgnoreProperties(ignoreUnknown = true)` for forward-compat), and consider a schema/`schemaVersion` field on cached payloads.

- **Resilience.** Redis is now in your read path. Decide the failure mode: if Redis is down, the cache-aside `get` should ideally **fall back to the DB** (degraded but available) rather than fail the request. Wrap Redis calls so a connection timeout doesn't cascade — proper circuit-breaking arrives on **Day 24 (Resilience)**.

### Stretch goals

1. **Negative caching with a tombstone.** Cache "not found" results under a short TTL (e.g. 30s) using a sentinel value so repeated lookups of a missing id don't hammer the DB — then prove it correctly expires after a `create`. Reason carefully about the create-then-read race.
2. **Two-tier cache.** Put a small Caffeine L1 in front of the Redis L2 from this day. Measure the latency difference on a hot key. Bonus: wire a Redis **pub/sub** channel so an eviction on one node invalidates every node's L1.
3. **Stampede protection.** Implement the `SETNX` mutex from the notes around a deliberately slow DB load, fire 100 concurrent reads at a cold key, and confirm the DB is loaded **once**, not 100 times. (Foreshadows Day 28.)
4. **Cache hit-rate metrics.** Expose `cache.hit` / `cache.miss` counters (you'll formalize this with Micrometer on **Day 25 / Observability**) and compute the hit ratio under load. A hit ratio below ~80% usually means your TTL is too short or your keys are too granular.

### Day 17 teaser

You've seen the dual-write problem in miniature: two systems (DB + cache) that can't be updated atomically. **Day 17 — Two-Phase Commit (2PC)** confronts that head-on across *transactional* resources. You'll implement a coordinator/participant prepare-then-commit protocol, see exactly how it provides atomicity across two databases, and — more importantly — discover why 2PC's **blocking on coordinator failure** makes it a poor fit for the internet-scale systems we're building, motivating the log-based alternatives (Kafka, Outbox) coming on Days 18–20.
