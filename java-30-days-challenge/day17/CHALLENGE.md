# Day 17: Transactions, Propagation & 2PC

| | |
|---|---|
| 🏗️ **Project** | **TxLab** — a transaction-propagation playground with a simulated 2PC coordinator |
| ☕ **Java & language skills** | @Transactional, Spring AOP proxies, propagation/rollback semantics, simulating a coordinator |
| 🧰 **Library / tool** | Spring Transaction Management (declarative @Transactional) |
| 🗄️ **DB / distributed-systems concept** | Transaction propagation & Two-Phase Commit (2PC) — and why it's avoided |
| 📊 **Difficulty** | Hard |

---

## Concept primer

### Part A — How `@Transactional` really works (proxy-based AOP)

On Day 11 you opened a JDBC `Connection`, set `autoCommit(false)`, and called `commit()` / `rollback()` by hand. On Day 12 the `EntityManager` did the SQL for you. `@Transactional` is the next layer up: it removes the *begin / commit / rollback* boilerplate — but it does so through a mechanism you must understand, because that mechanism is the source of nearly every transaction bug.

When Spring sees `@Transactional` on a bean, it does **not** rewrite your method. Instead it wraps your bean in a **proxy** (a CGLIB subclass for classes, or a JDK dynamic proxy for interfaces). Callers get a reference to the *proxy*, not your object. The proxy's flow per call:

```
caller ──▶ [PROXY] ──▶ TransactionInterceptor ──▶ your real method
                          │  1. getTransaction()  (begin or join)
                          │  2. invoke real method
                          │  3a. commit()   if no exception / non-rollback exception
                          │  3b. rollback() if rollback-worthy exception
```

The transaction logic lives in `TransactionInterceptor`, which delegates to a `PlatformTransactionManager` (e.g. `JpaTransactionManager`, `DataSourceTransactionManager`). Two consequences fall directly out of this proxy design and explain most surprises:

1. **Self-invocation is invisible.** If method `a()` calls `this.b()` where `b()` is `@Transactional`, the call goes straight object-to-object — it never touches the proxy — so `b()`'s transactional annotation is **completely ignored**. This is the #1 gotcha and we prove it below.
2. **Only `public` methods are advised** (by default with the standard proxy approach), and the annotation must be on a bean Spring manages.

### Part B — Propagation: what happens when transactional methods call each other

Propagation answers: *"When a transactional method is invoked, should it join the caller's transaction, start its own, or do something else?"* Here is the senior-level cheat sheet:

| Propagation     | If a tx exists                                  | If no tx exists           | Inner rollback effect on outer                                                                 |
|-----------------|-------------------------------------------------|---------------------------|------------------------------------------------------------------------------------------------|
| `REQUIRED` (default) | **Join** the existing tx                    | Start a new tx            | **Whole thing rolls back.** A rollback anywhere marks the *shared* tx `rollback-only`.          |
| `REQUIRES_NEW`  | **Suspend** outer, start a brand-new tx          | Start a new tx            | Independent. Inner can commit/roll back without affecting outer (and vice versa).               |
| `NESTED`        | Create a **savepoint** within the outer tx       | Start a new tx            | Inner rollback rewinds to the savepoint only; outer can continue. Needs JDBC savepoint support. |
| `SUPPORTS`      | Join it                                          | Run **non**-transactionally | n/a (no tx of its own)                                                                          |
| `MANDATORY`     | Join it                                          | **Throw** `IllegalTransactionStateException` | —                                                                            |
| `NOT_SUPPORTED` | **Suspend** the tx, run non-transactionally      | Run non-transactionally   | —                                                                                               |
| `NEVER`         | **Throw** an exception                           | Run non-transactionally   | —                                                                                               |

The three you must internalize:

- **`REQUIRED`** — one logical unit. There is really *one* physical transaction; nested `REQUIRED` calls just join it. **If the inner call throws and that exception propagates through any `REQUIRED` boundary, the entire transaction is doomed** — Spring sets a `rollback-only` flag, and even if the outer `catch`es the exception and tries to commit, it gets `UnexpectedRollbackException`. People are *constantly* surprised by this.
- **`REQUIRES_NEW`** — physically suspends the outer connection and opens a *second* one. Two independent commit/rollback scopes. Perfect for "write an audit/log row that must persist even if the business tx rolls back." Costs an extra connection from the pool (remember Day 9 — pool sizing matters; nesting `REQUIRES_NEW` can deadlock a small pool).
- **`NESTED`** — a savepoint inside the *same* physical transaction. Inner failure rewinds to the savepoint; the outer is untouched and continues. Requires a `DataSourceTransactionManager` with savepoint support (works on H2/Postgres via JDBC; **not** supported by the plain `JpaTransactionManager` for nested JPA in all configs — a classic gotcha).

### Part C — Rollback rules (checked vs unchecked)

Spring's **default**: roll back on `RuntimeException` and `Error` (unchecked); **commit** on checked exceptions. This trips up everyone porting code that throws `IOException`/`SQLException`-style checked exceptions for business failures — the transaction *commits anyway*. Override with:

```java
@Transactional(rollbackFor = Exception.class)            // roll back on ALL exceptions
@Transactional(noRollbackFor = OutOfStockException.class) // commit despite this one
```

### Part D — Distributed transactions and Two-Phase Commit (2PC)

Everything above is *one* resource (one database, one connection). The hard problem starts when a single business operation must atomically touch **more than one resource** — e.g. debit a row in DB-A *and* credit a row in DB-B, or write to a DB *and* publish a Kafka message (Day 18). A local `@Transactional` cannot span two independent resources; its `commit()` only covers one connection.

**Two-Phase Commit (2PC)** is the classic protocol to make N resources commit-or-abort atomically, run by a **coordinator** (transaction manager) talking to **participants** (resource managers):

```
   Phase 1 — PREPARE (voting)
   coordinator ──"prepare?"──▶ each participant
   participant: do the work, write it durably to a *pending* state, fsync, then vote
   participant ──"YES (ready)"──or──"NO (abort)"──▶ coordinator

   Phase 2 — COMMIT / ABORT (decision)
   if ALL voted YES:  coordinator ──"COMMIT"──▶ all   (and records the global decision durably)
   if ANY voted NO:   coordinator ──"ABORT"──▶ all
   participant: makes the pending state permanent (or discards it), then ACKs
```

The crucial property of phase 1: once a participant votes **YES**, it has *promised* it can commit and must hold the data locked until it hears the decision. This is also the fatal weakness:

> **The blocking failure mode.** Suppose every participant voted YES and is now waiting in the "prepared" state, holding locks. The coordinator records its decision and then **crashes before sending phase 2.** The participants cannot decide on their own — they don't know whether the coordinator decided COMMIT or ABORT, and they *cannot* unilaterally guess (committing when the global decision was abort, or vice versa, breaks atomicity). So they **block, holding their locks, until the coordinator recovers.** 2PC is not partition-tolerant: a coordinator failure at the wrong moment freezes resources indefinitely. (3PC tries to fix this with an extra phase but adds latency and still has failure windows.)

**Why senior systems avoid 2PC:**
- **Availability.** The blocking property is unacceptable for high-availability services — one coordinator hiccup stalls many participants.
- **Latency & coupling.** Synchronous cross-service locks across the network are slow and create tight runtime coupling; throughput collapses under contention.
- **Operational pain.** It needs XA-capable drivers and a JTA transaction manager (Narayana/Atomikos), and message brokers like Kafka don't participate cleanly in XA.

The modern answer is to **give up global atomicity and embrace eventual consistency** with compensation:
- **Sagas** — break the distributed transaction into a sequence of *local* transactions, each with a compensating action to undo it if a later step fails. No global lock; failures are handled by running compensations.
- **Transactional Outbox (Day 20)** — write the business change *and* an "outbox" event row in **one local transaction** (single resource, so a normal `@Transactional` is enough), then a relay publishes the event to the broker asynchronously and idempotently (idempotency = Day 7). This sidesteps 2PC entirely for the common "DB + broker" case.

