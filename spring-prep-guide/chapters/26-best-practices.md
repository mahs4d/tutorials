# 26. Best Practices

## Overview

This chapter is a tour of the habits that separate a "it works on my machine" Spring Boot app from one a team can maintain for years. None of these ideas are laws carved in stone — they are defaults that save you from common mistakes, and every default has a cost. Clean layering adds indirection. Strict validation adds boilerplate. Hexagonal architecture adds interfaces you may never need. The skill worth having is not "always do X" but "know why X exists, and know when the cost of X is not worth paying." A three-endpoint CRUD microservice and a core billing engine used by fifty other services should not be built the same way, even though both are "Spring Boot apps." Read every section below with that filter on: what problem does this solve, and do I actually have that problem?

## Clean Architecture

Clean Architecture (popularized by Robert C. Martin, "Uncle Bob") organizes code in concentric circles. The rule is simple: **dependencies point inward, never outward**. The center holds your business rules (entities, use cases). The outer rings hold things that change for reasons unrelated to your business — web frameworks, databases, UI. Nothing in the center should know these outer things exist.

Think of it like an onion. You can swap the skin (web framework) without touching the core (business rules), because the skin depends on the core, not the other way around.

```text
        +-----------------------------------------------+
        |                Frameworks & Drivers            |
        |   (Spring MVC, JPA, Kafka client, REST clients) |
        |     +---------------------------------------+   |
        |     |          Interface Adapters            |   |
        |     |   (Controllers, Repositories impl,     |   |
        |     |    Presenters, Gateways)                |   |
        |     |     +-----------------------------+     |   |
        |     |     |     Application / Use Cases |     |   |
        |     |     |   (Services orchestrating    |     |   |
        |     |     |    domain objects)            |     |   |
        |     |     |    +--------------------+     |     |   |
        |     |     |    |   Domain / Entities |     |     |   |
        |     |     |    |  (business rules)   |     |     |   |
        |     |     |    +--------------------+     |     |   |
        |     |     +-----------------------------+     |   |
        |     +---------------------------------------+   |
        +-----------------------------------------------+

              Arrows of dependency point INWARD only
```

A concrete Spring package layout:

```text
com.example.orders
├── domain
│   ├── Order.java              (plain Java, no annotations)
│   ├── OrderStatus.java
│   └── OrderRepository.java    (interface, a "port")
├── application
│   ├── PlaceOrderUseCase.java
│   └── PlaceOrderService.java
├── adapter
│   ├── in
│   │   └── web
│   │       └── OrderController.java
│   └── out
│       └── persistence
│           ├── OrderEntity.java
│           ├── OrderJpaRepository.java
│           └── OrderRepositoryAdapter.java
└── config
    └── BeanConfig.java
```

```java
// domain — no Spring, no JPA, just Java
public record Order(OrderId id, CustomerId customerId, Money total, OrderStatus status) {

    public Order confirm() {
        if (status != OrderStatus.PENDING) {
            throw new IllegalStateException("Only pending orders can be confirmed");
        }
        return new Order(id, customerId, total, OrderStatus.CONFIRMED);
    }
}
```

Clean Architecture is a mindset more than a fixed folder structure. Its sibling "Layered Architecture" and "Hexagonal Architecture" (below) are two ways of putting the same idea into practice.

## Layered Architecture

Layered architecture is the classic, simplest structure: stack code in horizontal layers, and each layer only talks to the layer directly below it.

```text
 ┌────────────────────────────┐
 │   Presentation (Controller)│   handles HTTP in/out
 ├────────────────────────────┤
 │   Service (business logic) │   orchestrates work
 ├────────────────────────────┤
 │   Repository (data access) │   talks to the database
 ├────────────────────────────┤
 │   Database                 │
 └────────────────────────────┘
```

Spring package layout (package-by-layer):

```text
com.example.orders
├── controller
│   └── OrderController.java
├── service
│   └── OrderService.java
├── repository
│   └── OrderRepository.java
├── model
│   └── Order.java
└── dto
    └── OrderResponse.java
```

```java
@RestController
@RequestMapping("/orders")
class OrderController {

    private final OrderService orderService;

    OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @GetMapping("/{id}")
    OrderResponse getOrder(@PathVariable Long id) {
        return orderService.findById(id);
    }
}

@Service
class OrderService {
    private final OrderRepository orderRepository;

    OrderService(OrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    OrderResponse findById(Long id) {
        Order order = orderRepository.findById(id)
            .orElseThrow(() -> new OrderNotFoundException(id));
        return OrderResponse.from(order);
    }
}
```

