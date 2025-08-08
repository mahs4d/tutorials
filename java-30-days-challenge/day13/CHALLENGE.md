# Day 13: Schema Migrations with Flyway

| | |
|---|---|
| 🏗️ **Project** | **MigrateLab** — a Flyway-managed evolving schema |
| ☕ **Java & language skills** | Spring Boot/Flyway integration, config, writing SQL migration files, disabling `ddl-auto` |
| 🧰 **Library / tool** | Flyway (versioned & repeatable migrations, schema history) |
| 🗄️ **DB / distributed-systems concept** | Schema evolution & zero-downtime expand–contract migrations |
| 📊 **Difficulty** | Easy |

---

## Concept primer

### 1. The problem: schemas are forever, and they change

On Day 12 you let Hibernate create the `author` and `book` tables for you via `ddl-auto`. That is fine for a tutorial. It is a catastrophe for a system that has been live for two years with 40 million rows and a column you now regret naming.

A production schema is a **living, append-only artifact**. You never "edit the schema" — you apply a *change* to the current schema to produce the next one. Each change must be:

- **Ordered** — `V2` only makes sense after `V1`. Schema state is the *fold* of all changes applied in sequence.
- **Immutable once shipped** — you cannot edit `V3` after it ran in production, because some databases already applied the old `V3`. You can only add `V4`.
- **Recorded** — the database itself must know which changes it has already seen, so re-running the app doesn't re-apply them.
- **Reproducible** — a brand-new dev database, the CI database, and the production database must all reach the *same* schema by replaying the *same* ordered list of changes.

If those four properties sound familiar, they should: this is **exactly the Write-Ahead Log from Day 1**. A WAL is an ordered, immutable, append-only sequence of changes; the current state is the replay of the log. Flyway's `flyway_schema_history` table is a WAL *for your DDL*. "Migrations as code" is just "treat schema changes the way a database treats writes."

### 2. Migrations as code

A migration is a small SQL (or Java) file that transforms the schema from version *N* to version *N+1*. You check these files into Git next to your application code. The benefits:

- The schema is **versioned in source control** alongside the code that depends on it. The PR that adds `Book.isbn` to the entity *also* contains `V2__add_isbn_to_book.sql`. Review them together.
- The schema is **reproducible**: `git checkout` an old commit and you get the migrations as they were then.
- Deployment is **deterministic**: every environment runs the identical ordered list.

This is the opposite of the "someone SSHed into prod and ran an `ALTER TABLE` by hand" anti-pattern, which leaves dev and prod silently divergent until something explodes at 3 a.m.

### 3. Versioned vs. repeatable migrations

Flyway has two kinds of migration, distinguished by filename prefix:

| Kind | Prefix | Runs when | Re-runs? | Use for |
|---|---|---|---|---|
| **Versioned** | `V` | exactly once, in version order | no | `CREATE TABLE`, `ALTER TABLE`, `INSERT` of reference data, backfills — anything that mutates structure or is a one-time data change |
| **Repeatable** | `R` | after all versioned, whenever its **checksum changes** | yes | views, stored procedures, functions — objects you `CREATE OR REPLACE` and want to keep in sync with the file |

Naming convention (this is mandatory and trips people up):

```
V 2 __ add_isbn_to_book .sql
│ │ │  │                  │
│ │ │  │                  └─ suffix
│ │ │  └─ description (underscores become spaces in the history table)
│ │ └─ separator: always TWO underscores
│ └─ version: 1, 2, 2.1, 20240613_1200 (dotted or underscored; sorted numerically)
└─ prefix: V (versioned) or R (repeatable)
```

A repeatable migration has **no version**: `R__create_book_summary_view.sql`. Flyway hashes its contents; change the file, it re-applies after all `V` migrations.

### 4. The `flyway_schema_history` table

The first time Flyway runs against a database it creates a bookkeeping table (default name `flyway_schema_history`):

