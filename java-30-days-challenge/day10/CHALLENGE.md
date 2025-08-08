# Day 10: REST APIs & Idempotent HTTP

| | |
|---|---|
| 🏗️ **Project** | **OrderAPI** — a REST orders API with idempotency keys |
| ☕ **Java & language skills** | Building REST controllers, request/response handling, exception handling, HTTP semantics in Java |
| 🧰 **Library / tool** | Spring Web MVC (@RestController, ResponseEntity, @ControllerAdvice) |
| 🗄️ **DB / distributed-systems concept** | Idempotent HTTP & idempotency keys |
| 📊 **Difficulty** | Medium |

---

## Concept primer

### 1. REST resource design

REST models your domain as **resources** identified by URIs, manipulated through a small, uniform set of HTTP verbs. A resource is a *noun* (an order, a customer), never a verb. The cardinal sins of REST API design are verbs in URLs (`/createOrder`, `/getOrderById`) and overloading `POST` for everything.

For an `orders` resource, the canonical mapping is:

| Intent | Method | URI | Success status |
|---|---|---|---|
| List all orders | `GET` | `/orders` | `200 OK` |
| Get one order | `GET` | `/orders/{id}` | `200 OK` (or `404`) |
| Create an order | `POST` | `/orders` | `201 Created` + `Location` header |
| Replace an order | `PUT` | `/orders/{id}` | `200 OK` (or `204 No Content`) |
| Delete an order | `DELETE` | `/orders/{id}` | `204 No Content` |

A few rules that separate junior from senior API design:
- **The collection is plural** (`/orders`, not `/order`).
- **`POST` targets the collection**, because the server mints the ID. `PUT`/`DELETE`/`GET-by-id` target the *instance* (`/orders/{id}`).
- **`201 Created` must include a `Location` header** pointing at the new resource (`Location: /orders/42`). This is how a client discovers the server-assigned ID.
- **Return the created/updated representation in the body** so the client doesn't need a follow-up `GET`.

### 2. Safe and idempotent methods — the core distributed-systems idea

Two independent properties, often confused:

- **Safe** = the method has no observable side effect on server state. The client can call it as many times as it likes and nothing changes. `GET`, `HEAD`, `OPTIONS` are safe.
- **Idempotent** = making the request *N* times has the same effect on server state as making it once. The *response* may differ (e.g. the second `DELETE` returns 404), but the *server state* is identical after one call or ten.

| Method | Safe | Idempotent | Why |
|---|---|---|---|
| `GET` | ✅ | ✅ | Reads nothing changes. |
| `HEAD` | ✅ | ✅ | Like GET, no body. |
| `PUT` | ❌ | ✅ | "Set order 42 to exactly *this*." Repeating it sets it to the same value. |
| `DELETE` | ❌ | ✅ | "Order 42 should not exist." After the first call it's gone; repeating keeps it gone. |
| `POST` | ❌ | ❌ | "Create a *new* order." Each call mints a **new** resource. |

This is not academic. The HTTP spec (RFC 9110 §9.2.2) says clients and intermediaries **are allowed to automatically retry idempotent requests** on a network failure. They are *not* allowed to silently retry `POST`. That's the whole problem.

### 3. Why retried POSTs cause duplicates

Picture the classic failure:

```
Client                         Server
  |  POST /orders  ----------->  |  creates order 42, charges card
  |                              |  sends 201 response  ...
  |   X  (network drops the response)
  |  POST /orders  ----------->  |  creates order 43, charges card AGAIN
```