This is the architecture most Spring tutorials teach first, and for good reason: it is easy to explain, easy to navigate, and every Spring Boot generator (Spring Initializr demos, most bootcamps) defaults to it.

## Hexagonal Architecture

Hexagonal architecture (a.k.a. "Ports and Adapters", coined by Alistair Cockburn) is Clean Architecture's most popular concrete shape. The domain sits in the middle. It defines **ports** — interfaces describing what it needs ("save an order") or offers ("place an order"). **Adapters** implement those ports for a specific technology (a JPA adapter, a REST adapter, a Kafka adapter). The hexagon shape itself is not important — it is just a way to draw "many sides, many adapters" without implying a left/right order like layers do.

```text
                     ┌───────────────────────────┐
        ┌───────────►│   Inbound Adapter (REST)  │
        │            └─────────────┬─────────────┘
        │                          │ calls
        │                          ▼
        │            ┌───────────────────────────┐
   HTTP request      │      Inbound Port          │
        │            │  (PlaceOrderUseCase)       │
        │            ├───────────────────────────┤
        │            │                           │
        │            │        DOMAIN CORE         │
        │            │   (Order, business rules)  │
        │            │                           │
        │            ├───────────────────────────┤
        │            │      Outbound Port         │
        │            │   (OrderRepository)        │
        │            └─────────────┬─────────────┘
        │                          │ implemented by
        │                          ▼
        │            ┌───────────────────────────┐
        └────────────┤ Outbound Adapter (JPA/DB) │
                      └───────────────────────────┘
```

Spring package layout:

```text
com.example.orders
├── domain
│   ├── model
│   │   └── Order.java
│   └── port
│       ├── in
│       │   └── PlaceOrderUseCase.java      (interface)
│       └── out
│           └── OrderRepositoryPort.java     (interface)
├── application
│   └── PlaceOrderService.java               (implements the "in" port)
└── adapter
    ├── in
    │   └── web
    │       └── OrderController.java         (calls the "in" port)
    └── out
        └── persistence
            ├── OrderJpaEntity.java
            ├── SpringDataOrderRepository.java
            └── OrderRepositoryAdapter.java  (implements the "out" port)
```

```java
// port — lives in the domain, pure Java
public interface OrderRepositoryPort {
    Optional<Order> findById(OrderId id);
    void save(Order order);
}

// adapter — lives in infrastructure, knows about JPA
@Component
class OrderRepositoryAdapter implements OrderRepositoryPort {

    private final SpringDataOrderRepository jpaRepository;

    OrderRepositoryAdapter(SpringDataOrderRepository jpaRepository) {
        this.jpaRepository = jpaRepository;
    }

    @Override
    public Optional<Order> findById(OrderId id) {
        return jpaRepository.findById(id.value()).map(OrderJpaEntity::toDomain);
    }

    @Override
    public void save(Order order) {
        jpaRepository.save(OrderJpaEntity.fromDomain(order));
    }
}
```

### Comparison table

| Aspect | Layered | Clean Architecture | Hexagonal |
|---|---|---|---|
| Mental model | Horizontal stack | Concentric circles | Core + ports/adapters |
| Learning curve | Low | Medium | Medium-High |
| Swapping infrastructure (e.g. DB) | Hard — service often imports repository types directly | Easy — inward-only rule enforced | Easy — swap the adapter, port stays |
| Boilerplate | Low | Medium | Medium-High (extra interfaces) |
| Best fit | Small-medium CRUD apps, internal tools | Medium-large apps with evolving business rules | Domain-heavy apps, multiple integrations, long-lived core logic |
| Testability of business logic | OK, but often coupled to Spring context | Good | Excellent — domain has zero framework dependencies |
| Risk of over-engineering | Low | Medium | High, if applied blindly |

**Be honest with yourself:** a service with three CRUD endpoints and one Postgres table does not need ports, adapters, and a domain model isolated from JPA. That is ceremony without payoff. Reach for hexagonal when the domain logic is genuinely complex, when you expect to swap or add integrations (multiple databases, external providers, messaging), or when the core rules must survive framework upgrades untouched. For a simple CRUD service, plain layered architecture is not a compromise — it is the correct choice.

## Dependency Inversion

Dependency Inversion is the "D" in SOLID. The rule: **high-level modules should not depend on low-level modules; both should depend on abstractions.** In practice for Spring: your business logic should depend on an interface, not on a concrete database class, and that interface should be *owned by* the business logic, not by the infrastructure.

