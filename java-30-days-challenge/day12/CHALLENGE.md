# Day 12: JPA, Hibernate & the N+1 Problem

| | |
|---|---|
| 🏗️ **Project** | **Bookshelf** — a JPA author/book app that reproduces and fixes N+1 |
| ☕ **Java & language skills** | JPA entities, annotations, relationships, repositories, JPQL, lazy/eager |
| 🧰 **Library / tool** | Spring Data JPA / Hibernate |
| 🗄️ **DB / distributed-systems concept** | ORM persistence context & the N+1 query problem |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. What is an ORM, and why does one exist?

On Day 11 you used `JdbcTemplate`: you wrote SQL by hand and mapped each `ResultSet` row into an object with a `RowMapper`. That's explicit, fast, and you always know exactly what runs against the DB. The cost is **boilerplate** and an **impedance mismatch** — your code thinks in object graphs (an `Author` *has* a list of `Book`s, each `Book` points *back* to its `Author`), but SQL thinks in flat rows and foreign keys. Translating between the two by hand, for every query, every insert, every update, is tedious and error-prone.

An **Object-Relational Mapper** automates that translation. You annotate plain Java classes to describe how they map to tables, and the ORM generates the SQL, runs it, and hydrates objects for you — including walking relationships. Hibernate is the dominant Java ORM; **JPA (Jakarta Persistence API)** is the *standard* it implements, so your annotations (`@Entity`, `@Id`, …) are portable across JPA providers. **Spring Data JPA** adds the final convenience layer: you declare a `JpaRepository` *interface* and Spring generates the implementation at runtime.

The stack, bottom to top:

```
JDBC driver  ->  Hibernate (ORM engine: SQL gen, caching, dirty checking)
             ->  JPA (the annotations + EntityManager API you code against)
             ->  Spring Data JPA (auto-implemented repositories, derived queries)
```

The ORM tradeoff in one sentence: **you trade explicit control over SQL for productivity and an object-oriented programming model — and the bill for that trade usually arrives as the N+1 problem.**

### 2. The persistence context (first-level cache)

This is the single most important concept in JPA, and the one most people skip.

Every JPA operation happens inside a **persistence context**, managed by an `EntityManager`. In a Spring app, the persistence context is bound to the **transaction**: it's created when a `@Transactional` method begins and flushed/closed when it commits. While it's open it acts as a **first-level cache** and an **identity map** with three guarantees:

1. **Identity guarantee.** Within one persistence context, loading the same row twice returns the *same object instance* (`==`). The second load is served from memory, not the DB. This is why entities are "managed."
2. **Dirty checking.** Hibernate keeps a snapshot of every managed entity as it was loaded. At flush time (usually right before commit) it compares the current state to the snapshot and **auto-generates `UPDATE` statements for whatever changed** — you never call `save()` for an entity you loaded inside the transaction. Mutating a getter result is enough:

   ```java
   @Transactional
   public void renameAuthor(Long id, String name) {
       Author a = authorRepository.findById(id).orElseThrow();
       a.setName(name);          // no save() call
   }                              // commit -> Hibernate emits UPDATE author SET name=? WHERE id=?
   ```

   This surprises everyone the first time. It is also a footgun: an accidental setter mutation inside a transaction will silently persist.
3. **Write-behind.** Hibernate batches and reorders SQL, delaying writes until flush, so it can coalesce and respect FK ordering.

An entity has a **lifecycle**: `transient` (just `new`ed, unknown to JPA) -> `managed` (attached to a persistence context, dirty-checked) -> `detached` (context closed; changes no longer tracked) -> `removed` (scheduled for `DELETE`). Most JPA bugs are really "I touched a *detached* entity and expected it to be managed," or the inverse.

### 3. Lazy vs eager fetching

Relationships have a **fetch type** controlling *when* the related data is loaded:

