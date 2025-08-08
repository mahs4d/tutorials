# Day 7: Error Handling, Optional & Idempotency

| | |
|---|---|
| 🏗️ **Project** | **SafeCommit** — an idempotent command/payment processor |
| ☕ **Java & language skills** | Checked vs unchecked exceptions, exception design, `Optional`, `Result` types, try-with-resources |
| 🧰 **Library / tool** | Gradle (build.gradle.kts, wrapper, tasks) — the Maven alternative |
| 🗄️ **DB / distributed-systems concept** | Idempotency & delivery semantics (at-least/at-most/exactly-once) |
| 📊 **Difficulty** | Easy |

> **Continuity note.** Day 1 you built a WAL with **Maven** — `pom.xml`, `mvn package`, the dependency coordinate triple. Today is the *foundation week finale*: same kind of plain-Java project, but driven by **Gradle**, plus the error-handling and idempotency muscle you'll lean on for the rest of the course. The dedup store you build here is a baby version of the **transactional outbox** (Day 20) and underpins **idempotent HTTP** (Day 10). The retry logic foreshadows **resilience** (Day 24). From Day 8 on we enter Spring — but every Spring `@Service` is still just the plain Java you're writing today.

---

## Concept primer

### 1. What is idempotency?

An operation is **idempotent** if applying it *N* times has the same observable effect as applying it *once* (for N ≥ 1).

```
charge(card, $10)        -> NOT idempotent: call twice, customer charged $20
chargeWithKey("abc",$10) -> idempotent: call twice with key "abc", customer charged $10 ONCE
setBalance($10)          -> idempotent by nature: assigning is naturally repeatable
DELETE /order/42         -> idempotent: deleting an already-deleted thing is still "deleted"
incrementBy(1)           -> NOT idempotent: each call moves the state
```

Note the distinction:
- Some operations are **naturally idempotent** (`PUT`, `DELETE`, absolute assignment).
- Some are **inherently non-idempotent** (charge money, send email, increment) and must be **made idempotent** by attaching an **idempotency key** and remembering what you've already done.

### 2. Why idempotency is THE distributed-systems primitive

In a single process, you call a method and you *know* it ran. Across a network you do **not** have that luxury. Consider a client calling a payment service:

```
Client ----charge---->  Server   (server applies the charge, commits)
Client  <--X--  ack lost (timeout / connection reset / GC pause)
Client ----charge---->  Server   (RETRY — but was the first one applied?!)
```

The client cannot tell the difference between "the request never arrived" and "the request arrived, succeeded, but the *acknowledgement* was lost." Both look identical: a timeout. Its only safe move is to **retry**. But a naive retry double-charges the customer.

This is not an edge case — it is the *normal* condition of distributed systems. Networks drop packets, load balancers reset connections, message brokers (Kafka, Day 18) redeliver, clients have aggressive timeouts. The universal answer is:

> **Make the operation idempotent, give every logical request a stable key, and retry freely.**

Idempotency turns "did it happen?" (unanswerable) into "make it have happened exactly once" (achievable). It is the foundation under retries, message queues, the outbox pattern, and exactly-once processing.

### 3. Delivery semantics

When one component sends a message/request to another, you get one of three guarantees:

| Semantic | Guarantee | Failure mode | Where you see it |
|---|---|---|---|
| **At-most-once** | Delivered 0 or 1 times | Can **lose** messages (no retry) | Fire-and-forget metrics, UDP |
| **At-least-once** | Delivered 1+ times | Can **duplicate** messages (retries) | Kafka default, SQS, most real systems |
| **Exactly-once** | Delivered exactly 1 time | The holy grail | Marketing slides |

**Key insight:** at-most-once and at-least-once are a trade between *losing* data and *duplicating* data. Almost every serious system chooses **at-least-once** — because duplicates are *fixable* (with idempotency) while loss usually is not.

### 4. The "exactly-once" myth

You cannot get exactly-once **delivery** over an unreliable network — it's provably impossible (a variant of the Two Generals problem; we hit this again at 2PC on Day 17). What you *can* get is **exactly-once processing / effectively-once**:

