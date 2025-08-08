# Day 14: Validation, DTOs & API Contracts

| | |
|---|---|
| 🏗️ **Project** | **ContractAPI** — a validated DTO ↔ entity API boundary |
| ☕ **Java & language skills** | Records as DTOs, bean validation annotations, `@Valid`, annotation processing, exception handling |
| 🧰 **Library / tool** | Hibernate Validator + MapStruct |
| 🗄️ **DB / distributed-systems concept** | API contracts & the entity↔DTO boundary (mass-assignment, versioning) |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. The boundary: why entities must not cross the wire

On Day 12 you mapped an `Order` (and an `Author`/`Book`) to a database table with JPA. It's tempting to take that same `@Entity` and slap it on a `@RestController` as both `@RequestBody` and return type. One class, zero mapping, ship it. This is the single most common mistake in Spring codebases, and it's wrong on four independent axes.

**(a) Mass-assignment / over-posting — a real vulnerability.** When Jackson deserializes a JSON body into an entity, it will happily set *any* field the JSON names that has a matching setter or constructor param. Suppose your `Order` entity has:

```java
@Entity
class Order {
    @Id Long id;
    String customer;
    BigDecimal amount;
    String status;       // CREATED, PAID, CANCELLED — server-controlled
    boolean internalFlag;// pricing override, set only by ops
}
```

A client `POST`s:

```json
{ "customer": "alice", "amount": 49.99, "status": "PAID", "internalFlag": true }
```

If you bound the body straight to the entity, the customer just marked their own order **PAID** without paying and flipped an internal pricing flag. This is the GitHub 2012 mass-assignment incident in miniature (an attacker added their key to any repo by posting an extra field). The DTO is the fix: the request DTO simply **doesn't have** `status` or `internalFlag`, so there's no field for the attacker to over-post into. *The contract is the allow-list.*

**(b) Leaking internals.** Entities carry things the outside world should never see: surrogate primary keys you'd rather not expose, audit columns, soft-delete flags, password hashes, FK ids, `@Version` columns. Serialize the entity and it all goes out the door.

**(c) Schema coupling.** If the JSON *is* the table, then renaming a column, splitting a field, or changing a type is now a **breaking API change**. Every consumer breaks when you refactor your database. The DTO decouples these two rates of change: the schema can evolve behind a stable contract.

**(d) Lazy-loading explosions.** Serializing a JPA entity with lazy `@OneToMany` associations triggers `LazyInitializationException` (session closed) or, worse, accidentally fetches and serializes the entire object graph — the N+1 problem from Day 12, now leaking into your JSON. A response DTO only contains the fields you explicitly map.

> **Rule of thumb:** the entity is your *persistence* model; the DTO is your *contract* model. They look similar today and will diverge tomorrow. Keep them separate from day one — retrofitting DTOs into a code base that leaks entities is painful.

### 2. Request DTO vs response DTO

They are **not** the same object, and conflating them re-introduces the over-posting problem:

- A **request DTO** is the allow-list of what a client may set. It omits server-controlled fields (`id`, `status`, `createdAt`). It carries validation annotations.
- A **response DTO** is what you choose to reveal. It *includes* server-computed fields (`id`, `status`, `createdAt`) and omits secrets. It carries no validation annotations (you're producing it, not validating it).

Even when they share most fields today, model them separately. `CreateOrderRequest` and `OrderResponse` will drift — that's the point.

### 3. Bean Validation — declarative, at the boundary

Jakarta Bean Validation (JSR 380, implemented by Hibernate Validator) lets you express constraints declaratively as annotations and have them enforced automatically. When you put `@Valid` on a `@RequestBody`, Spring runs the validator *before* your controller method body executes. If anything fails, Spring throws `MethodArgumentNotValidException` and your controller code never runs with bad data.

The senior insight: **validate at the edge, trust within.** Once a DTO has passed validation at the controller boundary, the rest of the application can assume it is well-formed. You don't sprinkle null checks through the service layer. The boundary is the one place that distrusts input.

### 4. Compile-time mapping (MapStruct) vs runtime mapping (ModelMapper)

Once you have DTOs, you need to map `CreateOrderRequest → Order` and `Order → OrderResponse`. Three options:

1. **Hand-written mappers** — explicit, fast, but tedious and error-prone (forget a field, it silently stays null).
2. **Reflection-based mappers (ModelMapper, Dozer)** — match fields by name at *runtime* using reflection. Convenient, but: slow (reflection on every call), type-*unsafe* (a renamed field fails silently at runtime, not at compile time), and opaque (matching is "magic" you debug at 3 a.m.).
3. **Compile-time code generators (MapStruct)** — you declare an interface; an **annotation processor** generates a plain Java implementation **at build time** that does `target.setX(source.getX())`. No reflection at runtime. It's exactly the code you'd hand-write, written for you.

Why a senior reaches for MapStruct:
- **Performance:** generated getter/setter calls, zero reflection. Effectively free at runtime.
- **Compile-time safety:** if a target property has no source and no mapping rule, MapStruct **fails the build** with a warning/error. A renamed field breaks the *build*, not production.
- **Debuggability:** the generated source is readable Java you can step into. No proxy magic.
- **Spring integration:** `componentModel = "spring"` makes each mapper a `@Component` you inject like any bean.

This is the same philosophy as Day 13's Flyway (explicit, versioned, reviewable) over "auto" magic — push surprises to build time, not runtime.

### 5. The API contract as a distributed-systems concern

An HTTP API is a **contract** between a producer (your service) and consumers (other services, mobile apps, partners) that you do **not** deploy together. This is the crux of microservices: **independent deployability**. If changing your service forces every consumer to redeploy in lockstep, you have a distributed monolith, not microservices.

A contract change is either:
- **Backward-compatible (non-breaking):** adding an optional field, adding a new endpoint, adding an enum value consumers can ignore. Old clients keep working. Deploy freely.
- **Breaking:** removing/renaming a field, making an optional field required, changing a type, tightening validation. Old clients break.

Breaking changes demand **versioning** so old and new consumers coexist during the migration window:
- **URI versioning:** `/v1/orders`, `/v2/orders` — explicit, cache-friendly, most common.
- **Header / media-type versioning:** `Accept: application/vnd.acme.v2+json` — "purer" REST, but harder to test/curl.
- **The robustness principle (Postel's law):** be conservative in what you send, liberal in what you accept — e.g. ignore unknown JSON fields on input (`FAIL_ON_UNKNOWN_PROPERTIES=false`) so a newer client talking to an older server doesn't explode.

DTOs are *what makes contract stability achievable*: because the wire format is a deliberate, separate type, you can keep `v1`'s `OrderResponse` frozen while the entity and a `v2` DTO evolve. This thread continues into Kafka schemas (Day 18) and event-sourcing payload evolution (Day 19) — the same producer/consumer compatibility problem, just over a different transport.

---

## Prerequisites

- The **Day 12** Spring Boot + JPA project (an `Order` `@Entity`, a `JpaRepository`, an H2 or Postgres datasource). You're extending it.
- `spring-boot-starter-web` and `spring-boot-starter-validation` on the classpath.
- JDK 17+ (records, `HexFormat`, etc.). Base package `com.example.demo` — adjust to yours.

### Maven dependencies

`spring-boot-starter-validation` brings in Hibernate Validator transitively:

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-validation</artifactId>
</dependency>
```

MapStruct is **two** pieces — the runtime API and the build-time annotation processor:

```xml
<properties>
  <org.mapstruct.version>1.6.3</org.mapstruct.version>
</properties>

<dependencies>
  <dependency>
    <groupId>org.mapstruct</groupId>
    <artifactId>mapstruct</artifactId>
    <version>${org.mapstruct.version}</version>
  </dependency>
</dependencies>
```

**The critical, easy-to-miss part:** MapStruct generates code via the `javac` annotation processor, so it must be registered on the compiler plugin's `annotationProcessorPaths`. If you use Lombok, **order matters** — `lombok` and `lombok-mapstruct-binding` must precede `mapstruct-processor` so MapStruct sees Lombok-generated getters/setters. (We use records + plain getters here, so Lombok is optional; the snippet shows the correct order anyway.)

```xml
<build>
  <plugins>
    <plugin>
      <groupId>org.apache.maven.plugins</groupId>
      <artifactId>maven-compiler-plugin</artifactId>
      <configuration>
        <annotationProcessorPaths>
          <!-- If you use Lombok, these two come FIRST: -->
          <!--
          <path>
            <groupId>org.projectlombok</groupId>
            <artifactId>lombok</artifactId>
            <version>${lombok.version}</version>
          </path>
          <path>
            <groupId>org.projectlombok</groupId>
            <artifactId>lombok-mapstruct-binding</artifactId>
            <version>0.2.0</version>
          </path>
          -->
          <path>
            <groupId>org.mapstruct</groupId>
            <artifactId>mapstruct-processor</artifactId>
            <version>${org.mapstruct.version}</version>
          </path>
        </annotationProcessorPaths>
        <compilerArgs>
          <!-- Helpful build-time diagnostics: -->
          <compilerArg>-Amapstruct.unmappedTargetPolicy=WARN</compilerArg>
          <compilerArg>-Amapstruct.defaultComponentModel=spring</compilerArg>
        </compilerArgs>
      </configuration>
    </plugin>
  </plugins>
</build>
```

> After a successful compile, look in `target/generated-sources/annotations/com/example/demo/order/OrderMapperImpl.java`. Reading that generated file is the single best way to *understand* MapStruct — it's just getters and setters.

---

## 🛠️ Project Walkthrough — ContractAPI

Roll up your sleeves and build the validated DTO + mapping layer step by step, from the persistence entity through to running the app and exercising it with curl.

---

## Step 1 — The JPA entity (recap from Day 12, with the dangerous fields)

This is your persistence model. Note the fields a client must **not** be able to set directly.

`src/main/java/com/example/demo/order/Order.java`:

```java
package com.example.demo.order;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "orders")
public class Order {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;                 // server-assigned — never from the client

    @Column(nullable = false)
    private String customer;

    @Column(nullable = false)
    private String email;

    @Column(nullable = false)
    private BigDecimal amount;

    @Column(nullable = false)
    private String status;           // server-controlled lifecycle

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Version
    private Long version;            // optimistic-lock column — internal

    protected Order() { }            // JPA needs a no-arg ctor

    public Order(String customer, String email, BigDecimal amount) {
        this.customer = customer;
        this.email = email;
        this.amount = amount;
        this.status = "CREATED";
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public String getCustomer() { return customer; }
    public void setCustomer(String customer) { this.customer = customer; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
    public BigDecimal getAmount() { return amount; }
    public void setAmount(BigDecimal amount) { this.amount = amount; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public Instant getCreatedAt() { return createdAt; }
    public Long getVersion() { return version; }
}
```

If you serialized *this* over HTTP, clients could read `version` and try to set `status`. We will not.

---

## Step 2 — Request and response DTOs as records

`src/main/java/com/example/demo/order/dto/CreateOrderRequest.java` — the **inbound allow-list**. Notice what is *absent*: `id`, `status`, `createdAt`, `version`. There is no field for a client to over-post into.

```java
package com.example.demo.order.dto;

import jakarta.validation.constraints.*;
import java.math.BigDecimal;

public record CreateOrderRequest(

        @NotBlank(message = "customer must not be blank")
        @Size(max = 100, message = "customer must be at most 100 characters")
        String customer,

        @NotBlank(message = "email is required")
        @Email(message = "email must be a valid address")
        String email,

        @NotNull(message = "amount is required")
        @Positive(message = "amount must be positive")
        @Digits(integer = 8, fraction = 2, message = "amount must have at most 2 decimal places")
        BigDecimal amount
) { }
```

`src/main/java/com/example/demo/order/dto/UpdateStatusRequest.java` — a separate, narrow DTO for the one legitimate way to change status. The validity of the value is enforced by a **custom constraint** (Step 4).

```java
package com.example.demo.order.dto;

import com.example.demo.order.validation.ValidOrderStatus;
import jakarta.validation.constraints.NotBlank;

public record UpdateStatusRequest(
        @NotBlank(message = "status is required")
        @ValidOrderStatus
        String status
) { }
```

`src/main/java/com/example/demo/order/dto/OrderResponse.java` — the **outbound** view. It includes server-computed fields but deliberately omits `version`.

```java
package com.example.demo.order.dto;

import java.math.BigDecimal;
import java.time.Instant;

public record OrderResponse(
        Long id,
        String customer,
        String email,
        BigDecimal amount,
        String status,
        Instant createdAt
) { }
```

---

## Step 3 — The MapStruct mapper

Declare an **interface**; MapStruct generates the implementation at compile time.

`src/main/java/com/example/demo/order/OrderMapper.java`:

```java
package com.example.demo.order;

import com.example.demo.order.dto.CreateOrderRequest;
import com.example.demo.order.dto.OrderResponse;
import org.mapstruct.Mapper;
import org.mapstruct.Mapping;
import org.mapstruct.MappingTarget;
import org.mapstruct.factory.Mappers;

import java.util.List;

@Mapper(componentModel = "spring")
public interface OrderMapper {

    /**
     * Entity -> response DTO. All target fields have a same-named source,
     * so MapStruct maps them automatically. `version` is not on OrderResponse,
     * so it's simply never read — no leak.
     */
    OrderResponse toResponse(Order order);

    List<OrderResponse> toResponseList(List<Order> orders);

    /**
     * Request DTO -> NEW entity. We deliberately do NOT map id/status/createdAt/version
     * from the request — the entity's constructor sets status/createdAt, and id/version
     * are DB-assigned. `ignore = true` documents that intent AND silences the
     * unmappedTargetPolicy=WARN we configured, proving the omission is on purpose.
     */
    @Mapping(target = "id", ignore = true)
    @Mapping(target = "status", ignore = true)
    @Mapping(target = "createdAt", ignore = true)
    @Mapping(target = "version", ignore = true)
    Order toEntity(CreateOrderRequest req);

    /**
     * Update an EXISTING managed entity in place from a request DTO.
     * @MappingTarget tells MapStruct to mutate the passed-in entity rather
     * than create a new one — perfect for PUT/PATCH on a loaded entity.
     */
    @Mapping(target = "id", ignore = true)
    @Mapping(target = "status", ignore = true)
    @Mapping(target = "createdAt", ignore = true)
    @Mapping(target = "version", ignore = true)
    void updateEntity(CreateOrderRequest req, @MappingTarget Order order);
}
```

> **`toEntity` caveat with records + constructors:** our `Order` has no public no-arg constructor and no setters for `id/status/createdAt`. MapStruct prefers the all-args/best-matching constructor or setters. Because our only public constructor is `Order(customer, email, amount)`, MapStruct will use it and the `ignore`d targets are naturally unset — exactly what we want. If MapStruct can't find a usable constructor it fails the build (compile-time safety in action). For mixed cases, annotate the intended constructor with `@org.mapstruct.factory`/use a `@ObjectFactory` method.

After `./mvnw compile`, the generated `OrderMapperImpl` looks like (abridged):

```java
@Component
public class OrderMapperImpl implements OrderMapper {
    @Override
    public OrderResponse toResponse(Order order) {
        if (order == null) return null;
        return new OrderResponse(
            order.getId(), order.getCustomer(), order.getEmail(),
            order.getAmount(), order.getStatus(), order.getCreatedAt());
    }
    @Override
    public Order toEntity(CreateOrderRequest req) {
        if (req == null) return null;
        return new Order(req.customer(), req.email(), req.amount());
    }
    // ...
}
```

Plain Java. No reflection. This is the whole pitch.

---

## Step 4 — A custom constraint (`@ValidOrderStatus`)

Built-in annotations cover format; business rules need custom constraints. Here: a status must be one of a known set. A custom constraint is two parts — the annotation and the validator.

`src/main/java/com/example/demo/order/validation/ValidOrderStatus.java`:

```java
package com.example.demo.order.validation;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import java.lang.annotation.*;

@Documented
@Constraint(validatedBy = OrderStatusValidator.class)
@Target({ ElementType.FIELD, ElementType.PARAMETER, ElementType.RECORD_COMPONENT })
@Retention(RetentionPolicy.RUNTIME)
public @interface ValidOrderStatus {
    String message() default "status must be one of CREATED, PAID, CANCELLED";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}
```

`src/main/java/com/example/demo/order/validation/OrderStatusValidator.java`:

```java
package com.example.demo.order.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import java.util.Set;

public class OrderStatusValidator
        implements ConstraintValidator<ValidOrderStatus, String> {

    private static final Set<String> ALLOWED = Set.of("CREATED", "PAID", "CANCELLED");

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        // null is handled by @NotBlank; a missing value shouldn't also fail here.
        return value == null || ALLOWED.contains(value);
    }
}
```

> **Why `null` returns `true`:** the single-responsibility rule for validators — let `@NotNull`/`@NotBlank` own nullness, and let `@ValidOrderStatus` own "is it a known value." Otherwise a blank value yields two confusing errors for the same field.

---

## Step 5 — The service, using the mapper

The service speaks entities internally; it accepts a validated request DTO and returns an entity. Mapping to the response DTO happens at the controller edge (or here — both are fine; keeping it in the controller keeps the service transport-agnostic).

`src/main/java/com/example/demo/order/OrderService.java`:

```java
package com.example.demo.order;

import com.example.demo.order.dto.CreateOrderRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.List;

@Service
public class OrderService {

    private final OrderRepository repository;   // JpaRepository<Order, Long> from Day 12
    private final OrderMapper mapper;

    public OrderService(OrderRepository repository, OrderMapper mapper) {
        this.repository = repository;
        this.mapper = mapper;
    }

    @Transactional
    public Order create(CreateOrderRequest req) {
        Order order = mapper.toEntity(req);     // status/createdAt set by ctor, NOT the client
        return repository.save(order);
    }

    @Transactional(readOnly = true)
    public List<Order> list() {
        return repository.findAll();
    }

    @Transactional(readOnly = true)
    public Order get(Long id) {
        return repository.findById(id)
                .orElseThrow(() -> new OrderNotFoundException(id));
    }

    @Transactional
    public Order updateStatus(Long id, String newStatus) {
        Order order = get(id);                  // managed entity within the tx
        order.setStatus(newStatus);             // dirty-checked & flushed on commit
        return order;
    }
}
```

`OrderNotFoundException` is the same one from Day 10 (`extends RuntimeException`).

---

## Step 6 — The controller with `@Valid`

`@Valid` on the `@RequestBody` triggers Bean Validation *before* the method body runs. The controller maps entities to response DTOs and never returns an entity.

`src/main/java/com/example/demo/order/OrderController.java`:

```java
package com.example.demo.order;

import com.example.demo.order.dto.CreateOrderRequest;
import com.example.demo.order.dto.OrderResponse;
import com.example.demo.order.dto.UpdateStatusRequest;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.support.ServletUriComponentsBuilder;

import java.net.URI;
import java.util.List;

@RestController
@RequestMapping("/v1/orders")     // URI versioning from day one — see notes
public class OrderController {

    private final OrderService service;
    private final OrderMapper mapper;

    public OrderController(OrderService service, OrderMapper mapper) {
        this.service = service;
        this.mapper = mapper;
    }

    @GetMapping
    public List<OrderResponse> list() {
        return mapper.toResponseList(service.list());
    }

    @GetMapping("/{id}")
    public OrderResponse get(@PathVariable Long id) {
        return mapper.toResponse(service.get(id));
    }

    @PostMapping
    public ResponseEntity<OrderResponse> create(@Valid @RequestBody CreateOrderRequest req) {
        Order created = service.create(req);
        URI location = ServletUriComponentsBuilder
                .fromCurrentRequest().path("/{id}")
                .buildAndExpand(created.getId()).toUri();
        return ResponseEntity.created(location).body(mapper.toResponse(created));
    }

    // The ONLY sanctioned path to change status — guarded by @ValidOrderStatus.
    @PatchMapping("/{id}/status")
    public OrderResponse updateStatus(@PathVariable Long id,
                                      @Valid @RequestBody UpdateStatusRequest req) {
        return mapper.toResponse(service.updateStatus(id, req.status()));
    }
}
```

> **Note `@Valid` vs `@Validated`:** `@Valid` (Jakarta) on a `@RequestBody` triggers body validation and yields `MethodArgumentNotValidException`. For validating `@RequestParam`/`@PathVariable` directly (method-level constraints), put Spring's `@Validated` on the *class*; those failures throw `ConstraintViolationException` (and need a separate handler — see notes).

---

## Step 7 — Clean validation error responses with `@ControllerAdvice`

This is the payoff: a malformed payload becomes a precise, field-level JSON body, not a stack trace.

`src/main/java/com/example/demo/error/ApiError.java` (same shape as Day 10):

```java
package com.example.demo.error;

import java.time.Instant;
import java.util.Map;

public record ApiError(
        Instant timestamp,
        int status,
        String error,
        String message,
        Map<String, String> fieldErrors
) { }
```

`src/main/java/com/example/demo/error/GlobalExceptionHandler.java`:

```java
package com.example.demo.error;

import com.example.demo.order.OrderNotFoundException;
import jakarta.validation.ConstraintViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ExceptionHandler;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

@ControllerAdvice
public class GlobalExceptionHandler {

    // 422 — body parsed, but @Valid on the @RequestBody failed.
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleBodyValidation(MethodArgumentNotValidException ex) {
        Map<String, String> fieldErrors = new LinkedHashMap<>();
        for (FieldError fe : ex.getBindingResult().getFieldErrors()) {
            // keep the FIRST message per field for a stable, predictable body
            fieldErrors.putIfAbsent(fe.getField(), fe.getDefaultMessage());
        }
        return build(HttpStatus.UNPROCESSABLE_ENTITY, "Validation failed", fieldErrors);
    }

    // 422 — @Validated on @RequestParam/@PathVariable (method-level) failed.
    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ApiError> handleParamValidation(ConstraintViolationException ex) {
        Map<String, String> fieldErrors = new LinkedHashMap<>();
        ex.getConstraintViolations().forEach(v ->
                fieldErrors.putIfAbsent(v.getPropertyPath().toString(), v.getMessage()));
        return build(HttpStatus.UNPROCESSABLE_ENTITY, "Validation failed", fieldErrors);
    }

    // 400 — body didn't even parse (malformed JSON, wrong type).
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> handleUnreadable(HttpMessageNotReadableException ex) {
        return build(HttpStatus.BAD_REQUEST, "Malformed request body", Map.of());
    }

    // 404 — resource missing (from Day 10).
    @ExceptionHandler(OrderNotFoundException.class)
    public ResponseEntity<ApiError> handleNotFound(OrderNotFoundException ex) {
        return build(HttpStatus.NOT_FOUND, ex.getMessage(), Map.of());
    }

    private ResponseEntity<ApiError> build(HttpStatus status, String message,
                                           Map<String, String> fieldErrors) {
        ApiError body = new ApiError(Instant.now(), status.value(),
                status.getReasonPhrase(), message, fieldErrors);
        return ResponseEntity.status(status).body(body);
    }
}
```

> **400 vs 422 (the Day 10 distinction, formalized):** `HttpMessageNotReadableException` = the bytes weren't valid JSON for the target type → `400`. `MethodArgumentNotValidException` = the JSON deserialized fine but violated a constraint → `422 Unprocessable Entity`. Returning `422` for *semantic* validation tells the client "your syntax is fine, your data isn't."

---

## Step 8 — Run it and exercise valid + invalid payloads

Start the app (from the Day 12 project root):

```bash
./mvnw spring-boot:run
```

### Valid create (→ 201)

```bash
curl -i -X POST http://localhost:8080/v1/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer":"alice","email":"alice@example.com","amount":49.99}'
```

Expected — note `status` is `CREATED` (server-set, *not* from the client) and `version` is **absent** from the response:

```
HTTP/1.1 201 Created
Location: http://localhost:8080/v1/orders/1
Content-Type: application/json

{"id":1,"customer":"alice","email":"alice@example.com","amount":49.99,
 "status":"CREATED","createdAt":"2026-06-16T10:00:00Z"}
```

### Over-posting attempt — the contract ignores it

```bash
curl -i -X POST http://localhost:8080/v1/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer":"mallory","email":"m@example.com","amount":1.00,
       "status":"PAID","version":99,"internalFlag":true}'
```

`CreateOrderRequest` has no `status`/`version`/`internalFlag` components, so Jackson simply drops the unknown fields (default `FAIL_ON_UNKNOWN_PROPERTIES=false`). The created order is still `CREATED`:

```
HTTP/1.1 201 Created
{"id":2,"customer":"mallory","email":"m@example.com","amount":1.00,
 "status":"CREATED","createdAt":"..."}
```

The attacker's `status:PAID` and `internalFlag:true` went **nowhere**. That is the DTO doing its job as an allow-list.

### Invalid payload (→ 422, field-level errors)

```bash
curl -i -X POST http://localhost:8080/v1/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer":"","email":"not-an-email","amount":-3}'
```

Expected:

```
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/json

{"timestamp":"2026-06-16T10:01:00Z","status":422,"error":"Unprocessable Entity",
 "message":"Validation failed",
 "fieldErrors":{
   "customer":"customer must not be blank",
   "email":"email must be a valid address",
   "amount":"amount must be positive"
 }}
```

Three independent constraints, three clear messages, one round trip — the client knows exactly what to fix.

### Custom-constraint failure (→ 422)

```bash
curl -i -X PATCH http://localhost:8080/v1/orders/1/status \
  -H 'Content-Type: application/json' \
  -d '{"status":"SHIPPED"}'
```

```
HTTP/1.1 422 Unprocessable Entity
{"status":422,"error":"Unprocessable Entity","message":"Validation failed",
 "fieldErrors":{"status":"status must be one of CREATED, PAID, CANCELLED"}}
```

A valid status transition works:

```bash
curl -i -X PATCH http://localhost:8080/v1/orders/1/status \
  -H 'Content-Type: application/json' \
  -d '{"status":"PAID"}'
# 200, body shows "status":"PAID"
```

### Malformed JSON (→ 400)

```bash
curl -i -X POST http://localhost:8080/v1/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer":"alice", "amount":}'   # broken JSON
# HTTP/1.1 400 Bad Request -> "Malformed request body"
```

---

## 🚀 Going Deeper & Next Steps

## Going deeper / senior-level notes

**Validation groups.** The same DTO is often used in `create` and `update`, where different rules apply (e.g. `id` must be null on create, non-null on update). Define marker interfaces `OnCreate`/`OnUpdate`, tag constraints with `groups = OnCreate.class`, and trigger a specific group with Spring's `@Validated(OnCreate.class)` on the parameter. This avoids cloning the DTO per use case while keeping rules precise.

```java
public interface OnCreate {}
public interface OnUpdate {}

public record SaveOrderRequest(
    @Null(groups = OnCreate.class) @NotNull(groups = OnUpdate.class) Long id,
    @NotBlank String customer) {}

// controller param: @Validated(OnCreate.class) @RequestBody SaveOrderRequest req
```

**Cross-field (class-level) validation.** Some rules span multiple fields — "`discountedPrice` must be ≤ `price`," or "`endDate` after `startDate`." A field-level annotation can't see siblings. Write a **class-level** constraint: `@Constraint` whose `ConstraintValidator<MyAnnotation, MyDto>` receives the whole object, and (importantly) attach the error to a specific field via `context.buildConstraintViolationWithTemplate(...).addPropertyNode("endDate").addConstraintViolation()` so it still shows up in `fieldErrors`.

**Nested & collection validation.** `@Valid` cascades: put `@Valid` on a nested DTO field or on `List<@Valid ItemDto>` to validate the whole graph. Without the inner `@Valid`, only the top level is checked. This is how you validate an order with line items in one pass.

**`@Validated` vs `@Valid` recap.** `@Valid` is the Jakarta annotation for cascading bean validation (use on `@RequestBody` and nested fields). `@Validated` is the Spring annotation that (a) enables method-level validation on a bean and (b) carries group selection. They are complementary, not interchangeable.

**MapStruct power features.** `expression = "java(...)"` for inline logic, `@Mapping(source="a.b.c", target="x")` for nested flattening, `qualifiedByName` to pick a specific conversion method, `uses = { OtherMapper.class }` to compose mappers, `nullValuePropertyMappingStrategy = IGNORE` for PATCH-style partial updates, and `@AfterMapping`/`@BeforeMapping` hooks. Set `unmappedTargetPolicy = ERROR` on critical mappers to make a forgotten field **fail the build** — the strongest form of compile-time safety.

**Contract-first / OpenAPI with springdoc.** The above is *code-first*: the contract is implied by your DTOs. The senior alternative is **contract-first** — write the OpenAPI spec, generate DTOs/clients from it. At minimum, add `springdoc-openapi-starter-webmvc-ui` to auto-generate an OpenAPI document and Swagger UI from your annotated controllers/DTOs (`@Schema`, `@Operation`). This makes the contract a *reviewable artifact* and lets consumers generate typed clients. Pair Bean Validation annotations with springdoc and they surface as `minLength`/`pattern`/`required` in the OpenAPI schema for free.

**Consumer-driven contracts (CDC).** Auto-generated docs prove what you *say*; they don't prove you didn't *break* a consumer. **Spring Cloud Contract** (or Pact) lets each consumer publish the slice of your API it depends on; your build runs those contracts as tests and **fails if you break them**. This is the test-time enforcement of the "independent deployability" idea from the primer — a producer literally cannot merge a breaking change without a failing build. This thread continues to Kafka schema compatibility on Day 18.

**Versioning in practice.** We used URI versioning (`/v1/orders`). The senior discipline: keep `v1` DTOs **frozen**, add fields only in backward-compatible ways, and introduce `/v2` only for true breaking changes — running both until consumers migrate, then sunsetting `v1` with deprecation headers (`Deprecation`, `Sunset`) and a comms plan. Never mutate a shipped contract in place.

**Where mapping lives — service vs controller.** Mapping `entity → responseDTO` at the controller keeps the service transport-agnostic and reusable (a future Kafka listener calls the same service without dragging HTTP DTOs along). Some teams map in the service to keep controllers thin. Either is defensible; be consistent. What's *not* defensible is mapping in the repository layer or leaking entities upward.

---

## Stretch goals

1. **Add validation groups** to a single `SaveOrderRequest` used by both `POST` (create) and `PUT` (replace), with `@Null(groups=OnCreate)` / `@NotNull(groups=OnUpdate)` on `id`, and prove each path enforces the right rules via curl.
2. **Write a class-level cross-field constraint** (e.g. `@AmountWithinCustomerLimit` checking `amount` against a per-customer cap field) and make sure the error attaches to the `amount` field in `fieldErrors`.
3. **Set `unmappedTargetPolicy = ReportingPolicy.ERROR`** on `OrderMapper`, then add a field to `OrderResponse` with no source and watch the **build fail** — then read the generated `OrderMapperImpl` to see the difference vs reflection mappers.
4. **Add springdoc-openapi**, expose Swagger UI at `/swagger-ui.html`, and confirm your Bean Validation annotations show up as constraints in the generated OpenAPI schema. Bonus: write one **Spring Cloud Contract** stub and run it as a test.

---

## Day 15 teaser

You now have a clean contract and a fast, validated mapping layer. Tomorrow we make reads **fast** with **caching** — Spring's `@Cacheable`/`@CacheEvict` abstraction, cache-aside vs read-through, TTLs, and the classic distributed-systems traps: cache **stampede**, **thundering herd**, and **invalidation** (the "two hard things in computer science"). Your `OrderResponse` DTO becomes the perfect cache value — immutable, self-contained, and free of lazy-loading landmines — tying directly into today's "DTOs are self-contained snapshots" insight, and setting up Day 16's jump to Redis as a distributed cache.
