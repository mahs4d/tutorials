# 1. Spring Fundamentals

## Overview

Spring is a framework for building Java applications, and it has been the dominant choice in enterprise Java for over 20 years. At its core, Spring solves one big problem: it manages the *plumbing* of your application (creating objects, wiring them together, handling cross-cutting concerns like transactions and security) so you can focus on business logic. It does this through a technique called **Inversion of Control (IoC)**, where the framework — not your code — decides when and how objects are created and connected. Spring Boot, built on top of the core Spring Framework, removes almost all the manual configuration that older Spring applications needed. Understanding these fundamentals is essential because every other Spring topic (data access, web MVC, security, testing) is built directly on top of the container, dependency injection, and bean lifecycle concepts covered in this chapter.

## What is Spring?

Spring is an open-source **application framework** for Java. Think of it as a toolbox plus a set of rules for organizing your code so that different parts of your application don't need to know how to create each other — they just get handed what they need.

Before Spring, Java developers often used **EJB (Enterprise JavaBeans)**, which was powerful but heavy, verbose, and hard to test. Spring emerged (2003, Rod Johnson) as a lighter alternative that let you write plain Java objects (POJOs — Plain Old Java Objects) and have the framework add enterprise features around them, instead of forcing your classes to extend framework base classes.

Key ideas behind Spring:

- **Non-invasive**: your business classes are usually plain Java classes, not subclasses of framework types.
- **Container-managed objects**: Spring creates and manages the objects your application needs (called **beans**).
- **Convention over configuration** (especially in Spring Boot): sensible defaults so you write less boilerplate.
- **Modular**: use only the parts you need (data access, web, messaging, security, etc.).

A minimal Spring Boot application:

```java
package com.example.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class DemoApplication {

    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }
}
```

Running this single class boots up an entire application context, auto-configures common infrastructure (like an embedded web server if you have `spring-boot-starter-web` on the classpath), and starts your app — no XML, no manual server setup.

- **Spring Framework**: the core (IoC container, AOP, data access abstractions, MVC, etc.).
- **Spring Boot**: an opinionated layer on top of Spring Framework that auto-configures things and gives you standalone, production-ready applications with minimal setup.

## Spring Ecosystem Overview

Spring isn't one library — it's a family of projects, each solving a specific problem, all built on the same core IoC container.

| Project | Purpose |
|---|---|
| **Spring Framework** | The foundation: IoC container, DI, AOP, core abstractions. |
| **Spring Boot** | Auto-configuration, embedded servers, starters, production-ready defaults. |
| **Spring MVC** | Web framework for building REST APIs and server-rendered web apps. |
| **Spring Data** | Simplifies data access (JPA, MongoDB, Redis, etc.) with repository abstractions. |
| **Spring Security** | Authentication, authorization, and protection against common exploits. |
| **Spring Cloud** | Tools for building distributed systems / microservices (config, service discovery, circuit breakers). |
| **Spring Batch** | Framework for batch processing (large volume, offline jobs). |
| **Spring Integration** | Implements enterprise integration patterns (messaging, pipelines). |
| **Spring WebFlux** | Reactive, non-blocking web framework (alternative to Spring MVC). |
| **Spring Test** | Testing support for Spring applications (context loading, mocking, slices). |

A typical Spring Boot web application pulls in several of these as dependencies via **starters** — curated dependency bundles that save you from hunting down compatible library versions.

```xml
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-security</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-test</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>
```

- All Spring projects share the same underlying container and programming model, so knowledge transfers between them.
- Spring Boot's **starters** are named `spring-boot-starter-*` and bring in a coherent, version-aligned set of transitive dependencies.
- The **Spring Initializr** (start.spring.io) is the standard way to bootstrap a new project with the right starters.

## Inversion of Control (IoC)

**Inversion of Control** is a design principle where the control of object creation and flow is *inverted* — moved from your code to a framework or container.

Without IoC, your code looks like this:

```java
public class OrderService {
    private final PaymentGateway paymentGateway = new StripePaymentGateway();
    // OrderService is responsible for creating its own dependency
}
```

Here, `OrderService` decides exactly which implementation it needs and creates it itself. This is tight coupling — you can't swap `StripePaymentGateway` for a `PaypalPaymentGateway` without editing `OrderService`.

With IoC, the *creation and wiring* responsibility moves outside the class:

```java
public class OrderService {
    private final PaymentGateway paymentGateway;

    public OrderService(PaymentGateway paymentGateway) {
        this.paymentGateway = paymentGateway; // handed to me, I don't create it
    }
}
```

Now something else (the Spring container) decides which `PaymentGateway` implementation to hand to `OrderService`. This is sometimes explained with the **Hollywood Principle**: "Don't call us, we'll call you." Your class no longer reaches out and grabs its dependencies; the framework calls into your class and provides them.

- IoC is the *general principle*.
- **Dependency Injection (DI)** is the *specific technique* Spring uses to implement IoC (see next section).
- Other ways to implement IoC exist (e.g., the Service Locator pattern), but DI is what Spring uses almost exclusively.

Analogy: think of a restaurant kitchen. Without IoC, each chef would have to go to the farm, buy the ingredients, and bring them back before cooking. With IoC, a supplier (the container) delivers the exact ingredients each chef needs, exactly when needed. The chef just cooks.

## Dependency Injection (DI)

**Dependency Injection** is how Spring supplies an object with the other objects it depends on ("dependencies"), rather than having the object create them itself.

There are three common styles of DI in Spring:

```java
// 1. Constructor Injection (recommended default)
@Service
public class OrderService {
    private final PaymentGateway paymentGateway;

    public OrderService(PaymentGateway paymentGateway) {
        this.paymentGateway = paymentGateway;
    }
}
```

```java
// 2. Setter Injection
@Service
public class OrderService {
    private PaymentGateway paymentGateway;

    @Autowired
    public void setPaymentGateway(PaymentGateway paymentGateway) {
        this.paymentGateway = paymentGateway;
    }
}
```

```java
// 3. Field Injection (convenient, but discouraged for production code)
@Service
public class OrderService {
    @Autowired
    private PaymentGateway paymentGateway;
}
```

The dependency itself is just a normal bean, discovered via component scanning:

```java
public interface PaymentGateway {
    void charge(BigDecimal amount);
}

@Component
public class StripePaymentGateway implements PaymentGateway {
    @Override
    public void charge(BigDecimal amount) {
        // call Stripe API
    }
}
```

| Style | Pros | Cons |
|---|---|---|
| Constructor injection | Immutable fields (`final`), dependencies are explicit and required, easy to unit test with plain `new`, fails fast if a dependency is missing | Slightly more boilerplate for many dependencies (often a sign to refactor) |
| Setter injection | Good for optional dependencies, allows re-configuration after construction | Object can exist in a partially-initialized state; mutable |
| Field injection | Least code to write | Can't be `final`, hard to unit test without reflection or a Spring context, hides dependencies from the public API |

Since Spring 4.3, if a class has **only one constructor**, you don't even need `@Autowired` on it — Spring infers it automatically. `@Autowired` is still required if there are multiple constructors and you need to tell Spring which one to use.

```java
@RestController
public class OrderController {

    private final OrderService orderService; // implicit constructor injection

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }
}
```

- Constructor injection is the officially recommended default in Spring documentation.
- DI decouples "what a class needs" from "how that thing is created."
- DI makes unit testing dramatically easier — you can inject mocks/stubs instead of real implementations.

## Bean Lifecycle

A **bean** is simply an object that is managed by the Spring IoC container. Every bean goes through a well-defined lifecycle from creation to destruction.

Simplified lifecycle order:

1. **Instantiation** — the container creates the object (usually via constructor).
2. **Populate properties** — dependencies are injected (setters/fields).
3. **`@PostConstruct`** — a callback method annotated `@PostConstruct` runs (bean is fully constructed and wired).
4. **Bean is ready for use** — the application can now use it.
5. **`@PreDestroy`** — runs just before the container destroys the bean (e.g., on application shutdown).

```java
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.springframework.stereotype.Component;

@Component
public class ConnectionPoolManager {

    @PostConstruct
    public void init() {
        System.out.println("Opening connection pool...");
        // e.g., warm up connections
    }

    @PreDestroy
    public void cleanup() {
        System.out.println("Closing connection pool...");
        // release resources
    }
}
```

You can also hook into the lifecycle without annotations, by implementing interfaces:

```java
import org.springframework.beans.factory.InitializingBean;
import org.springframework.beans.factory.DisposableBean;

public class LegacyStyleBean implements InitializingBean, DisposableBean {

    @Override
    public void afterPropertiesSet() {
        // equivalent to @PostConstruct
    }

    @Override
    public void destroy() {
        // equivalent to @PreDestroy
    }
}
```

Or declare init/destroy methods explicitly in a `@Bean` definition:

```java
@Configuration
public class AppConfig {

    @Bean(initMethod = "init", destroyMethod = "cleanup")
    public ConnectionPoolManager connectionPoolManager() {
        return new ConnectionPoolManager();
    }
}
```

- If a bean implements `AutoCloseable`/`Closeable`, Spring automatically calls `close()` on shutdown — no `destroyMethod` needed.
- `@PostConstruct`/`@PreDestroy` come from `jakarta.annotation` (part of Jakarta EE / JSR-250), not Spring itself.
- **`BeanPostProcessor`** implementations can hook into every bean's lifecycle globally (e.g., to wrap beans in proxies) — this is how features like `@Transactional` and `@Async` are implemented under the hood.

## Bean Scopes

A **scope** determines *how many instances* of a bean the container creates, and *how long* each instance lives.

| Scope | Description | Typical use case |
|---|---|---|
| `singleton` (default) | One instance per Spring container, shared everywhere | Stateless services, repositories |
| `prototype` | A new instance every time the bean is requested | Stateful, non-thread-safe helper objects |
| `request` | One instance per HTTP request (web-aware) | Holding per-request data |
| `session` | One instance per HTTP session (web-aware) | User-specific shopping cart, etc. |
| `application` | One instance per `ServletContext` (web-aware) | App-wide shared state in a web app |
| `websocket` | One instance per WebSocket session | WebSocket-specific state |

```java
import org.springframework.context.annotation.Scope;
import org.springframework.stereotype.Component;

@Component
@Scope("singleton") // this is the default, shown here for clarity
public class ReportGeneratorService {
    // shared, stateless — safe as a singleton
}

@Component
@Scope("prototype")
public class CsvExportJob {
    // holds mutable state per export — needs a fresh instance each time
    private final List<String> rows = new ArrayList<>();
}
```

Injecting a `prototype` bean into a `singleton` bean is a classic gotcha: the singleton is only created once, so it only gets **one** instance of the prototype at injection time, forever. To get a fresh prototype instance on every use, use a `ObjectProvider` or scoped proxy:

```java
@Component
public class ExportScheduler {

    private final ObjectProvider<CsvExportJob> jobProvider;

    public ExportScheduler(ObjectProvider<CsvExportJob> jobProvider) {
        this.jobProvider = jobProvider;
    }

    public void runExport() {
        CsvExportJob job = jobProvider.getObject(); // fresh instance every call
        job.run();
    }
}
```

- `singleton` scope means "one per Spring container," not "one per JVM" — it's not the classic Gang-of-Four Singleton pattern.
- Web-aware scopes (`request`, `session`, `application`, `websocket`) require a web-aware `ApplicationContext` (automatic in Spring Boot web apps).
- Most beans should stay `singleton` — reach for `prototype` only when a bean truly holds mutable, request-specific state.