- **`EAGER`** — load the association immediately, in (or alongside) the query that loads the owner.
- **`LAZY`** — don't load it now; return a **proxy** (for `@ManyToOne`) or a lazy collection wrapper (for `@OneToMany`). The real query fires only when you first *touch* the association (call a getter, iterate the list).

JPA's defaults are deliberate and worth memorizing:

| Relationship | Default fetch |
|---|---|
| `@ManyToOne` | **EAGER** |
| `@OneToOne` | **EAGER** |
| `@OneToMany` | **LAZY** |
| `@ManyToMany` | **LAZY** |

The rule of thumb among experienced engineers: **make everything `LAZY`** (override the `*ToOne` defaults explicitly), and fetch what you need, when you need it, per query. `EAGER` is a global decision baked into the mapping; it robs you of the ability to *not* load something. Lazy + per-query fetch planning is strictly more flexible. The price of laziness is the trap we cover next.

### 4. The N+1 query problem — the headline bug

This is the most common ORM performance defect in the wild. Setup: `Author` `1—*` `Book`, with `books` lazy (the default). You write what looks like innocent code:

```java
List<Author> authors = authorRepository.findAll();   // query #1: SELECT * FROM author
for (Author a : authors) {
    a.getBooks().size();                              // touches the lazy collection...
}
```

The first call runs **1** query to load N authors. Then, for **each** of the N authors, touching `getBooks()` lazily fires **another** query (`SELECT * FROM book WHERE author_id = ?`). Total: **1 + N** queries. With 100 authors that's 101 round trips to the database — each with network latency, parse, and execution overhead. The page is slow, the DB CPU spikes, and nothing in your Java code *looks* wrong. That's why it's insidious.

**Why it happens mechanically:** Hibernate has no way to know, when it runs query #1, that you intend to walk every author's books. Each lazy collection is initialized independently, on first access, with its own `SELECT`. The fix is always to tell Hibernate your *fetch plan up front* so it can load the books in the same trip (a join) or in a few batched trips.

**How to *see* it:** turn on SQL logging and, better, the Hibernate **Statistics** API to count queries programmatically (so a test can assert the count). We do both below. If you only learn one diagnostic habit from Day 12, make it: **count the queries, don't eyeball the logs.**

The three standard fixes (all implemented in the project):
- **`JOIN FETCH`** (JPQL): `SELECT a FROM Author a JOIN FETCH a.books` — one query, a single join. Watch for row multiplication and the `DISTINCT`/`Set` nuance (covered below).
- **`@EntityGraph`** — declarative fetch plan on a repository method; same effect as `JOIN FETCH` but without writing JPQL.
- **Batch fetching** (`@BatchSize` / `hibernate.default_batch_fetch_size`) — keeps lazy semantics but loads collections in chunks via `WHERE author_id IN (?, ?, …)`, turning 1+N into 1+(N/batch).

---

## Prerequisites & setup

You're extending the same Boot project from Days 8-11. Java 17+ and Maven as on Day 1.

### Maven dependencies

Add JPA and H2 to your `pom.xml` (`spring-boot-starter-web` is already there from Day 10):

```xml
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>

    <dependency>
        <groupId>com.h2database</groupId>
        <artifactId>h2</artifactId>
        <scope>runtime</scope>
    </dependency>

    <!-- already present from Day 10 -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
</dependencies>
```

`spring-boot-starter-data-jpa` pulls in Hibernate, the JPA API, Spring Data JPA, and a connection pool (HikariCP — the same pool you tuned by hand on Day 9; Boot now configures it for you).

### Configure H2, SQL logging, and statistics

`src/main/resources/application.properties`:

```properties
# --- H2 in-memory DB ---
spring.datasource.url=jdbc:h2:mem:day12;DB_CLOSE_DELAY=-1
spring.datasource.username=sa
spring.datasource.password=
spring.h2.console.enabled=true
# visit http://localhost:8080/h2-console (JDBC URL must match the one above)

# Let Hibernate create the schema from the @Entity classes (fine for a demo;
# you'll replace this with Flyway migrations on Day 13).
spring.jpa.hibernate.ddl-auto=create-drop

# --- See the SQL Hibernate emits ---
spring.jpa.show-sql=false
# show-sql dumps to stdout unformatted; prefer the loggers below for clean, formatted SQL.
logging.level.org.hibernate.SQL=DEBUG
logging.level.org.hibernate.orm.jdbc.bind=TRACE
spring.jpa.properties.hibernate.format_sql=true

# --- Turn on the Statistics API so we can COUNT queries ---
spring.jpa.properties.hibernate.generate_statistics=true
```

`hibernate.generate_statistics=true` makes Hibernate log a stats block per session and exposes `SessionFactory.getStatistics()`, whose `getPrepareStatementCount()` is the exact query count we'll assert on.

---

---

## 🛠️ Project Walkthrough — Bookshelf

Roll up your sleeves and build the project step by step, running each piece as you go.

## Step 1 — Model the entities

Create the `Author` and `Book` entities. Note the deliberate choices: `Book.author` is `@ManyToOne(fetch = LAZY)` (overriding the EAGER default), and the bidirectional link is kept consistent with helper methods.

`src/main/java/com/example/demo/jpa/Author.java`:

```java
package com.example.demo.jpa;

import jakarta.persistence.*;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "author")
public class Author {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String name;

    // One author has many books. LAZY is the default for @OneToMany.
    // mappedBy = "author" means Book owns the FK column (author_id);
    // this side is just the inverse view.
    @OneToMany(
            mappedBy = "author",
            cascade = CascadeType.ALL,
            orphanRemoval = true,
            fetch = FetchType.LAZY)
    private List<Book> books = new ArrayList<>();

    protected Author() { }   // JPA requires a no-arg constructor

    public Author(String name) {
        this.name = name;
    }

    // Keep both sides of the relationship in sync — JPA does NOT do this for you.
    public void addBook(Book book) {
        books.add(book);
        book.setAuthor(this);
    }

    public Long getId() { return id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public List<Book> getBooks() { return books; }
}
```

`src/main/java/com/example/demo/jpa/Book.java`:

```java
package com.example.demo.jpa;

import jakarta.persistence.*;

@Entity
@Table(name = "book")
public class Book {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String title;

    // The OWNING side: this is where the author_id FK column lives.
    // Override the @ManyToOne EAGER default to LAZY.
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "author_id", nullable = false)
    private Author author;

    protected Book() { }

    public Book(String title) {
        this.title = title;
    }

    public Long getId() { return id; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public Author getAuthor() { return author; }
    public void setAuthor(Author author) { this.author = author; }
}
```

A few senior notes on the mapping:
- **`mappedBy` decides the FK owner.** The side *without* `mappedBy` (here `Book`) owns the foreign key. If you put `@JoinColumn` on both sides or forget `mappedBy`, Hibernate creates a redundant join table or an extra column.
- **Don't put `@OneToMany` as EAGER.** An eager `@OneToMany` means *every* time you load *any* author, Hibernate joins in all the books — you can never opt out. Keep collections lazy.
- **`equals`/`hashCode`**: for brevity we omit them, but for entities used in `Set`s or across detached boundaries, implement them using a business key (not the generated `id`, which is null before persist). This matters for the `Set`-based N+1 fix below.

## Step 2 — Repositories (Spring Data JPA)

`src/main/java/com/example/demo/jpa/AuthorRepository.java`:

```java
package com.example.demo.jpa;

import jakarta.persistence.NamedAttributeNode;
import jakarta.persistence.NamedEntityGraph;
import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.util.List;

public interface AuthorRepository extends JpaRepository<Author, Long> {

    // 1) Derived query: Spring parses the METHOD NAME into a query.
    //    SELECT * FROM author WHERE name = ?
    List<Author> findByName(String name);

    // 2) The N+1 fix via JPQL JOIN FETCH.
    //    DISTINCT collapses the duplicate Author rows the join produces.
    @Query("SELECT DISTINCT a FROM Author a JOIN FETCH a.books")
    List<Author> findAllWithBooksJoinFetch();

    // 3) The N+1 fix via @EntityGraph — no JPQL, declarative fetch plan.
    //    "books" is loaded eagerly for THIS query only.
    @EntityGraph(attributePaths = "books")
    @Query("SELECT a FROM Author a")
    List<Author> findAllWithBooksEntityGraph();
}
```