We will *simulate* a 2PC coordinator over two in-memory resources so you can watch the prepare/vote/commit phases — and force a coordinator crash to feel the participants block. That visceral experience is the whole point: it's why Day 20 exists.

---

## Prerequisites

- JDK 21+, Maven (Day 1)
- Familiarity with `@Transactional`-free JDBC transactions and isolation levels (Day 11) and JPA entities (Day 12)
- A scaffolded Spring Boot project. If starting fresh:

```bash
mkdir -p day17 && cd day17
```

---

## 🛠️ Project Walkthrough — TxLab

Follow the build steps below in order, writing each file as you go and running the demo at the end to watch the transaction and 2PC behavior happen live.

---

## Step 1 — Project setup (`pom.xml`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.3.4</version>
        <relativePath/>
    </parent>

    <groupId>com.learning</groupId>
    <artifactId>day17-transactions</artifactId>
    <version>1.0.0</version>

    <properties>
        <java.version>21</java.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
        </dependency>
        <dependency>
            <groupId>com.h2database</groupId>
            <artifactId>h2</artifactId>
            <scope>runtime</scope>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>
</project>
```

`src/main/resources/application.yml`:

```yaml
spring:
  datasource:
    # DataSourceTransactionManager-style NESTED savepoints work on H2.
    url: jdbc:h2:mem:day17;DB_CLOSE_DELAY=-1
    username: sa
    password: ""
  jpa:
    hibernate:
      ddl-auto: create-drop
    show-sql: false
    properties:
      hibernate.format_sql: false
logging:
  level:
    # See begin/commit/rollback and savepoint events as they happen.
    org.springframework.orm.jpa.JpaTransactionManager: DEBUG
    org.springframework.transaction.interceptor: TRACE
```

---

## Step 2 — Domain: an `Account` and a transactional money transfer

`src/main/java/com/learning/day17/Account.java`:

```java
package com.learning.day17;

import jakarta.persistence.*;

@Entity
public class Account {

    @Id
    private String id;

    private long balance;

    protected Account() { } // JPA

    public Account(String id, long balance) {
        this.id = id;
        this.balance = balance;
    }

    public String getId() { return id; }
    public long getBalance() { return balance; }
    public void setBalance(long balance) { this.balance = balance; }

    public void debit(long amount) {
        if (balance < amount) {
            throw new InsufficientFundsException(id + " has " + balance + ", needs " + amount);
        }
        balance -= amount;
    }

    public void credit(long amount) {
        balance += amount;
    }
}
```

`src/main/java/com/learning/day17/AccountRepository.java`:

```java
package com.learning.day17;

import org.springframework.data.jpa.repository.JpaRepository;

public interface AccountRepository extends JpaRepository<Account, String> { }
```

`src/main/java/com/learning/day17/InsufficientFundsException.java` (an **unchecked** business exception — rolls back by default):

```java
package com.learning.day17;

public class InsufficientFundsException extends RuntimeException {
    public InsufficientFundsException(String message) { super(message); }
}
```

`src/main/java/com/learning/day17/AuditLog.java` + repo — used to prove `REQUIRES_NEW` survives an outer rollback:

```java
package com.learning.day17;

import jakarta.persistence.*;

@Entity
public class AuditLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String message;

    protected AuditLog() { }
    public AuditLog(String message) { this.message = message; }

    public Long getId() { return id; }
    public String getMessage() { return message; }
}
```

```java
package com.learning.day17;

import org.springframework.data.jpa.repository.JpaRepository;

