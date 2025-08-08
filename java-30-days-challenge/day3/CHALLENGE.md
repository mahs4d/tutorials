# Day 3: Streams & a Volcano Query Iterator

| | |
|---|---|
| 🏗️ **Project** | **MiniQL** — a Volcano-model in-memory query engine |
| ☕ **Java & language skills** | Streams API, functional interfaces, lambdas, lazy pipelines, Optional, generics |
| 🧰 **Library / tool** | AssertJ (fluent assertions, alongside JUnit 5) |
| 🗄️ **DB / distributed-systems concept** | Volcano / iterator model of query execution (pull-based operators) |
| 📊 **Difficulty** | Easy |

---

## Where we are in the journey

On **Day 1** you wrote a write-ahead log and met Maven. On **Day 2** you built a hash index and learned JUnit 5 — `@Test`, `@DisplayName`, lifecycle hooks. You wrote assertions like `assertEquals(expected, index.get(key))`.

Today we stay close to the data-plane of a database but move up one layer: from *how rows are stored and found* to **how a query reads them**. We will build a tiny query engine and discover that the execution model relational databases have used since the late 1980s is the same mental model behind `java.util.stream.Stream`. Then we'll re-test the whole thing with AssertJ and feel why fluent assertions matter the moment a test fails.

---

## Concept primer

### 1. A query is a tree of operators

When you run:

```sql
SELECT name, age          -- Project
FROM users                -- Scan
WHERE age >= 30           -- Filter
LIMIT 3;                  -- Limit
```

the database does **not** execute this top-to-bottom as written. It compiles it into a *physical plan* — a tree of **operators**:

```
        LimitOp(3)
            |
        ProjectOp(name, age)
            |
        FilterOp(age >= 30)
            |
        ScanOp(users)
```

Each node is an **operator**, and in the classic **Volcano model** (Goetz Graefe, 1990 — also called the *iterator model*) every operator implements the same minimal interface:

```
open()        // prepare (acquire file handles, reset cursors)
next() -> Row // produce the next row, or signal "no more"
close()       // release resources
```

We'll fold `open`/`close` into construction and just focus on `next()` returning `Optional<Row>` (empty = end of stream).

### 2. Pull-based execution

Control flows **top-down**, data flows **bottom-up**. The consumer (or the root operator) calls `next()` on `LimitOp`. `LimitOp` calls `next()` on `ProjectOp`, which calls `next()` on `FilterOp`, which calls `next()` on `ScanOp`. `ScanOp` reads *one* row from the underlying list and hands it back up. Each operator transforms or drops the row and returns.

This is **demand-driven / pull-based**: nothing is computed until something upstream asks for it. Key consequences:

- **Lazy.** `FilterOp` doesn't scan the whole table and build an intermediate list. It pulls rows one at a time and loops internally until one passes the predicate.
- **Pipelined.** A single row can flow Scan → Filter → Project → out, all the way to the consumer, before the *second* row is ever touched. No giant intermediate buffers.
- **Early termination.** `LIMIT 3` means `LimitOp` returns `Optional.empty()` after emitting 3 rows. The operators below it are simply never asked again — the scan stops mid-table. This is exactly why `SELECT ... LIMIT 1` over a billion-row table can return instantly.

### 3. Java Streams ARE the Volcano model

A `Stream` pipeline is built from:

- **A source** — `list.stream()` ≈ `ScanOp`.
- **Intermediate operations** — `filter`, `map`, `limit`, `sorted`, `distinct`. These are **lazy**: they return a new `Stream` and record *what to do*, but execute nothing. `filter` ≈ `FilterOp`, `map` ≈ `ProjectOp`, `limit` ≈ `LimitOp`.
- **A terminal operation** — `collect`, `reduce`, `forEach`, `count`, `findFirst`. **Eager**: it triggers the actual pull. Without a terminal op, *zero* elements are processed.

The JDK literally implements streams with a pull/push hybrid over `Spliterator` + `Sink` chains, and it does early termination too: `stream().filter(...).limit(3)` stops as soon as 3 elements pass — just like `LimitOp`.

