# 7. Spring Data

## Overview

Spring Data is the part of the Spring ecosystem that removes boilerplate from data access code. Instead of writing repetitive DAO classes full of `EntityManager` calls, you declare an interface and Spring Data generates the implementation for you at runtime. It works across many storage technologies (relational databases via JPA, MongoDB, Redis, Elasticsearch, and more) using the same core programming model: repositories. This chapter focuses mainly on **Spring Data JPA**, the module used for relational databases, because it is the one interviewers ask about most. Understanding it well means understanding how Spring turns an interface into working SQL, how to write custom queries safely, and how to page, sort, and audit data correctly.

## Spring Data Overview

Spring Data is an umbrella project, not a single library. Each storage technology gets its own module (`spring-data-jpa`, `spring-data-mongodb`, `spring-data-redis`, `spring-data-elasticsearch`, ...), but they all share the same central idea: **you write an interface, Spring Data writes the implementation.**

Think of it like ordering food by pointing at a picture on a menu instead of writing out a recipe. You describe *what* you want (`findByEmail`), and the kitchen (Spring Data) figures out *how* to make it (the SQL).

Key building blocks you will meet in this chapter:

- **Repository interfaces** — the contract you write (e.g., `UserRepository`).
- **Proxy implementation** — Spring Data creates a dynamic proxy at startup that implements your interface.
- **Query derivation** — method names like `findByLastName` are parsed into queries automatically.
- **`@Query`** — an escape hatch for when method-name derivation is not enough.

```java
// This is ALL the code you need to write for basic CRUD on a User entity.
public interface UserRepository extends JpaRepository<User, Long> {
    Optional<User> findByEmail(String email);
}
```

Spring Boot auto-configures everything: it scans for repository interfaces, creates proxy beans for them, and wires in an `EntityManager` behind the scenes. You never call `new UserRepositoryImpl()` — Spring does it for you.

| Term | Plain-English meaning |
|---|---|
| Repository | An interface describing operations on one entity type (find, save, delete...) |
| Proxy | An auto-generated object that implements your interface at runtime |
| Derived query | A query built automatically by parsing the method name |
| `@Query` | A manually written query attached to a repository method |
| Entity | A Java class mapped to a database table (`@Entity`) |

## Repository Pattern

The **Repository Pattern** is a design pattern (not a Spring-specific idea) that hides data-access logic behind a collection-like interface. Instead of your service code knowing about SQL, JDBC, or JPA, it just calls methods like `save()`, `findById()`, or `deleteAll()` — as if the database were an in-memory `List` or `Map`.

Why this matters:

- It **decouples** business logic from persistence technology. You could swap JPA for MongoDB and your service layer barely changes.
- It makes **testing** easier — you can mock the repository interface without touching a real database.
- It centralizes data-access rules in one place.

Spring Data takes this pattern and automates the *implementation* step, which is usually the tedious part.

```java
public interface AccountRepository extends Repository<Account, Long> {
    Optional<Account> findById(Long id);
    Account save(Account account);
}
```

- `Repository<T, ID>` is the **root marker interface** in Spring Data — it has no methods at all, it just tells Spring "this is a repository."
- Almost nobody extends `Repository` directly; you normally extend a richer interface like `CrudRepository` or `JpaRepository` that already has useful methods.

```
Repository<T, ID>                     <- empty marker interface
   └── CrudRepository<T, ID>          <- adds save/find/delete
         └── ListCrudRepository<T,ID> <- like CrudRepository but returns List instead of Iterable
   └── PagingAndSortingRepository<T,ID>     <- adds paging & sorting
         └── ListPagingAndSortingRepository<T,ID>
   └── JpaRepository<T, ID>           <- JPA-specific, extends the above + batch ops
```

## CrudRepository

`CrudRepository<T, ID>` is the first "useful" interface in the hierarchy. CRUD stands for **C**reate, **R**ead, **U**pdate, **D**elete — the four basic database operations. It gives you these operations for free, without writing a single line of implementation.

```java
public interface ProductRepository extends CrudRepository<Product, Long> {
}
```