public interface AuditLogRepository extends JpaRepository<AuditLog, Long> { }
```

---

## Step 3 — Demonstrate propagation: `REQUIRED` vs `REQUIRES_NEW` vs `NESTED`

The key idea: an *outer* transactional method calls an *inner* one with various propagation modes, and the inner one fails. We observe whether the outer's work survives.

`src/main/java/com/learning/day17/AuditService.java`:

```java
package com.learning.day17;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AuditService {

    private final AuditLogRepository repo;

    public AuditService(AuditLogRepository repo) { this.repo = repo; }

    /**
     * REQUIRES_NEW: suspends any caller transaction and commits independently.
     * Used so an audit row persists EVEN IF the business transaction rolls back.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void auditIndependently(String message) {
        repo.save(new AuditLog("[REQUIRES_NEW] " + message));
    }

    /**
     * REQUIRED (default): joins the caller's transaction. If the caller rolls
     * back, this audit row is lost too.
     */
    @Transactional(propagation = Propagation.REQUIRED)
    public void auditJoined(String message) {
        repo.save(new AuditLog("[REQUIRED] " + message));
    }
}
```

`src/main/java/com/learning/day17/TransferService.java`:

```java
package com.learning.day17;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TransferService {

    private final AccountRepository accounts;
    private final AuditService audit;

    public TransferService(AccountRepository accounts, AuditService audit) {
        this.accounts = accounts;
        this.audit = audit;
    }

    /**
     * Outer transaction (REQUIRED). It:
     *  1. writes an audit row via REQUIRES_NEW (independent — will survive),
     *  2. writes an audit row via REQUIRED   (joined    — will be lost),
     *  3. then forces a rollback by overdrawing.
     * After this throws, the REQUIRES_NEW row remains; the REQUIRED row is gone.
     */
    @Transactional
    public void transferThenFail(String fromId, String toId, long amount) {
        audit.auditIndependently("attempting transfer of " + amount);
        audit.auditJoined("joined-tx note for transfer of " + amount);

        Account from = accounts.findById(fromId).orElseThrow();
        Account to = accounts.findById(toId).orElseThrow();
        from.debit(amount); // throws InsufficientFundsException if overdrawn -> outer rolls back
        to.credit(amount);
    }

    /**
     * NESTED demo: the inner transfer uses a SAVEPOINT. If it fails, we catch the
     * exception, roll back to the savepoint only, and the OUTER transaction
     * continues and commits its own work.
     */
    @Transactional
    public void transferWithNestedRetry(String fromId, String toId, long good, long tooMuch) {
        // First a legit transfer in the outer tx.
        move(fromId, toId, good);
        try {
            // Inner NESTED unit that will fail and rewind only to its savepoint.
            nestedOverdraw(fromId, toId, tooMuch);
        } catch (InsufficientFundsException e) {
            // Outer survives — the 'good' transfer above is NOT rolled back.
            System.out.println("  nested unit rolled back to savepoint: " + e.getMessage());
        }
    }

    @Transactional(propagation = Propagation.NESTED)
    public void nestedOverdraw(String fromId, String toId, long amount) {
        move(fromId, toId, amount); // will overdraw -> rewind to savepoint
    }

    private void move(String fromId, String toId, long amount) {
        Account from = accounts.findById(fromId).orElseThrow();
        Account to = accounts.findById(toId).orElseThrow();
        from.debit(amount);
        to.credit(amount);
    }
}
```

> **Why `REQUIRED` dooms the whole tx:** if instead of catching, you let an inner `REQUIRED` exception bubble through the outer, Spring marks the shared transaction `rollback-only`. Even an outer `catch` that swallows the exception then fails at commit with `UnexpectedRollbackException` — "Transaction silently rolled back because it has been marked as rollback-only." Add a fourth service method that does exactly this and watch it happen; it's a rite of passage.

> **Note on NESTED + JPA:** `NESTED` relies on JDBC savepoints. With Spring Boot's default `JpaTransactionManager`, nested behavior can be limited; if your run logs `NestedTransactionNotSupportedException`, switch the transaction manager to `DataSourceTransactionManager` for the savepoint demo, or use the `TransactionTemplate` with `PROPAGATION_NESTED`. This caveat is itself a senior-level lesson: propagation semantics depend on the transaction manager and underlying resource.

---

## Step 4 — Prove the self-invocation bug (and fix it)

`src/main/java/com/learning/day17/SelfInvocationDemo.java`:

```java
package com.learning.day17;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationContext;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Service
public class SelfInvocationDemo {

    private final AuditLogRepository repo;
    private final ApplicationContext ctx;     // Fix #2: look the proxy up
    @Autowired private SelfInvocationDemo self; // Fix #1: inject the proxy of myself

    public SelfInvocationDemo(AuditLogRepository repo, ApplicationContext ctx) {
        this.repo = repo;
        this.ctx = ctx;
    }

