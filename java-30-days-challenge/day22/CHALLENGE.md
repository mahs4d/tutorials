# Day 22: Sharding & Consistent Hashing

| | |
|---|---|
| 🏗️ **Project** | **HashRing** — a consistent-hashing shard router with virtual nodes |
| ☕ **Java & language skills** | TreeMap/SortedMap, hashing (MessageDigest/Guava), generics, JUnit5 verification |
| 🧰 **Library / tool** | Guava (hashing utilities) |
| 🗄️ **DB / distributed-systems concept** | Sharding & consistent hashing (hash ring, virtual nodes) |
| 📊 **Difficulty** | Hard |

---

## Concept primer

### What "sharding" actually means

Up to now a single machine has held all your data. Eventually one box can't hold the data, serve the QPS, or keep the working set in RAM. **Vertical scaling** (a bigger box) hits a price/physics wall. **Horizontal scaling** means spreading the data across `N` machines — each machine owns a **shard** (a disjoint subset of the keyspace). The central question of sharding is one function:

> Given a key, **which shard owns it?**

Get this function right and you scale linearly. Get it wrong and you either create hotspots (one shard does all the work) or you make adding capacity catastrophically expensive (every scale event moves all the data).

### The three sharding strategies

**1. Range sharding** — partition by key ranges. `a–f → shard0, g–m → shard1, n–z → shard2`.
- ✅ Range scans are cheap (`WHERE user BETWEEN 'a' AND 'c'` hits one shard) — this is what B-Trees (Day 6) and ordered indexes (Day 21) love.
- ❌ **Hotspots from skew**: monotonic keys (timestamps, auto-increment IDs) all land on the *last* shard — the dreaded "hot last partition". HBase, Bigtable, and time-series DBs all fight this.

**2. Hash sharding** — `shard = hash(key) % N`. Spreads keys uniformly regardless of key distribution.
- ✅ Excellent uniform load, no skew hotspots.
- ❌ **Range scans are destroyed** (adjacent keys scatter). And — the topic of today — `% N` makes membership changes brutal.

**3. Directory / lookup sharding** — keep an explicit map `key → shard` in a coordination service (a "shard map" / "tablet directory", e.g. Vitess, BigTable's metadata tablets).
- ✅ Maximum flexibility: move any key anywhere, split hot shards arbitrarily.
- ❌ The directory is an extra hop and a **single point of truth** you must make HA and fast (cache it!). It's the most powerful and the most operationally heavy.

Consistent hashing is a refinement of strategy **2** that fixes its fatal membership-change flaw while keeping its uniform load.

### The modulo-rehash catastrophe

Hash sharding routes with `shard = hash(key) % N`. Beautifully uniform... until `N` changes.

Suppose `N = 4` and you add one node, `N = 5`. A key whose hash is `h` was on shard `h % 4` and is now on shard `h % 5`. For how many keys does `h % 4 == h % 5`? Almost none.

```
key hash h:    10   11   12   13   14   15   16   17   18   19
h % 4     :     2    3    0    1    2    3    0    1    2    3
h % 5     :     0    1    2    3    4    0    1    2    3    4
moved?    :     ✗    ✗    ✓    ✓    ✓    ✓    ✓    ✓    ✗    ✗
```

In general, going from `N` to `N+1` keeps only ~`1/(N+1)` of keys in place and **moves the rest**. For a cache (Day 15/16) this is a **miss storm**: nearly the entire cache is suddenly invalid, every request falls through to the database simultaneously, and you get a thundering-herd outage *caused by adding capacity*. For a datastore it means physically copying almost all your data on every scale event. This single property is why naive modulo hashing is unusable for elastic distributed systems — and why consistent hashing was invented (Karger et al., 1997, originally for distributed web caches).

### Consistent hashing: the ring

Map the hash output space onto a **circle** (the "ring") — e.g. `0 .. 2^64-1` wrapping around. Then:

1. **Place each node** on the ring by hashing its identity: `position = hash(nodeId)`.
2. **Place each key** on the ring the same way: `position = hash(key)`.
3. **A key is owned by the first node found walking clockwise** from the key's position (the node whose position is the smallest value `>=` the key's position, wrapping past the top).

