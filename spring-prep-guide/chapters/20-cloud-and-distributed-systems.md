# 20. Cloud & Distributed Systems

## Overview

A single "monolith" application is easy to reason about: one process, one memory space, one deploy. Split that same application into ten microservices and you inherit a whole new set of headaches: services need to *find* each other, calls over the network can fail or hang in ways a local method call never would, configuration is scattered across many deployable units, and a slow dependency can cascade into a total outage. Spring Cloud is a family of projects that gives you off-the-shelf answers to these problems: service discovery, centralized configuration, client-side load balancing, an API gateway, declarative HTTP clients, and resilience patterns like circuit breakers and rate limiters. None of this is magic — it is mostly well-known distributed-systems patterns wrapped in Spring-friendly annotations and auto-configuration. This chapter walks through each building block, shows realistic code, and ends with the pitfalls interviewers love to probe.

## Spring Cloud Overview

**Spring Cloud** is not a single library — it is an umbrella of independent projects (Spring Cloud Config, Spring Cloud Gateway, Spring Cloud OpenFeign, Spring Cloud LoadBalancer, and more) that are versioned and released together as a "release train."

Think of it like a boxed set of tools that are guaranteed to work well together, even though each tool is built by a slightly different team on its own schedule.

### What's inside

| Project | Purpose |
|---|---|
| Spring Cloud Config | Centralized, versioned configuration server |
| Spring Cloud Netflix Eureka | Service registry (client-side discovery) |
| Spring Cloud Consul / Zookeeper | Alternative service registries |
| Spring Cloud Gateway | Reactive API gateway / edge router |
| Spring Cloud OpenFeign | Declarative REST clients |
| Spring Cloud LoadBalancer | Client-side load balancing (replaces Ribbon) |
| Spring Cloud Circuit Breaker | Abstraction over Resilience4j (and others) |
| Spring Cloud Sleuth / Micrometer Tracing | Distributed tracing, correlation IDs |
| Spring Cloud Bus | Event bus to broadcast config changes |
| Spring Cloud Kubernetes | Kubernetes-native discovery & config |

### The release-train / BOM model

Spring Cloud releases are named after London Underground stations, e.g. `2023.0.x` (codename "Leyton"), `2022.0.x` ("Kilburn"). Each train pins compatible versions of every sub-project. You don't pick individual module versions — you import one BOM (Bill of Materials) and Maven/Gradle resolves consistent versions for you.

```xml
<!-- pom.xml -->
<properties>
    <spring-cloud.version>2023.0.3</spring-cloud.version>
</properties>

<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.springframework.cloud</groupId>
            <artifactId>spring-cloud-dependencies</artifactId>
            <version>${spring-cloud.version}</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>
```

| Spring Boot | Spring Cloud release train |
|---|---|
| 3.2.x / 3.3.x | 2023.0.x ("Leyton") |
| 3.0.x / 3.1.x | 2022.0.x ("Kilburn") |
| 2.7.x | 2021.0.x ("Jubilee") |

### What's dead vs alive

Netflix OSS components dominated Spring Cloud for years, but Netflix stopped actively maintaining most of them. Spring Cloud reacted by replacing them with its own implementations:

| Old (maintenance mode / removed) | Replacement |
|---|---|
| Netflix Ribbon (client-side load balancing) | Spring Cloud LoadBalancer |
| Netflix Hystrix (circuit breaker) | Resilience4j via Spring Cloud Circuit Breaker |
| Netflix Zuul (gateway) | Spring Cloud Gateway |
| Netflix Eureka | Still actively maintained and widely used, but Consul / Kubernetes-native discovery are common alternatives |

**Interview one-liner:** "Ribbon, Hystrix, and Zuul are legacy Netflix OSS projects — modern Spring Cloud apps use Spring Cloud LoadBalancer, Resilience4j, and Spring Cloud Gateway instead."

## Config Server

**Config Server** is a small Spring Boot app whose whole job is to serve configuration files to other applications over HTTP, usually backed by a Git repository. Instead of every microservice carrying its own `application.yml` with duplicated database URLs, feature flags, etc., they all ask one central server: "give me the config for `order-service`, profile `prod`."