## Spring Container

The **Spring container** (also called the **IoC container**) is the core engine of the framework. It is responsible for:

- Reading configuration metadata (annotations, Java `@Configuration` classes, or XML).
- Instantiating beans.
- Wiring dependencies between beans (DI).
- Managing each bean's full lifecycle.
- Disposing of beans when the application shuts down.

Think of the container as a factory-plus-registry: it knows the *recipe* for every bean (how to build it, what it depends on) and it hands out the finished object whenever something asks for it.

```java
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.boot.SpringApplication;

public class DemoApplication {
    public static void main(String[] args) {
        ConfigurableApplicationContext context =
                SpringApplication.run(DemoApplication.class, args);

        OrderService orderService = context.getBean(OrderService.class);
        orderService.placeOrder();

        context.close(); // triggers @PreDestroy on all singleton beans
    }
}
```

In everyday Spring Boot development you rarely call `getBean()` yourself — the container injects dependencies for you — but understanding that the container is a real, inspectable object (an `ApplicationContext`) is important for debugging and for advanced use cases like plugin systems or manual bean lookup.

- The container is created once at startup and lives for the lifetime of the application.
- It is configured by one or more **configuration metadata** sources (see below).
- Everything else in Spring (AOP, transactions, event publishing) is built as extensions around this container.

## BeanFactory vs ApplicationContext

Spring actually has two levels of container API: `BeanFactory` (the basic one) and `ApplicationContext` (an enhanced one built on top of it).

| Feature | `BeanFactory` | `ApplicationContext` |
|---|---|---|
| Bean instantiation & DI | Yes | Yes |
| Lazy initialization by default | Yes | No (eager by default, though configurable) |
| Automatic `BeanPostProcessor` registration | Manual | Automatic |
| Internationalization (`MessageSource`) | No | Yes |
| Event publishing (`ApplicationEventPublisher`) | No | Yes |
| Environment abstraction (profiles, properties) | No | Yes |
| AOP integration | Manual | Automatic |
| Typical usage today | Rare, low-level | Standard — used by virtually all Spring/Spring Boot apps |

```java
import org.springframework.context.ApplicationContext;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

public class ManualBootstrapExample {
    public static void main(String[] args) {
        ApplicationContext context =
                new AnnotationConfigApplicationContext(AppConfig.class);

        OrderService orderService = context.getBean(OrderService.class);
        orderService.placeOrder();
    }
}
```

