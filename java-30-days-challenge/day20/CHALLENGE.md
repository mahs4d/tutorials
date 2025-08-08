# Day 20: The Transactional Outbox Pattern

| | |
|---|---|
| 🏗️ **Project** | **OutboxRelay** — a transactional-outbox publisher with an idempotent consumer |
| ☕ **Java & language skills** | @Transactional + @Scheduled, JPA entities, Kafka producer/consumer, idempotent consumer code |
| 🧰 **Library / tool** | Spring Kafka + Spring Data JPA |
| 🗄️ **DB / distributed-systems concept** | Transactional Outbox pattern — the dual-write problem & reliable delivery |
| 📊 **Difficulty** | Hard |

---

## Concept primer

### 1. The dual-write problem

Almost every event-driven service wants to do two things when something happens: **change its own state** and **tell the world**. Concretely, when an order is placed you must:

1. `INSERT` the order into your database.
2. Publish an `OrderPlaced` event to Kafka so the shipping, billing, and analytics services react.

The naive code looks completely reasonable:

```java
@Transactional
public void placeOrder(CreateOrderRequest req) {
    Order order = orderRepository.save(new Order(req)); // (1) DB write
    kafkaTemplate.send("orders", new OrderPlaced(order)); // (2) Kafka publish  <-- BUG
}
```

This is a **dual write**: two independent systems mutated in one logical operation, with **no single transaction spanning both**. There is no atomicity, and *every possible interleaving has a failure that corrupts the system*:

- **DB commits, then the process crashes before the Kafka send.** The order exists; **no event was ever published.** Shipping never hears about it. The event is *lost forever* — the worst outcome because it is silent.
- **Kafka send succeeds, then the DB transaction rolls back** (constraint violation, deadlock, connection drop on commit). Downstream consumers ship an order that **does not exist** in your database. A **phantom event**.
- **Kafka send "times out"** but actually succeeded on the broker. You don't know whether to roll back the DB or not. (This is the *exact* uncertainty of Day 10's retried-POST and the Two Generals problem, now between *you* and the broker.)

Note the subtle trap: putting `kafkaTemplate.send(...)` *inside* `@Transactional` does **not** help. The Spring transaction only governs the JDBC connection. Kafka is a different resource with its own commit. The DB `COMMIT` and the Kafka append are two separate, non-atomic facts. You can reorder them, retry them, wrap them — you cannot make them one atomic step this way. **A dual write can never be made atomic by ordering alone.**

### 2. Why not just use a distributed transaction (2PC)? — callback to Day 17

On **Day 17** you implemented Two-Phase Commit and then spent the second half of the day on *why production systems avoid it*. The dual-write problem is *exactly* the situation 2PC was invented for: one coordinator, a PREPARE round to every participant, then a COMMIT round, giving atomicity across heterogeneous resources (Postgres + a message broker) via XA.

So why not reach for XA here? Recapping Day 17, sharpened for this case:

- **Kafka has no XA / no real 2PC participant.** Kafka's "transactions" are *intra-Kafka* (atomic writes across topic-partitions plus consumer-offset commits). They are **not** an XA resource you can enlist alongside a Postgres `XAConnection`. There is no production-grade `KafkaXAResource`. So the one technology you'd most want to two-phase-commit with simply doesn't play.
- **Blocking on coordinator failure.** 2PC's fatal flaw: a participant that has voted "yes" in PREPARE must **hold its locks and wait** for the coordinator's decision. If the coordinator dies after PREPARE, the participant is stuck *in-doubt*, locks held, indefinitely. That couples your order DB's availability to a transaction coordinator.
- **Latency and throughput.** Two network round-trips to every participant, with `fsync`s, on the hot path of every order. You're paying distributed-consensus latency for a single business write.
- **Operational weight.** A transaction manager (Narayana/Atomikos), recovery logs, in-doubt resolution tooling, XA driver configuration. Enormous complexity for "save a row and emit an event."

The senior conclusion (same as Day 17): **avoid distributed transactions; design so that one local transaction is enough.** The outbox does precisely that.

### 3. The Transactional Outbox — mechanics

The insight: you already have **one** transactional resource that can commit atomically — your relational database. So put *both* writes there.

> **Write the business row and the event into the same database, in the same local transaction.** Then have a separate process move events from the database to Kafka.

You add an `outbox` table. In the *single* `@Transactional` service method you:

1. `INSERT`/`UPDATE` the `orders` row (business state).
2. `INSERT` an `outbox` row describing the event (`OrderPlaced`, with the payload as JSON).