| Volcano operator | Stream op | Lazy/Eager |
|---|---|---|
| `ScanOp` (source) | `list.stream()` | source |
| `FilterOp` | `.filter(pred)` | lazy intermediate |
| `ProjectOp` | `.map(fn)` | lazy intermediate |
| `LimitOp` | `.limit(n)` | lazy intermediate (short-circuiting) |
| consumer loop | `.collect(toList())` / `.reduce(...)` | **eager terminal** |

The point of today: you'll build the iterator engine by hand to *see* the laziness explicitly (a counter proving the scan stops early), then write the same query as a one-liner Stream pipeline and assert the two produce identical results.

---

## Prerequisites

- JDK 21 installed (`java -version` shows 21).
- Maven (Day 1).
- Familiarity with `record` types and `Optional`. We'll use `record Row(...)`. (Records get a full treatment on Day 5; today we just use them as immutable data carriers.)

---

---

## 🛠️ Project Walkthrough — MiniQL

Roll up your sleeves: from here you'll build the operator tree file by file, then run it and check the output.

## Step 0 — Project layout

```
day3/
├── pom.xml
└── src
    ├── main/java/dev/days/day3/
    │   ├── Row.java
    │   ├── Operator.java
    │   ├── ScanOp.java
    │   ├── FilterOp.java
    │   ├── ProjectOp.java
    │   ├── LimitOp.java
    │   ├── StreamEngine.java
    │   └── Users.java
    └── test/java/dev/days/day3/
        └── QueryEngineTest.java
```

---

## Step 1 — `pom.xml` (adding AssertJ next to JUnit 5)

JUnit 5 came on Day 2. Today we add **AssertJ** as a test-scoped dependency. The `assertj-core` artifact is all you need — it transitively gives you `org.assertj.core.api.Assertions.assertThat`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>dev.days</groupId>
    <artifactId>day3-volcano</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.release>21</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <junit.version>5.10.2</junit.version>
        <assertj.version>3.26.3</assertj.version>
    </properties>

    <dependencies>
        <!-- JUnit 5 (from Day 2) -->
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>${junit.version}</version>
            <scope>test</scope>
        </dependency>

        <!-- AssertJ — new today: fluent assertions -->
        <dependency>
            <groupId>org.assertj</groupId>
            <artifactId>assertj-core</artifactId>
            <version>${assertj.version}</version>
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

---

## Step 2 — The `Row` record

A row is just an ordered, named bag of values. To keep operators generic and to make `ProjectOp` (column selection) trivial, we model a row as an immutable map of column name → value.

```java
package dev.days.day3;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * An immutable tuple: ordered column-name -> value.
 * LinkedHashMap preserves column order (matters for projection & display).
 */
public record Row(Map<String, Object> columns) {

    public Row {
        // Defensive copy so callers can't mutate our data after construction.
        columns = new LinkedHashMap<>(columns);
    }

    public Object get(String column) {
        return columns.get(column);
    }

    /** Convenience factory: Row.of("name", "Ada", "age", 36) */
    public static Row of(Object... kv) {
        if (kv.length % 2 != 0) {
            throw new IllegalArgumentException("Row.of requires key/value pairs");
        }
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return new Row(m);
    }
}
```

> Note: the compact canonical constructor reassigns `columns` to a defensive copy. Records make the *fields* final, but a `Map` reference is only as immutable as the map it points at — so we copy on the way in. (More record nuance on Day 5.)

---

## Step 3 — The `Operator` interface

This is the heart of the Volcano model. One method. `Optional.empty()` is our "end of stream" sentinel.

```java
package dev.days.day3;

import java.util.Optional;

/**
 * Volcano / iterator-model operator.
 * Each call to next() pulls one row from below, or returns empty when exhausted.
 * Pull-based: data flows UP only when next() is called from above.
 */
public interface Operator {
    Optional<Row> next();
}
```

---

## Step 4 — `ScanOp` (the leaf / source)

Reads rows from an in-memory list, one per `next()` call. A counter (`rowsRead`) lets our test *prove* laziness — that a `LIMIT 3` query never scans the whole table.