```java
@Service
public class ProductService {

    private final ProductRepository productRepository;

    public ProductService(ProductRepository productRepository) {
        this.productRepository = productRepository;
    }

    public Product create(Product product) {
        return productRepository.save(product);           // INSERT or UPDATE
    }

    public Optional<Product> findOne(Long id) {
        return productRepository.findById(id);             // SELECT ... WHERE id = ?
    }

    public Iterable<Product> findAll() {
        return productRepository.findAll();                // SELECT * FROM product
    }

    public void remove(Long id) {
        productRepository.deleteById(id);                  // DELETE ... WHERE id = ?
    }
}
```

Common methods provided by `CrudRepository`:

| Method | Purpose |
|---|---|
| `save(entity)` | Insert or update (decides based on the ID) |
| `saveAll(entities)` | Bulk insert/update |
| `findById(id)` | Returns `Optional<T>` |
| `existsById(id)` | Returns `boolean` |
| `findAll()` | Returns `Iterable<T>` (all rows — be careful on big tables!) |
| `count()` | Returns row count |
| `deleteById(id)` | Delete by primary key |
| `delete(entity)` | Delete a specific entity |
| `deleteAll()` | Delete everything (dangerous in production code) |

Note: `CrudRepository.findAll()` returns `Iterable<T>`, which is mildly annoying since you usually want a `List`. This is exactly why Spring Data 3.x added `ListCrudRepository`.

## PagingAndSortingRepository

`PagingAndSortingRepository<T, ID>` adds the ability to fetch data **one page at a time** and in a **specific order**, instead of pulling the whole table into memory.

Analogy: instead of asking a librarian for "every book in the library," you ask for "the third shelf's worth of books, sorted by title."

```java
public interface OrderRepository extends PagingAndSortingRepository<Order, Long> {
}
```

```java
Pageable pageable = PageRequest.of(0, 20, Sort.by("createdAt").descending());
Page<Order> page = orderRepository.findAll(pageable);

System.out.println(page.getTotalElements()); // total rows matching the query
System.out.println(page.getTotalPages());    // total pages
System.out.println(page.getContent());       // the actual List<Order> for this page
```

Key points:

- `PagingAndSortingRepository` on its own does **not** include `save`/`delete` — in practice you almost never extend it alone; you use `JpaRepository`, which combines everything.
- Since Spring Data 3.x, `findAll()` here also returns `Iterable`, unless you use `ListPagingAndSortingRepository`, which returns `List` for the non-paged `findAll()`.

## JpaRepository

`JpaRepository<T, ID>` is the interface you will use in almost every real project. It extends both `PagingAndSortingRepository` and `QueryByExampleExecutor`, and adds JPA-specific extras like batch operations and flushing.

```java
public interface CustomerRepository extends JpaRepository<Customer, Long> {

    List<Customer> findByLastName(String lastName);  // derived query, returns List directly
}
```

Why `JpaRepository` instead of `CrudRepository` almost everywhere:

| Feature | `CrudRepository` | `PagingAndSortingRepository` | `JpaRepository` |
|---|---|---|---|
| Basic CRUD | Yes | Yes (inherited) | Yes (inherited) |
| Paging & sorting | No | Yes | Yes |
| `findAll()` return type | `Iterable<T>` | `Iterable<T>` | **`List<T>`** |
| Batch delete/save helpers | No | No | Yes (`deleteAllInBatch`, etc.) |
| Flushing (`flush()`, `saveAndFlush()`) | No | No | Yes |
| Query by Example support | No | No | Yes |

```java
customerRepository.flush();                 // force pending changes to the DB now
Customer saved = customerRepository.saveAndFlush(customer);
customerRepository.deleteAllInBatch();       // single bulk DELETE statement (fast)
```

**Spring Data 3.x repository hierarchy recap:**

```
Repository<T, ID>
 ├── CrudRepository<T, ID>
 │     └── ListCrudRepository<T, ID>              // findAll() returns List<T>
 ├── PagingAndSortingRepository<T, ID>
 │     └── ListPagingAndSortingRepository<T, ID>   // findAll() returns List<T>
 └── JpaRepository<T, ID>  extends ListCrudRepository, ListPagingAndSortingRepository, QueryByExampleExecutor
```