Because both are ordinary rows in the same DB, the local transaction makes them **atomic together**: either both the order and its outbox event are committed, or neither is. The dual write is gone — there is now only a **single write** (to one DB), and a separate read-and-forward step.

A separate **relay** (a.k.a. *message relay* / *outbox publisher*) then:

3. Reads unsent rows from `outbox` (`WHERE sent = false ORDER BY id`).
4. Publishes each to Kafka via `KafkaTemplate`.
5. Marks them `sent = true` (or deletes them) **after** the broker acknowledges.

```
                 ┌─────────────────── one local DB transaction ──────────────────┐
   placeOrder -> │ INSERT INTO orders ...      INSERT INTO outbox (..., sent=f)   │  COMMIT (atomic)
                 └────────────────────────────────────────────────────────────────┘
                                                  │
                          (separate process, separate time)
                                                  ▼
   relay  ──>  SELECT * FROM outbox WHERE sent=false ORDER BY id
          ──>  kafkaTemplate.send(topic, key, payload)  ──>  Kafka
          ──>  UPDATE outbox SET sent=true WHERE id=?
```

**Where the data-loss bug went:** if the process crashes *after* the DB `COMMIT* but *before* publishing, the event is **still sitting in the `outbox` table**. On restart the relay finds it and publishes it. Nothing is lost. The crash window that silently destroyed events in §1 is now *durable, recoverable state*. That is the whole point of today.

### 4. Relay strategy A — the polling publisher (`@Scheduled`)

The simplest relay: a scheduled job that polls.

```sql
SELECT * FROM outbox WHERE sent = false ORDER BY id ASC LIMIT 100 FOR UPDATE SKIP LOCKED;
```

For each row: `kafkaTemplate.send(...)`, wait for the broker ack, then `UPDATE ... SET sent = true`.

- **Pros:** trivial to build (just JPA + `@Scheduled` + Kafka, all things you already have), no extra infrastructure, easy to reason about and test.
- **Cons:** **polling latency** (events wait up to one poll interval), **constant DB load** even when idle, and you must think about **multiple instances** racing for the same rows (solved with `FOR UPDATE SKIP LOCKED` or a leader election so only one poller runs).

`SKIP LOCKED` (Postgres) is the key trick: each poller instance grabs a *different* batch of unlocked rows and skips rows another instance already locked, so N pollers parallelize safely without double-publishing within a poll.

### 5. Relay strategy B — CDC / log-tailing (Debezium)

Instead of polling the *table*, tail the database's **write-ahead log** (the WAL from **Day 1**). **Debezium** is a Kafka Connect connector that reads Postgres logical replication (the WAL), turns every committed `INSERT` into the `outbox` table into a Kafka message, and uses the **Outbox Event Router** SMT to route it to the right topic with the right key.

- **Pros:** **near-zero latency** (events flow as soon as the WAL is written), **no polling load** on the table, **no application code** in the publish path, naturally ordered per partition because the WAL is ordered.
- **Cons:** operational complexity — you run Kafka Connect + Debezium, configure logical replication (`wal_level=logical`), manage connector offsets and slot lag. This is the production-grade choice for high throughput; the poller is the right *first* implementation and what we build today.

A pragmatic middle ground many teams use: the poller for simplicity, then graduate to Debezium when latency/load demands it — the **outbox table schema stays the same**, only the relay changes. That's the architectural elegance: the outbox decouples "record the intent to publish" from "how publishing happens."

### 6. Delivery guarantees — and *why*

The relay is **at-least-once**, never exactly-once, and the reason is the *same dual-write uncertainty* in miniature:

> The relay must (a) publish to Kafka and (b) mark the row `sent`. These are again two systems. If it crashes *after* Kafka acks but *before* the `UPDATE sent=true` commits, on restart it will re-read the still-`unsent` row and **publish the event a second time.**

You *cannot* eliminate this duplicate at the producer — it's structurally the same problem one level down. (Kafka's idempotent producer dedups *retries within a single producer session* via a producer-id + sequence number, but it does **not** dedup across a relay restart with a fresh session.) So the rule is:

- **Producer side: at-least-once.** Accept that duplicates happen. Use `acks=all` and producer retries so you never *lose* an event; tolerate that you may *repeat* one.
- **Consumer side: make it idempotent.** This is where **Day 7** pays off. Every outbox event carries a stable, unique **event id** (a UUID generated when the outbox row is written). The consumer keeps a `processed_events` table and, before acting, checks "have I already processed this id?" If yes, it skips. Processing the event and recording the id happen **in the consumer's own local transaction**, so dedup is itself crash-safe.

At-least-once delivery **+** an idempotent consumer **= effectively-exactly-once** end to end. This is the standard, battle-tested recipe; true exactly-once across heterogeneous systems is essentially the 2PC mirage we rejected in §2.

### 7. Ordering

Kafka guarantees order **only within a partition**. To preserve per-aggregate order (all events for `order-42` in the sequence they happened):

- Choose a **Kafka message key** that is the aggregate id (e.g. the order id). Kafka hashes the key to a partition, so all events for one order land in one partition and stay ordered.
- Have the relay publish in **outbox-id order** (`ORDER BY id`) so events for the same aggregate are sent in the order they were written.
- If you need strict ordering and use a multi-instance poller with `SKIP LOCKED`, be aware that parallel pollers can publish *different* aggregates concurrently (fine) but you must not split one aggregate's events across pollers out of order — keying + single-threaded send per key, or partition-by-aggregate batching, handles this. (Debezium preserves WAL order naturally.)

---

## Prerequisites

You're combining two earlier projects:

- **Day 12 (JPA / N+1):** the Spring Boot app with `spring-boot-starter-data-jpa`, an `Order` `@Entity`, a `JpaRepository`, and a real database (we use **Postgres** now, not H2, because we want WAL/`SKIP LOCKED` semantics and because Debezium needs it).
- **Day 18 (Kafka / the log):** `spring-kafka`, a running broker, a topic, `KafkaTemplate`, and `@KafkaListener` consumers.

`pom.xml` dependencies (you should already have these from Days 12 and 18):

```xml
<dependencies>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-jpa</artifactId>
  </dependency>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter</artifactId> <!-- @Scheduled lives here -->
  </dependency>
  <dependency>
    <groupId>org.springframework.kafka</groupId>
    <artifactId>spring-kafka</artifactId>
  </dependency>
  <dependency>
    <groupId>org.postgresql</groupId>
    <artifactId>postgresql</artifactId>
    <scope>runtime</scope>
  </dependency>
