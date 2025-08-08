# 9. Transactions

## Overview

A **transaction** is a group of operations that must succeed or fail together — think of it like an envelope around several database statements. If everything inside works, the envelope is sealed and saved (**commit**). If anything inside fails, the whole envelope is thrown away as if nothing happened (**rollback**). This "all or nothing" guarantee is usually described with the acronym **ACID**: **A**tomicity (all steps happen or none do), **C**onsistency (the database never ends up in a broken, half-updated state), **I**solation (transactions running at the same time don't see each other's half-finished work), and **D**urability (once committed, the data survives even a crash). Transactions matter because real business operations rarely touch just one row — transferring money between two bank accounts means debiting one and crediting the other, and you never want only one of those to happen. Spring makes transaction management easy through the `@Transactional` annotation, but that convenience hides a lot of behavior you need to understand to avoid subtle bugs — especially around proxies, propagation, and rollback rules. This chapter walks through how Spring manages transactions and the traps that trip up almost every developer at least once.

## Transaction Management

Spring supports two styles of transaction management: **programmatic** (you write code that explicitly starts, commits, and rolls back) and **declarative** (you just annotate a method and Spring handles the rest). Almost all real-world Spring Boot code uses the declarative style, but it's built on top of a programmatic foundation, so it helps to see both.

At the core sits the `PlatformTransactionManager` interface. Spring Boot auto-configures an implementation for you based on what's on the classpath — for a plain JDBC/JPA app it's usually `JpaTransactionManager` or `DataSourceTransactionManager`.

**Programmatic transaction management** using `TransactionTemplate`:

```java
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.stereotype.Service;

@Service
public class AccountService {

    private final TransactionTemplate transactionTemplate;
    private final AccountRepository accountRepository;

    public AccountService(TransactionTemplate transactionTemplate,
                           AccountRepository accountRepository) {
        this.transactionTemplate = transactionTemplate;
        this.accountRepository = accountRepository;
    }

    public void transferFunds(Long fromId, Long toId, BigDecimal amount) {
        transactionTemplate.executeWithoutResult(status -> {
            Account from = accountRepository.findById(fromId).orElseThrow();
            Account to = accountRepository.findById(toId).orElseThrow();

            from.withdraw(amount);
            to.deposit(amount);

            accountRepository.save(from);
            accountRepository.save(to);
            // If an exception is thrown here, status marks rollback automatically.
        });
    }
}
```

**Declarative transaction management** using `@Transactional` — the same logic, far less boilerplate:

```java
@Service
public class AccountService {

    private final AccountRepository accountRepository;

    public AccountService(AccountRepository accountRepository) {
        this.accountRepository = accountRepository;
    }

    @Transactional
    public void transferFunds(Long fromId, Long toId, BigDecimal amount) {
        Account from = accountRepository.findById(fromId).orElseThrow();
        Account to = accountRepository.findById(toId).orElseThrow();

        from.withdraw(amount);
        to.deposit(amount);

        accountRepository.save(from);
        accountRepository.save(to);
    }
}
```

Key points:

- Programmatic management gives you fine-grained control (useful for conditional transaction logic) but adds noise.
- Declarative management is the default choice in Spring Boot applications — it separates transaction logic from business logic.
- Both approaches ultimately delegate to a `PlatformTransactionManager`.

## Declarative Transactions

"Declarative" means you *declare* that a method needs a transaction, and Spring wires up the plumbing behind the scenes using **AOP (Aspect-Oriented Programming)** — a technique where Spring wraps your object in a **proxy** (a stand-in object) that adds behavior before and after your method runs.

To enable this, you need:

1. `@EnableTransactionManagement` — automatically added by Spring Boot's auto-configuration when a `PlatformTransactionManager` bean exists, so you rarely add it yourself.
2. A `PlatformTransactionManager` bean — auto-configured by Spring Boot if you have `spring-boot-starter-data-jpa` (or JDBC) on the classpath.
3. The `@Transactional` annotation on the methods (or classes) that need transaction behavior.

```java
@Configuration
@EnableTransactionManagement // usually implicit in Spring Boot, shown here for clarity
public class AppConfig {
    // DataSource, EntityManagerFactory, and transaction manager beans
    // are typically auto-configured by Spring Boot.
}
```