`JpaRepository` already combines the "List-returning" versions, so in JPA projects you rarely need to reach for `ListCrudRepository` directly — it matters more for non-JPA Spring Data modules.

## Derived Queries

A **derived query** (a.k.a. **query derivation**) is a query Spring Data builds automatically by parsing your method's name. You never write SQL or JPQL — the method signature *is* the query.

```java
public interface EmployeeRepository extends JpaRepository<Employee, Long> {

    List<Employee> findByDepartment(String department);
    List<Employee> findByDepartmentAndActiveTrue(String department);
    List<Employee> findByAgeGreaterThanEqual(int age);
    List<Employee> findByLastNameContainingIgnoreCase(String namePart);
    Optional<Employee> findFirstByOrderBySalaryDesc();
    boolean existsByEmail(String email);
    long countByDepartment(String department);
}
```

Spring Data parses `findByDepartmentAndActiveTrue` roughly like this:

1. `findBy` → this is a query, select entities.
2. `Department` → match the `department` property.
3. `And` → combine with the next condition.
4. `ActiveTrue` → match `active = true`.

### Keyword reference table

| Keyword | Example method | Generated condition (roughly) |
|---|---|---|
| `And` | `findByFirstNameAndLastName` | `WHERE first_name = ? AND last_name = ?` |
| `Or` | `findByFirstNameOrLastName` | `WHERE first_name = ? OR last_name = ?` |
| `Is`, `Equals` | `findByStatusIs` | `WHERE status = ?` |
| `Between` | `findByAgeBetween` | `WHERE age BETWEEN ? AND ?` |
| `LessThan` / `LessThanEqual` | `findByAgeLessThan` | `WHERE age < ?` |
| `GreaterThan` / `GreaterThanEqual` | `findByAgeGreaterThanEqual` | `WHERE age >= ?` |
| `After` / `Before` | `findByCreatedAtAfter` | `WHERE created_at > ?` |
| `IsNull` / `IsNotNull` | `findByManagerIsNull` | `WHERE manager_id IS NULL` |
| `Like` / `NotLike` | `findByNameLike` | `WHERE name LIKE ?` |
| `Containing` | `findByNameContaining` | `WHERE name LIKE '%?%'` |
| `StartingWith` / `EndingWith` | `findByNameStartingWith` | `WHERE name LIKE '?%'` |
| `In` / `NotIn` | `findByStatusIn` | `WHERE status IN (?, ?, ...)` |
| `True` / `False` | `findByActiveTrue` | `WHERE active = true` |
| `IgnoreCase` | `findByNameIgnoreCase` | case-insensitive comparison |
| `OrderBy...Asc/Desc` | `findByDeptOrderBySalaryDesc` | `ORDER BY salary DESC` |
| `Not` | `findByStatusNot` | `WHERE status <> ?` |
| `Top` / `First` | `findTop5ByDept` | `LIMIT 5` |
| `Distinct` | `findDistinctByDept` | `SELECT DISTINCT ...` |

Key points:

- Method names must match **entity property names**, not database column names.
- Long method names (`findByDepartmentAndActiveTrueAndSalaryGreaterThan`) are legal but hurt readability — past a certain complexity, prefer `@Query` or Specifications.
- Spring Data validates these method names at **startup**, so a typo fails fast instead of silently at runtime.

## Query Methods

"Query methods" is the umbrella term for any repository method whose query is defined declaratively — either through **name derivation** (see above) or through an explicit **`@Query`** annotation. This section focuses on the mechanics of how Spring Data resolves and executes them.

```java
public interface BookRepository extends JpaRepository<Book, Long> {

    // Derived query method
    List<Book> findByAuthorName(String authorName);

    // Query method backed by an explicit query
    @Query("SELECT b FROM Book b WHERE b.publishedYear > :year")
    List<Book> findRecentBooks(@Param("year") int year);
}
```

How Spring Data resolves a query method at startup:

1. Is there a `@Query` annotation? If yes, use it directly.
2. Is there a matching named query (`@NamedQuery` on the entity)? If yes, use it.
3. Otherwise, try to **derive** the query from the method name.
4. If none of these work, startup fails with an error — you find out immediately, not in production.

