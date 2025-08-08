# Day 1: Build Tooling & a Write-Ahead Log

| | |
|---|---|
| 🏗️ **Project** | **TinyKV** — an append-only write-ahead-log key-value store |
| ☕ **Java & language skills** | Standard project structure, file I/O with `java.nio`, classes & immutable records, sealed interfaces, exhaustive switch patterns, `try-with-resources`, append-only writing & `fsync` |
| 🧰 **Library / tool** | Maven (build lifecycle, POM, dependencies, plugins) + JDK 21 setup |
| 🗄️ **DB / distributed-systems concept** | Write-Ahead Log (WAL) — durability, append-only logs, crash recovery / replay |
| 📊 **Difficulty** | Easy |

---

## Concept primer: the Write-Ahead Log (WAL)

A database has two jobs that fight each other: be **fast** and be **durable**. "Durable" means: once you tell a client *"your write succeeded"*, that write must survive a crash a microsecond later — a power cut, an OOM kill, a `kill -9`. If your only copy of the data lives in an in-memory `HashMap`, a crash wipes everything. So you must write to disk. But disks are slow, and worse, *partial* writes are dangerous: if you're updating a large on-disk data structure (a B-Tree page, say — that's Day 6) and the machine dies halfway through, you can corrupt the structure and lose data you'd already acknowledged.

The **Write-Ahead Log** is the classic answer, and it rests on one rule:

> **Before you modify the actual data, first append a record describing the change to a sequential log, and make sure that log record is physically on disk.**

That ordering — *log first, data second* — is where the name comes from: the log is written *ahead* of the data.

### Why append-only?

The WAL is **append-only**: you only ever write to the end of the file, never seek back to overwrite. This matters for three reasons:

1. **Sequential writes are fast.** Even on SSDs, and dramatically so on spinning disks, appending to the end of a file is far cheaper than random-access writes scattered across the disk. You turn many small random updates into one fast sequential stream.
2. **Append is hard to corrupt.** You never overwrite committed data, so a crash can at worst leave a half-written record at the *tail*, which recovery can detect and discard. The bytes before it are untouched.
3. **The log is the source of truth.** The order of records in the log *is* the order operations happened. Replaying them in order reconstructs the exact state.

### Why `fsync` matters (the part everyone gets wrong)