An analogy: a lamp does not depend on "the specific power plant." It depends on "a standard electrical socket" (the abstraction). Any power plant that fits the socket works. Swap coal for solar, the lamp does not notice.

In Spring, this means: define an interface (a "port") in your domain package. Implement it in an infrastructure package (an "adapter"). The domain never imports `org.springframework.*` or `jakarta.persistence.*`. Why? Because the moment domain code has an `@Entity` or `@Transactional` annotation on it, you can no longer test business rules without spinning up Spring/JPA, and you can no longer change your persistence technology without touching business rules.

**Before** — service depends directly on JPA, tightly coupled:

```java
@Entity
class Order {                 // domain object glued to JPA
    @Id @GeneratedValue
    private Long id;
    private BigDecimal total;
    @Enumerated(EnumType.STRING)
    private OrderStatus status;
    // getters/setters...
}

@Service
class OrderService {
    private final OrderJpaRepository jpaRepository;   // depends on a concrete Spring Data interface

    OrderService(OrderJpaRepository jpaRepository) {
        this.jpaRepository = jpaRepository;
    }

    void confirm(Long id) {
        Order order = jpaRepository.findById(id).orElseThrow();
        order.setStatus(OrderStatus.CONFIRMED);   // logic mixed with persistence type
        jpaRepository.save(order);
    }
}
```

Problems: `OrderService` cannot be unit tested without a JPA context (or heavy mocking of Spring Data internals), and switching to MongoDB means rewriting the domain.

**After** — domain defines the port, infrastructure adapts to it:

```java
// domain — plain Java, zero framework imports
public record Order(Long id, BigDecimal total, OrderStatus status) {
    public Order confirm() {
        if (status != OrderStatus.PENDING) {
            throw new IllegalOrderStateException(id, status);
        }
        return new Order(id, total, OrderStatus.CONFIRMED);
    }
}

public interface OrderRepository {           // port, owned by the domain/application
    Order findById(Long id);
    void save(Order order);
}

@Service
class OrderService {
    private final OrderRepository orderRepository;   // depends on the abstraction

    OrderService(OrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    void confirm(Long id) {
        Order order = orderRepository.findById(id);
        orderRepository.save(order.confirm());
    }
}

// infrastructure — the only place that knows about JPA
@Entity
@Table(name = "orders")
class OrderJpaEntity {
    @Id @GeneratedValue
    private Long id;
    private BigDecimal total;
    @Enumerated(EnumType.STRING)
    private OrderStatus status;
    // getters/setters, toDomain(), fromDomain()...
}

@Repository
class JpaOrderRepository implements OrderRepository {
    private final SpringDataOrderJpaRepository springDataRepo;

    JpaOrderRepository(SpringDataOrderJpaRepository springDataRepo) {
        this.springDataRepo = springDataRepo;
    }

    @Override
    public Order findById(Long id) {
        return springDataRepo.findById(id)
            .map(OrderJpaEntity::toDomain)
            .orElseThrow(() -> new OrderNotFoundException(id));
    }

    @Override
    public void save(Order order) {
        springDataRepo.save(OrderJpaEntity.fromDomain(order));
    }
}
```

Now `OrderService` can be unit tested with a hand-written fake `OrderRepository` — no Spring context, no database, milliseconds instead of seconds.

## DTO Mapping

Four shapes usually show up in a well-structured Spring app:

| Shape | Lives at | Purpose |
|---|---|---|
| Request DTO | API edge, inbound | Shape of data the client sends; carries bean validation annotations |
| Response DTO | API edge, outbound | Shape of data the client receives; hides internal fields |
| Domain model | Business/domain layer | Pure business object, no framework annotations |
| Entity | Persistence layer | Maps to a database table, carries JPA annotations |

Never return a JPA `@Entity` straight from a controller. It leaks database column names, lazy-loading fields that blow up with `LazyInitializationException`, and internal-only data. Mapping between these four shapes is tedious but important — it is the seam that keeps each layer free to change independently.

```java
// Request DTO — a Java record, immutable, validated at the edge
public record CreateOrderRequest(
    @NotNull Long customerId,
    @NotEmpty List<@Valid OrderLineRequest> lines
) {}

// Response DTO
public record OrderResponse(Long id, String status, BigDecimal total) {
    static OrderResponse from(Order order) {
        return new OrderResponse(order.id(), order.status().name(), order.total());
    }
}
```

### Manual mapping vs MapStruct vs ModelMapper

