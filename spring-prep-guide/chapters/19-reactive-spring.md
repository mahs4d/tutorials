# 19. Reactive Spring

## Overview

Most Spring applications you've built so far are **blocking**: a thread picks up a request, walks through your code, and if it needs to wait for a database or a remote call, it just sits there — blocked — doing nothing until the answer comes back. **Non-blocking** (reactive) code never sits and waits. When it needs to wait for something, the thread is released back to a pool to do other work, and a callback fires later when the data is ready. Reactive Spring is Spring's toolkit (Reactor, WebFlux, R2DBC) for writing this non-blocking style of code. It genuinely pays off when you have lots of concurrent, I/O-heavy requests (proxies/gateways, streaming APIs, high-fan-out microservices) and you want to serve thousands of connections with a small, fixed number of threads. It is *not* automatically "faster" — for a typical CRUD app backed by a relational database over JDBC, classic Spring MVC is simpler, easier to debug, and usually just as good. Reactive is a tool for a specific problem (scaling concurrent I/O), not a free performance upgrade.

## Reactive Programming

Reactive programming is a style of programming built around **streams of data over time** and **non-blocking backpressure**. Instead of asking for a value and waiting, you *subscribe* to a stream and get notified as values arrive.

The industry standard for this is the **Reactive Streams specification**, which Java itself absorbed into `java.util.concurrent.Flow` (Java 9+), and which Reactor implements. It defines four interfaces:

| Interface | Role | Analogy |
|---|---|---|
| `Publisher<T>` | Produces a stream of items | A YouTube channel that publishes videos |
| `Subscriber<T>` | Consumes items, reacts to completion/errors | A subscriber who watches videos as they're uploaded |
| `Subscription` | The contract between one Publisher and one Subscriber; lets the subscriber `request(n)` items or `cancel()` | The "notify me" + "unsubscribe" button |
| `Processor<T,R>` | Both a Subscriber and a Publisher — a step that transforms a stream | A translator who watches a video and republishes it dubbed |

The key idea is **push vs pull**:

- **Pull** (classic iteration): the consumer calls `next()` whenever it's ready. The consumer is in control, but if the source is slow, the consumer just blocks waiting.
- **Push** (classic callback/observer): the producer sends data whenever it has it. The producer is in control, but if the consumer is slow, it can be overwhelmed.
- **Reactive Streams** is a hybrid: it's push-based, but the *consumer* tells the producer how many items it can handle right now via `request(n)`. This is backpressure — more on that below.

This matters for Spring because it changes the **threading model**:

| | Spring MVC (Servlet) | Spring WebFlux (Reactive) |
|---|---|---|
| Model | Thread-per-request | Event loop (small fixed thread pool) |
| Waiting for I/O | Thread blocks, sits idle | Thread is released, callback resumes later |
| Threads needed for 10,000 slow connections | ~10,000 (one each, plus OS overhead) | A handful (e.g. number of CPU cores) |
| Programming style | Simple, linear, easy to debug/step through | Chained operators, harder to read stack traces |
| Best for | CPU-bound work, simple CRUD, JDBC-backed apps | High-concurrency I/O-bound work, streaming, gateways |
| Underlying server | Tomcat/Jetty/Undertow (blocking I/O by default) | Netty (non-blocking I/O), or Servlet 3.1+ async |

ASCII picture of the difference:

```
Spring MVC — thread per request
 Request 1 -> [Thread-1] --- waits for DB -----------------> response
 Request 2 -> [Thread-2] --- waits for DB -----------------> response
 Request 3 -> [Thread-3] --- waits for DB -----------------> response
 (need N threads for N concurrent slow requests)

Spring WebFlux — event loop
 Request 1 -> [event-loop-1] -- register callback --\
 Request 2 -> [event-loop-1] -- register callback --+--> loop keeps working
 Request 3 -> [event-loop-1] -- register callback --/     on other requests
                                                            |
                     DB driver calls back when data is ready
                                                            v
                             [event-loop-1] resumes and writes response
 (a handful of threads handle thousands of requests)
```

## Reactor

**Reactor** is the library Spring uses to implement Reactive Streams. It gives you two main types, `Mono` and `Flux` (covered next), plus the machinery to build, transform, and run streams.

The most important mental model in Reactor: **a publisher is a recipe, not a running task.**