</dependencies>
```

Spin up Postgres + Kafka with docker compose (Testcontainers alternative shown in Step 7):

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: shop
      POSTGRES_USER: shop
      POSTGRES_PASSWORD: shop
    ports: ["5432:5432"]
  kafka:
    image: apache/kafka:3.7.0   # KRaft mode, no ZooKeeper
    ports: ["9092:9092"]
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@localhost:9093
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
```

`application.yml`:

```yaml
spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/shop
    username: shop
    password: shop
  jpa:
    hibernate:
      ddl-auto: update        # use Flyway (Day 13) in real life
    properties:
      hibernate.format_sql: true
  kafka:
    bootstrap-servers: localhost:9092
    producer:
      acks: all               # never lose a write on the broker
      properties:
        enable.idempotence: true   # dedup producer *retries* within a session
    consumer:
      group-id: shipping-service
      auto-offset-reset: earliest
      enable-auto-commit: false      # we commit after successful processing
    listener:
      ack-mode: record               # commit offset per successfully handled record

outbox:
  poll-delay-ms: 500
  batch-size: 100
```

Base package: `com.example.shop` — adjust to match your earlier days.

---

## 🛠️ Project Walkthrough — OutboxRelay

Roll up your sleeves — from here on you build the outbox pipeline step by step, then run it and watch events flow end to end.

## Step 1 — The `Order` entity (business state)

`src/main/java/com/example/shop/order/Order.java`:

```java
package com.example.shop.order;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "orders")
public class Order {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String customer;

    @Column(nullable = false)
    private BigDecimal amount;

    @Column(nullable = false)
    private String status;        // CREATED, PAID, CANCELLED

    @Column(nullable = false)
    private Instant createdAt;

    protected Order() { }          // JPA needs a no-arg ctor

    public Order(String customer, BigDecimal amount) {
        this.customer = customer;
        this.amount = amount;
        this.status = "CREATED";
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public String getCustomer() { return customer; }
    public BigDecimal getAmount() { return amount; }
    public String getStatus() { return status; }
    public Instant getCreatedAt() { return createdAt; }
}
```

`src/main/java/com/example/shop/order/OrderRepository.java`:

```java
package com.example.shop.order;

import org.springframework.data.jpa.repository.JpaRepository;

public interface OrderRepository extends JpaRepository<Order, Long> { }
```

---