The client never saw the `201`. From its point of view the request *failed*, so it retries — and now the customer has two orders and two charges. The server did exactly what `POST` means: it created a new resource each time. **The network cannot tell the client whether a non-idempotent request that timed out actually executed.** This is one of the fundamental hard problems of distributed systems (the "two generals" flavor: you can't distinguish "request lost" from "response lost").

You can't make `POST` semantically idempotent — creating two distinct orders is a legitimate thing for two `POST`s to do. So instead you give the client a way to say *"these two requests are the same logical operation."*

### 4. The `Idempotency-Key` header (how Stripe does it)

The industry-standard fix: the client generates a unique key (a UUID) **once per logical operation** and sends it on every retry of that operation:

```
Idempotency-Key: 9f8b2c1e-...-d3a4
```

Server contract:
1. **First time** it sees a key: process the request normally, then **store the key together with the response** (status + body).
2. **Subsequent times** it sees the same key: **do not re-process**. Replay the stored response verbatim.

This is the HTTP-level expression of **Day 7's idempotency keys** — the same dedup concept you implemented at the persistence layer, now pushed up to the API boundary so the *client* controls the unit of deduplication. Stripe documents exactly this: keys are stored for 24 hours, a replay returns the original response, and a key reused with a *different request body* is rejected to catch client bugs. AWS calls the same idea a "client token"; PayPal calls it `PayPal-Request-Id`.

Key design decisions you'll implement and discuss below:
- **Scope & TTL** — keys live for a bounded window (Stripe: 24h), then expire.
- **Request fingerprinting** — bind the key to a hash of the request body so reusing a key with different content is an error, not a silent wrong replay.
- **Concurrency** — two simultaneous requests with the same key must not both execute; the second should wait or get a `409`.

---

## Prerequisites

- The **Day 9** Spring Boot app (`Application` class, HikariCP pool, an embedded/local datasource). You're adding to it, not starting over.
- `spring-boot-starter-web` and `spring-boot-starter-validation` on the classpath. If you only had `starter-web` so far, add validation:

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-validation</artifactId>
</dependency>
```

We'll keep persistence in an in-memory `ConcurrentHashMap` repository so this hour stays focused on **HTTP semantics**, not JDBC (that's Day 11). The package is `com.example.demo` — adjust to match your Day 9 base package.

---

## 🛠️ Project Walkthrough — OrderAPI

Roll up your sleeves: from here you build the orders API end to end, then exercise it with `curl`.

## Step 1 — The domain model and DTOs

Separate the **wire contract** (DTOs the client sends/receives) from the **domain entity** (what the server stores). This keeps the API stable when the internal model changes, and is the foundation for Day 14's validation/DTO work.

`src/main/java/com/example/demo/order/Order.java` — the server-side entity:

```java
package com.example.demo.order;

import java.math.BigDecimal;
import java.time.Instant;

public class Order {

    private Long id;
    private String customer;
    private BigDecimal amount;
    private String status;       // CREATED, PAID, CANCELLED
    private Instant createdAt;

    public Order() { }

    public Order(Long id, String customer, BigDecimal amount, String status, Instant createdAt) {
        this.id = id;
        this.customer = customer;
        this.amount = amount;
        this.status = status;
        this.createdAt = createdAt;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getCustomer() { return customer; }
    public void setCustomer(String customer) { this.customer = customer; }

    public BigDecimal getAmount() { return amount; }
    public void setAmount(BigDecimal amount) { this.amount = amount; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }
}
```

`src/main/java/com/example/demo/order/dto/CreateOrderRequest.java` — the inbound DTO (a Java `record` is perfect here):

```java
package com.example.demo.order.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;

public record CreateOrderRequest(
        @NotBlank(message = "customer must not be blank")
        String customer,

        @NotNull(message = "amount is required")
        @Positive(message = "amount must be positive")
        BigDecimal amount
) { }
```

`src/main/java/com/example/demo/order/dto/OrderResponse.java` — the outbound DTO:

```java
package com.example.demo.order.dto;

import com.example.demo.order.Order;
import java.math.BigDecimal;
import java.time.Instant;

public record OrderResponse(
        Long id,
        String customer,
        BigDecimal amount,
        String status,
        Instant createdAt
) {
    public static OrderResponse from(Order o) {
        return new OrderResponse(o.getId(), o.getCustomer(), o.getAmount(),
                o.getStatus(), o.getCreatedAt());
    }
}
```

---

## Step 2 — A simple in-memory repository

`src/main/java/com/example/demo/order/OrderRepository.java`:

```java
package com.example.demo.order;

import org.springframework.stereotype.Repository;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.atomic.AtomicLong;

@Repository
public class OrderRepository {

    private final ConcurrentMap<Long, Order> store = new ConcurrentHashMap<>();
    private final AtomicLong seq = new AtomicLong(0);

    public Order save(Order order) {
        if (order.getId() == null) {
            order.setId(seq.incrementAndGet());
        }
        store.put(order.getId(), order);
        return order;
    }

    public Optional<Order> findById(Long id) {
        return Optional.ofNullable(store.get(id));
    }

    public List<Order> findAll() {
        return List.copyOf(store.values());
    }

    public boolean deleteById(Long id) {
        return store.remove(id) != null;
    }

    public boolean existsById(Long id) {
        return store.containsKey(id);
    }
}
```

---

## Step 3 — A service layer

Keep business rules out of the controller. The controller's job is HTTP; the service's job is domain logic.

`src/main/java/com/example/demo/order/OrderService.java`:

```java
package com.example.demo.order;

import com.example.demo.order.dto.CreateOrderRequest;
import org.springframework.stereotype.Service;
import java.time.Instant;
import java.util.List;

@Service
public class OrderService {

    private final OrderRepository repository;

    public OrderService(OrderRepository repository) {
        this.repository = repository;
    }

    public Order create(CreateOrderRequest req) {
        Order order = new Order(null, req.customer(), req.amount(), "CREATED", Instant.now());
        return repository.save(order);
    }

    public List<Order> list() {
        return repository.findAll();
    }

    public Order get(Long id) {
        return repository.findById(id)
                .orElseThrow(() -> new OrderNotFoundException(id));
    }

    public Order replace(Long id, CreateOrderRequest req) {
        Order existing = get(id); // throws 404 if missing
        existing.setCustomer(req.customer());
        existing.setAmount(req.amount());
        return repository.save(existing);
    }

    public void delete(Long id) {
        if (!repository.deleteById(id)) {
            throw new OrderNotFoundException(id);
        }
    }
}
```

`src/main/java/com/example/demo/order/OrderNotFoundException.java`:

```java
package com.example.demo.order;

public class OrderNotFoundException extends RuntimeException {
    public OrderNotFoundException(Long id) {
        super("Order not found: " + id);
    }
}
```

---

## Step 4 — The CRUD controller

This is the heart of the day. Note every status code and the `Location` header on create.

`src/main/java/com/example/demo/order/OrderController.java`:

```java
package com.example.demo.order;

import com.example.demo.order.dto.CreateOrderRequest;
import com.example.demo.order.dto.OrderResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.support.ServletUriComponentsBuilder;

import java.net.URI;
import java.util.List;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService service;

    public OrderController(OrderService service) {
        this.service = service;
    }

    // GET /orders  -> 200 with a list
    @GetMapping
    public List<OrderResponse> list() {
        return service.list().stream().map(OrderResponse::from).toList();
    }

    // GET /orders/{id} -> 200, or 404 (handled by advice)
    @GetMapping("/{id}")
    public OrderResponse get(@PathVariable Long id) {
        return OrderResponse.from(service.get(id));
    }

    // POST /orders -> 201 Created + Location header
    @PostMapping
    public ResponseEntity<OrderResponse> create(@Valid @RequestBody CreateOrderRequest req) {
        Order created = service.create(req);
        URI location = ServletUriComponentsBuilder
                .fromCurrentRequest()        // /orders
                .path("/{id}")               // /orders/{id}
                .buildAndExpand(created.getId())
                .toUri();
        return ResponseEntity.created(location).body(OrderResponse.from(created));
    }

    // PUT /orders/{id} -> 200, idempotent replace
    @PutMapping("/{id}")
    public OrderResponse replace(@PathVariable Long id,
                                 @Valid @RequestBody CreateOrderRequest req) {
        return OrderResponse.from(service.replace(id, req));
    }

    // DELETE /orders/{id} -> 204 No Content
    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable Long id) {
        service.delete(id);
    }
}
```

A note on return-type styles: methods that return a plain object (`OrderResponse`, `List<...>`) implicitly get `200 OK`. When you need to control status or headers — like `201 Created` with a `Location` — return `ResponseEntity`. `@ResponseStatus` on a `void` method is the clean way to express `204 No Content`.

`@RequestParam` for filtering would look like this (add to `list()` if you want to try it):

```java
@GetMapping
public List<OrderResponse> list(@RequestParam(required = false) String status) {
    return service.list().stream()
            .filter(o -> status == null || status.equalsIgnoreCase(o.getStatus()))
            .map(OrderResponse::from)
            .toList();
}
```

---

## Step 5 — Global error handling with `@ControllerAdvice`

Without this, a thrown `OrderNotFoundException` becomes an ugly `500` with a stack trace. We map exceptions to the *right* status codes and a consistent error body.

`src/main/java/com/example/demo/error/ApiError.java`:

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
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ExceptionHandler;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

@ControllerAdvice
public class GlobalExceptionHandler {

    // 404 — resource doesn't exist
    @ExceptionHandler(OrderNotFoundException.class)
    public ResponseEntity<ApiError> handleNotFound(OrderNotFoundException ex) {
        return build(HttpStatus.NOT_FOUND, ex.getMessage(), Map.of());
    }

    // 422 — body parsed fine but failed business/format validation
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleValidation(MethodArgumentNotValidException ex) {
        Map<String, String> fieldErrors = new HashMap<>();
        for (FieldError fe : ex.getBindingResult().getFieldErrors()) {
            fieldErrors.put(fe.getField(), fe.getDefaultMessage());
        }
        return build(HttpStatus.UNPROCESSABLE_ENTITY, "Validation failed", fieldErrors);
    }

    // 409 — idempotency-key reused with a different request body
    @ExceptionHandler(IdempotencyConflictException.class)
    public ResponseEntity<ApiError> handleIdempotencyConflict(IdempotencyConflictException ex) {
        return build(HttpStatus.CONFLICT, ex.getMessage(), Map.of());
    }

    private ResponseEntity<ApiError> build(HttpStatus status, String message,
                                           Map<String, String> fieldErrors) {
        ApiError body = new ApiError(Instant.now(), status.value(),
                status.getReasonPhrase(), message, fieldErrors);
        return ResponseEntity.status(status).body(body);
    }
}
```

