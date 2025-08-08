# 12. Spring AOP

## Overview

Some things need to happen in many places in your code, but they aren't really part of what that code is *for*. Logging, security checks, transaction handling, retrying failed calls, and measuring how long a method takes are good examples. If you wrote this logic by hand inside every method, you'd repeat yourself constantly and mix unrelated concerns together. These repeated, scattered concerns are called **cross-cutting concerns**, because they "cut across" many classes and layers instead of living in one place. **Aspect-Oriented Programming (AOP)** is a programming paradigm that lets you pull this logic out into its own module and have it applied automatically wherever it's needed. Spring AOP is the framework's implementation of this idea, and it's the mechanism that quietly powers features like `@Transactional` and `@Cacheable`. This chapter explains how Spring AOP works under the hood, how to write your own aspects, and the pitfalls that trip up beginners and reviewers alike.

## Aspect-Oriented Programming

Think of a restaurant kitchen. Each chef focuses on cooking their dish — that's the "business logic." But every dish also needs to be logged for the till, checked for allergens, and timed so it doesn't overcook. Instead of asking every chef to remember all of that on top of cooking, the restaurant assigns a runner who watches every dish come out and handles logging, checks, and timing automatically. That runner is an aspect: a separate piece of behavior that attaches itself to many places without those places needing to know about it.

In code terms, AOP separates two kinds of logic:

- **Core (business) logic** — what a method is actually supposed to do, e.g., `placeOrder()`, `saveUser()`.
- **Cross-cutting logic** — logging, security, transactions, caching, metrics — needed by many methods, but not part of their core purpose.

Without AOP, cross-cutting logic gets copy-pasted everywhere:

```java
public void placeOrder(Order order) {
    long start = System.currentTimeMillis();
    log.info("placeOrder called with {}", order);
    try {
        // actual business logic
        orderRepository.save(order);
    } catch (Exception e) {
        log.error("placeOrder failed", e);
        throw e;
    } finally {
        log.info("placeOrder took {} ms", System.currentTimeMillis() - start);
    }
}
```

With AOP, the method becomes just the business logic, and a separate aspect handles the rest:

```java
public void placeOrder(Order order) {
    orderRepository.save(order);
}
```

Spring AOP is built on top of the **AOP Alliance** / **AspectJ annotation style**, but it only implements a practical subset of full AspectJ. It works by wrapping your beans in **proxies** (explained later) that intercept method calls and run your cross-cutting code before, after, or around the real method.

## Join Points

A **join point** is a specific point during program execution where an aspect *could* be applied — most commonly, a method call. Think of join points as all the doors in a building where the "runner" from our restaurant analogy could step in and act.

Full AspectJ supports many kinds of join points (constructor calls, field access, exception handlers, static initializers). **Spring AOP only supports method execution join points** on Spring-managed beans. That's an important interview fact: if someone asks "can Spring AOP intercept field access?" — the answer is no.

```java
@Service
public class OrderService {

    public void placeOrder(Order order) {   // <-- this method execution is a join point
        orderRepository.save(order);        // <-- so is this one, on a different bean
    }
}
```

Every time `placeOrder()` or `save()` runs, that's a distinct join point in time. A pointcut (next section) is how you *select* which join points you care about.

## Pointcuts

If a join point is "a door where an aspect could act," a **pointcut** is the rule that says "only these doors, not all of them." A pointcut is an expression that matches a set of join points — usually, a set of methods — based on criteria like package, class name, method name, arguments, or annotations.

```java
@Pointcut("execution(* com.example.shop.service.*.*(..))")
public void serviceLayer() {}
```

This pointcut matches every method, on every class, inside the `com.example.shop.service` package.

### Pointcut expression reference

| Designator | Matches on | Example |
|---|---|---|
| `execution()` | Method signature (return type, package, class, method name, params) | `execution(* com.example.service.*.*(..))` |
| `within()` | All methods inside a given type or package | `within(com.example.service..*)` |
| `@annotation()` | Methods annotated with a given annotation | `@annotation(com.example.LogExecutionTime)` |
| `@within()` | All methods of classes annotated with a given annotation | `@within(org.springframework.stereotype.Service)` |
| `args()` | Methods whose arguments match given types at runtime | `args(String,..)` |
| `this()` | Join points where the AOP proxy is an instance of the given type | `this(com.example.service.OrderService)` |
| `target()` | Join points where the target (real) object is an instance of the given type | `target(com.example.service.OrderService)` |
| `bean()` | Methods on beans matching a name or name pattern (Spring-specific extension) | `bean(orderService)` or `bean(*Service)` |