    // ----- THE BUG -----------------------------------------------------------
    // No @Transactional here. We call this.inner() directly: the call NEVER
    // touches the proxy, so inner()'s @Transactional is IGNORED.
    public void buggyEntryPoint() {
        this.inner();
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void inner() {
        boolean active = TransactionSynchronizationManager.isActualTransactionActive();
        System.out.println("  inner(): actual transaction active? " + active);
        repo.save(new AuditLog("inner ran; tx active = " + active));
    }

    // ----- FIX #1: call through an injected self-proxy ------------------------
    public void fixedViaSelfProxy() {
        self.inner(); // goes through the proxy -> @Transactional honored
    }

    // ----- FIX #2: look the bean up from the context (also the proxy) ---------
    public void fixedViaContext() {
        ctx.getBean(SelfInvocationDemo.class).inner();
    }

    // FIX #3 (best, not shown here): move inner() into a SEPARATE bean and
    // inject that bean. Cleaner design; no self-reference smell.
}
```

When you call `buggyEntryPoint()`, `inner()` prints `tx active? false` — the annotation was bypassed. Both fixes print `true`.

---

## Step 5 — A simulated Two-Phase Commit coordinator over two resources

We model two independent "resources" (think: two databases or a DB + a broker) with an explicit prepare/commit/abort lifecycle, plus a coordinator that drives the protocol — and can be told to **crash** between phases so you watch participants block.

`src/main/java/com/learning/day17/twopc/Resource.java`:

```java
package com.learning.day17.twopc;

/** A transactional participant in 2PC (a "resource manager"). */
public interface Resource {
    String name();

    /**
     * Phase 1. Do the work into a durable PENDING state and vote.
     * Returning true == "YES, I can commit and have locked/persisted my pending change."
     * After voting YES, the resource is PREPARED and must hold until told COMMIT/ABORT.
     */
    boolean prepare(String txId, Runnable work);

    /** Phase 2 — make the prepared change permanent. */
    void commit(String txId);

    /** Phase 2 — discard the prepared change and release locks. */
    void abort(String txId);

    /** For inspection in the demo. */
    String state();
}
```

`src/main/java/com/learning/day17/twopc/InMemoryResource.java`:

```java
package com.learning.day17.twopc;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public class InMemoryResource implements Resource {

    enum Status { IDLE, PREPARED, COMMITTED, ABORTED }

    private final String name;
    private final boolean voteYes;          // simulate a participant that votes NO
    private Status status = Status.IDLE;
    private final Map<String, Long> committed = new ConcurrentHashMap<>();
    private long pending;                    // the prepared-but-not-committed value
    private long value;                      // the durable value

    public InMemoryResource(String name, boolean voteYes) {
        this.name = name;
        this.voteYes = voteYes;
    }

    @Override public String name() { return name; }

    @Override
    public boolean prepare(String txId, Runnable work) {
        System.out.println("    [" + name + "] PREPARE for " + txId);
        if (!voteYes) {
            System.out.println("    [" + name + "] votes NO");
            status = Status.ABORTED;
            return false;
        }
        work.run();                          // perform & log the change to a pending area
        this.pending = value + 1;            // toy "change": increment, held as pending
        status = Status.PREPARED;            // now LOCKED until phase 2
        System.out.println("    [" + name + "] PREPARED (locked), votes YES");
        return true;
    }

    @Override
    public void commit(String txId) {
        if (status != Status.PREPARED) throw new IllegalStateException(name + " not prepared");
        value = pending;
        committed.put(txId, value);
        status = Status.COMMITTED;
        System.out.println("    [" + name + "] COMMIT -> value=" + value);
    }

    @Override
    public void abort(String txId) {
        pending = 0;
        status = Status.ABORTED;
        System.out.println("    [" + name + "] ABORT -> rolled back to value=" + value);
    }

    @Override
    public String state() {
        return name + "{status=" + status + ", value=" + value + ", pending=" + pending + "}";
    }

    boolean isPrepared() { return status == Status.PREPARED; }
}
```

`src/main/java/com/learning/day17/twopc/TwoPhaseCommitCoordinator.java`:

```java
package com.learning.day17.twopc;

import java.util.List;
import java.util.UUID;

/**
 * A toy 2PC coordinator. NOT production code — it exists so you can watch the
 * protocol and the blocking failure mode with your own eyes.
 */
public class TwoPhaseCommitCoordinator {

