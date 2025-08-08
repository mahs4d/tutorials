# Day 11: JDBC, Transactions & Isolation Levels

| | |
|---|---|
| 🏗️ **Project** | **BankLedger** — a money-transfer demo exploring isolation-level anomalies |
| ☕ **Java & language skills** | JDBC, JdbcTemplate, RowMapper, parameterized queries, programmatic transactions, threads for concurrency demo |
| 🧰 **Library / tool** | Spring JDBC (JdbcTemplate) + H2 |
| 🗄️ **DB / distributed-systems concept** | ACID transactions & SQL isolation levels (dirty/non-repeatable/phantom reads) |
| 📊 **Difficulty** | Medium |

---

## Concept primer: ACID, transactions, and isolation

### What a transaction is (and why)

A **transaction** is a unit of work that the database promises to treat as a single, indivisible operation. The classic example — and our project — is a bank transfer: *debit account A by 100, credit account B by 100*. These are two `UPDATE` statements, but they must succeed or fail **together**. If the process crashes between them, you have either created or destroyed money.

On **Day 1** you built a write-ahead log (WAL); that machinery exists precisely so a database can promise the four **ACID** properties:

- **Atomicity** — all statements in the transaction commit, or none do. The WAL + rollback segment is how the engine "undoes" a partial transaction.
- **Consistency** — the transaction moves the DB from one valid state to another, honouring constraints (FKs, checks, your invariant that total money is conserved). This one is partly the application's job.
- **Isolation** — concurrent transactions don't step on each other; each runs *as if* it were alone. The *degree* to which this is true is the **isolation level**, and it's the heart of today.
- **Durability** — once committed, the data survives a crash. That's the WAL `fsync` from Day 1.

In JDBC, a transaction is bounded by **autocommit**. By default every JDBC `Connection` is in `autocommit = true`: each statement is its own transaction. To group statements you call `connection.setAutoCommit(false)`, run your statements, then `commit()` or `rollback()`. Spring's `DataSourceTransactionManager` does exactly this for you and ties the connection to the current thread so every `JdbcTemplate` call inside the boundary uses the *same* connection.

### Isolation is a trade-off, not a setting you "turn up"

If every transaction ran strictly one-at-a-time (truly serial), there would be no anomalies — and no throughput. Real engines run transactions concurrently and let you choose **how much concurrency anomaly you tolerate in exchange for speed**. The ANSI standard defines the menu in terms of *which anomalies are allowed*:

| Isolation level     | Dirty read | Non-repeatable read | Phantom read | (Lost update*) |
|---------------------|:----------:|:-------------------:|:------------:|:--------------:|
| READ UNCOMMITTED    | **allowed**    | allowed             | allowed      | allowed        |
| READ COMMITTED      | prevented  | **allowed**             | allowed      | allowed        |
| REPEATABLE READ     | prevented  | prevented           | **allowed***    | prevented**     |
| SERIALIZABLE        | prevented  | prevented           | prevented    | prevented      |

\* The ANSI standard never listed *lost update* or *write skew*; they were added by Berenson et al. ("A Critique of ANSI SQL Isolation Levels", 1995). And the standard defines `REPEATABLE READ` as *allowing* phantoms, but real MVCC engines (Postgres, MySQL/InnoDB) prevent phantoms at `REPEATABLE READ` for *snapshot* reads — see the MVCC note below. The standard is a floor, not a spec of behaviour.

The anomalies, concretely:

- **Dirty read** — you read a row another transaction has written but **not yet committed**. If that other transaction rolls back, you acted on data that *never existed*. Prevented at `READ COMMITTED` and above.
- **Non-repeatable read** — you read row X (balance = 100), another transaction commits an update to X, you read X again *in the same transaction* and get a **different value** (balance = 50). The same query, two answers. Prevented at `REPEATABLE READ`.
- **Phantom read** — you run a *range/predicate* query (`WHERE balance > 100`) and get N rows; another transaction commits a new row matching the predicate; you re-run the query and get **N+1 rows** — a "phantom" appeared. Prevented at `SERIALIZABLE` (and at `REPEATABLE READ` in snapshot engines).
- **Lost update** — two transactions read the same balance (100), each adds 10 in app code, each writes 110. One write silently clobbers the other; the result is 110 instead of 120. *One update was lost.* This is the one that bites bank transfers, and read-modify-write code generally.

