# 8. Spring & JPA

## Overview

JPA (Jakarta Persistence API) is a specification for mapping Java objects to relational database tables. Hibernate is the most popular implementation of that specification, and Spring Data JPA is a layer on top of Hibernate/JPA that removes boilerplate code. Together, these three form the backbone of almost every Spring Boot application that talks to a relational database. Interviewers love this topic because it separates developers who "just call `save()`" from developers who understand what actually happens underneath — how many SQL queries ran, when they ran, and why. Getting this wrong in production usually means one of two things: silent performance disasters (like the N+1 problem) or subtle data-corruption bugs (like lost updates from missing locking). This chapter walks through the full lifecycle of an entity, how Hibernate tracks and persists changes, and the performance traps that come up constantly in code review and interviews.

## JPA Fundamentals

JPA itself is **just an API** — a set of interfaces and annotations (`jakarta.persistence.*`). It does not do anything by itself. Think of JPA as a contract, and Hibernate as the company that fulfills the contract. Spring Data JPA then wraps Hibernate to give you repository interfaces where you barely have to write any code.

The three layers, from bottom to top:

| Layer | What it is | Example |
|---|---|---|
| JDBC | Raw Java-to-SQL API | `PreparedStatement`, `ResultSet` |
| JPA | Specification/annotations for ORM | `@Entity`, `EntityManager` |
| Hibernate | JPA implementation (the actual ORM engine) | `SessionFactory`, HQL |
| Spring Data JPA | Repository abstraction over JPA | `JpaRepository<T, ID>` |

An **ORM** (Object-Relational Mapper) is a tool that converts Java objects into rows in a table, and back again, so you don't have to write SQL by hand for basic CRUD.

A minimal entity and repository look like this:

```java
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;

@Entity
public class Book {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String title;
    private String author;

    protected Book() {
        // required by JPA
    }

    public Book(String title, String author) {
        this.title = title;
        this.author = author;
    }

    // getters and setters omitted for brevity
}
```

```java
import org.springframework.data.jpa.repository.JpaRepository;

public interface BookRepository extends JpaRepository<Book, Long> {
    // findAll(), findById(), save(), delete()... come for free
}
```

Key points:

- JPA requires every entity to have a **no-argument constructor** (can be `protected`) — the persistence provider uses reflection to create instances.
- `@Id` marks the primary key field; `@GeneratedValue` tells the database/Hibernate how to generate it (`IDENTITY`, `SEQUENCE`, `AUTO`, `TABLE`).
- Spring Boot auto-configures the `EntityManagerFactory`, `DataSource`, and transaction manager for you when you add the `spring-boot-starter-data-jpa` dependency.

## Entity Lifecycle

Every JPA entity moves through four states during its life. Understanding these states is the single most important mental model for this whole chapter — almost every bug (`LazyInitializationException`, duplicate inserts, "why didn't my update save?") traces back to a misunderstanding of which state an entity is in.

```
   new Book()
       │
       ▼
 ┌───────────┐   persist()/save()   ┌───────────┐
 │ TRANSIENT │ ────────────────────▶│  MANAGED  │
 │ (not in DB,│                     │ (tracked by│
 │ not tracked)│◀────────────────── │ persistence│
 └───────────┘   remove() (rare,    │  context)  │
       ▲          usually goes to   └─────┬──────┘
       │          REMOVED instead)        │
       │                                  │ detach() /
       │                                  │ close session /
       │                                  │ clear()
       │                                  ▼
       │                           ┌────────────┐
       │        merge()            │  DETACHED  │
       └───────────────────────────│ (has DB id, │
                                    │ not tracked)│
                                    └────────────┘

 MANAGED ──remove()──▶ ┌─────────┐ ──flush/commit──▶ DELETED from DB
                        │ REMOVED │
                        └─────────┘
```

| State | Meaning | Has DB row? | Tracked by Hibernate? |
|---|---|---|---|
| Transient | Freshly created with `new`, no id | No | No |
| Managed (persistent) | Attached to a persistence context | Yes (or will after flush) | Yes |
| Detached | Persistence context closed/cleared | Yes | No |
| Removed | Marked for deletion | Yes (until flush) | Yes, until deleted |

```java
Book book = new Book("Clean Code", "Robert Martin"); // TRANSIENT

entityManager.persist(book); // now MANAGED, id assigned (or assigned at flush)

book.setTitle("Clean Code (2nd ed.)"); // change tracked automatically

entityManager.detach(book); // now DETACHED, changes no longer tracked

Book merged = entityManager.merge(book); // merged copy is MANAGED again

entityManager.remove(merged); // now REMOVED, DELETE issued at flush/commit
```