Analogy: it's like a shared settings drawer in an office — everyone pulls the current version instead of keeping their own private, possibly stale, copy.

### Server setup

```java
@SpringBootApplication
@EnableConfigServer
public class ConfigServerApplication {
    public static void main(String[] args) {
        SpringApplication.run(ConfigServerApplication.class, args);
    }
}
```

```yaml
# config-server application.yml
server:
  port: 8888

spring:
  cloud:
    config:
      server:
        git:
          uri: https://github.com/my-org/config-repo
          default-label: main
          search-paths: '{application}'
          clone-on-start: true
```

Config files in the git repo follow a naming convention: `order-service.yml`, `order-service-prod.yml`, `application.yml` (shared defaults for everyone).

### Client side

```properties
# order-service/src/main/resources/application.properties
spring.application.name=order-service
spring.config.import=configserver:http://localhost:8888
spring.cloud.config.profile=prod
```

The client fetches its config at startup, before the rest of the Spring context is built, so config values are available for placeholder resolution like `${payment.api.url}`.

### Refreshing config without a restart

Config values are normally read once at startup. To pick up changes live, mark the bean `@RefreshScope` and trigger a refresh via the Actuator endpoint.

```java
@RestController
@RefreshScope
public class FeatureController {

    @Value("${feature.new-checkout-enabled:false}")
    private boolean newCheckoutEnabled;

    @GetMapping("/feature-status")
    public String status() {
        return "new-checkout-enabled=" + newCheckoutEnabled;
    }
}
```

```bash
# enable the endpoint
# management.endpoints.web.exposure.include=refresh

curl -X POST http://localhost:8080/actuator/refresh
```

`@RefreshScope` works by throwing away the bean and re-creating it on the next call, re-injecting fresh `@Value`/`@ConfigurationProperties` values. Spring Cloud Bus can broadcast this refresh event to *all* instances at once via a message broker (RabbitMQ/Kafka), instead of calling `/actuator/refresh` on every pod one by one.

### Encrypting sensitive properties

Config Server can decrypt values on the fly so secrets never sit in git as plain text.

```yaml
# encrypted value stored in git, prefixed with {cipher}
payment:
  api-key: '{cipher}AQBx7f3z...'
```

```bash
# ask the config server to encrypt a value (uses a symmetric or RSA key)
curl -X POST http://localhost:8888/encrypt -d "my-super-secret-value"
```

In practice, most teams outgrow git-based secret encryption quickly and move secrets to a dedicated vault (see the Distributed Configuration section).

## Service Discovery

In a fixed environment you might hard-code `http://192.168.1.10:8080` for the payment service. In the cloud, instances come and go constantly — autoscaling, rolling deploys, crashes — so IP addresses are not stable. **Service discovery** solves this: services register themselves with a **registry** when they start, and other services ask the registry "where is `payment-service` right now?" instead of hard-coding an address.

There are two flavors:

- **Client-side discovery** — the calling service asks the registry directly and picks an instance itself (load balancing happens on the client). Eureka + Spring Cloud LoadBalancer works this way.
- **Server-side discovery** — the caller talks to a fixed address (e.g., a Kubernetes `Service` or a load balancer), and that infrastructure component looks up and forwards to a healthy instance. The caller doesn't even know discovery is happening.

### Registration and heartbeat

Every registry works on the same basic loop:

1. Service starts, calls the registry's `/register` endpoint with its host, port, and metadata.
2. Service periodically sends a **heartbeat** ("I'm still alive") every few seconds.
3. If heartbeats stop, the registry marks the instance as down and eventually evicts it.
4. Clients cache the registry's list of instances locally and refresh it periodically, so they aren't hitting the registry on every single call.

## Eureka

**Eureka** is Netflix's service registry, still one of the most common choices in Spring Cloud apps.

### Eureka Server

```java
@SpringBootApplication
@EnableEurekaServer
public class EurekaServerApplication {
    public static void main(String[] args) {
        SpringApplication.run(EurekaServerApplication.class, args);
    }
}
```

```yaml
server:
  port: 8761

eureka:
  client:
    register-with-eureka: false   # the server doesn't need to register with itself
    fetch-registry: false
```

### Eureka client