### How this relates to MVCC (Day 5) — the "why"

On **Day 5** you implemented snapshot isolation with MVCC: instead of locking, each transaction sees a **consistent snapshot** of the database as of the moment it started (or as of each statement), and writers create *new row versions* rather than overwriting. Readers never block writers and writers never block readers.

That design is *why* the table above has those asterisks. A snapshot engine like Postgres implements:

- `READ COMMITTED` as **"a fresh snapshot per statement."** Each statement sees everything committed before it began — so no dirty reads, but two statements in the same transaction can see different data (non-repeatable read is allowed).
- `REPEATABLE READ` as **"one snapshot for the whole transaction."** Every statement sees the database as of transaction start. Because the snapshot is frozen, you get repeatable reads *and* — as a free side effect — no phantoms either, which is *stronger* than the ANSI standard requires.

The cost is that snapshot isolation can't detect every serialization problem (notably **write skew**), so the strongest level needs more than a frozen snapshot:

- Postgres `SERIALIZABLE` uses **SSI (Serializable Snapshot Isolation)** — it keeps the snapshot model for performance but *tracks read/write dependencies between transactions* and **aborts** one of a pair that would have produced a non-serializable schedule, with `ERROR: could not serialize access`. So at `SERIALIZABLE` you must be prepared to **retry**.

Key senior takeaway: lock-based isolation (old SQL Server / DB2) and MVCC-based isolation can both satisfy the *same ANSI level name* with very different runtime behaviour and failure modes. "We use `REPEATABLE READ`" tells you the anomaly guarantees, not the mechanism. Always know which engine you're on.

---

## Prerequisites

- The Day 9 Boot app (HikariCP datasource over H2). We'll reuse the `pom.xml` and `application.properties`.
- JDK 17+, Maven.
- Understanding of Day 5 (MVCC/snapshot isolation) — we lean on it heavily.

> **H2 gotcha that matters for the demos:** the *default* in-memory H2 (`jdbc:h2:mem:test`) is single-connection-per-DB unless you keep it alive, and H2's MVCC behaviour at `REPEATABLE READ`/`SERIALIZABLE` differs from Postgres. To see real concurrency we run H2 in **TCP server mode** (or use `DB_CLOSE_DELAY=-1` so the in-memory DB outlives the first connection). If you want textbook MVCC behaviour, point the same code at **Postgres via Testcontainers** — which is exactly what Day 23 will formalize.

### Maven dependencies

Building on Day 9's `pom.xml`, you need `spring-boot-starter-jdbc` (which pulls in `spring-jdbc` + HikariCP) and the H2 driver:

```xml
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-jdbc</artifactId>
    </dependency>
    <dependency>
        <groupId>com.h2database</groupId>
        <artifactId>h2</artifactId>
    </dependency>

    <!-- Optional: run the same demos against real Postgres MVCC -->
    <dependency>
        <groupId>org.postgresql</groupId>
        <artifactId>postgresql</artifactId>
        <scope>runtime</scope>
        <optional>true</optional>
    </dependency>

    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-test</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>
```

### `application.properties`

We use H2 in server-compatible mode and keep the DB alive so two connections share state. `AUTO_SERVER=TRUE` lets multiple JVM connections (and your threads) hit one DB; `DB_CLOSE_DELAY=-1` stops H2 from dropping the in-memory DB when the first connection closes.

```properties
spring.datasource.url=jdbc:h2:mem:bank;DB_CLOSE_DELAY=-1;DB_CLOSE_ON_EXIT=FALSE
spring.datasource.driver-class-name=org.h2.Driver
spring.datasource.username=sa
spring.datasource.password=

# Bigger pool than Day 9's default so concurrent demos don't starve
spring.datasource.hikari.maximum-pool-size=10

# Let us see SQL while learning
logging.level.org.springframework.jdbc.core.JdbcTemplate=DEBUG
```

### Schema SQL — `src/main/resources/schema.sql`

Spring Boot runs this automatically at startup (it picks up `schema.sql`/`data.sql` on the classpath).