- **Transient**: exists only in JVM memory. If the JVM crashes now, it's gone.
- **Managed**: any field change is automatically detected and eventually written to the DB (this is "dirty checking", covered later).
- **Detached**: still has a database identity but Hibernate is no longer watching it. Changing its fields does nothing to the database.
- **Removed**: scheduled for deletion; the actual `DELETE` SQL runs on flush.

## Persistence Context

The **persistence context** is Hibernate's in-memory cache of managed entities for the current unit of work — often called the "first-level cache". It is represented by the `EntityManager` (JPA) or `Session` (Hibernate). Think of it as a whiteboard: every entity you load or save gets written on the whiteboard, and Hibernate compares the whiteboard against the database when it's time to flush.

Key properties:

- It guarantees **identity**: if you load the same row twice in one persistence context, you get the *same Java object reference* back, not two copies.
- It is normally scoped to one transaction (`@Transactional` method) in Spring Boot.
- It is **not shared** across HTTP requests unless you use the "open session in view" pattern (a pitfall, discussed later).

```java
@Transactional
public void demoIdentity(Long id) {
    Book first = entityManager.find(Book.class, id);
    Book second = entityManager.find(Book.class, id);

    System.out.println(first == second); // true — same object, only one SELECT fired
}
```

```java
@Service
@RequiredArgsConstructor
public class BookService {

    private final BookRepository bookRepository;

    @Transactional
    public void renameBook(Long id, String newTitle) {
        Book book = bookRepository.findById(id).orElseThrow();
        book.setTitle(newTitle);
        // no explicit save() call needed — the persistence context
        // detects the change and flushes it automatically on commit
    }
}
```

- The persistence context is cleared when the transaction ends — that's when entities become detached.
- Calling `entityManager.clear()` detaches everything in the context; `entityManager.flush()` pushes pending SQL to the DB without ending the transaction.

## Hibernate Basics

Hibernate is the ORM engine that actually implements JPA. Spring Boot wires it up for you, but it helps to know the core pieces so error messages make sense.

| Concept | JPA name | Hibernate-native name |
|---|---|---|
| Factory for sessions | `EntityManagerFactory` | `SessionFactory` |
| Unit of work | `EntityManager` | `Session` |
| Query language | JPQL | HQL (superset of JPQL) |
| Query object | `TypedQuery` | `Query` |

A basic `application.yml` configuration:

```yaml
spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/bookstore
    username: app
    password: secret
  jpa:
    hibernate:
      ddl-auto: validate   # never "update" in production
    show-sql: true
    properties:
      hibernate:
        format_sql: true
        jdbc:
          batch_size: 20
```

- `ddl-auto` controls schema generation: `none`, `validate`, `update`, `create`, `create-drop`. Use `validate` or `none` in production and let a migration tool (Flyway/Liquibase) own the schema.
- `show-sql` / `format_sql` are invaluable for debugging N+1 issues and understanding what Hibernate actually sends to the database.
- Hibernate also supports its own query language (HQL) and a fluent Criteria API for type-safe dynamic queries:

```java
public interface BookRepository extends JpaRepository<Book, Long> {

    @Query("select b from Book b where b.author = :author")
    List<Book> findByAuthorHql(@Param("author") String author);
}
```

## Entity Mapping

Entity mapping is the set of annotations that tell Hibernate how a Java class corresponds to a database table.

```java
import jakarta.persistence.*;
import java.time.LocalDate;

@Entity
@Table(name = "books", indexes = @Index(name = "idx_book_isbn", columnList = "isbn"))
public class Book {

    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "book_seq")
    @SequenceGenerator(name = "book_seq", sequenceName = "book_id_seq", allocationSize = 50)
    private Long id;

    @Column(name = "title", nullable = false, length = 255)
    private String title;

    @Column(unique = true)
    private String isbn;

    @Enumerated(EnumType.STRING)
    private BookStatus status;

    @Temporal(TemporalType.DATE) // only needed for legacy java.util.Date
    private LocalDate publishedDate; // java.time types don't need @Temporal

    @Transient
    private String cachedDisplayLabel; // never persisted, computed at runtime

    @Lob
    private byte[] coverImage;
}
```

- `@Table` lets you override the table name and define indexes/constraints.
- `@Column` controls nullability, length, uniqueness, and custom column names.
- `@Enumerated(EnumType.STRING)` is strongly preferred over `EnumType.ORDINAL` — reordering enum constants silently corrupts `ORDINAL` data.
- `@Transient` (JPA) excludes a field from persistence entirely — not to be confused with the "transient" *lifecycle state*.
- `GenerationType.SEQUENCE` with a batched `allocationSize` scales much better than `IDENTITY` because Hibernate can grab a block of IDs in memory instead of round-tripping to the DB for every insert.

