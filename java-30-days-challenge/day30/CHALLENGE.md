# Day 30: Capstone — A Distributed Order System & CAP

| | |
|---|---|
| 🏗️ **Project** | **OrderFlow** — an end-to-end distributed order-processing system |
| ☕ **Java & language skills** | Integrating a full Spring Boot system end-to-end, wiring many modules together |
| 🧰 **Library / tool** | Full stack — Spring Boot, Postgres/Flyway, Kafka, Redis, Resilience4j, Spring Security, Micrometer, Testcontainers |
| 🗄️ **DB / distributed-systems concept** | The CAP theorem & PACELC — consistency vs availability tradeoffs |
| 📊 **Difficulty** | Hard (2–4 hour capstone) |

---

## Concept primer: CAP, PACELC, and the consistency spectrum

### 1. What CAP actually says (and what it doesn't)

Eric Brewer's conjecture, proven by Gilbert & Lynch (2002), is about a **single piece of replicated data** under a **network partition**. Three properties:

- **C — Consistency** (here it means **linearizability**, the *strongest* model): every read sees the most recent write, as if there were a single copy. Note this is a *much* stronger "C" than the **C** in ACID, which only means "no broken invariants." Conflating the two is the single most common CAP misconception.
- **A — Availability**: every request to a *non-failing* node gets a non-error response (eventually, in bounded time).
- **P — Partition tolerance**: the system keeps operating even when the network drops/delays messages between nodes.

The theorem: **during a partition you can guarantee at most two of the three.** Since you cannot wish away partitions on a real network (cables get cut, GC pauses look like partitions, a switch reboots), **P is non-negotiable**. So CAP, honestly stated, is a forced binary *while a partition is happening*:

```
            Partition happens. A node on the minority side gets a write request.
                                       |
              +------------------------+------------------------+
              |                                                 |
         Answer it anyway                              Refuse / block until it
        (stay AVAILABLE)                               can reach a quorum
              |                                        (stay CONSISTENT)
       => may serve/accept STALE                  => returns errors / times out
          or conflicting data                        => UNAVAILABLE for that data
              |                                                 |
            AP system                                         CP system
   (Dynamo, Cassandra w/ low quorum,             (Postgres primary, ZooKeeper,
    Redis as cache, DNS, your read-model)         etcd, a quorum-write store)
```

"CA" — consistent *and* available with no partition tolerance — is not a system you can build on a network; it's a description of a single node, or an honest admission that you've *assumed partitions away*.

### 2. PACELC — the part you tune every day

CAP only talks about the partition case, which is rare. Abadi's **PACELC** extends it to normal operation:

> **if P** (partition): choose **A** or **C** — **E**lse (no partition): choose **L** (latency) or **C** (consistency).

This is the lens a senior engineer actually uses, because *the ELC trade-off is on every single request*. Examples in our system:

- A read served from the **Redis/Caffeine cache** is **EL** — sub-millisecond latency at the cost of possibly-stale data. A read that bypasses cache and hits the Postgres primary is **EC** — fresh, but slower and load-bearing on the DB.
- A Kafka producer with `acks=all` is **PC/EC** (wait for the ISR — consistent, higher latency); `acks=1` is **PA/EL** (faster, can lose the tail on failover).
- Postgres synchronous replication is **PC/EC**; asynchronous replicas are **PA/EL** (a read replica can lag).

Our whole architecture is one big PACELC decision: **the write path is CP** (you must not double-sell stock or lose an order), and **the read path is AP/EL** (a stale inventory count for 200ms is fine; a 500 error on a product page is not).

### 3. The consistency spectrum (strong → eventual)

"Consistency" is a dial, not a switch. Strongest to weakest:

| Model | Guarantee | In our system |
|---|---|---|
| **Linearizable** | Single-copy illusion; reads see latest write in real time | Postgres primary single-row read; a Redis distributed lock's lease |
| **Sequential** | All nodes see ops in *some* single order (not necessarily real-time) | Kafka *within a partition* (per-key total order) |
| **Causal** | Effects never observed before their causes | Event ordering when partitioned by `orderId` |
| **Read-your-writes / monotonic** | You see your own writes; reads don't go backwards | "After POST, my SSE stream shows my order" — engineered, not free |
| **Eventual** | If writes stop, all replicas converge — eventually | The inventory **read-model**, the read **cache** |

The capstone's defining tension: the **command side is strongly consistent** (linearizable per order row, in one ACID transaction) while the **query side is eventually consistent** (a projection that lags the log by however long the consumer takes). This is **CQRS**, and CQRS *is* a deliberate placement of the two sides at opposite ends of the spectrum — exactly the design CAP forces on you once you stop pretending one database can be everything to everyone.

---

## Architecture

Two Spring Boot services, three stateful backends, one Kafka log between them.