```yaml
spring:
  application:
    name: order-service

eureka:
  client:
    service-url:
      defaultZone: http://localhost:8761/eureka
  instance:
    lease-renewal-interval-in-seconds: 10   # heartbeat frequency
    lease-expiration-duration-in-seconds: 30
```

```java
@Configuration
public class ClientConfig {

    @Bean
    @LoadBalanced   // resolves "http://payment-service/..." via Eureka + Spring Cloud LoadBalancer
    public RestTemplate restTemplate() {
        return new RestTemplate();
    }

    @Bean
    @LoadBalanced
    public WebClient.Builder webClientBuilder() {
        return WebClient.builder();
    }
}
```

```java
@Service
public class OrderService {

    private final RestTemplate restTemplate;

    public OrderService(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public PaymentResponse charge(PaymentRequest request) {
        // "payment-service" is a logical name, resolved to a real host:port by LoadBalancer
        return restTemplate.postForObject(
                "http://payment-service/payments", request, PaymentResponse.class);
    }
}
```

`@LoadBalanced` is the key annotation: it tells Spring to intercept calls made through this `RestTemplate`/`WebClient.Builder` and replace the logical service name with an actual instance address chosen by the load balancer.

## Consul

**HashiCorp Consul** is a service registry (and much more — key/value store, health checking, service mesh) that works across any language, not just the JVM.

```yaml
spring:
  cloud:
    consul:
      host: localhost
      port: 8500
      discovery:
        register: true
        health-check-path: /actuator/health
        health-check-interval: 10s
```

```java
@SpringBootApplication
@EnableDiscoveryClient   // works for both Eureka and Consul
public class OrderServiceApplication { }
```

Note the annotation: `@EnableDiscoveryClient` is the vendor-neutral way to opt into discovery — the actual registry (Eureka, Consul, Zookeeper) is decided by which starter dependency is on the classpath, so switching registries often means changing a dependency and some YAML, not application code.

### Comparison: Eureka vs Consul vs Kubernetes-native

| Aspect | Eureka | Consul | Kubernetes-native |
|---|---|---|---|
| Origin | Netflix OSS | HashiCorp | Kubernetes built-in |
| Health checking | Client heartbeat (self-reported) | Active checks (HTTP/TCP/script) run by Consul agent | kubelet liveness/readiness probes |
| Consistency model | AP (available, eventually consistent) | CP for its KV store, tunable for catalog | Backed by etcd (CP) |
| Extra features | Mostly just discovery | KV store, service mesh (Consul Connect), multi-datacenter | Native `Service`/`Endpoints`, DNS-based discovery |
| Language support | JVM-centric (but has a REST API) | Polyglot, first-class | Polyglot, orchestrator-native |
| Best fit | Classic Spring Cloud microservices, VM/EC2-based | Mixed-language fleets, service mesh needs | Anything already running on Kubernetes |

If you're already deploying to Kubernetes, many teams skip Eureka/Consul entirely and rely on Kubernetes `Service` objects + DNS for discovery — one less moving part to operate.

## Gateway

**Spring Cloud Gateway** sits in front of your microservices as a single entry point: it inspects incoming requests and routes them to the right backend service, and can also do cross-cutting things like authentication, rate limiting, and logging on the way through. Think of it as a reception desk that looks at what you need and sends you to the correct department, while also checking your badge and counting visitors.

Gateway is built on **Spring WebFlux and Netty**, so it is fully **reactive and non-blocking** — it can hold many concurrent in-flight requests with a small number of threads, which matters a lot for something sitting on the critical path of every call.

### Core concepts

- **Route** — a destination (URI) plus a set of predicates and filters.
- **Predicate** — a condition that decides if a request matches a route (path, header, method, etc.). Analogous to a Java `Predicate<ServerWebExchange>`.
- **Filter** — modifies the request or response as it passes through (add a header, strip a path prefix, apply a circuit breaker, rate limit, etc.).

### YAML route configuration

```yaml
spring:
  cloud:
    gateway:
      routes:
        - id: order-service-route
          uri: lb://order-service        # "lb://" = resolve via load balancer/discovery
          predicates:
            - Path=/api/orders/**
          filters:
            - StripPrefix=1
            - AddRequestHeader=X-Gateway-Source, edge

        - id: payment-service-route
          uri: lb://payment-service
          predicates:
            - Path=/api/payments/**
            - Method=GET,POST
          filters:
            - name: CircuitBreaker
              args:
                name: paymentCircuitBreaker
                fallbackUri: forward:/fallback/payments
```

