# 13. Spring Boot Testing

## Overview

Testing is how you prove your code works before a user (or a production outage) proves it doesn't. Spring Boot gives you a whole toolbox for this: plain JUnit for fast unit tests, "slice" annotations that boot only a piece of the application, `@SpringBootTest` for full end-to-end checks, and Testcontainers for testing against real databases and message brokers instead of fakes. The right strategy is not "test everything with `@SpringBootTest`" — that's slow and brittle. The right strategy is a mix of test types, most of them fast and narrow, a few of them slow and broad.

A simple mental model is the **test pyramid**, in plain English:

- **Bottom (widest, most numerous): unit tests.** Test one class in isolation, no Spring context, no database, no network. Milliseconds each. You should have hundreds of these.
- **Middle: slice / integration tests.** Test a few collaborating classes together — a controller with a real MVC dispatcher, a repository with a real database. Seconds each. You should have dozens of these.
- **Top (narrowest, fewest): end-to-end / full-context tests.** Boot the whole application, maybe with a real browser or real HTTP client, hitting real infrastructure via Testcontainers. Tens of seconds to minutes each. You should have a handful of these, covering your critical user journeys.

The shape matters: many cheap tests at the bottom catch most bugs cheaply and fast; a few expensive tests at the top catch the "does it actually all wire together" class of bugs that unit tests can't see. If your test suite is shaped like an upside-down pyramid (mostly slow full-context tests), your build gets slow, flaky, and people start skipping tests — which is exactly what you want to avoid.

## Unit Testing

A **unit test** tests one unit of code — usually one class or one method — completely in isolation. No Spring container, no database, no network calls. Any collaborator (another class this one depends on) is replaced with a **test double**, typically a mock, so you're only testing the logic of the class itself.

Unit tests are:

- Fast (milliseconds), because there's no framework startup.
- Deterministic, because there's no shared state or external system.
- Precise, because a failure points at exactly one class.

A typical unit test uses plain JUnit 5 plus AssertJ for readable assertions, and Mockito for fakes (covered in more detail later in this chapter).

```java
class OrderPriceCalculatorTest {

    private final DiscountService discountService = mock(DiscountService.class);
    private final OrderPriceCalculator calculator = new OrderPriceCalculator(discountService);

    @Test
    void appliesDiscountToOrderTotal() {
        given(discountService.discountFor("GOLD")).willReturn(BigDecimal.valueOf(0.10));

        BigDecimal total = calculator.calculate(
                new Order("GOLD", BigDecimal.valueOf(100)));

        assertThat(total).isEqualByComparingTo("90.00");
    }

    @Test
    void returnsFullPriceWhenNoDiscountTier() {
        given(discountService.discountFor("NONE")).willReturn(BigDecimal.ZERO);

        BigDecimal total = calculator.calculate(
                new Order("NONE", BigDecimal.valueOf(50)));

        assertThat(total).isEqualByComparingTo("50.00");
    }
}
```

Notes:

- No `@SpringBootTest`, no `@Autowired`, no application context — just `new OrderPriceCalculator(...)`.
- `given(...).willReturn(...)` is Mockito's BDD-style syntax; it reads like a specification.
- AssertJ's `assertThat(...)` gives fluent, readable assertions and much better failure messages than plain JUnit `assertEquals`.

## Integration Testing

An **integration test** checks that two or more real components work together correctly — for example, a service class talking to a real (or realistically simulated) database, or a controller talking to a real Spring MVC dispatcher. Unlike a unit test, it usually needs a Spring application context, because the whole point is to test the wiring, not just one class's logic.

Integration tests catch bugs that unit tests structurally cannot see:

- Wrong SQL or JPQL that only fails against a real database.
- Missing `@Transactional` boundaries.
- Serialization bugs (a field that doesn't map to JSON the way you expect).
- Misconfigured beans (wrong qualifier, missing `@Bean`, circular dependency).

They are slower than unit tests because they start (some of) the Spring container, so you write fewer of them and aim them at the seams between components rather than at every branch of business logic.

```java
@SpringBootTest
class OrderServiceIntegrationTest {

    @Autowired
    private OrderService orderService;

    @Autowired
    private OrderRepository orderRepository;

    @Test
    void placingOrderPersistsItAndReturnsGeneratedId() {
        Order saved = orderService.placeOrder(new PlaceOrderRequest("SKU-1", 3));

        assertThat(saved.getId()).isNotNull();
        assertThat(orderRepository.findById(saved.getId())).isPresent();
    }
}
```

Rule of thumb: use a **unit test** when you're testing logic inside one class; use an **integration test** (often a slice test, see next section) when you're testing that components collaborate correctly.

## Slice Tests

A **slice test** loads only the part ("slice") of the Spring context relevant to one architectural layer, instead of the whole application. For example, `@WebMvcTest` loads the web layer (controllers, filters, JSON converters) but not repositories or services — you mock those. `@DataJpaTest` loads only JPA infrastructure (entity manager, repositories) but not controllers.

Why slice tests exist: `@SpringBootTest` boots *everything* — every `@Component`, every auto-configuration, every bean you have. That's slow, and it means a controller test can fail because of an unrelated repository bug. Slice tests boot a minimal, targeted subset, so they start faster and fail for the right reason.

Here is the full picture of the main slice-test annotations:

| Annotation | Loads | Does NOT load | Typical use |
|---|---|---|---|
| `@WebMvcTest(SomeController.class)` | The specified `@Controller`/`@RestController`, `@ControllerAdvice`, filters, converters, `MockMvc` | `@Service`, `@Repository`, full auto-configuration | Test a controller's request mapping, validation, JSON output |
| `@DataJpaTest` | `@Entity` classes, Spring Data JPA repositories, an embedded/test `DataSource`, `TestEntityManager`; wraps each test in a rollback transaction | `@Controller`, `@Service`, most other beans | Test repository queries and JPA mappings |
| `@JsonTest` | Jackson/Gson `ObjectMapper`, `@JsonComponent` beans | Everything else | Test custom (de)serializers, `JacksonTester` assertions |
| `@WebFluxTest` | Reactive `@Controller`/`@RestController`, `WebTestClient` | `@Service`, `@Repository` | Test a WebFlux controller in isolation |
| `@DataMongoTest` | Embedded/test Mongo template and repositories | Web layer, unrelated beans | Test MongoDB repositories |
| `@DataRedisTest` | Redis template and repositories | Web layer, unrelated beans | Test Redis repositories |
| `@RestClientTest(SomeClient.class)` | `RestTemplateBuilder`/`RestClient.Builder`, `MockRestServiceServer` | Web/server layer | Test an outbound REST client |
| `@JdbcTest` | `DataSource`, `JdbcTemplate`; rollback transaction | JPA repositories, web layer | Test raw JDBC/`JdbcTemplate` code |

Common traits of every slice annotation:

- They're all meta-annotated with `@BootstrapWith` and typically disable full auto-configuration, enabling only the relevant `@AutoConfigure...` set.
- You almost always need `@MockBean` (or the newer `@MockitoBean`) to supply fake versions of the beans the slice doesn't create but your class under test still needs.
- They still create a (cached) Spring `ApplicationContext`, just a smaller one — so they're faster than `@SpringBootTest` but not as fast as a pure unit test.

## @SpringBootTest

`@SpringBootTest` boots the **entire** Spring application context — same as running the real application (minus, optionally, the embedded web server). It's the closest you get to "just run the app" inside a test, which makes it powerful but also the slowest and heaviest option.

It has a `webEnvironment` attribute controlling how (or whether) a web server is started:

| `webEnvironment` value | Behavior |
|---|---|
| `MOCK` (default) | Loads a web `ApplicationContext` with a mock servlet environment — no real port is opened. Use with `MockMvc`. |
| `RANDOM_PORT` | Starts a real embedded server (Tomcat/Netty) on a free random port. Use with `TestRestTemplate` or `WebTestClient` for real HTTP calls. |
| `DEFINED_PORT` | Starts a real embedded server on the port from configuration (e.g. `server.port` in `application.yml`), defaulting to 8080. |
| `NONE` | No web environment at all — for non-web contexts. |

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class OrderControllerFullStackTest {

    @Autowired
    private TestRestTemplate restTemplate;

    @Test
    void createOrderReturns201WithLocationHeader() {
        PlaceOrderRequest request = new PlaceOrderRequest("SKU-1", 2);

        ResponseEntity<OrderResponse> response =
                restTemplate.postForEntity("/orders", request, OrderResponse.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
        assertThat(response.getHeaders().getLocation()).isNotNull();
        assertThat(response.getBody().id()).isNotNull();
    }
}
```

`TestRestTemplate` is a test-friendly wrapper around `RestTemplate` that Spring Boot auto-configures when you use `RANDOM_PORT` or `DEFINED_PORT`. It knows the base URL already (`/orders` resolves against the running test server), and it doesn't throw exceptions on 4xx/5xx responses by default, so you can assert on the status code directly instead of catching exceptions.

### Context caching — and why `@DirtiesContext` is expensive

Starting a Spring context is slow (hundreds of milliseconds to seconds). Spring's test framework **caches** contexts across test classes: if two test classes ask for the exact same configuration (same classes, same profiles, same properties, same mocks), Spring reuses the already-built context instead of rebuilding it.

`@DirtiesContext` tells Spring "this test polluted the context, throw it away after this test/class." That forces a rebuild for the *next* test that would otherwise have reused it — potentially adding seconds to your build for every test class that follows.

```java
@SpringBootTest
class CacheMutatingTest {

    @Autowired
    private CacheManager cacheManager;

    @Test
    @DirtiesContext // we mutate shared cache state; force a fresh context afterwards
    void evictsAllCachesWorks() {
        cacheManager.getCacheNames().forEach(name -> cacheManager.getCache(name).clear());
        // ...
    }
}
```

Guidelines:

- Only add `@DirtiesContext` when a test truly leaves the context in a bad state (e.g. it shuts down a bean, mutates a static/singleton cache, changes a `@MockBean`'s stubbing in a way other tests would inherit).
- Prefer designing tests so they don't need it — reset state in `@AfterEach` instead of nuking the whole context.
- Overusing `@DirtiesContext` is one of the most common causes of a test suite that "used to be fast" becoming slow over months, because every context rebuild is paid by whichever test class runs next.

## @WebMvcTest

`@WebMvcTest` is a slice test for the Spring MVC layer only. It auto-configures `MockMvc`, JSON converters, `@ControllerAdvice` exception handlers, and validation — but **not** `@Service`, `@Repository`, or `@Component` beans (except a few web-specific ones like filters and converters). You must supply mocks for anything the controller needs.

```java
@WebMvcTest(OrderController.class)
class OrderControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private OrderService orderService; // controller's dependency, faked

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void getOrderReturns200WithJsonBody() throws Exception {
        given(orderService.findById(1L))
                .willReturn(new OrderResponse(1L, "SKU-1", 2, "PLACED"));

        mockMvc.perform(get("/orders/{id}", 1L))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(1))
                .andExpect(jsonPath("$.status").value("PLACED"));
    }

    @Test
    void getUnknownOrderReturns404() throws Exception {
        given(orderService.findById(99L))
                .willThrow(new OrderNotFoundException(99L));

        mockMvc.perform(get("/orders/{id}", 99L))
                .andExpect(status().isNotFound());
    }

    @Test
    void createOrderValidatesRequestBody() throws Exception {
        PlaceOrderRequest invalid = new PlaceOrderRequest("", -1); // fails @NotBlank / @Positive

        mockMvc.perform(post("/orders")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(invalid)))
                .andExpect(status().isBadRequest());
    }
}
```

Key points:

- You narrow the slice to one controller with `@WebMvcTest(OrderController.class)`; omit the argument to load all `@Controller` beans in the package.
- The real `@ExceptionHandler` methods run, so you can verify your error-handling actually maps exceptions to status codes.
- Bean validation (`@Valid`, `@NotBlank`, etc.) runs for real, because the full web layer, including argument resolvers, is loaded.

## @DataJpaTest

`@DataJpaTest` is a slice test for the JPA/persistence layer. It configures an embedded (or test-scoped) `DataSource`, Spring Data JPA repositories, Hibernate, and a `TestEntityManager` — but not controllers or services. Each test method runs inside a transaction that's **rolled back** at the end, so tests don't leak data into each other by default.

```java
@DataJpaTest
class OrderRepositoryTest {

