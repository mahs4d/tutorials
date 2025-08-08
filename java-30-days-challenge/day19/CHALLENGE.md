# Day 19: Event Sourcing

| | |
|---|---|
| 🏗️ **Project** | **EventBank** — an event-sourced account aggregate with projections & snapshots |
| ☕ **Java & language skills** | Sealed interfaces + records for events, pattern matching, fold/replay, immutability |
| 🧰 **Library / tool** | Spring (events) / Kafka + Jackson for event serialization |
| 🗄️ **DB / distributed-systems concept** | Event Sourcing — append-only event store, projections, CQRS, snapshots |
| 📊 **Difficulty** | Hard |

---

## Concept primer: events as the source of truth

Every system you've built so far stores **current state**: a row in a `accounts` table has a `balance` column, and when money moves you `UPDATE` it. The old value is gone. You can answer *"what is the balance now?"* but not *"what was it last Tuesday?"* or *"what sequence of actions produced it?"* — that history was overwritten.

Event Sourcing inverts this. You never store the balance. You store the **ordered, immutable sequence of facts** that occurred:

```
1. AccountOpened(owner="Mahdi")
2. Deposited(amount=100)
3. Withdrawn(amount=30)
4. Deposited(amount=10)
```

The current balance (80) is *not stored* — it is **derived** by replaying ("folding") these events from the beginning:

```
state = 0
state = apply(state, Deposited 100)  -> 100
state = apply(state, Withdrawn 30)   ->  70
state = apply(state, Deposited 10)   ->  80
```

This should feel deeply familiar. On **Day 1** you replayed a Write-Ahead Log to reconstruct state after a crash: the log was the truth, the in-memory state was a cache of it. Event Sourcing is exactly that idea, except the log is no longer a recovery mechanism *behind* the database — **the log of events IS the database**. There is no separate "real" state being protected by the WAL; the events are the system of record, and any state you hold in memory or in a table is just a materialized view of them.

It also leans hard on **Day 5 (immutability / MVCC)**: events are never updated or deleted. The store is **append-only**. Once `Withdrawn(30)` is written, it is a permanent historical fact. If you later decide it was a mistake, you don't edit it — you append a *new* compensating event (`Reversed(...)`). This is the same "never mutate, only add a new version" discipline that made MVCC snapshots possible, applied at the domain level.

### Events vs. commands — a critical distinction

These are easy to conflate, but they are opposites in intent:

| | **Command** | **Event** |
|---|---|---|
| Tense | Imperative — *"do this"* | Past tense — *"this happened"* |
| Example | `Withdraw(50)` | `Withdrawn(50)` |
| Can fail? | **Yes** — may be rejected (insufficient funds) | **No** — it's a fact; the past can't be rejected |
| Mutability | Transient request | Immutable, persisted forever |
| Naming | `OpenAccount`, `Deposit` | `AccountOpened`, `Deposited` |

A command expresses *intent* and runs through validation (Day 14) and business rules. If the rules pass, the aggregate **decides** what happened and emits one or more events. Once emitted, an event is permanent. You validate commands; you never validate events on the way back in during replay — they already happened.

### The aggregate

The **aggregate** is the consistency boundary that owns the rules. For us it's a single `BankAccount`. It has two responsibilities, and keeping them separate is the whole craft of event sourcing:

1. **`apply(event)`** — *evolve* state given an event. Pure, total, no validation, no decisions. Used both for live changes **and** for replay. This is the fold function.
2. **`handle(command)`** — *decide*: validate the command against current state, and if valid, produce new event(s), which are then applied. This holds the business logic.

The golden rule: **`apply` must never reject anything and never have side effects.** If `apply` could fail or behave differently on replay than it did originally, your history would no longer reconstruct the same state — replay would be non-deterministic, which defeats the entire model.

### Snapshots — bounding replay cost

Replaying from event #1 is fine for 4 events. For an account with 2 million events it's a performance disaster. A **snapshot** is a cached materialization of the aggregate state at a known version `N`. To rebuild, you load the latest snapshot and replay only the events *after* `N`:

```
load snapshot at version 1_000_000  (balance = 5000)
replay events 1_000_001 .. 1_000_004
```