```java
Mono<String> mono = Mono.fromSupplier(() -> {
    System.out.println("Computing...");
    return "hello";
});
// Nothing is printed yet! Nothing has happened.

mono.subscribe(System.out::println);
// NOW "Computing..." prints, then "hello" prints.
```

This is the golden rule: **nothing happens until you subscribe.** Building a chain of operators (`map`, `filter`, `flatMap`...) just describes *what should happen*, like assembling a recipe. Only `subscribe()` (or something that subscribes on your behalf, like a WebFlux controller returning a `Mono`) actually starts the oven.

This gives us two distinct points in time:

- **Assembly time**: when you build the chain (`Mono.just(...).map(...).filter(...)`). Operators are wired together, but no data flows.
- **Subscription time**: when `subscribe()` is called. Data starts flowing from the source, through each operator, to the subscriber.

### Cold vs hot publishers

| | Cold | Hot |
|---|---|---|
| Definition | Data is generated fresh for *each* subscriber | Data is generated once and shared/broadcast to whoever is currently subscribed |
| Analogy | A Netflix movie — starts from the beginning every time you press play | A live TV broadcast — you see whatever is airing *now*, you missed what aired before |
| Example | `Mono.fromCallable(...)`, `Flux.fromIterable(...)`, a database query | `Sinks.many().multicast()`, a mouse-click event stream, `ConnectableFlux` |
| Late subscriber | Gets everything from the start | Might miss earlier items |

```java
Flux<Long> cold = Flux.interval(Duration.ofSeconds(1)).take(3);
cold.subscribe(v -> System.out.println("A: " + v)); // starts its own 0,1,2 sequence
cold.subscribe(v -> System.out.println("B: " + v)); // starts its own separate 0,1,2 sequence

Sinks.Many<String> sink = Sinks.many().multicast().onBackpressureBuffer();
Flux<String> hot = sink.asFlux();
hot.subscribe(v -> System.out.println("Late subscriber sees: " + v));
sink.tryEmitNext("event-1"); // only subscribers registered *before* this see it
```

### Schedulers

A `Scheduler` decides *which thread(s)* run a piece of work. Reactor ships with a few ready-made pools:

| Scheduler | Backed by | Use it for |
|---|---|---|
| `Schedulers.parallel()` | Fixed pool, sized to CPU cores | CPU-bound, non-blocking computation |
| `Schedulers.boundedElastic()` | Elastic pool (grows, caps around 10x CPU cores), designed for blocking calls | Wrapping blocking legacy code: JDBC, file I/O, blocking legacy clients |
| `Schedulers.single()` | One reusable thread | Low-volume tasks that must run sequentially on the same thread |
| `Schedulers.immediate()` | The calling thread itself | "Don't switch threads" (mostly for internal/test use) |

### `subscribeOn` vs `publishOn`

Both move execution to a different thread, but they affect different parts of the chain:

- `subscribeOn(scheduler)` — affects where the **source** (the emission, the subscription itself) runs. Placement in the chain doesn't matter; it always affects the earliest point (the origin). Only the *first* `subscribeOn` in a chain has effect.
- `publishOn(scheduler)` — affects everything **downstream** of where you place it, until the next `publishOn`. Placement matters a lot.

```java
Mono.fromCallable(() -> blockingJdbcCall())      // blocking work
    .subscribeOn(Schedulers.boundedElastic())     // run the source on a safe thread
    .map(this::toDto)                              // still on boundedElastic
    .publishOn(Schedulers.parallel())               // switch downstream work to CPU pool
    .map(this::expensiveCpuTransform)               // runs on parallel scheduler
    .subscribe(dto -> log.info("Got {}", dto));
```

Think of `subscribeOn` as "where does the engine start" and `publishOn` as "from this point on, hand results to a different crew."

## Mono / Flux

`Mono<T>` and `Flux<T>` are Reactor's two Publisher implementations:

- `Mono<T>` — zero or **one** element, then completes (or errors). Think of it as a reactive `Optional`/`CompletableFuture`.
- `Flux<T>` — zero to **many** (even infinite) elements over time. Think of it as a reactive, lazy `Stream` or `List`.

```java
Mono<User> user = userRepository.findById(id);          // 0 or 1 User
Flux<Order> orders = orderRepository.findByUserId(id);  // 0..N Orders
```