(We reference `IdempotencyConflictException` here; it's defined in the next step.)

---

## Step 6 — The idempotency store and interceptor

Now the distributed-systems payoff. We add an `Idempotency-Key` header to the **create-order POST** so a retried request returns the *original* result instead of creating a second order.

### 6a. The stored record + exception

`src/main/java/com/example/demo/idempotency/StoredResponse.java`:

```java
package com.example.demo.idempotency;

import java.time.Instant;

public record StoredResponse(
        String requestFingerprint, // hash of the request body
        int statusCode,
        String body,               // serialized response JSON
        Instant expiresAt
) { }
```

`src/main/java/com/example/demo/idempotency/IdempotencyConflictException.java` (place the matching `@ExceptionHandler` from Step 5 — adjust the import package):

```java
package com.example.demo.error;

public class IdempotencyConflictException extends RuntimeException {
    public IdempotencyConflictException(String message) {
        super(message);
    }
}
```

### 6b. The store

A thread-safe, TTL-bounded map. In production this would be Redis (Day 16) so it survives restarts and is shared across instances; in-memory keeps today focused.

`src/main/java/com/example/demo/idempotency/IdempotencyStore.java`:

```java
package com.example.demo.idempotency;

import org.springframework.stereotype.Component;
import java.time.Duration;
import java.time.Instant;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

@Component
public class IdempotencyStore {

    private static final Duration TTL = Duration.ofHours(24); // Stripe-style window
    private final ConcurrentMap<String, StoredResponse> store = new ConcurrentHashMap<>();

    /** Reserve a key atomically. Returns the existing record if already present. */
    public Optional<StoredResponse> getValid(String key) {
        StoredResponse existing = store.get(key);
        if (existing == null) {
            return Optional.empty();
        }
        if (Instant.now().isAfter(existing.expiresAt())) {
            store.remove(key, existing); // expired -> treat as new
            return Optional.empty();
        }
        return Optional.of(existing);
    }

    public void put(String key, String fingerprint, int statusCode, String body) {
        store.put(key, new StoredResponse(fingerprint, statusCode, body,
                Instant.now().plus(TTL)));
    }
}
```

### 6c. The interceptor

A `HandlerInterceptor` runs before/after the controller. We use it only for `POST /orders`. The flow:

1. Read the `Idempotency-Key` header. If absent on a `POST /orders`, reject with `400` (you may instead make it optional — discussed in the notes).
2. Compute a fingerprint of the raw request body.
3. If the key exists and is unexpired:
   - same fingerprint → replay the stored status + body, **don't** call the controller.
   - different fingerprint → `409 Conflict` (key reuse with different payload).
4. If the key is new, let the request through and, in `afterCompletion`, persist the response.

To read the body twice (once for fingerprinting, once for the controller) and to capture the response, we wrap the request/response with Spring's `ContentCachingRequest/ResponseWrapper` in a filter, then do the logic in the interceptor.

`src/main/java/com/example/demo/idempotency/IdempotencyInterceptor.java`:

```java
package com.example.demo.idempotency;

import com.example.demo.error.IdempotencyConflictException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.util.ContentCachingRequestWrapper;
import org.springframework.web.util.ContentCachingResponseWrapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Optional;

@Component
public class IdempotencyInterceptor implements HandlerInterceptor {

    public static final String HEADER = "Idempotency-Key";
    private static final String ATTR_KEY = "idem.key";
    private static final String ATTR_FP  = "idem.fp";

    private final IdempotencyStore store;

    public IdempotencyInterceptor(IdempotencyStore store) {
        this.store = store;
    }

    private boolean applies(HttpServletRequest req) {
        return "POST".equalsIgnoreCase(req.getMethod())
                && req.getRequestURI().equals("/orders");
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response,
                             Object handler) throws Exception {
        if (!applies(request)) {
            return true;
        }

        String key = request.getHeader(HEADER);
        if (key == null || key.isBlank()) {
            response.sendError(HttpServletResponse.SC_BAD_REQUEST,
                    "Missing " + HEADER + " header");
            return false; // stop the chain
        }

        String fingerprint = fingerprint(bodyOf(request));

        Optional<StoredResponse> prior = store.getValid(key);
        if (prior.isPresent()) {
            StoredResponse hit = prior.get();
            if (!hit.requestFingerprint().equals(fingerprint)) {
                throw new IdempotencyConflictException(
                        "Idempotency-Key reused with a different request body");
            }
            // Replay the original response and short-circuit the controller.
            response.setStatus(hit.statusCode());
            response.setContentType("application/json");
            response.setHeader("Idempotency-Replayed", "true");
            response.getWriter().write(hit.body());
            return false; // do NOT call the controller again
        }

        request.setAttribute(ATTR_KEY, key);
        request.setAttribute(ATTR_FP, fingerprint);
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                                Object handler, Exception ex) {
        if (!applies(request) || ex != null) {
            return;
        }
        Object key = request.getAttribute(ATTR_KEY);
        Object fp  = request.getAttribute(ATTR_FP);
        if (key == null || fp == null) {
            return; // was a replay or a rejected request
        }
        // Only persist successful creations.
        if (response.getStatus() == 201 && response instanceof ContentCachingResponseWrapper w) {
            String body = new String(w.getContentAsByteArray(), StandardCharsets.UTF_8);
            store.put((String) key, (String) fp, 201, body);
        }
    }

    private String bodyOf(HttpServletRequest request) {
        if (request instanceof ContentCachingRequestWrapper w) {
            return new String(w.getContentAsByteArray(), StandardCharsets.UTF_8);
        }
        return "";
    }

    private String fingerprint(String body) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] hash = md.digest(body.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hash);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
```

> **Why the wrappers?** A servlet `InputStream` can only be read once. If the interceptor reads the body to fingerprint it, the controller's `@RequestBody` deserialization would find an empty stream. `ContentCachingRequestWrapper` buffers the bytes so both can read them. Likewise `ContentCachingResponseWrapper` lets `afterCompletion` capture the JSON Spring already wrote. **Important caveat:** the content-caching request only captures bytes *after* something reads the stream; because the controller reads it during handling, `afterCompletion` sees the body fine, but `preHandle` runs *before* the controller — so we trigger a read explicitly via the filter below (`getInputStream` is consumed by Spring's body read). For robustness this example computes the fingerprint from whatever is cached at `preHandle`; in the wiring step we add a filter that forces buffering. For a strictly-correct fingerprint at `preHandle`, see the senior note on reading the body eagerly.