```
                                  ┌─────────────────────────────────────────────┐
   client ──JWT, Idempotency-Key─▶│             ORDER-SERVICE (command)          │
   (curl / WebFlux SSE client)    │                                              │
                                  │  [Security/JWT  Day26] [Bucket4j RL  Day27]  │
                                  │  [Bean Validation + MapStruct DTO  Day14]    │
                                  │  [Idempotency filter  Day7/10]               │
                                  │            │                                 │
                                  │            ▼   ONE @Transactional (Day17)    │
                                  │   ┌────────────────────────────────┐        │
                                  │   │  INSERT orders                 │        │
                                  │   │  INSERT outbox (OrderPlaced)   │  Day20 │
                                  │   └──────────────┬─────────────────┘        │
                                  │                  │ commit                    │
                                  │   ┌──────────────▼─────────────────┐        │
                                  │   │ Outbox relay @Scheduled        │  Day20 │
                                  │   │  ─► publish to Kafka, mark sent│        │
                                  │   └──────────────┬─────────────────┘        │
                                  └──────────────────┼──────────────────────────┘
                                                     │  acks=all (Day18)
   ┌──────────┐   ┌──────────┐                       ▼            ┌──────────────┐
   │ Postgres │◀──┤  orders  │             ┌───────────────────┐ │    Redis     │
   │ (orders) │   │  outbox  │             │  KAFKA topic      │ │ L2 cache,    │
   └──────────┘   └──────────┘             │  "order-events"   │ │ dist. lock   │
        ▲ system of record                 │  key=orderId      │ │ (Day16/28)   │
        │                                   │  partitioned log  │ └──────┬───────┘
        │                                   └─────────┬─────────┘        │
        │                                             │ @KafkaListener   │
        │                              ┌──────────────▼──────────────────▼────────┐
        │  (reconciliation             │        INVENTORY-SERVICE (query)         │
        │   reads SoR under            │                                          │
        │   a Redis lock)              │  [Idempotent consumer dedup  Day7]       │
        └──────────────────────────────│  [Event-sourced projection   Day19]      │
                                       │   reserve/decrement stock read-model     │
                                       │  [Caffeine L1 + Redis L2 cache  Day15/16] │
                                       │  [Resilience4j around pricing API Day24]  │
                                       │  [Reconcile job under Redis lock  Day28]  │
                                       └──────────────┬───────────────────────────┘
                                                      │ GET /inventory (AP, cached)
                                                      ▼
                                              query clients

  Both services ──Micrometer──▶ /actuator/prometheus ──scrape──▶ Prometheus (Day25)
```

### Request flow (write path — CP)

1. `POST /api/orders` with `Authorization: Bearer <JWT>` and an `Idempotency-Key` header.
2. Security filter validates the JWT (Day 26). Bucket4j checks the per-user token bucket (Day 27) → 429 if empty.
3. Idempotency filter (Day 7/10) checks Redis for the key; on a hit, replays the stored response — no double order.
4. Validation + MapStruct map the DTO → entity (Day 14).
5. In **one** `@Transactional` (Day 17): `INSERT` into `orders` **and** `INSERT` an `OrderPlaced` row into `outbox`. Commit makes both durable atomically — no dual-write (Day 20).
6. The `@Scheduled` outbox relay publishes unsent rows to Kafka with `acks=all`, keyed by `orderId` so a customer's events stay ordered in one partition (Day 18), then marks them `sent`.

### Event flow (read path — AP / eventual)

7. `inventory-service`'s `@KafkaListener` consumes `OrderPlaced`. It **dedups on `eventId`** (idempotent consumer, Day 7) — the relay is at-least-once, so duplicates are expected.
8. It applies the event to the **inventory read-model** (Day 19 event-sourcing flavor): decrement `available`, append to a per-product event log, advance the projection offset.
9. Reads (`GET /api/inventory/{sku}`) hit **Caffeine L1 → Redis L2 → Postgres** (Days 15/16). These are intentionally eventually consistent and **highly available**.
10. A **reconciliation job** periodically recomputes truth from the order system-of-record and corrects projection drift. Only **one** instance may run it cluster-wide → guarded by a **Redis distributed lock / leader election** (Day 28).
11. External pricing/tax calls are wrapped in a Resilience4j **circuit breaker + retry + bulkhead** (Day 24) so a slow dependency degrades gracefully instead of cascading.

---

## Prerequisites

A `docker-compose.yml` at the repo root bringing up the three backends (Days 16, 18, 23 infra):

```yaml
# day30/docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: orders
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d orders"]
      interval: 3s
      retries: 10

  redis:
    image: redis:7
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 3s
      retries: 10

  kafka:
    image: confluentinc/cp-kafka:7.6.0        # KRaft mode, no ZooKeeper
    ports: ["9092:9092"]
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:29093
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:29093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      CLUSTER_ID: "MkU3OEVBNTcwNTJENDM2Qk"
```

```bash
cd day30
docker compose up -d
# wait for healthy, then run the services (build steps below)
```

The `pom.xml` pulls together every starter from the journey:

```xml
<dependencies>
  <!-- web, security, validation, data-jpa, actuator, kafka, redis -->
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-webflux</artifactId></dependency>            <!-- Day29 SSE -->
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-security</artifactId></dependency>           <!-- Day26 -->
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-validation</artifactId></dependency>         <!-- Day14 -->
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>           <!-- Day12 -->
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-actuator</artifactId></dependency>           <!-- Day25 -->
  <dependency><groupId>org.springframework.kafka</groupId><artifactId>spring-kafka</artifactId></dependency>                          <!-- Day18 -->
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-redis</artifactId></dependency>         <!-- Day16 -->
  <dependency><groupId>org.flywaydb</groupId><artifactId>flyway-core</artifactId></dependency>                                        <!-- Day13 -->
  <dependency><groupId>org.flywaydb</groupId><artifactId>flyway-database-postgresql</artifactId></dependency>
  <dependency><groupId>org.postgresql</groupId><artifactId>postgresql</artifactId><scope>runtime</scope></dependency>
  <dependency><groupId>com.github.ben-manes.caffeine</groupId><artifactId>caffeine</artifactId></dependency>                          <!-- Day15 -->
  <dependency><groupId>io.github.resilience4j</groupId><artifactId>resilience4j-spring-boot3</artifactId><version>2.2.0</version></dependency> <!-- Day24 -->
  <dependency><groupId>com.bucket4j</groupId><artifactId>bucket4j-core</artifactId><version>8.10.1</version></dependency>             <!-- Day27 -->
  <dependency><groupId>io.jsonwebtoken</groupId><artifactId>jjwt-api</artifactId><version>0.12.6</version></dependency>               <!-- Day26 -->
  <dependency><groupId>io.micrometer</groupId><artifactId>micrometer-registry-prometheus</artifactId></dependency>                    <!-- Day25 -->
  <dependency><groupId>org.mapstruct</groupId><artifactId>mapstruct</artifactId><version>1.6.2</version></dependency>                 <!-- Day14 -->
  <dependency><groupId>org.projectlombok</groupId><artifactId>lombok</artifactId><optional>true</optional></dependency>              <!-- Day5 -->
  <!-- tests -->
  <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency>
  <dependency><groupId>org.testcontainers</groupId><artifactId>junit-jupiter</artifactId><scope>test</scope></dependency>            <!-- Day23 -->
  <dependency><groupId>org.testcontainers</groupId><artifactId>postgresql</artifactId><scope>test</scope></dependency>
  <dependency><groupId>org.testcontainers</groupId><artifactId>kafka</artifactId><scope>test</scope></dependency>
</dependencies>
```

---

## 🛠️ Project Walkthrough — OrderFlow

Assemble the system end to end here.

## Build steps — assembling the journey

We keep both services in one module for the capstone (split into two Gradle/Maven modules as an extension). Each step says *which day it comes from* and shows the **integration glue**, not a re-teach of the original.

### Step 1 — Flyway schema: orders, outbox, inventory (Days 13, 21)

`src/main/resources/db/migration/V1__init.sql`:

```sql
CREATE TABLE orders (
    id              UUID PRIMARY KEY,
    customer_id     UUID        NOT NULL,
    sku             VARCHAR(64) NOT NULL,
    quantity        INT         NOT NULL CHECK (quantity > 0),
    status          VARCHAR(24) NOT NULL,          -- PLACED, CONFIRMED, REJECTED
    idempotency_key VARCHAR(80) UNIQUE,            -- write-path dedup (Day 7/10)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_orders_customer ON orders (customer_id, created_at DESC);  -- Day21: supports "my orders" page

-- Transactional outbox (Day 20)
CREATE TABLE outbox (
    id           UUID PRIMARY KEY,
    aggregate_id UUID         NOT NULL,            -- = orderId, used as the Kafka key
    type         VARCHAR(64)  NOT NULL,
    payload      JSONB        NOT NULL,            -- Jackson-serialized event (Day 6)
    sent         BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_outbox_unsent ON outbox (created_at) WHERE sent = FALSE;    -- Day21: partial index, the relay's hot query

-- Inventory read-model / projection (Day 19)
CREATE TABLE inventory (
    sku        VARCHAR(64) PRIMARY KEY,
    available  INT         NOT NULL,
    version    BIGINT      NOT NULL DEFAULT 0      -- last applied projection offset
);

-- Consumer dedup table — idempotent consumer (Day 7)
CREATE TABLE processed_events (
    event_id   UUID PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`application.yml` (excerpt) ties Flyway, JPA, Kafka, Redis, and Actuator together:

```yaml
spring:
  datasource: { url: jdbc:postgresql://localhost:5432/orders, username: app, password: app }
  jpa: { hibernate.ddl-auto: validate, open-in-view: false }   # validate, never auto-ddl — Flyway owns schema (Day13)
  flyway: { enabled: true }
  kafka:
    bootstrap-servers: localhost:9092
    producer: { acks: all, properties.enable.idempotence: true }     # Day18: no duplicate appends on producer retry
    consumer: { group-id: inventory, auto-offset-reset: earliest, enable-auto-commit: false }
    listener: { ack-mode: manual }                                   # we commit offset only after the projection applies
  data.redis: { host: localhost, port: 6379 }
management:
  endpoints.web.exposure.include: [health, prometheus, metrics]      # Day25
  metrics.tags.application: ${spring.application.name}
```

### Step 2 — Entities + records (Days 5, 12)

```java
// Order.java — JPA entity (Day 12), Lombok for boilerplate (Day 5)
@Entity @Table(name = "orders")
@Getter @Setter @NoArgsConstructor
public class Order {
    @Id private UUID id;
    private UUID customerId;
    private String sku;
    private int quantity;
    @Enumerated(EnumType.STRING) private OrderStatus status;
    private String idempotencyKey;
    private Instant createdAt;
}

// OutboxEntry.java (Day 20)
@Entity @Table(name = "outbox")
@Getter @Setter @NoArgsConstructor
public class OutboxEntry {
    @Id private UUID id;
    private UUID aggregateId;
    private String type;
    @JdbcTypeCode(SqlTypes.JSON) private String payload;   // JSONB column
    private boolean sent;
    private Instant createdAt;
}

// The event itself — an immutable record (Day 5), serialized with Jackson (Day 6)
public record OrderPlaced(UUID eventId, UUID orderId, UUID customerId,
                          String sku, int quantity, Instant occurredAt) {}
```

### Step 3 — The atomic write + outbox (Days 17, 20) — the CP heart

```java
@Service
@RequiredArgsConstructor
public class OrderCommandService {
    private final OrderRepository orders;
    private final OutboxRepository outbox;
    private final ObjectMapper json;                       // Day 6
    private final MeterRegistry meters;                    // Day 25

    /** ONE local transaction: business row + event row commit atomically. No dual write (Day 20). */
    @Transactional                                         // Day 17
    public Order place(UUID customerId, String sku, int qty, String idemKey) {
        Order o = new Order();
        o.setId(UUID.randomUUID());
        o.setCustomerId(customerId);
        o.setSku(sku); o.setQuantity(qty);
        o.setStatus(OrderStatus.PLACED);
        o.setIdempotencyKey(idemKey);
        o.setCreatedAt(Instant.now());
        orders.save(o);                                    // INSERT orders

        var event = new OrderPlaced(UUID.randomUUID(), o.getId(), customerId, sku, qty, o.getCreatedAt());
        OutboxEntry entry = new OutboxEntry();
        entry.setId(UUID.randomUUID());
        entry.setAggregateId(o.getId());                   // Kafka partition key => per-order ordering (Day 18)
        entry.setType("OrderPlaced");
        entry.setPayload(writeJson(event));
        entry.setCreatedAt(Instant.now());
        outbox.save(entry);                                // INSERT outbox — same TX, commits together

        meters.counter("orders.placed").increment();       // Day 25
        return o;
    }

    private String writeJson(Object o) {
        try { return json.writeValueAsString(o); }
        catch (JsonProcessingException e) { throw new IllegalStateException(e); }
    }
}
```

The relay (Day 20) — at-least-once publisher, runs on a schedule:

```java
@Component
@RequiredArgsConstructor
public class OutboxRelay {
    private final OutboxRepository outbox;
    private final KafkaTemplate<String, String> kafka;     // Day 18

    @Scheduled(fixedDelay = 500)
    @Transactional
    public void publish() {
        for (OutboxEntry e : outbox.findTop100BySentFalseOrderByCreatedAt()) {
            // key = aggregateId => all events for one order land in one partition, stay ordered
            kafka.send("order-events", e.getAggregateId().toString(), e.getPayload());
            e.setSent(true);                                // marked sent in this TX; relay is at-least-once
        }
    }
}
```

> **Why this is at-least-once, not exactly-once:** the broker can ack a send that the relay never records as `sent` (crash between send and commit), so it republishes on restart. That's *fine* — the consumer dedups (Step 4). Pairing an at-least-once relay with an idempotent consumer gives *effectively-exactly-once* end to end (Day 20's punchline).

### Step 4 — Idempotent consumer + event-sourced projection (Days 7, 19)

```java
@Component
@RequiredArgsConstructor
public class InventoryProjector {
    private final InventoryRepository inventory;
    private final ProcessedEventRepository processed;       // dedup table (Day 7)
    private final ObjectMapper json;
    private final CacheManager cache;                       // evict on apply (Day 15/16)

    @KafkaListener(topics = "order-events", groupId = "inventory")
    @Transactional                                          // projection + offset + dedup commit atomically
    public void on(String payload, Acknowledgment ack) throws Exception {
        OrderPlaced ev = json.readValue(payload, OrderPlaced.class);

        // Idempotent consumer (Day 7): the relay is at-least-once, so duplicates WILL arrive.
        if (processed.existsById(ev.eventId())) { ack.acknowledge(); return; }

        // Apply the event to the read-model (Day 19: state = fold over events)
        Inventory inv = inventory.findById(ev.sku())
                .orElseGet(() -> inventory.save(new Inventory(ev.sku(), 0, 0)));
        inv.setAvailable(inv.getAvailable() - ev.quantity());
        inventory.save(inv);

        processed.save(new ProcessedEvent(ev.eventId()));   // mark applied — same TX
        cache.getCache("inventory").evictIfPresent(ev.sku());// keep cache from serving the now-stale count too long
        ack.acknowledge();                                  // commit offset only after apply (manual ack)
    }
}
```

> **The consistency seam lives here.** Between the order's `COMMIT` (Step 3) and this `ack` there is a window where the order *exists* but inventory has *not* been decremented. That window is the **eventual consistency** of the read side — bounded by relay delay + Kafka latency + consumer lag, typically sub-second, but *non-zero by construction*. CAP says you cannot make it zero without making the write path synchronously depend on the read side (and thereby coupling their availability).

### Step 5 — Security, rate limit, idempotency, validation (Days 7, 10, 14, 26, 27)

The controller composes four cross-cutting concerns:

```java
@RestController
@RequestMapping("/api/orders")
@RequiredArgsConstructor
public class OrderController {
    private final OrderCommandService commands;
    private final IdempotencyStore idem;                    // Redis-backed (Day 7/10/16)
    private final OrderMapper mapper;                        // MapStruct (Day 14)

    @PostMapping
    public ResponseEntity<OrderResponse> place(
            @AuthenticationPrincipal Jwt principal,         // Day 26 — identity from validated JWT
            @RequestHeader("Idempotency-Key") String key,   // Day 7/10
            @Valid @RequestBody CreateOrderRequest req) {    // Day 14 — Bean Validation

        // Replay-protect: if we've answered this key, return the stored response verbatim.
        var cached = idem.find(key);
        if (cached.isPresent()) return ResponseEntity.status(200).body(cached.get());

        UUID customerId = UUID.fromString(principal.getSubject());
        Order o = commands.place(customerId, req.sku(), req.quantity(), key);
        OrderResponse resp = mapper.toResponse(o);
        idem.save(key, resp, Duration.ofHours(24));
        return ResponseEntity.status(201).body(resp);
    }
}
```

Rate limiting as a filter (Day 27, Bucket4j), keyed per authenticated user:

```java
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
@RequiredArgsConstructor
public class RateLimitFilter extends OncePerRequestFilter {
    private final Map<String, Bucket> buckets = new ConcurrentHashMap<>();

    @Override protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String user = req.getUserPrincipal() == null ? req.getRemoteAddr() : req.getUserPrincipal().getName();
        Bucket bucket = buckets.computeIfAbsent(user, u ->
            Bucket.builder().addLimit(l -> l.capacity(20).refillGreedy(20, Duration.ofMinutes(1))).build());
        if (bucket.tryConsume(1)) { chain.doFilter(req, res); }
        else { res.setStatus(429); res.getWriter().write("rate limit exceeded"); }   // shed load — an AP choice
    }
}
```

Security config (Day 26) — stateless resource server validating JWTs:

```java
@Configuration @EnableWebSecurity
public class SecurityConfig {
    @Bean SecurityFilterChain chain(HttpSecurity http) throws Exception {
        return http
            .csrf(csrf -> csrf.disable())
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(a -> a
                .requestMatchers("/actuator/**", "/api/inventory/**").permitAll()   // reads are public/cached
                .anyRequest().authenticated())
            .oauth2ResourceServer(o -> o.jwt(Customizer.withDefaults()))             // HS256/RS256 from Day 26
            .build();
    }
}
```

### Step 6 — Read cache: Caffeine L1 + Redis L2 (Days 15, 16)

```java
@Service
@RequiredArgsConstructor
public class InventoryQueryService {
    private final InventoryRepository repo;

