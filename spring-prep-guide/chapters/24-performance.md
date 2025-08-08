# 24. Performance

## Overview

Performance work in Spring Boot falls into two buckets: making the application **start faster** and making it **run faster** under load. Before touching any of this, measure first — use a profiler, a load test tool (JMeter, Gatling, k6), or Spring's own startup metrics, and find the actual bottleneck instead of guessing. This matters because premature optimization wastes time on code that was never slow, and it can make code harder to read for no real benefit. It also helps to separate two different goals: **latency** is how long one request takes, and **throughput** is how many requests you can handle per second — improving one does not automatically improve the other, and sometimes they trade off against each other (batching improves throughput but can add latency). Keep that distinction in mind for every subtopic below: a connection pool tweak that helps throughput under heavy load might hurt latency for a lightly loaded service, and vice versa.

## Startup Optimization

Startup time matters for local development (faster feedback loop) and for cloud deployments (faster autoscaling, faster rolling restarts, lower cost if you pay per second). Spring Boot gives you tools to see *where* time goes during startup, and several levers to cut it.

### Measure before you optimize

Spring has a built-in instrument called `ApplicationStartup`. Think of it like a stopwatch that Spring itself uses to time each phase of its own boot sequence (loading configuration, scanning for beans, creating beans, etc.). The simplest implementation, `BufferingApplicationStartup`, records these timings in memory so you can inspect them after startup.

```java
@SpringBootApplication
public class DemoApplication {

    public static void main(String[] args) {
        SpringApplication app = new SpringApplication(DemoApplication.class);
        app.setApplicationStartup(new BufferingApplicationStartup(2048));
        ConfigurableApplicationContext context = app.run(args);

        BufferingApplicationStartup startup =
                (BufferingApplicationStartup) context.getApplicationStartup();
        startup.getBufferedTimings().forEach(timing ->
                System.out.printf("%-40s %6d ms%n",
                        timing.getStartupStep().getName(),
                        timing.getDuration().toMillis()));
    }
}
```

You can also expose this data as an actuator endpoint (`/actuator/startup`) instead of printing it manually. For a quicker, low-effort check, just run the app with debug logging turned on:

```bash
java -jar app.jar --debug
```

The `--debug` flag prints the **auto-configuration report**: which auto-configurations were applied, and — more usefully — which ones were skipped and why. This tells you if Spring Boot is pulling in configuration you don't actually need (for example, a database auto-configuration when you don't use a database in that particular module).

### Trim what Spring has to scan and configure

- **Reduce `@ComponentScan` scope.** Scanning `com.company` instead of `com.company.myservice` forces Spring to inspect far more classes than necessary.
- **Remove unused starters.** Every `spring-boot-starter-*` you don't need still triggers auto-configuration classes to be evaluated at startup.
- **Exclude auto-configurations you don't use:**

```java
@SpringBootApplication(exclude = {
        JmxAutoConfiguration.class,
        SecurityAutoConfiguration.class
})
public class DemoApplication { }
```

- **Prefer constructor injection and avoid classpath scanning tricks** (like scanning by annotation across huge packages) that force early class loading.

### Class Data Sharing (CDS) and AppCDS

The JVM spends real time parsing and verifying class files on every startup. **Class Data Sharing (CDS)** lets the JVM pre-process a set of classes once and dump them into a shared archive file. On subsequent startups, the JVM just memory-maps this archive instead of re-parsing everything — like pre-chewing your food once so every future meal is faster to digest. **AppCDS** extends this to your own application classes, not just JDK classes.

Spring Boot 3.3+ makes this easy to combine with its AOT (Ahead-Of-Time) processing. The workflow is a two-step "training run" followed by the real run:

```bash
# Step 1: training run — start the app once to generate the CDS archive
java -XX:ArchiveClassesAtExit=app-cds.jsa \
     -Dspring.context.exit=onRefresh \
     -jar app.jar

# Step 2: real runs — reuse the archive for faster startup
java -XX:SharedArchiveFile=app-cds.jsa \
     -jar app.jar
```

Combined with Spring AOT processing (`-Dspring.aot.enabled=true`, generated automatically when you build a native or AOT-optimized jar), CDS can meaningfully cut the time spent on class loading and bean definition processing.

### The extreme option: AOT and native images