| installed_rank | version | description | type | script | checksum | installed_by | installed_on | execution_time | success |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | init schema | SQL | V1__init_schema.sql | -1408324918 | app | 2026-06-16 10:00:01 | 42 | t |
| 2 | 2 | add isbn to book | SQL | V2__add_isbn_to_book.sql | 884512330 | app | 2026-06-16 10:00:01 | 11 | t |

On every startup Flyway: (1) reads this table, (2) scans `classpath:db/migration`, (3) applies any `V` migration whose version is greater than the latest applied, in order, (4) re-applies any `R` whose checksum changed. The **`checksum`** column is a tripwire: if you *edit an already-applied versioned migration*, the file's checksum no longer matches the recorded one and Flyway **fails the build** with a validation error. That is a feature — it enforces immutability. (The escape hatch is `flyway repair`, used rarely and deliberately.)

### 5. Zero-downtime schema change: expand–contract (parallel change)

Here is the senior-level core of the day. In a real system you deploy with **rolling updates**: for a window of minutes, **old and new versions of your app run simultaneously against the same database.** This makes any *destructive* or *incompatible* schema change a live hazard.

Consider the seemingly trivial "rename `book.title` to `book.name`":

```sql
ALTER TABLE book RENAME COLUMN title TO name;  -- DO NOT DO THIS in one step
```

The instant this runs, every still-running *old* instance — which `SELECT title FROM book` — starts throwing `column "title" does not exist`. You have just taken a partial outage during a deploy, and a rollback is now a *second* destructive migration. Dropping a column has the same problem; so does adding a `NOT NULL` column with no default to a populated table.

The fix is **expand–contract** (a.k.a. *parallel change*). Split the dangerous change into a sequence of individually backward/forward-compatible steps, each shipped in its own deploy:

1. **Expand** — add the new structure *additively*. Add the `name` column (nullable). The old code ignores it; the new code can write it. Nothing breaks.
2. **Migrate / dual-write** — backfill existing rows (`name = title`) and have the app write *both* columns. Keep them in sync (trigger or app-level dual write).
3. **Switch reads** — deploy code that reads from `name`. Old `title`-reading code is now fully drained.
4. **Contract** — once no code references `title`, ship a final migration that drops it.

Each step is **backward-compatible** (old code keeps working) and **forward-compatible** (new code works against the not-yet-migrated state). The destructive step happens last, when it's provably safe. This is the schema equivalent of the idempotent/retry-safe thinking from Days 7 and 10: assume two versions coexist and design so neither breaks.

**Rules of thumb:**
- *Additive* changes (new nullable column, new table, new index) are almost always safe in one step.
- *Destructive* changes (drop/rename column, drop table, narrow a type, add `NOT NULL`) need multiple steps spanning multiple releases.
- A new `NOT NULL` column is added in three moves: add nullable + default → backfill → set `NOT NULL`.

### 6. Why `ddl-auto=update` is dangerous (and what to use)

Hibernate's `spring.jpa.hibernate.ddl-auto` controls what Hibernate does to the schema at startup:

| Value | Behavior | Verdict |
|---|---|---|
| `create` / `create-drop` | drops & recreates schema | dev/test scratch only |
| `update` | diffs entities vs. DB, issues `ALTER`s to "catch up" | **never in production** |
| `validate` | checks entities match the DB, changes nothing | **production-correct** |
| `none` | does nothing | fine if you fully trust migrations |

`ddl-auto=update` looks magical — add a field, Hibernate adds the column. The reasons it is **banned in serious systems**:

- **It is non-deterministic and order-blind.** It diffs *current* entities against *current* DB. There is no ordered history, no replay, no record of *what* it did or *when*. It is the antithesis of a WAL.
- **It only ever adds; it never drops or migrates data.** Renames become "add new column, orphan the old one." It cannot do a backfill. It cannot do expand–contract. It has no concept of intent — only of difference.
- **It does destructive-ish `ALTER`s with no review.** You cannot inspect the SQL in a PR, cannot test it in CI, cannot reason about locking on a 40M-row table (see §"Going deeper").
- **The generated SQL is dialect-dependent and Hibernate-version-dependent.** An upgrade can change what `update` emits.

