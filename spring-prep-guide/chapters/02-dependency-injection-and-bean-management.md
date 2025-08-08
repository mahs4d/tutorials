# 2. Dependency Injection & Bean Management

## Overview

Dependency Injection (DI) is the core idea that makes Spring "Spring." Instead of a class creating the objects it depends on, those objects (called **dependencies**) are handed to it from the outside. Spring's **IoC container** (Inversion of Control container) is the engine that creates objects, wires their dependencies together, and manages their lifecycle. The objects it manages are called **beans**. Understanding how beans are declared, discovered, injected, and disambiguated is one of the most heavily tested areas in Spring interviews, because almost every real bug in a Spring app (circular dependency errors, `NoUniqueBeanDefinitionException`, mysterious `null` fields) traces back to a misunderstanding of these mechanics. This chapter walks through how beans are defined, how they get injected, and how to manage bean scope, ordering, and conditional creation.

## @Component, @Service, @Repository, @Controller

Spring needs to know which classes should become beans. The simplest way is to put an annotation on the class and let **component scanning** find it. `@Component` is the generic, general-purpose annotation. `@Service`, `@Repository`, and `@Controller` are all **specializations** of `@Component` — they do the same basic job (register the class as a bean) but also carry extra meaning for readability and, in some cases, extra behavior.

| Annotation | Layer | Extra behavior |
|---|---|---|
| `@Component` | Generic | None — plain bean registration |
| `@Service` | Business/service layer | None extra, purely semantic |
| `@Repository` | Data access layer | Enables automatic translation of persistence exceptions into Spring's `DataAccessException` hierarchy |
| `@Controller` | Web layer (MVC) | Marks class as a request handler; combine with `@ResponseBody` or use `@RestController` for REST APIs |

```java
@Component
public class EmailFormatter {
    public String format(String email) {
        return email.trim().toLowerCase();
    }
}

@Repository
public interface UserRepository extends JpaRepository<User, Long> {
    Optional<User> findByEmail(String email);
}

@Service
public class UserService {
    private final UserRepository userRepository;

    public UserService(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    public User register(String email) {
        return userRepository.save(new User(email));
    }
}

@RestController
@RequestMapping("/api/users")
public class UserController {
    private final UserService userService;

    public UserController(UserService userService) {
        this.userService = userService;
    }

    @PostMapping
    public User create(@RequestBody String email) {
        return userService.register(email);
    }
}
```

Key points:

- All four annotations are found by `@ComponentScan` (enabled automatically by `@SpringBootApplication`).
- They only mark **your own classes**. Classes from third-party libraries need `@Bean` methods instead (see below).
- Using the right specialization (`@Service` vs `@Component`) is mostly about communicating intent to other developers — it costs nothing and makes the codebase self-documenting.

## @Configuration and @Bean

Sometimes you cannot (or should not) annotate a class directly — for example, a class from a third-party library, or an object that needs custom construction logic. For this, you write a **configuration class** annotated with `@Configuration`, and inside it, methods annotated with `@Bean`. Each `@Bean` method's return value is registered as a bean, and the method name becomes the default bean name.

```java
@Configuration
public class AppConfig {

    @Bean
    public RestTemplate restTemplate(RestTemplateBuilder builder) {
        return builder
                .setConnectTimeout(Duration.ofSeconds(2))
                .setReadTimeout(Duration.ofSeconds(5))
                .build();
    }

    @Bean
    public ObjectMapper objectMapper() {
        ObjectMapper mapper = new ObjectMapper();
        mapper.registerModule(new JavaTimeModule());
        return mapper;
    }
}
```

- `@Configuration` classes are themselves beans, and Spring proxies them (by default, `proxyBeanMethods = true`) so that calling one `@Bean` method from another still returns the *same singleton instance* rather than a new object.
- Use `@Bean` for: third-party classes, objects needing conditional or complex construction, or when you want configuration and wiring logic centralized in one place.
- Use `@Component` for: your own classes where the class itself can just be annotated.