> at-least-once delivery  +  idempotent consumer  =  effectively-once

The duplicates still *arrive*; you just **dedup** them so the *effect* happens once. Kafka's "exactly-once semantics" is exactly this — idempotent producers + transactional offsets — not magic delivery.

### 5. The idempotency key

An **idempotency key** is a client-generated, unique-per-logical-operation token (often a UUID) sent with the request. The server keeps a **dedup store** mapping `key -> stored result`:

- **First time** it sees a key: execute the operation, store `(key -> result)`, return the result.
- **Subsequent times** it sees the same key: skip execution, return the **stored** result.

Two subtle but critical rules a senior engineer must internalize:
1. **The key scopes a logical request, not a retry.** A retry reuses the same key; a genuinely new operation gets a new key. The *client* owns key generation.
2. **You must store the result, not just a "done" flag** — so that retries get the identical response, including any generated IDs.

We'll also see two failure subtleties later: the **in-flight race** (two requests with the same key arrive concurrently) and the **dedup window** (you can't remember keys forever).

### 6. Java error handling — the senior view

Idempotency and error handling are joined at the hip: you can only retry safely if you can classify *why* something failed. Java gives you several tools; the skill is picking the right one.

**Checked vs unchecked exceptions**
- `Exception` (checked) — compiler forces you to `catch` or `throws`. Intended for *recoverable* conditions the caller should handle.
- `RuntimeException` (unchecked) — not enforced. Intended for *programming errors* (bugs): `NullPointerException`, `IllegalArgumentException`, `IllegalStateException`.
- **Modern guidance:** checked exceptions don't compose well with lambdas/streams (Day 3) and tend to leak implementation detail. Many senior codebases lean toward unchecked exceptions for truly unexpected failures, and use **return types** (`Optional`, `Result`) for *expected* outcomes. Reserve checked exceptions for boundaries where the caller genuinely must decide.

**Three ways to signal "no value / failure" — pick deliberately:**

| Tool | Use it for | Don't use it for |
|---|---|---|
| **Exception** | Truly exceptional / unexpected failures; programmer errors | Expected, frequent control flow (exceptions are expensive + non-local) |
| **`Optional<T>`** | Legitimate *absence* of a value ("not found") | Carrying *why* something failed; never for fields/params |
| **`Result<T>` / sealed type** | Expected failures where the *reason* matters and the caller must branch | Bugs you can't recover from |

**Rules of thumb that signal seniority:**
- `Optional` answers **"is there a value?"** It does **not** answer "why not?". If the caller needs the reason, return a `Result`.
- Never `optional.get()` without checking — use `orElse`, `orElseThrow`, `map`, `ifPresentOrElse`.
- Never use `Optional` for method parameters or class fields. It's a *return* type.
- **`try-with-resources`** for anything `AutoCloseable` (files, sockets, DB connections — Day 9 pooling) so cleanup is deterministic even on exception.
- Design a small **exception hierarchy** rooted at one app exception, so callers can catch broadly or narrowly.
- **Fail fast** on bugs (throw), **return** on expected outcomes (Optional/Result).

---

## Prerequisites & Gradle setup

You need **JDK 21**. Check:

```bash
java -version   # should report 21.x
```

### Install Gradle (you actually won't need to)

The professional way is to **never install Gradle globally**. Instead you commit the **Gradle wrapper** (`gradlew`) — a tiny script + jar that downloads and runs the exact Gradle version the project pins. This guarantees everyone (and CI) builds with the *same* Gradle, just like Maven's wrapper (`mvnw`).

