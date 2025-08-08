# 14. Concurrency (Advanced)

Chapter 13 covered the low-level tools: threads, locks, `volatile`, and atomics. Those are the building blocks, but almost nobody writes raw `Thread` code in production anymore. This chapter covers the higher-level tools built on top of them — thread pools that run tasks for you, composable async pipelines, queues that coordinate producers and consumers, a map built for concurrent access, and synchronizers that make threads wait for each other in specific patterns. These are the tools that actually show up in day-to-day code and in code review. Examples target Java 21+; where a virtual-thread (Chapter 15) note changes the advice, it's called out.

## Table of Contents

- [Executors Framework](#executors-framework)
- [ForkJoinPool](#forkjoinpool)
- [CompletableFuture](#completablefuture)
- [BlockingQueue](#blockingqueue)
- [ConcurrentHashMap Internals](#concurrenthashmap-internals)
- [CountDownLatch](#countdownlatch)
- [CyclicBarrier](#cyclicbarrier)
- [Phaser](#phaser)
- [Semaphore](#semaphore)
- [Exchanger](#exchanger)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Executors Framework

Creating a new `Thread` for every unit of work is expensive and unbounded — nothing stops you from creating ten thousand threads and crashing the process. A **thread pool** is a manager that keeps a fixed set of reusable worker threads and hands them tasks from a queue. The **Executors Framework** is Java's standard toolkit for thread pools.

Three interfaces matter:

| Interface | Adds |
|---|---|
| `Executor` | Just one method: `execute(Runnable)`. Fire-and-forget. |
| `ExecutorService` | Adds `submit()` (returns a `Future`), lifecycle (`shutdown()`), and batch methods (`invokeAll`/`invokeAny`). |
| `ScheduledExecutorService` | Adds `schedule()`, `scheduleAtFixedRate()`, `scheduleWithFixedDelay()` — run tasks later or repeatedly. |

```java
import java.util.concurrent.*;

public class ExecutorBasics {
    public static void main(String[] args) throws InterruptedException {
        ExecutorService pool = Executors.newFixedThreadPool(4);
        pool.execute(() -> System.out.println("running on: " + Thread.currentThread().getName()));
        pool.shutdown();
    }
}
```

### `Executors` factory methods — and why two of them are risky

| Factory method | Pool shape | Queue | Risk |
|---|---|---|---|
| `newFixedThreadPool(n)` | Fixed `n` threads | **Unbounded** `LinkedBlockingQueue` | Tasks pile up forever if producers outrun consumers. No backpressure, memory grows until OOM. |
| `newCachedThreadPool()` | 0 core, **unbounded** max threads | `SynchronousQueue` (no storage) | No queueing at all — instead it just creates a new thread for every task that arrives while all existing threads are busy. Under load this can spawn thousands of threads, each with its own stack (~512KB–1MB), and the process runs out of memory or the OS runs out of native threads. |
| `newSingleThreadExecutor()` | 1 thread | Unbounded queue | Same unbounded-queue risk as fixed pool, just with one worker. |
| `newScheduledThreadPool(n)` | `n` threads | Unbounded delayed queue | Fine for scheduling, same queue caveat applies to backlog. |
| `newVirtualThreadPerTaskExecutor()` (Java 21) | One virtual thread per task, no pooling | N/A | Different tradeoffs entirely — see Chapter 15. |

The common thread: **both risky factories hide an unbounded resource** (queue or thread count) behind a friendly one-line call. In an interview or review, treat `Executors.newFixedThreadPool` and `Executors.newCachedThreadPool` as a yellow flag, not an automatic pass — ask "what happens when load exceeds capacity?"

### `ScheduledExecutorService`

Use this when work needs to run later, or repeatedly, instead of right now:

```java
import java.util.concurrent.*;

public class ScheduledJobs {
    public static void main(String[] args) {
        ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(2);

        // run once, after a delay
        scheduler.schedule(() -> System.out.println("ran once after delay"), 5, TimeUnit.SECONDS);

        // run repeatedly, every 10s, measured from the START of each run
        // (if a run takes longer than the period, the next run starts immediately after it finishes)
        scheduler.scheduleAtFixedRate(() -> pollForUpdates(), 0, 10, TimeUnit.SECONDS);

        // run repeatedly, with 10s of rest AFTER each run finishes before the next one starts
        scheduler.scheduleWithFixedDelay(() -> pollForUpdates(), 0, 10, TimeUnit.SECONDS);
    }

    private static void pollForUpdates() {
        System.out.println("polling at " + java.time.Instant.now());
    }
}
```

The difference between `scheduleAtFixedRate` and `scheduleWithFixedDelay` trips people up: fixed-rate measures the interval from the *start* of one run to the *start* of the next (so a slow task can cause back-to-back runs with no gap, or even overlap being queued up), while fixed-delay measures the interval from the *end* of one run to the *start* of the next (always leaves a real gap). If a scheduled task throws an uncaught exception, that task's future scheduled runs **stop silently** — always wrap the body in a try/catch that logs, so one bad run doesn't quietly kill all future polling.

### Building a `ThreadPoolExecutor` by hand

For anything that matters in production, construct a `ThreadPoolExecutor` explicitly so every limit is visible:

```java
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

public class BoundedPoolExample {
    public static void main(String[] args) {
        BlockingQueue<Runnable> boundedQueue = new ArrayBlockingQueue<>(200); // bounded! not unbounded

        ThreadFactory namedThreads = new ThreadFactory() {
            private final AtomicInteger counter = new AtomicInteger(1);
            @Override
            public Thread newThread(Runnable r) {
                Thread t = new Thread(r, "order-worker-" + counter.getAndIncrement());
                t.setDaemon(false);
                return t;
            }
        };

        ThreadPoolExecutor executor = new ThreadPoolExecutor(
                4,                                  // core pool size
                8,                                   // max pool size
                60L, TimeUnit.SECONDS,                // keep-alive for extra (non-core) threads
                boundedQueue,
                namedThreads,
                new ThreadPoolExecutor.CallerRunsPolicy() // rejection handler = backpressure
        );

        executor.execute(() -> System.out.println("handled by " + Thread.currentThread().getName()));
        executor.shutdown();
    }
}
```

### How core size, max size, and the queue interact

This order surprises a lot of people, so it's worth memorizing:

1. If fewer than `corePoolSize` threads exist, **a new thread is created** for the task — even if other core threads happen to be idle.
2. Once `corePoolSize` threads exist, new tasks go into the **queue** instead.
3. Only when the **queue is full** does the pool create additional threads, up to `maximumPoolSize`.
4. If the queue is full **and** `maximumPoolSize` is reached, the task is **rejected** — handled by the `RejectedExecutionHandler`.

This means with an *unbounded* queue, step 3 never triggers — `maximumPoolSize` is dead code, because the queue absorbs everything first. That's exactly why `newFixedThreadPool`'s extra threads (beyond the fixed count, since core == max there) never get created, and why `newCachedThreadPool`'s `SynchronousQueue` never queues — it forces step 3 immediately for every task past the core threads (core is 0), maximizing thread creation instead.

### `RejectedExecutionHandler` policies

| Policy | Behavior |
|---|---|
| `AbortPolicy` (default) | Throws `RejectedExecutionException`. |
| `CallerRunsPolicy` | Runs the task on the **calling thread** instead — naturally throttles the producer since it's now busy running the task instead of submitting more. |
| `DiscardPolicy` | Silently drops the task. Dangerous — hides failures. |
| `DiscardOldestPolicy` | Drops the oldest queued task, then retries submission. |

You can also implement your own `RejectedExecutionHandler` — for example, to log a metric before falling back to `CallerRunsPolicy`.

### `submit()` vs `execute()` — and the exception trap

```java
ExecutorService pool = Executors.newFixedThreadPool(2);

// execute(): fire-and-forget. An uncaught exception goes to the thread's
// UncaughtExceptionHandler (usually prints to stderr) — at least it's visible.
pool.execute(() -> { throw new RuntimeException("boom (execute)"); });

// submit(): returns a Future. The exception is CAPTURED inside the Future
// and only rethrown (wrapped in ExecutionException) when you call get().
Future<?> future = pool.submit(() -> { throw new RuntimeException("boom (submit)"); });
// If nobody ever calls future.get(), this exception disappears silently!

try {
    future.get();
} catch (ExecutionException e) {
    System.out.println("caught: " + e.getCause()); // this is how you actually find out
} catch (InterruptedException e) {
    Thread.currentThread().interrupt();
}
```

**Rule of thumb:** if you use `submit()`, you must eventually call `get()` (or check `isDone()`/`isCancelled()`) or handle the exception some other way — otherwise failures vanish.

### Two-phase shutdown

`shutdown()` is graceful: it stops accepting new tasks but lets queued and running tasks finish. `shutdownNow()` is aggressive: it interrupts running tasks and returns the list of tasks that were still waiting in the queue, so you can decide what to do with the lost work. The standard idiom combines both:

```java
public void shutdownAndAwait(ExecutorService pool) {
    pool.shutdown(); // phase 1: stop accepting new work, let existing work finish
    try {
        if (!pool.awaitTermination(30, TimeUnit.SECONDS)) {
            pool.shutdownNow(); // phase 2: didn't finish in time, force it
            if (!pool.awaitTermination(10, TimeUnit.SECONDS)) {
                System.err.println("pool did not terminate");
            }
        }
    } catch (InterruptedException e) {
        pool.shutdownNow(); // this thread was interrupted while waiting; force shutdown too
        Thread.currentThread().interrupt();
    }
}
```

### `invokeAll` and `invokeAny`

```java
ExecutorService pool = Executors.newFixedThreadPool(3);

List<Callable<Integer>> tasks = List.of(
        () -> { Thread.sleep(100); return 1; },
        () -> { Thread.sleep(50);  return 2; },
        () -> { Thread.sleep(200); return 3; }
);

// invokeAll: blocks until ALL tasks finish (or the timeout expires), returns a Future per task
List<Future<Integer>> results = pool.invokeAll(tasks, 5, TimeUnit.SECONDS);

// invokeAny: blocks until the FIRST task succeeds, cancels the rest, returns that value directly
Integer fastest = pool.invokeAny(tasks);
```

### `ExecutorService` as `AutoCloseable` (Java 19+)

Since JDK 19, `ExecutorService` extends `AutoCloseable`. `close()` calls `shutdown()`, waits, and escalates to `shutdownNow()` if needed — so try-with-resources gives you the two-phase idiom for free:

```java
try (ExecutorService pool = Executors.newFixedThreadPool(4)) {
    pool.submit(() -> System.out.println("work"));
} // close() called automatically: shutdown() + await, escalating to shutdownNow() if it hangs
```

### Sizing a pool: CPU-bound vs IO-bound

- **CPU-bound work** (tight loops, computation, no blocking): size the pool close to the number of CPU cores (`Runtime.getRuntime().availableProcessors()`, maybe `+1`). More threads than cores just adds context-switch overhead — the CPU is already saturated.
- **IO-bound work** (waiting on network calls, disk, database): threads spend most of their time *blocked*, not computing, so you need many more of them to keep the CPU busy while some threads wait. A common formula:

```
threads = cores * (1 + waitTime / computeTime)
```

If a task waits 90ms for a network call and computes for 10ms, that's a wait/compute ratio of 9, so on an 8-core box you'd want roughly `8 * (1 + 9) = 80` threads.

Note for Chapter 15: with **virtual threads**, this whole calculation mostly disappears for IO-bound work — you can have millions of cheap virtual threads blocked on I/O without tying up an OS thread each. The pool-sizing math above is still exactly right for CPU-bound work and for platform-thread pools.

## ForkJoinPool

A `ForkJoinPool` is a thread pool optimized for **divide-and-conquer** work: split a big task into smaller subtasks, run them in parallel, combine the results. It's the engine behind parallel streams and `CompletableFuture`'s default async methods.

### Work stealing

Each worker thread has its own double-ended queue (deque) of tasks. A thread pushes/pops its *own* tasks from one end (like a normal stack, cheap, no contention). When a thread runs out of work, it "steals" a task from the *other end* of some other busy thread's deque. This keeps all cores busy without a central lock that everyone contends on — the key reason `ForkJoinPool` scales better than a naive shared-queue pool for recursive work.

### `RecursiveTask` vs `RecursiveAction`

Both extend `ForkJoinTask` and require implementing `compute()`. `RecursiveTask<V>` returns a value; `RecursiveAction` returns nothing (pure side effect).

```java
import java.util.concurrent.RecursiveTask;
import java.util.concurrent.ForkJoinPool;

public class ParallelSum extends RecursiveTask<Long> {
    private static final int THRESHOLD = 10_000; // tune based on benchmarking
    private final long[] array;
    private final int start, end;

    ParallelSum(long[] array, int start, int end) {
        this.array = array; this.start = start; this.end = end;
    }

    @Override
    protected Long compute() {
        int length = end - start;
        if (length <= THRESHOLD) {
            long sum = 0;
            for (int i = start; i < end; i++) sum += array[i];
            return sum; // small enough: just compute directly, don't fork further
        }
        int mid = start + length / 2;
        ParallelSum left = new ParallelSum(array, start, mid);
        ParallelSum right = new ParallelSum(array, mid, end);
        left.fork();                 // run left asynchronously on another worker
        long rightResult = right.compute(); // compute right on THIS thread (avoid an extra fork)
        long leftResult = left.join();      // wait for left, combine
        return leftResult + rightResult;
    }

    public static void main(String[] args) {
        long[] data = new long[1_000_000];
        java.util.Arrays.fill(data, 1);
        ForkJoinPool pool = new ForkJoinPool(); // defaults to availableProcessors()
        long total = pool.invoke(new ParallelSum(data, 0, data.length));
        System.out.println(total); // 1000000
    }
}
```

**Picking the threshold matters.** Fork too aggressively (tiny tasks) and the overhead of creating/scheduling tasks dwarfs the actual work — slower than sequential. Fork too little (huge tasks) and you don't get enough parallelism. There's no universal number; a common starting point is "a few thousand basic operations per leaf task," then benchmark and adjust.

### The common pool

`ForkJoinPool.commonPool()` is a shared, JVM-wide pool created lazily on first use, sized by default to `availableProcessors() - 1`. Two big consumers use it *by default*:

- **Parallel streams** (`.parallelStream()`, `.parallel()`).
- **`CompletableFuture`**'s `*Async` methods, when you don't pass an explicit `Executor`.

Because it's shared, **blocking a common-pool thread hurts everyone** — an unrelated parallel stream elsewhere in the same JVM can stall because there simply aren't enough worker threads left. This is a real production hazard: a slow blocking call buried inside a `.parallelStream()` or a `thenApplyAsync` without an executor can silently degrade throughput app-wide.

You can size the common pool with the system property `-Djava.util.concurrent.ForkJoinPool.common.parallelism=N` at JVM startup, or avoid the shared pool entirely by creating your own `new ForkJoinPool(parallelism)` for work you want isolated from parallel streams and `CompletableFuture` defaults:

```java
ForkJoinPool dedicatedPool = new ForkJoinPool(4); // isolated from the common pool
dedicatedPool.submit(new ParallelSum(data, 0, data.length));
```

### `ManagedBlocker`

If a fork/join task genuinely must block (e.g., waiting on I/O), it should announce that via `ForkJoinPool.ManagedBlocker` so the pool can compensate by spinning up a replacement worker, keeping parallelism intact instead of just losing a thread to the block:

```java
import java.util.concurrent.ForkJoinPool;
import java.util.concurrent.locks.Lock;

public class LockBlocker implements ForkJoinPool.ManagedBlocker {
    private final Lock lock;
    private boolean acquired = false;

    LockBlocker(Lock lock) { this.lock = lock; }

    @Override
    public boolean block() {
        if (!acquired) acquired = lock.tryLock();
        return acquired;
    }

    @Override
    public boolean isReleasable() {
        return acquired || (acquired = lock.tryLock());
    }

    public static void useLock(Lock lock) throws InterruptedException {
        ForkJoinPool.managedBlock(new LockBlocker(lock));
        try {
            // critical section
        } finally {
            lock.unlock();
        }
    }
}
```

## CompletableFuture

`CompletableFuture<T>` is a `Future` you can **compose**: chain transformations, combine multiple futures, and react to completion or failure, all without manually calling blocking `get()` everywhere.

### Creating one

```java
import java.util.concurrent.*;

ExecutorService executor = Executors.newFixedThreadPool(4);

CompletableFuture<String> fromSupplier = CompletableFuture.supplyAsync(
        () -> fetchUserName(42), executor); // runs on the given executor, returns a value

CompletableFuture<Void> fromRunnable = CompletableFuture.runAsync(
        () -> System.out.println("side effect"), executor); // no return value

CompletableFuture<String> already = CompletableFuture.completedFuture("cached"); // no async work at all
```

### `thenApply` vs `thenCompose` vs `thenCombine`

```java
// thenApply: transform the result, like Optional.map / Stream.map
CompletableFuture<Integer> length = fromSupplier.thenApply(String::length);

// thenCompose: chain to ANOTHER CompletableFuture-returning step, like Optional.flatMap.
// Using thenApply here would give you a CompletableFuture<CompletableFuture<Address>> — wrong shape.
CompletableFuture<Address> address = fromSupplier.thenCompose(name -> lookupAddressAsync(name));

// thenCombine: combine two INDEPENDENT futures once both are done
CompletableFuture<String> userName = CompletableFuture.supplyAsync(() -> "Alice");
CompletableFuture<Integer> userAge = CompletableFuture.supplyAsync(() -> 30);
CompletableFuture<String> combined = userName.thenCombine(userAge,
        (name, age) -> name + " is " + age + " years old");
```

### `thenAccept` / `thenRun`

```java
fromSupplier.thenAccept(name -> System.out.println("got: " + name)); // consume result, no return value
fromSupplier.thenRun(() -> System.out.println("done"));              // ignore the result entirely
```

### The `*Async` variants — which thread runs what

Every `then*` method has a plain version and an `*Async` version (`thenApplyAsync`, `thenComposeAsync`, ...):

- **Plain** (`thenApply`): the continuation runs on whichever thread completed the *previous* stage. If the previous stage was already complete when you attach the continuation, it may even run on the **calling thread**, synchronously. This is subtle and easy to get wrong in reasoning about which thread does what.
- **`*Async` without an executor**: runs on `ForkJoinPool.commonPool()`.
- **`*Async` with an executor**: runs on the executor you pass — the version you want for anything that matters.

```java
CompletableFuture.supplyAsync(() -> "raw", executor)
        .thenApplyAsync(String::toUpperCase, executor) // explicit: runs on our pool, not the common pool
        .thenAccept(System.out::println);
```

### `allOf` / `anyOf`

```java
CompletableFuture<String> f1 = CompletableFuture.supplyAsync(() -> "a", executor);
CompletableFuture<String> f2 = CompletableFuture.supplyAsync(() -> "b", executor);
CompletableFuture<String> f3 = CompletableFuture.supplyAsync(() -> "c", executor);

// allOf: waits for every future; itself completes with Void — you collect results manually
CompletableFuture<Void> all = CompletableFuture.allOf(f1, f2, f3);
CompletableFuture<List<String>> collected = all.thenApply(v -> List.of(f1.join(), f2.join(), f3.join()));

// anyOf: completes as soon as ANY one completes; type is Object (loses specific type info)
CompletableFuture<Object> first = CompletableFuture.anyOf(f1, f2, f3);
```

### Error handling: `exceptionally` / `handle` / `whenComplete`

```java
CompletableFuture<Integer> risky = CompletableFuture.supplyAsync(() -> {
    if (Math.random() < 0.5) throw new RuntimeException("failed");
    return 42;
}, executor);

// exceptionally: only runs on failure, supplies a fallback VALUE, swallows the exception
risky.exceptionally(ex -> {
    System.out.println("recovered from: " + ex.getMessage());
    return -1;
});

// handle: runs on EITHER success or failure, gets both (result, exception) — one is always null
risky.handle((result, ex) -> ex != null ? -1 : result);

// whenComplete: side effect only, does NOT change the result, and re-throws the original exception
risky.whenComplete((result, ex) -> {
    if (ex != null) System.out.println("logging failure: " + ex);
    else System.out.println("logging success: " + result);
});
```

### Timeouts (Java 9+)

```java
CompletableFuture<String> slow = CompletableFuture.supplyAsync(() -> {
    sleepUninterruptibly(5000);
    return "late";
}, executor);

// orTimeout: completes EXCEPTIONALLY with a TimeoutException if not done in time
slow.orTimeout(1, TimeUnit.SECONDS);

// completeOnTimeout: completes with a FALLBACK VALUE instead of failing
slow.completeOnTimeout("default value", 1, TimeUnit.SECONDS);
```

### Always pass an explicit executor

Relying on the default (common pool) mixes your application's async work with parallel streams and any other library using the common pool. It also makes tests slower/flakier (shared global state) and hides real capacity limits. Prefer:

```java
CompletableFuture.supplyAsync(this::loadFromDatabase, ioExecutor)
        .thenApplyAsync(this::transform, cpuExecutor)
        .thenAcceptAsync(this::publish, ioExecutor);
```

### Why `.join()` inside a callback is dangerous

`.join()` (like `.get()`) **blocks the current thread**. If that current thread is itself a worker thread from a *bounded* pool (the common pool, or any fixed executor), and the future you're joining needs a worker thread from that *same* pool to complete, you can starve or deadlock the pool: all workers end up blocked waiting on each other, and none are left to actually run the work that would unblock them.

```java
// DANGEROUS: runs on the common pool, then blocks a common-pool thread waiting on
// another common-pool computation. Under load, this can starve the whole pool.
CompletableFuture.supplyAsync(() -> {
    CompletableFuture<String> inner = CompletableFuture.supplyAsync(() -> fetchRemote());
    return inner.join(); // blocking a pool worker while waiting for another pool worker
});

// BETTER: compose instead of blocking — no thread sits idle waiting
CompletableFuture.supplyAsync(() -> prepareRequest())
        .thenCompose(req -> fetchRemoteAsync(req)); // chained, nobody blocks
```

If you must block, use a separate, appropriately-sized executor dedicated to blocking work — never the common pool.

## BlockingQueue

A `BlockingQueue<E>` is a queue that can make the caller **wait**: `put()` waits if the queue is full, `take()` waits if it's empty. It's the standard tool for producer-consumer pipelines.

### The family

| Implementation | Bounded? | Ordering | Notes |
|---|---|---|---|
| `ArrayBlockingQueue` | Yes, fixed at creation | FIFO | Backed by an array; capacity can't change. |
| `LinkedBlockingQueue` | Optional (default unbounded = `Integer.MAX_VALUE`) | FIFO | Watch out — "unbounded by default" is a common trap. |
| `SynchronousQueue` | Zero capacity | N/A | No storage at all — a `put()` blocks until a `take()` is ready to receive it, and vice versa. Pure hand-off. |
| `PriorityBlockingQueue` | Unbounded | Priority order (`Comparable`/`Comparator`) | `put()` never blocks (unbounded); `take()` blocks if empty. |
| `DelayQueue` | Unbounded | By delay expiry | Elements implement `Delayed`; `take()` only returns an element once its delay has elapsed. |
| `LinkedTransferQueue` | Unbounded | FIFO | Adds `transfer()`: like `put()`, but waits until a consumer actually receives the element — combines queue and synchronous hand-off. |

### `put`/`take` vs `offer`/`poll` (with timeout) vs `add`/`remove`

```java
BlockingQueue<String> queue = new ArrayBlockingQueue<>(10);

queue.put("x");              // blocks forever if full
String taken = queue.take(); // blocks forever if empty

boolean added = queue.offer("y", 500, TimeUnit.MILLISECONDS); // waits up to 500ms, then gives up
String polled = queue.poll(500, TimeUnit.MILLISECONDS);       // same, for reading

queue.add("z");        // throws IllegalStateException immediately if full — no waiting
String r = queue.remove(); // throws NoSuchElementException immediately if empty — no waiting
```

Use `put`/`take` when waiting is fine (typical producer-consumer). Use `offer`/`poll` with a timeout when you need bounded patience (e.g., don't wait more than a second). Use `add`/`remove` only when you've already guaranteed capacity/non-emptiness and truly want a hard failure otherwise — rare in practice.

### Producer-consumer example

```java
import java.util.concurrent.*;

public class ProducerConsumer {
    private static final String POISON_PILL = "__STOP__";

    public static void main(String[] args) throws InterruptedException {
        BlockingQueue<String> queue = new ArrayBlockingQueue<>(50); // bounded = backpressure
        ExecutorService pool = Executors.newFixedThreadPool(2);

        Runnable producer = () -> {
            try {
                for (int i = 0; i < 100; i++) {
                    queue.put("job-" + i); // blocks if consumer falls behind — natural throttling
                }
                queue.put(POISON_PILL); // tell the consumer to stop
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        };

        Runnable consumer = () -> {
            try {
                while (true) {
                    String job = queue.take();
                    if (job.equals(POISON_PILL)) break; // sentinel: no more work coming
                    System.out.println("processing " + job);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        };

        pool.submit(producer);
        pool.submit(consumer);
        pool.shutdown();
        pool.awaitTermination(1, TimeUnit.MINUTES);
    }
}
```

**Poison pills** are sentinel values that mean "stop, there's no more work." With multiple consumers, you typically send one pill per consumer (or use a shared atomic "shutting down" flag) so every consumer thread gets the signal to exit.

### Bounded queues are backpressure

A bounded `BlockingQueue` is a **feedback mechanism**. When the queue fills up, `put()` blocks the producer — which slows it down to match the consumer's real speed. An unbounded queue removes that feedback: the producer never feels resistance, memory quietly grows, and the failure shows up later as an `OutOfMemoryError` far from its actual cause. Choosing a bounded queue is a deliberate design decision to fail fast (or throttle) instead of failing silently later.

## ConcurrentHashMap Internals

`ConcurrentHashMap` is a thread-safe map designed for **high concurrency**, not just thread safety. Unlike a synchronized `HashMap` (one lock for everything), it splits work so many threads can operate on different parts of the map at the same time.

### No full-table lock

There is no single lock guarding the whole map. Reads generally don't lock at all (volatile reads of table slots). Writes lock only what they need to.

### CAS on an empty bin

Inserting into an **empty bin** (bucket) uses a **CAS** (compare-and-swap) — a lock-free atomic operation — instead of taking a lock. This is the fast path: most insertions into a well-sized map hit empty or nearly-empty bins.

### Per-bin synchronization

When a bin already has entries (a collision, or an update to an existing key), the map synchronizes on that bin's **first node** only — not the whole table. Two threads working on different bins never contend with each other at all.

### Treeification

If a single bin's linked list grows past a threshold (8 nodes, and the table itself has at least 64 slots), that bin is converted into a small **red-black tree**, turning worst-case lookup from O(n) into O(log n) for that bin. It converts back to a linked list if the bin shrinks below 6 nodes. This is an internal defense against pathological hash collisions (e.g., poor `hashCode()` implementations or deliberately crafted keys) — you never interact with it directly, but it's worth knowing it exists.

### `size()` / `mappingCount()` are estimates

Because there's no global lock, there's no way to get a perfectly exact count under concurrent modification without stopping the world. `size()` and `mappingCount()` (the `long`-returning, Java 8+ preferred alternative) use a scalable striped counter internally and sum it up on demand — accurate as of *some* moment, but another thread could be mid-insert at the exact time you read it. Treat both as approximate under concurrent writes; don't use them for correctness-critical logic (e.g., "if size == 0 then...").

### Atomic `compute` / `computeIfAbsent` / `merge` / `putIfAbsent`

These perform an atomic read-modify-write for a single key, holding that key's bin lock for the duration of the supplied function:

```java
ConcurrentHashMap<String, Integer> counts = new ConcurrentHashMap<>();

// atomic increment — no lost updates even with many threads calling this concurrently
counts.compute("clicks", (key, current) -> current == null ? 1 : current + 1);

// only computes the value if the key is absent — common for caches
counts.computeIfAbsent("views", key -> expensiveInitialValue());

// merge: combine an existing value with a new one, or just insert if absent
counts.merge("clicks", 1, Integer::sum);

// putIfAbsent: insert only if key is not already present, atomically
counts.putIfAbsent("clicks", 0);
```

### The recursive-update deadlock hazard

The function you pass to `computeIfAbsent`/`compute`/`merge` runs **while the bin's lock is held**. If that function calls back into the *same map* — especially for a key that could hash to the same bin, or worse, the same key — you can deadlock (the thread tries to re-acquire a lock it already holds in a way that isn't reentrant for this purpose) or, since Java 9+, the map explicitly detects some of these cases and throws `IllegalStateException: Recursive update`.

```java
ConcurrentHashMap<String, Integer> map = new ConcurrentHashMap<>();

// DANGEROUS: the remapping function touches the SAME map it's being called from
map.computeIfAbsent("a", key -> {
    return map.computeIfAbsent("b", k2 -> 1); // may deadlock or throw IllegalStateException
});

// SAFER: never call back into the same map from inside compute/computeIfAbsent/merge.
// Compute the value independently first, then insert.
Integer bValue = map.computeIfAbsent("b", k2 -> 1);
map.computeIfAbsent("a", key -> bValue);
```

### Weakly-consistent iterators

Iterators over a `ConcurrentHashMap` (keys, values, entries) are **weakly consistent**: they never throw `ConcurrentModificationException`, they reflect the state of the map at some point at or since the iterator was created, and they're guaranteed not to return the same mapping twice — but they might or might not reflect an update made by another thread mid-iteration. This is different from `HashMap`'s fail-fast iterators, and it's exactly what you want for a map that's actively being written to by other threads while you iterate.

### Bulk operations: `forEach` / `search` / `reduce`

```java
ConcurrentHashMap<String, Integer> scores = new ConcurrentHashMap<>();
scores.put("alice", 90);
scores.put("bob", 75);
scores.put("carol", 88);

// parallelismThreshold: below this element count, runs sequentially; above it, may run in parallel
scores.forEach(1, (key, value) -> System.out.println(key + "=" + value));

String highScorer = scores.search(1, (key, value) -> value > 85 ? key : null);

int total = scores.reduce(1, (key, value) -> value, Integer::sum);
```

### `ConcurrentHashMap.newKeySet()`

A quick way to get a thread-safe `Set<E>` backed by a `ConcurrentHashMap<E, Boolean>` internally — simpler than wrapping with `Collections.newSetFromMap`:

```java
Set<String> activeSessions = ConcurrentHashMap.newKeySet();
activeSessions.add("session-123");
activeSessions.remove("session-456");
```

## CountDownLatch

A `CountDownLatch` lets one or more threads wait until a set of events has happened. It's initialized with a count; each event calls `countDown()`; waiting threads call `await()` and are released once the count hits zero. **It's one-shot** — once it reaches zero, it stays there forever. It cannot be reset or reused.

Typical use: a main thread waits for several background services or worker threads to finish starting up before continuing.

```java
import java.util.concurrent.CountDownLatch;

public class ServiceStartup {
    public static void main(String[] args) throws InterruptedException {
        int serviceCount = 3;
        CountDownLatch startupLatch = new CountDownLatch(serviceCount);

        for (int i = 1; i <= serviceCount; i++) {
            int id = i;
            new Thread(() -> {
                initializeService(id);
                System.out.println("service " + id + " ready");
                startupLatch.countDown(); // one fewer thing to wait for
            }).start();
        }

        startupLatch.await(); // blocks until all 3 have called countDown()
        System.out.println("all services ready, starting server");
    }

    private static void initializeService(int id) {
        try { Thread.sleep(100L * id); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
```

## CyclicBarrier

A `CyclicBarrier` makes a fixed number of threads (**parties**) all wait at a point until every one of them has arrived — then releases all of them at once. Unlike `CountDownLatch`, it's **reusable**: once everyone passes through, it automatically resets for the next round. It also supports an optional **barrier action** — a `Runnable` that runs once, on one of the arriving threads, right before the barrier releases everyone.

Typical use: a simulation or algorithm that runs in rounds, where every worker thread must finish round *N* before any of them can start round *N+1*.

```java
import java.util.concurrent.BrokenBarrierException;
import java.util.concurrent.CyclicBarrier;

public class SimulationRounds {
    public static void main(String[] args) {
        int workerCount = 4;
        CyclicBarrier barrier = new CyclicBarrier(workerCount,
                () -> System.out.println("--- round complete, advancing ---")); // barrier action

        for (int i = 1; i <= workerCount; i++) {
            int id = i;
            new Thread(() -> {
                for (int round = 1; round <= 3; round++) {
                    doRoundOfWork(id, round);
                    try {
                        barrier.await(); // wait here until all 4 workers finish this round
                    } catch (InterruptedException | BrokenBarrierException e) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }).start();
        }
    }

    private static void doRoundOfWork(int workerId, int round) {
        System.out.println("worker " + workerId + " doing round " + round);
    }
}
```

If a thread times out or is interrupted while waiting at the barrier, the barrier becomes **broken**: it throws `BrokenBarrierException` for every other thread currently waiting or that arrives later, and it stays broken until explicitly reset via `barrier.reset()`.

## Phaser

A `Phaser` is a more flexible generalization of both `CountDownLatch` and `CyclicBarrier`. The key difference: the number of parties can change dynamically at runtime — threads can `register()` to join and `arriveAndDeregister()` to leave, even mid-run. Each round is called a **phase**, and phases increment automatically as parties arrive.

Typical use: anything where the set of participants isn't fixed up front — e.g., a pool of workers that grows or shrinks between phases, or a multi-stage pipeline where different stages have different party counts.

```java
import java.util.concurrent.Phaser;

public class DynamicWorkers {
    public static void main(String[] args) {
        Phaser phaser = new Phaser(1); // 1 party: the main thread registers itself first

        for (int i = 1; i <= 3; i++) {
            int workerId = i;
            phaser.register(); // dynamically add a party for this new worker
            new Thread(() -> {
                for (int phase = 0; phase < 2; phase++) {
                    System.out.println("worker " + workerId + " working on phase " + phase);
                    phaser.arriveAndAwaitAdvance(); // arrive, then wait for the phase to advance
                }
                phaser.arriveAndDeregister(); // this worker is done, shrink the party count
            }).start();
        }

        phaser.arriveAndDeregister(); // main thread's initial registration is done too
    }
}
```

Phaser also supports **tiered** (hierarchical) structures for very large numbers of parties, and you can override `onAdvance(phase, registeredParties)` to control termination — returning `true` from it terminates the phaser after that phase.

### Latch vs Barrier vs Phaser

| | `CountDownLatch` | `CyclicBarrier` | `Phaser` |
|---|---|---|---|
| Reusable? | No — one-shot | Yes — resets automatically each round | Yes — advances through phases |
| Party count fixed? | Fixed at creation (the initial count) | Fixed at creation | **Dynamic** — register/deregister at runtime |
| Who signals? | Any thread can call `countDown()`, independent of `await()` | Only the parties themselves, via `await()` | Only the parties themselves, via `arrive*()` |
| Optional action on completion? | No | Yes, one barrier action | Yes, override `onAdvance()` |
| Typical use | Wait for N independent events (startup, N tasks done) | Wait for N *same* threads to reach a sync point each round | Multi-phase work with a changing number of participants |

## Semaphore

A `Semaphore` guards access to a limited number of **permits** — think of it as a bouncer letting only N people into a room at a time. Unlike a lock, a semaphore has **no ownership**: any thread can `release()` a permit, not just the thread that `acquire()`d it. That makes it useful for resource pools where the "returning" thread isn't necessarily the "borrowing" thread.

Typical use: limiting how many concurrent requests hit a downstream service, or guarding a fixed-size pool of expensive resources (e.g., database connections).

```java
import java.util.concurrent.Semaphore;

public class RateLimitedClient {
    private final Semaphore permits = new Semaphore(5); // at most 5 concurrent calls

    public String callExternalApi(String request) throws InterruptedException {
        permits.acquire(); // blocks if 5 calls are already in flight
        try {
            return doHttpCall(request);
        } finally {
            permits.release(); // always release, even on exception — hence try/finally
        }
    }

    private String doHttpCall(String request) {
        return "response for " + request;
    }
}
```

`tryAcquire()` (optionally with a timeout) lets you back off instead of blocking indefinitely — useful for "fail fast if the pool is currently exhausted" behavior. A `Semaphore` can also be constructed as **fair** (`new Semaphore(5, true)`), which guarantees FIFO ordering among waiting threads at some throughput cost, versus the default unfair mode which allows barging for better overall throughput.

A **binary semaphore** (1 permit) looks like a mutex but isn't one: it has no notion of "owner," so thread A can acquire it and thread B can release it — a flexible but also easy-to-misuse property.

## Exchanger

An `Exchanger<V>` is the simplest of these tools: it lets **exactly two threads** meet at a rendezvous point and atomically swap an object. Each thread calls `exchange(myObject)`, which blocks until the other thread also calls `exchange()`, and each returns the *other* thread's object.

Typical use: a two-stage pipeline where one thread fills a buffer while another drains/processes a different buffer, and they periodically swap so nobody has to wait for a full read+write cycle on a shared buffer.

```java
import java.util.concurrent.Exchanger;
import java.util.ArrayList;
import java.util.List;

public class BufferSwap {
    public static void main(String[] args) {
        Exchanger<List<String>> exchanger = new Exchanger<>();

        Thread producer = new Thread(() -> {
            List<String> buffer = new ArrayList<>();
            try {
                while (true) {
                    buffer.add("item-" + System.nanoTime());
                    if (buffer.size() == 10) {
                        buffer = exchanger.exchange(buffer); // hand off full buffer, get an empty one back
                    }
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        });

        Thread consumer = new Thread(() -> {
            List<String> buffer = new ArrayList<>();
            try {
                while (true) {
                    buffer = exchanger.exchange(buffer); // hand off empty buffer, get the full one back
                    System.out.println("processing " + buffer.size() + " items");
                    buffer.clear();
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        });

        producer.setDaemon(true);
        consumer.setDaemon(true);
        producer.start();
        consumer.start();
    }
}
```

`Exchanger` is intentionally narrow — exactly two parties, one swap at a time. For more than two participants, reach for a `CyclicBarrier`, `Phaser`, or a `BlockingQueue` instead.

## Common Code-Review Interview Pitfalls

1. **Reaching for `Executors.newFixedThreadPool`/`newCachedThreadPool` without checking their queue/thread limits.** Why it matters: a fixed pool's unbounded queue can grow without limit under sustained load, and a cached pool can spawn unbounded threads — both are a slow-motion `OutOfMemoryError` waiting to happen.
   ```java
   // Before
   ExecutorService pool = Executors.newFixedThreadPool(10); // unbounded queue behind the scenes
   // After
   ExecutorService pool = new ThreadPoolExecutor(10, 10, 0L, TimeUnit.MILLISECONDS,
           new ArrayBlockingQueue<>(500), new ThreadPoolExecutor.CallerRunsPolicy());
   ```

2. **Calling `submit()` and never checking the returned `Future`.** Why it matters: exceptions thrown inside the task are captured in the `Future` and silently disappear unless you call `get()` (or otherwise inspect it) — a failing task looks like a succeeding one.
   ```java
   // Before
   pool.submit(() -> riskyWork()); // exception vanishes if thrown
   // After
   Future<?> f = pool.submit(() -> riskyWork());
   f.get(); // or handle it in a wrapper/logging layer
   ```

3. **Never shutting down an `ExecutorService`.** Why it matters: a pool with non-daemon threads keeps the JVM alive, and a pool that's simply forgotten leaks threads for the life of the application.
   ```java
   // Before
   ExecutorService pool = Executors.newFixedThreadPool(4);
   // ... used once, never shut down
   // After
   try (ExecutorService pool = Executors.newFixedThreadPool(4)) {
       pool.submit(this::work);
   } // auto shutdown via AutoCloseable (Java 19+)
   ```

4. **Using `shutdownNow()` without handling the tasks it returns.** Why it matters: `shutdownNow()` cancels running tasks and hands back the still-queued ones — dropping that list silently discards work that may need to be retried or logged.
   ```java
   // Before
   pool.shutdownNow(); // returned list ignored, queued work is just gone
   // After
   List<Runnable> abandoned = pool.shutdownNow();
   abandoned.forEach(task -> log.warn("dropped task: {}", task));
   ```

5. **Sizing a thread pool for IO-bound work the same way as CPU-bound work.** Why it matters: a pool sized to `cores` for work that mostly waits on network/disk will badly under-utilize the machine — most threads are blocked, not computing.
   ```java
   // Before: 8 threads for an HTTP-call-heavy workload on an 8-core box
   ExecutorService pool = Executors.newFixedThreadPool(8);
   // After: size based on wait/compute ratio, e.g. cores * (1 + waitTime/computeTime)
   ExecutorService pool = Executors.newFixedThreadPool(64);
   ```

6. **Choosing a `ForkJoinTask` threshold that's too small.** Why it matters: forking tiny units of work means the bookkeeping overhead (task objects, scheduling, stealing) outweighs the actual computation, making it slower than sequential code.
   ```java
   // Before
   private static final int THRESHOLD = 2; // forks constantly, dominated by overhead
   // After
   private static final int THRESHOLD = 10_000; // benchmark and tune from here
   ```

7. **Blocking inside a parallel stream or common-pool task without a `ManagedBlocker`.** Why it matters: the common pool is shared JVM-wide; a blocking call ties up one of its few worker threads and can starve unrelated parallel streams elsewhere in the app.
   ```java
   // Before
   list.parallelStream().forEach(item -> blockingHttpCall(item)); // starves the common pool
   // After: run blocking work on a dedicated executor, not the common pool
   list.forEach(item -> ioExecutor.submit(() -> blockingHttpCall(item)));
   ```

8. **Calling `.join()`/`.get()` inside a `CompletableFuture` callback that shares a bounded pool with its dependency.** Why it matters: it can starve or deadlock the pool — all worker threads end up blocked waiting on each other with none left to make progress.
   ```java
   // Before
   CompletableFuture.supplyAsync(() -> inner().join()); // blocks a shared-pool worker thread
   // After
   CompletableFuture.supplyAsync(this::prepare).thenCompose(x -> innerAsync(x)); // no blocking
   ```

9. **Not passing an explicit `Executor` to `CompletableFuture`'s async methods.** Why it matters: it silently defaults to the shared common pool, mixing unrelated workloads together and making capacity and failure modes unpredictable.
   ```java
   // Before
   CompletableFuture.supplyAsync(this::loadData); // runs on ForkJoinPool.commonPool()
   // After
   CompletableFuture.supplyAsync(this::loadData, dedicatedExecutor);
   ```

10. **Using an unbounded `LinkedBlockingQueue` for a producer-consumer pipeline.** Why it matters: `new LinkedBlockingQueue<>()` defaults to `Integer.MAX_VALUE` capacity — there is no backpressure, so a slow consumer just lets memory grow until the process dies.
    ```java
    // Before
    BlockingQueue<Job> queue = new LinkedBlockingQueue<>(); // effectively unbounded
    // After
    BlockingQueue<Job> queue = new LinkedBlockingQueue<>(1000); // bounded = backpressure
    ```

11. **Using `add()`/`remove()` on a queue and expecting blocking behavior.** Why it matters: `add()` throws `IllegalStateException` immediately if the queue is full (it doesn't wait), and `remove()` throws `NoSuchElementException` if empty — surprising if you meant `put()`/`take()` or `offer()`/`poll()` with a timeout.
    ```java
    // Before
    queue.add(job); // throws immediately when full, no waiting
    // After
    queue.put(job); // blocks until space is available
    ```

12. **Calling back into the same `ConcurrentHashMap` from inside `compute`/`computeIfAbsent`/`merge`.** Why it matters: the remapping function runs while the bin's lock is held; recursively touching the same map (especially the same key) can deadlock or throw `IllegalStateException`.
    ```java
    // Before
    map.computeIfAbsent(key, k -> map.computeIfAbsent(otherKey, k2 -> 1)); // risky recursion
    // After
    int otherValue = map.computeIfAbsent(otherKey, k2 -> 1);
    map.computeIfAbsent(key, k -> otherValue);
    ```

13. **Treating `ConcurrentHashMap.size()` as an exact value for correctness-critical logic.** Why it matters: under concurrent modification, `size()`/`mappingCount()` are best-effort estimates from striped counters, not a locked, precise snapshot.
    ```java
    // Before
    if (cache.size() == 0) { /* assume nothing cached yet */ }
    // After: don't rely on size for correctness; check isEmpty()/containsKey() for the specific
    // guarantee you actually need, and design around the map's atomic per-key operations instead.
    ```

14. **Expecting a `CountDownLatch` to reset for a second round.** Why it matters: `CountDownLatch` is one-shot by design — once it hits zero it's done forever; reusing the same instance for a second wave of work silently does nothing (it's already at zero, so `await()` returns instantly without waiting for anyone).
    ```java
    // Before
    CountDownLatch latch = new CountDownLatch(3);
    // ... used for round 1, then reused for round 2 — await() no longer actually waits
    // After: use a new CountDownLatch per round, or use CyclicBarrier/Phaser which are reusable
    CyclicBarrier barrier = new CyclicBarrier(3);
    ```

15. **Leaving a `CyclicBarrier` broken after a timeout or interruption.** Why it matters: once one waiting thread times out or is interrupted, the barrier becomes broken and throws `BrokenBarrierException` for every other party — the barrier must be explicitly `reset()` before it can be used again, and skipping that hangs or fails the next round.
    ```java
    // Before: catch BrokenBarrierException and just log it, never reset the barrier
    // After
    catch (BrokenBarrierException | TimeoutException e) {
        barrier.reset(); // required before the barrier can be reused
    }
    ```

16. **Releasing a `Semaphore` permit without a `try`/`finally`.** Why it matters: if the guarded code throws, the permit is never released, permanently shrinking the effective pool size — a slow permit leak that eventually blocks everyone.
    ```java
    // Before
    permits.acquire();
    doWork(); // if this throws, release() below never runs
    permits.release();
    // After
    permits.acquire();
    try {
        doWork();
    } finally {
        permits.release();
    }
    ```

17. **Ignoring `InterruptedException` by swallowing it instead of restoring the interrupt status.** Why it matters: swallowing the exception silently (empty catch block) breaks cooperative cancellation — code further up the call stack that checks `Thread.interrupted()` never finds out the thread was asked to stop.
    ```java
    // Before
    try { queue.take(); } catch (InterruptedException e) { /* ignored */ }
    // After
    try { queue.take(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    ```

18. **Using `invokeAny` without realizing it cancels the other in-flight tasks.** Why it matters: reviewers sometimes assume all submitted tasks run to completion; `invokeAny` returns as soon as one succeeds and actively cancels the rest, so any side effects those tasks were relying on to complete may never finish.
    ```java
    // Before: assuming all 3 "save to replica" tasks fully complete
    String result = pool.invokeAny(saveToReplicaTasks); // the other 2 get cancelled mid-flight
    // After: use invokeAll if every task's side effect must complete, invokeAny only for
    // "first successful result wins, and it's fine to abandon the rest"
    List<Future<String>> results = pool.invokeAll(saveToReplicaTasks);
    ```