```java
@Configuration
public class ClientConfig {

    @Bean
    public HttpClient httpClient() {
        return HttpClient.newHttpClient();
    }

    @Bean
    public ApiClient apiClient(HttpClient httpClient) {
        // reuses the exact same httpClient singleton
        return new ApiClient(httpClient);
    }
}
```

## @Autowired

`@Autowired` tells Spring "please inject a bean here." Spring looks at the required type, finds a matching bean in the container, and wires it in. It can be placed on constructors, fields, or setter methods.

```java
@Service
public class OrderService {

    private final PaymentGateway paymentGateway;

    @Autowired // optional on a single constructor since Spring 4.3+
    public OrderService(PaymentGateway paymentGateway) {
        this.paymentGateway = paymentGateway;
    }
}
```

Key points:

- If a class has **only one constructor**, `@Autowired` is optional — Spring uses it automatically.
- If there are **multiple constructors**, you must mark exactly one with `@Autowired` (or use `@Autowired(required = false)` on more than one, though this is rare and confusing).
- By default, `@Autowired` requires a matching bean to exist; set `required = false` to allow it to be `null` if nothing matches (rare, usually a sign of a design smell).
- Since Java doesn't allow `@Autowired` in records directly as constructor injection, use a plain class or Lombok `@RequiredArgsConstructor` for concise constructor injection instead.

## Constructor Injection

Constructor injection passes dependencies through the class constructor. This is the **recommended** style in modern Spring.

```java
@Service
public class InvoiceService {

    private final InvoiceRepository invoiceRepository;
    private final TaxCalculator taxCalculator;

    public InvoiceService(InvoiceRepository invoiceRepository, TaxCalculator taxCalculator) {
        this.invoiceRepository = invoiceRepository;
        this.taxCalculator = taxCalculator;
    }
}
```

Why it's preferred:

- Dependencies can be made `final`, so they're **immutable** after construction — no accidental reassignment.
- The object is never in a half-initialized state; if a dependency is missing, the app fails fast at startup, not with a `NullPointerException` at runtime.
- It makes unit testing trivial — just call `new InvoiceService(mockRepo, mockCalculator)`, no Spring container or reflection needed.
- It makes circular dependencies **visible immediately** at startup instead of silently working around them.

## Field Injection

Field injection uses `@Autowired` directly on a field, skipping constructors and setters entirely.

```java
@Service
public class ReportService {

    @Autowired
    private ReportRepository reportRepository; // works, but discouraged
}
```

- Looks convenient (less boilerplate) but is broadly considered an **anti-pattern** in production code.
- The field can't be `final`, so it's mutable.
- You cannot construct the object without Spring's reflection-based injection — plain `new ReportService()` leaves the field `null`, making unit testing harder (you'd need `ReflectionTestUtils` or a full Spring context).
- Hides true dependencies — a class can silently accumulate ten `@Autowired` fields and no one notices it has become a "god class."
- Spring's own documentation and most static analysis tools (e.g., SonarQube, IntelliJ inspections) flag field injection as a warning.

## Setter Injection

Setter injection uses a public setter method annotated with `@Autowired`, and the dependency is injected after the object is constructed.

```java
@Service
public class NotificationService {

    private EmailClient emailClient;

    @Autowired
    public void setEmailClient(EmailClient emailClient) {
        this.emailClient = emailClient;
    }
}
```

- Useful mainly for **optional dependencies** that can be reconfigured or reassigned after construction.
- Object can exist temporarily without the dependency set, which risks `NullPointerException` if used too early.
- Less common today — constructor injection has taken over as the default recommendation for required dependencies.

### Injection Styles Compared

| Style | Immutable? | Fails fast at startup? | Easy to unit test? | Recommended? |
|---|---|---|---|---|
| Constructor | Yes (`final`) | Yes | Yes (plain `new`) | Yes — default choice |
| Field | No | No (fails on first use) | Hard (needs reflection) | No — avoid |
| Setter | No | No | Medium | Only for optional deps |

## @Primary

When two or more beans implement the same interface, Spring can't guess which one you want and throws a `NoUniqueBeanDefinitionException`. `@Primary` marks one bean as the **default choice** when there's ambiguity.