| Approach | How it works | Pros | Cons |
|---|---|---|---|
| Manual mapping | You write `from()`/`toDomain()` methods by hand | No magic, easy to debug, compiles to plain code, fast | Boilerplate grows with field count |
| MapStruct | Annotation processor generates mapping code at compile time | Compile-time safety, fast (no reflection), errors caught at build time, easy to unit test generated code | Adds a build-time dependency; generated code needs to be understood when debugging |
| ModelMapper | Reflection-based, matches fields by convention at runtime | Very little code to write upfront | Reflection cost at runtime, silent mismatches (typo'd field just doesn't map, no compile error), hard to review in a PR because "the mapping" is invisible |

```java
// MapStruct — you write the interface, the mapper is generated at compile time
@Mapper(componentModel = "spring")
public interface OrderMapper {
    OrderResponse toResponse(Order order);
    Order toDomain(CreateOrderRequest request);
}
```

Why reflection-based mappers (ModelMapper, or hand-rolled reflection utilities) get flagged in code review: they hide the mapping logic behind "convention," so a renamed field silently stops mapping instead of failing the build. They also add real runtime overhead in hot paths. MapStruct avoids this because the mapping code is generated and visible — you can open the generated class and read exactly what happens, and a broken mapping is a compile error, not a production bug discovered three weeks later.

Using Java records for DTOs is now the default in Spring Boot 3.x / Java 17+: records are immutable, get `equals`/`hashCode`/`toString` for free, and work directly with `@RequestBody` and Bean Validation annotations on the record's components.

## Exception Handling

Design a small hierarchy of domain exceptions instead of throwing generic `RuntimeException` everywhere. A hierarchy lets you catch broad categories when useful and specific types when needed, and it documents intent.

```java
public abstract class DomainException extends RuntimeException {
    protected DomainException(String message) {
        super(message);
    }
}

public class OrderNotFoundException extends DomainException {
    public OrderNotFoundException(Long id) {
        super("Order not found: " + id);
    }
}

public class InvalidOrderStateException extends DomainException {
    public InvalidOrderStateException(String message) {
        super(message);
    }
}
```

Centralize translation from exception to HTTP response with `@RestControllerAdvice`. Since Spring Boot 3, the recommended response body for errors is `ProblemDetail`, which implements RFC 9457 ("Problem Details for HTTP APIs") — a standard JSON shape for error responses instead of every team inventing its own.

```java
@RestControllerAdvice
class GlobalExceptionHandler {

    @ExceptionHandler(OrderNotFoundException.class)
    ProblemDetail handleNotFound(OrderNotFoundException ex) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, ex.getMessage());
        problem.setTitle("Order Not Found");
        return problem;
    }

    @ExceptionHandler(InvalidOrderStateException.class)
    ProblemDetail handleInvalidState(InvalidOrderStateException ex) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(HttpStatus.CONFLICT, ex.getMessage());
        problem.setTitle("Invalid Order State");
        return problem;
    }

    @ExceptionHandler(Exception.class)
    ProblemDetail handleUnexpected(Exception ex) {
        // log full details internally, but never leak them to the client
        log.error("Unexpected error", ex);
        return ProblemDetail.forStatusAndDetail(
            HttpStatus.INTERNAL_SERVER_ERROR, "An unexpected error occurred");
    }
}
```

Example RFC 9457 response body:

```json
{
  "type": "about:blank",
  "title": "Order Not Found",
  "status": 404,
  "detail": "Order not found: 42",
  "instance": "/orders/42"
}
```

Rules of thumb:

- **Never leak stack traces** to API clients. Log them server-side, return a generic, safe message externally.
- **Map domain errors to HTTP status deliberately**: "not found" → 404, "invalid state transition" → 409 Conflict, "validation failure" → 400, "not authorized" → 403, "unexpected/unhandled" → 500.
- **Checked vs unchecked**: prefer unchecked exceptions (extending `RuntimeException`) for most application errors in Spring codebases. Checked exceptions make sense for genuinely recoverable conditions the *caller is required to handle* (e.g. some low-level I/O APIs), but forcing every layer to declare `throws` for business errors that 99% of callers just propagate upward adds ceremony without safety. Spring's own data-access and transaction exceptions are all unchecked for this reason.

## Validation Strategy

Validation belongs at different layers depending on what is being checked. Putting all validation in one place (usually the controller, or worse, the database) either overloads the edge with business knowledge it should not have, or lets bad data slip deep into the system before anyone notices.