`JpaRepository<Author, Long>` gives you `findAll()`, `findById()`, `save()`, `count()`, `deleteById()`, paging, and sorting for free — no implementation written. The method-name derivation in `findByName` is the headline Spring Data feature: the property path in the name (`Name`) becomes the `WHERE` clause.

`BookRepository` is trivial:

```java
package com.example.demo.jpa;

import org.springframework.data.jpa.repository.JpaRepository;

public interface BookRepository extends JpaRepository<Book, Long> { }
```

## Step 3 — Seed data on startup

`src/main/java/com/example/demo/jpa/DataSeeder.java`:

```java
package com.example.demo.jpa;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

@Component
public class DataSeeder implements CommandLineRunner {

    private final AuthorRepository authorRepository;

    public DataSeeder(AuthorRepository authorRepository) {
        this.authorRepository = authorRepository;
    }

    @Override
    public void run(String... args) {
        if (authorRepository.count() > 0) return;

        seed("Ursula K. Le Guin", "A Wizard of Earthsea", "The Left Hand of Darkness", "The Dispossessed");
        seed("Terry Pratchett", "Mort", "Guards! Guards!", "Small Gods", "Night Watch");
        seed("Octavia E. Butler", "Kindred", "Parable of the Sower");
        seed("Isaac Asimov", "Foundation", "I, Robot", "The Caves of Steel");
        seed("Frank Herbert", "Dune", "Dune Messiah");
    }

    private void seed(String authorName, String... titles) {
        Author author = new Author(authorName);
        for (String t : titles) {
            author.addBook(new Book(t));     // keeps both sides in sync
        }
        authorRepository.save(author);       // cascade saves the books too
    }
}
```

`cascade = CascadeType.ALL` on the `@OneToMany` means saving the `Author` cascades the inserts to its `Book`s — you don't save books individually.

## Step 4 — Deliberately trigger N+1

`src/main/java/com/example/demo/jpa/AuthorService.java`:

```java
package com.example.demo.jpa;

import org.hibernate.SessionFactory;
import org.hibernate.stat.Statistics;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import jakarta.persistence.EntityManagerFactory;
import java.util.List;
import java.util.stream.Collectors;

@Service
public class AuthorService {

    private final AuthorRepository authorRepository;
    private final Statistics statistics;

    public AuthorService(AuthorRepository authorRepository,
                         EntityManagerFactory emf) {
        this.authorRepository = authorRepository;
        // Reach through JPA's EntityManagerFactory to Hibernate's SessionFactory
        // to grab the Statistics object that counts JDBC statements.
        this.statistics = emf.unwrap(SessionFactory.class).getStatistics();
    }

    /**
     * THE N+1 VERSION. findAll() = 1 query. Then touching each author's lazy
     * `books` collection fires 1 query PER author -> 1 + N total.
     */
    @Transactional(readOnly = true)
    public List<String> listAuthorsWithBooks_N1() {
        statistics.clear();

        List<Author> authors = authorRepository.findAll();      // query #1
        List<String> result = authors.stream()
                .map(a -> a.getName() + ": " + a.getBooks().size() + " books") // +1 query each
                .collect(Collectors.toList());

        System.out.println(">>> N+1  queries executed = "
                + statistics.getPrepareStatementCount());
        return result;
    }

    /** FIX A: JOIN FETCH — exactly 1 query. */
    @Transactional(readOnly = true)
    public List<String> listAuthorsWithBooks_JoinFetch() {
        statistics.clear();

        List<Author> authors = authorRepository.findAllWithBooksJoinFetch();
        List<String> result = authors.stream()
                .map(a -> a.getName() + ": " + a.getBooks().size() + " books")
                .collect(Collectors.toList());

        System.out.println(">>> JOIN FETCH queries executed = "
                + statistics.getPrepareStatementCount());
        return result;
    }

    /** FIX B: @EntityGraph — also 1 query, no JPQL. */
    @Transactional(readOnly = true)
    public List<String> listAuthorsWithBooks_EntityGraph() {
        statistics.clear();

        List<Author> authors = authorRepository.findAllWithBooksEntityGraph();
        List<String> result = authors.stream()
                .map(a -> a.getName() + ": " + a.getBooks().size() + " books")
                .collect(Collectors.toList());

        System.out.println(">>> @EntityGraph queries executed = "
                + statistics.getPrepareStatementCount());
        return result;
    }
}
```