    @Cacheable(cacheNames = "inventory", key = "#sku")     // Caffeine near-cache (Day 15) backed by Redis (Day 16)
    public InventoryView get(String sku) {
        return repo.findById(sku)
                   .map(i -> new InventoryView(i.getSku(), i.getAvailable()))
                   .orElseThrow();
    }
}
```

> This read is deliberately **AP / eventually consistent**: it may serve a count that's a few hundred ms stale. That is the *correct* trade-off for a product-listing read — availability and latency beat freshness. The cache is invalidated by the projector (Step 4) on each apply, bounding staleness.

### Step 7 — Singleton reconciliation under a Redis distributed lock (Day 28)

The projection can drift (a dropped message, a bug, a manual DB edit). A reconciliation job recomputes inventory from the order system-of-record. It must run on **exactly one** node — leader election via a Redis lock (Day 28):

```java
@Component
@RequiredArgsConstructor
public class ReconciliationJob {
    private final StringRedisTemplate redis;                // Day 16
    private final ReconciliationService svc;

    @Scheduled(fixedDelay = 60_000)
    public void run() {
        String token = UUID.randomUUID().toString();
        // SET key token NX PX 55000 — atomic acquire-with-lease (Day 28). NX = only if absent.
        Boolean acquired = redis.opsForValue()
            .setIfAbsent("lock:reconcile", token, Duration.ofSeconds(55));
        if (!Boolean.TRUE.equals(acquired)) return;          // someone else is leader this round
        try {
            svc.reconcile();                                 // recompute projection from SoR — the CP truth
        } finally {
            // release only if we still own it (check-and-delete via Lua to avoid releasing someone else's lock)
            redis.execute(RELEASE_IF_OWNER, List.of("lock:reconcile"), token);
        }
    }

