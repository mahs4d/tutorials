# 6. REST API Design

## Overview

REST (Representational State Transfer) is the dominant style for designing HTTP APIs. It is not a protocol or a library — it is a set of conventions for how clients and servers talk to each other using URLs, HTTP methods, and status codes. Spring Boot makes it easy to expose REST endpoints, but the framework will happily let you build a *bad* REST API just as fast as a good one. Interviewers care about this topic because API design mistakes (leaking entities, ignoring status codes, no pagination, no versioning strategy) are the kind of thing that shows up in code review at every company, on every team. Getting the fundamentals right — resource modeling, DTOs, validation, consistent error handling — is what separates a "works on my machine" endpoint from a production-grade one.

## REST Principles

REST was described by Roy Fielding in his 2000 PhD dissertation. In practice, "RESTful" APIs follow a handful of conventions:

- **Resources, not actions.** URLs identify *things* (`/orders/42`), not verbs (`/getOrder?id=42`).
- **HTTP methods carry the verb.** `GET` reads, `POST` creates, `PUT`/`PATCH` update, `DELETE` removes.
- **Statelessness.** Each request carries everything the server needs (e.g., an auth token). The server does not keep session state about "where the client is" between requests.
- **Uniform interface.** Every resource is manipulated the same way, using standard HTTP semantics (status codes, headers, media types).
- **Client-server separation.** The client and server evolve independently as long as the contract (the API) is respected.
- **Cacheable responses.** Responses declare whether they can be cached (`Cache-Control`, `ETag`), so intermediaries can optimize.
- **Layered system.** Clients don't need to know if they're talking directly to the server or through a proxy/gateway/load balancer.

A simple analogy: think of a REST API like a library's card catalog. Each book (resource) has a fixed location (URL). You don't call a special "fetch-book" procedure for each title — you use the same lookup mechanism (`GET /books/{id}`) for every book.

```java
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @GetMapping("/{id}")
    public OrderResponse getOrder(@PathVariable Long id) {
        return orderService.findById(id);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public OrderResponse createOrder(@RequestBody CreateOrderRequest request) {
        return orderService.create(request);
    }
}
```

Key points:

- HTTP methods have well-defined semantics — don't fight them (e.g., don't use `GET` to delete data).
- `GET`, `PUT`, `DELETE` should be **idempotent** (repeating them has the same effect as doing them once). `POST` usually is not.
- `GET` and `HEAD` should be **safe** (no side effects at all).

| Method | Purpose | Idempotent? | Has a body? |
|---|---|---|---|
| GET | Read a resource | Yes | No |
| POST | Create a resource / trigger an action | No | Yes |
| PUT | Replace a resource entirely | Yes | Yes |
| PATCH | Partially update a resource | Usually not guaranteed | Yes |
| DELETE | Remove a resource | Yes | Usually no |

## Resource Design

A **resource** is a noun: a customer, an order, an invoice. Good resource design means picking clear, consistent, plural nouns for collections and predictable nesting for relationships.

- Use nouns, not verbs: `/products`, not `/getProducts`.
- Use plural nouns for collections: `/orders` (collection), `/orders/42` (single item).
- Nest resources only when the child cannot exist without the parent: `/orders/42/items` is fine because order items belong to an order. Don't nest more than 2 levels deep — it gets unreadable.
- Use query parameters for filtering/sorting/pagination, not for identifying a resource: `/orders?status=SHIPPED`, not `/orders/status/SHIPPED`.
- Keep casing consistent — `kebab-case` in URLs is the most common convention (`/order-items`, not `/orderItems`).

```http
GET /api/customers/123/orders/42/items      → get one item on one order for one customer
GET /api/orders?status=SHIPPED&page=0       → filter + paginate a collection
POST /api/orders                            → create a new order
PUT  /api/orders/42                         → replace order 42 entirely
PATCH /api/orders/42                        → partially update order 42
DELETE /api/orders/42                       → remove order 42
```

```java
@RestController
@RequestMapping("/api/customers/{customerId}/orders")
public class CustomerOrderController {

    @GetMapping
    public List<OrderSummary> listOrders(@PathVariable Long customerId) {
        // ...
        return List.of();
    }

    @GetMapping("/{orderId}")
    public OrderResponse getOrder(@PathVariable Long customerId, @PathVariable Long orderId) {
        // ...
        return null;
    }
}
```