    @Autowired
    private TestEntityManager entityManager;

    @Autowired
    private OrderRepository orderRepository;

    @Test
    void findsOrdersByStatus() {
        entityManager.persist(new Order("SKU-1", 2, OrderStatus.PLACED));
        entityManager.persist(new Order("SKU-2", 1, OrderStatus.SHIPPED));
        entityManager.flush();

        List<Order> placed = orderRepository.findByStatus(OrderStatus.PLACED);

        assertThat(placed).hasSize(1)
                .extracting(Order::getSku)
                .containsExactly("SKU-1");
    }
}
```

Things to know:

- By default `@DataJpaTest` replaces your configured `DataSource` with an embedded, in-memory one (H2, if it's on the classpath) unless you tell it not to.
- To test against the *real* production database engine instead of an in-memory fake, disable that replacement and point it at a Testcontainers database (see the Testcontainers section):

```java
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import(TestcontainersConfiguration.class) // provides a real Postgres @Bean via @ServiceConnection
class OrderRepositoryPostgresTest {
    // ...
}
```

- `TestEntityManager` is a test-friendly wrapper around JPA's `EntityManager` with helper methods (`persist`, `find`, `flush`) that are convenient for arranging test data directly, bypassing your repository's own methods.

## @MockBean

`@MockBean` tells Spring's test context to replace a real bean in the `ApplicationContext` with a Mockito mock. It's used heavily inside slice tests (`@WebMvcTest`, `@DataJpaTest`, etc.) and full `@SpringBootTest`s to stub out a dependency you don't want to exercise for real — a service, a repository, an external client.

```java
@WebMvcTest(PaymentController.class)
class PaymentControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private PaymentGatewayClient paymentGatewayClient; // real one would call a network API

    @Test
    void chargeSucceedsWhenGatewayApproves() throws Exception {
        given(paymentGatewayClient.charge(any())).willReturn(new ChargeResult("APPROVED"));

        mockMvc.perform(post("/payments/charge")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"amount\":100}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("APPROVED"));
    }
}
```

### Deprecation: use `@MockitoBean` / `@MockitoSpyBean` in Spring Boot 3.4+

As of **Spring Boot 3.4**, `@MockBean` and `@SpyBean` (from `org.springframework.boot.test.mock.mockito`) are **deprecated**. They still work, and you'll see them everywhere in existing codebases and interview questions, but new code should use the replacements from Spring Framework's own test support:

| Deprecated (Spring Boot) | Replacement (Spring Framework 6.2 / Boot 3.4+) |
|---|---|
| `@MockBean` | `@org.springframework.test.context.bean.override.mockito.MockitoBean` |
| `@SpyBean` | `@org.springframework.test.context.bean.override.mockito.MockitoSpyBean` |

```java
@WebMvcTest(PaymentController.class)
class PaymentControllerModernTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean // modern replacement for @MockBean
    private PaymentGatewayClient paymentGatewayClient;

    @Test
    void chargeSucceedsWhenGatewayApproves() throws Exception {
        given(paymentGatewayClient.charge(any())).willReturn(new ChargeResult("APPROVED"));

        mockMvc.perform(post("/payments/charge")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"amount\":100}"))
                .andExpect(status().isOk());
    }
}
```

Why the change happened: `@MockBean`/`@SpyBean` were Spring Boot–specific and only worked with the `SpringBootTestContextBootstrapper`. The new `@MockitoBean`/`@MockitoSpyBean` are part of Spring Framework's general-purpose "bean override" mechanism, so they work uniformly across plain Spring tests and Spring Boot tests, and follow a consistent contract for other kinds of test bean overrides Spring may add later. Functionally, for everyday use, they behave the same way: replace a bean in the context with a Mockito mock/spy for the duration of the test.

- `@MockBean` / `@MockitoBean` → replaces the bean entirely with a mock (all methods return defaults unless stubbed).
- `@SpyBean` / `@MockitoSpyBean` → wraps the *real* bean, so real methods run unless you explicitly stub them with `willReturn`/`doReturn`.

## Mockito

Mockito is the most widely used mocking framework for Java. A **mock** is a fake object that records how it was called and returns pre-programmed answers instead of running real logic. Mocks let you test a class's logic without dragging in its real collaborators (databases, network clients, other slow or complex objects).

Enable Mockito in plain JUnit 5 tests with the extension:

```java
@ExtendWith(MockitoExtension.class)
class InvoiceServiceTest {