## Relationships

JPA maps foreign keys using relationship annotations. The four kinds:

| Annotation | Meaning | Example |
|---|---|---|
| `@OneToOne` | One row relates to exactly one row | `User` ↔ `UserProfile` |
| `@OneToMany` | One row relates to many rows | `Author` → many `Book`s |
| `@ManyToOne` | Many rows relate to one row | `Book` → one `Author` |
| `@ManyToMany` | Many rows relate to many rows | `Student` ↔ `Course` |

```java
@Entity
public class Author {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String name;

    @OneToMany(mappedBy = "author", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Book> books = new ArrayList<>();
}
```

```java
@Entity
public class Book {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String title;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "author_id")
    private Author author;
}
```

- The side with `@JoinColumn` owns the foreign key column — that's the **owning side**.
- The side with `mappedBy` is the **inverse side**; it doesn't create a column, it just describes the relationship from the other direction.
- For `@ManyToMany`, use a `@JoinTable`:

```java
@Entity
public class Student {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToMany
    @JoinTable(
        name = "student_course",
        joinColumns = @JoinColumn(name = "student_id"),
        inverseJoinColumns = @JoinColumn(name = "course_id"))
    private Set<Course> courses = new HashSet<>();
}
```

- Prefer `Set` over `List` for `@ManyToMany`/`@OneToMany` collections when order doesn't matter — it avoids Hibernate's occasionally expensive "remove and re-insert everything" behavior for `List` with certain cascade operations.
- Always add a helper method to keep both sides of a bidirectional relationship in sync (see Pitfalls section).

## Cascade Types

Cascading means: "when I do X to the parent, automatically do X to the children too." Without cascading, you'd have to manually persist/remove every related entity yourself.

| Cascade type | Effect |
|---|---|
| `PERSIST` | Saving the parent also saves new children |
| `MERGE` | Merging the parent also merges children |
| `REMOVE` | Deleting the parent also deletes children |
| `REFRESH` | Refreshing the parent also refreshes children |
| `DETACH` | Detaching the parent also detaches children |
| `ALL` | All of the above |

```java
@Entity
public class Order {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @OneToMany(mappedBy = "order", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<OrderLine> lines = new ArrayList<>();

    public void addLine(OrderLine line) {
        lines.add(line);
        line.setOrder(this);
    }
}
```

```java
Order order = new Order();
order.addLine(new OrderLine("Widget", 3));
orderRepository.save(order); // cascades PERSIST to the OrderLine automatically
```

- `orphanRemoval = true` is different from `CascadeType.REMOVE`: it deletes a child when it's *removed from the collection*, even if the parent itself is never deleted.
- Only use `CascadeType.REMOVE` / `ALL` on relationships that represent true ownership (e.g., `Order` owns `OrderLine`s). Never cascade `REMOVE` from `Book` to `Author` — deleting a book should not delete the author.

## Fetch Types

Fetch type controls **when** related data is loaded from the database.

| Fetch type | Behavior | JPA default for |
|---|---|---|
| `EAGER` | Loaded immediately, together with the owning entity | `@ManyToOne`, `@OneToOne` |
| `LAZY` | Loaded only when the association is first accessed | `@OneToMany`, `@ManyToMany` |

```java
@ManyToOne(fetch = FetchType.LAZY) // explicitly override the EAGER default
@JoinColumn(name = "author_id")
private Author author;

@OneToMany(mappedBy = "author", fetch = FetchType.LAZY)
private List<Book> books;
```

- The JPA spec defaults `@ManyToOne` and `@OneToOne` to `EAGER`. This surprises almost everyone and is a classic interview trick question — see the Pitfalls section.
- `LAZY` is almost always the safer default for every association; make it explicit rather than relying on spec defaults.

## Lazy Loading

Lazy loading means the related entity/collection is **not fetched** until you actually call a getter on it. Hibernate achieves this with a runtime-generated proxy object (or, since Hibernate 6, bytecode enhancement) that looks like the real entity but only fires a `SELECT` the first time you touch it.

```java
@Transactional
public void printAuthorName(Long bookId) {
    Book book = bookRepository.findById(bookId).orElseThrow(); // SELECT * FROM books
    Author author = book.getAuthor();       // no SQL yet — it's a proxy
    System.out.println(author.getName());   // SELECT * FROM authors WHERE id = ? fires now
}
```

- If the above method were **not** annotated `@Transactional` and the persistence context had already closed, calling `author.getName()` would throw `LazyInitializationException`.
- Lazy collections behave the same way: `book.getReviews()` returns a proxy collection; iterating it triggers the query.