```
                hash(keyB)
        nodeC      |
          \        v
   ........●........k........●........●........
           ^                 ^        ^
        position of      nodeA     nodeB
        nodeC          (owns keyB: first node clockwise)
   ring wraps: after the largest node, you wrap to the smallest
```

Now the magic. **Add nodeD** somewhere on the ring. Only the keys in the arc *between nodeD and the previous node clockwise-before it* change owner (they move from whoever used to own that arc, to nodeD). **Every other key is untouched.** On average a new node steals the arc covering ~`1/N` of the keyspace. **Remove** a node and only *its* arc's keys move — to the next node clockwise. So a membership change touches **~1/N of keys**, not ~all of them. That is the entire point.

Implementation insight: "first node clockwise" is exactly `TreeMap.ceilingEntry(keyHash)`, falling back to `firstEntry()` on wrap. A `SortedMap` *is* a hash ring.

### Virtual nodes: fixing balance

A ring with one point per physical node is **badly balanced**. With 3 random points on a circle, the arcs are nowhere near equal — one node might own 50% of the ring by luck. Worse, when a node dies, **all** its load dumps onto its single clockwise neighbor (not spread out).

Fix: give each physical node **V virtual points** (vnodes) on the ring, by hashing `nodeId#0, nodeId#1, ... nodeId#(V-1)`. With `V = 100–200` per node:
- The law of large numbers smooths the arc sizes → each physical node owns ~`1/N` of the ring within a few percent.
- When a node dies, its `V` arcs are scattered around the ring, so its load is redistributed across **many** remaining nodes, not dumped on one.
- You can give a beefier machine **more** vnodes → **weighted** placement, proportional to capacity.

This is exactly Cassandra's `num_tokens` setting and DynamoDB's "tokens".

### Replication on the ring (the why)

A key needs `R` replicas for durability/availability. The ring gives you a free, deterministic replica set: walk clockwise from the key and take the **next `R` *distinct physical* nodes**. (Distinct is critical — naively taking the next R *vnodes* could pick the same physical node twice and give you fake redundancy.) These are Dynamo's **preference list**. Because every node computes the same ring, every node agrees on who holds each key with **zero coordination** — no directory lookup, no metadata server. That coordination-free agreement is the property that makes Dynamo-style systems horizontally scalable.

---

## Prerequisites

- JDK 17+ and Maven (Day 1).
- A scratch Maven module. Add **Guava** for its hashing utilities:

```xml
<dependencies>
  <dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.2.1-jre</version>
  </dependency>

  <!-- JUnit 5 (Day 2) -->
  <dependency>
    <groupId>org.junit.jupiter</groupId>
    <artifactId>junit-jupiter</artifactId>
    <version>5.10.2</version>
    <scope>test</scope>
  </dependency>
</dependencies>
```

Why Guava's `murmur3` over `String.hashCode()`? `String.hashCode()` is stable within a JVM but is a *weak* hash with poor avalanche — close strings produce close values, which clumps the ring. MurmurHash3 has strong avalanche (one input bit flips ~half the output bits) and is the de-facto choice in Cassandra, Kafka (murmur2), and Guava-based caches. It's also **stable across JVMs/restarts**, which a distributed ring requires.

---

---

## 🛠️ Project Walkthrough — HashRing

Roll up your sleeves and build the ring step by step, then run the driver and tests to see the numbers for yourself.

## Step 1 — Define the ring contract

```java
package day22;

import java.util.List;

/** Routes keys to nodes via consistent hashing with virtual nodes. */
public interface HashRing<N> {
    void addNode(N node);
    void removeNode(N node);

    /** The single owner of this key (first physical node clockwise). */
    N getNode(String key);

    /** The preference list: next {@code count} DISTINCT physical nodes clockwise (for replication). */
    List<N> getNodes(String key, int count);

    int physicalNodeCount();
}
```