Key points:

- Prefer flat structures over deeply nested ones. `/orders/42/items/7` is fine; `/customers/1/orders/42/items/7/notes/3` is not.
- Model actions that don't map cleanly to CRUD as sub-resources: `POST /orders/42/cancel` rather than `PATCH /orders/42` with a magic `status=CANCELLED` field.
- Return `404 Not Found` for a resource that doesn't exist, not `200` with a null body.

## DTOs

A **DTO (Data Transfer Object)** is a plain object whose only job is to carry data across a boundary — in this case, across the HTTP layer between your API and its clients. DTOs are *not* domain objects; they have no business logic.

Think of a DTO like a shipping manifest: it lists exactly what's in the box for the person receiving it, without exposing the entire warehouse's inventory system behind it.

```java
public record OrderResponse(
        Long id,
        String status,
        BigDecimal totalAmount,
        List<OrderItemResponse> items,
        Instant createdAt
) {}

public record OrderItemResponse(
        Long productId,
        String productName,
        int quantity,
        BigDecimal unitPrice
) {}

public record CreateOrderRequest(
        @NotNull Long customerId,
        @NotEmpty List<@Valid CreateOrderItemRequest> items
) {}
```

Key points:

- Java `record`s (Java 16+) are a great fit for DTOs: immutable, concise, auto-generated `equals`/`hashCode`/`toString`.
- Separate **request** DTOs (what the client sends) from **response** DTOs (what the server returns) — they often need different fields and different validation.
- Mapping between entities and DTOs is usually done with a mapping library (MapStruct) or manual mapper methods — avoid putting mapping logic inside controllers.

```java
@Component
public class OrderMapper {
    public OrderResponse toResponse(Order order) {
        return new OrderResponse(
                order.getId(),
                order.getStatus().name(),
                order.getTotalAmount(),
                order.getItems().stream().map(this::toItemResponse).toList(),
                order.getCreatedAt()
        );
    }

    private OrderItemResponse toItemResponse(OrderItem item) {
        return new OrderItemResponse(
                item.getProduct().getId(),
                item.getProduct().getName(),
                item.getQuantity(),
                item.getUnitPrice()
        );
    }
}
```

## Entity vs DTO

An **entity** is a JPA-managed class that maps to a database table (`@Entity`). A **DTO** is what you expose over HTTP. Conflating the two — returning `@Entity` objects straight from a `@RestController` — is one of the most common REST mistakes in Spring codebases.

| Aspect | Entity | DTO |
|---|---|---|
| Purpose | Persistence (maps to a DB table) | Data transfer over the wire |
| Annotations | `@Entity`, `@Id`, `@OneToMany`, etc. | Validation annotations (`@NotBlank`, etc.) |
| Lifecycle | Managed by JPA / the persistence context | Created fresh per request/response |
| Lazy loading | Can have `LAZY` associations | Never — it's a flat snapshot |
| Exposure risk | High — internal fields, relationships, passwords | Low — you control exactly what's shown |
| Mutability | Often mutable (JPA needs setters/no-args ctor) | Best as immutable `record` |

Why not just return the entity?

- **Leaks internal structure.** Renaming a database column shouldn't break your public API contract — but it will if the entity *is* the contract.
- **Lazy-loading exceptions.** Serializing an entity with a `LAZY` collection outside a transaction throws `LazyInitializationException`.
- **Infinite recursion / huge payloads.** Bidirectional `@OneToMany`/`@ManyToOne` relationships can serialize into infinite loops or massive nested JSON.
- **Security.** You might accidentally expose a password hash, an internal admin flag, or another customer's data.

```java
// ❌ Bad: entity returned directly from controller
@GetMapping("/{id}")
public Order getOrder(@PathVariable Long id) {
    return orderRepository.findById(id).orElseThrow();
}

// ✅ Good: DTO shields the API contract from the persistence model
@GetMapping("/{id}")
public OrderResponse getOrder(@PathVariable Long id) {
    Order order = orderRepository.findById(id)
            .orElseThrow(() -> new OrderNotFoundException(id));
    return orderMapper.toResponse(order);
}
```

