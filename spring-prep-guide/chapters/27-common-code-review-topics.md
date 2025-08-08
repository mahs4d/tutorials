# 27. Common Code Review Topics

## Overview

A code-review interview usually works like this: the interviewer pastes 50-150 lines of Spring code into a shared editor and asks "what would you flag if this landed in a pull request?" There's rarely one bug to find — there's a pile of small smells, and your job is to spot as many as you can, explain *why* they matter, and suggest a fix. Interviewers are grading your process as much as your answers: do you scan for security holes, or only style nits? Do you know which mistakes are "will break in production" versus "nice to have"? This chapter is a checklist of the smells that show up again and again in real Spring Boot code reviews, with the words to use when you spot them.

**How to run a code review out loud** — say these categories as you scan, in this order:

1. **Correctness** — does the code do what it claims? Off-by-one, wrong null handling, wrong exception type.
2. **Security** — auth, input validation, secrets, injection, CORS/CSRF.
3. **Data & transactions** — transaction boundaries, N+1 queries, lazy loading, isolation.
4. **Performance** — unbounded queries, loops that should be batched, blocking calls.
5. **Readability** — naming, method length, dead code, magic numbers.
6. **Testability** — can this be unit tested without spinning up all of Spring?

Say the category out loud before each finding ("security-wise, this endpoint has no CSRF protection because...") — it shows structure, not just a list of complaints.

## Constructor Injection vs Field Injection

### What reviewers look for

Field injection (`@Autowired` on a field) is the single most common junior-level smell. Reviewers want to see constructor injection: dependencies as `final` fields, set through the constructor. It signals that you understand immutability, testability, and fail-fast wiring — not just "it compiles."

### ❌ The smell

```java
@Service
public class OrderService {

    @Autowired
    private PaymentClient paymentClient;

    @Autowired
    private InventoryRepository inventoryRepository;

    @Autowired
    private NotificationService notificationService;

    public OrderResult placeOrder(OrderRequest request) {
        inventoryRepository.reserve(request.items());
        paymentClient.charge(request.paymentToken(), request.total());
        notificationService.sendConfirmation(request.customerEmail());
        return OrderResult.success();
    }
}
```

### Why it's a problem

- You cannot construct `OrderService` in a plain unit test without reflection or a Spring context.
- Fields aren't `final`, so nothing stops someone from reassigning them later, or leaving one `null` after a refactor.
- Missing dependencies fail at runtime with an obscure `NullPointerException`, not at startup.
- Circular dependencies between beans are easy to create and hard to notice, because Spring resolves fields after construction.
- Mockito's `@InjectMocks` on field injection is fragile and hides real usage.

### ✅ The fix

```java
@Service
public class OrderService {

    private final PaymentClient paymentClient;
    private final InventoryRepository inventoryRepository;
    private final NotificationService notificationService;

    public OrderService(PaymentClient paymentClient,
                         InventoryRepository inventoryRepository,
                         NotificationService notificationService) {
        this.paymentClient = paymentClient;
        this.inventoryRepository = inventoryRepository;
        this.notificationService = notificationService;
    }

    public OrderResult placeOrder(OrderRequest request) {
        inventoryRepository.reserve(request.items());
        paymentClient.charge(request.paymentToken(), request.total());
        notificationService.sendConfirmation(request.customerEmail());
        return OrderResult.success();
    }
}
```

Note: with a single constructor, `@Autowired` is optional — Spring uses it automatically. You can build this class in a plain JUnit test with `new OrderService(fakePaymentClient, fakeInventoryRepository, fakeNotificationService)`, no Spring context required.

### What to say in the interview

"I'd switch this to constructor injection — it makes the dependencies explicit, lets me mark them `final`, and I can unit test this class with plain `new` and mocks instead of booting a Spring context."

## Proper Transaction Boundaries

### What reviewers look for

Reviewers check where `@Transactional` starts and ends, whether it wraps things it shouldn't (HTTP calls, user waiting time), whether self-invocation breaks the proxy, and whether read-only queries are marked as such.

### ❌ The smell

```java
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @Transactional
    @PostMapping
    public OrderResponse createOrder(@RequestBody OrderRequest request) {
        return orderService.createOrder(request);
    }
}

@Service
public class OrderService {

    private final OrderRepository orderRepository;
    private final ShippingCarrierClient shippingCarrierClient;

    // constructor omitted for brevity

    @Transactional
    public OrderResponse createOrder(OrderRequest request) {
        Order order = orderRepository.save(new Order(request));
        // blocking HTTP call happening *inside* the DB transaction
        String trackingId = shippingCarrierClient.registerShipment(order);
        order.setTrackingId(trackingId);
        applyDiscount(order); // calling a @Transactional method on "this"
        return OrderResponse.from(order);
    }

    @Transactional
    public void applyDiscount(Order order) {
        order.setTotal(order.getTotal().multiply(BigDecimal.valueOf(0.9)));
    }
}
```

### Why it's a problem

- `@Transactional` on a `@RestController` method holds a database connection open for the entire HTTP request, including any downstream calls — this exhausts the connection pool under load.
- The call to `shippingCarrierClient.registerShipment(order)` is a network call made *inside* the transaction. If the carrier is slow, the DB transaction — and the row locks it holds — stay open the whole time.
- `applyDiscount(order)` is called as `this.applyDiscount(...)`. Spring's `@Transactional` works via a proxy, so **self-invocation bypasses the proxy entirely** — no new transaction is started, and REQUIRES_NEW / propagation settings are silently ignored.
- No `readOnly = true` on read-only query methods, so Hibernate can't skip dirty-checking and flush optimizations.
- If `createOrder` throws a checked exception, the default `@Transactional` behavior does **not** roll back — only unchecked (`RuntimeException`) exceptions trigger rollback by default.

### ✅ The fix

```java
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    // no @Transactional here — the controller layer should not own transactions
    @PostMapping
    public OrderResponse createOrder(@RequestBody OrderRequest request) {
        return orderService.createOrder(request);
    }
}

@Service
public class OrderService {

    private final OrderRepository orderRepository;
    private final ShippingCarrierClient shippingCarrierClient;
    private final DiscountService discountService; // extracted, no self-invocation

    @Transactional
    public OrderResponse createOrder(OrderRequest request) {
        Order order = orderRepository.save(new Order(request));
        discountService.applyDiscount(order); // real proxy call, different bean
        return OrderResponse.from(order);
    }

    // called *after* the transaction commits, outside the DB transaction
    public String registerShipment(Order order) {
        return shippingCarrierClient.registerShipment(order);
    }

    @Transactional(readOnly = true)
    public List<OrderResponse> listOrders(Long customerId) {
        return orderRepository.findByCustomerId(customerId)
            .stream().map(OrderResponse::from).toList();
    }
}
```

The HTTP call to the carrier should happen after the transaction commits — for example, published as a domain event and handled by an `@TransactionalEventListener(phase = AFTER_COMMIT)` listener, or called directly in the controller after `orderService.createOrder(...)` returns.

### What to say in the interview

"I'd move `@Transactional` off the controller and down to the service, keep transactions short, and pull the shipping-carrier call out of the transaction since it's a network call that shouldn't hold a DB connection open. I'd also flag the self-invocation on `applyDiscount` — that call skips the proxy, so the `@Transactional` annotation on it does nothing."

## N+1 Query Detection

### What reviewers look for

The N+1 problem is the single most common Hibernate/JPA smell: one query to fetch a list, then one extra query *per row* to fetch an association. Reviewers want you to recognize the loop pattern, know the generated SQL shape, and name at least two fixes.

### ❌ The smell

```java
@GetMapping("/api/orders")
public List<OrderSummary> getOrders() {
    List<Order> orders = orderRepository.findAll(); // 1 query

    return orders.stream()
        .map(order -> new OrderSummary(
            order.getId(),
            order.getCustomer().getName(), // triggers a lazy load per order
            order.getItems().size()))      // triggers another lazy load per order
        .toList();
}
```

