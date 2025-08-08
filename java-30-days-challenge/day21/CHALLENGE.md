# Day 21: Indexing & Query Optimization

| | |
|---|---|
| 🏗️ **Project** | **IndexLab** — a Postgres indexing & EXPLAIN-tuning lab |
| ☕ **Java & language skills** | Connecting to Postgres from Java/JPA, running SQL, reading query plans |
| 🧰 **Library / tool** | PostgreSQL + EXPLAIN / EXPLAIN ANALYZE |
| 🗄️ **DB / distributed-systems concept** | Indexing & the query planner (B-tree, composite/covering indexes, selectivity) |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. B-tree recap — Day 6 meets a real engine

On **Day 6** you built a B-tree by hand: fixed-size **pages**, a high **fanout** (many keys per node so the tree stays shallow), and `O(log n)` lookups where the log base is the fanout, not 2. PostgreSQL's default index is exactly that structure — a **B+tree** (specifically the Lehman-Yao variant for concurrency). Every node is an **8 KB page**. With a fanout of a few hundred entries per page, even a billion-row table is ~4-5 levels deep, so a point lookup is ~4-5 page reads instead of scanning millions of rows.

The key mental model: a heap table is an **unordered pile of pages** (Day 1's append-only WAL/heap intuition). An index is a *separate* ordered structure whose leaf entries hold the indexed key plus a **TID** (`(page, offset)` tuple identifier) pointing back into the heap. To answer a query the engine walks the index to find matching TIDs, then visits the heap to fetch the full rows — unless the index already contains everything the query needs (covering / index-only scan, below).

Other index types you should *know exist* (B-tree is the default and 90% case):

| Type   | Good for                                   | Why |
|--------|--------------------------------------------|-----|
| B-tree | equality + range (`=`, `<`, `>`, `BETWEEN`, `ORDER BY`, prefix `LIKE 'foo%'`) | ordered, balanced, high fanout |
| Hash   | equality only (`=`)                        | `O(1)` bucket lookup, no range/sort; rarely worth it over B-tree (this is Day 2's hash index inside a DB) |
| GIN    | "many values per row": arrays, `jsonb`, full-text | inverted index — maps each element to the rows containing it |
| GiST   | geometry, ranges, nearest-neighbour        | generalized tree for non-linear orderings |
| BRIN   | huge, naturally-ordered tables (time-series, append-only) | stores min/max per *block range* — tiny index, great when data correlates with physical order |

### 2. The planner / optimizer

SQL is **declarative** — you say *what*, not *how*. The **planner** enumerates candidate physical plans (seq scan, index scan, bitmap scan, nested-loop vs hash vs merge join, etc.), estimates a **cost** for each (an abstract number combining estimated page reads and CPU work), and picks the cheapest. It does **not** measure your query — it predicts using **statistics** gathered by `ANALYZE`: per-column cardinality (`n_distinct`), most-common-values, histograms, and physical/logical correlation. Bad statistics → bad estimates → bad plans. This is why "I added an index but it's still slow" is usually a *statistics* problem, not an *index* problem.

### 3. Selectivity & cardinality — the whole game

**Selectivity** = the fraction of rows a predicate keeps. `WHERE status = 'PENDING'` on a column with 4 distinct values evenly spread is ~25% selective — terrible for an index, because reading 250k random heap pages via an index is *slower* than streaming all 1M sequentially. `WHERE email = 'x@y.com'` on a unique column is ~0.0001% selective — perfect for an index. The rule of thumb the planner internalizes: **an index wins only when the predicate is selective enough that random index+heap I/O beats a sequential scan.** PostgreSQL's `random_page_cost` (default 4.0) vs `seq_page_cost` (1.0) encodes exactly this tradeoff.

### 4. Reading `EXPLAIN ANALYZE`

- `EXPLAIN` → the *plan* and *estimates* only (doesn't run the query).
- `EXPLAIN ANALYZE` → **actually executes** it and adds real timings + actual row counts. (Careful with `INSERT/UPDATE/DELETE` — it really runs them; wrap in a transaction and `ROLLBACK`.)
- `EXPLAIN (ANALYZE, BUFFERS)` → adds buffer/page hit/read counts — the truest measure of I/O.

Each node prints: `cost=startup..total rows=N width=bytes` (estimates) and `(actual time=startup..total rows=N loops=L)` (reality). **The single most important habit: compare estimated `rows` vs actual `rows`.** A 100x divergence means the planner is flying blind and every decision above that node is suspect.

### 5. Composite & covering indexes

- **Composite** `(a, b)`: ordered first by `a`, then by `b`. It can serve `WHERE a = ?`, `WHERE a = ? AND b = ?`, and `WHERE a = ? ORDER BY b`. It **cannot** efficiently serve `WHERE b = ?` alone — that's the "leftmost prefix" rule. Column order matters: put the column used for **equality** first, the column used for **range/sort** second.
- **Covering** (`INCLUDE`): adds non-key payload columns to the leaf so a query reading only those columns never touches the heap → **index-only scan**.

### 6. The cost of indexes (the *why*)

Indexes are not free lunches:
- **Write amplification**: every `INSERT`/`UPDATE`/`DELETE` must also update every relevant index. 6 indexes ≈ 7x the write work on the index side.
- **HOT-update defeat**: in PostgreSQL an `UPDATE` that changes an indexed column cannot use the **Heap-Only Tuple** optimization, forcing new index entries.
- **Bloat**: MVCC (Day 5) means old tuples linger until `VACUUM`; indexes accumulate dead entries and grow, hurting cache locality.
- **Planner confusion / disk space**: more indexes = larger plan search space and more storage. Index the queries you actually run, not every column "just in case."

---

## Prerequisites & setup

### Run Postgres in Docker

```bash
docker run -d --name pg21 \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=shop \
  -p 5432:5432 \
  postgres:16

# wait for it to be ready
docker exec pg21 bash -c 'until pg_isready -U postgres; do sleep 1; done'

# enable the query-stats extension (used in "Going deeper")
docker exec pg21 psql -U postgres -d shop \
  -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
```

Connect via psql (run it inside the container so you need no local client):

```bash
docker exec -it pg21 psql -U postgres -d shop
```

JDBC URL for the optional Spring Boot app: `jdbc:postgresql://localhost:5432/shop` (user `postgres`, pass `secret`).

> Tip: turn on timing in psql with `\timing on` so every statement reports wall-clock time alongside the plan.

---

## 🛠️ Project Walkthrough — IndexLab

Roll up your sleeves: the steps below are hands-on — run each one against your own Postgres instance and inspect the plans as you go.

### Step 1 — Create the table and generate 1,000,000 rows

```sql
CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT       NOT NULL,
    status      TEXT         NOT NULL,   -- low cardinality (4 values)
    email       TEXT         NOT NULL,   -- high cardinality (near-unique)
    total_cents INTEGER      NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL
);

-- Generate 1,000,000 rows with a single set-returning query.
INSERT INTO orders (customer_id, status, email, total_cents, created_at)
SELECT
    (random() * 50000)::bigint + 1                       AS customer_id,
    (ARRAY['NEW','PAID','SHIPPED','CANCELLED'])[floor(random()*4)+1] AS status,
    'user' || g || '@example.com'                        AS email,
    (random() * 20000)::int                              AS total_cents,
    NOW() - (random() * interval '365 days')             AS created_at
FROM generate_series(1, 1000000) AS g;

-- CRUCIAL: refresh planner statistics. Without this the planner has stale/empty
-- stats and every estimate below would be garbage.
ANALYZE orders;
```

Note `id` already has a B-tree (the `PRIMARY KEY`). `customer_id`, `status`, `email`, `created_at` have **none** yet — that's deliberate.

### Step 2 — A slow query, and the sequential scan (BEFORE)

We want all orders for one customer:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, email, total_cents, created_at
FROM orders
WHERE customer_id = 12345;
```

Expected plan (numbers vary by machine):

```
Seq Scan on orders  (cost=0.00..23334.00 rows=20 width=45)
                    (actual time=0.412..118.too rows=21 loops=1)
  Filter: (customer_id = 12345)
  Rows Removed by Filter: 999979
  Buffers: shared hit=8334
Planning Time: 0.18 ms
Execution Time: 118.9 ms
```

**Annotated:**
- `Seq Scan` — the engine read **every page** of the heap (`Buffers: shared hit=8334` ≈ the whole table) and threw away 999,979 rows. This is the smell: `Rows Removed by Filter` is enormous.
- `cost=0.00..23334.00` — startup cost 0 (can emit first row immediately), total cost 23334 (proportional to all pages).
- `rows=20` estimated vs `rows=21` actual — estimates are *good* (we ran `ANALYZE`), so the planner isn't wrong about *what* the query returns; a seq scan is simply the only tool it has.
- ~119 ms to find 21 rows. That's the problem.

### Step 3 — Add the index (AFTER)

```sql
CREATE INDEX idx_orders_customer ON orders (customer_id);
ANALYZE orders;   -- not strictly required, CREATE INDEX updates some stats, but good habit
```

Re-run the exact same `EXPLAIN (ANALYZE, BUFFERS)` from Step 2:

```
Index Scan using idx_orders_customer on orders
        (cost=0.42..89.61 rows=20 width=45)
        (actual time=0.041..0.094 rows=21 loops=1)
  Index Cond: (customer_id = 12345)
  Buffers: shared hit=24
Planning Time: 0.21 ms
Execution Time: 0.121 ms
```

**Annotated:**
- `Index Scan` — walked the B-tree (`Index Cond`) to find 21 TIDs, then fetched those rows from the heap.
- `Buffers: shared hit=24` vs **8334** before — ~350x fewer pages touched.
- **0.12 ms vs 119 ms** — roughly **1000x faster**. This is the day's headline: the same query, same data, the structure from Day 6 turning a linear scan into a logarithmic one.
- `cost` dropped from 23334 to 89.61 — that drop is *why* the planner chose this plan.

### Step 4 — When the index does NOT help (low selectivity)

```sql
CREATE INDEX idx_orders_status ON orders (status);
ANALYZE orders;

EXPLAIN ANALYZE
SELECT count(*) FROM orders WHERE status = 'PAID';
```

Likely plan:

```
Finalize Aggregate ...
  ->  Gather ...
        ->  Parallel Seq Scan on orders  (cost=... rows=125000 ...)
              Filter: (status = 'PAID')
```

The planner **ignores `idx_orders_status`** and seq-scans anyway. Why? `status` has 4 values → `'PAID'` matches ~25% of the table (250k rows). Reading 250k rows via random index+heap I/O (`random_page_cost`) is *more expensive* than streaming sequentially. **This is the planner being correct, not broken.** A low-cardinality column is rarely worth a plain B-tree. (A *partial* index — see "Going deeper" — is the senior fix here.) Compare estimated `rows=125000`; the planner knows it's reading a quarter of the table, so it picks the scan.

### Step 5 — Composite index: correct vs wrong column order

Query: "recent orders for a customer, newest first."

```sql
EXPLAIN ANALYZE
SELECT id, total_cents
FROM orders
WHERE customer_id = 12345 AND created_at >= NOW() - interval '90 days'
ORDER BY created_at DESC;
```

**Wrong order** — range/sort column first:

```sql
CREATE INDEX idx_wrong ON orders (created_at, customer_id);
```

The planner can use `created_at` for the range, but because `customer_id` is *after* a range column it can't be used as an equality seek — it ends up filtering, scanning many index entries (all 90 days for *all* customers).

**Right order** — equality column first, then range/sort:

```sql
DROP INDEX idx_wrong;
CREATE INDEX idx_right ON orders (customer_id, created_at);
ANALYZE orders;
```

Now:

```
Index Scan using idx_right on orders
   (cost=0.42..12.08 rows=2 width=12) (actual time=0.03..0.04 rows=2 loops=1)
   Index Cond: ((customer_id = 12345) AND (created_at >= ...))
```

**Annotated:**
- `customer_id = 12345` is an exact B-tree seek (leftmost prefix); `created_at >=` is a range *within* that customer's contiguous slice.
- Because the index is already ordered by `created_at` within a customer, the `ORDER BY created_at DESC` needs **no separate sort** — you'll see no `Sort` node (the engine reads the index backwards). That eliminated sort is often a bigger win than the seek itself.
- Rule learned: **equality columns first, then the range/sort column.** `(b, a)` ≠ `(a, b)`.

### Step 6 — Covering index → index-only scan

Suppose a hot query only needs `customer_id` + `total_cents`:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT customer_id, total_cents
FROM orders
WHERE customer_id = 12345;
```

With `idx_orders_customer` (key = `customer_id` only) you get an `Index Scan` that *still* visits the heap to fetch `total_cents`. Add a **covering** index that carries the payload in the leaf:

```sql
CREATE INDEX idx_cover ON orders (customer_id) INCLUDE (total_cents);
ANALYZE orders;
```

Re-run:

```
Index Only Scan using idx_cover on orders
     (cost=0.42..8.61 rows=20 width=12) (actual time=0.02..0.03 rows=21 loops=1)
   Index Cond: (customer_id = 12345)
   Heap Fetches: 0
   Buffers: shared hit=3
```

**Annotated:**
- `Index Only Scan` + `Heap Fetches: 0` — everything came from the index leaf; the heap was never touched. Fewest buffers of any plan so far.
- `INCLUDE (total_cents)` keeps `total_cents` in the leaf as **non-key** payload (doesn't bloat the tree's ordering, can't be a search/range condition, but is returnable).
- Caveat (deeper below): `Heap Fetches: 0` depends on the **visibility map**; right after heavy writes you may see non-zero heap fetches until `VACUUM` marks pages all-visible.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

- **Partial indexes** — the right fix for Step 4. If you only ever query *open* orders, index just those rows:
  ```sql
  CREATE INDEX idx_open_orders ON orders (created_at) WHERE status IN ('NEW','PAID');
  ```
  Smaller index, cheaper to maintain, and the planner uses it *only* when the `WHERE` matches the partial predicate. Great for "active/soft-deleted" flags.

- **Index-only scans & the visibility map.** Because of MVCC (Day 5), an index entry alone can't prove a tuple is visible to your transaction. PostgreSQL keeps a **visibility map** marking heap pages where *all* tuples are visible. An index-only scan can skip the heap *only* for tuples on all-visible pages; otherwise it does a `Heap Fetch`. `VACUUM` maintains the visibility map — this is why `autovacuum` health directly affects index-only-scan performance. Check with `EXPLAIN (ANALYZE)` → `Heap Fetches:`.

- **When the planner ignores an index (and is right).** Low selectivity (Step 4); table too small (seq scan of a few pages beats any index); statistics stale (run `ANALYZE`); a function/expression on the column (`WHERE lower(email) = ...` can't use a plain index on `email` — needs an **expression index** `CREATE INDEX ON orders (lower(email))`); implicit type casts (`WHERE customer_id = '123'` vs bigint); or `random_page_cost` mis-tuned for SSDs (lower it toward ~1.1 on fast storage and watch plans flip to index scans). To investigate a *suspected* wrong choice, temporarily `SET enable_seqscan = off;` and re-`EXPLAIN` — if the forced index plan has a *higher* cost, the planner was right.

- **Estimated vs actual rows — the master diagnostic.** When they diverge wildly, suspect correlated columns (the planner assumes independence). Fix with **extended statistics**: `CREATE STATISTICS s (dependencies) ON status, customer_id FROM orders; ANALYZE orders;`.

- **B-tree vs LSM recap.** PostgreSQL's B-tree updates **in place** (read-modify-write of 8 KB pages) — great reads, write amplification + bloat under churn (mitigated by `VACUUM`). An **LSM-tree** (Day 2's hash index → SSTables, used by RocksDB/Cassandra) buffers writes in memory and flushes immutable sorted runs, then compacts — great write throughput and append-friendly, at the cost of read amplification and compaction overhead. The B-tree vs LSM choice is the classic read-optimized vs write-optimized tradeoff; Postgres bets on B-tree, and your indexing decisions live inside that bet.

- **`pg_stat_statements` — find the queries worth indexing.** Don't guess which queries are slow; measure aggregate impact:
  ```sql
  SELECT substring(query,1,60) AS query, calls, mean_exec_time, total_exec_time, rows
  FROM pg_stat_statements
  ORDER BY total_exec_time DESC
  LIMIT 10;
  ```
  `total_exec_time` (calls x mean) is the column that matters — a 5 ms query run 1,000,000 times deserves an index more than a 2 s query run twice. This is the production loop: `pg_stat_statements` finds the offender → `EXPLAIN ANALYZE` explains it → index/rewrite → verify the plan flipped.

### Optional Java glue (Spring Boot / JPA)

Tie this back to **Day 12** (N+1 / the SQL JPA actually emits). Log the real SQL and confirm your derived query hits the index:

```properties
# application.properties
spring.datasource.url=jdbc:postgresql://localhost:5432/shop
spring.datasource.username=postgres
spring.datasource.password=secret
spring.jpa.show-sql=true
spring.jpa.properties.hibernate.format_sql=true
logging.level.org.hibernate.SQL=DEBUG
```

```java
public interface OrderRepository extends JpaRepository<Order, Long> {
    // Generates: WHERE customer_id = ? AND created_at >= ? ORDER BY created_at DESC
    // -> exactly the query that idx_right (customer_id, created_at) serves index-only-ish.
    List<Order> findByCustomerIdAndCreatedAtGreaterThanEqualOrderByCreatedAtDesc(
            Long customerId, OffsetDateTime since);
}
```

Then prove it: copy the logged SQL into psql and prefix with `EXPLAIN ANALYZE`. The point: your ORM's convenience methods still bottom out in a query plan you are responsible for.

---

### Stretch goals

1. **Bitmap heap scan**: write a query with *two* moderately-selective predicates each on its own index (e.g. `customer_id` AND a `total_cents` range), and watch the planner produce a `BitmapAnd` → `Bitmap Heap Scan` that combines two indexes. Explain in your notes why a bitmap scan sits *between* a pure index scan and a seq scan in cost.
2. **Bloat & VACUUM**: `UPDATE orders SET total_cents = total_cents + 1 WHERE customer_id < 10000;` several times, then compare `pg_relation_size('idx_right')` before/after and after `VACUUM (ANALYZE, VERBOSE) orders;`. Quantify the bloat.
3. **BRIN vs B-tree on `created_at`**: since `created_at` correlates with insertion order, build `CREATE INDEX ... USING brin (created_at)` and compare its size (`pg_relation_size`) and a range-query plan against a B-tree on the same column. BRIN should be orders of magnitude smaller.
4. **Force the bad plan**: `SET enable_indexscan = off;` and re-EXPLAIN your Step 3 query. Confirm the cost the planner *would have* paid, proving its choice was rational.

### Day 22 teaser

You've made *one* node fast. But what happens when 1M rows becomes 1B and won't fit on one box? **Day 22: Sharding & Consistent Hashing** — we split data across nodes, learn why naive `hash(key) % N` melts down when `N` changes, and build a consistent-hash ring so adding a shard only remaps `1/N` of the keys. The B-tree you indexed today lives *inside each shard*; tomorrow is about choosing *which* shard.
