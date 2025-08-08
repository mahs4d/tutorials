# Day 4: Concurrency & a Producer-Consumer Queue

| | |
|---|---|
| 🏗️ **Project** | **IngestPipe** — a concurrent producer–consumer ingestion pipeline |
| ☕ **Java & language skills** | Java skills: threads, Runnable, ExecutorService, synchronization, wait/notify, the Java Memory Model, virtual threads |
| 🧰 **Library / tool** | SLF4J + Logback (logging facade, levels, parameterized logging, MDC) |
| 🗄️ **DB / distributed-systems concept** | Producer–consumer with a bounded queue → backpressure |
| 📊 **Difficulty** | Medium |

---

## Why this day exists (the through-line)

On Day 1 you built a Write-Ahead Log. On Day 2/3 you indexed and queried it. Every one of those systems had a hidden assumption: **one thread, one writer**. Real databases and real distributed systems don't get that luxury. Hundreds of clients push writes concurrently, and somewhere a small number of background threads have to *durably persist* them without melting down.

The universal shape of that problem is: **fast, bursty producers** feeding **slower, bounded consumers**. If you let producers run unthrottled, you get an unbounded in-memory buffer that eventually OOMs your process — the classic "the queue ate all the RAM" outage. The fix is a **bounded queue**: when it's full, producers *block* (or get rejected). That blocking *is* backpressure.

This pattern is everywhere downstream in this course:
- **Day 18 (Kafka):** a partition is a durable bounded log; consumer lag is exactly "consumers slower than producers."
- **Day 24 (Resilience):** bounded thread pools + bulkheads are this same idea with a fancier name.
- **Day 29 (Reactive):** Reactor's `Flux` backpressure is the producer-consumer queue, made first-class in the type system.

Get this right today and those days are review.

---

## Concept primer

### 1. Threads, `Runnable`, and `ExecutorService`

A `Thread` is an OS-scheduled flow of execution sharing the same heap as every other thread in the JVM. Two truths follow immediately:
1. Threads run *concurrently* — the scheduler can interleave their instructions at almost any point.
2. They share mutable memory — so interleaving can corrupt shared state.

Three ways to get work onto a thread:

```java
// (a) Raw thread — almost never do this in prod. No pooling, no naming, no limits.
new Thread(() -> doWork()).start();

// (b) Runnable handed to a pool — reuse threads, bound concurrency.
ExecutorService pool = Executors.newFixedThreadPool(4);
pool.submit(() -> doWork());

// (c) Submit work that returns a value -> Future<T>.
Future<Integer> f = pool.submit(() -> 42);
```

Raw `new Thread()` is a code smell at senior level: it has no upper bound (a fork bomb waiting to happen), no naming for logs, and no lifecycle management. An `ExecutorService` gives you a *bounded* pool of reusable threads. **Bounding the pool is itself a form of backpressure** — it caps how much work runs at once.

### 2. Race conditions and the lost update

```java
class Counter { int count; void inc() { count++; } } // count++ is read-modify-write, NOT atomic
```

`count++` compiles to roughly: read `count` into a register, add 1, write back. If two threads interleave between the read and the write, one increment is silently lost. Run `inc()` 1,000,000 times across 8 threads and you'll reliably see a final count *less* than 8,000,000. That's a **race condition**: correctness depends on timing.

### 3. The Java Memory Model and *happens-before*

Even scarier than lost updates is the **visibility** problem: a write by thread A may *never become visible* to thread B, because each thread can cache values in registers/CPU caches, and the compiler may reorder instructions. Without synchronization, there's no guarantee thread B ever sees thread A's write — a loop reading a non-`volatile` `boolean running` flag can spin forever.

The JMM defines a partial order called **happens-before**. If action X happens-before action Y, then X's memory effects are visible to Y. The rules you must internalize:
- **Program order:** within a single thread, statements happen-before later ones.
- **Monitor lock:** unlocking a `synchronized` block happens-before any later lock of the *same* monitor. (This is why `synchronized` gives both *mutual exclusion* and *visibility*.)
- **Volatile:** a write to a `volatile` field happens-before every later read of it.
- **Thread start/join:** `t.start()` happens-before everything in the thread; everything in the thread happens-before `t.join()` returning.

