# 16. Caching

## Overview

Caching means storing a copy of expensive-to-compute or expensive-to-fetch data somewhere fast, so the next request can skip the slow path. In practice that "slow path" is usually a database query, a REST call to another service, or a heavy computation. A well-placed cache can turn a 200ms database round-trip into a 1ms in-memory lookup, which reduces load on downstream systems and makes your application feel snappier under traffic. The cost is complexity: caches can serve stale data, they consume memory, they need eviction rules, and in a multi-instance deployment they can get out of sync unless you use a shared (distributed) cache. Caching is also a classic source of subtle bugs — "cache invalidation" is famously one of the two hard problems in computer science (the other being naming things, and off-by-one errors). This chapter covers Spring's caching abstraction, the popular cache backends, and how to evict and synchronize caches correctly.

## Spring Cache

Spring's caching support lets you add caching to a method by putting an annotation on it — no manual "check cache, else call the method, then store result" boilerplate. It works through Spring AOP: Spring wraps your bean in a proxy, and the proxy intercepts calls to annotated methods.

To turn caching on, add `@EnableCaching` to a configuration class (or your `@SpringBootApplication` class):

```java
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableCaching
public class CachingConfig {
}
```

Once enabled, Spring Boot auto-detects a `CacheManager` bean (more on that below) and wires up the annotation-driven caching machinery automatically.

### Key annotations

| Annotation | What it does |
|---|---|
| `@Cacheable` | Look up the cache first; if a value is present, return it and skip the method body. If not present ("cache miss"), run the method and store the result. |
| `@CachePut` | Always run the method, then store (or overwrite) the result in the cache. Useful for "update" operations that should refresh the cache. |
| `@CacheEvict` | Remove one or more entries from the cache, typically after a delete or update. |
| `@Caching` | Group multiple cache annotations (e.g. two `@CacheEvict`s) on one method. |
| `@CacheConfig` | Class-level annotation to share common settings (like the cache name) across all cached methods in that class. |

A simple example:

```java
import org.springframework.cache.annotation.CacheConfig;
import org.springframework.cache.annotation.CacheEvict;
import org.springframework.cache.annotation.CachePut;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;

@Service
@CacheConfig(cacheNames = "products")
public class ProductService {

    private final ProductRepository repository;

    public ProductService(ProductRepository repository) {
        this.repository = repository;
    }

    @Cacheable(key = "#id")
    public Product findById(Long id) {
        return repository.findById(id)
                .orElseThrow(() -> new ProductNotFoundException(id));
    }

    @CachePut(key = "#product.id")
    public Product update(Product product) {
        return repository.save(product);
    }

    @CacheEvict(key = "#id")
    public void delete(Long id) {
        repository.deleteById(id);
    }
}
```

### Cache keys with SpEL

By default, Spring builds a key from all method arguments. You can control the key explicitly using SpEL (Spring Expression Language):

```java
@Cacheable(cacheNames = "products", key = "#id")
public Product findById(Long id) { ... }

@Cacheable(cacheNames = "userOrders", key = "#userId + '-' + #status")
public List<Order> findOrders(Long userId, String status) { ... }
```

`condition` decides whether caching applies at all (evaluated before the method runs); `unless` decides whether the *result* should be stored (evaluated after the method runs, so it can inspect the return value):

```java
@Cacheable(cacheNames = "products",
           key = "#id",
           condition = "#id > 0",
           unless = "#result == null || #result.price < 0")
public Product findById(Long id) { ... }
```

| Attribute | Evaluated | Purpose |
|---|---|---|
| `key` | before invocation | Customize the cache key |
| `condition` | before invocation | Skip caching entirely for some inputs |
| `unless` | after invocation | Skip *storing* the result, based on the return value |

### Custom `KeyGenerator`

If you need the same key logic in many places, write a `KeyGenerator` instead of repeating SpEL:

```java
import org.springframework.cache.interceptor.KeyGenerator;
import org.springframework.stereotype.Component;
import java.lang.reflect.Method;
import java.util.Arrays;

@Component("myKeyGenerator")
public class MyKeyGenerator implements KeyGenerator {
    @Override
    public Object generate(Object target, Method method, Object... params) {
        return method.getName() + "_" + Arrays.toString(params);
    }
}
```