Supported return types for query methods include:

| Return type | Meaning |
|---|---|
| `List<T>` / `Iterable<T>` | Multiple results |
| `Optional<T>` | Zero or one result |
| `T` | Exactly one result (throws if more than one) |
| `Page<T>` | A page of results + total count |
| `Slice<T>` | A page-like result *without* a total count query (cheaper) |
| `Stream<T>` | Lazily-loaded stream (must be closed!) |
| `boolean` | For `existsBy...` |
| `long` / `int` | For `countBy...` |

```java
// Stream must be closed to release the underlying resources
try (Stream<Book> books = bookRepository.streamAllBy()) {
    books.forEach(System.out::println);
}
```

## JPQL

**JPQL** (Java Persistence Query Language) is a database-agnostic query language, similar to SQL but written in terms of **entities and their fields**, not tables and columns. It's the language you use inside `@Query` most of the time.

```java
public interface OrderRepository extends JpaRepository<Order, Long> {

    @Query("SELECT o FROM Order o WHERE o.customer.email = :email AND o.status = :status")
    List<Order> findByCustomerEmailAndStatus(@Param("email") String email,
                                              @Param("status") OrderStatus status);

    @Modifying
    @Query("UPDATE Order o SET o.status = :status WHERE o.id = :id")
    int updateStatus(@Param("id") Long id, @Param("status") OrderStatus status);
}
```

Key differences from SQL:

| JPQL | SQL |
|---|---|
| `SELECT o FROM Order o` | `SELECT * FROM orders` |
| Refers to **entity name** (`Order`) | Refers to **table name** (`orders`) |
| Refers to **field name** (`o.customer.email`) navigates relationships | Requires explicit `JOIN` |
| Database-portable (works on MySQL, Postgres, etc. unchanged) | Tied to a specific database dialect |

Important rules:

- Use `@Param("name")` to bind named parameters (`:name`) — safer and clearer than positional `?1` parameters.
- Any query that changes data (`UPDATE`/`DELETE`) needs `@Modifying`, or Spring Data throws an exception.
- `@Modifying` queries usually also need `@Transactional` on the calling service method (or the repository method itself), since they must run inside a transaction.

```java
@Modifying
@Transactional
@Query("DELETE FROM Order o WHERE o.status = 'CANCELLED'")
void deleteCancelledOrders();
```

## Native Queries

A **native query** is plain SQL specific to your actual database (MySQL, PostgreSQL, etc.), used when JPQL cannot express what you need — vendor-specific functions, complex joins, window functions, or performance-tuned raw SQL.

```java
public interface ReportRepository extends JpaRepository<SalesRecord, Long> {

    @Query(value = "SELECT * FROM sales_record WHERE sale_date >= :from AND sale_date <= :to",
           nativeQuery = true)
    List<SalesRecord> findSalesBetween(@Param("from") LocalDate from, @Param("to") LocalDate to);

    @Query(value = """
            SELECT region, SUM(amount) AS total
            FROM sales_record
            GROUP BY region
            ORDER BY total DESC
            """, nativeQuery = true)
    List<Object[]> totalSalesByRegion();
}
```

```sql
-- The raw SQL Spring Data executes for the query above
SELECT region, SUM(amount) AS total
FROM sales_record
GROUP BY region
ORDER BY total DESC;
```

| | JPQL | Native query |
|---|---|---|
| Portable across databases | Yes | No — tied to one DB's SQL dialect |
| Refers to | Entity/field names | Table/column names |
| Supports vendor-specific SQL | No | Yes |
| Pagination support | Full | Limited/tricky (needs a `countQuery` for `Page<T>`) |
| Typical use case | Everyday queries | Reporting, performance tuning, DB-specific features |

For paginated native queries, you must supply a separate count query:

```java
@Query(value = "SELECT * FROM sales_record WHERE region = :region",
       countQuery = "SELECT count(*) FROM sales_record WHERE region = :region",
       nativeQuery = true)
Page<SalesRecord> findByRegion(@Param("region") String region, Pageable pageable);
```

## Specifications

