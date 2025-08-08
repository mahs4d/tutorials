# 17. Scheduling & Async

## Overview

Some work does not need to happen while a user is waiting on an HTTP response. A nightly report, a cleanup job, or an email notification can run in the background or on a timer. Spring Boot gives you two lightweight tools for this: **scheduling** (run a method automatically at fixed times or intervals) and **async processing** (run a method on a separate thread so the caller does not block). Both are built on top of Java's thread-pooling and proxy mechanisms, so understanding how Spring wires them up helps you avoid subtle bugs like silently-dropped exceptions or jobs that never actually run in parallel. This chapter walks through `@Scheduled`, cron expressions, `@Async`, executor configuration, and how to combine async results with `CompletableFuture`.

## Scheduling

Scheduling means telling Spring "run this method automatically, on a timer, without anyone calling it directly." Spring Boot has a built-in scheduler for this — you don't need Quartz or an external cron daemon for simple cases.

To turn scheduling on, add `@EnableScheduling` to a configuration class:

```java
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

@Configuration
@EnableScheduling
public class SchedulingConfig {
}
```

Without `@EnableScheduling`, every `@Scheduled` annotation in your app is silently ignored — no error, the method just never runs. This is a very common "why isn't my job firing?" bug.

### @Scheduled

`@Scheduled` goes directly on a method. The method must return `void` and normally takes no arguments.

```java
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class ReportJob {

    @Scheduled(fixedRate = 5000)
    public void runEveryFiveSeconds() {
        System.out.println("Tick at " + System.currentTimeMillis());
    }
}
```

There are three main ways to control timing:

| Attribute | Meaning | Example |
|---|---|---|
| `fixedRate` | Start a new run every N ms, measured from the **start** of the previous run | `@Scheduled(fixedRate = 5000)` |
| `fixedDelay` | Start a new run N ms after the **previous run finished** | `@Scheduled(fixedDelay = 5000)` |
| `cron` | Run at specific calendar times (like "every day at 2am") | `@Scheduled(cron = "0 0 2 * * *")` |

The difference between `fixedRate` and `fixedDelay` matters a lot if the job itself is slow:

```
fixedRate = 1000ms, job takes 300ms:
|--run1(300ms)--|.......|--run2(300ms)--|.......|--run3--|
0              1000    1300           2000     2300     3000
(next run scheduled 1000ms after previous START)

fixedRate = 1000ms, job takes 1500ms (slower than the rate):
|--run1 (1500ms, overruns)--|--run2 starts immediately--|--run3 immediately--|
0                          1500                        3000                4500
(runs back-to-back, no gap — Spring does NOT run them concurrently by default)

fixedDelay = 1000ms, job takes 300ms:
|--run1(300ms)--|  gap 1000ms  |--run2(300ms)--|  gap 1000ms  |--run3--|
0              300            1300           1600           2600
(next run always starts 1000ms after previous FINISHED)
```

Key point: by default Spring's scheduler is **single-threaded**, so even with `fixedRate`, two runs of the *same* task never overlap — if a run takes longer than the rate, the next run simply starts right after the previous one ends instead of waiting for the full interval.

Other useful attributes:

```java
@Component
public class BillingJob {

    // wait 10s after startup before the first run, then every 60s
    @Scheduled(initialDelay = 10000, fixedRate = 60000)
    public void generateInvoices() {
        // ...
    }

    // read the rate from application.properties, with a default fallback
    @Scheduled(fixedRateString = "${billing.job.rate-ms:60000}")
    public void generateInvoicesConfigurable() {
        // ...
    }

    // run at 2am every day, interpreted in a specific timezone
    @Scheduled(cron = "0 0 2 * * *", zone = "Europe/Amsterdam")
    public void nightlyCleanup() {
        // ...
    }
}
```

```properties
# application.properties
billing.job.rate-ms=30000
```