```java
public interface PaymentGateway {
    void charge(BigDecimal amount);
}

@Component
@Primary
public class StripeGateway implements PaymentGateway {
    public void charge(BigDecimal amount) { /* ... */ }
}

@Component
public class PaypalGateway implements PaymentGateway {
    public void charge(BigDecimal amount) { /* ... */ }
}

@Service
public class CheckoutService {
    private final PaymentGateway paymentGateway; // injects StripeGateway automatically

    public CheckoutService(PaymentGateway paymentGateway) {
        this.paymentGateway = paymentGateway;
    }
}
```

- `@Primary` is a "soft default" — it can still be overridden at an individual injection point with `@Qualifier`.
- Only put `@Primary` on **one** candidate; if two beans both claim `@Primary`, you're back to an ambiguity error.

## @Qualifier

`@Qualifier` lets you pick a *specific* bean by name when multiple candidates exist, instead of relying on a single `@Primary` default.

```java
public interface NotificationSender {
    void send(String message);
}

@Component("smsSender")
public class SmsNotificationSender implements NotificationSender {
    public void send(String message) { /* ... */ }
}

@Component("emailSender")
public class EmailNotificationSender implements NotificationSender {
    public void send(String message) { /* ... */ }
}

@Service
public class AlertService {

    private final NotificationSender sender;

    public AlertService(@Qualifier("emailSender") NotificationSender sender) {
        this.sender = sender;
    }
}
```

- `@Qualifier("beanName")` matches against the bean's name (default is the class name with a lowercase first letter, or whatever you gave `@Component("...")`).
- You can also define **custom qualifier annotations** for a more type-safe alternative to string matching:

```java
@Qualifier
@Retention(RetentionPolicy.RUNTIME)
public @interface Sms { }

@Sms
@Component
public class SmsNotificationSender implements NotificationSender { /* ... */ }

// injection point
public AlertService(@Sms NotificationSender sender) { ... }
```

- `@Qualifier` at an injection point always wins over `@Primary` on a bean.

## Lazy Initialization

By default, Spring creates all **singleton** beans eagerly at application startup. `@Lazy` defers creation of a bean until it's first actually needed (first injected/requested).

```java
@Component
@Lazy
public class ExpensiveReportGenerator {

    public ExpensiveReportGenerator() {
        System.out.println("Expensive setup happening...");
    }
}
```

You can also apply `@Lazy` at the injection point instead of the bean declaration:

```java
@Service
public class AdminService {

    private final ExpensiveReportGenerator reportGenerator;

    public AdminService(@Lazy ExpensiveReportGenerator reportGenerator) {
        this.reportGenerator = reportGenerator; // wrapped in a proxy, real object created on first use
    }
}
```

Global lazy initialization can also be turned on for the whole application:

```yaml
spring:
  main:
    lazy-initialization: true
```

Trade-offs:

- **Pros:** faster startup time, useful for rarely-used or heavyweight beans.
- **Cons:** errors that would normally surface at startup (e.g., a missing dependency) now surface later, at runtime, which can be confusing in production. Also adds a small proxy overhead.
- Good default advice: keep initialization eager unless you have a measured reason (startup time, expensive resource) to make something lazy.

## Bean Profiles

**Profiles** let you activate different sets of beans depending on the environment (e.g., `dev`, `test`, `prod`). A bean annotated with `@Profile` is only registered if that profile is active.

```java
@Configuration
public class DataSourceConfig {

    @Bean
    @Profile("dev")
    public DataSource devDataSource() {
        return new EmbeddedDatabaseBuilder()
                .setType(EmbeddedDatabaseType.H2)
                .build();
    }

    @Bean
    @Profile("prod")
    public DataSource prodDataSource() {
        HikariDataSource ds = new HikariDataSource();
        ds.setJdbcUrl("jdbc:postgresql://prod-db:5432/app");
        return ds;
    }
}
```

Activating a profile:

```properties
# application.properties
spring.profiles.active=dev
```

```bash
# or via command line / environment variable
java -jar app.jar --spring.profiles.active=prod
```

