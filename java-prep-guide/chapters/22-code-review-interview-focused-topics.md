# 22. Code-Review Interview Focused Topics

A code-review interview hands you a snippet — sometimes a whole small class, sometimes a diff — and asks: "what's wrong with this?" It is a different skill from solving an algorithm puzzle on a whiteboard. Nobody is grading whether you can invent clever code; they are grading whether you can **read someone else's code critically**, recognize the same handful of mistakes that break production systems every day, and talk about them the way a helpful senior engineer would in a real pull request.

Interviewers (and good reviewers in general) judge findings against a rough priority order. Learn this order and use it to decide what to say first:

1. **Correctness** — does the code do what it claims? Off-by-one errors, wrong boolean logic, unhandled edge cases, silent data corruption.
2. **Safety** — will it corrupt shared state, leak a resource, deadlock, or blow up under concurrent/production load, even if the "happy path" looks fine on a single thread?
3. **Design** — is it maintainable and testable? Right abstraction, right exception type, right collection, sensible API shape?
4. **Performance** — is it needlessly slow or memory-hungry, once correctness and safety are already settled?
5. **Style** — naming, formatting, and other things a linter could catch.

If you spend the first three minutes discussing a variable name (style) while a `HashMap` is being mutated from two threads two lines above it (safety), you have failed the interview even if every word about naming was correct. Everything in this chapter is organized around that ordering — find the highest-severity issue first, say it first, and only get to nits if there's time left.

This chapter assumes you have already read the earlier chapters on the underlying mechanics — it does not re-derive *why* `HashMap` isn't thread-safe or *why* checked exceptions exist. It focuses purely on the reviewer's lens: what to look for, how to phrase it, and worked examples.

## Table of Contents