- `initialDelay` — wait this long after the application starts before the first execution.
- `fixedRateString` / `fixedDelayString` — same as `fixedRate`/`fixedDelay` but accept a `${...}` placeholder so the value comes from configuration instead of being hardcoded.
- `zone` — the timezone used to evaluate a `cron` expression. If omitted, the server's local timezone is used, which is a common source of bugs (see the pitfalls section).

## Cron Expressions

A **cron expression** is a compact string describing a recurring schedule, like "every weekday at 9am." Spring's cron format has **6 fields** (Unix cron has only 5 — it lacks the seconds field). This is a classic interview trap: pasting a Unix crontab line straight into `@Scheduled(cron = ...)` shifts every field by one and produces the wrong schedule.

| Field | Allowed values | Allowed special characters |
|---|---|---|
| Second | 0-59 | `, - * /` |
| Minute | 0-59 | `, - * /` |
| Hour | 0-23 | `, - * /` |
| Day of month | 1-31 | `, - * ? / L` |
| Month | 1-12 or JAN-DEC | `, - * /` |
| Day of week | 0-7 or SUN-SAT (0 and 7 both = Sunday) | `, - * ? / L #` |

Special characters:

| Character | Meaning |
|---|---|
| `*` | "every" value in this field |
| `?` | "no specific value" — used in day-of-month or day-of-week when the other one is set, since only one of the two can be restrictive |
| `/` | step values, e.g. `0/15` in minutes means "every 15 minutes starting at 0" |
| `-` | a range, e.g. `9-17` in hours means "9 through 17" |
| `,` | a list, e.g. `MON,WED,FRI` |
| `L` | "last" — last day of the month, or last given weekday of the month |
| `#` | the nth weekday of the month, e.g. `MON#2` means "the second Monday" |

Spring also supports convenient macros as a shortcut for common schedules:

| Macro | Equivalent to |
|---|---|
| `@yearly` (or `@annually`) | `0 0 0 1 1 *` |
| `@monthly` | `0 0 0 1 * *` |
| `@weekly` | `0 0 0 * * 0` |
| `@daily` (or `@midnight`) | `0 0 0 * * *` |
| `@hourly` | `0 0 * * * *` |

```java
@Scheduled(cron = "@daily")
public void midnightJob() { /* ... */ }
```

Worked examples (format is `second minute hour day-of-month month day-of-week`):

| Cron expression | Plain-English meaning |
|---|---|
| `0 0 2 * * *` | Every day at 2:00:00 AM |
| `0 30 9 * * MON-FRI` | 9:30 AM every weekday |
| `0 0/15 * * * *` | Every 15 minutes, on the hour, :15, :30, :45 |
| `0 0 0 1 * *` | Midnight on the 1st of every month |
| `0 0 12 * * ?` | Noon every day (day-of-week left unspecified with `?`) |
| `0 0 8 ? * MON#1` | 8:00 AM on the first Monday of every month |
| `0 0 22 L * ?` | 10:00 PM on the last day of every month |
| `0 0/5 9-17 * * MON-FRI` | Every 5 minutes, between 9 AM and 5 PM, Monday to Friday |
| `0 0 0 25 12 *` | Midnight every December 25th |
| `0 15 10 * * SUN` | 10:15 AM every Sunday |

## Async Processing

Async processing means: instead of running a method on the caller's thread and making the caller wait for it to finish, Spring hands the work off to a different thread and lets the caller continue immediately. This is useful for slow operations (sending emails, calling a third-party API, writing an audit log) that don't need to block the main request-response flow.

Enable it once with `@EnableAsync`:

```java
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;

@Configuration
@EnableAsync
public class AsyncConfig {
}
```

### @Async

Put `@Async` on any Spring-managed bean method to make it run on a background thread.

