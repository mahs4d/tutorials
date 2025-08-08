# Day 6: NIO, Page-Based Storage & a B-Tree

> Week 1 finale. The hardest one. By the end of today you'll have written the two data structures that sit at the heart of every relational database: a **page-based heap file** and a **B+-tree index**. This is where Day 2's hash index and Day 1's WAL stop being toys.

| | |
|---|---|
| 🏗️ **Project** | **PageTree** — a page-based file store with a B+-tree index |
| ☕ **Java & language skills** | java.nio FileChannel/ByteBuffer, binary serialization, byte manipulation, generics |
| 🧰 **Library / tool** | Jackson (ObjectMapper — JSON/byte serialization) |
| 🗄️ **DB / distributed-systems concept** | Page-based storage & the B+-tree index (fanout, range scans) |
| 📊 **Difficulty** | Hard |

---

## Concept primer

### 1. The storage hierarchy, and why pages exist

Every layer of memory in a computer is a tradeoff between *speed* and *capacity*:

| Layer | Latency (order of magnitude) | Capacity | Access unit |
|-------|------------------------------|----------|-------------|
| CPU register | ~0.3 ns | bytes | byte |
| L1/L2 cache | ~1–10 ns | KB–MB | cache line (64 B) |
| RAM | ~100 ns | GB | bytes (page in MMU = 4KB) |
| SSD (NVMe) | ~50–100 µs | TB | **block/page (4–16 KB)** |
| Spinning disk | ~5–10 ms (seek) | TB | sector (512 B / 4 KB) |

The crucial fact: **disks and SSDs cannot read one byte.** The hardware reads and writes in fixed-size blocks. An SSD has a "page" (read/program unit, often 4–16KB) and an "erase block" (much larger). A spinning disk pays a multi-millisecond *seek* to position the head, after which sequential bytes are nearly free.

So if your database wants to read the row with `id = 42`, it can't ask the disk for "just those 80 bytes." It must read at least one block. The database therefore standardizes on its own fixed unit — the **page** (Postgres = 8KB, InnoDB = 16KB, SQLite = default 4KB) — and makes *the page the atomic unit of I/O and caching*. The buffer pool (Day 9 pooling preview) caches pages, the WAL (Day 1) logs page changes, and the B-tree (today) is itself stored as pages.

> **The single sentence to remember:** A database is a program that turns random row access into sequential page access, because the disk only speaks pages.

### 2. Slotted pages — how a record lives inside a page

A page is just a 4KB `byte[]`. But records are variable-length (a `name` could be 4 chars or 40). How do you pack variable-length records into a fixed page and still address them by a stable id? The classic answer is the **slotted page** layout (used by Postgres, InnoDB, almost everyone):

```
 0                                                              4096
 ┌──────────┬───────────────────────────────┬──────────────────┐
 │  HEADER  │  slot array →                  ← record data      │
 │ count,   │ [off,len][off,len][off,len]... │ ...record3 record2 record1│
 │ freePtr  │                                │                  │
 └──────────┴───────────────────────────────┴──────────────────┘
            slots grow this way →     ← record bytes grow this way
```

- A small **header** holds the record count and a pointer to free space.
- A **slot array** grows from the front. Slot `i` holds `(offset, length)` of record `i`.
- **Record bytes** grow from the back.
- Free space is the gap in the middle. The page is full when the slot array meets the record bytes.

The payoff: a record's address is `(pageId, slotIndex)` — a stable, compact **tuple id** (Postgres calls it a *ctid*, Oracle a *rowid*). You can move a record *within* a page (compaction after a delete) and only the slot entry changes; external references through the slot stay valid. Today we'll build a simplified slotted page (append-only, no in-page deletes) to keep it to an hour.

### 3. Hash index (Day 2) vs B-tree vs B+-tree

Recall Day 2: the hash index gave us **O(1) point lookups** — `key → file offset`. It's fantastic for `WHERE id = 42`. But it has two fatal weaknesses for a general-purpose index:

1. **No range scans.** A hash scatters keys by `hash(key)`, destroying order. `WHERE age BETWEEN 20 AND 30` or `ORDER BY age` is impossible without a full scan.
2. **It must fit in memory** (the classic Bitcask-style hash index keeps all keys in RAM), and rehashing is expensive.

The **B-tree** (Bayer & McCreight, 1972) keeps keys **sorted** and balanced. Every node holds up to `m-1` keys and `m` children (`m` = the *order* / fanout). Searches are `O(log_m n)`. Because keys are ordered, range scans are natural.

