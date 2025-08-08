# 13. Concurrency (Core)

Concurrency is what happens when more than one thread touches your program's state at the same time. Get it wrong and bugs show up rarely, non-deterministically, and usually in production under load — which makes concurrency one of the highest-value topics in a code-review interview. This chapter covers the classic "platform threads" toolkit: threads themselves, locks, `volatile`, atomics, `ThreadLocal`, and the memory-model rules (happens-before) that explain *why* all of it works. Examples target Java 21+; virtual threads and structured concurrency get their own treatment in Chapter 15.

## Table of Contents

- [Threads](#threads)
- [Runnable and Callable](#runnable-and-callable)
- [Thread Lifecycle](#thread-lifecycle)
- [Synchronization](#synchronization)
- [Intrinsic Locks](#intrinsic-locks)
- [ReentrantLock](#reentrantlock)
- [ReadWriteLock](#readwritelock)
- [StampedLock](#stampedlock)
- [Volatile](#volatile)
- [Atomic Classes](#atomic-classes)
- [ThreadLocal](#threadlocal)
- [Happens-Before Relationship](#happens-before-relationship)
- [Visibility, Atomicity, and Ordering](#visibility-atomicity-and-ordering)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Threads

A **thread** is an independent path of execution inside a program. A single Java process can run many threads at once, each with its own call stack, but all of them share the same heap memory — the same objects, the same static fields. That sharing is exactly what makes concurrency both powerful and dangerous.

The classic (pre-virtual-thread) way to create one is `Thread` backed directly by an OS thread — a "platform thread." You can subclass `Thread` and override `run()`, or (preferred) pass it a `Runnable`:

```java
public class ThreadBasics {
    public static void main(String[] args) throws InterruptedException {
        Thread worker = new Thread(() -> {
            System.out.println("Running on: " + Thread.currentThread().getName());
        }, "worker-1"); // name the thread — huge help in thread dumps and logs

        worker.start(); // schedules the thread to run; never call run() directly
        worker.join();  // wait for it to finish before continuing
        System.out.println("Done");
    }
}
```

**`start()` vs `run()`.** Calling `start()` asks the JVM to create a new call stack and schedule execution. Calling `run()` directly just executes the code on the *current* thread — no concurrency happens at all. This is a common interview trick question.

**Daemon vs user (non-daemon) threads.** A **user thread** keeps the JVM alive — the process won't exit while any user thread is still running. A **daemon thread** is a "background helper" thread; the JVM will exit even if daemon threads are still running, and it won't wait for them. Garbage collection and JIT compilation run on daemon threads internally.

```java
Thread heartbeat = new Thread(() -> {
    while (true) {
        System.out.println("tick");
        try { Thread.sleep(1000); } catch (InterruptedException e) { break; }
    }
});
heartbeat.setDaemon(true); // must be called BEFORE start()
heartbeat.start();
// If main() returns here, the JVM exits and heartbeat is killed mid-loop —
// no cleanup code in it is guaranteed to run.
```

**Thread priorities.** Every thread has a priority from `Thread.MIN_PRIORITY` (1) to `Thread.MAX_PRIORITY` (10), default `Thread.NORM_PRIORITY` (5). This is only a **hint** to the OS scheduler about relative importance — the JVM spec doesn't guarantee any particular scheduling behavior, and most OSes map Java's ten levels onto a much coarser native scale. Don't design correctness around priorities; use them only as a mild tuning hint, if at all.

```java
Thread background = new Thread(() -> doLowPriorityWork());
background.setPriority(Thread.MIN_PRIORITY); // hint: "less urgent," not a guarantee
background.start();
```

**Naming threads.** Give threads meaningful names. It costs nothing and it's the difference between a readable thread dump and a wall of `Thread-0`, `Thread-1`, `Thread-2`.

**`UncaughtExceptionHandler`.** If a thread's `run()` throws an unchecked exception and nobody catches it, the thread just dies silently by default — the exception goes to `System.err`, but nothing else happens, and no other thread is notified. Set a handler to observe or react to that:

```java
Thread risky = new Thread(() -> {
    throw new IllegalStateException("boom");
});
risky.setUncaughtExceptionHandler((t, e) ->
    System.err.println("Thread " + t.getName() + " died: " + e));
risky.start();
// Prints: Thread Thread-0 died: java.lang.IllegalStateException: boom
// Without the handler, this exception would be easy to miss in production logs.
```

You can also set a JVM-wide default via `Thread.setDefaultUncaughtExceptionHandler(...)`, useful for centralized alerting/metrics.

**Interruption is cooperative cancellation.** `Thread.interrupt()` does not forcibly stop a thread. It just sets an internal "interrupted" flag and, if the thread is blocked in something interruptible (`sleep`, `wait`, `join`, many blocking I/O and concurrency calls), wakes it up early with an `InterruptedException`. The target thread has to *check* for interruption and decide to stop. This is why it's called cooperative — both sides have to participate.

```java
public class CooperativeCancellation {
    public static void main(String[] args) throws InterruptedException {
        Thread worker = new Thread(() -> {
            while (!Thread.currentThread().isInterrupted()) {
                // do a unit of work
            }
            System.out.println("Worker noticed the interrupt and is exiting cleanly");
        });
        worker.start();
        Thread.sleep(50);
        worker.interrupt(); // politely ask it to stop
        worker.join();
    }
}
```

If your loop calls a blocking method, catch `InterruptedException` and either exit or **re-interrupt and rethrow** — never swallow it silently, because that erases the cancellation signal for anyone further up the call stack:

```java
void pollUntilCancelled() {
    while (true) {
        try {
            Thread.sleep(500);
            // do work
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt(); // restore the flag for callers
            return; // stop the loop
        }
    }
}
```

**Why `Thread.stop()` and `Thread.suspend()` are dead.** Both are deprecated for removal. `stop()` kills a thread instantly, mid-operation, which can leave shared objects in a half-updated, permanently broken state (it releases locks it holds without any guarantee the protected data is consistent). `suspend()` freezes a thread while it may be holding a lock, which can deadlock every other thread that needs that lock. Neither has a safe way to guarantee the target thread cleans up. **Cooperative interruption (or a `volatile` cancel flag) is the only sound replacement.**

**Virtual threads.** Java 21 introduced lightweight, JVM-managed threads that let you write blocking-style code cheaply at massive scale. They're built on the same `Thread` API you just saw, but are covered in full in Chapter 15 (Structured Concurrency / Modern Concurrency), since they change a lot of the cost-benefit calculus for the patterns in this chapter.

## Runnable and Callable

`Runnable` and `Callable<V>` are the two "unit of work" interfaces you hand to a thread or an executor.

| | `Runnable` | `Callable<V>` |
|---|---|---|
| Method | `void run()` | `V call() throws Exception` |
| Returns a result? | No | Yes, typed `V` |
| Can throw checked exceptions? | No | Yes |
| Introduced | Java 1.0 | Java 5 (`java.util.concurrent`) |
| Typical use | `new Thread(...)`, fire-and-forget tasks | `ExecutorService.submit(...)` when you need a `Future<V>` |

```java
import java.util.concurrent.*;

public class RunnableVsCallable {
    public static void main(String[] args) throws Exception {
        Runnable fireAndForget = () -> System.out.println("logged, no result");

        Callable<Integer> withResult = () -> {
            Thread.sleep(10);
            return 42;
        };

        ExecutorService pool = Executors.newFixedThreadPool(2);
        pool.execute(fireAndForget);              // Runnable: no Future
        Future<Integer> future = pool.submit(withResult); // Callable: get a Future
        System.out.println("Result: " + future.get());    // blocks until done: 42
        pool.shutdown();
    }
}
```

**`Thread.sleep()` vs `Object.wait()`.** Both pause a thread, but they're built for different purposes:

| | `Thread.sleep(ms)` | `Object.wait()` |
|---|---|---|
| Holds locks while paused? | Yes — keeps any locks it holds | No — releases the monitor lock it's waiting on |
| Needs a lock to call? | No | Yes — must be called while holding the object's monitor |
| How it wakes up | Timeout elapses, or interrupted | Another thread calls `notify()`/`notifyAll()`, timeout (if given), or interrupted |
| Typical use | "Pause for a fixed duration" | "Wait until some condition, controlled by another thread, becomes true" |

```java
synchronized (lock) {
    while (!conditionIsTrue) {
        lock.wait(); // releases 'lock' while waiting — other threads can acquire it
    }
}
// vs:
Thread.sleep(1000); // holds every lock this thread already has for the full second
```

Sleeping while holding a lock is a common cause of accidental contention — a slow, sleeping thread blocks everyone else waiting on that same lock.

## Thread Lifecycle

Every `Thread` is always in exactly one of six states, defined by the `Thread.State` enum: `NEW`, `RUNNABLE`, `BLOCKED`, `WAITING`, `TIMED_WAITING`, `TERMINATED`.

```
        new Thread(...)
              |
              v
      +---------------+
      |      NEW       |   (created, start() not yet called)
      +---------------+
              | start()
              v
      +---------------+   synchronized block/method     +---------------+
      |    RUNNABLE    | -------------------------------> |    BLOCKED    |
      | (running, or   |  <------------------------------ | (waiting for  |
      |  ready to run,  |    lock becomes available        |  a monitor    |
      |  scheduler-      |                                  |  lock)        |
      |  dependent)     |                                  +---------------+
      +---------------+
        |     ^   |      ^
        |     |   |      |
 wait()/join()/  |   sleep(ms)/wait(ms)/join(ms)/
 park() (no      |   parkNanos (has a timeout)
 timeout)        |        |                  ^
        v        |        v                  |
  +---------------+   +-------------------+  |
  |   WAITING     |   |  TIMED_WAITING    |--+
  | (indefinite,   |   |  (bounded wait,   |
  |  needs another |   |   times out on    |
  |  thread to act) |   |   its own too)    |
  +---------------+   +-------------------+
        |                       |
        +-----------+-----------+
                     | notify()/notifyAll(),
                     |  interrupt, timeout,
                     |  target thread finishes
                     v
              +---------------+
              |   RUNNABLE    |  (back in the run queue)
              +---------------+
                     |
              run() method returns,
              or an uncaught exception propagates out
                     v
              +---------------+
              |  TERMINATED   |   (finished — cannot be restarted)
              +---------------+
```

Key transitions to remember for a review conversation:

- **NEW → RUNNABLE**: `start()`. You can only call `start()` once; calling it again throws `IllegalThreadStateException`.
- **RUNNABLE → BLOCKED**: the thread tries to enter a `synchronized` block/method whose lock another thread already holds.
- **RUNNABLE → WAITING**: `Object.wait()` (no timeout), `Thread.join()` (no timeout), or `LockSupport.park()`.
- **RUNNABLE → TIMED_WAITING**: `Thread.sleep(ms)`, `Object.wait(ms)`, `Thread.join(ms)`, `LockSupport.parkNanos(...)`.
- **Anything → TERMINATED**: `run()` returns normally, or an exception propagates out of it uncaught. There is no supported way back from `TERMINATED` — a finished `Thread` object is a dead end; make a new `Thread` instead.

```java
public class LifecycleDemo {
    public static void main(String[] args) throws InterruptedException {
        Object lock = new Object();
        Thread t = new Thread(() -> {
            synchronized (lock) {
                try { Thread.sleep(200); } catch (InterruptedException ignored) {}
            }
        });
        System.out.println(t.getState()); // NEW
        t.start();
        Thread.sleep(50);
        System.out.println(t.getState()); // TIMED_WAITING (inside Thread.sleep)
        t.join();
        System.out.println(t.getState()); // TERMINATED
    }
}
```

## Synchronization

**Synchronization** means controlling access to shared, mutable state so that only one thread modifies it at a time, and every thread sees the latest value. In Java, the primary built-in tool is the `synchronized` keyword, which uses every object's built-in **monitor lock** (also called its intrinsic lock — see next section).

**Synchronized methods vs synchronized blocks.**

```java
public class Counter {
    private int count = 0;

    // Synchronized instance method: locks 'this'
    public synchronized void incrementWhole() {
        count++;
    }

    // Synchronized block: locks an explicit object, narrower scope
    private final Object lock = new Object();
    public void incrementBlock() {
        int expensive = computeSomethingUnrelated(); // not protected — doesn't need to be
        synchronized (lock) {
            count++; // only the truly shared part is inside the lock
        }
    }

    private int computeSomethingUnrelated() { return 1; }

    public synchronized int getCount() {
        return count; // reads must be synchronized too, or you can see a stale value
    }
}
```

**What object gets locked?**

| Declaration | Lock acquired |
|---|---|
| `synchronized void method()` | the instance (`this`) |
| `static synchronized void method()` | the `Class` object (one lock shared by *all* instances) |
| `synchronized (obj) { ... }` | whatever object `obj` refers to |

Mixing an instance-level lock with a static-level lock is a classic bug: two threads think they're synchronizing on "the same thing" but are actually holding two different locks, so no mutual exclusion happens at all.

**Reentrancy.** Intrinsic locks are **reentrant**: a thread that already holds a lock can acquire it again (e.g., calling another synchronized method on the same object from inside a synchronized method) without blocking itself. The JVM tracks a hold count per thread.

```java
public synchronized void outer() {
    inner(); // same thread re-acquiring the same lock — allowed, does not deadlock
}
public synchronized void inner() {
    // ...
}
```

**`wait()` / `notify()` / `notifyAll()` — and the mandatory `while` loop.** These three methods coordinate threads around a condition, and must be called while holding the object's monitor. `wait()` releases the lock and parks the thread; `notify()` wakes *one* waiting thread; `notifyAll()` wakes *all* of them. Always check the condition in a `while` loop, never an `if`:

```java
public class BoundedBuffer<T> {
    private final Queue<T> queue = new LinkedList<>();
    private final int capacity;

    public BoundedBuffer(int capacity) { this.capacity = capacity; }

    public synchronized void put(T item) throws InterruptedException {
        while (queue.size() == capacity) { // WHILE, not if — re-check after waking up
            wait(); // releases the lock, parks this thread
        }
        queue.add(item);
        notifyAll(); // wake up any waiting consumers
    }

    public synchronized T take() throws InterruptedException {
        while (queue.isEmpty()) { // WHILE — guard condition must be re-checked
            wait();
        }
        T item = queue.remove();
        notifyAll(); // wake up any waiting producers
        return item;
    }
}
```

Why `while`, not `if`:

- **Spurious wakeups.** The JVM spec permits `wait()` to return without any `notify()` ever being called. Rare, but legal — so the condition must be re-checked regardless.
- **Multiple waiters.** With `notifyAll()`, several threads wake up but only one may get to act before the condition becomes false again (e.g., two consumers wake up but only one item was added). Each thread must re-verify before proceeding.

**Lost wakeup.** This happens when a thread calls `notify()`/`notifyAll()` *before* another thread starts waiting, and the signal is lost forever because there was nobody listening yet.

```java
// BROKEN: checking the condition outside the lock creates a window for a lost wakeup
public void putBroken(T item) throws InterruptedException {
    if (isFull()) {          // check happens OUTSIDE the lock
        synchronized (this) {
            wait();           // another thread could notify() in between check and wait()
        }                     // — that notify is lost, and this thread waits forever
    }
    // ...
}
```

The fix is what `BoundedBuffer` already does above: the check and the `wait()` call happen **inside the same synchronized block**, so no notification can slip through the gap.

**Lock granularity.** A single, coarse lock around an entire large object is simple and safe, but it forces every operation to queue up behind every other, even unrelated ones — that's a **throughput** cost. Splitting into several fine-grained locks (e.g., one lock per shard/bucket) improves parallelism but is more complex and raises the risk of deadlock when a thread needs more than one lock at a time.

**Deadlock.** Two (or more) threads each hold a lock the other needs, and neither can proceed. Classic two-lock example:

```java
public class DeadlockDemo {
    private static final Object lockA = new Object();
    private static final Object lockB = new Object();

    public static void main(String[] args) {
        Thread t1 = new Thread(() -> {
            synchronized (lockA) {
                sleepQuiet(50);
                synchronized (lockB) { System.out.println("t1 got both"); }
            }
        });
        Thread t2 = new Thread(() -> {
            synchronized (lockB) {           // t2 grabs B first...
                sleepQuiet(50);
                synchronized (lockA) { System.out.println("t2 got both"); } // ...then wants A
            }
        });
        t1.start();
        t2.start();
        // t1 holds A, waits for B. t2 holds B, waits for A. Neither ever finishes.
    }

    private static void sleepQuiet(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException ignored) {}
    }
}
```

**The fix: consistent lock ordering.** If every thread in the system always acquires locks in the same global order, a cycle can never form:

```java
// FIXED: every thread acquires lockA before lockB, no exceptions.
Thread t1 = new Thread(() -> {
    synchronized (lockA) {
        synchronized (lockB) { System.out.println("t1 got both"); }
    }
});
Thread t2 = new Thread(() -> {
    synchronized (lockA) {   // t2 also takes A first now, matching t1's order
        synchronized (lockB) { System.out.println("t2 got both"); }
    }
});
```

Other standard deadlock fixes: use `tryLock` with a timeout instead of a blocking acquire (covered under `ReentrantLock`), or acquire all needed locks up front and back out entirely if any single one isn't available.

**Livelock.** Threads aren't blocked — they're both actively running — but they keep responding to each other in a way that stops either from making progress. A common example is two threads that each politely "back off" and retry when they detect contention, but they back off in lockstep forever and never actually get through.

**Starvation.** A thread is perpetually denied access to a resource because other threads keep getting priority — e.g., low-priority threads never running because high-priority ones keep flooding the scheduler, or a "fair" resource being repeatedly grabbed by newcomers ahead of a long-waiting thread. Using a **fair lock** (see `ReentrantLock` below) is one direct mitigation.

## Intrinsic Locks

Every Java object has a built-in lock, historically called its **monitor** or **intrinsic lock**. It's what `synchronized` uses under the hood — there's no separate "lock object" you create; the object *is* the lock. Only one thread can hold a given object's monitor at a time.

```java
public class IntrinsicLockExample {
    private final Object monitor = new Object();
    private int balance = 0;

    public void deposit(int amount) {
        synchronized (monitor) { // acquire monitor's intrinsic lock
            balance += amount;
        } // lock is automatically released here, even if an exception is thrown
    }
}
```

Intrinsic locks have three defining properties, all already demonstrated above:

- **Mutual exclusion** — only one thread inside the block/method at a time.
- **Reentrancy** — the holding thread can re-enter without blocking itself.
- **Automatic release** — released when the block exits, normally or via exception. You never write an explicit "unlock" call, which avoids one entire class of bugs (forgetting to unlock) — but it also means you can't do things like acquire in one method and release in another, or time out on acquisition. That's exactly the gap `ReentrantLock` fills.

Intrinsic locks are **not fair** by default — the JVM makes no promise about which waiting thread gets the lock next — and they offer no way to interrupt a thread that's blocked waiting to enter a `synchronized` block, and no way to check "is this lock free?" without blocking. Those limitations are the whole reason `java.util.concurrent.locks` exists.

## ReentrantLock

`ReentrantLock` is an explicit, class-based lock with the same reentrant mutual-exclusion guarantee as `synchronized`, plus extra capabilities `synchronized` doesn't offer: fairness policies, timed/non-blocking acquisition, interruptible acquisition, and multiple wait-conditions per lock.

**The mandatory pattern: acquire, then `try`/`finally` to release.** Unlike `synchronized`, nothing releases the lock for you automatically.

```java
import java.util.concurrent.locks.ReentrantLock;

public class Account {
    private final ReentrantLock lock = new ReentrantLock();
    private int balance = 0;

    public void withdraw(int amount) {
        lock.lock();
        try {
            if (amount > balance) throw new IllegalStateException("insufficient funds");
            balance -= amount;
        } finally {
            lock.unlock(); // MUST be in finally — otherwise an exception leaks the lock forever
        }
    }
}
```

**Fairness.** The no-arg constructor gives a non-fair lock (higher throughput, but a thread can theoretically be overtaken repeatedly). `new ReentrantLock(true)` gives a fair lock — the longest-waiting thread goes next, which trades some throughput for protection against starvation.

```java
ReentrantLock fair = new ReentrantLock(true); // FIFO-ish ordering, reduces starvation risk
```

**`tryLock` with a timeout.** Instead of blocking forever, ask for the lock and give up after a bound — a direct deadlock-avoidance tool:

```java
import java.util.concurrent.TimeUnit;

public boolean tryWithdraw(int amount) throws InterruptedException {
    if (!lock.tryLock(200, TimeUnit.MILLISECONDS)) {
        return false; // couldn't get the lock in time — back off instead of deadlocking
    }
    try {
        balance -= amount;
        return true;
    } finally {
        lock.unlock();
    }
}
```

**`lockInterruptibly()`.** Acquire the lock, but allow this thread to be interrupted while it's waiting — useful when a blocked-on-lock thread still needs to respond to cancellation:

```java
public void withdrawOrCancel(int amount) throws InterruptedException {
    lock.lockInterruptibly(); // throws InterruptedException instead of blocking forever
    try {
        balance -= amount;
    } finally {
        lock.unlock();
    }
}
```

**`Condition` — like `wait`/`notify`, but per-lock and multiple per lock.** `synchronized` gives every object exactly one implicit wait-set. A `ReentrantLock` can have several independent `Condition`s, which is handy for producer/consumer designs where "buffer is full" and "buffer is empty" are logically distinct waits:

```java
import java.util.concurrent.locks.*;
import java.util.*;

public class BoundedBufferWithLock<T> {
    private final ReentrantLock lock = new ReentrantLock();
    private final Condition notFull = lock.newCondition();
    private final Condition notEmpty = lock.newCondition();
    private final Queue<T> queue = new LinkedList<>();
    private final int capacity;

    public BoundedBufferWithLock(int capacity) { this.capacity = capacity; }

    public void put(T item) throws InterruptedException {
        lock.lock();
        try {
            while (queue.size() == capacity) notFull.await(); // like wait(), but on this Condition
            queue.add(item);
            notEmpty.signal(); // like notify(), but only wakes threads waiting on notEmpty
        } finally {
            lock.unlock();
        }
    }

    public T take() throws InterruptedException {
        lock.lock();
        try {
            while (queue.isEmpty()) notEmpty.await();
            T item = queue.remove();
            notFull.signal();
            return item;
        } finally {
            lock.unlock();
        }
    }
}
```

The same `while`-loop rule from intrinsic locks applies here too: always re-check the condition after `await()` returns.

## ReadWriteLock

Many workloads are read-heavy: lots of threads just want to read shared state, and only occasionally does a thread need to write it. A plain mutual-exclusion lock forces even simultaneous *reads* to queue up one at a time, which wastes parallelism. `ReadWriteLock` splits the lock into two: any number of readers can hold the read lock at once, but a writer needs exclusive access — no readers and no other writer at the same time.

```java
import java.util.concurrent.locks.*;
import java.util.*;

public class Cache<K, V> {
    private final Map<K, V> map = new HashMap<>();
    private final ReadWriteLock rwLock = new ReentrantReadWriteLock();
    private final Lock readLock = rwLock.readLock();
    private final Lock writeLock = rwLock.writeLock();

    public V get(K key) {
        readLock.lock(); // many threads can hold this simultaneously
        try {
            return map.get(key);
        } finally {
            readLock.unlock();
        }
    }

    public void put(K key, V value) {
        writeLock.lock(); // exclusive: blocks all readers and other writers
        try {
            map.put(key, value);
        } finally {
            writeLock.unlock();
        }
    }
}
```

**When it helps:** read-mostly data structures — caches, configuration snapshots, lookup tables — where writes are rare and reads vastly outnumber them. **When it doesn't:** if writes are frequent, or reads are extremely short (a few nanoseconds of work), the bookkeeping overhead of tracking readers/writers can cost more than the parallelism gains — a plain `ReentrantLock` or even `synchronized` may be faster in practice. Always measure before reaching for this.

## StampedLock

`StampedLock` (Java 8+) is a further evolution: it supports the same read/write split as `ReadWriteLock`, plus a third mode — **optimistic reads** — that don't block writers at all and cost almost nothing when there's no contention. Instead of an object-based lock, it hands back a `long` **stamp** that you must validate afterward.

```java
import java.util.concurrent.locks.StampedLock;

public class Point {
    private final StampedLock stampedLock = new StampedLock();
    private double x, y;

    public void move(double dx, double dy) {
        long stamp = stampedLock.writeLock(); // exclusive, like a normal write lock
        try {
            x += dx;
            y += dy;
        } finally {
            stampedLock.unlockWrite(stamp);
        }
    }

    public double distanceFromOrigin() {
        long stamp = stampedLock.tryOptimisticRead(); // does NOT block, does NOT take a real lock
        double currentX = x, currentY = y;             // read the fields hopefully
        if (!stampedLock.validate(stamp)) {             // did a writer sneak in while we read?
            stamp = stampedLock.readLock();             // fall back to a real, blocking read lock
            try {
                currentX = x;
                currentY = y;
            } finally {
                stampedLock.unlockRead(stamp);
            }
        }
        return Math.sqrt(currentX * currentX + currentY * currentY);
    }
}
```

The optimistic-read pattern is always: **grab a stamp → read the data → validate the stamp → if invalid, retry with a real lock.** Skipping the validation step is a serious bug — it silently allows reading data that a writer changed mid-read.

**Critical gotcha: `StampedLock` is NOT reentrant.** Unlike `ReentrantLock` and intrinsic locks, calling `writeLock()` again from the same thread that already holds it will simply deadlock the thread against itself:

```java
long stamp = stampedLock.writeLock();
try {
    // BUG: if this method (directly or indirectly) tries to acquire
    // stampedLock.writeLock() again on the SAME thread, it blocks forever —
    // StampedLock does not track "who already owns this."
    doSomethingThatAlsoLocks();
} finally {
    stampedLock.unlockWrite(stamp);
}
```

Because of this, be very careful using `StampedLock` in any code path that might recurse or call back into itself. It also has no `Condition` support and doesn't play with `synchronized`-style monitor semantics — it's a specialized tool for exactly the read-heavy, low-contention scenario it's built for.

## Volatile

`volatile` is a field modifier that gives two guarantees, and only two: **visibility** and a limited form of **ordering**. It does **not** give atomicity.

- **Visibility**: a write to a `volatile` field by one thread is guaranteed to be immediately visible to any thread that subsequently reads it. Without `volatile`, a reading thread might keep seeing a stale, cached value indefinitely (in theory — real JITs/CPUs vary, but the JMM makes no promise otherwise).
- **Ordering**: the compiler and CPU cannot reorder other instructions across a `volatile` read/write in ways that would break the visible sequence (this feeds directly into the happens-before rules below).
- **What it does NOT give you: atomicity.** `count++` on a `volatile int` is still read-modify-write — three separate steps — and two threads can interleave those steps and lose an update, exactly like a non-volatile field.

```java
public class VolatileIsNotAtomic {
    private volatile int count = 0;

    public void increment() {
        count++; // READS count, ADDS 1, WRITES count — three steps, not one
    }
}
// With 2 threads each calling increment() 100,000 times, the final count
// is very likely LESS than 200,000 — some increments are silently lost,
// even though 'volatile' is present.
```

**The classic correct use case: a stop flag.**

```java
public class StopFlagExample {
    private volatile boolean running = true; // visibility is ALL this needs

    public void doWork() {
        while (running) {
            // do a unit of work
        }
        System.out.println("Stopped cleanly");
    }

    public void stop() {
        running = false; // guaranteed to become visible to the worker thread promptly
    }
}
```

This works with plain `volatile` because it's a single write and a single read of one variable — no read-modify-write, so no lost-update risk. Swap `volatile boolean` for a plain `boolean` here and the worker thread might loop forever, never observing the change (a real, documented class of bug).

**Double-checked locking, done right.** A common (broken) attempt at lazy, thread-safe singleton initialization:

```java
// BROKEN: without 'volatile', another thread can see a half-constructed object
public class BrokenSingleton {
    private static BrokenSingleton instance;

    public static BrokenSingleton getInstance() {
        if (instance == null) {                 // first check, no lock — fast path
            synchronized (BrokenSingleton.class) {
                if (instance == null) {          // second check, with lock
                    instance = new BrokenSingleton(); // NOT guaranteed fully-constructed
                    // before this reference becomes visible to other threads without 'volatile'
                }
            }
        }
        return instance;
    }
}
```

The problem: `new BrokenSingleton()` isn't a single atomic step from the JMM's point of view — it involves allocating memory, running the constructor, and assigning the reference. Without `volatile`, those steps can appear reordered to another thread, which could see a non-null `instance` reference that points to a not-yet-fully-initialized object.

```java
// FIXED: 'volatile' prevents the reordering and publishes a fully-built object
public class CorrectSingleton {
    private static volatile CorrectSingleton instance;

    public static CorrectSingleton getInstance() {
        if (instance == null) {
            synchronized (CorrectSingleton.class) {
                if (instance == null) {
                    instance = new CorrectSingleton();
                }
            }
        }
        return instance;
    }
}
```

(In modern Java, the simplest and safest fix for a plain singleton is usually a static holder class, which relies on class-initialization guarantees instead of double-checked locking at all — but double-checked locking remains a fair-game interview topic.)

## Atomic Classes

The `java.util.concurrent.atomic` package gives you classes that perform single-variable read-modify-write operations **atomically**, using hardware-level compare-and-swap (CAS) instructions instead of locks. That makes them faster than a lock under light-to-moderate contention, with no risk of deadlock (there's nothing to hold across a blocking call).

```java
import java.util.concurrent.atomic.*;

public class AtomicBasics {
    public static void main(String[] args) {
        AtomicInteger counter = new AtomicInteger(0);
        counter.incrementAndGet();          // atomic ++counter, returns new value
        counter.getAndIncrement();          // atomic counter++, returns old value
        counter.addAndGet(5);               // atomic counter += 5

        AtomicLong bigCounter = new AtomicLong(0L);
        bigCounter.incrementAndGet();

        AtomicReference<String> ref = new AtomicReference<>("initial");
        boolean swapped = ref.compareAndSet("initial", "updated"); // CAS: only if still "initial"
        System.out.println(swapped);        // true
        System.out.println(ref.get());      // "updated"

        // updateAndGet: apply a function atomically, retrying under contention
        AtomicInteger price = new AtomicInteger(100);
        price.updateAndGet(current -> current + 10); // atomic "price += 10", CAS-retry loop internally
    }
}
```

**`compareAndSet` (CAS)** is the primitive underneath almost everything here: "set this value to X, but only if it's still currently Y." If another thread changed it in between, the CAS fails and the caller (or the atomic class internally, for methods like `updateAndGet`) retries.

**The ABA problem.** CAS only checks "is the value still what I last saw?" — it can't tell if the value was changed to something else and then changed *back* to the original value while you weren't looking. If your logic assumes "unchanged" means "nothing happened in between," ABA breaks that assumption.

```java
// Thread 1 reads reference, sees "A", plans to CAS "A" -> "C"
// Thread 2 (in between) CAS "A" -> "B", then CAS "B" -> "A"
// Thread 1's CAS("A", "C") now succeeds — it thinks nothing changed,
// but state actually went A -> B -> A, and any side effects tied to
// that B intermediate state have already happened invisibly.
AtomicReference<String> state = new AtomicReference<>("A");
// state.compareAndSet("A", "C") succeeds even though "B" happened in between.
```

Mitigation: use a version/stamp alongside the value (`AtomicStampedReference`) so a CAS also checks that the "version" hasn't moved, not just the value.

**`LongAdder` vs `AtomicLong` under contention.** `AtomicLong` funnels every thread's CAS attempt at the *same single memory location* — under heavy contention (many threads incrementing constantly), that creates a hot spot where CAS attempts keep failing and retrying. `LongAdder` (Java 8+) spreads the count across multiple internal cells and only combines them when you call `sum()`, dramatically reducing contention at the cost of `sum()` being an eventually-consistent snapshot rather than a single atomic memory location.

```java
import java.util.concurrent.atomic.LongAdder;

LongAdder hits = new LongAdder();
// Many threads, high contention:
hits.increment();   // internally spreads updates across cells — much less CAS contention
long total = hits.sum(); // combines cells; fine for metrics/counters, not for tight CAS logic
```

Rule of thumb: use `AtomicLong` when you need the exact current value read consistently (e.g., you're going to `compareAndSet` against it). Use `LongAdder` for pure counters/metrics under high contention where you only need the total occasionally.

**`AtomicIntegerFieldUpdater` (briefly).** Lets you get atomic CAS-style updates on a plain `volatile int` field of an *existing* class, without changing its declared type to `AtomicInteger` — useful when you can't change a field's type (e.g., it's part of a serialized format or an inherited class) but still need atomic updates.

```java
import java.util.concurrent.atomic.AtomicIntegerFieldUpdater;

class Node {
    volatile int visits = 0; // must be volatile, not private, and not final
}

AtomicIntegerFieldUpdater<Node> updater =
        AtomicIntegerFieldUpdater.newUpdater(Node.class, "visits");
Node node = new Node();
updater.incrementAndGet(node); // atomic increment on node.visits without an AtomicInteger field
```

## ThreadLocal

`ThreadLocal<T>` gives each thread its own independent copy of a variable. Two threads reading/writing the "same" `ThreadLocal` never see each other's values — it's per-thread storage, not shared state, so it needs no locking at all.

```java
public class ThreadLocalBasics {
    private static final ThreadLocal<Integer> requestId =
            ThreadLocal.withInitial(() -> 0);

    public static void main(String[] args) throws InterruptedException {
        Runnable task = () -> {
            requestId.set((int) (Math.random() * 1000));
            System.out.println(Thread.currentThread().getName() + " sees " + requestId.get());
        };
        Thread t1 = new Thread(task, "t1");
        Thread t2 = new Thread(task, "t2");
        t1.start(); t2.start();
        t1.join(); t2.join();
        // t1 and t2 print different values — each has its own independent slot
    }
}
```

**Common use cases:** per-request context (user id, transaction id, locale) in web servers, `SimpleDateFormat`/`DecimalFormat` instances (historically not thread-safe, so one-per-thread avoided sharing), database connections or transaction handles tied to "whatever thread is currently servicing this request."

**`remove()` in thread pools, or you leak.** A `ThreadLocal`'s value is stored in a map owned by the `Thread` object itself. In a thread pool, threads are *reused* across many tasks/requests — if you never call `remove()`, the value from request #1 is still sitting there when the same pooled thread later handles request #47, which is both a memory leak (values pile up and are never GC'd while the thread lives) and a correctness bug (stale data leaking across unrelated requests).

```java
// BROKEN in a pooled environment: value from a previous task can leak into the next one
private static final ThreadLocal<User> currentUser = new ThreadLocal<>();

void handleRequest(User user) {
    currentUser.set(user);
    process();
    // no cleanup — the next task run on this SAME pooled thread inherits 'user' by accident
}
```

```java
// FIXED: always clean up, in a finally block, regardless of success or failure
void handleRequest(User user) {
    currentUser.set(user);
    try {
        process();
    } finally {
        currentUser.remove(); // returns the thread's slot to its initial, empty state
    }
}
```

**`InheritableThreadLocal`.** A variant that copies the parent thread's value into any *child* thread it creates at the moment the child is constructed (via an overridable `childValue()` hook). Useful for propagating context (like a trace id) into worker threads spawned by a request handler — but it only captures a snapshot at thread-creation time, and it doesn't work at all with pooled worker threads that were created long before the current task existed.

```java
public class InheritableExample {
    static final InheritableThreadLocal<String> traceId = new InheritableThreadLocal<>();

    public static void main(String[] args) throws InterruptedException {
        traceId.set("trace-123");
        Thread child = new Thread(() -> {
            System.out.println(traceId.get()); // prints "trace-123" — inherited at creation time
        });
        child.start();
        child.join();
    }
}
```

**Looking ahead.** Java 21 introduced (as a preview, stabilizing later) **Scoped Values** as a safer, immutable alternative to `ThreadLocal` for propagating context — especially well-suited to virtual threads and structured concurrency, where `ThreadLocal`'s mutability and lifecycle can be awkward. Scoped Values are covered in Chapter 15.

## Happens-Before Relationship

The **Java Memory Model (JMM)** defines exactly when one thread's actions are *guaranteed* to be visible, in order, to another thread. That guarantee is called **happens-before**: if action A happens-before action B, every effect of A (every write to memory) is visible to whatever executes B. Without an explicit happens-before edge between two threads' operations, the JMM makes **no promise at all** about ordering or visibility — the reordering and staleness bugs seen earlier in this chapter are exactly what happens in that gap.

The core rules:

**1. Program order rule.** Within a single thread, each action happens-before every subsequent action in that thread's own code, in the order the code specifies (even though the JIT/CPU may reorder instructions *invisibly* to that thread — the thread itself always observes its own actions in order).

```java
int a = 1;
int b = 2;
int c = a + b; // program order guarantees 'a' and 'b' are both assigned before this line runs
```

**2. Monitor lock rule.** Unlocking a monitor happens-before every subsequent lock of that *same* monitor by any thread. This is what makes `synchronized` work: everything a thread wrote before releasing a lock is visible to the next thread that acquires that same lock.

```java
private final Object lock = new Object();
private int sharedValue = 0;

void writer() {
    synchronized (lock) {
        sharedValue = 42; // write happens before this unlock
    } // unlock here
}

void reader() {
    synchronized (lock) { // this lock happens-after the writer's unlock
        System.out.println(sharedValue); // guaranteed to see 42, not 0
    }
}
```

**3. Volatile variable rule.** A write to a `volatile` field happens-before every subsequent read of that *same* field by any thread. This is exactly why the stop-flag example above is correct.

```java
private volatile boolean ready = false;
private int data = 0;

void writer() {
    data = 100;      // (a) plain write
    ready = true;     // (b) volatile write — happens-before any later read of 'ready'
}

void reader() {
    if (ready) {                 // (c) volatile read
        System.out.println(data); // guaranteed to see 100, NOT 0 —
    }                              // because (a) happens-before (b) [program order],
}                                  // and (b) happens-before (c) [volatile rule],
                                   // so by transitivity (a) happens-before (c)
```

**4. Thread start/join rules.** A call to `Thread.start()` happens-before any action inside the started thread's `run()`. And every action inside a thread happens-before another thread's successful `join()` on it.

```java
int[] result = new int[1];
Thread worker = new Thread(() -> result[0] = 99); // write happens inside worker's run()
worker.start(); // happens-before every action inside worker.run()
worker.join();  // worker's actions happen-before this returning
System.out.println(result[0]); // guaranteed to see 99, safely, no lock or volatile needed
```

**5. Final field rule.** If an object is properly constructed (its constructor doesn't leak `this` before finishing), the values of its `final` fields are guaranteed visible to any thread that gets a reference to that object after construction — no synchronization needed for those fields specifically.

```java
public final class ImmutablePoint {
    private final int x, y; // final fields
    public ImmutablePoint(int x, int y) { this.x = x; this.y = y; }
    public int getX() { return x; }
    public int getY() { return y; }
}
// Any thread that receives a reference to a fully-constructed ImmutablePoint
// is guaranteed to see the correct x and y, even with zero synchronization —
// as long as the reference wasn't published before the constructor finished.
```

**6. Transitivity.** If A happens-before B, and B happens-before C, then A happens-before C — even if A and C are actions on different threads that never directly synchronize with each other. This is what let the volatile example above chain a plain write through a volatile write/read pair into a guaranteed-visible result.

## Visibility, Atomicity, and Ordering

Nearly every concurrency bug is one (or more) of exactly three failures: **visibility** (a thread doesn't see another thread's latest write), **atomicity** (an operation that looks like one step is actually several, and gets interrupted mid-way), and **ordering** (instructions execute or become visible in a different sequence than the source code implies). Below: three broken examples, each fixed three different ways, with trade-offs.

### Broken counter (atomicity failure)

```java
// BROKEN: count++ is read-modify-write, not atomic — updates get lost under contention
public class BrokenCounter {
    private int count = 0;
    public void increment() { count++; }
    public int get() { return count; }
}
// 10 threads x 100,000 increments each: final count is reliably LESS than 1,000,000
```

**Fix 1 — `synchronized`:**

```java
public class SynchronizedCounter {
    private int count = 0;
    public synchronized void increment() { count++; }
    public synchronized int get() { return count; }
}
```

**Fix 2 — `volatile` alone does NOT work here** (shown earlier) — you'd need `volatile` *plus* some other mechanism to make the read-modify-write atomic, so `volatile` by itself is not a valid fix for a counter.

**Fix 3 — atomic class:**

```java
import java.util.concurrent.atomic.AtomicInteger;

public class AtomicCounter {
    private final AtomicInteger count = new AtomicInteger(0);
    public void increment() { count.incrementAndGet(); }
    public int get() { return count.get(); }
}
```

### Broken flag (visibility failure)

```java
// BROKEN: no visibility guarantee — the reading thread may cache 'running' forever
public class BrokenFlag {
    private boolean running = true;
    public void stop() { running = false; }
    public void run() {
        while (running) { /* busy loop, may never see 'false' */ }
    }
}
```

**Fix 1 — `synchronized`** (works, but heavier than needed for a single boolean):

```java
public class SynchronizedFlag {
    private boolean running = true;
    public synchronized void stop() { running = false; }
    public synchronized boolean isRunning() { return running; }
    public void run() {
        while (isRunning()) { /* every check takes a lock */ }
    }
}
```

**Fix 2 — `volatile`** (the idiomatic fix — single variable, single reads/writes, no atomicity needed):

```java
public class VolatileFlag {
    private volatile boolean running = true;
    public void stop() { running = false; }
    public void run() {
        while (running) { /* cheap, correct, no locking */ }
    }
}
```

**Fix 3 — atomic class** (works, but overkill — you're not doing CAS logic on a plain flag):

```java
import java.util.concurrent.atomic.AtomicBoolean;

public class AtomicFlag {
    private final AtomicBoolean running = new AtomicBoolean(true);
    public void stop() { running.set(false); }
    public void run() {
        while (running.get()) { /* correct, but heavier machinery than needed */ }
    }
}
```

### Broken publication (reordering failure)

```java
// BROKEN: reader thread might see 'configured == true' before seeing the fully-set config fields,
// because there's no happens-before edge forcing the writes to become visible in order.
public class BrokenPublication {
    private int timeout;
    private String host;
    private boolean configured = false;

    public void configure(int timeout, String host) {
        this.timeout = timeout;  // (1)
        this.host = host;        // (2)
        this.configured = true;  // (3) — nothing stops (3) being seen before (1)/(2) by another thread
    }

    public void useConfig() {
        if (configured) {
            System.out.println(host + ":" + timeout); // could print a stale/partial host or 0 timeout
        }
    }
}
```

**Fix 1 — `synchronized`** (both methods share a lock, establishing happens-before via the monitor rule):

```java
public class SynchronizedPublication {
    private int timeout;
    private String host;
    private boolean configured = false;
    private final Object lock = new Object();

    public void configure(int timeout, String host) {
        synchronized (lock) {
            this.timeout = timeout;
            this.host = host;
            this.configured = true;
        }
    }

    public void useConfig() {
        synchronized (lock) {
            if (configured) System.out.println(host + ":" + timeout); // always consistent
        }
    }
}
```

**Fix 2 — `volatile`** on the guard flag (relies on the volatile happens-before rule to publish everything written before it):

```java
public class VolatilePublication {
    private int timeout;
    private String host;
    private volatile boolean configured = false;

    public void configure(int timeout, String host) {
        this.timeout = timeout;
        this.host = host;
        this.configured = true; // volatile write: everything above is now visible to any reader
    }

    public void useConfig() {
        if (configured) { // volatile read
            System.out.println(host + ":" + timeout); // guaranteed correct, by the volatile rule
        }
    }
}
```

**Fix 3 — atomic reference to an immutable snapshot** (publish one object atomically instead of three separate fields):

```java
import java.util.concurrent.atomic.AtomicReference;

public class AtomicPublication {
    private record Config(int timeout, String host) {}
    private final AtomicReference<Config> config = new AtomicReference<>();

    public void configure(int timeout, String host) {
        config.set(new Config(timeout, host)); // single atomic reference swap, fully-built object
    }

    public void useConfig() {
        Config c = config.get();
        if (c != null) System.out.println(c.host() + ":" + c.timeout()); // always consistent
    }
}
```

### Trade-offs at a glance

| Approach | Visibility | Atomicity | Ordering | Performance | Best for |
|---|---|---|---|---|---|
| `synchronized` | Yes (via monitor rule) | Yes (whole block) | Yes | Lock overhead; blocks other threads | Multi-field invariants, compound operations |
| `volatile` | Yes | No (single read/write only) | Yes (for that field) | Very cheap, no blocking | Single flags, single published references |
| Atomic classes (`Atomic*`, `LongAdder`) | Yes | Yes (single variable) | Yes | CAS retry cost under heavy contention; `LongAdder` reduces this | Counters, single-variable compare-and-update logic |
| `ReentrantLock` / `ReadWriteLock` / `StampedLock` | Yes | Yes (whole critical section) | Yes | More features than `synchronized`, similar or better cost | Timed/interruptible acquisition, read-heavy workloads, multiple conditions |

## Common Code-Review Interview Pitfalls

1. **Calling `run()` instead of `start()`.** Why it matters: `run()` executes on the calling thread — no new thread, no concurrency, and the bug is easy to miss because the code still "works," just without any parallelism.
   ```java
   // Before
   Thread t = new Thread(task);
   t.run(); // runs on the CURRENT thread
   // After
   t.start(); // actually schedules a new thread
   ```

2. **Swallowing `InterruptedException` without restoring the interrupt flag.** Why it matters: catching and ignoring it erases the cancellation signal, so any outer loop or framework relying on interruption to stop the thread never finds out.
   ```java
   // Before
   try { Thread.sleep(100); } catch (InterruptedException e) { /* ignored */ }
   // After
   try { Thread.sleep(100); } catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
   ```

3. **Using `wait()`/`notify()` (or a `Condition.await()`) with `if` instead of `while`.** Why it matters: spurious wakeups and multiple competing waiters mean the condition must be re-checked after waking — an `if` proceeds on a stale assumption.
   ```java
   // Before
   if (queue.isEmpty()) wait();
   // After
   while (queue.isEmpty()) wait();
   ```

4. **Checking a condition outside the lock, then locking separately (lost wakeup / TOCTOU).** Why it matters: another thread can change the condition and signal in the gap between the check and the lock, and that signal is lost forever.
   ```java
   // Before
   if (isEmpty()) { synchronized (this) { wait(); } }
   // After
   synchronized (this) { while (isEmpty()) wait(); }
   ```

5. **Acquiring locks in inconsistent order across different code paths (deadlock risk).** Why it matters: two threads taking the same two locks in opposite order is the textbook deadlock; it often only shows up under real production load, not in tests.
   ```java
   // Before: thread A locks(a,b); thread B locks(b,a);
   // After: every thread locks in the same global order, e.g. always a before b
   ```

6. **Using `ReentrantLock` without `try`/`finally`.** Why it matters: unlike `synchronized`, nothing releases the lock automatically — an exception between `lock()` and `unlock()` leaks the lock permanently, freezing every other thread that needs it.
   ```java
   // Before
   lock.lock();
   doWork(); // if this throws, the lock is NEVER released
   lock.unlock();
   // After
   lock.lock();
   try { doWork(); } finally { lock.unlock(); }
   ```

7. **Assuming `volatile` makes compound operations like `count++` atomic.** Why it matters: `volatile` only guarantees visibility and ordering for single reads/writes; a read-modify-write is still three separate steps that can interleave across threads.
   ```java
   // Before
   private volatile int count = 0;
   void increment() { count++; } // still lossy under contention
   // After
   private final AtomicInteger count = new AtomicInteger(0);
   void increment() { count.incrementAndGet(); }
   ```

8. **Double-checked-locking singleton missing `volatile` on the instance field.** Why it matters: without `volatile`, another thread can observe a non-null reference to an object whose constructor hasn't finished running, i.e., a partially-built object.
   ```java
   // Before
   private static Singleton instance;
   // After
   private static volatile Singleton instance;
   ```

9. **Forgetting `remove()` on a `ThreadLocal` used inside a pooled executor.** Why it matters: pooled threads are reused across tasks, so stale values silently leak from one task/request into the next — both a memory leak and a data-isolation bug.
   ```java
   // Before
   currentUser.set(user);
   process();
   // After
   currentUser.set(user);
   try { process(); } finally { currentUser.remove(); }
   ```

10. **Using `StampedLock` recursively, assuming it behaves like `ReentrantLock`.** Why it matters: `StampedLock` is not reentrant — a thread re-acquiring its own write lock deadlocks against itself, with no exception to warn you.
    ```java
    // Before
    long s = lock.writeLock();
    recursiveCallThatAlsoLocks(); // deadlocks if it calls lock.writeLock() again on this thread
    // After: restructure so the lock is acquired exactly once per call chain, or use ReentrantLock
    ```

11. **Skipping `validate()` after a `StampedLock.tryOptimisticRead()`.** Why it matters: without validation, the "optimistic" read might have raced with a concurrent writer and returned torn or inconsistent data with no error at all.
    ```java
    // Before
    long stamp = lock.tryOptimisticRead();
    double result = x + y; // used without checking if a writer interleaved
    // After
    long stamp = lock.tryOptimisticRead();
    double result = x + y;
    if (!lock.validate(stamp)) { /* retry with lock.readLock() */ }
    ```

12. **Relying on thread priority for correctness or scheduling guarantees.** Why it matters: priority is only a scheduling hint with JVM/OS-dependent behavior — no ordering or fairness guarantee exists, so "fixing" a race by tweaking priorities is not a real fix.
    ```java
    // Before: "the writer thread has higher priority, so it always runs first"
    writerThread.setPriority(Thread.MAX_PRIORITY); // not a synchronization mechanism
    // After: use an actual ordering guarantee — a lock, a latch, or happens-before via volatile/join
    ```

13. **Using `Thread.stop()` or `Thread.suspend()` to cancel work.** Why it matters: both are deprecated for removal because they can kill a thread mid-update, leaving shared state permanently corrupted, or freeze a thread while it holds a lock other threads need.
    ```java
    // Before
    worker.stop(); // can leave shared state half-updated
    // After
    volatile boolean cancelled; // or Thread.interrupt() + cooperative checks
    void run() { while (!cancelled) { doUnitOfWork(); } }
    ```

14. **Mixing `synchronized(this)` in one method with `synchronized(SomeClass.class)` in another for the same shared state.** Why it matters: these are two different locks, so "synchronizing" both methods provides zero actual mutual exclusion between them.
    ```java
    // Before
    public synchronized void instanceMethod() { sharedStatic++; }       // locks 'this'
    public static synchronized void staticMethod() { sharedStatic++; } // locks the Class object
    // After: pick one lock and use it consistently for all access to sharedStatic
    ```

15. **Holding a lock while doing slow I/O, network calls, or `Thread.sleep()`.** Why it matters: every other thread waiting on that lock is blocked for the full duration of the slow operation, turning a local slowdown into a system-wide bottleneck.
    ```java
    // Before
    synchronized (lock) { callSlowRemoteService(); updateSharedState(); }
    // After
    Result r = callSlowRemoteService(); // do the slow part outside the lock
    synchronized (lock) { updateSharedState(r); } // hold the lock only for the fast part
    ```

16. **Publishing a partially-constructed object by leaking `this` from inside a constructor.** Why it matters: this breaks the final-field happens-before guarantee — other threads can see the object before construction finishes, e.g., via a listener registration or a static collection add inside the constructor.
    ```java
    // Before
    public Widget() { registry.add(this); this.id = computeId(); } // 'this' escapes before id is set
    // After
    public Widget() { this.id = computeId(); }
    // then, after construction completes: registry.add(widget);
    ```

17. **Choosing `AtomicLong` for a hot counter under heavy multi-threaded contention.** Why it matters: every thread's CAS attempt targets the same memory location, so under high contention most CAS attempts fail and retry, wasting CPU; `LongAdder` spreads updates across cells for far better throughput.
    ```java
    // Before
    private final AtomicLong hits = new AtomicLong();
    void recordHit() { hits.incrementAndGet(); } // hot spot under heavy contention
    // After
    private final LongAdder hits = new LongAdder();
    void recordHit() { hits.increment(); } // much less contention; sum() when you need the total
    ```

18. **Treating `ReadWriteLock`/`StampedLock` as a free performance win without measuring.** Why it matters: for short critical sections or write-heavy workloads, the extra bookkeeping of tracking readers/writers can be slower than a plain `synchronized` block or `ReentrantLock` — reviewers should ask "was this actually measured?"
    ```java
    // Before: reflexively reaching for ReentrantReadWriteLock on a tiny, write-heavy map
    // After: benchmark synchronized/ReentrantLock vs ReadWriteLock under the REAL read/write ratio
    //        before committing to the more complex API
    ```