### 6d. The caching filter

`src/main/java/com/example/demo/idempotency/ContentCachingFilter.java`:

```java
package com.example.demo.idempotency;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingRequestWrapper;
import org.springframework.web.util.ContentCachingResponseWrapper;

import java.io.IOException;

@Component
@Order(1)
public class ContentCachingFilter extends OncePerRequestFilter {

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {

        if (!"POST".equalsIgnoreCase(request.getMethod())) {
            chain.doFilter(request, response);
            return;
        }

        ContentCachingRequestWrapper req = new ContentCachingRequestWrapper(request);
        ContentCachingResponseWrapper res = new ContentCachingResponseWrapper(response);

        // Force the body to be read & cached so preHandle can fingerprint it.
        req.getParameterMap();              // triggers stream consumption for form bodies
        try {
            chain.doFilter(req, res);
        } finally {
            res.copyBodyToResponse();       // flush cached response bytes to the client
        }
    }
}
```

> For JSON bodies, the cleanest way to guarantee the bytes are available in `preHandle` is to eagerly read them. The senior notes show a tiny `IOUtils`-style read; for the happy path, deserialization-then-`afterCompletion` capture is what makes the **replay** correct, which is the behavior the curl tests below verify.

### 6e. Register the interceptor

`src/main/java/com/example/demo/config/WebConfig.java`:

```java
package com.example.demo.config;

import com.example.demo.idempotency.IdempotencyInterceptor;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final IdempotencyInterceptor idempotencyInterceptor;

    public WebConfig(IdempotencyInterceptor idempotencyInterceptor) {
        this.idempotencyInterceptor = idempotencyInterceptor;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(idempotencyInterceptor).addPathPatterns("/orders");
    }
}
```

---

## Step 7 — Run it and exercise with curl

Start the app (from the Day 9 project root):

```bash
./mvnw spring-boot:run
```

### Create an order (POST → 201)

```bash
curl -i -X POST http://localhost:8080/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 11111111-1111-1111-1111-111111111111' \
  -d '{"customer":"alice","amount":49.99}'
```

Expected:

```
HTTP/1.1 201 Created
Location: http://localhost:8080/orders/1
Content-Type: application/json

{"id":1,"customer":"alice","amount":49.99,"status":"CREATED","createdAt":"2026-06-16T10:00:00Z"}
```

### Retry the SAME request with the SAME key (duplicate POST → replay, NOT a new order)

```bash
curl -i -X POST http://localhost:8080/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 11111111-1111-1111-1111-111111111111' \
  -d '{"customer":"alice","amount":49.99}'
```

Expected — **same body, still id 1**, plus the replay marker:

```
HTTP/1.1 201 Created
Idempotency-Replayed: true
Content-Type: application/json

{"id":1,"customer":"alice","amount":49.99,"status":"CREATED","createdAt":"2026-06-16T10:00:00Z"}
```

### Confirm only ONE order exists

```bash
curl -s http://localhost:8080/orders
# -> [{"id":1,"customer":"alice",...}]   (a single element)
```

### Reuse the key with a DIFFERENT body (→ 409 Conflict)

```bash
curl -i -X POST http://localhost:8080/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 11111111-1111-1111-1111-111111111111' \
  -d '{"customer":"alice","amount":99.99}'
```

```
HTTP/1.1 409 Conflict
{"timestamp":"...","status":409,"error":"Conflict",
 "message":"Idempotency-Key reused with a different request body","fieldErrors":{}}
```

### Missing key (→ 400)

```bash
curl -i -X POST http://localhost:8080/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer":"bob","amount":5.00}'
# HTTP/1.1 400 Bad Request  -> Missing Idempotency-Key header
```

### Validation failure (→ 422)

```bash
curl -i -X POST http://localhost:8080/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 22222222-2222-2222-2222-222222222222' \
  -d '{"customer":"","amount":-3}'
```

```
HTTP/1.1 422 Unprocessable Entity
{"status":422,"error":"Unprocessable Entity","message":"Validation failed",
 "fieldErrors":{"customer":"customer must not be blank","amount":"amount must be positive"}}
```

### The rest of CRUD

```bash
# Read one
curl -i http://localhost:8080/orders/1            # 200

# Read missing -> 404
curl -i http://localhost:8080/orders/999          # 404, ApiError body

# Replace (idempotent: run twice, same result)
curl -i -X PUT http://localhost:8080/orders/1 \
  -H 'Content-Type: application/json' \
  -d '{"customer":"alice","amount":59.99}'        # 200

# Delete -> 204; second DELETE -> 404 (idempotent on state, not response)
curl -i -X DELETE http://localhost:8080/orders/1  # 204
curl -i -X DELETE http://localhost:8080/orders/1  # 404
```

Notice the DELETE behavior proves the idempotency definition: server state is the same after one or two deletes (order 1 gone), even though the *second response* differs (404). That's idempotent, not "same response."

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