```java
package dev.days.day3;

import java.util.List;
import java.util.Optional;

/** Leaf operator: streams rows out of an in-memory list. */
public final class ScanOp implements Operator {

    private final List<Row> source;
    private int cursor = 0;
    private int rowsRead = 0;

    public ScanOp(List<Row> source) {
        this.source = source;
    }

    @Override
    public Optional<Row> next() {
        if (cursor >= source.size()) {
            return Optional.empty();
        }
        rowsRead++;
        return Optional.of(source.get(cursor++));
    }

    /** How many rows this scan actually touched — used to demonstrate laziness. */
    public int rowsRead() {
        return rowsRead;
    }
}
```

---

## Step 5 — `FilterOp` (WHERE)

Wraps a child operator. On `next()`, it loops — pulling from the child until a row passes the predicate, or the child is exhausted. This internal loop is the operator "swallowing" rows that don't match.

```java
package dev.days.day3;

import java.util.Optional;
import java.util.function.Predicate;

/** WHERE clause: emits only child rows that satisfy the predicate. */
public final class FilterOp implements Operator {

    private final Operator child;
    private final Predicate<Row> predicate;

    public FilterOp(Operator child, Predicate<Row> predicate) {
        this.child = child;
        this.predicate = predicate;
    }

    @Override
    public Optional<Row> next() {
        Optional<Row> row;
        while ((row = child.next()).isPresent()) {
            if (predicate.test(row.get())) {
                return row;
            }
            // didn't match -> keep pulling
        }
        return Optional.empty();
    }
}
```

---

## Step 6 — `ProjectOp` (SELECT columns)

Pulls one row, returns a new row containing only the requested columns (in the requested order).

```java
package dev.days.day3;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/** SELECT col1, col2, ... : narrows each row to the chosen columns. */
public final class ProjectOp implements Operator {

    private final Operator child;
    private final List<String> columns;

    public ProjectOp(Operator child, List<String> columns) {
        this.child = child;
        this.columns = List.copyOf(columns);
    }

    @Override
    public Optional<Row> next() {
        return child.next().map(this::project);
    }

    private Row project(Row in) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (String col : columns) {
            out.put(col, in.get(col));
        }
        return new Row(out);
    }
}
```

---

## Step 7 — `LimitOp` (LIMIT)

Counts emitted rows; after `n` it returns empty *without* asking the child again. This short-circuit is what stops the scan early.

```java
package dev.days.day3;

import java.util.Optional;

/** LIMIT n : emits at most n rows, then stops pulling from the child. */
public final class LimitOp implements Operator {

    private final Operator child;
    private final long limit;
    private long emitted = 0;

    public LimitOp(Operator child, long limit) {
        this.child = child;
        this.limit = limit;
    }

    @Override
    public Optional<Row> next() {
        if (emitted >= limit) {
            return Optional.empty();   // short-circuit: child is never asked again
        }
        Optional<Row> row = child.next();
        if (row.isPresent()) {
            emitted++;
        }
        return row;
    }
}
```

---

## Step 8 — Driving the tree (the consumer loop)

A small helper that drains an operator tree into a list. This is the "client" sitting above the root operator, repeatedly calling `next()` until empty. Add it as a static method anywhere; we'll put it on a `Query` utility.

```java
package dev.days.day3;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public final class Query {
    private Query() {}

    /** Pull rows from the root operator until exhausted. */
    public static List<Row> run(Operator root) {
        List<Row> out = new ArrayList<>();
        Optional<Row> row;
        while ((row = root.next()).isPresent()) {
            out.add(row.get());
        }
        return out;
    }
}
```

---

## Step 9 — The same query with Java Streams

Now the punchline. The entire `Scan → Filter → Project → Limit` tree is one Stream pipeline. Notice the structural one-to-one mapping.