```sql
DROP TABLE IF EXISTS account;

CREATE TABLE account (
    id      BIGINT PRIMARY KEY,
    owner   VARCHAR(64) NOT NULL,
    balance DECIMAL(15, 2) NOT NULL,
    version BIGINT NOT NULL DEFAULT 0,   -- used in the optimistic-locking demo
    CHECK (balance >= 0)                 -- the invariant transactions must preserve
);

INSERT INTO account (id, owner, balance) VALUES (1, 'Alice', 1000.00);
INSERT INTO account (id, owner, balance) VALUES (2, 'Bob',   1000.00);
```

---

---

## 🛠️ Project Walkthrough — BankLedger

Roll up your sleeves: from here we build the bank app step by step, then run it and reproduce each anomaly live.

## Step 1 — A domain object and a `RowMapper`

A `RowMapper<T>` is the single-responsibility callback Spring calls **once per row** to turn a `ResultSet` row into your object. It's the idiomatic seam between SQL and Java.

```java
package com.example.day11;

import java.math.BigDecimal;

public record Account(long id, String owner, BigDecimal balance, long version) {}
```

```java
package com.example.day11;

import org.springframework.jdbc.core.RowMapper;

import java.sql.ResultSet;
import java.sql.SQLException;

public class AccountRowMapper implements RowMapper<Account> {
    @Override
    public Account mapRow(ResultSet rs, int rowNum) throws SQLException {
        return new Account(
                rs.getLong("id"),
                rs.getString("owner"),
                rs.getBigDecimal("balance"),
                rs.getLong("version"));
    }
}
```

## Step 2 — A repository with parameterized queries (no SQL injection)

The cardinal rule: **never** build SQL by string-concatenating user input. A query like
`"SELECT * FROM account WHERE owner = '" + owner + "'"` lets an attacker pass `x' OR '1'='1` and read everything — or `x'; DROP TABLE account; --`. Parameterized queries (`?` placeholders) send the SQL *structure* and the *values* over separate channels; the value is never parsed as SQL. `JdbcTemplate` parameterizes by default — use it.

```java
package com.example.day11;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.util.List;

@Repository
public class AccountRepository {

    private final JdbcTemplate jdbc;
    private final AccountRowMapper mapper = new AccountRowMapper();

    public AccountRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    // queryForObject -> exactly one row expected. The 'id' is a BOUND parameter, not concatenated.
    public Account findById(long id) {
        return jdbc.queryForObject(
                "SELECT id, owner, balance, version FROM account WHERE id = ?",
                mapper, id);
    }

    public BigDecimal balanceOf(long id) {
        return jdbc.queryForObject(
                "SELECT balance FROM account WHERE id = ?",
                BigDecimal.class, id);
    }

    // A range query -> used later to demonstrate phantom reads.
    public List<Account> richerThan(BigDecimal threshold) {
        return jdbc.query(
                "SELECT id, owner, balance, version FROM account WHERE balance > ?",
                mapper, threshold);
    }

    // update() returns the affected row count. Parameters are bound.
    public int adjustBalance(long id, BigDecimal delta) {
        return jdbc.update(
                "UPDATE account SET balance = balance + ? WHERE id = ?",
                delta, id);
    }

    // Optimistic-lock-style conditional update: only succeeds if version is unchanged.
    public int adjustBalanceIfVersion(long id, BigDecimal delta, long expectedVersion) {
        return jdbc.update(
                "UPDATE account SET balance = balance + ?, version = version + 1 " +
                "WHERE id = ? AND version = ?",
                delta, id, expectedVersion);
    }

    public Account findByIdForUpdate(long id) {
        // SELECT ... FOR UPDATE takes a row-level write lock (pessimistic).
        return jdbc.queryForObject(
                "SELECT id, owner, balance, version FROM account WHERE id = ? FOR UPDATE",
                mapper, id);
    }
}
```

## Step 3 — The transfer, inside a transaction

We do the transfer **programmatically** with `TransactionTemplate` so the transaction boundary is explicit and visible — perfect for learning. (Day 17 will revisit declarative `@Transactional` and propagation in depth.)

`TransactionTemplate` wraps a callback: it asks the `PlatformTransactionManager` to begin a transaction, binds the connection to the thread, runs your code, then **commits** if the callback returns normally or **rolls back** if it throws a `RuntimeException`.