We make a `Node` a tiny value type so `equals`/`hashCode` and a stable id are explicit.

```java
package day22;

import java.util.Objects;

/** A physical node. {@code weight} scales how many vnodes it gets (capacity-aware). */
public record Node(String id, int weight) {
    public Node(String id) { this(id, 1); }
    public Node {
        Objects.requireNonNull(id, "id");
        if (weight < 1) throw new IllegalArgumentException("weight >= 1");
    }
    @Override public String toString() { return id; }
}
```

## Step 2 — Implement `ConsistentHashRing` with a `TreeMap`

The ring is a `TreeMap<Long, Node>` mapping **vnode hash → owning physical node**. `ceilingEntry` finds "first clockwise"; `firstEntry` handles the wrap.

```java
package day22;

import com.google.common.hash.HashFunction;
import com.google.common.hash.Hashing;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.NavigableMap;
import java.util.SortedMap;
import java.util.TreeMap;

public final class ConsistentHashRing<N> implements HashRing<N> {

    /** Base virtual nodes per unit of weight. 100–200 gives good balance. */
    private final int vnodesPerWeight;
    private final HashFunction hash = Hashing.murmur3_128();

    /** ring position (64-bit) -> physical node. */
    private final NavigableMap<Long, N> ring = new TreeMap<>();
    /** distinct physical nodes (for counts and distinct-replica logic). */
    private final List<N> physicalNodes = new ArrayList<>();

    public ConsistentHashRing(int vnodesPerWeight) {
        if (vnodesPerWeight < 1) throw new IllegalArgumentException("vnodesPerWeight >= 1");
        this.vnodesPerWeight = vnodesPerWeight;
    }

    public ConsistentHashRing() { this(150); }

    private long hash(String s) {
        return hash.hashString(s, StandardCharsets.UTF_8).asLong();
    }

    private int vnodeCount(N node) {
        int weight = (node instanceof Node n) ? n.weight() : 1;
        return vnodesPerWeight * weight;
    }

    @Override
    public synchronized void addNode(N node) {
        if (physicalNodes.contains(node)) return;
        physicalNodes.add(node);
        int v = vnodeCount(node);
        for (int i = 0; i < v; i++) {
            // hash a per-vnode label so the V points scatter around the ring
            long pos = hash(node + "#vn" + i);
            // collisions are astronomically unlikely with 64 bits; probe forward if any
            while (ring.containsKey(pos)) pos++;
            ring.put(pos, node);
        }
    }

    @Override
    public synchronized void removeNode(N node) {
        if (!physicalNodes.remove(node)) return;
        ring.values().removeIf(n -> n.equals(node)); // drop all its vnodes
    }

    @Override
    public synchronized N getNode(String key) {
        if (ring.isEmpty()) return null;
        long h = hash(key);
        // first vnode clockwise (>= h); wrap to the smallest if none.
        Map.Entry<Long, N> e = ring.ceilingEntry(h);
        return (e != null) ? e.getValue() : ring.firstEntry().getValue();
    }

    @Override
    public synchronized List<N> getNodes(String key, int count) {
        if (ring.isEmpty() || count <= 0) return List.of();
        int want = Math.min(count, physicalNodes.size());
        List<N> result = new ArrayList<>(want);
        long h = hash(key);

        // tailMap from h, then continue from the head to emulate the wrap.
        SortedMap<Long, N> tail = ring.tailMap(h);
        // chain tail values then all values, dedup to DISTINCT physical nodes.
        Iterable<N> walk = concat(tail.values(), ring.values());
        for (N n : walk) {
            if (!result.contains(n)) {       // distinct physical nodes only
                result.add(n);
                if (result.size() == want) break;
            }
        }
        return result;
    }

    @Override
    public synchronized int physicalNodeCount() { return physicalNodes.size(); }

    private static <T> Iterable<T> concat(Iterable<T> a, Iterable<T> b) {
        List<T> all = new ArrayList<>();
        a.forEach(all::add);
        b.forEach(all::add);
        return all;
    }
}
```