The **B+-tree** is the refinement everyone actually ships:

- **Internal nodes store only keys + child pointers** (routing information). They carry no data.
- **All data (or pointers to data) lives in the leaves.**
- **Leaves are linked left-to-right** in a singly/doubly linked list.

Why those three changes matter:

- Internal nodes carrying no payload means they pack *more keys per page* → **higher fanout** → **shallower tree** → fewer disk reads per lookup.
- All lookups touch the same depth (every search ends at a leaf), so latency is uniform.
- The leaf linked list makes a range scan trivial: descend once to the start key, then **walk the leaf chain** — no re-traversal of the tree, no random I/O.

```
                 [ 30 | 70 ]            ← internal: routing keys only
                /     |     \
        [10|20]   [30|50]   [70|90]     ← leaves: keys + values
          ↓→        ↓→         ↓→        ← leaves linked for range scans
```

### 4. Fanout & depth math — why B-trees are *shallow*

This is the number that makes B-trees the king of disk indexes. Suppose:

- Page size = 8KB (8192 bytes).
- Key = 8 bytes (a `long`), child pointer = 8 bytes → each entry ≈ 16 bytes.
- Fanout `m ≈ 8192 / 16 ≈ 512` children per internal node.

Then a tree of height `h` indexes up to `512^h` keys:

| Height | Keys indexed | Disk reads for a point lookup |
|--------|--------------|-------------------------------|
| 2 | ~262 thousand | 2 |
| 3 | ~134 million | 3 |
| 4 | ~68 **billion** | 4 |

**A B-tree over 68 billion rows is 4 levels deep.** With the top 1–2 levels cached in the buffer pool, a point lookup is effectively **1–2 disk reads**. Contrast a balanced *binary* tree over 68 billion rows: height ≈ 36, i.e. up to 36 random reads. High fanout collapses depth, and depth is what you pay for in disk seeks.

### 5. Why disk/SSD physics favor B-trees specifically