The senior posture: **migrations own the schema; Hibernate only *validates* against it.** So you set `ddl-auto=validate` and let Flyway run first. If your entities and your migrations drift apart, the app refuses to start — which is exactly what you want.

---

## Prerequisites & setup

You continue the **Day 12** project (the Author/Book JPA app with `spring-boot-starter-data-jpa` and a PostgreSQL datasource). We change three things: add the Flyway dependency, switch `ddl-auto` to `validate`, and add migration files.

### Maven dependency

Flyway 10+ split the database-specific code into separate modules, so you need both `flyway-core` and the PostgreSQL module. Spring Boot's dependency management aligns the versions for you.

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.flywaydb</groupId>
    <artifactId>flyway-core</artifactId>
</dependency>
<dependency>
    <groupId>org.flywaydb</groupId>
    <artifactId>flyway-database-postgresql</artifactId>
</dependency>
```

(If you are on the older Flyway 9.x that ships with Spring Boot 3.1 and earlier, `flyway-core` alone is enough.)

### application.yml

```yaml
spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/library
    username: app
    password: app
  jpa:
    hibernate:
      ddl-auto: validate        # <-- the headline change. NOT 'update'.
    properties:
      hibernate:
        format_sql: true
    show-sql: true
  flyway:
    enabled: true               # default true when flyway-core is on the classpath
    locations: classpath:db/migration
    baseline-on-migrate: false  # true only when adopting an EXISTING db (see step 6)
    # baseline-version: 1
    # validate-on-migrate: true # default true: verifies checksums of applied migrations