Spring's AOT engine can also produce a **native image** (via GraalVM) — a self-contained executable with no JVM startup phase at all. Native images start in tens of milliseconds instead of seconds, and use far less memory at idle. The trade-off: longer build times, some reflection/dynamic-proxy limitations, and a build pipeline that requires GraalVM. Use it when startup time is a hard requirement (e.g., scale-to-zero serverless functions); for a typical always-on service, the JVM plus CDS plus lazy init is usually enough.

| Technique | Startup improvement | Cost |
|---|---|---|
| Trim component scan / auto-config | Small–medium | Low effort |
| Lazy initialization | Medium | First-request latency |
| CDS / AppCDS | Medium | Extra build step, training run |
| AOT / native image | Very large | Build complexity, GraalVM toolchain |

## Lazy Initialization

By default, Spring creates **all** singleton beans when the application context starts. Lazy initialization flips this: beans are created only the first time something actually needs them.

Enable it globally with one property:

```properties
spring.main.lazy-initialization=true
```

Or mark individual beans:

```java
@Lazy
@Service
public class ReportGeneratorService {
    // heavy setup deferred until first use
}
```

Analogy: it's like not turning on every light in the house when you wake up — you flip the switch only when you walk into a room. This reduces the amount of work Spring does before it declares "the app is ready," so the server starts accepting traffic sooner.

The trade-off is real, though:

- **First-request latency spikes.** The very first call that touches a lazily-initialized bean now pays the bean-creation cost. If that bean is expensive (opens a connection pool, loads a cache, connects to a message broker), the user making that first request feels it.
- **Configuration errors surface late.** With eager initialization, a broken bean definition (missing property, wrong wiring) fails immediately at startup — you find out before you deploy to production traffic. With lazy init, that same broken bean might not be touched until 2 a.m. in production, when someone finally calls the one endpoint that needs it.
- Health checks and readiness probes can be misleading: the app reports "started" before its dependent beans are actually verified.

| Aspect | Eager (default) | Lazy |
|---|---|---|
| Startup time | Slower | Faster |
| First-request latency | Normal | Higher (pays bean cost) |
| Config error visibility | Immediate, at boot | Delayed, at first use |
| Best for | Production services with health checks | Local dev, CLI tools, low-traffic services |

A common middle ground: keep lazy init off in production but use it in local development (`spring.profiles.active=dev` with a dev-only properties file) for faster iteration, or apply `@Lazy` selectively to a handful of genuinely expensive beans rather than globally.

## Bean Optimization

Beans are the objects Spring manages for you. Most performance mistakes with beans come from doing too much work at the wrong time, or creating too many of them.

### Avoid heavy work in `@PostConstruct`

`@PostConstruct` runs right after Spring finishes injecting a bean's dependencies — during application startup, for singleton beans. Doing slow I/O here (a network call, a large file read, a synchronous warm-up query) directly adds to your startup time.

```java
// Bad: blocks startup on an external call
@Component
public class PricingCache {
    @PostConstruct
    public void warmUp() {
        this.prices = pricingClient.fetchAllPrices(); // slow network call at boot
    }
}

// Better: warm up asynchronously, or lazily on first access
@Component
public class PricingCache {
    private final AtomicReference<Map<String, BigDecimal>> prices = new AtomicReference<>(Map.of());

    @Async
    @EventListener(ApplicationReadyEvent.class)
    public void warmUp() {
        prices.set(pricingClient.fetchAllPrices());
    }
}
```

### Prefer singleton scope; be careful with prototype

Spring beans are **singletons** by default — one instance shared for the whole application. This is cheap: created once, reused forever. **Prototype** scope creates a brand-new instance every time the bean is requested, which means paying the full construction cost (dependency resolution, any `@PostConstruct` logic) on every single injection point that resolves it.

```java
@Scope("prototype")
@Component
public class ExpensiveParser {
    // A new instance is created every time this bean is looked up.
    // Fine if genuinely stateful per-use and cheap to build.
    // Expensive if construction does real work.
}
```

- Never create a full Spring bean (via `ApplicationContext.getBean(...)` or a prototype-scoped bean lookup) **per HTTP request** unless you specifically need per-request state and the bean is cheap to build. It's far cheaper to keep a stateless singleton and pass request data as method parameters.
- Watch out for accidentally creating new objects that *look* like simple POJOs but are actually resolved through the Spring container on every call — that lookup overhead adds up under load.

### Keep component scanning tight

A `@ComponentScan` rooted too high in your package tree (e.g., at `com.company` instead of `com.company.orders`) forces Spring to examine every class underneath it for annotations, even classes with nothing to do with Spring. In a large monolith this can add real seconds to startup and unnecessary memory for bean definitions that are never used.

