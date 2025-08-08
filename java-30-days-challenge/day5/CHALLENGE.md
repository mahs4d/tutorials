# Day 5: Immutability, Records & Copy-on-Write MVCC

| | |
|---|---|
| 🏗️ **Project** | **SnapStore** — a copy-on-write MVCC versioned store |
| ☕ **Java & language skills** | records, immutability, sealed types, final fields, defensive copying, equals/hashCode |
| 🧰 **Library / tool** | Lombok (@Getter/@Builder/@Value/@Slf4j) vs Java records |
| 🗄️ **DB / distributed-systems concept** | MVCC & copy-on-write snapshot isolation |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. Why immutability matters (the senior framing)

An object is **immutable** if its observable state cannot change after construction. The payoff is not aesthetic — it's operational:

- **Thread-safety with zero synchronization.** If a value never changes, two threads reading it can never see a torn or half-updated state. There is no critical section to protect because there is no mutation. Day 4 forced you to reason about locks and queues; immutability lets you *delete* a whole class of those problems.
- **Safe publication.** In the Java Memory Model, a `final` field set in a constructor is guaranteed visible to other threads once the constructor completes (no `volatile` needed). Immutable objects are safe to hand off across threads via a plain reference.
- **No defensive copies.** Mutable APIs force you to copy on the way in *and* the way out (`return new ArrayList<>(internal)`) to stop callers from corrupting your internals. Immutable objects can be shared freely — they *are* the defensive copy.
- **Value semantics & caching.** Immutable objects make great `Map` keys and cache entries because their `hashCode`/`equals` never drift after insertion.
- **Reasoning & debugging.** State that can't change is state you can't accidentally clobber three stack frames away.

The cost: every "change" allocates a new object. The trick that makes this cheap at scale — **structural sharing** — is exactly what database MVCC and persistent data structures exploit. That's the bridge from "immutability is nice" to "immutability is how real databases stay fast under concurrency."

### 2. Records vs. classes vs. Lombok `@Value`

A **record** is a transparent, shallowly-immutable data carrier. This one line:

```java
public record Money(long cents, String currency) {}
```

generates: a canonical constructor, a `private final` field + accessor for each component (`cents()`, `currency()` — note: no `get` prefix), and value-based `equals`, `hashCode`, and `toString`. Records are implicitly `final`, cannot extend another class, and their components are `final`.

**Lombok `@Value`** predates records and produces a comparable shape on an ordinary class:

```java
@Value
public class Money {
    long cents;
    String currency;
}
```

`@Value` makes the class `final`, all fields `private final`, and generates getters (`getCents()` — JavaBean style), `equals`/`hashCode`/`toString`, and an all-args constructor.

| | `record` | Lombok `@Value` | Plain class |
|---|---|---|---|
| Boilerplate | Zero (language feature) | Zero (codegen) | You write it all |
| Accessor style | `cents()` | `getCents()` | Your choice |
| Needs build-time tool | No | Yes (annotation processor) | No |
| Can extend a class | No | Yes | Yes |
| Customize per-field | Compact ctor / extra methods | `@With`, `@Builder`, etc. | Full freedom |
| Deep immutability | No (you must pass immutable components) | No | No |

**When to prefer records (default in 2026):** plain immutable data — DTOs, value objects, events, snapshot/version types. They're the language's blessed answer, need no dependency, pattern-match cleanly, and are understood by every modern tool.