```java
package com.example.day11;

import org.springframework.stereotype.Service;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;

@Service
public class BankService {

    private final AccountRepository accounts;
    private final TransactionTemplate tx;

    public BankService(AccountRepository accounts, TransactionTemplate tx) {
        this.accounts = accounts;
        this.tx = tx;
        // Default isolation; we'll override per-demo below.
        this.tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    public void transfer(long fromId, long toId, BigDecimal amount) {
        tx.executeWithoutResult(status -> {
            BigDecimal fromBalance = accounts.balanceOf(fromId);
            if (fromBalance.compareTo(amount) < 0) {
                // Throwing inside the callback triggers a ROLLBACK (Atomicity).
                throw new IllegalStateException(
                        "Insufficient funds in account " + fromId);
            }
            accounts.adjustBalance(fromId, amount.negate()); // debit
            accounts.adjustBalance(toId, amount);            // credit
            // Falling off the end commits both updates atomically.
        });
    }
}
```

And the `TransactionTemplate` / `DataSourceTransactionManager` wiring. With Spring Boot, `DataSourceTransactionManager` is auto-configured; we just expose a `TransactionTemplate`:

```java
package com.example.day11;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@Configuration
public class TxConfig {
    @Bean
    public TransactionTemplate transactionTemplate(PlatformTransactionManager tm) {
        return new TransactionTemplate(tm);
    }
}
```

> What `DataSourceTransactionManager` does under the hood, step by step: borrows a connection from Hikari → `setAutoCommit(false)` → `setTransactionIsolation(...)` → binds the connection to the current thread (so the `JdbcTemplate` inside picks it up) → runs your callback → `commit()` or `rollback()` → restores autocommit → returns the connection to the pool. That is exactly the manual JDBC dance, centralized.

## Step 4 — Demonstrate a **non-repeatable read** at READ COMMITTED, then fix it

This is the cleanest anomaly to see. We open a long-lived "reader" transaction that reads Alice's balance, *pauses*, then reads it again. Meanwhile a "writer" transaction commits a change in the gap.

We need to control isolation per transaction, so the demo uses two `TransactionTemplate`s with explicit isolation, and `CountDownLatch`es to choreograph the threads.

```java
package com.example.day11;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.util.concurrent.CountDownLatch;

@Component
public class NonRepeatableReadDemo implements CommandLineRunner {

    private final AccountRepository accounts;
    private final PlatformTransactionManager tm;

    public NonRepeatableReadDemo(AccountRepository accounts, PlatformTransactionManager tm) {
        this.accounts = accounts;
        this.tm = tm;
    }

    @Override
    public void run(String... args) throws Exception {
        System.out.println("\n=== Non-repeatable read @ READ COMMITTED ===");
        runAt(TransactionDefinition.ISOLATION_READ_COMMITTED);

        System.out.println("\n=== Same scenario @ REPEATABLE READ (fixed) ===");
        runAt(TransactionDefinition.ISOLATION_REPEATABLE_READ);
    }

    private void runAt(int isolation) throws InterruptedException {
        // Reset Alice to a known value before each run.
        new TransactionTemplate(tm).executeWithoutResult(s ->
                accounts.adjustBalance(1, new BigDecimal("1000.00")
                        .subtract(accounts.balanceOf(1))));

        CountDownLatch firstReadDone = new CountDownLatch(1);
        CountDownLatch writeCommitted = new CountDownLatch(1);

        // READER transaction: read, wait for the writer to commit, read again.
        Thread reader = new Thread(() -> {
            TransactionTemplate tx = new TransactionTemplate(tm);
            tx.setIsolationLevel(isolation);
            tx.executeWithoutResult(status -> {
                BigDecimal first = accounts.balanceOf(1);
                System.out.println("  reader: first  read = " + first);
                firstReadDone.countDown();
                await(writeCommitted);                       // hold the transaction open
                BigDecimal second = accounts.balanceOf(1);
                System.out.println("  reader: second read = " + second
                        + (first.compareTo(second) == 0
                           ? "   (repeatable)"
                           : "   <-- NON-REPEATABLE READ"));
            });
        });

        // WRITER transaction: after the reader's first read, debit Alice and commit.
        Thread writer = new Thread(() -> {
            await(firstReadDone);
            TransactionTemplate tx = new TransactionTemplate(tm);
            tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
            tx.executeWithoutResult(status ->
                    accounts.adjustBalance(1, new BigDecimal("-500.00")));
            System.out.println("  writer: committed -500.00");
            writeCommitted.countDown();
        });

        reader.start();
        writer.start();
        reader.join();
        writer.join();
    }

    private static void await(CountDownLatch l) {
        try { l.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
```