    private final List<Resource> participants;

    /** If true, the coordinator "crashes" right after it has decided to commit
     *  but BEFORE telling the participants. They are left prepared & blocked. */
    private final boolean crashBeforePhase2;

    public TwoPhaseCommitCoordinator(List<Resource> participants, boolean crashBeforePhase2) {
        this.participants = participants;
        this.crashBeforePhase2 = crashBeforePhase2;
    }

    public boolean execute(Runnable work) {
        String txId = "tx-" + UUID.randomUUID().toString().substring(0, 8);
        System.out.println("[coordinator] BEGIN " + txId);

        // ---- Phase 1: PREPARE / vote ----
        boolean allYes = true;
        for (Resource r : participants) {
            boolean vote = r.prepare(txId, work);
            allYes &= vote;
        }

        // ---- Decision ----
        if (!allYes) {
            System.out.println("[coordinator] a participant voted NO -> global ABORT");
            participants.forEach(r -> r.abort(txId));
            return false;
        }

        System.out.println("[coordinator] all voted YES -> decision = COMMIT (recorded durably)");

        if (crashBeforePhase2) {
            // THE BLOCKING FAILURE MODE.
            System.out.println("[coordinator] *** CRASH *** before sending COMMIT!");
            System.out.println("[coordinator] participants are now stuck PREPARED, holding locks:");
            participants.forEach(r -> System.out.println("      " + r.state()));
            System.out.println("[coordinator] they CANNOT decide alone -> they BLOCK until recovery.");
            return false; // simulate the JVM/coordinator going away
        }

        // ---- Phase 2: COMMIT ----
        participants.forEach(r -> r.commit(txId));
        System.out.println("[coordinator] COMMIT complete for " + txId);
        return true;
    }
}
```

> Notice what 2PC bought us and what it cost. The "all YES then COMMIT" path gives atomic, all-or-nothing across two resources — exactly what a single `@Transactional` cannot do across resources. But the crash path shows the price: after voting YES the participants are *prepared and locked*, and a coordinator that dies before phase 2 leaves them unable to proceed or release. That is the blocking weakness, in code.

---

## Step 6 — Wire it all into a runner

`src/main/java/com/learning/day17/Day17Application.java`:

```java
package com.learning.day17;

import com.learning.day17.twopc.InMemoryResource;
import com.learning.day17.twopc.Resource;
import com.learning.day17.twopc.TwoPhaseCommitCoordinator;
import org.springframework.boot.CommandLineRunner;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;

import java.util.List;

@SpringBootApplication
public class Day17Application {

    public static void main(String[] args) {
        SpringApplication.run(Day17Application.class, args);
    }