Two important details:
- **`@Transactional` is required for the N+1 method to even run.** The lazy collection can only be initialized while the persistence context is open. Without an open transaction, touching `getBooks()` throws `LazyInitializationException` (the classic detached-entity error). The `readOnly = true` hint lets Hibernate skip dirty-checking/flush — appropriate for read paths.
- We unwrap the Hibernate `Statistics` from the JPA `EntityManagerFactory` so the count is exact and assertable, not eyeballed.

## Step 5 — Expose it via a controller (optional, to hit from `curl`)

```java
package com.example.demo.jpa;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/authors")
public class AuthorController {

    private final AuthorService service;

    public AuthorController(AuthorService service) { this.service = service; }

    @GetMapping("/n1")          List<String> n1()    { return service.listAuthorsWithBooks_N1(); }
    @GetMapping("/joinfetch")   List<String> jf()    { return service.listAuthorsWithBooks_JoinFetch(); }
    @GetMapping("/entitygraph") List<String> graph() { return service.listAuthorsWithBooks_EntityGraph(); }
}
```

## How to run

```bash
mvn spring-boot:run

# In another terminal:
curl http://localhost:8080/authors/n1            # watch the log flood
curl http://localhost:8080/authors/joinfetch     # one query
curl http://localhost:8080/authors/entitygraph   # one query
```

Each endpoint returns the same JSON (`["Ursula K. Le Guin: 3 books", ...]`); the difference is entirely in how many queries the server ran, visible in the log and the printed count.

## Expected log output

### The N+1 path (`/authors/n1`) — 5 authors -> 1 + 5 = 6 queries

```
Hibernate: select a1_0.id, a1_0.name from author a1_0
Hibernate: select b1_0.author_id, b1_0.id, b1_0.title from book b1_0 where b1_0.author_id=?
Hibernate: select b1_0.author_id, b1_0.id, b1_0.title from book b1_0 where b1_0.author_id=?
Hibernate: select b1_0.author_id, b1_0.id, b1_0.title from book b1_0 where b1_0.author_id=?
Hibernate: select b1_0.author_id, b1_0.id, b1_0.title from book b1_0 where b1_0.author_id=?
Hibernate: select b1_0.author_id, b1_0.id, b1_0.title from book b1_0 where b1_0.author_id=?
>>> N+1  queries executed = 6
```

One `author` query, then **one `book` query per author**. Imagine 10,000 authors.

### The JOIN FETCH path (`/authors/joinfetch`) — exactly 1 query

```
Hibernate:
    select distinct
        a1_0.id, a1_0.name,
        b1_0.author_id, b1_0.id, b1_0.title
    from author a1_0
    join book b1_0 on a1_0.id = b1_0.author_id
>>> JOIN FETCH queries executed = 1
```

### The @EntityGraph path (`/authors/entitygraph`) — also 1 query

```
Hibernate:
    select a1_0.id, a1_0.name,
           b1_0.author_id, b1_0.id, b1_0.title
    from author a1_0
    left join book b1_0 on a1_0.id = b1_0.author_id
>>> @EntityGraph queries executed = 1
```

