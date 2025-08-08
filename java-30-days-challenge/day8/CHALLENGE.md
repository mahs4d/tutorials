# Day 8: Spring IoC & Dependency Injection

| | |
|---|---|
| 🏗️ **Project** | **TinyDI** — a hand-rolled DI container, then rebuilt with Spring |
| ☕ **Java & language skills** | Reflection, annotations, constructor injection, interfaces/polymorphism |
| 🧰 **Library / tool** | Spring Core / spring-context (ApplicationContext, @Configuration/@Bean, @Component) |
| 🗄️ **DB / distributed-systems concept** | Inversion of Control / Dependency Injection & service wiring |
| 📊 **Difficulty** | Easy |

---

This is your **first Spring day**. Everything from Days 1–7 was you holding the wiring by hand: in Day 4's queue and Day 5's MVCC engine you `new`-ed your components and threaded them together manually in `main`. That worked because the graphs were tiny. Today we attack the *wiring problem itself* — and then hand it to a container.

Crucially, we use **Spring Core without Spring Boot**. Day 9 brings in Boot and connection pooling. By doing IoC the "raw" way first, you'll understand exactly what Boot's auto-configuration is automating — so Boot feels like a convenience, not a black box.

---

## Concept primer: Inversion of Control & Dependency Injection

### The problem with `new`