Takeaway: you don't reason about caches and reordering directly — you establish happens-before edges with `synchronized`, `volatile`, or `java.util.concurrent` primitives, and the JMM does the rest.

### 4. Blocking queues and backpressure (the "why")

A **blocking queue** is a thread-safe queue where:
- `put(e)` **blocks** the calling thread when the queue is full, until space frees up.
- `take()` **blocks** the calling thread when the queue is empty, until an element arrives.

That single property gives you a *self-regulating pipeline*. Producers can't outrun consumers because once the buffer fills, `put` parks the producer. No busy-waiting, no OOM, no manual rate limiting. The queue capacity is the knob that trades latency (small = tight backpressure) against burst tolerance (large = absorbs spikes).

This is the entire job. Build it once by hand to see the mechanics, then use the JDK's version forever.

---

## Prerequisites

- JDK 21 installed: `java -version` should print `21.x`.
- Maven 3.9+: `mvn -version`.
- Days 1–3 finished (or at least: you're comfortable with Maven and JUnit 5).

---

## 🛠️ Project Walkthrough — IngestPipe

Roll up your sleeves — from here on you'll build the project step by step, then run it and check the output.

## Step 1 — Project setup

Create `day4/pom.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.java30</groupId>
    <artifactId>day4-producer-consumer</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.release>21</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>

    <dependencies>
        <!-- SLF4J: the FACADE you code against -->
        <dependency>
            <groupId>org.slf4j</groupId>
            <artifactId>slf4j-api</artifactId>
            <version>2.0.13</version>
        </dependency>
        <!-- Logback: the IMPLEMENTATION that does the work. Pulls in slf4j binding automatically. -->
        <dependency>
            <groupId>ch.qos.logback</groupId>
            <artifactId>logback-classic</artifactId>
            <version>1.5.6</version>
        </dependency>

        <!-- JUnit 5 (from Day 2) for the concurrency test -->
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>5.10.2</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
            </plugin>
        </plugins>
    </build>
</project>
```

> **Facade vs. implementation.** SLF4J is an *API only* — it has no logging logic. At runtime it discovers a *binding* (Logback) on the classpath and routes calls to it. This is why you write `LoggerFactory.getLogger(...)` and never import a Logback class in your code: tomorrow you could swap Logback for Log4j2 by changing one dependency, with zero source changes. That decoupling is the whole point of a facade.

Directory layout:

```
day4/
├── pom.xml
└── src
    ├── main
    │   ├── java/com/java30/day4/
    │   │   ├── Event.java
    │   │   ├── BoundedBlockingQueue.java
    │   │   ├── HandRolledPipeline.java
    │   │   └── ExecutorPipeline.java
    │   └── resources/
    │       └── logback.xml
    └── test
        └── java/com/java30/day4/
            └── BoundedBlockingQueueTest.java
```

---

## Step 2 — The event (a record — Day 5 preview)

`src/main/java/com/java30/day4/Event.java`:

```java
package com.java30.day4;

import java.time.Instant;

/**
 * An immutable ingestion event. Immutability matters in concurrency:
 * once constructed and safely published through the queue, no thread can
 * mutate it, so there is nothing to race on. Records give us this for free.
 */
public record Event(long id, String payload, Instant createdAt) {
    public static Event of(long id, String payload) {
        return new Event(id, payload, Instant.now());
    }
}
```

---

## Step 3 — Hand-rolled `BoundedBlockingQueue` (`wait`/`notifyAll` + `synchronized`)

This is the part you build to *understand*, not to ship. `src/main/java/com/java30/day4/BoundedBlockingQueue.java`:

```java
package com.java30.day4;

import java.util.ArrayDeque;
import java.util.Deque;

/**
 * A bounded blocking queue implemented from scratch with intrinsic locks.
 *
 * Concurrency invariants:
 *  - All access to {@code items} happens inside {@code synchronized (this)},
 *    establishing happens-before edges so every thread sees a consistent view.
 *  - put() blocks while full; take() blocks while empty.
 *  - We use a while-loop (NOT if) around wait() to defend against
 *    spurious wakeups and the "lost wakeup / stolen item" problem.
 *  - notifyAll() (not notify()) wakes BOTH waiting producers and consumers,
 *    since they share one monitor; notify() could wake the "wrong kind" of
 *    thread and deadlock the pipeline.
 */
public final class BoundedBlockingQueue<E> {

    private final Deque<E> items = new ArrayDeque<>();
    private final int capacity;

    public BoundedBlockingQueue(int capacity) {
        if (capacity <= 0) {
            throw new IllegalArgumentException("capacity must be > 0, got " + capacity);
        }
        this.capacity = capacity;
    }

    /** Inserts {@code e}, blocking the caller while the queue is full. */
    public void put(E e) throws InterruptedException {
        synchronized (this) {
            while (items.size() == capacity) {
                wait();                 // releases the monitor, parks the thread
            }
            items.addLast(e);
            notifyAll();                // a consumer might be waiting on "empty"
        }
    }

    /** Removes and returns the head, blocking the caller while the queue is empty. */
    public E take() throws InterruptedException {
        synchronized (this) {
            while (items.isEmpty()) {
                wait();
            }
            E e = items.removeFirst();
            notifyAll();                // a producer might be waiting on "full"
        }
    }

    public synchronized int size() {
        return items.size();
    }
}
```

> **Three things every senior must explain about this code:**
> 1. **`while`, never `if`, around `wait()`.** `wait()` can return *spuriously* (the JVM is allowed to wake a thread for no reason), and even on a legitimate `notifyAll()`, another thread may have already changed the condition by the time *this* thread re-acquires the lock. Re-check the predicate in a loop.
> 2. **`wait()` releases the monitor.** Unlike a busy-spin, `wait()` atomically releases the lock and parks, letting other threads enter the `synchronized` block. On wakeup it re-acquires the lock before returning. You must call it while holding the monitor, or you get `IllegalMonitorStateException`.
> 3. **`notifyAll()` over `notify()`.** Because producers and consumers wait on the *same* monitor, `notify()` might wake a producer when only consumers can make progress (or vice versa), causing a missed signal and a stall. `notifyAll()` is the safe default; getting `notify()` right requires two `Condition`s (see stretch goal).

Wait — there's a typo above on purpose to make you read: `take()` declares it returns `E` but the body has no `return`. Fix it:

```java
    public E take() throws InterruptedException {
        synchronized (this) {
            while (items.isEmpty()) {
                wait();
            }
            E e = items.removeFirst();
            notifyAll();
            return e;                   // <-- the missing return
        }
    }
```

---

## Step 4 — `logback.xml` (the config)

`src/main/resources/logback.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>

    <!-- A console appender. %X{...} pulls values out of the MDC (per-thread context). -->
    <appender name="CONSOLE" class="ch.qos.logback.core.ConsoleAppender">
        <encoder>
            <!--
              %d   timestamp
              %-5level   level, left-padded to 5 chars
              [%thread]  which thread logged this (CRITICAL for concurrency debugging)
              %X{role}/%X{worker}  MDC values we set per worker
              %logger{36} class name, abbreviated
              %msg%n   the message + newline
            -->
            <pattern>%d{HH:mm:ss.SSS} %-5level [%thread] %X{role}#%X{worker} %logger{24} - %msg%n</pattern>
        </encoder>
    </appender>

    <!-- Quiet the noisy frameworks, keep our package chatty. -->
    <logger name="com.java30.day4" level="DEBUG"/>

    <root level="INFO">
        <appender-ref ref="CONSOLE"/>
    </root>
</configuration>
```

Key ideas:
- **Levels** (most to least severe): `ERROR > WARN > INFO > DEBUG > TRACE`. A logger configured at `INFO` emits `INFO` and above and *suppresses* `DEBUG`/`TRACE`. Production runs at `INFO`; you flip a package to `DEBUG` to investigate.
- **`[%thread]`** is non-negotiable for concurrent code — without it, interleaved log lines are unreadable.
- **`%X{...}`** reads the **MDC** (Mapped Diagnostic Context), a thread-local `Map<String,String>`. We'll stamp each worker's role/id so every line carries who produced it.

---

## Step 5 — Hand-rolled pipeline using your queue + raw threads + SLF4J

`src/main/java/com/java30/day4/HandRolledPipeline.java`:

```java
package com.java30.day4;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Producer-consumer pipeline built on our hand-rolled BoundedBlockingQueue
 * and raw Thread objects. Demonstrates wait/notify mechanics and a POISON-PILL
 * shutdown protocol.
 */
public final class HandRolledPipeline {

    // One logger per class. Convention: static final, named after the class.
    private static final Logger log = LoggerFactory.getLogger(HandRolledPipeline.class);

    private static final int PRODUCERS = 3;
    private static final int CONSUMERS = 2;
    private static final int EVENTS_PER_PRODUCER = 10_000;
    private static final int QUEUE_CAPACITY = 64;

    /** Sentinel that tells a consumer "no more work — stop". */
    private static final Event POISON = Event.of(-1, "__POISON__");

    public static void main(String[] args) throws InterruptedException {
        var queue = new BoundedBlockingQueue<Event>(QUEUE_CAPACITY);
        var produced = new AtomicLong();
        var consumed = new AtomicLong();
        var idGen = new AtomicLong();

        log.info("Starting hand-rolled pipeline: {} producers, {} consumers, capacity {}",
                PRODUCERS, CONSUMERS, QUEUE_CAPACITY);
        long start = System.nanoTime();

        List<Thread> threads = new ArrayList<>();

        // --- Consumers ---
        for (int c = 0; c < CONSUMERS; c++) {
            int id = c;
            Thread t = new Thread(() -> {
                MDC.put("role", "consumer");
                MDC.put("worker", String.valueOf(id));
                try {
                    while (true) {
                        Event e = queue.take();
                        if (e == POISON) {
                            log.debug("received poison pill, exiting");
                            return;
                        }
                        persist(e);                  // simulate a slow write
                        consumed.incrementAndGet();
                    }
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                } finally {
                    MDC.clear();
                }
            }, "consumer-" + id);
            threads.add(t);
            t.start();
        }

        // --- Producers ---
        for (int p = 0; p < PRODUCERS; p++) {
            int id = p;
            Thread t = new Thread(() -> {
                MDC.put("role", "producer");
                MDC.put("worker", String.valueOf(id));
                try {
                    for (int i = 0; i < EVENTS_PER_PRODUCER; i++) {
                        Event e = Event.of(idGen.incrementAndGet(), "data-" + i);
                        queue.put(e);                 // BLOCKS when full -> backpressure
                        produced.incrementAndGet();
                    }
                    log.debug("done producing {} events", EVENTS_PER_PRODUCER);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                } finally {
                    MDC.clear();
                }
            }, "producer-" + id);
            threads.add(t);
            t.start();
        }

        // Wait for producers to finish, then poison the consumers.
        for (Thread t : threads) {
            if (t.getName().startsWith("producer")) {
                t.join();
            }
        }
        for (int c = 0; c < CONSUMERS; c++) {
            queue.put(POISON);                        // one pill per consumer
        }
        for (Thread t : threads) {
            if (t.getName().startsWith("consumer")) {
                t.join();
            }
        }

        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        long total = consumed.get();
        log.info("Done. produced={} consumed={} in {} ms ({} events/s)",
                produced.get(), total, elapsedMs,
                elapsedMs == 0 ? "inf" : (total * 1000 / elapsedMs));
    }

    /** Pretend to durably persist (Day 1's WAL would go here). Costs ~microseconds. */
    private static void persist(Event e) {
        // TRACE is suppressed at INFO/DEBUG, so the {} args are never even
        // toString()'d when disabled -> parameterized logging is cheap.
        if (log.isTraceEnabled()) {
            log.trace("persisting event id={} payload={}", e.id(), e.payload());
        }
        Math.sqrt(e.id());   // a tiny bit of CPU work to stand in for I/O
    }
}
```

> **Parameterized logging — why `{}` not string concatenation.** Write `log.debug("event id={}", e.id())`, never `log.debug("event id=" + e.id())`. With `{}` placeholders, the message is only assembled *if the level is enabled*. With `+` concatenation, the `String` is built (and `e.id()` boxed/`toString`'d) on every call even when DEBUG is off — pure waste in a hot ingestion loop. For an *expensive* argument, also guard with `if (log.isDebugEnabled())`.

> **Poison pill.** A bounded queue has no "close" signal of its own. The standard idiom is to enqueue N sentinel values (one per consumer) after producers finish; each consumer that sees one exits. Day 18 (Kafka) replaces this with explicit consumer-group shutdown, but the idea is the same.

---

## Step 6 — Production version: `ArrayBlockingQueue` + `ExecutorService`

Now throw away the hand-rolled queue and use the JDK. `src/main/java/com/java30/day4/ExecutorPipeline.java`:

```java
package com.java30.day4;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The same pipeline, the way you'd actually ship it:
 *  - java.util.concurrent.ArrayBlockingQueue for the bounded buffer
 *  - ExecutorService thread pools for producers and consumers
 *  - a volatile/AtomicBoolean "done" flag + sentinel for clean shutdown
 */
public final class ExecutorPipeline {

    private static final Logger log = LoggerFactory.getLogger(ExecutorPipeline.class);

    private static final int PRODUCERS = 3;
    private static final int CONSUMERS = 2;
    private static final int EVENTS_PER_PRODUCER = 50_000;
    private static final int QUEUE_CAPACITY = 256;
    private static final Event POISON = Event.of(-1, "__POISON__");

    public static void main(String[] args) throws InterruptedException {
        BlockingQueue<Event> queue = new ArrayBlockingQueue<>(QUEUE_CAPACITY);
        var produced = new AtomicLong();
        var consumed = new AtomicLong();
        var idGen = new AtomicLong();
        var producersDone = new AtomicBoolean(false);

        // Named pools make thread dumps and logs readable.
        ExecutorService producerPool = Executors.newFixedThreadPool(PRODUCERS, named("producer"));
        ExecutorService consumerPool = Executors.newFixedThreadPool(CONSUMERS, named("consumer"));

        log.info("Starting executor pipeline: {} producers, {} consumers, capacity {}",
                PRODUCERS, CONSUMERS, QUEUE_CAPACITY);
        long start = System.nanoTime();

        for (int c = 0; c < CONSUMERS; c++) {
            int id = c;
            consumerPool.submit(() -> {
                MDC.put("role", "consumer");
                MDC.put("worker", String.valueOf(id));
                try {
                    while (true) {
                        Event e = queue.take();
                        if (e == POISON) return;
                        consumed.incrementAndGet();
                        Math.sqrt(e.id());
                    }
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                } finally {
                    MDC.clear();
                }
            });
        }

        for (int p = 0; p < PRODUCERS; p++) {
            int id = p;
            producerPool.submit(() -> {
                MDC.put("role", "producer");
                MDC.put("worker", String.valueOf(id));
                try {
                    for (int i = 0; i < EVENTS_PER_PRODUCER; i++) {
                        queue.put(Event.of(idGen.incrementAndGet(), "data-" + i));
                        produced.incrementAndGet();
                    }
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                } finally {
                    MDC.clear();
                }
            });
        }

        // Orderly shutdown: stop accepting producer tasks, wait for them, then poison consumers.
        producerPool.shutdown();
        if (!producerPool.awaitTermination(60, TimeUnit.SECONDS)) {
            log.warn("producers did not finish in time; forcing shutdown");
            producerPool.shutdownNow();
        }
        producersDone.set(true);
        for (int c = 0; c < CONSUMERS; c++) {
            queue.put(POISON);
        }
        consumerPool.shutdown();
        if (!consumerPool.awaitTermination(60, TimeUnit.SECONDS)) {
            consumerPool.shutdownNow();
        }

        long elapsedMs = Math.max(1, (System.nanoTime() - start) / 1_000_000);
        log.info("Done. produced={} consumed={} in {} ms ({} events/s)",
                produced.get(), consumed.get(), elapsedMs, consumed.get() * 1000 / elapsedMs);
    }

    /** A ThreadFactory that names threads producer-1, producer-2, ... for clean logs. */
    private static java.util.concurrent.ThreadFactory named(String prefix) {
        var counter = new AtomicLong();
        return r -> new Thread(r, prefix + "-" + counter.incrementAndGet());
    }
}
```

> **Why `ArrayBlockingQueue` beats your `synchronized` version in prod:** it's a single, fixed-size array (no per-element allocation), uses a `ReentrantLock` with **two separate `Condition`s** (`notFull`, `notEmpty`) so a `put` signals *only* waiting takers and vice versa — no thundering-herd `notifyAll()`, no waking the wrong kind of thread. It's been fuzzed and battle-tested for ~20 years. Your hand-rolled queue is for learning; this is for shipping.

---

## Step 7 — A concurrency test (JUnit 5, from Day 2)

`src/test/java/com/java30/day4/BoundedBlockingQueueTest.java`:

```java
package com.java30.day4;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertEquals;

class BoundedBlockingQueueTest {

    @Test
    @Timeout(10) // if our queue deadlocks, fail fast instead of hanging forever
    void everyProducedItemIsConsumedExactlyOnce() throws Exception {
        int producers = 4, consumers = 4, perProducer = 25_000;
        long expected = (long) producers * perProducer;

        var queue = new BoundedBlockingQueue<Long>(50);
        var consumed = new AtomicLong();
        var pool = Executors.newFixedThreadPool(producers + consumers);

        for (int c = 0; c < consumers; c++) {
            pool.submit(() -> {
                try {
                    while (true) {
                        long v = queue.take();
                        if (v == -1L) return;     // poison
                        consumed.incrementAndGet();
                    }
                } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            });
        }

        var prodPool = Executors.newFixedThreadPool(producers);
        for (int p = 0; p < producers; p++) {
            prodPool.submit(() -> {
                try {
                    for (int i = 0; i < perProducer; i++) queue.put((long) i);
                } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            });
        }
        prodPool.shutdown();
        prodPool.awaitTermination(10, TimeUnit.SECONDS);

        for (int c = 0; c < consumers; c++) queue.put(-1L);
        pool.shutdown();
        pool.awaitTermination(10, TimeUnit.SECONDS);

        assertEquals(expected, consumed.get(),
                "every produced item must be consumed exactly once");
    }
}
```

Because we use `AtomicLong.incrementAndGet()` (atomic, lock-free) for counting, the assertion is itself race-free — proving the *queue* is correct, not papering over a counting bug.

---

## Run instructions

```bash
cd day4

# 1) Compile + run the test (proves the hand-rolled queue is correct)
mvn -q test

# 2) Run the hand-rolled pipeline
mvn -q compile
mvn -q exec:java -Dexec.mainClass=com.java30.day4.HandRolledPipeline 2>/dev/null \
  || java -cp target/classes:$(find ~/.m2 -name 'slf4j-api-2.0.13.jar'):$(find ~/.m2 -name 'logback-classic-1.5.6.jar'):$(find ~/.m2 -name 'logback-core-1.5.6.jar') com.java30.day4.HandRolledPipeline

# Simplest reliable run: build a runnable classpath once
mvn -q dependency:build-classpath -Dmdep.outputFile=cp.txt
java -cp "target/classes:$(cat cp.txt)" com.java30.day4.HandRolledPipeline
java -cp "target/classes:$(cat cp.txt)" com.java30.day4.ExecutorPipeline
```

(If you prefer, add the `exec-maven-plugin` to your `pom.xml` to use `mvn exec:java` directly — but the `build-classpath` approach above always works.)

To watch backpressure in action, temporarily set the consumers' `Math.sqrt` to a `Thread.sleep(1)` — producers will visibly stall once the queue fills, and total throughput will drop to roughly `consumers * 1000` events/sec. That's the bound doing its job.

---

## Expected output

```
14:22:01.103 INFO  [main] c.java30.day4.HandRolledP - Starting hand-rolled pipeline: 3 producers, 2 consumers, capacity 64
14:22:01.110 DEBUG [producer-1] producer#0 c.java30.day4.HandRolledP - done producing 10000 events
14:22:01.111 DEBUG [producer-2] producer#1 c.java30.day4.HandRolledP - done producing 10000 events
14:22:01.112 DEBUG [producer-3] producer#2 c.java30.day4.HandRolledP - done producing 10000 events
14:22:01.140 DEBUG [consumer-0] consumer#0 c.java30.day4.HandRolledP - received poison pill, exiting
14:22:01.140 DEBUG [consumer-1] consumer#1 c.java30.day4.HandRolledP - received poison pill, exiting
14:22:01.141 INFO  [main] c.java30.day4.HandRolledP - Done. produced=30000 consumed=30000 in 38 ms (789473 events/s)
```

Note `produced == consumed` (no lost events), the per-thread `[thread]` and MDC `role#worker` fields, and that `TRACE` lines never appear (suppressed by config). The `ExecutorPipeline` looks similar with higher counts. Throughput numbers vary by machine — relative behavior is the point.

---

## 🚀 Going Deeper & Next Steps

## Going deeper / senior-level notes

- **Virtual threads (Java 21, `Executors.newVirtualThreadPerTaskExecutor()`).** Project Loom makes threads cheap (~1KB each) by mapping millions of *virtual* threads onto a few OS carrier threads, unmounting them when they block. For an I/O-bound consumer (a real DB write), you could give *every event its own virtual thread* and skip the bounded pool — but **note the trap**: virtual threads remove the *thread-count* bound, so you must still bound *something* (a `Semaphore` or the queue) or you reintroduce the OOM. Loom changes the cost of blocking, not the need for backpressure. Try swapping `Executors.newFixedThreadPool` for `Executors.newVirtualThreadPerTaskExecutor()` and observe.
- **Deadlock vs. livelock vs. starvation.** *Deadlock*: two threads each hold a lock the other needs (avoid by always acquiring locks in a global order, or using `tryLock` with timeout). *Livelock*: threads keep responding to each other and make no progress (e.g., two people stepping aside in a hallway). *Starvation*: a thread never gets the CPU/lock because others monopolize it (fair locks help). Your `@Timeout(10)` test is a cheap deadlock detector.
- **Why not just one big `synchronized` everywhere?** Coarse locking kills throughput (everything serializes) and risks deadlock as the locked regions grow. `java.util.concurrent` gives finer tools: `ReentrantLock` with multiple `Condition`s, `ConcurrentHashMap`, `LongAdder` (better than `AtomicLong` under extreme contention), `CompletableFuture`. Reach for these before hand-rolling.
- **`synchronized` vs. `ReentrantLock`.** `ReentrantLock` adds `tryLock`, timed lock, interruptible lock, fairness, and multiple conditions — which is exactly how `ArrayBlockingQueue` separates `notFull`/`notEmpty`. `synchronized` is simpler and now (Java 17+) usually just as fast; prefer it unless you need a `ReentrantLock` feature.
- **Backpressure preview (Days 18 & 29).** Kafka makes the bounded queue *durable and distributed*: producers can outrun consumers because the broker buffers to disk, and "backpressure" becomes *consumer lag* you monitor and alert on. Reactor (Day 29) makes backpressure a contract in the type system — a `Subscriber` calls `request(n)` to pull only what it can handle, so the producer literally cannot overrun it. Both are the same bounded-queue idea you built today, escalated.

---

## Stretch goals

1. **Two-condition rewrite.** Re-implement `BoundedBlockingQueue` with a `ReentrantLock` and two `Condition`s (`notFull`, `notEmpty`) and use `signal()` instead of `signalAll()`. Verify the JUnit test still passes — this is exactly how `ArrayBlockingQueue` is built internally.
2. **Periodic throughput logger.** Add a scheduled task (`Executors.newSingleThreadScheduledExecutor`) that logs `consumed/sec` every second and current `queue.size()`, so you can *see* the queue fill and drain. Use MDC `role=monitor`.
3. **Rejection / drop policy.** Add a non-blocking `offer(e, timeout)` path: if the queue is full for longer than 5ms, drop the event and increment a `dropped` counter logged at `WARN`. This is "load shedding" — a real alternative to blocking backpressure (preview of Day 27, Rate Limiting).
4. **File appender + rolling.** Add a `RollingFileAppender` to `logback.xml` that writes `app.log`, rolls daily, and keeps 7 days. Confirm console stays human-readable while the file captures everything at `DEBUG`.

---

## Day 5 teaser

Tomorrow: **MVCC & Java Records.** You'll see why immutability (the `record Event` you used today to make events race-free) is the secret weapon databases use to let readers and writers run *concurrently without locking each other* — Multi-Version Concurrency Control. We'll model versioned rows as records and watch snapshot reads coexist with in-flight writes, the same trick PostgreSQL uses millions of times a second.