### Java `RouteLocator` configuration

```java
@Configuration
public class GatewayRoutesConfig {

    @Bean
    public RouteLocator customRoutes(RouteLocatorBuilder builder) {
        return builder.routes()
                .route("inventory-service-route", r -> r
                        .path("/api/inventory/**")
                        .filters(f -> f
                                .stripPrefix(1)
                                .retry(retryConfig -> retryConfig.setRetries(2)))
                        .uri("lb://inventory-service"))
                .build();
    }
}
```

### Global filters

Route-level filters apply to one route; **global filters** apply to every request that passes through the gateway — perfect for things like adding a correlation ID or logging latency.

```java
@Component
public class CorrelationIdFilter implements GlobalFilter, Ordered {

    private static final String HEADER = "X-Correlation-Id";

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String correlationId = exchange.getRequest().getHeaders().getFirst(HEADER);
        if (correlationId == null) {
            correlationId = UUID.randomUUID().toString();
        }
        ServerWebExchange mutated = exchange.mutate()
                .request(r -> r.header(HEADER, correlationId))
                .build();
        return chain.filter(mutated);
    }

    @Override
    public int getOrder() {
        return -1; // run early
    }
}
```

## OpenFeign

Writing raw `RestTemplate`/`WebClient` calls for every remote service gets repetitive. **OpenFeign** lets you declare a Java interface, annotate it, and Spring generates the HTTP client implementation for you at runtime. You write *what* the call looks like, not *how* to make it.

```java
@FeignClient(name = "payment-service", path = "/payments")
public interface PaymentClient {

    @PostMapping
    PaymentResponse charge(@RequestBody PaymentRequest request);

    @GetMapping("/{id}")
    PaymentResponse getById(@PathVariable("id") String id);
}
```

```java
@Service
public class OrderService {

    private final PaymentClient paymentClient;

    public OrderService(PaymentClient paymentClient) {
        this.paymentClient = paymentClient;
    }

    public void placeOrder(Order order) {
        PaymentResponse response = paymentClient.charge(order.toPaymentRequest());
        // ...
    }
}
```

```java
@EnableFeignClients
@SpringBootApplication
public class OrderServiceApplication { }
```

### Configuration: timeouts and error handling

Feign clients are notorious for silently using very generous default timeouts — always override them.

```yaml
spring:
  cloud:
    openfeign:
      client:
        config:
          payment-service:
            connect-timeout: 2000
            read-timeout: 3000
            logger-level: basic
```

```java
public class PaymentErrorDecoder implements ErrorDecoder {

    @Override
    public Exception decode(String methodKey, Response response) {
        if (response.status() == 404) {
            return new PaymentNotFoundException();
        }
        if (response.status() >= 500) {
            return new RetryableException(
                    response.status(), "server error", response.request().httpMethod(),
                    (Long) null, response.request());
        }
        return new IllegalStateException("Unexpected payment error: " + response.status());
    }
}
```

### Combining with Resilience4j

Feign clients are plain method calls from the caller's point of view, so they wrap perfectly with Resilience4j annotations (covered next):

```java
@Service
public class OrderService {

    private final PaymentClient paymentClient;

    @CircuitBreaker(name = "paymentService", fallbackMethod = "chargeFallback")
    @Retry(name = "paymentService")
    public PaymentResponse charge(PaymentRequest request) {
        return paymentClient.charge(request);
    }

    private PaymentResponse chargeFallback(PaymentRequest request, Exception ex) {
        return PaymentResponse.pending();
    }
}
```

### The framework-native alternative: `@HttpExchange`

Since Spring 6 / Spring Boot 3, you don't strictly need Feign for a declarative client — Spring itself offers `@HttpExchange` interfaces backed by `HttpServiceProxyFactory`, using the same underlying `RestClient` or `WebClient` you already configure elsewhere.