- `ApplicationContext` extends `BeanFactory` (via `ListableBeanFactory` and `HierarchicalBeanFactory`), so everything a `BeanFactory` can do, `ApplicationContext` can also do — plus more.
- In modern Spring Boot development, you interact with `ApplicationContext` (or don't interact with the container directly at all, since DI handles it).
- Common `ApplicationContext` implementations: `AnnotationConfigApplicationContext` (Java config), `GenericWebApplicationContext` / `AnnotationConfigServletWebServerApplicationContext` (Spring Boot web apps).

## Configuration Metadata

**Configuration metadata** is the information that tells the Spring container *which beans to create and how to wire them*. Historically Spring supported three forms, and Spring Boot apps today almost always use the last two.

| Form | How it looks | Status today |
|---|---|---|
| XML configuration | `<bean>` tags in an `.xml` file | Legacy; still supported, rarely used in new code |
| Java configuration | `@Configuration` classes with `@Bean` methods | Common, explicit, type-safe |
| Annotation-based configuration | `@Component`, `@Service`, `@Repository`, `@Autowired` on your own classes, discovered via scanning | Most common in day-to-day Spring Boot code |

All three forms can be mixed in the same application — the container doesn't care where the metadata came from, only that it ends up with a complete "bean definition" for each bean (its class, scope, dependencies, lifecycle callbacks, etc.).

```java
// A bean definition can come from an annotated component...
@Service
public class InvoiceService { }

// ...or from an explicit @Bean method...
@Configuration
public class AppConfig {
    @Bean
    public InvoiceService invoiceService() {
        return new InvoiceService();
    }
}
```

```xml
<!-- ...or from XML. All three end up as the same kind of BeanDefinition -->
<bean id="invoiceService" class="com.example.demo.InvoiceService"/>
```

- Regardless of source, the container converts each declaration into an internal `BeanDefinition` object.
- Mixing styles is fine, but consistency within a codebase makes it much easier to reason about where beans come from.
- Spring Boot's auto-configuration mechanism is itself just a large, conditional set of Java configuration classes shipped inside starter JARs.

## XML Configuration

XML configuration was the *original* way to configure Spring (pre-2.5) and is still fully supported, though it's now considered legacy for new projects. You define beans declaratively in an XML file.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<beans xmlns="http://www.springframework.org/schema/beans"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
       xsi:schemaLocation="http://www.springframework.org/schema/beans
           http://www.springframework.org/schema/beans/spring-beans.xsd">

    <bean id="paymentGateway" class="com.example.demo.StripePaymentGateway"/>

    <bean id="orderService" class="com.example.demo.OrderService">
        <constructor-arg ref="paymentGateway"/>
    </bean>

</beans>
```

Loading it in code:

```java
import org.springframework.context.ApplicationContext;
import org.springframework.context.support.ClassPathXmlApplicationContext;

public class XmlBootstrapExample {
    public static void main(String[] args) {
        ApplicationContext context =
                new ClassPathXmlApplicationContext("applicationContext.xml");

        OrderService orderService = context.getBean(OrderService.class);
        orderService.placeOrder();
    }
}
```

- XML configuration is verbose and not type-safe — a typo in a class name or property is only caught at runtime.
- You may still encounter it in older, long-lived enterprise codebases — it's important to be able to *read* it even if you'd never *write* it today.
- Refactoring tools (rename class, rename method) don't automatically update XML the way they update Java code.
- Modern guidance (including official Spring documentation) recommends Java or annotation-based configuration for all new development.

## Java Configuration

**Java configuration** uses plain Java classes annotated `@Configuration`, with `@Bean` methods that return the objects to be managed by the container. This gives you full type safety and refactoring support from your IDE.

```java
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class AppConfig {

    @Bean
    public PaymentGateway paymentGateway() {
        return new StripePaymentGateway();
    }

    @Bean
    public OrderService orderService(PaymentGateway paymentGateway) {
        // parameters of a @Bean method are resolved from the container automatically
        return new OrderService(paymentGateway);
    }
}
```

Java configuration is especially useful when:

- You need to configure a **third-party class** you can't annotate directly (e.g., you can't put `@Component` on a class from an external library).
- The bean's construction logic is conditional or non-trivial.

```java
@Configuration
public class ObjectMapperConfig {

    @Bean
    public ObjectMapper objectMapper() {
        ObjectMapper mapper = new ObjectMapper();
        mapper.registerModule(new JavaTimeModule());
        mapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        return mapper;
    }
}
```

You can also compose multiple configuration classes:

```java
@Configuration
@Import({ AppConfig.class, ObjectMapperConfig.class })
public class RootConfig {
}
```

- `@Configuration` classes are themselves beans, and Spring proxies them (via CGLIB by default) so that calling one `@Bean` method from another still returns the *same* managed singleton instance rather than a new object.
- Prefer Java config for beans you don't own the source code of, or when construction needs custom logic.
- `@Bean` methods can take parameters — Spring resolves them from the container just like constructor injection.

## Annotation-based Configuration

**Annotation-based configuration** (sometimes called "component scanning") is the style used for almost all of *your own* classes in a modern Spring Boot app. You annotate a class to mark it as a bean, and Spring finds it automatically by scanning the classpath.

```java
import org.springframework.stereotype.Component;
import org.springframework.stereotype.Service;
import org.springframework.stereotype.Repository;
import org.springframework.web.bind.annotation.RestController;

@Component   // generic bean
public class AuditLogger { }

@Service     // semantic alias for @Component — marks a service-layer class
public class OrderService { }

@Repository  // semantic alias for @Component — marks a data-access class,
             // also enables persistence-exception translation
public class OrderRepositoryImpl { }

@RestController // semantic alias combining @Controller + @ResponseBody
public class OrderController { }
```

For component scanning to find these classes, they must live in a package scanned by the application. Spring Boot's `@SpringBootApplication` includes `@ComponentScan`, which by default scans the package the main class is in, and all sub-packages:

```java
@SpringBootApplication // includes @ComponentScan on the current package + subpackages
public class DemoApplication {
    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }
}
```

If your beans live outside that package tree, you must widen the scan explicitly:

```java
@SpringBootApplication
@ComponentScan(basePackages = { "com.example.demo", "com.example.shared" })
public class DemoApplication {
    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }
}
```

| Annotation | Meaning |
|---|---|
| `@Component` | Generic Spring-managed bean |
| `@Service` | Business/service layer bean (semantically the same as `@Component`) |
| `@Repository` | Data-access layer bean; adds automatic translation of persistence exceptions |
| `@Controller` | Web layer bean that returns view names (server-rendered pages) |
| `@RestController` | `@Controller` + `@ResponseBody` — returns data (JSON/XML) directly |
| `@Configuration` | A class providing `@Bean` definitions |

- Annotation-based configuration + component scanning is the dominant style in modern Spring Boot code.
- `@Service`, `@Repository`, `@Controller`, `@RestController` are all specializations of `@Component` — using the most specific one communicates intent and can enable extra behavior (like exception translation for `@Repository`).
- `@Autowired`, `@Value`, and `@Qualifier` are the companion annotations used to inject dependencies and configuration values into these components.

## Common Code Review / Interview Pitfalls

- **Field injection everywhere.** It's quick to write but hides dependencies, prevents `final` fields, and makes unit testing without a Spring context painful.
  ❌
  ```java
  @Service
  public class OrderService {
      @Autowired
      private PaymentGateway paymentGateway;
  }
  ```
  ✅
  ```java
  @Service
  public class OrderService {
      private final PaymentGateway paymentGateway;
      public OrderService(PaymentGateway paymentGateway) {
          this.paymentGateway = paymentGateway;
      }
  }
  ```

- **Injecting a `prototype`-scoped bean into a `singleton` without a provider.** The singleton is built once, so it silently captures only the first prototype instance forever, defeating the purpose of the scope. Fix: inject an `ObjectProvider<T>` (or use a scoped proxy) and call `getObject()` each time a fresh instance is needed.

- **Circular dependencies between beans (A needs B, B needs A).** Constructor injection will fail fast at startup with a clear error; some developers "fix" this by switching to field/setter injection, which just papers over a design smell. Fix: break the cycle by extracting shared logic into a third bean, or use events/`@Lazy` only as a last resort.

- **Overusing `@Autowired` on multiple constructors without a clear primary one.** Spring can't guess which constructor to use if there's more than one and none marked, causing ambiguous injection errors. Fix: keep one constructor for DI, or explicitly annotate the intended one with `@Autowired`.

- **Treating `singleton` scope like the Gang-of-Four Singleton pattern.** A Spring singleton is "one instance per container," not "one instance per JVM" — if you spin up two contexts (e.g., in tests), you get two instances. Don't rely on identity comparisons or static-like assumptions across contexts.

- **Putting mutable state in a singleton service.** Since only one instance is shared across all requests/threads, mutable instance fields cause race conditions under concurrent load.
  ❌
  ```java
  @Service
  public class ReportService {
      private List<String> lastRows; // shared mutable state — not thread-safe
  }
  ```
  ✅
  ```java
  @Service
  public class ReportService {
      public List<String> generate(ReportRequest request) {
          List<String> rows = new ArrayList<>(); // local, per-call state
          return rows;
      }
  }
  ```

- **Forgetting that `@PostConstruct` runs after DI, not during construction.** Doing initialization work that depends on injected fields inside the constructor (before setter/field injection has run) can NPE. Fix: use `@PostConstruct` (or constructor injection, which guarantees dependencies are set before the constructor body runs) for init logic that needs dependencies.

- **Relying on XML configuration in new code "because that's what the old project used."** It's verbose, not type-safe, and IDE refactors won't update it. Fix: migrate new modules to Java/annotation configuration; only touch XML when maintaining legacy code.

- **Using `javax.annotation.PostConstruct`/`PreDestroy` in a Spring Boot 3.x project.** Spring Boot 3 moved to the Jakarta EE 9+ namespace; the old `javax.*` packages don't exist on the classpath anymore and will fail to compile. Fix: import from `jakarta.annotation.*`.

- **Manually calling `new` on a class that should be a Spring bean.** This bypasses the container entirely — no DI, no lifecycle callbacks, no AOP proxying (so `@Transactional`, `@Async`, etc. silently won't work). Fix: let the class be managed by the container and injected where needed.

- **Widening `@ComponentScan` unnecessarily (e.g., scanning the entire `com.example` root across unrelated modules).** This slows startup and can accidentally pick up beans that were never meant to be shared, causing surprising bean collisions. Fix: scope `@ComponentScan`/package structure deliberately, one bounded package tree per module.

- **Not understanding `BeanFactory` vs `ApplicationContext` in an interview.** A common trick question is "what's the difference?" — answering "they're the same" or not knowing `ApplicationContext` adds events, i18n, and eager initialization is a red flag. Know the comparison table.

- **Assuming `@Service`, `@Repository`, `@Component` behave identically in every respect.** They're mostly interchangeable for component scanning, but `@Repository` specifically triggers persistence exception translation via a `PersistenceExceptionTranslationPostProcessor` — using plain `@Component` on a DAO class silently loses that behavior.

- **Two-object `@Bean` calling convention confusion:** calling another `@Bean` method directly as a plain Java method call *inside a non-`@Configuration` class* (e.g., a `@Component`) does **not** return the managed singleton — CGLIB proxying that makes intra-config calls return the shared bean only applies to `@Configuration` classes. Fix: never rely on that behavior outside of `@Configuration`; inject beans instead of calling factory methods directly.

## Quick Recap

- **Spring** = a framework that manages object creation/wiring (the container) so your business code stays plain Java.
- **IoC** = the principle of handing control of object creation to a framework instead of doing it yourself; **DI** is how Spring implements it.
- Prefer **constructor injection** — it's explicit, testable, and supports immutability (`final` fields).
- Every bean goes through a lifecycle: instantiate → inject dependencies → `@PostConstruct` → ready → `@PreDestroy`.
- Default bean scope is `singleton` (one per container); use `prototype` only for genuinely stateful, short-lived beans, and inject those via `ObjectProvider`.
- The **Spring container** (`ApplicationContext`) reads configuration metadata and manages every bean's full lifecycle.
- `ApplicationContext` is a superset of `BeanFactory` — it adds eager init, events, i18n, environment/profile support, and automatic AOP/post-processor wiring.
- Configuration metadata can come from **XML** (legacy), **Java `@Configuration`/`@Bean`** (explicit, type-safe), or **annotations + component scanning** (`@Component`/`@Service`/`@Repository`/`@Controller`, most common today).
- In Spring Boot 3.x, use `jakarta.*` packages (e.g., `jakarta.annotation.PostConstruct`), not `javax.*`.
- Know the ecosystem map: Spring Boot (auto-config), Spring MVC/WebFlux (web), Spring Data (persistence), Spring Security (auth), Spring Cloud (microservices) — all sit on top of the same core IoC container.