## Step 2 — The `OutboxEvent` entity (the durable event)

This is the new idea. Every event we *want* to publish becomes a row here, written in the *same* transaction as the business change.

`src/main/java/com/example/shop/outbox/OutboxEvent.java`:

```java
package com.example.shop.outbox;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "outbox", indexes = {
        // the relay's hot query: find unsent rows in id order
        @Index(name = "ix_outbox_unsent", columnList = "sent, id")
})
public class OutboxEvent {

    /** Stable, unique event id. This is the consumer's dedup key (Day 7). */
    @Id
    @Column(columnDefinition = "uuid")
    private UUID eventId;

    /** The aggregate this event is about (e.g. "Order"). */
    @Column(nullable = false)
    private String aggregateType;

    /** The aggregate id, used as the Kafka message KEY to preserve ordering. */
    @Column(nullable = false)
    private String aggregateId;

    /** The event type, e.g. "OrderPlaced". */
    @Column(nullable = false)
    private String eventType;

    /** The destination Kafka topic. */
    @Column(nullable = false)
    private String topic;

    /** The serialized event body (JSON). */
    @Column(nullable = false, columnDefinition = "text")
    private String payload;

    @Column(nullable = false)
    private boolean sent;

    @Column(nullable = false)
    private Instant createdAt;

    private Instant sentAt;

    protected OutboxEvent() { }

    public OutboxEvent(String aggregateType, String aggregateId, String eventType,
                       String topic, String payload) {
        this.eventId = UUID.randomUUID();   // generated at write time, travels with the event
        this.aggregateType = aggregateType;
        this.aggregateId = aggregateId;
        this.eventType = eventType;
        this.topic = topic;
        this.payload = payload;
        this.sent = false;
        this.createdAt = Instant.now();
    }

    public void markSent() {
        this.sent = true;
        this.sentAt = Instant.now();
    }

    public UUID getEventId() { return eventId; }
    public String getAggregateType() { return aggregateType; }
    public String getAggregateId() { return aggregateId; }
    public String getEventType() { return eventType; }
    public String getTopic() { return topic; }
    public String getPayload() { return payload; }
    public boolean isSent() { return sent; }
    public Instant getCreatedAt() { return createdAt; }
}
```

`src/main/java/com/example/shop/outbox/OutboxRepository.java` — note the `SKIP LOCKED` query that makes the relay safe across instances:

```java
package com.example.shop.outbox;

import jakarta.persistence.LockModeType;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.QueryHints;
import jakarta.persistence.QueryHint;
import org.springframework.data.jpa.repository.Query;

import java.util.List;
import java.util.UUID;

public interface OutboxRepository extends JpaRepository<OutboxEvent, UUID> {

    /**
     * Pull a batch of unsent events in insertion order, locking each row so a
     * second relay instance skips them (FOR UPDATE SKIP LOCKED). This lets
     * multiple pollers run safely without publishing the same event twice.
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "-2")) // SKIP LOCKED
    @Query("select o from OutboxEvent o where o.sent = false order by o.id")
    List<OutboxEvent> findUnsentBatch(Limit limit);
}
```

> The `lock.timeout = -2` hint maps to Postgres `SKIP LOCKED` in Hibernate 6. If your provider/version doesn't honor it, use a native query: `select * from outbox where sent = false order by created_at limit :n for update skip locked`. Either way the intent is identical.

---

## Step 3 — The service: business write + outbox in ONE transaction

This is the heart of the pattern. The order and the event are written under a single `@Transactional`. There is **no Kafka call here** — the service never touches the broker. It only records the *intent* to publish.

`src/main/java/com/example/shop/order/OrderEvents.java` (the event payload + a tiny serializer helper):

```java
package com.example.shop.order;

import java.math.BigDecimal;

public record OrderPlaced(Long orderId, String customer, BigDecimal amount) { }
```

`src/main/java/com/example/shop/order/OrderService.java`:

```java
package com.example.shop.order;

import com.example.shop.outbox.OutboxEvent;
import com.example.shop.outbox.OutboxRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderService {

    public static final String ORDERS_TOPIC = "orders";

    private final OrderRepository orderRepository;
    private final OutboxRepository outboxRepository;
    private final ObjectMapper objectMapper;

    public OrderService(OrderRepository orderRepository,
                        OutboxRepository outboxRepository,
                        ObjectMapper objectMapper) {
        this.orderRepository = orderRepository;
        this.outboxRepository = outboxRepository;
        this.objectMapper = objectMapper;
    }

    /**
     * ONE local transaction writes BOTH the order and its outbox event.
     * Either both commit or neither does — the dual write is gone.
     * Crucially: NO kafkaTemplate.send() here.
     */
    @Transactional
    public Order placeOrder(String customer, java.math.BigDecimal amount) {
        // (1) business state
        Order order = orderRepository.save(new Order(customer, amount));

        // (2) the event, as a row in the SAME transaction
        OrderPlaced event = new OrderPlaced(order.getId(), order.getCustomer(), order.getAmount());
        OutboxEvent outbox = new OutboxEvent(
                "Order",
                String.valueOf(order.getId()),     // -> Kafka key, preserves per-order ordering
                "OrderPlaced",
                ORDERS_TOPIC,
                toJson(event));
        outboxRepository.save(outbox);

        return order;
        // COMMIT here makes order + outbox atomic.
    }

    private String toJson(Object o) {
        try {
            return objectMapper.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Failed to serialize event", e);
        }
    }
}
```

A thin REST controller so we can trigger it (reusing Day 10 conventions):

```java
package com.example.shop.order;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import java.math.BigDecimal;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService service;

    public OrderController(OrderService service) { this.service = service; }

    public record CreateOrderRequest(String customer, BigDecimal amount) { }

    @PostMapping
    public ResponseEntity<Long> create(@RequestBody CreateOrderRequest req) {
        Order o = service.placeOrder(req.customer(), req.amount());
        return ResponseEntity.created(java.net.URI.create("/orders/" + o.getId())).body(o.getId());
    }
}
```

---

## Step 4 — The `@Scheduled` relay (the message publisher)

A separate process step polls unsent rows, publishes to Kafka, and marks them sent **only after** the broker acknowledges. Enable scheduling on your main app class with `@EnableScheduling`.

`src/main/java/com/example/shop/outbox/OutboxRelay.java`:

```java
package com.example.shop.outbox;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Limit;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;

@Component
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final OutboxRepository outboxRepository;
    private final KafkaTemplate<String, String> kafkaTemplate;

    @Value("${outbox.batch-size:100}")
    private int batchSize;

    public OutboxRelay(OutboxRepository outboxRepository,
                       KafkaTemplate<String, String> kafkaTemplate) {
        this.outboxRepository = outboxRepository;
        this.kafkaTemplate = kafkaTemplate;
    }

    /**
     * Poll -> publish -> mark sent. The whole batch runs in a transaction so the
     * SELECT ... FOR UPDATE SKIP LOCKED row locks are held while we publish, and
     * the markSent() flips are committed together.
     *
     * fixedDelayString = wait this long AFTER the previous run finishes, so a slow
     * Kafka doesn't pile up overlapping polls.
     */
    @Scheduled(fixedDelayString = "${outbox.poll-delay-ms:500}")
    @Transactional
    public void publishOutbox() {
        List<OutboxEvent> batch = outboxRepository.findUnsentBatch(Limit.of(batchSize));
        if (batch.isEmpty()) {
            return;
        }
        log.info("Outbox relay: publishing {} event(s)", batch.size());

        for (OutboxEvent e : batch) {
            try {
                // Send with the aggregate id as KEY -> all events for one order
                // go to one partition and stay ordered. Block on the ack so we
                // only mark sent after the broker has durably accepted it (acks=all).
                kafkaTemplate.send(e.getTopic(), e.getAggregateId(), e.getPayload())
                        .get(10, TimeUnit.SECONDS);

                e.markSent();   // dirty-checked & flushed at commit
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("Relay interrupted", ie);
            } catch (ExecutionException | java.util.concurrent.TimeoutException ex) {
                // Broker rejected/timed out: leave sent=false. Row stays in the
                // outbox and will be retried on the next poll. AT-LEAST-ONCE.
                log.warn("Failed to publish event {}, will retry: {}", e.getEventId(), ex.toString());
                // Stop the batch here so we don't publish later events out of order
                // for the same aggregate after a gap.
                break;
            }
        }
        // COMMIT: row locks released, markSent() persisted.
    }
}
```

Two design points to internalize:

- **The Kafka send is *outside* the order-placing transaction and *inside* the relay's own transaction.** The service that creates the order never blocks on Kafka — order placement stays fast and never fails because the broker is down.
- **Mark-sent-after-ack is the at-least-once seam.** If we crash after `.get()` returns (broker has the message) but before COMMIT persists `markSent()`, the row is still `sent=false` on restart and we publish it **again**. That duplicate is expected and handled by the idempotent consumer in Step 5.