Snapshots are an **optimization, never the source of truth**. You must be able to delete every snapshot and rebuild purely from events. (Again: same spirit as a DB checkpoint truncating WAL replay on Day 1.)

### CQRS & read projections

**CQRS** = Command Query Responsibility Segregation: the model you *write* through is not the model you *read* through.

- **Write model:** the aggregate + event store. Optimized for enforcing invariants on a single aggregate.
- **Read model (projection):** a separate, denormalized, query-optimized view built by consuming the event stream. E.g. a `balance_view` table, a "monthly statement" view, a "total deposited across all accounts" counter.

Projections **subscribe to events** and update themselves. A single event stream can feed *many* projections, each shaped for a different query — without the write side knowing they exist. This is why event sourcing pairs so naturally with the pub/sub log from Day 18.

### Eventual consistency

Because the projection is updated *after* the event is stored (often asynchronously, in another process), there's a window where the event store says balance=80 but the read view still says 70. The read model is **eventually consistent**. This is the price of CQRS, and it's a real product decision: a "show balance" screen reading a projection may briefly lag a deposit the user just made. You handle it with read-your-writes tricks, by reading the aggregate directly for screens that need strong consistency, or by designing the UX to tolerate the lag.

### Tradeoffs — and *why*

| Benefit | Why it falls out of the model |
|---|---|
| **Perfect audit log** | The events *are* the audit log — it's not a bolt-on you can forget to write. |
| **Temporal queries / time travel** | Replay up to any point to get the state "as of" then; debug by replaying a customer's exact history. |
| **Multiple read models, cheaply** | New projection? Replay the existing stream into it. No schema migration of historical data. |
| **Natural fit for messaging** | Events are already the integration contract for other services. |

| Cost | Why |
|---|---|
| **Complexity** | Two models, projections, replay, snapshots — far more moving parts than `UPDATE accounts SET balance=?`. |
| **Eventual consistency** | Read side lags the write side; not free. |
| **Schema evolution is hard** | Events live *forever*; an event written in 2019 must still deserialize in 2026 → versioning & upcasting (see senior notes). |
| **Querying current state is awkward** | "Sum of all balances" requires a projection; you can't just `SELECT SUM(balance)`. |

Don't event-source everything. Use it where **history and auditability are first-class** (ledgers, orders, compliance) and skip it for CRUD-shaped data.

---

## Prerequisites

- JDK 17+ (we use `sealed` interfaces and `record`s).
- Jackson (`com.fasterxml.jackson.core:jackson-databind`) from Day 6.
- Spring context is optional for the core; the final step shows the Spring `ApplicationEventPublisher` projection wiring. The pure-Java steps run with `main()` and no framework, so you can focus on the pattern.

Maven deps (light, as required):

```xml
<dependencies>
  <dependency>
    <groupId>com.fasterxml.jackson.core</groupId>
    <artifactId>jackson-databind</artifactId>
    <version>2.17.1</version>
  </dependency>
  <!-- Optional, only for Step 7's Spring event bus -->
  <dependency>
    <groupId>org.springframework</groupId>
    <artifactId>spring-context</artifactId>
    <version>6.1.10</version>
  </dependency>
</dependencies>
```

---

---

## 🛠️ Project Walkthrough — EventBank

Build a small bank-account event-sourcing engine from the ground up — modeling events, deciding/applying them in an aggregate, persisting to an append-only store, and projecting a read model — then run it and watch the events prove themselves as the source of truth.

## Step 1 — Immutable events as a `sealed interface` + `record`s

Events are facts. They are immutable (Day 5) and the set of possible events is closed and known, so a `sealed` interface is the perfect model: the compiler forces every `switch` over events to be exhaustive, so if you add a new event type you *cannot* forget to handle it in `apply` or a projection.

```java
package day19;

import java.time.Instant;

/**
 * The closed set of facts that can happen to a BankAccount.
 * sealed => exhaustive switches; record => immutable value with structural equality.
 */
public sealed interface AccountEvent
        permits AccountEvent.AccountOpened,
                AccountEvent.Deposited,
                AccountEvent.Withdrawn {

    String accountId();
    Instant occurredAt();

    record AccountOpened(String accountId, String owner, Instant occurredAt)
            implements AccountEvent {}

    record Deposited(String accountId, long amountCents, Instant occurredAt)
            implements AccountEvent {}

    record Withdrawn(String accountId, long amountCents, Instant occurredAt)
            implements AccountEvent {}
}
```