```java
@Cacheable(cacheNames = "products", keyGenerator = "myKeyGenerator")
public Product findById(Long id) { ... }
```

### Grouping with `@Caching`

```java
@Caching(evict = {
        @CacheEvict(cacheNames = "products", key = "#id"),
        @CacheEvict(cacheNames = "productsByCategory", allEntries = true)
})
public void delete(Long id) {
    repository.deleteById(id);
}
```

### It's proxy-based — beware self-invocation

Spring's caching (like `@Transactional`) is implemented with a dynamic proxy wrapped around your bean. Calls coming from *outside* the bean go through the proxy, so caching logic runs. Calls made *from inside the same class* (`this.someMethod()`) bypass the proxy entirely, so caching (and eviction) silently does nothing.

```java
@Service
public class ReportService {

    @Cacheable("reports")
    public Report generate(String id) { ... }

    public Report generateWrapper(String id) {
        // ❌ self-invocation: this call skips the proxy, no caching happens
        return this.generate(id);
    }
}
```

Fix: call through another bean, or split the cached logic into a separate bean/component so the call always goes through the Spring-managed proxy.

```java
@Service
public class ReportFacade {
    private final ReportService reportService;
    public ReportFacade(ReportService reportService) { this.reportService = reportService; }

    public Report generateWrapper(String id) {
        // ✅ external call, goes through the proxy
        return reportService.generate(id);
    }
}
```

## Cache Abstraction

Spring's caching support is deliberately split into two layers:

- **Abstraction** — the `@Cacheable` family of annotations, plus the `Cache` and `CacheManager` interfaces. This layer is provider-agnostic: your business code never imports Redis or Caffeine classes directly.
- **Implementation** — the actual storage engine (an in-memory map, Caffeine, Redis, Hazelcast, etc.) that plugs in behind a `CacheManager`.

This separation means you can start with a trivial in-memory cache during development, and swap in Redis for production, without touching a single `@Cacheable` annotation in your service classes. All you change is the `CacheManager` bean (usually just a dependency and some configuration, since Spring Boot auto-configures it).

The two core interfaces:

```java
public interface Cache {
    String getName();
    ValueWrapper get(Object key);
    <T> T get(Object key, Class<T> type);
    void put(Object key, Object value);
    void evict(Object key);
    void clear();
}

public interface CacheManager {
    Cache getCache(String name);
    Collection<String> getCacheNames();
}
```

You rarely implement these yourself — Spring Boot's auto-configuration picks a suitable `CacheManager` based on what's on the classpath, or you define one explicitly.

## Cache Managers

A `CacheManager` is the factory that hands out named `Cache` instances (e.g. `"products"`, `"userOrders"`). Spring Boot auto-detects which one to use based on classpath dependencies and `spring.cache.type`, but you can also define your own `@Bean`.

| Cache Manager | Storage | Distributed? | TTL support | Eviction policy | Good for |
|---|---|---|---|---|---|
| `ConcurrentMapCacheManager` | JVM heap (`ConcurrentHashMap`) | No | None built-in | None (grows forever) | Quick prototypes, tests — **not production** |
| Caffeine (`CaffeineCacheManager`) | JVM heap, off-heap possible | No | Yes (per-cache spec) | LRU/LFU-like (W-TinyLFU) | Single-instance or per-instance local caching, very low latency |
| Redis (`RedisCacheManager`) | External Redis server | Yes | Yes (per-cache config) | Redis eviction policies (e.g. `allkeys-lru`) | Multi-instance apps that need a shared, consistent cache |
| Hazelcast | In-memory data grid, can span nodes | Yes (clustered) | Yes | Configurable | Distributed caching without a separate Redis server; also gives distributed locks/queues |
| JCache (JSR-107) | Pluggable — Ehcache, Hazelcast, etc. via a standard API | Depends on provider | Yes | Depends on provider | Portable code that shouldn't hard-code a specific cache vendor |

Rules of thumb:

- **Local vs. distributed**: local caches (Caffeine, plain heap maps) are extremely fast but each app instance has its own copy — instance A's cache doesn't know instance B just evicted something. Distributed caches (Redis, Hazelcast) are consistent across instances but add network latency and an external dependency.
- Never ship `ConcurrentMapCacheManager` to production — it has no TTL, no size limit, and no eviction, so it's an unbounded memory leak waiting to happen.
- If you're running more than one instance of your service behind a load balancer and correctness matters (e.g. inventory counts), prefer Redis/Hazelcast over a purely local cache.

Explicitly wiring a `CacheManager` (Caffeine example):

```java
import com.github.benmanes.caffeine.cache.Caffeine;
import org.springframework.boot.autoconfigure.cache.CaffeineCacheManagerBuilderCustomizer;
import org.springframework.cache.CacheManager;
import org.springframework.cache.caffeine.CaffeineCacheManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import java.util.concurrent.TimeUnit;

@Configuration
public class CacheManagerConfig {

    @Bean
    public CacheManager cacheManager() {
        CaffeineCacheManager manager = new CaffeineCacheManager("products", "userOrders");
        manager.setCaffeine(Caffeine.newBuilder()
                .maximumSize(10_000)
                .expireAfterWrite(10, TimeUnit.MINUTES));
        return manager;
    }
}
```

## Redis Cache

Redis is an external, in-memory key-value store. Because it lives outside the JVM, every instance of your application sees the same cache — that's what makes it a good fit for horizontally-scaled services.

Add the dependency:

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-redis</artifactId>
</dependency>
```

Minimal connection config:

```yaml
spring:
  data:
    redis:
      host: localhost
      port: 6379
  cache:
    type: redis
    redis:
      time-to-live: 600000        # default TTL in ms (10 minutes)
      cache-null-values: false    # don't cache nulls by default
      key-prefix: "myapp::"
      use-key-prefix: true
```

The `spring.cache.redis.*` properties above configure the *default* behavior applied to every cache. If you need **different TTLs per cache name** (e.g. "products" cached for an hour, "sessions" cached for 5 minutes), you configure a `RedisCacheManager` bean explicitly, since the properties alone only set one global default:

```java
import org.springframework.boot.autoconfigure.cache.RedisCacheManagerBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.cache.RedisCacheConfiguration;
import org.springframework.data.redis.cache.RedisCacheManager;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.serializer.GenericJackson2JsonRedisSerializer;
import org.springframework.data.redis.serializer.RedisSerializationContext;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

@Configuration
public class RedisCacheConfig {

    @Bean
    public RedisCacheManager cacheManager(RedisConnectionFactory connectionFactory) {
        RedisCacheConfiguration defaultConfig = RedisCacheConfiguration.defaultCacheConfig()
                .entryTtl(Duration.ofMinutes(10))
                .disableCachingNullValues()
                .serializeValuesWith(RedisSerializationContext.SerializationPair
                        .fromSerializer(new GenericJackson2JsonRedisSerializer()));

        Map<String, RedisCacheConfiguration> perCacheConfig = new HashMap<>();
        perCacheConfig.put("products", defaultConfig.entryTtl(Duration.ofHours(1)));
        perCacheConfig.put("sessions", defaultConfig.entryTtl(Duration.ofMinutes(5)));

        return RedisCacheManager.builder(connectionFactory)
                .cacheDefaults(defaultConfig)
                .withInitialCacheConfigurations(perCacheConfig)
                .build();
    }
}
```

### Choosing a serializer

Cached values are stored as bytes in Redis, so Spring needs a serializer to convert your Java objects to/from `byte[]`.

| Serializer | Human-readable in Redis? | Cross-language friendly? | Class-change tolerance | Notes |
|---|---|---|---|---|
| `GenericJackson2JsonRedisSerializer` | Yes (JSON) | Yes | Good — JSON tolerates added/removed fields reasonably well | Recommended default; embeds type info so it can deserialize back to the right class |
| JDK serialization (`JdkSerializationRedisSerializer`) | No (binary blob) | No — Java-only | Poor — changing a class can break deserialization of old entries | Simple but fragile; avoid for anything long-lived or shared across services |

Prefer `GenericJackson2JsonRedisSerializer` in almost all cases: it's debuggable (you can `redis-cli GET` a key and read it), and it doesn't tie you to Java-only consumers.

## Caffeine Cache

Caffeine is a high-performance, in-memory (local, per-JVM) caching library, often described as "Guava cache, but faster." It's the go-to choice when you don't need the cache shared across instances.

Dependency:

```xml
<dependency>
    <groupId>com.github.ben-manes.caffeine</groupId>
    <artifactId>caffeine</artifactId>