Notes on the implementation:
- **`getNodes` walks `tailMap(h)` then wraps** to the head, collecting *distinct physical* nodes — that's the Dynamo preference list with real (not fake) redundancy.
- `synchronized` keeps it simple and correct; a production ring would use a `volatile` immutable snapshot swapped on membership change so reads are lock-free (a stretch goal below).
- The vnode label `node + "#vn" + i` is what scatters one physical node's points around the whole ring.

## Step 3 — A baseline: naive modulo router

To *prove* the difference we need the thing we're beating. This is `hash(key) % N` with an ordered node list.

```java
package day22;

import com.google.common.hash.Hashing;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/** The naive baseline: shard = hash(key) % N over an ordered node list. */
public final class ModuloRouter<N> {
    private final List<N> nodes = new ArrayList<>();

    public void addNode(N node)    { nodes.add(node); }
    public void removeNode(N node) { nodes.remove(node); }

    public N getNode(String key) {
        if (nodes.isEmpty()) return null;
        long h = Hashing.murmur3_128().hashString(key, StandardCharsets.UTF_8).asLong();
        int idx = Math.floorMod(h, nodes.size()); // floorMod: non-negative even if h < 0
        return nodes.get(idx);
    }
}
```

## Step 4 — The driver: measure key movement and balance

This is the payoff. We assign a large set of keys, change membership, and **count how many keys changed owner** — for both routers.

```java
package day22;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

public final class ShardingDriver {

    static final int KEYS = 100_000;

    public static void main(String[] args) {
        var keys = new java.util.ArrayList<String>(KEYS);
        for (int i = 0; i < KEYS; i++) keys.add("user:" + UUID.randomUUID());

        System.out.println("=== Adding a 5th node to a 4-node cluster ===\n");

        // ---- Naive modulo ----
        var mod = new ModuloRouter<Node>();
        for (int i = 0; i < 4; i++) mod.addNode(new Node("n" + i));
        Map<String, Node> before = assignMod(mod, keys);
        mod.addNode(new Node("n4"));
        Map<String, Node> after = assignMod(mod, keys);
        double movedMod = movedFraction(before, after);
        System.out.printf("modulo  hash(k)%%N : %.1f%% of keys moved%n", movedMod * 100);

        // ---- Consistent hashing ----
        var ring = new ConsistentHashRing<Node>(150);
        for (int i = 0; i < 4; i++) ring.addNode(new Node("n" + i));
        Map<String, Node> rBefore = assignRing(ring, keys);
        ring.addNode(new Node("n4"));
        Map<String, Node> rAfter = assignRing(ring, keys);
        double movedRing = movedFraction(rBefore, rAfter);
        System.out.printf("ring (150 vnodes): %.1f%% of keys moved   (ideal ~1/5 = 20%%)%n",
                          movedRing * 100);

        System.out.printf("%nConsistent hashing moved %.0fx fewer keys.%n", movedMod / movedRing);

        // ---- Load balance across 5 nodes ----
        System.out.println("\n=== Load distribution (5 nodes, " + KEYS + " keys) ===");
        printDistribution("modulo", assignMod(mod, keys));
        printDistribution("ring  ", rAfter);

        // ---- Replication / preference list ----
        System.out.println("\n=== Preference list (R=3) for a sample key ===");
        String sample = keys.get(0);
        System.out.println(sample + " -> " + ring.getNodes(sample, 3));
    }

    static Map<String, Node> assignMod(ModuloRouter<Node> r, Iterable<String> keys) {
        Map<String, Node> m = new HashMap<>();
        for (String k : keys) m.put(k, r.getNode(k));
        return m;
    }
    static Map<String, Node> assignRing(ConsistentHashRing<Node> r, Iterable<String> keys) {
        Map<String, Node> m = new HashMap<>();
        for (String k : keys) m.put(k, r.getNode(k));
        return m;
    }

    static double movedFraction(Map<String, Node> before, Map<String, Node> after) {
        long moved = before.entrySet().stream()
                .filter(e -> !e.getValue().equals(after.get(e.getKey())))
                .count();
        return (double) moved / before.size();
    }

    static void printDistribution(String label, Map<String, Node> assignment) {
        Map<Node, Integer> counts = new LinkedHashMap<>();
        assignment.values().forEach(n -> counts.merge(n, 1, Integer::sum));
        counts.entrySet().stream()
              .sorted(Map.Entry.comparingByKey(java.util.Comparator.comparing(Node::id)))
              .forEach(e -> System.out.printf("  %s %-4s %6d keys (%.1f%%)%n",
                      label, e.getKey().id(), e.getValue(),
                      100.0 * e.getValue() / assignment.size()));
    }
}
```

