# Day 2: Collections, Generics & a Hash Index

| | |
|---|---|
| 🏗️ **Project** | **HashIndex** — a generic hash-index map built from scratch |
| ☕ **Java & language skills** | Generics (`<K, V>`, type erasure, bounded use), collections, the `equals`/`hashCode` contract, arrays, and reasoning about O(1) vs. O(n) lookups |
| 🧰 **Library / tool** | JUnit 5 (Jupiter) — `@Test`, assertions, `@ParameterizedTest`, Surefire |
| 🗄️ **DB / distributed-systems concept** | Hash index — buckets, collision resolution, load factor, rehashing |
| 📊 **Difficulty** | Easy |

---

## Concept primer

### What problem does a hash table solve?

You have keys (`"alice"`, `42`, a `UserId`) and you want to find the value associated with a key **fast** — ideally without scanning every entry. A balanced tree (Day 6's B-Tree) gives you O(log n). A **hash table** does better on average: **O(1)**.

The trick is an array plus a function:

1. Keep an array of *buckets* (slots), say length `N`.
2. Compute `hashCode()` on the key — an `int` that's *derived from the key's contents*.
3. Reduce that `int` to a valid array index: `index = hash % N` (Java actually uses `hash & (N-1)` because `N` is always a power of two — a bit-mask is faster than `%`).
4. Store the entry in `buckets[index]`.

To look a key up, you recompute the index and go *straight to that bucket*. No scan. That's the magic: **the key tells you where it lives.**

```
buckets (N = 8)
 index:  0     1     2     3     4     5     6     7
        [ ]   [A]   [ ]   [ ]   [B→C] [ ]   [D]   [ ]
                                  ^^^^^
                            collision: B and C hashed to slot 4,
                            so we chain them in a linked list
```

### Collisions are inevitable — and that's fine

Different keys can produce the same bucket index (the pigeonhole principle guarantees it once you have more keys than slots). This is a **collision**. Two main strategies:

- **Separate chaining** (what we'll build, and what `java.util.HashMap` uses): each bucket holds a small linked list (or, for big buckets, a tree). On collision you append/scan the list. Simple, robust, degrades gracefully.
- **Open addressing** (probing): on collision you scan forward to the next free slot (linear/quadratic probing, or double hashing). No per-bucket lists, great cache locality, but deletions and high load factors are trickier. Used by `IdentityHashMap` and many high-performance maps.

### Load factor & resizing — keeping buckets short

If you put 1,000 entries into 8 buckets, each bucket holds ~125 items and lookup is effectively O(n) — a linked-list scan. The fix is to **grow the array** before that happens.

- **Load factor** = `size / capacity`. Java's default threshold is **0.75**.
- When `size > capacity * loadFactor`, you **resize**: allocate a bigger array (double it) and **rehash** every existing entry into the new array, because `hash & (N-1)` changes when `N` changes.
- Resizing is O(n), but it's *amortized* across many cheap inserts, so the average insert stays O(1).

### The `equals` / `hashCode` contract — the part that bites everyone

A hash table is only correct if your keys obey this contract:

1. **Consistency:** if `a.equals(b)` is `true`, then `a.hashCode() == b.hashCode()` **must** be true.
2. **Stability:** `hashCode()` must return the same value as long as the object's `equals`-relevant fields don't change.
3. The reverse is *not* required: unequal objects *may* share a hash code (that's just a collision).

**Why it matters:** to find a key, the table first jumps to `bucket = hash & (N-1)`, *then* walks that bucket calling `equals`. If you override `equals` but forget `hashCode` (or vice versa), equal objects land in **different buckets** and the map can't find them — `get` returns `null` for a key you definitely put in. This is the single most common Java bug around maps. Rule of thumb: **always override `equals` and `hashCode` together**, over the same fields. (Day 5's records do this for you automatically.)

A *good* `hashCode` spreads keys uniformly across buckets. A *bad* one (e.g. `return 1;` for everything) is technically legal but collapses every entry into one bucket — turning your O(1) map into an O(n) linked list.

### Where databases use this

- **Hash indexes:** PostgreSQL has `CREATE INDEX ... USING hash`; in-memory engines and MySQL's MEMORY tables use hash indexes. They give O(1) **equality** lookups (`WHERE id = ?`) but are useless for ranges (`WHERE id > ?`) or `ORDER BY` — because a hash deliberately destroys ordering. That's why B-trees (Day 6) remain the default index.
- **Hash joins:** the classic way to join two tables is to build a hash table on the smaller table's join key, then probe it row-by-row from the larger table — exactly the structure you're about to build.
- **Partitioning / sharding:** "which node owns this key?" is `hash(key) % numNodes`. We'll improve on that naive version with **consistent hashing** on Day 22.

---

## Prerequisites

- JDK **21** installed (`java -version` should print 21).
- Maven installed (`mvn -version`).
- Comfort with Day 1's Maven project layout. We'll create a fresh `day2/` project here so it stands alone.

---

## 🛠️ Project Walkthrough — HashIndex

This is the hands-on build: follow the numbered steps below to create the project, implement `HashIndex<K, V>`, write the JUnit 5 tests, and run them.

## Step 1 — Create the project

From `/home/mahdi/Projects/learning/java-30-days/day2`, create the standard Maven layout:

```bash
mkdir -p src/main/java/com/learn/index
mkdir -p src/test/java/com/learn/index
```

You should end up with:

```
day2/
├── pom.xml
├── src/main/java/com/learn/index/HashIndex.java
└── src/test/java/com/learn/index/HashIndexTest.java
```

## Step 2 — The `pom.xml`

Create `day2/pom.xml`. This pulls in JUnit 5 and configures Surefire (the Maven plugin that runs tests during `mvn test`). The `junit-jupiter` artifact is an aggregator that brings in the API, the engine, and the params extension in one dependency.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.learn</groupId>
    <artifactId>day2-hash-index</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.release>21</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <junit.version>5.10.2</junit.version>
    </properties>

    <dependencies>
        <!-- One aggregator dependency: API + engine + params -->
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>${junit.version}</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <!-- Surefire runs JUnit tests on `mvn test`. 3.x has native JUnit 5 support. -->
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
            </plugin>
        </plugins>
    </build>
</project>
```

## Step 3 — Implement `HashIndex<K, V>`

Create `src/main/java/com/learn/index/HashIndex.java`. Read the comments — they explain *why* each piece exists. This is a real, working generic hash map with separate chaining and automatic resizing.

```java
package com.learn.index;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * A from-scratch generic hash map using an array of buckets and
 * separate-chaining collision resolution (singly-linked nodes per bucket).
 *
 * Mirrors the core mechanics of java.util.HashMap so you can SEE how it works:
 *  - power-of-two capacity so we can index with a bitmask instead of %
 *  - load-factor-driven doubling + rehash
 *  - the hashCode/equals contract is what makes get() find what put() stored
 *
 * Not thread-safe, and not a full Map implementation — just enough to learn from.
 *
 * @param <K> key type
 * @param <V> value type
 */
public class HashIndex<K, V> {

    /** One entry in a bucket's chain. */
    private static final class Node<K, V> {
        final int hash;     // cached spread-hash of the key
        final K key;
        V value;
        Node<K, V> next;    // next entry in the same bucket (collision chain)

        Node(int hash, K key, V value, Node<K, V> next) {
            this.hash = hash;
            this.key = key;
            this.value = value;
            this.next = next;
        }
    }

    private static final int DEFAULT_CAPACITY = 16;   // must be a power of two
    private static final float DEFAULT_LOAD_FACTOR = 0.75f;

    private Node<K, V>[] buckets;
    private int size;                 // number of key-value entries stored
    private int threshold;            // resize when size exceeds this
    private final float loadFactor;

    public HashIndex() {
        this(DEFAULT_CAPACITY, DEFAULT_LOAD_FACTOR);
    }

    @SuppressWarnings("unchecked")
    public HashIndex(int initialCapacity, float loadFactor) {
        if (initialCapacity <= 0) {
            throw new IllegalArgumentException("capacity must be > 0: " + initialCapacity);
        }
        if (loadFactor <= 0 || Float.isNaN(loadFactor)) {
            throw new IllegalArgumentException("loadFactor must be > 0: " + loadFactor);
        }
        int cap = tableSizeFor(initialCapacity);  // round up to a power of two
        this.buckets = (Node<K, V>[]) new Node[cap];
        this.loadFactor = loadFactor;
        this.threshold = (int) (cap * loadFactor);
    }

    /** Smallest power of two >= cap (so cap=10 -> 16, cap=16 -> 16). */
    private static int tableSizeFor(int cap) {
        int n = 1;
        while (n < cap) {
            n <<= 1;
        }
        return n;
    }

    /**
     * Spread the key's hashCode. Like HashMap, we XOR the high bits down into
     * the low bits, because index = hash & (capacity-1) only looks at the LOW
     * bits — and many real hashCodes vary mostly in their high bits.
     */
    private static int spread(Object key) {
        if (key == null) {
            return 0;  // we support a single null key, like HashMap
        }
        int h = key.hashCode();
        return h ^ (h >>> 16);
    }

    /** Map a spread-hash to a bucket index. capacity is a power of two. */
    private int indexFor(int hash) {
        return hash & (buckets.length - 1);
    }

    /**
     * Insert or update. Returns the previous value for the key, or null if none.
     */
    public V put(K key, V value) {
        int hash = spread(key);
        int idx = indexFor(hash);

        // Walk the chain: update if the key already exists.
        for (Node<K, V> n = buckets[idx]; n != null; n = n.next) {
            if (n.hash == hash && keysEqual(n.key, key)) {
                V old = n.value;
                n.value = value;
                return old;
            }
        }

        // New key: prepend to the chain (O(1) insert).
        buckets[idx] = new Node<>(hash, key, value, buckets[idx]);
        size++;
        if (size > threshold) {
            resize();
        }
        return null;
    }

    /** Returns the value for the key, or null if absent. */
    public V get(K key) {
        Node<K, V> n = findNode(key);
        return n == null ? null : n.value;
    }

    public boolean containsKey(K key) {
        return findNode(key) != null;
    }

    private Node<K, V> findNode(K key) {
        int hash = spread(key);
        int idx = indexFor(hash);
        for (Node<K, V> n = buckets[idx]; n != null; n = n.next) {
            if (n.hash == hash && keysEqual(n.key, key)) {
                return n;
            }
        }
        return null;
    }

    /** Removes the key. Returns the removed value, or null if not present. */
    public V remove(K key) {
        int hash = spread(key);
        int idx = indexFor(hash);
        Node<K, V> prev = null;
        for (Node<K, V> n = buckets[idx]; n != null; prev = n, n = n.next) {
            if (n.hash == hash && keysEqual(n.key, key)) {
                if (prev == null) {
                    buckets[idx] = n.next;   // removing the head of the chain
                } else {
                    prev.next = n.next;      // unlink from the middle/tail
                }
                size--;
                return n.value;
            }
        }
        return null;
    }

    public int size() {
        return size;
    }

    public boolean isEmpty() {
        return size == 0;
    }

    /** Snapshot of all keys (order is unspecified — hashing destroys order). */
    public List<K> keys() {
        List<K> out = new ArrayList<>(size);
        for (Node<K, V> head : buckets) {
            for (Node<K, V> n = head; n != null; n = n.next) {
                out.add(n.key);
            }
        }
        return out;
    }

    /** Current bucket-array length — exposed so tests can observe resizing. */
    int capacity() {
        return buckets.length;
    }

    /** Null-safe key comparison — this is where the equals contract is honored. */
    private boolean keysEqual(K a, K b) {
        return Objects.equals(a, b);
    }

    /**
     * Double the capacity and rehash every entry. Necessary because
     * index = hash & (capacity-1) changes when capacity changes.
     */
    @SuppressWarnings("unchecked")
    private void resize() {
        int newCap = buckets.length << 1;       // double
        Node<K, V>[] newBuckets = (Node<K, V>[]) new Node[newCap];

        for (Node<K, V> head : buckets) {
            Node<K, V> n = head;
            while (n != null) {
                Node<K, V> next = n.next;        // remember before we relink
                int idx = n.hash & (newCap - 1); // recompute index for new size
                n.next = newBuckets[idx];        // prepend into new bucket
                newBuckets[idx] = n;
                n = next;
            }
        }

        this.buckets = newBuckets;
        this.threshold = (int) (newCap * loadFactor);
    }
}
```

### Why these details matter (skim now, revisit after the tests pass)

- **`spread()`**: without it, keys whose hashes differ only in high bits would all collide once we mask down to the low bits. One XOR mixes the high bits in cheaply.
- **Power-of-two capacity + `& (N-1)`**: equivalent to `% N` when `N` is a power of two, but a single AND instruction instead of an integer division.
- **Prepend on insert**: putting the new node at the *head* of the chain is O(1). We already scanned the chain to check for an existing key, so duplicates are handled.
- **Caching `n.hash`**: lets `resize()` reindex without recomputing `hashCode()`, and lets `get`/`put` skip the (possibly expensive) `equals` call when hashes differ.

## Step 4 — Write the JUnit 5 tests

Create `src/test/java/com/learn/index/HashIndexTest.java`. This is your JUnit 5 tour: lifecycle, plain assertions, parameterized tests, and an explicit collision/resize stress test. It also defines a deliberately bad key (`SameHashKey`) to prove the chain logic works when *every* key collides.

```java
package com.learn.index;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ThreadLocalRandom;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@DisplayName("HashIndex<K,V>")
class HashIndexTest {

    private HashIndex<String, Integer> index;

    // @BeforeEach runs before EVERY @Test, giving each test a fresh, isolated map.
    @BeforeEach
    void setUp() {
        index = new HashIndex<>();
    }

    @Test
    @DisplayName("a new index is empty")
    void newIndexIsEmpty() {
        assertTrue(index.isEmpty());
        assertEquals(0, index.size());
        assertNull(index.get("missing"));
        assertFalse(index.containsKey("missing"));
    }

    @Test
    @DisplayName("put then get returns the stored value")
    void putThenGet() {
        assertNull(index.put("alice", 1));   // put returns previous value (none)
        assertEquals(1, index.get("alice"));
        assertEquals(1, index.size());
        assertTrue(index.containsKey("alice"));
    }

    @Test
    @DisplayName("put with an existing key updates and returns the old value")
    void putUpdatesExistingKey() {
        index.put("k", 10);
        Integer previous = index.put("k", 20);

        assertEquals(10, previous);
        assertEquals(20, index.get("k"));
        assertEquals(1, index.size(), "updating must NOT increase size");
    }

    @Test
    @DisplayName("remove deletes the entry and returns its value")
    void removeEntry() {
        index.put("a", 1);
        index.put("b", 2);

        assertEquals(1, index.remove("a"));
        assertNull(index.get("a"));
        assertFalse(index.containsKey("a"));
        assertEquals(1, index.size());

        assertNull(index.remove("a"), "removing a missing key returns null");
        assertNull(index.remove("never-there"));
    }

    @Test
    @DisplayName("supports a single null key")
    void supportsNullKey() {
        index.put(null, 99);
        assertEquals(99, index.get(null));
        assertTrue(index.containsKey(null));

        index.put(null, 100);                // overwrite via null key
        assertEquals(100, index.get(null));
        assertEquals(1, index.size());
    }

    // ----- Parameterized tests: one method, many inputs -----

    @ParameterizedTest(name = "key=\"{0}\" survives a round-trip")
    @ValueSource(strings = {"", "a", "Z", "hello world", "péché", "🦆"})
    void roundTripsArbitraryStringKeys(String key) {
        index.put(key, 7);
        assertEquals(7, index.get(key));
    }

    @ParameterizedTest(name = "{0} -> {1}")
    @CsvSource({
            "one,   1",
            "two,   2",
            "three, 3",
            "four,  4"
    })
    void storesEachPair(String word, int number) {
        index.put(word, number);
        assertEquals(number, index.get(word));
    }

    // ----- The interesting part: collisions and resizing -----

    /**
     * A key whose hashCode is ALWAYS 42, but whose equals still distinguishes
     * instances by id. Every instance lands in the same bucket, forcing the
     * chaining logic to do real work.
     */
    private record SameHashKey(int id) {
        @Override
        public int hashCode() {
            return 42;
        }
        // record auto-generates equals() over `id`, which is exactly what we want
    }

    @Test
    @DisplayName("handles total hash collisions correctly via chaining")
    void handlesCollisions() {
        HashIndex<SameHashKey, Integer> m = new HashIndex<>();
        int n = 50;
        for (int i = 0; i < n; i++) {
            m.put(new SameHashKey(i), i * 10);
        }
        assertEquals(n, m.size());
        for (int i = 0; i < n; i++) {
            assertEquals(i * 10, m.get(new SameHashKey(i)),
                    "every colliding key must still be findable");
        }
        // Remove from the middle of the long chain, then re-check the rest.
        assertEquals(250, m.remove(new SameHashKey(25)));
        assertNull(m.get(new SameHashKey(25)));
        assertEquals(n - 1, m.size());
        assertEquals(0, m.get(new SameHashKey(0)));
        assertEquals(490, m.get(new SameHashKey(49)));
    }

    @Test
    @DisplayName("resizes (grows capacity) as it fills, without losing entries")
    void resizesWithoutDataLoss() {
        HashIndex<Integer, Integer> m = new HashIndex<>();
        int startCap = m.capacity();          // 16 by default

        for (int i = 0; i < 1000; i++) {
            m.put(i, i * i);
        }

        assertTrue(m.capacity() > startCap, "capacity should have grown");
        assertEquals(1000, m.size());
        for (int i = 0; i < 1000; i++) {
            assertEquals(i * i, m.get(i), "entry " + i + " survived rehashing");
        }
    }

    @Test
    @DisplayName("constructor rejects invalid arguments")
    void rejectsBadConstructorArgs() {
        assertThrows(IllegalArgumentException.class, () -> new HashIndex<>(0, 0.75f));
        assertThrows(IllegalArgumentException.class, () -> new HashIndex<>(16, 0f));
        assertThrows(IllegalArgumentException.class, () -> new HashIndex<>(-1, 0.75f));
    }

    // ----- Differential test against the real thing -----

    @Test
    @DisplayName("behaves identically to java.util.HashMap under random ops")
    void matchesJavaHashMap() {
        HashIndex<Integer, Integer> mine = new HashIndex<>();
        Map<Integer, Integer> ref = new HashMap<>();
        var rnd = ThreadLocalRandom.current();

        for (int i = 0; i < 20_000; i++) {
            int key = rnd.nextInt(500);   // small key space -> lots of collisions & updates
            int op = rnd.nextInt(3);
            switch (op) {
                case 0 -> {                            // put
                    int v = rnd.nextInt();
                    assertEquals(ref.put(key, v), mine.put(key, v));
                }
                case 1 -> assertEquals(ref.get(key), mine.get(key));     // get
                case 2 -> assertEquals(ref.remove(key), mine.remove(key)); // remove
                default -> throw new AssertionError();
            }
            assertEquals(ref.size(), mine.size());
        }

        // Final full reconciliation.
        List<Integer> myKeys = mine.keys();
        assertEquals(ref.size(), myKeys.size());
        for (Integer k : myKeys) {
            assertEquals(ref.get(k), mine.get(k));
        }
        // Make the unused import meaningful and assert no phantom keys.
        for (Integer k : ref.keySet()) {
            assertTrue(Objects.equals(ref.get(k), mine.get(k)));
        }
    }
}
```

## Step 5 — Run the tests

```bash
mvn test
```

Maven compiles `src/main` and `src/test`, then Surefire runs every `@Test` and each parameterized invocation.

### Expected output (abridged)

```
[INFO] -------------------------------------------------------
[INFO]  T E S T S
[INFO] -------------------------------------------------------
[INFO] Running com.learn.index.HashIndexTest
[INFO] Tests run: 20, Failures: 0, Errors: 0, Skipped: 0 ...
[INFO]
[INFO] Results:
[INFO]
[INFO] Tests run: 20, Failures: 0, Errors: 0, Skipped: 0
[INFO]
[INFO] BUILD SUCCESS
```

(The exact count includes each `@ParameterizedTest` row: 6 from `@ValueSource` + 4 from `@CsvSource` plus the `@Test` methods.)

Useful variations:

```bash
mvn -q test                              # quieter output
mvn test -Dtest=HashIndexTest#handlesCollisions   # run one test method
mvn test -Dtest='HashIndexTest#put*'              # run by name pattern
```

### Prove your tests actually test something

Temporarily break the implementation and confirm a test goes red — a test that can't fail is worthless:

1. In `resize()`, change `int newCap = buckets.length << 1;` to `int newCap = buckets.length;` (no growth). Run `mvn test`: chains grow unbounded but data isn't lost, so it still passes — capacity assertion catches it only if it stays equal... so instead try: in `put`, comment out `size++;`. Now `size()` and the resize trigger break, and several tests go red. **Revert it** once you've seen the failures.
2. Make `SameHashKey.hashCode()` legal but break `put`'s update path: change `n.value = value;` to do nothing. `putUpdatesExistingKey` fails. Revert.

This habit — *watch the test fail before you trust it* — is core to senior-level testing.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Java's HashMap "treeifies" long chains.** When a single bucket's chain reaches **8 nodes** *and* the table capacity is ≥ 64, `HashMap` converts that bucket from a linked list into a **red-black tree**, making worst-case lookup within the bucket O(log n) instead of O(n). It "untreeifies" back to a list when the bucket shrinks to ≤ 6. This is a defense against hash-collision denial-of-service attacks (e.g., an attacker sending keys engineered to collide). Your `HashIndex` always uses lists — a fine learning version, but now you know what the real one does and why.
- **A good `hashCode` is a performance feature, not a formality.** Uniform distribution keeps chains short and lookups O(1). `String.hashCode()` (`s[0]*31^(n-1) + ...`) and the field-by-field `Objects.hash(...)` pattern are designed for spread. A constant `hashCode` is *correct* but turns the map into a list. Records (Day 5) generate a solid `hashCode`/`equals` for you.
- **`hashCode` ≠ identity.** Two distinct objects sharing a hash code is normal and expected. Equality is decided by `equals`; `hashCode` only chooses the bucket. The contract is one-directional: equal ⇒ same hash; same hash ⇏ equal.
- **Mutable keys are a trap.** If you mutate a key's `equals`/`hashCode` fields *after* inserting it, the entry is now in the wrong bucket and effectively lost. Prefer immutable keys (Day 5).
- **Iteration order is undefined** and changes across resizes. If you need order, that's `LinkedHashMap` (insertion order) or `TreeMap` (sorted) — which connects to Day 6's B-Tree.
- **DB hash indexes vs. B-trees.** Hash indexes win for point lookups and equality joins (build/probe) but can't serve range scans, prefix matches, or `ORDER BY`, because hashing intentionally scrambles order. That single trade-off — O(1) point lookup *vs.* ordered traversal — is why production databases default to B-trees and reach for hash indexes only for equality-heavy workloads. We'll revisit the ordered side in Day 6 and the distributed side (hashing keys across nodes) in Day 22.
- **Amortized analysis.** Resizing is O(n), but because capacity doubles, those O(n) events are rare enough that the *average* cost per insert is O(1). Doubling (geometric growth) is what makes the amortization work; growing by a constant amount would not.

### Stretch goals

1. **Implement `Iterable<K>`** on `HashIndex` so you can write `for (K key : index)`. Add an inner `Iterator` that walks buckets and chains; throw `ConcurrentModificationException` if the map is structurally modified mid-iteration (study how `HashMap` uses a `modCount`).
2. **Add open-addressing variant** `OpenHashIndex<K,V>` using linear probing with tombstones for deletion, and write a JUnit test that compares it head-to-head with the chaining version (and add `@DisplayName`s describing the strategy). Note where deletion gets tricky.
3. **Measure the cost of a bad `hashCode`.** Write a small benchmark (or a timed test) inserting 100k keys with a good `hashCode` vs. a constant one; observe the runtime explode from ~linear to ~quadratic. Bonus: enable JUnit 5's `assertTimeout` to fail if the good path exceeds a budget.
4. **Track and expose collision stats** (max chain length, average chain length, number of resizes) and add a parameterized test asserting that with a good hash, max chain length stays small even at high load.

**Day 3 teaser:** We'll turn this stored data into *queries* — building a Volcano-model (pull-based) query iterator with Java Streams, and leveling up our assertions with the fluent **AssertJ** library.