- [The Review Checklist](#the-review-checklist)
- [How to Communicate in a Code-Review Interview](#how-to-communicate-in-a-code-review-interview)
- [Common Java Interview Pitfalls](#common-java-interview-pitfalls)
- [Code Review Scenarios](#code-review-scenarios)
- [Performance Optimization](#performance-optimization)
- [Thread Safety Review](#thread-safety-review)
- [Collections Selection](#collections-selection)
- [Exception Design](#exception-design)
- [API Design Review](#api-design-review)
- [Memory & GC Review](#memory--gc-review)
- [Concurrency Review](#concurrency-review)
- [Modern Java Feature Usage](#modern-java-feature-usage)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## The Review Checklist

Run through this whenever you're handed a snippet, top to bottom, in this order. It mirrors the correctness → safety → design → performance → style priority from the intro.

### Correctness

- [ ] Does every branch (including the ones with no `else`) do the right thing? What happens on empty input, `null`, zero, negative numbers, max values?
- [ ] Are loop bounds right? Off-by-one on `<` vs `<=`, first/last index, empty collections.
- [ ] Is `==` used where `.equals()` was needed (boxed types, `String`, custom objects)?
- [ ] Does the method actually do what its name and signature promise?
- [ ] Are there unhandled edge cases the tests (if any) don't cover?

### Thread Safety & Concurrency

- [ ] Is any state **shared** across threads (static fields, singletons, fields on a class handed to an executor)?
- [ ] If shared and mutable — is access synchronized, or is it a concurrent-safe type (`ConcurrentHashMap`, `AtomicInteger`, `volatile`, immutable value)?
- [ ] Does construction fully finish before the object is published to another thread (no leaking `this`)?
- [ ] Are there lazy-initialized singletons, caches, or `SimpleDateFormat`-style non-thread-safe fields stored `static`?
- [ ] Is every `ExecutorService`/thread pool shut down somewhere? Is every lock released on all paths, including exceptions?
- [ ] Any risk of deadlock from inconsistent lock ordering, or of double-checked locking without `volatile`?

### Resource & Exception Management

- [ ] Is every `Closeable`/`AutoCloseable` (stream, connection, statement, lock) closed on **every** path, including exceptions? Prefer try-with-resources — see `06-exception-handling.md`.
- [ ] Is any `catch` block empty, or does it just log-and-swallow without rethrowing or handling?
- [ ] Is the `catch` scope too wide — wrapping code that can throw for unrelated reasons?
- [ ] Is the original cause preserved when wrapping an exception (`new Foo("msg", e)`, not `new Foo("msg")`)?
- [ ] Are checked vs. unchecked exceptions used appropriately at the API boundary?

### Collections & Data Structures

- [ ] Is the chosen collection right for the access pattern (lookup-heavy vs. order-sensitive vs. dedupe vs. FIFO)? See `07-collections-framework.md`.
- [ ] Are keys used in a `HashMap`/`HashSet` immutable, with a consistent `equals`/`hashCode` pair?
- [ ] Any `equals()` override missing a matching `hashCode()` override?
- [ ] Any unbounded growth — cache, list, map that is only ever added to and never trimmed or evicted?
- [ ] Any `computeIfAbsent`/`merge` call whose lambda has side effects or re-enters the same map?

### API Design

- [ ] Can any public parameter or return value legally be `null`? Is that documented, or should it be `Optional`?
- [ ] Are parameters validated at the boundary (fail fast) instead of failing deep inside with a confusing error?
- [ ] Is the class needlessly mutable when it could be immutable?
- [ ] Do overloads behave consistently, and is the naming unambiguous about side effects (`getX` vs `computeX`)?
- [ ] Would this change break existing callers (removed method, changed semantics, widened exceptions)?

### Performance & Memory

- [ ] Any O(n²) work disguised as "just a loop inside a loop" (nested lookups, repeated linear scans)?
- [ ] Any string concatenation with `+` inside a loop instead of `StringBuilder`?
- [ ] Any object allocation, regex compilation, or reflection happening inside a hot loop that could be hoisted out?
- [ ] Is autoboxing happening in a tight numeric loop (`Integer` instead of `int`, boxed streams)?
- [ ] Any premature `synchronized`/parallelism added without evidence it's needed, at the cost of readability?

### Style (only after everything above is clean)

- [ ] Naming clear and consistent with the domain?
- [ ] Method length / single responsibility reasonable?
- [ ] Are modern idioms used well (records, `var`, streams, switch expressions) without overuse — see [Modern Java Feature Usage](#modern-java-feature-usage)?

## How to Communicate in a Code-Review Interview

Finding the bug is half the exercise. How you talk about it is the other half — interviewers are evaluating whether you'd be a good teammate in an actual review, not just a bug detector.

- **Lead with the highest-severity issue.** Don't narrate top-to-bottom in file order; scan the whole snippet first, rank what you found, and open with the worst one. "Before anything else — this singleton isn't thread-safe" beats starting with a variable-naming nit.
- **Say why, not just what.** "This will break" is a claim; "this will break *because* two threads can both pass the null-check before either assigns the instance, so you can get two singletons" is a review. Explaining the mechanism is what proves you understand it rather than pattern-matching a lint rule.
- **Propose a concrete fix.** Don't stop at "this is unsafe." Say what you'd change it to — an initialization-on-demand holder, `ConcurrentHashMap`, a try-with-resources block — ideally in a sentence or two of code, not just an adjective.
- **Separate blocking issues from nits.** Explicitly label things: "this one's a blocker, it will corrupt data in production" vs. "this is a nit, purely a naming preference — not worth blocking on." Interviewers want to see you can triage, not just that you can spot fifteen things.
- **Ask questions instead of accusing.** "Was this meant to run in a single-threaded context, or could multiple requests hit it concurrently?" reads as collaborative. "You clearly didn't think about thread safety" reads as hostile — and might be wrong if there's context you don't have (maybe it really is confined to one thread by contract).
- **Say what's good, too.** If the error handling is solid or the naming is clear, say so briefly. It shows judgment (you're not fault-finding for its own sake) and it's simply what a real review looks like — real PRs aren't 100% criticism.

A reasonable spoken structure: *"The biggest problem is X, because Y — I'd fix it by Z. There's also a smaller issue with A. Everything else — B, C — are just nits, not blockers. One thing I like here is D."*

## Common Java Interview Pitfalls

These are the recurring, easy-to-miss patterns interviewers plant because they show up constantly in real codebases. Recognize the *shape* of each one instantly — you shouldn't need to trace through logic to spot them.

1. **`==` on boxed types or `String`.** `Integer a = 1000, b = 1000; a == b` is `false` outside the `-128..127` cache range. Always `.equals()` for objects.
2. **Autoboxing NPE.** `Integer count = map.get(key); if (count > 0)` throws `NullPointerException` when `count` is `null` — the unboxing happens invisibly at `>`.
3. **Mutable `static` fields shared across requests/threads** — the classic `SimpleDateFormat`, lazily-initialized singleton, or cache-as-`HashMap` pattern (see Scenarios below).
4. **`equals()` without `hashCode()`**, or vice versa — breaks every hash-based collection silently (no compiler error, just wrong behavior).
5. **Using a mutable object as a `Map`/`Set` key**, then mutating it after insertion — the entry becomes unreachable by lookup.
6. **String concatenation with `+` in a loop** — quadratic behavior because each `+` builds a new `String`.
7. **Catching `Exception` (or `Throwable`) far wider than needed**, and/or doing nothing with it — see `06-exception-handling.md` for the full treatment.
8. **`finally` with a `return`/`throw`** that silently discards the exception that was in flight.
9. **Off-by-one and empty-collection bugs** — `for (int i = 0; i <= list.size(); i++)`, or forgetting a collection can be empty before calling `.get(0)`.
10. **Integer overflow from `int` arithmetic** — `int total = a * b;` overflows silently where `long` wouldn't; no exception, just a wrong answer.
11. **Comparing floating point with `==`** instead of an epsilon, or using `double`/`float` for money instead of `BigDecimal`.
12. **Modifying a collection while iterating it** with a plain `for-each`, throwing `ConcurrentModificationException` — should use an `Iterator.remove()`, `removeIf`, or a copy.
13. **Ignoring the return value of an immutable operation** — `str.trim();` on its own line does nothing, because `String` is immutable and `trim()` returns a new value.
14. **Leaking `this` from a constructor** — starting a thread, registering a listener, or passing `this` to another object before the constructor finishes (see Scenario 15).
15. **Unbounded recursion or unbounded collection growth** with no base case / no eviction, eventually a `StackOverflowError` or `OutOfMemoryError` under load rather than at compile time.
16. **Checked exception swallowed and converted to a generic, cause-less `RuntimeException`**, losing the original stack trace.
17. **Using `Optional` as a field type, method parameter, or inside a collection** instead of only as a return type — it's designed for one purpose, and misusing it is itself a common interview trap (see [API Design Review](#api-design-review)).
18. **Relying on `HashMap`/`HashSet` iteration order**, which is unspecified and can silently change between JDK versions or after resizing.

These patterns recur throughout the scenarios below — as you read each one, try to name which pitfall(s) from this list it is before reading the hidden answer.

## Code Review Scenarios

Each scenario is realistic production-style code with a real, plantable bug (sometimes more than one). Read the snippet, form your own opinion, rank the issues by severity, then expand the review to check yourself.

### Scenario 1: The Configuration Singleton

```java
public class ConfigManager {
    private static ConfigManager instance;
    private final Map<String, String> settings = new HashMap<>();

    private ConfigManager() {
        settings.put("timeout", "30");
        settings.put("retries", "3");
    }

    public static ConfigManager getInstance() {
        if (instance == null) {
            instance = new ConfigManager();
        }
        return instance;
    }

    public String get(String key) {
        return settings.get(key);
    }

    public void set(String key, String value) {
        settings.put(key, value);
    }
}
```

<details><summary>Show the review</summary>

- **Blocker — non-thread-safe lazy singleton.** `getInstance()` has a classic race: two threads can both see `instance == null`, and both construct a `ConfigManager`, handing out two different instances to different callers. This is the textbook double-checked-locking setup, minus the locking. Under low concurrency it "works" in testing and then fails intermittently in production — the worst kind of bug to debug.
- **Major — `settings` map is a mutable `HashMap` with no synchronization**, and `set()`/`get()` can be called concurrently once multiple threads hold references (even to the same instance). Concurrent writes to a plain `HashMap` can corrupt its internal structure, not just race on values.
- **Minor — no way to remove/reset settings** for tests, which tends to leak state between test cases sharing the singleton. Not a functional bug, but worth flagging as a design smell for testability.

Fixed code — initialization-on-demand holder for lazy, thread-safe singleton construction, plus a concurrent-safe backing map:

```java
public class ConfigManager {
    private final Map<String, String> settings = new ConcurrentHashMap<>();

    private ConfigManager() {
        settings.put("timeout", "30");
        settings.put("retries", "3");
    }

    private static final class Holder {
        static final ConfigManager INSTANCE = new ConfigManager();
    }

    public static ConfigManager getInstance() {
        return Holder.INSTANCE; // JVM class-loading guarantees are the "locking"
    }

    public String get(String key) {
        return settings.get(key);
    }

    public void set(String key, String value) {
        settings.put(key, value);
    }
}
```

The holder idiom is thread-safe without explicit locking because the JVM guarantees a class is initialized exactly once, under a lock, the first time it's actively used — see `13-concurrency-core.md` for the Java Memory Model guarantees behind this.

</details>

### Scenario 2: The Request Counter

```java
public class RequestCounterService {
    private final Map<String, Integer> countsByEndpoint = new HashMap<>();

    // Called concurrently from many servlet request threads
    public void recordRequest(String endpoint) {
        Integer current = countsByEndpoint.get(endpoint);
        if (current == null) {
            countsByEndpoint.put(endpoint, 1);
        } else {
            countsByEndpoint.put(endpoint, current + 1);
        }
    }

    public int getCount(String endpoint) {
        return countsByEndpoint.getOrDefault(endpoint, 0);
    }

    public Map<String, Integer> snapshot() {
        return countsByEndpoint;
    }
}
```

<details><summary>Show the review</summary>

- **Blocker — plain `HashMap` mutated from multiple threads.** The comment even says "called concurrently," so this isn't a hypothetical: concurrent `put()` calls on a `HashMap` can corrupt internal bucket structure (infinite loops during resize in older JDKs, or silently dropped entries), not just lose a counter increment.
- **Major — read-then-write is not atomic** even with a concurrent map: `get` then `put(current + 1)` is a classic lost-update race — two threads can both read the same `current` and both write `current + 1`, losing one increment. Switching the map type alone does not fix this.
- **Minor — `snapshot()` returns the live internal map**, not a copy, so callers can mutate internal state, and any iteration over it concurrently with a write is still unsafe even with `ConcurrentHashMap` (iteration is weakly consistent, which is fine for a snapshot but callers should not assume they can safely mutate what they get back).

Fixed code — `ConcurrentHashMap` plus an atomic update method (`merge`, or an `AtomicInteger` per key) instead of get-then-put, and a defensive copy for the snapshot:

```java
public class RequestCounterService {
    private final Map<String, AtomicInteger> countsByEndpoint = new ConcurrentHashMap<>();

    public void recordRequest(String endpoint) {
        countsByEndpoint.computeIfAbsent(endpoint, e -> new AtomicInteger())
                        .incrementAndGet();
    }

    public int getCount(String endpoint) {
        AtomicInteger count = countsByEndpoint.get(endpoint);
        return count == null ? 0 : count.get();
    }

    public Map<String, Integer> snapshot() {
        Map<String, Integer> copy = new HashMap<>();
        countsByEndpoint.forEach((k, v) -> copy.put(k, v.get()));
        return copy;
    }
}
```

</details>

### Scenario 3: The Order Deduplicator

```java
public class OrderKey {
    private String customerId;
    private LocalDate orderDate;

    public OrderKey(String customerId, LocalDate orderDate) {
        this.customerId = customerId;
        this.orderDate = orderDate;
    }

    public void setOrderDate(LocalDate orderDate) {
        this.orderDate = orderDate;
    }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof OrderKey other)) return false;
        return customerId.equals(other.customerId) && orderDate.equals(other.orderDate);
    }

    @Override
    public int hashCode() {
        return Objects.hash(customerId, orderDate);
    }
}

public class OrderDeduplicator {
    private final Map<OrderKey, Order> seen = new HashMap<>();

    public boolean isDuplicate(OrderKey key, Order order, LocalDate correctedDate) {
        key.setOrderDate(correctedDate); // apply a late correction before checking
        boolean duplicate = seen.containsKey(key);
        seen.put(key, order);
        return duplicate;
    }
}
```

<details><summary>Show the review</summary>

- **Blocker — mutable object used as a hash-based map key.** `OrderKey` has a setter (`setOrderDate`) and participates in `hashCode()`. Mutating a key after it's already stored in a `HashMap` changes its hash code, so the entry ends up in the wrong bucket — it becomes permanently unreachable via `get`/`containsKey` even though it's still sitting in the map, silently leaking memory *and* breaking the dedup logic it exists to implement.
- **Major — the mutation happens right before the lookup**, so even a *new* key that's mutated before first insertion could match against a stale bucket layout if the same key object is reused/cached elsewhere. Any key type used in a hash-based collection must be effectively immutable for its whole lifetime.
- **Minor — `OrderKey` fields aren't `final`**, which invites exactly this kind of mistake in the future even if today's only caller happens to use it "safely" for a single mutation before insertion.

Fixed code — make `OrderKey` immutable; if a corrected date is needed, build a new key instead of mutating the old one:

```java
public final class OrderKey {
    private final String customerId;
    private final LocalDate orderDate;

    public OrderKey(String customerId, LocalDate orderDate) {
        this.customerId = Objects.requireNonNull(customerId);
        this.orderDate = Objects.requireNonNull(orderDate);
    }

    public OrderKey withDate(LocalDate correctedDate) {
        return new OrderKey(customerId, correctedDate); // new key, old one untouched
    }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof OrderKey other)) return false;
        return customerId.equals(other.customerId) && orderDate.equals(other.orderDate);
    }

    @Override
    public int hashCode() {
        return Objects.hash(customerId, orderDate);
    }
}

public class OrderDeduplicator {
    private final Map<OrderKey, Order> seen = new HashMap<>();

    public boolean isDuplicate(OrderKey key, Order order, LocalDate correctedDate) {
        OrderKey correctedKey = key.withDate(correctedDate);
        boolean duplicate = seen.containsKey(correctedKey);
        seen.put(correctedKey, order);
        return duplicate;
    }
}
```

A record (`03-modern-java-language-features.md`) would have made this mistake impossible by construction, since record components can't have setters.

</details>

### Scenario 4: The Report Exporter

```java
public class ReportExporter {

    public String loadTemplate(String templatePath) throws IOException {
        FileInputStream fis = new FileInputStream(templatePath);
        byte[] data = fis.readAllBytes();
        return new String(data, StandardCharsets.UTF_8);
    }

    public void exportToFile(String content, String outputPath) throws IOException {
        FileOutputStream fos = new FileOutputStream(outputPath);
        Connection conn = dataSource.getConnection();
        PreparedStatement stmt = conn.prepareStatement("INSERT INTO exports(path) VALUES (?)");
        stmt.setString(1, outputPath);
        stmt.executeUpdate();
        fos.write(content.getBytes(StandardCharsets.UTF_8));
        fos.close();
    }

    private DataSource dataSource;
}
```

<details><summary>Show the review</summary>

- **Blocker — `loadTemplate` never closes `fis`.** Every call leaks a file descriptor; under load this exhausts the process's file descriptor limit and every subsequent I/O call in the JVM starts failing, not just this method.
- **Blocker — `exportToFile` leaks `conn` and `stmt` unconditionally**, and additionally leaks `fos` if `stmt.executeUpdate()` throws (e.g., a SQL error), because `fos.close()` is only reached on the successful path. Database connections are a scarcer, more expensive resource than file handles — this will exhaust the connection pool quickly.
- **Major — mixing an I/O write and a DB write with no transactional/ordering thought.** If the DB insert succeeds but the file write fails (or vice versa), the export record and the exported file disappear out of sync with no rollback or compensation. Not the focus of this exercise, but worth a one-line flag.

Fixed code — try-with-resources for every `AutoCloseable`, guaranteeing cleanup on every path including exceptions (see `06-exception-handling.md`):

```java
public class ReportExporter {

    public String loadTemplate(String templatePath) throws IOException {
        try (FileInputStream fis = new FileInputStream(templatePath)) {
            return new String(fis.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    public void exportToFile(String content, String outputPath) throws IOException, SQLException {
        try (Connection conn = dataSource.getConnection();
             PreparedStatement stmt = conn.prepareStatement("INSERT INTO exports(path) VALUES (?)")) {
            stmt.setString(1, outputPath);
            stmt.executeUpdate();
        }
        try (FileOutputStream fos = new FileOutputStream(outputPath)) {
            fos.write(content.getBytes(StandardCharsets.UTF_8));
        }
    }

    private DataSource dataSource;
}
```

</details>

### Scenario 5: The Payment Processor

```java
public class PaymentProcessor {

    public PaymentResult process(PaymentRequest request) {
        try {
            validate(request);
            ChargeResult charge = gateway.charge(request.amount(), request.cardToken());
            ledger.record(request.orderId(), charge.transactionId());
            return PaymentResult.success(charge.transactionId());
        } catch (Exception e) {
            return PaymentResult.failure("Payment could not be processed");
        }
    }

    private void validate(PaymentRequest request) {
        if (request.amount().compareTo(BigDecimal.ZERO) <= 0) {
            throw new IllegalArgumentException("amount must be positive");
        }
    }
}
```

<details><summary>Show the review</summary>

- **Blocker — the exception is swallowed with no logging and no rethrow.** `catch (Exception e) { return PaymentResult.failure(...); }` discards every detail about *why* a payment failed — network timeout, declined card, a `NullPointerException` bug in `validate`, or the ledger write failing after the charge already succeeded. Support will get "payment could not be processed" tickets with zero ability to diagnose them, and real bugs will hide behind the same generic message as legitimate declines.
- **Blocker — the charge can succeed but the ledger write can fail**, and both end up reported identically as a generic failure to the caller. That's a customer charged with no record of it — a money-losing (or trust-losing) bug, not just a logging annoyance. This needs to be handled as a distinct case (compensating action, alerting, retry-safe ledger write), not lumped into the same catch.
- **Major — `catch (Exception e)` is too wide.** It will also catch bugs (`NullPointerException`, `ClassCastException`) from anywhere in this call chain and report them as ordinary payment failures instead of surfacing them as defects to be fixed.

Fixed code — narrow the catch to expected failure modes, log with full context, and treat "charged but not recorded" as its own alertable case:

```java
public class PaymentProcessor {
    private static final Logger log = LoggerFactory.getLogger(PaymentProcessor.class);

    public PaymentResult process(PaymentRequest request) {
        validate(request); // let IllegalArgumentException propagate — it's a caller bug, fail fast

        ChargeResult charge;
        try {
            charge = gateway.charge(request.amount(), request.cardToken());
        } catch (GatewayException e) {
            log.warn("Charge declined for order {}: {}", request.orderId(), e.getMessage());
            return PaymentResult.failure("Payment declined: " + e.getMessage());
        }

        try {
            ledger.record(request.orderId(), charge.transactionId());
        } catch (LedgerException e) {
            // Money moved but we failed to record it — this must page someone, not just log.
            log.error("CRITICAL: charge {} succeeded but ledger record failed for order {}",
                    charge.transactionId(), request.orderId(), e);
            alerting.pageOnCall("Unreconciled charge " + charge.transactionId());
            throw new UnreconciledChargeException(request.orderId(), charge.transactionId(), e);
        }

        return PaymentResult.success(charge.transactionId());
    }

    private void validate(PaymentRequest request) {
        if (request.amount().compareTo(BigDecimal.ZERO) <= 0) {
            throw new IllegalArgumentException("amount must be positive");
        }
    }
}
```

</details>

### Scenario 6: The Order Summary Builder

```java
public class OrderSummaryBuilder {

    public List<OrderSummary> buildSummaries(List<Order> orders) {
        List<OrderSummary> summaries = new ArrayList<>();
        for (Order order : orders) {
            Customer customer = customerRepository.findById(order.customerId());
            List<LineItem> items = lineItemRepository.findByOrderId(order.id());
            BigDecimal total = BigDecimal.ZERO;
            for (LineItem item : items) {
                for (LineItem discountRule : discountRules) {
                    if (discountRule.appliesTo(item)) {
                        item = discountRule.apply(item);
                    }
                }
                total = total.add(item.price());
            }
            summaries.add(new OrderSummary(order.id(), customer.name(), total));
        }
        return summaries;
    }

    private CustomerRepository customerRepository;
    private LineItemRepository lineItemRepository;
    private List<LineItem> discountRules;
}
```

<details><summary>Show the review</summary>

- **Blocker — classic N+1 query pattern.** For every single order, `customerRepository.findById(...)` and `lineItemRepository.findByOrderId(...)` each issue a separate round trip to the database. For 1,000 orders that's 2,000+ queries where 2-3 would do. This is the single most common real-world performance bug found in code review, and it degrades gracefully in dev (small data) and catastrophically in production.
- **Major — O(n × m) discount-rule loop nested inside the per-order loop**, itself nested inside the per-line-item loop — for `orders × items × rules` this is cubic in the worst case relative to the input sizes, when the rules could be pre-indexed or applied via a single pass per item without needing to be recomputed identically for every order.
- **Minor — `discountRule.apply(item)` reassigns the loop variable `item`**, which is legal but confusing: it looks like line items and discount rules are the same type (`LineItem`), which they likely aren't conceptually — this is either a copy-paste type mismatch or a naming/API smell worth a question to the author.

Fixed code — batch-load customers and line items once, outside the per-order loop:

```java
public class OrderSummaryBuilder {

    public List<OrderSummary> buildSummaries(List<Order> orders) {
        List<String> customerIds = orders.stream().map(Order::customerId).distinct().toList();
        List<Long> orderIds = orders.stream().map(Order::id).toList();

        Map<String, Customer> customersById = customerRepository.findAllByIds(customerIds)
                .stream().collect(Collectors.toMap(Customer::id, c -> c));
        Map<Long, List<LineItem>> itemsByOrderId = lineItemRepository.findByOrderIds(orderIds)
                .stream().collect(Collectors.groupingBy(LineItem::orderId));

        List<OrderSummary> summaries = new ArrayList<>();
        for (Order order : orders) {
            Customer customer = customersById.get(order.customerId());
            List<LineItem> items = itemsByOrderId.getOrDefault(order.id(), List.of());
            BigDecimal total = BigDecimal.ZERO;
            for (LineItem item : items) {
                LineItem discounted = applyDiscounts(item);
                total = total.add(discounted.price());
            }
            summaries.add(new OrderSummary(order.id(), customer.name(), total));
        }
        return summaries;
    }

    private LineItem applyDiscounts(LineItem item) {
        for (DiscountRule rule : discountRules) {
            if (rule.appliesTo(item)) {
                item = rule.apply(item);
            }
        }
        return item;
    }

    private CustomerRepository customerRepository;
    private LineItemRepository lineItemRepository;
    private List<DiscountRule> discountRules;
}
```

Two round trips total, regardless of how many orders are in the list. See [Performance Optimization](#performance-optimization) for more on spotting this pattern.

</details>

### Scenario 7: The Audit Log Formatter

```java
public class AuditLogFormatter {

    public String formatEntries(List<AuditEntry> entries) {
        String report = "";
        report += "Audit Report\n";
        report += "=============\n";
        for (AuditEntry entry : entries) {
            report += entry.timestamp() + " | " + entry.user() + " | " + entry.action();
            report += "\n";
        }
        report += "Total entries: " + entries.size() + "\n";
        return report;
    }
}
```

<details><summary>Show the review</summary>

- **Major — string concatenation with `+=` inside a loop.** `String` is immutable, so every `+=` allocates a brand-new `String` and copies everything seen so far into it. For `n` entries, that's roughly O(n²) total character copying. For a handful of audit entries this is invisible; for a report over tens of thousands of entries it becomes a real, measurable slowdown and a burst of short-lived garbage for the GC to clean up.
- **Nit — mixing string-building style** (some lines start fresh, some append) makes the method harder to scan; consolidating onto one `StringBuilder` also reads more clearly as "building one thing incrementally."

Fixed code — a single `StringBuilder` for the whole method:

```java
public class AuditLogFormatter {

    public String formatEntries(List<AuditEntry> entries) {
        StringBuilder report = new StringBuilder();
        report.append("Audit Report\n");
        report.append("=============\n");
        for (AuditEntry entry : entries) {
            report.append(entry.timestamp()).append(" | ")
                  .append(entry.user()).append(" | ")
                  .append(entry.action()).append('\n');
        }
        report.append("Total entries: ").append(entries.size()).append('\n');
        return report.toString();
    }
}
```

Note: a single `+` expression made of several pieces on one line (`a + " " + b`) is fine — javac compiles that to one `StringBuilder` chain automatically. The bug is specifically repeated concatenation *across loop iterations*, where each iteration starts from an already-large accumulated string.

</details>

### Scenario 8: The Invoice Date Parser

```java
public class InvoiceDateParser {

    private static final SimpleDateFormat FORMAT = new SimpleDateFormat("yyyy-MM-dd");

    public Date parse(String rawDate) throws ParseException {
        return FORMAT.parse(rawDate);
    }

    public String format(Date date) {
        return FORMAT.format(date);
    }
}
```

<details><summary>Show the review</summary>

- **Blocker — `SimpleDateFormat` is not thread-safe, and here it's a shared `static final` field.** Its internal `Calendar` field is mutated during both `parse()` and `format()`. Two threads calling either method concurrently can interleave their mutations of that shared `Calendar`, producing wrong dates, garbage results, or a thrown exception — silently and intermittently, which makes it brutal to reproduce in a bug report.
- **Major — even ignoring the field being `static`**, sharing *any single* `SimpleDateFormat` instance across threads (static or not) has this problem; the fix is either "don't share it" or "don't use `SimpleDateFormat`."

Fixed code — use `java.time`'s `DateTimeFormatter`, which is immutable and thread-safe by design (see `09-date-time-and-localization.md`):

```java
public class InvoiceDateParser {

    private static final DateTimeFormatter FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd");

    public LocalDate parse(String rawDate) {
        return LocalDate.parse(rawDate, FORMAT);
    }

    public String format(LocalDate date) {
        return date.format(FORMAT);
    }
}
```

If `SimpleDateFormat` truly cannot be avoided (some legacy API demands `Date`), the safe fallback is a fresh instance per call, or one instance per thread via `ThreadLocal<SimpleDateFormat>` — never a shared mutable field.

</details>

### Scenario 9: The Translation Cache

```java
public class TranslationService {

    private static final Map<String, String> cache = new HashMap<>();

    public String translate(String key, String locale) {
        String cacheKey = key + ":" + locale;
        return cache.computeIfAbsent(cacheKey, k -> callTranslationApi(key, locale));
    }

    private String callTranslationApi(String key, String locale) {
        // expensive network call
        return translationClient.fetch(key, locale);
    }

    private TranslationClient translationClient;
}
```

<details><summary>Show the review</summary>

- **Blocker — unbounded cache.** Every distinct `(key, locale)` pair ever requested stays in `cache` for the lifetime of the process — there is no eviction, no size cap, no TTL. In a long-running service with many keys/locales (or with any attacker- or user-influenced key), this is a slow, guaranteed memory leak that eventually produces an `OutOfMemoryError` days or weeks after deployment, long after the code that caused it has been forgotten.
- **Major — plain `HashMap` shared as a `static` field with no synchronization**, read and written from what is presumably a multi-threaded service (translation lookups happening per request). Same category of bug as Scenario 2 — needs a concurrent-safe map at minimum, independent of the eviction problem.
- **Minor — string-concatenated composite key (`key + ":" + locale`)** works but is fragile (a `key` containing `:` could collide with a different `locale` split) and allocates a new `String` per lookup; a small record `TranslationKey(String key, String locale)` is clearer and avoids the delimiter-collision risk.

Fixed code — bounded, size- and time-limited cache (e.g., Caffeine), or at minimum a bounded `LinkedHashMap`-based LRU, plus a proper composite key:

```java
public class TranslationService {

    private record TranslationKey(String key, String locale) {}

    private final Cache<TranslationKey, String> cache = Caffeine.newBuilder()
            .maximumSize(10_000)
            .expireAfterWrite(Duration.ofHours(6))
            .build();

    public String translate(String key, String locale) {
        return cache.get(new TranslationKey(key, locale),
                k -> callTranslationApi(k.key(), k.locale()));
    }

    private String callTranslationApi(String key, String locale) {
        return translationClient.fetch(key, locale);
    }

    private TranslationClient translationClient;
}
```

See [Memory & GC Review](#memory--gc-review) for the general "unbounded cache" pattern and how to spot it without a library like Caffeine available.

</details>

### Scenario 10: The Duplicate-Free Product List

```java
public class Product {
    private final String sku;
    private String displayName;

    public Product(String sku, String displayName) {
        this.sku = sku;
        this.displayName = displayName;
    }

    public void rename(String newDisplayName) {
        this.displayName = newDisplayName;
    }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof Product other)) return false;
        return sku.equals(other.sku);
    }

    public String getSku() { return sku; }
}

public class ProductCatalog {
    private final Set<Product> products = new HashSet<>();

    public void add(Product product) {
        products.add(product);
    }

    public boolean contains(String sku) {
        return products.contains(new Product(sku, ""));
    }
}
```

<details><summary>Show the review</summary>

- **Blocker — `equals()` is overridden without overriding `hashCode()`.** `Product` still uses `Object`'s identity-based `hashCode()`, so two `Product` instances with the same `sku` are `equals()` but almost certainly land in different `HashSet` buckets. `contains(String sku)` builds a throwaway `Product` with a different identity hash than any stored one, so it will essentially always return `false` — the exact opposite of what this class exists to do. This is a silent, no-compiler-warning correctness bug: the equals/hashCode contract ("equal objects must have equal hash codes") is broken.
- **Major — `Product` is mutable (`rename`) but is stored in a `HashSet`.** Even after fixing `hashCode()`, `sku` is the only field participating in equality, so mutating `displayName` is actually safe here — but this is fragile by accident, not by design; a future field added to `equals`/`hashCode` that is also mutable would reintroduce Scenario 3's mutable-key bug.
- **Nit — `contains(String sku)` constructs a throwaway `Product("", sku)` just to do a lookup** — legal once `equals`/`hashCode` are fixed, but a `Map<String, Product>` keyed directly by `sku` is more direct and avoids the wasted allocation per lookup.

Fixed code — add the missing `hashCode()`, and prefer keying directly by the identity field:

```java
public class Product {
    private final String sku;
    private String displayName;

    public Product(String sku, String displayName) {
        this.sku = sku;
        this.displayName = displayName;
    }

    public void rename(String newDisplayName) {
        this.displayName = newDisplayName;
    }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof Product other)) return false;
        return sku.equals(other.sku);
    }

    @Override
    public int hashCode() {
        return sku.hashCode();
    }

    public String getSku() { return sku; }
}

public class ProductCatalog {
    private final Map<String, Product> productsBySku = new HashMap<>();

    public void add(Product product) {
        productsBySku.put(product.getSku(), product);
    }

    public boolean contains(String sku) {
        return productsBySku.containsKey(sku);
    }
}
```

</details>

### Scenario 11: The Per-User Session Cache

```java
public class SessionCache {

    private final Map<String, List<String>> activityByUser = new ConcurrentHashMap<>();

    public void recordActivity(String userId, String activity) {
        activityByUser.computeIfAbsent(userId, id -> {
            List<String> history = loadHistoryFromDb(id); // side effect: DB call inside the lambda
            history.add(activity);
            return history;
        });
    }

    public void addRelatedUser(String userId, String relatedUserId) {
        activityByUser.computeIfAbsent(userId, id -> new ArrayList<>())
                .add("related:" + relatedUserId);
        activityByUser.computeIfAbsent(relatedUserId, id ->
                activityByUser.get(userId)); // reuse the same list reference
    }

    private List<String> loadHistoryFromDb(String userId) { /* ... */ return new ArrayList<>(); }
}
```

<details><summary>Show the review</summary>

- **Blocker — `recordActivity` only adds the new activity on the *first* call for a user.** `computeIfAbsent`'s mapping function only runs when the key is absent; on every subsequent call for a user who's already in the map, the lambda is skipped entirely and `activity` is silently dropped. This looks like it works in a quick manual test (first call always "works") and then quietly loses data for every repeat visitor — the most dangerous kind of bug because the obvious test case passes.
- **Blocker — `loadHistoryFromDb` (a blocking, potentially slow DB call) runs *inside* the `computeIfAbsent` mapping function**, which `ConcurrentHashMap` executes while holding an internal lock on that key's bin. If it's slow, or if it happens to (directly or transitively) call back into the same map for the same key, you can get very long stalls for every other thread touching that bin, or in some JDK versions an actual deadlock/`IllegalStateException` (recursive update). `computeIfAbsent`'s mapping function must be fast and must never touch the same map.
- **Major — `addRelatedUser` aliases two users to the *same mutable `List` instance*.** After this call, `activityByUser.get(userId)` and `activityByUser.get(relatedUserId)` are the identical list object; recording activity for one silently mutates the "history" of the other. This is almost certainly not intended and is very hard to spot from the call site.

Fixed code — separate the DB load from the update, keep the mapping function cheap and side-effect-free with respect to the map itself, and never alias mutable collections between keys:

```java
public class SessionCache {

    private final Map<String, List<String>> activityByUser = new ConcurrentHashMap<>();

    public void recordActivity(String userId, String activity) {
        List<String> history = activityByUser.computeIfAbsent(userId,
                id -> new CopyOnWriteArrayList<>(loadHistoryFromDb(id))); // fast to construct, DB call happens once, outside any recursive risk
        history.add(activity); // always runs, regardless of whether this was the first call
    }

    public void addRelatedUser(String userId, String relatedUserId) {
        activityByUser.computeIfAbsent(userId, id -> new CopyOnWriteArrayList<>())
                .add("related:" + relatedUserId);
        activityByUser.computeIfAbsent(relatedUserId, id -> new CopyOnWriteArrayList<>())
                .add("related-to:" + userId); // own list, not a shared reference
    }

    private List<String> loadHistoryFromDb(String userId) { /* ... */ return new ArrayList<>(); }
}
```

</details>

### Scenario 12: The Invalid Order Filter

```java
public class OrderValidationReport {

    public List<String> findInvalidOrderIds(List<Order> orders) {
        List<String> invalidIds = new ArrayList<>();
        int[] invalidCount = {0};

        orders.parallelStream().forEach(order -> {
            if (!isValid(order)) {
                invalidIds.add(order.id());
                invalidCount[0]++;
            }
        });

        System.out.println("Found " + invalidCount[0] + " invalid orders");
        return invalidIds;
    }

    private boolean isValid(Order order) {
        return order.total().compareTo(BigDecimal.ZERO) >= 0 && order.customerId() != null;
    }
}
```

<details><summary>Show the review</summary>

- **Blocker — `invalidIds.add(...)` from a `parallelStream().forEach`.** `ArrayList` is not thread-safe, and `parallelStream()` runs the lambda on multiple threads from the common `ForkJoinPool`. Concurrent, unsynchronized `add()` calls on an `ArrayList` can throw `ArrayIndexOutOfBoundsException`, silently drop elements, or corrupt the list's internal array — a data race hiding behind a one-line stream call that looks innocuous.
- **Major — `invalidCount[0]++` is also a race**, for the same reason: the array-boxing trick to mutate a captured variable from a lambda doesn't make the increment atomic. Multiple threads can read-increment-write the same stale value and lose counts.
- **Major — reaching for `parallelStream()` at all here is questionable** ([Performance Optimization](#performance-optimization) covers this pattern): `isValid` is a trivial, non-blocking check, so the overhead of splitting work across the shared `ForkJoinPool` easily outweighs any benefit, and it puts load on a JVM-wide shared pool that other unrelated parallel streams also depend on.
- **Nit — mixing a raw `System.out.println` for what looks like log-worthy operational information** instead of a logger.

Fixed code — collect via a proper stream `Collector` (thread-safe by construction) and drop the unnecessary parallelism:

```java
public class OrderValidationReport {
    private static final Logger log = LoggerFactory.getLogger(OrderValidationReport.class);

    public List<String> findInvalidOrderIds(List<Order> orders) {
        List<String> invalidIds = orders.stream()
                .filter(order -> !isValid(order))
                .map(Order::id)
                .toList();

        log.info("Found {} invalid orders", invalidIds.size());
        return invalidIds;
    }

    private boolean isValid(Order order) {
        return order.total().compareTo(BigDecimal.ZERO) >= 0 && order.customerId() != null;
    }
}
```

If this really needs to run in parallel because `isValid` does expensive, CPU-bound, side-effect-free work over a very large list, `parallelStream()` is fine — but then `.filter(...).map(...).toList()` (a proper collector) is still the right way to gather results, never a shared mutable list touched from inside `forEach`.

</details>

### Scenario 13: The Batch Email Sender

```java
public class BatchEmailSender {

    public void sendBatch(List<Email> emails) {
        ExecutorService executor = Executors.newFixedThreadPool(10);
        for (Email email : emails) {
            executor.submit(() -> smtpClient.send(email));
        }
    }

    private SmtpClient smtpClient;
}
```

<details><summary>Show the review</summary>

- **Blocker — the executor is never shut down.** A brand-new `ExecutorService` (with 10 live platform threads) is created on every call to `sendBatch` and simply abandoned once the method returns. `ExecutorService`s hold real threads that are not garbage-collected just because the reference goes out of scope — each call leaks 10 threads that live forever (or until the JVM exits), and a service that calls `sendBatch` repeatedly will exhaust available threads/memory and eventually fail with `OutOfMemoryError: unable to create new native thread`.
- **Major — no way to know when the batch actually finished**, and no error handling for individual sends: `smtpClient.send(email)` inside the submitted task can throw, and since nothing calls `Future.get()` on the submitted tasks, that exception is silently swallowed by the executor (visible only if you dig through logs for an "uncaught exception in thread pool" trace, if that's even configured).
- **Minor — a fresh fixed pool per batch is also wasteful even if shut down correctly**; a single shared, appropriately-sized executor reused across calls (owned by the surrounding component's lifecycle, not created ad hoc per method call) is both cheaper and easier to reason about.

Fixed code — own the executor as a long-lived field with a proper lifecycle (`shutdown`), and track task outcomes:

```java
public class BatchEmailSender implements AutoCloseable {
    private static final Logger log = LoggerFactory.getLogger(BatchEmailSender.class);
    private final ExecutorService executor = Executors.newFixedThreadPool(10);
    private final SmtpClient smtpClient;

    public BatchEmailSender(SmtpClient smtpClient) {
        this.smtpClient = smtpClient;
    }

    public void sendBatch(List<Email> emails) {
        List<Future<?>> futures = emails.stream()
                .map(email -> executor.submit(() -> smtpClient.send(email)))
                .toList();

        for (Future<?> future : futures) {
            try {
                future.get();
            } catch (ExecutionException e) {
                log.warn("Failed to send an email in batch", e.getCause());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("Interrupted while sending batch", e);
            }
        }
    }

    @Override
    public void close() {
        executor.shutdown(); // let in-flight sends finish; see 14-concurrency-advanced.md for shutdown patterns
    }
}
```

</details>

### Scenario 14: The Import Job Runner

```java
public class ImportJobRunner {

    public ImportResult runImport(String filePath) {
        try {
            List<String> lines = Files.readAllLines(Path.of(filePath));
            List<Record> records = new ArrayList<>();
            for (String line : lines) {
                Record record = parseLine(line);
                validate(record);
                records.add(record);
            }
            repository.saveAll(records);
            notifyStakeholders(records.size());
            return ImportResult.success(records.size());
        } catch (Exception e) {
            return ImportResult.failure("Import failed");
        }
    }

    private Record parseLine(String line) { /* throws NumberFormatException on bad rows */ return null; }
    private void validate(Record record) { /* throws ValidationException */ }
    private void notifyStakeholders(int count) { /* sends an email, can throw MessagingException */ }
}
```

<details><summary>Show the review</summary>

- **Major — one giant `try` wraps five completely different failure domains**: file I/O (`IOException`), per-line parsing (`NumberFormatException`), business validation (`ValidationException`), the DB save (`repository.saveAll` — any runtime exception it throws), and notification (`MessagingException`). All of them collapse into the same generic `"Import failed"` message, so a caller (or a human on call) cannot tell "the file didn't exist" apart from "row 4,502 had a wrong data type" apart from "the DB write itself failed after everything else succeeded" apart from "the import fully succeeded but the confirmation email failed to send." Those require completely different responses.
- **Major — a failure in `notifyStakeholders` (just sending an email) reports the entire import as failed**, even though `repository.saveAll(records)` already committed. The caller will likely re-run the import, potentially double-importing records, because of a problem in an unrelated, non-critical side effect.
- **Minor — no logging at all inside the `catch`**, generic or otherwise — whatever the real exception was is discarded completely, not even preserved for later diagnosis.

Fixed code — scope each `try` to the failure domain that's actually being handled, and don't let a non-critical side effect (notification) mark an otherwise-successful core operation as failed:

```java
public class ImportJobRunner {
    private static final Logger log = LoggerFactory.getLogger(ImportJobRunner.class);

    public ImportResult runImport(String filePath) {
        List<String> lines;
        try {
            lines = Files.readAllLines(Path.of(filePath));
        } catch (IOException e) {
            log.error("Could not read import file {}", filePath, e);
            return ImportResult.failure("Import file could not be read: " + e.getMessage());
        }

        List<Record> records = new ArrayList<>();
        for (int i = 0; i < lines.size(); i++) {
            try {
                Record record = parseLine(lines.get(i));
                validate(record);
                records.add(record);
            } catch (NumberFormatException | ValidationException e) {
                log.error("Bad row {} in {}: {}", i + 1, filePath, e.getMessage());
                return ImportResult.failure("Row " + (i + 1) + " is invalid: " + e.getMessage());
            }
        }

        repository.saveAll(records); // let a DB failure propagate as-is — it's a real defect, not an expected outcome

        try {
            notifyStakeholders(records.size());
        } catch (MessagingException e) {
            // Import already succeeded; a failed notification shouldn't undo that fact.
            log.warn("Import of {} records succeeded but notification failed", records.size(), e);
        }

        return ImportResult.success(records.size());
    }

    private Record parseLine(String line) { return null; }
    private void validate(Record record) { }
    private void notifyStakeholders(int count) throws MessagingException { }
}
```

</details>

### Scenario 15: The Live Price Ticker

```java
public class LivePriceTicker {

    private final List<PriceListener> listeners = new ArrayList<>();
    private volatile double lastPrice;

    public LivePriceTicker(PriceFeed feed) {
        feed.subscribe(this::onPriceUpdate); // registers 'this' before construction finishes
        this.startBackgroundRefresh();
    }

    private void startBackgroundRefresh() {
        Thread refresher = new Thread(() -> {
            while (true) {
                refreshFromFeed();
                sleepQuietly();
            }
        });
        refresher.start(); // thread started from inside the constructor
    }

    public void addListener(PriceListener listener) {
        listeners.add(listener);
    }

    private void onPriceUpdate(double price) {
        this.lastPrice = price;
        for (PriceListener listener : listeners) {
            listener.onPrice(price);
        }
    }

    private void refreshFromFeed() { /* ... */ }
    private void sleepQuietly() { try { Thread.sleep(1000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } }
}
```

<details><summary>Show the review</summary>

- **Blocker — `this` escapes the constructor twice, before construction has finished.** `feed.subscribe(this::onPriceUpdate)` hands out a reference to the object (via the method reference, which captures `this`) to another object (`feed`) *while the constructor is still running*. If `feed` calls back synchronously, or another thread reaches `this` through `feed` before the constructor returns, it can observe a half-initialized `LivePriceTicker` — for example, `listeners` might still be `null` if this call happened before the field initializer ran, or fields set later in the constructor simply won't be visible yet. This is the "leaking `this`" pitfall, and it's especially dangerous combined with...
- **Blocker — starting a background thread from inside the constructor.** `refresher.start()` hands the *same partially-constructed* object to a new thread of execution, with no memory barrier guaranteeing the new thread sees a fully-initialized object — the Java Memory Model does not guarantee that a background thread started during construction sees writes made later in that same constructor, unless those fields are `final` or otherwise safely published. This is a subtle, hard-to-reproduce race that depends on JIT/CPU reordering and timing.
- **Major — the refresher thread runs `while (true)` with no way to stop it**, and it's never tracked anywhere (no field holding the `Thread`). There is no way to shut this down cleanly — same lifecycle problem as Scenario 13's abandoned executor, but worse, because there isn't even a reference to call `interrupt()` on later.
- **Minor — `listeners` (an `ArrayList`) is read and written without synchronization**, and `onPriceUpdate` (which iterates it) can be invoked concurrently with `addListener` (which mutates it) from different threads (the feed's callback thread vs. whatever thread calls `addListener`) — another `ConcurrentModificationException`/corruption risk layered on top of the construction issue.

Fixed code — finish construction fully before publishing `this` anywhere, use a static factory to start the background work only after the object is safely built, keep a handle to the thread for shutdown, and use a concurrent-safe listener list:

```java
public class LivePriceTicker {

    private final List<PriceListener> listeners = new CopyOnWriteArrayList<>();
    private final Thread refresher;
    private volatile double lastPrice;
    private volatile boolean running = true;

    private LivePriceTicker() {
        this.refresher = new Thread(this::refreshLoop);
    }

    // Factory method: object is fully constructed before anything external can see it.
    public static LivePriceTicker start(PriceFeed feed) {
        LivePriceTicker ticker = new LivePriceTicker();
        feed.subscribe(ticker::onPriceUpdate); // 'this' only escapes after the constructor returned
        ticker.refresher.start();
        return ticker;
    }

    private void refreshLoop() {
        while (running) {
            refreshFromFeed();
            sleepQuietly();
        }
    }

    public void shutdown() {
        running = false;
        refresher.interrupt();
    }

    public void addListener(PriceListener listener) {
        listeners.add(listener);
    }

    private void onPriceUpdate(double price) {
        this.lastPrice = price;
        for (PriceListener listener : listeners) {
            listener.onPrice(price);
        }
    }

    private void refreshFromFeed() { /* ... */ }
    private void sleepQuietly() { try { Thread.sleep(1000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } }
}
```

See `13-concurrency-core.md` for the Java Memory Model rules behind "safe publication," and `02-object-oriented-programming.md` for constructor discipline in general.

</details>

## Performance Optimization

A checklist first, then the wins that show up over and over in review.

### Checklist

- [ ] Is there actual evidence (a profile, a benchmark, a production metric) that this code is a bottleneck, or is this optimization speculative?
- [ ] Is the algorithmic complexity (Big-O) obviously worse than it needs to be for the data sizes involved?
- [ ] Is the right data structure being used for the access pattern (see [Collections Selection](#collections-selection))?
- [ ] Is boxing/unboxing happening in a numeric hot path that could use primitives?
- [ ] Is a `Pattern` being compiled repeatedly (e.g., inside a loop or via `String.matches()`) instead of once?
- [ ] Is I/O unbuffered, or done byte-by-byte/line-by-line in a way that causes excessive system calls?
- [ ] Are database calls batched, or issued one-by-one in a loop (N+1)?
- [ ] Is expensive work done eagerly at startup/construction when it could be lazy, or vice versa — lazily recomputed on every call when it could be cached once?
- [ ] Is parallelism (`parallelStream`, manual thread pools) applied to trivial or I/O-bound work where the overhead outweighs the benefit?

### Measure First

The single biggest mistake in performance review is optimizing before measuring. "This *looks* slow" is not evidence; a profiler, a JFR recording (`12-memory-management.md`), or a microbenchmark is. For method-level questions ("is `StringBuilder` actually faster here, and by how much"), the standard tool is **JMH** (Java Microbenchmark Harness) — it handles JIT warm-up, dead-code elimination by the optimizer, and statistical noise that a naive `System.nanoTime()` loop gets wrong. In an interview, you won't run JMH, but saying "I'd confirm this with a JMH benchmark rather than guessing" is exactly the right instinct to voice.

### Big-O Awareness

Recognize complexity by shape, without doing formal analysis:

- A loop calling `.contains()` or `.get()` on a `List` inside another loop → O(n²) (should be a `Set`/`Map` lookup, O(n)).
- A loop making a DB/network call per iteration → O(n) round trips that should be O(1) (Scenario 6).
- Nested loops over independent dimensions that don't need to be recomputed per outer iteration → hoist the inner computation out or precompute it once (Scenario 6's discount rules).
- String-building with `+` across iterations → O(n²) character copying (Scenario 7).

```java
// Flagged: O(n * m) — .contains() on a List is a linear scan, done once per outer element
List<String> blockedIds = loadBlockedIds(); // a List
for (Order order : orders) {
    if (blockedIds.contains(order.customerId())) { ... }
}
```

```java
// Fixed: O(n + m) — one linear pass to build the Set, then O(1) lookups
Set<String> blockedIds = new HashSet<>(loadBlockedIds());
for (Order order : orders) {
    if (blockedIds.contains(order.customerId())) { ... }
}
```

### Common Real Wins

```java
// Flagged: Pattern compiled on every call — String.matches() compiles internally, every time
public boolean isValidSku(String sku) {
    return sku.matches("[A-Z]{3}-\\d{4}");
}
```

```java
// Fixed: compile once, reuse the compiled Pattern
private static final Pattern SKU_PATTERN = Pattern.compile("[A-Z]{3}-\\d{4}");

public boolean isValidSku(String sku) {
    return SKU_PATTERN.matcher(sku).matches();
}
```

```java
// Flagged: boxed Integer accumulator in a tight numeric loop — boxes/unboxes a million times
Integer total = 0;
for (int i = 0; i < prices.length; i++) {
    total += prices[i]; // unbox total, add, re-box into a new Integer, every iteration
}
```

```java
// Fixed: primitive accumulator, no boxing at all
int total = 0;
for (int i = 0; i < prices.length; i++) {
    total += prices[i];
}
```

```java
// Flagged: unbuffered, one-byte-at-a-time reads — one native call per byte
try (FileInputStream in = new FileInputStream(path)) {
    int b;
    while ((b = in.read()) != -1) { process(b); }
}
```

```java
// Fixed: wrap with a BufferedInputStream (or use readAllBytes/NIO for whole-file reads)
try (BufferedInputStream in = new BufferedInputStream(new FileInputStream(path))) {
    int b;
    while ((b = in.read()) != -1) { process(b); }
}
```

### Premature Optimization Is Also a Finding

Flag the opposite mistake too: adding a cache, a custom data structure, or manual parallelism for code that isn't a bottleneck adds real cost — more moving parts, more places to get thread-safety wrong (see Scenario 9, Scenario 12), harder-to-read code — for a performance gain nobody asked for or measured. "I'd leave this as a simple loop unless we have evidence it's hot" is a legitimate, senior-sounding review comment, not a cop-out.

## Thread Safety Review

### Checklist

- [ ] What state does this object hold, and is any of it mutable?
- [ ] Can more than one thread reach this object at the same time (is it a singleton, a field on a shared service, handed to an executor, stored in a servlet-scoped or application-scoped context)?
- [ ] If shared and mutable: is every access to that state properly synchronized, `volatile`, or backed by a concurrent-safe type?
- [ ] Does the object fully finish constructing before any reference to it (or to `this` via a lambda/method reference) is handed to another thread?
- [ ] Is the class's thread-safety contract documented, so callers don't have to read the implementation to find out?

### Reasoning About Shared Mutable State

The question to ask about *every* field is: **who else can see this, and can they see it while I'm changing it?** Three outcomes:

1. **Not shared** — a local variable, or an object fully confined to one thread. No synchronization needed. This is the best outcome; prefer designs that keep state confined.
2. **Shared but immutable** — the value never changes after construction (`final` fields, no setters, defensively-copied collections). Safe to share freely with no locking, because there's nothing to race on. This is the second-best outcome — see `05-object-class-and-common-apis.md` on immutability.
3. **Shared and mutable** — now you need an explicit safety mechanism: a lock (`synchronized`, `ReentrantLock`), `volatile` for single-variable visibility, an atomic class, or a concurrent collection. This is the case that needs the most scrutiny in review, and where most real bugs (Scenarios 1, 2, 9, 15) live.

**Prefer immutability first.** Before reaching for a lock, ask whether the mutable field even needs to be mutable — could it instead be replaced wholesale (a `volatile` reference to a new immutable object) rather than mutated in place? This sidesteps entire classes of bugs instead of managing them.

**Confinement** is the other escape hatch: if an object is only ever touched by one thread (e.g., a per-request object never handed off, a `ThreadLocal`), it doesn't need synchronization regardless of whether the class itself is thread-safe in general — but this must be true structurally, not "true in practice today," because it silently breaks the moment someone hands the object to an executor.

### The Escape/Publication Question

Ask, for every object: **how does a reference to this object first reach another thread, and has construction fully finished by then?** This is exactly what broke in Scenario 15 — `this` was captured by a lambda and handed to `feed.subscribe(...)` before the constructor returned. Safe publication options, cheapest first:

- Return the fully-constructed object from a `static` factory method instead of doing any handoff inside the constructor itself.
- Store the reference in a `volatile` or `final` field, or into a properly locked structure, before any other thread can see it.
- Use a concurrent collection (`ConcurrentHashMap`, etc.) to publish it — insertion into those collections has the necessary memory-visibility guarantees built in.

### Documenting Thread Safety

A class's thread-safety contract is part of its API, not an implementation detail — callers need to know it without reading the source. State it explicitly, ideally in the class Javadoc: "this class is thread-safe; all methods may be called concurrently" or "this class is not thread-safe; instances must be confined to a single thread" or "safe for concurrent reads, writes must be externally synchronized." See `13-concurrency-core.md` for the Java Memory Model vocabulary (visibility, atomicity, ordering, happens-before) to use precisely when writing or reviewing such statements.

## Collections Selection

### Decision Table

| Need | Prefer | Avoid | Why |
|---|---|---|---|
| Index-based random access, mostly reads | `ArrayList` | `LinkedList` | O(1) indexed get vs. O(n) traversal |
| Frequent insert/remove at both ends | `ArrayDeque` | `LinkedList`, `Stack` | Better cache locality, no legacy `synchronized` overhead |
| Fast key lookup, order doesn't matter | `HashMap` / `HashSet` | `TreeMap`/`TreeSet` (unless sorted order needed) | O(1) average vs. O(log n) |
| Insertion order must be preserved | `LinkedHashMap` / `LinkedHashSet` | `HashMap` (order unspecified) | Predictable iteration order |
| Sorted key order needed | `TreeMap` / `TreeSet` | Manually sorting a `HashMap`'s keys repeatedly | Maintains order incrementally, O(log n) per op |
| Multiple threads read/write a map concurrently | `ConcurrentHashMap` | `HashMap`, or `Collections.synchronizedMap` for hot paths | Lock-striped/segment-free reads, far less contention |
| Mostly-read, occasionally-written list shared across threads | `CopyOnWriteArrayList` | `synchronized(List)` with manual locking, or plain `ArrayList` | Read without locking at all; write cost is acceptable when reads dominate |
| Producer/consumer handoff between threads | `BlockingQueue` (`LinkedBlockingQueue`, `ArrayBlockingQueue`) | A `List` polled in a loop ("busy-wait") | Built-in blocking wait, no CPU-spinning |
| A fixed, never-changing collection | `List.of(...)` / `Map.of(...)` | `Collections.unmodifiableList(new ArrayList<>(...))` for something that's genuinely constant | Simpler, truly immutable, slightly more memory-efficient |
| Enum-keyed map/set | `EnumMap` / `EnumSet` | `HashMap<SomeEnum, V>` | Backed by an array indexed by ordinal — faster and more memory-compact |
| Cache that shouldn't pin entries in memory once nothing else references the key | `WeakHashMap` (or better, a real cache library) | `HashMap` | Entries can be GC'd once the key is otherwise unreachable |

See `07-collections-framework.md` for the full breakdown of each type's internals.

### "They Used X, Should Be Y" Cases

```java
// Flagged: LinkedList for pure random access by index
LinkedList<Transaction> transactions = loadTransactions();
Transaction tenth = transactions.get(9); // O(n) traversal from the head every time
```
Should be: `ArrayList<Transaction>` — O(1) indexed access, and `LinkedList` rarely wins in practice even for its supposed strengths, because of pointer-chasing and per-node allocation overhead.

```java
// Flagged: HashMap iterated in a context that assumes stable, predictable order (e.g., building a UI list or a diff)
Map<String, BigDecimal> balancesByAccount = new HashMap<>();
// ... populate ...
for (var entry : balancesByAccount.entrySet()) { render(entry); } // order can change silently between runs/resizes
```
Should be: `LinkedHashMap` if insertion order matters for the output, or `TreeMap` if sorted-by-key order matters — never rely on `HashMap`'s iteration order, which is an implementation detail, not a contract.

```java
// Flagged: Vector or Collections.synchronizedList for a hot, contended, mostly-read list
List<Listener> listeners = new Vector<>(); // synchronizes every single method call, even reads
```
Should be: `CopyOnWriteArrayList` if reads vastly outnumber writes (typical for listener lists) — no locking at all on reads.

```java
// Flagged: a List used to check membership repeatedly
List<String> allowedRoles = List.of("ADMIN", "OWNER", "BILLING");
if (allowedRoles.contains(user.role())) { ... } // fine for 3 elements, a trap if this list grows or is called in a hot loop
```
Should be, once the list could grow or the check is on a hot path: `Set.of("ADMIN", "OWNER", "BILLING")` — O(1) lookup instead of O(n) linear scan, and it also documents "this is a membership set," not an ordered sequence.

## Exception Design

### Checklist

- [ ] Is the exception type at this API boundary checked (caller must handle/declare) or unchecked (caller may ignore)? Is that the right choice for whether callers can realistically recover?
- [ ] Does a custom exception type add real domain meaning, or is it one more type to remember with no behavioral difference from an existing one?
- [ ] Is any exception ever caught and silently discarded (`catch (X e) {}` or logged with no rethrow and no handling)?
- [ ] Is any exception logged **and** rethrown at the same layer (double reporting up the call stack)?
- [ ] When wrapping a lower-level exception, is the original passed as the `cause` (`new Foo(msg, e)`), never dropped?
- [ ] Does validation happen at the boundary (fail fast, clear message) rather than deep inside, producing a confusing downstream `NullPointerException` instead?

Full mechanics — `finally`/`return` interactions, suppressed exceptions, multi-catch, `NullPointerException` messages — are covered in `06-exception-handling.md`; this section is the *design* lens specifically for review.

### Checked vs. Unchecked at API Boundaries

| Situation | Choice |
|---|---|
| Caller can genuinely do something different on failure (retry, fallback, prompt again) | Checked, or a documented unchecked type callers are expected to catch |
| Failure represents a bug in the calling code (bad argument, wrong state) | Unchecked (`IllegalArgumentException`, `IllegalStateException`) |
| Code composed with streams/lambdas, where checked exceptions don't fit the functional interfaces | Unchecked, or wrap with `UncheckedIOException`-style adapters |
| A public library boundary where you want to force callers to consciously acknowledge a failure mode | Checked |

### Never Swallow, Never Log-and-Rethrow

```java
// Flagged: swallowed — the failure vanishes with no trace
try {
    inventoryService.reserve(sku, qty);
} catch (Exception e) {
    // ignored
}
```

```java
// Flagged: logged AND rethrown — the same failure gets logged again by every caller up the stack
try {
    inventoryService.reserve(sku, qty);
} catch (InventoryException e) {
    log.error("Reservation failed", e);
    throw e; // caller will likely log this again
}
```

```java
// Fixed: handle it here, or propagate it — pick one. If propagating, don't also log here;
// log once, at the boundary that actually decides what to do about it.
try {
    inventoryService.reserve(sku, qty);
} catch (InventoryException e) {
    throw new OrderFulfillmentException("Could not reserve " + sku, e); // wrapped, cause preserved, not logged yet
}
```

### Wrap With Cause, Fail Fast

```java
// Flagged: cause dropped, and validation happens deep inside where the error is confusing
public void applyDiscount(Order order, String code) {
    DiscountRule rule = ruleRepository.findByCode(code); // returns null if not found
    order.applyDiscount(rule.percentage()); // NPE here if code was invalid — confusing stack trace
}
```

```java
// Fixed: fail fast with a clear message, and preserve any real cause when wrapping
public void applyDiscount(Order order, String code) {
    Objects.requireNonNull(order, "order must not be null");
    DiscountRule rule = ruleRepository.findByCode(code)
            .orElseThrow(() -> new InvalidDiscountCodeException("No discount rule for code: " + code));
    order.applyDiscount(rule.percentage());
}
```

### Custom Exception Hierarchies

Keep them shallow and purposeful — one or two levels, each type adding real meaning a caller might act on differently:

```java
public class OrderException extends RuntimeException {
    public OrderException(String message, Throwable cause) { super(message, cause); }
    public OrderException(String message) { super(message); }
}

public class InsufficientInventoryException extends OrderException {
    public InsufficientInventoryException(String sku) {
        super("Insufficient inventory for SKU: " + sku);
    }
}

public class InvalidDiscountCodeException extends OrderException {
    public InvalidDiscountCodeException(String message) { super(message); }
}
```

A reviewer should flag a hierarchy that's either too flat (everything is a bare `RuntimeException`, so callers can't distinguish failure modes without string-matching messages — pitfall #14 from the [Common Java Interview Pitfalls](#common-java-interview-pitfalls) list) or too deep (five levels of subclassing with no behavioral difference, adding ceremony with no payoff).

## API Design Review

### Checklist

- [ ] Can any parameter or return value be `null`? If so, is that documented, defended against with `Objects.requireNonNull`, or better, redesigned to avoid `null` altogether?
- [ ] Is `Optional` used only as a return type, never as a field, a parameter, or inside a collection?
- [ ] Are parameters validated at the top of the method (fail fast) with a clear message, rather than failing confusingly several calls deep?
- [ ] Is the type unnecessarily mutable — could fields be `final` and setters removed in favor of a constructor or a "with"-style copy method?
- [ ] Do method names accurately signal cost and side effects (`getX` should be cheap and side-effect-free; `computeX`/`fetchX` signals real work)?
- [ ] Do overloads behave consistently with each other, and is it clear which one a given call site will resolve to?
- [ ] Would a currently-reasonable call site break if this method's signature, exceptions, or semantics changed — i.e., is this API is easy to evolve later?

See `19-software-engineering-best-practices.md` for the broader API-design and clean-code principles this section specializes for review purposes.

### Nulls and Optional

```java
// Flagged: null used as "not found," undocumented, and Optional used as a field type
public class UserLookupResult {
    private Optional<User> user; // don't do this — Optional as a field
    public User find(String id) {
        return repository.findById(id); // returns null if missing, no Javadoc says so
    }
}
```

```java
// Fixed: Optional only as a return type; the field itself is a plain, possibly-null-free reference
public class UserLookupResult {
    /**
     * @return the user, or empty if no user exists with the given id.
     */
    public Optional<User> find(String id) {
        return repository.findById(id); // repository itself now returns Optional<User>
    }
}
```

### Parameter Validation and Naming

```java
// Flagged: no validation; a bad call fails deep inside, far from the actual mistake
public void scheduleShipment(String orderId, int daysFromNow) {
    LocalDate shipDate = LocalDate.now().plusDays(daysFromNow);
    warehouse.schedule(orderId, shipDate); // throws something cryptic if orderId is blank
}
```

```java
// Fixed: fail fast, at the boundary, with a message that names the actual problem
public void scheduleShipment(String orderId, int daysFromNow) {
    if (orderId == null || orderId.isBlank()) {
        throw new IllegalArgumentException("orderId must not be blank");
    }
    if (daysFromNow < 0) {
        throw new IllegalArgumentException("daysFromNow must not be negative: " + daysFromNow);
    }
    warehouse.schedule(orderId, LocalDate.now().plusDays(daysFromNow));
}
```

### Mutability and Backward Compatibility

```java
// Flagged: needlessly mutable value type, and a public setter that could break invariants
public class Money {
    private BigDecimal amount;
    private String currency;
    public void setAmount(BigDecimal amount) { this.amount = amount; } // no validation, no currency re-check
}
```

```java
// Fixed: immutable, validated at construction, changes produce a new instance
public final class Money {
    private final BigDecimal amount;
    private final String currency;

    public Money(BigDecimal amount, String currency) {
        if (amount.scale() > 2) throw new IllegalArgumentException("amount must have at most 2 decimal places");
        this.amount = amount;
        this.currency = Objects.requireNonNull(currency);
    }

    public Money withAmount(BigDecimal newAmount) {
        return new Money(newAmount, currency);
    }
}
```

For backward compatibility: widening what a method accepts (e.g., `List<? extends T>` instead of `List<T>`) or narrowing what it promises to throw is generally safe; removing a public method, changing a return type, or *narrowing* accepted input is a breaking change and should be flagged as a compatibility risk even if it "looks like a small tidy-up."

## Memory & GC Review

### Checklist

- [ ] Any `static` collection/field that only ever grows (cache, registry, listener list) with no eviction or removal path?
- [ ] Any allocation happening inside a hot loop that could be hoisted outside it (a `new` for an object that could be reused, a boxed wrapper, a formatter)?
- [ ] Any long-lived object holding a reference to something that should have a short lifetime (e.g., an outer class implicitly held by a non-static inner class registered as a long-lived listener)?
- [ ] Any `ThreadLocal` used inside a thread-pool-backed executor, without being explicitly cleared when the task finishes?
- [ ] Would a heap dump plausibly show one dominant retained-size culprit here, or is this a death-by-a-thousand-small-allocations concern instead?

Deep GC algorithm mechanics (G1, ZGC, Shenandoah, tuning flags) live in `12-memory-management.md`; this section is about spotting the *code patterns* that create memory pressure or leaks in the first place, regardless of which collector is running.

### Leak-Prone Patterns

```java
// Flagged: static, ever-growing collection — a permanent, unbounded leak
public class SessionRegistry {
    private static final Map<String, Session> ACTIVE_SESSIONS = new HashMap<>();
    public static void register(Session s) { ACTIVE_SESSIONS.put(s.id(), s); } // never removed
}
```

```java
// Fixed: an explicit removal path tied to the session's actual lifecycle, or a bounded/expiring cache
public class SessionRegistry {
    private static final Map<String, Session> ACTIVE_SESSIONS = new ConcurrentHashMap<>();
    public static void register(Session s) { ACTIVE_SESSIONS.put(s.id(), s); }
    public static void unregister(String sessionId) { ACTIVE_SESSIONS.remove(sessionId); } // called on logout/expiry
}
```

### ThreadLocal in a Pool

```java
// Flagged: ThreadLocal set but never removed, on a thread that will be reused by the pool
public class RequestContext {
    private static final ThreadLocal<User> CURRENT_USER = new ThreadLocal<>();
    public void handle(Request req) {
        CURRENT_USER.set(req.user());
        process(req);
        // no CURRENT_USER.remove() — the User object (and everything it references) stays
        // reachable from this pool thread until some *unrelated* future task overwrites it
    }
}
```

```java
// Fixed: always remove in a finally, so the pool thread doesn't retain the value between tasks
public class RequestContext {
    private static final ThreadLocal<User> CURRENT_USER = new ThreadLocal<>();
    public void handle(Request req) {
        CURRENT_USER.set(req.user());
        try {
            process(req);
        } finally {
            CURRENT_USER.remove();
        }
    }
}
```

This matters specifically *because* pool threads are long-lived and reused — a `ThreadLocal` left set silently extends an object's lifetime far beyond the request that created it, and can even leak one user's data into logic that (mistakenly) reads the `ThreadLocal` during a later, different request on the same pooled thread.

### Heap Dump Basics (What a Reviewer Should Know to Ask For)

When a leak is suspected but not obvious from reading the code, the review-worthy question is "have we taken a heap dump (`jcmd <pid> GC.heap_dump`, or `-XX:+HeapDumpOnOutOfMemoryError`) and looked at retained size by class/dominator tree?" A handful of instances of one class holding a disproportionate amount of retained memory is the signature of exactly the patterns above — an unbounded map, a listener list nobody ever removes from, or an accidental reference chain keeping something alive. See `12-memory-management.md` for the tools (`jmap`, `jcmd`, JFR, JMC) in depth.

## Concurrency Review

### Checklist

- [ ] Is every `ExecutorService` shut down somewhere on every relevant path (normal completion *and* error/shutdown paths)?
- [ ] Any double-checked locking pattern — is the field `volatile`? Without it, the second thread can see a partially-constructed object (see `13-concurrency-core.md`'s Java Memory Model section).
- [ ] Is `volatile` used where an actual atomic compound operation (`i++`, check-then-act) was needed instead? `volatile` only guarantees visibility, not atomicity of anything beyond a single read or write.
- [ ] If multiple locks are acquired, is the acquisition order consistent everywhere, avoiding a lock-ordering deadlock?
- [ ] Does any `CompletableFuture` chain swallow exceptions (no `.exceptionally`/`.handle`), or block on `.get()`/`.join()` on a thread that shouldn't block (e.g., an event-loop thread)?
- [ ] If virtual threads are used, does any code hold a lock (`synchronized`) or otherwise pin the platform thread across a blocking call, defeating the point of virtual threads?

### Double-Checked Locking Without `volatile`

```java
// Flagged: classic double-checked locking bug — missing volatile
public class ExpensiveResource {
    private static ExpensiveResource instance;
    public static ExpensiveResource getInstance() {
        if (instance == null) {
            synchronized (ExpensiveResource.class) {
                if (instance == null) {
                    instance = new ExpensiveResource(); // can be observed half-built without volatile
                }
            }
        }
        return instance;
    }
}
```

```java
// Fixed: volatile field restores the needed happens-before edge, OR use the holder idiom (Scenario 1) instead
public class ExpensiveResource {
    private static volatile ExpensiveResource instance;
    public static ExpensiveResource getInstance() {
        if (instance == null) {
            synchronized (ExpensiveResource.class) {
                if (instance == null) {
                    instance = new ExpensiveResource();
                }
            }
        }
        return instance;
    }
}
```

Without `volatile`, another thread can see `instance` as non-`null` while still observing the *partially initialized* object it points to, because the write to `instance` and the writes inside the constructor can be reordered from that thread's point of view. In review, the holder idiom (Scenario 1) is almost always the simpler recommendation over fixing double-checked locking by hand.

### Deadlock Ordering

```java
// Flagged: two methods acquire the same two locks in opposite order — classic deadlock
public class AccountTransfer {
    public void transfer(Account from, Account to, BigDecimal amount) {
        synchronized (from) {
            synchronized (to) {
                from.debit(amount);
                to.credit(amount);
            }
        }
    }
    // if some other code path calls transfer(to, from, ...) concurrently, thread A can hold
    // from's lock waiting for to's, while thread B holds to's lock waiting for from's — deadlock
}
```

```java
// Fixed: always acquire locks in a consistent, global order (e.g., by account id)
public class AccountTransfer {
    public void transfer(Account from, Account to, BigDecimal amount) {
        Account first = from.id().compareTo(to.id()) < 0 ? from : to;
        Account second = first == from ? to : from;
        synchronized (first) {
            synchronized (second) {
                from.debit(amount);
                to.credit(amount);
            }
        }
    }
}
```

### `CompletableFuture` Pitfalls

```java
// Flagged: exception in the async stage is silently lost; nothing observes the completed future
CompletableFuture.supplyAsync(() -> paymentGateway.charge(request))
        .thenAccept(result -> ledger.record(result));
// if charge() or record() throws, this failure disappears — nobody calls get()/join(),
// and there's no .exceptionally/.handle to observe it
```

```java
// Fixed: handle the failure explicitly
CompletableFuture.supplyAsync(() -> paymentGateway.charge(request))
        .thenAccept(result -> ledger.record(result))
        .exceptionally(ex -> {
            log.error("Async charge/record pipeline failed", ex);
            alerting.pageOnCall("Payment pipeline failure: " + ex.getMessage());
            return null;
        });
```

### Virtual Thread Pinning

```java
// Flagged: synchronized block around a blocking call, run on a virtual thread — pins the carrier platform thread
public synchronized String fetchAndCache(String key) {
    String cached = cache.get(key);
    if (cached != null) return cached;
    String value = blockingHttpCall(key); // blocks while holding the monitor
    cache.put(key, value);
    return value;
}
```

A `synchronized` block that's holding its monitor while making a blocking call prevents the virtual thread from unmounting from its carrier platform thread during that block — under load, this can exhaust the small pool of carrier threads and stall unrelated virtual threads, defeating the scalability virtual threads exist to provide.

```java
// Fixed: use a java.util.concurrent.locks.ReentrantLock, which does allow unmounting, or restructure
// to avoid holding any lock across the blocking call in the first place
private final ReentrantLock lock = new ReentrantLock();

public String fetchAndCache(String key) {
    String cached = cache.get(key);
    if (cached != null) return cached;
    String value = blockingHttpCall(key); // no lock held here at all
    lock.lock();
    try {
        cache.putIfAbsent(key, value);
    } finally {
        lock.unlock();
    }
    return cache.get(key);
}
```

See `15-modern-concurrency.md` for virtual threads and structured concurrency in depth, and `14-concurrency-advanced.md` for the executor/`CompletableFuture` framework this section leans on.

## Modern Java Feature Usage

Modern syntax is not automatically good style — review both directions: missed opportunities to simplify, *and* overuse that hurts readability. Background on each feature is in `03-modern-java-language-features.md`; this section is specifically about judging usage.

### Records — Good Fit

```java
// Flagged: a hand-rolled immutable value class with all the equals/hashCode/toString boilerplate
public final class Point {
    private final int x, y;
    public Point(int x, int y) { this.x = x; this.y = y; }
    public int getX() { return x; }
    public int getY() { return y; }
    @Override public boolean equals(Object o) { /* ... */ return false; }
    @Override public int hashCode() { /* ... */ return 0; }
    @Override public String toString() { return "Point(" + x + ", " + y + ")"; }
}
```

```java
// Fixed: a record generates the constructor, accessors, equals/hashCode/toString for free
public record Point(int x, int y) {}
```

### Records — Bad Fit

```java
// Flagged: a record with a mutable component defeats the entire point of using a record
public record OrderBatch(List<Order> orders) {} // caller can still mutate the passed-in list from outside
```

```java
// Fixed: defensively copy in the compact constructor, and expose an unmodifiable view
public record OrderBatch(List<Order> orders) {
    public OrderBatch(List<Order> orders) {
        this.orders = List.copyOf(orders); // immutable copy, breaks the aliasing hazard
    }
}
```

A record whose only mutable-looking component is an array or a mutable collection is a very common planted bug — the record's own fields are `final`, but that says nothing about whether the *referenced* object can still be mutated by the caller.

### Pattern Matching vs. Polymorphism

```java
// Flagged: pattern-matching switch reimplementing what virtual dispatch already does better
public double area(Shape shape) {
    return switch (shape) {
        case Circle c -> Math.PI * c.radius() * c.radius();
        case Square s -> s.side() * s.side();
        case Rectangle r -> r.width() * r.height();
        default -> throw new IllegalStateException();
    };
}
```

```java
// Fixed: if this switch would need a new case every time a new Shape type is added,
// and Shape is a type you own, plain polymorphism is simpler and can't forget a case
sealed interface Shape { double area(); }
record Circle(double radius) implements Shape { public double area() { return Math.PI * radius * radius; } }
record Square(double side) implements Shape { public double area() { return side * side; } }
record Rectangle(double width, double height) implements Shape { public double area() { return width * height; } }
```

Pattern matching earns its keep when the operation is genuinely external to the type (e.g., a serializer that needs to know about every type but the types themselves shouldn't know about serialization), or when combined with a `sealed` hierarchy so the compiler enforces exhaustiveness. It's a smell when it's just reinventing a method that `area()` on the interface would express more simply — and a `sealed` hierarchy makes the compiler catch a missing case either way, so exhaustiveness isn't a deciding factor between the two styles.

### `var` — Good and Bad

```java
// Flagged: var hides the type where the type is exactly the information the reader needs
var result = process(data); // what does process() return? Not obvious from this line alone
```

```java
// Fixed: keep the explicit type when it's not obvious from the right-hand side
ValidationResult result = process(data);
```

```java
// Good use of var: the type is already obvious from the constructor call — no information lost
var orders = new ArrayList<Order>();
var connection = DriverManager.getConnection(url);
```

The rule of thumb: `var` is good when the type is redundant with something already visible on the same line (a constructor call, a well-named factory method); it's a readability regression when it hides the type of something returned by a method whose return type isn't obvious from its name.

### Streams — Good and Bad

```java
// Flagged: a stream pipeline that's harder to read than the equivalent loop, with a side-effecting peek
List<String> ids = orders.stream()
        .peek(o -> auditLog.add(o.id())) // side effect hidden inside peek — easy to miss, and peek isn't guaranteed to run for every element in all pipelines
        .filter(o -> o.total().compareTo(BigDecimal.TEN) > 0)
        .map(Order::id)
        .collect(Collectors.toList());
```

```java
// Fixed: keep the side effect explicit and separate from the pipeline; let the stream just transform data
List<String> ids = orders.stream()
        .filter(o -> o.total().compareTo(BigDecimal.TEN) > 0)
        .map(Order::id)
        .toList();
ids.forEach(auditLog::add); // or better, log alongside the filtering with a plain loop if the two concerns are related
```

Streams earn their keep for a clear filter/map/collect pipeline over a collection. They're a smell when: the lambda has side effects (mutating something outside the stream — see Scenario 12), the pipeline is nested or nearly unreadable compared to an equivalent loop, or exceptions need awkward wrapping because the functional interfaces involved don't declare `throws` (see `06-exception-handling.md` and `08-functional-programming.md`).

### Switch Expressions and Text Blocks

```java
// Flagged: old-style switch statement with fall-through risk and repeated assignment
String tier;
switch (score) {
    case 0: case 1: case 2: tier = "BRONZE"; break;
    case 3: case 4: tier = "SILVER"; break;
    default: tier = "GOLD";
}
```

```java
// Fixed: switch expression — no fall-through risk, no repeated variable, exhaustiveness is clearer
String tier = switch (score) {
    case 0, 1, 2 -> "BRONZE";
    case 3, 4 -> "SILVER";
    default -> "GOLD";
};
```

```java
// Flagged: multi-line SQL/JSON built with escaped string concatenation
String query = "SELECT id, name\n" +
        "FROM customers\n" +
        "WHERE status = 'ACTIVE'\n";
```

```java
// Fixed: a text block reads exactly like the text it represents
String query = """
        SELECT id, name
        FROM customers
        WHERE status = 'ACTIVE'
        """;
```

These two are close to unconditional wins — flag their *absence* as a missed simplification, not just misuse.

## Common Code-Review Interview Pitfalls

These are mistakes candidates make *in the interview itself* — separate from the Java-language pitfalls earlier in this chapter. Interviewers see these constantly, and each one costs real points even when the candidate's technical instincts are otherwise sound.

1. **Bikeshedding a style nit while a data race sits three lines away.** Why it matters: it signals you can't triage severity, which is the actual skill being tested — finding bugs is only half the job.
2. **Not asking about the concurrency model before reviewing.** Why it matters: "is this called from multiple threads?" changes almost every finding in a snippet; guessing wrong wastes the whole review and can make you miss the one bug that mattered.
3. **Missing the concurrency bug entirely and only flagging surface-level issues (naming, formatting).** Why it matters: concurrency bugs are usually the actual point of the exercise; if the snippet has a shared mutable `HashMap` or an unsynchronized singleton, that has to be the headline finding.
4. **Listing every issue in file order with no prioritization.** Why it matters: a review that treats a missing `@Override` annotation with the same weight as a resource leak tells the interviewer you don't understand production risk.
5. **Being rude or accusatory about the (fictional) author's competence.** Why it matters: real code review is collaborative; interviewers are explicitly evaluating whether they'd want you reviewing a teammate's PR, not just whether you can spot bugs.
6. **Not verifying assumptions before declaring something broken.** Why it matters: jumping to "this is definitely a bug" about, say, an overload's resolution without checking the actual types involved can make you confidently state something wrong — worse than not spotting it, because it damages credibility on the things you got right.
7. **Over-engineering the fix — proposing a distributed lock, a message queue, or a rewrite for a five-line bug.** Why it matters: it shows you can't scope a fix to the actual problem, and real reviewers are wary of suggestions that would balloon the size and risk of a PR.
8. **Not asking about requirements or constraints before assuming the "obvious" fix is right.** Why it matters: what looks like an unbounded cache bug might be intentional if the process is short-lived and restarted daily — the right fix depends on context you don't have unless you ask.
9. **Silence — reading the code for a long time without narrating your thought process.** Why it matters: interviewers are assessing *how* you think, not just your final answer; silent reading gives them nothing to evaluate and often reads as being stuck.
10. **Fixating on one bug and running out of time to scan the rest of the snippet.** Why it matters: most review snippets have 2-4 issues by design; spending the entire session on the first one you spot means you miss the ones planted specifically to test breadth.
11. **Proposing a fix without explaining the mechanism behind the bug.** Why it matters: "just make it a `ConcurrentHashMap`" without explaining *why* the current code races sounds like memorized pattern-matching rather than understanding — interviewers probe follow-ups precisely to distinguish the two.
12. **Ignoring what's actually good about the code and only producing criticism.** Why it matters: real reviews acknowledge good decisions; an interview review that's 100% negative, even of intentionally-buggy code, reads as a habit rather than a calibrated judgment for this specific snippet.
13. **Getting defensive or argumentative when the interviewer pushes back or asks "are you sure?"** Why it matters: pushback is often a genuine probe to see if you'll re-examine your reasoning, not a challenge to win — treat it as an invitation to double-check, not an attack.
14. **Confusing "I would add a unit test for this" with actually explaining what the bug is.** Why it matters: proposing tests is good practice, but it doesn't substitute for identifying and explaining the defect itself — interviewers will ask "so what's actually wrong?" if you deflect into testing talk.
15. **Not distinguishing "this will definitely break" from "this could theoretically be an issue under conditions we haven't confirmed."** Why it matters: conflating certain bugs with speculative ones misleads the listener about urgency — say which is which.
16. **Trying to rewrite the entire snippet from scratch instead of making the minimal fix.** Why it matters: real reviews respect the existing design and diff size where possible; a wholesale rewrite in an interview eats your limited time and often introduces new bugs of its own.
17. **Forgetting to mention severity labels or any triage vocabulary at all.** Why it matters: explicit labels (blocker/major/minor/nit) are what make a review actionable for a team lead deciding whether to merge — omitting them makes your feedback harder to act on even if it's technically correct.
18. **Not asking whether this code is even meant to run in production as-is, versus being a simplified teaching example.** Why it matters: some interview snippets are deliberately minimal and not every "issue" (e.g., missing logging, missing tests) is worth raising with the same weight as an actual correctness or safety bug — read the intent of the exercise.
19. **Spending so long on the review checklist ritual that you never get to the actual snippet.** Why it matters: the checklist in this chapter is a tool to internalize beforehand, not a script to recite out loud during the interview — using it as a mental scan, not a spoken monologue, keeps the conversation moving.
20. **Treating the interviewer as an adversary to be defeated rather than a colleague to collaborate with.** Why it matters: the entire point of the exercise is simulating a real code review conversation — the candidates who do best treat it exactly like one, asking questions, taking suggestions, and building toward the same goal as the interviewer: correct, safe, maintainable code.