Enable scheduling:

```java
package com.example.shop;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class ShopApplication {
    public static void main(String[] args) {
        SpringApplication.run(ShopApplication.class, args);
    }
}
```

---

## Step 5 — The idempotent consumer (dedup by event id) — callback to Day 7

The relay is at-least-once, so the consumer **must** tolerate duplicates. We carry the `eventId` as a Kafka **header** and dedup on it with a `processed_events` table — the persistence-layer idempotency key from **Day 7**, now applied to event consumption.

First, make the relay attach the event id as a header (replace the `send` in Step 4 with a `ProducerRecord` carrying it):

```java
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.header.internals.RecordHeader;
import java.nio.charset.StandardCharsets;

ProducerRecord<String, String> record =
        new ProducerRecord<>(e.getTopic(), e.getAggregateId(), e.getPayload());
record.headers().add(new RecordHeader("eventId",
        e.getEventId().toString().getBytes(StandardCharsets.UTF_8)));
kafkaTemplate.send(record).get(10, TimeUnit.SECONDS);
```

The dedup table:

`src/main/java/com/example/shop/shipping/ProcessedEvent.java`:

```java
package com.example.shop.shipping;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "processed_events")
public class ProcessedEvent {

    @Id
    @Column(columnDefinition = "uuid")
    private UUID eventId;        // unique -> the dedup guarantee

    @Column(nullable = false)
    private Instant processedAt;

    protected ProcessedEvent() { }

    public ProcessedEvent(UUID eventId) {
        this.eventId = eventId;
        this.processedAt = Instant.now();
    }
}
```

```java
package com.example.shop.shipping;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.UUID;

public interface ProcessedEventRepository extends JpaRepository<ProcessedEvent, UUID> { }
```

The consumer — the dedup check and the business effect happen in the **same local transaction**, so we never act without recording the id (and vice versa):

`src/main/java/com/example/shop/shipping/ShippingConsumer.java`:

```java
package com.example.shop.shipping;

import com.example.shop.order.OrderPlaced;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.messaging.handler.annotation.Header;
import org.springframework.messaging.handler.annotation.Payload;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

@Component
public class ShippingConsumer {

    private static final Logger log = LoggerFactory.getLogger(ShippingConsumer.class);

    private final ProcessedEventRepository processed;
    private final ObjectMapper objectMapper;

    public ShippingConsumer(ProcessedEventRepository processed, ObjectMapper objectMapper) {
        this.processed = processed;
        this.objectMapper = objectMapper;
    }

    @KafkaListener(topics = "orders", groupId = "shipping-service")
    @Transactional
    public void onOrderPlaced(@Payload String json,
                              @Header("eventId") String eventIdHeader) throws Exception {
        UUID eventId = UUID.fromString(eventIdHeader);

        // DEDUP: have we already processed this exact event? (Day 7)
        if (processed.existsById(eventId)) {
            log.info("Duplicate event {} ignored", eventId);
            return; // offset still commits; we just do nothing
        }

        OrderPlaced event = objectMapper.readValue(json, OrderPlaced.class);

        // --- the real business effect (idempotent or not, doesn't matter now) ---
        log.info("SHIPPING order {} for {} (amount {})",
                event.orderId(), event.customer(), event.amount());

        // Record that we handled it, in the SAME transaction as the effect.
        // If this commit fails, the effect rolls back too and we'll re-receive it.
        processed.save(new ProcessedEvent(eventId));
    }
}
```

> **Why a DB table and not just an in-memory `Set`?** The consumer can restart, and Kafka will redeliver any record whose offset wasn't committed. Dedup state must survive restarts and be shared across consumer instances — exactly the Day 7 argument for persisting idempotency keys. The unique PK on `eventId` also gives you a hard backstop: a concurrent double-process attempt fails the second `INSERT` on the constraint.

---

## Step 6 — Run it and watch end to end

Start infra and the app:

```bash
docker compose up -d
./mvnw spring-boot:run
```

Create an order:

```bash
curl -i -X POST http://localhost:8080/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer":"alice","amount":49.99}'
# HTTP/1.1 201 Created
# Location: /orders/1
# 1
```

Within ~500 ms the relay log fires and the consumer reacts:

```
OutboxRelay  : Outbox relay: publishing 1 event(s)
ShippingConsumer : SHIPPING order 1 for alice (amount 49.99)
```

Inspect the outbox flipping to sent:

