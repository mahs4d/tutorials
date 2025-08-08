# Day 23: Integration Testing with Testcontainers

| | |
|---|---|
| 🏗️ **Project** | **TestLab** — a Testcontainers-backed integration test suite |
| ☕ **Java & language skills** | JUnit5 integration tests, @SpringBootTest, MockMvc/TestRestTemplate, @Container lifecycle |
| 🧰 **Library / tool** | Testcontainers (+ Spring Boot Test, @ServiceConnection) |
| 🗄️ **DB / distributed-systems concept** | Hermetic test isolation against real dependencies (test pyramid) |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. The test pyramid for data-driven systems

You've written tests before — JUnit 5 on Day 2, AssertJ on Day 3. Those were **unit tests**: a single class, no Spring, no DB, microseconds to run. They are the foundation, and you should have hundreds of them. But a unit test of `OrderService` with a *mocked* `OrderRepository` can only prove that your code calls the methods you told it to call. It proves nothing about whether your SQL is valid, your JPA mapping is correct, your Flyway migration applies, or a unique constraint actually fires.

The classic **test pyramid**:

```
            /\
           /e2e\          few   — whole system, real network, slow, flaky-prone
          /------\
         / integ. \       some  — your app + REAL dependencies (DB, broker, cache)
        /----------\
       /   unit      \    many  — one class, no I/O, microseconds
      /----------------\
```

- **Unit (base, many):** pure logic. Fast, deterministic, no I/O. Mock collaborators. Days 2/3.
- **Integration (middle, some):** your code wired to *real* external systems — a real Postgres, a real Kafka, a real Redis — but still in-process and scoped to a slice of the app. This is the layer Testcontainers owns.
- **E2E (top, few):** the deployed system poked from the outside (HTTP in, side effects out), often across a network. Valuable but slow and brittle; keep them scarce.

The pyramid is a *budget*: fast tests are cheap so you write many; slow tests are expensive so you write few but make each count. The mistake juniors make is the **ice-cream cone** (lots of slow e2e, few units) — slow, flaky, and it tells you *that* something broke but not *where*. The mistake the "just mock everything" crowd makes is the opposite: a fat unit layer that's all green while the integration seams quietly rot.

### 2. Why H2 (and mocks) lie

On Day 12 you ran JPA against **H2 in-memory** so the project was self-contained. That was a deliberate convenience and it is fine for *unit-ish* slice tests of mapping basics. But H2 is **not** Postgres. It is a different database with a different SQL dialect, different type coercion, different concurrency model, different functions, and different DDL support. "Postgres compatibility mode" is a best-effort emulation, not a guarantee.

Concretely, things that pass on H2 and break on Postgres:

- **Type strictness.** Postgres won't silently compare a `text` to an `integer`; H2 is laxer.
- **DDL features.** `GENERATED ... AS IDENTITY` semantics, partial/`WHERE` indexes, `JSONB`, array types, `TIMESTAMPTZ`, exclusion constraints, `ON CONFLICT` (upsert) — Postgres-specific surface that H2 either rejects or fakes.
- **Functions & operators.** Regex operators (`~`), `string_agg`, `gen_random_uuid()`, `now()` semantics, `ON CONFLICT DO UPDATE`.
- **Constraint timing & error messages.** Your code that catches a unique-violation may match on the wrong SQLState/message when it ran against H2.
- **Flyway migrations themselves.** Your Day-13 migrations contained a `plpgsql` trigger function and Postgres regex. **H2 can't even run those.** If you test against H2, you can't test your real migrations at all — you test a *different* schema than production.

A mock lies even harder: it returns exactly what you programmed it to. A mock `OrderRepository.save()` that returns an `Order` with an `id` never touches a database, so it cannot catch a missing column, a bad cast, a constraint, or an N+1. **Mocks verify interactions; they cannot verify integration.** That's the whole point of the middle layer.

The senior framing: **test isolation is good, but isolating away the thing most likely to break is self-defeating.** The database is where your hardest bugs live. Test against the real one.