```java
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Future;

@Service
public class NotificationService {

    // fire-and-forget: caller does not wait, and cannot know if/when it finished
    @Async
    public void sendWelcomeEmail(String userEmail) {
        // ... slow email call ...
    }

    // caller can block on the result later, but exceptions are wrapped
    @Async
    public Future<Boolean> sendWithFutureResult(String userEmail) {
        boolean sent = trySend(userEmail);
        return new AsyncResult<>(sent);
    }

    // preferred modern style: composable, non-blocking callbacks
    @Async
    public CompletableFuture<Boolean> sendWithCompletableFuture(String userEmail) {
        boolean sent = trySend(userEmail);
        return CompletableFuture.completedFuture(sent);
    }

    private boolean trySend(String userEmail) {
        return true;
    }
}
```

Return type choices:

| Return type | Caller can get the result? | Caller can chain/compose? | Notes |
|---|---|---|---|
| `void` | No | No | Fire-and-forget. Exceptions never reach the caller. |
| `Future<T>` | Yes, via blocking `get()` | Limited | Older API, from `java.util.concurrent`. |
| `CompletableFuture<T>` | Yes, blocking or non-blocking | Yes — `thenApply`, `thenCombine`, etc. | Recommended for new code. |

How `@Async` works under the hood: Spring wraps the bean in a **proxy**. When some *other* bean calls `myService.sendWelcomeEmail(...)`, the call goes through the proxy first, which schedules the real method on a thread pool. But if a method **inside the same class** calls `this.sendWelcomeEmail(...)` (self-invocation), it bypasses the proxy entirely and runs synchronously on the caller's thread — `@Async` is silently ignored. This is exactly the same proxy limitation that affects `@Transactional`.

```java
@Service
public class ReportService {

    public void generateAll() {
        // BUG: calls the real method directly, not through the proxy —
        // sendReport() runs synchronously here, @Async has no effect
        sendReport();
    }

    @Async
    public void sendReport() {
        // ...
    }
}
```

Exception handling for `void` async methods needs special wiring, because there is no caller to throw the exception back to. Spring lets you register an `AsyncUncaughtExceptionHandler`:

```java
import org.springframework.aop.interceptor.AsyncUncaughtExceptionHandler;
import org.springframework.scheduling.annotation.AsyncConfigurer;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import java.lang.reflect.Method;

@Configuration
@EnableAsync
public class AsyncConfig implements AsyncConfigurer {

    @Override
    public AsyncUncaughtExceptionHandler getAsyncUncaughtExceptionHandler() {
        return (Throwable ex, Method method, Object... params) -> {
            System.err.printf(
                "Async method '%s' failed with parameters %s: %s%n",
                method.getName(), params, ex.getMessage());
            // send to logging/monitoring here
        };
    }
}
```

Without this, an exception thrown inside a `void @Async` method is logged by Spring at best, but the caller never finds out anything went wrong.

## Async Executors

By default, `@Async` methods run on Spring's built-in executor, which in older Spring versions falls back to `SimpleAsyncTaskExecutor` — a naive executor that creates a **new thread per task with no pooling and no upper bound**. In production this can exhaust system resources under load. The fix is to define your own `ThreadPoolTaskExecutor` bean.

```java
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import java.util.concurrent.ThreadPoolExecutor;

@Configuration
@EnableAsync
public class ExecutorConfig {

    @Bean(name = "emailExecutor")
    public ThreadPoolTaskExecutor emailExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(4);
        executor.setMaxPoolSize(10);
        executor.setQueueCapacity(50);
        executor.setThreadNamePrefix("email-exec-");
        executor.setRejectedExecutionHandler(new ThreadPoolExecutor.CallerRunsPolicy());
        executor.initialize();
        return executor;
    }
}
```

How the pool sizing knobs interact:

- **Core pool size**: threads kept alive even when idle. New tasks use one of these first.
- **Queue capacity**: once all core threads are busy, new tasks wait here instead of spawning a thread.
- **Max pool size**: only once the queue is **full** does the executor create extra threads, up to this maximum.
- If the queue is full *and* the pool is at max size, the **rejection policy** decides what happens to the next submitted task.

| Rejection policy | Behavior |
|---|---|
| `AbortPolicy` (default) | Throws `RejectedExecutionException` |
| `CallerRunsPolicy` | Runs the task on the *caller's* thread, slowing the caller down but not losing the task |
| `DiscardPolicy` | Silently drops the task |
| `DiscardOldestPolicy` | Drops the oldest queued task, then tries to enqueue the new one |