</dependency>
```

Simplest setup — Spring Boot auto-configures a `CaffeineCacheManager` if you set a spec string:

```properties
spring.cache.type=caffeine
spring.cache.cache-names=products,userOrders
spring.cache.caffeine.spec=maximumSize=5000,expireAfterWrite=10m,recordStats
```

The spec string supports several knobs:

| Spec key | Meaning |
|---|---|
| `maximumSize=N` | Evict entries once the cache holds more than N entries (size-based eviction) |
| `expireAfterWrite=Xm` | Entry expires X minutes after it was written/updated (classic TTL) |
| `expireAfterAccess=Xm` | Entry expires X minutes after it was last read (TTI — time-to-idle) |
| `refreshAfterWrite=Xm` | After X minutes, the *next* read triggers an async reload, but stale data is still returned to that reader while the reload happens in the background |
| `recordStats` | Enable hit/miss statistics, useful for monitoring |

For more control, configure Caffeine's builder directly in Java:

```java
import com.github.benmanes.caffeine.cache.Caffeine;
import org.springframework.cache.CacheManager;
import org.springframework.cache.caffeine.CaffeineCacheManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import java.time.Duration;

@Configuration
public class CaffeineConfig {

    @Bean
    public CacheManager cacheManager() {
        CaffeineCacheManager manager = new CaffeineCacheManager();
        manager.setCaffeine(Caffeine.newBuilder()
                .maximumSize(5_000)
                .expireAfterWrite(Duration.ofMinutes(10))
                .recordStats());
        return manager;
    }
}
```

### When local (Caffeine) beats distributed (Redis)

- Data is cheap to recompute per instance and doesn't need to be identical across instances (e.g. a parsed config value, a small reference/lookup table).
- Latency matters more than perfect consistency — Caffeine reads are in-process, no network hop.
- You want zero extra infrastructure to operate (no Redis cluster to patch and monitor).
- The dataset is small enough to fit comfortably in heap per instance, multiplied by however many instances you run.

Reach for Redis instead when multiple instances must see the *same* cached value (e.g. rate-limit counters, session data, anything where staleness across instances would cause a visible bug).

## Cache Eviction

Eviction is how entries leave the cache before you explicitly ask for them — either because they expired, because the cache got full, or because you told it to remove something.

### TTL vs. TTI

| Term | Meaning | Caffeine spec key |
|---|---|---|
| **TTL** (Time-To-Live) | Entry expires a fixed time after it was **written** or last updated, regardless of how often it's read | `expireAfterWrite` |
| **TTI** (Time-To-Idle) | Entry expires a fixed time after it was **last read**; frequently-accessed entries stay alive indefinitely | `expireAfterAccess` |

Use TTL when data has a natural "freshness window" (e.g. an exchange rate good for 5 minutes). Use TTI when you want popular items to stay cached as long as someone keeps asking for them, and unpopular items to drop out quickly.

### Eviction policies (when the cache is full)

| Policy | Idea | Where you'll see it |
|---|---|---|
| **LRU** (Least Recently Used) | Evict the entry that hasn't been accessed for the longest time | Redis `allkeys-lru`, many simple caches |
| **LFU** (Least Frequently Used) | Evict the entry accessed the fewest number of times | Redis `allkeys-lfu` |
| **W-TinyLFU** | A hybrid: tracks frequency cheaply with a probabilistic filter, and combines it with a small LRU-like window to handle sudden bursts well | Caffeine's default algorithm |

You don't usually need to implement these — just know the trade-off: pure LRU can be fooled by a one-off scan that evicts genuinely popular items; frequency-aware policies like W-TinyLFU resist that better, which is why Caffeine tends to outperform naive LRU caches in benchmarks.

### `@CacheEvict` attributes

```java
@CacheEvict(cacheNames = "products", key = "#id")
public void delete(Long id) { repository.deleteById(id); }

// wipe the whole cache
@CacheEvict(cacheNames = "products", allEntries = true)
public void clearAll() { }