If `Order.customer` and `Order.items` are lazy (the JPA default for `@ManyToOne`/`@OneToMany` in practice), this generates:

```sql
select * from orders;                    -- 1 query
select * from customer where id = ?;     -- once per order
select * from order_item where order_id = ?; -- once per order
```

For 200 orders, that's 401 queries instead of 1-3. This is the "N+1" — and it silently scales with data size, so it often passes code review and then falls over in production.

### Why it's a problem

- Response time scales linearly with row count — fine with 10 test rows, catastrophic with 10,000 production rows.
- Extra round-trips to the database dominate latency, especially with network hops to a managed DB.
- It's invisible in local testing with small seed data — it only shows up under load or in production.
- Connection pool pressure increases because each request holds a connection longer.

### ✅ The fix

```java
// Option 1: JOIN FETCH in a JPQL query
@Query("select o from Order o join fetch o.customer join fetch o.items where o.id in :ids")
List<Order> findAllWithCustomerAndItems(@Param("ids") List<Long> ids);

// Option 2: @EntityGraph, keeps the repository method signature clean
@EntityGraph(attributePaths = {"customer", "items"})
List<Order> findAll();

// Option 3: @BatchSize, when JOIN FETCH would multiply rows badly (e.g. two collections)
@Entity
public class Order {
    @OneToMany(mappedBy = "order")
    @BatchSize(size = 50) // fetches items for 50 orders in one IN(...) query
    private List<OrderItem> items;
}

// Option 4: DTO projection, skip entities entirely for a read-only endpoint
@Query("""
    select new com.example.orders.OrderSummary(o.id, c.name, size(o.items))
    from Order o join o.customer c
    """)
List<OrderSummary> findOrderSummaries();
```

**How to spot it in review or locally:** turn on `spring.jpa.show-sql=true` (or better, `logging.level.org.hibernate.SQL=debug`) and count queries for a single request; wrap the datasource with `datasource-proxy` or `p6spy` to log query counts per request in CI; or enable Hibernate statistics (`spring.jpa.properties.hibernate.generate_statistics=true`) and watch the "second query" and "entity load" counters spike.

### What to say in the interview

"This loop is going to fire one query per order to load the customer and items — classic N+1. For a read endpoint like this I'd reach for a DTO projection or `@EntityGraph` first; I'd only use `@BatchSize` if I have multiple collections that JOIN FETCH would cartesian-product together."

## Lazy Loading Pitfalls

### What reviewers look for

Lazy loading is correct by default for `@OneToMany`/`@ManyToMany`, but it causes two very common bugs: accessing a lazy association after the session is closed (`LazyInitializationException`), and papering over that with `spring.jpa.open-in-view` instead of fixing the real problem.

### ❌ The smell

```java
@RestController
public class OrderController {

    private final OrderRepository orderRepository;

    @GetMapping("/api/orders/{id}")
    public OrderResponse getOrder(@PathVariable Long id) {
        Order order = orderRepository.findById(id).orElseThrow();
        // controller has no open Hibernate session by the time this runs
        // if open-in-view is disabled, this throws LazyInitializationException
        List<String> itemNames = order.getItems().stream()
            .map(OrderItem::getName)
            .toList();
        return new OrderResponse(order.getId(), itemNames);
    }
}
```

### Why it's a problem