### Wildcard syntax cheat sheet

| Symbol | Meaning | Example |
|---|---|---|
| `*` | Any single element (return type, one package segment, class name, method name) | `execution(* *.save*(..))` — matches any method starting with `save` |
| `..` | Zero or more (packages in a path, or any arguments) | `execution(* com.example..*.*(..))` — matches all sub-packages |
| `+` | Type and its subtypes | `within(com.example.service.OrderService+)` |
| `(..)` | Any number of arguments, any type | `execution(* *.placeOrder(..))` |

A few concrete `execution()` examples, since this is the one people write most often:

```java
// Any public method, any return type, in OrderService
execution(public * com.example.service.OrderService.*(..))

// Any method named "find*" that returns anything, takes a single Long
execution(* find*(Long))

// Any method in any class under com.example.repository, any number of args
execution(* com.example.repository..*.*(..))
```

Combine pointcuts with `&&` (and), `||` (or), and `!` (not):

```java
@Pointcut("execution(* com.example.service..*(..)) && !execution(* *.toString())")
public void serviceMethodsExceptToString() {}
```

## Advice Types

**Advice** is the actual code that runs at a join point — the action the "runner" takes. Spring AOP offers five advice types, each defined with an annotation on a method inside an `@Aspect` class.

| Advice | Annotation | Runs when |
|---|---|---|
| Before | `@Before` | Before the target method runs |
| After (finally) | `@After` | After the target method completes, success or failure |
| After returning | `@AfterReturning` | Only after the target method returns successfully |
| After throwing | `@AfterThrowing` | Only after the target method throws an exception |
| Around | `@Around` | Wraps the entire call; you control if/when the target runs |

```java
@Aspect
@Component
public class OrderLoggingAspect {

    private static final Logger log = LoggerFactory.getLogger(OrderLoggingAspect.class);

    @Before("execution(* com.example.service.OrderService.placeOrder(..))")
    public void logBefore(JoinPoint jp) {
        log.info("BEFORE: about to call {}", jp.getSignature().getName());
    }

    @After("execution(* com.example.service.OrderService.placeOrder(..))")
    public void logAfter(JoinPoint jp) {
        log.info("AFTER: finished (success or failure) {}", jp.getSignature().getName());
    }

    @AfterReturning(pointcut = "execution(* com.example.service.OrderService.placeOrder(..))",
                     returning = "result")
    public void logAfterReturning(JoinPoint jp, Object result) {
        log.info("AFTER RETURNING: {} -> {}", jp.getSignature().getName(), result);
    }

    @AfterThrowing(pointcut = "execution(* com.example.service.OrderService.placeOrder(..))",
                    throwing = "ex")
    public void logAfterThrowing(JoinPoint jp, Exception ex) {
        log.error("AFTER THROWING: {} failed with {}", jp.getSignature().getName(), ex.getMessage());
    }

    @Around("execution(* com.example.service.OrderService.placeOrder(..))")
    public Object logAround(ProceedingJoinPoint pjp) throws Throwable {
        log.info("AROUND - before proceed");
        Object result = pjp.proceed();
        log.info("AROUND - after proceed");
        return result;
    }
}
```

### Execution order example

If a success path runs through `placeOrder()`, with all five advices attached, Spring executes them in this order:

```
AROUND - before proceed
BEFORE: about to call placeOrder
   >>> placeOrder() actually runs <<<
AROUND - after proceed
AFTER RETURNING: placeOrder -> null
AFTER: finished (success or failure) placeOrder
```

Key things to notice:

- `@Around` wraps *everything else* — it starts first and ends last, because the other advices execute inside its call to `proceed()`.
- `@Before` runs right before the real method.
- On success, `@AfterReturning` runs before the general `@After`.
- On failure, `@AfterThrowing` would run instead of `@AfterReturning`, still followed by `@After`.
- If an exception is thrown, order changes to: `AROUND - before proceed` → `BEFORE` → method throws → `AFTER THROWING` → `AFTER` (and the `@Around` advice must rethrow or handle the exception — if it does not call `proceed()` again or rethrow, the exception is swallowed).