Analogy: a lazy association is like a sealed envelope. You know it's there, but nothing is read out of it until you actually open it — and you can only open it while the "office" (persistence context/transaction) is still open.

## Eager Loading

Eager loading fetches the association immediately, in the same query (via a JOIN) or in an immediate follow-up query, whether you need it or not.

```java
@Entity
public class Book {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @OneToOne(fetch = FetchType.EAGER)
    @JoinColumn(name = "metadata_id")
    private BookMetadata metadata; // always loaded whenever a Book is loaded
}
```

```sql
-- Hibernate typically generates a JOIN for EAGER associations
SELECT b.*, m.*
FROM books b
LEFT JOIN book_metadata m ON m.id = b.metadata_id
WHERE b.id = ?;
```

- Eager loading avoids `LazyInitializationException` but at a real cost: every time you load a `Book` — even in a list of 1000 — you also drag in its metadata, whether you need it or not.
- Eager associations compound: eager-loading an association that itself has eager associations can silently blow up your query into a huge join, or trigger cascading extra `SELECT`s.
- Rule of thumb: default everything to `LAZY`, and eager-load specific things on a per-query basis with `JOIN FETCH` or `@EntityGraph` when you actually need them.

## N+1 Problem

The N+1 problem is the single most common Hibernate performance bug. It happens when you load a list of N parent entities, and then lazily access an association on each one — resulting in 1 query for the parents plus N additional queries, one per parent.

**The bad case:**

```java
List<Author> authors = authorRepository.findAll(); // 1 query
for (Author author : authors) {
    System.out.println(author.getBooks().size()); // 1 query PER author!
}
```

```sql
-- 1 query for the parents
SELECT * FROM authors;

-- then N queries, one per author (this is the problem)
SELECT * FROM books WHERE author_id = 1;
SELECT * FROM books WHERE author_id = 2;
SELECT * FROM books WHERE author_id = 3;
-- ... repeated N times
```

With 100 authors, that's 101 round trips to the database instead of 1 or 2.

**Fix 1 — `JOIN FETCH`:** pull everything in a single query using JPQL.

```java
@Query("select distinct a from Author a left join fetch a.books")
List<Author> findAllWithBooks();
```

```sql
SELECT a.*, b.*
FROM authors a
LEFT JOIN books b ON b.author_id = a.id;
-- one single query, no matter how many authors
```

**Fix 2 — `@EntityGraph`:** declaratively tell Spring Data which associations to fetch eagerly, without hand-writing JPQL.

```java
public interface AuthorRepository extends JpaRepository<Author, Long> {

    @EntityGraph(attributePaths = "books")
    List<Author> findAll();
}
```

```sql
SELECT a.*, b.*
FROM authors a
LEFT JOIN books b ON b.author_id = a.id;
-- Spring Data generates the same kind of join under the hood
```

**Fix 3 — `@BatchSize`:** instead of one SELECT per parent, Hibernate batches several parents' lazy loads into one `IN (...)` query.

```java
@Entity
public class Author {

    @OneToMany(mappedBy = "author")
    @org.hibernate.annotations.BatchSize(size = 25)
    private List<Book> books;
}
```

```sql
-- with 100 authors and a batch size of 25, you get 4 queries instead of 100
SELECT * FROM books WHERE author_id IN (1,2,3,...,25);
SELECT * FROM books WHERE author_id IN (26,27,...,50);
-- and so on
```

**Fix 4 — DTO projection:** skip entities entirely and select only the columns you need. This avoids both the N+1 problem and the overhead of loading full entity graphs.

```java
public record AuthorBookCount(String authorName, long bookCount) {}
```

```java
@Query("""
    select new com.example.bookstore.AuthorBookCount(a.name, count(b))
    from Author a left join a.books b
    group by a.name
    """)
List<AuthorBookCount> findAuthorBookCounts();
```

```sql
SELECT a.name, COUNT(b.id)
FROM authors a
LEFT JOIN books b ON b.author_id = a.id
GROUP BY a.name;
```

| Fix | Best for | Trade-off |
|---|---|---|
| `JOIN FETCH` | One specific query, one association | Can't paginate collections cleanly (memory blow-up risk) |
| `@EntityGraph` | Repository-method-level, reusable, declarative | Same join/pagination caveats as `JOIN FETCH` |
| `@BatchSize` | General-purpose safety net, works everywhere | Still N/batchSize queries, not 1 |
| DTO projection | Read-only views, reports, list screens | You lose the managed entity (no lazy nav, no dirty checking) |