    private static final RedisScript<Long> RELEASE_IF_OWNER = new DefaultRedisScript<>(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end", Long.class);
}
```

> **CAP caveat to state out loud (Day 28's hard lesson):** a single-node Redis lock is **not** a correctness-safe distributed lock under partition — a GC pause longer than the lease, or a Redis failover, can let two nodes believe they're leader. For a *self-healing* reconcile job that's tolerable (idempotent recompute). If you needed strict mutual exclusion you'd reach for a fencing token + a CP coordinator (etcd/ZooKeeper). Naming this trade-off explicitly is exactly the senior move.

### Step 8 — Resilience around external calls (Day 24)

```java
@Service
@RequiredArgsConstructor
public class PricingClient {
    private final RestClient http;

    @CircuitBreaker(name = "pricing", fallbackMethod = "cachedPrice")  // Day 24
    @Retry(name = "pricing")
    @Bulkhead(name = "pricing")
    public Money price(String sku) {
        return http.get().uri("https://pricing.internal/price/{sku}", sku)
                   .retrieve().body(Money.class);
    }

    // Fallback: degrade gracefully (AP) instead of failing the whole request when pricing is down.
    private Money cachedPrice(String sku, Throwable t) { return Money.lastKnownOrDefault(sku); }
}
```

### Step 9 — Metrics wiring (Day 25)

Actuator + the Prometheus registry are already on the classpath (Step 1). Micrometer auto-instruments HTTP, JDBC pool (HikariCP, Day 9), Kafka, and cache. Add domain metrics and a **consumer-lag-flavored gauge** for the projection — the single most important health signal of an eventually-consistent system:

```java
@Component
@RequiredArgsConstructor
public class ProjectionMetrics {
    private final OutboxRepository outbox;
    private final MeterRegistry meters;