```java
public interface PaymentHttpClient {

    @PostExchange("/payments")
    PaymentResponse charge(@RequestBody PaymentRequest request);

    @GetExchange("/payments/{id}")
    PaymentResponse getById(@PathVariable String id);
}
```

```java
@Configuration
public class HttpClientConfig {

    @Bean
    public PaymentHttpClient paymentHttpClient(RestClient.Builder builder) {
        RestClient restClient = builder.baseUrl("http://payment-service").build();
        HttpServiceProxyFactory factory =
                HttpServiceProxyFactory.builderFor(RestClientAdapter.create(restClient)).build();
        return factory.createClient(PaymentHttpClient.class);
    }
}
```

| | OpenFeign | `@HttpExchange` |
|---|---|---|
| Origin | Spring Cloud (Netflix-era, still maintained) | Spring Framework core (6.x) |
| Extra dependency | Yes (`spring-cloud-starter-openfeign`) | No, ships with Spring |
| Load-balanced service names (`lb://`) | Built-in with Spring Cloud LoadBalancer | Needs manual wiring |
| Interceptors / error decoders | Rich Feign-specific SPI | Uses standard `RestClient`/`WebClient` customizers |
| Best fit | Existing Spring Cloud microservice stacks | New projects wanting fewer dependencies |

## Circuit Breaker

Imagine calling a dependency that is timing out. Without protection, every incoming request blocks waiting on that slow call, threads pile up, and your own service falls over too — a **cascading failure**. A **circuit breaker** stops this by "tripping" after too many failures: further calls fail fast (or hit a fallback) without even trying the network, giving the failing dependency time to recover. It behaves just like an electrical circuit breaker protecting a house from overload.

### The state machine

```
        failure rate exceeds
        threshold in the
        sliding window
   ┌────────────────────────────┐
   │                            ▼
┌───────┐                  ┌────────┐
│CLOSED │                  │ OPEN   │
│(calls │                  │(calls  │
│ pass  │                  │ fail   │
│through│                  │ fast / │
│normally)                 │fallback)
└───┬───┘                  └───┬────┘
    │                          │ wait-duration-in-open-state elapses
    │ success rate healthy     ▼
    │                    ┌───────────┐
    └────────────────────│HALF_OPEN  │
     enough trial calls  │(let a few │
     succeed              trial calls│
                          │ through)  │
                          └─────┬─────┘
                                │ trial calls fail
                                ▼
                             back to OPEN
```

- **CLOSED** — normal operation, calls go through, failures are counted.
- **OPEN** — too many failures; calls short-circuit immediately (no network call at all) for a configured wait duration.
- **HALF_OPEN** — after the wait, a limited number of trial calls are let through. If they succeed, go back to CLOSED. If they fail, go back to OPEN.

### `@CircuitBreaker` with a fallback

```java
@Service
public class InventoryClient {

    private final RestTemplate restTemplate;

    @CircuitBreaker(name = "inventoryService", fallbackMethod = "getStockFallback")
    public int getStockLevel(String sku) {
        return restTemplate.getForObject(
                "http://inventory-service/stock/" + sku, Integer.class);
    }

    private int getStockFallback(String sku, Exception ex) {
        // return a conservative default instead of blowing up the whole request
        return 0;
    }
}
```

### Configuration

```yaml
resilience4j:
  circuitbreaker:
    instances:
      inventoryService:
        failure-rate-threshold: 50          # % of failed calls that trips the breaker
        sliding-window-size: 20             # count-based window of the last N calls
        sliding-window-type: COUNT_BASED
        wait-duration-in-open-state: 10s    # how long to stay OPEN before trying again
        permitted-number-of-calls-in-half-open-state: 5
        minimum-number-of-calls: 10         # don't evaluate failure rate on tiny samples
        automatic-transition-from-open-to-half-open-enabled: true
```

### Bulkhead

A **bulkhead** limits how many concurrent calls can be in flight to a given dependency, so one slow dependency can't exhaust the thread pool (or connection pool) that other, healthy dependencies also rely on. The name comes from ship design: bulkheads are watertight walls that stop one flooded compartment from sinking the whole ship.

```yaml
resilience4j:
  bulkhead:
    instances:
      inventoryService:
        max-concurrent-calls: 25
        max-wait-duration: 0
  thread-pool-bulkhead:
    instances:
      inventoryService:
        max-thread-pool-size: 10
        core-thread-pool-size: 5
        queue-capacity: 20
```

