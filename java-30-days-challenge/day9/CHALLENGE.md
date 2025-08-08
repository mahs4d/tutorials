# Day 9: Spring Boot & Connection Pooling

| | |
|---|---|
| 🏗️ **Project** | **PoolLab** — a Spring Boot service that stress-tests a connection pool |
| ☕ **Java & language skills** | Spring Boot app structure, configuration (yml/properties), running concurrent clients |
| 🧰 **Library / tool** | Spring Boot (auto-config, starters, Actuator) + HikariCP |
| 🗄️ **DB / distributed-systems concept** | Database connection pooling & pool exhaustion |
| 📊 **Difficulty** | Medium |

---

## Concept primer: connection pooling

### The cost of a connection (why we pool at all)

On **Day 2** you saw that a hash index turns an O(n) scan into an O(1) lookup. Connection pooling is the same kind of win, but for a different bottleneck: the *setup cost of talking to the database*.

When your code calls `DriverManager.getConnection(url, user, pass)` against a real database like PostgreSQL, a surprising amount happens before a single query runs:

1. **TCP handshake** — a 3-way SYN/SYN-ACK/ACK round trip to the DB host.
2. **TLS handshake** (if encrypted) — several more round trips for certificate exchange and key agreement.
3. **Protocol startup + authentication** — the client sends a startup packet; the server may challenge with SCRAM/MD5; passwords are hashed and exchanged. More round trips.
4. **Server-side allocation** — Postgres *forks a backend process* per connection (~1–2 MB of RAM and OS scheduling overhead each). MySQL spawns a thread. This is real server work.

On a LAN this is easily **1–5 ms**; across availability zones or with TLS it can be **tens of milliseconds**. If every HTTP request opens and closes a connection, you pay that tax on *every single request*, and you hammer the DB with connection churn. Under load you can exhaust the DB's `max_connections` limit and take the whole database down — connections become the scarce resource, not CPU or disk.

A **connection pool** solves this by keeping a set of already-open, already-authenticated connections alive in memory. When your code needs a connection it *borrows* one from the pool (microseconds), uses it, and *returns* it. The expensive handshake happened once, at startup or pool growth, and is amortized over thousands of queries.

> Mental model: a pool is an object cache (Day 15 will generalize caching) where the cached objects are live TCP+auth sessions, and "eviction" is closing idle/aged connections.

### Pool sizing — and the "small pool" counter-intuition

The intuitive (wrong) instinct is "more connections = more throughput, so set the pool huge." In reality, **a small pool almost always outperforms a large one**, because a database is mostly bounded by a small number of physical resources:

- **CPU cores** — a query can only execute on a core; more in-flight queries than cores just means context-switch thrashing.
- **Disk spindles / IOPS** — random I/O does not parallelize past the device's capability.
- **Lock contention** — more concurrent transactions = more lock waits and (recall **Day 5, MVCC**) more version bookkeeping.

The widely-cited **PostgreSQL pool-sizing formula** (from the HikariCP wiki, derived from Postgres benchmarking) is:

```
connections = ((core_count * 2) + effective_spindle_count)
```

For an 8-core box with one SSD-backed volume, that's roughly `8*2 + 1 = 17`. People are shocked that a pool of ~10–20 can serve thousands of requests per second — but it can, because each query finishes in milliseconds and the connection is immediately handed to the next waiting thread. A pool of 200 against that same box would spend its time thrashing and queueing *inside the database* instead of *in the pool's borrow queue* where you can actually see and control it.

The key reframing: **a request waiting in the pool's borrow queue is cheap and observable; the same request fighting for a DB core is expensive and invisible.** Keep the contention in your app where you can measure it.

### HikariCP internals at a high level (and the "why")

HikariCP is the fastest mainstream JVM pool and Spring Boot's default. A few design points worth knowing as a senior engineer:

- **`ConcurrentBag`** — instead of a classic blocking queue with a single lock, Hikari uses a custom lock-light structure with thread-local lists. A thread that returns a connection prefers to hand it back to *itself* for its next borrow (thread affinity), avoiding cross-thread cache-line bouncing. *Why:* borrow/return is on the hot path of every query, so contention there must be near-zero.
- **No per-borrow validation by default** — instead of running `SELECT 1` on every checkout (slow), Hikari relies on JDBC4 `Connection.isValid()` and aggressive lifecycle management. *Why:* validation round trips defeat the purpose of pooling.
- **`maxLifetime`** — connections are retired and recreated after this duration (default 30 min), *staggered* so they don't all expire at once. *Why:* long-lived TCP connections rot — load balancers, firewalls, and DB-side timeouts silently kill them; proactively recycling avoids handing out a dead connection. Always set `maxLifetime` a few seconds *below* any DB/infra-side idle timeout.
- **`connectionTimeout`** — how long a thread will wait in the borrow queue before Hikari throws `SQLTransientConnectionException`. *Why:* fail fast. A hung request that waits forever for a connection is worse than a fast error you can retry (foreshadowing **Day 24, Resilience**).
- **`minimumIdle` vs `maximumPoolSize`** — Hikari recommends setting them *equal* (a fixed-size pool) for production. *Why:* a fixed pool avoids the latency spike of growing the pool (a fresh handshake) right when you're already under load. `minimumIdle < max` only makes sense for spiky, mostly-idle workloads where you want to release resources.

---

## Project: scaffold Boot, pool an H2 datasource, blow it up, then fix it

We'll build a minimal Spring Boot app with an H2 datasource backed by HikariCP, expose Actuator metrics, deliberately misconfigure the pool to size 1 with a short timeout, drive concurrent load to trigger exhaustion, then fix the config and watch the metrics recover.

### Prerequisites

- JDK 17+ (Spring Boot 3 requires 17). `java -version` to check.
- Maven 3.8+. (Day 1 set up your Maven muscle memory.)
- `curl` and a shell with `for`/background jobs (bash/zsh). Optional: `jq` to pretty-print metric JSON.

### Project layout (Spring Initializr style)

```
day9/
├── pom.xml
└── src
    └── main
        ├── java
        │   └── com/example/day9
        │       ├── Day9Application.java
        │       ├── PingController.java
        │       └── SlowQueryService.java
        └── resources
            └── application.yml
```