```bash
docker exec -it $(docker compose ps -q postgres) \
  psql -U shop -d shop -c \
  "select event_id, event_type, aggregate_id, sent, sent_at from outbox;"
```

```
               event_id               | event_type | aggregate_id | sent |          sent_at
--------------------------------------+------------+--------------+------+----------------------------
 7c9e6a1b-...-2f3d                     | OrderPlaced| 1            | t    | 2026-06-16 10:00:00.5+00
```

---

## Step 7 — Simulate the crash that *used* to lose events

This is the proof that the outbox fixes the dual-write bug. We make the relay "crash" *after* the business commit but *before* publishing, then restart and watch the event survive.

**A. Stop the relay from running, then place an order.** The simplest way is to temporarily disable scheduling — set `outbox.poll-delay-ms` to something huge, or comment out `@EnableScheduling`, restart, and POST an order. (This models "the DB committed, but the publish step never got to run because the process died.")

```bash
curl -s -X POST http://localhost:8080/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer":"bob","amount":12.00}'
```

**B. Confirm the order is committed but the event is NOT yet in Kafka.**

```bash
# Order row exists:
psql ... -c "select id, customer, status from orders where customer='bob';"
#  id | customer | status
#   2 | bob      | CREATED

# Outbox row exists, still unsent — the event is NOT lost, it's durable:
psql ... -c "select event_id, sent from outbox where aggregate_id='2';"
#  event_id | sent
#  ...      | f       <-- still false, never published, but SAFE in the DB
```

With the naive dual-write code (`save` then `send`, no outbox), at this point the event would be **gone forever** — the process crashed between commit and send, and there is no record that the event was ever owed.

**C. Restart with the relay enabled.** Re-enable scheduling and restart:

```bash
./mvnw spring-boot:run
```

The relay finds the unsent row on its first poll and publishes it:

```
OutboxRelay  : Outbox relay: publishing 1 event(s)
ShippingConsumer : SHIPPING order 2 for bob (amount 12.00)
```

```bash
psql ... -c "select event_id, sent from outbox where aggregate_id='2';"
#  event_id | sent
#  ...      | t       <-- now published, after the crash, with NO loss
```

**Expected result / what it proves:** the event placed during the "crash window" was **not lost** — it waited durably in the outbox and was published on recovery. The dual-write data-loss bug from §1 is eliminated.

**D. (Optional) Prove the at-least-once duplicate is harmless.** Manually re-arm a sent row and watch the consumer dedup it:

```bash
psql ... -c "update outbox set sent=false where aggregate_id='1';"
```

Next poll republishes order 1's event; the consumer logs:

```
ShippingConsumer : Duplicate event 7c9e6a1b-...-2f3d ignored
```

The business effect ran **once**, even though the event was delivered **twice** — at-least-once delivery + idempotent consumer = effectively-exactly-once.

### Testcontainers version (preview of Day 23)

For an automated test that spins up real Postgres + Kafka and asserts no loss:

```java
@SpringBootTest
@Testcontainers
class OutboxIntegrationTest {

    @Container static PostgreSQLContainer<?> pg = new PostgreSQLContainer<>("postgres:16");
    @Container static KafkaContainer kafka =
            new KafkaContainer(DockerImageName.parse("apache/kafka:3.7.0"));

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.datasource.url", pg::getJdbcUrl);
        r.add("spring.datasource.username", pg::getUsername);
        r.add("spring.datasource.password", pg::getPassword);
        r.add("spring.kafka.bootstrap-servers", kafka::getBootstrapServers);
    }

    @Autowired OrderService orderService;
    @Autowired OutboxRepository outbox;

    @Test
    void event_survives_when_relay_runs_after_commit() {
        var order = orderService.placeOrder("carol", new BigDecimal("7.50"));
        // Before the relay runs, the event is durable but unsent:
        assertThat(outbox.findAll()).anyMatch(e -> !e.isSent());
        // The @Scheduled relay (or a manual call) then publishes it; assert via a test consumer.
    }
}
```

Day 23 turns this into the standard way you test the whole stack.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

**Debezium CDC as the production relay.** The poller is great to start, but at scale you swap it for Debezium (no app code in the publish path, near-zero latency, no table polling). You write the **same** `outbox` rows; Debezium tails the Postgres WAL via logical replication and its **Outbox Event Router** SMT maps your columns (`aggregateType` → topic, `aggregateId` → key, `payload` → value, `eventId` → header) onto Kafka records. Requires `wal_level=logical`, a replication slot, and Kafka Connect. The beauty: **the application code in Steps 1–3 does not change at all** — only the relay implementation. Note one nuance: with CDC you typically don't even need a `sent` column; the WAL position *is* the cursor, so the table can be `INSERT`-only (or even capture from a transient table). Some teams `DELETE` the outbox row right after inserting it in the same transaction — Debezium still captures the `INSERT` from the WAL, and the table stays empty.