Note we store money as `long` cents — never `double` for currency. And every event carries `occurredAt`, which is what unlocks temporal queries later.

## Step 2 — Commands

Commands are separate types expressing intent. They may be rejected.

```java
package day19;

public sealed interface AccountCommand
        permits AccountCommand.OpenAccount,
                AccountCommand.Deposit,
                AccountCommand.Withdraw {

    String accountId();

    record OpenAccount(String accountId, String owner) implements AccountCommand {}
    record Deposit(String accountId, long amountCents)  implements AccountCommand {}
    record Withdraw(String accountId, long amountCents) implements AccountCommand {}
}
```

## Step 3 — The aggregate: `apply` (evolve) and `handle` (decide)

This is the heart of the pattern. Study the split:

- `apply` is **total and pure** — it never throws on valid history and never validates. It's the fold function used by both live changes and replay.
- `handle` contains **all the business rules** — it validates against current state and *decides* which events to emit.

```java
package day19;

import day19.AccountCommand.*;
import day19.AccountEvent.*;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

public final class BankAccount {

    private String accountId;
    private String owner;
    private long balanceCents;
    private boolean open;
    private long version;   // number of events applied; key for snapshots & optimistic concurrency

    public BankAccount() {
        this.version = 0;
    }

    // ---- EVOLVE: apply an event to mutate state. Pure-ish, total, NEVER validates. ----
    public void apply(AccountEvent event) {
        switch (event) {                       // exhaustive thanks to sealed
            case AccountOpened e -> {
                this.accountId = e.accountId();
                this.owner = e.owner();
                this.balanceCents = 0;
                this.open = true;
            }
            case Deposited e  -> this.balanceCents += e.amountCents();
            case Withdrawn e  -> this.balanceCents -= e.amountCents();
        }
        this.version++;
    }

    // ---- DECIDE: validate a command, return the events it produces (does NOT persist). ----
    public List<AccountEvent> handle(AccountCommand cmd) {
        Instant now = Instant.now();
        List<AccountEvent> produced = new ArrayList<>();

        switch (cmd) {
            case OpenAccount c -> {
                if (open) throw new IllegalStateException("Account already open");
                if (c.owner() == null || c.owner().isBlank())
                    throw new IllegalArgumentException("Owner required");
                produced.add(new AccountOpened(c.accountId(), c.owner(), now));
            }
            case Deposit c -> {
                requireOpen();
                if (c.amountCents() <= 0) throw new IllegalArgumentException("Deposit must be positive");
                produced.add(new Deposited(c.accountId(), c.amountCents(), now));
            }
            case Withdraw c -> {
                requireOpen();
                if (c.amountCents() <= 0) throw new IllegalArgumentException("Withdrawal must be positive");
                if (c.amountCents() > balanceCents)
                    throw new IllegalStateException("Insufficient funds: have %d, want %d"
                            .formatted(balanceCents, c.amountCents()));
                produced.add(new Withdrawn(c.accountId(), c.amountCents(), now));
            }
        }

        // Apply locally so the in-memory aggregate reflects the new events immediately.
        produced.forEach(this::apply);
        return produced;
    }

    private void requireOpen() {
        if (!open) throw new IllegalStateException("Account is not open");
    }

    /** Rebuild an aggregate purely by folding a stream of events. THIS is the WAL replay. */
    public static BankAccount rebuild(Iterable<AccountEvent> history) {
        BankAccount acc = new BankAccount();
        for (AccountEvent e : history) acc.apply(e);
        return acc;
    }

    public String accountId()   { return accountId; }
    public String owner()       { return owner; }
    public long balanceCents()  { return balanceCents; }
    public boolean isOpen()     { return open; }
    public long version()       { return version; }
}
```

The two-phase `handle` (decide, then `apply` the result) means there is exactly **one** place that mutates state — `apply` — whether the event comes from a fresh command or from replaying history. That symmetry is what guarantees determinism.

## Step 4 — The append-only event store