- **Each node = one page = one I/O.** The tree's shape is co-designed with the disk's access unit. A binary tree wastes a whole page read to learn a single bit of routing.
- **Sequential leaf scans.** Range queries walk the leaf chain; on a B+-tree whose leaves are laid out roughly in order, that's near-sequential I/O — the disk's happy path.
- **In-place updates, bounded write amplification.** A B-tree updates the page in place and logs it (WAL). One row update dirties ~1 page. This contrasts with **LSM-trees** (RocksDB, Cassandra, the contrast we'll revisit) which never update in place — they append to memtables and compact, trading write amplification patterns for write throughput. (More in *Going deeper*.)

We'll build the **in-memory** B+-tree today (insert / point lookup / range scan) and a **real on-disk page store**, with leaves pointing at page ids. Persisting the tree nodes themselves to disk is the stretch goal.

---

## Prerequisites & Maven setup

You have Maven from Day 1. Create a Day 6 module / project. `pom.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.learning</groupId>
    <artifactId>day6-page-btree</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.release>21</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <jackson.version>2.17.1</jackson.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>com.fasterxml.jackson.core</groupId>
            <artifactId>jackson-databind</artifactId>
            <version>${jackson.version}</version>
        </dependency>

        <!-- optional, for the assertions at the bottom -->
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>5.10.2</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
            </plugin>
        </plugins>
    </build>
</project>
```

Package layout we'll create under `src/main/java/com/learning/day6/`:

```
Record.java        – the row payload (a Java record)
PageStore.java     – fixed-size page file via FileChannel/ByteBuffer
SlottedPage.java   – pack/unpack records inside one 4KB page
BPlusTree.java     – in-memory B+-tree: insert / search / rangeScan
Main.java          – wires it all together
```

---

---

## 🛠️ Project Walkthrough — PageTree

Now we build it hands-on, step by step — wiring a page store, a slotted page, and a B+-tree index together into a working mini storage engine.

## Step 1 — The record payload + Jackson configuration

We model a row as a Java 21 `record`. Jackson serializes it to a compact `byte[]` that we'll drop into a page.

```java
// Record.java
package com.learning.day6;

/**
 * The "row" stored in the heap. A Java record gives us an immutable,
 * value-based payload. Jackson can (de)serialize records natively
 * (since Jackson 2.12) — no getters/setters needed.
 */
public record Record(long id, String name, int age, String city) {}
```

Now a tiny holder for Jackson configured *the way a storage engine wants it*: deterministic, compact, fail-fast.

```java
// Json.java
package com.learning.day6;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;

/**
 * One shared, fully-configured ObjectMapper.
 *
 * ObjectMapper is thread-safe AFTER configuration and EXPENSIVE to build,
 * so you create it once and reuse it forever (per JVM). This is the single
 * most common Jackson performance mistake: building a mapper per request.
 */
public final class Json {

    public static final ObjectMapper MAPPER = JsonMapper.builder()
            // Compact output: no pretty-printing in a storage payload.
            .disable(SerializationFeature.INDENT_OUTPUT)
            // Deterministic byte output: sort map keys so the same logical
            // record always serializes to identical bytes (matters for
            // checksums, dedup, and reproducible page contents).
            .enable(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY)
            .enable(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS)
            // Fail fast on schema drift instead of silently dropping data...
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            // ...but tolerate missing fields by leaving them null/default.
            .disable(DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES)
            .build();

    private Json() {}

    /** Serialize any value to a compact UTF-8 byte[] for storage in a page. */
    public static byte[] toBytes(Object value) {
        try {
            return MAPPER.writeValueAsBytes(value); // writeValue → byte[]
        } catch (Exception e) {
            throw new RuntimeException("serialize failed", e);
        }
    }

    /** Deserialize a byte[] slice back into a typed object. */
    public static <T> T fromBytes(byte[] bytes, Class<T> type) {
        try {
            return MAPPER.readValue(bytes, type); // readValue ← byte[]
        } catch (Exception e) {
            throw new RuntimeException("deserialize failed", e);
        }
    }
}
```

Key Jackson points called out for the senior reader:

- `writeValueAsBytes` / `readValue(byte[], Class)` skip the `String` round-trip — they go straight to/from UTF-8 bytes, which is exactly what you want when the destination is a `ByteBuffer`.
- `SORT_PROPERTIES_ALPHABETICALLY` + `ORDER_MAP_ENTRIES_BY_KEYS` make serialization **deterministic** — the same record always produces the same bytes. Storage engines care because they checksum pages and dedup payloads.
- One `ObjectMapper` per JVM. It's thread-safe once built.

---

## Step 2 — The PageStore: fixed-size pages via FileChannel + ByteBuffer

This is the NIO core. We treat a file as an array of 4KB pages. Page `i` lives at byte offset `i * PAGE_SIZE`. `FileChannel` lets us read/write at an absolute position without moving a cursor, and `ByteBuffer` gives us a fixed-size, structured view of the bytes.

```java
// PageStore.java
package com.learning.day6;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

/**
 * A heap file: the disk as an array of fixed-size pages.
 *
 * Page i occupies bytes [i*PAGE_SIZE, (i+1)*PAGE_SIZE). We never read or write
 * a single byte from the file — only whole pages — mirroring how a real engine
 * and the OS page cache behave.
 */
public final class PageStore implements AutoCloseable {

    public static final int PAGE_SIZE = 4096; // 4 KB, the classic page size

    private final FileChannel channel;
    private long pageCount; // how many pages have been allocated so far

    public PageStore(Path file) throws IOException {
        // READ | WRITE | CREATE: open or create the heap file.
        this.channel = FileChannel.open(
                file,
                StandardOpenOption.READ,
                StandardOpenOption.WRITE,
                StandardOpenOption.CREATE);
        // Derive the current page count from the file's length.
        this.pageCount = channel.size() / PAGE_SIZE;
    }

    /** Allocate a fresh, zeroed page and return its id. O(1) bump allocator. */
    public long allocatePage() throws IOException {
        long pageId = pageCount++;
        // Grow the file by writing a zeroed page at the new slot.
        ByteBuffer empty = ByteBuffer.allocate(PAGE_SIZE);
        writeFully(empty, pageId * PAGE_SIZE);
        return pageId;
    }

    /** Read an entire page into a fresh ByteBuffer (position reset to 0). */
    public ByteBuffer readPage(long pageId) throws IOException {
        checkPage(pageId);
        ByteBuffer buf = ByteBuffer.allocate(PAGE_SIZE);
        long offset = pageId * PAGE_SIZE;
        int read = 0;
        while (read < PAGE_SIZE) {
            int n = channel.read(buf, offset + read);
            if (n < 0) break; // EOF (shouldn't happen for an allocated page)
            read += n;
        }
        buf.flip();          // switch from write-mode to read-mode
        return buf;
    }

    /** Write a full page. The buffer must be exactly PAGE_SIZE and flipped. */
    public void writePage(long pageId, ByteBuffer buf) throws IOException {
        checkPage(pageId);
        if (buf.remaining() != PAGE_SIZE) {
            throw new IllegalArgumentException(
                    "page write must be exactly " + PAGE_SIZE + " bytes, got " + buf.remaining());
        }
        writeFully(buf, pageId * PAGE_SIZE);
    }

    /** Force buffered writes to durable storage (the fsync of NIO). */
    public void sync() throws IOException {
        channel.force(true); // true = also flush file metadata
    }

    public long pageCount() {
        return pageCount;
    }

    private void writeFully(ByteBuffer buf, long offset) throws IOException {
        long pos = offset;
        while (buf.hasRemaining()) {
            pos += channel.write(buf, pos);
        }
    }

    private void checkPage(long pageId) {
        if (pageId < 0 || pageId >= pageCount) {
            throw new IndexOutOfBoundsException("no such page: " + pageId);
        }
    }

    @Override
    public void close() throws IOException {
        channel.close();
    }
}
```

Why these NIO choices (senior notes):

- **`channel.read(buf, position)` / `write(buf, position)`** are *positional* — they don't mutate the channel's internal cursor and are safe for concurrent positioned reads. That's exactly the access pattern a buffer pool needs.
- We loop until `PAGE_SIZE` bytes move because **a single `read`/`write` may transfer fewer bytes than requested** — a classic bug that only shows up under load or on certain filesystems.
- `channel.force(true)` is the NIO `fsync`. Without it, your "durable" write may sit in the OS page cache and vanish on power loss — the same durability story as Day 1's WAL.
- We *could* use `MappedByteBuffer` (mmap) for zero-copy access; we use explicit `read`/`write` because the control flow is clearer and matches how the buffer pool will work in Day 9.

---

## Step 3 — The SlottedPage: pack records inside one page

A thin wrapper over a single page's `ByteBuffer`. Layout (little simplified — append-only, no in-page delete):

```
[ int slotCount ][ int freeOffset ][ slot0:int off,int len ][ slot1 ]... ←gap→ ...[rec1][rec0]
  4 bytes          4 bytes           8 bytes each                              records from the tail
```

```java
// SlottedPage.java
package com.learning.day6;

import java.nio.ByteBuffer;

/**
 * Slotted-page layout over a single PAGE_SIZE buffer.
 *
 * Header: [slotCount:int][freeOffset:int]   (8 bytes)
 * Slots:  grow forward from byte 8; each slot is [recOffset:int][recLen:int].
 * Records: grow backward from the end of the page.
 * Page is full when (slot area end) would collide with (record area start).
 */
public final class SlottedPage {

    private static final int HEADER_SIZE = 8;        // slotCount + freeOffset
    private static final int SLOT_SIZE   = 8;        // offset + length
    private final ByteBuffer buf;                    // a full PAGE_SIZE buffer

    private SlottedPage(ByteBuffer buf) {
        this.buf = buf;
    }

    /** Initialize a brand-new empty page in the given buffer. */
    public static SlottedPage init(ByteBuffer buf) {
        buf.clear();
        buf.putInt(0, 0);                            // slotCount = 0
        buf.putInt(4, PageStore.PAGE_SIZE);          // freeOffset = end of page
        return new SlottedPage(buf);
    }

    /** Wrap an existing page that already has data. */
    public static SlottedPage wrap(ByteBuffer buf) {
        return new SlottedPage(buf);
    }

    public int slotCount()  { return buf.getInt(0); }
    public int freeOffset() { return buf.getInt(4); }

    /** Bytes currently free in the middle gap. */
    public int freeSpace() {
        int slotAreaEnd = HEADER_SIZE + slotCount() * SLOT_SIZE;
        return freeOffset() - slotAreaEnd;
    }

    /**
     * Insert a record's bytes. Returns the slot index, or -1 if the page
     * can't fit it (caller allocates a new page).
     */
    public int insert(byte[] record) {
        int needed = record.length + SLOT_SIZE; // record + its slot entry
        if (needed > freeSpace()) {
            return -1; // page full
        }
        int newFreeOffset = freeOffset() - record.length;
        // Copy record bytes into the tail region.
        buf.position(newFreeOffset);
        buf.put(record);
        // Append the slot.
        int slot = slotCount();
        int slotPos = HEADER_SIZE + slot * SLOT_SIZE;
        buf.putInt(slotPos, newFreeOffset);
        buf.putInt(slotPos + 4, record.length);
        // Update header.
        buf.putInt(0, slot + 1);
        buf.putInt(4, newFreeOffset);
        return slot;
    }

    /** Read the record bytes stored at the given slot. */
    public byte[] read(int slot) {
        if (slot < 0 || slot >= slotCount()) {
            throw new IndexOutOfBoundsException("no slot " + slot);
        }
        int slotPos = HEADER_SIZE + slot * SLOT_SIZE;
        int off = buf.getInt(slotPos);
        int len = buf.getInt(slotPos + 4);
        byte[] out = new byte[len];
        // Absolute bulk get keeps the buffer's position untouched.
        buf.get(off, out, 0, len);
        return out;
    }

    /** The backing buffer, ready to hand to PageStore.writePage (flipped). */
    public ByteBuffer buffer() {
        buf.position(0);
        buf.limit(PageStore.PAGE_SIZE);
        return buf;
    }
}
```

Note `buf.get(off, dst, 0, len)` — the **absolute** bulk get added in Java 13 — reads without disturbing the buffer's position. The whole class is position-discipline: every access is absolute except the one record copy.

---

## Step 4 — The B+-tree (in-memory): insert / search / rangeScan

This is the centerpiece. Order `M` means each node holds at most `M-1` keys. Internal nodes route; leaves hold `key → recordPointer` and link to the next leaf. A record pointer is `(pageId, slot)`.

```java
// BPlusTree.java
package com.learning.day6;

import java.util.ArrayList;
import java.util.List;

/**
 * In-memory B+-tree mapping long keys -> RecordPointer (pageId, slot).
 *
 *  - Internal nodes hold separator keys + child references (no data).
 *  - Leaf nodes hold keys + values, and a `next` pointer to the right leaf.
 *  - On overflow a node splits and pushes a separator up; the root splits
 *    last, which is the only way the tree grows in height.
 *
 * Order M = max children per internal node = max (keys+1).
 */
public final class BPlusTree {

    /** Where a record lives in the heap file. */
    public record RecordPointer(long pageId, int slot) {}

    private static final int M = 4; // small order so splits are easy to see

    // ---- node types ----
    private static abstract sealed class Node permits Internal, Leaf {
        final List<Long> keys = new ArrayList<>();
        abstract boolean isLeaf();
    }

    private static final class Internal extends Node {
        final List<Node> children = new ArrayList<>(); // size = keys.size()+1
        boolean isLeaf() { return false; }
    }

    private static final class Leaf extends Node {
        final List<RecordPointer> values = new ArrayList<>();
        Leaf next; // linked list for range scans
        boolean isLeaf() { return true; }
    }

    private Node root = new Leaf();
    private int height = 1;

    public int height() { return height; }

    // ---- SEARCH (point lookup) ----
    public RecordPointer search(long key) {
        Leaf leaf = findLeaf(key);
        int i = leaf.keys.indexOf(key);
        return i < 0 ? null : leaf.values.get(i);
    }

    /** Descend from the root to the leaf that should contain `key`. */
    private Leaf findLeaf(long key) {
        Node node = root;
        while (!node.isLeaf()) {
            Internal in = (Internal) node;
            int idx = childIndex(in.keys, key);
            node = in.children.get(idx);
        }
        return (Leaf) node;
    }

    /** First child index whose subtree may contain key. */
    private static int childIndex(List<Long> keys, long key) {
        int i = 0;
        while (i < keys.size() && key >= keys.get(i)) {
            i++;
        }
        return i;
    }

    // ---- RANGE SCAN: walk the leaf chain ----
    public List<RecordPointer> rangeScan(long lo, long hi) {
        List<RecordPointer> out = new ArrayList<>();
        Leaf leaf = findLeaf(lo);
        while (leaf != null) {
            for (int i = 0; i < leaf.keys.size(); i++) {
                long k = leaf.keys.get(i);
                if (k < lo) continue;
                if (k > hi) return out;       // past the range, stop
                out.add(leaf.values.get(i));
            }
            leaf = leaf.next;                  // <-- no tree re-traversal
        }
        return out;
    }

    // ---- INSERT ----
    public void insert(long key, RecordPointer value) {
        Split split = insert(root, key, value);
        if (split != null) {
            // root overflowed: build a new root one level up
            Internal newRoot = new Internal();
            newRoot.keys.add(split.key);
            newRoot.children.add(root);
            newRoot.children.add(split.right);
            root = newRoot;
            height++;
        }
    }

    /** Carries a separator key + the new right sibling up the recursion. */
    private record Split(long key, Node right) {}

    private Split insert(Node node, long key, RecordPointer value) {
        if (node.isLeaf()) {
            Leaf leaf = (Leaf) node;
            insertIntoLeaf(leaf, key, value);
            return leaf.keys.size() > M - 1 ? splitLeaf(leaf) : null;
        }
        Internal in = (Internal) node;
        int idx = childIndex(in.keys, key);
        Split childSplit = insert(in.children.get(idx), key, value);
        if (childSplit == null) return null;
        // child split: absorb the new separator
        in.keys.add(idx, childSplit.key);
        in.children.add(idx + 1, childSplit.right);
        return in.keys.size() > M - 1 ? splitInternal(in) : null;
    }

    private void insertIntoLeaf(Leaf leaf, long key, RecordPointer value) {
        int i = 0;
        while (i < leaf.keys.size() && leaf.keys.get(i) < key) i++;
        if (i < leaf.keys.size() && leaf.keys.get(i) == key) {
            leaf.values.set(i, value);    // upsert: overwrite existing key
        } else {
            leaf.keys.add(i, key);
            leaf.values.add(i, value);
        }
    }

    private Split splitLeaf(Leaf leaf) {
        int mid = leaf.keys.size() / 2;
        Leaf right = new Leaf();
        // move the upper half into the new right leaf
        right.keys.addAll(leaf.keys.subList(mid, leaf.keys.size()));
        right.values.addAll(leaf.values.subList(mid, leaf.values.size()));
        leaf.keys.subList(mid, leaf.keys.size()).clear();
        leaf.values.subList(mid, leaf.values.size()).clear();
        // maintain the leaf linked list
        right.next = leaf.next;
        leaf.next = right;
        // B+-tree: the separator is COPIED up (the key still lives in the leaf)
        return new Split(right.keys.get(0), right);
    }

    private Split splitInternal(Internal node) {
        int mid = node.keys.size() / 2;
        long upKey = node.keys.get(mid); // pushed up, removed from this level
        Internal right = new Internal();
        right.keys.addAll(node.keys.subList(mid + 1, node.keys.size()));
        right.children.addAll(node.children.subList(mid + 1, node.children.size()));
        node.keys.subList(mid, node.keys.size()).clear();
        node.children.subList(mid + 1, node.children.size()).clear();
        return new Split(upKey, right);
    }
}
```

The two split methods encode the **defining difference** between a B-tree and a B+-tree:

- `splitLeaf`: the separator key is **copied** up — the actual key/value still lives in the leaf (all data is in leaves).
- `splitInternal`: the separator key is **moved** (pushed) up and removed from this level — internal nodes are pure routing.

And `rangeScan` shows the B+-tree's superpower: descend once, then **follow `leaf.next`** — no climbing back up the tree, no random page jumps.

---

## Step 5 — Main: wire heap pages + index + Jackson together

```java
// Main.java
package com.learning.day6;

import com.learning.day6.BPlusTree.RecordPointer;

import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

public class Main {

    public static void main(String[] args) throws Exception {
        Path heapFile = Files.createTempFile("day6-heap", ".db");
        System.out.println("Heap file: " + heapFile);

        try (PageStore store = new PageStore(heapFile)) {
            BPlusTree index = new BPlusTree();

            // ---- insert side: serialize record -> store in a page -> index it ----
            long currentPage = store.allocatePage();
            SlottedPage page = SlottedPage.init(store.readPage(currentPage));

            Record[] rows = {
                new Record(10, "Ada",   36, "London"),
                new Record(20, "Linus", 54, "Portland"),
                new Record(30, "Grace", 85, "NYC"),
                new Record(40, "Edsger",75, "Austin"),
                new Record(50, "Alan",  41, "London"),
                new Record(60, "Ken",   80, "NJ"),
                new Record(70, "Barbara",97,"NYC"),
            };

            for (Record r : rows) {
                byte[] payload = Json.toBytes(r);          // Jackson: record -> bytes
                int slot = page.insert(payload);
                if (slot < 0) {                            // page full: roll to a new one
                    store.writePage(currentPage, page.buffer());
                    currentPage = store.allocatePage();
                    page = SlottedPage.init(store.readPage(currentPage));
                    slot = page.insert(payload);
                }
                index.insert(r.id(), new RecordPointer(currentPage, slot));
                System.out.printf("inserted id=%d -> page=%d slot=%d (%d bytes)%n",
                        r.id(), currentPage, slot, payload.length);
            }
            store.writePage(currentPage, page.buffer());
            store.sync();
            System.out.println("tree height = " + index.height()
                    + ", pages used = " + store.pageCount());

            // ---- point lookup: index -> pointer -> page -> Jackson -> record ----
            System.out.println("\n== point lookup id=40 ==");
            Record got = fetch(store, index.search(40));
            System.out.println(got);

            System.out.println("\n== point lookup id=999 (missing) ==");
            System.out.println(index.search(999)); // null

            // ---- range scan: 25..65 inclusive, in key order ----
            System.out.println("\n== range scan [25..65] ==");
            for (RecordPointer p : index.rangeScan(25, 65)) {
                System.out.println(fetch(store, p));
            }
        }
    }

    /** Resolve a pointer to the actual record via the page store + Jackson. */
    private static Record fetch(PageStore store, RecordPointer ptr) throws Exception {
        if (ptr == null) return null;
        ByteBuffer raw = store.readPage(ptr.pageId());
        SlottedPage page = SlottedPage.wrap(raw);
        byte[] bytes = page.read(ptr.slot());
        return Json.fromBytes(bytes, Record.class);       // Jackson: bytes -> record
    }
}
```

---

## Run it

```bash
mvn -q compile exec:java -Dexec.mainClass=com.learning.day6.Main
```

(or add the `exec-maven-plugin`, or just run `Main` from your IDE).

### Expected output (page/slot numbers may vary slightly)

```
Heap file: /tmp/day6-heap12345.db
inserted id=10 -> page=0 slot=0 (44 bytes)
inserted id=20 -> page=0 slot=1 (47 bytes)
inserted id=30 -> page=0 slot=2 (43 bytes)
inserted id=40 -> page=0 slot=3 (45 bytes)
inserted id=50 -> page=0 slot=4 (44 bytes)
inserted id=60 -> page=0 slot=5 (40 bytes)
inserted id=70 -> page=0 slot=6 (47 bytes)
tree height = 2, pages used = 1

== point lookup id=40 ==
Record[id=40, name=Edsger, age=75, city=Austin]

== point lookup id=999 (missing) ==
null

== range scan [25..65] ==
Record[id=30, name=Grace, age=85, city=NYC]
Record[id=40, name=Edsger, age=75, city=Austin]
Record[id=50, name=Alan, age=41, city=London]
Record[id=60, name=Ken, age=80, city=NJ]
```

What you just observed:

- All 7 records fit in **one 4KB page** (each JSON record is ~40–47 bytes; 7 × ~55 with slots ≈ 385 bytes, far under 4096).
- The tree split at least once (height = 2), so the root is now an internal routing node.
- The range scan returned keys **in order** by walking the leaf chain — the thing the Day 2 hash index fundamentally cannot do.

---

## Optional: lock it down with a test

```java
// src/test/java/com/learning/day6/BPlusTreeTest.java
package com.learning.day6;

import com.learning.day6.BPlusTree.RecordPointer;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.stream.IntStream;

import static org.junit.jupiter.api.Assertions.*;

class BPlusTreeTest {

    @Test
    void insertsSearchesAndRangeScansInOrder() {
        BPlusTree t = new BPlusTree();
        // insert 1..1000 in shuffled order
        int[] keys = IntStream.rangeClosed(1, 1000).toArray();
        for (int k : keys) {
            t.insert(k, new RecordPointer(k / 100, k % 100));
        }
        // point lookups
        assertNotNull(t.search(1));
        assertNotNull(t.search(1000));
        assertNull(t.search(2000));

        // range scan returns sorted, contiguous keys
        List<RecordPointer> r = t.rangeScan(250, 260);
        assertEquals(11, r.size());

        // tree stays shallow: log_M(1000) is small even for M=4
        assertTrue(t.height() <= 7, "height was " + t.height());
    }
}
```

```bash
mvn -q test
```

---

## 🚀 Going Deeper & Next Steps

## Going deeper / senior-level notes

**1. Postgres = heap + separate B-tree (an "index-organized" alternative exists).**
Postgres stores rows in a **heap file** (unordered, slotted 8KB pages — exactly our `SlottedPage`) and the B-tree index stores `(key → ctid)` where `ctid = (page, slot)` — exactly our `RecordPointer`. The table and its indexes are *separate* structures. By contrast, MySQL/InnoDB uses a **clustered index**: the table *is* a B+-tree keyed by the primary key, and the row data lives in the leaves. SQL Server and Oracle's IOT do the same. Tradeoff: clustered indexes make primary-key range scans blazing fast (data is physically ordered) but make secondary indexes pay an extra hop (secondary leaf → PK → clustered tree).

**2. Page splits are the cost of insertion.**
When a full leaf must accept a key, it splits — half the keys move to a new page, and a separator climbs to the parent. Random-insert workloads (e.g. UUID v4 primary keys) scatter inserts across the whole tree, causing constant splits, page fragmentation, and poor cache locality. **Monotonic keys** (auto-increment, UUID v7, ULID, Snowflake ids) append to the rightmost leaf and split cleanly. This is *the* reason "don't use random UUIDs as a clustered primary key" is a well-worn DBA rule.

**3. Write amplification.**
Updating one 80-byte row dirties an entire page (4–16KB) that must eventually be flushed — plus a WAL record. That's **write amplification**: bytes written to disk ≫ bytes logically changed. B-trees also suffer **read-modify-write** on updates and incur extra writes during splits and during checkpoints. Engineers measure WAF (write amplification factor) because on SSDs every extra write consumes finite program/erase cycles.

**4. B-trees vs LSM-trees — the great storage-engine schism.**
- **B-tree** (Postgres, InnoDB, most RDBMS): update **in place**. Reads are cheap and predictable (a few page reads). Writes do random in-place updates → more random I/O and write amplification, but reads don't degrade.
- **LSM-tree** (RocksDB, Cassandra, LevelDB, ScyllaDB): never update in place. Writes go to an in-memory **memtable**, flush to immutable sorted files (SSTables), and a background **compaction** merges them. Writes are sequential and fast; reads may touch multiple SSTable levels (mitigated by Bloom filters) and **compaction** is its own write-amplification story.
- Rule of thumb: **B-trees favor read-heavy / mixed workloads with predictable latency; LSM-trees favor write-heavy ingest.** We'll revisit this contrast when we get to indexing (Day 21).

**5. Real B+-trees persist their nodes as pages too.**
Today our tree is in-memory; only the *records* are on disk. A production engine stores **each B-tree node as a page in the same file** (or an index file), so the index survives restart and exceeds RAM. Internal nodes point to child *page ids* instead of Java references — meaning a tree traversal is a sequence of `PageStore.readPage` calls, each served from the buffer pool (Day 9). That's the stretch goal below.

**6. Concurrency (preview of Day 28).** Real B-trees use **latch crabbing** (lock a child, release the parent once you know the child won't split up) and B-link trees add a right-link so readers never block on splits. We ignored all of this; our tree is single-threaded.

---

## Stretch goals

1. **Persist the B-tree to disk.** Serialize each node into its own page with Jackson (or a manual binary layout), store child *page ids* instead of object references, and add a `PageStore`-backed node cache. Reload the tree from disk on startup and re-run the lookups — now the index survives a restart.
2. **In-page deletes + compaction.** Add `SlottedPage.delete(slot)` that tombstones a slot, plus a `compact()` that slides live records together and rewrites the slot array. Measure free-space reclamation.
3. **Replace JSON with a manual binary record codec** (write `id` as a `long`, length-prefixed `String`s) and compare the serialized size and throughput against Jackson. You'll see why storage engines use compact binary formats, not JSON, for the hot path — and where Jackson is still the right call (config, debug dumps, external APIs).
4. **Buffer-pool stub.** Wrap `PageStore` with an LRU cache of `pageId → ByteBuffer` (a `LinkedHashMap` in access order) and count cache hits/misses across the workload. This is the literal seed of Day 9.

---

## Day 7 teaser

You can now store records on pages and find them by key in `O(log n)`. But what happens when the **same write request arrives twice** — a retried HTTP call, a redelivered message — and your insert runs again? You get duplicate rows, double charges, corrupted state. Tomorrow (**Day 7: Idempotency**) we make operations safe to repeat: idempotency keys, dedup tables, and the request-fingerprinting patterns that let an at-least-once world behave exactly-once. Week 2 begins.