// evict BEFORE the method runs, not after
@CacheEvict(cacheNames = "products", key = "#id", beforeInvocation = true)
public void deleteRiskyOperation(Long id) { repository.deleteById(id); }
```

| Attribute | Default | Effect |
|---|---|---|
| `allEntries` | `false` | If `true`, clears every entry in the named cache instead of just the one matching `key` |
| `beforeInvocation` | `false` | If `true`, evicts *before* the method runs. Normally eviction happens *after* the method completes successfully — `beforeInvocation=true` is for cases where you want the entry gone even if the method throws |

### Keeping cache and database consistent

The core rule: **write to the database, then update or evict the cache** — not the other way round. If you evict first and the database write fails, the cache is empty but stale data might get re-cached from a concurrent read before the retry. If you evict after a successful write, worst case is a brief window where the cache still serves the old value.

Common patterns:

- **Cache-aside** (most common with `@Cacheable`/`@CacheEvict`): the app checks the cache, falls back to the database on a miss, and explicitly evicts/updates the cache on writes.
- **Write-through**: every write goes through the cache, which itself writes to the database — the cache is always in sync, but this couples the cache tightly to the write path.
- **Write-behind**: writes go to the cache immediately and are flushed to the database asynchronously — fast, but risks data loss if the process crashes before the flush.

For most CRUD services, cache-aside with `@Cacheable` for reads and `@CacheEvict`/`@CachePut` for writes is the simplest correct option.

## Cache Synchronization

Synchronization matters once you have multiple threads (or, with a distributed cache, multiple instances) racing to fill the same cache entry.

### Cache stampede / thundering herd

Imagine a popular cache entry expires. If ten requests arrive in the same instant, all ten see a cache miss, and — without protection — all ten hit the database at the same time to recompute the same value. This is called a **cache stampede** or **thundering herd**, and it can take down a database that would otherwise handle the load fine if only one request had recomputed the value.

`@Cacheable(sync = true)` fixes exactly this for local caches: it makes concurrent callers for the *same key* block and wait for the first caller to finish, instead of all of them recomputing independently.

```java
@Cacheable(cacheNames = "products", key = "#id", sync = true)
public Product findById(Long id) {
    return repository.findById(id)
            .orElseThrow(() -> new ProductNotFoundException(id));
}
```

Notes on `sync = true`:

- It only synchronizes *within* one JVM — it does not prevent a stampede across multiple instances hitting a shared Redis cache simultaneously. For that, you'd need a distributed lock (e.g. Redis `SETNX`) or a caching library with built-in "loading cache" semantics (Caffeine's `LoadingCache` behaves similarly, coordinating concurrent loads for the same key).
- Combining `sync = true` with `unless` isn't supported by the abstraction — keep synchronized cache methods simple.

### `@Transactional` + cache eviction ordering

A subtle bug: if a method is `@Transactional` and also does `@CacheEvict`, and the eviction runs *before* the transaction commits, another thread could read the database (via a cache miss) and re-populate the cache with the **old** pre-commit value — right before your transaction actually commits the new value. Now the cache is stuck showing stale data until the next write.

```java
@Transactional
@CacheEvict(cacheNames = "products", key = "#product.id")
public void updateProduct(Product product) {
    repository.save(product);
    // if eviction fires here, immediately, another thread might
    // re-read+re-cache the OLD row before this transaction commits
}
```

Spring's fix is `TransactionAwareCacheManagerProxy` (or, more simply, setting `transactionAware` on cache managers that support it), which defers cache operations — puts and evictions — until the surrounding transaction actually commits. If the transaction rolls back, the cache operation never happens either.

```java
import org.springframework.cache.CacheManager;
import org.springframework.cache.transaction.TransactionAwareCacheManagerProxy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;

@Configuration
public class TransactionAwareCacheConfig {