    @Mock
    private TaxCalculator taxCalculator;

    @InjectMocks
    private InvoiceService invoiceService; // Mockito injects the @Mock fields via constructor/setter

    @Captor
    private ArgumentCaptor<Invoice> invoiceCaptor;

    @Test
    void appliesTaxToInvoiceTotal() {
        given(taxCalculator.rateFor("NL")).willReturn(BigDecimal.valueOf(0.21));

        Invoice invoice = invoiceService.createInvoice("NL", BigDecimal.valueOf(100));

        assertThat(invoice.getTotal()).isEqualByComparingTo("121.00");
    }

    @Test
    void publishesInvoiceCreatedEvent(@Mock ApplicationEventPublisher publisher) {
        InvoiceService service = new InvoiceService(taxCalculator, publisher);

        service.createInvoice("NL", BigDecimal.valueOf(50));

        verify(publisher).publishEvent(invoiceCaptor.capture());
        assertThat(invoiceCaptor.getValue().getCountry()).isEqualTo("NL");
    }
}
```

Core vocabulary:

| Term | Meaning |
|---|---|
| `mock(X.class)` / `@Mock` | Create a fake `X` with no real behavior. |
| `spy(realObject)` / `@Spy` | Wrap a *real* object; calls run for real unless stubbed. |
| `given(x.foo()).willReturn(y)` | BDD-style stubbing: "given foo() is called, return y." |
| `when(x.foo()).thenReturn(y)` | Classic-style stubbing, same effect as `given/willReturn`. |
| `verify(x).foo()` | Assert that `foo()` was actually called on the mock. |
| `verify(x, never()).foo()` | Assert `foo()` was never called. |
| `ArgumentCaptor` | Captures the actual argument passed to a mock call, so you can assert on it. |
| `@InjectMocks` | Mockito creates the object under test and injects `@Mock`/`@Spy` fields into it. |

Common pitfall: Mockito mocks return `null` for unstubbed methods that return objects, `0`/`false` for primitives, and empty collections for collection return types — they never "guess" real behavior. If you forgot to stub something and get a confusing `NullPointerException`, that's usually why.

## Testcontainers

Embedded/in-memory databases are fast but not exactly what runs in production. **Testcontainers** is a Java library that spins up real Docker containers — a real PostgreSQL, real Kafka, real Redis — for the duration of a test, then tears them down. Your tests run against the *actual* technology you deploy, not an approximation, catching bugs that H2-vs-Postgres SQL dialect differences would otherwise hide until production.

### Classic pattern: `@Container` + `@DynamicPropertySource`

```java
@SpringBootTest
@Testcontainers
class OrderRepositoryTestcontainersTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine");

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @Autowired
    private OrderRepository orderRepository;

    @Test
    void savesAndReadsOrderFromRealPostgres() {
        Order saved = orderRepository.save(new Order("SKU-1", 2, OrderStatus.PLACED));

        assertThat(orderRepository.findById(saved.getId())).isPresent();
    }
}
```

- `@Testcontainers` (from the JUnit 5 Testcontainers extension) manages the container lifecycle — starting it before tests, stopping it after.
- `@Container` marks the field as a container to manage; `static` means one container shared across all test methods in the class (much faster than starting one per test).
- `@DynamicPropertySource` lets you push properties (the container's randomly-assigned host/port) into the Spring `Environment` *before* the context starts — you can't know the port until the container is actually running, so this can't be a static `application.yml` value.

### Modern pattern: `@ServiceConnection` (Spring Boot 3.1+)

Spring Boot 3.1 added `@ServiceConnection`, which removes the need to manually wire properties. Spring Boot detects the container type and configures the matching connection details (datasource URL, username, password, driver) automatically.

```java
@SpringBootTest
@Testcontainers
class OrderRepositoryServiceConnectionTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine");

    @Autowired
    private OrderRepository orderRepository;

    @Test
    void savesAndReadsOrderFromRealPostgres() {
        Order saved = orderRepository.save(new Order("SKU-1", 2, OrderStatus.PLACED));

        assertThat(orderRepository.findById(saved.getId())).isPresent();
    }
}
```

A common trick is to centralize container setup in a shared `@TestConfiguration` and `@Import` it, so every test class that needs Postgres reuses the same bean definition (and, thanks to context caching, potentially the same running container):

```java
@TestConfiguration(proxyBeanMethods = false)
class TestcontainersConfiguration {