A **Specification** is a way to build queries **dynamically at runtime**, using Java code instead of a fixed method name or JPQL string. It's based on JPA's Criteria API and is ideal for search/filter forms where any combination of fields might be present.

Analogy: derived query methods are like a fixed restaurant menu; Specifications are like a build-your-own-sandwich counter — you combine only the ingredients (conditions) the customer actually wants.

```java
public interface EmployeeRepository extends JpaRepository<Employee, Long>,
                                              JpaSpecificationExecutor<Employee> {
}
```

```java
public class EmployeeSpecifications {

    public static Specification<Employee> hasDepartment(String department) {
        return (root, query, cb) ->
                department == null ? null : cb.equal(root.get("department"), department);
    }

    public static Specification<Employee> minSalary(BigDecimal min) {
        return (root, query, cb) ->
                min == null ? null : cb.greaterThanOrEqualTo(root.get("salary"), min);
    }
}
```

```java
Specification<Employee> spec = Specification
        .where(EmployeeSpecifications.hasDepartment("Engineering"))
        .and(EmployeeSpecifications.minSalary(new BigDecimal("50000")));

List<Employee> results = employeeRepository.findAll(spec);
```

Key points:

- Returning `null` from a `Specification` lambda means "skip this condition" — extremely useful for optional search filters.
- Specifications compose with `.and()`, `.or()`, and `.not()`.
- Requires the entity's repository to also implement `JpaSpecificationExecutor<T>`.
- More verbose than derived queries, but far more flexible for dynamic filtering.

## Query by Example

**Query by Example (QBE)** lets you build a query from a partially-filled example entity instead of writing conditions in code. You create an instance of your entity, set only the fields you care about, and let Spring Data figure out the `WHERE` clause.

```java
Employee probe = new Employee();
probe.setDepartment("Engineering");
probe.setActive(true);

ExampleMatcher matcher = ExampleMatcher.matching()
        .withIgnoreNullValues()
        .withMatcher("department", ExampleMatcher.GenericPropertyMatchers.exact())
        .withStringMatcher(ExampleMatcher.StringMatcher.CONTAINING);

Example<Employee> example = Example.of(probe, matcher);

List<Employee> matches = employeeRepository.findAll(example);
```

| | Specifications | Query by Example |
|---|---|---|
| Based on | JPA Criteria API | An example object + `ExampleMatcher` |
| Good for | Complex, arbitrary conditions (ranges, joins) | Simple "match these fields" searches |
| Supports `OR` logic across fields | Yes | Limited |
| Requires extra interface | `JpaSpecificationExecutor<T>` | Built into `JpaRepository` / `QueryByExampleExecutor` |
| Readability | More code, more power | Less code, less power |

Limitations to remember:

- QBE cannot easily express nested/associated-entity conditions or complex logical combinations.
- It does not support matching on collection properties well.
- Null fields on the probe object are ignored by default (unless `ExampleMatcher` configuration is changed).

## Pagination

**Pagination** means returning data in fixed-size chunks ("pages") instead of the entire result set at once. This is essential for performance and for good UX — nobody wants to load 2 million rows into memory to show a table of 20.

```java
Pageable pageable = PageRequest.of(2, 10); // 3rd page (0-indexed), 10 items per page

Page<Customer> page = customerRepository.findAll(pageable);

List<Customer> customers = page.getContent();
int totalPages = page.getTotalPages();
long totalElements = page.getTotalElements();
boolean isLast = page.isLast();
```

You can also request a page directly from a derived query method:

```java
public interface CustomerRepository extends JpaRepository<Customer, Long> {
    Page<Customer> findByCountry(String country, Pageable pageable);
}
```

