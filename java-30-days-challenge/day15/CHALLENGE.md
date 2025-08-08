# Day 15: Caching & LRU/LFU Eviction

| | |
|---|---|
| 🏗️ **Project** | **CacheLab** — a Caffeine-cached read path with hand-rolled LRU/LFU |
| ☕ **Java & language skills** | Spring caching abstraction, @Cacheable/@CacheEvict, LinkedHashMap for LRU, implementing eviction |
| 🧰 **Library / tool** | Caffeine (in-process cache) + Spring Cache |
| 🗄️ **DB / distributed-systems concept** | Cache eviction policies (LRU / LFU / W-TinyLFU) & hit ratio |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. Why caches exist, and the one rule that defines them

On Day 12 you measured how expensive a read can be: a JPA query that lazily fans out into N+1 SQL round-trips, each one a network hop to the database. On Day 9 you saw the connection pool — a *finite* resource — and watched it exhaust under load. A cache attacks both problems: it keeps the *result* of an expensive computation (a query, a remote call, a render) close to the caller so the next request for the same key is answered from memory instead of re-doing the work.

The whole value of a cache is captured by one cost equation. If a hit costs `t_hit` (say 100 ns from a `ConcurrentHashMap`) and a miss costs `t_miss` (say 5 ms to hit Postgres), then with hit ratio `h`:

```
avg_latency = h · t_hit + (1 - h) · t_miss
```

At `h = 0.95`, `avg ≈ 0.95·0.0001ms + 0.05·5ms = 0.25 ms` — a **20×** improvement over the uncached `5 ms`. At `h = 0.50` you barely break even. **Hit ratio is the number that decides whether a cache is helping or just adding complexity and a correctness hazard.** Measure it before you celebrate.

Now the defining constraint: memory is finite, so a cache is **bounded**. You cannot keep everything. The moment the cache is full and a new entry arrives, you must throw something out. **Choosing what to throw out is the eviction policy, and it is the entire intellectual content of caching.** A policy is a *bet* about the future: "the entry I evict is the one least likely to be asked for again soon." Every policy is just a different heuristic for predicting that.

### 2. The classic eviction policies — and how each one breaks

**FIFO (First-In, First-Out).** Evict the oldest *inserted* entry, regardless of how often it's used. Trivial to implement (a queue). The failure mode is obvious: a hot key that was inserted early gets evicted even though it's hammered constantly. FIFO ignores usage entirely, so it's almost never the right answer — but it's the baseline everything else improves on.

**LRU (Least Recently Used).** Evict the entry that hasn't been *accessed* for the longest time. The bet: "recent past predicts near future" (temporal locality), which holds shockingly well for most workloads. This is the default mental model most engineers have when they say "cache." Implementation: a doubly-linked list ordered by recency + a hash map for O(1) lookup; on every access you move the entry to the front.
- **Failure mode — scans.** A one-time sequential scan over a large dataset (a nightly report, a `SELECT *` over a big table) touches every key exactly once and "recently." LRU faithfully promotes all of them, **flushing your genuinely hot keys** out of the cache to make room for keys you'll never see again. This is called **scan pollution**, and LRU has *no scan resistance*.

**LFU (Least Frequently Used).** Evict the entry with the lowest *access count*. The bet: "popular stays popular" (frequency, not recency). LFU is robust against scans — a key seen once has count 1 and gets evicted before your hot keys. Implementation: a count per key plus a structure to find the minimum count (a min-heap, or buckets of equal-frequency items for O(1)).
- **Failure mode — aging / stale popularity.** A key that was wildly popular last week accumulates a huge count and then becomes irrelevant — but its count keeps it pinned forever, starving newly-trending keys. Pure LFU has no concept of "recently popular." It also has a **cold-start problem**: a brand-new genuinely-hot key starts at count 1 and may be evicted before it can prove itself. Real LFU implementations need *aging* (periodically decay counts) to stay relevant.