```java
package dev.days.day3;

import java.util.List;
import java.util.function.Predicate;
import java.util.stream.Collectors;

/** Expresses the same query as a lazy, pipelined Java Stream. */
public final class StreamEngine {
    private StreamEngine() {}

    public static List<Row> selectWhereLimit(
            List<Row> source,
            Predicate<Row> where,
            List<String> projectColumns,
            long limit) {

        return source.stream()                    // ScanOp
                .filter(where)                     // FilterOp
                .map(r -> project(r, projectColumns)) // ProjectOp
                .limit(limit)                      // LimitOp (short-circuits)
                .collect(Collectors.toList());     // terminal -> triggers the pull
    }

    private static Row project(Row in, List<String> columns) {
        var m = new java.util.LinkedHashMap<String, Object>();
        for (String c : columns) {
            m.put(c, in.get(c));
        }
        return new Row(m);
    }
}
```

> Subtle ordering note: in this Stream version `filter` runs before `limit`, exactly like SQL (`WHERE` then `LIMIT`). If you swapped `.limit(limit)` *before* `.filter(...)` you'd get different semantics — limit the raw scan first, then filter. Operator ordering is a real query-planning concern.

---

## Step 10 — Sample data

```java
package dev.days.day3;

import java.util.List;

public final class Users {
    private Users() {}

    public static List<Row> sample() {
        return List.of(
            Row.of("id", 1, "name", "Ada",   "age", 36),
            Row.of("id", 2, "name", "Linus", "age", 24),
            Row.of("id", 3, "name", "Grace", "age", 45),
            Row.of("id", 4, "name", "Dennis","age", 41),
            Row.of("id", 5, "name", "Margaret","age", 29),
            Row.of("id", 6, "name", "Edsger","age", 52)
        );
    }
}
```

---

## Step 11 — The tests: JUnit 5 + AssertJ

Here is the day's tooling payoff. We test:

1. The Volcano tree produces the right rows.
2. The Stream version produces **identical** rows.
3. Laziness: a `LIMIT 2` query does **not** scan all 6 rows.

```java
package dev.days.day3;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.function.Predicate;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("Volcano iterator engine vs. Stream engine")
class QueryEngineTest {

    private final List<Row> users = Users.sample();
    private final Predicate<Row> age30Plus = r -> (int) r.get("age") >= 30;
    private final List<String> nameAndAge = List.of("name", "age");

    @Test
    @DisplayName("Volcano tree: SELECT name, age WHERE age>=30 LIMIT 3")
    void volcanoTree() {
        Operator plan =
            new LimitOp(
                new ProjectOp(
                    new FilterOp(
                        new ScanOp(users),
                        age30Plus),
                    nameAndAge),
                3);

        List<Row> result = Query.run(plan);

        // --- AssertJ: read it like a sentence ---
        assertThat(result)
            .hasSize(3)
            .extracting(r -> r.get("name"))
            .containsExactly("Ada", "Grace", "Dennis");

        // every projected row has exactly the two requested columns
        assertThat(result)
            .allSatisfy(row ->
                assertThat(row.columns().keySet())
                    .containsExactly("name", "age"));
    }

    @Test
    @DisplayName("Stream engine produces identical rows to the Volcano tree")
    void streamMatchesVolcano() {
        Operator plan =
            new LimitOp(
                new ProjectOp(
                    new FilterOp(new ScanOp(users), age30Plus),
                    nameAndAge),
                3);
        List<Row> volcano = Query.run(plan);

        List<Row> streamed =
            StreamEngine.selectWhereLimit(users, age30Plus, nameAndAge, 3);

        // Records implement equals() structurally -> Rows compare by content.
        assertThat(streamed).containsExactlyElementsOf(volcano);
    }

    @Test
    @DisplayName("Laziness: LIMIT 2 stops the scan early — not all rows are read")
    void limitShortCircuitsTheScan() {
        ScanOp scan = new ScanOp(users);   // hold a reference to inspect rowsRead()
        Operator plan =
            new LimitOp(
                new ProjectOp(
                    new FilterOp(scan, age30Plus),
                    nameAndAge),
                2);

        List<Row> result = Query.run(plan);

        assertThat(result)
            .extracting(r -> r.get("name"))
            .containsExactly("Ada", "Grace");

        // Ada(36) passes, Linus(24) rejected, Grace(45) passes -> we stop at row 3.
        // The scan touched only 3 of 6 rows. THAT is pull-based laziness.
        assertThat(scan.rowsRead())
            .as("scan should stop early once LIMIT is satisfied")
            .isEqualTo(3)
            .isLessThan(users.size());
    }

    @Test
    @DisplayName("AssertJ vs plain JUnit: why fluent reads better")
    void fluentVsPlain() {
        List<Row> result =
            StreamEngine.selectWhereLimit(users, age30Plus, nameAndAge, 10);

        // One fluent chain expresses size + ordering + extraction.
        assertThat(result)
            .hasSize(4)
            .extracting(r -> r.get("name"))
            .containsExactly("Ada", "Grace", "Dennis", "Edsger")
            .doesNotContain("Linus", "Margaret");
    }
}
```

