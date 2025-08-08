# Day 28: Distributed Locks & Leader Election

| | |
|---|---|
| 🏗️ **Project** | **LockSmith** — a Redis distributed lock with singleton scheduling / leader election |
| ☕ **Java & language skills** | Redis SET NX PX + Lua compare-and-delete, @Scheduled, unique tokens, ShedLock |
| 🧰 **Library / tool** | Redis (+ ShedLock; ZooKeeper/etcd noted) |
| 🗄️ **DB / distributed-systems concept** | Distributed locks, leader election & a consensus (Raft) intro |
| 📊 **Difficulty** | Hard |

---

## Concept primer

### 1. What is mutual exclusion, and why does the distributed version hurt?

On Day 11 you used database row locks; in plain Java you'd reach for `synchronized` or a `ReentrantLock`. All of those rely on one thing: **shared memory inside a single process** (or a single DB). The JVM's lock is backed by an object monitor that *every* thread can see, and the OS/hardware guarantees that exactly one thread holds it. Acquire is cheap, release is guaranteed, and a "crashed" thread simply doesn't exist independently of the process.

A **distributed lock** throws all of those guarantees away:

- **No shared memory.** Two instances of your service on two machines share *nothing*. The only common ground is some external store (Redis, ZooKeeper, a DB row). The lock "state" lives over a network, and the network is slow, lossy, and can partition.
- **Partial failure.** In one JVM, either the whole process is up or it's down. In a distributed system, the holder of a lock can *vanish* (crash, get killed, lose its network) while still "holding" the lock as far as the store knows. If a lock had no expiry, that lock would be held *forever* — a deadlock you can't `kill -9` your way out of. So distributed locks need a **TTL** (auto-expiry). But a TTL is itself a source of bugs (see fencing tokens below).
- **Clock skew & pauses.** Different machines have different clocks. Worse, *any* process can be paused arbitrarily long — a GC stop-the-world, a hypervisor pausing the VM, a `SIGSTOP`, an overloaded CPU. The holder may *believe* it still holds the lock while its TTL has already expired on the server and someone else has acquired it. You cannot reason about distributed safety using wall-clock time on the client.

This is why distributed locking is in the "expert" bucket: the happy path is trivial, but every interesting failure mode breaks naive implementations.

### 2. Acquire with `SET NX PX`, release with compare-and-delete

The minimal correct Redis lock:

```
SET lock:resourceX <unique-token> NX PX 30000
```

- `NX` — set **only if it doesn't already exist** (this is the atomic "acquire").
- `PX 30000` — auto-expire after 30s. This is the safety net for crashed holders. The two flags applied in one command are atomic — no race between "check" and "set".
- `<unique-token>` — a UUID unique to *this* acquisition. **This is the part people forget**, and it's critical.

Why the token matters. Naive release is `DEL lock:resourceX`. Consider:

1. Instance A acquires the lock with a 30s TTL.
2. A is slow (GC pause). The lock **expires** at 30s.
3. Instance B acquires the lock (it's free now).
4. A wakes up, finishes, and calls `DEL lock:resourceX` — **deleting B's lock!**
5. Instance C now acquires it. A and B and C all think they hold it. Mutual exclusion is gone.

The fix: release must be a **compare-and-delete** — "delete the key *only if* its value is still my token." That check-then-delete must be atomic, so we run it as a **Lua script** (Redis executes a script atomically):

```lua
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
```

Now if A's lock already expired and B owns it, A's `GET` returns B's token != A's token, so A deletes nothing. Good.

### 3. The fencing-token problem (the deeper bug TTL can't fix)

Compare-and-delete fixes *release*, but it does **not** fix the dangerous window in steps 2–4 above where **A and B both believe they hold the lock simultaneously**. During A's pause, the TTL expired and B legitimately acquired. If A wakes and writes to the protected resource *before* checking anything, two writers hit it at once.

No amount of cleverness in Redis alone closes this, because the client's belief ("I still hold it") and reality ("the server expired it") have diverged and the client can't know. This is the core argument Martin Kleppmann made against using Redis locks for correctness-critical work.

The accepted fix is a **fencing token**: every successful acquisition returns a *monotonically increasing* number (1, 2, 3, ...). The client passes that token along with every write to the protected resource, and the resource (a DB, a storage service) **rejects any write whose token is lower than the highest it has already seen.**

```
A acquires -> token 33. A pauses.
B acquires -> token 34. B writes with token 34. Resource records "max seen = 34".
A wakes, writes with token 33. Resource sees 33 < 34 -> REJECTED.
```

Mutual exclusion is now enforced **at the resource**, not by trusting the lock holder's clock. A plain Redis lock doesn't give you a monotonic token for free (you'd need `INCR` on a counter, and it's still not linearizable across failover). Systems built on **consensus** (ZooKeeper's `zxid`, etcd's revision) give you exactly such a monotonic number — which is one reason they're preferred when correctness matters.

> Senior takeaway: use a Redis lock for **efficiency** ("usually only one worker does this, and occasional double-work is merely wasteful"). Use a consensus-backed lock + **fencing tokens** for **correctness** ("double execution corrupts data or money").

### 4. Leader election

A distributed lock generalizes to **leader election**: "elect exactly one node to do a singleton job" is just "whoever holds the lock is the leader." Examples: only one node should run the Day 20 **outbox relay**, only one should be the scheduler, only one should compact a log. The leader holds a lease; if it dies, the lease expires and a follower takes over. The same TTL/fencing concerns apply: a "former leader" recovering from a pause must not keep acting as leader.

### 5. Consensus: how etcd/ZooKeeper make locks *reliable* (Raft in one breath)

Why is etcd's lock trustworthy when a single Redis node's lock isn't? Because etcd/ZooKeeper run a **consensus protocol** across an odd number of nodes (3 or 5) and only act on decisions agreed by a **majority (quorum)**. **Raft** (the modern, teachable cousin of **Paxos**) works like this:

- **Leader election.** Nodes elect one **leader** for a term. Followers vote; a candidate needs a majority. Only the leader accepts writes.
- **Replicated log.** Every state change (including "lock acquired by client X with token N") is an **append to a log**, replicated to followers. An entry is **committed** only once a **majority** has persisted it. This should feel familiar: it's a **write-ahead log (Day 1)** — append-only, durable, replayed to rebuild state — now *replicated* across machines. And the "ordered, append-only, replicated log that consumers follow" is exactly the mental model of a **Kafka partition (Day 18)**.
- **Quorum / majority.** Because any two majorities of an odd cluster overlap in at least one node, the system stays consistent across failures and a single source of truth (the committed log) is preserved. This is what gives you **linearizable**, fenceable locks with monotonic revision numbers — the thing a lone Redis instance cannot promise during failover.

So the spectrum is: `ReentrantLock` (one JVM) -> single Redis lock (fast, "mostly correct") -> Redlock (multiple Redis, controversial) -> etcd/ZooKeeper leases (consensus-backed, correct, slower). Pick based on what a failure *costs* you.

---

## Prerequisites

- JDK 17+, Maven, Docker (for Redis).
- Reuse your Day 16 Redis or start a fresh one:

```bash
docker run -d --name redis-day28 -p 6379:6379 redis:7
```

---

## Maven dependencies

`pom.xml`:

```xml
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-redis</artifactId>
    </dependency>

    <!-- ShedLock: singleton @Scheduled across instances -->
    <dependency>
        <groupId>net.javacrumbs.shedlock</groupId>
        <artifactId>shedlock-spring</artifactId>
        <version>5.16.0</version>
    </dependency>
    <dependency>
        <groupId>net.javacrumbs.shedlock</groupId>
        <artifactId>shedlock-provider-redis-spring</artifactId>
        <version>5.16.0</version>
    </dependency>

    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-test</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>
```

`src/main/resources/application.yml`:

```yaml
spring:
  data:
    redis:
      host: localhost
      port: 6379

server:
  port: ${SERVER_PORT:8080}   # override per instance

app:
  instance-id: ${INSTANCE_ID:instance-A}
```

---

## 🛠️ Project Walkthrough — LockSmith

Roll up your sleeves — from here you'll build, run, and watch two instances contend for the lock.

---

## Step 1 — The lock primitive: `RedisDistributedLock`

We acquire with `SET ... NX PX` and release with the Lua compare-and-delete. The acquisition returns a small handle carrying the **unique token** so only the true owner can release.

```java
package com.example.day28.lock;

import org.springframework.data.redis.connection.RedisStringCommands.SetOption;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.types.Expiration;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * A minimal but correct single-node Redis distributed lock.
 *
 * Acquire: SET key <token> NX PX <ttl>     (atomic check-and-set)
 * Release: Lua compare-and-delete           (atomic check-and-del)
 *
 * NOTE: this is an "efficiency" lock. For correctness-critical work you also
 * need a fencing token enforced at the resource (see FencingDemo + notes).
 */
@Component
public class RedisDistributedLock {

    // KEYS[1] = lock key, ARGV[1] = our token. Delete only if still ours.
    private static final String RELEASE_LUA =
            "if redis.call('get', KEYS[1]) == ARGV[1] then " +
            "  return redis.call('del', KEYS[1]) " +
            "else " +
            "  return 0 " +
            "end";

    // KEYS[1] = lock key, ARGV[1] = our token, ARGV[2] = new ttl millis.
    // Extend (heartbeat) the TTL only if we still own the lock.
    private static final String EXTEND_LUA =
            "if redis.call('get', KEYS[1]) == ARGV[1] then " +
            "  return redis.call('pexpire', KEYS[1], ARGV[2]) " +
            "else " +
            "  return 0 " +
            "end";

    private final StringRedisTemplate redis;
    private final DefaultRedisScript<Long> releaseScript;
    private final DefaultRedisScript<Long> extendScript;

    public RedisDistributedLock(StringRedisTemplate redis) {
        this.redis = redis;
        this.releaseScript = new DefaultRedisScript<>(RELEASE_LUA, Long.class);
        this.extendScript = new DefaultRedisScript<>(EXTEND_LUA, Long.class);
    }

    /** Try once to acquire. Returns a handle if we got it, empty otherwise. */
    public Optional<LockHandle> tryAcquire(String key, Duration ttl) {
        String token = UUID.randomUUID().toString();
        Boolean ok = redis.execute((connection) -> connection.stringCommands().set(
                key.getBytes(StandardCharsets.UTF_8),
                token.getBytes(StandardCharsets.UTF_8),
                Expiration.from(ttl),     // PX
                SetOption.ifAbsent()),    // NX
                true, false);
        return Boolean.TRUE.equals(ok)
                ? Optional.of(new LockHandle(key, token, ttl))
                : Optional.empty();
    }

    /** Block (poll) up to waitFor, trying every retry interval. */
    public Optional<LockHandle> acquire(String key, Duration ttl,
                                        Duration waitFor, Duration retry) {
        long deadline = System.nanoTime() + waitFor.toNanos();
        do {
            Optional<LockHandle> h = tryAcquire(key, ttl);
            if (h.isPresent()) return h;
            try {
                Thread.sleep(retry.toMillis());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return Optional.empty();
            }
        } while (System.nanoTime() < deadline);
        return Optional.empty();
    }

    /** Safe release: only deletes the key if our token still owns it. */
    public boolean release(LockHandle handle) {
        Long result = redis.execute(
                releaseScript,
                List.of(handle.key()),
                handle.token());
        return result != null && result == 1L;
    }

    /** Heartbeat: extend the TTL while we still hold the lock. */
    public boolean extend(LockHandle handle, Duration newTtl) {
        Long result = redis.execute(
                extendScript,
                List.of(handle.key()),
                handle.token(),
                String.valueOf(newTtl.toMillis()));
        return result != null && result == 1L;
    }

    /** Handle returned by a successful acquisition; carries the unique token. */
    public record LockHandle(String key, String token, Duration ttl) {}
}
```

Key points:
- `SetOption.ifAbsent()` is `NX`; `Expiration.from(ttl)` is `PX` — applied atomically in one `SET`.
- The **token** lives only in this client; release proves ownership via the Lua script.
- `extend` is a heartbeat: long-running critical sections should renew the TTL so a slow-but-alive holder doesn't lose the lock. (It still can't beat a *frozen* process — that's the fencing problem.)

---

## Step 2 — Guard a critical section with the lock

A service method that must not run concurrently across instances.

```java
package com.example.day28.service;

import com.example.day28.lock.RedisDistributedLock;
import com.example.day28.lock.RedisDistributedLock.LockHandle;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.Optional;
import java.util.function.Supplier;

@Service
public class CriticalSectionService {

    private static final Logger log = LoggerFactory.getLogger(CriticalSectionService.class);

    private final RedisDistributedLock lock;
    private final String instanceId;

    public CriticalSectionService(RedisDistributedLock lock,
                                   @Value("${app.instance-id}") String instanceId) {
        this.lock = lock;
        this.instanceId = instanceId;
    }

    /** Run `work` only if we win the lock; otherwise skip. */
    public <T> Optional<T> runExclusively(String resource, Supplier<T> work) {
        String key = "lock:" + resource;
        Optional<LockHandle> handle = lock.tryAcquire(key, Duration.ofSeconds(30));
        if (handle.isEmpty()) {
            log.info("[{}] could not acquire {} -> skipping", instanceId, key);
            return Optional.empty();
        }
        log.info("[{}] ACQUIRED {} (token={})", instanceId, key, handle.get().token());
        try {
            return Optional.of(work.get());
        } finally {
            boolean released = lock.release(handle.get());
            log.info("[{}] released {} (clean={})", instanceId, key, released);
        }
    }
}
```

---

## Step 3 — Make the Day 20 outbox relay a singleton (hand-rolled)

Recall Day 20: every instance runs an `@Scheduled` outbox relay polling for unsent events. If *every* instance runs it, you risk double-publishing (or at least wasteful contention on the same rows). Wrap it in the lock so only one instance does the work per tick:

```java
package com.example.day28.scheduled;

import com.example.day28.service.CriticalSectionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private final CriticalSectionService critical;
    private final String instanceId;

    public OutboxRelay(CriticalSectionService critical,
                       @Value("${app.instance-id}") String instanceId) {
        this.critical = critical;
        this.instanceId = instanceId;
    }

    @Scheduled(fixedDelay = 5000)
    public void relay() {
        critical.runExclusively("outbox-relay", () -> {
            // The real Day 20 work: SELECT unsent rows, publish to Kafka,
            // mark as sent. Here we just simulate it.
            log.info("[{}] >>> publishing outbox events <<<", instanceId);
            try { Thread.sleep(1000); } catch (InterruptedException ignored) {}
            return null;
        });
    }
}
```

Don't forget to enable scheduling:

```java
package com.example.day28;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class Day28Application {
    public static void main(String[] args) {
        SpringApplication.run(Day28Application.class, args);
    }
}
```

Run two instances and you'll see exactly **one** of them print `>>> publishing outbox events <<<` per tick.

---

## Step 4 — Demonstrate the fencing-token problem (conceptually, in code)

This endpoint shows why a TTL-based lock alone is unsafe under a pause, and how a fencing token at the *resource* saves you. We simulate a pause and a "resource" that rejects stale tokens.

```java
package com.example.day28.fencing;

import com.example.day28.lock.RedisDistributedLock;
import com.example.day28.lock.RedisDistributedLock.LockHandle;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.bind.annotation.*;

import java.time.Duration;
import java.util.Optional;

/**
 * A "protected resource" that only accepts writes with a monotonically
 * increasing fencing token. This is the part a plain Redis lock CANNOT
 * give you for free; it's what consensus stores (etcd revision, ZK zxid)
 * provide and what makes locks safe under process pauses.
 */
class FencedResource {
    private long highestTokenSeen = 0;

    synchronized boolean write(long fencingToken, String payload) {
        if (fencingToken < highestTokenSeen) {
            return false; // stale writer -> rejected, mutual exclusion preserved
        }
        highestTokenSeen = fencingToken;
        // ... actually apply the write ...
        return true;
    }
}

@RestController
@RequestMapping("/fencing")
public class FencingDemoController {

    private final RedisDistributedLock lock;
    private final StringRedisTemplate redis;
    private final FencedResource resource = new FencedResource();

    public FencingDemoController(RedisDistributedLock lock, StringRedisTemplate redis) {
        this.lock = lock;
        this.redis = redis;
    }

    /** Acquire lock + a monotonic fencing token (Redis INCR as a stand-in). */
    @PostMapping("/acquire")
    public String acquireWithToken() {
        Optional<LockHandle> h = lock.tryAcquire("lock:fenced", Duration.ofSeconds(30));
        if (h.isEmpty()) return "LOCK BUSY";
        long fence = redis.opsForValue().increment("fence:fenced");
        return "token=" + h.get().token() + " fence=" + fence;
    }

    /** Attempt a write with a given fencing token. */
    @PostMapping("/write")
    public String write(@RequestParam long fence, @RequestParam String payload) {
        boolean ok = resource.write(fence, payload);
        return ok ? "ACCEPTED (fence=" + fence + ")"
                  : "REJECTED stale writer (fence=" + fence + ")";
    }
}
```

To act out the bug: client A acquires (`fence=1`), then "pauses". The lock TTL expires, client B acquires (`fence=2`) and writes with `fence=2` -> ACCEPTED. Now A wakes and writes with `fence=1` -> **REJECTED**. Without the fence, A's stale write would have clobbered B's. Note `INCR` is a decent *stand-in*, but it isn't guaranteed monotonic across Redis failover — which is precisely why correctness-grade systems use a consensus log's revision number.

---

## Step 5 — The production-grade option: ShedLock for `@Scheduled` singletons

Hand-rolling is great for learning, but in production use **ShedLock**. It does one focused thing: ensures a scheduled task runs on **only one node at a time**, using a shared store (Redis, JDBC, Mongo, etc.) as the lock. It's the idiomatic answer to "make the Day 20 outbox relay a singleton."

Config:

```java
package com.example.day28.config;

import net.javacrumbs.shedlock.core.LockProvider;
import net.javacrumbs.shedlock.provider.redis.spring.RedisLockProvider;
import net.javacrumbs.shedlock.spring.annotation.EnableSchedulerLock;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;

@Configuration
@EnableSchedulerLock(defaultLockAtMostFor = "PT30S")
public class ShedLockConfig {

    @Bean
    public LockProvider lockProvider(RedisConnectionFactory connectionFactory) {
        return new RedisLockProvider(connectionFactory, "day28");
    }
}
```

The scheduled task — note it's just two annotations on a normal `@Scheduled` method:

```java
package com.example.day28.scheduled;

import net.javacrumbs.shedlock.spring.annotation.SchedulerLock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class ShedLockOutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(ShedLockOutboxRelay.class);
    private final String instanceId;

    public ShedLockOutboxRelay(@Value("${app.instance-id}") String instanceId) {
        this.instanceId = instanceId;
    }

    @Scheduled(fixedDelay = 5000)
    @SchedulerLock(name = "outbox-relay-shedlock",
                   lockAtMostFor = "PT30S",   // safety: max held even if node dies mid-run
                   lockAtLeastFor = "PT2S")   // min held: avoids two nodes racing fast clocks
    public void relay() {
        log.info("[{}] (ShedLock) publishing outbox events", instanceId);
    }
}
```

- `lockAtMostFor` is the TTL — the upper bound the lock is held even if the node dies mid-execution (prevents a permanent deadlock). Set it comfortably longer than the worst-case run time.
- `lockAtLeastFor` keeps the lock held for a minimum even if the task finishes instantly — this guards against two nodes with skewed clocks both grabbing it in quick succession. (Same clock-skew worry from the primer, handled pragmatically.)

> ShedLock is an **efficiency** lock too — its docs are explicit that it does *not* give correctness guarantees against process pauses; it's the right tool to stop duplicate scheduled runs, not to gate money movement without a fence.

---

## How to run

Build, then start **two** instances on different ports with different IDs so you can watch them contend:

```bash
mvn -q clean package -DskipTests

# Terminal 1
SERVER_PORT=8080 INSTANCE_ID=instance-A java -jar target/day28-0.0.1-SNAPSHOT.jar

# Terminal 2
SERVER_PORT=8081 INSTANCE_ID=instance-B java -jar target/day28-0.0.1-SNAPSHOT.jar
```

### Watch the scheduled singleton

Both instances tick every 5s, but only one wins the lock per tick:

```
[instance-A] ACQUIRED lock:outbox-relay (token=2f1c...)
[instance-A] >>> publishing outbox events <<<
[instance-B] could not acquire lock:outbox-relay -> skipping
[instance-A] released lock:outbox-relay (clean=true)
```

(If A is busy/down, B starts winning — that's leader-by-lock failover.)

### Inspect the lock in Redis

```bash
docker exec -it redis-day28 redis-cli
> GET lock:outbox-relay     # the token of the current holder (or nil)
> PTTL lock:outbox-relay    # remaining ms before auto-expiry
```

### Walk through the fencing demo

```bash
# A acquires
curl -XPOST localhost:8080/fencing/acquire        # -> token=... fence=1
# Simulate A's pause + TTL expiry: just wait 30s (or DEL the key), then B acquires
curl -XPOST localhost:8081/fencing/acquire        # -> token=... fence=2
# B writes with the newer fence -> accepted
curl -XPOST "localhost:8081/fencing/write?fence=2&payload=B"   # ACCEPTED
# A finally wakes and writes with its stale fence -> rejected
curl -XPOST "localhost:8080/fencing/write?fence=1&payload=A"   # REJECTED stale writer
```

## How to test two instances contending (single JVM)

You can also prove mutual exclusion with a test that fires many threads at one lock and asserts only one is inside the critical section at a time:

```java
package com.example.day28;

import com.example.day28.lock.RedisDistributedLock;
import com.example.day28.lock.RedisDistributedLock.LockHandle;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.time.Duration;
import java.util.Optional;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;

@SpringBootTest   // requires a running Redis on localhost:6379
class DistributedLockTest {

    @Autowired RedisDistributedLock lock;

    @Test
    void onlyOneThreadHoldsLockAtATime() throws Exception {
        String key = "lock:test:" + System.nanoTime();
        int threads = 20;
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        AtomicInteger concurrentInside = new AtomicInteger();
        AtomicInteger maxObserved = new AtomicInteger();
        AtomicInteger acquisitions = new AtomicInteger();
        CountDownLatch done = new CountDownLatch(threads);

        for (int i = 0; i < threads; i++) {
            pool.submit(() -> {
                try {
                    Optional<LockHandle> h =
                            lock.acquire(key, Duration.ofSeconds(5),
                                         Duration.ofSeconds(10), Duration.ofMillis(50));
                    if (h.isPresent()) {
                        acquisitions.incrementAndGet();
                        int now = concurrentInside.incrementAndGet();
                        maxObserved.accumulateAndGet(now, Math::max);
                        Thread.sleep(50);              // be "inside" the section
                        concurrentInside.decrementAndGet();
                        lock.release(h.get());
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                } finally {
                    done.countDown();
                }
            });
        }
        done.await(30, TimeUnit.SECONDS);
        pool.shutdownNow();

        assertEquals(1, maxObserved.get(), "more than one thread was inside the lock!");
        assertEquals(threads, acquisitions.get(), "every thread should eventually get in");
    }
}
```

`maxObserved == 1` is the proof of mutual exclusion. Each thread here stands in for a separate instance.

### Expected output (summary)

- Scheduled task: exactly one `>>> publishing outbox events <<<` per 5s tick across both processes; the other logs `skipping`.
- ShedLock task: exactly one `(ShedLock) publishing outbox events` per tick.
- Fencing demo: stale-token write is `REJECTED`.
- Test: passes — `maxObserved == 1`, all 20 threads eventually acquired.

---

## 🚀 Going Deeper & Next Steps

### Senior-level notes

- **The Redlock controversy.** Redlock is Redis's *multi-master* lock algorithm: acquire on a majority of N independent Redis nodes within a time bound. **Martin Kleppmann** argued it's unsafe for correctness because it relies on bounded clocks and bounded pauses (both false in practice) and offers no fencing token; **Salvatore Sanfilippo (antirez)** rebutted that with `monotonicTime` and proper config it's reasonable for many uses. The pragmatic consensus today: a single-node Redis lock (what we built) or Redlock is fine as an **efficiency** lock; for **correctness**, use a consensus system *and* fencing tokens. Know both sides of this debate in interviews.
- **ZooKeeper & etcd leases / ephemeral nodes.** ZooKeeper locks use **ephemeral znodes**: a node tied to a client session that the server *deletes automatically when the session dies* — no TTL guessing, the death is detected by the cluster. Lock fairness comes from **ephemeral-sequential** znodes (each acquirer gets a monotonically increasing sequence number — a natural fencing token, the `zxid`). etcd offers **leases**: a key bound to a lease that auto-expires unless the client keeps it alive (`KeepAlive`), plus a monotonic `revision`. Both are backed by consensus, so the lock survives leader failover with correctness intact. This is why "use ZooKeeper/etcd" is the standard answer when a wrong lock costs real money.
- **Raft vs Paxos.** Same goal (agree on a replicated log via majority quorum). **Paxos** is the original, notoriously hard to understand and implement piecemeal (Multi-Paxos for a log). **Raft** was explicitly designed for *understandability*: a strong leader, append-only log replication, and a clean leader-election protocol with terms. Raft powers etcd; ZooKeeper uses Zab (Paxos-like). For day-to-day engineering, reason in Raft terms: leader, log, commit-on-majority.
- **Leases vs locks.** A **lock** is "I hold it until I release." A **lease** is "I hold it until time T unless I renew." Every distributed lock with a TTL is really a lease — which is *why* fencing tokens matter: a lease can expire under your feet. Designing for lease semantics (renew via heartbeat, tolerate expiry, fence the resource) is the senior mindset.
- **Lock granularity.** Coarse locks (one global lock) are simple but serialize everything and create a single contention point. Fine-grained, per-resource locks (`lock:account:42`) scale far better but raise the odds of deadlock if a path acquires several — order your acquisitions consistently, keep critical sections tiny, and prefer optimistic concurrency (Day 5 MVCC, version columns) over locks when you can.
- **Tie-back.** The replicated log at the heart of Raft is the **WAL of Day 1** made distributed; consumers reading an ordered, append-only, committed log is the **Kafka partition of Day 18**. Distributed locking, leader election, and consensus are all the same problem wearing different hats: *agree on one source of truth despite partial failure.*

### Stretch goals

1. **Real fencing end-to-end.** Add a `version`/`fence` column to the Day 20 outbox table and have the relay pass its fencing token on the `UPDATE ... WHERE fence <= :token`, rejecting stale writers at the DB. Prove a paused relay's late write is rejected.
2. **Auto-renewing lock (watchdog).** Add a background thread that calls `extend()` every `ttl/3` while the critical section runs, like Redisson's watchdog. Then *kill* the renewer mid-section and show the lock expiring — illustrating that a renewer can't save a frozen process (still need fencing).
3. **Use Redisson.** Swap your hand-rolled lock for `RedissonClient.getLock("...")` (`tryLock`/`unlock`) and compare ergonomics, then enable its Redlock variant across multiple Redis nodes.
4. **etcd leader election.** Run a 3-node etcd cluster (`docker compose`) and use jetcd's `Election` / `Lease` APIs to elect a leader for the relay. Compare failover behavior and the monotonic `revision` against your Redis token.

### Day 29 teaser

You've spent 28 days mostly in the blocking, thread-per-request world. **Day 29: Reactive** flips that — backpressure, non-blocking I/O, and Project Reactor (`Mono`/`Flux`) with Spring WebFlux. We'll see why a reactive outbox relay or lock client can serve thousands of concurrent waiters on a handful of threads, and where reactive helps (and where it just adds complexity) on the road to the Day 30 capstone.