## Validation

Spring Boot uses the **Bean Validation** spec (Jakarta Validation, `jakarta.validation.constraints.*`) implemented by Hibernate Validator. You annotate DTO fields with constraints, then trigger validation in the controller with `@Valid` or `@Validated`.

```java
public record CreateOrderItemRequest(
        @NotNull(message = "productId is required")
        Long productId,

        @Min(value = 1, message = "quantity must be at least 1")
        @Max(value = 100, message = "quantity cannot exceed 100")
        int quantity
) {}

public record CreateOrderRequest(
        @NotNull Long customerId,

        @NotEmpty(message = "an order must have at least one item")
        List<@Valid CreateOrderItemRequest> items,

        @Email
        String notificationEmail
) {}
```

```java
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public OrderResponse createOrder(@Valid @RequestBody CreateOrderRequest request) {
        return orderService.create(request);
    }
}
```

When validation fails, Spring throws `MethodArgumentNotValidException`, which you typically catch in a `@RestControllerAdvice` to produce a clean error response (see Problem Details below).

Key points:

- `@Valid` triggers cascading validation into nested objects and collections (note `List<@Valid ...>` above).
- `@Validated` (Spring's own annotation) supports **validation groups**, useful when the same DTO needs different rules for create vs. update.
- Common constraints: `@NotNull`, `@NotBlank`, `@NotEmpty`, `@Size`, `@Min`/`@Max`, `@Pattern`, `@Email`, `@Positive`.
- Custom constraints can be built with `@Constraint` + a `ConstraintValidator` implementation.
- Validate at the **boundary** (DTOs), not deep inside services — fail fast, before touching the database.

```java
// Custom validator example
@Target({ElementType.FIELD})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = AllowedCurrencyValidator.class)
public @interface AllowedCurrency {
    String message() default "unsupported currency code";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}

public class AllowedCurrencyValidator implements ConstraintValidator<AllowedCurrency, String> {
    private static final Set<String> ALLOWED = Set.of("EUR", "USD", "GBP");

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        return value == null || ALLOWED.contains(value);
    }
}
```

## Pagination

**Pagination** means returning results in fixed-size "pages" instead of dumping an entire table in one response. Imagine a phone book with a million entries — no client wants (or can handle) all of it at once; they want it a page at a time.

Spring Data provides `Pageable` and `Page<T>` out of the box.

```java
@GetMapping
public Page<OrderSummary> listOrders(
        @PageableDefault(size = 20, sort = "createdAt", direction = Sort.Direction.DESC)
        Pageable pageable) {
    return orderRepository.findAll(pageable)
            .map(orderMapper::toSummary);
}
```

```http
GET /api/orders?page=0&size=20&sort=createdAt,desc
```

```json
{
  "content": [
    { "id": 42, "status": "SHIPPED", "totalAmount": 129.90 }
  ],
  "page": {
    "number": 0,
    "size": 20,
    "totalElements": 137,
    "totalPages": 7
  }
}
```

| Style | How it works | Pros | Cons |
|---|---|---|---|
| Offset-based (`page`, `size`) | Skip N rows, take M | Simple, supports jumping to any page | Slow / inconsistent on large, changing datasets |
| Cursor-based (`after=<cursor>`) | Use an opaque pointer to the last seen item | Stable under concurrent writes, scales well | Can't jump to arbitrary page number |
| Keyset (`WHERE id > lastId`) | Filter by the last seen key | Fast, index-friendly | Requires a stable sort key |

Key points:

- Never return an unbounded list from a collection endpoint — always paginate, or cap the max page size (e.g., reject `size > 100`).
- Returning raw Spring Data `Page<T>` as JSON works but couples your API contract to Spring's internal representation; many teams wrap it in a custom `PagedResponse<T>` DTO for stability.
- Cap `size` server-side even if the client asks for more (`Math.min(request.size(), 100)`).

## Sorting

**Sorting** lets clients control the order of results. Spring Data's `Sort` (bundled inside `Pageable`) supports multiple sort keys and direction per key.

```java
@GetMapping
public Page<OrderSummary> listOrders(Pageable pageable) {
    return orderRepository.findAll(pageable).map(orderMapper::toSummary);
}
```

```http
GET /api/orders?sort=totalAmount,desc&sort=createdAt,asc
```

```java
// Manual Sort construction, e.g. when not using Pageable directly
Sort sort = Sort.by(Sort.Order.desc("totalAmount"), Sort.Order.asc("createdAt"));
```

Key points:

- Always **whitelist** sortable fields. If you blindly pass a client-supplied field name into `ORDER BY`, you risk exposing internal column names or enabling SQL-injection-adjacent issues with certain query builders.
- Document the allowed sort fields in your OpenAPI spec — don't make clients guess.

```java
// ❌ Bad: any field name is accepted, including internal/unmapped ones
Sort sort = Sort.by(request.getSortField());

// ✅ Good: only allow a known, safe set of sort fields
private static final Set<String> ALLOWED_SORT_FIELDS = Set.of("createdAt", "totalAmount", "status");

Sort sort = Sort.by(
    request.getSortField() != null && ALLOWED_SORT_FIELDS.contains(request.getSortField())
        ? request.getSortField()
        : "createdAt"
);
```

## Filtering

**Filtering** narrows a collection down using query parameters. Simple filters map directly to query params; complex filtering (many optional combinations) is often handled with the **Specification** pattern from Spring Data JPA.

```http
GET /api/orders?status=SHIPPED&minAmount=50&customerId=123
```

```java
@GetMapping
public Page<OrderSummary> listOrders(
        @RequestParam(required = false) OrderStatus status,
        @RequestParam(required = false) BigDecimal minAmount,
        @RequestParam(required = false) Long customerId,
        Pageable pageable) {

    Specification<Order> spec = Specification.where(null);
    if (status != null) {
        spec = spec.and((root, query, cb) -> cb.equal(root.get("status"), status));
    }
    if (minAmount != null) {
        spec = spec.and((root, query, cb) -> cb.greaterThanOrEqualTo(root.get("totalAmount"), minAmount));
    }
    if (customerId != null) {
        spec = spec.and((root, query, cb) -> cb.equal(root.get("customer").get("id"), customerId));
    }

    return orderRepository.findAll(spec, pageable).map(orderMapper::toSummary);
}
```

Key points:

- Every filter parameter should be optional and independently combinable.
- Validate filter values (e.g., an unknown `status` enum value should return `400 Bad Request`, not silently return zero results).
- For very complex filtering needs (dozens of fields, boolean logic), consider a dedicated query DSL (e.g., RSQL, `querydsl`) rather than an ever-growing list of `@RequestParam`s.
- Keep filtering and pagination/sorting orthogonal — a client should be able to combine all three freely.

## HATEOAS

**HATEOAS** (Hypermedia As The Engine Of Application State) means responses include links describing what a client can do *next*, instead of the client hardcoding URLs. It's the most "purist" REST constraint from Fielding's dissertation, and the least adopted in practice.

Analogy: a well-designed website has clickable links guiding you to related pages. HATEOAS brings that same idea to APIs — the response itself tells the client "here's how you cancel this order" via a link, rather than the client needing out-of-band documentation.

Spring provides **Spring HATEOAS** for this.

```java
@GetMapping("/{id}")
public EntityModel<OrderResponse> getOrder(@PathVariable Long id) {
    OrderResponse order = orderService.findById(id);

    EntityModel<OrderResponse> model = EntityModel.of(order);
    model.add(linkTo(methodOn(OrderController.class).getOrder(id)).withSelfRel());
    model.add(linkTo(methodOn(OrderController.class).cancelOrder(id)).withRel("cancel"));
    return model;
}
```

```json
{
  "id": 42,
  "status": "PENDING",
  "totalAmount": 129.90,
  "_links": {
    "self": { "href": "/api/orders/42" },
    "cancel": { "href": "/api/orders/42/cancel" }
  }
}
```

| Pros | Cons |
|---|---|
| Clients can discover available actions dynamically | Adds complexity to both server and client code |
| Decouples client from hardcoded URL structure | Most front-end/mobile teams ignore the links and hardcode URLs anyway |
| Useful for APIs with complex state machines (e.g., order lifecycle) | Rare in practice outside specific domains (banking, some public APIs) |

Key points:

- Most internal/microservice APIs skip HATEOAS entirely — it's more common in public APIs with long-lived external consumers.
- If asked in an interview, know *what it is* and *why it exists*, even if you'd rarely implement it day-to-day.
- `EntityModel<T>` and `CollectionModel<T>` are the Spring HATEOAS wrapper types.

## Versioning

**Versioning** lets an API evolve without breaking existing clients. When you must change a contract in a backward-incompatible way (rename a field, change a type, remove an endpoint), you introduce a new version rather than break v1 clients overnight.

| Strategy | Example | Pros | Cons |
|---|---|---|---|
| URI path | `/api/v1/orders`, `/api/v2/orders` | Simple, visible, easy to route/cache | "Pollutes" the URL; resource identity arguably shouldn't include version |
| Query parameter | `/api/orders?version=2` | Easy to add | Easy to forget; less visible in logs/docs |
| Custom header | `X-API-Version: 2` | Keeps URLs clean | Less discoverable; harder to test with a browser |
| Media type (`Accept` header) | `Accept: application/vnd.myapp.v2+json` | "Proper" REST way (content negotiation) | More complex to implement and document |

```java
@RestController
@RequestMapping("/api/v1/orders")
public class OrderControllerV1 {
    // legacy response shape
}

@RestController
@RequestMapping("/api/v2/orders")
public class OrderControllerV2 {
    // new response shape
}
```

```java
// Header-based versioning example
@GetMapping(value = "/orders", headers = "X-API-Version=2")
public OrderResponseV2 getOrderV2(@PathVariable Long id) { ... }

@GetMapping(value = "/orders", headers = "X-API-Version=1")
public OrderResponseV1 getOrderV1(@PathVariable Long id) { ... }
```

Key points:

- URI-path versioning is the most common in practice — easy to understand, easy to route at the load balancer / API gateway level.
- Prefer **additive, backward-compatible changes** (new optional fields) over bumping a version whenever possible — versioning has a real maintenance cost (you now support N API versions in production).
- Always have a deprecation policy: announce, support in parallel for a period, then sunset — don't just delete v1 overnight.

## Error Responses

Clients need a **consistent, predictable shape** for errors — otherwise every integration ends up special-casing your API. The two pillars are: correct HTTP status codes, and a structured error body.

| Status code | Meaning | Typical cause |
|---|---|---|
| 400 Bad Request | Malformed/invalid input | Failed validation, bad JSON |
| 401 Unauthorized | Missing/invalid credentials | No token, expired token |
| 403 Forbidden | Authenticated but not allowed | Insufficient role/permission |
| 404 Not Found | Resource doesn't exist | Bad ID, wrong URL |
| 409 Conflict | State conflict | Duplicate resource, optimistic-lock failure |
| 422 Unprocessable Entity | Semantically invalid | Business-rule violation (some teams use 400 instead) |
| 500 Internal Server Error | Unhandled exception | Bug, unexpected failure |

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(OrderNotFoundException.class)
    public ResponseEntity<ApiError> handleNotFound(OrderNotFoundException ex) {
        ApiError error = new ApiError("ORDER_NOT_FOUND", ex.getMessage(), Instant.now());
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(error);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleValidation(MethodArgumentNotValidException ex) {
        String message = ex.getBindingResult().getFieldErrors().stream()
                .map(fe -> fe.getField() + ": " + fe.getDefaultMessage())
                .collect(Collectors.joining(", "));
        ApiError error = new ApiError("VALIDATION_FAILED", message, Instant.now());
        return ResponseEntity.badRequest().body(error);
    }
}

public record ApiError(String code, String message, Instant timestamp) {}
```

Key points:

- Never leak stack traces or internal exception messages to clients in production — log them server-side, return a generic, safe message.
- Use a machine-readable `code` field (e.g., `ORDER_NOT_FOUND`) in addition to a human-readable `message`, so clients can branch on it without string-matching.
- Handle exceptions centrally with `@RestControllerAdvice` — don't scatter `try/catch` blocks across every controller method.

## Problem Details (RFC 9457)

**RFC 9457** ("Problem Details for HTTP APIs", the successor to RFC 7807) defines a standard JSON shape for error responses, so every API — regardless of who built it — can return errors in the same structure. Spring Boot 3 has **built-in support** for this via `ProblemDetail` and `ErrorResponse`.

The standard fields are:

- `type` — a URI identifying the problem type (defaults to `about:blank`)
- `title` — a short, human-readable summary
- `status` — the HTTP status code
- `detail` — a human-readable explanation specific to this occurrence
- `instance` — a URI identifying this specific occurrence

Enable it globally in `application.yml`:

```yaml
spring:
  mvc:
    problemdetails:
      enabled: true
```

With this enabled, Spring Boot's default error handling (and exceptions like `MethodArgumentNotValidException`, `NoResourceFoundException`, etc.) automatically render as `application/problem+json`.

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(OrderNotFoundException.class)
    public ProblemDetail handleNotFound(OrderNotFoundException ex) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, ex.getMessage());
        problem.setTitle("Order Not Found");
        problem.setType(URI.create("https://api.example.com/problems/order-not-found"));
        problem.setProperty("orderId", ex.getOrderId());
        return problem;
    }
}
```

You can also throw a subclass of `ErrorResponseException`, which lets an exception carry its own `ProblemDetail`:

```java
public class OrderNotFoundException extends ErrorResponseException {

    public OrderNotFoundException(Long orderId) {
        super(HttpStatus.NOT_FOUND, asProblemDetail(orderId), null);
    }

    private static ProblemDetail asProblemDetail(Long orderId) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(
                HttpStatus.NOT_FOUND, "Order " + orderId + " was not found");
        problem.setTitle("Order Not Found");
        problem.setProperty("orderId", orderId);
        return problem;
    }
}
```

Example response body:

```json
{
  "type": "https://api.example.com/problems/order-not-found",
  "title": "Order Not Found",
  "status": 404,
  "detail": "Order 42 was not found",
  "instance": "/api/orders/42",
  "orderId": 42
}
```

Key points:

- `ProblemDetail` is part of Spring Framework 6 (`org.springframework.http.ProblemDetail`), no extra dependency required.
- `Content-Type` for problem responses is `application/problem+json`, not plain `application/json`.
- `setProperty(...)` lets you add custom fields beyond the RFC's standard ones — very handy for machine-readable error codes.
- Since Spring Boot 3, this is the recommended, idiomatic way to build error responses — prefer it over a fully custom `ApiError` shape for new projects.

## Idempotency

An operation is **idempotent** if performing it multiple times has the same effect as performing it once. `GET`, `PUT`, and `DELETE` are supposed to be idempotent by HTTP's own definition. `POST` is not — calling `POST /orders` twice normally creates two orders.

Real-world problem: a mobile client submits `POST /payments`, the request succeeds server-side, but the response is lost on a flaky connection. The client retries. Without protection, the customer is charged twice.

**Idempotency keys** solve this: the client generates a unique key per logical operation (often a UUID) and sends it in a header. The server remembers which keys it has already processed and returns the *original* response for a repeat.

```http
POST /api/payments
Idempotency-Key: 6f9619ff-8b86-4d91-93b0-6bd7bb2cf7f7
Content-Type: application/json

{ "orderId": 42, "amount": 129.90 }
```

```java
@RestController
@RequestMapping("/api/payments")
public class PaymentController {

    private final PaymentService paymentService;
    private final IdempotencyStore idempotencyStore;

    public PaymentController(PaymentService paymentService, IdempotencyStore idempotencyStore) {
        this.paymentService = paymentService;
        this.idempotencyStore = idempotencyStore;
    }

    @PostMapping
    public ResponseEntity<PaymentResponse> createPayment(
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @Valid @RequestBody CreatePaymentRequest request) {

        return idempotencyStore.findResponse(idempotencyKey)
                .map(ResponseEntity::ok)
                .orElseGet(() -> {
                    PaymentResponse response = paymentService.process(request);
                    idempotencyStore.save(idempotencyKey, response);
                    return ResponseEntity.status(HttpStatus.CREATED).body(response);
                });
    }
}
```

Key points:

- Idempotency keys are typically stored with a TTL (e.g., 24 hours) in a fast store like Redis.
- The key should be scoped per logical operation, not reused across unrelated requests.
- `PUT` is naturally idempotent *if* implemented correctly — replacing a resource with the same payload twice should leave it in the same state. Be careful with server-generated timestamps that break this (`updatedAt = now()` on every `PUT` technically breaks strict idempotency of the response, though the resource's *meaningful* state is unchanged).
- `DELETE` should be idempotent too: deleting an already-deleted resource should return `404` (or `204`) consistently, not throw a `500`.

## OpenAPI / Swagger

**OpenAPI** is a specification format (JSON/YAML) for describing a REST API: its endpoints, parameters, request/response schemas, and authentication. **Swagger UI** is the most popular tool for rendering that spec as interactive, browsable documentation. The old "Springfox" library is unmaintained and does not support Spring Boot 3 — use **springdoc-openapi** instead.

```xml
<dependency>
    <groupId>org.springdoc</groupId>
    <artifactId>springdoc-openapi-starter-webmvc-ui</artifactId>
    <version>2.5.0</version>
</dependency>
```

With this single dependency, springdoc automatically generates an OpenAPI spec by scanning your `@RestController`s and DTOs — no manual YAML required for the basics. It's exposed at:

- `/v3/api-docs` — the raw OpenAPI JSON
- `/swagger-ui.html` — the interactive UI

You can enrich the generated docs with annotations:

```java
@RestController
@RequestMapping("/api/orders")
@Tag(name = "Orders", description = "Endpoints for managing customer orders")
public class OrderController {

    @Operation(summary = "Get an order by ID", description = "Returns a single order, including its line items.")
    @ApiResponses({
        @ApiResponse(responseCode = "200", description = "Order found",
                content = @Content(schema = @Schema(implementation = OrderResponse.class))),
        @ApiResponse(responseCode = "404", description = "Order not found", content = @Content)
    })
    @GetMapping("/{id}")
    public OrderResponse getOrder(
            @Parameter(description = "The order's unique identifier") @PathVariable Long id) {
        return orderService.findById(id);
    }
}
```

Global metadata (title, version, contact info) is configured with a `@Bean`:

```java
@Configuration
public class OpenApiConfig {

    @Bean
    public OpenAPI apiInfo() {
        return new OpenAPI()
                .info(new Info()
                        .title("Order Service API")
                        .version("v1")
                        .description("REST API for managing orders and payments"));
    }
}
```

```yaml
# application.yml — customizing paths
springdoc:
  api-docs:
    path: /v3/api-docs
  swagger-ui:
    path: /swagger-ui.html
```

Key points:

- springdoc-openapi supports Spring MVC (`webmvc`) and WebFlux (`webflux`) — pick the starter matching your stack.
- The generated spec can be exported and fed into client-code generators (`openapi-generator`) to produce typed SDKs for other teams.
- Keep DTOs annotated with `@Schema(description = "...")` on fields for richer docs — the schema is derived from your DTOs, so good DTO naming pays off twice (in code readability and in docs quality).
- Lock down Swagger UI in production (behind auth, or disabled entirely) — it's a detailed map of your API surface and shouldn't be publicly exposed by default on sensitive systems.

## Common Code Review / Interview Pitfalls

- **Returning JPA entities directly from controllers.** This leaks persistence details and risks `LazyInitializationException` or infinite recursion on bidirectional relationships. Fix: always map entities to DTOs before returning them.

  ```java
  // ❌ Bad
  @GetMapping("/{id}")
  public Order getOrder(@PathVariable Long id) { return repo.findById(id).orElseThrow(); }

  // ✅ Good
  @GetMapping("/{id}")
  public OrderResponse getOrder(@PathVariable Long id) { return mapper.toResponse(repo.findById(id).orElseThrow()); }
  ```

- **Using `GET` for operations with side effects.** `GET` must be safe (no state change); using it to delete or mutate data breaks caching, prefetching, and REST semantics. Fix: use `POST`/`PUT`/`DELETE` for anything that changes state.

- **Unbounded collection endpoints.** Returning "all orders" with no pagination will eventually return millions of rows and take down the service. Fix: always paginate list endpoints, and enforce a maximum page size server-side.

- **Wrong or inconsistent HTTP status codes.** Returning `200 OK` with an error message in the body (or `500` for a simple "not found") confuses every client integration. Fix: match status codes to their documented HTTP semantics (404 for missing, 400 for bad input, 409 for conflicts).

- **Accepting any client-supplied field for sorting/filtering without validation.** This can expose internal column names or let clients trigger expensive, unindexed queries. Fix: whitelist allowed sort/filter fields explicitly.

  ```java
  // ❌ Bad
  Sort.by(request.getSortField());

  // ✅ Good
  Sort.by(ALLOWED_SORT_FIELDS.contains(request.getSortField()) ? request.getSortField() : "createdAt");
  ```

- **No input validation on request DTOs.** Skipping `@Valid`/Bean Validation means bad data (negative quantities, missing required fields) reaches the service and database layer, where errors are harder to diagnose. Fix: annotate DTOs with constraints and add `@Valid` in the controller.

- **Leaking stack traces or internal exception messages to clients.** This exposes implementation details and can aid attackers. Fix: use a `@RestControllerAdvice` that returns sanitized, generic error messages while logging the full exception server-side.

- **Inconsistent error response shapes across endpoints.** If every controller invents its own error JSON, client code has to special-case each endpoint. Fix: centralize error handling and adopt a single standard shape — ideally RFC 9457 `ProblemDetail`.

- **No API versioning strategy from day one.** Teams that skip versioning early end up making breaking changes to a "v1" that external clients already depend on. Fix: pick a versioning strategy (commonly URI path) before the first external consumer integrates.

- **Treating `PATCH` and `PUT` as interchangeable.** `PUT` should replace the whole resource; `PATCH` should apply a partial update. Mixing them up leads to accidental data loss (a `PUT` with a partial payload nulling out fields the client didn't send). Fix: implement `PUT` as a full replace and `PATCH` as a true partial update (e.g., using `JsonNullable` or a merge-patch library).

- **No idempotency protection on `POST` endpoints that trigger real-world side effects (payments, emails, order creation).** Network retries can cause duplicate charges or duplicate orders. Fix: support an `Idempotency-Key` header and de-duplicate on the server.

- **Ignoring `Content-Type`/`Accept` negotiation.** Hardcoding `application/json` everywhere and ignoring the `Accept` header breaks clients that need `application/problem+json` or other media types. Fix: let Spring's content negotiation do its job; don't manually serialize JSON strings.

- **Publicly exposing Swagger UI / OpenAPI docs on a production system with sensitive internal APIs.** This hands attackers a complete map of your API surface. Fix: restrict `/swagger-ui.html` and `/v3/api-docs` behind authentication or disable them in production profiles.

- **Deeply nested resource URLs.** URLs like `/customers/1/orders/42/items/7/notes/3/replies/9` are hard to read, hard to route, and usually signal a modeling problem. Fix: flatten to 1–2 levels of nesting and use query parameters or dedicated endpoints for the rest.

- **Forgetting to cap page size.** Even with pagination implemented, if a client can request `?size=100000`, you haven't actually solved the unbounded-response problem. Fix: clamp requested page size to a sane server-defined maximum.

## Quick Recap

- REST models APIs around **resources** (nouns) manipulated via standard **HTTP methods** (verbs), and stays **stateless**.
- Design URLs with plural nouns, shallow nesting, and query params for filtering/sorting/pagination — not for identifying resources.
- **DTOs** decouple your public API contract from internal implementation; **never return JPA entities directly**.
- Validate all inbound DTOs with Jakarta Bean Validation (`@Valid`, `@NotNull`, `@Size`, etc.) — fail fast at the boundary.
- Always **paginate** collection endpoints and cap the maximum page size; combine cleanly with **sorting** and **filtering**, whitelisting any client-supplied field names.
- **HATEOAS** adds discoverable links to responses — conceptually important, rarely implemented outside specific domains.
- Pick an explicit **versioning** strategy (URI path is the most common) before external clients depend on your API.
- Use **consistent, structured error responses** — Spring Boot 3's built-in `ProblemDetail` (RFC 9457) is the modern, idiomatic choice (`spring.mvc.problemdetails.enabled: true`).
- Protect side-effecting `POST` endpoints with **idempotency keys** to survive client retries safely.
- Document your API with **springdoc-openapi** (not Springfox) and lock down Swagger UI in production.
- The most common real-world review flags: leaking entities, unbounded lists, wrong status codes, missing validation, and no versioning plan.