`@EntityGraph` uses a `LEFT JOIN` (so authors with zero books still appear); a plain `JOIN FETCH` is an inner join (use `LEFT JOIN FETCH` if you need the same inclusivity).

## Step 6 — Lock it in with a test that *counts* queries

Eyeballing logs doesn't survive refactors. Assert the count so an accidental N+1 regression fails CI.

`src/test/java/com/example/demo/jpa/N1Test.java`:

```java
package com.example.demo.jpa;

import org.hibernate.SessionFactory;
import org.hibernate.stat.Statistics;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import jakarta.persistence.EntityManagerFactory;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
class N1Test {

    @Autowired AuthorService service;
    @Autowired EntityManagerFactory emf;

    Statistics stats;

    @BeforeEach
    void setUp() {
        stats = emf.unwrap(SessionFactory.class).getStatistics();
        stats.setStatisticsEnabled(true);
    }

    @Test
    void naiveListingTriggersNPlusOne() {
        stats.clear();
        service.listAuthorsWithBooks_N1();
        // 5 authors seeded -> 1 + 5 = 6
        assertThat(stats.getPrepareStatementCount()).isEqualTo(6);
    }

    @Test
    void joinFetchUsesSingleQuery() {
        stats.clear();
        service.listAuthorsWithBooks_JoinFetch();
        assertThat(stats.getPrepareStatementCount()).isEqualTo(1);
    }

    @Test
    void entityGraphUsesSingleQuery() {
        stats.clear();
        service.listAuthorsWithBooks_EntityGraph();
        assertThat(stats.getPrepareStatementCount()).isEqualTo(1);
    }
}
```

Run with `mvn test`. This is the senior habit: **make the query count a tested invariant.** (On Day 23 you'll run such tests against a real Postgres in Testcontainers instead of H2.)

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

**DTO projections — often better than fetching entities at all.** If a read endpoint only needs a few columns, don't load full managed entities (and don't pay for dirty-checking or lazy proxies). Project straight into a DTO with JPQL `new`:

```java
public record AuthorBookCount(String name, long bookCount) { }

@Query("""
       SELECT new com.example.demo.jpa.AuthorBookCount(a.name, COUNT(b))
       FROM Author a LEFT JOIN a.books b
       GROUP BY a.id, a.name
       """)
List<AuthorBookCount> authorBookCounts();
```

This is one query, returns only the data the UI needs, and sidesteps N+1 *and* the persistence-context overhead entirely. Spring Data also supports **interface-based projections** (declare an interface with getters; Spring builds the SELECT). For read-heavy APIs, projecting is frequently the right answer over fetch tuning.

**The "JOIN FETCH a collection + pagination" trap.** You cannot safely `JOIN FETCH` a `@OneToMany` *and* paginate in the same query. The join multiplies rows (one row per book), so `LIMIT`/`OFFSET` slices *book rows*, not authors. Hibernate detects this and falls back to fetching **everything into memory then paginating in Java** — logging the dreaded `HHH000104: firstResult/maxResults specified with collection fetch; applying in memory`. The fix: fetch IDs (paged) in query #1, then fetch the collections for those IDs with `WHERE id IN (...)` (the "two-query" pattern), or use `@BatchSize` / `default_batch_fetch_size` which paginates fine because the collection stays lazy.

**`MultipleBagFetchException` — don't `JOIN FETCH` two `List` collections.** Joining two bags (`List`s) multiplies the cartesian product and Hibernate refuses. Either make them `Set`s, or fetch one collection per query.

**Batch fetching keeps laziness but kills the round trips.** Set `spring.jpa.properties.hibernate.default_batch_fetch_size=100`. Now the N+1 path becomes 1 query for authors + `ceil(N/100)` queries using `WHERE author_id IN (?, ?, …)`. With 1000 authors and batch size 100, that's 11 queries instead of 1001 — and you keep lazy semantics everywhere. This is the lowest-effort global mitigation and a great default to set project-wide.