### `map` vs `flatMap` — the most-confused pair

- `map(Function<T, R>)` — **synchronous**, one-to-one transformation. You give it a plain value, you get a plain value back, *immediately*, no new Publisher involved.
- `flatMap(Function<T, Publisher<R>>)` — **asynchronous**, your function returns *another* `Mono`/`Flux` (usually because it calls another reactive source: a repository, a `WebClient` call). `flatMap` subscribes to that inner publisher and flattens its result into the outer stream.

```java
// map: pure, synchronous transformation — String -> String
Mono<String> upper = Mono.just("hello").map(String::toUpperCase);

// flatMap: the transformation itself is asynchronous (returns a Mono)
Mono<UserDto> dto = userRepository.findById(id)          // Mono<User>
    .flatMap(user -> profileRepository.findByUser(user)   // returns Mono<Profile> -- must flatMap!
        .map(profile -> new UserDto(user, profile)));      // combining two plain values -> map
```

**Rule of thumb**: if your lambda returns a plain value, use `map`. If your lambda returns a `Mono`/`Flux` (because it calls something reactive), use `flatMap`. Using `map` where `flatMap` belongs gives you a compile error (`Mono<Mono<X>>`) — but people "fix" it by calling `.block()` inside the `map`, which silently reintroduces blocking on the event loop. That is one of the most common reactive bugs.

### Operator cheat sheet

| Operator | What it does |
|---|---|
| `map(fn)` | Synchronously transform each element, 1-to-1 |
| `flatMap(fn)` | Transform each element into a new Publisher and flatten the results (async, concurrent by default) |
| `filter(predicate)` | Drop elements that don't match |
| `zip(other, combiner)` | Combine values from two+ publishers pairwise once *all* have emitted |
| `then()` | Ignore all values, just wait for completion, return `Mono<Void>` |
| `switchIfEmpty(alternate)` | If the source completes with no elements, switch to another publisher |
| `defaultIfEmpty(value)` | If the source completes with no elements, emit a fallback value instead |
| `onErrorResume(fn)` | If an error occurs, switch to a fallback publisher instead of propagating the error |
| `onErrorReturn(value)` | If an error occurs, emit a fixed fallback value instead |
| `retryWhen(spec)` | Resubscribe to the source according to a retry strategy (e.g. exponential backoff) on error |
| `timeout(duration)` | Error out if no item arrives within the given duration |
| `doOnNext(consumer)` | Side-effect only (logging, metrics) — does NOT change the emitted value |
| `collectList()` | Collect all elements of a `Flux` into a single `Mono<List<T>>` |

### Error handling example

```java
public Mono<Product> findProductWithFallback(String id) {
    return productRepository.findById(id)
        .switchIfEmpty(Mono.error(new ProductNotFoundException(id)))
        .timeout(Duration.ofSeconds(2))
        .doOnNext(p -> log.info("Loaded product {}", p.getId()))
        .onErrorResume(TimeoutException.class,
            ex -> Mono.just(Product.placeholder()))
        .onErrorReturn(ProductNotFoundException.class, Product.notFound());
}
```

### Testing with `StepVerifier`

`StepVerifier` from `reactor-test` subscribes to your publisher and asserts each emitted signal (`onNext`, `onComplete`, `onError`) step by step — no need to sprinkle `.block()` in your tests.

```java
@Test
void shouldEmitProductsInOrder() {
    Flux<String> flux = Flux.just("apple", "banana", "cherry");

    StepVerifier.create(flux)
        .expectNext("apple")
        .expectNext("banana", "cherry")
        .verifyComplete();
}

@Test
void shouldPropagateNotFoundError() {
    StepVerifier.create(findProductWithFallback("missing-id"))
        .expectNextMatches(p -> p.equals(Product.notFound()))
        .verifyComplete();
}

@Test
void shouldRespectVirtualTime() {
    StepVerifier.withVirtualTime(() -> Flux.interval(Duration.ofHours(1)).take(2))
        .expectSubscription()
        .thenAwait(Duration.ofHours(2))
        .expectNextCount(2)
        .verifyComplete();
}
```

## Backpressure