    @Bean
    CommandLineRunner demo(AccountRepository accounts,
                           AuditLogRepository auditLogs,
                           TransferService transfers,
                           SelfInvocationDemo selfDemo) {
        return args -> {
            accounts.save(new Account("alice", 100));
            accounts.save(new Account("bob", 0));

            System.out.println("\n=== 1) PROPAGATION: REQUIRES_NEW survives an outer rollback ===");
            try {
                transfers.transferThenFail("alice", "bob", 1_000); // overdraw -> outer rollback
            } catch (InsufficientFundsException e) {
                System.out.println("  outer tx rolled back: " + e.getMessage());
            }
            auditLogs.findAll().forEach(a -> System.out.println("  audit row survived: " + a.getMessage()));
            System.out.println("  (only the [REQUIRES_NEW] row should be present; [REQUIRED] was rolled back)");
            System.out.println("  alice=" + accounts.findById("alice").orElseThrow().getBalance()
                    + " bob=" + accounts.findById("bob").orElseThrow().getBalance()
                    + "  (unchanged -> business tx rolled back)");

            System.out.println("\n=== 2) SELF-INVOCATION BUG vs FIX ===");
            selfDemo.buggyEntryPoint();   // tx active = false  (proxy bypassed)
            selfDemo.fixedViaSelfProxy(); // tx active = true   (through proxy)

            System.out.println("\n=== 3) SIMULATED 2PC: happy path (both vote YES) ===");
            new TwoPhaseCommitCoordinator(
                    List.of(new InMemoryResource("DB-A", true),
                            new InMemoryResource("DB-B", true)),
                    false
            ).execute(() -> { /* the work both resources perform */ });

            System.out.println("\n=== 4) SIMULATED 2PC: a participant votes NO -> global ABORT ===");
            new TwoPhaseCommitCoordinator(
                    List.of(new InMemoryResource("DB-A", true),
                            new InMemoryResource("DB-B", false)),
                    false
            ).execute(() -> { });

            System.out.println("\n=== 5) SIMULATED 2PC: COORDINATOR CRASH between phases -> BLOCKING ===");
            new TwoPhaseCommitCoordinator(
                    List.of(new InMemoryResource("DB-A", true),
                            new InMemoryResource("DB-B", true)),
                    true   // crash before phase 2
            ).execute(() -> { });
            System.out.println("\n--> THIS is why we prefer the outbox pattern (Day 20) over 2PC.");
        };
    }
}
```

---

## How to run

```bash
cd day17
mvn spring-boot:run
```

(Or `mvn -q clean package && java -jar target/day17-transactions-1.0.0.jar`.)

## Expected output (abridged)

```
=== 1) PROPAGATION: REQUIRES_NEW survives an outer rollback ===
  outer tx rolled back: alice has 100, needs 1000
  audit row survived: [REQUIRES_NEW] attempting transfer of 1000
  (only the [REQUIRES_NEW] row should be present; [REQUIRED] was rolled back)
  alice=100 bob=0  (unchanged -> business tx rolled back)

=== 2) SELF-INVOCATION BUG vs FIX ===
  inner(): actual transaction active? false      <-- BUG: @Transactional ignored
  inner(): actual transaction active? true       <-- FIX: went through proxy

=== 3) SIMULATED 2PC: happy path (both vote YES) ===
[coordinator] BEGIN tx-...
    [DB-A] PREPARE ...   [DB-A] PREPARED (locked), votes YES
    [DB-B] PREPARE ...   [DB-B] PREPARED (locked), votes YES
[coordinator] all voted YES -> decision = COMMIT (recorded durably)
    [DB-A] COMMIT -> value=1
    [DB-B] COMMIT -> value=1
[coordinator] COMMIT complete

=== 4) SIMULATED 2PC: a participant votes NO -> global ABORT ===
    [DB-A] PREPARED (locked), votes YES
    [DB-B] votes NO
[coordinator] a participant voted NO -> global ABORT
    [DB-A] ABORT -> rolled back to value=0
    [DB-B] ABORT -> rolled back to value=0

=== 5) SIMULATED 2PC: COORDINATOR CRASH between phases -> BLOCKING ===
    [DB-A] PREPARED (locked), votes YES
    [DB-B] PREPARED (locked), votes YES
[coordinator] all voted YES -> decision = COMMIT (recorded durably)
[coordinator] *** CRASH *** before sending COMMIT!
[coordinator] participants are now stuck PREPARED, holding locks:
      DB-A{status=PREPARED, value=0, pending=1}
      DB-B{status=PREPARED, value=0, pending=1}
[coordinator] they CANNOT decide alone -> they BLOCK until recovery.