At `READ COMMITTED` the reader's second read sees the committed `-500`, so the two reads differ. At `REPEATABLE READ` the reader holds one snapshot for its whole transaction, so both reads return the original value even though the writer committed.

## Step 5 — Demonstrate a **phantom read** (range query)

Same shape, but the reader runs a *predicate* query twice and the writer **inserts** a new matching row. This is the anomaly `REPEATABLE READ` *allows* in the ANSI standard but snapshot engines (Postgres/InnoDB) actually prevent — a great talking point.

```java
package com.example.day11;

import org.springframework.boot.CommandLineRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.util.concurrent.CountDownLatch;

@Component
public class PhantomReadDemo implements CommandLineRunner {

    private final AccountRepository accounts;
    private final JdbcTemplate jdbc;
    private final PlatformTransactionManager tm;

    public PhantomReadDemo(AccountRepository accounts, JdbcTemplate jdbc, PlatformTransactionManager tm) {
        this.accounts = accounts;
        this.jdbc = jdbc;
        this.tm = tm;
    }

    @Override
    public void run(String... args) throws Exception {
        System.out.println("\n=== Phantom read @ REPEATABLE READ ===");
        // Try SERIALIZABLE to see it prevented on engines that need it.
        runAt(TransactionDefinition.ISOLATION_REPEATABLE_READ);
    }

    private void runAt(int isolation) throws InterruptedException {
        jdbc.update("DELETE FROM account WHERE id >= 100");   // clean slate

        CountDownLatch firstCountDone = new CountDownLatch(1);
        CountDownLatch insertCommitted = new CountDownLatch(1);
        BigDecimal threshold = new BigDecimal("500.00");

        Thread reader = new Thread(() -> {
            TransactionTemplate tx = new TransactionTemplate(tm);
            tx.setIsolationLevel(isolation);
            tx.executeWithoutResult(status -> {
                int first = accounts.richerThan(threshold).size();
                System.out.println("  reader: first  count = " + first);
                firstCountDone.countDown();
                await(insertCommitted);
                int second = accounts.richerThan(threshold).size();
                System.out.println("  reader: second count = " + second
                        + (first == second ? "   (no phantom)" : "   <-- PHANTOM ROW"));
            });
        });

        Thread writer = new Thread(() -> {
            await(firstCountDone);
            new TransactionTemplate(tm).executeWithoutResult(status ->
                    jdbc.update("INSERT INTO account (id, owner, balance) VALUES (?,?,?)",
                            100, "Carol", new BigDecimal("9999.00")));
            System.out.println("  writer: inserted Carol (9999.00) and committed");
            insertCommitted.countDown();
        });

        reader.start(); writer.start();
        reader.join();  writer.join();
    }

    private static void await(CountDownLatch l) {
        try { l.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
```

## Step 6 — Demonstrate (and fix) a **lost update** — the one that matters for transfers

Two threads each read Alice's balance and add 100 in *application code*, then write back. At `READ COMMITTED` (the typical default) the second write clobbers the first: +200 expected, +100 observed. We fix it three ways.