- `order.getItems()` is a lazy proxy. If the Hibernate session is already closed (no transaction wraps the controller), touching it throws `LazyInitializationException`.
- The "fix" many teams apply is leaving `spring.jpa.open-in-view=true` (Spring Boot's **default**), which keeps a DB session open for the entire HTTP request, including view rendering and serialization. This hides the problem instead of solving it, and it's controversial for good reason: it makes every controller implicitly capable of triggering lazy loads and hidden N+1 queries at serialization time, and it holds a connection for the whole request-response cycle, not just the DB work.
- Even with open-in-view on, lazy loading inside Jackson serialization is unpredictable — it depends on property access order and can trigger dozens of queries you never see in the service layer.
- Initializing collections lazily and inconsistently makes performance non-deterministic: the same endpoint is fast or slow depending on what happens to already be loaded.

### ✅ The fix

```java
// application.yml — be explicit, don't rely on the hidden default
spring:
  jpa:
    open-in-view: false

@Service
public class OrderQueryService {

    private final OrderRepository orderRepository;

    @Transactional(readOnly = true)
    public OrderResponse getOrder(Long id) {
        Order order = orderRepository.findById(id).orElseThrow();
        // fetched and mapped to a DTO *inside* the transaction, while the session is open
        List<String> itemNames = order.getItems().stream()
            .map(OrderItem::getName)
            .toList();
        return new OrderResponse(order.getId(), itemNames);
    }
}
```

Even better for a read endpoint: skip entities and lazy loading altogether with a projection query, so there's no session-lifetime question at all:

```java
public record OrderResponse(Long id, List<String> itemNames) {}

@Query("select new com.example.orders.OrderResponse(o.id, i.name) from Order o join o.items i where o.id = :id")
OrderResponse findOrderResponse(@Param("id") Long id);
```

### What to say in the interview

"I'd turn `open-in-view` off explicitly and do the DTO mapping inside the `@Transactional` service method, while the session is still open, instead of letting the controller or Jackson trigger lazy loads. For read-only endpoints I'd rather project straight to a DTO and skip the lazy-loading question entirely."

## Entity vs DTO Separation

### What reviewers look for

Using `@Entity` classes directly as `@RequestBody`/`@ResponseBody` is a recurring smell with real security and correctness consequences, not just "layering purity." Reviewers want to see a DTO boundary at the controller.

### ❌ The smell

```java
@Entity
public class User {
    @Id @GeneratedValue
    private Long id;
    private String email;
    private String passwordHash;
    private boolean admin;
    private Instant createdAt;
    // getters/setters
}

@RestController
@RequestMapping("/api/users")
public class UserController {

    @PostMapping
    public User createUser(@RequestBody User user) { // entity as request body!
        return userRepository.save(user);
    }

    @GetMapping("/{id}")
    public User getUser(@PathVariable Long id) { // entity as response body!
        return userRepository.findById(id).orElseThrow();
    }
}
```

### Why it's a problem

- **Mass assignment vulnerability**: because `User` is bound directly from JSON, a client can send `{"email": "me@x.com", "admin": true}` and set fields the API never intended to expose — there's no allowlist of bindable fields.
- The response leaks `passwordHash` and any other internal field straight to clients, unless every sensitive field is manually annotated with `@JsonIgnore` (easy to forget on a new field).
- Lazy-loaded associations on the entity can throw `LazyInitializationException` during Jackson serialization, or trigger surprise N+1 queries if they happen to be initialized.
- Changing the entity (e.g. renaming a column-backed field for a migration) breaks the public API contract, because the API and the persistence model are the same class.
- No natural place to add API-specific validation, versioning, or default values without polluting the entity.

### ✅ The fix

```java
public record CreateUserRequest(
    @Email @NotBlank String email,
    @Size(min = 8) String password
) {}

public record UserResponse(Long id, String email, Instant createdAt) {
    static UserResponse from(User user) {
        return new UserResponse(user.getId(), user.getEmail(), user.getCreatedAt());
    }
}

@RestController
@RequestMapping("/api/users")
public class UserController {

    private final UserService userService;

    @PostMapping
    public UserResponse createUser(@Valid @RequestBody CreateUserRequest request) {
        User user = userService.createUser(request.email(), request.password());
        return UserResponse.from(user);
    }

    @GetMapping("/{id}")
    public UserResponse getUser(@PathVariable Long id) {
        return UserResponse.from(userService.getUser(id));
    }
}
```

Records are a great fit for DTOs: they're immutable, `equals`/`hashCode`/`toString` are free, and there's no risk of a stray setter mutating a response after it's built.

### What to say in the interview

"I'd never bind an entity directly from a request body — that's a mass-assignment risk, since the client can set fields like `admin` that were never meant to be user-settable. I'd introduce a request/response DTO, ideally a record, and map explicitly at the boundary."

## Exception Handling

### What reviewers look for

Reviewers watch for exceptions that are caught and thrown away, caught too broadly, logged and rethrown redundantly (double-logging), or allowed to leak internal details (stack traces, SQL, class names) to API clients.

### ❌ The smell

```java
@Service
public class PaymentService {

    public PaymentResult charge(String cardToken, BigDecimal amount) {
        try {
            return paymentGateway.charge(cardToken, amount);
        } catch (Exception e) {
            log.error("Payment failed", e); // logged here...
            throw new RuntimeException(e);  // ...and rethrown, causing double logging upstream
        }
    }

    public void refund(String transactionId) {
        try {
            paymentGateway.refund(transactionId);
        } catch (GatewayException e) {
            // swallowed — caller has no idea the refund failed
        }
    }
}

@RestController
public class PaymentController {

    @PostMapping("/api/payments")
    public ResponseEntity<?> charge(@RequestBody ChargeRequest request) {
        try {
            return ResponseEntity.ok(paymentService.charge(request.cardToken(), request.amount()));
        } catch (Exception e) {
            return ResponseEntity.status(500).body(e.getMessage()); // leaks internals to the client
        }
    }
}
```

### Why it's a problem

- `catch (Exception e)` catches everything, including bugs like `NullPointerException`, and hides *what actually went wrong* behind a generic message.
- Logging and then rethrowing means the same error gets logged twice (once here, once wherever it's caught next), polluting logs and making incident triage harder.
- Swallowing `GatewayException` in `refund` means a failed refund silently looks like a success — a real money bug, not a style nit.
- Returning `e.getMessage()` (or a stack trace) to an HTTP client can leak internal class names, SQL fragments, or file paths — an information-disclosure issue, and it's a bad user experience besides.
- Wrapping a checked exception in a bare `RuntimeException` loses the original exception's type, so upstream code can no longer catch anything specific.

### ✅ The fix

```java
@Service
public class PaymentService {

    public PaymentResult charge(String cardToken, BigDecimal amount) {
        try {
            return paymentGateway.charge(cardToken, amount);
        } catch (GatewayTimeoutException e) {
            throw new PaymentProcessingException("Payment gateway timed out", e);
        }
        // let unexpected exceptions propagate — don't catch what you can't handle
    }

    public void refund(String transactionId) {
        try {
            paymentGateway.refund(transactionId);
        } catch (GatewayException e) {
            throw new RefundFailedException("Refund failed for transaction " + transactionId, e);
        }
    }
}

@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(PaymentProcessingException.class)
    public ProblemDetail handlePaymentFailure(PaymentProcessingException ex) {
        log.warn("Payment processing failed", ex); // logged exactly once, at the boundary
        ProblemDetail problem = ProblemDetail.forStatus(HttpStatus.BAD_GATEWAY);
        problem.setTitle("Payment failed");
        problem.setDetail("We couldn't process your payment. Please try again.");
        return problem; // no internal details leaked
    }

    @ExceptionHandler(Exception.class)
    public ProblemDetail handleUnexpected(Exception ex) {
        log.error("Unexpected error", ex);
        ProblemDetail problem = ProblemDetail.forStatus(HttpStatus.INTERNAL_SERVER_ERROR);
        problem.setTitle("Something went wrong");
        return problem;
    }
}
```

Logging happens once, at the boundary where the exception is finally handled — not at every layer it passes through.

### What to say in the interview

"I'd replace the broad `catch (Exception e)` blocks with specific exception types, stop the swallow in `refund` since that's a silent money bug, and centralize error responses in a `@RestControllerAdvice` using `ProblemDetail` so clients get a consistent, safe error shape instead of a leaked stack trace."

## Logging Best Practices

### What reviewers look for

Reviewers look for structured, parameterized logging at the right level, no secrets or PII in logs, no `printStackTrace`, a correlation/trace ID to tie a request together across log lines, and no logging inside hot loops.

### ❌ The smell

```java
@Service
public class LoginService {

    public LoginResult login(String username, String password) {
        log.info("Login attempt for user: " + username + " with password: " + password); // PII + secret!

        try {
            User user = userRepository.findByUsername(username).orElseThrow();
            boolean valid = passwordEncoder.matches(password, user.getPasswordHash());
            if (!valid) {
                log.error("Login failed for " + username); // wrong level for a routine failed login
            }
            return new LoginResult(valid);
        } catch (Exception e) {
            e.printStackTrace(); // goes to stdout, not the log pipeline, no context
            return new LoginResult(false);
        }
    }

    public void syncAllUsers(List<User> users) {
        for (User user : users) {
            log.info("Syncing user " + user.getId()); // one log line per row, floods logs at scale
            externalDirectory.sync(user);
        }
    }
}
```

### Why it's a problem

- Logging the raw password is a serious data leak — logs get shipped, retained, and read by more people than the database ever is.
- String concatenation (`"text" + variable`) builds the string even when the log level is disabled, wasting CPU; parameterized logging (`log.info("... {}", var)`) skips that work.
- `log.error` for an everyday "wrong password" event pollutes error dashboards and trains people to ignore alerts ("alert fatigue"). A failed login is `info` or `warn` at most.
- `printStackTrace()` writes to stdout, bypassing log levels, log aggregation, and structured fields — it's invisible to your log pipeline and unsearchable in production.
- No correlation ID means that when ten requests interleave in the logs, you cannot tell which log lines belong to which request.
- Logging once per row in a loop over thousands of users turns a sync job into a multi-gigabyte log file and can itself become the bottleneck.

### ✅ The fix

```java
@Service
public class LoginService {

    private static final Logger log = LoggerFactory.getLogger(LoginService.class);

    public LoginResult login(String username, String password) {
        log.info("Login attempt for user={}", username); // no password, parameterized

        User user = userRepository.findByUsername(username).orElseThrow();
        boolean valid = passwordEncoder.matches(password, user.getPasswordHash());
        if (!valid) {
            log.warn("Login failed for user={}", username); // warn, not error — expected outcome
        }
        return new LoginResult(valid);
        // let unexpected exceptions bubble to a central handler that logs with full context
    }

    public void syncAllUsers(List<User> users) {
        log.info("Starting sync for {} users", users.size()); // one summary line, not one per row
        int failures = 0;
        for (User user : users) {
            try {
                externalDirectory.sync(user);
            } catch (SyncException e) {
                failures++;
                log.debug("Sync failed for user={}", user.getId(), e); // debug for per-item detail
            }
        }
        log.info("Sync finished: {} users, {} failures", users.size(), failures);
    }
}
```

In a real service, requests also carry a correlation/trace ID (e.g. via Spring Cloud Sleuth/Micrometer Tracing, or an MDC value set in a filter) so every log line for one request can be grepped together.

### What to say in the interview

"The password should never be logged — that's a hard no regardless of level. I'd also drop this to parameterized logging, fix the log levels since a failed login isn't an `error`, and replace the per-row logging in the sync loop with a summary line plus per-item detail at `debug`."

## Validation Placement

### What reviewers look for

Reviewers check that input is validated at the boundary (`@Valid` on the controller parameter), that bean validation is used for *shape* checks (not-null, format, length) while business rules live in the service layer, and that the API never trusts client-supplied values it shouldn't (price, role, ownership).

### ❌ The smell

```java
public class CreateDiscountRequest {
    private String code;
    private BigDecimal percentage; // no constraints at all
}

@RestController
public class DiscountController {

    @PostMapping("/api/discounts")
    public DiscountResponse create(@RequestBody CreateDiscountRequest request) { // no @Valid
        // validation happens deep in the service, after the object is already built
        if (request.getPercentage() == null) {
            throw new IllegalArgumentException("percentage required");
        }
        if (request.getPercentage().compareTo(BigDecimal.ZERO) < 0
                || request.getPercentage().compareTo(BigDecimal.valueOf(100)) > 0) {
            throw new IllegalArgumentException("percentage must be 0-100");
        }
        return discountService.create(request.getCode(), request.getPercentage());
    }

    @PostMapping("/api/orders/{id}/apply-discount")
    public OrderResponse applyDiscount(@PathVariable Long id, @RequestBody ApplyDiscountRequest request) {
        // trusting a client-supplied discount amount instead of looking it up server-side
        return orderService.applyDiscount(id, request.getDiscountAmount());
    }
}
```

### Why it's a problem

- Missing `@Valid` means the manual `if` checks in the controller are the *only* validation — easy to forget on the next field someone adds, and it duplicates what Bean Validation gives for free.
- Shape validation (range checks, not-null, format) mixed into imperative `if` blocks is harder to read than declarative annotations, and it's not reusable across other endpoints that accept the same fields.
- `applyDiscount` trusts a `discountAmount` sent by the client instead of recomputing it server-side from the discount code and order total — a client can send any amount it wants, including a negative one that inflates the order.
- Business rules (e.g. "a discount code can only be used once per customer," "percentage discounts don't apply to already-discounted items") don't belong in `@Min`/`@Max` annotations — they need the service layer, where you have access to the database and other business state.

### ✅ The fix

```java
public record CreateDiscountRequest(
    @NotBlank @Size(max = 20) String code,
    @NotNull @DecimalMin("0") @DecimalMax("100") BigDecimal percentage
) {}

public record ApplyDiscountRequest(@NotBlank String discountCode) {}

@RestController
public class DiscountController {

    @PostMapping("/api/discounts")
    public DiscountResponse create(@Valid @RequestBody CreateDiscountRequest request) {
        // shape is already guaranteed valid by the time we get here
        return discountService.create(request.code(), request.percentage());
    }

    @PostMapping("/api/orders/{id}/apply-discount")
    public OrderResponse applyDiscount(@PathVariable Long id, @Valid @RequestBody ApplyDiscountRequest request) {
        // service looks up the discount server-side and enforces business rules
        return orderService.applyDiscount(id, request.discountCode());
    }
}

@Service
public class OrderService {
    public OrderResponse applyDiscount(Long orderId, String discountCode) {
        Discount discount = discountRepository.findByCode(discountCode)
            .orElseThrow(() -> new DiscountNotFoundException(discountCode));
        // business rule: one use per customer, checked here, not in an annotation
        if (discountUsageRepository.hasBeenUsed(discountCode, orderId)) {
            throw new DiscountAlreadyUsedException(discountCode);
        }
        // amount is computed server-side, never trusted from the client
        return OrderResponse.from(orderRepository.applyDiscountAmount(orderId, discount.computeAmount()));
    }
}
```

### What to say in the interview

"I'd add `@Valid` plus Bean Validation annotations for the shape checks, and move the 'one use per customer' rule into the service since that needs a database lookup, not an annotation. And the discount amount should be computed server-side from the code — never trusted straight from the request body."

## Security Misconfigurations

### What reviewers look for

This is the highest-stakes category. Reviewers scan Spring Security config, CORS config, and data access code for anything that widens the attack surface: disabled protections, overly broad `permitAll()`, wildcard CORS with credentials, hardcoded secrets, weak hashing, unchecked JWTs, exposed actuator endpoints, and string-built SQL.

### ❌ The smell

```java
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    private static final String JWT_SECRET = "s3cr3t123"; // hardcoded secret in source control

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .csrf(csrf -> csrf.disable()) // disabled on a cookie-session app
            .cors(cors -> cors.configurationSource(request -> {
                CorsConfiguration config = new CorsConfiguration();
                config.setAllowedOrigins(List.of("*"));      // wildcard origin...
                config.setAllowCredentials(true);             // ...combined with credentials
                config.setAllowedMethods(List.of("*"));
                return config;
            }))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/actuator/**").permitAll()   // actuator wide open
                .requestMatchers("/api/**").permitAll()        // entire API open
                .anyRequest().authenticated()
            );
        return http.build();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new MessageDigestPasswordEncoder("MD5"); // weak, fast, unsalted-by-default hashing
    }
}

@Repository
public class ReportRepository {
    public List<Report> findByOwner(String owner) {
        String sql = "SELECT * FROM reports WHERE owner = '" + owner + "'"; // SQL injection
        return jdbcTemplate.query(sql, new ReportRowMapper());
    }
}
```

### Why it's a problem

- A hardcoded JWT secret in source code is visible to anyone with repo access (including in git history forever) — an attacker who gets it can forge valid tokens.
- Disabling CSRF is correct for a stateless, token-based API, but wrong for a session-cookie-based app — without it, any site can make the browser submit authenticated requests to yours.
- Wildcard CORS origin (`*`) combined with `allowCredentials(true)` is actually rejected by browsers as invalid, but teams sometimes work around it with a reflected origin, which is just as dangerous: it lets *any* website read authenticated responses from your API.
- Exposing `/actuator/**` without auth leaks environment variables, beans, heap dumps, and sometimes a `/shutdown` endpoint — a well-known reconnaissance and attack vector.
- `permitAll()` on the whole `/api/**` tree means new endpoints are open by default unless someone remembers to lock them down — the default should be closed, with explicit exceptions.
- MD5 (or SHA-1, or unsalted hashing in general) is not a password hash — it's fast to brute-force with commodity hardware and has no per-password salt.
- Building SQL by string concatenation from user input is textbook SQL injection: `owner` could be `' OR '1'='1`.

### ✅ The fix

```java
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            // CSRF stays enabled for cookie-session auth; disable only for genuinely stateless
            // token-based APIs (e.g. Bearer JWT with no session cookie)
            .cors(cors -> cors.configurationSource(corsConfigurationSource()))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/actuator/health").permitAll()
                .requestMatchers("/actuator/**").hasRole("ADMIN")
                .requestMatchers("/api/public/**").permitAll()
                .anyRequest().authenticated() // closed by default
            );
        return http.build();
    }

    private CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOrigins(List.of("https://app.example.com")); // explicit allowlist
        config.setAllowCredentials(true);
        config.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE"));
        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/api/**", config);
        return source;
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder(); // salted, slow-by-design
    }
}
```

```yaml
# application.yml — secret comes from the environment, not source control
jwt:
  secret: ${JWT_SECRET}
```

```java
@Repository
public class ReportRepository {
    public List<Report> findByOwner(String owner) {
        String sql = "SELECT * FROM reports WHERE owner = ?"; // parameterized
        return jdbcTemplate.query(sql, new ReportRowMapper(), owner);
    }
}
```

### What to say in the interview

"There are several security issues here I'd block the PR on, not just flag: the hardcoded secret, the wide-open actuator endpoints, the SQL built by string concatenation, and MD5 for password hashing. Any one of these I'd treat as a merge blocker, not a nitpick."

## Bean Scope Issues

### What reviewers look for

Almost every Spring bean is a **singleton** by default — one instance for the whole application. Reviewers look for mutable instance state stored on a singleton (which becomes shared, unsynchronized state across all requests) and for a smaller-scoped bean (`request`, `session`, `prototype`) injected naively into a singleton.

### ❌ The smell

```java
@Service
public class ReportGenerator {

    private String currentUserRegion; // mutable field on a singleton — shared across all requests!
    private final List<String> processedIds = new ArrayList<>(); // grows forever, shared across requests

    public Report generate(String userId, String region) {
        this.currentUserRegion = region; // race: another thread's request can overwrite this
        processedIds.add(userId);
        return buildReport(currentUserRegion);
    }
}

@Component
@Scope(value = "request", proxyMode = ScopedProxyMode.NO) // no proxy!
public class RequestContext {
    private String correlationId;
    // getters/setters
}

@Service // singleton
public class AuditService {
    private final RequestContext requestContext; // request-scoped bean injected directly

    public AuditService(RequestContext requestContext) {
        this.requestContext = requestContext; // captured once, at startup — wrong instance forever
    }
}
```

### Why it's a problem

- `ReportGenerator` is a singleton, so `currentUserRegion` is shared by every concurrent request. Two users' requests can interleave and generate a report with the *other* user's region.
- `processedIds` never shrinks — it's an unbounded list living for the lifetime of the application, a slow memory leak.
- Injecting a `request`-scoped bean directly into a singleton without a scoped proxy fails at startup (Spring can't satisfy the dependency, since there's no "current request" when the singleton is built) or, if it does start, captures a single stale instance forever instead of the current request's instance.

### ✅ The fix

```java
@Service
public class ReportGenerator {
    // no mutable instance state — everything is a local variable, scoped to the call
    public Report generate(String userId, String region) {
        return buildReport(region);
    }
}

@Component
@Scope(value = "request", proxyMode = ScopedProxyMode.TARGET_CLASS) // proxy re-resolves per request
public class RequestContext {
    private String correlationId;
    // getters/setters
}

@Service
public class AuditService {
    private final RequestContext requestContext; // this is a proxy, delegates to the real per-request bean

    public AuditService(RequestContext requestContext) {
        this.requestContext = requestContext;
    }

    public void record(String action) {
        log.info("action={} correlationId={}", action, requestContext.getCorrelationId());
    }
}
```

If you need a fresh instance of a `prototype` bean on demand from inside a singleton, without a scoped proxy, use `ObjectProvider<T>` (`objectProvider.getObject()` gets a new instance each call) or an abstract `@Lookup` method that Spring overrides at runtime.

### What to say in the interview

"Since Spring beans are singletons by default, any mutable field here is shared state across every concurrent request — that's a data race waiting to happen, not just a style issue. I'd remove the instance fields and pass state through method parameters instead, and for the request-scoped bean I'd use a scoped proxy or `ObjectProvider` so the singleton always resolves the *current* request's instance."

## Circular Dependencies

### What reviewers look for

Reviewers want to know why a circular dependency happened (usually two services that both need "a little bit" of each other) and whether the fix is a real redesign or a bandaid flag.

### ❌ The smell

```java
@Service
public class OrderService {
    private final CustomerService customerService;

    public OrderService(CustomerService customerService) {
        this.customerService = customerService;
    }

    public void placeOrder(Order order) {
        customerService.recordOrderHistory(order);
    }
}

@Service
public class CustomerService {
    private final OrderService orderService; // OrderService <-> CustomerService cycle

    public CustomerService(OrderService orderService) {
        this.orderService = orderService;
    }

    public void banCustomer(Long customerId) {
        orderService.cancelAllOrders(customerId);
    }
}
```

```yaml
# application.yml — the actual smell: papering over the cycle instead of fixing it
spring:
  main:
    allow-circular-references: true
```

### Why it's a problem

- Spring Boot 2.6+ fails startup by default (`BeanCurrentlyInCreationException`) when it detects a constructor-injection cycle — this is intentional, because the cycle usually signals two services with overlapping responsibilities, not just a wiring inconvenience.
- Setting `spring.main.allow-circular-references=true` makes Spring fall back to field-injection-style resolution to break the cycle at startup, but it doesn't fix the design problem — it just silences the warning. The two services are still tightly coupled and now harder to reason about (which one's constructor runs "first" is no longer obvious).
- A cycle usually means the domain boundary is wrong: `OrderService` and `CustomerService` both want to reach into each other's job, which means part of what one of them does actually belongs to a third concept.

### ✅ The fix

```java
// Real fix #1: extract the shared responsibility into a third bean
@Service
public class OrderHistoryService {
    // owns the "record and cancel order history" behavior that both sides needed
    public void recordOrderHistory(Order order) { /* ... */ }
    public void cancelAllOrders(Long customerId) { /* ... */ }
}

@Service
public class OrderService {
    private final OrderHistoryService orderHistoryService;
    public OrderService(OrderHistoryService orderHistoryService) {
        this.orderHistoryService = orderHistoryService;
    }
    public void placeOrder(Order order) {
        orderHistoryService.recordOrderHistory(order);
    }
}

@Service
public class CustomerService {
    private final OrderHistoryService orderHistoryService;
    public CustomerService(OrderHistoryService orderHistoryService) {
        this.orderHistoryService = orderHistoryService;
    }
    public void banCustomer(Long customerId) {
        orderHistoryService.cancelAllOrders(customerId);
    }
}
```

```java
// Real fix #2: use application events to decouple the direction of the call entirely
@Service
public class CustomerService {
    private final ApplicationEventPublisher events;

    public void banCustomer(Long customerId) {
        events.publishEvent(new CustomerBannedEvent(customerId)); // no direct dependency on OrderService
    }
}

@Component
public class OrderCancellationListener {
    @EventListener
    public void onCustomerBanned(CustomerBannedEvent event) {
        orderService.cancelAllOrders(event.customerId());
    }
}
```

### What to say in the interview

"A circular dependency between two services almost always means the boundary is drawn in the wrong place. I'd pull the shared behavior into a third service, or decouple the two with an application event, rather than reaching for `allow-circular-references` — that flag hides the design problem instead of fixing it."

## Configuration Smells

### What reviewers look for

Reviewers check whether config is centralized and typed (`@ConfigurationProperties`) or scattered as raw `@Value` strings, whether secrets live in `application.yml` in plaintext, whether "works on my machine" defaults sneak into production, and whether config gets validated at startup.

### ❌ The smell

```java
@Service
public class PricingService {

    @Value("${pricing.tax-rate:0.08}")
    private double taxRate;

    @Value("${pricing.max-discount-percentage:20}")
    private int maxDiscountPercentage;

    public BigDecimal calculateTotal(BigDecimal subtotal) {
        // 0.9 is a magic number nobody can trace back to a requirement
        BigDecimal adjusted = subtotal.multiply(BigDecimal.valueOf(0.9));
        return adjusted.add(adjusted.multiply(BigDecimal.valueOf(taxRate)));
    }
}
```

```yaml
# application.yml — checked into source control
spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/mydb  # only works on a laptop
    username: admin
    password: admin123          # plaintext secret in source control
stripe:
  api-key: sk_live_51H8x...     # live payment API key, in source control
```

### Why it's a problem

- `@Value` scattered across many classes means there's no single place to see "what does this application need configured" — every new field means hunting through the codebase.
- `0.9` with no name or comment is a magic number: nobody six months from now knows if it's a fee, a rounding factor, or a bug.
- Plaintext secrets in `application.yml`, committed to source control, are visible to anyone with repo access, forever (git history doesn't forget), and get picked up by any CI job that checks out the repo.
- A live Stripe key in source control is a "call it an incident" level mistake — it should be rotated the moment it's found.
- `jdbc:postgresql://localhost:5432/mydb` as the checked-in default means the app "just happens" to work locally and silently does the wrong thing (or fails confusingly) the moment it's deployed anywhere else, because nobody is forced to override it explicitly.
- Without `@Validated` on a configuration properties class, a typo'd or missing required property fails at first use, deep in business logic, instead of at startup where it's easy to diagnose.

### ✅ The fix

```java
@ConfigurationProperties(prefix = "pricing")
@Validated
public record PricingProperties(
    @NotNull @DecimalMin("0") @DecimalMax("1") BigDecimal taxRate,
    @Min(0) @Max(100) int maxDiscountPercentage
) {}

@Service
public class PricingService {

    private static final BigDecimal MEMBER_DISCOUNT_FACTOR = BigDecimal.valueOf(0.9); // named, not magic

    private final PricingProperties pricing;

    public PricingService(PricingProperties pricing) {
        this.pricing = pricing;
    }

    public BigDecimal calculateTotal(BigDecimal subtotal) {
        BigDecimal adjusted = subtotal.multiply(MEMBER_DISCOUNT_FACTOR);
        return adjusted.add(adjusted.multiply(pricing.taxRate()));
    }
}
```

```yaml
# application.yml — no secrets, no environment-specific values checked in
spring:
  datasource:
    url: ${DATABASE_URL}
    username: ${DATABASE_USERNAME}
    password: ${DATABASE_PASSWORD}
stripe:
  api-key: ${STRIPE_API_KEY}
pricing:
  tax-rate: ${TAX_RATE:0.08}
  max-discount-percentage: ${MAX_DISCOUNT_PERCENTAGE:20}
```

Secrets come from environment variables (or a secrets manager like Vault/AWS Secrets Manager), never from a file in the repo. `@ConfigurationProperties` plus `@Validated` fails fast at startup if a required value is missing or out of range.

### What to say in the interview

"I'd move these from scattered `@Value` fields to a single typed, validated `@ConfigurationProperties` record — that gives one place to see the config surface and a startup-time failure instead of a runtime surprise. And the plaintext secrets need to come out of `application.yml` entirely; that Stripe key should be treated as a leaked-credential incident."

## Testability

### What reviewers look for

Reviewers check whether the code *can* be unit tested at all: hidden static/`new` calls that can't be substituted, non-deterministic time, tests that reach for `@SpringBootTest` when a plain unit test would do, missing coverage for error paths, and mocks built around internals the team doesn't own.

### ❌ The smell

```java
@Service
public class SubscriptionService {

    public Invoice generateInvoice(Subscription subscription) {
        LocalDateTime now = LocalDateTime.now(); // untestable — can't control "now" in a test
        if (now.isAfter(subscription.getRenewalDate())) {
            throw new SubscriptionExpiredException();
        }
        InvoiceIdGenerator generator = new InvoiceIdGenerator(); // hard-coded dependency, can't stub
        String invoiceId = generator.generate();
        return new Invoice(invoiceId, now, subscription.getAmount());
    }
}

@SpringBootTest // spins up the entire application context for one method's logic
class SubscriptionServiceTest {

    @Autowired
    private SubscriptionService subscriptionService;

    @Test
    void generatesInvoice() {
        Subscription sub = new Subscription(/* ... */);
        Invoice invoice = subscriptionService.generateInvoice(sub);
        assertNotNull(invoice);
    }
    // no test at all for the "subscription expired" path
}
```

### Why it's a problem

- `LocalDateTime.now()` inside business logic makes the method non-deterministic — a test can't assert "given it's exactly midnight on renewal day" without sleeping or mocking static calls.
- `new InvoiceIdGenerator()` hard-wires a concrete dependency inside the method; there's no seam to substitute a fake in a test, so the test is really an integration test of both classes at once.
- `@SpringBootTest` boots the *entire* Spring context — every bean, every auto-configuration — just to test one service's branching logic. That's slow (seconds instead of milliseconds) and multiplies CI time across a large test suite.
- There's no test for the expired-subscription path — the interesting, failure-prone branch is exactly the one left untested.

### ✅ The fix

```java
@Service
public class SubscriptionService {

    private final Clock clock;
    private final InvoiceIdGenerator invoiceIdGenerator;

    public SubscriptionService(Clock clock, InvoiceIdGenerator invoiceIdGenerator) {
        this.clock = clock;
        this.invoiceIdGenerator = invoiceIdGenerator;
    }

    public Invoice generateInvoice(Subscription subscription) {
        LocalDateTime now = LocalDateTime.now(clock); // injected, controllable in tests
        if (now.isAfter(subscription.getRenewalDate())) {
            throw new SubscriptionExpiredException();
        }
        String invoiceId = invoiceIdGenerator.generate();
        return new Invoice(invoiceId, now, subscription.getAmount());
    }
}

@Configuration
class ClockConfig {
    @Bean
    Clock systemClock() {
        return Clock.systemUTC(); // production wiring, one line
    }
}

class SubscriptionServiceTest { // plain JUnit, no Spring context at all

    private final Clock fixedClock = Clock.fixed(Instant.parse("2026-08-07T00:00:00Z"), ZoneOffset.UTC);
    private final InvoiceIdGenerator fakeGenerator = () -> "INV-TEST-1";
    private final SubscriptionService service = new SubscriptionService(fixedClock, fakeGenerator);

    @Test
    void generatesInvoiceBeforeRenewal() {
        Subscription sub = subscriptionRenewingOn("2026-09-01T00:00:00Z");
        Invoice invoice = service.generateInvoice(sub);
        assertThat(invoice.id()).isEqualTo("INV-TEST-1");
    }

    @Test
    void throwsWhenSubscriptionExpired() {
        Subscription sub = subscriptionRenewingOn("2026-08-01T00:00:00Z"); // in the past vs fixed clock
        assertThrows(SubscriptionExpiredException.class, () -> service.generateInvoice(sub));
    }
}
```

An injected `Clock` and a constructor-injected `InvoiceIdGenerator` turn this into a class you can construct with `new` in a millisecond-fast test, with both the happy path and the error path covered.

### What to say in the interview

"I'd inject a `Clock` instead of calling `LocalDateTime.now()` directly, so tests can pin time exactly. I'd also swap `@SpringBootTest` for a plain unit test since none of this logic needs a running application context, and I'd add the missing test for the expired-subscription branch — that's the path most likely to have a bug."

## Thread Safety

### What reviewers look for

Because most Spring beans are singletons shared across every request thread, reviewers look hard for mutable state that isn't synchronized, non-thread-safe JDK classes used as shared fields, check-then-act races, `ThreadLocal` values that leak across requests on pooled threads, and `@Async` methods that touch shared state without a lock.

### ❌ The smell

```java
@Service
public class ReportFormatter {

    private final SimpleDateFormat dateFormat = new SimpleDateFormat("yyyy-MM-dd"); // not thread-safe!
    private int reportsGenerated = 0; // unguarded shared counter

    public String format(Date date) {
        reportsGenerated++; // check-then-act race: read + write isn't atomic
        return dateFormat.format(date); // concurrent calls corrupt each other's parsing state
    }
}

@Component
public class TenantContext {
    private static final ThreadLocal<String> currentTenant = new ThreadLocal<>();

    public static void set(String tenantId) {
        currentTenant.set(tenantId);
    }

    public static String get() {
        return currentTenant.get();
    }
    // no remove() call anywhere
}

@Service
public class BulkExportService {
    private final Map<String, ExportJob> activeJobs = new HashMap<>(); // plain HashMap, shared

    @Async
    public void startExport(String jobId) {
        activeJobs.put(jobId, new ExportJob(jobId)); // concurrent writes from multiple threads
        // ... do export ...
        activeJobs.remove(jobId);
    }
}
```

### Why it's a problem

- `SimpleDateFormat` is documented as not thread-safe. Two concurrent requests calling `format()` on the same shared instance can corrupt each other's internal calendar state and produce wrong dates — intermittently, which makes it brutal to debug.
- `reportsGenerated++` looks atomic but isn't: it's a read, an increment, and a write as three separate steps. Two threads can both read `5`, both write `6`, and one increment is lost.
- `TenantContext` uses `ThreadLocal` without ever calling `remove()`. On a pooled thread (like a servlet container's worker threads, or any `ExecutorService`), the thread is reused for a *different* request later — and it still has the previous request's tenant ID unless it's explicitly cleared, leaking one tenant's context into another's request. This is a real data-isolation bug, not just a leak.
- Plain `HashMap` is not safe for concurrent modification; `@Async` methods run on separate threads by design, so concurrent `put`/`remove` calls on a shared `HashMap` can corrupt its internal structure or throw `ConcurrentModificationException`.

### ✅ The fix

```java
@Service
public class ReportFormatter {

    private static final DateTimeFormatter DATE_FORMAT = DateTimeFormatter.ISO_LOCAL_DATE; // immutable, thread-safe
    private final AtomicInteger reportsGenerated = new AtomicInteger(); // atomic increment

    public String format(LocalDate date) {
        reportsGenerated.incrementAndGet();
        return date.format(DATE_FORMAT);
    }
}

@Component
public class TenantContext {
    private static final ThreadLocal<String> currentTenant = new ThreadLocal<>();

    public static void set(String tenantId) {
        currentTenant.set(tenantId);
    }

    public static String get() {
        return currentTenant.get();
    }

    public static void clear() { // called in a filter's finally block, every request
        currentTenant.remove();
    }
}

@Service
public class BulkExportService {
    private final Map<String, ExportJob> activeJobs = new ConcurrentHashMap<>(); // safe for concurrent access

    @Async
    public void startExport(String jobId) {
        activeJobs.put(jobId, new ExportJob(jobId));
        try {
            // ... do export ...
        } finally {
            activeJobs.remove(jobId);
        }
    }
}
```

`java.time.DateTimeFormatter` (unlike `SimpleDateFormat`) is immutable and thread-safe, so it can safely be a shared `static final` field. The `ThreadLocal` gets cleared in a filter's `finally` block so a reused pooled thread never carries stale tenant data into the next request.

### What to say in the interview

"`SimpleDateFormat` as a shared field is a classic thread-safety bug — I'd switch to `DateTimeFormatter`, which is immutable. The `ThreadLocal` without a `remove()` is the one I'd push hardest on, though: on a pooled thread that gets reused, this leaks one tenant's context into a different tenant's request. And the plain `HashMap` under `@Async` needs to be a `ConcurrentHashMap` at minimum."

## Performance Bottlenecks

### What reviewers look for

Reviewers scan for the "works with 10 rows, dies with 10 million" patterns: unbounded `findAll()`, no pagination, no index behind a filtered query, per-row work that should be batched, missing timeouts on outbound calls, and unbounded in-memory caches or queues.

### ❌ The smell

```java
@RestController
public class ProductController {

    @GetMapping("/api/products")
    public List<Product> getAllProducts() {
        return productRepository.findAll(); // no pagination — returns the entire table
    }

    @GetMapping("/api/products/search")
    public List<Product> search(@RequestParam String category) {
        // filters on a column with no index — full table scan on every call
        return productRepository.findByCategory(category);
    }
}

@Service
public class InventorySyncService {

    public void syncPrices(List<ProductPriceUpdate> updates) {
        for (ProductPriceUpdate update : updates) {
            Product product = productRepository.findById(update.productId()).orElseThrow();
            product.setPrice(update.newPrice());
            productRepository.save(product); // one INSERT/UPDATE round-trip per item
        }
    }

    public String fetchSupplierCatalog() {
        // no timeout — a slow or hung supplier endpoint can block this thread indefinitely
        return restTemplate.getForObject("https://supplier.example.com/catalog", String.class);
    }
}

@Service
public class PriceCache {
    private final Map<String, BigDecimal> cache = new HashMap<>(); // grows forever, no eviction, no bound
    public void put(String sku, BigDecimal price) { cache.put(sku, price); }
}
```

### Why it's a problem

- `findAll()` with no pagination returns every row in one response — fine with 200 products, an outage with 2 million.
- Filtering on an unindexed column forces a full table scan; latency grows linearly (or worse) with table size and gets worse every month as the table grows.
- The `syncPrices` loop does one `SELECT` and one `UPDATE` per item — for 10,000 price updates that's 20,000 round-trips instead of a handful of batched statements.
- No timeout on the outbound HTTP call means a hung supplier endpoint can tie up a request thread indefinitely, eventually exhausting the thread pool for the whole application.
- The unbounded `HashMap` cache never evicts anything — every SKU ever seen stays in memory forever, a slow memory leak that eventually causes `OutOfMemoryError`.

### ✅ The fix

```java
@RestController
public class ProductController {

    @GetMapping("/api/products")
    public Page<Product> getProducts(Pageable pageable) { // bounded page size
        return productRepository.findAll(pageable);
    }

    @GetMapping("/api/products/search")
    public Page<Product> search(@RequestParam String category, Pageable pageable) {
        return productRepository.findByCategory(category, pageable);
    }
}
```

```java
@Entity
@Table(name = "products", indexes = @Index(name = "idx_products_category", columnList = "category"))
public class Product { /* ... */ }
```

```java
@Service
public class InventorySyncService {

    private final JdbcTemplate jdbcTemplate;
    private final RestClient restClient; // configured with explicit timeouts

    public void syncPrices(List<ProductPriceUpdate> updates) {
        jdbcTemplate.batchUpdate(
            "UPDATE products SET price = ? WHERE id = ?",
            updates.stream()
                .map(u -> new Object[]{u.newPrice(), u.productId()})
                .toList()
        ); // one batched round-trip instead of thousands
    }

    public String fetchSupplierCatalog() {
        return restClient.get()
            .uri("https://supplier.example.com/catalog")
            .retrieve()
            .body(String.class); // restClient built with connect/read timeouts configured
    }
}
```

```java
@Configuration
public class RestClientConfig {
    @Bean
    RestClient restClient() {
        ClientHttpRequestFactorySettings settings = ClientHttpRequestFactorySettings.DEFAULTS
            .withConnectTimeout(Duration.ofSeconds(2))
            .withReadTimeout(Duration.ofSeconds(5));
        return RestClient.builder()
            .requestFactory(ClientHttpRequestFactories.get(settings))
            .build();
    }
}
```

```java
@Service
public class PriceCache {
    // bounded, size-evicting cache instead of an unbounded HashMap
    private final Cache<String, BigDecimal> cache = Caffeine.newBuilder()
        .maximumSize(10_000)
        .expireAfterWrite(Duration.ofMinutes(30))
        .build();

    public void put(String sku, BigDecimal price) { cache.put(sku, price); }
}
```

### What to say in the interview

"I'd paginate both list endpoints — an unbounded `findAll()` is a ticking time bomb as the table grows. The sync loop should be a batch update, not one round-trip per row, and the outbound call needs an explicit timeout so a hung supplier can't tie up our thread pool. I'd also swap the raw `HashMap` cache for a bounded one with eviction."

## The 60-Second Review Checklist

Run through this list top to bottom on any Spring PR:

1. **Injection style** — constructor injection, `final` fields, no field `@Autowired`?
2. **Transaction boundary** — `@Transactional` on the right layer, no self-invocation, no HTTP/IO inside it, `readOnly` set where applicable?
3. **N+1 risk** — any loop touching a lazy association? Any collection accessed after a query without a fetch strategy?
4. **Lazy loading** — is `open-in-view` relied on implicitly? Are associations accessed outside the transaction?
5. **Entity leakage** — is an `@Entity` used as a request or response body anywhere?
6. **Exception handling** — any `catch (Exception e)`, swallowed exceptions, or leaked stack traces to clients?
7. **Logging** — any secrets/PII logged, wrong log levels, `printStackTrace`, or logging inside a loop?
8. **Validation** — is `@Valid` present? Are business rules mistakenly encoded as Bean Validation annotations, or vice versa?
9. **Security** — CSRF/CORS config sane for the auth model? Any hardcoded secret? Any string-built SQL? Actuator locked down? Password hashing strong?
10. **Bean scope** — any mutable field on a singleton? Any scope mismatch injected without a proxy/`ObjectProvider`?
11. **Circular dependencies** — does the wiring hint at overlapping responsibilities between two services?
12. **Configuration** — typed and validated (`@ConfigurationProperties`), or scattered `@Value` and magic numbers? Any secret or laptop-only default checked into config files?
13. **Testability** — can this class be constructed and tested without `@SpringBootTest`? Is time/randomness injected?
14. **Thread safety** — any shared mutable state, non-thread-safe JDK class as a field, or `ThreadLocal` without cleanup?
15. **Performance** — pagination present? Any per-row DB round-trip that should be batched? Any outbound call without a timeout? Any unbounded cache?

## Rapid-Fire Pitfall Table

| Smell | Why it's bad | Fix |
|---|---|---|
| Field injection (`@Autowired` on a field) | Can't unit test without Spring, mutable, hides missing deps until runtime | Constructor injection with `final` fields |
| `@Transactional` on a controller | Holds a DB connection open for the whole HTTP request | Put `@Transactional` on the service layer |
| Self-invocation of a `@Transactional` method | Bypasses the Spring proxy, annotation is silently ignored | Move the method to another bean, or inject a self-reference |
| HTTP/network call inside a `@Transactional` method | Holds DB locks and a connection open for the network call's duration | Do network calls before or after the transaction |
| Missing `readOnly = true` on read queries | Hibernate can't skip dirty-checking/flush optimizations | Mark query-only transactions `@Transactional(readOnly = true)` |
| Checked exception inside `@Transactional` | Doesn't roll back by default | Throw unchecked exceptions, or set `rollbackFor` |
| Loop that lazy-loads an association per row | N+1 queries, latency scales with row count | `JOIN FETCH`, `@EntityGraph`, `@BatchSize`, or DTO projection |
| Lazy association accessed outside the transaction | `LazyInitializationException` | Fetch/DTO-map inside the `@Transactional` method |
| Relying on `open-in-view=true` implicitly | Hidden queries during serialization, connection held for whole request | Set `open-in-view: false` explicitly, map to DTOs in the service |
| Entity as `@RequestBody` | Mass-assignment vulnerability | Dedicated request DTO/record with only bindable fields |
| Entity as `@ResponseBody` | Leaks internal fields, lazy proxies break Jackson | Dedicated response DTO/record |
| `catch (Exception e)` | Hides real cause, catches bugs along with expected failures | Catch specific exception types |
| Log-then-rethrow | Double logging, noisy logs | Log once, at the boundary that finally handles it |
| Swallowed exception (empty catch block) | Silent failure, especially dangerous for money/data operations | Rethrow a domain exception, or handle explicitly and log |
| Stack trace / exception message returned to client | Information disclosure | `@RestControllerAdvice` + `ProblemDetail` with a safe message |
| Logging secrets or PII | Leaks sensitive data into log aggregation and retention | Never log credentials/PII; redact before logging |
| String-concatenated log messages | Built even when the log level is disabled, wastes CPU | Parameterized logging: `log.info("... {}", var)` |
| `printStackTrace()` | Bypasses log levels and aggregation, invisible in production | Use the logger with the exception object |
| Logging per-row inside a large loop | Floods logs, can become the bottleneck itself | Summary log line plus per-item detail at `debug` |
| Missing `@Valid` on a request body | Manual, duplicated, easy-to-forget validation | `@Valid` + Bean Validation annotations on the DTO |
| Business rule encoded as a Bean Validation annotation | Annotations can't query the database or other state | Move business rules to the service layer |
| Trusting a client-supplied price/amount/role | Client can send any value it wants | Recompute/verify server-side, never trust client-sent business values |
| `csrf.disable()` on a cookie-session app | Opens the door to cross-site request forgery | Keep CSRF enabled for session-cookie auth; disable only for stateless token APIs |
| Wildcard CORS origin with credentials | Lets any site read authenticated responses | Explicit origin allowlist |
| Hardcoded secret in source | Visible to anyone with repo access, forever, via git history | Load from environment variables / secrets manager |
| Actuator endpoints wide open | Leaks env vars, beans, heap dumps; possible shutdown endpoint exposure | Restrict to `ADMIN` role, expose only `health`/`info` publicly |
| Weak/unsalted password hashing (MD5/SHA-1) | Fast to brute-force, no salt | `BCryptPasswordEncoder` or similar adaptive hash |
| SQL built by string concatenation | SQL injection | Parameterized queries / `PreparedStatement` / JPQL params |
| Mutable field on a singleton bean | Shared, unsynchronized state across every concurrent request | Keep state local to the method, or externalize per-request state |
| Smaller-scoped bean injected directly into a singleton | Fails at startup, or captures a stale instance forever | Scoped proxy, `ObjectProvider`, or `@Lookup` |
| Circular dependency between two services | Signals overlapping responsibilities; `allow-circular-references` hides the real problem | Extract a third bean, or decouple with an event |
| `@Value` scattered across many classes | No single view of the app's configuration surface | `@ConfigurationProperties` record, one place per concern |
| Magic numbers in business logic | Untraceable to a requirement, easy to misread | Named constants or config properties |
| Secrets or env-specific values in `application.yml` | Checked into source control, wrong per environment | Environment variables / secrets manager, per-environment profiles |
| No `@Validated` on configuration properties | Missing/invalid config fails deep in business logic, not at startup | `@Validated` + Bean Validation on the properties class |
| `LocalDateTime.now()` / `new Random()` inline in logic | Non-deterministic, untestable | Inject `Clock` / a seedable source, construct via the constructor |
| `@SpringBootTest` for pure unit logic | Slow, boots the whole application context unnecessarily | Plain JUnit test with constructor-injected fakes |
| No test for the error/exception path | The riskiest branch is the one left unverified | Add a test asserting the failure behavior explicitly |
| `SimpleDateFormat` as a shared field | Not thread-safe, corrupts state under concurrent use | `DateTimeFormatter` (immutable, thread-safe) |
| Unguarded `HashMap` shared across threads/`@Async` | Concurrent modification corrupts the map | `ConcurrentHashMap` |
| `ThreadLocal` without `remove()` | Leaks data across requests on a reused pooled thread | Clear it in a `finally` block / filter |
| `findAll()` with no pagination | Returns the entire table, fails as data grows | `Pageable` + `Page<T>` |
| Filtered query on an unindexed column | Full table scan, worsens as the table grows | Add a database index on the filtered column |
| Per-row DB round-trip inside a loop | Thousands of round-trips instead of a handful | Batch updates (`batchUpdate`, bulk JPQL update) |
| Outbound HTTP call with no timeout | A hung dependency can exhaust the thread pool | Configure explicit connect/read timeouts |
| Unbounded in-memory cache | Slow memory leak, eventual `OutOfMemoryError` | Bounded cache with eviction (e.g. Caffeine `maximumSize`) |

## Quick Recap

- Say the review categories out loud, in order: correctness, security, data/transactions, performance, readability, testability.
- Constructor injection with `final` fields is the default; field injection is a smell.
- Keep `@Transactional` short, on the service layer, and free of network calls or self-invocation.
- N+1 queries hide in loops over lazy associations — fix with `JOIN FETCH`, `@EntityGraph`, `@BatchSize`, or DTO projection.
- Never bind or return `@Entity` classes directly at the API boundary — use DTOs/records.
- Catch specific exceptions, log once at the boundary, and never leak stack traces to clients.
- Never log secrets or PII; use parameterized logging and correct levels.
- Validate shape with Bean Validation at the boundary; keep business rules in the service layer; never trust client-sent business values.
- Security issues (secrets, SQL injection, open actuator, weak hashing, broad CORS/CSRF misconfig) are merge blockers, not nitpicks.
- Remember almost every bean is a singleton — mutable instance fields are shared, unsynchronized state.
- A circular dependency is a design smell; fix the boundary, don't just flip `allow-circular-references`.
- Centralize configuration with `@ConfigurationProperties` and `@Validated`; keep secrets out of `application.yml`.
- Inject `Clock` and other non-deterministic sources so classes are testable without a full Spring context.
- Watch for non-thread-safe shared fields, unguarded collections, and `ThreadLocal` leaks on pooled threads.
- Paginate, batch, index, time out, and bound your caches — the four horsemen of "worked in staging, died in production."