```text
   Syntactic validation        Semantic / business validation      DB constraints
   (Bean Validation on DTO)    (domain / service layer)            (last line of defence)
   "is this shaped right?"     "is this allowed, right now?"       "is this physically possible?"
   e.g. @NotNull, @Email,      e.g. "can't ship an order          e.g. UNIQUE, NOT NULL,
   @Min, @Size                 that's already cancelled"          FOREIGN KEY, CHECK
```

```java
// 1. Syntactic — Bean Validation on the request DTO, at the edge
public record CreateCustomerRequest(
    @NotBlank String name,
    @Email String email,
    @Min(18) int age
) {}

@RestController
class CustomerController {
    @PostMapping("/customers")
    ResponseEntity<CustomerResponse> create(@Valid @RequestBody CreateCustomerRequest request) {
        // if we get here, the request is well-formed
        return ResponseEntity.ok(customerService.create(request));
    }
}
```

```java
// 2. Semantic / business — belongs in the domain or service layer
class CustomerService {
    CustomerResponse create(CreateCustomerRequest request) {
        if (customerRepository.existsByEmail(request.email())) {
            throw new DuplicateEmailException(request.email());  // business rule, not a shape rule
        }
        // ...
    }
}
```

```sql
-- 3. Database constraint — last line of defence, catches races and bugs
ALTER TABLE customers ADD CONSTRAINT uq_customers_email UNIQUE (email);
```

Why all three layers, and not just one? Bean Validation catches malformed input cheaply, before any business code runs. Business validation catches rules that depend on state and cannot be expressed as a simple annotation ("this order cannot be cancelled because it already shipped"). The database constraint catches the case your application code missed — a race condition between two concurrent requests, a bug in a rarely-used code path, or a direct data fix by another team. Relying on the database alone means users see ugly `SQLException` stack traces instead of a clean 400 response; relying on the application alone means a bug or a race condition can still corrupt your data.

## Configuration Management

Prefer typed, immutable configuration classes over scattering `@Value("${...}")` across the codebase. Scattered `@Value` calls are hard to find, hard to test, and give you no compile-time safety if a property name is misspelled.

```yaml
# application.yml
app:
  mail:
    host: smtp.example.com
    port: 587
    from-address: no-reply@example.com
  rate-limit:
    requests-per-minute: 100
```

```java
@ConfigurationProperties(prefix = "app.mail")
@Validated
public record MailProperties(
    @NotBlank String host,
    @Min(1) @Max(65535) int port,
    @Email String fromAddress
) {}
```

```java
@ConfigurationProperties(prefix = "app.rate-limit")
@Validated
public record RateLimitProperties(
    @Min(1) int requestsPerMinute
) {}
```

```java
@Configuration
@EnableConfigurationProperties({MailProperties.class, RateLimitProperties.class})
class AppConfig {
}
```

Guidelines:

- **One configuration class per concern** (`MailProperties`, `RateLimitProperties`, `SecurityProperties`) instead of one giant `AppProperties` blob that mixes unrelated settings.
- **Validate at startup with `@Validated`** — if `app.mail.port` is missing or out of range, the application should refuse to start with a clear error, not fail three hours later when the first email is sent. This is the "fail-fast" principle: catch configuration mistakes at boot time, not in production traffic.
- **No secrets in the repo.** Never commit passwords, API keys, or tokens in `application.yml`. Use environment variables, a secrets manager (HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager), or Spring Cloud Config with encryption. Reference them with placeholders:

```yaml
spring:
  datasource:
    username: ${DB_USERNAME}
    password: ${DB_PASSWORD}
```

```properties
# .env (never committed — added to .gitignore)
DB_USERNAME=app_user
DB_PASSWORD=super-secret-value
```

## Package Organization

Two common ways to lay out packages: by layer (group by technical role) or by feature (group by business capability).

**Package-by-layer** — `com.example.orders.controller`, `com.example.orders.service`, `com.example.orders.repository`. Everything of one technical kind lives together.

**Package-by-feature** — `com.example.orders.order`, `com.example.orders.customer`, `com.example.orders.payment`. Everything needed for one business capability lives together.

```text
Package-by-layer                       Package-by-feature
com.example.shop                       com.example.shop
├── controller                         ├── order
│   ├── OrderController.java           │   ├── OrderController.java
│   ├── CustomerController.java        │   ├── OrderService.java
│   └── PaymentController.java         │   ├── OrderRepository.java
├── service                            │   └── Order.java
│   ├── OrderService.java              ├── customer
│   ├── CustomerService.java           │   ├── CustomerController.java
│   └── PaymentService.java            │   ├── CustomerService.java
├── repository                         │   ├── CustomerRepository.java
│   ├── OrderRepository.java           │   └── Customer.java
│   ├── CustomerRepository.java        └── payment
│   └── PaymentRepository.java             ├── PaymentController.java
└── model                                  ├── PaymentService.java
    ├── Order.java                         ├── PaymentRepository.java
    ├── Customer.java                      └── Payment.java
    └── Payment.java
```