Backpressure is what happens when a **fast producer** meets a **slow consumer**. Imagine a water hose (producer) connected to a small cup (consumer/subscriber) — if you don't control the flow, the cup overflows. Reactive Streams solves this by letting the *subscriber* say how much it can handle: `subscription.request(n)`. The producer must never send more than requested.

But sometimes the source can't be told to slow down (e.g. mouse events, sensor readings, an external push feed). For those cases, Reactor's `Flux.create` lets you pick an **overflow strategy** for what to do when the buffer is full:

| Strategy | Behavior when overwhelmed |
|---|---|
| `BUFFER` | Queue everything unboundedly (or up to a configured size) — risk of `OutOfMemoryError` if unbounded |
| `DROP` | Drop the newest incoming items that don't fit, keep already-buffered ones |
| `LATEST` | Keep only the most recent item, discard older unconsumed ones |
| `ERROR` | Signal an error (`Exceptions.failWithOverflow()`) when the buffer is exceeded |

```java
Flux<SensorReading> readings = Flux.create(sink -> {
    sensor.onReading(reading -> sink.next(reading));
    sensor.onClose(sink::complete);
}, FluxSink.OverflowStrategy.LATEST);
```

`limitRate` is a simpler tool: it caps how many items are requested from upstream at once, smoothing out the flow instead of requesting everything at full speed.

```java
Flux<Item> items = itemRepository.findAll()
    .limitRate(50); // request upstream in batches of 50 instead of "give me everything"
```

## Spring WebFlux

**WebFlux** is Spring's reactive web framework, sitting alongside (not replacing) Spring MVC. It runs by default on **Netty**, a non-blocking, event-loop based server (it can also run on Servlet 3.1+ containers like Tomcat/Jetty/Undertow in async mode, but Netty is the natural fit).

Annotation-based controllers look almost identical to Spring MVC — just swap the return types for `Mono`/`Flux`:

```java
@RestController
@RequestMapping("/api/products")
public class ProductController {

    private final ProductService productService;

    public ProductController(ProductService productService) {
        this.productService = productService;
    }

    @GetMapping("/{id}")
    public Mono<Product> getProduct(@PathVariable String id) {
        return productService.findById(id);
    }

    @GetMapping
    public Flux<Product> getAllProducts() {
        return productService.findAll();
    }

    @PostMapping
    public Mono<ResponseEntity<Product>> createProduct(@RequestBody Mono<Product> product) {
        return product
            .flatMap(productService::save)
            .map(saved -> ResponseEntity.status(HttpStatus.CREATED).body(saved));
    }
}
```

```xml
<!-- Maven dependency swap: use webflux instead of (or alongside) web -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-webflux</artifactId>
</dependency>
```

### `WebClient` — the reactive replacement for `RestTemplate`

`RestTemplate` is blocking and, as of Spring Framework 5, in maintenance mode. `WebClient` is its non-blocking, fluent replacement, usable from both WebFlux and MVC apps.

```java
@Configuration
public class WebClientConfig {

    @Bean
    public WebClient paymentsWebClient(WebClient.Builder builder) {
        return builder
            .baseUrl("https://payments.internal")
            .defaultHeader(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
            .build();
    }
}

@Service
public class PaymentClient {

    private final WebClient webClient;

    public PaymentClient(WebClient paymentsWebClient) {
        this.webClient = paymentsWebClient;
    }

    public Mono<PaymentResult> charge(PaymentRequest request) {
        return webClient.post()
            .uri("/charges")
            .bodyValue(request)
            .retrieve()
            .onStatus(HttpStatusCode::is4xxClientError,
                resp -> Mono.error(new PaymentRejectedException()))
            .bodyToMono(PaymentResult.class)
            .timeout(Duration.ofSeconds(3))
            .retryWhen(Retry.backoff(3, Duration.ofMillis(200)));
    }
}
```

| | `RestTemplate` | `WebClient` |
|---|---|---|
| Style | Blocking, synchronous | Non-blocking, fluent, reactive |
| Return type | Plain object | `Mono<T>` / `Flux<T>` |
| Status | Maintenance mode | Actively developed, recommended |
| Works in MVC apps | Yes (native fit) | Yes (call `.block()` at the edge if needed) |

### SSE / streaming responses

Because a controller can return a `Flux`, WebFlux naturally supports **Server-Sent Events (SSE)** and other streaming responses — the connection stays open and items are pushed as they become available.