If you have Gradle once (via [SDKMAN](https://sdkman.io/): `sdk install gradle 8.10`) you can bootstrap the wrapper:

```bash
gradle wrapper --gradle-version 8.10
```

After that, you only ever type `./gradlew` (or `gradlew.bat` on Windows). If you don't have Gradle at all, the project structure below plus the committed wrapper files is all you need — and we'll show how to generate them.

### Maven ↔ Gradle mental model (read this carefully)

| Concept | Maven (Day 1) | Gradle (today) |
|---|---|---|
| Build file | `pom.xml` (XML, declarative) | `build.gradle.kts` (Kotlin DSL, programmatic) |
| Wrapper | `mvnw` / `.mvn/` | `gradlew` / `gradle/wrapper/` |
| Dependency coordinate | `<groupId>:<artifactId>:<version>` | `"group:name:version"` (same triple!) |
| Compile dependency | `<scope>compile</scope>` | `implementation(...)` |
| Test-only dependency | `<scope>test</scope>` | `testImplementation(...)` |
| Build lifecycle | fixed phases: `validate→compile→test→package→install` | a **DAG of tasks**; you wire/extend them |
| Build the jar | `mvn package` | `./gradlew build` |
| Run tests | `mvn test` | `./gradlew test` |
| Clean | `mvn clean` | `./gradlew clean` |
| Repositories | `<repositories>` (Central by default) | `repositories { mavenCentral() }` |
| Output dir | `target/` | `build/` |

**The one big difference to hold in your head:** Maven is *declarative phases* (you fit into a fixed lifecycle); Gradle is *programmable tasks* (a real Kotlin/Groovy program that builds a task graph, with caching and incremental builds). Maven is more uniform; Gradle is more flexible and faster on big projects. Both pull the same dependencies from the same Maven Central.

---

## Project: an idempotent payment/command processor

We'll build a `PaymentProcessor` that:
1. Accepts a `PaymentCommand` carrying an **idempotency key**.
2. Uses a **dedup store** so replaying the same key returns the **original result** instead of charging twice.
3. Models failures (declines, transient glitches) using a **`Result` type** and `Optional`.
4. Simulates **retries** over a flaky channel and proves the customer is charged once.

### Project layout

```
day7/
├── settings.gradle.kts
├── build.gradle.kts
├── gradlew                      (wrapper script — committed)
├── gradlew.bat
├── gradle/wrapper/
│   ├── gradle-wrapper.jar
│   └── gradle-wrapper.properties
└── src/
    ├── main/java/dev/day7/
    │   ├── App.java
    │   ├── Result.java
    │   ├── PaymentCommand.java
    │   ├── Receipt.java
    │   ├── PaymentError.java
    │   ├── PaymentException.java
    │   ├── IdempotencyStore.java
    │   ├── InMemoryIdempotencyStore.java
    │   ├── PaymentGateway.java
    │   └── PaymentProcessor.java
    └── test/java/dev/day7/
        └── PaymentProcessorTest.java
```

---

## 🛠️ Project Walkthrough — SafeCommit

Roll up your sleeves: build the idempotent payment processor step by step, then run it and check the output against what's expected below.

### Step 1 — `settings.gradle.kts`

This is Gradle's project-naming/aggregation file (no Maven equivalent — Maven uses the `pom.xml` `<artifactId>`).

```kotlin
// settings.gradle.kts
rootProject.name = "day7-idempotent-payments"
```

### Step 2 — `build.gradle.kts`

The Gradle counterpart to Day 1's `pom.xml`. Read every line against the Maven↔Gradle table above.

```kotlin
// build.gradle.kts

plugins {
    application                 // gives us a `run` task + main-class wiring
    java                        // compile/test/jar tasks for a Java project
}

group = "dev.day7"
version = "1.0.0"

repositories {
    mavenCentral()              // same Central as Maven (Day 1)
}

java {
    toolchain {
        // Gradle will locate/download a JDK 21 — reproducible builds across machines
        languageVersion = JavaLanguageVersion.of(21)
    }
}

dependencies {
    // JUnit 5 — note the coordinate triple, identical idea to Maven's GAV
    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
}

application {
    mainClass = "dev.day7.App"  // ./gradlew run will launch this
}

tasks.test {
    useJUnitPlatform()          // tell Gradle to run JUnit 5
    testLogging {
        events("passed", "skipped", "failed")
    }
}

// A custom task — proving Gradle is "programmable tasks", not fixed phases.
tasks.register("hello") {
    group = "day7"
    description = "Sanity check that Gradle is wired up."
    doLast { println("Gradle is working. Run `./gradlew run` next.") }
}
```

> Try `./gradlew tasks` after this and you'll see your `hello` task under a "day7" group, alongside built-ins like `build`, `test`, `run`. That's the task DAG Maven doesn't expose.

### Step 3 — generate the wrapper

If you have any Gradle available:

```bash
cd day7
gradle wrapper --gradle-version 8.10
```

This writes `gradlew`, `gradlew.bat`, and `gradle/wrapper/` (pinning Gradle 8.10 in `gradle-wrapper.properties`). **Commit these.** From now on always use `./gradlew`. If you have *no* Gradle at all, install once via SDKMAN as shown above, then run the wrapper command — you never touch a global Gradle again.

### Step 4 — the `Result` type (expected failure without exceptions)

A sealed `Result<T>` makes success/failure explicit in the type system and forces the caller to handle both via pattern-matching `switch` (Java 21). This is the idiom you'd reach for instead of throwing on *expected* failures.

```java
// Result.java
package dev.day7;

import java.util.Optional;
import java.util.function.Function;

/**
 * A tiny Result type: either Ok(value) or Err(error).
 * Use this (not exceptions) for EXPECTED failures the caller must branch on.
 */
public sealed interface Result<T> permits Result.Ok, Result.Err {

    record Ok<T>(T value) implements Result<T> {}
    record Err<T>(PaymentError error) implements Result<T> {}

    static <T> Result<T> ok(T value) { return new Ok<>(value); }
    static <T> Result<T> err(PaymentError error) { return new Err<>(error); }

    default boolean isOk() { return this instanceof Ok<T>; }

    /** Absence-style view: present only when Ok. Optional answers "is there a value?". */
    default Optional<T> value() {
        return this instanceof Ok<T>(T v) ? Optional.of(v) : Optional.empty();
    }

    /** Map the success value, leaving errors untouched (railway-oriented style). */
    default <R> Result<R> map(Function<? super T, ? extends R> f) {
        return switch (this) {
            case Ok<T>(T v)        -> Result.ok(f.apply(v));
            case Err<T>(var error) -> Result.err(error);
        };
    }
}
```

### Step 5 — domain types (records) and the error/exception split

```java
// PaymentCommand.java
package dev.day7;

import java.util.Objects;

/**
 * A command carrying its idempotency key. The CLIENT generates the key once
 * per logical payment and REUSES it on every retry of that same payment.
 */
public record PaymentCommand(String idempotencyKey, String account, long amountCents) {
    public PaymentCommand {
        Objects.requireNonNull(idempotencyKey, "idempotencyKey required");
        Objects.requireNonNull(account, "account required");
        // A bug, not an expected failure -> unchecked exception, fail fast.
        if (amountCents <= 0) {
            throw new IllegalArgumentException("amountCents must be positive: " + amountCents);
        }
    }
}
```

```java
// Receipt.java
package dev.day7;

import java.time.Instant;

/** The stored result of a successful charge. We store the WHOLE receipt, not a flag. */
public record Receipt(String transactionId, String account, long amountCents, Instant chargedAt) {}
```

```java
// PaymentError.java
package dev.day7;

/** EXPECTED, recoverable-or-reportable failure reasons -> carried by Result, not thrown. */
public enum PaymentError {
    INSUFFICIENT_FUNDS,   // permanent for this attempt; do NOT retry
    CARD_DECLINED,        // permanent; do NOT retry
    GATEWAY_UNAVAILABLE   // transient; SAFE to retry (this is the interesting one)
}
```

```java
// PaymentException.java
package dev.day7;

/**
 * Root of our UNCHECKED exception hierarchy, for truly unexpected/programmer errors.
 * Compare: PaymentError (above) is for EXPECTED failures we return, not throw.
 */
public class PaymentException extends RuntimeException {
    public PaymentException(String message) { super(message); }
    public PaymentException(String message, Throwable cause) { super(message, cause); }
}
```

> **Why two failure channels?** `PaymentError` (returned in a `Result`) = expected outcomes the caller branches on (declined card). `PaymentException` (thrown) = "this should never happen" bugs. Mixing them is the #1 error-handling smell.

### Step 6 — the dedup store

```java
// IdempotencyStore.java
package dev.day7;

import java.util.Optional;

/** Maps an idempotency key to the previously-produced receipt. */
public interface IdempotencyStore extends AutoCloseable {

    /** Returns the stored receipt for this key, or empty if never seen. */
    Optional<Receipt> find(String key);

    /**
     * Atomically remember (key -> receipt) ONLY if the key is unseen.
     * Returns the receipt now associated with the key (existing one wins on a race).
     * This atomic "put-if-absent" is what defends against the in-flight race.
     */
    Receipt putIfAbsent(String key, Receipt receipt);

    @Override default void close() {}   // AutoCloseable -> usable in try-with-resources
}
```

```java
// InMemoryIdempotencyStore.java
package dev.day7;

import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Day-7 in-memory store. In production this is a row in Postgres with a UNIQUE
 * constraint on the key, or a Redis SETNX (Day 16) — same put-if-absent semantics.
 */
public final class InMemoryIdempotencyStore implements IdempotencyStore {

    private final ConcurrentHashMap<String, Receipt> store = new ConcurrentHashMap<>();

    @Override
    public Optional<Receipt> find(String key) {
        return Optional.ofNullable(store.get(key));
    }

    @Override
    public Receipt putIfAbsent(String key, Receipt receipt) {
        // putIfAbsent is atomic: concurrent callers can't both "win" the same key.
        Receipt existing = store.putIfAbsent(key, receipt);
        return existing != null ? existing : receipt;
    }

    public int size() { return store.size(); }
}
```

### Step 7 — a flaky gateway + try-with-resources

```java
// PaymentGateway.java
package dev.day7;

import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Simulates the downstream payment rail. It is NOT idempotent itself
 * (each successful call mints a new transactionId and "moves money").
 * It also fails transiently the first `failuresBeforeSuccess` times,
 * to model at-least-once / lost-ack reality.
 */
public final class PaymentGateway implements AutoCloseable {

    private final AtomicInteger callCount = new AtomicInteger();
    private final int failuresBeforeSuccess;

    public PaymentGateway(int failuresBeforeSuccess) {
        this.failuresBeforeSuccess = failuresBeforeSuccess;
    }

    public int rawChargeCount() { return callCount.get(); }

    /** A real charge against the rail. Returns a Result; never double-charges itself. */
    public Result<Receipt> charge(String account, long amountCents) {
        int n = callCount.incrementAndGet();
        if (n <= failuresBeforeSuccess) {
            // transient -> EXPECTED, retryable failure
            return Result.err(PaymentError.GATEWAY_UNAVAILABLE);
        }
        if (amountCents > 1_000_00) {
            return Result.err(PaymentError.INSUFFICIENT_FUNDS);
        }
        return Result.ok(new Receipt(
                "txn_" + UUID.randomUUID(), account, amountCents, Instant.now()));
    }

    @Override
    public void close() {
        System.out.println("[gateway] closed (real charges made: " + callCount.get() + ")");
    }
}
```

### Step 8 — the idempotent processor (the heart)

```java
// PaymentProcessor.java
package dev.day7;

/**
 * Wraps a non-idempotent gateway with an idempotency key + dedup store,
 * turning unsafe retries into safe ones.
 */
public final class PaymentProcessor {

    private final IdempotencyStore store;
    private final PaymentGateway gateway;

    public PaymentProcessor(IdempotencyStore store, PaymentGateway gateway) {
        this.store = store;
        this.gateway = gateway;
    }

    /**
     * Process a command idempotently:
     *  - If the key was already applied, return the ORIGINAL receipt (no re-charge).
     *  - Otherwise charge once, store (key -> receipt), and return it.
     * Transient errors are surfaced as Result.Err so the CALLER can retry
     * with the SAME key — which this method then dedups.
     */
    public Result<Receipt> process(PaymentCommand cmd) {
        // 1. Fast path: have we already applied this key? Optional answers "is there a value?".
        var existing = store.find(cmd.idempotencyKey());
        if (existing.isPresent()) {
            System.out.println("[processor] replay of key=" + cmd.idempotencyKey()
                    + " -> returning stored receipt (NO re-charge)");
            return Result.ok(existing.get());
        }

        // 2. Not seen: attempt the real charge.
        Result<Receipt> charged = gateway.charge(cmd.account(), cmd.amountCents());

        // 3. Only persist + dedup on SUCCESS. On error we store nothing, so a
        //    later retry with the same key can try again (key not yet "burned").
        return switch (charged) {
            case Result.Ok<Receipt>(Receipt receipt) -> {
                Receipt winner = store.putIfAbsent(cmd.idempotencyKey(), receipt);
                yield Result.ok(winner);   // race-safe: existing receipt wins if present
            }
            case Result.Err<Receipt> err -> {
                System.out.println("[processor] key=" + cmd.idempotencyKey()
                        + " failed with " + ((Result.Err<Receipt>) err).error()
                        + " (not stored)");
                yield err;
            }
        };
    }
}
```

> **Design subtlety (senior):** we store the receipt *only on success*. That means a transient `GATEWAY_UNAVAILABLE` does **not** burn the key — a retry can succeed. For *permanent* failures (declined) you might instead store the failure so retries return the same decline. Real systems (Stripe) store the response — success *or* a final error — and lock the key in-flight. We keep it simple but call out the trade-off.

### Step 9 — a retry helper + `App` driver

```java
// App.java
package dev.day7;

import java.util.UUID;
import java.util.function.Supplier;

public final class App {

    public static void main(String[] args) {
        System.out.println("=== Day 7: idempotent payment processor ===\n");

        // try-with-resources: store + gateway are AutoCloseable, closed deterministically.
        try (var store = new InMemoryIdempotencyStore();
             var gateway = new PaymentGateway(/* fail this many times first */ 2)) {

            var processor = new PaymentProcessor(store, gateway);

            // The client generates ONE key for this logical payment and reuses it on retry.
            String key = "idem_" + UUID.randomUUID();
            var command = new PaymentCommand(key, "acct_42", 500_00);

            System.out.println("Submitting payment with key=" + key + "\n");

            // Retry up to 5 times, but ONLY for transient (retryable) errors.
            Result<Receipt> result = retry(5, () -> processor.process(command));

            switch (result) {
                case Result.Ok<Receipt>(Receipt r) ->
                        System.out.println("\nSUCCESS: " + r);
                case Result.Err<Receipt>(PaymentError e) ->
                        System.out.println("\nFAILED permanently: " + e);
            }

            System.out.println("\n--- proving idempotency: replay the SAME command 3x ---");
            for (int i = 1; i <= 3; i++) {
                processor.process(command);
            }

            System.out.println("\nReal charges that hit the rail: " + gateway.rawChargeCount());
            System.out.println("Distinct keys remembered:        " + store.size());
            System.out.println("=> customer charged exactly ONCE despite retries + replays.");
        }
    }

    /** Retry only transient errors; permanent errors and success return immediately. */
    static Result<Receipt> retry(int maxAttempts, Supplier<Result<Receipt>> op) {
        Result<Receipt> last = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            last = op.get();
            switch (last) {
                case Result.Ok<Receipt> ok -> { return ok; }
                case Result.Err<Receipt>(PaymentError e) -> {
                    if (e != PaymentError.GATEWAY_UNAVAILABLE) {
                        return last;                      // permanent -> stop retrying
                    }
                    System.out.println("  attempt " + attempt + " transient ("
                            + e + "), retrying with SAME key...");
                }
            }
        }
        return last;
    }
}
```

### Step 10 — a JUnit 5 test (run by Gradle's `test` task)

```java
// src/test/java/dev/day7/PaymentProcessorTest.java
package dev.day7;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class PaymentProcessorTest {

    @Test
    void replayingSameKeyDoesNotDoubleCharge() {
        var store = new InMemoryIdempotencyStore();
        var gateway = new PaymentGateway(0);            // always succeeds
        var processor = new PaymentProcessor(store, gateway);
        var cmd = new PaymentCommand("k1", "acct", 1000);

        Result<Receipt> first  = processor.process(cmd);
        Result<Receipt> second = processor.process(cmd);  // replay

        assertTrue(first.isOk());
        assertTrue(second.isOk());
        // Same receipt returned both times...
        assertEquals(first.value().orElseThrow(), second.value().orElseThrow());
        // ...and the rail was hit only ONCE.
        assertEquals(1, gateway.rawChargeCount());
        assertEquals(1, store.size());
    }

    @Test
    void transientFailureIsRetryableAndKeyNotBurned() {
        var store = new InMemoryIdempotencyStore();
        var gateway = new PaymentGateway(1);            // fail once, then succeed
        var processor = new PaymentProcessor(store, gateway);
        var cmd = new PaymentCommand("k2", "acct", 1000);

        Result<Receipt> failed = processor.process(cmd);     // transient err
        assertFalse(failed.isOk());
        assertEquals(0, store.size());                       // key NOT burned

        Result<Receipt> ok = processor.process(cmd);         // retry, same key
        assertTrue(ok.isOk());
        assertEquals(1, store.size());
    }

    @Test
    void invalidAmountIsAProgrammerErrorNotAResult() {
        // amount <= 0 is a bug -> unchecked exception, not a Result.Err
        assertThrows(IllegalArgumentException.class,
                () -> new PaymentCommand("k3", "acct", 0));
    }
}
```

---

## Run it with Gradle

```bash
cd day7
./gradlew run        # compiles + runs App.main  (Maven: mvn compile exec:java)
./gradlew test       # runs JUnit 5              (Maven: mvn test)
./gradlew build      # compile + test + jar      (Maven: mvn package)
./gradlew tasks      # list every task, incl. your custom `hello`
./gradlew clean      # delete build/             (Maven: mvn clean)
```

The first run downloads the pinned Gradle (via the wrapper) and the JDK toolchain if needed — subsequent runs are fast thanks to Gradle's build cache and incremental compilation.

### Expected output

```
=== Day 7: idempotent payment processor ===

Submitting payment with key=idem_3f2c... 

  attempt 1 transient (GATEWAY_UNAVAILABLE), retrying with SAME key...
[processor] key=idem_3f2c... failed with GATEWAY_UNAVAILABLE (not stored)
  attempt 2 transient (GATEWAY_UNAVAILABLE), retrying with SAME key...
[processor] key=idem_3f2c... failed with GATEWAY_UNAVAILABLE (not stored)

SUCCESS: Receipt[transactionId=txn_8b1d..., account=acct_42, amountCents=50000, chargedAt=...]

--- proving idempotency: replay the SAME command 3x ---
[processor] replay of key=idem_3f2c... -> returning stored receipt (NO re-charge)
[processor] replay of key=idem_3f2c... -> returning stored receipt (NO re-charge)
[processor] replay of key=idem_3f2c... -> returning stored receipt (NO re-charge)

Real charges that hit the rail: 3
Distinct keys remembered:        1
[gateway] closed (real charges made: 3)
=> customer charged exactly ONCE despite retries + replays.
```

Read that "Real charges = 3 / Distinct keys = 1" line carefully: the rail was *called* 3 times (2 transient failures + 1 success), but only **one** charge actually committed and every replay returned the *same* receipt. That is idempotency doing its job.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Stripe & real payment APIs.** Stripe's API takes an `Idempotency-Key` HTTP header (a client UUID). Stripe stores the **first response** (success *or* error) for **24 hours** and replays it for any request with the same key — including the same status code and body. They also reject a reused key if the *request body differs*, catching client bugs. This is precisely Day 10's idempotent HTTP, built on today's dedup store.
- **The in-flight race.** Two retries with the same key can arrive *concurrently* (the client fired a retry before the first response). Our `putIfAbsent` makes the *store* atomic, but a real system also needs a **lock or "in-progress" record** keyed by the idempotency key, so the second concurrent request waits/409s rather than double-executing the gateway call. In Postgres this is a `UNIQUE` constraint + `INSERT ... ON CONFLICT`; in Redis a `SET key value NX` (Day 16). Locks return in depth on Day 28.
- **Dedup windows.** You cannot remember every key forever — the store would grow unbounded. So dedup is bounded by a **window** (Stripe: 24h; Kafka: a configurable producer window). Outside the window, a very-late duplicate *could* re-execute. Senior trade-off: longer window = more storage + safety; shorter = cheaper but more replay risk. Choose based on your retry/timeout budget.
- **Where to store the key.** For *true* end-to-end idempotency, the key write and the side effect must be **atomic**. If you charge then crash before storing the key, a retry double-charges. The fix is to make "do the work" and "record the key" one transaction — which is exactly the **transactional outbox** (Day 20) and the core of "effectively-once."
- **Natural vs synthetic idempotency.** Prefer designing operations to be *naturally* idempotent (PUT a full resource, `UPSERT`, set-absolute-value) before reaching for keys. A key is the fallback for inherently non-idempotent acts (charge, send, increment).
- **Result vs exceptions in real codebases.** A `Result`/`Either` type (popular via Vavr, or hand-rolled as here) gives compile-time-checked error flow and composes with streams (Day 3) — but Java's ecosystem still leans on exceptions, and `Optional` for absence. The senior move is consistency within a layer, not dogma: returns for expected outcomes, exceptions for bugs, and never `Optional.get()` unchecked.
- **`Optional` is for absence, not failure.** `find(key)` returning `Optional<Receipt>` is perfect ("is it there?"). `process(cmd)` returning `Result<Receipt>` is right ("did it succeed, and if not, why?"). Using `Optional` to mean "it failed" loses the reason and is a code smell.
- **Gradle vs Maven, the honest take.** Gradle's programmability (custom tasks, build logic) and incremental/parallel builds win on large multi-module projects; Maven's rigidity wins on predictability and onboarding. Spring projects (from Day 8) ship `start.spring.io` templates for *both* — you'll meet Gradle Kotlin DSL and Maven XML in the wild, so being fluent reading both is the real goal of today.

### Stretch goals

1. **In-flight locking.** Add an `IN_PROGRESS` marker to the store when a key is first seen, and make a concurrent second request with the same key either wait for the result or return a `409`-style `Result.Err`. Reproduce the race with two threads and a `CountDownLatch`.
2. **Persist failures too.** Change the processor so a *permanent* error (CARD_DECLINED) is stored against the key and replayed, while transient errors stay retryable. Match Stripe's "replay the stored response, success or error" behavior.
3. **Dedup window / TTL.** Add an expiry to stored entries (an `Instant expiresAt`) and a sweeper that evicts expired keys. Write a test proving a key *outside* the window re-executes while one *inside* dedups.
4. **Compare-request-body guard.** Store a hash of the request alongside the receipt; if the same key arrives with a *different* command, return an error (`KEY_REUSED_WITH_DIFFERENT_BODY`) — catching the client bug Stripe guards against.
5. **Gradle muscle.** Add the `jacoco` plugin for test coverage and a custom task `verifyIdempotency` that depends on `test`; explore `./gradlew build --scan` and `./gradlew dependencies` to see the resolved dependency graph (Maven's `mvn dependency:tree`).

### Day 8 teaser — entering Spring

Foundation week is done: you've built a WAL, a hash index, streams, a queue, MVCC, a B-tree, and today an idempotent processor — all in *plain* Java, with both Maven and Gradle in your toolbelt. **Tomorrow we enter Spring.** Day 8 is **Spring IoC / Dependency Injection**: instead of `new PaymentProcessor(store, gateway)` wiring objects by hand in `App.main`, the Spring container will *invert control* and inject those collaborators for you. Everything you built today becomes a `@Component`/`@Service` — and you'll see that the magic is just the constructor wiring you've already been doing, automated. Bring today's processor; we'll Spring-ify it.
