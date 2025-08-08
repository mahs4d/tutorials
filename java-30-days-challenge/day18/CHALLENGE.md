# Day 18: Kafka, Partitions & the Log

| | |
|---|---|
| 🏗️ **Project** | **EventLog** — a Kafka partitioned producer/consumer-group demo |
| ☕ **Java & language skills** | KafkaTemplate producer, @KafkaListener, serializers, Docker compose, consumer groups in code |
| 🧰 **Library / tool** | Spring Kafka |
| 🗄️ **DB / distributed-systems concept** | The distributed commit log — topics, partitions, offsets, delivery semantics |
| 📊 **Difficulty** | Hard |

---

## Concept primer: it's all a log

### Callback to Day 1 — the WAL

On Day 1 you built a Write-Ahead Log: an **append-only file** where every mutation was written sequentially before being applied. You learned three things:
1. **Appends are cheap** — sequential I/O is dramatically faster than random writes.
2. **The log is the source of truth** — state is a *derivation* you replay from the log.
3. **An offset (byte position / sequence number) names a point in the log**, and "where am I caught up to" is just a stored offset.

Kafka is that exact idea, taken to its logical extreme and distributed across a cluster. A Kafka **topic** is a log. Producers **append** to the end; consumers **read forward** from a remembered offset and never block writers. There is no in-place update. If your Day 1 WAL and a Kafka topic feel suspiciously similar — good, they're the same abstraction. The hard parts Kafka adds are *partitioning* (one log isn't enough throughput) and *replication* (one disk isn't durable enough).

### Topics, partitions, offsets

A single append-only file is a throughput bottleneck: one writer, one disk, one ordering. Kafka splits a topic into **N partitions**, each of which is an independent append-only log on disk.

```
topic "orders" (numPartitions = 3)

partition 0:  [o#0][o#1][o#2][o#3] ...   <- offsets are per-partition, monotonic
partition 1:  [o#0][o#1][o#2] ...
partition 2:  [o#0][o#1][o#2][o#3][o#4] ...
                                  ^
                            log end offset (next append goes here)
```

Key facts:
- An **offset** is a 64-bit integer that is monotonic **within a partition**. Offset 5 in partition 0 has nothing to do with offset 5 in partition 1.
- The partition a record lands in is chosen by the **key**: `partition = hash(key) % numPartitions` (Kafka uses murmur2 on the key bytes; null key → round-robin/sticky). This is *exactly* the hashing idea you'll formalize on Day 22 (Consistent Hashing) — for now note the consequence: **same key ⇒ same partition ⇒ ordered relative to each other**.
- Records are immutable once written. A consumer's progress is a single number per partition: its **committed offset**.

### Ordering guarantees — the rule everyone trips on

> Kafka guarantees ordering **per partition**, never across partitions.

So if order matters for an entity (e.g. all events for `order-42` must be processed `CREATED → PAID → SHIPPED`), you **key by that entity id**. All `order-42` events hash to the same partition and arrive in append order. But `order-42` (partition 1) and `order-99` (partition 0) have **no defined global order** — and that's fine, because they're unrelated. This is the central trade-off of the day: **partitions buy you parallelism at the cost of global ordering**, and you recover the ordering you actually need by choosing the key well.

### Consumer groups & rebalancing

Multiple consumers cooperate via a **consumer group** (a `group.id`). Kafka's invariant:

> Within one group, each partition is consumed by **exactly one** consumer instance.

```
topic orders: partitions [0,1,2]

Group "billing", 1 consumer:        Group "billing", 2 consumers:
  C1 <- p0, p1, p2                    C1 <- p0, p1
                                      C2 <- p2

Group "billing", 4 consumers:
  C1 <- p0,  C2 <- p1,  C3 <- p2,  C4 <- (idle! no partition to own)
```

- Adding consumers scales throughput **up to the partition count**. A 3-partition topic can usefully run at most 3 consumers per group; a 4th sits idle. This is why **partition count caps consumer parallelism** — you size partitions for your peak parallelism, and over-partitioning is the usual fix.
- When a consumer joins or dies, Kafka triggers a **rebalance**: partitions are reassigned across the surviving members. During a rebalance, processing pauses briefly (the classic "stop-the-world" rebalance; cooperative rebalancing reduces this).
- A **different** group (`group.id = "analytics"`) gets its **own independent copy** of every partition with its own offsets. This is how the same log feeds many consumers — the foundation of event-driven architecture and the springboard to Event Sourcing (Day 19).

### Replication & the ISR — durability

Each partition has **R replicas** on different brokers. One is the **leader** (handles all reads/writes); the rest are **followers** that pull from the leader. The set of replicas currently caught up is the **ISR (in-sync replica set)**.

- `acks=all` + `min.insync.replicas=2` means a write is only acknowledged once it's on the leader **and** at least one in-sync follower. If the leader dies, a follower in the ISR is promoted and **no acknowledged data is lost**.
- `acks=1` acks after the leader writes — fast, but a leader crash before replication loses that record.
- This is the distributed analog of your Day 1 `fsync`: "is it durable enough that I can tell the client it's committed?"

### Delivery semantics — why idempotency (Day 7) returns

Default Kafka consumption is **at-least-once**:

1. Consumer reads record at offset 100.
2. Consumer processes it (charges a card, updates a DB).
3. Consumer commits offset 100.

If the consumer crashes **between 2 and 3**, the offset was never committed, so after restart it re-reads offset 100 and **processes it again**. That's a duplicate. You cannot eliminate this with at-least-once delivery — you can only make reprocessing *safe*, which is precisely the **idempotency** work from Day 7 (idempotency keys / dedup tables / natural upserts). Today you'll deliberately trigger a redelivery and watch the duplicate appear.

(The opposite failure — commit *before* processing — gives at-most-once: you can lose records but never duplicate. Almost nobody wants that. Exactly-once exists in Kafka via transactions; see the senior notes.)

---

## Prerequisites

- JDK 17+ and Maven (Day 1).
- Docker / Docker Compose (Day 16).
- Comfort with `@Component`/`@Configuration` beans (Day 8 IoC) and idempotency (Day 7).

### docker-compose: a single-broker Kafka (Redpanda)

We use **Redpanda** — Kafka-API-compatible, a single Go binary, **no ZooKeeper**, fast to boot, and it ships the `rpk` CLI. Spring Kafka talks to it unchanged.

`day18/docker-compose.yml`:

```yaml
services:
  redpanda:
    image: redpandadata/redpanda:v24.2.7
    container_name: day18-redpanda
    command:
      - redpanda
      - start
      - --overprovisioned          # fine for a laptop / single core
      - --smp=1
      - --memory=1G
      - --reserve-memory=0M
      - --node-id=0
      - --check=false
      # advertise localhost so the host JVM can connect
      - --kafka-addr=PLAINTEXT://0.0.0.0:9092
      - --advertise-kafka-addr=PLAINTEXT://localhost:9092
    ports:
      - "9092:9092"     # Kafka API
      - "9644:9644"     # admin / metrics
    healthcheck:
      test: ["CMD", "rpk", "cluster", "health", "-x", "use_metrics"]
      interval: 5s
      timeout: 5s
      retries: 10

  # Optional web UI to eyeball topics, partitions, offsets, consumer lag.
  console:
    image: redpandadata/console:v2.7.2
    container_name: day18-console
    depends_on: [redpanda]
    environment:
      KAFKA_BROKERS: redpanda:9092
    ports:
      - "8088:8080"
```

> Prefer "real" Kafka? Swap the service for `confluentinc/cp-kafka:7.7.0` in KRaft mode (no ZooKeeper) or `apache/kafka:3.8.0`. Everything below is identical — it's the Kafka protocol either way. On Day 23 we'll spin this up automatically with **Testcontainers** instead of by hand.

Bring it up:

```bash
cd day18
docker compose up -d
docker compose ps          # wait until redpanda is healthy
```

### Maven dependency

`day18/pom.xml` (Spring Boot parent assumed, as in earlier days):

```xml
<dependencies>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter</artifactId>
  </dependency>
  <dependency>
    <groupId>org.springframework.kafka</groupId>
    <artifactId>spring-kafka</artifactId>
  </dependency>
  <!-- record DTOs as JSON -->
  <dependency>
    <groupId>com.fasterxml.jackson.core</groupId>
    <artifactId>jackson-databind</artifactId>
  </dependency>
</dependencies>
```

### Config — `src/main/resources/application.yml`

```yaml
spring:
  kafka:
    bootstrap-servers: localhost:9092
    producer:
      key-serializer: org.apache.kafka.common.serialization.StringSerializer
      value-serializer: org.springframework.kafka.support.serializer.JsonSerializer
      acks: all                         # wait for leader + ISR -> durable
      properties:
        enable.idempotence: true        # producer-side dedup on retries (no dup appends)
    consumer:
      group-id: billing                 # default group; we override per-listener below
      key-deserializer: org.apache.kafka.common.serialization.StringDeserializer
      value-deserializer: org.springframework.kafka.support.serializer.JsonDeserializer
      auto-offset-reset: earliest       # new group reads the log from the start
      enable-auto-commit: false         # WE commit, so we can observe at-least-once
      properties:
        spring.json.trusted.packages: "*"
    listener:
      ack-mode: manual                  # listener gets an Acknowledgment to commit by hand

app:
  topic: orders
```

Two deliberate choices that make today's experiments work:
- `enable-auto-commit: false` + `ack-mode: manual` → **we** decide when an offset is committed, so we can *not* commit and force a redelivery.
- `auto-offset-reset: earliest` → a brand-new consumer group replays the whole log (handy for demos and central to Day 19).

---

## 🛠️ Project Walkthrough — EventLog

Roll up your sleeves: from here you'll build the topic, producer, consumer, and runner step by step, then run the experiments and read the output.

## Step 1 — Create the topic with 3 partitions

Let Spring declare it on startup (it talks to the broker's AdminClient). 3 partitions, replication-factor 1 (single broker).

`src/main/java/.../KafkaTopicConfig.java`:

```java
package com.example.day18;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

@Configuration
public class KafkaTopicConfig {

    @Bean
    NewTopic ordersTopic() {
        return TopicBuilder.name("orders")
                .partitions(3)
                .replicas(1)            // single broker -> RF must be 1
                .build();
    }
}
```

> On a multi-broker cluster you'd set `.replicas(3)` and `min.insync.replicas=2` for the durability story above. With one broker, RF=1 means a broker loss = data loss — acceptable only for a learning rig.

## Step 2 — The event DTO

`OrderEvent.java`:

```java
package com.example.day18;

public record OrderEvent(String orderId, String type, long amountCents) {}
```

## Step 3 — Producer keyed by `orderId`

The **key is the whole point**: keying by `orderId` pins all events for one order to one partition, preserving their relative order.

`OrderProducer.java`:

```java
package com.example.day18;

import org.apache.kafka.clients.producer.RecordMetadata;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class OrderProducer {

    private final KafkaTemplate<String, OrderEvent> kafka;
    private final String topic;

    public OrderProducer(KafkaTemplate<String, OrderEvent> kafka,
                         @Value("${app.topic}") String topic) {
        this.kafka = kafka;
        this.topic = topic;
    }

    /** Key = orderId => same order's events always go to the same partition, in order. */
    public void send(OrderEvent event) {
        kafka.send(topic, event.orderId(), event)
             .whenComplete((result, ex) -> {
                 if (ex != null) {
                     System.err.printf("FAILED to send %s: %s%n", event, ex.getMessage());
                     return;
                 }
                 RecordMetadata m = result.getRecordMetadata();
                 System.out.printf("SENT    key=%-8s type=%-8s -> partition=%d offset=%d%n",
                         event.orderId(), event.type(), m.partition(), m.offset());
             });
    }
}
```

## Step 4 — Consumer in a group, partition-aware logging

We log the partition and offset for every record so we can *see* the ordering and assignment. The `Acknowledgment` is how we manually commit (because `ack-mode: manual`).

`OrderConsumer.java`:

```java
package com.example.day18;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

@Component
public class OrderConsumer {

    /**
     * groupId "billing": all instances of this app share the partitions of "orders".
     * concurrency would also work, but we'll run TWO JVMs to make assignment obvious.
     */
    @KafkaListener(topics = "${app.topic}", groupId = "billing")
    public void consume(ConsumerRecord<String, OrderEvent> rec, Acknowledgment ack) {
        OrderEvent e = rec.value();
        System.out.printf("RECV    [p%d@%d] key=%-8s type=%-8s amount=%d  (thread %s)%n",
                rec.partition(), rec.offset(), rec.key(), e.type(), e.amountCents(),
                Thread.currentThread().getName());

        // ... real work: charge card, write DB, etc. (must be IDEMPOTENT - Day 7) ...

        ack.acknowledge();   // commit this offset only AFTER successful processing
    }
}
```

## Step 5 — A runner that produces a burst across several orders

`DemoRunner.java` (Spring `CommandLineRunner` so it fires once on boot):

```java
package com.example.day18;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

@Component
public class DemoRunner implements CommandLineRunner {

    private final OrderProducer producer;

    public DemoRunner(OrderProducer producer) {
        this.producer = producer;
    }

    @Override
    public void run(String... args) throws Exception {
        // Only produce when started with: --produce
        if (args.length == 0 || !args[0].equals("--produce")) {
            System.out.println(">> consumer-only mode (no --produce). Waiting for records...");
            return;
        }

        String[] orders = {"order-1", "order-2", "order-3", "order-4", "order-5"};
        String[] lifecycle = {"CREATED", "PAID", "SHIPPED"};

        // Interleave orders so the GLOBAL send order is mixed,
        // but each order's 3 events keep their relative order.
        for (String stage : lifecycle) {
            for (String orderId : orders) {
                producer.send(new OrderEvent(orderId, stage, 1000 + stage.length()));
                Thread.sleep(50);
            }
        }
        Thread.sleep(2000); // let async callbacks print
        System.out.println(">> done producing");
    }
}
```

`Day18Application.java`:

```java
package com.example.day18;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class Day18Application {
    public static void main(String[] args) {
        SpringApplication.run(Day18Application.class, args);
    }
}
```

---

## How to run the experiments

### Experiment A — per-partition ordering vs global ordering

Start **one** instance that produces and consumes:

```bash
cd day18
docker compose up -d
mvn -q spring-boot:run -Dspring-boot.run.arguments=--produce
```

Look at the `SENT` lines: orders are spread across partitions 0/1/2 by their key hash, but **every event of a given `order-N` goes to the same partition**. Look at the `RECV` lines: within any one partition the offsets are strictly increasing and `CREATED` precedes `PAID` precedes `SHIPPED` for that order — but across partitions the interleaving is arbitrary.

### Experiment B — two consumers, watch partition assignment & rebalancing

Terminal 1 (consumer-only, no `--produce`):

```bash
mvn -q spring-boot:run
```

Terminal 2 (a second instance, also consumer-only — same `group.id=billing`):

```bash
mvn -q spring-boot:run
```

Watch the logs: Kafka **rebalances** and splits the 3 partitions across the two JVMs (e.g. JVM-1 gets p0,p1 and JVM-2 gets p2). Spring logs lines like `partitions assigned: [orders-0, orders-1]`. Now from a third terminal, produce:

```bash
mvn -q spring-boot:run -Dspring-boot.run.arguments=--produce
```

Each JVM only receives records for the partitions it owns. Kill one consumer (Ctrl-C) and watch the other JVM get **all 3 partitions reassigned** within a few seconds — that's a rebalance.

### Inspect the log with the CLI (`rpk`, Kafka-compatible)

```bash
# topic layout: 3 partitions, leader, replicas
docker exec -it day18-redpanda rpk topic describe orders

# read the raw log of ONE partition from the start, showing key/offset
docker exec -it day18-redpanda rpk topic consume orders --partition 0 --offset start --format '%p@%o key=%k %v\n'

# consumer group state + LAG per partition (committed offset vs log-end offset)
docker exec -it day18-redpanda rpk group describe billing
```

`rpk group describe billing` shows `LOG-END-OFFSET`, `CURRENT-OFFSET`, and `LAG` per partition — the single most important operational metric for a Kafka consumer.

> Vanilla Kafka equivalents (if you used `cp-kafka`): `kafka-topics.sh --describe --topic orders`, `kafka-console-consumer.sh --topic orders --partition 0 --from-beginning --property print.key=true`, `kafka-consumer-groups.sh --describe --group billing`.

### Experiment C — at-least-once redelivery (the duplicate)

Temporarily make the consumer **throw before committing** to simulate a crash after processing but before the offset commit:

```java
@KafkaListener(topics = "${app.topic}", groupId = "billing")
public void consume(ConsumerRecord<String, OrderEvent> rec, Acknowledgment ack) {
    OrderEvent e = rec.value();
    System.out.printf("RECV    [p%d@%d] key=%s type=%s%n",
            rec.partition(), rec.offset(), rec.key(), e.type());

    // SIMULATE a crash after the side-effect but before commit:
    if ("PAID".equals(e.type())) {
        throw new RuntimeException("boom! crashing before ack -> offset NOT committed");
    }
    ack.acknowledge();
}
```

With the default error handler, the failed record is retried/redelivered: because the offset was never committed, on the next poll Kafka **re-delivers the same record**. You'll see the same `[p?@?]` offset logged **more than once**. That is at-least-once in action. Stop the loop by reverting the throw (or by making the handler idempotent — Day 7). To prove the "restart" angle instead: comment out `ack.acknowledge()`, run, kill the app mid-stream, restart **without** `--produce`, and watch it reconsume uncommitted records from where the *committed* offset (not the read position) left off.

---

## Expected output (abridged)

Producer (note same key → same partition every time):

```
SENT    key=order-1  type=CREATED  -> partition=2 offset=0
SENT    key=order-2  type=CREATED  -> partition=0 offset=0
SENT    key=order-3  type=CREATED  -> partition=0 offset=1
SENT    key=order-4  type=CREATED  -> partition=1 offset=0
SENT    key=order-5  type=CREATED  -> partition=2 offset=1
SENT    key=order-1  type=PAID     -> partition=2 offset=2   <- order-1 again -> p2
SENT    key=order-1  type=SHIPPED  -> partition=2 offset=4
```

Consumer — within partition 2, `order-1` is CREATED→PAID→SHIPPED in offset order; partition 0 interleaves order-2/order-3 but each keeps its own sequence:

```
RECV    [p2@0] key=order-1  type=CREATED  ...
RECV    [p2@1] key=order-5  type=CREATED  ...
RECV    [p2@2] key=order-1  type=PAID     ...   <- still ordered for order-1
RECV    [p0@0] key=order-2  type=CREATED  ...
RECV    [p0@1] key=order-3  type=CREATED  ...
RECV    [p2@4] key=order-1  type=SHIPPED  ...
```

Two consumers (assignment):

```
[JVM-1] partitions assigned: [orders-0, orders-1]
[JVM-2] partitions assigned: [orders-2]
... kill JVM-2 ...
[JVM-1] partitions assigned: [orders-0, orders-1, orders-2]   <- rebalanced
```

Redelivery (Experiment C) — same offset processed twice:

```
RECV    [p2@2] key=order-1  type=PAID     ... boom!
RECV    [p2@2] key=order-1  type=PAID     ... (redelivered - same offset!)
```

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Log compaction.** Besides time/size retention, a topic can be `cleanup.policy=compact`: Kafka keeps **only the latest value per key**, garbage-collecting older records. This turns a log into a durable, replayable **key-value snapshot** — the mechanism behind Kafka Streams' `KTable`, Connect's offset storage, and "current state from a log." It's a direct bridge to Day 19 Event Sourcing (the full log is your event store; a compacted log is a materialized latest-state view).
- **Exactly-once (EOS) & transactions.** Kafka supports exactly-once *within Kafka* via the **idempotent producer** (`enable.idempotence=true`, which we set — dedups producer retries by producer-id + sequence number) plus **transactions** (`transactional.id`, `KafkaTemplate.executeInTransaction(...)`, and `isolation.level=read_committed` on the consumer). This gives atomic **consume-process-produce** ("read from topic A, write to topic B, commit offset" — all or nothing). But the moment a side-effect leaves Kafka (a SQL UPDATE, an HTTP call), EOS no longer covers you — you're back to at-least-once + application idempotency. This is exactly why Day 7 exists and why Day 20 builds the **Transactional Outbox** to bridge a DB transaction and a Kafka publish atomically.
- **Consumer lag** (`log-end-offset − committed-offset`) is the heartbeat of a streaming system. Rising lag = consumers can't keep up; flat-at-zero = healthy. You can only reduce lag by adding consumers **up to the partition count**, or by speeding up per-record processing. Wire this into the metrics work on Day 25 (Observability) — lag is the SLO you alert on.
- **Partitions cap parallelism — choose the count carefully.** Max useful consumers per group = number of partitions. Too few partitions = a throughput ceiling you can't scale past without repartitioning (which reshuffles keys and breaks ordering during the move). Too many = more files, more rebalancing overhead, more open connections. Rule of thumb: pick the count for your *future* peak parallelism, since increasing it later changes key→partition mapping.
- **Keying is a design decision, not an afterthought.** The key determines both ordering *and* load skew. A hot key (e.g. one whale customer) overloads a single partition while others idle — the partition equivalent of a hot shard. Day 22 (Consistent Hashing) generalizes the placement problem you met here.
- **`acks`, `min.insync.replicas`, and the durability/latency knob.** `acks=all` + `min.insync.replicas=2` on a 3-replica topic survives one broker loss with zero acknowledged-data loss, at the cost of write latency. This is the distributed version of the Day 1 fsync trade-off; production systems tune it per-topic by how much each topic's data is worth.

### Stretch goals
1. **Force a hot partition.** Send 90% of events with the same key and watch `rpk group describe billing` — one partition's lag balloons while others sit at zero. Then switch the key to a higher-cardinality field and observe the lag even out.
2. **Make Experiment C safe.** Add an idempotency table (Day 7): on each record, `INSERT ... ON CONFLICT DO NOTHING` keyed by `(orderId, type, offset)`; prove that redelivery now produces no duplicate side-effect even though the record is consumed twice.
3. **Add a second consumer group** `analytics` (different `group.id`) and confirm it receives a full independent copy of every record with its own offsets — the fan-out pattern underpinning Day 19.
4. **Turn on a real transaction.** Set a `transactional.id`, wrap a produce-then-consume in `executeInTransaction`, set the consumer to `isolation.level=read_committed`, and verify aborted transactions are invisible to the consumer.

### Day 19 teaser
You now have a durable, ordered, replayable log of *events*. Tomorrow — **Event Sourcing** — we stop storing current state and instead make the **append-only stream of events the system of record**, rebuilding state by replaying the log (just like recovering from your Day 1 WAL) and projecting it into read models. The Kafka topic you built today is exactly the kind of event store that makes it possible.
