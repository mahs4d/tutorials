# 12. Memory Management

Memory management is where "the code compiles" and "the code survives production" part ways. A code reviewer who understands garbage collection, common leak patterns, and the diagnostic tools can catch problems that unit tests never will — a slowly growing heap, a rogue `ThreadLocal`, a GC pause that violates an SLA. This chapter covers how the JVM's garbage collectors work, how to spot and debug memory leaks, what escape analysis actually does (and doesn't) for you, and the real commands you'll reach for when production memory goes wrong. Examples target Java 21+ HotSpot, since that is the current LTS baseline for interviews and production.

## Table of Contents

- [Garbage Collection](#garbage-collection)
- [Serial GC](#serial-gc)
- [Parallel GC](#parallel-gc)
- [G1 GC](#g1-gc)
- [ZGC](#zgc)
- [Shenandoah](#shenandoah)
- [Collector Comparison](#collector-comparison)
- [Memory Leaks](#memory-leaks)
- [Escape Analysis](#escape-analysis)
- [JVM Flags and Diagnostics](#jvm-flags-and-diagnostics)
- [Garbage Collection Tuning](#garbage-collection-tuning)
- [JVM Performance Tuning](#jvm-performance-tuning)
- [Profiling and Monitoring](#profiling-and-monitoring)
- [JFR (JDK Flight Recorder)](#jfr-jdk-flight-recorder)
- [JMC (JDK Mission Control)](#jmc-jdk-mission-control)
- [jcmd](#jcmd)
- [jmap](#jmap)
- [jstack](#jstack)
- [Worked Walkthrough: Debugging an OutOfMemoryError](#worked-walkthrough-debugging-an-outofmemoryerror)
- [Worked Walkthrough: Debugging a Deadlock with jstack](#worked-walkthrough-debugging-a-deadlock-with-jstack)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Garbage Collection

**Garbage collection (GC)** is the JVM automatically finding objects nobody can reach anymore and freeing their memory. You never call `free()` like in C — the collector does it for you. That's a huge productivity win, but it means understanding *how* it decides what's garbage is part of writing (and reviewing) correct, fast Java.

**Reachability and GC roots.** An object is "alive" if it's *reachable* — if you can follow a chain of references to it starting from a **GC root**. GC roots are the starting points the collector trusts by definition: local variables and parameters on any thread's stack, active static fields, JNI references, and a few JVM-internal references (like classes loaded by the bootstrap classloader). If an object can't be reached by following references from any GC root, it's garbage — even if it still has a valid memory address and even if two garbage objects point at each other (a reference cycle does **not** keep objects alive in Java, unlike naive reference counting).

```java
class Node {
    Node next;
}

Node a = new Node();      // 'a' is a GC root (local variable) -> object A is reachable
Node b = new Node();
a.next = b;                // B is reachable through A
a = null;                  // A is no longer reachable from any root...
// ...so A AND B are both garbage now, even though B still exists in memory,
// because nothing reachable from a GC root points to A anymore.
```

**Mark-sweep-compact.** This is the classic three-phase algorithm most collectors build on:
1. **Mark** — walk from GC roots and flag every reachable object.
2. **Sweep** — reclaim the memory used by everything unmarked.
3. **Compact** — slide the surviving objects together to remove gaps, so the heap has one large free region instead of fragmented holes.

Compaction matters because fragmented free memory can make a large allocation fail even though the *total* free space is enough — like having plenty of parking but no single spot big enough for a bus.

**Copying collectors.** Instead of sweeping in place, a copying collector moves all live objects from one memory region ("from-space") to another ("to-space") and then treats the entire from-space as free. This is very fast when most objects are garbage (which is typical for young objects) because it only touches *live* objects, not dead ones — cost is proportional to what survives, not to heap size.

**The generational hypothesis.** Empirically, most objects die young — a request-scoped object, a loop temporary, a `String` built for logging. Few objects live a long time (caches, singletons, connection pools). Collectors exploit this by splitting the heap into generations:

- **Young generation** — where new objects are born. Collected frequently and cheaply with a copying collector. A young-gen collection is called a **minor GC**.
- **Old (tenured) generation** — where objects that survive several young collections get promoted ("tenured"). Collected less often but more expensively, since it's usually larger and mark-sweep-compact style. Collecting the old generation (or the whole heap) is a **major GC**; collecting the *entire* heap including metaspace is often called a **full GC**. Full GCs are the ones most likely to cause a noticeable pause.

```
Young Gen                              Old Gen
+---------+---------+---------+       +---------------------+
|  Eden   | Surv. 0 | Surv. 1 |  -->  |   Tenured objects   |
+---------+---------+---------+       +---------------------+
new objects here    ping-pong          long-lived objects promoted here
```

New objects are allocated in **Eden**. When Eden fills up, a minor GC copies survivors into a **survivor space**, ping-ponging between two survivor spaces on each collection, and objects that survive enough cycles get promoted to the old generation.

**TLABs (Thread-Local Allocation Buffers).** To avoid every thread contending on a lock just to bump a heap pointer, each thread gets its own small private chunk of Eden (a TLAB) to allocate into without synchronization. Allocation becomes "bump a pointer in my own buffer" — extremely fast — until the TLAB is full, at which point the thread grabs a new one. This is why object allocation in Java is often faster than a naive C `malloc`.

**Stop-the-world (STW) pauses.** Most collectors need to pause all application threads at some point — for example, to safely walk thread stacks for GC roots without them changing underneath the collector. This pause is called **stop-the-world**. The whole story of modern collector design (G1, ZGC, Shenandoah) is about shrinking STW pauses from "proportional to heap size" down to a few milliseconds, regardless of how big the heap is.

**Minor, major, and full GC — a quick disambiguation.** Interviewers like this because the terms are used loosely in the wild:

| Term | What it collects | Typical cost | Triggered by |
|---|---|---|---|
| **Minor GC** | Young generation only (Eden + survivor spaces) | Cheap, frequent | Eden fills up |
| **Major GC** | Old generation (definitions vary slightly by collector) | Expensive, less frequent | Old gen fills up / promotion pressure |
| **Full GC** | Entire heap, and usually metaspace too | Most expensive, rarest | Old gen exhausted, explicit `System.gc()`, metaspace pressure |

In G1, the line between "major" and "full" is blurrier than in Parallel GC, because G1 does most old-gen work incrementally and concurrently — a true G1 full GC (falling back to single-threaded mark-sweep-compact of the whole heap) is meant to be a rare emergency fallback, and seeing one repeatedly in a log is itself a red flag that the collector is under more pressure than it's designed to handle.

**Allocation rate and promotion rate.** Two numbers worth knowing by name because tools report them directly: **allocation rate** is how many bytes per second the application creates (mostly in Eden); **promotion rate** is how many bytes per second survive long enough to be pushed into the old generation. A high allocation rate with a *low* promotion rate is healthy (objects are dying young, exactly as the generational hypothesis predicts). A high promotion rate is a warning sign — it means minor GCs are working harder to fill up the old generation faster, dragging major/full GCs closer together.

**Weak, soft, and phantom references.** Normal ("strong") references keep an object alive. Java also has three weaker kinds, all in `java.lang.ref`:

| Reference type | Collected when | Typical use |
|---|---|---|
| **Strong** (normal) | Never, while reachable | Everyday references |
| **Soft** (`SoftReference`) | GC *may* clear it, typically only under memory pressure | Memory-sensitive caches |
| **Weak** (`WeakReference`) | GC clears it as soon as nothing else strongly reaches it | `WeakHashMap` keys, avoiding leaks in listener maps |
| **Phantom** (`PhantomReference`) | After the object is finalized/unreachable, but before memory is reclaimed; `get()` always returns `null` | Precise cleanup via `Cleaner` |

```java
Map<Key, Value> cache = new java.util.WeakHashMap<>();
// entries disappear automatically once 'Key' has no other strong references —
// useful for caches keyed by objects you don't own the lifecycle of
```

**Why `System.gc()` is wrong.** Calling `System.gc()` only *suggests* a full GC to the JVM — it does not force one, and on some collectors it's an expensive full GC that pauses the whole application. Application code should never call it: it's the collector's job to decide when collection is worthwhile, and it usually knows better than a hardcoded call buried in business logic. (A code reviewer should flag any `System.gc()` call outside of a benchmark harness or manual profiling session.)

**Why `finalize()` is wrong.** `Object.finalize()` was Java's original hook to run cleanup code before an object is reclaimed. It's a bad tool: it runs on an unpredictable finalizer thread at an unpredictable time (or never, if the JVM exits first), it can resurrect objects, it slows GC down, and a throwing finalizer silently swallows the exception. **`finalize()` has been deprecated for removal since Java 9** — don't use it in new code.

```java
// Wrong — deprecated for removal, unpredictable timing
@Override
protected void finalize() {
    connection.close();
}

// Right — deterministic, explicit
try (var connection = openConnection()) {
    // use connection
} // .close() called here, guaranteed

// If you truly need a GC-triggered safety net (not the primary cleanup path):
import java.lang.ref.Cleaner;

class ResourceHolder implements AutoCloseable {
    private static final Cleaner CLEANER = Cleaner.create();
    private final Cleaner.Cleanable cleanable;
    private final State state;

    private static class State implements Runnable {
        Connection connection;
        @Override public void run() { connection.close(); } // no reference back to ResourceHolder!
    }

    ResourceHolder(Connection c) {
        state = new State();
        state.connection = c;
        cleanable = CLEANER.register(this, state);
    }

    @Override public void close() { cleanable.clean(); }
}
```

`Cleaner` is the modern replacement — it still shouldn't be your *primary* cleanup strategy (`try`-with-resources / explicit `close()` should be), but it's a safe backstop, unlike `finalize()`, because its cleanup action must not hold a reference back to the object being cleaned, avoiding the resurrection problem.

## Serial GC

**How it works.** The simplest collector. One single thread does all the work — marking, sweeping, compacting — while every application thread is stopped. No parallelism, no concurrency.

**Pause characteristics.** Pauses are proportional to heap size and can be long, but there's zero coordination overhead between GC threads, so for a *small* heap the pause is small too.

**Heap sweet spot.** Small heaps — typically under a few hundred MB. Think embedded devices, small CLI tools, containers with 1-2 CPUs and constrained memory.

**Flag to enable:** `-XX:+UseSerialGC`

**When to pick it.** Single-core or memory-constrained environments where the simplicity and low overhead outweigh the fact that pauses aren't parallelized. Rarely the right choice for a server with multiple cores and a heap above a few hundred MB.

Example log line — notice the pause scales with the *whole* young generation size, with no "(Normal)"/"(Concurrent Start)" qualifiers because there's only one kind of pause:
```
[gc] GC(5) Pause Young (Allocation Failure) 96M->12M(128M) 45.112ms
```

## Parallel GC

**How it works.** Same generational, mark-sweep-compact/copying design as Serial GC, but both the young and old generation collections use **multiple threads** to do the marking, copying, and compacting work. The application is still fully stopped during a collection (it's still stop-the-world) — the parallelism just makes that pause shorter by spreading the work across cores.

**Pause characteristics.** Optimizes for **throughput** (maximum total work done by the application over time), not for pause length. Pauses can still be noticeable, especially full GCs on a large old generation, but total CPU spent on GC (versus useful work) is low.

**Heap sweet spot.** Medium to large heaps on multi-core machines, for batch jobs and throughput-sensitive workloads that don't have a strict pause-time SLA.

**Flag to enable:** `-XX:+UseParallelGC`

**When to pick it.** Batch processing, offline data pipelines, scientific computing — anything where total job completion time matters more than any individual pause, and there's no interactive user waiting on a response.

Example log line — same shape as Serial's, but the wall-clock duration is shorter for a similarly sized young generation because multiple GC threads did the copying in parallel:
```
[gc] GC(5) Pause Young (Allocation Failure) 96M->10M(128M) 12.847ms
```

## G1 GC

**How it works.** **G1 (Garbage-First)** divides the heap into many equally sized **regions** (typically 1-32 MB each) rather than into two contiguous young/old blocks. Each region can play the role of Eden, survivor, or old space, and this assignment can change over time. G1 tracks how much garbage is in each region and, true to its name, collects the regions with the *most garbage first* — maximizing reclaimed memory per unit of pause time. Most of its work (finding live objects, some copying) happens concurrently with the application; only short phases are stop-the-world.

**Pause characteristics.** Designed around a **pause-time goal** you set, not a fixed algorithm — G1 tries to keep pauses under a target (default 200 ms) by choosing how many regions to collect in each cycle. It trades a bit of throughput for much more predictable pauses than Parallel GC.

**Heap sweet spot.** Medium to large heaps (multi-GB, up into hundreds of GB) on multi-core machines — this is the general-purpose "good enough for almost everything" choice.

**Flag to enable:** `-XX:+UseG1GC` (this is also the **default collector since JDK 9**, so usually no flag is needed at all)

**When to pick it.** The default choice for most server applications: web services, microservices, anything wanting a balance of throughput and bounded pauses without special tuning. Tune the target with `-XX:MaxGCPauseMillis=<ms>`.

**Humongous objects.** Any object at least half the size of a single G1 region (region size defaults based on heap size, typically 1-32 MB) is classified as **humongous** and allocated directly into one or more dedicated regions, bypassing the normal young-gen path entirely. Humongous objects are never copied during young collections (too expensive to move), so they can only be reclaimed by an old-gen/full collection — a service that allocates lots of large arrays or buffers (e.g. big `byte[]` responses, large `ArrayList` backing arrays) can suffer humongous-allocation-driven full GCs even though the heap "looks" mostly empty. Symptom in the log: frequent `Pause Young (Concurrent Start) (G1 Humongous Allocation)` entries. Fix by increasing `-XX:G1HeapRegionSize` so fewer objects cross the humongous threshold, or by reducing how often the code allocates very large single objects (e.g. streaming/chunking instead of buffering the whole payload).

Example log line — G1 pauses are qualified with *why* the pause happened:
```
[gc] GC(41) Pause Young (Concurrent Start) (G1 Evacuation Pause) 612M->140M(2048M) 9.774ms
[gc] GC(42) Pause Remark 2.011ms
[gc] GC(43) Pause Cleanup 0.415ms
```
`Concurrent Start` kicks off a background marking cycle for the old generation; `Remark` and `Cleanup` are the short stop-the-world phases that bookend that concurrent marking work.

## ZGC

**How it works.** **ZGC (Z Garbage Collector)** is designed for extremely low pause times, largely independent of heap size. It does almost all of its work — marking, relocating objects, remapping references — concurrently with the running application, using **colored pointers** (metadata bits embedded in the reference itself) and **load barriers** (a small check the JVM inserts on reference reads) to let it move objects while the application keeps running, safely redirecting any thread that reads a stale reference. Since JDK 21, ZGC has a **generational mode** that adds young/old generations (like G1) for much better throughput, because it can collect young garbage cheaply instead of scanning the whole heap every cycle.

**Pause characteristics.** Sub-millisecond stop-the-world pauses, and — this is the headline feature — pause time does **not** scale with heap size. A 10 GB heap and a 10 TB heap have similarly tiny pauses.

**Heap sweet spot.** Large to very large heaps (multi-GB to multi-TB) where minimizing worst-case pause latency matters more than raw throughput or memory footprint (ZGC uses more memory and CPU overhead for its barriers and metadata than G1).

**Flag to enable:** `-XX:+UseZGC` (generational mode is the default *shape* of ZGC as of JDK 21+; explicitly request it with `-XX:+UseZGC -XX:+ZGenerational` on JDK 21, and it's simply the only mode from JDK 23 onward)

**When to pick it.** Latency-critical services with strict pause-time SLAs (trading systems, real-time bidding, anything where a 500 ms pause is a user-visible incident) and large heaps. **Non-generational ZGC is deprecated for removal starting JDK 23** — generational ZGC is the production-ready, actively developed mode going forward.

Example log line — note the tiny STW pauses; almost everything else in a ZGC cycle is logged as `Concurrent`, not `Pause`:
```
[gc] GC(88) Pause Mark Start 0.012ms
[gc] GC(88) Pause Mark End 0.031ms
[gc] GC(88) Pause Relocate Start 0.028ms
[gc] GC(88) Garbage Collection (Allocation Rate) 8192M(50%)->6144M(37%)
```

## Shenandoah

**How it works.** Shenandoah (developed by Red Hat) has a similar goal to ZGC — pause times independent of heap size — but a different mechanism: it uses **Brooks pointers** (an extra forwarding pointer stored with each object) and concurrent evacuation to move objects while the application runs, along with concurrent marking and reference updating. Conceptually it's a sibling approach to ZGC solving the same problem with different bookkeeping.

**Pause characteristics.** Very low, largely heap-size-independent pauses, comparable in spirit to ZGC, though the two have different throughput/footprint trade-offs depending on workload — benchmark both if latency is critical.

**Heap sweet spot.** Same territory as ZGC: large heaps, latency-sensitive workloads.

**Flag to enable:** `-XX:+UseShenandoahGC`

**When to pick it.** Available in most OpenJDK builds (Red Hat, Eclipse Temurin/Adoptium, Amazon Corretto) but notably **not shipped in Oracle's own JDK builds**. Pick it when you're already on a distribution that includes it and want ZGC-like pause behavior, or want to compare it against ZGC for your specific workload.

## Collector Comparison

| Collector | Pause goal | Throughput | Heap size sweet spot | Enable flag | Default since |
|---|---|---|---|---|---|
| **Serial** | Long, but simple | Low (single-threaded) | Small (< few hundred MB) | `-XX:+UseSerialGC` | Never default on server VMs |
| **Parallel** | Long, throughput-first | Highest | Medium-large, batch jobs | `-XX:+UseParallelGC` | Default in JDK 8 |
| **G1** | Configurable target (default 200 ms) | High | Medium-large, general purpose | `-XX:+UseG1GC` | **Default since JDK 9** |
| **ZGC** | Sub-millisecond, size-independent | Good (generational mode) | Large-huge, latency-critical | `-XX:+UseZGC` | Not default; opt-in |
| **Shenandoah** | Sub-millisecond, size-independent | Good | Large, latency-critical | `-XX:+UseShenandoahGC` | Not default; opt-in, OpenJDK-only |

Rule of thumb for an interview: **G1 unless you have a specific reason not to.** Reach for ZGC or Shenandoah when pause time is the dominant concern and you have the memory/CPU budget to spend on it; reach for Parallel for pure batch throughput; reach for Serial only in tiny, constrained environments.

## Memory Leaks

A Java "memory leak" doesn't mean memory vanishes — it means objects stay **reachable** long after they're logically dead, so the GC can never reclaim them. The heap slowly grows until an `OutOfMemoryError`. Here are the classic causes.

**Static collections that only grow.**

```java
public class SessionRegistry {
    // Wrong: nothing ever removes entries, so this grows forever
    private static final Map<String, Session> SESSIONS = new HashMap<>();

    public static void register(String id, Session s) {
        SESSIONS.put(id, s);
    }
    // no remove() is ever called
}
```
A `static` field lives as long as the class is loaded — effectively forever in most applications. If nothing ever calls `remove`, this is an unbounded leak in plain sight.

**Unbounded caches.**

```java
// Wrong: a plain HashMap used as a cache has no eviction policy
private final Map<Long, Report> cache = new HashMap<>();

public Report get(Long id) {
    return cache.computeIfAbsent(id, this::loadReport); // grows forever
}
```
```java
// Right: bound it, with size or time-based eviction
private final Cache<Long, Report> cache = Caffeine.newBuilder()
        .maximumSize(10_000)
        .expireAfterAccess(Duration.ofMinutes(30))
        .build();
```

**Listeners/callbacks never unregistered.**

```java
public class EventBus {
    private final List<Listener> listeners = new ArrayList<>();
    public void subscribe(Listener l) { listeners.add(l); }
    // if callers subscribe but never call a matching unsubscribe(l),
    // every subscriber (and everything it references) leaks forever
}
```
This is especially common in UI frameworks and long-lived event buses. Always pair `subscribe`/`addListener` with a matching `unsubscribe`/`removeListener`, ideally enforced with try-finally or `AutoCloseable`.

**`ThreadLocal` in pooled threads.**

```java
// Wrong: thread pools reuse threads, so a ThreadLocal that's set but
// never removed keeps its value (and everything it references) alive
// for the LIFE OF THE POOLED THREAD, across unrelated tasks
private static final ThreadLocal<UserContext> CONTEXT = new ThreadLocal<>();

void handleRequest(Request r) {
    CONTEXT.set(loadUserContext(r));
    process(r);
    // missing CONTEXT.remove() — the UserContext (and its whole reference
    // graph) stays attached to this pool thread until it's overwritten
}
```
```java
// Right
void handleRequest(Request r) {
    CONTEXT.set(loadUserContext(r));
    try {
        process(r);
    } finally {
        CONTEXT.remove(); // always clean up, even on exception
    }
}
```

**ClassLoader leaks.** Common in application servers that redeploy webapps without restarting the JVM. If any object (a thread, a static reference in a shared library, a JDBC driver registered in `DriverManager`) keeps a reference into classes loaded by the *old* deployment's classloader, that entire classloader — and every class and static field it loaded — cannot be garbage collected, even after redeploy. Symptom: `OutOfMemoryError: Metaspace` (or the older `PermGen space`) that grows with every redeploy.

**`substring` history (old JDKs).** Before Java 7u6, `String.substring()` shared the backing `char[]` array with the original string — a small substring of a huge string kept the *entire* huge array alive.

```java
// Only a real leak on JDK <= 6 / early 7 (fixed since 7u6 — substring copies now)
String hugeLine = readEntireFileAsOneLine(); // 10 MB string
String tinyPart = hugeLine.substring(0, 5);  // pre-fix: still references the 10 MB array!
```
On modern JDKs (7u6+) `substring` always copies, so this specific leak is historical — but it's still a favorite interview question, and the general lesson (watch what a "small" object secretly references) still applies to things like `ByteBuffer` slices.

**Non-static inner classes holding outer references.**

```java
public class ReportGenerator {           // large object with lots of state
    private byte[] hugeBuffer = new byte[100_000_000];

    class ProgressListener {              // non-static inner class!
        void onProgress(int percent) { System.out.println(percent); }
    }

    ProgressListener newListener() {
        return new ProgressListener();    // implicitly holds ReportGenerator.this
    }
}
```
Every instance of a non-static inner class carries a hidden reference to its enclosing instance. If that listener is registered somewhere long-lived (a static event bus, a cache), the *entire* outer `ReportGenerator` — including its 100 MB buffer — leaks with it. Fix: make the inner class `static` (or a top-level class) if it doesn't need the outer instance, and pass in only the specific data it needs.

**Unclosed resources.**

```java
// Wrong: if readAll() throws, the stream is never closed — leaks a file handle
// and, since streams often hold internal buffers, JVM-heap memory too
FileInputStream in = new FileInputStream(path);
byte[] data = in.readAllBytes();
in.close();

// Right
try (FileInputStream in = new FileInputStream(path)) {
    byte[] data = in.readAllBytes();
} // closed automatically, even on exception
```

**Direct (off-heap) buffer leaks.** `ByteBuffer.allocateDirect()` and memory-mapped files allocate native memory *outside* the Java heap, freed only when the buffer object itself is garbage collected and its cleaner runs. Holding on to direct buffers longer than necessary (e.g., caching them the same way you'd cache a normal object) leaks native memory that **won't show up in a heap dump or `-Xmx` usage at all** — it shows up as the process's resident memory (RSS) growing past what the heap accounts for, which is a common source of confusing container OOM-kills where "the heap looks fine."

```java
// Wrong: direct buffers cached indefinitely, never explicitly released
private static final Map<String, ByteBuffer> BUFFER_CACHE = new HashMap<>();
ByteBuffer buf = ByteBuffer.allocateDirect(64 * 1024 * 1024); // 64 MB native memory
BUFFER_CACHE.put(key, buf); // never removed — native memory leak, invisible on the heap

// Right: bound the cache and/or size direct memory explicitly with
// -XX:MaxDirectMemorySize=<size> so a leak throws an OutOfMemoryError instead
// of silently exhausting container memory
```

**Summary of causes and fixes:**

| Cause | Why it leaks | Fix |
|---|---|---|
| Static collection that only grows | Static fields live for the program's lifetime | Bound size, add explicit removal |
| Unbounded cache | No eviction policy | Use a cache library with `maximumSize`/TTL |
| Listener never unregistered | Event source outlives the "logical" subscriber | Pair every `subscribe` with `unsubscribe` |
| `ThreadLocal` in a pool | Pooled threads outlive individual tasks | Always call `remove()` in a `finally` |
| ClassLoader leak | A live reference reaches into an "unloaded" deployment's classes | Deregister drivers/threads on shutdown; restart JVM on redeploy if needed |
| `substring` history | Pre-7u6 shared backing array | N/A on modern JDKs; still watch buffer slices |
| Non-static inner class | Hidden reference to outer instance | Make it `static` unless it needs the outer instance |
| Unclosed resource | Native/buffered state never released | `try`-with-resources |
| Direct buffer left cached | Off-heap memory tied to buffer object's lifecycle | Bound cache; set `-XX:MaxDirectMemorySize` |

**How to find a leak: heap dump + dominator tree.**

1. Capture a heap dump while the process is exhibiting the growth (or trigger one automatically on OOM — see [JVM Flags and Diagnostics](#jvm-flags-and-diagnostics)):
   ```bash
   jcmd <pid> GC.heap_dump /tmp/heap.hprof
   ```
2. Open it in a heap dump analyzer (Eclipse MAT, or JFR/JMC's heap dump view, or VisualVM).
3. Look at the **dominator tree**, not just "biggest objects." An object X *dominates* object Y if every path from a GC root to Y passes through X — meaning if X were freed, Y would become garbage too. The dominator tree groups the *retained* size (an object plus everything it exclusively keeps alive) under its dominator, so the true "root cause" object floats to the top instead of being hidden among thousands of small leaked instances.
4. Compare two heap dumps taken minutes apart (MAT's "compare" feature) to see which object counts are *still climbing* — that's a much stronger leak signal than a single snapshot, since some growth is normal caching, not a leak.
5. Once you've found the dominating object (say, a `HashMap` field on some `SessionRegistry`), trace back through its "path to GC roots" to find *why* it's still reachable, then fix the reachability (add eviction, call `remove`, unregister the listener, etc.).

## Escape Analysis

**What it is.** Escape analysis is a JIT compiler optimization (see Chapter 11) that determines whether an object created inside a method can be observed — can "escape" — outside that method (returned, stored in a field, passed to another thread, etc.). If the JIT can *prove* an object never escapes its allocating method or thread, it can apply optimizations that would be unsafe for an object visible elsewhere.

**Scalar replacement.** If an object provably never escapes, the JIT can skip allocating it as an object on the heap at all and instead break it into its individual primitive fields ("scalars"), keeping those in registers or on the stack — exactly as if you'd hand-written the method using separate local variables instead of a wrapper object.

```java
class Point {
    int x, y;
    Point(int x, int y) { this.x = x; this.y = y; }
}

int distanceSquared(int x1, int y1, int x2, int y2) {
    Point p1 = new Point(x1, y1); // may never actually be allocated on the heap
    Point p2 = new Point(x2, y2); // if the JIT proves neither escapes this method
    int dx = p1.x - p2.x;
    int dy = p1.y - p2.y;
    return dx * dx + dy * dy;
}
```

**Lock elision.** Similarly, if the JIT proves an object is only ever visible to one thread (it never escapes to another thread), any `synchronized` block guarding that object is provably uncontended — no other thread could possibly be waiting on that monitor — so the JIT can remove the locking overhead entirely.

```java
void process() {
    StringBuilder sb = new StringBuilder(); // local, never escapes this method
    synchronized (sb) {                      // lock is pointless — nothing else can see 'sb'
        sb.append("data");
    }
    // JIT may elide the lock/unlock entirely
}
```

**Why "objects are always on the heap" is not strictly true.** It's a common simplification for beginners, and it's *usually* true — but escape analysis is a real, everyday counterexample. A non-escaping object can live entirely in registers/stack, never touching the heap and never costing the GC anything. This is why microbenchmarks that appear to allocate millions of objects sometimes show suspiciously low GC activity — the JIT proved the objects never escaped and optimized the allocation away (this is also a classic JMH benchmarking pitfall — see Chapter 20).

**Why you should NOT design code around it.** Escape analysis is a best-effort, unguaranteed, JIT-tier-dependent optimization:
- It only kicks in after the JIT has profiled the method enough to compile it at a high tier (see Chapter 11 on JIT compilation) — cold code gets no benefit.
- Any change that makes escape harder to prove — passing the object to an interface method the JIT can't fully inline, storing it in a collection "just for logging," returning it conditionally — silently disables the optimization, with no compiler warning.
- It's a JVM/version-specific implementation detail, not part of the Java Language Specification. Different JVMs (or the same JVM with different flags) may or may not apply it.

Write clear code — small objects, minimal scope, no unnecessary escapes — and let escape analysis take advantage of it *if* it can. Never rely on it being active as a correctness or capacity-planning assumption.

## JVM Flags and Diagnostics

Flags are grouped into standard (`-X...`), and the vast "extra" (`-XX:...`) category, which itself splits into product flags (safe, documented) and experimental/diagnostic flags (need `-XX:+UnlockExperimentalVMOptions` or `-XX:+UnlockDiagnosticVMOptions`).

Commonly needed flags for memory work:

| Flag | Purpose |
|---|---|
| `-Xms<size>` | Initial heap size (e.g. `-Xms512m`) |
| `-Xmx<size>` | Maximum heap size (e.g. `-Xmx4g`) |
| `-Xss<size>` | Thread stack size |
| `-XX:MetaspaceSize=<size>` | Initial metaspace size before triggering a GC to expand it |
| `-XX:MaxMetaspaceSize=<size>` | Cap on metaspace (class metadata) — unbounded by default! |
| `-XX:+HeapDumpOnOutOfMemoryError` | Automatically write a `.hprof` heap dump when an `OutOfMemoryError` is thrown |
| `-XX:HeapDumpPath=<path>` | Where to write that automatic heap dump |
| `-XX:+PrintFlagsFinal` | Print the effective value of every JVM flag (great for checking what a collector chose automatically) |
| `-Xlog:gc*:file=gc.log:time,uptime,level,tags` | Unified logging: write detailed GC logs with timestamps to a file |
| `-XX:MaxDirectMemorySize=<size>` | Cap on off-heap `ByteBuffer.allocateDirect()` memory — unbounded by default |
| `-XX:+ExitOnOutOfMemoryError` | Kill the JVM immediately on any `OutOfMemoryError`, instead of limping along in a corrupted state (useful under an orchestrator that will restart the pod/container) |
| `-XX:+UseStringDeduplication` | (G1 only) Collapse duplicate `String` backing `char[]/byte[]` arrays during GC to save heap — helpful for apps that hold many identical strings (e.g. parsed JSON keys) |
| `-XX:+AlwaysPreTouch` | Commit and zero all heap pages at startup instead of lazily — trades slower startup for eliminating page-fault pauses later, useful with `-Xms == -Xmx` |
| `-XX:NativeMemoryTracking=summary` | Turn on Native Memory Tracking (NMT) to account for *non-heap* JVM memory (thread stacks, GC structures, code cache, metaspace) |

```bash
# See exactly what heap size G1 picked by default on this machine
java -XX:+PrintFlagsFinal -version | grep -i heapsize

# Turn on detailed GC logging to a rotating file
java -Xlog:gc*:file=gc.log:time,uptime,level,tags:filecount=5,filesize=10M -jar app.jar
```

**Native Memory Tracking (NMT).** The heap is only part of a JVM process's memory footprint. NMT breaks down exactly where the *rest* went — thread stacks, GC bookkeeping, the JIT code cache, metaspace, internal buffers — which is exactly what you need when RSS (resident memory) is much bigger than `-Xmx` would suggest (a classic container OOM-kill mystery).

```bash
# Enable at startup (small, constant overhead — safe for production)
java -XX:NativeMemoryTracking=summary -jar app.jar

# Query it later with jcmd
jcmd <pid> VM.native_memory summary
```
```
Total: reserved=5324912KB, committed=2109344KB
-               Java Heap (reserved=4194304KB, committed=1048576KB)
-                   Class (reserved=1069056KB, committed=45120KB)
-                  Thread (reserved=32784KB, committed=32784KB)
-                      GC (reserved=25368KB, committed=25368KB)
-                  Symbol (reserved=15236KB, committed=15236KB)
```
If "Thread" is unexpectedly huge, that's often thousands of leaked/unbounded threads each holding a full stack (`-Xss`, default ~512KB-1MB each) — a different flavor of "memory leak" than a heap leak, but just as real.

Reading a GC log line (unified logging, JDK 9+):
```
[2026-08-07T10:15:22.123+0000][3.456s][info][gc] GC(12) Pause Young (Normal) (G1 Evacuation Pause) 512M->128M(1024M) 8.213ms
```
Read this as: at wall-clock time `10:15:22`, `3.456s` after JVM start, GC cycle #12 was a young "Normal" pause caused by a G1 evacuation. Heap usage went from 512 MB *before* the collection to 128 MB *after*, out of a current heap capacity of 1024 MB, and the whole pause took 8.213 ms. Watch three things across many lines: pause **duration** (spikes = latency problem), the **before→after gap** (how much was actually reclaimed — if it's small and shrinking over time, that's a leak signature), and **frequency** (collections getting closer together = allocation rate is outrunning what the heap can absorb).

## Garbage Collection Tuning

Tuning a collector means adjusting a handful of knobs, always **after** you've measured, never before:

- **Pick the pause-time target, not a lower-level knob, first.** For G1: `-XX:MaxGCPauseMillis=100`. G1 will then choose region counts and collection sets to try to hit that target — you're telling it *what you want*, not *how to get there*.
- **Heap sizing.** A too-small heap causes frequent GCs; a too-large heap causes rare but longer full GCs and wastes memory. Start with `-Xms` and `-Xmx` set to the *same* value in server/container deployments (see [JVM Performance Tuning](#jvm-performance-tuning)) so the heap doesn't spend time resizing itself under load.
- **New (young) generation size.** `-XX:NewRatio=<n>` (old:young ratio) or explicit `-XX:NewSize=<size>` — a larger young gen means fewer, but bigger, minor GCs; too large and minor GC pauses grow.
- **Survivor space tuning.** `-XX:SurvivorRatio=<n>` — rarely needed unless promotion is happening too eagerly or too late (check with `-Xlog:gc+age=trace`).
- **Concurrent GC threads.** `-XX:ConcGCThreads=<n>` for G1/ZGC/Shenandoah's background work; `-XX:ParallelGCThreads=<n>` for the STW parallel phases. Defaults scale with CPU count and are usually fine.
- **Watch for promotion failure / to-space exhaustion.** If the old generation doesn't have enough free space to accept everything a minor GC needs to promote, the collector falls back to a full GC to make room — an expensive, avoidable pause. The GC log shows this explicitly:
  ```
  [gc] GC(77) To-space exhausted
  [gc] GC(78) Pause Full (G1 Evacuation Pause) 3800M->3750M(4096M) 1044.221ms
  ```
  Fixes: grow the heap, grow the young generation's headroom, or address whatever is driving the promotion rate up (see the leak-hunting steps above — a real leak often first shows up as promotion failures before it shows up as an outright OOM).
- **One change at a time, measured.** Change a single flag, run a representative load test, compare GC logs before/after. Changing three flags simultaneously and seeing an improvement tells you nothing about *which* flag helped (or which one is secretly hurting and being masked by the others).

## JVM Performance Tuning

Memory tuning is one piece of the larger performance-tuning picture:

- **`-Xms` == `-Xmx` in containers.** Setting the initial and maximum heap to the *same* value avoids runtime heap-resizing pauses and — critically in Kubernetes/Docker — makes memory usage **predictable**, which matters because the container's memory limit will hard-kill (OOMKilled) the process if the JVM heap plus off-heap usage exceeds it, regardless of what the JVM *thinks* its budget is.
- **`-XX:MaxRAMPercentage=<pct>`** — instead of a hardcoded `-Xmx`, tell the JVM to use a *percentage* of the container's memory limit (default up to 25% historically; explicitly set higher, e.g. `75.0`, for a heap-heavy service). This is the recommended approach for containers because it automatically adapts if the container's memory limit changes (e.g. between environments) without editing JVM flags.
  ```bash
  java -XX:MaxRAMPercentage=75.0 -XX:InitialRAMPercentage=75.0 -jar app.jar
  ```
- **Container awareness.** Modern JVMs (since JDK 10, mature by JDK 17+) read cgroup limits automatically — `Runtime.availableProcessors()` and `MaxRAMPercentage` calculations respect the container's *actual* CPU and memory limits, not the host machine's. Verify with `-XX:+PrintFlagsFinal` inside the container, not on your laptop — a value that looks right locally can be wrong inside a 512 MB container.
- **CPU quota affects GC thread count too.** `-XX:ParallelGCThreads` and `-XX:ConcGCThreads` default based on the *visible* CPU count. In a container with a low CPU quota (e.g. `limits.cpu: "2"` on a 32-core host), an old or misconfigured JVM/runtime combination that doesn't honor cgroup CPU quotas correctly can size GC thread pools for 32 cores, causing them to thrash for CPU time against the application's own threads — worth checking explicitly with `jcmd <pid> VM.flags | grep GCThreads` rather than assuming it's right.
- **Off-heap memory needs its own budget.** Direct buffers (`-XX:MaxDirectMemorySize`), the JIT code cache, metaspace, and thread stacks are all outside `-Xmx` — when sizing a container's memory limit, budget for heap **plus** all of these, not just the heap. A common rule of thumb is heap = ~70-75% of the container limit, leaving room for everything else; verify with NMT rather than guessing.
- **Metaspace sizing.** Metaspace holds class metadata (not object instances) and is off-heap by default with **no cap** unless you set one — a classloader leak (see above) can silently grow metaspace until the *whole container* runs out of memory, not just "the heap." Set `-XX:MaxMetaspaceSize` explicitly in production so a metaspace leak becomes a visible, boring `OutOfMemoryError: Metaspace` instead of an unpredictable OS-level OOM kill.
- **Choosing a pause target.** Ask "what does the SLA actually require?" before picking a collector or a `MaxGCPauseMillis` value. A batch job with no user waiting has no reason to pay ZGC's throughput/memory tax; a p99-latency-sensitive API absolutely does.
- **The golden rule: measure first, one change at a time.** Get a baseline (GC logs, JFR recording, actual latency/throughput numbers) *before* touching any flag. Change one thing. Re-measure under the same load. Keep the change only if it measurably helps your actual metric (not a synthetic microbenchmark). Tuning by folklore ("everyone sets `-XX:NewRatio=2`") without measurement on *your* workload is a code smell in itself.

## Profiling and Monitoring

**Profiling** answers "where is time/memory actually going" with low enough overhead to run in production; **monitoring** answers "is anything wrong right now" continuously over time. For memory specifically, you want to watch: heap usage over time (sawtooth is healthy — a rising floor is a leak), GC pause frequency/duration, allocation rate (bytes/sec), and promotion rate (how much survives into old gen per minor GC). The tools below (JFR, JMC, `jcmd`, `jmap`, `jstack`) are the practical toolbox; a good code reviewer should be able to name which tool answers which question without reaching for a web search.

| Question | Reach for |
|---|---|
| "What's the effective value of every JVM flag right now?" | `jcmd <pid> VM.flags` |
| "What's happening in this app over the last hour, in production, with low overhead?" | JFR recording, analyzed in JMC |
| "Why did we just get an OutOfMemoryError?" | Heap dump (`jmap`/`jcmd`) + Eclipse MAT or JMC |
| "Are threads stuck / is there a deadlock right now?" | `jstack` / `jcmd Thread.print` |
| "What is allocating the most garbage?" | JFR allocation profiling, viewed in JMC |
| "Is memory usage trending up or holding steady over days?" | A metrics pipeline (Micrometer/Prometheus scraping JMX GC/heap metrics) plus dashboards, not one-off tool runs |
| "Quick, lightweight look at live heap/GC/thread stats without a full recording?" | `jstat` (e.g. `jstat -gcutil <pid> 1000`) or VisualVM's live view |
| "Where is CPU time going, down to the line, with minimal overhead?" | `async-profiler` (third-party, flame graphs) or JFR's method-profiling events |

For continuous production monitoring (as opposed to point-in-time debugging), most teams expose JVM metrics through Micrometer or the JMX GC/heap/thread MBeans into Prometheus/Grafana or an APM tool, and alert on trends (heap floor rising over days, GC pause p99 crossing a threshold) rather than manually running these tools reactively. The manual tools in this chapter are for *investigating* an alert or incident, not replacing that baseline monitoring.

## JFR (JDK Flight Recorder)

**JFR** is a low-overhead (typically <1-2%) profiling and event-recording engine built into the JDK itself — no external agent needed. It records hundreds of event types: object allocations (sampled), GC pauses, thread state changes, lock contention, exceptions, class loading, and more, all with timestamps, into a compact `.jfr` binary file you analyze after the fact (or stream live).

```bash
# Start a 60-second recording on a running process, using the built-in
# "profile" settings (more detail, still low overhead) and write it to rec.jfr
jcmd <pid> JFR.start settings=profile duration=60s filename=rec.jfr

# Start recording from the moment the JVM launches (captures startup too)
java -XX:StartFlightRecording=filename=startup.jfr,duration=60s -jar app.jar

# Check on / stop a running recording
jcmd <pid> JFR.check
jcmd <pid> JFR.stop name=1 filename=final.jfr
```

Because overhead is low enough for production, JFR is the tool of choice when you can't reproduce an issue locally and need to capture "what actually happened" on the live system. The resulting `.jfr` file is best read with JMC.

Some of the event categories most relevant to memory work:

| Event category | What it tells you |
|---|---|
| `jdk.ObjectAllocationSample` / `jdk.ObjectAllocationInNewTLAB` | Which classes/call sites are allocating the most, sampled cheaply |
| `jdk.GarbageCollection` / `jdk.GCPhase*` | Every GC pause, its cause, duration, and phase breakdown |
| `jdk.OldObjectSample` | Objects that have survived a long time — a direct signal for leak-hunting without a full heap dump |
| `jdk.ThreadPark` / `jdk.JavaMonitorWait` | Lock contention and thread blocking, useful alongside `jstack` for deadlock/contention analysis |
| `jdk.ExecutionSample` | Periodic stack-trace sampling for CPU hot-spot / "hot method" analysis |

The `jdk.OldObjectSample` event deserves a callout: it's JFR's built-in, low-overhead leak detector — it tracks a sample of objects that have lived unusually long and shows their allocation stack trace and retained size, often letting you spot a leak from a *live* recording without ever needing to pull a full heap dump.

## JMC (JDK Mission Control)

**JMC (JDK Mission Control)** is the GUI tool for *analyzing* JFR recordings (and for live-monitoring a running JVM via JMX). Open a `.jfr` file in JMC and you get: a memory tab showing heap usage and GC pause timelines, an allocation tab ranking classes by bytes allocated (often the fastest way to spot "why is GC busy" — a class allocating way more than expected jumps out immediately), a "hot methods" view from sampled stack traces, and a thread/lock-contention view. JMC ships separately from the JDK (download from Adoptium/Eclipse) but reads recordings from any JDK 11+ vendor. For a code review context: if someone claims "this change reduces allocations," JMC's allocation-by-class view is the objective way to check.

## jcmd

**`jcmd`** is the modern, unified command-line tool for talking to a running JVM — it has mostly superseded the older single-purpose tools (`jstack`, older `jmap` use cases) because it exposes the same diagnostic commands through one consistent interface, and it works reliably even in JVMs where the legacy tools are flaky (e.g. minimal container base images). List a process's PIDs and available commands first:

```bash
# List all running JVMs and their PIDs (like a Java-aware 'ps')
jcmd -l

# List every diagnostic command a specific JVM supports
jcmd <pid> help
```

Common diagnostic commands:

```bash
# Heap summary: generation sizes, usage, GC algorithm in use
jcmd <pid> GC.heap_info

# Show every JVM flag and its current effective value
jcmd <pid> VM.flags

# Dump all thread stacks (equivalent to jstack, but built-in)
jcmd <pid> Thread.print

# Trigger a heap dump without waiting for OOM
jcmd <pid> GC.heap_dump /tmp/heap.hprof

# Start a JFR profiling recording for 60 seconds
jcmd <pid> JFR.start settings=profile duration=60s filename=rec.jfr

# Force a full GC right now (diagnostic use only — never in app code!)
jcmd <pid> GC.run

# Class-level allocation histogram (like jmap -histo:live, built into jcmd)
jcmd <pid> GC.class_histogram

# Native (off-heap) memory breakdown, if NMT was enabled at startup
jcmd <pid> VM.native_memory summary

# Print system properties / general JVM version and uptime info
jcmd <pid> VM.system_properties
jcmd <pid> VM.uptime
```

A quick reference of the commands most relevant to memory and thread debugging:

| Command | What it does |
|---|---|
| `GC.heap_info` | Per-generation heap usage summary |
| `GC.heap_dump <path>` | Write a full heap dump (`.hprof`) |
| `GC.class_histogram` | Live-object counts and bytes by class |
| `GC.run` | Request a full GC (diagnostic use only) |
| `VM.flags` | Effective value of every JVM flag |
| `VM.native_memory summary` | Off-heap memory breakdown (requires NMT enabled) |
| `Thread.print` | Full thread dump, including deadlock detection |
| `JFR.start` / `JFR.stop` / `JFR.check` | Control a Flight Recorder recording |

`GC.heap_info` output looks like this — read the "used" vs "capacity" per generation to see how full each region is and whether the heap has room to grow:

```
garbage-first heap   total 1048576K, used 402384K [0x...]
  region size 1024K, 1024 total, 84 available
 Metaspace       used 45231K, committed 46080K, reserved 1114112K
```

## jmap

**`jmap`** is the older, more narrowly-focused memory tool. Its two most useful invocations:

```bash
# Live-object histogram: class name, instance count, total bytes — sorted by size.
# ":live" forces a GC first so only reachable objects are counted (skip it and you
# also count garbage waiting to be collected, which inflates the numbers).
jmap -histo:live <pid>

# Full heap dump in the standard .hprof binary format, for MAT / JMC / VisualVM
jmap -dump:live,format=b,file=heap.hprof <pid>
```

Histogram output looks like:
```
 num     #instances         #bytes  class name
----------------------------------------------
   1:       842,113      67,369,040  [C                 (char[])
   2:       412,904      23,942,432  java.lang.String
   3:        88,213      21,171,120  com.example.model.OrderLine
```
Read it top-to-bottom by bytes: if `OrderLine` has far more instances than the number of orders you'd expect to have in memory, that's your leak candidate — cross-reference with a heap dump's dominator tree to find *why* they're still reachable.

**`jmap` is largely superseded by `jcmd`.** `jcmd <pid> GC.heap_dump` and `jcmd <pid> GC.class_histogram` do the same jobs, plus `jcmd` is maintained more actively and is more reliable across JVM versions and restricted environments. New tooling knowledge should default to `jcmd`; know `jmap`'s syntax mainly because it still appears in older runbooks, documentation, and — often — interview questions.

## jstack

**`jstack`** dumps the stack trace of every thread in a running JVM — essential for diagnosing hangs, deadlocks, and high-CPU-but-stuck-somewhere issues.

```bash
jstack <pid>
# or, if the process is unresponsive to normal signals:
jstack -F <pid>
```

Each thread entry shows its name, state, and a Java-level stack trace, e.g.:
```
"pool-1-thread-3" #15 prio=5 os_prio=0 tid=0x... nid=0x1a2b waiting on condition [0x...]
   java.lang.Thread.State: WAITING (parking)
        at jdk.internal.misc.Unsafe.park(Native Method)
        at java.util.concurrent.locks.AbstractQueuedSynchronizer.parkAndCheckInterrupt
        at com.example.OrderProcessor.awaitCompletion(OrderProcessor.java:88)
```
Taking two or three `jstack` dumps a few seconds apart and comparing them is a classic trick: a thread stuck at the *exact same line* across all of them is a strong signal of a genuine hang (deadlock, infinite loop, or blocked I/O), whereas a thread that's at a *different* line each time is simply doing normal work.

**Thread states you'll see in a dump:**

| State | Meaning |
|---|---|
| `NEW` | Thread object created, `start()` not yet called |
| `RUNNABLE` | Executing, or ready and waiting for CPU — includes threads doing blocking I/O in native code |
| `BLOCKED` | Waiting to enter a `synchronized` block/method held by another thread |
| `WAITING` | Waiting indefinitely for another thread (`Object.wait()`, `Thread.join()`, `LockSupport.park()` with no timeout) |
| `TIMED_WAITING` | Same as `WAITING` but with a timeout (`Thread.sleep()`, `wait(ms)`, `park(nanos)`) |
| `TERMINATED` | Run to completion |

A thread pool full of `BLOCKED` threads all waiting on the *same* lock, with one thread holding it and itself stuck elsewhere (often in `WAITING` on I/O or another lock), is the classic "everything is stuck behind one slow/stuck thread" pattern — look for the single thread that's the odd one out, not the many that are merely blocked on it.

**`jstack` vs `jcmd Thread.print`.** They produce essentially the same information; `jcmd <pid> Thread.print` is the more modern, actively maintained path (same rationale as `jmap` vs `jcmd` for heap dumps), and it works over the same attach mechanism, so prefer it when starting fresh, while recognizing `jstack` output in existing runbooks and tickets.

## Worked Walkthrough: Debugging an OutOfMemoryError

**Scenario:** a service occasionally crashes with `java.lang.OutOfMemoryError: Java heap space` in production.

**Step 1 — make sure the next crash captures evidence.** Add this flag (ideally it's *always* on in production, before the incident even happens):
```bash
java -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/dumps/ -jar app.jar
```

**Step 2 — while the process is still alive but suspected of leaking, capture a live heap dump too** (don't wait for the crash if you can catch it early):
```bash
jcmd <pid> GC.heap_dump /var/dumps/pre-crash.hprof
```

**Step 3 — check the GC log for the shape of the problem.** If GC logging was already enabled (`-Xlog:gc*:file=gc.log:...`), look at the tail of the log right before the crash:
```
[gc] GC(430) Pause Full (G1 Evacuation Pause) 3980M->3971M(4096M) 812.442ms
[gc] GC(431) Pause Full (G1 Evacuation Pause) 3985M->3978M(4096M) 799.113ms
[gc] GC(432) Pause Full (G1 Evacuation Pause) 3990M->3982M(4096M) 820.977ms
```
This pattern — heap usage barely drops after each *full* GC, and full GCs are happening back-to-back — is the signature of a real leak (versus normal pressure, where after-GC usage would drop substantially).

**Step 4 — open the heap dump in Eclipse MAT (or JMC).** Run MAT's built-in "Leak Suspects" report, or manually open the dominator tree, sort by retained size, and look at the top entries.

**Step 5 — read the dominator tree result**, e.g.:
```
Class Name                                    | Shallow Heap | Retained Heap
com.example.cache.ReportCache @ 0x7f2a...     |          48  |   3,842,199,120
  -> java.util.HashMap$Node[] entries          |     524,288 |   3,842,190,000
```
The `ReportCache` instance alone retains ~3.8 GB — almost the entire heap. That's the leak. Trace its "path to GC roots" in MAT to see it's a `static` field with no eviction policy.

**Step 6 — fix and verify.** Add a bounded eviction policy (e.g. switch to Caffeine with `maximumSize`), redeploy, and confirm with a fresh GC log that full-GC after-usage now drops back down close to the old-gen baseline instead of climbing indefinitely.

## Worked Walkthrough: Debugging a Deadlock with jstack

**Scenario:** a service's health check starts timing out; CPU is idle (not spinning), suggesting threads are stuck waiting, not busy-looping.

**Step 1 — take a thread dump:**
```bash
jstack <pid> > threads.txt
# or: jcmd <pid> Thread.print > threads.txt
```

**Step 2 — search for the deadlock report.** `jstack` detects classic lock-ordering deadlocks automatically and prints a dedicated section:
```
Found one Java-level deadlock:
=============================
"Thread-A":
  waiting to lock monitor 0x00007f... (object 0x000000070a, a java.lang.Object),
  which is held by "Thread-B"
"Thread-B":
  waiting to lock monitor 0x00007f... (object 0x000000070b, a java.lang.Object),
  which is held by "Thread-A"

Java stack information for the threads listed above:
===================================================
"Thread-A":
        at com.example.TransferService.debit(TransferService.java:42)
        - waiting to lock <0x000000070a> (a java.lang.Object)
        - locked <0x000000070b> (a java.lang.Object)
"Thread-B":
        at com.example.TransferService.credit(TransferService.java:58)
        - waiting to lock <0x000000070b> (a java.lang.Object)
        - locked <0x000000070a> (a java.lang.Object)
```

**Step 3 — read it as a cycle.** Thread-A holds lock B's target and wants lock A's target; Thread-B holds the opposite. Neither can proceed — classic circular wait, the textbook deadlock condition.

**Step 4 — trace it back to the code.** Both `debit()` and `credit()` lock two account objects in *opposite order* depending on which account is the caller vs. the target:
```java
// Wrong — lock order depends on argument order, so A->B and B->A both happen
synchronized (fromAccount) {
    synchronized (toAccount) {
        // transfer logic
    }
}
```

**Step 5 — fix with a consistent lock order** (e.g., always lock the account with the smaller ID first), or replace with a higher-level concurrency utility that avoids manual nested locking entirely (`java.util.concurrent` structures, or `ReentrantLock.tryLock` with a timeout so the app can recover instead of hanging forever). See Chapter 13 for lock-ordering strategies in depth.

**Step 6 — verify.** Under the same load test that reproduced the hang, confirm the health check stays responsive and no deadlock section appears in a follow-up `jstack` dump.

## Common Code-Review Interview Pitfalls

1. **Calling `System.gc()` in application code.** Why it matters: it's only a hint the JVM may ignore, and on some collectors it can trigger a genuinely expensive full GC pause in production for no real benefit.
   ```java
   // Before
   cache.clear();
   System.gc(); // "just to be safe" — actually just a pause risk
   // After
   cache.clear(); // trust the collector; profile if there's an actual problem
   ```

2. **Using `finalize()` for cleanup.** Why it matters: it's deprecated for removal, runs at an unpredictable time (or never), and can silently swallow exceptions.
   ```java
   // Before
   @Override protected void finalize() { socket.close(); }
   // After
   try (var socket = openSocket()) { /* use it */ } // deterministic close
   ```

3. **Not calling `ThreadLocal.remove()` in pooled-thread code.** Why it matters: pooled threads outlive any single task, so a forgotten value (and its whole reference graph) leaks for the pool's lifetime and can even leak data between unrelated requests.
   ```java
   // Before
   CONTEXT.set(userContext); process(request);
   // After
   CONTEXT.set(userContext);
   try { process(request); } finally { CONTEXT.remove(); }
   ```

4. **Using an unbounded `HashMap` as a cache.** Why it matters: with no eviction policy, it grows without limit and is a slow-motion `OutOfMemoryError` waiting to happen.
   ```java
   // Before
   private final Map<Long, Report> cache = new HashMap<>();
   // After
   private final Cache<Long, Report> cache =
           Caffeine.newBuilder().maximumSize(10_000).build();
   ```

5. **Registering listeners without a matching unregister.** Why it matters: the listener (and everything it closes over) stays reachable from the event source forever, even after the "subscriber" logically should be gone.
   ```java
   // Before
   eventBus.subscribe(listener); // no corresponding unsubscribe anywhere
   // After
   eventBus.subscribe(listener);
   // ... and on shutdown/cleanup:
   eventBus.unsubscribe(listener);
   ```

6. **Making a class a non-static inner class when it doesn't need the outer instance.** Why it matters: every instance silently holds a hidden reference to its enclosing object, which can keep a large outer object (and its state) alive far longer than intended.
   ```java
   // Before
   class Listener { /* implicitly holds Outer.this, unused */ }
   // After
   static class Listener { /* no hidden outer reference */ }
   ```

7. **Assuming `-Xmx` alone controls container memory usage.** Why it matters: metaspace, thread stacks, direct buffers, and JIT code cache are all off-heap and can push total memory past the container limit even when the heap itself looks fine, causing an OOM-kill with no Java-level stack trace.
   ```bash
   # Before: heap-only thinking
   java -Xmx3800m -jar app.jar   # in a 4 GB container — no room for anything else
   # After: leave headroom, or size as a percentage of the container limit
   java -XX:MaxRAMPercentage=70.0 -jar app.jar
   ```

8. **Setting `-Xms` far below `-Xmx` in a server/container workload.** Why it matters: the JVM has to repeatedly resize the heap under load, causing extra pauses and unpredictable memory footprint — exactly the opposite of what you want in a container with a hard memory limit.
   ```bash
   # Before
   java -Xms256m -Xmx4g -jar app.jar
   # After
   java -Xms4g -Xmx4g -jar app.jar
   ```

9. **Leaving metaspace uncapped in a service that dynamically loads classes (or gets redeployed).** Why it matters: a classloader leak silently grows metaspace with no upper bound until the JVM (or container) runs out of memory in a confusing way.
   ```bash
   # Before: no cap at all
   java -jar app.jar
   # After: explicit cap turns a silent leak into a clear, diagnosable OOM
   java -XX:MaxMetaspaceSize=512m -jar app.jar
   ```

10. **Writing code that depends on escape analysis for correctness or guaranteed performance.** Why it matters: it's a best-effort, JIT-tier-dependent optimization with no language-level guarantee — a minor refactor can silently disable it with no warning.
    ```java
    // Before — relying on "the JIT will optimize this away" as a design assumption
    Point p = new Point(x, y); // assumed to never really allocate
    // After — write clear code; treat escape analysis as a bonus, not a plan
    int dx = x2 - x1, dy = y2 - y1; // if it matters, measure, don't assume
    ```

11. **Reaching for `jmap -dump` under time pressure without knowing `jcmd` exists.** Why it matters: `jcmd` is the actively maintained, more reliable tool for the same job, and knowing it signals up-to-date operational knowledge in a review.
    ```bash
    # Older habit
    jmap -dump:live,format=b,file=heap.hprof <pid>
    # Preferred
    jcmd <pid> GC.heap_dump /tmp/heap.hprof
    ```

12. **Choosing ZGC/Shenandoah "because it's the newest" without a pause-time requirement.** Why it matters: both trade extra memory and CPU overhead for lower pauses — paying that cost with no latency SLA to justify it is a wasted resource, and G1's balance is right for most services.
    ```
    # Before: reflexively latency-optimizing an offline batch job
    -XX:+UseZGC
    # After: throughput-first collector for a job nobody is waiting on interactively
    -XX:+UseParallelGC
    ```

13. **Treating a single heap-usage snapshot as proof of (or against) a leak.** Why it matters: normal caching and generational promotion both look like "heap usage went up" in one snapshot; only a trend across multiple full-GC cycles (usage not dropping back down) distinguishes a leak from healthy behavior.
    ```
    # Before: "heap usage is 80% full, must be a leak!"
    # After: compare after-full-GC usage across several cycles over time;
    # a leak shows a rising floor, not just a high peak.
    ```

14. **Manually locking two objects in an order that depends on caller-supplied argument order.** Why it matters: it creates a classic deadlock the moment two threads call the method with the arguments swapped — exactly the bug `jstack`'s deadlock detector exists to catch.
    ```java
    // Before
    synchronized (fromAccount) { synchronized (toAccount) { /* ... */ } }
    // After: always lock in a fixed, consistent order regardless of arguments
    Account first = fromAccount.id() < toAccount.id() ? fromAccount : toAccount;
    Account second = fromAccount.id() < toAccount.id() ? toAccount : fromAccount;
    synchronized (first) { synchronized (second) { /* ... */ } }
    ```

15. **Not enabling `-XX:+HeapDumpOnOutOfMemoryError` in production.** Why it matters: without it, an `OutOfMemoryError` crash leaves no artifact to analyze — the exact evidence needed to diagnose the leak disappears along with the crashed process.
    ```bash
    # Before
    java -jar app.jar
    # After
    java -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/dumps/ -jar app.jar
    ```