## Step 5 — Run it

```bash
mvn -q compile exec:java -Dexec.mainClass=day22.ShardingDriver
# or, after `mvn package`:
mvn -q exec:java -Dexec.mainClass=day22.ShardingDriver
```

(If you didn't add the `exec-maven-plugin`, just run `ShardingDriver` from your IDE.)

### Expected output (your exact numbers vary slightly by random keys)

```
=== Adding a 5th node to a 4-node cluster ===

modulo  hash(k)%N : 80.0% of keys moved
ring (150 vnodes): 19.7% of keys moved   (ideal ~1/5 = 20%)

Consistent hashing moved 4x fewer keys.

=== Load distribution (5 nodes, 100000 keys) ===
  modulo n0    19984 keys (20.0%)
  modulo n1    20070 keys (20.0%)
  modulo n2    19955 keys (20.0%)
  modulo n3    20012 keys (20.0%)
  modulo n4    19979 keys (20.0%)
  ring   n0    19612 keys (19.6%)
  ring   n1    20431 keys (20.4%)
  ring   n2    19880 keys (19.9%)
  ring   n3    20093 keys (20.1%)
  ring   n4    19984 keys (20.0%)

=== Preference list (R=3) for a sample key ===
user:... -> [n2, n0, n3]
```

Read the headline: **modulo moved 80%** of keys (≈ `(N-1)/N = 4/5`), while **the ring moved ~20%** (≈ `1/N` for the new node's share, since 1 of 5 nodes' worth of keyspace was carved out). Modulo's distribution is *perfectly* even but its membership cost is ruinous; the ring is within ~2% of even **and** cheap to rebalance. That trade — slightly-less-perfect balance for dramatically-cheaper membership changes — is the whole reason consistent hashing exists.

## Step 6 — JUnit 5 tests that assert the properties

Don't trust eyeballed numbers — encode the invariants. (JUnit 5 from Day 2.)

```java
package day22;

import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.IntStream;

import static org.junit.jupiter.api.Assertions.*;

class ConsistentHashRingTest {

    private List<String> keys(int n) {
        return IntStream.range(0, n).mapToObj(i -> "k:" + UUID.randomUUID()).toList();
    }

    private Map<String, Node> assign(ConsistentHashRing<Node> r, List<String> keys) {
        Map<String, Node> m = new HashMap<>();
        keys.forEach(k -> m.put(k, r.getNode(k)));
        return m;
    }

    @Test
    void addingNthNodeMovesRoughlyOneOverN() {
        var ring = new ConsistentHashRing<Node>(200);
        for (int i = 0; i < 4; i++) ring.addNode(new Node("n" + i));
        var ks = keys(50_000);

        var before = assign(ring, ks);
        ring.addNode(new Node("n4"));          // 4 -> 5 nodes
        var after = assign(ring, ks);

        long moved = before.entrySet().stream()
                .filter(e -> !e.getValue().equals(after.get(e.getKey()))).count();
        double frac = (double) moved / ks.size();

        // ideal 1/5 = 0.20; allow generous tolerance for randomness
        assertTrue(frac > 0.12 && frac < 0.28,
                "expected ~0.20 of keys to move, got " + frac);

        // keys that DID move must all have moved TO the new node (ring invariant)
        Node n4 = new Node("n4");
        boolean allToNew = before.entrySet().stream()
                .filter(e -> !e.getValue().equals(after.get(e.getKey())))
                .allMatch(e -> after.get(e.getKey()).equals(n4));
        assertTrue(allToNew, "every moved key must move to the newly added node");
    }

    @Test
    void moduloMovesNearlyEverything() {
        var mod = new ModuloRouter<Node>();
        for (int i = 0; i < 4; i++) mod.addNode(new Node("n" + i));
        var ks = keys(50_000);

        Map<String, Node> before = new HashMap<>();
        ks.forEach(k -> before.put(k, mod.getNode(k)));
        mod.addNode(new Node("n4"));
        long moved = before.entrySet().stream()
                .filter(e -> !e.getValue().equals(mod.getNode(e.getKey()))).count();

        double frac = (double) moved / ks.size();
        assertTrue(frac > 0.70, "modulo should move ~(N-1)/N = 0.8, got " + frac);
    }

    @Test
    void loadIsBalancedWithVnodes() {
        var ring = new ConsistentHashRing<Node>(200);
        int n = 8;
        for (int i = 0; i < n; i++) ring.addNode(new Node("n" + i));
        var counts = new HashMap<Node, Integer>();
        for (String k : keys(80_000)) counts.merge(ring.getNode(k), 1, Integer::sum);

        double ideal = 80_000.0 / n;                       // 10_000
        for (var e : counts.entrySet()) {
            double dev = Math.abs(e.getValue() - ideal) / ideal;
            assertTrue(dev < 0.15, e.getKey() + " off by " + dev); // within 15%
        }
        assertEquals(n, counts.size(), "every node should own some keys");
    }

    @Test
    void preferenceListIsDistinctAndRightSize() {
        var ring = new ConsistentHashRing<Node>(150);
        for (int i = 0; i < 5; i++) ring.addNode(new Node("n" + i));

        List<Node> prefs = ring.getNodes("some-key", 3);
        assertEquals(3, prefs.size());
        assertEquals(3, prefs.stream().distinct().count(), "replicas must be DISTINCT physical nodes");
    }

    @Test
    void weightedNodeGetsProportionallyMoreKeys() {
        var ring = new ConsistentHashRing<Node>(100);
        ring.addNode(new Node("small", 1));
        ring.addNode(new Node("big",   3));   // 3x the vnodes -> ~3x the keys

        var counts = new HashMap<Node, Integer>();
        for (String k : keys(60_000)) counts.merge(ring.getNode(k), 1, Integer::sum);

        int big = counts.get(new Node("big", 3));
        int small = counts.get(new Node("small", 1));
        double ratio = (double) big / small;
        assertTrue(ratio > 2.3 && ratio < 3.7, "expected ~3:1, got " + ratio);
    }
}
```

```bash
mvn -q test
```

All five should pass. The `addingNthNodeMovesRoughlyOneOverN` test is the heart of the day: it asserts both the **~1/N magnitude** *and* the **structural invariant** that every moved key moved *to the new node* (a property modulo hashing flatly violates).

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

**Dynamo & Cassandra rings.** Amazon's Dynamo paper (2007) is the canonical consistent-hashing-in-production reference; Cassandra implements it almost directly. Each node owns a set of token ranges (`num_tokens`, i.e. vnodes); a key's coordinator is the node owning its token, and the **replicas are the next N nodes clockwise** — your `getNodes(key, N)` exactly. Replica placement gets one more wrinkle: a **rack-/datacenter-aware** strategy skips nodes that would put two replicas in the same failure domain, so a rack power loss can't take out all copies.

**Hot shards / hot keys.** Consistent hashing balances *keys*, not *traffic*. One viral key (a celebrity's profile, a flash-sale SKU) still pins all its load to one node — consistent hashing can't help because that key is a single point on the ring. Mitigations: **key splitting** (`celeb:123#0..#9` fanned across the ring), a small **local cache** in front (Day 15) to absorb reads, or **request coalescing**. Knowing that consistent hashing solves *placement* but not *per-key skew* is a senior distinction.

**Rebalancing & bootstrapping.** When a node joins, it must *stream* the data for its newly-owned arcs from the current owners before it can serve them — and serve reads from the old owners until streaming completes (Cassandra's bootstrap). Vnodes make this **parallel and incremental**: the joining node pulls many small ranges from many nodes at once, instead of one giant range from one neighbor.

**Jump consistent hash** (Lamping & Veach, Google, 2014). A ~5-line function `jumpHash(key, numBuckets) -> bucket` that needs **no ring, no memory** and gives near-perfect balance with optimal `1/N` movement when `numBuckets` grows. The catch: buckets are `0..N-1` integers and you can only *grow*/shrink at the top — you **can't remove an arbitrary node**, only the highest-numbered one. Perfect for sharding into a numbered set (e.g. fixed shard count); not for an arbitrary churning node set.

**Rendezvous (Highest Random Weight) hashing.** For each candidate node compute `score = hash(key, nodeId)` and pick the **max**. No ring, naturally handles weights, trivially gives you the top-R for replication (just take the R highest scores), and removing a node only reshuffles *its* keys. Cost is `O(N)` per lookup vs the ring's `O(log V)`, so it shines when `N` is small/moderate. Many engineers now prefer HRW over a vnode ring for its simplicity. Worth implementing as a stretch goal to feel the difference.

**Resharding a live system.** The hard operational reality: changing the ring means moving data *while serving traffic*. The playbook: (1) **double-write / dual-read** during migration; (2) **backfill** historical data for the new ranges; (3) **flip reads** to the new owner once backfill + tailing writes have caught up; (4) **stop double-writing** and clean up. Tie this back to Day 20 (Outbox) and Day 7 (Idempotency) — migrations replay and retry, so every move must be idempotent. Redis Cluster does this with 16384 fixed **hash slots** (`CRC16(key) % 16384`) that map to nodes; resharding moves *slots* (a level of indirection — note this is the **directory** strategy layered over hashing!), and clients follow `MOVED`/`ASK` redirections during the move.

**Why fixed slots (Redis) vs free ring (Cassandra)?** Redis Cluster's 16384 slots are a deliberate simplification: a tiny, gossip-able slot→node map (≈2KB) every client caches, instead of a continuous token ring. It's the directory strategy with a *bounded* directory — cheaper to reason about and migrate, at the cost of a fixed maximum granularity. Recognizing that real systems blend the three strategies (hash *into* slots, then a directory maps slots → nodes) is the senior takeaway.

### Stretch goals

1. **Lock-free reads via immutable snapshots.** Replace `synchronized` with a `volatile` reference to an immutable ring (a frozen `TreeMap` or a sorted `long[]` + parallel node array). On `addNode`/`removeNode`, build a new snapshot and atomically swap it. Readers never block — exactly how a production router serves hot-path lookups.
2. **Implement rendezvous (HRW) hashing** with the same `HashRing<N>` interface and add a JUnit test asserting it *also* moves ~1/N keys on membership change. Benchmark `getNode` latency vs the vnode ring as `N` grows from 5 to 500 (use JMH if you know it, or a crude `System.nanoTime` loop) and find the crossover.
3. **Implement jump consistent hash** and write a test proving it moves *exactly* the optimal fraction when `numBuckets` grows by one — then demonstrate its limitation by trying (and failing cleanly) to remove a middle node.
4. **Simulate a rebalance.** Track, per key, its `R=3` preference list before and after removing a node; report not just "how many keys' owners changed" but "how many *bytes* must stream and from→to which nodes," producing a migration plan. Bonus: weight nodes unevenly and watch the plan shift.

### Day 23 teaser

You've now got a `ConsistentHashRing` and a Dynamo-style preference list — but every test so far ran against in-memory fakes. **Day 23: Testcontainers.** You'll spin up *real* Redis, Postgres, and Kafka instances inside your JUnit tests via throwaway Docker containers — so the caching (Day 16), JPA (Day 12), and Kafka (Day 18) code you've written gets verified against the actual systems, not mocks. We'll wire a Redis Cluster (which, as you just learned, shards by 16384 hash slots) into a Testcontainers integration test and watch a key route to its real slot.