Reference a specific executor by name on the `@Async` annotation:

```java
@Async("emailExecutor")
public void sendWelcomeEmail(String userEmail) {
    // ...
}
```

If you don't want to define a `ThreadPoolTaskExecutor` bean manually, Spring Boot 3.x auto-configures one from properties:

```properties
spring.task.execution.pool.core-size=8
spring.task.execution.pool.max-size=20
spring.task.execution.pool.queue-capacity=100
spring.task.execution.thread-name-prefix=async-task-
spring.task.scheduling.pool.size=5
```

`spring.task.execution.*` configures the executor used for `@Async`. `spring.task.scheduling.*` configures the (separate) thread pool used for `@Scheduled` — by default the scheduler only has a **single thread**, so multiple `@Scheduled` jobs can block each other unless you bump `spring.task.scheduling.pool.size`.

Spring Boot 3.2 introduced first-class support for **virtual threads** (Project Loom, Java 21+). Virtual threads are lightweight threads managed by the JVM rather than the OS, so you can have thousands of them without exhausting memory — ideal for I/O-bound async work.

```properties
spring.threads.virtual.enabled=true
```

With this enabled, Spring Boot uses a virtual-thread-backed executor for `@Async` (and for web request handling) instead of a fixed-size platform thread pool. You still write the same `@Async` code — no API changes — but blocking I/O calls inside async methods become much cheaper. Note virtual threads need Java 21; on Java 17 this property has no effect.

## CompletableFuture Integration

`CompletableFuture<T>` is the modern way to compose asynchronous results without deeply nested callbacks or manual blocking. It lets you chain transformations, combine multiple async calls, and add timeouts.

```java
import java.util.concurrent.CompletableFuture;
import java.util.List;

@Service
public class PricingService {

    @Async
    public CompletableFuture<Double> getBasePrice(String sku) {
        return CompletableFuture.completedFuture(42.0);
    }

    @Async
    public CompletableFuture<Double> getDiscount(String sku) {
        return CompletableFuture.completedFuture(5.0);
    }

    public CompletableFuture<Double> getFinalPrice(String sku) {
        CompletableFuture<Double> base = getBasePrice(sku);
        CompletableFuture<Double> discount = getDiscount(sku);

        // combine two independent async results once BOTH complete
        return base.thenCombine(discount, (basePrice, disc) -> basePrice - disc)
                    // transform the combined result further
                    .thenApply(price -> Math.max(price, 0.0));
    }

    public CompletableFuture<List<Double>> getAllPrices(List<String> skus) {
        List<CompletableFuture<Double>> futures = skus.stream()
            .map(this::getBasePrice)
            .toList();

        // wait for ALL futures to finish, then collect results
        return CompletableFuture.allOf(futures.toArray(new CompletableFuture[0]))
            .thenApply(v -> futures.stream().map(CompletableFuture::join).toList());
    }
}
```

Adding a timeout so a slow downstream call cannot hang forever (Java 9+):

```java
CompletableFuture<Double> priceWithTimeout = pricingService.getBasePrice("SKU-1")
        .orTimeout(2, TimeUnit.SECONDS)
        .exceptionally(ex -> {
            // fallback value if it times out or fails
            return 0.0;
        });
```

Blocking with a timeout on the caller's side instead:

```java
try {
    Double price = pricingService.getBasePrice("SKU-1").get(2, TimeUnit.SECONDS);
} catch (TimeoutException e) {
    // handle slow downstream service
}
```

**Context propagation**: thread-local state like Spring Security's `SecurityContext` or a logging `MDC` (Mapped Diagnostic Context, used to tag log lines with a request ID) lives on the *calling* thread. When `@Async` switches to a worker thread, that state does not automatically follow. A `TaskDecorator` lets you copy it over manually:

```java
import org.springframework.core.task.TaskDecorator;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.slf4j.MDC;
import java.util.Map;

public class ContextCopyingDecorator implements TaskDecorator {

    @Override
    public Runnable decorate(Runnable runnable) {
        SecurityContext securityContext = SecurityContextHolder.getContext();
        Map<String, String> mdcContext = MDC.getCopyOfContextMap();

        return () -> {
            try {
                SecurityContextHolder.setContext(securityContext);
                if (mdcContext != null) {
                    MDC.setContextMap(mdcContext);
                }
                runnable.run();
            } finally {
                SecurityContextHolder.clearContext();
                MDC.clear();
            }
        };
    }
}
```

```java
@Bean(name = "emailExecutor")
public ThreadPoolTaskExecutor emailExecutor() {
    ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
    executor.setCorePoolSize(4);
    executor.setMaxPoolSize(10);
    executor.setQueueCapacity(50);
    executor.setTaskDecorator(new ContextCopyingDecorator());
    executor.initialize();
    return executor;
}
```

## Common Code Review / Interview Pitfalls

- **Self-invocation on `@Async`/`@Scheduled`.** Spring's annotations rely on a proxy, and calling a method on `this` from inside the same class skips the proxy — the method runs synchronously.
  ❌ `public void doWork() { this.asyncStep(); }`
  ✅ Move `asyncStep()` into a separate bean and inject it, or call it through the proxy via `AopContext.currentProxy()`.

- **`@Async` on a private method.** Proxies work by subclassing/wrapping public (or at least non-private) methods; a private method can't be intercepted, so `@Async` is silently ignored.
  ❌ `@Async private void sendEmail() { ... }`
  ✅ `@Async public void sendEmail() { ... }`

- **Default executor is unbounded (pre-Spring-Boot-3.2 without a configured pool).** `SimpleAsyncTaskExecutor` spins up a brand-new OS thread per task with no cap, which can exhaust memory/threads under load.
  ❌ Relying on the default executor in production.
  ✅ Define a `ThreadPoolTaskExecutor` bean with explicit core/max size and queue capacity, or enable virtual threads on Java 21+.

- **Swallowed exceptions from `void @Async` methods.** There is no caller to propagate the exception to, so by default it just gets logged (or lost) and nobody is alerted.
  ❌ `@Async void process() { throw new RuntimeException("boom"); }` with no handler configured.
  ✅ Register an `AsyncUncaughtExceptionHandler`, or return `CompletableFuture<T>` and call `.exceptionally(...)`.

- **Single-threaded default scheduler blocking other jobs.** `@Scheduled` methods share one thread by default; a slow job delays every other scheduled job in the app.
  ❌ Ten `@Scheduled` jobs, no scheduler pool configured.
  ✅ Set `spring.task.scheduling.pool.size` to a value greater than 1, sized to your number of jobs.

- **Scheduled jobs running on every instance in a multi-instance deployment.** If you scale to 3 pods, a naive `@Scheduled` job runs 3 times instead of once — duplicate emails, duplicate charges, etc.
  ❌ A billing job with `@Scheduled(cron = "0 0 2 * * *")` deployed across multiple replicas with no coordination.
  ✅ Use a distributed lock (e.g. ShedLock) or leader election so only one instance actually executes the job body.

- **`@Transactional` + `@Async` on the same method.** Both are proxy-based AOP; stacking them on one method is confusing and the transaction boundary often does not behave as expected because the async call already happens on a different thread than the one that opened the transaction.
  ❌ `@Async @Transactional public void save() { ... }`
  ✅ Keep the `@Async` entry point separate from the `@Transactional` business method it calls, and reason carefully about which thread owns the transaction.

- **Unbounded queues causing OOM.** Setting `queueCapacity` to `Integer.MAX_VALUE` (or leaving it unset with an unbounded default) means the max pool size is never reached — tasks just pile up in memory forever under sustained load.
  ❌ `executor.setQueueCapacity(Integer.MAX_VALUE);`
  ✅ Pick a bounded queue size and a sensible rejection policy (e.g. `CallerRunsPolicy`) so overload applies backpressure instead of crashing.

