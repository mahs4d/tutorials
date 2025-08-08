# 7. Collections Framework

The Collections Framework is Java's standard toolkit for storing and processing groups of objects. It gives you ready-made data structures (lists, sets, maps, queues) so you don't write your own array-resizing or hash-table code. This chapter walks through the interfaces, the concrete classes that implement them, and the mistakes that come up most often in code review. The goal is that you can pick the right collection quickly and explain *why* it is right.

## Table of Contents

- [Collections Framework Overview](#collections-framework-overview)
- [Collection Interface](#collection-interface)
- [List](#list)
- [Set](#set)
- [Queue](#queue)
- [Deque](#deque)
- [Map](#map)
- [ArrayList](#arraylist)
- [LinkedList](#linkedlist)
- [HashMap](#hashmap)
- [LinkedHashMap](#linkedhashmap)
- [TreeMap](#treemap)
- [HashSet](#hashset)
- [LinkedHashSet](#linkedhashset)
- [TreeSet](#treeset)
- [PriorityQueue](#priorityqueue)
- [ArrayDeque](#arraydeque)
- [IdentityHashMap](#identityhashmap)
- [WeakHashMap](#weakhashmap)
- [EnumMap](#enummap)
- [EnumSet](#enumset)
- [Immutable Collections (List.of())](#immutable-collections-listof)
- [Concurrent Collections](#concurrent-collections)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Collections Framework Overview

A **collection** is an object that holds other objects, called *elements*. The Collections Framework is a set of interfaces (contracts) and classes (implementations) that all work together. Almost every Java program uses it, so interviewers expect you to know the shape of it cold.

Here is the interface hierarchy you need in your head. `Iterable` is the root because "something you can loop over" is the most basic idea. `Map` is drawn separately because a map does not extend `Collection` — it holds key-value pairs, not single elements.

```
Iterable<E>
   |
   +-- Collection<E>
          |
          +-- List<E>            (ordered, index-based, duplicates allowed)
          |      +-- ArrayList
          |      +-- LinkedList
          |      +-- Vector (legacy)
          |
          +-- Set<E>              (no duplicates)
          |      +-- HashSet
          |      |      +-- LinkedHashSet
          |      +-- SortedSet<E>
          |             +-- NavigableSet<E>
          |                    +-- TreeSet
          |
          +-- Queue<E>            (head/tail processing)
                 +-- PriorityQueue
                 +-- Deque<E>      (double-ended queue)
                        +-- ArrayDeque
                        +-- LinkedList (also a List)

Map<K,V>                          (separate hierarchy, NOT a Collection)
   +-- HashMap
   |      +-- LinkedHashMap
   +-- SortedMap<K,V>
   |      +-- NavigableMap<K,V>
   |             +-- TreeMap
   +-- Hashtable (legacy)
   +-- IdentityHashMap
   +-- WeakHashMap
   +-- EnumMap
```

Java 21 added two small but handy interfaces:

- **`SequencedCollection<E>`** — for collections that have a well-defined *first* and *last* element (like `List` and `Deque`). It adds `getFirst()`, `getLast()`, `addFirst()`, `addLast()`, `removeFirst()`, `removeLast()`, and `reversed()`. `List` and `Deque` now implement it, so you get these methods on `ArrayList`, `LinkedList`, and `ArrayDeque` for free.
- **`SequencedMap<K,V>`** — the map equivalent, with `firstEntry()`, `lastEntry()`, `putFirst()`, `putLast()`, and `reversed()`. `LinkedHashMap` and `TreeMap` implement it.

```java
List<String> names = new ArrayList<>(List.of("Ann", "Bob", "Cara"));
System.out.println(names.getFirst()); // Ann
System.out.println(names.getLast());  // Cara
List<String> reversed = names.reversed();
System.out.println(reversed);         // [Cara, Bob, Ann]
```

**Why the framework matters in a code review**: picking the wrong collection is one of the most common review findings — using a `List` where a `Set` was meant, using `ArrayList` where a `Deque` fits better, or forgetting that `HashMap` iteration order is unspecified. Knowing the contracts lets you spot these fast.

## Collection Interface

`Collection<E>` is the root interface for anything that is a "bunch of elements" (excluding maps). It defines the operations every collection must support: `add`, `remove`, `contains`, `size`, `isEmpty`, `iterator`, `stream`, `forEach`, and bulk operations like `addAll`, `removeAll`, `retainAll`.

```java
Collection<String> col = new ArrayList<>();
col.add("apple");
col.add("banana");
System.out.println(col.contains("apple")); // true
System.out.println(col.size());            // 2
col.removeIf(s -> s.startsWith("b"));
System.out.println(col);                   // [apple]
```

Key things every `Collection` implementation must get right:

- **`equals`/`hashCode` contract**: `Collection.equals` is defined precisely for `List` and `Set` (element-by-element), so if you put custom objects in a collection, your `equals`/`hashCode` must be consistent (see Chapter 5).
- **Optional operations**: some collections (like the ones from `List.of()`) throw `UnsupportedOperationException` for `add`/`remove`. The interface documents these as "optional operations."
- **Fail-fast iterators**: most `java.util` collections detect structural modification during iteration and throw `ConcurrentModificationException` (covered in detail below).

## List

A `List<E>` is an **ordered** collection accessed by a zero-based index. It allows **duplicate** elements. Think of it as a resizable array with extra methods.

Key methods: `get(int)`, `set(int, E)`, `add(int, E)`, `remove(int)`, `indexOf(Object)`, `subList(from, to)`, plus everything from `Collection` and (since Java 21) `SequencedCollection`.

```java
List<String> fruits = new ArrayList<>();
fruits.add("apple");
fruits.add("banana");
fruits.add(1, "kiwi");       // insert at index 1
System.out.println(fruits);  // [apple, kiwi, banana]
System.out.println(fruits.get(0)); // apple
```

Main implementations: `ArrayList` (array-backed, default choice), `LinkedList` (doubly-linked list, rarely the right default), `Vector` (legacy, synchronized, avoid).

`List.equals` compares element order and values, so `[1, 2, 3]` and `[3, 2, 1]` are **not** equal even though they hold the same elements.

## Set

A `Set<E>` is a collection with **no duplicate elements**, based on `equals`. It models mathematical sets. It has no index-based access.

Sub-interfaces:
- `SortedSet<E>` — elements kept in sorted order (by natural ordering or a `Comparator`).
- `NavigableSet<E>` — adds navigation methods like `floor`, `ceiling`, `higher`, `lower`.

```java
Set<String> tags = new HashSet<>();
tags.add("java");
tags.add("java"); // ignored, duplicate
tags.add("spring");
System.out.println(tags.size()); // 2
System.out.println(tags.contains("java")); // true
```

Main implementations: `HashSet` (fast, no order), `LinkedHashSet` (insertion order), `TreeSet` (sorted order).

## Queue

A `Queue<E>` models a line of elements processed in a specific order, typically **FIFO** (first-in-first-out). Core methods come in two flavors: ones that throw exceptions and ones that return a special value (`null` or `false`) on failure.

| Operation | Throws exception | Returns special value |
|---|---|---|
| Insert | `add(e)` | `offer(e)` |
| Remove | `remove()` | `poll()` |
| Examine | `element()` | `peek()` |

```java
Queue<Integer> queue = new java.util.ArrayDeque<>();
queue.offer(1);
queue.offer(2);
queue.offer(3);
System.out.println(queue.poll()); // 1 (FIFO order)
System.out.println(queue.peek()); // 2, does not remove
```

Main implementations: `PriorityQueue` (elements ordered by priority, not insertion order), `ArrayDeque` (fast general-purpose queue/stack), `LinkedList` (also implements `Queue`, rarely the best choice).

Prefer the `offer`/`poll`/`peek` family in production code — they signal "empty" or "full" with a return value instead of an exception, which is usually easier to handle in a loop.

## Deque

A `Deque<E>` ("deck", **d**ouble-**e**nded **queue**) supports insertion and removal at **both ends**. It can act as a `Queue` (FIFO), a `Stack` (LIFO), or both.

```java
Deque<Integer> stack = new java.util.ArrayDeque<>();
stack.push(1);   // addFirst
stack.push(2);
stack.push(3);
System.out.println(stack.pop()); // 3 (LIFO, like a Stack)

Deque<Integer> dq = new java.util.ArrayDeque<>();
dq.addFirst(10);
dq.addLast(20);
System.out.println(dq); // [10, 20]
System.out.println(dq.pollFirst()); // 10
System.out.println(dq.pollLast());  // 20
```

Because `Deque` extends `SequencedCollection`, it also has `getFirst()`, `getLast()`, and `reversed()`.

**Deque replaces both `Stack` and `java.util.Stack`/legacy usage.** The official Javadoc recommends `ArrayDeque` over `java.util.Stack` (legacy, synchronized, extends `Vector`) for stack behavior, and over `LinkedList` for queue behavior, because `ArrayDeque` is faster and has no synchronization overhead.

## Map

A `Map<K,V>` stores **key-value pairs**. Keys are unique; each key maps to exactly one value. `Map` does **not** extend `Collection` because its elements are pairs, not single objects — but it does expose collection views: `keySet()`, `values()`, and `entrySet()`.

```java
Map<String, Integer> ages = new HashMap<>();
ages.put("Ann", 30);
ages.put("Bob", 25);
ages.put("Ann", 31); // overwrites, put returns old value (30)

for (Map.Entry<String, Integer> entry : ages.entrySet()) {
    System.out.println(entry.getKey() + " -> " + entry.getValue());
}
// Ann -> 31
// Bob -> 25 (order not guaranteed for plain HashMap)
```

Useful modern default methods (Java 8+), all worth knowing for reviews:

```java
Map<String, Integer> scores = new HashMap<>();
scores.putIfAbsent("Ann", 10);        // inserts only if key missing
scores.merge("Ann", 5, Integer::sum); // Ann -> 15
scores.computeIfAbsent("Bob", k -> 0); // Bob -> 0
scores.compute("Bob", (k, v) -> v + 1); // Bob -> 1
scores.getOrDefault("Cara", -1);       // -1, no exception
```

Sub-interfaces:
- `SortedMap<K,V>` — keys kept sorted.
- `NavigableMap<K,V>` — adds `floorKey`, `ceilingKey`, `firstEntry`, `lastEntry`, etc.
- `SequencedMap<K,V>` (Java 21) — `putFirst`, `putLast`, `firstEntry`, `lastEntry`, `reversed()`. Implemented by `LinkedHashMap` and `TreeMap`.

Main implementations: `HashMap`, `LinkedHashMap`, `TreeMap`, plus special-purpose ones: `IdentityHashMap`, `WeakHashMap`, `EnumMap`.

## Master "Which Collection Should I Pick?" Table

Use this as your mental cheat sheet in an interview. "Amortized" means the average cost over many operations; an occasional resize can cost more.

| Collection | Get by index | Get by key | Add/remove at end | Add/remove at front | Contains | Ordering | Duplicates | Null elements |
|---|---|---|---|---|---|---|---|---|
| `ArrayList` | O(1) | — | O(1) amortized | O(n) | O(n) | insertion order | yes | yes |
| `LinkedList` | O(n) | — | O(1) | O(1) | O(n) | insertion order | yes | yes |
| `HashMap` | — | O(1) avg | — | — | O(1) avg (key) | unspecified | keys: no, values: yes | 1 null key, many null values |
| `LinkedHashMap` | — | O(1) avg | — | — | O(1) avg | insertion (or access) order | keys: no | same as HashMap |
| `TreeMap` | — | O(log n) | — | — | O(log n) | sorted by key | keys: no | no null keys |
| `HashSet` | — | — | O(1) avg | — | O(1) avg | unspecified | no | one null |
| `LinkedHashSet` | — | — | O(1) avg | — | O(1) avg | insertion order | no | one null |
| `TreeSet` | — | — | O(log n) | — | O(log n) | sorted | no | no null |
| `ArrayDeque` | — | — | O(1) | O(1) | O(n) | insertion order | yes | no null |
| `PriorityQueue` | — | — | O(log n) insert | O(log n) poll head | O(n) | priority order (only head is guaranteed smallest) | yes | no null |

Quick decision guide:

- Need index access, mostly append at the end → **`ArrayList`**.
- Need a stack or a queue, or fast add/remove at both ends → **`ArrayDeque`**.
- Need uniqueness, don't care about order → **`HashSet`**.
- Need uniqueness and insertion order preserved → **`LinkedHashSet`**.
- Need uniqueness and sorted order → **`TreeSet`**.
- Need key-value lookups, don't care about order → **`HashMap`**.
- Need key-value lookups with predictable iteration order → **`LinkedHashMap`**.
- Need key-value lookups sorted by key, or range queries → **`TreeMap`**.
- Need "always process smallest/largest next" → **`PriorityQueue`**.
- Multithreaded access → see [Concurrent Collections](#concurrent-collections).

## ArrayList

**How it works internally**: `ArrayList` wraps a plain `Object[]` array. When the array is full and you add another element, it allocates a new array (roughly 1.5x the old capacity) and copies everything over (`Arrays.copyOf`). This is why appends are "amortized" O(1) — most calls are cheap, but occasionally one call pays for the copy.

```java
List<Integer> list = new ArrayList<>();       // default capacity, grows as needed
List<Integer> sized = new ArrayList<>(1000);   // pre-size to avoid early resizes
for (int i = 0; i < 5; i++) list.add(i);
list.remove(Integer.valueOf(2));  // removes value 2, not index 2
list.remove(2);                   // removes index 2 (int overload)
System.out.println(list);         // [0, 1, 4] after both removals
```

**Big-O**:
- `get(index)` / `set(index, e)`: O(1) — direct array access.
- `add(e)` (append at end): O(1) amortized, O(n) worst case (on resize).
- `add(index, e)` / `remove(index)` in the middle or front: O(n) — must shift elements.
- `contains` / `indexOf`: O(n) — linear scan.

**Ordering**: preserves insertion order (it's index-based).

**Null handling**: allows multiple `null` elements.

**Thread safety**: not thread-safe. Concurrent modification from multiple threads can corrupt internal state or throw `ConcurrentModificationException`.

**When to choose it**: default `List` choice. Use it when you mostly read by index or append at the end, and rarely insert/remove in the middle or at the front.

## LinkedList

**How it works internally**: a doubly-linked list of nodes; each node holds the element plus references to the previous and next node. It implements both `List` and `Deque`.

```java
LinkedList<String> chain = new LinkedList<>();
chain.addLast("b");
chain.addFirst("a");
chain.addLast("c");
System.out.println(chain); // [a, b, c]
chain.removeFirst();
System.out.println(chain); // [b, c]
```

**Big-O**:
- `addFirst` / `addLast` / `removeFirst` / `removeLast`: O(1) — just pointer updates.
- `get(index)`: O(n) — must walk the list from the nearer end.
- `add(index, e)` / `remove(index)`: O(n) to find the position, O(1) to splice.

**Ordering**: insertion order.

**Null handling**: allows `null` elements.

**Thread safety**: not thread-safe.

**When to choose it**: rarely the best default. Choose it only when you need frequent insert/remove at both ends *and* also need `List` semantics (index access) in the same structure. For pure queue/stack behavior, prefer `ArrayDeque` — it's faster and uses less memory (no per-node object overhead, no boxing of pointers).

```java
// Common mistake: using LinkedList as a general-purpose List
List<Integer> bad = new LinkedList<>();
for (int i = 0; i < 100_000; i++) bad.add(i);
// bad.get(50_000) is O(n) -- walks half the list every time!
```

## HashMap

`HashMap` is the workhorse map implementation. Understanding its internals is one of the most frequently asked collection topics in interviews.

**How it works internally**:

1. **Buckets**: internally, `HashMap` holds an array called the *table*. Each slot in that array is called a **bucket**. A bucket can hold zero, one, or many entries.
2. **Hashing**: when you call `put(key, value)`, the map computes `key.hashCode()`, then applies an internal "spread" function (XORs the hash with its own upper bits) to reduce collisions, then uses `hash & (table.length - 1)` to pick a bucket index. This is why table size is always a power of two — `& (length - 1)` is a fast way to do `% length` when `length` is a power of two.
3. **Collision handling**: if two keys land in the same bucket, they used to be stored as a **linked list** of entries in that bucket. You look up a key by finding its bucket, then walking the list and comparing with `equals()`.
4. **Treeification (JDK 8+)**: if a single bucket's linked list grows to **8 or more** entries (and the table has at least 64 buckets), that bucket is converted into a small **red-black tree** instead of a linked list. This turns worst-case lookup within a bucket from O(n) into O(log n). This mostly protects against pathological hash collisions (e.g., poorly written `hashCode()` or adversarial input) — it is a safety net, not something you should rely on for normal use. If entries in a treeified bucket drop below 6, it converts back to a linked list.
5. **Resizing / load factor**: the map tracks a **load factor**, default `0.75`. When `size > capacity * loadFactor`, the table **doubles** in size and every entry is rehashed into the new, larger table (a "resize" or "rehash"). Default initial capacity is 16, so the first resize happens after the 13th entry (16 * 0.75 = 12). Resizing is O(n) but happens rarely, so `put` is amortized O(1).

```java
Map<String, Integer> inventory = new HashMap<>(); // capacity 16, load factor 0.75
inventory.put("apples", 10);
inventory.put("bananas", 20);
inventory.put(null, 0);          // one null key is allowed
inventory.put("apples", 15);     // overwrites, returns old value 10

System.out.println(inventory.get("apples")); // 15
System.out.println(inventory.get("kiwi"));   // null: no such key
System.out.println(inventory.get(null));     // 0
```

```java
// Pre-sizing avoids repeated resizes when you know the approximate size
Map<String, String> big = new HashMap<>(200); // avoids ~4 resize/rehash cycles
```

**Big-O**: `get`/`put`/`remove`/`containsKey`: O(1) average, O(log n) worst case per bucket after treeification (was O(n) before JDK 8). This assumes a good `hashCode()` distribution; a bad `hashCode()` (e.g., always returning `0`) degrades performance because everything lands in one bucket.

**Ordering**: **unspecified and not guaranteed to be stable** across JDK versions or even across resizes of the same map. Never rely on `HashMap` iteration order.

**Null handling**: allows exactly **one** `null` key and any number of `null` values.

**Thread safety**: not thread-safe. Concurrent `put` calls from multiple threads can corrupt the internal structure (classic bug: infinite loop during resize under older JDKs, or lost updates). Use `ConcurrentHashMap` instead.

**When to choose it**: the default map. Use it whenever you need key-value lookups and don't care about iteration order.

```java
// hashCode()/equals() contract matters for keys!
class BadKey {
    int id;
    BadKey(int id) { this.id = id; }
    // no equals()/hashCode() override -> uses Object identity
}
Map<BadKey, String> map = new HashMap<>();
map.put(new BadKey(1), "a");
System.out.println(map.get(new BadKey(1))); // null! different object, same "logical" key
```

## LinkedHashMap

**How it works internally**: extends `HashMap` and adds a doubly-linked list threading through all entries, to remember order. Every `Map.Entry` also has `before`/`after` pointers.

```java
Map<String, Integer> lru = new LinkedHashMap<>(16, 0.75f, true); // accessOrder = true
lru.put("a", 1);
lru.put("b", 2);
lru.put("c", 3);
lru.get("a"); // touches "a", moves it to the end in access-order mode
System.out.println(lru.keySet()); // [b, c, a]
```

Because it tracks access order, `LinkedHashMap` is the standard building block for a simple **LRU cache**:

```java
class LruCache<K, V> extends LinkedHashMap<K, V> {
    private final int maxSize;
    LruCache(int maxSize) {
        super(16, 0.75f, true); // true = access-order
        this.maxSize = maxSize;
    }
    @Override
    protected boolean removeEldestEntry(Map.Entry<K, V> eldest) {
        return size() > maxSize; // evict the oldest entry once full
    }
}

LruCache<String, String> cache = new LruCache<>(2);
cache.put("a", "1");
cache.put("b", "2");
cache.get("a");      // "a" becomes most-recently-used
cache.put("c", "3"); // evicts "b" (least recently used)
System.out.println(cache.keySet()); // [a, c]
```

**Big-O**: same as `HashMap` — O(1) average for `get`/`put`/`remove` — plus a small constant overhead to maintain the linked list.

**Ordering**: insertion order by default, or access order if constructed with `accessOrder = true`.

**Null handling**: same as `HashMap` (one null key, many null values).

**Thread safety**: not thread-safe.

**When to choose it**: when you need predictable, repeatable iteration order (e.g., for deterministic tests, or to preserve the order fields were added for serialization), or to build an LRU cache.

## TreeMap

**How it works internally**: a **red-black tree** (a self-balancing binary search tree), keyed by `Comparable` order or a supplied `Comparator`. Every insert/lookup walks down the tree comparing keys.

```java
TreeMap<String, Integer> sorted = new TreeMap<>();
sorted.put("banana", 2);
sorted.put("apple", 1);
sorted.put("cherry", 3);
System.out.println(sorted); // {apple=1, banana=2, cherry=3} -- sorted by key

System.out.println(sorted.firstKey());        // apple
System.out.println(sorted.lastKey());         // cherry
System.out.println(sorted.ceilingKey("b"));   // banana (smallest key >= "b")
System.out.println(sorted.floorKey("b"));     // apple  (largest key <= "b")
System.out.println(sorted.headMap("banana")); // {apple=1} -- keys strictly less than "banana"
```

**Big-O**: `get`/`put`/`remove`/`containsKey`: O(log n). Range operations (`headMap`, `tailMap`, `subMap`) are O(log n) to locate the boundary, and the returned view is backed by the original map.

**Ordering**: sorted by natural ordering of keys, or by the `Comparator` given at construction time.

**Null handling**: **no null keys** allowed (throws `NullPointerException` — comparisons need a real key). Null values are allowed if the comparator doesn't touch values.

**Thread safety**: not thread-safe.

**When to choose it**: when you need keys sorted, or range queries (`floorKey`, `ceilingKey`, `subMap`), or the smallest/largest key on demand.

```java
// Custom key type needs a Comparator, or must implement Comparable
TreeMap<String, Integer> byLength = new TreeMap<>(Comparator.comparingInt(String::length));
byLength.put("kiwi", 1);
byLength.put("fig", 2);
System.out.println(byLength); // {fig=2, kiwi=1} -- sorted by string length
```

## HashSet

**How it works internally**: backed internally by a `HashMap<E, Object>` where every element is stored as a key, and all values point to a shared dummy constant object. So everything said about `HashMap` buckets, hashing, treeification, and resizing applies here too.

```java
Set<String> set = new HashSet<>();
set.add("x");
set.add("y");
set.add("x"); // ignored -- already present
System.out.println(set.size()); // 2
System.out.println(set);        // order unspecified, e.g. [x, y] or [y, x]
```

**Big-O**: `add`/`remove`/`contains`: O(1) average, same worst-case caveats as `HashMap`.

**Ordering**: unspecified.

**Null handling**: allows one `null` element.

**Thread safety**: not thread-safe.

**When to choose it**: default `Set` choice, when you need fast membership tests and don't care about order.

## LinkedHashSet

**How it works internally**: backed by a `LinkedHashMap` internally, so it has the same linked-list-through-buckets trick to preserve insertion order.

```java
Set<String> visited = new LinkedHashSet<>();
visited.add("home");
visited.add("cart");
visited.add("home"); // ignored, but original position is kept
visited.add("checkout");
System.out.println(visited); // [home, cart, checkout]
```

**Big-O**: same as `HashSet` — O(1) average.

**Ordering**: insertion order, preserved.

**Null handling**: one `null` allowed.

**Thread safety**: not thread-safe.

**When to choose it**: when you want set semantics (no duplicates) but also need to remember the order elements were added — e.g., de-duplicating a list while preserving first-seen order.

```java
List<String> withDupes = List.of("b", "a", "b", "c", "a");
Set<String> deduped = new LinkedHashSet<>(withDupes);
System.out.println(deduped); // [b, a, c] -- de-duplicated, first-seen order kept
```

## TreeSet

**How it works internally**: backed by a `TreeMap<E, Object>`, so it's a red-black tree under the hood.

```java
TreeSet<Integer> scores = new TreeSet<>(List.of(42, 7, 15, 4));
System.out.println(scores);          // [4, 7, 15, 42] -- sorted
System.out.println(scores.first());  // 4
System.out.println(scores.last());   // 42
System.out.println(scores.higher(15)); // 42 (smallest element strictly greater than 15)
System.out.println(scores.lower(15));  // 7  (largest element strictly less than 15)
```

**Big-O**: `add`/`remove`/`contains`: O(log n).

**Ordering**: sorted, by natural order or supplied `Comparator`.

**Null handling**: **no nulls** (comparisons would throw `NullPointerException`).

**Thread safety**: not thread-safe.

**When to choose it**: when you need a de-duplicated, always-sorted collection, or navigation methods like `first()`, `last()`, `higher()`, `lower()`, `subSet()`.

## PriorityQueue

**How it works internally**: a **binary heap** stored in an array. The heap property guarantees the smallest element (by natural order or comparator) is always at the root/head, so `peek()`/`poll()` are cheap. It is **not** a full sort — only the head is guaranteed to be the minimum; the rest of the internal array is *not* in sorted order.

```java
PriorityQueue<Integer> pq = new PriorityQueue<>();
pq.offer(5);
pq.offer(1);
pq.offer(3);
System.out.println(pq.poll()); // 1 (smallest first)
System.out.println(pq.poll()); // 3
System.out.println(pq.poll()); // 5

// Max-heap using a reverse comparator
PriorityQueue<Integer> maxHeap = new PriorityQueue<>(Comparator.reverseOrder());
maxHeap.offer(5);
maxHeap.offer(1);
maxHeap.offer(3);
System.out.println(maxHeap.poll()); // 5 (largest first)
```

```java
// Common use case: "top-k" or scheduling by priority
record Task(String name, int priority) {}
PriorityQueue<Task> tasks = new PriorityQueue<>(Comparator.comparingInt(Task::priority));
tasks.offer(new Task("cleanup", 3));
tasks.offer(new Task("urgent-fix", 1));
tasks.offer(new Task("report", 2));
System.out.println(tasks.poll().name()); // urgent-fix (lowest number = highest priority)
```

**Big-O**: `offer`/`add` (insert): O(log n). `poll`/`remove` head: O(log n). `peek`: O(1). `contains` (arbitrary element): O(n).

**Ordering**: only the head element order is guaranteed; iterating the queue directly does **not** yield sorted order (use repeated `poll()` for that).

**Null handling**: **no nulls** — throws `NullPointerException` (nulls can't be compared).

**Thread safety**: not thread-safe. For a concurrent priority queue, use `PriorityBlockingQueue`.

**When to choose it**: task scheduling, "always process the most urgent/smallest item next," or top-k problems (e.g., "k largest elements in a stream").

## ArrayDeque

**How it works internally**: a resizable circular array (a ring buffer) with head and tail indices. Unlike `ArrayList`, it can grow efficiently at both ends because it wraps around the array instead of always shifting from index 0.

```java
Deque<String> deque = new ArrayDeque<>();
deque.addFirst("b");
deque.addFirst("a");
deque.addLast("c");
System.out.println(deque); // [a, b, c]

// as a Stack (LIFO)
Deque<Integer> stack = new ArrayDeque<>();
stack.push(1); stack.push(2); stack.push(3);
System.out.println(stack.pop()); // 3

// as a Queue (FIFO)
Deque<Integer> queue = new ArrayDeque<>();
queue.offer(1); queue.offer(2); queue.offer(3);
System.out.println(queue.poll()); // 1
```

**Big-O**: `addFirst`/`addLast`/`removeFirst`/`removeLast`/`peekFirst`/`peekLast`: O(1) amortized. `get(index)`-style access is not offered (it's not a `List`); `contains`: O(n).

**Ordering**: insertion order (head to tail).

**Null handling**: **no nulls** — `null` is used internally as a sentinel to mean "empty," so `add(null)` throws `NullPointerException`.

**Thread safety**: not thread-safe. For concurrent use, see `ConcurrentLinkedDeque` or `LinkedBlockingDeque`.

**When to choose it**: the default choice for both stacks and queues. Faster than `Stack` (legacy, synchronized) and faster than `LinkedList` for the same job, with less memory overhead.

## IdentityHashMap

**How it works internally**: looks like `HashMap`, but uses **reference equality** (`==`) instead of `.equals()`, and `System.identityHashCode()` instead of `.hashCode()`, to compare and hash keys. Two distinct objects that are "equal" by `.equals()` are treated as **different** keys if they are not the same object in memory.

```java
Map<String, String> identityMap = new IdentityHashMap<>();
String a = new String("key");
String b = new String("key"); // different object, same content
identityMap.put(a, "first");
identityMap.put(b, "second"); // treated as a DIFFERENT key
System.out.println(identityMap.size()); // 2, not 1!
System.out.println(identityMap.get(a)); // first
System.out.println(identityMap.get(new String("key"))); // null -- not the same reference
```

**Big-O**: O(1) average for `get`/`put` — same style of hash table as `HashMap`, just with identity hashing.

**Ordering**: unspecified.

**Null handling**: allows a `null` key and null values.

**Thread safety**: not thread-safe.

**When to choose it**: rare. Useful for object-graph traversal algorithms (e.g., serialization, deep-copy, cycle detection) where you need to track "have I visited this exact object instance before," regardless of its `.equals()` implementation. The JDK itself uses it in `ObjectOutputStream`.

## WeakHashMap

**How it works internally**: stores keys as **weak references**. If nothing else in the program holds a strong reference to a key, the garbage collector is free to reclaim it. Once a key is collected, its entry is removed from the map automatically (lazily, on the next access or via an internal cleanup on the next operation).

```java
Map<Object, String> cache = new WeakHashMap<>();
Object key = new Object();
cache.put(key, "metadata");
System.out.println(cache.size()); // 1

key = null;          // drop the only strong reference
System.gc();          // suggest GC (not guaranteed, but likely in this simple example)
// after GC runs, the entry may be gone:
// System.out.println(cache.size()); // often 0, though timing isn't guaranteed
```

**Big-O**: O(1) average for `get`/`put`, same as `HashMap`.

**Ordering**: unspecified.

**Null handling**: allows a null key/value (though a null key is a bit unusual conceptually).

**Thread safety**: not thread-safe.

**When to choose it**: caches or metadata maps keyed by objects you don't want to keep alive artificially — e.g., attaching extra data to objects managed elsewhere, such as classloader-scoped caches. Not a general-purpose cache eviction tool; for size/time-based eviction, prefer a real caching library (Caffeine, Guava) or `LinkedHashMap`-based LRU.

## EnumMap

**How it works internally**: a specialized `Map` implementation for **enum keys only**, backed internally by a plain array indexed by the enum constant's `ordinal()`. No hashing needed at all.

```java
enum Day { MON, TUE, WED, THU, FRI, SAT, SUN }

EnumMap<Day, String> schedule = new EnumMap<>(Day.class);
schedule.put(Day.MON, "Standup");
schedule.put(Day.FRI, "Retro");
System.out.println(schedule); // {MON=Standup, FRI=Retro} -- always in enum declaration order
```

**Big-O**: `get`/`put`/`remove`: O(1) — direct array indexing by ordinal, no hash collisions possible.

**Ordering**: always iterates in the natural order the enum constants are declared (i.e., ordinal order), regardless of insertion order.

**Null handling**: no null keys (throws `NullPointerException`); null values allowed.

**Thread safety**: not thread-safe.

**When to choose it**: whenever your map key is an enum. It is faster and more memory-efficient than `HashMap<EnumType, V>`, and its guaranteed ordering is a nice bonus.

## EnumSet

**How it works internally**: a specialized `Set` for enum values, internally represented as a **bitmask** (a `long`, or an array of `long`s for enums with more than 64 constants). Membership tests and unions/intersections become simple bitwise operations.

```java
enum Permission { READ, WRITE, EXECUTE, DELETE }

EnumSet<Permission> readWrite = EnumSet.of(Permission.READ, Permission.WRITE);
EnumSet<Permission> all = EnumSet.allOf(Permission.class);
EnumSet<Permission> none = EnumSet.noneOf(Permission.class);
EnumSet<Permission> everythingButDelete = EnumSet.complementOf(EnumSet.of(Permission.DELETE));

System.out.println(readWrite);            // [READ, WRITE]
System.out.println(everythingButDelete);  // [READ, WRITE, EXECUTE]
System.out.println(readWrite.contains(Permission.READ)); // true
```

**Big-O**: `add`/`remove`/`contains`: O(1) — bitmask operations.

**Ordering**: natural order of enum declaration (ordinal order).

**Null handling**: no nulls (throws `NullPointerException`).

**Thread safety**: not thread-safe (though because it's so compact, wrapping with `Collections.synchronizedSet` is cheap if needed).

**When to choose it**: whenever you have a set of flags/options drawn from a fixed enum — e.g., permissions, feature flags, days of the week. Much faster and more compact than `HashSet<EnumType>`.

## Immutable Collections (List.of())

Since Java 9, the JDK ships **factory methods** that create truly immutable collections directly, without needing a mutable builder first.

```java
List<String> list = List.of("a", "b", "c");
Set<String> set = Set.of("x", "y", "z");
Map<String, Integer> map = Map.of("a", 1, "b", 2);
Map<String, Integer> bigMap = Map.ofEntries(
    Map.entry("a", 1),
    Map.entry("b", 2),
    Map.entry("c", 3)
);
```

Key rules of these factories, all commonly tested in interviews:

1. **Null-hostile**: passing `null` as an element, key, or value throws `NullPointerException` immediately — even just constructing the list fails, it's not a lazy check.
   ```java
   List.of("a", null); // throws NullPointerException
   ```
2. **No duplicates in `Set.of`/`Map.of`**: passing a duplicate element or key throws `IllegalArgumentException` at creation time.
   ```java
   Set.of("a", "a"); // throws IllegalArgumentException: duplicate element
   Map.of("a", 1, "a", 2); // throws IllegalArgumentException: duplicate key
   ```
3. **Truly unmodifiable**: any mutating call (`add`, `remove`, `set`, `put`, `clear`, ...) throws `UnsupportedOperationException`.
   ```java
   List<String> immutable = List.of("a", "b");
   immutable.add("c"); // throws UnsupportedOperationException
   ```

**`List.copyOf` / `Set.copyOf` / `Map.copyOf`**: produce an immutable *copy* of an existing collection. If the source is already an immutable instance from these same factories, it may return the same instance instead of copying (an optimization, not something to rely on).

```java
List<String> mutable = new ArrayList<>(List.of("a", "b"));
List<String> snapshot = List.copyOf(mutable); // independent, immutable snapshot
mutable.add("c");
System.out.println(snapshot); // [a, b] -- unaffected by later changes to "mutable"
```

**`Collectors.toUnmodifiableList/Set/Map`**: the Streams equivalent, producing an immutable result directly from a stream pipeline.

```java
List<String> upper = java.util.stream.Stream.of("a", "b", "c")
        .map(String::toUpperCase)
        .collect(java.util.stream.Collectors.toUnmodifiableList());
// upper.add("D") would throw UnsupportedOperationException
```

**Immutable factories vs `Collections.unmodifiableList` — not the same thing**: `Collections.unmodifiableXxx` wraps an existing mutable collection in a **read-only view**. The view itself can't be modified directly, but if you keep a reference to the underlying mutable collection, changes there still show through the view.

```java
List<String> backing = new ArrayList<>(List.of("a", "b"));
List<String> view = Collections.unmodifiableList(backing);

view.add("c");     // throws UnsupportedOperationException (the view is read-only)
backing.add("c");  // allowed! and it changes what "view" shows
System.out.println(view); // [a, b, c] -- the "immutable" view just changed
```

| | `Collections.unmodifiableList` | `List.of()` / `List.copyOf()` |
|---|---|---|
| Backed by | the original mutable list (a view) | its own internal storage |
| Changes to source visible? | yes | no (copy) |
| Allows null elements? | depends on the backing list | never (throws NPE) |
| Created before Java 9? | yes (Java 1.2) | no (Java 9+) |

## Concurrent Collections

Plain `java.util` collections (`ArrayList`, `HashMap`, etc.) are **not** thread-safe. `java.util.concurrent` provides collections designed for multithreaded use, with better scalability than simply synchronizing everything.

### Why `Collections.synchronizedList` is not enough

`Collections.synchronizedXxx` wraps a collection so each **individual method call** is synchronized (guarded by a single lock). That protects against corruption from concurrent single calls, but it does **not** make compound actions (check-then-act, like "if absent, then add") atomic.

```java
List<String> list = Collections.synchronizedList(new ArrayList<>());

// BUG: this is two separate synchronized calls, not one atomic operation.
// Between contains() and add(), another thread could add the same item.
if (!list.contains("item")) {
    list.add("item"); // race condition: might now add a duplicate
}

// Correct: manually synchronize the whole compound action on the list's own lock
synchronized (list) {
    if (!list.contains("item")) {
        list.add("item");
    }
}

// Also: even a single iteration needs manual synchronization to avoid
// ConcurrentModificationException from another thread's concurrent write
synchronized (list) {
    for (String s : list) {
        System.out.println(s);
    }
}
```

This is exactly why `java.util.concurrent` collections exist: they provide atomic compound operations (like `putIfAbsent`) without you needing an external lock.

### ConcurrentHashMap

A highly-scalable map for concurrent access. Internally (JDK 8+) it does **not** lock the whole map; it uses fine-grained locking per bin (bucket) plus CAS (compare-and-swap) operations for many updates, so multiple threads can write to different parts of the map at the same time.

```java
ConcurrentHashMap<String, Integer> counts = new ConcurrentHashMap<>();
counts.putIfAbsent("errors", 0);
counts.compute("errors", (k, v) -> v + 1); // atomic read-modify-write
counts.merge("errors", 1, Integer::sum);   // also atomic

// Safe iteration while other threads mutate the map -- no ConcurrentModificationException,
// but the view may or may not reflect very recent concurrent updates ("weakly consistent")
for (String key : counts.keySet()) {
    System.out.println(key + " = " + counts.get(key));
}
```

Notes:
- Does **not** allow `null` keys or values (unlike `HashMap`) — this is deliberate, because `null` would be ambiguous in a concurrent `get` (is it "not present" or "present with null value"?).
- Iterators are **weakly consistent**: they never throw `ConcurrentModificationException`, and they reflect the state of the map at some point during or after the iterator was created, but not necessarily every single concurrent update.
- `size()` is an estimate under heavy concurrent modification, not a hard snapshot.

### CopyOnWriteArrayList

A `List` where every mutating operation (`add`, `remove`, `set`) makes a **fresh copy of the entire underlying array**. Reads never block and never see partial writes — they just read a stable snapshot array.

```java
CopyOnWriteArrayList<String> listeners = new CopyOnWriteArrayList<>();
listeners.add("listenerA");
listeners.add("listenerB");

// Safe to iterate even if another thread adds/removes concurrently --
// the iterator works on the snapshot array taken when the loop started,
// so no ConcurrentModificationException, ever.
for (String listener : listeners) {
    System.out.println("Notifying " + listener);
    listeners.remove(listener); // does NOT affect this ongoing iteration's snapshot
}
```

**When to use it**: read-heavy, write-rare scenarios, like a list of event listeners/observers. **Avoid** it for write-heavy workloads — every single write copies the whole array, which is O(n) per write and creates a lot of garbage.

### BlockingQueue family

`BlockingQueue<E>` extends `Queue` and adds methods that **block** the calling thread instead of failing, until space or an element becomes available. This is the standard building block for producer-consumer pipelines.

| Method | On empty (poll side) | On full (offer side) |
|---|---|---|
| `add`/`remove` | throws exception | throws exception |
| `offer`/`poll` | returns `null`/`false` immediately | returns `false` immediately |
| `put`/`take` | **blocks** until available | **blocks** until space |
| `offer(e, timeout)` / `poll(timeout)` | blocks up to a timeout, then gives up | blocks up to a timeout, then gives up |

```java
BlockingQueue<String> jobs = new java.util.concurrent.LinkedBlockingQueue<>(100); // bounded capacity

// Producer thread
new Thread(() -> {
    try {
        jobs.put("job-1"); // blocks if the queue is full
    } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
    }
}).start();

// Consumer thread
new Thread(() -> {
    try {
        String job = jobs.take(); // blocks until a job is available
        System.out.println("processing " + job);
    } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
    }
}).start();
```

Common implementations:
- **`ArrayBlockingQueue`**: fixed-capacity, array-backed, FIFO.
- **`LinkedBlockingQueue`**: optionally bounded, linked-node-backed, FIFO. Generally higher throughput than `ArrayBlockingQueue` under contention.
- **`PriorityBlockingQueue`**: unbounded, orders elements by priority like `PriorityQueue`, but with blocking `take()`.
- **`SynchronousQueue`**: zero capacity — a `put` must wait for a matching `take` (a direct handoff). Used inside some `ExecutorService` configurations.
- **`DelayQueue`**: elements become available only after their delay expires — useful for scheduling/retry logic.

### ConcurrentLinkedQueue / ConcurrentLinkedDeque

Unbounded, **non-blocking**, thread-safe queues/deques built with CAS (lock-free) operations instead of locks. They implement plain `Queue`/`Deque`, not `BlockingQueue` — there is no `put`/`take` that waits; `offer`/`poll` return immediately.

```java
ConcurrentLinkedQueue<String> queue = new ConcurrentLinkedQueue<>();
queue.offer("task1");
queue.offer("task2");
System.out.println(queue.poll()); // task1, thread-safe, no blocking, no external lock needed
```

**When to use it**: high-throughput producer-consumer scenarios where you don't need threads to block and wait — e.g., a metrics collector where producers just fire-and-forget and a consumer periodically drains what's there.

### Quick summary table

| Class | Blocking? | Bounded? | Typical use |
|---|---|---|---|
| `ConcurrentHashMap` | no | no | general-purpose concurrent map |
| `CopyOnWriteArrayList` | no | no | read-heavy list, e.g. listener lists |
| `ArrayBlockingQueue` | yes | yes (fixed) | producer-consumer with backpressure |
| `LinkedBlockingQueue` | yes | optional | producer-consumer, general purpose |
| `ConcurrentLinkedQueue` | no | no | lock-free, high-throughput, non-blocking |
| `SynchronousQueue` | yes | zero capacity | direct handoff between threads |

## Common Code-Review Interview Pitfalls

1. **Using `HashMap`/`HashSet` iteration order as if it were guaranteed.**
   Why it matters: order can change between JDK versions, or even between runs, and silently breaks logic or tests that depend on it.
   ```java
   // Before: relies on unspecified order
   Map<String, Integer> m = new HashMap<>();
   // ... assumes keys always print in insertion order

   // After: use LinkedHashMap if order must be predictable
   Map<String, Integer> m = new LinkedHashMap<>();
   ```

2. **Modifying a collection while iterating with a for-each loop.**
   Why it matters: fail-fast iterators detect structural changes and throw `ConcurrentModificationException` — this includes single-threaded code, not just concurrent access.
   ```java
   // Before: throws ConcurrentModificationException
   for (String s : list) {
       if (s.isEmpty()) list.remove(s);
   }

   // After: use the iterator's own remove, or removeIf
   list.removeIf(String::isEmpty);
   // or:
   Iterator<String> it = list.iterator();
   while (it.hasNext()) {
       if (it.next().isEmpty()) it.remove();
   }
   ```

3. **Storing mutable objects as `HashMap`/`HashSet` keys and then mutating them.**
   Why it matters: the bucket a key lives in is chosen from its hash at insertion time; if the key's hash changes afterward, the entry becomes unreachable ("lost" in the map).
   ```java
   // Before: mutable key breaks lookup
   List<Integer> key = new ArrayList<>(List.of(1, 2));
   Set<List<Integer>> set = new HashSet<>();
   set.add(key);
   key.add(3);              // hashCode changes!
   set.contains(key);       // often false -- looked up in the wrong bucket

   // After: use immutable keys (e.g., List.copyOf, records, Strings)
   List<Integer> key = List.of(1, 2);
   ```

4. **Assuming `List.remove(int)` and `List.remove(Object)` do the same thing with Integer arguments.**
   Why it matters: `list.remove(2)` on a `List<Integer>` calls the `int` overload (removes by index), not the `Object` overload (removes by value) — a classic autoboxing trap.
   ```java
   List<Integer> nums = new ArrayList<>(List.of(10, 20, 30));
   nums.remove(2);                  // removes INDEX 2 -> [10, 20]
   nums.remove(Integer.valueOf(20)); // removes VALUE 20 -> [10]
   ```

5. **Not overriding `equals()`/`hashCode()` on custom keys/elements.**
   Why it matters: without them, `HashMap`/`HashSet` fall back to identity comparison, so logically-equal objects are treated as distinct.
   ```java
   // Before: no equals()/hashCode() -> identity semantics
   class Point { int x, y; }

   // After: value semantics, works correctly as a map key / set element
   record Point(int x, int y) {}
   ```

6. **Choosing `LinkedList` as a default `List` implementation.**
   Why it matters: `get(index)` is O(n) on `LinkedList`, and it has higher memory overhead per element (node objects with two pointers each) than `ArrayList`'s flat array.
   ```java
   // Before: slow random access
   List<String> list = new LinkedList<>();

   // After: ArrayList is the right default unless you specifically need
   // O(1) insert/remove at both ends combined with List semantics
   List<String> list = new ArrayList<>();
   ```

7. **Using `Vector` or `Stack` in new code.**
   Why it matters: both are legacy, synchronized classes with unnecessary locking overhead for single-threaded use, and `Stack` extends `Vector` (an odd, dated design).
   ```java
   // Before: legacy, synchronized, slower
   Stack<Integer> stack = new Stack<>();

   // After: modern, faster, unsynchronized
   Deque<Integer> stack = new ArrayDeque<>();
   ```

8. **Passing an immutable collection (`List.of(...)`) somewhere it will be mutated later.**
   Why it matters: it throws `UnsupportedOperationException` at runtime, often far from where the immutable list was created, making the bug hard to trace.
   ```java
   // Before: crashes deep inside some other method
   List<String> config = List.of("a", "b");
   someLegacyCodeThatAddsDefaults(config); // throws UnsupportedOperationException

   // After: wrap in a mutable copy if the caller will mutate it
   List<String> config = new ArrayList<>(List.of("a", "b"));
   ```

9. **Assuming `Collections.synchronizedList`/`synchronizedMap` makes compound operations atomic.**
   Why it matters: each individual call is synchronized, but a sequence like check-then-add is still a race condition across threads.
   ```java
   // Before: race condition between contains() and add()
   if (!syncList.contains(x)) syncList.add(x);

   // After: synchronize the whole compound action, or use a concurrent collection
   synchronized (syncList) {
       if (!syncList.contains(x)) syncList.add(x);
   }
   ```

10. **Using `HashMap`/`HashSet` from multiple threads without synchronization.**
    Why it matters: concurrent structural modification can corrupt the internal bucket structure, causing silent data loss, infinite loops (older JDKs), or `ConcurrentModificationException`.
    ```java
    // Before: not thread-safe
    Map<String, Integer> counters = new HashMap<>();

    // After: use a collection designed for concurrency
    Map<String, Integer> counters = new ConcurrentHashMap<>();
    ```

11. **Putting `null` into a `ConcurrentHashMap` or a `TreeMap`/`TreeSet`.**
    Why it matters: `ConcurrentHashMap` rejects null keys/values outright (ambiguity with "absent"); `TreeMap`/`TreeSet` need to *compare* keys, and comparing against `null` throws `NullPointerException`.
    ```java
    // Before: throws NullPointerException at runtime
    new ConcurrentHashMap<String, String>().put("key", null);
    new TreeSet<String>().add(null);

    // After: use Optional, a sentinel value, or filter out nulls before inserting
    ```

12. **Forgetting that `PriorityQueue` iteration order is not sorted order.**
    Why it matters: only `peek()`/`poll()` guarantee the smallest element; iterating the queue (`for (var x : pq)`) or printing it shows heap-array order, which looks unsorted.
    ```java
    PriorityQueue<Integer> pq = new PriorityQueue<>(List.of(5, 1, 3));
    System.out.println(pq); // NOT guaranteed to print [1, 3, 5]

    // To get sorted output, drain with poll():
    List<Integer> sorted = new ArrayList<>();
    while (!pq.isEmpty()) sorted.add(pq.poll());
    ```

13. **Using `Arrays.asList()` and expecting a fully mutable, independent list.**
    Why it matters: it returns a fixed-size list backed by the array — `add`/`remove` throw `UnsupportedOperationException`, and `set` writes through to the original array.
    ```java
    // Before: surprising behavior
    Integer[] arr = {1, 2, 3};
    List<Integer> list = Arrays.asList(arr);
    list.add(4); // throws UnsupportedOperationException

    // After: wrap in a real, resizable, independent list
    List<Integer> list = new ArrayList<>(Arrays.asList(arr));
    ```

14. **Not pre-sizing collections when the final size is known.**
    Why it matters: repeated resizing (`ArrayList` array copies, `HashMap` rehashing) wastes CPU and creates avoidable garbage under load.
    ```java
    // Before: several resize/copy operations as the list grows
    List<String> results = new ArrayList<>();
    for (int i = 0; i < 100_000; i++) results.add(fetch(i));

    // After: allocate once
    List<String> results = new ArrayList<>(100_000);
    ```

15. **Assuming `TreeMap`/`TreeSet` ordering matches `equals()`.**
    Why it matters: `TreeSet`/`TreeMap` decide "duplicate" using the `Comparator`/`compareTo`, not `equals()`. Two elements that compare as `0` are treated as duplicates, even if `equals()` would say they differ.
    ```java
    // Before: silently drops "second" because comparator says they're "equal"
    TreeSet<String> set = new TreeSet<>(Comparator.comparingInt(String::length));
    set.add("cat");
    set.add("dog"); // same length as "cat" -> compareTo returns 0 -> treated as duplicate, ignored!
    System.out.println(set); // [cat]
    ```

16. **Choosing `CopyOnWriteArrayList` for a write-heavy workload.**
    Why it matters: every mutation copies the entire backing array — O(n) per write — which becomes a serious bottleneck as the list grows or writes become frequent.
    ```java
    // Before: O(n) per add(), terrible for a write-heavy queue-like list
    CopyOnWriteArrayList<String> events = new CopyOnWriteArrayList<>();
    for (int i = 0; i < 1_000_000; i++) events.add("event-" + i);

    // After: use a concurrent queue built for frequent writes
    ConcurrentLinkedQueue<String> events = new ConcurrentLinkedQueue<>();
    ```

17. **Relying on `Map.get()` returning `null` to mean "absent" when `null` values are also stored.**
    Why it matters: you can't tell "key missing" apart from "key present with a null value" using `get()` alone, which leads to subtle bugs.
    ```java
    // Before: ambiguous
    if (map.get("key") == null) { /* missing, or present-with-null? unclear */ }

    // After: use containsKey, or getOrDefault with a real sentinel, and avoid
    // storing null values in the first place when possible
    if (!map.containsKey("key")) { /* definitely missing */ }
    ```

18. **Using a plain `Iterator.remove()` loop when `removeIf` says exactly what you mean.**
    Why it matters: not a correctness bug, but a clarity/maintainability one that reviewers flag — `removeIf` is shorter, less error-prone (no risk of forgetting to call `it.remove()` and hitting `ConcurrentModificationException`), and communicates intent directly.
    ```java
    // Before: verbose, easy to get wrong
    Iterator<String> it = names.iterator();
    while (it.hasNext()) {
        if (it.next().isBlank()) it.remove();
    }

    // After: same result, clearer intent
    names.removeIf(String::isBlank);
    ```