    @PostConstruct void bind() {
        // How many events are written but not yet relayed? = how far the read side lags the write side.
        meters.gauge("outbox.unsent", outbox, OutboxRepository::countBySentFalse);
    }
}
```

Scrape with Prometheus at `GET /actuator/prometheus` on both services. Alert when `outbox_unsent` or Kafka consumer lag grows — that's your eventual-consistency window widening.

### Step 10 — A reactive order-status stream (Day 29)

A WebFlux SSE endpoint lets a client watch its order move PLACED → CONFIRMED, *feeling* the read-side lag directly:

```java
@GetMapping(value = "/api/orders/{id}/status", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
public Flux<OrderStatus> stream(@PathVariable UUID id) {        // Day 29 — backpressure-aware
    return Flux.interval(Duration.ofMillis(500))
               .flatMap(t -> Mono.fromCallable(() -> orders.statusOf(id)).subscribeOn(Schedulers.boundedElastic()))
               .distinctUntilChanged()
               .takeUntil(s -> s == OrderStatus.CONFIRMED || s == OrderStatus.REJECTED);
}
```

---

## End-to-end test with Testcontainers (Day 23)

One test boots all three backends and drives the *entire* pipeline: place an order → assert the outbox relay published → assert the consumer projected → assert the cached read reflects it (eventually).

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Testcontainers
class OrderPipelineEndToEndTest {

    @Container static PostgreSQLContainer<?> pg = new PostgreSQLContainer<>("postgres:16");
    @Container static KafkaContainer kafka = new KafkaContainer(DockerImageName.parse("confluentinc/cp-kafka:7.6.0"));
    @Container static GenericContainer<?> redis = new GenericContainer<>("redis:7").withExposedPorts(6379);

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {                       // Day 23 — wire container ports into Spring
        r.add("spring.datasource.url", pg::getJdbcUrl);
        r.add("spring.datasource.username", pg::getUsername);
        r.add("spring.datasource.password", pg::getPassword);
        r.add("spring.kafka.bootstrap-servers", kafka::getBootstrapServers);
        r.add("spring.data.redis.host", redis::getHost);
        r.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
    }

    @Autowired TestRestTemplate http;
    @Autowired InventoryRepository inventory;

    @Test
    void placingAnOrder_eventuallyDecrementsTheInventoryProjection() {
        // given a starting stock level (seeded via Flyway/repo)
        inventory.save(new Inventory("SKU-1", 100, 0));

        // when: a secured, idempotent POST (write path — CP, returns immediately on commit)
        var headers = new HttpHeaders();
        headers.setBearerAuth(TestTokens.forCustomer("11111111-1111-1111-1111-111111111111"));
        headers.set("Idempotency-Key", "key-abc");
        var body = new CreateOrderRequest("SKU-1", 3);
        var resp = http.postForEntity("/api/orders", new HttpEntity<>(body, headers), OrderResponse.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.CREATED);

        // then: the read-model converges (eventual consistency — we must AWAIT, not assert immediately)
        await().atMost(Duration.ofSeconds(10)).untilAsserted(() ->
            assertThat(inventory.findById("SKU-1")).get()
                .extracting(Inventory::getAvailable).isEqualTo(97));         // 100 - 3

        // and: idempotency holds — the same key does NOT place a second order
        var replay = http.postForEntity("/api/orders", new HttpEntity<>(body, headers), OrderResponse.class);
        assertThat(replay.getBody().id()).isEqualTo(resp.getBody().id());     // same order, no double-decrement
        assertThat(inventory.findById("SKU-1")).get()
            .extracting(Inventory::getAvailable).isEqualTo(97);              // still 97, not 94
    }
}
```

> Notice the test design *is* the CAP lesson: the write-side assertion is **immediate** (linearizable — the commit returned), but the read-side assertion is wrapped in **`await()`** because the projection is **eventually** consistent. If you ever write an end-to-end CQRS test that asserts the read side synchronously, it's flaky by construction — and that flakiness is CAP telling you the truth about your system.

---

## How to run the whole thing

```bash
cd day30
docker compose up -d                      # Postgres + Kafka + Redis (wait until healthy)
./mvnw spring-boot:run                     # boots order-service + inventory-service + relay + consumer

# 1. Get a token (dev login endpoint, or mint one — Day 26)
TOKEN=$(curl -s -XPOST localhost:8080/auth/login -d '{"user":"alice"}' -H 'Content-Type: application/json' | jq -r .token)

# 2. Place an order (idempotent, validated, rate-limited)
curl -XPOST localhost:8080/api/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"sku":"SKU-1","quantity":3}'
# => 201 { "id": "...", "status": "PLACED" }

# 3. Replay the SAME idempotency key => same order, no double order
#    (repeat the exact curl above with the same key) => 200, identical body

# 4. Read inventory (cached, eventually consistent)
curl localhost:8080/api/inventory/SKU-1     # => { "sku":"SKU-1", "available": 97 } after the projection catches up

# 5. Watch metrics
curl -s localhost:8080/actuator/prometheus | grep -E 'orders_placed|outbox_unsent|resilience4j'
```

**Expected end-to-end behavior**
- The POST returns `201` the instant the **local transaction commits** — it does *not* wait for Kafka or inventory. (Write path is fast and CP for the order row.)
- Within ~1 second, `GET /api/inventory/SKU-1` reflects the decrement. (Read path is eventual.)
- Replaying an idempotency key never double-counts.
- Exceeding 20 orders/min for a user returns `429`. (Load shedding — AP.)
- Killing the pricing dependency: orders still succeed, the circuit breaker opens, the fallback price is used, and `resilience4j_circuitbreaker_state` flips to `open` in Prometheus.
- Kill `inventory-service`, place 5 orders, restart it: it **catches up from the Kafka log** (replay) and the projection converges to the correct count — events were never lost (the log + outbox guaranteed delivery; the dedup table prevented double-apply).

---

## 🚀 CAP Analysis, Reflection & Where to Go Next

## CAP analysis & senior-level reflection

Now the payoff: place each component on the CP/AP map and justify the trade-off. This is the section a staff engineer will probe in a design review.

### Per-component placement

| Component | CAP stance | Consistency model | Why this choice |
|---|---|---|---|
| **Postgres `orders` (system of record)** | **CP** | Linearizable per row (under ACID, Day 11) | The truth of "did this order happen / was payment taken" must never fork. Under partition, the primary refuses writes rather than accept conflicting ones. We accept unavailability of *writes* over an inconsistent ledger. |
| **The single ACID write + outbox** | **CP** | Serializable-ish, single TX | Atomicity of business state + intent-to-publish. Avoids 2PC (Day 17) by keeping it one local transaction. |
| **Kafka log (`acks=all`)** | **CP within a partition** | Sequential per key | Per-`orderId` total order and no lost tail on failover. We pay latency (PC/EC) because event order *is* correctness for the projection. |
| **Outbox relay** | leans **AP** | at-least-once | Prioritizes *delivery* (availability of the event stream) over exactly-once; correctness recovered downstream by the idempotent consumer. |
| **Inventory projection / read-model** | **AP** | Eventual (read-your-writes only after lag) | A query service must stay *up and fast* even while the write side is busy or partitioned. A stale count for sub-second is acceptable; an outage of the catalog is not. This is the CQRS split made concrete. |
| **Caffeine + Redis read cache** | **AP / EL** | Eventual (bounded staleness) | The textbook PACELC-**EL** choice: trade freshness for latency on the hot read path. Invalidated on apply to bound staleness. |
| **Redis distributed lock (reconcile)** | **AP-flavored, *not* strictly safe** | Leased mutual exclusion, best-effort | Single-node Redis can't guarantee exclusion under partition/failover (Day 28). Acceptable *only* because reconcile is idempotent. Strict exclusion would demand a CP coordinator + fencing tokens. |
| **JWT auth** | **AP** | Stateless | No session store to partition from; the token is self-contained. Trades instant revocation (need a CP denylist for that) for availability and horizontal scale. |
| **Rate limiter (per-node Bucket4j)** | **AP** | Approximate | Per-instance buckets mean the cluster-wide limit is fuzzy under scale-out; we accept imprecision for availability. A globally exact limiter would need a CP counter (and would be a bottleneck). |
| **Resilience4j breaker** | makes downstream-failure **AP** | n/a | Converts a hard dependency failure into graceful degradation — choosing to stay *available* with a fallback over failing consistently. |

### The three headline trade-offs to be able to defend

1. **CQRS = a deliberate CAP split.** The write side is CP (one consistent ledger), the read side is AP (always-up, eventually-correct projection). We did *not* try to make one store be both. The price is a non-zero replication lag and the need to design every read to tolerate staleness — and to *test* with `await()`, never synchronous asserts.

2. **We refused 2PC and bought correctness with idempotency instead.** No distributed transaction spans Postgres and Kafka (Day 17/20). Instead: one local ACID transaction + at-least-once relay + idempotent consumer = effectively-exactly-once. This is the dominant pattern in real distributed systems precisely *because* 2PC's blocking behavior is a CAP catastrophe (a stuck coordinator makes participants unavailable).

3. **Availability is layered, and load-shedding is an availability *feature*.** Rate-limiting (429) and circuit-breaking (fallback) both *reduce* successful responses on purpose — but they preserve the availability of the *system* by preventing one tenant or one slow dependency from taking everything down. "Saying no fast" is more available than "saying yes until you fall over."

### How the 30-day arc was secretly all CAP

- **Day 1 WAL / Day 5 MVCC / Day 11 isolation** — local mechanisms for the *C* in a single node; the foundation you give up the moment data is replicated.
- **Day 17 2PC** — the textbook CP-across-systems answer, and why its blocking nature makes it an availability liability.
- **Day 18 Kafka / Day 19 event sourcing / Day 20 outbox** — the toolkit for building AP read sides from a CP-ordered log without dual writes.
- **Day 22 sharding / consistent hashing** — partitioning data is *introducing* the P, on purpose, and then choosing per-shard consistency.
- **Day 28 locks / leader election** — the place where CAP bites hardest: a "lock" that isn't safe under partition is a bug waiting for a failover.
- **Day 29 backpressure** — the *latency* side of PACELC made physical: a system that can't shed load loses availability.

Every day was a coordinate; today you drew the map.

---

## Where to go next — the senior roadmap

You can now build and *defend* a distributed system. To go from "can build it" to "can architect and operate it at scale":

- **System design at interview/staff depth.** Practice end-to-end designs (URL shortener → newsfeed → payment ledger → multi-region store). Force yourself to state the CAP/PACELC choice for every datastore. Reference: *Designing Data-Intensive Applications* (Kleppmann) — re-read Ch. 5 (replication), 7 (transactions), 8 (trouble with distributed systems), 9 (consistency & consensus) now that you've *built* these.
- **Consensus for real.** You used a best-effort Redis lock; next learn **Raft** (read the paper, then study etcd/ZooKeeper) and **fencing tokens** so you can build the CP coordinator when correctness demands it.
- **Sagas & process managers.** This system has one event hop. Real flows (order → payment → fulfillment → shipping, with compensations) need **choreographed or orchestrated sagas** built on the outbox you now own.
- **Exactly-once & stream processing.** Go deeper with Kafka transactions, Kafka Streams / Flink, and stateful stream processing for projections at scale.
- **Performance & JVM tuning.** Profile with async-profiler/JFR; understand GC (G1 vs ZGC), allocation pressure, connection-pool sizing (Little's Law on your HikariCP), and p99 latency under load (gatling/k6).
- **Operate it on Kubernetes.** Package both services as containers, add liveness/readiness probes wired to Actuator (Day 25), HPA on the custom `outbox_unsent`/consumer-lag metrics, run Postgres/Kafka/Redis as operators, and practice rolling deploys + chaos (kill pods, inject partitions with `toxiproxy`) to *see* your CAP choices under real failure.
- **Observability maturity.** Add distributed tracing (OpenTelemetry) end-to-end across the order→Kafka→inventory hop, SLOs, and alerting on the consistency-lag signals you instrumented today.

---

## Congratulations — you finished the 30 days

You started on **Day 1** writing bytes to a write-ahead log by hand. You finish on **Day 30** having assembled a secured, observable, resilient, event-driven distributed system — and, more importantly, able to *explain why every piece sits where it does on the CAP spectrum.*

**The arc, in one breath:** storage internals (WAL, hash & B-tree indexes, page storage, MVCC — Days 1–6) → the engineering discipline around them (idempotency, build tools, testing — Days 2, 7) → Spring as the application substrate (IoC, Boot, REST, JDBC, JPA, migrations, validation — Days 8–14) → making it fast and consistent (caching, Redis, transactions — Days 15–17) → going distributed (Kafka, event sourcing, the outbox, indexing-at-scale, sharding — Days 18–22) → making it production-grade (Testcontainers, circuit breakers, metrics, security, rate limiting, distributed locks, reactive — Days 23–29) → and today, the synthesis: the theory (CAP/PACELC) that explains *why* all those patterns exist and the capstone that *uses every one of them at once.*

The difference between a mid-level and a senior engineer is rarely knowing more APIs. It's holding the **trade-offs** in your head — and being able to say, out loud, in a design review: *"This is CP here because the ledger can't fork; this is AP there because the catalog must stay up; we lag by a few hundred milliseconds and that's the price we chose to pay."*

You can do that now. Go build something real.