--> THIS is why we prefer the outbox pattern (Day 20) over 2PC.
```

(The exact begin/commit/rollback DEBUG lines from `JpaTransactionManager` will be interleaved — read them; they show the real `REQUIRES_NEW` suspend/resume.)

---

## 🚀 Going Deeper & Next Steps

## Going deeper / senior-level notes

- **XA and JTA.** Real 2PC in Java goes through the **XA** protocol (`XAResource`, `javax.transaction`/`jakarta.transaction`) coordinated by a **JTA** transaction manager such as **Narayana** (JBoss) or **Atomikos**. Spring exposes this via `JtaTransactionManager`. You configure two XA `DataSource`s and the TM drives `prepare`/`commit` for you. It works — but you inherit the blocking weakness and operational weight we simulated, plus the need for XA-capable drivers.
- **`@Transactional` is one resource only.** A plain `@Transactional` over a `JpaTransactionManager` commits exactly one connection. It *cannot* atomically include a Kafka send (Day 18) or a second database. If you ever see code that does `repo.save(...)` then `kafkaTemplate.send(...)` inside one `@Transactional` and assumes atomicity — it's a **dual-write bug**: the DB can commit while the send fails (or vice versa). The fix is the outbox, not XA.
- **Sagas vs 2PC.** A saga trades atomicity for availability: each step is a *local* transaction with a *compensating* transaction (refund, release reservation). Orchestrated (a central saga coordinator issues commands) or choreographed (services react to each other's events). No locks held across the network; failures are normal and handled by compensation. This is the dominant pattern in microservices.
- **Why the outbox (Day 20) is preferred for DB+broker.** Instead of 2PC across DB and Kafka, write the business row **and** an `outbox` event row in **one local transaction** (single resource — a normal `@Transactional` is fully atomic here), then a separate relay/poller publishes outbox rows to Kafka and marks them sent. Crashes are safe: the relay just re-reads unsent rows.
- **Idempotency is the glue (Day 7).** Because the outbox relay (and sagas, and any at-least-once delivery) can publish the same event more than once on retry, **consumers must be idempotent** — dedupe on an event/business key. Without Day 7's idempotency, eventual-consistency patterns produce duplicates. 2PC's *exactly-once* illusion is exactly what you're giving up, and idempotency is how you live without it.
- **Isolation ties back to Day 11.** The `isolation` attribute on `@Transactional` maps straight to the JDBC isolation levels you set by hand on Day 11 (`READ_COMMITTED`, `REPEATABLE_READ`, `SERIALIZABLE`). `readOnly = true` lets the driver/ORM skip dirty-checking and flushing and can route to read replicas — set it on every query-only service method.
- **Connection-pool interaction (Day 9).** `REQUIRES_NEW` and `NESTED`-suspend acquire a *second* connection while the first is held. Nest these in a request with a small pool and you can self-deadlock: every thread holds one connection and waits for a second that the pool can't give. Size pools with propagation in mind.

---

## Stretch goals

1. **Trigger `UnexpectedRollbackException`.** Add `outerCatchesInnerRequired()` to `TransferService`: an outer `@Transactional` calls an inner `REQUIRED` method that throws, the outer *catches and swallows* it, then returns normally. Watch the commit fail with `UnexpectedRollbackException` ("marked as rollback-only"). Then fix it by making the inner `REQUIRES_NEW` so the outer truly can ignore it.
2. **Checked-exception rollback trap.** Add a `BusinessCheckedException extends Exception`, throw it from a `@Transactional` method, and prove the transaction **commits** anyway. Then add `rollbackFor = BusinessCheckedException.class` and prove it now rolls back.
3. **2PC recovery log.** Give the coordinator a durable "decision log." On restart after the simulated crash, have a `recover()` method read the log, see the recorded COMMIT decision, and finish phase 2 on the still-prepared participants — implementing *presumed-abort* recovery. Now you understand what real TMs do (and why crash windows still hurt).
4. **TransactionTemplate.** Re-implement one propagation demo using the *programmatic* API (`TransactionTemplate` / `PlatformTransactionManager.getTransaction(...)`) instead of `@Transactional`, to see the begin/commit calls explicitly — and note it sidesteps the self-invocation problem entirely because there's no proxy involved.

---

## Day 18 teaser

Tomorrow we meet **Kafka** — a distributed, partitioned, replicated commit log. We'll produce and consume events and confront the dual-write problem head-on: a DB commit and a Kafka publish are *two resources*, so today's lesson says we must NOT lean on 2PC. That sets up the **Transactional Outbox (Day 20)** as the real fix, and Event Sourcing (Day 19) as the model where the log itself is the source of truth. Bring your idempotency hat from Day 7.