**Outbox cleanup / retention.** With the poller marking `sent=true`, the table grows forever. You must reap it: a periodic `DELETE FROM outbox WHERE sent = true AND sent_at < now() - interval '7 days'`, or partition the table by day and drop old partitions, or `DELETE` immediately after mark-sent. Keep a short retention window for debugging/replay. An unbounded outbox table eventually destroys your `SKIP LOCKED` query's performance and your storage budget — make cleanup a first-class scheduled job, not an afterthought.

**Ordering, revisited.** Per-aggregate order is preserved by (a) keying on `aggregateId` and (b) publishing in outbox-id order. The break-the-batch-on-failure logic in Step 4 matters: if event #3 for order-42 fails, you must **not** publish #4 for order-42 before retrying #3, or the consumer sees them out of order. Stopping the batch on first failure is the simple correct choice; a more advanced relay tracks per-key cursors. Debezium sidesteps this because WAL order is total and preserved.

**The listen-to-yourself pattern.** A neat variant: the service that *owns* the aggregate also **consumes its own** outbox events from Kafka to update its read model / projection, instead of updating two tables in the write transaction. The write path stays tiny (one aggregate row + one outbox row), and *all* state derived from the event — including the owning service's own query-side views — is built off the single ordered event stream. This dovetails with **Day 19's event sourcing**: the outbox is how an event-sourced aggregate reliably gets its events onto the bus.

**Sagas.** The outbox is the reliable-publish primitive that makes **sagas** (distributed, multi-service business transactions coordinated by events instead of 2PC) actually safe. Each saga step is "update my DB + emit the next command/event" in one local transaction via the outbox; compensating actions are themselves outbox events. Without a reliable outbox, a saga has the same dual-write hole you fixed today at every single step. This is the direct, practical sequel to rejecting 2PC on Day 17: sagas + outbox + idempotent consumers are *the* mainstream alternative to distributed transactions.

**Why `acks=all` + producer idempotence matter here.** `acks=all` ensures a published event is on all in-sync replicas before the relay marks it sent — without it, a broker failover could lose an "acked" event, re-introducing loss. `enable.idempotence=true` dedups the *producer's own retries* within a session (so a transient retry inside one `send` doesn't duplicate), but it does **not** cover the relay-restart duplicate — that's why the consumer-side dedup is non-negotiable, not optional polish.

### Stretch goals

1. **Multi-instance relay race test.** Run two app instances pointed at the same DB and Kafka, hammer `POST /orders`, and verify with `SKIP LOCKED` that **every** event is published **exactly once to the producer** (no duplicate `sent` flips) and the consumer dedups any at-least-once repeats. Then remove `SKIP LOCKED` and watch the double-publishing appear — proof of why it's there.
2. **Swap the poller for Debezium.** Add Kafka Connect + the Debezium Postgres connector with the Outbox Event Router SMT, set `wal_level=logical`, and delete the `@Scheduled` relay entirely. Confirm events still flow with the *unchanged* `OrderService`. Measure the latency drop versus the 500 ms poll.
3. **Add a dead-letter + retry policy.** Give `OutboxEvent` an `attempts` counter; after N failed publishes, move the row to a `dead_outbox` table and alert, instead of blocking the batch forever on a poison event.
4. **Implement a two-step saga.** Add a `PaymentService` that consumes `OrderPlaced`, charges (simulated), and emits `PaymentCaptured` *via its own outbox*; have the order service consume that and flip the order to `PAID`. End to end this is a 2PC-free distributed transaction built entirely from outboxes + idempotent consumers.

### Day 21 teaser

You've now got an `outbox` table with a hot query — `WHERE sent = false ORDER BY id` — and a `processed_events` table probed on every message. Tomorrow, **Day 21: Indexing**, you'll dig into *why* the `(sent, id)` index you sprinkled in here actually makes that query fast, when Postgres chooses an index scan vs a sequential scan, the difference between a **partial index** (`WHERE sent = false` — perfect for an outbox!), covering indexes, and how to read `EXPLAIN ANALYZE`. You'll connect it back to the **B-tree** internals from **Day 6** and prove that the right index turns the relay's poll from a full-table scan into a millisecond lookup.