### Why AssertJ over plain assertions (the day's tooling lesson)

Compare the *same* check written both ways:

```java
// JUnit 5 plain (Day 2 style)
assertEquals(3, result.size());
assertEquals("Ada",    result.get(0).get("name"));
assertEquals("Grace",  result.get(1).get("name"));
assertEquals("Dennis", result.get(2).get("name"));
```

```java
// AssertJ (today)
assertThat(result)
    .hasSize(3)
    .extracting(r -> r.get("name"))
    .containsExactly("Ada", "Grace", "Dennis");
```

Concretely AssertJ wins on:

- **Readability** — one statement reads as a sentence; subject first (`assertThat(result)`), then expectations chained.
- **One subject, many checks** — no repeating `result.get(i)`.
- **`extracting`** — pull a field out of every element and assert on the collection of those fields. No manual loop.
- **Better failure messages.** A failed `containsExactly` prints the *full expected vs. actual list and the index of the first mismatch* — e.g. `expecting: ["Ada","Grace","Dennis"] but was: ["Ada","Dennis"]`. Plain `assertEquals` on `.get(1)` just says `expected <Grace> but was <Dennis>` and hides the rest.
- **`.as(...)` descriptions** — attach human context to *why* an assertion matters (see `rowsRead` test).
- **IDE discoverability** — type `assertThat(list).` and autocomplete shows `hasSize`, `contains`, `extracting`, `allSatisfy`, ... — assertions you'd otherwise hand-roll.

> One gotcha: `org.junit.jupiter.api.Assertions.assertThat` does **not** exist — `assertThat` comes from `org.assertj.core.api.Assertions`. Keep `assertEquals`/`assertThrows` from JUnit if you like, but import `assertThat` from AssertJ.

---

## Step 12 — Run it

```bash
cd day3
mvn test
```

### Expected output

```
[INFO] -------------------------------------------------------
[INFO]  T E S T S
[INFO] -------------------------------------------------------
[INFO] Running dev.days.day3.QueryEngineTest
[INFO] Tests run: 4, Failures: 0, Errors: 0, Skipped: 0 ...
[INFO]
[INFO] Results:
[INFO] Tests run: 4, Failures: 0, Errors: 0, Skipped: 0
[INFO]
[INFO] BUILD SUCCESS
```

To *see* the laziness yourself, temporarily add a `System.out.println("scan row " + cursor)` inside `ScanOp.next()` and run the `LIMIT 2` test — you'll see exactly **three** "scan row" lines printed, then silence: rows 4–6 are never read.

---

---

## 🚀 Going Deeper & Next Steps

## Going deeper / senior-level notes

**Pull vs. push execution.** The Volcano model is *pull-based*: the consumer drives, calling `next()` down the tree. The modern alternative is *push-based* (a.k.a. *data-centric* or *produce/consume*), popularized by Thomas Neumann's HyPer (2011): each operator *pushes* tuples to its parent's `consume()` callback. Push fuses operator pipelines into tight machine-code loops with no virtual `next()` call per row per operator, keeping data in CPU registers across operators. Spark, DuckDB-style engines, and many JIT-compiling databases use push.