**When Lombok still earns its place:** you need to *extend* a base class; you want a rich `@Builder` with defaults, `toBuilder`, and staged construction; you want `@Slf4j`/`@Getter`/`@With` sprinkled onto mutable entities (e.g., JPA `@Entity` classes, which can't be records); or you're on a codebase that predates records and consistency matters.

> Important caveat shared by **all three**: immutability here is *shallow*. `record Snapshot(Map<String,String> data)` is only immutable if `data` is itself unmodifiable. We enforce this below with `Map.copyOf` / `Collections.unmodifiableMap`.

### 3. How Lombok actually works (and the gotcha)

Lombok is a **JSR-269 annotation processor** that runs during `javac`. Unlike normal processors that only *generate new* files, Lombok reaches into `com.sun.tools.javac` internals and **rewrites the AST of your existing class** — injecting the getters, constructors, etc., before bytecode is emitted. Consequences:

- The generated members exist in the `.class` file but **not** in your `.java` source — so your IDE needs the **Lombok plugin** to *see* `getCents()` and not flag it red. The compiler is fine without the plugin; the IDE's own resolver is what gets confused.
- Because it's an annotation processor, the Lombok jar must be on the **annotation processor path** at compile time. With Maven this is automatic once Lombok is a dependency, but if you configure `maven-compiler-plugin` with explicit `<annotationProcessorPaths>`, you must list Lombok there too or it silently does nothing.
- Mark the dependency `<scope>provided</scope>` (or `optional`): Lombok is only needed at compile time, never at runtime.

### 4. MVCC, copy-on-write, and snapshot isolation

The central problem of a concurrent database: **readers and writers contend**. The naive fix is locking — a writer takes an exclusive lock, readers block until it's done (and vice versa). That serializes work and tanks throughput.

**MVCC (Multi-Version Concurrency Control)** sidesteps this. Instead of overwriting data in place, a write creates a **new version** of the affected data and leaves the old version intact. The invariant:

> **Readers never block writers, and writers never block readers.**

A reader operates against a **snapshot** — a consistent, point-in-time view defined by *which versions are visible to it*. A writer creating version *N+1* doesn't disturb a reader still looking at version *N*. This is **snapshot isolation**: every statement (or transaction) sees the database as of a fixed moment, immune to concurrent commits.

**Copy-on-write** is the simplest implementation of this idea: on every write, *copy* the data structure, apply the change to the copy, and atomically swap a single root pointer to the new version. Readers hold whatever root they grabbed; they keep seeing their version even after the swap. The atomic pointer swap is the *commit*. Our project implements exactly this with an `AtomicReference` to an immutable `Snapshot` record.

**How Postgres does it (high level).** Postgres bakes MVCC into the row format. Every row version (a "tuple") carries hidden system columns:

- `xmin` — the transaction id (xid) that **created** this tuple version.
- `xmax` — the xid that **deleted/superseded** it (0/unset while live).

An `UPDATE` is really *insert a new tuple with `xmin = myXid`* + *set `xmax = myXid` on the old tuple*. A transaction sees a tuple only if its `xmin` is committed-and-visible to the transaction's snapshot **and** its `xmax` is not (i.e., the deleting transaction isn't visible). The snapshot is essentially "the set of xids considered committed as of now." Old tuples linger until **`VACUUM`** reclaims those no longer visible to *any* live snapshot. (We'll see the practical fallout — bloat, long-running-transaction hazards, and isolation guarantees — on Day 11.)

Our toy store is the same idea collapsed to one level: instead of per-tuple `xmin`/`xmax`, we keep a stack of whole-map versions, each immutable, each tagged with a version number that plays the role of a commit id.

---

## Prerequisites & setup

You need JDK 21 and Maven 3.9+.

```bash
java -version   # expect 21.x
mvn -version
```

### Project layout

```
day5/
├── pom.xml
└── src
    ├── main/java/com/example/mvcc/
    │   ├── Version.java
    │   ├── Snapshot.java
    │   ├── CowKvStore.java
    │   └── Demo.java
    └── test/java/com/example/mvcc/
        └── CowKvStoreTest.java
```

---

## 🛠️ Project Walkthrough — SnapStore

Roll up your sleeves — from here you'll build the copy-on-write versioned store step by step and run it yourself.

### Step 1 — `pom.xml` with Lombok on the annotation processor path

Create `day5/pom.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.example</groupId>
    <artifactId>day5-cow-mvcc</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.release>21</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <lombok.version>1.18.34</lombok.version>
        <junit.version>5.10.2</junit.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.projectlombok</groupId>
            <artifactId>lombok</artifactId>
            <version>${lombok.version}</version>
            <scope>provided</scope> <!-- compile-time only; not shipped at runtime -->
        </dependency>

        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>${junit.version}</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.13.0</version>
                <configuration>
                    <release>21</release>
                    <!-- If you declare annotationProcessorPaths explicitly,
                         Lombok MUST be listed here or it won't run. -->
                    <annotationProcessorPaths>
                        <path>
                            <groupId>org.projectlombok</groupId>
                            <artifactId>lombok</artifactId>
                            <version>${lombok.version}</version>
                        </path>
                    </annotationProcessorPaths>
                </configuration>
            </plugin>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
            </plugin>
        </plugins>
    </build>
</project>
```

> IDE note: install the Lombok plugin (IntelliJ has it bundled since 2020.3; enable annotation processing under *Settings → Build → Compiler → Annotation Processors*). Without it the compile still works via Maven, but the IDE will red-underline Lombok-generated methods.

---

## Building the copy-on-write versioned key-value store

We model the store as a chain of immutable snapshots. Each `commit` produces a *new* `Snapshot` and atomically advances the store's "current" pointer. Readers grab a `Snapshot` reference and are immune to later commits.

### Step 2 — `Version` as a record (the commit id)

`day5/src/main/java/com/example/mvcc/Version.java`:

```java
package com.example.mvcc;

import java.time.Instant;

/**
 * Immutable version stamp — our analogue of a Postgres commit xid.
 * Records give us value-based equals/hashCode for free, which is exactly
 * what we want for a version identifier.
 */
public record Version(long id, Instant committedAt) {

    public static Version genesis() {
        return new Version(0L, Instant.EPOCH);
    }

    /** Compact constructor: validate invariants at construction time. */
    public Version {
        if (id < 0) {
            throw new IllegalArgumentException("version id must be >= 0, got " + id);
        }
    }

    public Version next() {
        return new Version(id + 1, Instant.now());
    }
}
```

Note the **compact constructor** (`public Version { ... }`) — a record feature for validating/normalizing components without restating the parameter list. This is the kind of per-field touch people assume forces you off records; it doesn't.

### Step 3 — `Snapshot` as a record holding an immutable map

`day5/src/main/java/com/example/mvcc/Snapshot.java`:

```java
package com.example.mvcc;

import java.util.Map;
import java.util.Optional;

/**
 * An immutable point-in-time view of the whole key space.
 *
 * Records are only SHALLOWLY immutable, so the canonical constructor defends
 * the invariant by wrapping the incoming map in an unmodifiable copy. After
 * this, the Snapshot cannot be mutated by anyone — readers can share it freely
 * across threads with no locking (safe publication via final fields).
 */
public record Snapshot(Version version, Map<String, String> data) {

    public Snapshot {
        // Map.copyOf returns an unmodifiable, defensively-copied map.
        data = Map.copyOf(data);
    }

    public static Snapshot empty() {
        return new Snapshot(Version.genesis(), Map.of());
    }

    public Optional<String> get(String key) {
        return Optional.ofNullable(data.get(key));
    }

    public int size() {
        return data.size();
    }

    /**
     * Copy-on-write: produce a NEW snapshot with one key changed.
     * The current snapshot is untouched, so any reader holding it is unaffected.
     */
    public Snapshot withPut(String key, String value) {
        var copy = new java.util.HashMap<>(data); // structural copy
        copy.put(key, value);
        return new Snapshot(version.next(), copy);
    }

    public Snapshot withRemove(String key) {
        if (!data.containsKey(key)) {
            return this; // nothing changed; reuse the same immutable instance
        }
        var copy = new java.util.HashMap<>(data);
        copy.remove(key);
        return new Snapshot(version.next(), copy);
    }
}
```

This is naive O(n) copy-on-write — fine for teaching and small maps. The *Going deeper* section shows how persistent data structures turn this into O(log n) with **structural sharing**, which is what real systems use.

### Step 4 — `CowKvStore`: the atomic pointer swap = commit

`day5/src/main/java/com/example/mvcc/CowKvStore.java`:

```java
package com.example.mvcc;

import lombok.extern.slf4j.Slf4j;

import java.util.concurrent.atomic.AtomicReference;
import java.util.function.UnaryOperator;

/**
 * A copy-on-write, multi-version key-value store.
 *
 * The "database" is a single AtomicReference to the current immutable Snapshot.
 * - Readers call {@link #currentSnapshot()} to PIN a version, then read from it
 *   as long as they like. Later commits never change what they see.
 * - Writers build a new Snapshot off the latest and CAS the root pointer.
 *   The compare-and-set is the commit point — it's atomic and lock-free.
 *
 * This mirrors MVCC: readers never block writers, writers never block readers.
 */
@Slf4j
public final class CowKvStore {

    private final AtomicReference<Snapshot> current =
            new AtomicReference<>(Snapshot.empty());

    /** Pin the current version. This is a reader's "begin snapshot". */
    public Snapshot currentSnapshot() {
        return current.get();
    }

    public java.util.Optional<String> get(String key) {
        return current.get().get(key);
    }

    /** Commit a write. Retries on contention (optimistic, lock-free). */
    public Snapshot put(String key, String value) {
        return commit(s -> s.withPut(key, value));
    }

    public Snapshot remove(String key) {
        return commit(s -> s.withRemove(key));
    }

    /**
     * Apply an immutable transformation and atomically advance the version.
     * If another writer committed in between, we retry against the new base —
     * just like optimistic MVCC re-derives its result on conflict.
     */
    private Snapshot commit(UnaryOperator<Snapshot> mutation) {
        while (true) {
            Snapshot base = current.get();
            Snapshot next = mutation.apply(base);
            if (next == base) {
                return base; // no-op (e.g. remove of absent key)
            }
            if (current.compareAndSet(base, next)) {
                log.info("committed version {} ({} keys)",
                        next.version().id(), next.size());
                return next;
            }
            log.debug("CAS lost the race at base v{}, retrying", base.version().id());
            // loop: rebuild on the newer base
        }
    }

    public long currentVersionId() {
        return current.get().version().id();
    }
}
```

`@Slf4j` is Lombok injecting a `private static final org.slf4j.Logger log = ...` field — a tiny but representative example of boilerplate removal. (We don't add an SLF4J binding, so logs print via the simple default / nothing fancy; the point is the generated field, not the appender.)

### Step 5 — `Demo`: a reader sees an old snapshot while a writer advances

`day5/src/main/java/com/example/mvcc/Demo.java`:

```java
package com.example.mvcc;

import java.util.concurrent.CountDownLatch;

/**
 * Demonstrates snapshot isolation:
 * a reader pins version N and keeps seeing it while a writer commits N+1, N+2...
 */
public final class Demo {

    public static void main(String[] args) throws InterruptedException {
        var store = new CowKvStore();
        store.put("account:1", "100");   // v1
        store.put("account:2", "50");    // v2

        // READER pins the current snapshot now (version 2).
        Snapshot readerView = store.currentSnapshot();
        System.out.printf("Reader pinned v%d: account:1=%s%n",
                readerView.version().id(), readerView.get("account:1").orElse("<none>"));

        var writerDone = new CountDownLatch(1);

        // WRITER thread races ahead, committing new versions.
        Thread writer = new Thread(() -> {
            store.put("account:1", "100000"); // v3 — a big deposit
            store.put("account:3", "777");     // v4 — new account
            store.remove("account:2");          // v5 — account closed
            writerDone.countDown();
        }, "writer");
        writer.start();
        writerDone.await();

        // The reader's pinned snapshot is UNCHANGED by the concurrent commits.
        System.out.println("\n--- after writer committed v" + store.currentVersionId() + " ---");
        System.out.printf("Reader (still v%d) sees account:1=%s, account:2=%s, account:3=%s%n",
                readerView.version().id(),
                readerView.get("account:1").orElse("<none>"),
                readerView.get("account:2").orElse("<none>"),
                readerView.get("account:3").orElse("<none>"));

        Snapshot freshView = store.currentSnapshot();
        System.out.printf("Fresh reader (v%d) sees account:1=%s, account:2=%s, account:3=%s%n",
                freshView.version().id(),
                freshView.get("account:1").orElse("<none>"),
                freshView.get("account:2").orElse("<none>"),
                freshView.get("account:3").orElse("<none>"));

        System.out.println("\nKey insight: same store, two readers, two consistent views, zero locks.");
    }
}
```

### Step 6 — A concurrency test proving non-blocking + isolation

`day5/src/test/java/com/example/mvcc/CowKvStoreTest.java`:

```java
package com.example.mvcc;

import org.junit.jupiter.api.Test;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.*;

class CowKvStoreTest {

    @Test
    void readerSeesStableSnapshotWhileWriterAdvances() {
        var store = new CowKvStore();
        store.put("k", "v0");

        Snapshot pinned = store.currentSnapshot();
        long pinnedVersion = pinned.version().id();

        store.put("k", "v1");
        store.put("k", "v2");

        // Pinned reader is immune to later commits.
        assertEquals("v0", pinned.get("k").orElseThrow());
        assertEquals(pinnedVersion, pinned.version().id());
        // Fresh read sees the latest.
        assertEquals("v2", store.get("k").orElseThrow());
        assertTrue(store.currentVersionId() > pinnedVersion);
    }

    @Test
    void concurrentWritersAllCommitWithoutLosingUpdates() throws Exception {
        var store = new CowKvStore();
        int threads = 8;
        int writesPerThread = 500;
        var pool = Executors.newFixedThreadPool(threads);
        var counter = new AtomicInteger();

        for (int t = 0; t < threads; t++) {
            pool.submit(() -> {
                for (int i = 0; i < writesPerThread; i++) {
                    int n = counter.getAndIncrement();
                    store.put("key-" + n, Integer.toString(n));
                }
            });
        }
        pool.shutdown();
        assertTrue(pool.awaitTermination(30, TimeUnit.SECONDS));

        // Lock-free CAS-retry must not drop any committed write.
        assertEquals(threads * writesPerThread, store.currentSnapshot().size());
        // Each successful put advanced the version exactly once.
        assertEquals(threads * writesPerThread, store.currentVersionId());
    }

    @Test
    void removeOfAbsentKeyReusesSameSnapshotInstance() {
        var store = new CowKvStore();
        Snapshot before = store.currentSnapshot();
        store.remove("nope");
        // No version churn for a no-op write.
        assertSame(before, store.currentSnapshot());
    }
}
```

The second test is the real proof: 4000 concurrent writes across 8 threads, each a copy-on-write commit through `compareAndSet`, must all land — the version ends at exactly 4000 and no key is lost. That can only hold if the CAS-retry loop correctly rebuilds on the latest base when it loses a race.

---

## Run instructions

```bash
cd day5

# Compile + run the test suite (proves isolation & lock-free correctness)
mvn -q test

# Run the interactive demo
mvn -q compile exec:java -Dexec.mainClass=com.example.mvcc.Demo
```

If you don't want the `exec` plugin, run it directly off the compiled classes:

```bash
mvn -q compile
java -cp target/classes com.example.mvcc.Demo
```

(Lombok is `provided` scope, so it isn't needed on the runtime classpath — only `org.slf4j` if you wanted real log output, which we keep optional.)

## Expected output

```
Reader pinned v2: account:1=100

--- after writer committed v5 ---
Reader (still v2) sees account:1=100, account:2=50, account:3=<none>
Fresh reader (v5) sees account:1=100000, account:2=<none>, account:3=777

Key insight: same store, two readers, two consistent views, zero locks.
```

The pinned reader still reads `account:1=100` and *still sees* the now-deleted `account:2=50`, even though the writer raised the balance to `100000`, closed account 2, and opened account 3. That divergence between the two views *is* snapshot isolation — and we got it with one `AtomicReference` and immutable records, no locks.

`mvn test` should report 3 passing tests.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Postgres tuple versioning in practice.** Each heap tuple stores `xmin`/`xmax` (plus `cmin`/`cmax` for intra-transaction command ordering). An `UPDATE` never edits in place — it writes a new tuple and stamps the old one's `xmax`. A transaction's visibility check compares tuple xids against its **snapshot** (`xmin`, `xmax`, and the `xip` list of in-progress xids). Our whole-map `Snapshot` chain is the same concept at coarse granularity.
- **VACUUM and bloat.** Dead tuples (no longer visible to any snapshot) accumulate until `VACUUM`/autovacuum reclaims them. Our toy store has the same hazard: old `Snapshot` versions stay alive as long as some reader holds a reference — exactly why a *long-running reader transaction* in Postgres blocks vacuum and bloats tables. The GC frees our orphaned snapshots; Postgres needs an explicit reclaimer. Same garbage problem, two collectors.
- **Persistent data structures = cheap COW.** Our `new HashMap<>(data)` copy is O(n) per write. Production-grade COW uses **persistent (immutable) data structures** with **structural sharing**: a HAMT (hash array mapped trie) or balanced tree shares all unchanged subtrees between versions, so a write copies only O(log n) nodes. See Clojure's `PersistentHashMap`, Scala's immutable collections, or Java libs like Vavr / PCollections / Eclipse Collections immutable maps. Swapping `Snapshot`'s `HashMap` copy for a Vavr `io.vavr.collection.HashMap` would make every commit cheap while keeping the exact same `AtomicReference` commit model.
- **Immutability in functional Java.** `record` + sealed interfaces + pattern matching (`switch` over a sealed type) give you algebraic-data-type style modeling. Combined with `Stream`/`Optional` (Day 3) and immutable collections, you can write large swaths of pure, side-effect-free code that's trivially parallelizable — the same property that makes MVCC snapshots safe to share makes pure functions safe to fork.
- **The MVCC tax.** MVCC isn't free: it trades write-in-place for version bloat, garbage collection, and the possibility of **write skew** under snapshot isolation (two transactions each reading a consistent snapshot and writing disjoint rows that jointly violate an invariant). Day 11 will name these anomalies precisely and show where `SERIALIZABLE` vs `REPEATABLE READ` draw the line.

### Stretch goals

1. **Structural sharing.** Replace the `HashMap` copy in `Snapshot` with Vavr's persistent `HashMap` and benchmark commit latency against the naive copy as the map grows to 100k keys. Observe O(n) vs O(log n).
2. **Transaction objects + snapshot isolation conflicts.** Add a `Transaction` that pins a base snapshot, buffers writes, and on `commit()` does an optimistic CAS — *aborting* (throwing `ConflictException`) if the store advanced past its base. This is first-committer-wins, the heart of snapshot isolation.
3. **Version history & time travel.** Keep the last K snapshots in a ring buffer and expose `getAsOf(long versionId, String key)`. Then add a `vacuum(minVisibleVersion)` that drops versions no live reader needs — a hand-rolled autovacuum.
4. **`@Builder` for a richer event.** Model commits as a Lombok `@Value @Builder` `CommitEvent` (version, timestamp, changed keys, author) with `@Builder.Default` fields, and contrast the ergonomics with the equivalent record + compact constructor. Decide which you'd actually ship.

### Day 6 teaser

We've kept everything in memory and copied whole maps. **Day 6: B-Tree / NIO** drops to disk: you'll build a B-Tree-backed store using `java.nio` memory-mapped files (`MappedByteBuffer`), learning why databases use B-Trees (high fan-out, page-aligned, range-scan-friendly) instead of hash indexes (Day 2), and how page-oriented storage interacts with the versioning ideas you just built.