- **No timeout on `future.get()`.** A plain `get()` blocks forever if the async task hangs, tying up the calling thread indefinitely.
  ❌ `Double price = future.get();`
  ✅ `Double price = future.get(2, TimeUnit.SECONDS);` or use `orTimeout(...)` on a `CompletableFuture`.

- **Losing `SecurityContext`/MDC in async threads.** Thread-local state does not cross over to the worker thread, so async code may see "no authenticated user" or logs missing the request/correlation ID.
  ❌ Assuming `SecurityContextHolder.getContext()` inside an `@Async` method still holds the caller's user.
  ✅ Attach a `TaskDecorator` to the executor that copies `SecurityContext` and `MDC` onto the worker thread before running the task.

- **Cron expressions evaluated in server-local timezone.** If servers run in UTC but the business expects "2am local time," a cron job without an explicit `zone` fires at the wrong wall-clock time for the business — and can silently shift after a timezone or daylight-saving change.
  ❌ `@Scheduled(cron = "0 0 2 * * *")` on a UTC server, when the business means 2am in `Europe/Amsterdam`.
  ✅ `@Scheduled(cron = "0 0 2 * * *", zone = "Europe/Amsterdam")` — always be explicit about the zone.

- **Confusing Spring's 6-field cron with Unix's 5-field cron.** Copy-pasting a Unix crontab line directly into `@Scheduled(cron = ...)` shifts every field by one position, since Spring adds a leading "seconds" field.
  ❌ `@Scheduled(cron = "0 2 * * *")` (this is a valid Unix line meaning "2:00 AM," but as a Spring expression it means "second 0, minute 2" — i.e. every hour at hh:02:00).
  ✅ `@Scheduled(cron = "0 0 2 * * *")` — remember to prepend the seconds field.

- **Forgetting `@EnableScheduling` or `@EnableAsync`.** The annotated methods compile fine and look correct, but nothing ever runs (or runs synchronously) because the underlying infrastructure was never switched on.
  ❌ `@Scheduled` methods with no `@EnableScheduling` anywhere in the app.
  ✅ Add `@EnableScheduling` (and/or `@EnableAsync`) to a `@Configuration` class.

## Quick Recap

- `@EnableScheduling` turns on `@Scheduled`; `@EnableAsync` turns on `@Async`. Forgetting either means the annotation is silently ignored.
- `fixedRate` times from the start of the previous run; `fixedDelay` times from the end of the previous run; `cron` fires at specific calendar times.
- Spring's cron format has **6 fields** (seconds first) — different from Unix's 5-field format.
- Macros like `@daily`, `@hourly` are shorthand for common cron patterns.
- `@Async` methods should return `void` (fire-and-forget, exceptions lost), `Future<T>`, or ideally `CompletableFuture<T>` (composable, non-blocking).
- Both `@Async` and `@Scheduled` are proxy-based — self-invocation bypasses the proxy and runs synchronously; the annotated method must be public.
- Always configure a bounded `ThreadPoolTaskExecutor` (core size, max size, queue capacity, rejection policy) instead of relying on defaults.
- `spring.task.execution.*` configures the async executor; `spring.task.scheduling.*` configures the scheduler's thread pool (default is a single thread).
- Spring Boot 3.2+ supports `spring.threads.virtual.enabled=true` for lightweight virtual threads on Java 21+.
- Use `AsyncUncaughtExceptionHandler` so failures in `void @Async` methods aren't silently swallowed.
- Combine async results with `thenApply`, `thenCombine`, and `allOf`; always add a timeout (`orTimeout` or `get(timeout, unit)`).
- Use a `TaskDecorator` to carry `SecurityContext` and MDC across to worker threads.
- In multi-instance deployments, coordinate scheduled jobs (e.g. with ShedLock or leader election) so they don't run once per instance.
- Always set an explicit `zone` on cron schedules to avoid timezone surprises.