When your program calls `write()` (or Java's `OutputStream.write`), the bytes usually land in the **OS page cache** — RAM managed by the kernel — and the call returns immediately. The OS flushes that cache to the physical device *later*, on its own schedule. If the machine loses power in that window, the data is gone even though `write()` "succeeded".

To get a real durability guarantee you must call **`fsync`** (in Java: `FileChannel.force(true)` or `FileDescriptor.sync()`), which blocks until the device confirms the bytes are persisted. This is the single most expensive operation in the whole system, which is why real databases let you trade durability for throughput (batching many commits into one `fsync`, group commit, `synchronous_commit=off` in Postgres, `acks` in Kafka, etc.). Today you'll do the safe thing: `fsync` on every write, and *feel* how it constrains throughput.

### Recovery / log replay

On startup the data was never the source of truth — the log was. So recovery is simple: start from an empty state and **replay** every record in the log in order, applying each to the in-memory state. After replay, your in-memory map is exactly what it was before the crash (minus, at most, a torn final record). This replay must be **idempotent-friendly**: replaying the same log twice yields the same state, because each record fully describes a deterministic state transition.

This single idea — *an ordered, append-only log is the source of truth; everything else is a materialized view of it* — is the spine of this whole 30-day course. **Postgres** has its WAL. **Kafka** *is* a distributed log (Day 18). **Event Sourcing** (Day 19) makes the log the primary model. The **Transactional Outbox** (Day 20) is a log used to reliably publish events. You're building the seed of all of it today.

---

## Project goal

Build **`TinyKV`**, a tiny *embeddable* key-value store (string→string) whose **only** durability mechanism is an append-only WAL file. Specifically:

- `put(key, value)` and `delete(key)` each append one record to `wal.log` and `fsync` it, *then* update an in-memory `HashMap`.
- `get(key)` reads only from the in-memory map.
- On construction, the store **replays** the existing `wal.log` to rebuild its state.
- A small `main` demonstrates: write some data, "crash" (exit), restart, and observe the data survived.

We'll keep the on-disk format dead simple and human-readable so you can `cat wal.log` and see exactly what durability looks like.

---

## Prerequisites & setup

### 1. Install JDK 21 (LTS)

Java 21 is the current Long-Term-Support release and what we'll use all course. Pick any vendor; **Eclipse Temurin (Adoptium)** is a great free default.

- **macOS** (Homebrew): `brew install --cask temurin@21`
- **Windows**: download the `.msi` from https://adoptium.net and run it (it sets `JAVA_HOME` and `PATH` for you).
- **Linux** (Debian/Ubuntu): `sudo apt install openjdk-21-jdk`, or use [SDKMAN!](https://sdkman.io): `sdk install java 21-tem`.

Verify — you should see version 21:

```bash
java -version
```

```text
openjdk version "21.0.x" 2024-xx-xx LTS
OpenJDK Runtime Environment Temurin-21.0.x ...
OpenJDK 64-Bit Server VM Temurin-21.0.x ...
```

If `java -version` shows an older version, your `PATH`/`JAVA_HOME` points at the wrong JDK. SDKMAN! avoids this pain by managing versions for you.

### 2. Install Maven 3.9+

- **macOS**: `brew install maven`
- **Linux**: `sudo apt install maven` or `sdk install maven`
- **Windows**: download from https://maven.apache.org, unzip, add `bin` to `PATH`.

Verify (note it reports which JDK it found — make sure it's 21):

```bash
mvn -version
```

```text
Apache Maven 3.9.x
Maven home: ...
Java version: 21.0.x, vendor: Eclipse Adoptium
```

### 3. Pick an IDE

Any of these will import a Maven project natively (just "Open" the folder containing `pom.xml`):

- **IntelliJ IDEA Community Edition** — the most popular for Java, free, best Maven/Spring support. Recommended for this course.
- **VS Code** with the "Extension Pack for Java".
- **Eclipse**.

You can do everything from the terminal too; the IDE just makes Days 8+ (Spring) nicer.

---

## A 5-minute Maven mental model

Maven is a **build tool** and **dependency manager** built on *convention over configuration*. If you follow its standard layout, you write almost no build config.

**Standard project layout** (we'll create exactly this):

```text
tinykv/
├── pom.xml                         <- the project descriptor
└── src/
    ├── main/java/                  <- production code
    │   └── com/example/tinykv/...
    └── test/java/                  <- test code
        └── com/example/tinykv/...
```

**Coordinates.** Every artifact (yours and every dependency) is identified by **groupId** (your org, reverse-DNS style: `com.example`), **artifactId** (the project name: `tinykv`), and **version** (`1.0.0`). Dependencies are declared by their coordinates and Maven downloads them from the central repository into a local cache (`~/.m2/repository`).

**Lifecycle & phases.** Maven runs a fixed sequence of *phases*. Running a phase runs every phase before it. The default lifecycle, abbreviated:

`validate` → `compile` → `test` → `package` → `verify` → `install` → `deploy`

So `mvn package` automatically compiles and tests first. Useful commands:

| Command | What it does |
|---|---|
| `mvn compile` | compile `src/main/java` into `target/classes` |
| `mvn test` | compile + run tests in `src/test/java` |
| `mvn package` | the above + bundle a `.jar` into `target/` |
| `mvn clean` | delete `target/` |
| `mvn clean package` | wipe and rebuild from scratch |

**Plugins.** Phases don't *do* anything by themselves; they're bound to **plugin goals**. The compiler plugin compiles, the Surefire plugin runs tests, the Shade/Assembly plugin builds runnable "fat" jars. We'll use a couple of plugins below and you'll see how they bind to phases.

---

## 🛠️ Project Walkthrough — TinyKV

Follow these steps end to end — this is the hands-on build.

### Step 1 — Create the project layout

From wherever you keep code:

```bash
mkdir -p tinykv/src/main/java/com/example/tinykv
mkdir -p tinykv/src/test/java/com/example/tinykv
cd tinykv
```

### Step 2 — Write the `pom.xml`

Create `tinykv/pom.xml`. Read the inline comments — this is your Maven Rosetta Stone.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">

    <!-- The POM model version. Always 4.0.0 for Maven 3/4. -->
    <modelVersion>4.0.0</modelVersion>

    <!-- ===== Coordinates: how the world identifies THIS artifact ===== -->
    <groupId>com.example</groupId>
    <artifactId>tinykv</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <!-- Centralized settings reused below. -->
    <properties>
        <!-- Compile against and target Java 21. The `maven.compiler.release`
             property tells the compiler plugin to use the Java 21 API and
             bytecode level, and to verify we don't use newer APIs. -->
        <maven.compiler.release>21</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <!-- The class Maven should run with the exec plugin / fat jar. -->
        <main.class>com.example.tinykv.TinyKvDemo</main.class>
    </properties>

    <!-- ===== Dependencies, declared by coordinates ===== -->
    <dependencies>
        <!-- JUnit 5 (Jupiter), only needed when compiling/running tests.
             `scope=test` keeps it off the production classpath. -->
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>5.10.2</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <!-- Compiles src/main/java and src/test/java.
                 Bound by default to the `compile` and `test-compile` phases. -->
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.13.0</version>
            </plugin>

            <!-- Runs JUnit tests during the `test` phase. -->
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
            </plugin>

            <!-- Lets us run the app with `mvn exec:java` (handy for a demo). -->
            <plugin>
                <groupId>org.codehaus.mojo</groupId>
                <artifactId>exec-maven-plugin</artifactId>
                <version>3.2.0</version>
                <configuration>
                    <mainClass>${main.class}</mainClass>
                </configuration>
            </plugin>

            <!-- Builds a runnable "fat" jar (your classes + deps) at `package`.
                 After packaging you can run: java -jar target/tinykv-1.0.0.jar -->
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.3</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>shade</goal></goals>
                        <configuration>
                            <transformers>
                                <transformer
                                    implementation="org.apache.maven.plugins.shade.resource.ManifestResourceTransformer">
                                    <mainClass>${main.class}</mainClass>
                                </transformer>
                            </transformers>
                        </configuration>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

### Step 3 — Define the log record (modern Java: sealed interface + records)

A WAL record is one of a small, *closed* set of shapes — here `Put` and `Delete`. A **sealed interface** says "these are the only possible implementations", which lets the compiler give us exhaustive `switch` over them. Each variant is a **record** (an immutable data carrier; we'll go deeper on Day 5).

Create `src/main/java/com/example/tinykv/WalRecord.java`:

```java
package com.example.tinykv;

/**
 * One entry in the write-ahead log. Sealed so the compiler knows the
 * complete set of variants — see the exhaustive switch in TinyKvStore.replay().
 */
public sealed interface WalRecord permits WalRecord.Put, WalRecord.Delete {

    String key();

    /** Append a value for a key. */
    record Put(String key, String value) implements WalRecord {}

    /** Tombstone: remove a key. */
    record Delete(String key) implements WalRecord {}
}
```

### Step 4 — Serialize records to/from a line of text

We use a trivial, human-readable, line-oriented format so you can literally read the durability with `cat`. Each record is one line:

```text
PUT<TAB>key<TAB>value
DEL<TAB>key
```

To keep keys/values from breaking the format (tabs, newlines) we URL-encode them. This is *not* production-grade framing (real systems use length-prefixed binary + checksums — see the senior notes), but it's correct, readable, and enough to learn the concept.

Create `src/main/java/com/example/tinykv/WalCodec.java`:

```java
package com.example.tinykv;

import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/** Encodes a {@link WalRecord} to a single log line and back. */
final class WalCodec {

    private WalCodec() {}

    static String encode(WalRecord record) {
        return switch (record) {
            case WalRecord.Put(String k, String v) ->
                    "PUT\t" + enc(k) + "\t" + enc(v);
            case WalRecord.Delete(String k) ->
                    "DEL\t" + enc(k);
        };
    }

    static WalRecord decode(String line) {
        String[] parts = line.split("\t", -1);
        return switch (parts[0]) {
            case "PUT" -> new WalRecord.Put(dec(parts[1]), dec(parts[2]));
            case "DEL" -> new WalRecord.Delete(dec(parts[1]));
            default -> throw new IllegalArgumentException("Bad WAL line: " + line);
        };
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    private static String dec(String s) {
        return URLDecoder.decode(s, StandardCharsets.UTF_8);
    }
}
```

### Step 5 — The store: append-then-apply, and replay-on-open

This is the heart of the project. Note the ordering in every mutation: **append to the log and `fsync` *first*, then mutate the in-memory map.** If the process dies between the two, recovery still finds the record in the log and re-applies it — we never acknowledge a write we haven't logged.

Create `src/main/java/com/example/tinykv/TinyKvStore.java`:

```java
package com.example.tinykv;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * A tiny embeddable key-value store whose ONLY durability mechanism is an
 * append-only write-ahead log. State lives in an in-memory map; the log on
 * disk is the source of truth and is replayed on startup.
 *
 * Not thread-safe and not concurrency-tuned — that's Day 4. The point today
 * is durability via WAL.
 */
public final class TinyKvStore implements AutoCloseable {

    private final Path walPath;
    private final Map<String, String> state = new HashMap<>();

    // We keep two handles to the same file:
    //  - a buffered writer for convenient text appends, and
    //  - the underlying channel so we can call force(true) == fsync.
    private final FileChannel channel;
    private final BufferedWriter writer;

    public TinyKvStore(Path walPath) {
        this.walPath = walPath;
        try {
            // 1) Replay any existing log to rebuild in-memory state.
            replay();

            // 2) Open the log for appending. CREATE makes it if missing.
            this.channel = FileChannel.open(
                    walPath,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE,
                    StandardOpenOption.APPEND);
            this.writer = new BufferedWriter(
                    java.nio.channels.Channels.newWriter(channel, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to open WAL: " + walPath, e);
        }
    }

    /** Durably record a PUT, then apply it in memory. */
    public void put(String key, String value) {
        append(new WalRecord.Put(key, value));
        state.put(key, value);
    }

    /** Durably record a DELETE, then apply it in memory. */
    public void delete(String key) {
        append(new WalRecord.Delete(key));
        state.remove(key);
    }

    /** Reads are served entirely from memory. */
    public Optional<String> get(String key) {
        return Optional.ofNullable(state.get(key));
    }

    public int size() {
        return state.size();
    }

    /**
     * THE WAL RULE: write the log record and fsync it to stable storage
     * BEFORE the caller is allowed to consider the write done.
     */
    private void append(WalRecord record) {
        try {
            writer.write(WalCodec.encode(record));
            writer.newLine();
            writer.flush();          // push buffered bytes into the OS page cache
            channel.force(true);     // fsync: block until the device confirms durability
        } catch (IOException e) {
            throw new UncheckedIOException("WAL append failed", e);
        }
    }

    /** Rebuild state by applying every record in the log, in order. */
    private void replay() throws IOException {
        if (!Files.exists(walPath)) {
            return; // fresh store, empty log
        }
        List<String> lines = Files.readAllLines(walPath, StandardCharsets.UTF_8);
        for (String line : lines) {
            if (line.isBlank()) {
                continue; // tolerate a torn/blank final line from a crash
            }
            WalRecord record = WalCodec.decode(line);
            switch (record) {
                case WalRecord.Put(String k, String v) -> state.put(k, v);
                case WalRecord.Delete(String k)        -> state.remove(k);
            }
        }
    }

    @Override
    public void close() {
        try {
            writer.close(); // also closes the underlying channel
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to close WAL", e);
        }
    }
}
```

> Two subtle points worth internalizing:
> - **`flush()` is not `fsync`.** `flush()` only moves bytes from *our* Java buffer into the OS. Durability requires `channel.force(true)`, which is the `fsync`. Comment out the `force` line later and you'll see it's faster — and unsafe.
> - **Replay tolerates a blank/torn final line** but not a corrupt middle line. Real systems checksum each record so they can detect *and discard* a torn tail precisely; we lean on the fact that a crash can only damage the very end of an append-only file.

### Step 6 — A demo `main` that simulates a crash and recovery

Create `src/main/java/com/example/tinykv/TinyKvDemo.java`:

```java
package com.example.tinykv;

import java.nio.file.Path;
import java.util.Optional;

public final class TinyKvDemo {

    public static void main(String[] args) {
        Path wal = Path.of("wal.log");

        // ---- "Session 1": write some data, then we DON'T clean up — we just
        // let the JVM exit, simulating a crash with the WAL on disk. ----
        try (TinyKvStore store = new TinyKvStore(wal)) {
            System.out.println("Opened store. Current size: " + store.size());
            store.put("user:1", "Ada Lovelace");
            store.put("user:2", "Alan Turing");
            store.put("lang", "Java");
            store.delete("lang");          // changed our mind
            store.put("user:2", "Grace Hopper"); // overwrite

            System.out.println("After writes, size = " + store.size());
            System.out.println("user:1 = " + store.get("user:1").orElse("<none>"));
            System.out.println("lang   = " + store.get("lang").orElse("<none>"));
        }

        // ---- "Session 2": brand-new store object, same WAL file. Its entire
        // state is rebuilt by replaying the log — proving durability. ----
        try (TinyKvStore recovered = new TinyKvStore(wal)) {
            System.out.println("\n--- Reopened (simulating restart after crash) ---");
            System.out.println("Recovered size = " + recovered.size());
            print(recovered, "user:1");
            print(recovered, "user:2");
            print(recovered, "lang");   // should be gone — the DELETE replayed
        }
    }

    private static void print(TinyKvStore store, String key) {
        Optional<String> v = store.get(key);
        System.out.printf("  %-8s -> %s%n", key, v.orElse("<none>"));
    }
}
```

### Step 7 — A test to prove recovery works (and that the build runs tests)

Create `src/test/java/com/example/tinykv/TinyKvStoreTest.java`:

```java
package com.example.tinykv;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class TinyKvStoreTest {

    @Test
    void stateSurvivesReopen(@TempDir Path dir) {
        Path wal = dir.resolve("wal.log");

        // First "process": write and close.
        try (TinyKvStore store = new TinyKvStore(wal)) {
            store.put("a", "1");
            store.put("b", "2");
            store.delete("a");
            store.put("b", "22"); // overwrite
        }

        // Second "process": same file, fresh object → must replay to same state.
        try (TinyKvStore reopened = new TinyKvStore(wal)) {
            assertEquals(1, reopened.size());
            assertTrue(reopened.get("a").isEmpty(), "deleted key must stay gone");
            assertEquals("22", reopened.get("b").orElse(null), "last write wins");
        }
    }

    @Test
    void replayIsIdempotent(@TempDir Path dir) {
        Path wal = dir.resolve("wal.log");
        try (TinyKvStore s = new TinyKvStore(wal)) {
            s.put("k", "v");
        }
        // Reopen twice; replaying the same log again changes nothing.
        try (TinyKvStore s1 = new TinyKvStore(wal)) { assertEquals(1, s1.size()); }
        try (TinyKvStore s2 = new TinyKvStore(wal)) {
            assertEquals(1, s2.size());
            assertEquals("v", s2.get("k").orElse(null));
        }
    }
}
```

---

## How to run it

From the `tinykv/` directory:

**Run the tests** (exercises `compile` → `test-compile` → `test` phases):

```bash
mvn test
```

You should see `BUILD SUCCESS` and `Tests run: 2, Failures: 0`.

**Run the demo** the quick way (compiles, then runs `main`):

```bash
mvn -q compile exec:java
```

**Or build a runnable jar and run it like a real app:**

```bash
mvn clean package
java -jar target/tinykv-1.0.0.jar
```

### Expected output

First run (the WAL is created):

```text
Opened store. Current size: 0
After writes, size = 2
user:1 = Ada Lovelace
lang   = <none>

--- Reopened (simulating restart after crash) ---
Recovered size = 2
  user:1   -> Ada Lovelace
  user:2   -> Grace Hopper
  lang     -> <none>
```

Now **inspect the durable log** — this is the whole lesson in one file:

```bash
cat wal.log
```

```text
PUT	user%3A1	Ada+Lovelace
PUT	user%3A2	Alan+Turing
PUT	lang	Java
DEL	lang
PUT	user%3A2	Grace+Hopper
```

Notice: the log keeps the *full history* of operations (including the overwrite and the delete), while the in-memory map only holds the *current* state. The map is a materialized view of the log.

**Run it a second time** without deleting `wal.log`: the very first line now prints `Opened store. Current size: 2` — because session 1's writes from the *previous run* were replayed from disk. That's durability. Then it appends session 2's records, so the log keeps growing.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **This is exactly how real databases stay durable.** PostgreSQL writes changes to its WAL (`pg_wal/`) and `fsync`s it at commit; the actual table/index pages are flushed lazily during a *checkpoint*. After a crash, Postgres replays WAL from the last checkpoint forward. SQLite's WAL mode and most LSM-tree stores (RocksDB, Cassandra, LevelDB) work the same way: a WAL/commit-log for durability, plus in-memory structures flushed to disk later.
- **The log isn't just durability — it's a stream.** Kafka (Day 18) takes the idea to its logical end: the append-only, ordered log *is the database*, partitioned and replicated across machines. MySQL's binlog and Postgres' logical replication ship the WAL to replicas. This is the foundation of Event Sourcing (Day 19) and the Transactional Outbox (Day 20).
- **`fsync` is the throughput bottleneck, by design.** We `fsync` per write, so our write rate is capped by disk latency (often hundreds–low thousands of ops/sec). Real systems use **group commit**: batch many pending writes and `fsync` once for all of them, amortizing the cost. Postgres' `commit_delay`, Kafka's `linger.ms`, and Redis' `appendfsync everysec` are all tunable points on the durability↔throughput curve.
- **Our text format is naive on purpose.** Production WALs use **length-prefixed binary records with a CRC checksum** so recovery can validate each record and cleanly truncate a torn tail after a crash. They also handle partial writes, log rotation/segmentation, and bounded growth.
- **Unbounded log growth is the real problem to solve next.** Our `wal.log` grows forever and replay gets slower over time. The fixes: **checkpointing/snapshotting** (periodically dump the in-memory map to disk and truncate the log up to that point) and **compaction** (drop superseded `PUT`s and tombstoned keys). LSM trees do this continuously. You now understand why.
- **Why log *before* data?** If you wrote the data structure first and crashed mid-write, you could corrupt it irrecoverably and *also* not know what you'd lost. Logging first means the worst case is "redo a few already-described operations" — always recoverable. This is the WAL invariant: *no data-page change reaches disk before the log record describing it.*

### Stretch goals

1. **Add checkpointing.** Add `void checkpoint()` that writes the current map to a `snapshot` file and truncates/replaces the WAL, then make startup load the snapshot first and replay only the WAL records after it. Measure how recovery time changes.
2. **Add per-record CRC32 checksums** (`java.util.zip.CRC32`) and make `replay()` *stop* at the first record whose checksum doesn't match (simulating a torn tail), instead of crashing. Write a test that appends garbage bytes to the end of the log and asserts recovery still succeeds with the valid prefix.
3. **Benchmark `fsync`.** Time 10,000 `put`s with `channel.force(true)` vs. with it commented out. Quantify the durability tax in ops/sec, then implement a simple group-commit `putBatch(List<...>)` that `fsync`s once.
4. **Switch to length-prefixed binary records** using `ByteBuffer` and the `FileChannel` directly (no text codec), and compare file size and parse speed against the text format.

### Next up

*Next up — **Day 2: Collections/Generics & a Hash Index (JUnit5).** Reads currently scan a plain `HashMap`; tomorrow you'll build your own hash index from scratch, master Java generics and the Collections framework, and write a real JUnit 5 test suite around it.*