```java
package com.example.day11;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.util.concurrent.CountDownLatch;

@Component
public class LostUpdateDemo implements CommandLineRunner {

    private final AccountRepository accounts;
    private final PlatformTransactionManager tm;

    public LostUpdateDemo(AccountRepository accounts, PlatformTransactionManager tm) {
        this.accounts = accounts;
        this.tm = tm;
    }

    @Override
    public void run(String... args) throws Exception {
        System.out.println("\n=== Lost update: read-modify-write race ===");

        reset();
        System.out.println("BROKEN (read in app, blind write): expected 1200, got "
                + brokenIncrementBothBy100());

        reset();
        System.out.println("FIXED with atomic UPDATE (balance = balance + ?): expected 1200, got "
                + atomicIncrementBothBy100());

        reset();
        System.out.println("FIXED with optimistic lock (version + retry): expected 1200, got "
                + optimisticIncrementBothBy100());
    }

    private void reset() {
        new TransactionTemplate(tm).executeWithoutResult(s ->
                accounts.adjustBalance(1, new BigDecimal("1000.00").subtract(accounts.balanceOf(1))));
    }

    // BROKEN: classic lost update. Both threads read 1000, write 1100. One +100 is lost.
    private BigDecimal brokenIncrementBothBy100() throws InterruptedException {
        runConcurrently(2, () -> {
            TransactionTemplate tx = new TransactionTemplate(tm);
            tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
            tx.executeWithoutResult(status -> {
                BigDecimal current = accounts.balanceOf(1);     // read
                sleep(50);                                      // widen the race window
                BigDecimal updated = current.add(new BigDecimal("100.00")); // modify in Java
                // blind write of an absolute value -> clobbers the other thread
                accounts.adjustBalance(1, updated.subtract(accounts.balanceOf(1)));
            });
        });
        return accounts.balanceOf(1);
    }

    // FIXED: let the DB do the arithmetic atomically. No read-modify-write in app space.
    private BigDecimal atomicIncrementBothBy100() throws InterruptedException {
        runConcurrently(2, () -> new TransactionTemplate(tm).executeWithoutResult(status ->
                accounts.adjustBalance(1, new BigDecimal("100.00"))));   // balance = balance + 100
        return accounts.balanceOf(1);
    }

    // FIXED: optimistic concurrency. Read version, conditional update, retry on conflict.
    private BigDecimal optimisticIncrementBothBy100() throws InterruptedException {
        runConcurrently(2, () -> {
            boolean done = false;
            while (!done) {
                done = Boolean.TRUE.equals(new TransactionTemplate(tm).execute(status -> {
                    var a = accounts.findById(1);
                    sleep(20);
                    int rows = accounts.adjustBalanceIfVersion(1, new BigDecimal("100.00"), a.version());
                    return rows == 1;   // 0 rows -> version moved under us -> retry
                }));
            }
        });
        return accounts.balanceOf(1);
    }

    private void runConcurrently(int n, Runnable task) throws InterruptedException {
        CountDownLatch start = new CountDownLatch(1);
        Thread[] ts = new Thread[n];
        for (int i = 0; i < n; i++) {
            ts[i] = new Thread(() -> { await(start); task.run(); });
            ts[i].start();
        }
        start.countDown();           // release all threads at once
        for (Thread t : ts) t.join();
    }

    private static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
    private static void await(CountDownLatch l) {
        try { l.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
```

## How to run

```bash
cd day11
mvn spring-boot:run
```