The store only ever **appends** and **reads forward**. No update, no delete. Here's an in-memory implementation plus the Jackson serialization that a real JPA/Kafka store would use on the wire.

```java
package day19;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/** One persisted row in the event store. In JPA this is an @Entity; in Kafka, a record. */
public record StoredEvent(
        long globalSequence,   // monotonic position in the whole log (like a WAL LSN)
        String aggregateId,
        long aggregateVersion, // version within this aggregate's stream
        String eventType,
        String payloadJson) {}

public final class EventStore {

    private final List<StoredEvent> log = new CopyOnWriteArrayList<>();
    private final ObjectMapper mapper = JsonMapper.builder()
            .addModule(new JavaTimeModule())   // Instant <-> ISO-8601
            .build();

    /**
     * Append events for an aggregate. expectedVersion implements OPTIMISTIC CONCURRENCY:
     * if someone else appended since we read, our expectedVersion is stale -> reject.
     * This is how event sourcing protects the single-aggregate invariant (cf. Day 11 isolation).
     */
    public synchronized void append(String aggregateId, long expectedVersion,
                                    List<AccountEvent> events) {
        long current = currentVersion(aggregateId);
        if (current != expectedVersion)
            throw new ConcurrentModificationException(
                    "Optimistic conflict on %s: expected v%d but store is at v%d"
                            .formatted(aggregateId, expectedVersion, current));

        long v = expectedVersion;
        for (AccountEvent e : events) {
            v++;
            try {
                log.add(new StoredEvent(
                        log.size() + 1L,
                        aggregateId,
                        v,
                        e.getClass().getSimpleName(),
                        mapper.writeValueAsString(e)));
            } catch (Exception ex) {
                throw new RuntimeException("Failed to serialize event", ex);
            }
        }
    }

    /** Read this aggregate's events in order, optionally only those AFTER a snapshot version. */
    public List<AccountEvent> readStream(String aggregateId, long afterVersion) {
        List<AccountEvent> out = new ArrayList<>();
        for (StoredEvent se : log) {
            if (se.aggregateId().equals(aggregateId) && se.aggregateVersion() > afterVersion) {
                out.add(deserialize(se));
            }
        }
        return out;
    }

    /** The full global log in order — what projections subscribe to. */
    public List<StoredEvent> readAll() {
        return List.copyOf(log);
    }

    public long currentVersion(String aggregateId) {
        long v = 0;
        for (StoredEvent se : log)
            if (se.aggregateId().equals(aggregateId)) v = se.aggregateVersion();
        return v;
    }

    private AccountEvent deserialize(StoredEvent se) {
        try {
            Class<? extends AccountEvent> type = switch (se.eventType()) {
                case "AccountOpened" -> AccountEvent.AccountOpened.class;
                case "Deposited"     -> AccountEvent.Deposited.class;
                case "Withdrawn"     -> AccountEvent.Withdrawn.class;
                default -> throw new IllegalStateException("Unknown event type: " + se.eventType());
            };
            return mapper.readValue(se.payloadJson(), type);
        } catch (Exception ex) {
            throw new RuntimeException("Failed to deserialize event", ex);
        }
    }
}
```