```

When `flyway-core` is on the classpath, Spring Boot autoconfigures a `Flyway` bean and runs `flyway.migrate()` **during startup, before the JPA `EntityManagerFactory` is initialized**. So the order is: pool comes up -> Flyway migrates -> Hibernate *validates* the now-current schema -> app is ready. If Flyway fails, the context fails to start.

---

## 🛠️ Project Walkthrough — MigrateLab

Roll up your sleeves: from here you'll wire Flyway into the Day 12 app and hand-author the migrations step by step, then boot it and read the schema history.

## Steps

### Step 1 — Stop letting Hibernate own the schema

If your Day 12 app had `ddl-auto: update` (or `create-drop`), change it to `validate` as above. From now on, **no entity change is complete without a matching migration.** Add a field to `Book` and forget the migration? `validate` will refuse to boot with a clear "missing column" error. Good.

### Step 2 — V1: the baseline schema

Create `src/main/resources/db/migration/V1__init_schema.sql`. This is the Day 12 schema, written by hand. This is your "genesis block."

```sql
-- V1__init_schema.sql
CREATE TABLE author (
    id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE book (
    id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    title       VARCHAR(300) NOT NULL,
    author_id   BIGINT       NOT NULL REFERENCES author(id),
    published_year SMALLINT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- The N+1 work on Day 12 made you care about this access path:
CREATE INDEX idx_book_author_id ON book(author_id);
```

> Note the FK index `idx_book_author_id`: PostgreSQL does **not** auto-create an index on the *child* side of a foreign key, and the join from Day 12 (`author.books`) hits exactly this path. (Day 21 goes deep on indexing.)

### Step 3 — V2: an additive (safe) change — add a column

A new requirement: track each book's ISBN. Adding a **nullable** column is the canonical safe, single-step migration — old code ignores it, new code can use it.

```sql
-- V2__add_isbn_to_book.sql
ALTER TABLE book ADD COLUMN isbn VARCHAR(20);

CREATE UNIQUE INDEX idx_book_isbn ON book(isbn);  -- partial-friendly: multiple NULLs allowed in Postgres
```

Then add the field to the entity so `validate` stays happy:

```java
@Column(length = 20, unique = true)
private String isbn;
```

> A `UNIQUE` index in PostgreSQL treats `NULL`s as distinct, so many books with no ISBN coexist fine — uniqueness only bites once values exist. That is *why* "nullable + unique" is safe to add in one shot.

### Step 4 — V3: a data migration / backfill

Migrations aren't only DDL. A versioned migration can carry **one-time data changes**. Suppose legacy rows stored the year inside the title like `"Dune (1965)"` and you want `published_year` populated. A backfill:

```sql
-- V3__backfill_published_year.sql
UPDATE book
SET published_year = CAST(substring(title FROM '\((\d{4})\)') AS SMALLINT)
WHERE published_year IS NULL
  AND title ~ '\(\d{4}\)';
```

Two senior points: (1) backfills go in **`V`** (versioned, run-once) migrations, never `R`, because you do not want them re-running. (2) On a large table, an unbounded `UPDATE` locks/bloats; in production you'd **batch** it (loop `UPDATE ... WHERE id BETWEEN ...`), often outside Flyway via a one-off job, because a long migration blocks deploys and holds locks.

### Step 5 — Expand–contract: rename `book.title` -> `book.name` across V4/V5

Now the real exercise. We want `title` renamed to `name`, with **zero downtime**. We refuse the one-liner from the primer. Split it.

**V4 — Expand + dual-write (deployed with code release A):**

```sql
-- V4__expand_book_name.sql
-- 1. Add the new column, nullable, so old code is untouched.
ALTER TABLE book ADD COLUMN name VARCHAR(300);

-- 2. Backfill existing rows so new readers see data immediately.
UPDATE book SET name = title WHERE name IS NULL;

-- 3. Keep the two in sync while BOTH old and new code run.
--    A trigger handles writes from old code (which only sets `title`)
--    and from new code (which only sets `name`).
CREATE OR REPLACE FUNCTION sync_book_title_name() RETURNS trigger AS $$
BEGIN
    IF NEW.name IS DISTINCT FROM OLD.name AND NEW.name IS NOT NULL THEN
        NEW.title := NEW.name;
    ELSIF NEW.title IS DISTINCT FROM OLD.title THEN
        NEW.name := NEW.title;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_book_title_name
    BEFORE INSERT OR UPDATE ON book
    FOR EACH ROW EXECUTE FUNCTION sync_book_title_name();
```

Release A's code maps the entity to `name` (or keeps `title` — either works, because the trigger keeps both columns identical). **Deploy A fully and let it drain old instances.** Now nothing reads `title` anymore.

**V5 — Contract (deployed with code release B, after A is fully rolled out):**

```sql
-- V5__contract_drop_book_title.sql
-- Pre-req: NO running code references `title` anymore (release A is everywhere).
DROP TRIGGER IF EXISTS trg_sync_book_title_name ON book;
DROP FUNCTION IF EXISTS sync_book_title_name();

ALTER TABLE book ALTER COLUMN name SET NOT NULL;  -- safe now: fully backfilled
ALTER TABLE book DROP COLUMN title;
```

The crucial discipline: **V5 ships in a *later* deploy than V4.** If you put both in the same release, the dropped column can break instances of the *previous* release that are still serving traffic mid-rollout. Expand–contract is fundamentally about *time between deploys*, not about clever SQL.

### Step 6 — Baselining an existing database (the adoption case)

Your Day 12 DB was created by `ddl-auto`, so it already has `author`/`book` tables but **no `flyway_schema_history`**. If you point Flyway at it with `V1__init_schema.sql`, Flyway tries to `CREATE TABLE author` and fails ("relation already exists").

`baseline-on-migrate` solves this. Set it once when adopting:

```yaml
spring:
  flyway:
    baseline-on-migrate: true
    baseline-version: 1          # treat the existing DB as already at V1
    baseline-description: "existing schema (pre-Flyway)"
```

Flyway then writes a `baseline` row into `flyway_schema_history` marking version 1 as already present, and **only applies V2 and above.** (For a clean DB created *by* Flyway, set `baseline-on-migrate: false` so V1 actually runs. The cleanest path: drop the auto-created tables and let Flyway build everything from V1.)

---

## How to run & how to read the history

Run the app:

```bash
mvn spring-boot:run
```

Watch the log — Flyway announces itself before Hibernate:

```
o.f.c.i.l.VersionPrinter : Flyway Community Edition 10.x by Redgate
o.f.core.internal.command.DbMigrate : Current version of schema "public": << Empty Schema >>
o.f.core.internal.command.DbMigrate : Migrating schema "public" to version "1 - init schema"
o.f.core.internal.command.DbMigrate : Migrating schema "public" to version "2 - add isbn to book"
o.f.core.internal.command.DbMigrate : Migrating schema "public" to version "3 - backfill published year"
o.f.core.internal.command.DbMigrate : Migrating schema "public" to version "4 - expand book name"
o.f.core.internal.command.DbMigrate : Migrating schema "public" to version "5 - contract drop book title"
o.f.core.internal.command.DbMigrate : Successfully applied 5 migrations to schema "public" (execution time 00:00.18s)
...
o.h.tool.schema.internal.SchemaValidatorImpl : (Hibernate validate runs, no errors)
```

Restart the app — Flyway sees the history and applies **nothing**:

```
o.f.core.internal.command.DbMigrate : Current version of schema "public": 5
o.f.core.internal.command.DbMigrate : Schema "public" is up to date. No migration necessary.
```

Inspect the history table directly:

```bash
psql library -c "SELECT installed_rank, version, description, type, success, execution_time \
                 FROM flyway_schema_history ORDER BY installed_rank;"
```

Expected output:

```
 installed_rank | version |     description      | type | success | execution_time
----------------+---------+----------------------+------+---------+----------------
              1 | 1       | init schema          | SQL  | t       |             42
              2 | 2       | add isbn to book     | SQL  | t       |             11
              3 | 3       | backfill published.. | SQL  | t       |              7
              4 | 4       | expand book name     | SQL  | t       |             19
              5 | 5       | contract drop book.. | SQL  | t       |              9
```

Verify the schema reached the contracted state:

```bash
psql library -c "\d book"
# 'title' is gone; 'name' is NOT NULL; 'isbn' is present and unique.
```

**Demonstrate the immutability tripwire:** edit V2 (add a trailing comment), restart. Flyway aborts:

```
FlywayValidateException: Migration checksum mismatch for migration version 2
-> Applied to database : 884512330
-> Resolved locally    : 1290114537
```

This is the WAL property enforced: you cannot rewrite history. The fix is a new `V6`, not an edit (or, only if you *know* the change is cosmetically safe, `flyway repair` to re-baseline the checksum).

---

## 🚀 Going Deeper & Next Steps

## Going deeper / senior-level notes

- **Liquibase as the alternative.** The other big JVM migration tool. Differences worth knowing: Liquibase changesets can be written in XML/YAML/JSON (database-agnostic abstraction) *or* raw SQL; it supports **declarative rollback** (`<rollback>` blocks / auto-generated undo for many change types), which Flyway only offers as a paid feature. Flyway's philosophy is "SQL is the source of truth, migrations are forward-only"; Liquibase's is "describe the change abstractly, generate per-dialect SQL, support rollback." Pick Flyway when your team thinks in SQL and one database; Liquibase when you genuinely target multiple DB engines or need built-in rollback. Both solve the same WAL-for-DDL problem.

- **Locking on large `ALTER TABLE`.** This is where migrations meet the distributed-systems reality. In PostgreSQL many `ALTER`s take an `ACCESS EXCLUSIVE` lock that blocks *all* reads and writes for the duration — fine on a small table, an outage on a hot 100M-row table. Crucial nuances: adding a column with a *non-volatile* default is fast (metadata-only) on PG 11+, but `ALTER COLUMN ... SET NOT NULL` historically scanned the whole table (mitigated on PG 12+ if a validated `CHECK` exists). **Create indexes with `CREATE INDEX CONCURRENTLY`** to avoid blocking writes — but note it cannot run inside a transaction, so configure that migration to run outside Flyway's wrapping transaction (Postgres-specific Flyway behavior / a separate non-transactional migration). Always `SET lock_timeout` before a risky `ALTER` so a blocked migration fails fast instead of queueing behind it and freezing the table.

- **Online schema-change tools (MySQL world):** `gh-ost` (GitHub) and `pt-online-schema-change` (Percona). On MySQL, big `ALTER`s are even more painful, so these tools do the expand–contract dance *automatically at the storage level*: create a shadow copy of the table with the new structure, backfill it, capture concurrent writes via triggers (pt-osc) or the binlog (gh-ost), then atomically swap the tables. They are the industrial-strength version of the V4/V5 pattern you did by hand. You typically invoke them *instead of* a raw `ALTER` in a migration step for huge tables.

- **Rollback strategy.** Flyway Community is **forward-only** — there is no automatic "undo." Senior practice does not rely on rolling *back* the database at all; it relies on the schema changes being **backward-compatible by construction** (that's the whole point of expand–contract). If V4 ships and you must revert the *code*, the old code still runs because V4 was additive. You "roll back" by deploying the previous app version, not by reversing DDL. When you genuinely must reverse a destructive change, you write a *new forward migration* that re-adds what was dropped (and you're glad you kept a backup, because the data is gone). The rule: **never let a migration be the thing you need to undo under pressure** — make destructive steps the *last*, *separate*, *deliberate* deploy.

- **CI gate.** Run `flyway validate` (or just boot the app with `validate`) in CI against a throwaway DB (hello, **Testcontainers** — Day 23) so a missing/mismatched migration fails the pipeline, not production.

---

## Stretch goals

1. **Repeatable migration.** Add `R__book_summary_view.sql` containing `CREATE OR REPLACE VIEW book_summary AS SELECT b.id, b.name, a.name AS author FROM book b JOIN author a ON a.id = b.author_id;`. Boot the app, confirm it ran *after* V5. Then edit the view (add `b.isbn`), reboot, and watch Flyway re-apply it because the checksum changed — while the `V` migrations stay untouched.

2. **Trigger the validation tripwire deliberately.** Add a field to the `Book` entity (`@Column private String publisher;`) *without* a migration. Boot with `ddl-auto=validate` and read the exact Hibernate `SchemaManagementException` ("missing column [publisher]"). Then add `V6__add_publisher.sql` and watch it boot cleanly. Feel why `validate` + migrations is safer than `update`.

3. **NOT NULL the safe way.** Add a non-null `status` column to `book` across the three-step pattern in one migration file for practice: `ADD COLUMN status VARCHAR(10) DEFAULT 'ACTIVE'` -> `UPDATE book SET status='ACTIVE' WHERE status IS NULL` -> `ALTER COLUMN status SET NOT NULL`. Explain in a comment why doing this as one combined statement is only safe on a *small/new* table.

4. **Baseline drill.** Spin up a *separate* Postgres, create `author`/`book` by hand (simulating a legacy DB), then point your app at it with `baseline-on-migrate=true` and `baseline-version=1`. Inspect the `baseline` row in `flyway_schema_history` and confirm only V2+ ran.

---

## Day 14 teaser

Your migrations now guarantee the *database* is well-shaped. **Day 14 — Validation & DTOs** guards the *other* boundary: the data coming *in* over the API. You'll formalize the Jakarta Bean Validation hints from Day 10 (`@Valid`, `@NotNull`, `@Size`, custom constraints), and split your JPA entities from request/response **DTOs** so your persistence model and your API contract can evolve independently — the application-layer cousin of the expand–contract decoupling you just practiced on the schema.