## Aspects

An **aspect** is simply the module that bundles a pointcut with one or more pieces of advice. In Spring, you create one by combining `@Aspect` (from AspectJ, tells Spring "this class contains advice") with `@Component` (tells Spring "register this as a bean so I can manage it").

```java
@Aspect
@Component
public class AuditAspect {

    @Pointcut("execution(* com.example.service..*(..))")
    public void serviceLayer() {}

    @Before("serviceLayer()")
    public void audit(JoinPoint jp) {
        System.out.println("Calling: " + jp.getSignature());
    }
}
```

You must also enable AOP in your configuration (Spring Boot autoconfigures this when `spring-boot-starter-aop` is on the classpath, but it's worth knowing the explicit form):

```java
@Configuration
@EnableAspectJAutoProxy
public class AopConfig {
}
```

```xml
<!-- pom.xml dependency needed for Spring Boot -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-aop</artifactId>
</dependency>
```

### Ordering aspects with `@Order`

When multiple aspects apply to the same join point, you often need to control which one runs first — for example, a security check aspect should probably run before a logging aspect. Use `@Order` (lower number = higher priority = runs first, on the "outside"):

```java
@Aspect
@Component
@Order(1)
public class SecurityAspect {
    @Before("execution(* com.example.service..*(..))")
    public void checkPermission(JoinPoint jp) {
        // runs first
    }
}

@Aspect
@Component
@Order(2)
public class LoggingAspect {
    @Before("execution(* com.example.service..*(..))")
    public void logCall(JoinPoint jp) {
        // runs second
    }
}
```

For `@Around` advice, lower-ordered aspects wrap outer-most: `SecurityAspect` starts, calls into `LoggingAspect`, which calls into the real method, then unwinds back out.

### Glossary of AOP terms

| Term | Meaning |
|---|---|
| Aspect | A module that groups a pointcut with its advice (e.g., a `LoggingAspect` class) |
| Join point | A point in execution where an aspect could apply (in Spring: a method execution) |
| Advice | The actual code that runs at a join point (`@Before`, `@Around`, etc.) |
| Pointcut | An expression that selects which join points an advice applies to |
| Target | The real object being advised (your actual bean, e.g., the real `OrderService`) |
| Proxy | The wrapper object Spring creates around the target to intercept calls |
| Weaving | The process of linking aspects into the target code (Spring does this at runtime, via proxies) |
| Introduction | Adding new methods or fields to an existing class via an aspect (rare in Spring AOP) |

## Proxy-based AOP

Spring AOP does not modify your `.class` files or bytecode at compile time (that's what full AspectJ can do). Instead, it does **runtime weaving**: at startup, for any bean matched by a pointcut, Spring creates a **proxy** — a stand-in object that has the same type/interface as your bean, sits in front of it, and forwards calls to the real object (the **target**) after running the relevant advice.

There are two proxy mechanisms:

| Mechanism | Used when | How it works | Requirement |
|---|---|---|---|
| JDK dynamic proxy | Your bean implements at least one interface | Creates a proxy class implementing the same interface(s) at runtime | Bean must be referenced through the interface type |
| CGLIB proxy | Your bean has no interface (a plain class) | Creates a runtime *subclass* of your class that overrides its methods | Class and advised methods must not be `final`; needs a (default or accessible) constructor |

```java
// Bean has an interface -> Spring AOP uses a JDK dynamic proxy
public interface OrderService {
    void placeOrder(Order order);
}

@Service
public class OrderServiceImpl implements OrderService {
    public void placeOrder(Order order) { /* ... */ }
}
```

```java
// Bean has no interface -> Spring AOP falls back to a CGLIB subclass proxy
@Service
public class ReportService {
    public void generateReport() { /* ... */ }
}
```

Since Spring Boot 2.x, CGLIB proxying is the **default for everything**, even when interfaces exist, unless you opt out — controlled by:

```properties
# application.properties
spring.aop.proxy-target-class=true   # true = always use CGLIB (Spring Boot default)
```

### Why `final`, `private`, and `static` methods can't be advised

Both proxy strategies work by **overriding methods** (CGLIB, via subclassing) or **implementing an interface's methods** (JDK proxies) and inserting advice logic before delegating to the real object.

- `final` methods and `final` classes cannot be overridden or subclassed — CGLIB has nothing to hook into, so the proxy silently skips them (no error, advice just never runs).
- `private` methods aren't part of any interface and can't be overridden across class boundaries — invisible to both proxy types.
- `static` methods belong to the class, not to an instance, so there's no object to intercept — proxies only wrap instance calls.

```java
@Service
public class PricingService {

    // Advice WILL run: normal public instance method
    public BigDecimal calculatePrice(Order order) { ... }

    // Advice will NEVER run: final method, cannot be overridden by CGLIB
    public final BigDecimal legacyCalculate(Order order) { ... }

    // Advice will NEVER run: private, not visible to the proxy
    private BigDecimal applyDiscount(BigDecimal price) { ... }

    // Advice will NEVER run: static, no instance to proxy
    public static BigDecimal roundPrice(BigDecimal price) { ... }
}
```

### The self-invocation problem

This is the single most common Spring AOP gotcha. Advice only triggers when a call comes **through the proxy**. If a method calls another method on `this` directly, that's a plain Java call on the real object — the proxy is bypassed entirely.

```java
@Service
public class OrderService {

    @LogExecutionTime
    public void placeOrder(Order order) {
        validate(order);      // fine, different method
        save(order);          // <-- self-invocation!
    }

    @LogExecutionTime
    public void save(Order order) {
        orderRepository.save(order);
    }
}
```

When some outside caller invokes `placeOrder()`, that call goes through the proxy, and `placeOrder`'s advice runs. But inside `placeOrder`, `save(order)` is called as `this.save(order)` — it never touches the proxy, so `save`'s `@LogExecutionTime` advice **never fires**, even though it looks like it should.

**Fixes:**

1. **Inject a self-reference proxy** and call through it instead of `this`:

```java
@Service
public class OrderService {

    @Lazy
    @Autowired
    private OrderService self; // Spring injects the proxy, not the raw bean

    public void placeOrder(Order order) {
        self.save(order); // goes through the proxy -> advice runs
    }

    @LogExecutionTime
    public void save(Order order) { ... }
}
```

2. **Split the logic into two beans** so the call always crosses a proxy boundary:

```java
@Service
public class OrderService {
    private final OrderPersistence persistence;

    public OrderService(OrderPersistence persistence) {
        this.persistence = persistence;
    }

    public void placeOrder(Order order) {
        persistence.save(order); // different bean -> real proxy call
    }
}

@Service
public class OrderPersistence {
    @LogExecutionTime
    public void save(Order order) { ... }
}
```

3. **Use `AopContext.currentProxy()`** (requires `exposeProxy = true` on `@EnableAspectJAutoProxy`) — works, but is generally considered less clean than the two options above.

```java
@EnableAspectJAutoProxy(exposeProxy = true)
```

```java
public void placeOrder(Order order) {
    ((OrderService) AopContext.currentProxy()).save(order);
}
```

## Common Use Cases

Aspects shine whenever the same "wrapper" behavior needs to apply across many unrelated methods.

| Use case | What the aspect does |
|---|---|
| Logging / audit | Records who called what, with which arguments, and when |
| Timing / metrics | Measures method duration and reports it to a monitoring system |
| Retry | Re-invokes a method automatically after a transient failure |
| Caching | Skips the method body and returns a cached value if available (this is how `@Cacheable` works) |
| Security checks | Verifies the caller is authorized before letting a method run |
| Transactions | Opens/commits/rolls back a transaction around a method (how `@Transactional` works) |

### Worked example: a full `@Around` timing aspect

```java
@Aspect
@Component
@Order(10)
public class TimingAspect {

    private static final Logger log = LoggerFactory.getLogger(TimingAspect.class);

    @Around("execution(* com.example.service..*(..))")
    public Object timeMethod(ProceedingJoinPoint pjp) throws Throwable {
        String methodName = pjp.getSignature().toShortString();
        long start = System.nanoTime();

        try {
            Object result = pjp.proceed();       // MUST call this to run the real method
            long durationMs = (System.nanoTime() - start) / 1_000_000;
            log.info("{} completed in {} ms", methodName, durationMs);
            return result;                       // MUST return the value proceed() gave back
        } catch (Throwable ex) {
            long durationMs = (System.nanoTime() - start) / 1_000_000;
            log.warn("{} failed after {} ms: {}", methodName, durationMs, ex.getMessage());
            throw ex;                            // MUST rethrow, don't swallow it
        }
    }
}
```

Walking through this:

- `ProceedingJoinPoint` is the special version of `JoinPoint` given only to `@Around` advice — it has a `proceed()` method that actually invokes the real target method (or the next aspect in the chain).
- Timing is captured both before and after `proceed()`, so it covers exactly the method's execution time.
- The `catch` block still measures and logs, but **rethrows** — an aspect that swallows exceptions turns real failures into silent no-ops, which is a serious bug.
- The method's return value (`result`) is passed straight back — forgetting this is a classic mistake (see pitfalls).

## Custom Annotations

Rather than writing a pointcut expression tied to package names (which breaks the moment you refactor), you can define your own marker annotation and match on it with `@annotation()`. This keeps the "what gets advised" decision next to the method itself, and is exactly how `@Transactional` and `@Cacheable` work internally.

### Step 1 — define the annotation

```java
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Retention(RetentionPolicy.RUNTIME) // must be RUNTIME so Spring can see it via reflection
@Target(ElementType.METHOD)
public @interface LogExecutionTime {
    String label() default ""; // an optional attribute we can read inside the advice
}
```

### Step 2 — use it on any method, anywhere

```java
@Service
public class OrderService {

    @LogExecutionTime(label = "order-placement")
    public void placeOrder(Order order) {
        // business logic
    }
}
```

### Step 3 — bind an aspect to it with `@annotation()`

```java
@Aspect
@Component
public class LogExecutionTimeAspect {

    private static final Logger log = LoggerFactory.getLogger(LogExecutionTimeAspect.class);

    @Around("@annotation(logExecutionTime)")
    public Object around(ProceedingJoinPoint pjp, LogExecutionTime logExecutionTime) throws Throwable {
        String label = logExecutionTime.label().isEmpty()
                ? pjp.getSignature().toShortString()
                : logExecutionTime.label();

        long start = System.nanoTime();
        Object result = pjp.proceed();
        long durationMs = (System.nanoTime() - start) / 1_000_000;

        log.info("[{}] took {} ms", label, durationMs);
        return result;
    }
}
```

Notice the parameter name `logExecutionTime` in `@Around("@annotation(logExecutionTime)")` matches the method parameter `LogExecutionTime logExecutionTime` — Spring AOP uses this name binding to pass the actual annotation instance (with its real attribute values) straight into your advice method. This is how you read annotation attributes like `label()` at runtime.

Now every method annotated `@LogExecutionTime` gets timed automatically, with zero coupling to package names or class hierarchies.

## Common Code Review / Interview Pitfalls

- **Self-invocation bypassing the proxy.** Calling another `@Aspect`-advised method via `this.method()` inside the same class skips the proxy entirely, so the advice silently never runs.
  ❌ `this.save(order);` (inside the same bean)
  ✅ Inject the bean into itself (`@Lazy` self-reference) or move the method to another bean.

- **Forgetting to return `proceed()`'s value in `@Around`.** If your advice calls `pjp.proceed()` but doesn't return the result, every advised method effectively starts returning `null`.
  ❌
  ```java
  @Around("...")
  public void around(ProceedingJoinPoint pjp) throws Throwable {
      pjp.proceed(); // return value discarded!
  }
  ```
  ✅
  ```java
  @Around("...")
  public Object around(ProceedingJoinPoint pjp) throws Throwable {
      return pjp.proceed();
  }
  ```

- **Swallowing exceptions in advice.** Catching an exception in `@Around` or `@AfterThrowing` and not rethrowing turns a real failure into a silent success, hiding bugs from callers and monitoring.
  ❌ `catch (Exception e) { log.error("failed", e); }` (no rethrow)
  ✅ `catch (Exception e) { log.error("failed", e); throw e; }`

- **Forgetting `@Around` must call `proceed()` at all.** If you never call it, the target method never runs — easy to miss when an aspect has an early `return` for some condition.

- **Over-broad pointcuts hurting performance and startup time.** A pointcut like `execution(* com.example..*(..))` matches thousands of methods across the whole codebase, forcing Spring to proxy far more beans than necessary and slowing container startup and every call through unrelated advice.
  ❌ `execution(* com.example..*(..))`
  ✅ `execution(* com.example.service..*(..)) && @annotation(com.example.Audited)` — scope it to what actually needs it.

- **Trying to advise `final` classes or methods.** CGLIB proxies work by subclassing; a `final` class can't be subclassed and a `final` method can't be overridden, so the advice is quietly never applied — no error, just a no-op.
  ❌ `public final class PricingService { ... }`
  ✅ Remove `final`, or restructure so the advised logic lives in a non-final class/interface.

- **Logging sensitive arguments.** A generic logging aspect that dumps `joinPoint.getArgs()` can accidentally log passwords, tokens, credit card numbers, or PII into application logs.
  ❌ `log.info("Called with args: {}", Arrays.toString(jp.getArgs()));`
  ✅ Log method names and IDs only, or explicitly redact/allow-list which fields get logged.

- **Aspect ordering surprises with `@Transactional`.** `@Transactional` is itself implemented as AOP advice. If your custom aspect runs *inside* the transactional advice (wrong `@Order`), your logic may execute outside the transaction boundary you assumed — e.g., a retry aspect retrying after the transaction already committed or rolled back.
  ✅ Use explicit `@Order` values and think about whether your aspect should wrap the transaction (run outside it) or be wrapped by it (run inside it).

- **Assuming Spring AOP can intercept private/static/constructor calls.** Spring AOP only proxies public instance methods on Spring beans. Anything else (private helpers, static utilities, constructors, field access) is invisible to it — full AspectJ (compile-time/load-time weaving) would be needed for those.

- **Using AOP where a plain decorator or interface would be clearer.** For a single, simple wrapping need (one method, one caller), a manual decorator or wrapper class is often easier to read and debug than an aspect with a pointcut expression, since aspects apply implicitly and can surprise future readers.
  ❌ Writing a whole `@Aspect` just to add a try/catch around one specific method call in one place.
  ✅ Just wrap that one call directly in code; reserve AOP for genuinely cross-cutting, repeated concerns.

- **Not adding `spring-boot-starter-aop` (or forgetting `@EnableAspectJAutoProxy` in non-Boot apps).** Aspect classes are annotated correctly but nothing happens because AOP auto-proxying was never enabled.

- **Multiple pointcuts matching the same method with conflicting behavior.** Two unrelated teams each add an aspect matching `execution(* com.example.service..*(..))`; combined, their advice order becomes hard to reason about. Keep pointcuts as narrow and explicit (ideally annotation-driven) as possible.

- **Testing an `@Autowired` field directly instead of through the proxy.** In tests, injecting the raw target bean (e.g., via reflection or `new`) instead of the Spring-managed proxy means the advice never runs in the test, giving false confidence.

## Quick Recap

- AOP separates cross-cutting concerns (logging, security, metrics, transactions) from core business logic.
- A **join point** is where advice could apply — in Spring, always a method execution on a bean.
- A **pointcut** is the expression that selects which join points get advice; `execution()`, `within()`, `@annotation()`, and `bean()` are the most common designators.
- **Advice** is the code that runs: `@Before`, `@After`, `@AfterReturning`, `@AfterThrowing`, `@Around` — with `@Around` wrapping everything else.
- An **aspect** = `@Aspect` + `@Component`, bundling a pointcut with its advice; use `@Order` to control precedence between aspects.
- Spring AOP works via runtime **proxies**: JDK dynamic proxies for interfaces, CGLIB subclass proxies otherwise (CGLIB is the Boot default).
- `final`, `private`, and `static` methods can't be advised because proxies work by overriding/implementing methods on an instance.
- **Self-invocation** (calling another advised method via `this`) bypasses the proxy — fix with a self-injected reference, splitting into two beans, or `AopContext.currentProxy()`.
- Custom annotations (e.g., `@LogExecutionTime`) bound via `@annotation()` are the cleanest way to mark "this method needs cross-cutting behavior" without coupling to package structure.
- Always return `proceed()`'s result in `@Around`, always rethrow caught exceptions in advice, and keep pointcuts as narrow as the use case actually requires.