**The cost Volcano pays: one virtual call per row, per operator.** A 6-row toy is fine. Over a billion rows, `next()` is invoked billions of times across the tree — each a polymorphic dispatch, with the row "alive" for only one operator at a time. That's a lot of branch misprediction and instruction overhead relative to the actual work (comparing an int). This per-tuple interpretation overhead is *the* reason classic Volcano engines became CPU-bound.

**Vectorized execution.** MonetDB/X100 (2005) and its descendants (DuckDB, Apache Arrow, ClickHouse, Velox) keep the pull structure but change the *granularity*: `next()` returns a **batch/vector of ~1024 rows** instead of one. Now the per-call overhead amortizes over a thousand rows, the inner loops are tight and branch-predictable, and the columnar layout lets the CPU/SIMD chew through them. Vectorization + columnar storage is why analytical engines are often 10–100× faster than row-at-a-time Volcano for scans/aggregations.

**Where did real DBs go?** Two dominant modern strategies: (1) **vectorized interpretation** (DuckDB, Velox, ClickHouse) — easy to build, great cache behavior; (2) **JIT query compilation** (HyPer, Umbra, Spark Tungsten's whole-stage codegen) — compile the plan to native/bytecode at runtime, eliminating interpretation entirely. PostgreSQL still uses a (recently tweaked) Volcano-style executor with optional LLVM JIT for expressions — pragmatic and good enough for OLTP, where row counts per query are modest.

**The Stream analogy holds — and breaks.** Java `Stream` is conceptually pull+short-circuit like Volcano, and `.parallel()` even gives you a (work-stealing, fork/join) parallel executor for free. But streams are general-purpose: they pay the same per-element megamorphic-lambda cost a naive Volcano engine does, and they are *not* vectorized. Great for clarity; not how you'd build a high-throughput query engine. The lesson is the *model*, not the performance.

**Blocking vs. streaming operators.** `ScanOp`/`FilterOp`/`ProjectOp`/`LimitOp` are *pipeline-able* (streaming) — a row flows straight through. But `SORT`, `GROUP BY`, `JOIN` (hash-build side), and `DISTINCT` are **pipeline breakers**: they must consume their *entire* input before they can emit the first output row. In Streams, `sorted()` and `collect(groupingBy(...))` are the equivalent blocking ops — which is also why an *infinite* stream works with `filter().limit()` but hangs on `.sorted()`.

---

## Stretch goals

1. **`SortOp` (a pipeline breaker).** Implement an `ORDER BY` operator. Notice you *must* drain the whole child in the constructor (or first `next()`) into a list, sort it, then emit. Add an AssertJ test asserting `isSortedAccordingTo(...)`. Then try the same with `stream().sorted(comparing(...))` and prove a `Stream.iterate(...)` (infinite) source hangs on `.sorted()` but not on `.limit()`.
2. **`AggregateOp` (`COUNT`, `SUM`, `AVG`).** Add a grouping/aggregating operator and compare to `Collectors.groupingBy(..., averagingInt(...))`. Use AssertJ's `Map` assertions (`containsEntry`, `hasSize`).
3. **An `EXPLAIN` printer.** Give each operator a `toExplainString(int depth)` and print the indented operator tree — exactly what `EXPLAIN <query>` shows in Postgres. Assert the tree shape with AssertJ `as(...)` descriptions.
4. **A fluent builder DSL.** Wrap operator construction in `QueryBuilder.scan(users).filter(p).project("name","age").limit(3).run()`, so plans read top-down like SQL instead of nesting inside-out. Compare ergonomics to the raw Stream pipeline.

---

## Day 4 teaser

Today's engine pulled rows on a **single thread**. Tomorrow — **Day 4: Concurrency / Producer–Consumer** — we let a *producer* thread scan rows and push them onto a `BlockingQueue` while a *consumer* thread drains them, the classic decoupling pattern. We'll meet `Thread`, `ExecutorService`, `BlockingQueue`, and the perils of shared mutable state — the foundations every database connection pool, WAL flusher, and Kafka consumer (Day 18) is built on.