- You can combine profiles: `spring.profiles.active=prod,metrics`.
- `@Profile("!dev")` activates a bean when the `dev` profile is **not** active.
- `@Profile` can be placed on `@Component` classes too, not just `@Bean` methods.
- Profile-specific property files are supported automatically: `application-dev.properties`, `application-prod.properties`.

## Conditional Beans

`@Conditional` (and its friendly Spring Boot variants) lets a bean be registered only if some condition holds — used heavily inside Spring Boot's **auto-configuration** classes.

```java
@Configuration
public class CacheConfig {

    @Bean
    @ConditionalOnProperty(name = "app.cache.enabled", havingValue = "true")
    public CacheManager cacheManager() {
        return new ConcurrentMapCacheManager();
    }

    @Bean
    @ConditionalOnMissingBean(CacheManager.class)
    public CacheManager noOpCacheManager() {
        return new NoOpCacheManager();
    }

    @Bean
    @ConditionalOnClass(name = "com.fasterxml.jackson.databind.ObjectMapper")
    public JsonSerializer jsonSerializer() {
        return new JsonSerializer();
    }
}
```

Common built-in conditional annotations:

| Annotation | Registers the bean when... |
|---|---|
| `@ConditionalOnProperty` | A given property equals a given value (or simply exists) |
| `@ConditionalOnMissingBean` | No bean of that type is already registered |
| `@ConditionalOnBean` | A specific bean already exists in the context |
| `@ConditionalOnClass` | A specific class is present on the classpath |
| `@ConditionalOnMissingClass` | A specific class is absent from the classpath |
| `@ConditionalOnWebApplication` | The app is a web application |

You can also write a fully custom condition by implementing `Condition`:

```java
public class OnLinuxCondition implements Condition {
    @Override
    public boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata) {
        return System.getProperty("os.name").toLowerCase().contains("linux");
    }
}

@Bean
@Conditional(OnLinuxCondition.class)
public FileWatcher fileWatcher() {
    return new LinuxFileWatcher();
}
```

- This is exactly the mechanism Spring Boot's `spring-boot-autoconfigure` module uses to decide, for example, whether to configure an embedded Tomcat, a `DataSource`, or a `RestTemplate` — all "auto-magically" based on what's on your classpath and in your properties.

## Circular Dependencies

A **circular dependency** happens when Bean A needs Bean B, and Bean B needs Bean A (directly, or through a longer chain A → B → C → A).

```java
@Service
public class ServiceA {
    private final ServiceB serviceB;
    public ServiceA(ServiceB serviceB) { this.serviceB = serviceB; }
}

@Service
public class ServiceB {
    private final ServiceA serviceA;
    public ServiceB(ServiceA serviceA) { this.serviceA = serviceA; }
}
```

With **constructor injection**, this throws a startup error:

```
BeanCurrentlyInCreationException: Error creating bean with name 'serviceA':
Requested bean is currently in creation: Is there an unresolvable circular reference?
```

This is a *good* thing — it forces you to notice the design problem immediately, rather than discovering it in production.

With **field or setter injection**, Spring can sometimes resolve the cycle silently by injecting a partially-initialized bean reference, which is why some teams historically leaned on field injection to "avoid" circular dependency errors — but this just hides a real design flaw.

How to actually fix a circular dependency:

1. **Refactor** — extract the shared logic both classes need into a third class (`ServiceC`) that both `ServiceA` and `ServiceB` depend on, removing the cycle entirely. This is almost always the right fix.
2. **Use `@Lazy` on one side** — defers creation of the dependency until first use, breaking the startup-time cycle:

```java
@Service
public class ServiceA {
    private final ServiceB serviceB;

    public ServiceA(@Lazy ServiceB serviceB) {
        this.serviceB = serviceB; // injected as a lazy proxy
    }
}
```

3. **Use setter injection for one side** — lets both beans get fully constructed first, then wired together (an older workaround, less preferred than refactoring).

- Since Spring Boot 2.6, circular references are **disallowed by default**, even for field/setter injection, unless you explicitly opt back in:

```properties
spring.main.allow-circular-references=true
```

- Interview tip: the "correct" answer to "how do you fix a circular dependency" is almost always **redesign the classes**, not just slap `@Lazy` on it.