```java
@GetMapping(value = "/stream/prices", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
public Flux<PriceTick> streamPrices() {
    return priceService.liveTicks() // a hot/infinite Flux
        .delayElements(Duration.ofSeconds(1));
}
```

## Functional Endpoints

Besides `@RestController`, WebFlux offers a **functional** style: instead of annotations, you build routes explicitly with `RouterFunction` and `HandlerFunction`. This is closer to how routing works in frameworks like Express.js — a list of "if this path/method matches, call this handler."

- `HandlerFunction<T>` — a function `ServerRequest -> Mono<ServerResponse>`. It reads the incoming request and produces the response.
- `RouterFunction<T>` — a function that maps a `ServerRequest` to a matching `HandlerFunction`, if any.
- `ServerRequest` / `ServerResponse` — immutable, reactive representations of the HTTP request/response (bodies are `Mono`/`Flux`, not raw streams).

```java
@Component
public class ProductHandler {

    private final ProductService productService;

    public ProductHandler(ProductService productService) {
        this.productService = productService;
    }

    public Mono<ServerResponse> getById(ServerRequest request) {
        String id = request.pathVariable("id");
        return productService.findById(id)
            .flatMap(product -> ServerResponse.ok().bodyValue(product))
            .switchIfEmpty(ServerResponse.notFound().build());
    }

    public Mono<ServerResponse> getAll(ServerRequest request) {
        return ServerResponse.ok()
            .contentType(MediaType.APPLICATION_JSON)
            .body(productService.findAll(), Product.class);
    }

    public Mono<ServerResponse> create(ServerRequest request) {
        return request.bodyToMono(Product.class)
            .flatMap(productService::save)
            .flatMap(saved -> ServerResponse.created(URI.create("/api/products/" + saved.getId()))
                .bodyValue(saved));
    }
}

@Configuration
public class ProductRoutes {

    @Bean
    public RouterFunction<ServerResponse> productRoutes(ProductHandler handler) {
        return RouterFunctions.nest(RequestPredicates.path("/api/products"),
            RouterFunctions.route(RequestPredicates.GET(""), handler::getAll)
                .andRoute(RequestPredicates.POST(""), handler::create)
                .andNest(RequestPredicates.path("/{id}"),
                    RouterFunctions.route(RequestPredicates.GET(""), handler::getById))
        );
    }
}
```

`nest` lets you group related routes under a shared path prefix or predicate — handy for keeping large route tables readable, similar to nested route groups in other web frameworks.

## Reactive Security

Spring Security has a fully reactive variant for WebFlux apps. Instead of the servlet filter chain, you configure a `SecurityWebFilterChain`.

```java
@Configuration
@EnableWebFluxSecurity
public class SecurityConfig {

    @Bean
    public SecurityWebFilterChain securityWebFilterChain(ServerHttpSecurity http) {
        return http
            .authorizeExchange(exchanges -> exchanges
                .pathMatchers("/api/public/**").permitAll()
                .pathMatchers(HttpMethod.POST, "/api/products/**").hasRole("ADMIN")
                .anyExchange().authenticated())
            .httpBasic(Customizer.withDefaults())
            .formLogin(Customizer.withDefaults())
            .csrf(ServerHttpSecurity.CsrfSpec::disable)
            .build();
    }

    @Bean
    public ReactiveUserDetailsService userDetailsService() {
        UserDetails admin = User.withDefaultPasswordEncoder()
            .username("admin")
            .password("secret")
            .roles("ADMIN")
            .build();
        return new MapReactiveUserDetailsService(admin);
    }
}
```

`ReactiveUserDetailsService` is the reactive counterpart of `UserDetailsService` — it returns `Mono<UserDetails>` instead of a plain `UserDetails`, so user lookups can hit a reactive database without blocking.

To read the currently authenticated user inside reactive code, use `ReactiveSecurityContextHolder` instead of the classic `SecurityContextHolder` (which relies on a `ThreadLocal` — and reactive code hops between threads, so a `ThreadLocal` would lose the value):

```java
public Mono<String> currentUsername() {
    return ReactiveSecurityContextHolder.getContext()
        .map(SecurityContext::getAuthentication)
        .map(Authentication::getName);
}
```

## Reactive Data Access