The three `CommandLineRunner` demos execute at startup and print to the console. (In a real app you'd put these in a test instead — Day 23 wires them to a Postgres Testcontainer so the MVCC behaviour is authentic.)

## Expected output

```
=== Non-repeatable read @ READ COMMITTED ===
  reader: first  read = 1000.00
  writer: committed -500.00
  reader: second read = 500.00   <-- NON-REPEATABLE READ

=== Same scenario @ REPEATABLE READ (fixed) ===
  reader: first  read = 1000.00
  writer: committed -500.00
  reader: second read = 1000.00   (repeatable)

=== Phantom read @ REPEATABLE READ ===
  reader: first  count = 2
  writer: inserted Carol (9999.00) and committed
  reader: second count = 2   (no phantom)        # snapshot engine prevents it; lock-based RR would show PHANTOM

=== Lost update: read-modify-write race ===
BROKEN (read in app, blind write): expected 1200, got 1100.00
FIXED with atomic UPDATE (balance = balance + ?): expected 1200, got 1200.00
FIXED with optimistic lock (version + retry): expected 1200, got 1200.00
```

> Exact numbers can vary run-to-run for the *broken* case (it's a race), but the lost-update version is consistently below 1200, while both fixes always reach 1200. If H2's behaviour at a given isolation level surprises you, that's the point — repoint the datasource at Postgres and compare. The differences *are* the lesson.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Postgres defaults to `READ COMMITTED`** — not `SERIALIZABLE`. So out of the box, *non-repeatable reads, phantoms, and lost updates are all possible* in your production database. Most apps live with this and defend the dangerous spots explicitly. Know your default: MySQL/InnoDB defaults to `REPEATABLE READ`, Oracle's "Serializable" is actually snapshot isolation, SQL Server defaults to `READ COMMITTED` (lock-based unless RCSI is enabled). The level *name* is not portable behaviour.
- **Lost update has three standard fixes**, shown above: (1) push the arithmetic into the DB so it's atomic (`SET balance = balance + ?`) — the simplest and best when applicable; (2) **optimistic locking** — a `version` column + conditional `UPDATE ... WHERE version = ?`, retry on 0 rows affected (this is exactly how JPA's `@Version` works — preview of Day 12); (3) **pessimistic locking** — `SELECT ... FOR UPDATE` to take a row lock before reading.
- **Optimistic vs pessimistic — when to choose which.** Optimistic assumes conflicts are *rare*: no locks held, cheap reads, but you pay with retries (and must write the retry loop) when conflicts do happen. Best for low-contention, read-heavy, web-scale work. Pessimistic assumes conflicts are *likely*: take the lock up front, serialize the contenders, no retries — but you hold a lock (reduced concurrency, deadlock risk, must keep the transaction short). Best for hot rows like a shared counter or inventory.
- **`SELECT ... FOR UPDATE`** locks the selected rows for the duration of the transaction; other transactions trying to write (or `FOR UPDATE`-read) those rows **block** until you commit. Variants matter: `FOR UPDATE SKIP LOCKED` (skip locked rows — the basis of a SQL work-queue, recall Day 4), `FOR UPDATE NOWAIT` (fail immediately instead of waiting), `FOR SHARE` (shared read lock). A bank transfer done pessimistically would `SELECT ... FOR UPDATE` *both* accounts — **always in a consistent order (e.g. by id ascending)** to avoid deadlocks.
- **`SERIALIZABLE` via SSI** (Postgres) keeps MVCC's non-blocking reads but tracks dangerous read/write dependency structures and **aborts** a transaction that would break serializability (`SQLSTATE 40001`). That means at `SERIALIZABLE` your code *must* catch the serialization-failure exception and **retry the whole transaction** — there's no partial recovery. This is the price of the strongest correctness guarantee, and it's why bulk jobs sometimes drop to `REPEATABLE READ` plus explicit locking instead.
- **Write skew** is the anomaly snapshot isolation (`REPEATABLE READ`) *cannot* prevent and that motivates `SERIALIZABLE`: two transactions read an overlapping set, each checks an invariant that currently holds, and each writes a *different* row — individually fine, jointly breaking the invariant (e.g. "at least one doctor must be on call," both resign because each sees the other still on). No lost update, no dirty read — yet the result is invalid. If you have a cross-row invariant, `READ COMMITTED`/`REPEATABLE READ` won't save you.
- **Keep transactions short.** Isolation guarantees are held by *keeping a transaction open*; a long transaction at `REPEATABLE READ` pins an old snapshot, bloats Postgres's version store (vacuum can't reclaim), and holds locks. Never do network I/O or user think-time inside a transaction.

### Stretch goals

1. **Reproduce the dirty read** at `READ UNCOMMITTED` (H2 supports it; Postgres silently treats it as `READ COMMITTED`, so use H2 here). Have the writer update, *not* commit, let the reader read, then have the writer roll back — and watch the reader having acted on money that never existed.
2. **Pessimistic transfer.** Rewrite `BankService.transfer` to `SELECT ... FOR UPDATE` both accounts (in id order), then run two opposing transfers (A→B and B→A) concurrently and confirm there's no lost update and no deadlock. Then *deliberately* lock in the wrong order in one thread and observe the deadlock + the DB's deadlock-victim error.
3. **Serialization-failure retry.** Run the write-skew scenario at `SERIALIZABLE` against Postgres, catch `SQLException` with SQLSTATE `40001`, and implement a bounded exponential-backoff retry wrapper around `TransactionTemplate`.
4. **Measure the cost.** Time the lost-update fix three ways (atomic update vs optimistic-with-retries vs pessimistic `FOR UPDATE`) under 50 concurrent threads on one hot row. Plot throughput vs contention and explain the crossover where pessimistic wins.

### Day 12 teaser

Today you wrote SQL and `RowMapper`s by hand and managed the read-modify-write problem yourself. **Day 12 — JPA, Hibernate & N+1** introduces an ORM that maps objects to rows *for* you, gives you `@Version`-based optimistic locking out of the box (exactly the pattern from Step 6), and a first-level cache that changes when SQL actually fires. With that power comes the most famous ORM footgun — the **N+1 query problem** — where loading 100 orders silently fires 101 queries. We'll reproduce it, see it in the SQL log you enabled today, and fix it with fetch joins and entity graphs.