## Entity Graphs

`@EntityGraph` is JPA's declarative way to specify "when you load this entity, also load these specific associations" — without writing custom JPQL for every variation.

```java
@Entity
@NamedEntityGraph(
    name = "Author.withBooks",
    attributeNodes = @NamedAttributeNode("books")
)
public class Author {
    // ...
}
```

```java
public interface AuthorRepository extends JpaRepository<Author, Long> {

    @EntityGraph(value = "Author.withBooks", type = EntityGraph.EntityGraphType.LOAD)
    Optional<Author> findById(Long id);
}
```

- `EntityGraphType.FETCH` overrides fetch types for named attributes and treats everything else as `LAZY`.
- `EntityGraphType.LOAD` keeps the default fetch type for everything else, and only forces the specified attributes to `EAGER` for this query.
- Ad-hoc entity graphs (no `@NamedEntityGraph` needed) can also be defined directly on the repository method:

```java
@EntityGraph(attributePaths = {"books", "books.reviews"})
List<Author> findByNameContaining(String namePart);
```

- Entity graphs are best thought of as a cleaner alternative to writing many slightly different `JOIN FETCH` queries — one per screen/use case, instead of one giant eager-loaded entity.

## Transactions

A transaction is a unit of work that either fully succeeds (commit) or fully fails (rollback) — no partial results. Spring manages this declaratively with `@Transactional`, backed by the `PlatformTransactionManager`.

```java
@Service
@RequiredArgsConstructor
public class TransferService {

    private final AccountRepository accountRepository;

    @Transactional
    public void transfer(Long fromId, Long toId, BigDecimal amount) {
        Account from = accountRepository.findById(fromId).orElseThrow();
        Account to = accountRepository.findById(toId).orElseThrow();

        from.withdraw(amount);
        to.deposit(amount);
        // if withdraw() throws (e.g. insufficient funds), BOTH changes roll back
    }
}
```

Key details:

- `@Transactional` is implemented with an AOP proxy. Calling a `@Transactional` method **from within the same class** (self-invocation) bypasses the proxy and the transaction never starts.
- By default, transactions roll back on unchecked exceptions (`RuntimeException`) and not on checked exceptions — configurable via `rollbackFor`.
- `@Transactional(readOnly = true)` is a hint that lets Hibernate skip dirty checking for that transaction, improving performance for read-only use cases.
- Propagation controls how nested `@Transactional` calls behave:

| Propagation | Meaning |
|---|---|
| `REQUIRED` (default) | Join the existing transaction, or start a new one |
| `REQUIRES_NEW` | Always start a brand-new transaction, suspending any existing one |
| `NESTED` | Start a savepoint inside the existing transaction |
| `SUPPORTS` | Join if one exists, otherwise run without a transaction |

```java
@Transactional(readOnly = true)
public List<Book> listBooks() {
    return bookRepository.findAll();
}
```

## Dirty Checking

Dirty checking is how Hibernate figures out what changed on a managed entity, without you calling `save()` explicitly. At flush time, Hibernate compares each managed entity's current field values against a snapshot it took when the entity was first loaded, and generates `UPDATE` statements only for the entities/fields that actually changed.

```java
@Transactional
public void markBookAsSoldOut(Long bookId) {
    Book book = bookRepository.findById(bookId).orElseThrow(); // snapshot taken here
    book.setStatus(BookStatus.SOLD_OUT);
    // no explicit save()/update() call needed!
    // Hibernate detects the change and issues an UPDATE at commit time
}
```

- Dirty checking only works on **managed** entities inside an active persistence context. It does nothing for detached entities.
- This is why calling `repository.save(entity)` on an already-managed entity is a harmless no-op in terms of extra SQL — dirty checking would have written the update anyway.
- `@DynamicUpdate` on an entity tells Hibernate to only include *changed* columns in the `UPDATE` statement (instead of all columns), which can help with very wide tables or optimistic locking edge cases.

```java
@Entity
@org.hibernate.annotations.DynamicUpdate
public class Book {
    // only changed columns appear in the generated UPDATE
}
```

## Optimistic Locking

Optimistic locking assumes conflicts are rare, so it doesn't lock any rows up front. Instead, it detects a conflict at update time using a **version column**. Think of it like a Wikipedia edit conflict: you and someone else both open the same page, but whoever saves last gets told "someone else already changed this, please refresh."

```java
@Entity
public class Book {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String title;
    private Integer stock;

    @Version
    private Long version;
}
```

```sql
UPDATE books
SET title = ?, stock = ?, version = version + 1
WHERE id = ? AND version = ?;
-- if 0 rows are affected, Hibernate throws OptimisticLockException
```