> JPA mapping (Day 12): make `StoredEvent` an `@Entity` with a unique constraint on `(aggregate_id, aggregate_version)`. That DB-level constraint enforces optimistic concurrency for free — a duplicate version insert fails. Kafka mapping (Day 18): the aggregate id is the partition key (so one aggregate's events stay ordered), `payloadJson` is the value.

(`ConcurrentModificationException` is `java.util.ConcurrentModificationException` — convenient here; in production define your own.)

## Step 5 — Snapshots to bound replay cost

A snapshot is the aggregate's state captured at a version, stored separately. Rebuild = load latest snapshot + replay only events after it.

```java
package day19;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/** Immutable point-in-time capture of aggregate state at a given version. */
public record AccountSnapshot(String accountId, String owner, long balanceCents,
                              boolean open, long version) {}

/** Snapshots are a CACHE, never the source of truth. Safe to delete & rebuild. */
public final class SnapshotStore {
    private final Map<String, AccountSnapshot> latest = new HashMap<>();

    public void save(AccountSnapshot snapshot) {
        latest.put(snapshot.accountId(), snapshot);
    }

    public Optional<AccountSnapshot> load(String accountId) {
        return Optional.ofNullable(latest.get(accountId));
    }
}
```

Add a snapshot-aware loader to a small repository that ties the store + snapshots together:

```java
package day19;

import day19.AccountEvent.*;
import java.util.List;

public final class AccountRepository {

    private static final int SNAPSHOT_EVERY = 50;   // snapshot cadence

    private final EventStore eventStore;
    private final SnapshotStore snapshotStore;

    public AccountRepository(EventStore eventStore, SnapshotStore snapshotStore) {
        this.eventStore = eventStore;
        this.snapshotStore = snapshotStore;
    }

    /** Load an aggregate: start from snapshot if present, replay only newer events. */
    public BankAccount load(String accountId) {
        BankAccount acc = new BankAccount();
        long fromVersion = 0;

        var snap = snapshotStore.load(accountId);
        if (snap.isPresent()) {
            acc.restoreFromSnapshot(snap.get());   // see helper below
            fromVersion = snap.get().version();
        }

        List<AccountEvent> tail = eventStore.readStream(accountId, fromVersion);
        tail.forEach(acc::apply);
        return acc;
    }

    /** Run a command: load, decide, append with optimistic version, maybe snapshot. */
    public List<AccountEvent> execute(String accountId, AccountCommand cmd) {
        BankAccount acc = load(accountId);
        long expectedVersion = acc.version();

        List<AccountEvent> newEvents = acc.handle(cmd);   // may throw on invalid command
        eventStore.append(accountId, expectedVersion, newEvents);

        maybeSnapshot(accountId, acc);
        return newEvents;
    }

    private void maybeSnapshot(String accountId, BankAccount acc) {
        if (acc.version() % SNAPSHOT_EVERY == 0 && acc.isOpen()) {
            snapshotStore.save(new AccountSnapshot(
                    acc.accountId(), acc.owner(), acc.balanceCents(),
                    acc.isOpen(), acc.version()));
        }
    }
}
```

Add the snapshot-restore helper to `BankAccount` (package-private; only the repository uses it):

```java
    // ---- in BankAccount ----
    void restoreFromSnapshot(AccountSnapshot s) {
        this.accountId = s.accountId();
        this.owner = s.owner();
        this.balanceCents = s.balanceCents();
        this.open = s.open();
        this.version = s.version();
    }
```

## Step 6 — A read projection (CQRS read side)

The projection is a separate, denormalized model built from the event stream. Here it's a simple balance + transaction-count view. It consumes events and never touches the aggregate.

```java
package day19;

import day19.AccountEvent.*;
import java.util.HashMap;
import java.util.Map;

/** A query-optimized READ MODEL. Independently rebuildable from the event log. */
public final class BalanceProjection {

    public record View(String accountId, String owner, long balanceCents, int txCount) {}

    private final Map<String, View> views = new HashMap<>();

    /** Fold a single event into the read model. Same shape as aggregate.apply, different target. */
    public void on(AccountEvent event) {
        switch (event) {
            case AccountOpened e ->
                views.put(e.accountId(), new View(e.accountId(), e.owner(), 0, 0));
            case Deposited e -> update(e.accountId(), e.amountCents());
            case Withdrawn e -> update(e.accountId(), -e.amountCents());
        }
    }

    private void update(String id, long delta) {
        View v = views.get(id);
        if (v == null) return; // ignore events for unknown accounts (defensive)
        views.put(id, new View(v.accountId(), v.owner(), v.balanceCents() + delta, v.txCount() + 1));
    }

    public View get(String id) { return views.get(id); }

    /** Blow away and rebuild the entire projection from the full log — a "projection rebuild". */
    public void rebuildFrom(EventStore store) {
        views.clear();
        for (StoredEvent se : store.readAll()) {
            on(deserialize(store, se));
        }
    }

    private AccountEvent deserialize(EventStore store, StoredEvent se) {
        // reuse the store's reader for the aggregate so we get the same event object
        return store.readStream(se.aggregateId(), se.aggregateVersion() - 1).stream()
                .findFirst().orElseThrow();
    }
}
```

> In a real app the projection subscribes to events as they're appended (push), not by scanning. The `rebuildFrom` method shows the killer feature: you can throw away the read model and rebuild it from scratch any time the query shape changes.

## Step 7 — Wiring it up (pure Java + the Spring event-bus variant)

Pure-Java driver showing the whole loop — including a projection updated on each command and a fresh rebuild-from-events to prove the events are the truth:

```java
package day19;

import day19.AccountCommand.*;

public class Day19App {
    public static void main(String[] args) {
        EventStore store = new EventStore();
        SnapshotStore snapshots = new SnapshotStore();
        AccountRepository repo = new AccountRepository(store, snapshots);
        BalanceProjection projection = new BalanceProjection();

        String id = "acc-1";

        // Commands -> events -> appended to store; also fed to the projection.
        for (AccountCommand cmd : new AccountCommand[]{
                new OpenAccount(id, "Mahdi"),
                new Deposit(id, 10_000),     // $100.00
                new Withdraw(id, 3_000),     //  $30.00
                new Deposit(id, 1_000)       //  $10.00
        }) {
            repo.execute(id, cmd).forEach(projection::on);
        }

        BankAccount live = repo.load(id);
        System.out.println("Live aggregate balance: " + live.balanceCents()
                + " cents (v" + live.version() + ")");

        // Prove the events are the source of truth: rebuild a brand-new aggregate from the log.
        var freshHistory = store.readStream(id, 0);
        BankAccount rebuilt = BankAccount.rebuild(freshHistory);
        System.out.println("Rebuilt-from-events balance: " + rebuilt.balanceCents() + " cents");

        // Projection independently rebuilt from the whole log.
        BalanceProjection fresh = new BalanceProjection();
        fresh.rebuildFrom(store);
        System.out.println("Projection view: " + fresh.get(id));

        // Temporal query: balance "as of" the first 2 events only.
        var asOf = store.readStream(id, 0).subList(0, 2);
        System.out.println("Balance as of event #2: "
                + BankAccount.rebuild(asOf).balanceCents() + " cents");

        // Try an invalid command -> rejected, NO event written.
        try {
            repo.execute(id, new Withdraw(id, 9_999_999));
        } catch (IllegalStateException e) {
            System.out.println("Rejected: " + e.getMessage());
        }
        System.out.println("Total events in store: " + store.readAll().size());
    }
}
```

The **Spring variant** swaps the manual `projection::on` for an event bus. After appending, publish each event; `@EventListener`s become your projections. This is the in-process rehearsal of Day 18's Kafka consumers — same shape, just a network hop away.

```java
package day19;

import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

@Component
class EventPublishingRepository {
    private final AccountRepository repo;
    private final ApplicationEventPublisher publisher;

    EventPublishingRepository(AccountRepository repo, ApplicationEventPublisher publisher) {
        this.repo = repo;
        this.publisher = publisher;
    }

    void execute(String accountId, AccountCommand cmd) {
        // Append first (the store is the source of truth), THEN publish for projections.
        repo.execute(accountId, cmd).forEach(publisher::publishEvent);
    }
}

@Component
class SpringBalanceProjection {
    private final BalanceProjection projection = new BalanceProjection();

    @EventListener
    void on(AccountEvent event) {   // one listener per event family; or split per type
        projection.on(event);
    }

    BalanceProjection.View view(String id) { return projection.get(id); }
}
```

> Subtle but important: publish **after** the append commits. If you publish then fail to persist, your projection drifts from a truth that never happened. Publishing reliably *after* a DB commit is precisely the problem the **Transactional Outbox (Day 20)** solves — keep that thread in mind.

## How to run

```bash
# from the day19 module
mvn -q compile exec:java -Dexec.mainClass=day19.Day19App
# or compile to target/classes and:  java -cp target/classes:<jackson jars> day19.Day19App
```

## Expected output

```
Live aggregate balance: 8000 cents (v4)
Rebuilt-from-events balance: 8000 cents
Projection view: View[accountId=acc-1, owner=Mahdi, balanceCents=8000, txCount=3]
Balance as of event #2: 10000 cents
Rejected: Insufficient funds: have 8000, want 9999999
Total events in store: 4
```

What this proves:
- The aggregate's live balance and a **freshly rebuilt-from-events** balance agree → state is genuinely derived, not stored.
- The **projection** (built independently from the stream) matches → the read model is a correct fold of the same events.
- A **temporal query** ("as of event #2") returns the historical balance of 10000, not the current 8000 → time travel.
- The invalid withdrawal is **rejected with no event appended** → commands are validated, events are facts (count stays 4).

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Event versioning & upcasting.** Events outlive your code. When `Deposited` v1 (`amountCents`) becomes v2 (adds `currency`), old events still deserialize. Strategies: keep old types and *upcast* on read (a function `v1 -> v2` that defaults `currency="USD"`), use Jackson `@JsonTypeInfo`/`@JsonSubTypes` with explicit type tags and tolerant readers (`FAIL_ON_UNKNOWN_PROPERTIES=false`), and **never** repurpose a field's meaning. Treat your event schema like a public API with backward-compat guarantees — because that's what it is.
- **Don't roll your own forever.** This is a teaching implementation. Production options: **EventStoreDB** (purpose-built append-only streams, subscriptions, projections), **Axon Framework** (full aggregate/command/event/saga toolkit on the JVM), or a Postgres `events` table + a CDC/outbox pipeline. The concepts transfer directly; the libraries handle concurrency, subscriptions, and snapshots robustly.
- **Optimistic concurrency is the isolation story.** The `expectedVersion` check (Step 4) is how event sourcing enforces the single-aggregate invariant without locks — a direct callback to **Day 11 (isolation)** and a preview of **Day 28 (locks)**, here solved optimistically. The unique constraint on `(aggregate_id, version)` pushes that guarantee into the database.
- **Projection rebuilds in practice.** New query shape, or a projection bug? Spin up a *new* projection, replay the full log into it, then atomically swap reads over (blue/green). Track each projection's last-processed `globalSequence` (a "checkpoint", exactly like a Day 18 consumer offset) so it can resume after a restart instead of replaying from zero.
- **Snapshots are throwaway.** Treat them like the Day 1 WAL checkpoint: a recovery optimization. A test worth writing: delete all snapshots, rebuild every aggregate, assert state is byte-identical. If it isn't, your `apply` isn't pure/deterministic.
- **The messaging trilogy.** Days 18–20 form one story: **Kafka (Day 18)** is the durable, ordered *log* — and an event store can literally *be* a Kafka topic. **Event Sourcing (Day 19, today)** makes that log of events the source of truth inside a service. **Transactional Outbox (Day 20)** is how you publish those events to the outside world atomically with your DB write, closing the "append then publish" gap we flagged in Step 7. Together they give you durable, ordered, exactly-the-state-you-expect messaging.

### Stretch goals

1. **Persist to a real DB.** Make `StoredEvent` a JPA `@Entity` (Day 12) with a unique constraint on `(aggregate_id, aggregate_version)` and a Flyway migration (Day 13). Watch the DB reject a duplicate-version insert — that's your optimistic-concurrency guard, enforced by the engine.
2. **Add a compensating event.** You can't delete `Withdrawn`. Add `WithdrawalReversed(originalEventSeq, amountCents)` and a `ReverseWithdrawal` command. Show the audit trail keeps *both* facts while the balance is corrected — the immutability discipline from Day 5 made visible.
3. **Async, eventually-consistent projection.** Push event handling onto a separate thread (or, ambitiously, a Kafka topic from Day 18). Add a deliberate delay and observe the window where the aggregate already shows the new balance but the projection still lags — then measure how long it takes to catch up.
4. **Event versioning / upcaster.** Introduce `Deposited` v2 with a `currency` field, keep some v1 events in the store, and write an upcaster so a single `readStream` transparently returns v2 events. Prove old and new events fold into one consistent state.

### Day 20 teaser

Today we appended events to a store and then *separately* published them to projections — and flagged the gap: what if the DB commit succeeds but the publish fails, or vice versa? Tomorrow, **Day 20: Transactional Outbox** closes that hole. You'll write the event and an "outbox" row in the *same* local transaction, then a relay reliably forwards outbox rows to Kafka (Day 18) — guaranteeing your event store and the outside world never disagree. The third piece of the messaging trilogy clicks into place.