How it actually works under the hood:

1. Spring creates a **proxy** around your bean (CGLIB subclass proxy by default for classes, or a JDK dynamic proxy if the bean implements an interface).
2. When you call a `@Transactional` method *from another bean*, the call goes through the proxy first.
3. The proxy starts a transaction, calls your real method, then commits or rolls back based on the outcome.

```java
// Simplified idea of what the proxy does — you never write this yourself.
public class AccountServiceProxy extends AccountService {
    @Override
    public void transferFunds(Long fromId, Long toId, BigDecimal amount) {
        TransactionStatus status = transactionManager.getTransaction(txDefinition);
        try {
            super.transferFunds(fromId, toId, amount);
            transactionManager.commit(status);
        } catch (RuntimeException ex) {
            transactionManager.rollback(status);
            throw ex;
        }
    }
}
```

- Declarative transactions rely entirely on Spring being able to see the method call through the proxy.
- Calls that bypass the proxy (like a method calling another method on `this`) skip transaction logic entirely — more on this in the [Nested Transactions](#nested-transactions) and pitfalls sections.

## @Transactional

`@Transactional` is the annotation you'll use most often. You can put it on:

- A **method** — only that method gets transactional behavior.
- A **class** — every public method in the class gets the same default transactional behavior, unless overridden at the method level.

```java
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Isolation;

@Service
public class OrderService {

    @Transactional(
        propagation = Propagation.REQUIRED,
        isolation = Isolation.READ_COMMITTED,
        timeout = 10,
        readOnly = false,
        rollbackFor = { OrderProcessingException.class },
        noRollbackFor = { OrderAlreadyShippedWarning.class }
    )
    public void placeOrder(Order order) {
        // business logic
    }
}
```

Common attributes:

| Attribute | Purpose | Default |
|---|---|---|
| `propagation` | How this transaction relates to an existing one | `REQUIRED` |
| `isolation` | Level of isolation from other concurrent transactions | Database default |
| `timeout` | Max seconds before the transaction is forced to roll back | -1 (no timeout) |
| `readOnly` | Hint that no writes will occur (allows optimizations) | `false` |
| `rollbackFor` / `rollbackForClassName` | Extra exception types that *should* trigger rollback | — |
| `noRollbackFor` / `noRollbackForClassName` | Exception types that should *not* trigger rollback | — |

Important behavioral rules:

- By default, `@Transactional` rolls back on **unchecked exceptions** (`RuntimeException` and `Error`) and does **not** roll back on **checked exceptions**.
- `@Transactional` only works on **public** methods when using the default proxy mechanism (Spring silently ignores it on non-public methods with the default proxy config — no error, just no transaction).
- It must be called through the Spring-managed proxy — calling it from within the same class (self-invocation) bypasses it entirely.

```java
@Service
public class ReportService {

    @Transactional
    public void generateMonthlyReport() {
        // works — called from a controller, i.e., through the proxy
    }

    // ❌ This annotation has no effect: private methods aren't proxied.
    @Transactional
    private void archiveOldReports() {
        // ...
    }
}
```

## Propagation

**Propagation** answers the question: "If I call a `@Transactional` method from inside another transaction, what should happen?" Should it join the existing transaction, start a brand-new one, or do something else entirely?

Think of a transaction like a train already in motion. Propagation settings decide whether a new method call hops onto the moving train, waits for it to stop and starts its own train, or refuses to board at all.

| Propagation | If a transaction already exists | If none exists |
|---|---|---|
| `REQUIRED` (default) | Joins the existing transaction | Starts a new transaction |
| `SUPPORTS` | Joins the existing transaction | Runs without a transaction |
| `MANDATORY` | Joins the existing transaction | Throws `IllegalTransactionStateException` |
| `REQUIRES_NEW` | Suspends the existing transaction, starts a brand-new independent one | Starts a new transaction |
| `NOT_SUPPORTED` | Suspends the existing transaction, runs without one | Runs without a transaction |
| `NEVER` | Throws `IllegalTransactionStateException` | Runs without a transaction |
| `NESTED` | Starts a nested transaction (savepoint) inside the existing one | Starts a new transaction |

```java
@Service
public class AuditService {

    // Always runs in its own transaction, independent of the caller's.
    // Useful for audit logs you want to keep even if the main operation fails.
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void logAction(String action, String actor) {
        auditRepository.save(new AuditEntry(action, actor, Instant.now()));
    }
}

@Service
public class PaymentService {

    private final AuditService auditService;
    private final PaymentRepository paymentRepository;

    @Transactional
    public void processPayment(Payment payment) {
        paymentRepository.save(payment);

        // Even if processPayment() later fails and rolls back,
        // this audit entry (in its own REQUIRES_NEW transaction) stays committed.
        auditService.logAction("PAYMENT_ATTEMPTED", payment.getPayerId());

        chargeCard(payment); // might throw
    }
}
```

Key points:

- `REQUIRED` is what you'll use 95% of the time — it's the sensible default.
- `REQUIRES_NEW` suspends the outer transaction; it physically opens a **second database connection** while the first is paused. Use it sparingly (it has a real performance/connection-pool cost).
- `NESTED` relies on database **savepoints** and only works with resource managers that support them (e.g., JDBC via `DataSourceTransactionManager`); it is **not** generally supported by JPA/`JpaTransactionManager`.

## Isolation Levels

**Isolation** controls how much one transaction can "see" of another transaction's uncommitted or concurrently changing data. Higher isolation means more safety but less concurrency (transactions block each other more and run slower).

Three classic problems isolation levels protect against:

- **Dirty read**: reading data that another transaction wrote but hasn't committed yet (and might roll back).
- **Non-repeatable read**: reading the same row twice in one transaction and getting different values because another transaction committed a change in between.
- **Phantom read**: re-running the same query twice in one transaction and getting a different *set of rows* (e.g., a new row now matches your `WHERE` clause) because another transaction inserted/deleted rows in between.

| Isolation Level | Dirty Read | Non-Repeatable Read | Phantom Read |
|---|---|---|---|
| `READ_UNCOMMITTED` | Possible | Possible | Possible |
| `READ_COMMITTED` | Prevented | Possible | Possible |
| `REPEATABLE_READ` | Prevented | Prevented | Possible |
| `SERIALIZABLE` | Prevented | Prevented | Prevented |

```java
@Service
public class InventoryService {

    // READ_COMMITTED is the default for most databases (e.g., PostgreSQL, SQL Server).
    // Prevents dirty reads while still allowing good concurrency.
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public int checkStock(Long productId) {
        return inventoryRepository.findStockLevel(productId);
    }

    // SERIALIZABLE gives the strongest guarantee but can cause more
    // lock contention / retries under high concurrency — use only when necessary,
    // e.g., for critical stock reservation logic.
    @Transactional(isolation = Isolation.SERIALIZABLE)
    public void reserveLastUnit(Long productId) {
        int stock = inventoryRepository.findStockLevel(productId);
        if (stock <= 0) {
            throw new OutOfStockException(productId);
        }
        inventoryRepository.decrementStock(productId);
    }
}
```

Key points:

- `ISOLATION_DEFAULT` (Spring's default value for the attribute) means "use whatever the underlying database's default is" — MySQL's InnoDB default is `REPEATABLE_READ`; PostgreSQL, Oracle, and SQL Server default to `READ_COMMITTED`.
- Stricter isolation = more correctness guarantees but more locking, more blocking, and lower throughput.
- Isolation level is normally set once per transaction at the start — it can't be changed once the transaction has partially started reading/writing in most databases.

## Rollback Rules

By default, Spring's declarative transaction management follows this rule: **roll back on unchecked exceptions (`RuntimeException`, `Error`), commit on checked exceptions**. This surprises a lot of developers coming from a "checked exceptions are for recoverable errors" mindset — Spring assumes checked exceptions are "expected" business outcomes you might want to commit through, and unchecked exceptions are "unexpected" failures you want to abort on.

```java
@Service
public class ShipmentService {

    // Checked exception — by DEFAULT this does NOT trigger a rollback!
    @Transactional
    public void shipOrder(Order order) throws CarrierUnavailableException {
        shipmentRepository.markAsProcessing(order);
        if (!carrierClient.isAvailable()) {
            throw new CarrierUnavailableException("No carrier available");
            // The "markAsProcessing" update above WILL be committed
            // unless you configure rollbackFor.
        }
    }
}
```

Fix it explicitly with `rollbackFor`:

```java
@Service
public class ShipmentService {

    @Transactional(rollbackFor = CarrierUnavailableException.class)
    public void shipOrder(Order order) throws CarrierUnavailableException {
        shipmentRepository.markAsProcessing(order);
        if (!carrierClient.isAvailable()) {
            throw new CarrierUnavailableException("No carrier available");
            // Now correctly rolled back.
        }
    }
}
```

You can also exclude specific unchecked exceptions from triggering rollback with `noRollbackFor`:

```java
@Transactional(noRollbackFor = LowPriorityWarning.class)
public void processItem(Item item) {
    // If LowPriorityWarning (a RuntimeException) is thrown here,
    // the transaction still commits.
}
```

| Exception type | Default behavior |
|---|---|
| `RuntimeException` / `Error` (unchecked) | Rollback |
| Checked exception (`extends Exception`, not `RuntimeException`) | Commit |
| Any exception listed in `rollbackFor` | Rollback |
| Any exception listed in `noRollbackFor` | Commit |

Key points:

- This default behavior is defined by Spring's `DefaultTransactionAttribute`, not by the JDBC/JPA layer itself.
- Always be explicit with `rollbackFor` when a method declares checked exceptions — don't rely on the default.
- A rollback only happens if the exception actually **propagates out of the proxied method**. If you catch it inside the method and don't rethrow, Spring never even sees it (see pitfalls).

## Read-only Transactions

`readOnly = true` is a **hint**, not an enforced restriction — it tells the underlying resource (JPA/Hibernate, the JDBC driver, or the database) that no writes are expected, allowing performance optimizations.

```java
@Service
public class ProductCatalogService {

    private final ProductRepository productRepository;

    public ProductCatalogService(ProductRepository productRepository) {
        this.productRepository = productRepository;
    }

    @Transactional(readOnly = true)
    public List<ProductDto> listAllProducts() {
        return productRepository.findAll()
                .stream()
                .map(ProductDto::from)
                .toList();
    }
}
```

What `readOnly = true` typically enables:

- **Hibernate**: can skip "dirty checking" (the process of comparing entity state to detect changes to flush) for entities loaded in that session, saving CPU and memory.
- **JDBC driver / database**: some drivers (and databases like MySQL with certain configurations) can route the connection differently or apply read-optimized locking.
- **Read replica routing**: in setups with a read/write-split `DataSource` (via `AbstractRoutingDataSource` or similar), `readOnly = true` is often the signal used to route reads to a replica.

| Aspect | `readOnly = true` | `readOnly = false` (default) |
|---|---|---|
| Writes attempted | Not expected — behavior on actual writes is database/driver-specific and may throw or may silently succeed | Fully allowed |
| Hibernate dirty checking | Can be skipped for that session | Performed |
| Performance | Generally lighter | Full transactional overhead |
| Intended use | Queries, reports, listing endpoints | Any method that inserts/updates/deletes |

```java
@Transactional(readOnly = true)
public OrderSummary getOrderSummary(Long orderId) {
    Order order = orderRepository.findById(orderId).orElseThrow();
    // ❌ Don't do writes here — behavior depends on the driver/DB and is not
    // reliably enforced by Spring itself; some setups will still let this through
    // and surprise you later, others will throw.
    order.setLastViewedAt(Instant.now());
    return OrderSummary.from(order);
}
```

Key points:

- Always mark pure query methods `readOnly = true` — it's cheap to add and communicates intent clearly.
- It is **not** a hard guarantee against writes in every database/driver combination — treat it as an optimization hint plus documentation, not a security boundary.
- Don't forget it on class-level `@Transactional` overrides — a `readOnly = true` at the class level can be overridden per-method for the few methods that actually write.

## Nested Transactions

"Nested" transactions in Spring specifically means `Propagation.NESTED`, which uses **savepoints** — a marker within a transaction you can roll back to, without rolling back the entire transaction.

Think of a savepoint like a checkpoint in a video game level: if you die after the checkpoint, you restart from the checkpoint, not from the very beginning of the level.

```java
@Service
public class BulkImportService {

    private final RecordRepository recordRepository;

    public BulkImportService(RecordRepository recordRepository) {
        this.recordRepository = recordRepository;
    }

    @Transactional
    public void importRecords(List<RawRecord> rawRecords) {
        for (RawRecord raw : rawRecords) {
            try {
                importSingleRecord(raw);
            } catch (ValidationException ex) {
                // Only this one record's savepoint is rolled back;
                // records already imported before it stay intact.
                log.warn("Skipping invalid record {}: {}", raw.getId(), ex.getMessage());
            }
        }
    }

    @Transactional(propagation = Propagation.NESTED)
    public void importSingleRecord(RawRecord raw) {
        Record record = Record.fromRaw(raw);
        recordRepository.save(record);
    }
}
```

Key points and caveats:

- `NESTED` requires a `PlatformTransactionManager` that supports savepoints — `DataSourceTransactionManager` (plain JDBC) supports it; `JpaTransactionManager` generally does **not** support true savepoints for typical JPA usage.
- Unlike `REQUIRES_NEW`, a `NESTED` transaction is still part of the **same physical database transaction and connection** — it just adds a rollback point within it.
- If the *outer* transaction rolls back, everything rolls back — including anything done in the nested transaction, savepoint or not.
- `NESTED` is different from having "two `@Transactional` methods calling each other with `REQUIRED`" — that's not nesting in the technical sense, it's just joining the same transaction.

| Feature | `REQUIRES_NEW` | `NESTED` |
|---|---|---|
| Physical transaction | New, independent, separate connection | Same physical transaction |
| Outer rollback affects it? | No — it's independent | Yes — outer rollback undoes everything |
| Rollback of just this part affects outer? | No | No (only rolls back to its savepoint) |
| Database support needed | Any | Savepoint support (mainly JDBC-based) |

## Transaction Synchronization

**Transaction synchronization** is the mechanism Spring uses to hook additional logic into the lifecycle of the *current* transaction — for example, "run this code only after the transaction successfully commits" or "clean up this resource right before the transaction finishes, regardless of outcome."

It's managed through `TransactionSynchronizationManager`, and most day-to-day code interacts with it indirectly via `@TransactionalEventListener` rather than the low-level API.

```java
import org.springframework.transaction.event.TransactionalEventListener;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.context.ApplicationEventPublisher;

@Service
public class OrderService {

    private final ApplicationEventPublisher eventPublisher;

    public OrderService(ApplicationEventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    @Transactional
    public void placeOrder(Order order) {
        orderRepository.save(order);
        // Published now, but the listener below only runs AFTER commit.
        eventPublisher.publishEvent(new OrderPlacedEvent(order.getId()));
    }
}

@Component
public class OrderNotificationListener {

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onOrderPlaced(OrderPlacedEvent event) {
        // Safe to send an email/notification here — the order is guaranteed
        // to actually exist in the database by the time this runs.
        emailService.sendOrderConfirmation(event.orderId());
    }
}
```

Lower-level API, for advanced cases (rarely needed directly in application code):

```java
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Transactional
public void doWork() {
    TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
        @Override
        public void afterCommit() {
            metrics.increment("work.committed");
        }

        @Override
        public void afterCompletion(int status) {
            // Runs regardless of commit or rollback — good place for cleanup.
            tempFileCleaner.cleanup();
        }
    });
}
```

Available `TransactionPhase` values for `@TransactionalEventListener`:

| Phase | Runs when |
|---|---|
| `BEFORE_COMMIT` | Just before the transaction commits |
| `AFTER_COMMIT` (default) | After the transaction has successfully committed |
| `AFTER_ROLLBACK` | After the transaction has rolled back |
| `AFTER_COMPLETION` | After commit or rollback, whichever happens |

Key points:

- Use `@TransactionalEventListener(phase = AFTER_COMMIT)` for side effects that should only happen if the data really was saved — sending emails, calling external APIs, publishing to a message queue.
- If there is **no active transaction** when the event is published, by default the listener simply doesn't fire (this is a common gotcha — always publish from within a transactional method if you rely on this).
- This decouples "save the data" from "react to the data being saved," keeping your transactional method focused and fast.

## The Self-Invocation Proxy Problem

This deserves its own deep dive because it's one of the most common causes of "why isn't my `@Transactional` working?!" bugs.

Recall from the [Declarative Transactions](#declarative-transactions) section: Spring implements `@Transactional` using a **proxy** — a wrapper object that intercepts method calls from the *outside*. The proxy only gets a chance to add transactional behavior when a call comes in through it. But when a method inside a class calls another method on `this`, that's a plain Java method call — it never goes through the proxy. This is called **self-invocation**.

```java
@Service
public class InvoiceService {

    @Transactional
    public void generateInvoices(List<Order> orders) {
        for (Order order : orders) {
            // ❌ This is a call on "this" — it bypasses the Spring proxy entirely.
            // The @Transactional on createInvoice() below has NO EFFECT here.
            createInvoice(order);
        }
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void createInvoice(Order order) {
        invoiceRepository.save(new Invoice(order));
        // This does NOT run in its own new transaction as intended —
        // it just silently joins whatever transaction generateInvoices() started
        // (or runs with none, if called from a non-transactional context).
    }
}
```

Why this happens, visually:

```
Caller  ──▶  Proxy (adds transaction logic)  ──▶  Real InvoiceService.generateInvoices()
                                                          │
                                                          │ this.createInvoice(order)
                                                          ▼
                                              Real InvoiceService.createInvoice()
                                              (proxy is completely skipped!)
```

### Fix 1: Self-injection

Inject the bean into itself (via constructor or `@Lazy` field) so calls go through the proxy, not `this`.

```java
@Service
public class InvoiceService {

    private final InvoiceService self; // the Spring-managed proxy, not "this"

    public InvoiceService(@Lazy InvoiceService self) {
        this.self = self;
    }

    @Transactional
    public void generateInvoices(List<Order> orders) {
        for (Order order : orders) {
            self.createInvoice(order); // goes through the proxy correctly
        }
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void createInvoice(Order order) {
        invoiceRepository.save(new Invoice(order));
    }
}
```

`@Lazy` is required here to avoid a circular-dependency startup error (the bean depending on itself while still being constructed).

### Fix 2: Move the method to a separate bean

Often the cleanest fix — split the method that needs its own transactional behavior into a different Spring bean, and inject that bean.

```java
@Service
public class InvoiceService {

    private final InvoiceCreator invoiceCreator;

    public InvoiceService(InvoiceCreator invoiceCreator) {
        this.invoiceCreator = invoiceCreator;
    }

    @Transactional
    public void generateInvoices(List<Order> orders) {
        for (Order order : orders) {
            invoiceCreator.createInvoice(order); // real call through a different bean's proxy
        }
    }
}

@Service
public class InvoiceCreator {

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void createInvoice(Order order) {
        invoiceRepository.save(new Invoice(order));
    }
}
```

### Fix 3: `TransactionTemplate` or `AopContext.currentProxy()`

Use `TransactionTemplate` to programmatically start the desired transaction inline, avoiding the need to call a separate proxied method at all:

```java
@Service
public class InvoiceService {

    private final TransactionTemplate requiresNewTemplate;
    private final InvoiceRepository invoiceRepository;

    public InvoiceService(PlatformTransactionManager transactionManager,
                           InvoiceRepository invoiceRepository) {
        this.requiresNewTemplate = new TransactionTemplate(transactionManager);
        this.requiresNewTemplate.setPropagationBehavior(Propagation.REQUIRES_NEW.value());
        this.invoiceRepository = invoiceRepository;
    }

    @Transactional
    public void generateInvoices(List<Order> orders) {
        for (Order order : orders) {
            requiresNewTemplate.executeWithoutResult(status ->
                invoiceRepository.save(new Invoice(order))
            );
        }
    }
}
```

Or, less commonly recommended, fetch the current proxy explicitly with `AopContext.currentProxy()` (requires `exposeProxy = true` on `@EnableAspectJAutoProxy`):

```java
@Configuration
@EnableAspectJAutoProxy(exposeProxy = true)
public class AppConfig { }

@Service
public class InvoiceService {

    @Transactional
    public void generateInvoices(List<Order> orders) {
        for (Order order : orders) {
            // Grabs the live proxy for "this" out of a thread-local holder.
            ((InvoiceService) AopContext.currentProxy()).createInvoice(order);
        }
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void createInvoice(Order order) {
        invoiceRepository.save(new Invoice(order));
    }
}
```

| Fix | Pros | Cons |
|---|---|---|
| Self-injection | Simple, stays in one class | Slightly unusual pattern; needs `@Lazy` |
| Separate bean | Cleanest, most idiomatic Spring | Requires splitting classes/responsibilities |
| `TransactionTemplate` | No proxy issues at all, explicit control | More verbose, imperative style |
| `AopContext.currentProxy()` | Works without redesigning classes | Couples code to Spring AOP internals; needs `exposeProxy = true`; considered a last resort |

## Common Code Review / Interview Pitfalls

- **Self-invocation bypassing the proxy.** Calling a `@Transactional` method from another method in the *same* class via `this` skips the proxy, so the annotation is silently ignored. Fix: inject the bean into itself, extract to a separate bean, or use `TransactionTemplate`.
  ```java
  // ❌ Bad: this.createInvoice() bypasses the proxy
  @Transactional
  public void run() { createInvoice(); }

  // ✅ Good: call through an injected bean/proxy
  @Transactional
  public void run() { invoiceCreator.createInvoice(); }
  ```

- **`@Transactional` on a private or final method.** With the default proxy-based AOP, Spring can only intercept calls to methods it can override — `private` methods aren't visible to a subclass proxy at all, and `final` methods can't be overridden by CGLIB. Spring silently does nothing; no exception is thrown, which makes this easy to miss in review. Fix: make the method `public` (or `protected`/package-private with interface-based proxies) and non-final.
  ```java
  // ❌ Bad: annotation has zero effect
  @Transactional
  private void archive() { ... }

  // ✅ Good
  @Transactional
  public void archive() { ... }
  ```

- **Assuming checked exceptions roll back.** Spring's default rollback rule only covers unchecked exceptions (`RuntimeException`/`Error`); a checked exception thrown from a `@Transactional` method commits by default. Fix: use `rollbackFor` explicitly whenever a transactional method declares checked exceptions.
  ```java
  // ❌ Bad: commits even though shipment failed
  @Transactional
  public void ship() throws CarrierException { ... }

  // ✅ Good
  @Transactional(rollbackFor = CarrierException.class)
  public void ship() throws CarrierException { ... }
  ```

- **Swallowing exceptions inside the transactional method.** If you `catch` an exception and don't rethrow it, Spring's proxy never sees a failure, so it commits as if everything succeeded — even though part of the work never happened. Fix: rethrow, wrap and rethrow, or explicitly call `TransactionAspectSupport.currentTransactionStatus().setRollbackOnly()` if you must swallow it.
  ```java
  // ❌ Bad: swallows the exception, transaction commits anyway
  @Transactional
  public void process() {
      try { risky(); } catch (Exception e) { log.error("oops", e); }
  }

  // ✅ Good: mark rollback-only explicitly if you handle the exception yourself
  @Transactional
  public void process() {
      try {
          risky();
      } catch (Exception e) {
          log.error("oops", e);
          TransactionAspectSupport.currentTransactionStatus().setRollbackOnly();
      }
  }
  ```

- **Long-running transactions holding a database connection.** Doing slow work (large loops, file I/O, sleeping) inside a transaction keeps a connection checked out of the pool the entire time, starving other requests and risking pool exhaustion. Fix: keep transactions short — do slow work *before* or *after* the transactional boundary, not inside it.

- **Making HTTP/network calls inside a transaction.** Calling an external API while holding a database transaction open ties your database connection's lifetime to the latency (and failures) of a remote system — a slow or hung external call can block a connection for a long time. Fix: call external services before starting the transaction (and pass results in), or after it commits (e.g., via `@TransactionalEventListener(phase = AFTER_COMMIT)`).

- **Putting `@Transactional` on a `@RestController` / `@Controller`.** Controllers are meant to handle HTTP concerns (parsing requests, calling services, building responses) — wrapping the whole request in a transaction ties the transaction's lifetime to view rendering, serialization, and any other controller-layer work, and mixes concerns. Fix: put `@Transactional` on the service layer, and keep the controller thin.
  ```java
  // ❌ Bad
  @RestController
  public class OrderController {
      @Transactional
      @PostMapping("/orders")
      public OrderDto create(@RequestBody OrderRequest req) { ... }
  }

  // ✅ Good
  @RestController
  public class OrderController {
      @PostMapping("/orders")
      public OrderDto create(@RequestBody OrderRequest req) {
          return orderService.create(req); // @Transactional lives here
      }
  }
  ```

- **Missing `readOnly = true` on query-only methods.** Skipping this hint on pure read methods means Hibernate performs unnecessary dirty-checking and you lose a free, low-risk optimization (and, in read/write-split setups, you may miss out on read-replica routing). Fix: annotate all pure lookup/query methods with `@Transactional(readOnly = true)`.

- **Overusing `Propagation.REQUIRES_NEW`.** Every `REQUIRES_NEW` call opens a second physical connection while the first is suspended — sprinkling it everywhere can quietly exhaust the connection pool under load. Fix: reserve it for genuine cases (e.g., audit logging that must survive a rollback), not as a default.

- **Expecting `NESTED` to work the same with JPA as with plain JDBC.** `Propagation.NESTED` relies on database savepoints, which `JpaTransactionManager` generally doesn't support for typical JPA usage — using it there can throw `NestedTransactionNotSupportedException` at runtime. Fix: verify savepoint support for your transaction manager before relying on `NESTED`, or use `DataSourceTransactionManager` with plain JDBC if you need it.

- **Relying on the class-level default without checking method-level overrides.** A class-level `@Transactional(readOnly = true)` silently applies to every method unless a specific method overrides it — a new write method added later without its own `@Transactional` inherits `readOnly = true` and may misbehave. Fix: explicitly annotate write methods at the method level, don't rely on inherited class defaults for anything beyond simple read paths.

- **No timeout on long-running transactional methods.** Without a `timeout`, a stuck or slow query can hold a transaction (and its connection) open indefinitely. Fix: set a sensible `timeout` (in seconds) on `@Transactional` for operations that shouldn't run forever.

- **Not understanding that rollback only triggers if the exception escapes the proxied method.** A common interview trap: "why didn't my transaction roll back even though I saw an exception in the logs?" — usually because it was caught and logged inside the method rather than rethrown. Fix: always let rollback-worthy exceptions propagate, or explicitly call `setRollbackOnly()`.

- **Assuming isolation level changes take effect mid-transaction.** Setting `isolation` on a method doesn't change the isolation of an already-started/joined transaction — for `REQUIRED` propagation joining an existing transaction, the isolation attribute on the inner method is ignored (and some transaction managers will even throw if the values conflict). Fix: set the isolation level on the method that *starts* the transaction, and be aware of how it interacts with propagation.

## Quick Recap

- ACID = Atomicity, Consistency, Isolation, Durability — the four guarantees transactions provide.
- Declarative `@Transactional` is built on Spring AOP proxies — no proxy call, no transaction. This is the root cause of the self-invocation problem.
- Self-invocation (calling a `@Transactional` method via `this`) silently skips the transaction — fix with self-injection, a separate bean, or `TransactionTemplate`.
- `@Transactional` needs `public` (and non-`final`, for CGLIB) methods to work with the default proxy setup.
- Default rollback rule: unchecked exceptions roll back, checked exceptions commit — override with `rollbackFor`/`noRollbackFor`.
- Swallowed exceptions never reach the proxy, so the transaction commits even if you "handled" the failure — rethrow or call `setRollbackOnly()`.
- Propagation controls how a method's transaction relates to an existing one; `REQUIRED` is the sane default, `REQUIRES_NEW` opens a second connection, `NESTED` uses savepoints (JDBC only, generally not JPA).
- Isolation controls what one transaction can see of another's changes; higher isolation = fewer anomalies (dirty/non-repeatable/phantom reads) but less concurrency.
- `readOnly = true` is a performance hint (skips dirty checking, can enable replica routing) — not a hard write-blocking guarantee.
- Keep transactions short: no long-running work, no HTTP/network calls, no sleeping inside a transaction — it all holds a database connection hostage.
- Never put `@Transactional` on controllers — keep it on the service layer.
- Use `@TransactionalEventListener(phase = AFTER_COMMIT)` for side effects (emails, notifications) that should only fire once data is truly saved.