```java
@Transactional
public void decrementStock(Long bookId) {
    Book book = bookRepository.findById(bookId).orElseThrow();
    book.setStock(book.getStock() - 1);
    // if another transaction updated this row (and its version) first,
    // this commit throws OptimisticLockException / ObjectOptimisticLockingFailureException
}
```

- The `@Version` field is automatically incremented by Hibernate; you should never set it manually.
- Handle the exception at the service/controller layer (e.g., retry, or return a 409 Conflict to the client).
- Optimistic locking scales well because it never blocks other transactions — the cost is paid only when a real conflict happens.

## Pessimistic Locking

Pessimistic locking assumes conflicts are likely, so it locks the row immediately using a database-level lock (`SELECT ... FOR UPDATE`), blocking other transactions from modifying (or sometimes even reading) that row until the lock is released.

```java
public interface BookRepository extends JpaRepository<Book, Long> {

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select b from Book b where b.id = :id")
    Optional<Book> findByIdForUpdate(@Param("id") Long id);
}
```

```sql
SELECT * FROM books WHERE id = ? FOR UPDATE;
-- other transactions trying to lock/update this row will block until this transaction commits
```

```java
@Transactional
public void reserveLastCopy(Long bookId) {
    Book book = bookRepository.findByIdForUpdate(bookId).orElseThrow();
    if (book.getStock() > 0) {
        book.setStock(book.getStock() - 1);
    }
    // row stays locked for the duration of this transaction
}
```

| Lock mode | Effect |
|---|---|
| `PESSIMISTIC_READ` | Blocks others from writing, allows other reads |
| `PESSIMISTIC_WRITE` | Blocks others from reading-for-update or writing |
| `PESSIMISTIC_FORCE_INCREMENT` | Write lock plus forces a version bump |

| | Optimistic | Pessimistic |
|---|---|---|
| When it detects conflicts | At commit time | Immediately, on read |
| Performance under low contention | Excellent | Wasted overhead |
| Performance under high contention | Many retries/failures | Predictable, but causes blocking |
| Requires | `@Version` column | DB-level row lock support |
| Good for | Web apps, typical CRUD | Financial transactions, inventory counters, "hot row" operations |

## Caching

Hibernate has two main levels of caching, plus Spring's own general-purpose caching abstraction.

| Cache level | Scope | Enabled by default? |
|---|---|---|
| First-level cache (persistence context) | Single transaction/session | Yes, always on |
| Second-level cache | Shared across sessions, whole application | No, opt-in |
| Query cache | Caches query result sets (works with 2nd-level cache) | No, opt-in |

The **first-level cache** is just the persistence context described earlier — no configuration needed.

The **second-level cache** requires a caching provider (e.g., Ehcache, Caffeine, Hazelcast) plus explicit opt-in per entity:

```properties
spring.jpa.properties.hibernate.cache.use_second_level_cache=true
spring.jpa.properties.hibernate.cache.region.factory_class=org.hibernate.cache.jcache.JCacheRegionFactory
```

```java
import jakarta.persistence.Cacheable;
import org.hibernate.annotations.Cache;
import org.hibernate.annotations.CacheConcurrencyStrategy;

@Entity
@Cacheable
@Cache(usage = CacheConcurrencyStrategy.READ_WRITE)
public class Genre {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String name; // rarely-changing reference data — a great cache candidate
}
```

Separately, Spring's own generic caching abstraction (`@Cacheable`, `@CacheEvict`) can wrap service methods, independent of Hibernate:

```java
@Service
@RequiredArgsConstructor
public class GenreService {

    private final GenreRepository genreRepository;

    @org.springframework.cache.annotation.Cacheable("genres")
    public List<Genre> findAllGenres() {
        return genreRepository.findAll();
    }
}
```

- Second-level cache is best for read-heavy, rarely-changing reference data (categories, countries, settings) — not for frequently updated entities.
- Caching entities that change often can introduce stale-data bugs across a multi-instance deployment unless the cache is properly invalidated/distributed.

## Batch Operations

Batching means grouping multiple SQL statements into fewer round trips to the database, instead of sending one statement per row.

```properties
spring.jpa.properties.hibernate.jdbc.batch_size=25
spring.jpa.properties.hibernate.order_inserts=true
spring.jpa.properties.hibernate.order_updates=true
```

```java
@Transactional
public void importBooks(List<Book> books) {
    int batchSize = 25;
    for (int i = 0; i < books.size(); i++) {
        entityManager.persist(books.get(i));
        if (i % batchSize == 0 && i > 0) {
            entityManager.flush(); // send the batch to the DB
            entityManager.clear(); // free memory, avoid huge persistence context
        }
    }
}
```