| Aspect | Package-by-layer | Package-by-feature |
|---|---|---|
| Navigation | Easy to find "all controllers" | Easy to find "everything about orders" |
| Coupling visibility | Hides feature coupling — `OrderService` and `CustomerService` sit far apart but may be tangled | Makes feature coupling obvious — cross-feature imports are visible immediately |
| Encapsulation | Weak — classes are `public` by necessity so other layer packages can reach them | Strong — internals can be package-private since the whole feature is one package |
| Scaling with team size | Gets messy as the app grows — one giant `service` package | Scales better — new features are new packages, isolated |
| Typical fit | Small apps, tutorials, early prototypes | Medium-large apps, apps expected to grow, multiple teams |

**Package-private visibility** is the practical tool that makes package-by-feature pay off: mark classes and methods with no modifier (package-private) instead of `public` when they are only used inside their own feature package. The compiler then enforces the boundary — another feature simply cannot import your internal `OrderValidator` class, because it is not visible outside `com.example.shop.order`.

```java
package com.example.shop.order;

class OrderValidator {           // package-private: only usable inside "order"
    void validate(Order order) { /* ... */ }
}

public class OrderService {      // public: this is the feature's intended entry point
    private final OrderValidator validator = new OrderValidator();
    // ...
}
```

For larger applications that want this boundary enforcement checked automatically (not just by convention), **Spring Modulith** is worth knowing about: it lets you organize a single Spring Boot application into explicit modules, verifies at test time that modules only depend on each other through their declared public API, and can even document the module structure and publish module-level events. It is a good middle ground between "one big ball of packages" and "split everything into microservices too early."

## API Versioning

At some point an API needs a breaking change: a field renamed, a field removed, a different response shape. Versioning is how you make that change without breaking every existing client at once.

| Strategy | Example | Pros | Cons |
|---|---|---|---|
| URI path | `/api/v1/orders`, `/api/v2/orders` | Simple, visible, easy to route/cache, easy to explain | URL for "the same resource" changes between versions; can lead to code duplication |
| Custom header | `X-API-Version: 2` | Keeps URLs clean, resource identity stays stable | Less discoverable, easy to forget, harder to test manually in a browser |
| Content negotiation (media type) | `Accept: application/vnd.example.v2+json` | RESTfully "correct" — versioning the representation, not the resource | More complex to implement and document, unfamiliar to many client developers |
| Query parameter | `/orders?version=2` | Easy to add without route changes | Easy to omit accidentally, mixes with other query params, caching gets tricky |

```java
// URI path versioning — the most common and easiest to reason about in practice
@RestController
@RequestMapping("/api/v1/orders")
class OrderControllerV1 {
    @GetMapping("/{id}")
    OrderResponseV1 getOrder(@PathVariable Long id) { /* ... */ }
}

@RestController
@RequestMapping("/api/v2/orders")
class OrderControllerV2 {
    @GetMapping("/{id}")
    OrderResponseV2 getOrder(@PathVariable Long id) { /* ... */ }
}
```

```java
// Header-based versioning with Spring MVC
@GetMapping(value = "/orders/{id}", headers = "X-API-Version=2")
OrderResponseV2 getOrderV2(@PathVariable Long id) { /* ... */ }
```

Backwards-compatible ("non-breaking") changes you can make without bumping the version:

- Adding a new optional field to a response.
- Adding a new endpoint.
- Adding a new optional request parameter with a sensible default.
- Loosening a validation rule (accepting more than before).

Breaking changes that require a new version:

- Removing or renaming a field.
- Changing a field's type or meaning.
- Tightening validation (rejecting something that used to be accepted).
- Changing the HTTP status code returned for an existing case.

Deprecation policy — a simple, honest example:

```text
1. Ship v2. Mark v1 as deprecated in docs and in response headers:
   Deprecation: true
   Sunset: Wed, 31 Dec 2026 23:59:59 GMT
2. Log usage of v1 so you know which clients still call it.
3. Notify known consumers directly, not just in a changelog.
4. Give a fixed, published removal date (weeks or months, not "soon").
5. Remove v1 only after the sunset date, and only if usage has actually dropped to zero.
```