## Bean Post Processors

A `BeanPostProcessor` is a hook that lets you run custom logic **before and after** every bean's initialization — a way to intercept bean creation across the whole container. It's the mechanism that powers many Spring features under the hood, such as `@Autowired` resolution and AOP proxy creation.

```java
@Component
public class LoggingBeanPostProcessor implements BeanPostProcessor {

    @Override
    public Object postProcessBeforeInitialization(Object bean, String beanName) {
        System.out.println("Before init: " + beanName);
        return bean;
    }

    @Override
    public Object postProcessAfterInitialization(Object bean, String beanName) {
        System.out.println("After init: " + beanName);
        return bean; // could also return a wrapped/proxy object here
    }
}
```

- `postProcessBeforeInitialization` runs before `@PostConstruct`/`InitializingBean.afterPropertiesSet()`.
- `postProcessAfterInitialization` runs after — this is where Spring AOP creates proxies for `@Transactional`, `@Async`, `@Cacheable`, etc. When you see a bean wrapped in a `$$SpringCGLIB$$` proxy class, a `BeanPostProcessor` did that.
- A related but distinct interface, `BeanFactoryPostProcessor`, runs even earlier — it operates on bean **definitions** (metadata) before any beans are actually instantiated. Useful for things like registering additional property sources or modifying bean definitions programmatically.

```java
@Component
public class CustomBeanFactoryPostProcessor implements BeanFactoryPostProcessor {
    @Override
    public void postProcessBeanFactory(ConfigurableListableBeanFactory beanFactory) {
        BeanDefinition def = beanFactory.getBeanDefinition("someBean");
        def.setScope(BeanDefinition.SCOPE_SINGLETON);
    }
}
```

| Interface | Operates on | Timing |
|---|---|---|
| `BeanFactoryPostProcessor` | Bean *definitions* (metadata) | Before any beans are instantiated |
| `BeanPostProcessor` | Bean *instances* (actual objects) | Around each bean's initialization |

- Most application developers never need to write a custom `BeanPostProcessor` — but knowing it exists explains *why* `@Transactional` methods only work through a proxy, and *why* calling an `@Transactional` method from another method in the same class doesn't trigger transaction behavior (the call bypasses the proxy).

## Common Code Review / Interview Pitfalls

- **Field injection everywhere** — makes classes impossible to unit test without a Spring context and hides true dependencies. Fix: switch to constructor injection with `final` fields.
  ```java
  // ❌ bad
  @Autowired
  private UserRepository userRepository;

  // ✅ good
  private final UserRepository userRepository;
  public UserService(UserRepository userRepository) {
      this.userRepository = userRepository;
  }
  ```
- **Too many constructor parameters** — a constructor with eight dependencies is a sign the class is doing too much. Fix: split the class by responsibility (Single Responsibility Principle).
- **Multiple `@Primary` beans for the same type** — reintroduces the exact ambiguity `@Primary` was meant to solve. Fix: keep exactly one `@Primary` bean per type, or use `@Qualifier` everywhere instead.
- **Relying on field/setter injection to "solve" circular dependencies** — it papers over a real design flaw instead of fixing it. Fix: refactor to remove the cycle, or extract shared logic into a third class.
- **Forgetting `@Repository` doesn't add query logic** — some engineers think `@Repository` magically generates SQL; it only enables exception translation. The actual query derivation comes from Spring Data JPA's method-name parsing or `@Query`.
- **Catching startup failures by disabling fail-fast behavior** — e.g., setting `spring.main.allow-circular-references=true` just to make a startup error go away, without fixing the underlying design. Fix: treat that flag as a temporary escape hatch, not a permanent solution.
- **Using `@Autowired(required = false)` and forgetting the null check** — leads to `NullPointerException` deep in business logic instead of a clear startup error. Fix: prefer `Optional<T>` as the injected type, or make the dependency required.
  ```java
  // ❌ bad
  @Autowired(required = false)
  private MetricsClient metricsClient;
  ...
  metricsClient.record(...); // NPE if absent

  // ✅ good
  private final Optional<MetricsClient> metricsClient;
  public MyService(Optional<MetricsClient> metricsClient) {
      this.metricsClient = metricsClient;
  }
  ...
  metricsClient.ifPresent(m -> m.record(...));
  ```