```sql
-- with batching enabled, Hibernate groups statements like this at the JDBC driver level
INSERT INTO books (title, author_id) VALUES (?, ?);
INSERT INTO books (title, author_id) VALUES (?, ?);
-- sent together as one batch instead of one round trip each
```

- `GenerationType.IDENTITY` **disables** JDBC batching for inserts, because Hibernate needs the generated ID back from the DB before it can process the next entity. Use `SEQUENCE` if bulk inserts matter to you.
- Spring Data JPA's `saveAll()` uses the same batching machinery under the hood, as long as batch size is configured and the ID strategy allows it.
- For very large bulk updates/deletes, a single bulk JPQL statement avoids loading entities into memory at all:

```java
@Modifying
@Query("update Book b set b.status = :status where b.publishedDate < :cutoff")
int markOldBooksArchived(@Param("status") BookStatus status, @Param("cutoff") LocalDate cutoff);
```

```sql
UPDATE books SET status = ? WHERE published_date < ?;
-- one statement, no entities loaded, but bypasses the persistence context
-- (already-loaded entities in memory will NOT reflect this change)
```

## Common Code Review / Interview Pitfalls

- **Using mutable fields (like `id`) in `equals()`/`hashCode()`.** An entity's `id` is `null` before it's persisted, so two transient entities become "equal" to everything and nothing consistently, breaking `HashSet`/`HashMap` behavior across the lifecycle. Fix: base equality on a stable business key, or use the id only with a fallback, and never regenerate `hashCode()` from mutable fields.

  ```java
  // ❌ bad — id is null before insert, and changes identity semantics as it's assigned
  @Override
  public boolean equals(Object o) {
      if (!(o instanceof Book other)) return false;
      return Objects.equals(id, other.id);
  }
  @Override
  public int hashCode() {
      return Objects.hash(id); // hashCode changes once id is assigned!
  }
  ```

  ```java
  // ✅ good — stable identity for all lifecycle states, uses a business key or a fixed type-based hash
  @Override
  public boolean equals(Object o) {
      if (this == o) return true;
      if (!(o instanceof Book other)) return false;
      return isbn != null && isbn.equals(other.isbn);
  }
  @Override
  public int hashCode() {
      return getClass().hashCode(); // constant — safe across the whole lifecycle
  }
  ```

- **`LazyInitializationException` from accessing a lazy association after the session closed.** This happens when code touches `book.getAuthor().getName()` outside of an active transaction/persistence context. Fix: fetch what you need inside the `@Transactional` boundary, use `JOIN FETCH`/`@EntityGraph`, or return DTOs from the service layer instead of raw entities.

- **Relying on Open Session In View (OSIV) to paper over lazy-loading issues.** Spring Boot enables `spring.jpa.open-in-view=true` by default, which keeps the persistence context open for the entire HTTP request, letting lazy loading happen in the view/controller layer. This hides N+1 problems, ties up a DB connection for the whole request (including template rendering/serialization), and makes performance issues invisible until load testing. Fix: set `spring.jpa.open-in-view=false` explicitly and fetch everything needed inside the service layer's transaction.

  ```properties
  # ✅ good — force yourself to load what you need where you control the query
  spring.jpa.open-in-view=false
  ```

- **Forgetting to keep both sides of a bidirectional relationship in sync.** If you only add a `Book` to `author.getBooks()` without also setting `book.setAuthor(author)`, the in-memory object graph is inconsistent with the database until the next reload. Fix: add a helper method on the owning side that updates both directions.

  ```java
  // ❌ bad — only one side updated, book.getAuthor() is still null in memory
  author.getBooks().add(book);
  ```

  ```java
  // ✅ good — helper method keeps both sides consistent
  public void addBook(Book book) {
      books.add(book);
      book.setAuthor(this);
  }
  ```

- **`CascadeType.REMOVE` (or `ALL`) on a relationship that isn't true ownership.** Cascading removal from `Book` to `Author` would delete an author just because one of their books was deleted — clearly wrong, since an author can have other books. Fix: only cascade `REMOVE` when the child truly cannot exist without the parent (e.g., `Order` → `OrderLine`).

- **Not knowing that `@ManyToOne` and `@OneToOne` default to `FetchType.EAGER`.** Many developers assume every association is lazy by default; in reality only `@OneToMany`/`@ManyToMany` are. Leaving `@ManyToOne` at its default can silently drag in extra joins on every query. Fix: always specify `fetch = FetchType.LAZY` explicitly on `@ManyToOne`/`@OneToOne`.

  ```java
  // ❌ bad — silently EAGER, always joins Author even when unneeded
  @ManyToOne
  private Author author;
  ```

  ```java
  // ✅ good — explicit, predictable
  @ManyToOne(fetch = FetchType.LAZY)
  private Author author;
  ```