The whole point of a reactive web layer collapses if it calls a **blocking** database driver underneath — you'd be blocking one of your precious few event-loop threads, stalling every other request on that thread. This is the single most important rule in this chapter:

> **JPA and JDBC are blocking APIs.** There is no reactive JDBC driver hiding underneath; the socket call blocks the calling thread until data comes back. Never call them directly from WebFlux request-handling code.

### R2DBC — the reactive relational option

**R2DBC** (Reactive Relational Database Connectivity) is a genuinely non-blocking driver spec for SQL databases, with Spring Data support via `ReactiveCrudRepository`.

```properties
spring.r2dbc.url=r2dbc:postgresql://localhost:5432/shop
spring.r2dbc.username=shop_user
spring.r2dbc.password=secret
```

```java
public interface ProductRepository extends ReactiveCrudRepository<Product, Long> {
    Flux<Product> findByCategory(String category);
    Mono<Long> countByCategory(String category);
}

@Service
public class ProductService {

    private final ProductRepository repository;

    public ProductService(ProductRepository repository) {
        this.repository = repository;
    }

    public Flux<Product> findByCategory(String category) {
        return repository.findByCategory(category); // fully non-blocking, end to end
    }
}
```

### Reactive MongoDB and Redis

MongoDB and Redis both ship official reactive drivers, wired up automatically by Spring Boot starters:

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-mongodb-reactive</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-redis-reactive</artifactId>
</dependency>
```

```java
public interface OrderDocumentRepository extends ReactiveMongoRepository<OrderDocument, String> {
    Flux<OrderDocument> findByCustomerId(String customerId);
}

@Service
public class CacheService {
    private final ReactiveRedisTemplate<String, String> redisTemplate;

    public CacheService(ReactiveRedisTemplate<String, String> redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    public Mono<Boolean> cache(String key, String value) {
        return redisTemplate.opsForValue().set(key, value, Duration.ofMinutes(10));
    }
}
```

### When you're stuck with JDBC/JPA anyway

Sometimes a legacy JDBC-based repository or a JPA entity graph you can't replace is unavoidable. The fix is to run that blocking call on `boundedElastic` (the pool built for exactly this) and wrap the result back into reactive types, isolating the blocking call so it never touches an event-loop thread:

```java
@Service
public class LegacyInventoryService {

    private final JdbcInventoryRepository jdbcRepository; // old-school, blocking

    public LegacyInventoryService(JdbcInventoryRepository jdbcRepository) {
        this.jdbcRepository = jdbcRepository;
    }