```java
// Bad: scans the entire codebase
@SpringBootApplication
@ComponentScan("com.company")
public class DemoApplication { }

// Better: scan only what this application module actually needs
@SpringBootApplication
@ComponentScan({"com.company.orders", "com.company.shared.config"})
public class DemoApplication { }
```

## Connection Pooling

Opening a new database connection is expensive: it means a TCP handshake, authentication, and session setup on the database side — commonly tens of milliseconds. If every HTTP request opened and closed its own database connection, most of the request's time would be spent just connecting, not doing real work.

A **connection pool** solves this by keeping a set of already-open connections ready to be reused. Think of it like a taxi rank: instead of building a new car every time someone needs a ride, a fleet of cars sits ready, and you just hop into the next free one and return it when you're done.

### Sizing the pool

More connections is not always better — each connection consumes memory and a thread/resource on the database side, and too many concurrent connections can make the database itself slower (context switching, lock contention). A widely cited starting formula from HikariCP's own documentation is:

```
connections ≈ (core_count * 2) + effective_spindle_count
```

- `core_count` — number of CPU cores available to the database server.
- `effective_spindle_count` — a rough measure of I/O capacity (for modern SSD-backed or cloud-managed databases, this is often just 1, since there's no spinning disk queue to model).

For a database server with 8 cores and SSD storage, that formula suggests roughly `(8 * 2) + 1 = 17` connections — often much smaller than the "just set it to 100" instinct. The right number ultimately comes from load testing, not the formula alone; treat it as a sane starting point.

### Recognizing pool exhaustion

Pool exhaustion happens when every connection in the pool is checked out and none are free. New requests then queue up waiting for a connection.

Symptoms:

- Requests that used to be fast suddenly hang for seconds, then either succeed or time out.
- Logs show messages like `Connection is not available, request timed out after 30000ms`.
- Thread dumps show many threads blocked waiting to acquire a connection (`HikariPool.getConnection`).
- The problem often gets *worse* under load, not better, because slow requests hold onto connections longer, starving new requests — a vicious cycle.

## HikariCP

HikariCP is the default connection pool in Spring Boot (bundled automatically when you use `spring-boot-starter-data-jpa` or `spring-boot-starter-jdbc`). It's known for being fast and lightweight, and Spring Boot exposes its settings under `spring.datasource.hikari.*`.

```properties
# Core sizing
spring.datasource.hikari.maximum-pool-size=15
spring.datasource.hikari.minimum-idle=5

# Timeouts (all in milliseconds)
spring.datasource.hikari.connection-timeout=30000
spring.datasource.hikari.idle-timeout=600000
spring.datasource.hikari.max-lifetime=1800000

# Leak detection
spring.datasource.hikari.leak-detection-threshold=60000

spring.datasource.hikari.pool-name=OrdersServicePool
```

| Property | Meaning | Typical value |
|---|---|---|
| `maximum-pool-size` | Hard cap on total connections in the pool | Sized from formula + load test |
| `minimum-idle` | Connections kept ready even when idle | A few less than max, or equal to it |
| `connection-timeout` | How long a caller waits for a free connection before failing | 20-30 seconds |
| `idle-timeout` | How long an idle connection sits before being closed (only applies if pool size > `minimum-idle`) | 10 minutes |
| `max-lifetime` | Maximum age of a connection before it's retired, even if in use it finishes first | 30 minutes, and should be a few minutes shorter than the database's own connection timeout |
| `leak-detection-threshold` | Logs a warning if a connection is checked out longer than this without being returned | 30-60 seconds, `0` disables it |

### Diagnosing leaked connections

A "leak" means code checked out a connection (e.g., via `dataSource.getConnection()` or an under-the-hood JPA transaction) and never returned it — usually because an exception path skipped closing it, or a `try` block wasn't wrapped in `try-with-resources`.

```java
// Bad: connection never closed if getData() throws
Connection conn = dataSource.getConnection();
ResultSet rs = conn.createStatement().executeQuery("SELECT * FROM orders");
processResults(rs);
conn.close(); // skipped entirely on exception

// Good: connection is always returned to the pool
try (Connection conn = dataSource.getConnection();
     Statement stmt = conn.createStatement();
     ResultSet rs = stmt.executeQuery("SELECT * FROM orders")) {
    processResults(rs);
}
```

When `leak-detection-threshold` is set, HikariCP logs a stack trace showing exactly where the leaked connection was checked out — that stack trace is your starting point for finding the missing `close()`.

### Monitoring with Micrometer

Spring Boot Actuator auto-configures HikariCP metrics through Micrometer once you add the actuator and a metrics registry:

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,metrics,prometheus
  metrics:
    enable:
      hikaricp: true
```

Key metrics to watch:

- `hikaricp.connections.active` — connections currently checked out.
- `hikaricp.connections.idle` — connections sitting ready.
- `hikaricp.connections.pending` — threads waiting for a connection (a sustained non-zero value here is a red flag).
- `hikaricp.connections.timeout` — count of callers that gave up waiting.

## HTTP Performance

HTTP performance is about reducing the cost of moving requests and responses across the network, and making sure your server's threading model matches its workload.

### Keep-alive and client-side connection pooling

Without **keep-alive**, every HTTP request opens a brand-new TCP connection (and, for HTTPS, redoes the TLS handshake), then closes it — expensive, especially over high-latency networks. Keep-alive reuses the same TCP connection for multiple requests, like keeping a phone call open instead of hanging up and redialing for every sentence.

When your Spring Boot app calls other services, make sure the HTTP client is configured to pool and reuse connections rather than opening a fresh one per call:

```java
@Bean
public RestClient restClient() {
    ClientHttpRequestFactorySettings settings = ClientHttpRequestFactorySettings.DEFAULTS
            .withConnectTimeout(Duration.ofSeconds(2))
            .withReadTimeout(Duration.ofSeconds(5));

    return RestClient.builder()
            .requestFactory(ClientHttpRequestFactories.get(settings))
            .baseUrl("https://inventory-service.internal")
            .build();
}

@Bean
public WebClient webClient() {
    ConnectionProvider provider = ConnectionProvider.builder("custom-pool")
            .maxConnections(50)
            .pendingAcquireTimeout(Duration.ofSeconds(5))
            .maxIdleTime(Duration.ofSeconds(30))
            .build();

    return WebClient.builder()
            .clientConnector(new ReactorClientHttpConnector(HttpClient.create(provider)))
            .baseUrl("https://inventory-service.internal")
            .build();
}
```

Apache HttpClient (often used under `RestTemplate`) needs the same care — use a `PoolingHttpClientConnectionManager` instead of the default single-connection behavior.

### Compression

Enabling response compression trades a little CPU time for a lot less data sent over the wire — usually a big win for JSON APIs with larger payloads:

```yaml
server:
  compression:
    enabled: true
    mime-types: application/json,application/xml,text/html,text/plain
    min-response-size: 1024
```

- Keep response payloads lean in the first place: return only the fields the client needs (see projections below), paginate large collections, and avoid nesting entire object graphs into a single response.

### Tomcat thread pool sizing

Spring Boot's embedded Tomcat uses a bounded thread pool to handle requests. Each thread handles one request at a time (in the traditional, non-virtual-thread model), so the pool size caps how many requests you can process concurrently.

```yaml
server:
  tomcat:
    threads:
      max: 200
      min-spare: 20
    accept-count: 100
```

- `threads.max` — maximum worker threads. Too low and requests queue even though the server has spare CPU; too high and you risk excessive context switching and memory use (each thread reserves stack memory).
- `accept-count` — how many incoming connections can queue once all threads are busy, before new connections get rejected outright.

### Java 21 virtual threads

Java 21 introduced **virtual threads** — lightweight threads managed by the JVM instead of the OS, so you can have thousands of them without the memory and scheduling overhead of that many OS threads. Spring Boot 3.2+ can switch Tomcat (and other blocking servers) to use one virtual thread per request:

```properties
spring.threads.virtual.enabled=true
```

What this fixes: if your request handling is mostly **blocked waiting** (on a database call, another HTTP call, a file read), virtual threads let you handle vastly more concurrent requests without exhausting a small OS thread pool, because a blocked virtual thread doesn't tie up an OS thread while it waits.

What it does **not** fix:

- CPU-bound work — if your bottleneck is computation, not waiting, virtual threads don't create more CPU.
- Slow downstream dependencies — a virtual thread waiting on a slow database is still waiting; the database is still slow.
- `synchronized` blocks that "pin" a virtual thread to its underlying OS thread while blocked inside them (fixed for most common cases in newer JDKs, but still worth knowing about) — see the pitfalls section.
- Bad connection pool sizing — you can now have thousands of virtual threads all wanting a database connection at once, so an undersized HikariCP pool becomes the new bottleneck instead of the thread pool.

### HTTP caching headers and ETag

HTTP caching lets clients (browsers, proxies, other services) skip re-fetching data that hasn't changed. An **ETag** is a short identifier (like a fingerprint) for a specific version of a response; the client sends it back on the next request, and if nothing changed, the server can reply "not modified" with an empty body instead of resending the whole payload.

```java
@GetMapping("/products/{id}")
public ResponseEntity<Product> getProduct(@PathVariable Long id, WebRequest request) {
    Product product = productService.findById(id);
    String etag = Long.toString(product.getVersion());

    if (request.checkNotModified(etag)) {
        return null; // Spring writes a 304 Not Modified automatically
    }

    return ResponseEntity.ok()
            .eTag(etag)
            .cacheControl(CacheControl.maxAge(Duration.ofMinutes(5)))
            .body(product);
}
```

- `Cache-Control: max-age=...` tells clients how long they can reuse a response without asking the server at all.
- Use these especially for read-heavy, rarely-changing resources — they cut both latency (no round trip needed) and server load.

## Database Performance

The database is very often the actual bottleneck behind a "slow API," so this is one of the highest-leverage areas to get right.

### Indexes

An index is a separate, sorted structure the database maintains so it can find rows matching a condition without scanning the entire table — like a book's index letting you jump straight to a topic instead of reading every page. Without the right index, a query filtering on `email` has to check every row.

```sql
-- Without this index, every login lookup scans the whole users table
CREATE INDEX idx_users_email ON users(email);

-- Composite index: order matters, put the most selective/most-filtered column first
CREATE INDEX idx_orders_customer_status ON orders(customer_id, status);
```

### Avoiding N+1 queries

The N+1 problem happens when loading a list of N parent entities triggers one extra query *per* parent to load a related child collection — 1 query for the list, plus N more queries, one at a time.

```java
// N+1 trap: fetching orders, then lazily loading items for each order separately
List<Order> orders = orderRepository.findAll();
for (Order order : orders) {
    System.out.println(order.getItems().size()); // triggers a separate query per order
}
```

```java
// Fixed: fetch everything in one query with a JOIN FETCH
@Query("SELECT DISTINCT o FROM Order o LEFT JOIN FETCH o.items WHERE o.status = :status")
List<Order> findByStatusWithItems(@Param("status") String status);
```

`@EntityGraph` is another way to declare which associations to fetch eagerly for a specific query, without changing the default fetch type on the entity itself.

### Batching writes

Instead of sending one `INSERT` or `UPDATE` statement per row, Hibernate can group several statements into a single batch sent to the database in one round trip.

```properties
spring.jpa.properties.hibernate.jdbc.batch_size=50
spring.jpa.properties.hibernate.order_inserts=true
spring.jpa.properties.hibernate.order_updates=true
```

- `batch_size` — how many statements to group per batch.
- `order_inserts` / `order_updates` — reorders statements so same-table operations are grouped together, which makes batching actually effective (mixed statement types can't be batched together).

### Pagination instead of loading everything

`findAll()` with no bounds loads the *entire* table into memory. On a table with a few hundred rows this is fine; on a table with millions, it can exhaust memory and take the connection pool hostage for a long time.

```java
// Bad on a large table
List<Order> allOrders = orderRepository.findAll();

// Good: bounded, predictable memory and latency
Page<Order> page = orderRepository.findAll(PageRequest.of(0, 50, Sort.by("createdAt").descending()));
```

### Projections instead of full entities

If a screen only needs a customer's name and email, don't load the entire `Customer` entity (with every column and every lazy association ready to be triggered). A **projection** fetches only the fields you actually need.

```java
public interface CustomerSummary {
    String getName();
    String getEmail();
}

public interface CustomerRepository extends JpaRepository<Customer, Long> {
    List<CustomerSummary> findByStatus(String status);
}
```

### Read-only transactions and short transactions

Marking a transaction read-only lets Hibernate skip dirty-checking (comparing entity state to see what changed) since nothing will be written back — a small but free win for read paths.

```java
@Transactional(readOnly = true)
public List<OrderSummary> listRecentOrders(Long customerId) {
    return orderRepository.findRecentByCustomer(customerId);
}
```

Keep transactions **short**. A transaction holds a database connection (and potentially locks) for its entire duration. Doing slow, unrelated work — like calling another HTTP service — inside a transaction ties up a pooled connection the whole time that unrelated call is in flight, which starves other requests of connections.

### Statement caching and EXPLAIN plans

- Most JDBC drivers and connection pools support **prepared statement caching**, reusing the database's parsed query plan for the same SQL shape instead of re-parsing it every time. HikariCP works well with the driver's own statement cache (e.g., PostgreSQL's `prepareThreshold`, MySQL's `cachePrepStmts`).
- `EXPLAIN` (or `EXPLAIN ANALYZE`) shows you the database's actual execution plan for a query — whether it used an index, how many rows it scanned, where the time went. Always check the plan for a slow query before guessing at a fix.

```sql
EXPLAIN ANALYZE
SELECT * FROM orders WHERE customer_id = 42 AND status = 'PENDING';
```

### Second-level cache trade-offs

Hibernate's second-level cache stores entities across sessions/transactions, potentially avoiding database round trips entirely for frequently-read, rarely-changed data. The trade-off: it adds memory usage, cache invalidation complexity, and the risk of serving **stale** data if something else updates the database directly (a batch job, another service, a manual SQL fix). Use it selectively for genuinely slow-changing reference data, not as a default for everything.

## JVM Tuning for Spring

Spring Boot apps run on the JVM, so JVM-level settings — heap size, garbage collector choice, container awareness — directly affect both startup and steady-state performance.

### Heap sizing

The heap is where your Java objects live. Set it too small and you get frequent garbage collection pauses (or `OutOfMemoryError`); set it too large relative to the container's memory limit and the JVM (or the OS) can be killed for using too much memory.

```bash
# Fixed heap size, common in traditional VMs
java -Xms512m -Xmx512m -jar app.jar

# Container-aware: let the JVM size the heap as a percentage of the container's memory limit
java -XX:MaxRAMPercentage=75.0 -jar app.jar
```

- `-Xms` / `-Xmx` set the initial and maximum heap size explicitly. Setting them equal avoids the overhead of the heap resizing itself while running.
- `-XX:MaxRAMPercentage` is the modern, container-friendly approach: instead of a fixed number, you say "use at most 75% of whatever memory this container has," which travels well across environments with different memory limits (dev laptop vs. a Kubernetes pod with a 512Mi limit).

### Choosing a garbage collector

| Collector | Best for | Trade-off |
|---|---|---|
| **G1 (default since Java 9)** | General-purpose, balanced throughput and pause times | Good default for most Spring Boot apps |
| **ZGC** | Very large heaps, ultra-low pause time requirements | More CPU/memory overhead; overkill for small heaps |
| **Serial GC** | Small containers, low memory, single-core environments | Pauses the whole application during collection ("stop-the-world"); fine only for small heaps and low traffic |

```bash
# Explicitly select G1 (default, but explicit is sometimes clearer in ops docs)
java -XX:+UseG1GC -jar app.jar

# ZGC for very large heaps needing minimal pause times
java -XX:+UseZGC -jar app.jar

# Serial GC for a small, memory-constrained container
java -XX:+UseSerialGC -jar app.jar
```

For a typical containerized Spring Boot microservice with a modest heap (say, under 2GB) and normal request-response traffic, G1 is almost always the right default — don't reach for ZGC just because it sounds more advanced.

### Container awareness

Modern JVMs (Java 10+) detect that they're running inside a container and read the container's CPU and memory limits (from cgroups) instead of the host machine's. This is on by default (`-XX:+UseContainerSupport`), but it's worth knowing the flag exists, because older base images or unusual container setups occasionally need it forced or, in rare debugging scenarios, disabled to compare behavior.

```bash
java -XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0 -jar app.jar
```

Without container awareness, a JVM inside a container with a 512Mi memory limit might think it has access to the *host's* full memory (say, 64GB) and size its heap accordingly — a recipe for the container being killed by the orchestrator for exceeding its memory limit (an "OOMKilled" pod in Kubernetes).

### JFR and jcmd

**JFR (Java Flight Recorder)** is a low-overhead built-in profiler that records detailed events (GC pauses, thread activity, allocations, method sampling) that you can inspect afterward — like a flight data recorder for your JVM. **`jcmd`** is a command-line tool for talking to a running JVM process: triggering a heap dump, starting/stopping a JFR recording, checking GC stats, without restarting the app.

```bash
# Start a 60-second JFR recording on a running process
jcmd <pid> JFR.start duration=60s filename=recording.jfr

# Check current heap and GC info
jcmd <pid> GC.heap_info

# Trigger a heap dump for later analysis
jcmd <pid> GC.heap_dump /tmp/heap.hprof
```

### Common flags at a glance

| Flag | Purpose |
|---|---|
| `-Xms` / `-Xmx` | Initial / maximum heap size |
| `-XX:MaxRAMPercentage` | Heap sized as % of container memory limit |
| `-XX:+UseG1GC` / `-XX:+UseZGC` / `-XX:+UseSerialGC` | Garbage collector choice |
| `-XX:+UseContainerSupport` | Detect and respect container CPU/memory limits (default on) |
| `-XX:+HeapDumpOnOutOfMemoryError` | Auto-dump the heap when an `OutOfMemoryError` happens |
| `-Xlog:gc*` | Detailed GC logging |
| `-Dspring.aot.enabled=true` | Enable Spring AOT optimizations |

## Memory Optimization

Memory problems in a Spring app usually show up as slow, creeping performance degradation followed by an `OutOfMemoryError` or a container restart — not an instant crash.

### Analyzing a heap dump

A heap dump is a snapshot of every object on the heap at a moment in time. Tools like Eclipse MAT (Memory Analyzer Tool) or VisualVM can open a dump and show you which objects are consuming the most memory and, critically, what's still holding a reference to them (the "retained size" and the reference chain back to a GC root) — that chain of references is usually how you find the actual leak.

```bash
# Capture a heap dump from a running process
jcmd <pid> GC.heap_dump /tmp/heap.hprof

# Or automatically on OOM
java -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/tmp/heap.hprof -jar app.jar
```

### Common leak sources in Spring apps

- **Unbounded caches.** A `HashMap` or `ConcurrentHashMap` used as a homemade cache, with entries added but never evicted or expired, grows forever. Use a proper caching library (Caffeine, via Spring's `@Cacheable`) with a size limit and expiry policy instead.

```java
// Leak-prone: grows without bound, entries never removed
private final Map<String, ExpensiveObject> cache = new ConcurrentHashMap<>();

// Better: bounded, with expiry
@Bean
public Cache<String, ExpensiveObject> cache() {
    return Caffeine.newBuilder()
            .maximumSize(10_000)
            .expireAfterWrite(Duration.ofMinutes(30))
            .build();
}
```

- **`ThreadLocal` values in pooled threads.** Web servers reuse a fixed pool of threads across many requests. If you set a `ThreadLocal` value (say, the current user, or a request ID) and never clear it, the *next* request handled by that same thread inherits stale data — and if the stored object is large, it also just sits there wasting memory until the thread happens to reuse that slot again.

```java
// Always clear ThreadLocal values, ideally in a finally block
private static final ThreadLocal<RequestContext> CONTEXT = new ThreadLocal<>();

public void handle(Request request) {
    CONTEXT.set(new RequestContext(request));
    try {
        process(request);
    } finally {
        CONTEXT.remove(); // without this, the value leaks into the next request on this thread
    }
}
```

- **Static collections.** A `static` list or map that objects get added to over the application's lifetime, with nothing ever removing them, keeps growing for as long as the JVM runs — effectively a permanent leak since `static` fields are never garbage collected while the class is loaded.
- **Listeners/subscribers never removed.** Event listeners, observers, or callback registrations that are added but never unregistered keep every object they reference alive, even after the "real" owner of that object is done with it.

### Off-heap memory and direct buffers

Not all memory used by your JVM process lives in the heap. **Direct (off-heap) buffers** — used heavily by NIO, Netty (which powers WebClient/reactive Spring), and some database drivers — are allocated outside the regular heap to avoid an extra copy between Java memory and native I/O buffers. This memory doesn't show up in heap dumps or `-Xmx` limits, but it still counts against the container's total memory, and it's controlled separately via `-XX:MaxDirectMemorySize`. If your container gets OOM-killed even though heap usage looks fine, off-heap/direct memory is a place to check next.

### String deduplication

Java strings backed by `char[]`/`byte[]` arrays can end up with many identical string contents living in memory as separate objects (very common in apps that parse a lot of similar JSON or log messages). G1GC supports **string deduplication**, which finds strings with identical content and makes them share the same backing array, reducing memory used without changing any application code:

```bash
java -XX:+UseG1GC -XX:+UseStringDeduplication -jar app.jar
```

### Right-sizing container limits vs. heap

The container's memory limit must always be comfortably larger than `-Xmx`, because the JVM needs room beyond the heap for thread stacks, metaspace (class metadata), direct buffers, JIT-compiled code, and native library memory.

```yaml
# Example Kubernetes resource limits alongside JVM flags
resources:
  requests:
    memory: "768Mi"
  limits:
    memory: "1024Mi"
```

```bash
# Leaves headroom above the heap for non-heap JVM memory
java -XX:MaxRAMPercentage=70.0 -jar app.jar
```

A rough rule of thumb: don't let `-Xmx` (or the percentage it resolves to) exceed roughly 70-75% of the container's memory limit, and validate the actual non-heap usage under real load rather than trusting the rule of thumb blindly.

## Common Code Review / Interview Pitfalls

- Changing code "to make it faster" without profiling or measuring first — you might optimize the wrong thing entirely.
- Calling `findAll()` on a table that can grow large, with no pagination or filtering.
- N+1 queries: fetching a list, then triggering one extra query per item to load a related collection or field.
- Endpoints returning unbounded lists with no pagination parameters at all.
- Frequently-queried columns with no supporting index — check with `EXPLAIN` before assuming an index will help.
- Using `SELECT *` or loading a full JPA entity when the caller only needs one or two fields — use a projection instead.
- Making an HTTP call or a slow query *inside* an open `@Transactional` method, holding a database connection (and locks) the whole time.
- HTTP or database calls with no timeout configured — a hung dependency can hang your whole request thread (or exhaust your pool) forever.
- Setting the connection pool size to some huge number "for speed" without sizing it against the database's actual CPU/connection capacity — this can make the database slower, not faster.
- Manually calling `getConnection()` without `try-with-resources`, leaking a connection whenever an exception is thrown before `close()`.
- Building an in-memory cache with a plain `Map` and no eviction policy or maximum size.
- String concatenation with `+` inside a loop or inside a hot logging statement — use `StringBuilder`, or better, parameterized logging (`log.debug("id={}", id)`) so the string isn't even built unless the log level is enabled.
- Using `@Async` backed by a thread pool with an unbounded queue — tasks pile up invisibly and memory grows until the JVM runs out, instead of failing fast or applying backpressure.
- Blocking calls (JDBC, blocking HTTP clients, `Thread.sleep`) made directly on a reactive event loop thread (e.g., inside a `WebClient`/reactor pipeline) — this can stall the whole event loop for other requests.
- Setting `-Xmx` larger than the container's memory limit, guaranteeing an eventual OOM-kill.
- Benchmarking against a JVM that just started — the JIT compiler hasn't warmed up yet, so early numbers are misleadingly slow.
- Reporting only average latency instead of p95/p99 — averages hide the slow outliers that real users actually experience.
- Combining Java 21 virtual threads with heavy use of `synchronized` blocks around blocking operations — under certain conditions this "pins" the virtual thread to its carrier OS thread, defeating the scalability benefit virtual threads are meant to provide.

## Quick Recap

- Measure first: use `ApplicationStartup`/`BufferingApplicationStartup`, `--debug`, profilers, and load tests before optimizing anything.
- Latency (per-request time) and throughput (requests/sec) are different goals — know which one you're optimizing.
- Cut startup time with tighter component scanning, fewer unused starters, lazy initialization, CDS/AppCDS, and — for extreme cases — AOT/native images.
- `spring.main.lazy-initialization=true` and `@Lazy` speed up startup but push bean-creation cost and config-error visibility to first use.
- Keep beans as cheap singletons; avoid heavy `@PostConstruct` work and per-request bean creation; keep `@ComponentScan` roots narrow.
- Connection pools avoid the cost of opening a new database connection per request; size them from CPU/spindle count and load tests, not from "make it big."
- HikariCP tuning centers on `maximum-pool-size`, `minimum-idle`, `connection-timeout`, `idle-timeout`, `max-lifetime`, and `leak-detection-threshold`; monitor via Micrometer's `hikaricp.*` metrics.
- For HTTP: reuse connections (keep-alive, pooled clients), compress responses, size the Tomcat thread pool deliberately, consider virtual threads for blocking-I/O-heavy workloads, and use ETag/`Cache-Control` for cacheable reads.
- For databases: index what you filter on, avoid N+1 with `JOIN FETCH`/`@EntityGraph`, batch writes, paginate, use projections, keep transactions short and read-only where possible, and check `EXPLAIN` plans before guessing.
- Tune the JVM with container-aware heap sizing (`MaxRAMPercentage`), a sensible GC (usually G1), and diagnose live processes with `jcmd` and JFR.
- Watch for memory leaks from unbounded caches, uncleaned `ThreadLocal`s, growing `static` collections, and forgotten listeners — analyze heap dumps to confirm.
- Always leave headroom between the container's memory limit and the JVM heap size.