**Open-Session-In-View (OSIV) — an anti-pattern Boot enables by default.** Spring Boot ships with `spring.jpa.open-in-view=true`, which keeps the persistence context open for the *entire* HTTP request, including view/JSON rendering. It "fixes" `LazyInitializationException` by letting Jackson trigger lazy loads while serializing the response — which means **N+1 queries fire silently during JSON serialization, in your web layer, outside any service transaction**, and you'll never see them in your service-layer reasoning. It also holds a DB connection for the whole request. Senior teams set `spring.jpa.open-in-view=false` and fetch explicitly in the service layer (as we did). Turn it off, hit `LazyInitializationException`, and let it *force* you to declare your fetch plans.

**When to drop back to JdbcTemplate (Day 11).** ORMs are a productivity tool, not a religion. Drop to JDBC / jOOQ / native SQL when: you need a complex analytical query, window functions, vendor-specific SQL, or bulk operations (a single `UPDATE ... WHERE` over a million rows should be one SQL statement, **not** a million dirty-checked entities — JPA bulk update via `@Modifying @Query` or raw JDBC). A healthy codebase uses JPA for the CRUD object graph and JDBC/native for reporting and bulk. Knowing *when* to leave the ORM is a senior skill.

**Connection to indexes (Day 21).** Every `book WHERE author_id = ?` query — whether the N+1 flood or the batched `IN` — hits the `author_id` foreign-key column. If that column isn't indexed, each is a full table scan; the N+1 problem then compounds into N full scans. The fix to N+1 reduces the *number* of queries; an index on `author_id` makes each remaining query *fast*. On Day 21 you'll `EXPLAIN` exactly this and add the index.

**Second-level cache preview (Days 15/16).** The first-level cache we relied on today lives and dies with one transaction. Hibernate's **second-level cache** (e.g. backed by Ehcache or, in distributed form, Redis — Day 16) survives across transactions and sessions, so re-reading the same `Author` across requests skips the DB entirely. It's powerful but adds the hard problem of cache invalidation — the entire subject of Day 15.

---

### Stretch goals

1. **Prove batch fetching.** Set `spring.jpa.properties.hibernate.default_batch_fetch_size=2`, add a fourth service method that uses plain `findAll()` (lazy), and assert via Statistics that the count drops to `1 + ceil(5/2) = 4` instead of 6. Observe the `WHERE author_id IN (?, ?)` SQL.
2. **Reproduce the pagination trap.** Add `findAllWithBooksJoinFetch` overload that takes a `Pageable`, request page 0 size 2, and watch the `HHH000104` in-memory-pagination warning appear. Then fix it with the two-query (paged IDs, then `IN`) pattern.
3. **Trigger and then fix `LazyInitializationException`.** Set `spring.jpa.open-in-view=false`, return `Author` entities directly from the controller (let Jackson serialize them), watch it blow up on the lazy `books`, then fix it by returning a DTO. This viscerally demonstrates why OSIV exists and why you still shouldn't rely on it.
4. **Watch dirty checking work.** Add a `@Transactional` `renameAuthor(id, name)` that loads, calls `setName`, and *never* calls `save`. Assert via SQL log that an `UPDATE` still fires at commit. Then call the same setter with the *same* value and confirm Hibernate emits *no* UPDATE (it diffs against the snapshot).

---

### Day 13 teaser

Today you let Hibernate auto-generate the schema with `ddl-auto=create-drop` — fine for a throwaway demo, catastrophic in production (it can drop your tables, and there's no history of how the schema evolved). Tomorrow you replace that magic with **Flyway** and learn **schema migrations**: versioned, ordered, immutable SQL scripts (`V1__create_author.sql`, `V2__add_book.sql`) that run exactly once, tracked in a `flyway_schema_history` table — the disciplined, auditable way real teams evolve a database, including the index on `book.author_id` that this lesson's N+1 fixes are quietly crying out for.