- **Overusing `@Lazy` as a performance hack without measuring** — adds proxy overhead and defers startup errors to runtime, which can be worse for debugging production incidents. Fix: only apply `@Lazy` to beans that are demonstrably expensive and rarely used.
- **Hardcoding environment-specific beans without profiles** — e.g., an in-memory `DataSource` accidentally shipped to production because there was no `@Profile` guard. Fix: use `@Profile` (or `@ConditionalOnProperty`) and verify active profiles per environment.
- **Confusing `@Component`-family annotations with behavior differences that don't exist** — `@Service` has zero functional difference from `@Component` at runtime. Fix: use them for readability, don't assume hidden magic.
- **Writing a custom `BeanPostProcessor` that swallows exceptions silently** — a `postProcessAfterInitialization` that catches and ignores errors can hide real startup problems for unrelated beans. Fix: log and rethrow, or scope the processor narrowly with `@ConditionalOnBean`/checks.
- **Calling an `@Transactional` (or `@Async`/`@Cacheable`) method from within the same class** — since these features rely on a `BeanPostProcessor`-created proxy, an internal (`this.foo()`) call bypasses the proxy and the annotation is silently ignored. Fix: call through a separate bean, or use `AopContext.currentProxy()` (self-injection) if unavoidable.
- **Qualifying by string bean name everywhere** — string-based `@Qualifier("someBeanName")` values are easy to typo and the compiler won't catch it. Fix: define custom qualifier annotations for frequently disambiguated types.
- **Assuming `@Bean` methods calling each other create separate instances** — without understanding `@Configuration` proxying, developers sometimes think two calls to another `@Bean` method inside the same config class create two different objects. Fix: know that Spring intercepts these calls (via CGLIB proxy) to return the same singleton, as long as the class is `@Configuration` (not `@Component`) and `proxyBeanMethods` is left at its default `true`.
- **Not understanding that `@Configuration` classes must be non-final** — CGLIB proxying requires subclassing, so a `final` configuration class (or one with `final` `@Bean` methods) can silently break the "singleton via method call" guarantee, or fail outright. Fix: leave configuration classes and their `@Bean` methods non-final, or use `proxyBeanMethods = false` deliberately if you don't need that behavior.

## Quick Recap

- Beans are objects managed by Spring's IoC container; DI is how their dependencies get wired in from the outside.
- `@Component`, `@Service`, `@Repository`, `@Controller` all register a class as a bean via component scanning; the specializations mostly add semantic meaning (`@Repository` also adds exception translation).
- `@Configuration` + `@Bean` is for beans you build with code, especially third-party classes; `@Configuration` classes are proxied so cross-method `@Bean` calls return the same singleton.
- `@Autowired` triggers injection; it's optional on a single constructor.
- **Constructor injection is the default recommendation**: immutable, fail-fast, easy to test. Field injection is an anti-pattern; setter injection is for optional/reconfigurable dependencies.
- `@Primary` sets a soft default among multiple candidate beans; `@Qualifier` picks a specific one and always wins over `@Primary`.
- `@Lazy` defers bean creation until first use — trades startup speed for delayed error visibility.
- Profiles (`@Profile`) activate different beans per environment (`dev`, `prod`, etc.), configured via `spring.profiles.active`.
- Conditional beans (`@ConditionalOnProperty`, `@ConditionalOnMissingBean`, `@ConditionalOnClass`, etc.) drive Spring Boot's auto-configuration and let you register beans only when certain conditions are met.
- Circular dependencies should be fixed by redesigning the classes, not by switching to field injection or blindly enabling `spring.main.allow-circular-references=true`.
- `BeanPostProcessor` hooks into every bean's initialization lifecycle (before/after); it's the mechanism behind AOP proxies for `@Transactional`, `@Async`, and `@Cacheable`. `BeanFactoryPostProcessor` operates even earlier, on bean definitions rather than instances.