## Security Best Practices

A checklist to run through on every service, not a one-time setup:

- **Least privilege** — a service account, database user, or IAM role should have exactly the permissions it needs, nothing more. A read-only reporting service should not have `DELETE` rights on production tables.
- **Deny by default** — start Spring Security config from "block everything," then explicitly open what is needed, instead of starting open and trying to remember to lock things down.

```java
@Bean
SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    return http
        .authorizeHttpRequests(auth -> auth
            .requestMatchers("/public/**").permitAll()
            .anyRequest().authenticated())          // deny by default
        .build();
}
```

- **Validate input at the edge** — Bean Validation on every request DTO (see Validation Strategy above); never trust client-supplied IDs, sizes, or types.
- **Output encoding** — encode data before rendering it (HTML-escape in templates, JSON-escape in responses) to prevent XSS; let Spring's templating engines (Thymeleaf) and Jackson do this by default rather than hand-building strings.
- **Secrets in a vault** — API keys, DB passwords, and signing keys live in a secrets manager or environment variables injected at deploy time, never in `application.yml` committed to git.
- **Dependency scanning** — run tools like OWASP Dependency-Check, Snyk, or GitHub Dependabot in CI to catch known-vulnerable libraries before they reach production.
- **HTTPS / HSTS everywhere** — terminate TLS, redirect HTTP to HTTPS, and send `Strict-Transport-Security` so browsers refuse to downgrade.

```java
http.requiresChannel(channel -> channel.anyRequest().requiresSecure())
    .headers(headers -> headers.httpStrictTransportSecurity(hsts -> hsts.includeSubDomains(true)));
```

- **Rate limiting** — protect login endpoints and expensive operations from brute-force and abuse (e.g. Bucket4j, or a gateway-level limiter).
- **Audit logging** — record who did what, when, for security-sensitive actions (login, permission changes, data export) in a tamper-resistant log, separate from regular application logs.
- **No sensitive data in logs or URLs** — never log passwords, tokens, or full card numbers; never put session tokens or passwords in query strings (they end up in browser history, proxy logs, and referrer headers).
- **Principle of failing closed** — when a security check errors out (e.g. the auth service is unreachable), the safe default is to deny access, not to let the request through "just this once."

## Performance Best Practices

Another checklist, this time for performance — treat it the same way: measure, don't guess.

- **Measure first** — profile and use metrics (Micrometer + Actuator, APM tools) before optimizing. Guessing which part is "slow" wastes effort on the wrong thing.
- **Pagination by default** — never expose an endpoint that can return an unbounded list.

```java
@GetMapping("/orders")
Page<OrderResponse> listOrders(@PageableDefault(size = 20) Pageable pageable) {
    return orderRepository.findAll(pageable).map(OrderResponse::from);
}
```

- **Avoid N+1 queries** — the classic JPA trap: fetching a list of parents, then lazily triggering one query per child. Use `JOIN FETCH`, `@EntityGraph`, or DTO projections.

```java
@Query("SELECT o FROM Order o JOIN FETCH o.lines WHERE o.customerId = :customerId")
List<Order> findWithLinesByCustomerId(@Param("customerId") Long customerId);
```

- **Short transactions** — keep `@Transactional` boundaries as small as possible; never hold a transaction open across a slow external call (an HTTP request, an email send). Long transactions hold database locks and connections longer than needed.
- **Timeouts on every remote call** — HTTP clients, database calls, message consumers. Without a timeout, one slow downstream dependency can exhaust your thread pool and take down the whole service.

```yaml
spring:
  datasource:
    hikari:
      connection-timeout: 3000
      maximum-pool-size: 10
```

```java
RestClient client = RestClient.builder()
    .requestFactory(ClientHttpRequestFactorySettings.DEFAULTS
        .withConnectTimeout(Duration.ofSeconds(2))
        .withReadTimeout(Duration.ofSeconds(5)))
    .build();
```

- **Connection pool sizing** — size the pool to match the database's real capacity and your workload, not an arbitrary default; oversized pools can overwhelm the database, undersized pools cause request queuing.
- **Caching with a TTL** — cache expensive, frequently-read, rarely-changed data, and always set a time-to-live so stale data eventually clears itself out.

```java
@Cacheable(value = "productCatalog", key = "#id")
Product getProduct(Long id) { /* ... */ }
```

```yaml
spring:
  cache:
    redis:
      time-to-live: 10m
```

- **Async for slow, non-critical work** — sending a confirmation email or writing an audit log should not block the user's request; push it to `@Async`, a message queue, or an event listener.