### Time limiter

Used with reactive/async calls to enforce a hard timeout on top of the circuit breaker.

```yaml
resilience4j:
  timelimiter:
    instances:
      inventoryService:
        timeout-duration: 2s
```

### Decorator order matters

Resilience4j lets you stack multiple annotations on one method. They are applied in a specific, fixed order (outermost to innermost):

```
Retry ( CircuitBreaker ( RateLimiter ( TimeLimiter ( Bulkhead ( actual call ) ) ) ) )
```

In practice, that means: a **Retry** wraps everything (so it can retry the whole protected call), the **CircuitBreaker** sees each attempt (including retries) and can trip based on them, and the **Bulkhead**/**TimeLimiter** are closest to the real call, controlling concurrency and duration of the actual network operation.

- Getting the order wrong is a classic mistake: e.g., if `Retry` were innermost, it would keep retrying *inside* a single circuit breaker call and never let the breaker see individual failures correctly.
- With annotations, Spring Cloud Circuit Breaker / Resilience4j applies a sensible default order automatically; when composing decorators manually with `Decorators.ofSupplier(...)`, you control (and must get right) the nesting order yourself.

## Retry

**Retry** simply means: if a call fails, try it again before giving up. It sounds trivial but is one of the easiest patterns to get badly wrong.

```java
@Service
public class InventoryClient {

    @Retry(name = "inventoryService", fallbackMethod = "getStockFallback")
    public int getStockLevel(String sku) {
        return restTemplate.getForObject(
                "http://inventory-service/stock/" + sku, Integer.class);
    }

    private int getStockFallback(String sku, Exception ex) {
        return 0;
    }
}
```

```yaml
resilience4j:
  retry:
    instances:
      inventoryService:
        max-attempts: 3
        wait-duration: 200ms
        enable-exponential-backoff: true
        exponential-backoff-multiplier: 2
        enable-randomized-wait: true        # adds jitter
        retry-exceptions:
          - java.io.IOException
          - org.springframework.web.client.ResourceAccessException
        ignore-exceptions:
          - com.example.BusinessValidationException
```

### Exponential backoff + jitter

Retrying immediately after a failure is a bad idea if the dependency is overloaded — you just add to the load that caused the failure. **Exponential backoff** waits progressively longer between attempts (200ms, 400ms, 800ms...). **Jitter** adds randomness to that wait so that many clients retrying at once don't all hit the dependency at exactly the same moment in near-perfect unison.

### Which failures are safe to retry? Idempotency matters

Only retry an operation if repeating it produces the same end result as running it once — i.e., the operation is **idempotent**.

| Safe to retry | Usually unsafe to retry |
|---|---|
| `GET` (read-only) | `POST /payments/charge` without an idempotency key |
| `PUT` with a full resource replace | Any operation with a side effect that isn't naturally idempotent (e.g., "increment balance by X") |
| Network-level failures (connection reset, timeout) before the server processed anything | Ambiguous failures — did the server actually apply the change before the timeout? |
| Calls using an idempotency key so the server can dedupe retries | 5xx from a handler with side effects and no dedupe key |

A common real-world safety net: pass a client-generated **idempotency key** with write operations so the server can recognize "I already processed this exact request" and return the original result instead of double-charging a customer.

### Retry storms

If many callers retry a failing dependency simultaneously (especially without backoff/jitter), the retries themselves become a denial-of-service attack on an already struggling service — a **retry storm**. This is why retry is almost always paired with a circuit breaker: once the breaker opens, retries stop hitting the network entirely, giving the dependency room to recover.

## Rate Limiting

**Rate limiting** caps how many requests are allowed in a given time window, protecting a service (or an expensive downstream dependency) from being overwhelmed — by a spike in traffic, a buggy client stuck in a loop, or an abusive user.

### Token bucket vs leaky bucket

| | Token bucket | Leaky bucket |
|---|---|---|
| Idea | A bucket holds tokens that refill at a fixed rate; each request consumes one token | Requests enter a queue/bucket and "leak out" (are processed) at a fixed rate |
| Handles bursts? | Yes — bursts are allowed up to the bucket capacity | No — smooths output to a strictly constant rate |
| Common use | API rate limiting (Resilience4j `RateLimiter`, Gateway `RequestRateLimiter`) | Traffic shaping, smoothing outbound calls |

Most Spring tooling (Resilience4j, Gateway's Redis limiter) uses a token-bucket-style algorithm because it tolerates short bursts gracefully, which matches how real clients behave.

### Gateway `RequestRateLimiter` (Redis-backed)

Because a gateway typically runs multiple instances, rate limit counters need to live somewhere shared — Redis is the standard backing store.

```yaml
spring:
  cloud:
    gateway:
      routes:
        - id: order-service-route
          uri: lb://order-service
          predicates:
            - Path=/api/orders/**
          filters:
            - name: RequestRateLimiter
              args:
                redis-rate-limiter.replenishRate: 10   # tokens added per second
                redis-rate-limiter.burstCapacity: 20    # bucket size (max burst)
                redis-rate-limiter.requestedTokens: 1
```

```java
@Bean
public KeyResolver userKeyResolver() {
    // rate limit per API key/user rather than globally
    return exchange -> Mono.just(
            exchange.getRequest().getHeaders().getFirst("X-Api-Key"));
}
```

### Resilience4j `RateLimiter`

Useful for limiting calls from within a service, e.g. to a third-party API with a strict quota.

```java
@Service
public class GeocodingClient {

    @RateLimiter(name = "geocodingApi")
    public Coordinates geocode(String address) {
        return externalGeocodingApi.lookup(address);
    }
}
```

```yaml
resilience4j:
  ratelimiter:
    instances:
      geocodingApi:
        limit-for-period: 50
        limit-refresh-period: 1s
        timeout-duration: 500ms
```

## Distributed Configuration

Once you have many services, many environments (dev/staging/prod), and many config sources (git, environment variables, Kubernetes secrets, command-line args), you need clear rules for which value wins and how to keep secrets safe.

### Configuration precedence (Spring Boot, roughly highest to lowest priority)

1. Command-line arguments
2. `SPRING_APPLICATION_JSON` / environment variables
3. Config Server (remote, fetched via `spring.config.import=configserver:`)
4. Profile-specific properties files (`application-prod.yml`)
5. Base `application.yml` packaged in the jar
6. `@PropertySource` annotations
7. Default values (e.g., `@Value("${x:default}")`)

The rule of thumb: **more specific / more externally-controlled beats more generic / more baked-in**, so ops can override a bad default at deploy time without a rebuild.

### Secrets: don't put them in the config git repo

Even encrypted, secrets in a config git repo are a liability (encryption keys leak, history is hard to purge, access control is coarse). The standard alternative is a dedicated secrets manager like **HashiCorp Vault**.

```yaml
spring:
  cloud:
    vault:
      uri: https://vault.internal:8200
      authentication: KUBERNETES
      kv:
        enabled: true
        backend: secret
        default-context: order-service
```

```properties
# reference like a normal property; Spring Cloud Vault resolves it at startup
spring.datasource.password=${vault.secret.db-password}
```

### Config drift

**Config drift** happens when the config that's actually running in an environment silently diverges from what's in source control — someone tweaked a value directly in a running instance, or an old deploy never picked up a later change. Over time nobody is sure what's really live. Mitigations:

- Treat config as code: only change it through the git repo / Config Server, never by SSHing into a box.
- Bake config into immutable deploys (containers) rather than mutating running instances.
- Use tools that diff "desired" vs "actual" config and alert on mismatches.

### Per-environment overrides

```
config-repo/
  application.yml            # shared defaults for all services, all envs
  order-service.yml          # defaults for order-service, all envs
  order-service-staging.yml  # overrides for order-service in staging
  order-service-prod.yml     # overrides for order-service in prod
```

Spring Cloud Config merges these from least to most specific, so `order-service-prod.yml` wins over `order-service.yml`, which wins over `application.yml` — the same "specific beats generic" principle as local Spring profiles, just centralized.

## Common Code Review / Interview Pitfalls

- **No timeouts on remote calls** — a `RestTemplate`/`WebClient`/Feign client with default (often unbounded or very long) timeouts can hang a thread indefinitely when a dependency stalls.
- **Retrying non-idempotent operations** — retrying a `POST /charge` without an idempotency key can double-charge a customer.
- **Retry without backoff/jitter** — fixed, immediate retries from many clients synchronize into a retry storm that worsens the outage they're reacting to.
- **Circuit breaker with no fallback** — the call still throws an exception when open; you've added complexity without actually protecting the caller.
- **A fallback that hides real errors** — silently returning `0`/empty/`"OK"` on failure can mask a genuine outage from users and monitoring, turning a loud failure into a confusing, silent one.
- **Fallbacks that call another remote service** — if the fallback itself makes a network call, it can fail (or add load) exactly when the system is already struggling; fallbacks should be fast and local (cache, default value, degrade gracefully).
- **Sharing a thread pool between healthy and unhealthy dependencies** — without a bulkhead, one slow dependency exhausts the shared pool and starves calls to completely healthy services.
- **Secrets committed to the config git repo** — even "encrypted" secrets in git are risky; use Vault or a cloud secrets manager instead.
- **`@RefreshScope` on everything** — it adds a proxy and recreation overhead to every bean; apply it only to beans that actually need live config reload.
- **Chatty service-to-service calls** — many small synchronous calls (an "N+1" problem across the network) multiply latency and failure surface; batch calls or rethink service boundaries.
- **Attempting distributed transactions with `@Transactional`** — `@Transactional` only manages a transaction within one JVM/database connection; it cannot atomically commit or roll back across multiple services. Use sagas, outbox patterns, or idempotent compensating actions instead.
- **No correlation ID across services** — without a shared request/trace ID propagated through headers, debugging a failure that spans five services becomes guesswork.
- **Hard-coded service URLs** — bypasses discovery/load balancing entirely and breaks the moment an instance moves or scales.
- **Ignoring the CAP theorem / eventual consistency reality** — expecting strongly consistent reads across services that communicate asynchronously (e.g., via events) leads to bugs when code assumes data is immediately visible everywhere.
- **Treating Eureka/Consul as always strongly consistent** — both are AP-leaning systems; a stale registry entry briefly pointing at a dead instance is normal, not a bug, so clients need to tolerate occasional failed calls and retry against another instance.
- **Not load-testing the fallback path** — teams tune the "happy path" for performance but never verify the fallback/circuit-open path behaves acceptably under real production load.

## Quick Recap

- Spring Cloud is a release-trained bundle of independent projects (Config, Gateway, OpenFeign, LoadBalancer, Circuit Breaker...); import one BOM to get compatible versions.
- Ribbon, Hystrix, and Zuul are dead; use Spring Cloud LoadBalancer, Resilience4j, and Spring Cloud Gateway instead.
- Config Server centralizes configuration (usually git-backed); clients pull it via `spring.config.import=configserver:`; `@RefreshScope` + `/actuator/refresh` reloads config live; encrypt secrets or, better, use Vault.
- Service discovery lets services find each other by logical name instead of hard-coded IPs, via registration + heartbeat; Eureka and Consul are common registries, Kubernetes offers discovery natively.
- `@LoadBalanced` on `RestTemplate`/`WebClient` resolves a logical service name to a real instance chosen by the load balancer.
- Spring Cloud Gateway is a reactive (Netty/WebFlux) edge router: routes match on predicates and apply filters; global filters run on every request.
- OpenFeign turns an annotated interface into an HTTP client; `@HttpExchange` + `HttpServiceProxyFactory` is the newer, dependency-free Spring-native alternative.
- Circuit breakers move through CLOSED → OPEN → HALF_OPEN based on failure rate, protecting callers from cascading failures; always pair with a sane fallback.
- Resilience4j decorator order (outer to inner): Retry → CircuitBreaker → RateLimiter → TimeLimiter → Bulkhead.
- Only retry idempotent operations; always use exponential backoff with jitter to avoid retry storms.
- Rate limiting (token bucket, typically) protects services from traffic spikes; Gateway's Redis-backed limiter works across instances, Resilience4j's works within one JVM.
- Config precedence runs from command-line/env vars (highest) down to packaged defaults (lowest); keep secrets out of git and watch for config drift between "desired" and "actual" state.