- **Ignoring the N+1 problem because "it works on my machine."** With small local datasets, an N+1 query pattern is invisible; in production with thousands of rows it becomes hundreds of extra round trips. Fix: enable SQL logging in development, use tools like Hibernate's statistics or a query counter in tests, and default to `JOIN FETCH`/`@EntityGraph`/`@BatchSize` for known hot paths.

- **Calling `save()` unnecessarily inside a `@Transactional` method on an already-managed entity.** It's not incorrect, but it signals a misunderstanding of dirty checking and adds noise/confusion in code review. Fix: rely on dirty checking for managed entities; only call `save()` for genuinely new/detached entities.

- **Self-invocation of `@Transactional` methods.** Calling a `@Transactional` method from another method in the *same class* bypasses the Spring AOP proxy entirely, so no transaction is created. Fix: move the method to a separate bean, or inject a self-reference proxy if you must call it internally.

  ```java
  // ❌ bad — internal call bypasses the proxy, @Transactional on doWork() has no effect
  @Service
  public class ReportService {
      public void run() {
          doWork(); // plain Java call, not intercepted!
      }
      @Transactional
      public void doWork() { /* ... */ }
  }
  ```

- **Using `List` with `@OrderColumn` or complex reordering on large `@OneToMany` collections.** Certain collection mutations force Hibernate to delete and re-insert every row to keep order columns consistent, causing surprising numbers of `DELETE`/`INSERT` statements for a single removal. Fix: prefer `Set` when order doesn't matter, or model ordering with an explicit sortable field instead of `@OrderColumn` where write performance matters.

- **Missing `@Version` on entities that are updated concurrently, then "fixing" races with manual locking everywhere.** Without optimistic locking, concurrent updates silently overwrite each other (a "lost update"), and developers sometimes compensate with ad-hoc synchronization that doesn't work across multiple app instances. Fix: add a `@Version` field and let Hibernate handle conflict detection.

- **Using `ddl-auto: update` (or worse, `create-drop`) in production.** It can silently alter or drop production schema/data in ways nobody reviewed, unlike a checked-in migration script. Fix: use `validate` or `none` in production, and manage schema changes with Flyway or Liquibase.

- **Fetching entire entity graphs just to render a summary list/DTO.** Loading full `Book` entities (with all lazy associations poking through templating engines) for a simple "list of titles" screen wastes memory and triggers avoidable extra queries. Fix: use interface-based or record-based DTO projections for read-only views.

- **Assuming `@Transactional(readOnly = true)` prevents writes at the database level.** It's a hint to Hibernate/the JDBC driver for optimization (e.g., skipping dirty checking, allowing `READ ONLY` transaction mode on the connection) — it is not a hard security guarantee against writes in all setups. Fix: don't rely on it as an access-control mechanism; use it purely as a performance hint, and enforce write protection at the database-user/permissions level if that's the real goal.

## Quick Recap

- JPA is a specification; Hibernate is the implementation; Spring Data JPA removes repository boilerplate on top.
- Entities move through four lifecycle states: **transient → managed → detached**, with **removed** as a special managed sub-state before deletion.
- The **persistence context** is Hibernate's per-transaction cache that guarantees object identity and powers automatic dirty checking.
- `@ManyToOne` and `@OneToOne` default to `EAGER`; `@OneToMany` and `@ManyToMany` default to `LAZY` — override these defaults explicitly.
- The **N+1 problem** is the most common JPA performance bug; fix it with `JOIN FETCH`, `@EntityGraph`, `@BatchSize`, or DTO projections depending on the situation.
- Use **cascades** only for true parent-child ownership; never cascade `REMOVE` across unrelated entities like `Book` → `Author`.
- **Optimistic locking** (`@Version`) scales well and is the default choice; **pessimistic locking** (`SELECT ... FOR UPDATE`) is for high-contention hot rows.
- Turn off `spring.jpa.open-in-view` and fetch what you need inside the service layer to avoid hidden lazy-loading traps.
- Never base `equals()`/`hashCode()` on a mutable, nullable field like `id`.
- Use `ddl-auto: validate`/`none` in production; let Flyway/Liquibase own schema changes.
- Batch inserts require `GenerationType.SEQUENCE` (not `IDENTITY`) plus `hibernate.jdbc.batch_size` to actually batch at the JDBC level.
- Second-level caching is for rarely-changing reference data, not hot, frequently-updated rows.