    @Bean
    @ServiceConnection
    PostgreSQLContainer<?> postgresContainer() {
        return new PostgreSQLContainer<>("postgres:16-alpine");
    }
}
```

```java
@SpringBootTest
@Import(TestcontainersConfiguration.class)
class OrderServiceIntegrationTest {
    // real Postgres wired in automatically, no manual property registration
}
```

| Pattern | Boilerplate | Property wiring |
|---|---|---|
| `@Container` + `@DynamicPropertySource` | More — you name every property manually | Manual, explicit, works with anything |
| `@Container` + `@ServiceConnection` | Less — Spring Boot infers the properties | Automatic, only for supported container types |

## Embedded Databases

An **embedded database** runs in the same JVM process as your test — no separate server, no Docker, often no persistence to disk. H2, HSQLDB, and Derby are the common Java embedded databases. Spring Boot auto-configures one automatically for `@DataJpaTest` (and similar slices) if it's on the classpath and you haven't disabled that behavior.

```gradle
testImplementation("com.h2database:h2")
```

```java
@DataJpaTest // uses an embedded H2 DataSource automatically
class ProductRepositoryTest {

    @Autowired
    private ProductRepository productRepository;

    @Test
    void savesProduct() {
        Product saved = productRepository.save(new Product("Widget", BigDecimal.TEN));

        assertThat(saved.getId()).isNotNull();
    }
}
```

Trade-offs:

| | Embedded DB (H2) | Testcontainers (real Postgres) |
|---|---|---|
| Startup speed | Very fast (in-process) | Slower (Docker container start) |
| SQL dialect fidelity | Approximate — H2 is *not* Postgres/MySQL | Exact — same engine as production |
| Infrastructure needed | None | Docker (or a compatible runtime) |
| Catches DB-specific bugs (JSON columns, sequences, locking, dialect functions) | No | Yes |
| Good for | Fast repository smoke tests, CI without Docker | Realistic integration tests before release |

Interview-relevant point: "we test against H2 but deploy on Postgres" is a well-known trap — see the pitfalls section. Many teams now default straight to Testcontainers because Docker is nearly universal in CI, and the fidelity gain is worth the extra seconds.

## MockMvc

`MockMvc` lets you test Spring MVC controllers by sending simulated HTTP requests *without* starting a real HTTP server. It runs your actual `DispatcherServlet` handling logic — mapping, filters, converters, validation, exception handling — in-process, which is much faster than firing real HTTP requests, while still testing "real" request handling instead of just calling the controller method directly.

### Standalone setup vs `@AutoConfigureMockMvc`

| Approach | What it wires up | When to use |
|---|---|---|
| `MockMvcBuilders.standaloneSetup(controller)` | Only the controller instance you pass in, manually | Pure unit-style test of one controller, no Spring context at all |
| `@WebMvcTest` + `@Autowired MockMvc` | Full web layer slice (converters, `@ControllerAdvice`, validation) | Most controller tests — the default choice |
| `@SpringBootTest` + `@AutoConfigureMockMvc` | Entire application context, but `MockMvc` instead of a real server | Full-stack test where you still want in-process speed |

```java
// Standalone: no Spring context, fastest, least realistic
class OrderControllerStandaloneTest {

    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        OrderService orderService = mock(OrderService.class);
        given(orderService.findById(1L)).willReturn(new OrderResponse(1L, "SKU-1", 2, "PLACED"));