This is exactly what [start.spring.io](https://start.spring.io) would hand you if you selected Web, JDBC, Actuator, and H2 — we're just writing it by hand to demystify it.

---

## 🛠️ Project Walkthrough — PoolLab

Follow these steps hands-on, building and running the app as you go.

### Step 1 — `pom.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <!-- The Boot parent gives us curated, mutually-compatible dependency
         versions ("dependency management") and sensible plugin defaults.
         This is the first thing Boot automates: you stop hand-picking
         version numbers that fit together. -->
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.3.2</version>
        <relativePath/>
    </parent>

    <groupId>com.example</groupId>
    <artifactId>day9</artifactId>
    <version>0.0.1-SNAPSHOT</version>
    <packaging>jar</packaging>

    <properties>
        <java.version>17</java.version>
    </properties>

    <dependencies>
        <!-- "starter-web" pulls in Spring MVC + an embedded Tomcat.
             Note: NO version numbers here — the parent manages them. -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>

        <!-- "starter-jdbc" pulls in spring-jdbc AND HikariCP (the default
             pool). This is where the pool comes from. -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-jdbc</artifactId>
        </dependency>

        <!-- Actuator exposes /actuator/* endpoints incl. metrics, which is
             how we'll observe HikariCP at runtime. -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-actuator</artifactId>
        </dependency>

        <!-- H2: embedded in-memory DB so there's nothing to install. -->
        <dependency>
            <groupId>com.h2database</groupId>
            <artifactId>h2</artifactId>
            <scope>runtime</scope>
        </dependency>

        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <!-- Repackages the jar into a runnable "fat jar" and enables
                 `mvn spring-boot:run`. -->
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>
</project>
```

**What Boot just automated (vs. Day 8):** On Day 8 you wrote an `AnnotationConfigApplicationContext`, declared every `@Bean`, and wired dependencies manually. Here you wrote *zero* configuration classes and got: a running web server, a `DataSource` bean, a `HikariCP` pool, a `JdbcTemplate`-ready context, JSON serialization, and a metrics endpoint. That is **auto-configuration**: Boot inspects the classpath (`spring-boot-autoconfigure` ships ~150 `@Conditional` config classes) and, seeing H2 + spring-jdbc present, *conditionally* creates a `DataSource` for you — unless you define your own, in which case it backs off (`@ConditionalOnMissingBean`).

### Step 2 — `src/main/resources/application.yml`

This is **externalized configuration** — another Boot pillar. Instead of recompiling to change wiring, you tune behavior in a file (or env vars, or command-line args, which override the file).

```yaml
spring:
  datasource:
    url: jdbc:h2:mem:day9;DB_CLOSE_DELAY=-1
    driver-class-name: org.h2.Driver
    username: sa
    password: ""
    hikari:
      pool-name: day9-pool
      # --- DELIBERATELY BROKEN CONFIG (we will fix this in Step 7) ---
      maximum-pool-size: 1        # only ONE connection for the whole app
      minimum-idle: 1
      connection-timeout: 2000    # wait at most 2s for a connection, then fail
      max-lifetime: 600000        # 10 min (kept short for demo; staggered)

# Expose the actuator endpoints we need. By default Boot only exposes
# /actuator/health, so we explicitly opt-in to metrics.
management:
  endpoints:
    web:
      exposure:
        include: health,metrics,info
  endpoint:
    health:
      show-details: always

logging:
  level:
    com.zaxxer.hikari: DEBUG   # see pool stats logged periodically
```

### Step 3 — The main application class

```java
package com.example.day9;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * @SpringBootApplication is three annotations in one:
 *   - @Configuration        : this class can declare @Beans
 *   - @ComponentScan        : scan this package (com.example.day9) and below
 *                             for @Component/@Service/@RestController, etc.
 *   - @EnableAutoConfiguration : turn on the conditional auto-config engine
 *                             that wires the DataSource, Tomcat, Actuator...
 *
 * Contrast Day 8, where you manually built the context and scanned/registered
 * beans yourself. Here, main() hands control to Boot.
 */
@SpringBootApplication
public class Day9Application {
    public static void main(String[] args) {
        SpringApplication.run(Day9Application.class, args);
    }
}
```

### Step 4 — A service that holds a connection "too long"

To make exhaustion easy to trigger, our query deliberately sleeps *while holding a borrowed connection*. In real life this models a slow query, an external HTTP call made mid-transaction (an anti-pattern!), or a lock wait.

```java
package com.example.day9;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class SlowQueryService {

    private final JdbcTemplate jdbc;

    // Constructor injection — Boot auto-creates a JdbcTemplate bean because
    // spring-jdbc + a DataSource are on the classpath. No @Bean needed.
    public SlowQueryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * H2's emulated SLEEP via a tiny busy query won't truly block a
     * connection, so we sleep in Java *between* borrowing and releasing.
     * The JdbcTemplate borrows a connection from Hikari for the duration of
     * this callback, so the Thread.sleep keeps the connection checked out.
     */
    public long slowSelect(long sleepMillis) {
        return jdbc.execute((java.sql.Connection conn) -> {
            try {
                // We are now HOLDING a pooled connection.
                Thread.sleep(sleepMillis);
                try (var ps = conn.prepareStatement("SELECT 1");
                     var rs = ps.executeQuery()) {
                    rs.next();
                    return (long) rs.getInt(1);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("interrupted", e);
            }
        });
    }
}
```

### Step 5 — A tiny controller

```java
package com.example.day9;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class PingController {

    private final SlowQueryService service;

    public PingController(SlowQueryService service) {
        this.service = service;
    }

    /**
     * GET /work?ms=1000
     * Borrows a pooled connection and holds it for `ms` milliseconds.
     * Fire several of these concurrently to starve a size-1 pool.
     */
    @GetMapping("/work")
    public Map<String, Object> work(@RequestParam(defaultValue = "1000") long ms) {
        long start = System.currentTimeMillis();
        long result = service.slowSelect(ms);
        long elapsed = System.currentTimeMillis() - start;
        return Map.of(
                "result", result,
                "heldConnectionForMs", ms,
                "totalElapsedMs", elapsed,
                "thread", Thread.currentThread().getName()
        );
    }
}
```

### Step 6 — Run it and look at the baseline pool metrics

```bash
cd day9
mvn spring-boot:run
```

You should see Boot's banner, then logs like:

```
HikariPool-1 - Starting...
HikariPool-1 - Added connection conn0: url=jdbc:h2:mem:day9 ...
HikariPool-1 - Start completed.
Tomcat started on port 8080 (http) with context path '/'
Started Day9Application in 1.84 seconds
```

In another terminal, inspect the pool via Actuator:

```bash
# List available hikari metrics
curl -s localhost:8080/actuator/metrics | jq '.names[] | select(startswith("hikaricp"))'

# Current active (checked-out) connections
curl -s localhost:8080/actuator/metrics/hikaricp.connections.active | jq
# Threads currently BLOCKED waiting to borrow a connection
curl -s localhost:8080/actuator/metrics/hikaricp.connections.pending | jq
# Total connections in the pool (active + idle)
curl -s localhost:8080/actuator/metrics/hikaricp.connections | jq
# Max time a thread waited to acquire a connection
curl -s localhost:8080/actuator/metrics/hikaricp.connections.acquire | jq
```

The key metrics to internalize:

| Metric | Meaning |
| --- | --- |
| `hikaricp.connections` | total connections (your `maximumPoolSize` once warm) |
| `hikaricp.connections.active` | currently borrowed (in use) |
| `hikaricp.connections.idle` | available to borrow right now |
| `hikaricp.connections.pending` | **threads stuck in the borrow queue** — the exhaustion smoke alarm |
| `hikaricp.connections.acquire` | timer for how long borrowing takes |
| `hikaricp.connections.usage` | timer for how long connections are held |

### Step 7 — Reproduce pool exhaustion

With `maximum-pool-size: 1`, only one request can hold the single connection at a time. Fire **5 concurrent** requests that each hold the connection for 5 seconds:

```bash
for i in 1 2 3 4 5; do
  curl -s "localhost:8080/work?ms=5000" \
    -w "  [req $i] http=%{http_code} time=%{time_total}s\n" -o /dev/null &
done
wait
```

While those are in flight, in a third terminal hammer the metrics:

```bash
watch -n 0.3 'curl -s localhost:8080/actuator/metrics/hikaricp.connections.pending | jq ".measurements[0].value"'
```

**Expected output / what you'll observe:**

- **Request 1** grabs the only connection and succeeds after ~5s (`http=200`).
- Requests 2–5 wait in Hikari's borrow queue. With `connection-timeout: 2000`, after **2 seconds** they fail without ever getting a connection:

```
  [req 2] http=500 time=2.01s
  [req 3] http=500 time=2.01s
  [req 4] http=500 time=2.02s
  [req 5] http=500 time=2.03s
  [req 1] http=200 time=5.04s
```

- The app log shows the smoking gun:

```
java.sql.SQLTransientConnectionException: day9-pool - Connection is not available,
request timed out after 2001ms (total=1, active=1, idle=0, waiting=4)
```

- `hikaricp.connections.pending` spikes to **4** during the storm — that number going above 0 *at all* in production is your "the pool is too small / something is holding connections too long" alarm.

This is **connection-pool exhaustion**, one of the most common real-world outages: the pool is fine until a slow query, a missing index (Day 21), a downstream hang, or a leak (next section) causes connections to be held longer than expected. Borrow times climb, `pending` climbs, then requests start timing out with 500s — and from the *outside* it looks like "the database is down" even though the DB is healthy. The DB isn't down; **you ran out of the right to talk to it.**

### Step 8 — Fix it and watch recovery

Stop the app. Edit `application.yml` to a sane fixed-size pool:

```yaml
    hikari:
      pool-name: day9-pool
      maximum-pool-size: 10     # fixed pool (see sizing notes below)
      minimum-idle: 10          # == max: avoid growth latency under load
      connection-timeout: 30000 # 30s default; fail-fast but tolerant
      max-lifetime: 600000
```

Restart (`mvn spring-boot:run`) and re-run the 5-concurrent load from Step 7:

```
  [req 1] http=200 time=5.03s
  [req 2] http=200 time=5.03s
  [req 3] http=200 time=5.04s
  [req 4] http=200 time=5.04s
  [req 5] http=200 time=5.05s
```

Now all five run **in parallel** — total wall-clock ~5s instead of serialized — and `hikaricp.connections.pending` stays at **0** because there are enough connections for all five borrowers. `hikaricp.connections.active` peaks at 5 (out of 10), then settles back to 0 as connections are returned. You just diagnosed and fixed a pool-exhaustion incident with metrics, exactly as you would in production.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **The sizing formula in practice.** `((cores * 2) + spindles)` is a *starting point for the DB-facing pool*, not gospel. The real method is empirical: load-test, watch `hikaricp.connections.acquire` (borrow latency) and `pending`. If `pending` is ~0 and acquire time is sub-millisecond, your pool is big enough — making it bigger only moves contention into the DB. If `pending > 0` under normal load, either the pool is too small *or* connections are held too long (the latter is usually the real bug — fix the slow query, don't just grow the pool).
- **Why too-big pools hurt.** Beyond the DB-thrashing point above: every connection is a real backend process/thread on the server consuming RAM. 100 app instances × a 50-connection pool = 5000 connections — most databases fall over well before that. Pools multiply across instances; size for *aggregate* connections, not per-instance comfort.
- **PgBouncer / connection proxies (foreshadows Day 22, consistent hashing & Day 21, Postgres).** When you have many app instances, you put a connection proxy like **PgBouncer** in front of Postgres. In *transaction pooling* mode it multiplexes thousands of short-lived client connections onto a tiny set of real server connections, because most "connections" are idle between transactions. The app's Hikari pool then talks to PgBouncer (cheap) instead of Postgres directly (expensive). Caveat: transaction pooling breaks session-scoped features (prepared statements, `SET`, advisory locks — Day 28).
- **Connection leaks.** A leak is a borrowed connection that's never returned (a missing `try-with-resources`, an exception path that skips `close()`, a transaction that never commits/rolls back). Leaks look exactly like exhaustion but get *monotonically worse* over time — `active` climbs and never falls. Hikari's `leakDetectionThreshold` (e.g. `60000` ms) logs a stack trace of the borrower that's held a connection too long — invaluable for finding the offending code path. Using `JdbcTemplate`/`@Transactional` (Day 11) instead of raw JDBC is the best leak prevention because the framework owns close/return.
- **`maxLifetime` vs infra timeouts.** Always set `maxLifetime` a bit below the most aggressive idle timeout in the path (DB `idle_in_transaction_session_timeout`, cloud load-balancer idle timeout, NAT gateway timeout). Otherwise Hikari hands out a connection that the network already silently dropped, and you get mysterious intermittent `Connection reset` errors.
- **Validation/keepalive.** `keepaliveTimeout` makes Hikari periodically ping idle connections so firewalls don't reap them; cheaper than discovering deadness at borrow time.

### Stretch goals

1. **Find the leak.** Add `leakDetectionThreshold: 3000` to the Hikari config, then write a controller method that borrows a connection (`dataSource.getConnection()`) and "forgets" to close it. Hit it a few times and watch Hikari log the leak stack trace and the pool shrink to zero usable connections. Then fix it with try-with-resources and confirm `active` returns to 0.
2. **Auto-config archaeology.** Run the app with `--debug` (`mvn spring-boot:run -Dspring-boot.run.arguments=--debug`) and read the **auto-configuration report** ("Positive matches" / "Negative matches"). Find `DataSourceAutoConfiguration` and `HikariDataSource` in the matches — this is *proof* of what Boot conditionally wired for you, the thing you did by hand on Day 8.
3. **Override the auto-config.** Define your own `@Bean DataSource` (or a `@Bean HikariConfigMetaData`) and observe Boot *back off* (`@ConditionalOnMissingBean`) — your bean wins. This is the escape hatch that makes auto-config safe: it never fights you.
4. **Make a real metrics dashboard.** Add `micrometer-registry-prometheus`, expose `/actuator/prometheus`, and scrape `hikaricp_connections_pending` / `hikaricp_connections_acquire_seconds_max`. Set up an alert rule: `pending > 0 for 1m`. This is the exact production guardrail that catches pool exhaustion before users do (and previews **Day 25, Observability**).

### Day 10 teaser

Tomorrow (**Day 10: REST APIs & Idempotent HTTP**) we build out the controller layer properly — resource modeling, status codes, content negotiation — and revisit the **idempotency** ideas from **Day 7** at the HTTP boundary: safe vs. idempotent methods, `Idempotency-Key` headers, and how to make a `POST` retry-safe so that a client timing out (just like our exhausted-pool 500s today!) and retrying doesn't double-charge a customer. The pool you tuned today is the engine; tomorrow we design the API that sits on top of it.