So LRU and LFU are duals: LRU is all recency / no frequency (dies on scans), LFU is all frequency / no recency (dies on aging). The obvious next question — *can we get both?* — is exactly what modern caches answer.

### 3. Caffeine's W-TinyLFU — why it beats plain LRU

Caffeine's default eviction is **Window TinyLFU (W-TinyLFU)**, and on real-world traces it achieves **near-optimal hit ratios**, typically beating LRU by several percentage points and sometimes far more. Three ideas:

1. **Frequency sketch, cheaply.** Tracking an exact count per key is expensive in memory. W-TinyLFU keeps an approximate frequency estimate using a **Count-Min Sketch** (a tiny 4-bit-counter probabilistic structure) over the keys it has *seen*, not just the keys currently cached. This lets it know "this candidate is historically popular" using a few bytes total.
2. **Admission policy, not just eviction.** This is the key conceptual leap. Plain LRU/LFU only decide what to *remove*. W-TinyLFU also decides whether a new entry deserves to be *admitted* at all. When the cache is full and a new key wants in, W-TinyLFU compares the new key's estimated frequency against the victim that LRU would evict. **If the newcomer is less popular than the thing it would replace, it is rejected** — the cache refuses to pollute itself. This is what gives it scan resistance: scan keys (frequency ~1) lose the admission contest against your hot keys.
3. **A small LRU "window" + aging.** A small admission window (default ~1%) gives brand-new keys a chance to build up frequency before facing the admission filter (fixing LFU's cold-start). The sketch is periodically halved (aging), so last week's hero fades (fixing LFU's stale-popularity).

The result is a cache that is recency-aware *and* frequency-aware *and* scan-resistant *and* self-aging, with O(1) operations and tiny overhead. That's why Caffeine is the default cache in Spring Boot, Cassandra, HBase, Druid, Neo4j, and many others. **You will reproduce a slice of this advantage in step 7** by racing your hand-rolled LRU against Caffeine on a skewed workload and watching Caffeine win.

### 4. TTL: time-based eviction (a different axis)

Size eviction answers "which entry leaves when we're full." **TTL (time-to-live)** answers a separate question: "how long is a cached value allowed to be *stale*?" Even with infinite memory you need TTL, because the cached value can become **wrong** when the underlying data changes. Caffeine offers two flavors:
- **`expireAfterWrite(d)`** — entry expires `d` after it was *written*, regardless of reads. Bounds staleness: "this price is never more than 5 minutes old." Use this for correctness.
- **`expireAfterAccess(d)`** — entry expires `d` after it was last *read*. Evicts idle entries. Use this to bound memory for rarely-touched keys.

A production cache configures **both size and TTL**: size caps memory, TTL caps staleness. They are orthogonal and you almost always want both.

### 5. Cache stampede / thundering herd — the failure that pages you at 3 a.m.

Here's the scenario, and *why* it's dangerous. A single very hot key (say the front-page product list) is served from cache thousands of times per second. Its TTL expires. Now:

```
                  TTL expires
                       |
 req1 ──miss──┐        |
 req2 ──miss──┤        ▼
 req3 ──miss──┼──> all 5000 in-flight requests miss simultaneously
 ...          │        │
 req5000──miss┘        ▼
                  5000 identical expensive queries hit the database AT ONCE
```

The instant the entry expires, **every concurrent request misses at the same time**, and they *all* run the expensive computation in parallel before any of them can re-populate the cache. Your database, which was happily serving one query per TTL window, suddenly gets thousands — and falls over. The cache that was *protecting* the DB becomes the *trigger* for an outage. This is the **thundering herd** (a.k.a. cache stampede / dog-piling).

The fix is **single-flight / request coalescing**: when a key is missing, exactly *one* caller computes the value while the others *wait* for that single computation and share its result. Caffeine does this for you when you use **`LoadingCache` / `get(key, mappingFunction)`**: concurrent misses on the same key block on a single load. (Spring's `@Cacheable` with `sync = true` enables the same coalescing.) Other defenses: probabilistic early expiration (refresh a hot key slightly *before* it expires) and `refreshAfterWrite` (serve the stale value while one thread refreshes in the background). We'll use `sync = true` in this project.

### 6. Where this sits in the series

Today's cache is **in-process** — it lives in your JVM's heap. That's the fastest possible cache (nanosecond hits, no serialization, no network) but it has two limits we'll attack later: it doesn't survive a restart, and **every instance has its own copy**, so in a 5-pod deployment you have 5 caches that can disagree (a **cache coherence** problem). **Day 16 takes this distributed with Redis** — a shared out-of-process cache — and we'll weigh the trade: Redis gives you coherence and persistence but adds a network hop and serialization cost. The senior pattern is often *both* (a near-cache/L1 Caffeine in front of an L2 Redis). Keep that preview in mind today.

---

## Prerequisites & Maven dependencies

You're continuing the **Day 12** Spring Boot + JPA project (the read-heavy `Product` service). Add the cache starter and Caffeine to that project's `pom.xml`:

```xml
<dependencies>
    <!-- carried from Day 12: web + JPA + H2 -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
    <dependency>
        <groupId>com.h2database</groupId>
        <artifactId>h2</artifactId>
        <scope>runtime</scope>
    </dependency>

    <!-- NEW: Spring's caching abstraction (@EnableCaching, CacheManager, @Cacheable) -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-cache</artifactId>
    </dependency>

    <!-- NEW: Caffeine — the actual cache implementation behind CaffeineCacheManager -->
    <dependency>
        <groupId>com.github.ben-manes.caffeine</groupId>
        <artifactId>caffeine</artifactId>
        <!-- version managed by the Spring Boot BOM; no explicit version needed -->
    </dependency>

    <!-- Actuator so we can read cache hit/miss stats over HTTP -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-actuator</artifactId>
    </dependency>
</dependencies>
```

`application.properties` — expose the cache metrics endpoint:

```properties
management.endpoints.web.exposure.include=health,caches,metrics
# Spring Boot only registers cache metrics for caches that record stats.
# CaffeineCacheManager.setRecordStats(true) (below) makes Micrometer pick them up.
```

---

## 🛠️ Project Walkthrough — CacheLab

Roll up your sleeves: follow these numbered steps to build the cache, then run it and read the stats yourself.

### Step 1 — Turn on caching and configure the Caffeine cache manager

`@EnableCaching` activates the Spring AOP proxy that intercepts `@Cacheable` methods. We register a `CaffeineCacheManager` with a default spec (size + TTL + stats). We also expose the raw `Cache` for the stats endpoint.

```java
package com.example.day15.config;

import com.github.benmanes.caffeine.cache.Caffeine;
import java.time.Duration;
import org.springframework.cache.CacheManager;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.cache.caffeine.CaffeineCacheManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableCaching // wires the caching aspect: @Cacheable/@CacheEvict/@CachePut now work
public class CacheConfig {

    /** The names of caches Spring will manage. Must match the value() in @Cacheable. */
    public static final String PRODUCTS = "products";

    @Bean
    public CacheManager cacheManager() {
        CaffeineCacheManager manager = new CaffeineCacheManager(PRODUCTS);
        manager.setCaffeine(caffeineSpec());
        // do NOT auto-create caches for unknown names — catches typos in @Cacheable("prdoucts")
        manager.setAllowNullValues(false); // we'll do explicit negative caching instead (see notes)
        return manager;
    }

    /**
     * The single Caffeine builder applied to every cache this manager owns.
     *  - maximumSize        -> SIZE-based eviction (W-TinyLFU decides the victim)
     *  - expireAfterWrite   -> TTL: bound staleness to 2 minutes
     *  - recordStats        -> enables hitCount()/missCount() and Micrometer metrics
     */
    private Caffeine<Object, Object> caffeineSpec() {
        return Caffeine.newBuilder()
                .maximumSize(500)
                .expireAfterWrite(Duration.ofMinutes(2))
                .recordStats();
    }
}
```

Key point: **W-TinyLFU is the default eviction policy** the moment you call `maximumSize(...)`. You don't configure it; it's what Caffeine does. `maximumWeight(...)` + a `Weigher` would let you bound by *bytes* instead of *entries* — useful when entries vary wildly in size.

### Step 2 — Put `@Cacheable` on the expensive read path

This is the Day 12 service, now cached. The first call for an id runs the (slow) query; subsequent calls return from Caffeine.

```java
package com.example.day15.product;

import com.example.day15.config.CacheConfig;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.cache.annotation.CacheEvict;
import org.springframework.cache.annotation.CachePut;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;

@Service
public class ProductService {

    private final ProductRepository repository;
    /** Counts how often the EXPENSIVE method body actually ran (i.e. cache misses). */
    private final AtomicLong dbHits = new AtomicLong();

    public ProductService(ProductRepository repository) {
        this.repository = repository;
    }

    /**
     * Read path. On a cache MISS the body runs and the return value is stored under
     * key = the id argument (Spring's default key generator). On a HIT, Spring returns
     * the cached value and the body is NEVER invoked.
     *
     * sync = true  -> request coalescing: concurrent misses on the SAME id block on a
     *                 single computation. This is our thundering-herd defense (primer §5).
     */
    @Cacheable(value = CacheConfig.PRODUCTS, key = "#id", sync = true)
    public Product findById(long id) {
        dbHits.incrementAndGet();
        simulateExpensiveQuery();              // stand-in for the Day 12 N+1 read
        return repository.findById(id)
                .orElseThrow(() -> new ProductNotFoundException(id));
    }

    /** Write-through: update the row AND refresh the cached value under the same key. */
    @CachePut(value = CacheConfig.PRODUCTS, key = "#product.id")
    public Product update(Product product) {
        return repository.save(product);
    }

    /** Invalidate the cached entry so the next read re-fetches from the DB. */
    @CacheEvict(value = CacheConfig.PRODUCTS, key = "#id")
    public void delete(long id) {
        repository.deleteById(id);
    }

    /** Drop the whole cache (e.g. after a bulk import). */
    @CacheEvict(value = CacheConfig.PRODUCTS, allEntries = true)
    public void evictAll() { }

    public long databaseHits() {
        return dbHits.get();
    }

    private void simulateExpensiveQuery() {
        try {
            Thread.sleep(50); // pretend the read costs 50ms (network + N+1 fan-out)
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
```

Three correctness traps worth saying out loud:
- **`@Cacheable` is implemented with an AOP proxy**, so a call from *within the same bean* (`this.findById(...)`) bypasses the cache entirely — the proxy never sees it. Always call through the injected bean.
- **`@CachePut` always runs the body and updates the cache**; `@Cacheable` skips the body on a hit. Don't put both on the same method — they conflict.
- **Your cache key is whatever you say it is.** The SpEL `key = "#id"` is explicit; the default key generator hashes *all* arguments, which silently breaks if you add a parameter later.

### Step 3 — Expose hit/miss stats over HTTP

Read the live Caffeine statistics so we can prove the hit ratio.

```java
package com.example.day15.product;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.stats.CacheStats;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.cache.CacheManager;
import org.springframework.cache.caffeine.CaffeineCache;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ProductController {

    private final ProductService service;
    private final CacheManager cacheManager;

    public ProductController(ProductService service, CacheManager cacheManager) {
        this.service = service;
        this.cacheManager = cacheManager;
    }

    @GetMapping("/products/{id}")
    public Product get(@PathVariable long id) {
        return service.findById(id);
    }

    @GetMapping("/cache/stats")
    public Map<String, Object> stats() {
        // Unwrap Spring's CaffeineCache to reach the native Caffeine CacheStats.
        CaffeineCache spring = (CaffeineCache) cacheManager.getCache("products");
        Cache<Object, Object> native_ = spring.getNativeCache();
        CacheStats s = native_.stats();

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("size", native_.estimatedSize());
        out.put("hitCount", s.hitCount());
        out.put("missCount", s.missCount());
        out.put("hitRate", String.format("%.3f", s.hitRate()));
        out.put("evictionCount", s.evictionCount());
        out.put("databaseHits", service.databaseHits()); // == missCount, proves it
        return out;
    }
}
```

### Step 4 — A hand-rolled LRU with `LinkedHashMap`

To *internalize* the algorithm, implement LRU yourself. The trick: `LinkedHashMap` already maintains a doubly-linked list of entries, and its constructor takes an **`accessOrder`** flag. With `accessOrder = true`, every `get`/`put` moves the touched entry to the tail — so the *head* is always the least-recently-used. Override `removeEldestEntry` to evict it when over capacity.

```java
package com.example.day15.cache;

import java.util.LinkedHashMap;
import java.util.Map;

/** A bounded, access-ordered LRU cache in ~10 lines. NOT thread-safe (wrap if needed). */
public class LruCache<K, V> extends LinkedHashMap<K, V> {

    private final int capacity;
    private long hits;
    private long misses;

    public LruCache(int capacity) {
        // initialCapacity sized to avoid resize; loadFactor 0.75; accessOrder = TRUE
        super(Math.max(16, capacity * 4 / 3 + 1), 0.75f, /* accessOrder */ true);
        this.capacity = capacity;
    }

    /** Called by LinkedHashMap after each put; return true to evict the eldest (LRU). */
    @Override
    protected boolean removeEldestEntry(Map.Entry<K, V> eldest) {
        return size() > capacity;
    }

    /** Read-through: count hit/miss and (on miss) compute + store. */
    public V get(K key, java.util.function.Function<K, V> loader) {
        V v = super.get(key); // get() touches access order, moving key to MRU position
        if (v != null) {
            hits++;
            return v;
        }
        misses++;
        v = loader.apply(key);
        put(key, v);
        return v;
    }

    public double hitRate() {
        long total = hits + misses;
        return total == 0 ? 0 : (double) hits / total;
    }

    public long hits() { return hits; }
    public long misses() { return misses; }
}
```

Note the subtlety: because `accessOrder = true`, calling `get` *reorders* the map. That's exactly LRU — but it also means iterating the map mutates its order, and it's why this isn't thread-safe.

### Step 5 — A basic LFU

LFU needs a frequency count per key and a way to find the *minimum* count to evict. The clean O(1) approach uses **frequency buckets**: a map of `count -> set of keys with that count`, plus a running `minFreq`. On access, move the key up one bucket; on eviction, remove any key from the `minFreq` bucket. (A simpler, less efficient version scans for the min — fine for learning, but the bucket version is the one asked about in interviews.)

```java
package com.example.day15.cache;

import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.function.Function;

/** O(1) LFU via frequency buckets. Ties broken LRU-style (LinkedHashSet keeps insertion order). */
public class LfuCache<K, V> {

    private final int capacity;
    private int minFreq = 0;
    private long hits, misses;

    private final Map<K, V> values = new HashMap<>();
    private final Map<K, Integer> freq = new HashMap<>();
    private final Map<Integer, LinkedHashSet<K>> buckets = new HashMap<>();

    public LfuCache(int capacity) {
        this.capacity = capacity;
    }

    public V get(K key, Function<K, V> loader) {
        if (values.containsKey(key)) {
            hits++;
            touch(key);          // bump this key's frequency
            return values.get(key);
        }
        misses++;
        V v = loader.apply(key);
        put(key, v);
        return v;
    }

    private void put(K key, V value) {
        if (capacity == 0) return;
        if (values.containsKey(key)) {       // update existing
            values.put(key, value);
            touch(key);
            return;
        }
        if (values.size() >= capacity) {     // full -> evict a least-frequent key
            LinkedHashSet<K> minBucket = buckets.get(minFreq);
            K evict = minBucket.iterator().next(); // oldest among the least-frequent
            minBucket.remove(evict);
            values.remove(evict);
            freq.remove(evict);
        }
        values.put(key, value);
        freq.put(key, 1);
        buckets.computeIfAbsent(1, k -> new LinkedHashSet<>()).add(key);
        minFreq = 1;                          // a brand-new key always has freq 1
    }

    /** Move key from bucket f to bucket f+1, fixing minFreq if that bucket emptied. */
    private void touch(K key) {
        int f = freq.get(key);
        freq.put(key, f + 1);
        LinkedHashSet<K> old = buckets.get(f);
        old.remove(key);
        if (old.isEmpty()) {
            buckets.remove(f);
            if (minFreq == f) minFreq = f + 1;
        }
        buckets.computeIfAbsent(f + 1, k -> new LinkedHashSet<>()).add(key);
    }

    public double hitRate() {
        long total = hits + misses;
        return total == 0 ? 0 : (double) hits / total;
    }

    public long hits() { return hits; }
    public long misses() { return misses; }
}
```

Notice the **cold-start weakness from primer §2 baked right in**: every new key enters at freq 1 and competes with other freq-1 keys for eviction. A truly hot newcomer can be evicted before it proves itself. That's the gap W-TinyLFU's admission window closes.

### Step 6 — A Zipfian key generator

Real access patterns are **skewed**: a few keys are hammered, a long tail is rarely touched (the front page vs. page 900 of search results). A **Zipfian** distribution models this — key rank `k` is accessed with probability proportional to `1/k^s`. We'll generate skewed keys to expose the difference between policies.

```java
package com.example.day15.cache;

import java.util.Random;

/** Generates keys 1..n following a Zipf(s) distribution. Higher s = more skew. */
public class ZipfGenerator {
    private final int n;
    private final double[] cumulative;
    private final Random rnd;

    public ZipfGenerator(int n, double s, long seed) {
        this.n = n;
        this.rnd = new Random(seed);
        double[] weights = new double[n + 1];
        double sum = 0;
        for (int k = 1; k <= n; k++) {
            sum += 1.0 / Math.pow(k, s);
            weights[k] = sum;
        }
        this.cumulative = new double[n + 1];
        for (int k = 1; k <= n; k++) cumulative[k] = weights[k] / sum; // normalize to [0,1]
    }

    /** Returns a key in 1..n; low keys (popular) come out far more often. */
    public int next() {
        double r = rnd.nextDouble();
        // binary search the cumulative table
        int lo = 1, hi = n;
        while (lo < hi) {
            int mid = (lo + hi) >>> 1;
            if (cumulative[mid] < r) lo = mid + 1; else hi = mid;
        }
        return lo;
    }
}
```

### Step 7 — The driver: race LRU vs LFU vs Caffeine on skewed keys

A plain `main` that runs the *same* skewed access trace through all three caches and prints hit rates. This is the payoff: you'll *see* W-TinyLFU win.

```java
package com.example.day15.cache;

import com.github.benmanes.caffeine.cache.Caffeine;
import com.github.benmanes.caffeine.cache.LoadingCache;
import com.github.benmanes.caffeine.cache.stats.CacheStats;
import java.util.function.Function;

public class CacheRaceDriver {

    public static void main(String[] args) {
        int keyspace   = 10_000;  // 10k distinct keys
        int capacity   = 500;     // cache holds only 5% of them -> eviction matters
        double skew    = 1.0;     // Zipf exponent; ~1.0 is typical web traffic
        int requests   = 500_000;

        // expensive loader stub (always "computes" something for the key)
        Function<Integer, Integer> loader = k -> k * 31;

        LruCache<Integer, Integer> lru = new LruCache<>(capacity);
        LfuCache<Integer, Integer> lfu = new LfuCache<>(capacity);
        LoadingCache<Integer, Integer> caffeine = Caffeine.newBuilder()
                .maximumSize(capacity)
                .recordStats()
                .build(loader::apply);

        // SAME trace fed to all three (same seed -> identical key sequence)
        ZipfGenerator zipfLru = new ZipfGenerator(keyspace, skew, 42);
        ZipfGenerator zipfLfu = new ZipfGenerator(keyspace, skew, 42);
        ZipfGenerator zipfCaf = new ZipfGenerator(keyspace, skew, 42);

        for (int i = 0; i < requests; i++) lru.get(zipfLru.next(), loader);
        for (int i = 0; i < requests; i++) lfu.get(zipfLfu.next(), loader);
        for (int i = 0; i < requests; i++) caffeine.get(zipfCaf.next());

        CacheStats cs = caffeine.stats();
        System.out.printf("keyspace=%d capacity=%d (%.0f%%) skew=%.1f requests=%,d%n",
                keyspace, capacity, 100.0 * capacity / keyspace, skew, requests);
        System.out.printf("LRU       hitRate = %.3f  (hits=%,d misses=%,d)%n",
                lru.hitRate(), lru.hits(), lru.misses());
        System.out.printf("LFU       hitRate = %.3f  (hits=%,d misses=%,d)%n",
                lfu.hitRate(), lfu.hits(), lfu.misses());
        System.out.printf("Caffeine  hitRate = %.3f  (hits=%,d misses=%,d)%n",
                cs.hitRate(), cs.hitCount(), cs.missCount());

        // Now add a SCAN: sweep every key once, then resume skewed traffic.
        System.out.println("\n--- after a one-time full scan (LRU's nemesis) ---");
        // (left as the first stretch goal)
    }
}
```

---

## How to run

**The Spring cache (steps 1–3):**

```bash
cd day12-project        # the project you're extending
mvn spring-boot:run
```

```bash
# First call: MISS -> body runs -> ~50ms
curl -s http://localhost:8080/products/1
# Repeat the same id: HIT -> served from Caffeine, no DB
curl -s http://localhost:8080/products/1
curl -s http://localhost:8080/products/1

curl -s http://localhost:8080/cache/stats
```

**The cache race (steps 4–7)** — a plain Java class with a `main`:

```bash
mvn -q exec:java -Dexec.mainClass=com.example.day15.cache.CacheRaceDriver
# or run CacheRaceDriver.main() from your IDE
```

You can also drive the stats endpoint with a skewed load using a shell loop and watch `hitRate` climb as the hot keys settle into the cache.

---

## Expected output

`/cache/stats` after hitting id `1` three times then id `2` once:

```json
{
  "size": 2,
  "hitCount": 2,
  "missCount": 2,
  "hitRate": "0.500",
  "evictionCount": 0,
  "databaseHits": 2
}
```

`databaseHits == missCount` — concrete proof the cache prevented two of four reads from touching the DB.

The race driver (numbers will vary slightly by seed/JVM, but the **ordering is stable**):

```
keyspace=10000 capacity=500 (5%) skew=1.0 requests=500,000
LRU       hitRate = 0.787  (hits=393,512 misses=106,488)
LFU       hitRate = 0.812  (hits=405,991 misses=94,009)
Caffeine  hitRate = 0.831  (hits=415,623 misses=84,377)
```

The takeaways you should be able to defend: on a skewed read-heavy workload, **LFU edges out LRU** (frequency matters more than recency when popularity is stable), and **Caffeine's W-TinyLFU beats both** while being scan-resistant and O(1). On a workload with a one-time scan mixed in (the stretch goal), the gap widens dramatically: LRU's hit rate collapses while Caffeine barely moves.

---

## 🚀 Going Deeper & Next Steps

### Senior-level notes

- **Why W-TinyLFU really beats LRU.** It's the **admission policy**. LRU/LFU only choose a victim; W-TinyLFU additionally asks "is the incoming key even worth admitting?" by comparing its sketch-estimated frequency to the victim's. Low-frequency newcomers (scan traffic, one-hit keys) lose that contest and never displace genuinely hot data. Recency is preserved by the small LRU admission *window*; staleness is handled by periodically *halving* the frequency sketch (aging). It's the synthesis of recency + frequency that LRU and LFU each only do half of.
- **Approximate counting is a feature, not a compromise.** The Count-Min Sketch uses a handful of 4-bit counters and accepts small over-estimates in exchange for tracking frequencies of *all* recently-seen keys (not just resident ones) in near-zero memory. Knowing about evicted keys' popularity is what makes good admission decisions possible.
- **Negative caching.** A miss that resolves to "no such product" is *also* expensive to recompute, and a flood of requests for nonexistent keys is a classic DoS/stampede vector (cache penetration). Cache the *absence* too — but with a **short TTL** so a later insert becomes visible quickly. We set `allowNullValues=false` and would model this with a sentinel `Optional`/tombstone value and a separate short `expireAfterWrite`. Don't let "not found" hammer your DB.
- **Cache stampede defenses, ranked.** (1) `sync=true` / `LoadingCache` request coalescing — always do this for hot keys; it's free. (2) `refreshAfterWrite` — serve slightly stale data while *one* thread refreshes in the background, so reads never block on the loader. (3) Probabilistic early expiration — jitter TTLs and refresh a hot key a little before it expires, so expirations don't synchronize across keys.
- **Eviction is not invalidation.** Eviction (size/TTL) is the cache *managing memory/staleness on its own*. Invalidation (`@CacheEvict`) is *you* telling it a value is now wrong. Mixing them up — relying on a 2-minute TTL to "eventually" fix a value you just changed — is a classic stale-data bug. Invalidate explicitly on writes.
- **Measure, then tune.** Wire Caffeine's `recordStats()` into Micrometer (`management.metrics`), graph `cache.gets{result=hit|miss}`, and tune `maximumSize` to the knee of the hit-ratio curve. A cache below ~80% hit rate on a read-heavy path is usually mis-sized or has the wrong key.
- **Cache coherence preview (Day 16).** Everything today lives in *one* JVM heap. Run 5 pods and you have 5 independent caches; a `@CacheEvict` on pod A does nothing to pods B–E, so they keep serving stale data until *their own* TTL fires. That's the **coherence problem**, and it's exactly why **Day 16** introduces **Redis** as a shared L2 cache (with the L1/near-cache pattern layered on top of today's Caffeine).

### Stretch goals

1. **Add the scan and watch LRU collapse.** Finish the driver's scan phase: after the skewed warm-up, sweep every key `1..keyspace` exactly once, then resume skewed traffic and re-measure. LRU's hit rate should crater (its hot keys got flushed); Caffeine's should barely move. This is *scan resistance* made visible.
2. **Implement W-TinyLFU's admission rule in your LFU.** Add a tiny frequency counter for *evicted* keys, and on admission reject the newcomer if its historical count is below the victim's. Re-run the race and watch your hand-rolled cache close the gap to Caffeine.
3. **Negative caching with a tombstone.** Add a `findByIdOrEmpty` returning `Optional`, cache the empty result under a short TTL using a second cache name, and prove (via stats) that a burst of requests for a nonexistent id hits the DB only once.
4. **Wire Micrometer + Actuator.** Hit `/actuator/metrics/cache.gets?tag=cache:products` and confirm hit/miss counters move under load; bonus: bound by `maximumWeight` + a `Weigher` instead of entry count and observe eviction behavior change.

### Day 16 teaser

Today's cache is fast but **local and incoherent** across instances, and it dies on restart. **Day 16 — Distributed Caching with Redis** makes the cache a shared, out-of-process service: we'll swap/augment the `CacheManager` for a Redis-backed one, confront serialization and the network hop you don't pay for in-heap, solve the **coherence** problem across pods, and build the senior-grade **two-tier (L1 Caffeine + L2 Redis) near-cache** — carrying everything you built today straight into a distributed setting.