        mockMvc = MockMvcBuilders
                .standaloneSetup(new OrderController(orderService))
                .build();
    }

    @Test
    void getOrderReturnsJson() throws Exception {
        mockMvc.perform(get("/orders/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sku").value("SKU-1"));
    }
}
```

```java
// Full context, real server config, but in-process MockMvc instead of sockets
@SpringBootTest
@AutoConfigureMockMvc
class OrderControllerFullContextMockMvcTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private OrderService orderService;

    @Test
    void getOrderReturnsJson() throws Exception {
        given(orderService.findById(1L)).willReturn(new OrderResponse(1L, "SKU-1", 2, "PLACED"));

        mockMvc.perform(get("/orders/1").accept(MediaType.APPLICATION_JSON))
                .andExpect(status().isOk())
                .andExpect(content().contentType(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.sku").value("SKU-1"))
                .andExpect(jsonPath("$.quantity").value(2));
    }
}
```

Anatomy of a `MockMvc` call:

- `perform(...)` — sends a simulated request; builders like `get("/orders/1")`, `post("/orders")`, `put(...)`, `delete(...)` describe the HTTP method and path.
- `.andExpect(...)` — asserts something about the response: `status().isOk()`, `header().string(...)`, `content().json(...)`.
- `jsonPath("$.field")` — pulls a value out of the JSON response body by JSONPath expression, so you don't have to manually deserialize it.
- `.andDo(print())` — dumps the full request/response for debugging when a test fails mysteriously.

## WebTestClient

`WebTestClient` is Spring's HTTP test client built for **WebFlux** (Spring's reactive stack), but it also works perfectly well against a classic Spring MVC application. It's non-blocking under the hood and gives you a fluent, chainable API for asserting on responses — including reactive `Flux`/`Mono` bodies.

### For WebFlux

```java
@WebFluxTest(ProductController.class)
class ProductControllerWebFluxTest {

    @Autowired
    private WebTestClient webTestClient;

    @MockitoBean
    private ProductService productService;

    @Test
    void streamsAllProducts() {
        given(productService.findAll()).willReturn(Flux.just(
                new Product("Widget", BigDecimal.TEN),
                new Product("Gadget", BigDecimal.valueOf(20))));

        webTestClient.get().uri("/products")
                .accept(MediaType.APPLICATION_NDJSON)
                .exchange()
                .expectStatus().isOk()
                .expectBodyList(Product.class)
                .hasSize(2);
    }

    @Test
    void createsProduct() {
        Product toCreate = new Product("Widget", BigDecimal.TEN);
        given(productService.create(any())).willReturn(Mono.just(toCreate));

        webTestClient.post().uri("/products")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(toCreate)
                .exchange()
                .expectStatus().isCreated()
                .expectBody()
                .jsonPath("$.name").isEqualTo("Widget");
    }
}
```

### For Spring MVC (bound to `MockMvc`, still no real server)

`WebTestClient` can wrap `MockMvc` internally, so you get its fluent assertion style even in a normal servlet-based MVC app:

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class OrderControllerWebTestClientTest {

    @Autowired
    private WebTestClient webTestClient; // Spring Boot auto-configures this for RANDOM_PORT tests

    @Test
    void createOrderReturns201() {
        webTestClient.post().uri("/orders")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(new PlaceOrderRequest("SKU-1", 2))
                .exchange()
                .expectStatus().isCreated()
                .expectHeader().exists(HttpHeaders.LOCATION)
                .expectBody()
                .jsonPath("$.id").isNumber();
    }
}
```

| Client | Blocking? | Best for |
|---|---|---|
| `MockMvc` | Blocking, in-process (no sockets) | Servlet-based Spring MVC apps |
| `TestRestTemplate` | Blocking, real HTTP over a real port | Full-stack MVC tests needing a real socket |
| `WebTestClient` | Non-blocking-capable, works in-process or over a real port | WebFlux apps always; MVC apps if you like its fluent API |

## Common Code Review / Interview Pitfalls

- **Using `@SpringBootTest` for everything.** It boots the *entire* application context for every test class, which is slow and makes CI crawl as the suite grows.
  - ❌ `@SpringBootTest class OrderControllerTest { ... }` just to test one controller's JSON mapping.
  - ✅ `@WebMvcTest(OrderController.class)` — loads only the web slice, seconds faster per run.

- **Tests that depend on execution order.** If `testCreatesUser()` must run before `testDeletesUser()` to pass, your tests aren't actually independent, and JUnit does not guarantee method order by default.
  - ❌ Test B reads data inserted by test A in the same class.
  - ✅ Each test sets up its own data (`@BeforeEach`) and cleans up after itself, so any test can run alone or in any order.

- **Shared mutable state between tests with no cleanup.** A static field, a shared cache, or a container-wide database with leftover rows from a previous test causes flaky, order-dependent failures.
  - ✅ Reset state in `@AfterEach`/`@BeforeEach`, use `@Transactional` rollback (see next point) or `@DirtiesContext` sparingly, or give each test unique data (unique IDs/emails) instead of fixed literals.

- **`@Transactional` on tests hiding real flush errors.** Wrapping a test in `@Transactional` (common with `@DataJpaTest`, or manually added to `@SpringBootTest`) rolls back changes at the end — convenient for cleanup — but Hibernate may batch SQL and only actually *flush* (send it to the database) at commit time, or when you call `flush()`. A rolled-back transaction can hide a constraint violation or a broken mapping that would blow up in production.
  - ❌ Test passes because the invalid SQL never actually executes before rollback.
  - ✅ Call `entityManager.flush()` (or `repository.flush()`) explicitly inside the test to force the SQL to run and surface real database errors.

- **Over-mocking.** Mocking every single collaborator, including simple value objects or the class's own internal helper methods, produces tests that verify "did I call the mock the way I wrote the mock to expect" rather than real behavior — the test breaks on every refactor and proves almost nothing.
  - ✅ Mock true external boundaries (network clients, databases in unit tests); let simple, deterministic collaborators run for real.

- **Asserting on log output.** Tests that scrape logger output (e.g. capturing `System.out` or a log appender) to verify behavior are brittle — a log message wording change breaks the test even though behavior didn't change, and it's an indirect way to test something you should assert directly.
  - ❌ `assertThat(logOutput).contains("Order placed")`.
  - ✅ Assert on the actual return value, persisted state, or a published event instead.

- **`Thread.sleep()` in tests to "wait for async stuff."** Sleeping a fixed amount of time is both slow (you always wait the full duration) and flaky (sometimes it's not long enough on a loaded CI machine).
  - ❌ `Thread.sleep(2000); assertThat(result).isDone();`
  - ✅ Use `Awaitility` (`await().atMost(2, SECONDS).until(...)`) or synchronize explicitly (e.g. block on a `CompletableFuture`, or use a `CountDownLatch`).

- **Testing against H2 in tests but Postgres/MySQL in production.** SQL dialects differ — window functions, JSON columns, sequence behavior, locking semantics, case sensitivity. A query that works on H2 can fail (or silently do the wrong thing) on the real engine.
  - ✅ Use Testcontainers to run integration tests against the exact same database engine and version used in production, at least for the tests that hit non-trivial SQL.

- **Random ports vs fixed ports, chosen carelessly.** A fixed port (`DEFINED_PORT`) causes test failures when two test JVMs run in parallel (CI agents, forked test workers) and fight over the same port.
  - ❌ `@SpringBootTest(webEnvironment = WebEnvironment.DEFINED_PORT)` with `server.port=8080` in CI running tests in parallel.
  - ✅ `RANDOM_PORT` (the common choice) so each test JVM gets its own free port automatically; only pin a port if you have a specific reason (e.g. a hard-coded external stub expecting it).

- **Not testing error paths.** Suites that only ever hit the "happy path" (order created successfully, user found) miss exactly the branches most likely to have bugs: validation failures, not-found cases, timeouts, concurrent conflicts.
  - ✅ For every success test, ask "what's the corresponding failure scenario?" and write it too — e.g. `getUnknownOrderReturns404()` alongside `getOrderReturns200()`.

- **Mocking the class under test.** If you mock the very class you're supposed to be testing (or mock so many of its internals that nothing real executes), the test can pass no matter what the real implementation does.
  - ❌ `OrderService service = mock(OrderService.class);` inside `OrderServiceTest` — you're testing Mockito, not your code.
  - ✅ Instantiate the real class under test; mock only its *collaborators*.

- **Forgetting `@AutoConfigureMockMvc` or the right slice annotation, then wondering why `MockMvc` is `null`.** `MockMvc` is only auto-configured by specific annotations (`@WebMvcTest`, or `@SpringBootTest` + `@AutoConfigureMockMvc`); a bare `@SpringBootTest` alone won't inject it.
  - ✅ Add `@AutoConfigureMockMvc` explicitly when you want `MockMvc` inside a full `@SpringBootTest`.

- **Using deprecated `@MockBean`/`@SpyBean` without knowing it, then being surprised in interviews or code review.** They still compile and run fine on Spring Boot 3.4+, but they're deprecated, and reviewers (and interviewers) increasingly expect you to know the replacement.
  - ✅ Prefer `@MockitoBean`/`@MockitoSpyBean` in new code on Spring Boot 3.4+; recognize `@MockBean`/`@SpyBean` when reading older code.

- **Ignoring context caching costs when writing test configuration.** Every unique combination of `@ActiveProfiles`, `@TestPropertySource`, `@MockBean` set, and configuration classes creates a *new* cached context. Slightly different setups across test classes multiply the number of contexts Spring has to build, slowing the whole suite even without any single test being "wrong."
  - ✅ Reuse shared base test classes or `@Import`ed test configuration so more test classes hit the same cached context.

## Quick Recap

- **Test pyramid**: many fast unit tests at the bottom, some slice/integration tests in the middle, few slow full-context/end-to-end tests at the top.
- **Unit test**: one class, no Spring context, collaborators mocked. Fast and precise.
- **Integration test**: multiple real components together, usually needs a Spring context.
- **Slice tests** boot only one layer: `@WebMvcTest` (web layer), `@DataJpaTest` (persistence layer), `@JsonTest` (JSON mapping), `@WebFluxTest` (reactive web layer), `@RestClientTest` (outbound REST clients), `@JdbcTest` (raw JDBC).
- **`@SpringBootTest`** boots the whole app; `webEnvironment` controls whether/how a web server starts (`MOCK`, `RANDOM_PORT`, `DEFINED_PORT`, `NONE`).
- **`TestRestTemplate`** makes real HTTP calls against a running test server (needs `RANDOM_PORT`/`DEFINED_PORT`).
- **Context caching** reuses Spring contexts across test classes with identical configuration; `@DirtiesContext` forces a rebuild — expensive, use sparingly.
- **`@MockBean`/`@SpyBean`** replace/wrap beans with Mockito mocks/spies inside a Spring test context; **deprecated since Spring Boot 3.4** in favor of **`@MockitoBean`/`@MockitoSpyBean`**.
- **Mockito**: `mock()`/`@Mock` for fakes, `spy()`/`@Spy` for partial fakes, `given/willReturn` or `when/thenReturn` for stubbing, `verify()` for call assertions, `ArgumentCaptor` to inspect arguments.
- **Testcontainers** runs real Docker containers (Postgres, Kafka, etc.) for tests. Classic wiring: `@Container` + `@DynamicPropertySource`. Modern (Boot 3.1+): `@Container` + `@ServiceConnection` — no manual property wiring.
- **Embedded databases** (H2, HSQLDB) are fast but not dialect-accurate; fine for quick repository smoke tests, risky as your only DB test strategy before release.
- **`MockMvc`**: in-process simulated HTTP for Spring MVC. Standalone setup (`standaloneSetup`) for a bare controller; `@WebMvcTest` or `@SpringBootTest` + `@AutoConfigureMockMvc` for a wired-up slice/full context. `perform()`/`andExpect()`/`jsonPath()` is the core API.
- **`WebTestClient`**: fluent, non-blocking-capable HTTP test client, standard for WebFlux (`@WebFluxTest`), also usable against MVC apps (auto-configured with `RANDOM_PORT`, or wrapping `MockMvc`).
- Keep the pyramid shape: mock true boundaries, test real logic, use Testcontainers for the database-fidelity-critical tests, and reserve `@SpringBootTest` for the handful of tests that truly need the whole application wired up.