    public Mono<Integer> currentStock(String sku) {
        return Mono.fromCallable(() -> jdbcRepository.findStockBySku(sku)) // blocking call
            .subscribeOn(Schedulers.boundedElastic());                     // isolated to a safe pool
    }
}
```

This is a valid escape hatch for migration periods — but if your whole app is built this way, ask whether WebFlux is actually buying you anything (see the pitfalls section below).

## Common Code Review / Interview Pitfalls

- **Blocking JDBC/JPA calls directly inside a WebFlux handler.**
  Why it's a problem: it stalls an event-loop thread; a handful of slow queries can freeze the entire server.
  ```java
  // ❌
  @GetMapping("/{id}")
  public Mono<User> get(@PathVariable Long id) {
      return Mono.just(jdbcRepository.findById(id)); // blocks the event loop while querying!
  }
  ```
  ```java
  // ✅
  @GetMapping("/{id}")
  public Mono<User> get(@PathVariable Long id) {
      return Mono.fromCallable(() -> jdbcRepository.findById(id))
          .subscribeOn(Schedulers.boundedElastic());
  }
  ```

- **Calling `Thread.sleep()` inside a reactive chain.**
  Why: same problem as above — it parks a shared thread instead of using a non-blocking delay.
  ```java
  // ❌
  .doOnNext(x -> { try { Thread.sleep(1000); } catch (Exception ignored) {} })
  ```
  ```java
  // ✅
  .delayElements(Duration.ofSeconds(1))
  ```

- **Calling `.block()` inside reactive code (e.g. inside a controller or another operator).**
  Why: it defeats the entire purpose of being reactive and can deadlock the event loop (a thread blocking, waiting for a task that needs that same thread).
  ```java
  // ❌
  @GetMapping("/{id}")
  public User get(@PathVariable Long id) {
      return userService.findById(id).block(); // blocking call inside WebFlux
  }
  ```
  ```java
  // ✅
  @GetMapping("/{id}")
  public Mono<User> get(@PathVariable Long id) {
      return userService.findById(id);
  }
  ```
  `.block()` is only acceptable at a true edge — e.g. a `main` method, a batch job, or a legacy MVC app calling `WebClient` once, never inside a reactive pipeline.

- **Forgetting to subscribe.**
  Why: "nothing happens until you subscribe" — a built chain that's never subscribed to (and never returned from a controller, which subscribes for you) silently does nothing.
  ```java
  // ❌
  public void sendWelcomeEmail(String userId) {
      emailService.send(userId); // returns Mono<Void>, nobody subscribes -- email never sent!
  }
  ```
  ```java
  // ✅
  public void sendWelcomeEmail(String userId) {
      emailService.send(userId).subscribe(); // or, better: return the Mono and let the caller subscribe/chain it
  }
  ```

- **Using `map` where `flatMap` is needed.**
  Why: `map`'s function must return a plain value; using it with a function that returns a `Mono` produces a `Mono<Mono<T>>` — a common "fix" is calling `.block()` inside `map`, reintroducing blocking.
  ```java
  // ❌
  Mono<Mono<Profile>> nested = userMono.map(u -> profileRepo.findByUser(u));
  ```
  ```java
  // ✅
  Mono<Profile> flat = userMono.flatMap(u -> profileRepo.findByUser(u));
  ```

- **Nested `subscribe()` calls.**
  Why: manually subscribing inside another operator breaks backpressure, error propagation, and makes execution order unpredictable — it's the reactive equivalent of "callback hell."
  ```java
  // ❌
  userRepo.findById(id).subscribe(user ->
      profileRepo.findByUser(user).subscribe(profile ->
          System.out.println(profile)));
  ```
  ```java
  // ✅
  userRepo.findById(id)
      .flatMap(profileRepo::findByUser)
      .subscribe(System.out::println);
  ```

- **Swallowing errors silently.**
  Why: an empty `onErrorResume` or a `subscribe()` with no error consumer hides failures — requests just seem to hang or vanish with no server-side trace.
  ```java
  // ❌
  externalService.call(id).onErrorResume(e -> Mono.empty());
  ```
  ```java
  // ✅
  externalService.call(id)
      .onErrorResume(e -> {
          log.error("External call failed for {}", id, e);
          return Mono.just(Result.fallback());
      });
  ```

- **No timeout on `WebClient` calls.**
  Why: a hung downstream service can pin resources indefinitely and cause cascading failures across your whole system.
  ```java
  // ❌
  webClient.get().uri("/slow-service").retrieve().bodyToMono(Data.class);
  ```
  ```java
  // ✅
  webClient.get().uri("/slow-service").retrieve().bodyToMono(Data.class)
      .timeout(Duration.ofSeconds(3));
  ```

- **Unbounded concurrency in `flatMap`.**
  Why: by default `flatMap` subscribes to *all* inner publishers eagerly and interleaves results — with a large source, this can open thousands of concurrent DB connections or HTTP requests at once and overwhelm downstream systems.
  ```java
  // ❌
  Flux<Order> orders = orderIds.flatMap(id -> orderService.fetch(id));
  ```
  ```java
  // ✅
  Flux<Order> orders = orderIds.flatMap(id -> orderService.fetch(id), 16); // cap concurrency at 16
  ```

- **Losing context (MDC / `SecurityContext`) across operators.**
  Why: reactive code hops between threads, so anything stored in a `ThreadLocal` (like classic SLF4J MDC or `SecurityContextHolder`) can be empty by the time a downstream operator runs.
  ```java
  // ❌
  MDC.put("traceId", traceId);
  return service.process(request); // MDC may be gone by the time this actually executes
  ```
  ```java
  // ✅
  return service.process(request)
      .contextWrite(Context.of("traceId", traceId)); // Reactor Context travels with the subscription
  ```
  Use `ReactiveSecurityContextHolder` (backed by Reactor `Context`, not `ThreadLocal`) for security info, as shown earlier.

- **Never running tests/CI with `BlockHound`.**
  Why: `BlockHound` instruments the JVM to throw an error the instant blocking code runs on a non-blocking thread — without it, accidental blocking calls (a JDBC call slipped into a `map`, a `Thread.sleep`) pass tests silently and only surface as production latency spikes under load.
  ```java
  // ✅ typical setup, e.g. in a JUnit @BeforeAll or Spring test config
  BlockHound.install();
  ```

- **Adopting WebFlux "for performance" on a plain JDBC-backed CRUD app.**
  Why: if every repository call is blocking JDBC wrapped in `boundedElastic`, you get all the complexity of reactive code (harder debugging, steeper learning curve, trickier stack traces) with none of the scalability benefit — you're still fundamentally thread-per-blocking-call underneath.
  Fix: stick with Spring MVC for JDBC/JPA-heavy CRUD services. Reach for WebFlux when you have genuinely non-blocking dependencies end-to-end (R2DBC, reactive Mongo/Redis, reactive downstream HTTP calls) or very high concurrent I/O load.

- **Returning `Flux` from a controller but the client can't actually stream it.**
  Why: if the client is a simple `fetch`/`HttpClient` call that waits for the full response body, or a proxy/load balancer buffers the whole response, you get zero streaming benefit and just add latency (nothing renders until the whole `Flux` completes).
  Fix: only use streaming response types (`text/event-stream`, `application/x-ndjson`) when the client is built to consume them incrementally (e.g. `EventSource`, a reactive HTTP client), and make sure nothing in between (proxies, gateways) buffers the whole response.

- **Blocking inside `doOnNext` (or other "side-effect only" operators).**
  Why: `doOnNext` is meant for side effects like logging or metrics — people forget it still executes *on the reactive thread* and put blocking I/O in it, same problem as blocking in `map`/`flatMap`.
  ```java
  // ❌
  flux.doOnNext(order -> auditJdbcRepository.save(order)); // blocking save on the event loop
  ```
  ```java
  // ✅
  flux.flatMap(order -> Mono.fromRunnable(() -> auditJdbcRepository.save(order))
          .subscribeOn(Schedulers.boundedElastic()));
  ```

## Quick Recap

- **Blocking vs non-blocking**: blocking ties up a thread while waiting; non-blocking frees the thread and resumes via callback when data is ready.
- **Reactive Streams**: `Publisher` / `Subscriber` / `Subscription` / `Processor` — push-based, but the subscriber controls flow via `request(n)` (backpressure).
- **MVC = thread-per-request**; **WebFlux = event loop** with a small fixed thread pool, running on Netty by default.
- **Reactor**: nothing happens until `subscribe()`; cold publishers replay per subscriber, hot publishers broadcast live; `subscribeOn` moves the source, `publishOn` moves everything downstream.
- **Schedulers**: `boundedElastic` for blocking calls, `parallel` for CPU-bound work, `single` for sequential single-thread work.
- **`Mono`** = 0 or 1 item; **`Flux`** = 0..N items.
- **`map`** = synchronous 1-to-1; **`flatMap`** = your function returns a Publisher, gets flattened and subscribed to.
- Handle errors with `onErrorResume`/`onErrorReturn`/`retryWhen`; guard slow calls with `timeout`; test with `StepVerifier`.
- **Backpressure** strategies for unruly sources: `BUFFER`, `DROP`, `LATEST`, `ERROR`; tame request rate with `limitRate`.
- **WebFlux** controllers return `Mono`/`Flux`; use `WebClient` (not `RestTemplate`) for outbound calls; stream with SSE (`text/event-stream`).
- **Functional endpoints**: `RouterFunction` + `HandlerFunction`, working with immutable `ServerRequest`/`ServerResponse`, nestable with `RouterFunctions.nest`.
- **Reactive Security**: `@EnableWebFluxSecurity` + `SecurityWebFilterChain`, `ReactiveUserDetailsService`, and `ReactiveSecurityContextHolder` instead of `ThreadLocal`-based `SecurityContextHolder`.
- **Reactive data**: R2DBC + `ReactiveCrudRepository` for SQL, reactive Mongo/Redis drivers for NoSQL — **JDBC/JPA are blocking**, always isolate them with `Mono.fromCallable(...).subscribeOn(Schedulers.boundedElastic())` if you must use them.
- Golden rule: never block the event loop, never nest `subscribe()`, always cap `flatMap` concurrency when talking to external systems, and only reach for WebFlux when you actually have non-blocking dependencies end to end.