| Type | Has total count? | Cost | Typical use |
|---|---|---|---|
| `Page<T>` | Yes (`getTotalElements()`, `getTotalPages()`) | Extra `COUNT` query | Classic paginated UI with page numbers |
| `Slice<T>` | No | Cheaper (fetches `pageSize + 1` rows to know if there's a next page) | "Load more" / infinite scroll UI |
| `List<T>` with `Pageable` | No metadata at all | Cheapest | When you only need the raw rows |

Controller example exposing pagination over REST:

```java
@RestController
@RequestMapping("/api/customers")
public class CustomerController {

    private final CustomerRepository customerRepository;

    public CustomerController(CustomerRepository customerRepository) {
        this.customerRepository = customerRepository;
    }

    @GetMapping
    public Page<Customer> list(@RequestParam(defaultValue = "0") int page,
                                @RequestParam(defaultValue = "20") int size) {
        return customerRepository.findAll(PageRequest.of(page, size));
    }
}
```

- Spring Boot MVC also supports resolving `Pageable` directly as a controller argument (`@PageableDefault`) if `spring-data-web` support is on the classpath.
- Page numbers are **0-indexed** by default — page `0` is the first page. This trips up a lot of beginners.

## Sorting

**Sorting** controls the order of results. Spring Data's `Sort` object can be combined with pagination or used entirely on its own.

```java
Sort sort = Sort.by("lastName").ascending()
                .and(Sort.by("firstName").descending());

List<Customer> sorted = customerRepository.findAll(sort);
```

```java
Pageable pageable = PageRequest.of(0, 20, Sort.by("createdAt").descending());
Page<Order> recentOrders = orderRepository.findAll(pageable);
```

Sorting can also be baked directly into a derived query name:

```java
List<Employee> findByDepartmentOrderBySalaryDescLastNameAsc(String department);
```

| Approach | Example |
|---|---|
| Method name | `findByDepartmentOrderBySalaryDesc` |
| `Sort` parameter | `findByDepartment(String dept, Sort sort)` |
| Combined with paging | `PageRequest.of(page, size, sort)` |

Watch out:

- Sorting by a property that doesn't exist on the entity throws an exception at query execution time (not always at startup, depending on how the `Sort` is built dynamically).
- Sorting on unindexed columns for large tables can be slow — always check that sortable columns have an index in production.

## Projections

A **projection** means fetching only a subset of an entity's fields — or reshaping the result into a different type — instead of loading the full entity. This is useful for performance (less data transferred, less memory) and for shaping API responses.

### Interface-based projection (the simplest kind)

```java
public interface CustomerSummary {
    String getFirstName();
    String getLastName();
    String getEmail();
}
```

```java
public interface CustomerRepository extends JpaRepository<Customer, Long> {
    List<CustomerSummary> findByCountry(String country);
}
```

Spring Data generates a proxy implementing `CustomerSummary` on the fly — no manual mapping code needed.

### DTO-based projection (a real class, using a constructor expression)

```java
public record CustomerDto(String firstName, String lastName, String email) {}
```

```java
public interface CustomerRepository extends JpaRepository<Customer, Long> {

    @Query("SELECT new com.example.dto.CustomerDto(c.firstName, c.lastName, c.email) " +
           "FROM Customer c WHERE c.country = :country")
    List<CustomerDto> findSummariesByCountry(@Param("country") String country);
}
```

### Dynamic projections (choose the return type at call time)

```java
public interface CustomerRepository extends JpaRepository<Customer, Long> {
    <T> List<T> findByCountry(String country, Class<T> type);
}
```

```java
List<CustomerSummary> summaries = customerRepository.findByCountry("NL", CustomerSummary.class);
List<Customer> fullEntities = customerRepository.findByCountry("NL", Customer.class);
```

| Projection type | Mechanism | Best for |
|---|---|---|
| Interface (closed) | Proxy generated automatically, getter names match entity fields | Quick, simple field subsets |
| Interface (open) | Uses `@Value("#{target.firstName + ' ' + target.lastName}")` SpEL | Computed values from multiple fields |
| DTO / record | JPQL constructor expression `new package.Dto(...)` | Immutable, type-safe results |
| Dynamic | `Class<T> type` parameter | Reusing one query method for multiple shapes |

Why bother with projections: fetching only 3 columns instead of a full entity with 20 columns and 4 relationships is faster and avoids the classic "N+1 query" trap when lazy associations are involved.

## Auditing

**Auditing** automatically tracks metadata like who created or last modified a record, and when. Instead of manually setting `createdAt`/`updatedAt` in every service method, Spring Data fills these fields in for you.

Step 1 — enable auditing on a configuration class:

```java
@Configuration
@EnableJpaAuditing
public class JpaAuditingConfig {
}
```

Step 2 — annotate the entity with `@EntityListeners` and mark the audit fields:

```java
@Entity
@EntityListeners(AuditingEntityListener.class)
public class Article {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String title;

    @CreatedDate
    private Instant createdAt;

    @LastModifiedDate
    private Instant updatedAt;

    @CreatedBy
    private String createdBy;

    @LastModifiedBy
    private String lastModifiedBy;

    // getters/setters omitted
}
```

Step 3 — tell Spring who the "current user" is, for `@CreatedBy`/`@LastModifiedBy` to work:

```java
@Bean
public AuditorAware<String> auditorProvider() {
    return () -> Optional.ofNullable(SecurityContextHolder.getContext().getAuthentication())
                          .map(Authentication::getName);
}
```

Even cleaner: extend a shared base class instead of repeating the four fields on every entity.

```java
@MappedSuperclass
@EntityListeners(AuditingEntityListener.class)
public abstract class Auditable {

    @CreatedDate
    private Instant createdAt;

    @LastModifiedDate
    private Instant updatedAt;

    // getters/setters omitted
}
```

```java
@Entity
public class Article extends Auditable {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String title;
}
```

| Annotation | Fills in | Requires |
|---|---|---|
| `@CreatedDate` | Timestamp when the row was first saved | `@EnableJpaAuditing` |
| `@LastModifiedDate` | Timestamp on every update | `@EnableJpaAuditing` |
| `@CreatedBy` | Current user at creation time | An `AuditorAware<T>` bean |
| `@LastModifiedBy` | Current user at last update | An `AuditorAware<T>` bean |

Key points:

- `@EntityListeners(AuditingEntityListener.class)` is what actually triggers the field population on JPA lifecycle events (`@PrePersist`, `@PreUpdate`).
- Without an `AuditorAware<T>` bean, `@CreatedBy`/`@LastModifiedBy` fields stay empty — auditing dates alone don't need it.
- `@MappedSuperclass` is the standard JPA way to share fields across entities without creating a table for the base class itself.

## Common Code Review / Interview Pitfalls

- **Calling `findAll()` on a huge table** — this loads every row into memory and can crash the app or the database. Fix: always paginate with `Pageable` for user-facing or unbounded queries.

  ```java
  // ❌ Bad
  List<Order> all = orderRepository.findAll();

  // ✅ Good
  Page<Order> page = orderRepository.findAll(PageRequest.of(0, 50));
  ```

- **Exposing JPA entities directly in REST responses** — entities carry lazy associations, internal fields, and JPA proxies that don't serialize cleanly and leak persistence details to clients. Fix: map to DTOs or use projections.

- **Concatenating user input into JPQL/native SQL strings** — this opens the door to SQL injection. Fix: always use named (`:param`) or positional parameters, never string concatenation.

  ```java
  // ❌ Bad
  @Query(value = "SELECT * FROM users WHERE email = '" + email + "'", nativeQuery = true)

  // ✅ Good
  @Query(value = "SELECT * FROM users WHERE email = :email", nativeQuery = true)
  List<User> findByEmailRaw(@Param("email") String email);
  ```

- **Forgetting `@Modifying` on `UPDATE`/`DELETE` `@Query` methods** — Spring Data assumes queries are `SELECT`s by default and throws `InvalidDataAccessApiUsageException` otherwise. Fix: annotate with `@Modifying` and wrap the call in a transaction.

- **Overly long derived query method names** — `findByStatusAndCustomerCountryAndCreatedAtBetweenAndTotalGreaterThan` is hard to read and easy to get wrong. Fix: switch to `@Query`, a Specification, or a QueryDSL/Criteria-based approach once you have more than 2-3 conditions.

- **Using `Page<T>` when you don't need a total count** — the `COUNT` query on a large, filtered table can be expensive and doubles your query cost. Fix: use `Slice<T>` for "infinite scroll" style UIs that don't display a total.

- **Ignoring the N+1 query problem with lazy associations** — iterating over a list of entities and touching a `@ManyToOne`/`@OneToMany` lazy field for each one triggers one extra query per row. Fix: use `JOIN FETCH` in JPQL, an `@EntityGraph`, or a DTO projection that selects only what's needed.

  ```java
  // ❌ Bad — N+1 queries when accessing order.getCustomer() for each order
  @Query("SELECT o FROM Order o")
  List<Order> findAllOrders();

  // ✅ Good — one query, eagerly fetches the association
  @Query("SELECT o FROM Order o JOIN FETCH o.customer")
  List<Order> findAllOrdersWithCustomer();
  ```

- **Assuming `save()` always inserts** — `save()` performs an `INSERT` or `UPDATE` depending on whether the entity's ID is already set/exists; misunderstanding this causes accidental overwrites or duplicate-key errors. Fix: know your ID generation strategy, and use `existsById` if the behavior needs to be explicit.

- **Not indexing columns used in `WHERE`/`ORDER BY` for derived or `@Query` methods** — a perfectly correct query can still be slow without a database index. Fix: add indexes for filter and sort columns on large tables, and verify with `EXPLAIN`.

- **Mutating entities returned by Query by Example or projections and expecting persistence** — interface-based projections and read-only queries are often not managed entities; calling setters on them does nothing to the database. Fix: fetch the real managed entity via `findById` when you intend to update it.

- **Skipping `AuditorAware` and expecting `@CreatedBy` to populate itself** — `@CreatedDate`/`@LastModifiedDate` work out of the box, but `@CreatedBy`/`@LastModifiedBy` silently stay `null` without a registered `AuditorAware<T>` bean. Fix: define the bean, typically backed by Spring Security's `SecurityContextHolder`.

- **Using `@Transactional` incorrectly around repository calls** — forgetting `@Transactional` on multi-step operations can leave data in a partially-updated state; overusing it on read-only queries can hold connections longer than needed. Fix: use `@Transactional(readOnly = true)` for reads, and a real transaction boundary for writes.

- **Native queries with pagination missing a `countQuery`** — `Page<T>` needs a count to compute `getTotalPages()`; without an explicit `countQuery`, Spring Data may fail or generate an inefficient one for native SQL. Fix: always supply `countQuery` alongside `nativeQuery = true` when paginating.

- **Comparing `Page` content by index across requests assuming stable ordering** — without an explicit `Sort`, most databases don't guarantee row order between queries, so paging without sorting can return duplicate or missing rows across pages. Fix: always pass a deterministic `Sort` (ideally including a unique tiebreaker column) alongside `Pageable`.

## Quick Recap

- Spring Data turns repository **interfaces** into working implementations automatically — no hand-written DAO boilerplate.
- The hierarchy: `Repository` → `CrudRepository` / `PagingAndSortingRepository` → `JpaRepository`. In Spring Data 3.x, `ListCrudRepository` and `ListPagingAndSortingRepository` exist to return `List<T>` instead of `Iterable<T>`; `JpaRepository` already combines both.
- **Derived queries** build SQL from method names (`findByLastNameAndActiveTrue`); great for simple cases, but don't push them too far.
- **`@Query`** lets you write **JPQL** (portable, entity-based) or **native SQL** (`nativeQuery = true`, database-specific) when derivation isn't enough.
- Use `@Modifying` + `@Transactional` for `UPDATE`/`DELETE` queries.
- **Specifications** and **Query by Example** enable dynamic, conditional queries — Specifications for complex logic, QBE for simple "match these fields" searches.
- **Pagination** (`Page`, `Slice`, `Pageable`) and **Sorting** (`Sort`) keep large result sets fast and manageable — page numbers are 0-indexed.
- **Projections** (interfaces, DTOs/records, dynamic `Class<T>`) let you fetch exactly the fields you need instead of full entities.
- **Auditing** (`@CreatedDate`, `@LastModifiedDate`, `@CreatedBy`, `@LastModifiedBy` + `@EnableJpaAuditing` + `AuditorAware`) automates "who/when" tracking without manual code.
- Always watch for: unbounded `findAll()`, SQL injection via string concatenation, N+1 queries on lazy associations, and missing indexes on filtered/sorted columns.