```java
@Async
@EventListener
void onOrderPlaced(OrderPlacedEvent event) {
    emailService.sendConfirmation(event.orderId());
}
```

- **Indexes** — every column used in a `WHERE`, `JOIN`, or `ORDER BY` on a large table should be indexed; check query plans (`EXPLAIN`) rather than assuming.

## Common Code Review / Interview Pitfalls

- **Anemic domain model / fat services** — domain objects are just getters and setters with no behavior, and all logic piles up in service classes. The object that owns the data should own the rules about that data.
- **Entities exposed as API responses** — returning a JPA `@Entity` directly from a controller leaks database structure and risks `LazyInitializationException`. Always map to a response DTO.
- **Business logic in controllers** — a controller should parse the request, call a service, and shape the response. Rules like "an order under review cannot be cancelled" belong in the domain or service layer, not in an `if` inside `@PostMapping`.
- **`RuntimeException` everywhere with no hierarchy** — makes it impossible to handle specific error cases differently, and impossible to map errors to the right HTTP status without string-matching messages.
- **Catching `Exception` and logging-and-swallowing** — `catch (Exception e) { log.error(e); }` with no rethrow hides real failures from callers and from monitoring; the caller thinks the operation succeeded.
- **God `util` packages** — a `com.example.util` package that accumulates unrelated static helpers over years is a sign nothing has an actual home; usually a symptom of package-by-layer taken too far, or of missing domain concepts.
- **`@Autowired` field injection** — hides required dependencies, makes classes impossible to construct without Spring, and cannot be made `final`. Prefer constructor injection (and skip `@Autowired` entirely on a single constructor — Spring infers it).
- **One giant `ApplicationConfig`** — a single configuration class holding every bean and every `@ConfigurationProperties` binding for the whole app becomes an unreviewable dumping ground. Split by concern.
- **Circular package dependencies** — package A imports package B, which imports package A. A sign that a shared concept needs to be extracted, or that a feature boundary is drawn in the wrong place.
- **No versioning strategy, then a breaking change ships** — clients silently break because there was never a plan for how to introduce v2 or deprecate old fields.
- **Over-engineering hexagonal architecture for a 3-endpoint service** — ports, adapters, and mappers for a service that just does CRUD on one table is pure ceremony; the extra layers add cost with no corresponding benefit.
- **Inconsistent naming** — `getUser`, `fetchOrder`, `retrieveCustomer`, `loadPayment` for the exact same kind of operation across services signals no shared convention, and slows every new reader down.
- **`Optional` used as a parameter or field** — `Optional` is meant as a *return type* to signal "may be absent." Using it as a method parameter or entity field just adds a wrapper everyone has to unwrap; use overloads or `null`-safe defaults instead.
- **Mutable shared state in singleton beans** — Spring beans are singletons by default; storing per-request mutable state in an instance field of a `@Service` creates race conditions under concurrent requests.
- **Comments instead of clear names** — `// this checks if the order can be shipped` above a badly named boolean is a sign the code should be renamed (`canBeShipped()`), not commented.

## Quick Recap

- Dependencies point inward: domain has no idea Spring or JPA exist; infrastructure depends on the domain, never the reverse.
- Layered architecture is fine for small CRUD apps; reach for Clean/Hexagonal only when the domain is genuinely complex or integrations will change.
- Dependency Inversion = define interfaces ("ports") where the business logic lives, implement them ("adapters") in infrastructure.
- Map between request DTO, response DTO, domain model, and entity — never return an entity from a controller; prefer MapStruct (compile-time) over reflection-based mappers.
- Build a small domain exception hierarchy, centralize translation to HTTP with `@RestControllerAdvice` and `ProblemDetail` (RFC 9457), never leak stack traces.
- Validate in three places: shape at the edge (Bean Validation), business rules in the domain/service, and constraints in the database as the last line of defence.
- Use typed `@ConfigurationProperties` records, validate them at startup, fail fast, and keep secrets out of the repo.
- Choose package-by-feature for anything expected to grow; use package-private visibility to enforce real boundaries; consider Spring Modulith to verify them automatically.
- Version your API deliberately (URI path is the simplest default), publish a deprecation policy, and know what counts as a breaking change.
- Security and performance are checklists to run on every service, not one-time setup steps — deny by default, validate at the edge, fail closed; measure first, paginate, avoid N+1, timeout every remote call.
- Every "best practice" here is a default with a cost. Know the cost before you pay it, and know when to skip it.