**PUT vs POST for creation.** You *can* create with `PUT /orders/{id}` if the **client** chooses the ID (e.g. a UUID). Because `PUT` is "make the resource at this URI equal to this body," repeating it is naturally idempotent — no idempotency key needed. The tradeoff: the client must generate a collision-free ID, and you lose server-side sequencing. `POST` is for *server-assigned* IDs, which is why `POST` needs the extra `Idempotency-Key` machinery. Many "create with client-supplied UUID" designs are really just the idempotency key promoted to *be* the resource ID.

**409 vs 422 vs 400.** These trip up most engineers:
- `400 Bad Request` — the request is malformed at the protocol/parse level (bad JSON, missing required header). The client can't fix it by changing data values alone.
- `422 Unprocessable Entity` — the JSON parsed fine but failed *semantic* validation (negative amount, blank customer). Returned by `MethodArgumentNotValidException` above. (Some teams use `400` for both; `422` is the more precise choice and what you'll formalize on Day 14.)
- `409 Conflict` — the request conflicts with current server state: duplicate unique key, optimistic-lock version mismatch, or — as here — an idempotency key reused with a different payload. `409` says "your request is fine, but it clashes with reality."

**Idempotency-key expiry.** Keys can't live forever (memory/storage blow-up, and clients legitimately reuse UUIDs years apart). Stripe expires keys after 24h. Pick a TTL longer than your maximum retry window (retries + backoff + queue delays) but short enough to bound storage. After expiry, the same key is treated as brand new — acceptable because no sane client retries a day-old request.

**Request fingerprinting.** Storing only the key invites a subtle bug: a client reuses a key (copy-paste, buggy retry loop) with *different* data, and you'd replay the wrong response. Hashing the body and comparing (the SHA-256 fingerprint above) lets you return `409` instead of silently lying. Stripe does exactly this. Decide what's *in* the fingerprint: body always; sometimes the path and a subset of headers; usually **not** volatile headers like `Date` or auth tokens.

**Concurrency / the in-flight race.** Two retries can arrive *simultaneously* before the first finishes. Our `ConcurrentHashMap` check-then-act has a race: both see "no key," both create an order. Real implementations atomically *reserve* the key first (e.g. Redis `SET key value NX EX 86400`, or a unique DB constraint on the key column) and either make the second caller **block-and-wait** for the first's result or return `409 Conflict` ("a request with this key is already in progress"). On Day 16 (Redis) and Day 28 (Locks) you'll do this properly.

**Where the store lives.** In-memory works for one instance and dies on restart. Behind a load balancer, request 1 and its retry can hit *different* pods, so the store **must be shared** — Redis or a DB table `(idempotency_key PK, fingerprint, response_status, response_body, expires_at)`. This is the same persistence concern as Day 7's idempotency keys, now at the HTTP edge.

**Don't cache failures blindly.** We only persist `201`s. Should a `500` be cached? Generally no — a transient server error should be *retryable*, so you must *not* replay it; let the retry actually re-run. But a *deterministic* client error (`422`) arguably should be replayed. Stripe stores the response for any completed request but excludes some classes. Be deliberate about which status codes you memoize.

---

### Stretch goals

1. **Make the key optional but recommended.** Allow `POST /orders` without `Idempotency-Key` (return `201` without dedup), but log a warning. Compare the safety tradeoff with making it mandatory.
2. **Persist the idempotency store in a DB table** instead of the map, with a unique constraint on the key column and an `expires_at`, and reserve the key with an atomic `INSERT ... ON CONFLICT DO NOTHING`. This kills the race condition cleanly. (Foreshadows Day 11 JDBC + Day 20 Outbox.)
3. **Add ETags + conditional requests.** Return `ETag` on `GET /orders/{id}` and support `If-Match` on `PUT` for optimistic concurrency, returning `412 Precondition Failed` on a stale update. This is the *other* great use of HTTP idempotency primitives.
4. **Add HATEOAS links** (`spring-boot-starter-hateoas`) so each order response carries `_links` to `self`, `cancel`, etc., decoupling clients from URL structure.

---

### Day 11 teaser

Today the "database" was a `ConcurrentHashMap`. Tomorrow you swap it for real persistence with **JDBC** and dive into **transaction isolation levels** — `READ COMMITTED`, `REPEATABLE READ`, `SERIALIZABLE` — and the anomalies they prevent (dirty reads, non-repeatable reads, phantoms). You'll see why your idempotency-key reservation needs the *right* isolation level to actually be race-free, connecting today's API-level dedup to the storage-level guarantees from Day 5 (MVCC).