Consider a classic three-layer service graph (the same one we'll build):

```
Facade  ──depends-on──▶  Service  ──depends-on──▶  Repository
```

The naive way:

```java
class OrderService {
    private final OrderRepository repo = new InMemoryOrderRepository(); // ❌
}
```

This single line carries three sins:

1. **Tight coupling.** `OrderService` is welded to a *concrete* class. You can't swap `InMemoryOrderRepository` for `PostgresOrderRepository` without editing `OrderService`.
2. **Untestable.** In a unit test you want a fake/mock repo. But `OrderService` builds its own — you can't inject a test double. (This is the single biggest practical reason DI exists.)
3. **Hidden lifecycle.** Who decides if the repo is a shared singleton or a fresh instance? `OrderService` does, accidentally, by calling `new`. That's a policy decision leaking into business code.

### Inversion of Control

**IoC** inverts *who is in control of object creation and wiring*. Instead of an object reaching out to construct/locate its dependencies, an external authority (a *container*) constructs them and hands them in. The object becomes passive about its collaborators — it just *declares* what it needs.

This is the **Hollywood Principle**: *"Don't call us, we'll call you."* Your class doesn't call the container to get a repo; the container calls your constructor with a repo already in hand.

> IoC is the *principle*. DI is one concrete *implementation* of it. (Other IoC flavors: the template-method pattern, event/callback systems, the service-locator pattern.) Spring chose DI.

### Dependency Injection — and why constructor injection

DI says: a dependency is supplied ("injected") from outside. Three injection styles:

| Style | Looks like | Verdict |
|---|---|---|
| **Constructor** | `OrderService(OrderRepository repo)` | ✅ **Preferred.** Dependencies are explicit, can be `final` (immutable), object is never in a half-built state, and it's trivially testable with `new OrderService(fakeRepo)` — *no container needed in tests*. |
| **Setter** | `setRepo(OrderRepository repo)` | ⚠️ For genuinely optional/reconfigurable deps only. Allows objects to exist half-wired. |
| **Field** | `@Autowired OrderRepository repo;` | ❌ Convenient but hides dependencies, can't be `final`, and is **untestable without reflection or a container**. Avoid in production code. |

**Rule of thumb (and a frequent senior interview answer):** mandatory dependencies → constructor injection; optional dependencies → setter injection; field injection → basically never (except quick throwaway tests).

A subtle bonus: if a class needs *too many* constructor args, the constructor *hurts*, and that pain is a **design smell** telling you the class does too much. Field injection hides that smell. Constructor injection surfaces it. The friction is a feature.

### Bean lifecycle & scopes (the container's job)

Once a container owns object creation, it also owns the **lifecycle**:

- **Instantiate** → **populate dependencies (inject)** → **initialization callbacks** (`@PostConstruct`) → bean is *ready* → ... → **destruction callbacks** (`@PreDestroy`) at shutdown.
- `@PostConstruct` runs *after* injection completes — the right place to validate config or warm a cache, because all collaborators are guaranteed present.

**Scopes** decide how many instances exist:

- **`singleton`** (Spring's default): one shared instance per container. Most beans are stateless services → singletons.
- **`prototype`**: a brand-new instance every time it's requested. Spring does *not* manage the full lifecycle of prototypes (it won't call `@PreDestroy`).
- (Web scopes — `request`, `session` — exist but need a web context; not today.)

### The distributed-systems angle: containers ≈ service discovery

Hold this mapping in your head — it recurs all month:

| In-process DI | Distributed systems |
|---|---|
| Object declares it needs `OrderRepository` | Service declares it needs the "inventory" service |
| Container injects a concrete bean | **Service registry / discovery** (Eureka, Consul, k8s DNS) injects a live endpoint |
| Bean scope (singleton vs prototype) | Connection/instance pooling, per-request endpoint selection |
| Swap impl via config, no code change | Swap backend via registry, no client change |
| `@PostConstruct` readiness | Health checks / readiness probes |

The deep idea is identical: **a component should not locate its own collaborators.** Whether the collaborator is an in-JVM object or a service across the network, *something external resolves the binding and supplies it.* The IoC container is service discovery for objects. Day 22 (Consistent Hashing) and the resilience/observability days build directly on this mental model.

---

## Prerequisites

- JDK 21 installed (`java -version` → 21.x).
- Maven 3.9+ (`mvn -version`).
- Comfort with reflection basics is helpful but we'll explain as we go.

### Project layout

```
day8/
├── pom.xml
└── src/main/java/com/db30/day8/
    ├── domain/        Order.java
    ├── repo/          OrderRepository.java, InMemoryOrderRepository.java
    ├── service/       OrderService.java
    ├── facade/        OrderFacade.java
    ├── mini/          MiniContainer.java, MiniMain.java   (hand-rolled DI)
    └── spring/        AppConfig.java, SpringMain.java     (Spring version)
```

### `pom.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.db30</groupId>
    <artifactId>day8-ioc-di</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.release>21</maven.compiler.release>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <spring.version>6.1.14</spring.version>
    </properties>

    <dependencies>
        <!-- The IoC container itself. Pulls in spring-core, spring-beans, spring-aop. -->
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-context</artifactId>
            <version>${spring.version}</version>
        </dependency>

        <!-- @PostConstruct / @PreDestroy moved out of the JDK in Java 11+.
             Spring uses jakarta.* annotations in 6.x. -->
        <dependency>
            <groupId>jakarta.annotation</groupId>
            <artifactId>jakarta.annotation-api</artifactId>
            <version>2.1.1</version>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.codehaus.mojo</groupId>
                <artifactId>exec-maven-plugin</artifactId>
                <version>3.5.0</version>
            </plugin>
        </plugins>
    </build>
</project>
```

> Note: there is **no `spring-boot-starter`** anywhere. We are deliberately living without Boot today. `spring-context` is the bare IoC container.

---

## 🛠️ Project Walkthrough — TinyDI

Roll up your sleeves: from here on you'll build the service graph, hand-roll a tiny DI container, then rebuild the exact same wiring with Spring — running each step and checking the output as you go.

---

## Step 1 — The domain and the service graph (no DI yet)

First, the plain objects. These have *zero* knowledge of any container — that's the point. A well-designed bean is just a POJO with a constructor.

**`domain/Order.java`**

```java
package com.db30.day8.domain;

import java.math.BigDecimal;

public record Order(String id, String customer, BigDecimal amount) {}
```

**`repo/OrderRepository.java`** — the *interface* is the dependency, not the impl:

```java
package com.db30.day8.repo;

import com.db30.day8.domain.Order;
import java.util.List;
import java.util.Optional;

public interface OrderRepository {
    void save(Order order);
    Optional<Order> findById(String id);
    List<Order> findAll();
}
```

**`repo/InMemoryOrderRepository.java`**

```java
package com.db30.day8.repo;

import com.db30.day8.domain.Order;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

public class InMemoryOrderRepository implements OrderRepository {

    private final Map<String, Order> store = new ConcurrentHashMap<>();

    public InMemoryOrderRepository() {
        System.out.println("[lifecycle] InMemoryOrderRepository constructed");
    }

    @Override public void save(Order order) {
        store.put(order.id(), order);
    }

    @Override public Optional<Order> findById(String id) {
        return Optional.ofNullable(store.get(id));
    }

    @Override public List<Order> findAll() {
        return List.copyOf(store.values());
    }
}
```

**`service/OrderService.java`** — note the **constructor injection** and the `final` field:

```java
package com.db30.day8.service;

import com.db30.day8.domain.Order;
import com.db30.day8.repo.OrderRepository;
import java.math.BigDecimal;
import java.util.List;

public class OrderService {

    private final OrderRepository repository;   // final → immutable, can't be half-wired

    // The ONLY way to build an OrderService is to hand it a repository.
    // No `new InMemoryOrderRepository()` anywhere in here.
    public OrderService(OrderRepository repository) {
        this.repository = repository;
        System.out.println("[lifecycle] OrderService constructed with " 
                + repository.getClass().getSimpleName());
    }

    public Order placeOrder(String id, String customer, BigDecimal amount) {
        if (amount.signum() <= 0) {
            throw new IllegalArgumentException("amount must be positive");
        }
        var order = new Order(id, customer, amount);
        repository.save(order);
        return order;
    }

    public List<Order> allOrders() {
        return repository.findAll();
    }
}
```

**`facade/OrderFacade.java`** — depends on the *service*, completing the chain:

```java
package com.db30.day8.facade;

import com.db30.day8.domain.Order;
import com.db30.day8.service.OrderService;
import java.math.BigDecimal;
import java.util.List;

public class OrderFacade {

    private final OrderService service;

    public OrderFacade(OrderService service) {
        this.service = service;
        System.out.println("[lifecycle] OrderFacade constructed");
    }

    public String summarizeNewOrder(String id, String customer, BigDecimal amount) {
        Order o = service.placeOrder(id, customer, amount);
        return "Placed order %s for %s: $%s (total orders now: %d)"
                .formatted(o.id(), o.customer(), o.amount(), service.allOrders().size());
    }

    public List<Order> list() {
        return service.allOrders();
    }
}
```

Three classes, a clean dependency chain `OrderFacade → OrderService → OrderRepository`, and **not one `new` of a collaborator inside any of them**. Each only knows the interface/type it needs. That property — being "injection-ready" — is what lets a container take over.

---

## Step 2 — Hand-roll a tiny DI container (demystify the magic)

Before Spring, let's prove there's no magic. A DI container is, at its core:

1. A **registry**: `Map<Class<?>, Object>` mapping a type → its single instance (we'll do singletons only).
2. A **resolver**: given a class to build, look at its constructor, recursively resolve each parameter type, then reflectively invoke the constructor.
3. **Lifecycle**: after construction, call any `@PostConstruct` method.

That's genuinely the essence of what Spring does (Spring adds *enormous* breadth — scopes, proxies, AOP, config parsing — but the spine is this).

**`mini/MiniContainer.java`**

```java
package com.db30.day8.mini;

import jakarta.annotation.PostConstruct;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;

/**
 * A ~50-line DI container. Singleton scope only. Constructor injection only.
 * Resolves an interface to a registered implementation, builds the graph
 * recursively, and runs @PostConstruct. This is the spine of every IoC container.
 */
public class MiniContainer {

    // type (often an interface) -> concrete class to instantiate for it
    private final Map<Class<?>, Class<?>> bindings = new HashMap<>();
    // fully-built singletons, cached by their requested type
    private final Map<Class<?>, Object> singletons = new HashMap<>();

    /** Register that `type` should be satisfied by `impl`. e.g. bind(Repo.class, InMemoryRepo.class) */
    public <T> MiniContainer bind(Class<T> type, Class<? extends T> impl) {
        bindings.put(type, impl);
        return this; // fluent
    }

    /** Register a concrete class as itself (e.g. a Service or Facade). */
    public MiniContainer register(Class<?> concrete) {
        bindings.put(concrete, concrete);
        return this;
    }

    /** Resolve (creating if needed) the singleton instance for `type`. */
    @SuppressWarnings("unchecked")
    public <T> T get(Class<T> type) {
        // Already built? return the cached singleton.
        if (singletons.containsKey(type)) {
            return (T) singletons.get(type);
        }

        Class<?> impl = bindings.get(type);
        if (impl == null) {
            throw new IllegalStateException("No binding for " + type.getName());
        }

        // Pick the (single) public constructor and resolve each parameter recursively.
        Constructor<?> ctor = impl.getConstructors()[0];
        Class<?>[] paramTypes = ctor.getParameterTypes();
        Object[] args = new Object[paramTypes.length];
        for (int i = 0; i < paramTypes.length; i++) {
            args[i] = get(paramTypes[i]); // <-- the recursion that wires the whole graph
        }

        Object instance;
        try {
            instance = ctor.newInstance(args);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("Failed to instantiate " + impl.getName(), e);
        }

        // Cache BEFORE @PostConstruct so the same singleton is shared everywhere.
        singletons.put(type, instance);
        if (impl != type) {
            singletons.put(impl, instance); // also key by impl
        }

        invokePostConstruct(instance);
        return (T) instance;
    }

    private void invokePostConstruct(Object instance) {
        for (Method m : instance.getClass().getDeclaredMethods()) {
            if (m.isAnnotationPresent(PostConstruct.class)) {
                try {
                    m.setAccessible(true);
                    m.invoke(instance);
                } catch (ReflectiveOperationException e) {
                    throw new IllegalStateException("@PostConstruct failed on "
                            + instance.getClass().getName(), e);
                }
            }
        }
    }
}
```

Add a `@PostConstruct` to the service so we can see the lifecycle hook fire. Update **`service/OrderService.java`** to include:

```java
import jakarta.annotation.PostConstruct;
// ...
@PostConstruct
void warmUp() {
    System.out.println("[lifecycle] OrderService @PostConstruct — dependencies are wired, ready to serve");
}
```

**`mini/MiniMain.java`**

```java
package com.db30.day8.mini;

import com.db30.day8.facade.OrderFacade;
import com.db30.day8.repo.InMemoryOrderRepository;
import com.db30.day8.repo.OrderRepository;
import com.db30.day8.service.OrderService;
import java.math.BigDecimal;

public class MiniMain {
    public static void main(String[] args) {
        System.out.println("=== Hand-rolled MiniContainer ===");

        // Configuration: declare bindings, but DON'T construct anything yet.
        var container = new MiniContainer()
                .bind(OrderRepository.class, InMemoryOrderRepository.class)
                .register(OrderService.class)
                .register(OrderFacade.class);

        // Ask for the top of the graph. The container builds Repo -> Service -> Facade
        // in dependency order, injecting as it goes.
        OrderFacade facade = container.get(OrderFacade.class);

        System.out.println(facade.summarizeNewOrder("o-1", "alice", new BigDecimal("42.00")));
        System.out.println(facade.summarizeNewOrder("o-2", "bob",   new BigDecimal("99.50")));

        // Prove singleton scope: same Service instance resolved twice.
        OrderService a = container.get(OrderService.class);
        OrderService b = container.get(OrderService.class);
        System.out.println("Same OrderService singleton? " + (a == b));
    }
}
```

Run it:

```bash
cd day8
mvn -q compile
mvn -q exec:java -Dexec.mainClass=com.db30.day8.mini.MiniMain
```

**Expected output:**

```
=== Hand-rolled MiniContainer ===
[lifecycle] InMemoryOrderRepository constructed
[lifecycle] OrderService constructed with InMemoryOrderRepository
[lifecycle] OrderService @PostConstruct — dependencies are wired, ready to serve
[lifecycle] OrderFacade constructed
Placed order o-1 for alice: $42.00 (total orders now: 1)
Placed order o-2 for bob: $99.50 (total orders now: 2)
Same OrderService singleton? true
```

Look at the construction order: **Repository first, then Service, then Facade** — exactly the dependency order, driven by the recursion in `get()`. You never wrote `new` for the wiring; you declared bindings and asked for the root. *That* is IoC. The container is in control.

---

## Step 3 — Rebuild the SAME wiring with Spring's `ApplicationContext`

Now hand the job to a real, battle-tested container. We'll show **both** styles Spring offers and use them together:

- **`@Configuration` + `@Bean`** — explicit, Java-based wiring (you write the factory methods). Great when you need fine control or are wiring third-party classes you can't annotate.
- **`@Component` + `@ComponentScan`** — Spring discovers and wires beans automatically by scanning packages. Less boilerplate; the dominant style in app code.

### Option A: explicit `@Bean` factory methods

**`spring/AppConfig.java`**

```java
package com.db30.day8.spring;

import com.db30.day8.facade.OrderFacade;
import com.db30.day8.repo.InMemoryOrderRepository;
import com.db30.day8.repo.OrderRepository;
import com.db30.day8.service.OrderService;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Explicit Java configuration. Each @Bean method is a factory the container calls.
 * Spring sees that orderService(...) needs an OrderRepository and passes the
 * orderRepository() bean automatically (by type). This is the @Bean equivalent
 * of MiniContainer's bindings — but Spring resolves the graph for you.
 */
@Configuration
public class AppConfig {

    @Bean
    public OrderRepository orderRepository() {
        return new InMemoryOrderRepository();
    }

    // Method parameters are dependencies; Spring injects matching beans by type.
    @Bean
    public OrderService orderService(OrderRepository repository) {
        return new OrderService(repository);
    }

    @Bean
    public OrderFacade orderFacade(OrderService service) {
        return new OrderFacade(service);
    }
}
```

**`spring/SpringMain.java`**

```java
package com.db30.day8.spring;

import com.db30.day8.facade.OrderFacade;
import com.db30.day8.service.OrderService;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import java.math.BigDecimal;

public class SpringMain {
    public static void main(String[] args) {
        System.out.println("=== Spring ApplicationContext (@Configuration/@Bean) ===");

        // AnnotationConfigApplicationContext is the no-XML, no-Boot way to start
        // a Spring container from a @Configuration class. (try-with-resources so
        // close() runs @PreDestroy callbacks at shutdown.)
        try (var ctx = new AnnotationConfigApplicationContext(AppConfig.class)) {

            // Resolve beans by type — same idea as MiniContainer.get(Type.class).
            OrderFacade facade = ctx.getBean(OrderFacade.class);

            System.out.println(facade.summarizeNewOrder("o-1", "alice", new BigDecimal("42.00")));
            System.out.println(facade.summarizeNewOrder("o-2", "bob",   new BigDecimal("99.50")));

            OrderService a = ctx.getBean(OrderService.class);
            OrderService b = ctx.getBean(OrderService.class);
            System.out.println("Same OrderService singleton? " + (a == b));

            System.out.println("Beans Spring manages: "
                    + java.util.Arrays.toString(ctx.getBeanDefinitionNames()));
        }
    }
}
```

Run it:

```bash
mvn -q exec:java -Dexec.mainClass=com.db30.day8.spring.SpringMain
```

**Expected output** (Spring banner/log lines elided; your bean output):

```
=== Spring ApplicationContext (@Configuration/@Bean) ===
[lifecycle] InMemoryOrderRepository constructed
[lifecycle] OrderService constructed with InMemoryOrderRepository
[lifecycle] OrderService @PostConstruct — dependencies are wired, ready to serve
[lifecycle] OrderFacade constructed
Placed order o-1 for alice: $42.00 (total orders now: 1)
Placed order o-2 for bob: $99.50 (total orders now: 2)
Same OrderService singleton? true
Beans Spring manages: [..., appConfig, orderRepository, orderService, orderFacade]
```

Notice it is **byte-for-byte the same lifecycle output** as your MiniContainer (same order, same singleton behavior, `@PostConstruct` fires). That's the payoff: you now *know* what Spring is doing, because you built the toy version. Spring just does it robustly, with scopes, proxies, error messages, and 20 years of edge cases handled.

### Option B: component scanning (the everyday style)

To wire by annotations instead of `@Bean` methods, annotate the classes as stereotypes and let Spring scan. Add these annotations (you can do this *in addition* — but to avoid duplicate beans, run scanning as a separate config without the `@Bean` methods for the same types):

```java
// repo/InMemoryOrderRepository.java
@org.springframework.stereotype.Repository
public class InMemoryOrderRepository implements OrderRepository { ... }

// service/OrderService.java
@org.springframework.stereotype.Service
public class OrderService {
    @org.springframework.beans.factory.annotation.Autowired   // optional on a SINGLE ctor in Spring 4.3+
    public OrderService(OrderRepository repository) { ... }
}

// facade/OrderFacade.java
@org.springframework.stereotype.Component
public class OrderFacade { ... }
```

> `@Repository`, `@Service`, `@Component` are all `@Component` specializations — semantic labels for "DB layer / business layer / generic bean." Spring treats them as scannable beans; `@Repository` additionally enables persistence-exception translation (relevant from Day 9 onward).
>
> **Constructor `@Autowired` is optional** when a class has exactly one constructor (Spring 4.3+). This is why modern Spring code shows constructor injection with *no annotation at all* — clean POJOs. Keep `@Autowired` only when there are multiple constructors and you must point to one.

A scanning config:

```java
package com.db30.day8.spring;

import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;

@Configuration
@ComponentScan(basePackages = {
        "com.db30.day8.repo",
        "com.db30.day8.service",
        "com.db30.day8.facade"
})
public class ScanConfig {}
```

Bootstrapped identically: `new AnnotationConfigApplicationContext(ScanConfig.class)`. Same graph, zero `@Bean` methods — Spring found the `@Component`s, read their constructors, and wired by type. This is essentially what **Spring Boot automates further** in Day 9: Boot picks the base package for you (from `@SpringBootApplication`) and adds auto-configuration on top.

---

## 🚀 Going Deeper & Next Steps

### Going deeper / senior-level notes

**`BeanFactory` vs `ApplicationContext`.** `BeanFactory` is the bare-bones IoC interface: lazy bean instantiation, basic DI. `ApplicationContext` *extends* it and adds the things real apps need: eager singleton instantiation at startup (so wiring errors fail fast, not at first request), event publishing (`ApplicationEvent`), internationalization, resource loading, and automatic `BeanPostProcessor`/`BeanFactoryPostProcessor` registration (the hooks that power `@Autowired`, `@Value`, AOP, `@Transactional`). **Always use `ApplicationContext`** in apps; `BeanFactory` matters mostly for understanding the layering and for ultra-constrained environments.

**Circular dependencies.** If `A` needs `B` and `B` needs `A` via **constructor** injection, Spring *cannot* build either (chicken-and-egg) and throws `BeanCurrentlyInCreationException` at startup. With **field/setter** injection Spring can sometimes resolve the cycle by injecting a half-built proxy — which is one reason field injection "feels" easier and is therefore a trap: it lets you *hide* a circular dependency that constructor injection would have forced you to fix. The senior move: a constructor-injection cycle is a **design signal** to break the cycle (extract a third collaborator, use an event, or invert one dependency) — not to switch to field injection to silence it. Our MiniContainer would `StackOverflowError` on a cycle; that honesty is instructive.

**Field vs constructor injection — the real debate.** Field injection (`@Autowired` on a field) is concise and dominates older tutorials, but: (1) fields can't be `final`, so the object is mutable and can be left half-wired; (2) dependencies are invisible to callers and tests — you *need* reflection or a running container to inject test doubles; (3) it silently permits an unbounded number of dependencies, hiding the "this class does too much" smell. Constructor injection fixes all three and lets you write `new OrderService(mockRepo)` in a plain JUnit test with no Spring at all. The community consensus (and Spring's own docs) now recommends **constructor injection for mandatory dependencies.** When you see field injection in a codebase, it's usually legacy or a velocity-over-discipline choice.

**`@PostConstruct` / `@PreDestroy` vs `@Bean(initMethod/destroyMethod)`.** Annotation callbacks live *in* the bean class (good for your own code); `initMethod`/`destroyMethod` on `@Bean` are for third-party classes you can't annotate. Prototype-scoped beans get `@PostConstruct` but **not** `@PreDestroy` — Spring hands ownership of prototypes to the caller after creation. (We'll lean on `@PreDestroy` for closing pools/connections in Day 9 and the resilience days.)

**How this maps to service discovery (the through-line).** Replace "`OrderService` needs an `OrderRepository`" with "the checkout service needs the inventory service." In-process, the container resolves the binding by type and injects the object. In a distributed system, a **service registry** (Consul, Eureka, Kubernetes Services/DNS) resolves the logical name to a live network endpoint and the client library injects/load-balances it. Same inversion: the consumer declares a *need*, not a *location*; an external authority binds it. Bean scope ↔ connection pooling and per-request instance selection; `@PostConstruct` readiness ↔ health/readiness probes; "swap the `@Bean` impl" ↔ "point the registry at a new backend." Keep this analogy live — Days 16 (Redis), 22 (Consistent Hashing), 24 (Resilience), and 25 (Observability) all assume you see wiring and discovery as the same problem at two scales.

---

### Stretch goals

1. **Add a second `OrderRepository` impl and use a qualifier.** Create `LoggingOrderRepository` (a decorator that wraps another repo and prints each save). Now two beans satisfy `OrderRepository` → Spring throws `NoUniqueBeanDefinitionException`. Fix it with `@Primary` on one, or `@Qualifier("logging")` at the injection point. Then make your **MiniContainer** support qualifiers too (key bindings by `(type, name)`), and feel how much Spring saves you.

2. **Add prototype scope and prove the difference.** Mark `OrderService` as `@Scope("prototype")` (or `@Scope(ConfigurableBeanFactory.SCOPE_PROTOTYPE)`), re-run `SpringMain`, and watch `Same OrderService singleton?` flip to `false`. Add a `@PreDestroy` and observe that it does *not* fire for the prototype on `ctx.close()` — confirming Spring doesn't manage prototype destruction.

3. **Force a circular dependency and break it.** Make `OrderRepository`'s impl take an `OrderService` in its constructor (artificial cycle). Watch Spring fail at startup with `BeanCurrentlyInCreationException`, and watch your MiniContainer `StackOverflowError`. Then break the cycle cleanly (extract an interface or publish an event) — internalize *why* constructor injection's failure here is a gift.

4. **Write a real unit test of `OrderService` with no Spring.** Add JUnit 5 + a hand-written fake `OrderRepository`, and test `placeOrder`/`allOrders` via `new OrderService(fakeRepo)`. This is the concrete, daily payoff of constructor injection — and sets up Day 23 (Testcontainers) where the *repository* itself gets a real backend in tests.

---

### Day 9 teaser

Today you started Spring the *hard way* — raw `spring-context`, manual `pom.xml`, an `AnnotationConfigApplicationContext` you started yourself. Tomorrow, **Day 9: Spring Boot & Connection Pooling** flips on the conveniences: `@SpringBootApplication` auto-picks your component scan base package, auto-configuration wires beans you didn't declare, and a real `DataSource` shows up — backed by a **HikariCP connection pool**. We'll connect the DI lifecycle you just learned (`@PostConstruct`/`@PreDestroy`) to pool startup/shutdown, and tie the pool back to the distributed-systems thread: a connection pool is *resource discovery + reuse*, the same inversion you saw today, applied to scarce DB connections. Because you built the toy container and ran Spring without Boot, Boot's "magic" will read as automation you can name — not mystery.
