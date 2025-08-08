# 15. Modern Concurrency (Java 21+)

Traditional Java concurrency (Chapters 13-14) is built on **platform threads** — thin wrappers around OS threads — and unstructured coordination primitives like `ExecutorService`. That model works, but it has two long-standing pain points: platform threads are too expensive to create "one per request," and fan-out/fan-in logic built from raw executors and futures is easy to get wrong (leaked tasks, missed cancellation, unclear ownership). Project Loom, delivered across JDK 19-25, attacks both problems: **virtual threads** make blocking, thread-per-request code cheap again, **structured concurrency** gives fan-out/fan-in a well-defined lifetime and error model, and **scoped values** give virtual threads a safer, cheaper alternative to `ThreadLocal`. This chapter targets JDK 21 (where virtual threads became final) through JDK 25 (where structured concurrency's API was reshaped), and is explicit everywhere about what is final versus preview in which release.

## Table of Contents

- [Virtual Threads (Project Loom)](#virtual-threads-project-loom)
- [Structured Concurrency](#structured-concurrency)
- [Scoped Values](#scoped-values)
- [Comparison Table: Platform Threads vs Virtual Threads](#comparison-table-platform-threads-vs-virtual-threads)
- [Worked Example: Fan-Out to Three Downstream Calls, Three Ways](#worked-example-fan-out-to-three-downstream-calls-three-ways)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Virtual Threads (Project Loom)

### The Thread-Per-Request Problem

The classic Java server model is thread-per-request: accept a connection, hand it a dedicated thread, let that thread block on I/O (database calls, HTTP calls to other services, disk reads) for as long as it needs. This model is simple to write, simple to debug (one thread = one request, so a thread dump tells you exactly what every request is doing), and works naturally with existing blocking APIs like JDBC, `java.io`, and synchronous HTTP clients.

The problem is that **platform threads are expensive**:

- Each platform thread reserves a large stack (commonly 512 KB-1 MB, configurable via `-Xss`), committed as OS memory.
- Creating a platform thread means an OS-level `clone`/`pthread_create` syscall — not free.
- The OS scheduler multiplexes a limited number of platform threads across a limited number of CPU cores; context switches between them are relatively costly.
- Because of the memory and scheduling cost, practical platform thread pools cap out somewhere in the low thousands, not millions.

So under load, thread-per-request architectures hit a wall: to serve more concurrent *blocked* requests (e.g., waiting on a slow downstream service), you need more threads, but you can't create enough of them without exhausting memory or thrashing the scheduler. Teams historically worked around this with either bounded thread pools (which cap throughput and can deadlock under saturation) or asynchronous/reactive programming (`CompletableFuture` chains, reactive streams), which scales far better but fragments the code into callbacks, breaks stack traces, and makes debugging much harder — you lose the "one thread = one request" story.

```java
// Classic thread-per-request server thread pool — caps around a few thousand
// concurrent in-flight requests no matter how much idle (blocked-on-I/O) time
// each request spends, because platform threads are the scarce resource.
ExecutorService pool = Executors.newFixedThreadPool(200);
for (Socket client : incomingConnections()) {
    pool.submit(() -> handleRequest(client)); // blocks on I/O inside handleRequest
}
```

### What a Virtual Thread Is

A **virtual thread** is a `Thread` (same `java.lang.Thread` API, same `Runnable`/interrupt/join semantics) whose stack is **not** a fixed-size native OS stack, but a *continuation* stored on the Java heap that can grow and shrink. Virtual threads are scheduled by the JVM, not the OS, using a small pool of platform threads called **carrier threads** (by default, sized to the number of CPU cores, via a `ForkJoinPool` in FIFO mode).

The key idea is **mounting**: when a virtual thread runs, the JVM *mounts* it onto a carrier thread — the carrier thread executes the virtual thread's code for a while. When the virtual thread does something blocking (e.g., a socket read, `Thread.sleep`, acquiring a `ReentrantLock`), the JVM recognizes this and **unmounts** the virtual thread from its carrier: it parks the continuation (saves just the relevant stack frames to the heap) and frees the carrier thread to mount and run a *different* virtual thread. When the blocking operation completes, the JVM finds a free carrier thread and **remounts** the virtual thread to resume exactly where it left off.

```
Carrier thread (platform thread)
   │
   ├── mounts virtual-thread-A ── runs until A blocks on I/O ── unmounts A
   ├── mounts virtual-thread-B ── runs until B blocks on I/O ── unmounts B
   ├── mounts virtual-thread-C ── runs to completion ────────── unmounts C
   └── mounts virtual-thread-A again (I/O finished) ── resumes A
```

Because the "expensive" part — the stack — lives on the heap as a lightweight, resizable object rather than as a pre-allocated OS stack, and because there is no OS-level thread creation involved, you can have **millions** of virtual threads outstanding, most of them parked waiting on I/O, backed by only a handful of carrier threads actually consuming CPU.

Crucially, this is invisible to your code: you still write plain, sequential, blocking-style code. `Thread.sleep(...)`, blocking `Socket` reads, JDBC calls, and synchronous `HttpClient.send(...)` all "just work" and internally cooperate with the scheduler to unmount the virtual thread while waiting, instead of tying up an OS thread.

### Creating Virtual Threads

```java
// 1. Thread.ofVirtual() builder — full control (name, inheritance of context, etc.)
Thread vt = Thread.ofVirtual()
        .name("order-worker-", 0)
        .start(() -> processOrder(orderId));
vt.join();

// 2. Thread.startVirtualThread — convenience one-liner, equivalent to
//    Thread.ofVirtual().start(task)
Thread vt2 = Thread.startVirtualThread(() -> {
    System.out.println("Running on: " + Thread.currentThread());
});

// 3. Executors.newVirtualThreadPerTaskExecutor() — an ExecutorService that
//    starts a brand-new virtual thread for every submitted task. This is the
//    recommended way to adopt virtual threads inside existing executor-based code.
try (ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor()) {
    List<Future<String>> results = new ArrayList<>();
    for (int i = 0; i < 10_000; i++) {
        int id = i;
        results.add(executor.submit(() -> callDownstreamService(id)));
    }
    for (Future<String> f : results) {
        System.out.println(f.get());
    }
} // executor.close() waits for in-flight tasks, same as shutdown + awaitTermination
```

`Thread.ofPlatform()` is the analogous builder for platform threads, useful when you need to opt back into a real OS thread deliberately (e.g., for a CPU-bound worker, discussed below).

### Final in JDK 21 (JEP 444)

Virtual threads shipped as a **preview feature** in JDK 19 (JEP 425) and JDK 20 (JEP 436), requiring `--enable-preview` to compile and run. They became a **final, standard feature in JDK 21** under **JEP 444** — no preview flag needed from JDK 21 onward. This is one of the headline reasons JDK 21 (an LTS release) is treated as the practical starting point for "modern concurrency" in production Java.

### When Virtual Threads Help — and When They Don't

Virtual threads help enormously for **I/O-bound, high-concurrency, blocking-style code**: web servers handling many concurrent requests that spend most of their time waiting on databases, REST calls to other services, message queues, or file/network I/O. The scheduler can run thousands of such threads on a handful of CPU cores because they're parked (not consuming CPU) most of the time.

Virtual threads do **not** help — and can actively hurt — for **CPU-bound work**. If a virtual thread is doing tight-loop computation (image processing, cryptography, sorting large in-memory data, JSON parsing of huge payloads), it never yields to the scheduler; it simply occupies its carrier thread until done, exactly like a platform thread would. Since there are only as many carrier threads as CPU cores, spinning up a million virtual threads that are all CPU-bound just serializes them behind the same small carrier pool — you get no more parallelism than you had before, plus the (small) overhead of virtual thread bookkeeping. For CPU-bound work, the right tool is still a **bounded platform thread pool** sized to the number of cores (e.g., `ForkJoinPool` or a fixed `ExecutorService`), so you get genuine parallel CPU utilization without oversubscribing cores.

```java
// GOOD fit: thousands of concurrent virtual threads, each mostly blocked on I/O
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    for (String url : urls) {
        executor.submit(() -> httpClient.send(request(url), BodyHandlers.ofString()));
    }
}

// BAD fit: CPU-bound matrix multiplication on virtual threads gains nothing —
// use a bounded platform-thread pool (e.g., Executors.newFixedThreadPool(cores))
// or ForkJoinPool.commonPool() instead.
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    for (double[][] matrix : matrices) {
        executor.submit(() -> multiply(matrix)); // pure CPU work — no yield points
    }
}
```

### Pinning

**Pinning** happens when a virtual thread blocks while it *cannot* be unmounted from its carrier thread — the carrier thread is stuck waiting right along with it, defeating the whole point of virtual threads and reducing the number of usable carriers for everyone else.

In **JDK 21**, pinning is caused by:

1. **`synchronized` blocks or methods** — if a virtual thread blocks *inside* a `synchronized` block (e.g., waiting on another lock, or performing blocking I/O while holding the monitor), it cannot be unmounted; the carrier thread is pinned until the virtual thread exits the `synchronized` region.
2. **Native frames** — if the virtual thread's call stack includes a native method (JNI) between it and the blocking operation, the JVM cannot unmount it, because it cannot relocate native stack frames.

```java
// Pinning hazard in JDK 21: blocking I/O while holding a monitor pins the
// carrier thread for the duration of the network call.
private final Object lock = new Object();

void handle() {
    synchronized (lock) {
        // If this blocks (slow socket, slow DB), the carrier thread is pinned —
        // it cannot run any other virtual thread until this call returns.
        String response = callSlowLegacyService();
        cache.put(key, response);
    }
}
```

The fix is usually to replace `synchronized` with `java.util.concurrent.locks.ReentrantLock`, which is pinning-safe, and to keep blocking calls out of any remaining `synchronized` sections:

```java
private final ReentrantLock lock = new ReentrantLock();

void handle() {
    lock.lock();
    try {
        String response = callSlowLegacyService(); // no pinning — ReentrantLock
        cache.put(key, response);                   // cooperates with the scheduler
    } finally {
        lock.unlock();
    }
}
```

**JDK 24 (JEP 491)** removed the `synchronized`-block pinning problem entirely: virtual threads can now be unmounted even while holding a monitor acquired via `synchronized`. This was a significant JVM-internal change (it required reworking how monitors interact with the continuation mechanism) and it means that, from JDK 24 onward, `synchronized` is no longer a pinning hazard — only native frames still pin. This is a common interview trap: candidates who memorized "avoid `synchronized` with virtual threads" as an absolute rule need to know it is version-specific (true for JDK 21-23, no longer the dominant concern from JDK 24 on), though switching to `ReentrantLock` remains harmless and is still good practice for other reasons (e.g., `tryLock`, fairness, interruptible acquisition).

#### Detecting Pinning

Two supported ways to detect pinning in production or during testing:

```
-Djdk.tracePinnedThreads=full
```

Run with this system property and the JVM prints a stack trace every time a virtual thread parks while pinned, showing exactly which frame (the `synchronized` block or native call) caused it:

```
Thread[#31,tid=31]
    java.base/java.lang.VirtualThread.parkOnCarrierThread(VirtualThread.java:661)
    java.base/java.lang.VirtualThread.park(VirtualThread.java:592)
    ...
    <== monitors:1
    OrderService.handle(OrderService.java:42) <-- monitor
```

Use `full` for complete stack traces, or `short` for a one-line-per-occurrence summary.

The second option is **JDK Flight Recorder (JFR)**: virtual thread pinning emits a `jdk.VirtualThreadPinned` event, which you can capture with a JFR recording and inspect in JDK Mission Control (JMC) or via `jfr print --events jdk.VirtualThreadPinned recording.jfr`. JFR is lower-overhead and more suitable for always-on production monitoring than the tracing flag, which is best for local debugging or CI.

```bash
# Capture a JFR recording and filter for pinning events after the fact
java -XX:StartFlightRecording=filename=app.jfr -jar app.jar
jfr print --events jdk.VirtualThreadPinned app.jfr
```

### Why You Should NOT Pool Virtual Threads

Thread pools exist to *reuse* expensive platform threads and to *bound* concurrency. Virtual threads are cheap to create and discard — creating a new one is closer to allocating an object than to an OS syscall — so pooling them buys you nothing on the "reuse" side, and actively hurts on the "bounding" side: pooling caps how many tasks can run concurrently by limiting the pool size, which reintroduces exactly the scalability ceiling virtual threads were meant to remove (and can also create priority inversion / deadlock risk if a pooled task blocks waiting on another task that never gets a pool slot).

The idiomatic pattern is **one virtual thread per task**, unbounded in count, with concurrency to any *scarce downstream resource* controlled separately (see semaphores below) — not by limiting how many virtual threads may exist.

```java
// Anti-pattern: don't do this — defeats the purpose of virtual threads
ExecutorService fakeVirtualPool = new ThreadPoolExecutor(
        200, 200, 0L, TimeUnit.MILLISECONDS, new LinkedBlockingQueue<>(),
        Thread.ofVirtual().factory()); // pooling virtual threads: pointless and limiting

// Idiomatic: unbounded virtual-thread-per-task
ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
```

### `ThreadLocal` at Millions of Threads

`ThreadLocal` was designed under the assumption that thread counts are small (dozens to low thousands) and thread lifetimes are long (pooled and reused), so the per-thread storage cost was negligible and cheap to amortize. Virtual threads invert both assumptions: there can be millions of them, and each is typically short-lived (one per task/request).

Every `ThreadLocal` variable set on a virtual thread still needs its own storage slot on *that* thread, and Java copies `ThreadLocal` values into `InheritableThreadLocal` children on thread creation. At scale:

- Memory cost multiplies by however many concurrent virtual threads exist — a `ThreadLocal<byte[]>` holding even a modest buffer, times a million virtual threads, is a real amount of heap.
- `InheritableThreadLocal` inheritance means every child virtual thread created (e.g., inside a `StructuredTaskScope`) does a copy/snapshot of the parent's inheritable locals, which is wasted work if most children never read them.
- `ThreadLocal` is **mutable** and has **no defined lifetime bound** — nothing forces you to call `remove()`, so leaked `ThreadLocal` entries silently retain memory (and, in pooled-thread contexts, correctness bugs) until the thread itself is discarded. With millions of short-lived virtual threads this is somewhat self-limiting (thread death frees the map), but the per-thread map allocation and lookup overhead still adds up.

This is exactly the gap that **Scoped Values** (later in this chapter) are designed to close.

### Semaphores Instead of Thread Pools for Limiting Concurrency

If the goal is not to bound *how many virtual threads exist* but to bound *how many concurrent requests hit a specific downstream resource* (a database connection pool, a rate-limited third-party API, a fixed-capacity in-memory cache), the correct tool is a `Semaphore`, acquired around just the call to that resource — not a bounded executor that throttles everything indiscriminately.

```java
// Bound concurrent calls to a downstream payment gateway to 50, while still
// allowing unlimited virtual threads for everything else the request does.
private final Semaphore paymentGatewayLimiter = new Semaphore(50);

String chargeCard(Order order) throws InterruptedException {
    paymentGatewayLimiter.acquire();
    try {
        return paymentGateway.charge(order); // blocking call, virtual-thread friendly
    } finally {
        paymentGatewayLimiter.release();
    }
}
```

Each caller still runs on its own virtual thread and can make unrelated progress (or block cheaply) while waiting for a permit; only access to the *specific scarce resource* is serialized to the configured limit. This decouples "how many logical tasks can be in flight" (effectively unbounded, one virtual thread each) from "how much load a specific downstream can take" (bounded by the semaphore).

### Observability and Thread Dumps

Because a JVM might now host millions of virtual threads, traditional `jstack`-style dumps (designed for thousands of platform threads) don't scale well as plain text. JDK 21+ added a dedicated, structured thread dump command:

```bash
jcmd <pid> Thread.dump_to_file -format=json threads.json
```

This produces a JSON dump that groups virtual threads by which carrier thread (if any) they're currently mounted on, distinguishes platform threads from virtual threads, and is designed to be machine-readable so tooling can visualize or query it (rather than grepping raw text). A plain-text variant is also available:

```bash
jcmd <pid> Thread.dump_to_file -format=text threads.txt
```

For live, low-level thread dumps of a *specific* thread, `Thread.dump()`-style diagnostics and JFR's `jdk.ThreadDump` and `jdk.VirtualThreadPinned`/`jdk.VirtualThreadSubmitFailed` events fill in the rest of the observability story: pinning (above), submission failures (e.g., carrier pool exhaustion under a custom scheduler), and thread lifecycle events are all available for continuous monitoring via JFR rather than one-off dumps.

### Migration Advice

- **Start at I/O boundaries.** The highest-value, lowest-risk place to introduce virtual threads is swapping a fixed-size platform-thread `ExecutorService` used for blocking I/O (handling requests, calling downstream services) for `Executors.newVirtualThreadPerTaskExecutor()`. Frameworks (Tomcat, Jetty, Spring Boot 3.2+) increasingly support flipping this via configuration rather than hand-written executor code.
- **Audit `synchronized` usage on JDK 21-23.** Use `-Djdk.tracePinnedThreads=full` in a load test or staging environment before rollout; replace hot, blocking `synchronized` blocks with `ReentrantLock`. This becomes far less urgent on JDK 24+ thanks to JEP 491, but native-frame pinning (JNI) can still occur on any version.
- **Remove artificial concurrency caps that were sized around thread cost.** A `newFixedThreadPool(200)` sized because "200 platform threads is about all we can afford" should usually become unbounded virtual threads plus a `Semaphore` around the *actual* scarce resource (see above) — don't just swap the factory and keep the artificial cap, or you gain nothing.
- **Don't rewrite CPU-bound pools.** Leave `ForkJoinPool`/fixed platform-thread pools alone for CPU-bound work; virtual threads are not a general replacement for all concurrency.
- **Watch `ThreadLocal`-heavy legacy code**, especially framework code (security contexts, MDC/logging context, transaction contexts) that relies on `InheritableThreadLocal`. At virtual-thread scale, either confirm the library has been updated to use scoped values / lightweight inheritance, or measure the actual overhead before assuming it's fine.
- **Re-baseline connection pool sizing.** Virtual threads can generate far more concurrent *logical* requests than before; connection pools (DB, HTTP) sized for a small number of platform threads may need semaphore-style gating even if they were "big enough" previously, because now thousands of virtual threads can genuinely try to use them at once.

## Structured Concurrency

### The Problem with Unstructured Fan-Out

A common pattern is: submit several tasks to an `ExecutorService`, then collect their results with `Future.get()`. This works for the happy path, but it has real structural gaps:

```java
// Unstructured fan-out — looks fine, has several latent bugs
ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
Future<User> userFuture = executor.submit(() -> fetchUser(userId));
Future<Order> orderFuture = executor.submit(() -> fetchOrder(orderId));

User user = userFuture.get();   // if this throws, orderFuture keeps running —
Order order = orderFuture.get(); // an orphaned task, nobody waits for or cancels it
```

Concretely:

- **Leaked/orphan tasks.** If `userFuture.get()` throws, execution never reaches `orderFuture.get()`. The order-fetching task keeps running in the background — consuming a thread, a downstream connection, CPU — with nothing watching it, joining it, or cancelling it. In a request-handling context this is a silent resource leak that repeats on every failure.
- **No automatic cancellation propagation.** If one subtask fails, the *others* should usually be cancelled (why keep computing a result nobody will use?), but plain futures don't do this for you — you have to remember to call `cancel()` on every sibling, in every error path, including exceptions thrown from unrelated code between submissions.
- **Unclear ownership and lifetime.** Nothing in the code enforces that all child tasks finish (or are cancelled) before the enclosing method returns. This violates the intuitive nesting of blocks in sequential code, where a child scope's lifetime is visibly bounded by `{ }`. Concurrent tasks submitted to a shared executor have no such visible boundary — they can outlive the method that created them, which makes reasoning about cancellation, exception propagation, and shutdown far harder than for straight-line code.

**Structured concurrency** fixes this by treating a group of related concurrent subtasks as a single unit of work: they are forked from one *scope*, and that scope cannot be exited (the `try`-block cannot complete) until all of its forked subtasks have completed, failed, or been cancelled. The concurrent code gets the same nesting and lifetime guarantees as sequential code — a child task's lifetime is strictly contained within its parent's block.

### `StructuredTaskScope` Basics

The core type is `java.util.concurrent.StructuredTaskScope`, used in a try-with-resources block:

```java
try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
    Subtask<User> userTask = scope.fork(() -> fetchUser(userId));
    Subtask<Order> orderTask = scope.fork(() -> fetchOrder(orderId));

    scope.join();            // wait for both forks to finish (or one to fail)
    scope.throwIfFailed();   // if either failed, rethrow that failure here

    // Both succeeded — safe to read results
    User user = userTask.get();
    Order order = orderTask.get();
    return new Profile(user, order);
} // scope.close() guarantees no forked subtask survives past this point
```

- **`fork(Callable<T>)`** starts a subtask (on its own virtual thread by default) and returns a `Subtask<T>` handle.
- **`join()`** blocks until all forked subtasks finish, or (for policy-aware scopes like `ShutdownOnFailure`) until the scope's shutdown condition triggers.
- **`throwIfFailed()`** (on `ShutdownOnFailure`) rethrows the first captured failure, wrapped, if any subtask failed — this is what makes error propagation explicit and centralized instead of scattered across each `Future.get()` call.
- **`close()`**, called automatically by try-with-resources, **is the structural guarantee**: it waits for any still-running subtasks to finish and, in later API shapes, cancels/interrupts them if the scope is exiting due to an exception. You cannot leave the `try` block with orphaned children — that's the whole point.

### `ShutdownOnFailure` — All-Must-Succeed

`ShutdownOnFailure` implements the common "fan out, and if *any* subtask fails, cancel the rest and fail fast" policy. This is the right shape when you need every result to build a combined response — e.g., assembling a page that needs a user profile *and* their recent orders *and* their loyalty status; if any one lookup fails, the whole response is incomplete and there's no point waiting for (or paying for) the others.

```java
record DashboardData(User user, List<Order> orders, LoyaltyStatus loyalty) {}

DashboardData loadDashboard(String userId) throws InterruptedException, ExecutionException {
    try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
        Subtask<User> userTask = scope.fork(() -> userService.fetch(userId));
        Subtask<List<Order>> ordersTask = scope.fork(() -> orderService.recentOrders(userId));
        Subtask<LoyaltyStatus> loyaltyTask = scope.fork(() -> loyaltyService.status(userId));

        scope.join();
        scope.throwIfFailed(ExecutionException::new); // custom exception wrapper

        return new DashboardData(userTask.get(), ordersTask.get(), loyaltyTask.get());
    }
    // If loyaltyService.status() throws, ShutdownOnFailure immediately signals
    // shutdown: userTask and ordersTask are cancelled/interrupted if still
    // running, join() returns promptly instead of waiting for them to finish
    // naturally, and throwIfFailed() rethrows the loyalty failure.
}
```

### `ShutdownOnSuccess` — First-Wins

`ShutdownOnSuccess` implements the opposite policy: race several subtasks and take the *first* one to succeed, cancelling the rest. This is the natural shape for redundant lookups — e.g., querying a primary and a fallback cache/region simultaneously and using whichever answers first, or trying multiple mirrors for the same resource.

```java
String fetchFromFastestMirror(String key) throws InterruptedException, ExecutionException {
    try (var scope = new StructuredTaskScope.ShutdownOnSuccess<String>()) {
        scope.fork(() -> primaryRegionCache.get(key));
        scope.fork(() -> secondaryRegionCache.get(key));
        scope.fork(() -> tertiaryRegionCache.get(key));

        scope.join();
        return scope.result(); // returns the first success; throws if all failed
        // As soon as one fork succeeds, ShutdownOnSuccess cancels the other
        // in-flight forks — no wasted work, no orphaned background lookups.
    }
}
```

### Timeouts via `joinUntil`

Instead of (or in addition to) `join()`, `joinUntil(Instant deadline)` waits only until a deadline, throwing `TimeoutException` if the subtasks haven't finished by then — and, combined with a shutdown-triggering policy, still ensures nothing is left running past the scope's `close()`.

```java
DashboardData loadDashboardWithTimeout(String userId)
        throws InterruptedException, ExecutionException, TimeoutException {
    try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
        Subtask<User> userTask = scope.fork(() -> userService.fetch(userId));
        Subtask<List<Order>> ordersTask = scope.fork(() -> orderService.recentOrders(userId));

        scope.joinUntil(Instant.now().plusSeconds(2)); // give downstreams 2s total
        scope.throwIfFailed();

        return new DashboardData(userTask.get(), ordersTask.get(), null);
    } // close() cancels anything still running if joinUntil timed out
}
```

### Custom Scope Policies

Both `ShutdownOnFailure` and `ShutdownOnSuccess` are built on the same extensible mechanism (overriding `handleComplete` in earlier preview shapes, or supplying a custom `Joiner` in the JDK 25 shape — see below). This lets you express policies neither built-in covers, such as "succeed once at least N of M subtasks succeed" (a quorum) or "collect all results, ignoring individual failures, and report which ones failed at the end" instead of failing fast.

```java
// Sketch of a quorum policy (conceptual; exact base API differs by JDK version) —
// wait for at least 2 of 3 replica writes to succeed before returning.
try (var scope = new QuorumScope<Boolean>(/* required successes */ 2)) {
    scope.fork(() -> replicaA.write(record));
    scope.fork(() -> replicaB.write(record));
    scope.fork(() -> replicaC.write(record));

    scope.join();
    if (!scope.quorumReached()) {
        throw new WriteFailedException("Could not reach write quorum for " + record.id());
    }
}
```

The exact base class/interface for building custom policies changed between the preview iterations and the JDK 25 API — treat the sketch above as illustrating the *idea* (a policy decides, as subtasks complete, whether the scope should shut down early and what the overall outcome is), not as a copy-paste API.

### Preview Status and the JDK 25 API Reshape

Structured concurrency is one of the more actively evolving Loom features, and its exact shape has changed across releases — this is a real trap for anyone reciting API details from memory in an interview:

- **JDK 21**: `StructuredTaskScope` introduced as a **preview API** (JEP 453), requiring `--enable-preview` at both compile and run time. Shape: `new StructuredTaskScope.ShutdownOnFailure()` / `ShutdownOnSuccess<T>()`, `fork`, `join`, `throwIfFailed`, `joinUntil` — as shown in the examples above.
- **JDK 22-24**: Continued as a **preview API** (re-previewed, JEP 480/499), with refinements but the same overall `ShutdownOnFailure`/`ShutdownOnSuccess` subclassing shape. Still requires `--enable-preview`.
- **JDK 25**: The API was **reshaped** around an explicit **`Joiner`** abstraction (finalized under JEP 505 as a preview-to-standard evolution track; check the exact JEP number against your JDK's release notes, as this has moved during the feature's preview lifetime). Instead of subclassing `ShutdownOnFailure`/`ShutdownOnSuccess`, you open a scope with a joiner strategy:

```java
// JDK 25-style shape (illustrative) — StructuredTaskScope.open(...) with a Joiner
try (var scope = StructuredTaskScope.open(Joiner.<String>awaitAllSuccessfulOrThrow())) {
    scope.fork(() -> userService.fetch(userId));
    scope.fork(() -> orderService.recentOrders(userId));
    List<Subtask<Object>> results = scope.join();
    // ...
}

// First-success equivalent to the old ShutdownOnSuccess
try (var scope = StructuredTaskScope.open(Joiner.<String>anySuccessfulResultOrThrow())) {
    scope.fork(() -> primaryRegionCache.get(key));
    scope.fork(() -> secondaryRegionCache.get(key));
    String result = scope.join();
}
```

The `Joiner` shape separates "how subtasks are combined/what triggers shutdown" (the `Joiner`) from "the scope mechanics" (`open`, `fork`, `join`), making custom policies a matter of implementing `Joiner` rather than subclassing `StructuredTaskScope`. **As of JDK 25, structured concurrency is still a preview feature** — `--enable-preview` is required to compile and run code using it, on every JDK version mentioned above, including 25. Do not present it as final/GA without checking the release notes of whatever JDK version is actually in play; at the time of writing it has not exited preview.

```bash
# Compiling and running structured concurrency code on JDK 21 or JDK 25
javac --release 21 --enable-preview Main.java
java --enable-preview Main
```

## Scoped Values

### Why `ThreadLocal` Is a Poor Fit for Virtual Threads

`ThreadLocal` gives each thread its own independent copy of a variable, which is a fine model when threads are few and long-lived. Three specific properties make it a poor fit once threads are cheap, numerous, and short-lived (i.e., virtual threads):

1. **Mutability.** `ThreadLocal.set(...)` can be called at any time, by any code that has a reference to the `ThreadLocal`, changing the value for the rest of the thread's lifetime (or until the next `set`/`remove`). This makes it hard to reason about what value is "in scope" at a given point — any called method could have mutated it. It also opens the door to values leaking between logical tasks if a thread is reused (a classic thread-pool bug) or forgotten to be cleared.
2. **Unbounded lifetime.** Nothing forces a `ThreadLocal` value to be cleared when the logical operation that set it is done; you must remember to call `remove()`, typically in a `finally` block, or the value (and whatever memory it retains) lives until the thread itself dies or is reused. Since there is no compiler or runtime enforcement, forgetting to clean up is a common, hard-to-spot leak.
3. **Inheritance cost.** `InheritableThreadLocal` copies parent values into every child thread at creation time. With platform thread pools this happens rarely (pools don't spawn new threads often). With virtual threads — where creating one per task, and forking many children per `StructuredTaskScope`, is the *normal* pattern — this copying happens constantly, at a scale (potentially millions of threads) where even a cheap copy operation adds up, and where most children may never actually read the inherited value.

### `ScopedValue` — Immutable, Bounded, Structured

`ScopedValue<T>` (introduced as a preview feature, JEP 429 in JDK 21, continuing through subsequent JDKs as a preview feature with refinements — check `--enable-preview` requirements per JDK) addresses all three issues by design:

- **Immutable within a scope.** Once bound via `where(...).run(...)`, the value cannot be changed from inside that block — there is no `set()` method. Rebinding is only possible by opening a *new*, nested `where(...)` scope (see below), which is visible and structured, not a silent mutation.
- **Bounded lifetime.** The binding exists only for the duration of the `run`/`call` invocation — it is automatically and unconditionally torn down when that method returns, including via exception. There's no `remove()` to forget.
- **Cheap, structured inheritance.** When a `ScopedValue`-bound thread forks children through a `StructuredTaskScope`, the binding is available to those children without a per-child copy — the runtime shares the immutable binding rather than duplicating it, which is both cheaper and safer at virtual-thread scale.

```java
public class RequestContext {
    private static final ScopedValue<String> REQUEST_ID = ScopedValue.newInstance();

    public static void handleRequest(String requestId, HttpExchange exchange) {
        ScopedValue.where(REQUEST_ID, requestId)
                   .run(() -> processRequest(exchange));
        // REQUEST_ID is unbound again as soon as run() returns — nothing to clean up
    }

    static void processRequest(HttpExchange exchange) {
        log.info("Handling request {}", REQUEST_ID.get()); // reads the bound value
        callDownstream(); // REQUEST_ID stays bound for the whole call tree beneath run()
    }

    static void callDownstream() {
        log.info("Still request {}", REQUEST_ID.get()); // visible several frames down
    }
}
```

`where(...).call(...)` is the equivalent for code that returns a value or throws a checked exception:

```java
String result = ScopedValue.where(REQUEST_ID, requestId)
                            .call(() -> fetchAndFormat(exchange));
```

### Rebinding

Because bindings are scoped to a block, "changing" a value for a nested piece of work means opening a new `where(...)` inside the existing one — the outer binding is restored automatically once the inner block exits:

```java
ScopedValue.where(REQUEST_ID, "req-1").run(() -> {
    log.info(REQUEST_ID.get()); // "req-1"

    ScopedValue.where(REQUEST_ID, "req-1-retry").run(() -> {
        log.info(REQUEST_ID.get()); // "req-1-retry" — rebound for this inner block only
    });

    log.info(REQUEST_ID.get()); // back to "req-1" — outer binding restored
});
```

### Inheritance into `StructuredTaskScope` Forks

`ScopedValue` bindings established before a `StructuredTaskScope` is opened are visible inside subtasks forked from that scope, without any explicit copying code — this is the natural pairing of the two features: bind request-scoped context once, fan out safely, and every forked subtask (and anything it calls) can read that context.

```java
ScopedValue<String> REQUEST_ID = ScopedValue.newInstance();

DashboardData loadDashboard(String userId, String requestId)
        throws InterruptedException, ExecutionException {
    return ScopedValue.where(REQUEST_ID, requestId).call(() -> {
        try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
            // Both forked subtasks can call REQUEST_ID.get() and see "requestId",
            // with no manual propagation and no InheritableThreadLocal copy cost.
            Subtask<User> userTask = scope.fork(() -> {
                log.info("[{}] fetching user", REQUEST_ID.get());
                return userService.fetch(userId);
            });
            Subtask<List<Order>> ordersTask = scope.fork(() -> {
                log.info("[{}] fetching orders", REQUEST_ID.get());
                return orderService.recentOrders(userId);
            });

            scope.join();
            scope.throwIfFailed();
            return new DashboardData(userTask.get(), ordersTask.get(), null);
        }
    });
}
```

### Preview Status

`ScopedValue` has been a **preview API since JDK 21** (JEP 429) and remains preview through the JDK versions covered by this chapter, refined along the way (JEP 481/506 in later releases — check the exact JEP number for the JDK you are targeting, since, like structured concurrency, this feature's finalization timeline has shifted release to release). `--enable-preview` is required to compile and run code that uses it, on every affected JDK version. Do not assume it is final without checking your specific JDK's release notes.

### Before/After: Migrating from `ThreadLocal`

```java
// BEFORE — ThreadLocal: mutable, must remember to clean up, costly to inherit
// into children at virtual-thread scale.
public class TenantContext {
    private static final ThreadLocal<String> TENANT_ID = new ThreadLocal<>();

    public static void handle(String tenantId, Runnable task) {
        TENANT_ID.set(tenantId);
        try {
            task.run();
        } finally {
            TENANT_ID.remove(); // easy to forget; leaks the value if omitted
        }
    }

    public static String currentTenant() {
        return TENANT_ID.get();
    }
}

// AFTER — ScopedValue: immutable for the duration of run(), automatically
// unbound on exit (even via exception), cheap to share with forked subtasks.
public class TenantContext {
    private static final ScopedValue<String> TENANT_ID = ScopedValue.newInstance();

    public static void handle(String tenantId, Runnable task) {
        ScopedValue.where(TENANT_ID, tenantId).run(task);
        // no finally, no remove() — unbinding is automatic and guaranteed
    }

    public static String currentTenant() {
        return TENANT_ID.orElse("unknown"); // safe default if unbound
    }
}
```

The `AFTER` version cannot leak a stale tenant into unrelated work on a reused thread (there's no `set()` outside the scope, and no thread reuse concern for one-virtual-thread-per-task code), cannot be forgotten to be cleaned up (there's no cleanup step at all), and shares its binding cheaply with any `StructuredTaskScope` forks started inside `task.run()`.

## Comparison Table: Platform Threads vs Virtual Threads

| Aspect | Platform Thread | Virtual Thread |
|---|---|---|
| Creation cost | High — OS-level thread creation (syscall), noticeable latency | Very low — heap allocation, no OS thread created per virtual thread |
| Stack | Fixed-size native stack (commonly 512 KB-1 MB), reserved upfront | Grows/shrinks on the heap as a continuation; starts small |
| Scheduling | OS scheduler, preemptive, across all platform threads on the machine | JVM scheduler (carrier-thread pool, typically ~one per core), cooperative unmount on blocking calls |
| Typical count | Hundreds to a few thousand before memory/scheduling cost becomes a problem | Hundreds of thousands to millions |
| Blocking behavior | Blocks the OS thread; that thread is unusable for anything else while blocked | Unmounts from its carrier on blocking I/O, freeing the carrier for other virtual threads (unless pinned) |
| Best use | CPU-bound work; long-lived worker/daemon threads; native-frame-heavy work | I/O-bound, high-concurrency, blocking-style code (thread-per-request servers, fan-out to downstreams) |
| Pooling | Yes — reuse is the point (`newFixedThreadPool`, etc.) | No — create one per task; never pool |

## Worked Example: Fan-Out to Three Downstream Calls, Three Ways

Scenario: a `ProfileService` needs to build a page that requires calling three independent downstream services — `UserService`, `OrderService`, and `RecommendationService` — and combining their results. All three calls are independent and can run concurrently; the page can only be built if all three succeed.

```java
record Profile(User user, List<Order> orders, List<Recommendation> recommendations) {}

interface UserService { User fetch(String userId) throws Exception; }
interface OrderService { List<Order> recentOrders(String userId) throws Exception; }
interface RecommendationService { List<Recommendation> recommendationsFor(String userId) throws Exception; }
```

### Version 1: Raw `ExecutorService` and `Future`

```java
class ExecutorServiceProfileService {
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
    private final UserService userService;
    private final OrderService orderService;
    private final RecommendationService recommendationService;

    Profile buildProfile(String userId) throws InterruptedException, ExecutionException {
        Future<User> userFuture = executor.submit(() -> userService.fetch(userId));
        Future<List<Order>> ordersFuture = executor.submit(() -> orderService.recentOrders(userId));
        Future<List<Recommendation>> recsFuture =
                executor.submit(() -> recommendationService.recommendationsFor(userId));

        try {
            // If userFuture.get() throws, ordersFuture and recsFuture keep running
            // unattended — we never cancel them. That's the unstructured-fan-out
            // problem in practice: no automatic cancellation, no bounded lifetime.
            User user = userFuture.get();
            List<Order> orders = ordersFuture.get();
            List<Recommendation> recs = recsFuture.get();
            return new Profile(user, orders, recs);
        } catch (ExecutionException e) {
            userFuture.cancel(true);   // must remember to do this manually,
            ordersFuture.cancel(true); // in every catch block, for every sibling —
            recsFuture.cancel(true);   // easy to omit, easy to get wrong under refactors
            throw e;
        }
    }
}
```

### Version 2: `CompletableFuture`

```java
class CompletableFutureProfileService {
    private final Executor executor = Executors.newVirtualThreadPerTaskExecutor();
    private final UserService userService;
    private final OrderService orderService;
    private final RecommendationService recommendationService;

    Profile buildProfile(String userId) throws ExecutionException, InterruptedException {
        CompletableFuture<User> userFuture =
                CompletableFuture.supplyAsync(() -> uncheck(() -> userService.fetch(userId)), executor);
        CompletableFuture<List<Order>> ordersFuture =
                CompletableFuture.supplyAsync(() -> uncheck(() -> orderService.recentOrders(userId)), executor);
        CompletableFuture<List<Recommendation>> recsFuture =
                CompletableFuture.supplyAsync(
                        () -> uncheck(() -> recommendationService.recommendationsFor(userId)), executor);

        // allOf composes cancellation-unaware futures; if one fails, the others
        // are NOT automatically cancelled by allOf — you'd need to chain
        // .exceptionally / .whenComplete on each one yourself to cancel siblings,
        // which most CompletableFuture code in the wild does not bother doing.
        return CompletableFuture.allOf(userFuture, ordersFuture, recsFuture)
                .thenApply(v -> new Profile(userFuture.join(), ordersFuture.join(), recsFuture.join()))
                .get();
    }

    private static <T> T uncheck(Callable<T> callable) {
        try {
            return callable.call();
        } catch (Exception e) {
            throw new CompletionException(e);
        }
    }
}
```

This is more composable than raw futures (chaining, combining, async pipelines) but the exception/cancellation story is still manual and easy to get subtly wrong — `allOf` waits for all of them regardless of failure, and nothing cancels the still-running siblings when one fails unless you add that logic yourself.

### Version 3: `StructuredTaskScope`

```java
class StructuredProfileService {
    private final UserService userService;
    private final OrderService orderService;
    private final RecommendationService recommendationService;

    Profile buildProfile(String userId) throws InterruptedException, ExecutionException {
        try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
            Subtask<User> userTask = scope.fork(() -> userService.fetch(userId));
            Subtask<List<Order>> ordersTask = scope.fork(() -> orderService.recentOrders(userId));
            Subtask<List<Recommendation>> recsTask =
                    scope.fork(() -> recommendationService.recommendationsFor(userId));

            scope.join();
            scope.throwIfFailed(); // rethrows the first failure; other subtasks are
                                    // already cancelled by ShutdownOnFailure by this point

            return new Profile(userTask.get(), ordersTask.get(), recsTask.get());
        } // close() guarantees zero orphaned subtasks, success or failure
    }
}
```

**Comparison:** all three versions run the three downstream calls concurrently and produce the same result on the happy path. They diverge sharply on the failure path: Version 1 requires you to remember manual cancellation in every catch block; Version 2's `allOf` does not cancel siblings on failure unless you add explicit `exceptionally`/`whenComplete` wiring per future; Version 3 gets fail-fast cancellation of siblings and a guaranteed-no-orphans lifetime *for free*, as a structural property of the scope, with the least code and the clearest error-handling story. This is why structured concurrency, despite still being preview, is the direction the language is pushing fan-out/fan-in code toward.

## Common Code-Review Interview Pitfalls

1. **Pooling virtual threads (`newFixedThreadPool` with a virtual thread factory).**
   Why it matters: pooling exists to amortize the cost of expensive platform threads and to bound concurrency; virtual threads are cheap to create, so pooling them buys nothing and reintroduces the same concurrency ceiling virtual threads were meant to remove.
   ```java
   // Before
   new ThreadPoolExecutor(200, 200, 0, TimeUnit.SECONDS, queue, Thread.ofVirtual().factory());
   // After
   Executors.newVirtualThreadPerTaskExecutor();
   ```

2. **Using virtual threads for CPU-bound work and expecting a speedup.**
   Why it matters: virtual threads only help when threads spend time *blocked*; CPU-bound work occupies its carrier thread the whole time, so a million CPU-bound virtual threads run no faster than a small platform-thread pool sized to the core count — and adds bookkeeping overhead for no benefit.
   ```java
   // Before
   try (var ex = Executors.newVirtualThreadPerTaskExecutor()) {
       for (var m : matrices) ex.submit(() -> multiply(m)); // pure CPU work
   }
   // After
   try (var ex = Executors.newFixedThreadPool(Runtime.getRuntime().availableProcessors())) {
       for (var m : matrices) ex.submit(() -> multiply(m));
   }
   ```

3. **Blocking inside a `synchronized` block on JDK 21-23 without realizing it pins the carrier thread.**
   Why it matters: pinning ties up a carrier thread for the whole blocking call, effectively turning a cheap virtual thread back into an expensive scarce resource, and can silently reduce throughput under load until diagnosed with `-Djdk.tracePinnedThreads`.
   ```java
   // Before (JDK 21-23)
   synchronized (lock) { String r = callSlowService(); }
   // After
   lock.lock();
   try { String r = callSlowService(); } finally { lock.unlock(); }
   ```

4. **Claiming `synchronized` always pins virtual threads, regardless of JDK version.**
   Why it matters: JEP 491 (JDK 24) removed `synchronized`-block pinning; stating this as a universal, version-independent rule in a review or interview is factually outdated and signals stale knowledge. Native-frame pinning is still a concern on every version.
   ```
   // Before: "never use synchronized with virtual threads, period"
   // After: "synchronized pins carriers on JDK 21-23; JEP 491 (JDK 24+) fixed
   //         this for synchronized specifically — native-frame pinning remains"
   ```

5. **Using `ThreadLocal`/`InheritableThreadLocal` for per-request context at virtual-thread scale.**
   Why it matters: mutability plus unbounded lifetime plus per-child inheritance copying becomes expensive and leak-prone once there can be millions of short-lived threads; `ScopedValue` gives immutability, automatic unbinding, and cheap structured inheritance instead.
   ```java
   // Before
   private static final ThreadLocal<String> TENANT_ID = new ThreadLocal<>();
   // After
   private static final ScopedValue<String> TENANT_ID = ScopedValue.newInstance();
   ```

6. **Fan-out with raw `ExecutorService`/`Future` and no cancellation on the failure path.**
   Why it matters: when one `Future.get()` throws, sibling tasks keep running unattended unless you explicitly cancel every one of them in every catch block — a leak that's easy to introduce and easy to miss in review.
   ```java
   // Before
   User u = userFuture.get(); Order o = orderFuture.get(); // orderFuture orphaned if userFuture throws
   // After
   try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
       var uT = scope.fork(() -> fetchUser()); var oT = scope.fork(() -> fetchOrder());
       scope.join(); scope.throwIfFailed();
   }
   ```

7. **Assuming `CompletableFuture.allOf(...)` cancels siblings when one future fails.**
   Why it matters: `allOf` simply waits for every future regardless of outcome; it does not cancel anything on failure, so "fail fast, stop wasted work" requires extra manual wiring that's frequently omitted.
   ```java
   // Before: assumes cancellation happens automatically
   CompletableFuture.allOf(f1, f2, f3).get();
   // After: use StructuredTaskScope.ShutdownOnFailure for genuine fail-fast cancellation
   ```

8. **Forgetting `--enable-preview` (or claiming the feature is final) for structured concurrency / scoped values.**
   Why it matters: both remain preview APIs through JDK 25 at the time of writing; code that compiles for a reviewer without the flag, or a claim in an interview that they are GA, is simply incorrect and easy for an interviewer to catch.
   ```bash
   # Before
   javac Main.java   # fails: preview API used without --enable-preview
   # After
   javac --release 25 --enable-preview Main.java && java --enable-preview Main
   ```

9. **Writing `StructuredTaskScope` code against the wrong API shape for the target JDK (subclassing vs `Joiner`).**
   Why it matters: JDK 21-24 use `new StructuredTaskScope.ShutdownOnFailure()`-style subclassing; JDK 25 reshapes this around `StructuredTaskScope.open(Joiner...)`. Code (or an answer) that mixes the two shapes reveals unfamiliarity with the feature's actual evolution.
   ```java
   // JDK 21-24
   try (var scope = new StructuredTaskScope.ShutdownOnFailure()) { ... }
   // JDK 25
   try (var scope = StructuredTaskScope.open(Joiner.awaitAllSuccessfulOrThrow())) { ... }
   ```

10. **Not bounding concurrency to a genuinely scarce downstream resource just because virtual threads themselves are unbounded.**
    Why it matters: "virtual threads are free" does not mean the database, connection pool, or third-party API on the other end can handle unbounded concurrent calls; without a `Semaphore` (or similar) around the call site, unbounded virtual-thread fan-out can overwhelm a downstream that used to be implicitly protected by a small platform-thread pool.
    ```java
    // Before: every virtual thread hits the DB directly, no limit
    executor.submit(() -> db.query(sql));
    // After
    limiter.acquire();
    try { db.query(sql); } finally { limiter.release(); }
    ```

11. **Letting a `ScopedValue` binding's lifetime outlive the logical operation by binding too broadly (e.g., at application startup).**
    Why it matters: the safety benefit of `ScopedValue` comes from its binding being tightly scoped to one call tree; binding it once at a very high level (defeating the purpose) reintroduces the same "value visible everywhere, for too long" problem `ThreadLocal` had, just with extra ceremony.
    ```java
    // Before: bound once for the whole application run — no real scoping benefit
    ScopedValue.where(TENANT_ID, "default").run(Application::run);
    // After: bind per request, inside the request-handling method
    ScopedValue.where(TENANT_ID, tenantId).run(() -> handleRequest(exchange));
    ```

12. **Expecting a `StructuredTaskScope`'s forked subtasks to survive past the enclosing try-with-resources block.**
    Why it matters: `close()` enforces that no subtask outlives the scope — leaking a `Subtask` handle or its result outside the block, expecting it to keep running in the background, misunderstands the entire point of structured concurrency (bounded, visible lifetimes).
    ```java
    // Before: expecting background work to continue after the block
    Subtask<String> leaked;
    try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
        leaked = scope.fork(() -> longRunningJob());
    } // scope.close() already joined/cancelled it — it did not "keep running"
    // After: if it must genuinely outlive this method, don't put it in a scope —
    //        use a plain virtual thread or executor with its own lifecycle
    ```

13. **Reusing a single, wide `Semaphore` (or none at all) for unrelated downstream resources instead of one per resource.**
    Why it matters: a shared limiter conflates unrelated concurrency budgets — a burst of calls to a fast, high-capacity service can starve permits needed by a slow, low-capacity one (or vice versa) — undermining the whole point of per-resource throttling.
    ```java
    // Before: one limiter for every downstream call
    private final Semaphore anyDownstream = new Semaphore(100);
    // After: one limiter per distinct scarce resource
    private final Semaphore paymentGatewayLimiter = new Semaphore(50);
    private final Semaphore inventoryServiceLimiter = new Semaphore(200);
    ```

14. **Ignoring `InterruptedException` inside a forked subtask instead of letting cancellation propagate.**
    Why it matters: `StructuredTaskScope`'s cancellation (on `ShutdownOnFailure`/`ShutdownOnSuccess`) works by interrupting running subtasks; a subtask that swallows `InterruptedException` without restoring the flag or exiting defeats the scope's ability to actually stop wasted work when a sibling fails or wins.
    ```java
    // Before
    scope.fork(() -> { try { return slowCall(); } catch (InterruptedException e) { return retry(); } });
    // After
    scope.fork(() -> {
        try { return slowCall(); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); throw e; }
    });
    ```

15. **Diagnosing "too many threads" performance problems on a virtual-thread-heavy service with `jstack`-style plain-text dumps instead of the JSON format.**
    Why it matters: with hundreds of thousands of virtual threads, a flat text dump is impractical to analyze; `jcmd <pid> Thread.dump_to_file -format=json` produces a structured dump (grouped by carrier thread) intended for tooling, and JFR's `jdk.VirtualThreadPinned` event is the supported way to find pinning at scale rather than eyeballing text.
    ```bash
    # Before
    jstack <pid> > dump.txt   # unwieldy at hundreds of thousands of virtual threads
    # After
    jcmd <pid> Thread.dump_to_file -format=json threads.json
    ```