    @Bean
    @Primary
    public CacheManager transactionAwareCacheManager(CacheManager targetCacheManager) {
        return new TransactionAwareCacheManagerProxy(targetCacheManager);
    }
}
```

With this wrapper in place, `@CacheEvict` on a `@Transactional` method is deferred until commit, closing the race window described above.

## Common Code Review / Interview Pitfalls

- **Self-invocation bypasses the cache proxy.** Calling a `@Cacheable` method from another method in the *same* class via `this.foo()` skips the AOP proxy entirely — no caching happens.
  ```java
  // ❌ silently uncached
  public Report wrap(String id) { return this.generate(id); }
  ```
  ```java
  // ✅ call through a separate bean so the proxy is invoked
  public Report wrap(String id) { return reportService.generate(id); }
  ```

- **Caching mutable objects that callers then mutate.** If you cache a mutable object and a caller mutates the returned reference, every future cache hit returns the corrupted object.
  ```java
  // ❌ caller mutates the cached instance directly
  Product p = productService.findById(1L);
  p.setPrice(BigDecimal.ZERO); // corrupts the cached copy for everyone
  ```
  ```java
  // ✅ return an immutable copy/record, or defensively copy on read
  public record ProductView(Long id, String name, BigDecimal price) {}
  ```

- **No TTL means unbounded memory or stale-forever data.** A cache with no expiration keeps growing (memory leak) and never refreshes (permanently stale).
  ```java
  // ❌ no size cap, no expiry
  Caffeine.newBuilder().build();
  ```
  ```java
  // ✅ always bound size and/or time
  Caffeine.newBuilder().maximumSize(10_000).expireAfterWrite(Duration.ofMinutes(10)).build();
  ```

- **Caching JPA entities directly.** Entities can carry lazy-loaded proxies (Hibernate) that throw `LazyInitializationException` once the session is closed, and once detached/serialized they can behave unpredictably.
  ```java
  // ❌ caching the entity itself
  @Cacheable("products")
  public Product findById(Long id) { return repository.findById(id).orElseThrow(); }
  ```
  ```java
  // ✅ cache a plain DTO/projection instead
  @Cacheable("products")
  public ProductDto findById(Long id) {
      return toDto(repository.findById(id).orElseThrow());
  }
  ```

- **Non-serializable or unstable cache keys.** With Redis, keys/values must serialize cleanly; keys built from objects without a stable `equals`/`hashCode` (or from mutable objects) cause cache misses or `ClassCastException`s.
  ```java
  // ❌ key derived from a mutable, non-serializable object
  @Cacheable(cacheNames = "orders", key = "#request")
  public Order find(OrderRequest request) { ... }
  ```
  ```java
  // ✅ key is a simple, stable, serializable value
  @Cacheable(cacheNames = "orders", key = "#request.orderId()")
  public Order find(OrderRequest request) { ... }
  ```

- **Evicting before the transaction commits.** Evicting mid-transaction leaves a race window where another thread re-caches the pre-commit (stale) value.
  ```java
  // ❌ eviction fires immediately, transaction may still roll back or hasn't committed
  @Transactional
  @CacheEvict(cacheNames = "products", key = "#p.id")
  public void update(Product p) { repository.save(p); }
  ```
  ```java
  // ✅ wrap the CacheManager with TransactionAwareCacheManagerProxy
  // so eviction is deferred until after commit
  ```

- **Caching null or error results by accident.** Without guarding, a failed lookup or exception path can get cached, so every subsequent call keeps returning "not found" even after the data exists.
  ```java
  // ❌ nulls get cached and stick around
  @Cacheable("products")
  public Product findById(Long id) { return repository.findByIdOrNull(id); }
  ```
  ```java
  // ✅ exclude null results explicitly
  @Cacheable(cacheNames = "products", unless = "#result == null")
  public Product findById(Long id) { return repository.findByIdOrNull(id); }
  ```

- **Caching per-user data under a global key — a data leak.** If the key doesn't include the user/tenant identifier, user A's cached response can be served to user B.
  ```java
  // ❌ key ignores which user is asking — everyone shares one entry
  @Cacheable(cacheNames = "cart", key = "'current'")
  public Cart getCart(Long userId) { ... }
  ```
  ```java
  // ✅ include the user in the key
  @Cacheable(cacheNames = "cart", key = "#userId")
  public Cart getCart(Long userId) { ... }
  ```

- **`@CacheEvict(allEntries = true)` on a hot path.** Wiping an entire cache on every write causes a stampede of cache misses right after, hurting the very requests you were trying to speed up.
  ```java
  // ❌ every single product update nukes the whole cache
  @CacheEvict(cacheNames = "products", allEntries = true)
  public void update(Product p) { repository.save(p); }
  ```
  ```java
  // ✅ evict only the affected entry
  @CacheEvict(cacheNames = "products", key = "#p.id")
  public void update(Product p) { repository.save(p); }
  ```

- **Unbounded `ConcurrentMapCacheManager` in production.** It's fine for a quick demo or a unit test, but it has no TTL and no size limit — a slow, invisible memory leak in a real deployment.
  ```java
  // ❌ default map-based cache manager, shipped to prod
  new ConcurrentMapCacheManager("products");
  ```
  ```java
  // ✅ use a bounded/evicting manager (Caffeine locally, Redis if distributed)
  new CaffeineCacheManager(); // configured with maximumSize + expireAfterWrite
  ```

- **Ignoring cache-miss latency spikes.** If every entry expires at the same instant (e.g. a fixed TTL set at deploy time for all keys), you get a synchronized wave of cache misses hitting the database together.
  ```java
  // ❌ every key expires at exactly the same moment
  expireAfterWrite(10, TimeUnit.MINUTES); // and everything was written at startup
  ```
  ```java
  // ✅ add jitter to TTLs, or use refreshAfterWrite for async background reloads
  expireAfterWrite(Duration.ofMinutes(10 + random.nextInt(3)));
  ```

- **Forgetting caching is proxy-based when unit testing.** Calling the method directly on a plain (non-Spring-context) instance in a unit test never triggers caching, which can mask self-invocation bugs that only appear in production.
  ```java
  // ❌ new ProductService() bypasses Spring entirely — "caching works" is untested
  ProductService service = new ProductService(repo);
  ```
  ```java
  // ✅ use @SpringBootTest (or a slice test) so the real caching proxy is exercised
  @SpringBootTest
  class ProductServiceCachingTest { @Autowired ProductService service; }
  ```

- **Using `@CachePut` when you meant `@Cacheable`.** `@CachePut` always executes the method body — using it for reads defeats the entire purpose of caching, since you never skip the expensive call.
  ```java
  // ❌ this "cached" read still hits the database every single time
  @CachePut(cacheNames = "products", key = "#id")
  public Product findById(Long id) { return repository.findById(id).orElseThrow(); }
  ```
  ```java
  // ✅ use @Cacheable for reads; reserve @CachePut for updates that must refresh the cache
  @Cacheable(cacheNames = "products", key = "#id")
  public Product findById(Long id) { return repository.findById(id).orElseThrow(); }
  ```

## Quick Recap

- Caching trades memory and staleness risk for speed; it's a tool, not a default.
- `@EnableCaching` turns on Spring's AOP-based caching; `@Cacheable`, `@CachePut`, `@CacheEvict`, `@Caching`, and `@CacheConfig` are the annotations you'll use daily.
- Caching is proxy-based — self-invocation within the same class bypasses it. Split logic into a separate bean if needed.
- `key`, `condition`, and `unless` give fine-grained SpEL control over what gets cached and under what key; a custom `KeyGenerator` centralizes repeated key logic.
- `ConcurrentMapCacheManager` is for demos/tests only — no TTL, no eviction, unbounded growth. Never use it in production.
- Caffeine is the standard local, in-JVM cache: fast, size- and time-bounded, uses the W-TinyLFU eviction algorithm.
- Redis is the standard distributed cache: shared across instances, configurable per-cache TTLs via `RedisCacheManager`, prefer `GenericJackson2JsonRedisSerializer` over JDK serialization.
- Hazelcast and JCache (JSR-107) are alternatives worth knowing for distributed or vendor-neutral scenarios.
- TTL expires entries by age since write; TTI (time-to-idle) expires entries by age since last access.
- `@CacheEvict(allEntries = true)` clears a whole cache — powerful but dangerous on hot write paths; prefer targeted eviction by key.
- Always write to the database first, then evict/update the cache — and use `TransactionAwareCacheManagerProxy` so eviction waits for commit.
- `@Cacheable(sync = true)` prevents a cache stampede within one JVM by letting only one caller recompute a missing entry per key.
- Never cache JPA entities directly (lazy proxies, detached state) — cache DTOs instead.
- Watch for data leaks: always scope cache keys by user/tenant when caching per-user data.