### 3. Hermetic tests

A test is **hermetic** if it carries everything it needs and depends on nothing ambient. No "first start Postgres on your laptop." No shared CI database that two builds clobber. No order dependence between tests. No leftover rows from yesterday.

The enemy of hermeticity is **shared mutable state**. A shared test database is a global variable: two pipelines hit it at once and corrupt each other; a failed run leaves dirt the next run trips over; a developer's local data differs from CI's. The result is the dreaded *flaky* test — red sometimes, green sometimes, trusted never.

Testcontainers makes tests hermetic by giving each test run its **own, disposable** database in a container, started from a pinned image, migrated from scratch, and **thrown away** at the end. The image tag (`postgres:16.4`) pins the exact engine version, so the test is reproducible on your laptop and on CI byte-for-byte. That reproducibility is the entire value proposition.

### 4. Testcontainers lifecycle & Ryuk

Testcontainers is a library that drives the Docker daemon from your JVM test process. The lifecycle:

1. **Pull** the image if not cached locally.
2. **Start** a container (random host port mapped to the container's service port — random ports are what make parallel/repeatable runs collision-free).
3. **Wait** until it's actually *ready* — not just "process started" but "accepting connections." Testcontainers' built-in `PostgreSQLContainer` uses a readiness `WaitStrategy` (a successful test query) so your test never races a half-booted DB. This solves the classic "container is up but the DB inside isn't listening yet" flake.
4. **Hand you** the dynamic JDBC URL / host / mapped port.
5. **Stop & remove** the container when done.

The cleanup guarantee comes from **Ryuk**, a tiny sidecar container Testcontainers starts automatically. Ryuk watches the test JVM. If your tests are **`kill -9`'d**, crash, or the IDE is force-quit — i.e. the normal JVM shutdown hooks never run — Ryuk still tears down every container/network/volume labeled by that session. This is why you don't end up with 40 orphaned Postgres containers after a week of flaky local runs. (On locked-down CI you sometimes disable Ryuk with `TESTCONTAINERS_RYUK_DISABLED=true` and rely on ephemeral runners to clean up — a tradeoff covered below.)

### 5. Isolation strategies between tests (and the *why*)

A real DB means tests can leave data behind and contaminate each other. Within a single container shared by many tests you need an isolation strategy. Three options, cheapest to most thorough:

| Strategy | How | Cost | When to use |
|---|---|---|---|
| **Transaction rollback** | wrap each test in a transaction and roll back at the end (Spring's `@Transactional` on the test does this automatically) | cheapest — no data ever commits | the default for repository/`@DataJpaTest` tests **where the code under test doesn't manage its own transactions** |
| **Truncate / clean** | after each test, `TRUNCATE` the touched tables (or re-run Flyway clean+migrate) | medium | when the code under test commits its own transactions, or you test across multiple connections |
| **Fresh container per class/test** | new container = pristine DB | most expensive (seconds of startup) | true isolation when nothing else is safe; rare because it's slow |

The crucial *why* on rollback: it's fast and clean, **but it's a lie in one important case.** If the thing you're testing opens its *own* transaction and commits (e.g. an outbox relay using `REQUIRES_NEW`, or you're testing the commit itself, or `SKIP LOCKED` across connections — Day 20), the test's enclosing rollback won't undo it, and rollback also hides bugs that only appear at *commit/flush* time. For those, truncate. The general rule: **rollback for read-shaped and single-transaction tests; truncate when commit semantics are part of what you're testing.**

A subtlety with shared containers: tests within a class run against the *same* DB instance, so non-rollback tests must clean up after themselves or be insensitive to order. Hermeticity is *per run*, not automatically *per test* — you choose how strong the per-test isolation is.

---

## Prerequisites & setup

### Docker
Testcontainers needs a working Docker daemon (Docker Desktop, Colima, Podman with the Docker API socket, or a remote `DOCKER_HOST`). Verify:

```bash
docker version          # client + server must both respond
docker run --rm hello-world
```

If `docker version` only shows the client, the daemon isn't reachable and every Testcontainers test will fail at startup with a clear "Could not find a valid Docker environment" message.

### Maven dependencies

Spring Boot's parent BOM manages Testcontainers versions, so you generally omit `<version>`. Spring Boot 3.1+ adds `spring-boot-testcontainers` which is what gives you `@ServiceConnection`.

```xml
<!-- pom.xml -->
<dependencies>
    <!-- ... your existing app deps: web, data-jpa, flyway-core,
             flyway-database-postgresql, postgresql driver ... -->

    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-test</artifactId>
        <scope>test</scope>
    </dependency>

    <!-- Spring Boot's Testcontainers support: @ServiceConnection, @ImportTestcontainers -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-testcontainers</artifactId>
        <scope>test</scope>
    </dependency>

    <!-- Testcontainers core + JUnit 5 extension + the Postgres module -->
    <dependency>
        <groupId>org.testcontainers</groupId>
        <artifactId>junit-jupiter</artifactId>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>org.testcontainers</groupId>
        <artifactId>postgresql</artifactId>
        <scope>test</scope>
    </dependency>

    <!-- optional, for the Kafka stretch goal -->
    <dependency>
        <groupId>org.testcontainers</groupId>
        <artifactId>kafka</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>
```

> If you're **not** on the Spring Boot parent BOM, import the Testcontainers BOM in `<dependencyManagement>`:
> `org.testcontainers:testcontainers-bom:<version>` with `<type>pom</type>` and `<scope>import</scope>`, then drop the versions on the modules above.

### The app under test

We continue the **orders app** (`com.example.shop.order`) from Days 10/12/20. As a reminder of its shape:

```java
// src/main/java/com/example/shop/order/Order.java  (from Day 20)
package com.example.shop.order;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "orders")
public class Order {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(nullable = false) private String customer;
    @Column(nullable = false) private BigDecimal amount;
    @Column(nullable = false) private String status;     // CREATED, PAID, CANCELLED
    @Column(nullable = false) private Instant createdAt;

    protected Order() { }
    public Order(String customer, BigDecimal amount) {
        this.customer = customer; this.amount = amount;
        this.status = "CREATED"; this.createdAt = Instant.now();
    }
    public Long getId() { return id; }
    public String getCustomer() { return customer; }
    public BigDecimal getAmount() { return amount; }
    public String getStatus() { return status; }
    public Instant getCreatedAt() { return createdAt; }
}
```

```java
// src/main/java/com/example/shop/order/OrderRepository.java
package com.example.shop.order;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface OrderRepository extends JpaRepository<Order, Long> {
    List<Order> findByCustomer(String customer);
}
```

```java
// src/main/java/com/example/shop/order/OrderController.java  (trimmed from Day 20)
package com.example.shop.order;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import java.math.BigDecimal;
import java.net.URI;

@RestController
@RequestMapping("/orders")
public class OrderController {
    private final OrderService service;
    public OrderController(OrderService service) { this.service = service; }

    public record CreateOrderRequest(String customer, BigDecimal amount) { }

    @PostMapping
    public ResponseEntity<Long> create(@RequestBody CreateOrderRequest req) {
        Order o = service.placeOrder(req.customer(), req.amount());
        return ResponseEntity.created(URI.create("/orders/" + o.getId())).body(o.getId());
    }

    @GetMapping("/{id}")
    public ResponseEntity<Order> get(@PathVariable Long id) {
        return service.find(id).map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.notFound().build());
    }
}
```

```java
// src/main/java/com/example/shop/order/OrderService.java  (trimmed)
package com.example.shop.order;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.math.BigDecimal;
import java.util.Optional;

@Service
public class OrderService {
    private final OrderRepository repo;
    public OrderService(OrderRepository repo) { this.repo = repo; }

    @Transactional
    public Order placeOrder(String customer, BigDecimal amount) {
        return repo.save(new Order(customer, amount));
    }

    @Transactional(readOnly = true)
    public Optional<Order> find(Long id) { return repo.findById(id); }
}
```

### The Flyway migration (Day 13), adapted for orders

Put this at `src/main/resources/db/migration/V1__init_orders.sql`. Note the **deliberately Postgres-specific** bits — they're what make H2 fall over and what make the test meaningful.

```sql
-- V1__init_orders.sql
CREATE TABLE orders (
    id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    customer    VARCHAR(200)  NOT NULL,
    amount      NUMERIC(12,2) NOT NULL CHECK (amount > 0),   -- CHECK constraint
    status      VARCHAR(20)   NOT NULL DEFAULT 'CREATED',
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()         -- TIMESTAMPTZ + now()
);

-- A *partial* unique index: at most one OPEN (non-cancelled) order per customer.
-- This WHERE-clause index is a Postgres feature; H2 does not support it.
CREATE UNIQUE INDEX uq_one_open_order_per_customer
    ON orders (customer)
    WHERE status <> 'CANCELLED';
```

> The `CHECK (amount > 0)`, `TIMESTAMPTZ`, and especially the **partial unique index** are intentional. They encode business rules in the schema (Day 13's "the engine enforces invariants" theme), and they are precisely the kind of DDL an H2-based test would never exercise.

---

## 🛠️ Project Walkthrough — TestLab

Roll up your sleeves: build each test below against a real Postgres container, then run the suite and read the log.

### Step 1 — A repository slice test against real Postgres (`@DataJpaTest` + `@ServiceConnection`)

`@DataJpaTest` boots *only* the JPA slice (repositories, `EntityManager`, a `DataSource`) — fast, focused. By default it tries to replace your datasource with an embedded H2 — **exactly the lie we're avoiding.** We override that with `@AutoConfigureTestDatabase(replace = NONE)` and point it at a real Postgres container wired by `@ServiceConnection`.

```java
// src/test/java/com/example/shop/order/OrderRepositoryTest.java
package com.example.shop.order;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.bean.override.mockito.MockitoBean; // not used here, shown for awareness
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.*; // AssertJ, from Day 3
import static org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase.Replace.NONE;

@Testcontainers                       // tells JUnit 5 to manage @Container fields
@DataJpaTest                          // JPA slice only
@AutoConfigureTestDatabase(replace = NONE)   // DO NOT swap in H2
class OrderRepositoryTest {

    @Container
    @ServiceConnection                // <-- Spring Boot 3.1+: auto-wires datasource from this container
    static PostgreSQLContainer<?> postgres =
            new PostgreSQLContainer<>("postgres:16.4");

    @Autowired OrderRepository repository;

    @Test
    void persists_and_finds_by_customer() {
        repository.save(new Order("alice", new BigDecimal("19.99")));
        repository.save(new Order("alice", new BigDecimal("5.00")));   // see Step 4 — this may violate the partial index
        repository.save(new Order("bob",   new BigDecimal("42.00")));

        List<Order> aliceOrders = repository.findByCustomer("alice");
        assertThat(aliceOrders).extracting(Order::getCustomer).containsOnly("alice");
        assertThat(repository.findByCustomer("bob")).hasSize(1);
    }
}
```

**What `@ServiceConnection` is doing.** Before Spring Boot 3.1 you had to bridge the container's *dynamic* JDBC URL into Spring's config by hand:

```java
// The OLD way (pre-3.1) — for understanding only; you don't need this anymore:
@DynamicPropertySource
static void props(DynamicPropertyRegistry r) {
    r.add("spring.datasource.url", postgres::getJdbcUrl);
    r.add("spring.datasource.username", postgres::getUsername);
    r.add("spring.datasource.password", postgres::getPassword);
}
```

`@ServiceConnection` replaces all of that. Spring Boot recognizes the `PostgreSQLContainer` type, reads its host/port/credentials *after it starts*, and registers a `JdbcConnectionDetails` bean so the datasource is configured automatically — zero properties. (`@DataJpaTest` is `static`-container friendly; the container starts before the Spring context, and `@ServiceConnection` reads its details at context-init time.) This is the headline Day-23 ergonomics win.

> **Important:** `@DataJpaTest` runs each test method inside a transaction that is **rolled back** at the end (Spring's default test isolation, §5 strategy #1). So the rows above never commit — the next test sees a clean table without any manual cleanup. Flyway still runs once to build the schema.

### Step 2 — Confirm Flyway ran against the container, not H2

`@DataJpaTest` keeps Flyway autoconfiguration on, so your **real V1 migration** runs against the real Postgres before any test. To *prove* it (and prove the Postgres-only DDL applied), query the Flyway history and the `pg_indexes` catalog — the partial index simply could not exist on H2.

```java
// add to OrderRepositoryTest
@Autowired javax.sql.DataSource dataSource;

@Test
void flyway_ran_postgres_specific_ddl() throws Exception {
    try (var conn = dataSource.getConnection(); var st = conn.createStatement()) {
        // 1. The Flyway history table exists and recorded V1.
        var hist = st.executeQuery(
            "SELECT version, success FROM flyway_schema_history WHERE version = '1'");
        assertThat(hist.next()).isTrue();
        assertThat(hist.getBoolean("success")).isTrue();

        // 2. The PARTIAL unique index physically exists (Postgres-only catalog + feature).
        var idx = st.executeQuery(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_one_open_order_per_customer'");
        assertThat(idx.next()).isTrue();
        assertThat(idx.getString("indexdef")).contains("WHERE").contains("CANCELLED");

        // 3. We really are talking to Postgres, not H2.
        var ver = st.executeQuery("SELECT version()");
        ver.next();
        assertThat(ver.getString(1)).contains("PostgreSQL");
    }
}
```

Expected: green. Run the same assertions against H2 and step 2/3 fail outright — `pg_indexes` doesn't exist and `version()` doesn't say "PostgreSQL." That contrast *is* the lesson.

### Step 3 — A full-stack API test (`@SpringBootTest` + `MockMvc`)

Now boot the *whole* app — controller, service, repository, real DB — and drive it through the HTTP layer. `@SpringBootTest` + `@AutoConfigureMockMvc` gives you `MockMvc`, which exercises the full Spring MVC stack (routing, JSON (de)serialization, validation, the controller) **without** opening a real socket.

```java
// src/test/java/com/example/shop/order/OrderApiTest.java
package com.example.shop.order;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import static org.hamcrest.Matchers.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@Testcontainers
@SpringBootTest                       // full application context
@AutoConfigureMockMvc                 // gives us MockMvc, no real port
class OrderApiTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4");

    @Autowired MockMvc mvc;

    @Test
    void create_then_fetch_order_end_to_end() throws Exception {
        // POST /orders -> 201 Created, body is the new id
        var location = mvc.perform(post("/orders")
                    .contentType("application/json")
                    .content("""
                        {"customer":"carol","amount":12.50}
                        """))
                .andExpect(status().isCreated())
                .andExpect(content().string(matchesPattern("\\d+")))
                .andReturn().getResponse().getHeader("Location");

        // GET /orders/{id} -> 200, persisted state is correct
        mvc.perform(get(location))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.customer").value("carol"))
                .andExpect(jsonPath("$.status").value("CREATED"))
                .andExpect(jsonPath("$.amount").value(12.50));
    }
}
```

This proves the *seams* a mock can't: JSON binding into `CreateOrderRequest`, the `@Transactional` commit in `OrderService`, the actual `INSERT` honoring the `NOT NULL`/`CHECK`/`DEFAULT` columns, the `IDENTITY` id generation, and the read-back through JPA — all against a real Postgres.

> **Isolation note:** unlike `@DataJpaTest`, a plain `@SpringBootTest` does **not** roll back by default (the test thread and the request handling may run in different transactions, and the service commits). So this test *commits* `carol`. If you add more tests to this class, clean up — e.g. a `@AfterEach` that `repository.deleteAll()` or a `@Sql`/`TRUNCATE` — or make assertions order-independent. This is §5 strategy #2 in action, and it's the honest cost of testing real commit behavior.

`MockMvc` vs `TestRestTemplate`: `MockMvc` is faster and stays in-process (no real HTTP), ideal for controller/serialization assertions. When you need a *real* socket — testing filters, real HTTP semantics, or an actual client — use `@SpringBootTest(webEnvironment = RANDOM_PORT)` with an injected `TestRestTemplate`:

```java
// alternative: real HTTP on a random port
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Testcontainers
class OrderRestTemplateTest {
    @Container @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4");
    @Autowired TestRestTemplate rest;

    @Test
    void createOverRealHttp() {
        var res = rest.postForEntity("/orders",
                new OrderController.CreateOrderRequest("dave", new java.math.BigDecimal("7.00")),
                Long.class);
        org.assertj.core.api.Assertions.assertThat(res.getStatusCode().value()).isEqualTo(201);
        org.assertj.core.api.Assertions.assertThat(res.getBody()).isNotNull();
    }
}
```

### Step 4 — The bug H2 would have hidden

Here's the payoff. Our schema says **"at most one non-cancelled order per customer"** via the partial unique index. Suppose a junior "fixes" a bug by saving a second open order for the same customer. On **H2** (which silently ignores the partial-index `WHERE` clause, or rejects the DDL and falls back to no constraint at all) the test passes — and the invariant is **silently broken in production**. On **real Postgres**, the constraint fires and the test catches it *before merge*.

```java
// src/test/java/com/example/shop/order/PartialIndexConstraintTest.java
package com.example.shop.order;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.dao.DataIntegrityViolationException;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.*;
import static org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase.Replace.NONE;

@Testcontainers
@DataJpaTest
@AutoConfigureTestDatabase(replace = NONE)
class PartialIndexConstraintTest {

    @Container @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4");

    @Autowired OrderRepository repository;

    @Test
    void second_open_order_for_same_customer_is_rejected_by_postgres() {
        repository.saveAndFlush(new Order("erin", new BigDecimal("10.00")));   // OK: first open order

        // A real Postgres rejects this; H2 would have let it through.
        assertThatThrownBy(() ->
                repository.saveAndFlush(new Order("erin", new BigDecimal("20.00"))))
            .isInstanceOf(DataIntegrityViolationException.class);
    }
}
```

Notes that make this a *real* lesson, not a toy:
- We use **`saveAndFlush`**, not `save`. JPA's persistence context (Day 12) defers the `INSERT` until flush; without flushing, the constraint violation wouldn't surface until commit — *after* the assertion. Forcing the flush makes the DB speak now. This itself is a subtlety H2 + auto-commit can mask.
- Spring translates the native Postgres `SQLException` (SQLState `23505`, unique violation) into its `DataIntegrityViolationException` — the *exception type your catch blocks key on* is only validated when you run against a database that actually throws it.
- This is the concrete answer to "why not H2": the bug is invisible on H2 because the constraint **doesn't exist there**. Testing against the real engine is the only way to know your schema invariants hold.

### Step 5 — How to run

```bash
mvn test
```

First run pulls `postgres:16.4` (and `testcontainers/ryuk`) — a one-time download. Subsequent runs reuse the cached images. Watch the log:

```
... org.testcontainers.dockerclient ... : Found Docker environment with ...
... 🐳 [postgres:16.4] : Creating container for image: postgres:16.4
... 🐳 [postgres:16.4] : Container postgres:16.4 started in PT3.214S
... o.f.core.internal.command.DbMigrate : Migrating schema "public" to version "1 - init orders"
... o.f.core.internal.command.DbMigrate : Successfully applied 1 migration ...
... o.s.t.web.servlet.TestDispatcherServlet : Completed initialization ...

[INFO] Tests run: 5, Failures: 0, Errors: 0, Skipped: 0
[INFO] BUILD SUCCESS
```

After the run, `docker ps` shows **no** leftover containers — Ryuk reaped them. (If a run is killed mid-flight, Ryuk still cleans up within seconds.)

To *feel* the H2 lie, temporarily delete `@AutoConfigureTestDatabase(replace = NONE)` from `PartialIndexConstraintTest` and add H2 as a test dependency: the test goes **green** (constraint silently absent) — a false pass. Restore the annotation and it correctly goes **red** on the duplicate. That diff is the entire argument for this day.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **The startup-cost problem.** Each `@Container static` field on a test class starts a *fresh* container per class (it's `static`, so once per class, not per method — already a win over instance fields). But across dozens of test classes that's dozens of Postgres boots × ~2–4s each = minutes of pure overhead. The fixes below all attack that.

- **Singleton container pattern.** Start the container **once per JVM** and share it across all test classes. You start it manually (no `@Container` annotation, so JUnit doesn't manage/stop it) in a static initializer of a base class; JVM exit + Ryuk handle cleanup. With Spring you expose it as a `@ServiceConnection` bean via a test config so every `@SpringBootTest` reuses the same DB:

  ```java
  // src/test/java/com/example/shop/TestcontainersConfiguration.java
  package com.example.shop;

  import org.springframework.boot.test.context.TestConfiguration;
  import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
  import org.springframework.context.annotation.Bean;
  import org.testcontainers.containers.PostgreSQLContainer;

  @TestConfiguration(proxyBeanMethods = false)
  class TestcontainersConfiguration {
      @Bean
      @ServiceConnection
      PostgreSQLContainer<?> postgresContainer() {
          // Spring's testcontainers support starts this bean once and reuses it.
          return new PostgreSQLContainer<>("postgres:16.4");
      }
  }
  ```

  ```java
  // any test that imports it shares the SAME container
  @SpringBootTest
  @org.springframework.context.annotation.Import(TestcontainersConfiguration.class)
  class SomeServiceTest { /* ... */ }
  ```

  Because Spring caches the application context across test classes (same config = same cached context = same container bean), this is the idiomatic Boot-3.1 way to get a singleton. Tradeoff: a shared DB means you must manage isolation yourself (truncate/rollback, §5) — you've traded startup cost for cleanup discipline.

- **Spinning containers up alongside `bootRun` for local dev.** The same `TestcontainersConfiguration` can power a local-dev launcher (`SpringApplication.from(App::main).with(TestcontainersConfiguration.class).run(args)`) so `./mvnw spring-boot:test-run` boots your app against a throwaway Postgres — no local DB install. Same machinery, dev-time payoff.

- **Container reuse (`withReuse(true)`).** Goes further than singleton-per-JVM: keep the container **alive across multiple test runs** (and IDE restarts) by opting in with `withReuse(true)` *and* `testcontainers.reuse.enable=true` in `~/.testcontainers.properties`. Reused containers are **not** Ryuk-reaped, so startup cost drops to ~zero on repeated local runs. Caveat: state persists between runs, so reuse demands rock-solid per-test cleanup, and you generally **do not enable reuse in CI** (you want pristine, ephemeral containers there).

- **Parallel tests.** Testcontainers is parallel-safe *because of random host ports* — two containers never collide. JUnit 5 parallelism (`junit.jupiter.execution.parallel.enabled=true`) can run classes concurrently. But parallel + a *shared* singleton DB reintroduces shared mutable state — so either run parallel with **fresh-per-class** containers (more memory/CPU), or keep the singleton and isolate via per-test schemas/transactions. There's a genuine resource-vs-isolation tradeoff here; on a beefy CI runner, parallel fresh containers can be *faster* end-to-end despite more startups.

- **Testcontainers in CI.** The runner needs Docker access — Docker-in-Docker, a mounted `/var/run/docker.sock`, or a remote `DOCKER_HOST`. Pin image tags (never `:latest`) for reproducibility. Pre-pull or cache images to cut cold-start time. On ephemeral runners you can set `TESTCONTAINERS_RYUK_DISABLED=true` (the runner is destroyed after the job, so the reaper is redundant), but **never** disable Ryuk on a long-lived/shared runner or you'll leak containers. GitHub Actions, GitLab CI, and CircleCI all support this; Testcontainers Cloud offloads the Docker work to a managed backend when local Docker is awkward.

- **`@DataJpaTest` H2 trap, restated.** The single most common Testcontainers mistake: forgetting `@AutoConfigureTestDatabase(replace = NONE)`, so `@DataJpaTest` *silently swaps your real datasource for H2* and you think you're testing Postgres while you're not. If a `@DataJpaTest` is suspiciously fast and your Postgres-only DDL "works," check this first.

- **This is the Day 13 CI gate, realized.** Day 13 ended by promising that Testcontainers would let CI run your real Flyway migrations against a throwaway Postgres so a bad migration fails the pipeline, not production. Step 2 *is* that gate. A broken `V7__...sql` now turns the build red.

- **Contract testing (the layer above).** Testcontainers verifies *your* service against *real infrastructure*. It does **not** verify that your service and a *separate* service agree on their HTTP/message contract. For that, reach for **consumer-driven contract testing** (Pact, or Spring Cloud Contract): the consumer publishes the requests/responses it expects, and the provider's build verifies it still satisfies them — catching breaking API changes without a full, flaky e2e environment. Testcontainers (real deps, one service) and contract tests (agreed interfaces, across services) are complementary slices just below the e2e tip of the pyramid.

- **Beyond Postgres.** The exact same pattern wraps the rest of your stack: `KafkaContainer` (Day 18 — and `@ServiceConnection` auto-wires `spring.kafka.bootstrap-servers`), `GenericContainer`/the Redis module (Days 15/16), Elasticsearch, LocalStack for AWS, even `DockerComposeContainer` to bring up a multi-service topology. Integration testing your *whole* messaging path (produce → consume) against real brokers is the natural next exercise.

### Stretch goals

1. **Kafka integration test.** Add the `org.testcontainers:kafka` dependency and a `@Container @ServiceConnection KafkaContainer` (`confluentinc/cp-kafka:7.6.1`). Re-introduce the Day-20 outbox relay, then write a test that POSTs an order and asserts an `OrderPlaced` message lands on the `orders` topic (consume with a test consumer, await with Awaitility). Verify `@ServiceConnection` auto-configured `spring.kafka.bootstrap-servers` with zero properties.

2. **Singleton + parallel.** Convert the three test classes above to share one container via `TestcontainersConfiguration` (singleton pattern), enable JUnit 5 parallel execution, and add per-test isolation (`@Transactional` where safe, `TRUNCATE orders RESTART IDENTITY` where not). Measure total `mvn test` time before/after and explain the tradeoff you made between startup cost and isolation.

3. **Make the H2 lie explicit, as a test.** Write two parameterized runs of `PartialIndexConstraintTest`: one with `replace = NONE` (real Postgres, expects the exception) and one forced onto H2 (expects *no* exception, documenting the false pass). Capturing the divergence in code is a powerful artifact for convincing a team to drop H2.

4. **Migration-only CI gate.** Add a `V2` migration that does something Postgres-specific and *wrong* (e.g. references a column that doesn't exist, or a `plpgsql` typo). Confirm a Testcontainers `@SpringBootTest` fails at context startup with the Flyway error — proving the build catches broken DDL. Then fix it. This is the Day-13 promise made executable.

### Day 24 teaser

Your tests now prove the app is correct when its dependencies are *healthy*. **Day 24 — Resilience** confronts the opposite: dependencies that are *slow, failing, or absent*. You'll add **Resilience4j** — circuit breakers, retries with backoff, bulkheads, rate limiters, timeouts, and fallbacks — so a flaky downstream degrades gracefully instead of cascading into a full outage. And because Testcontainers can simulate failure (pause a container, inject latency, use Toxiproxy), the *integration tests you just learned* become the way you **prove** your resilience patterns actually trip and recover.
