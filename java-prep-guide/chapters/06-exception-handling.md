# 6. Exception Handling

Things go wrong at runtime: files are missing, networks time out, arrays get the wrong index. Java uses **exceptions** to signal that something went wrong, separate from your normal return values. This chapter explains how exceptions work under the hood, how to handle them correctly, and the mistakes that reviewers flag most often in real code review. We target Java 21+ throughout.

## Table of Contents

- [Exceptions](#exceptions)
- [Checked vs Unchecked Exceptions](#checked-vs-unchecked-exceptions)
- [Exception Handling Best Practices](#exception-handling-best-practices)
- [Try-with-Resources](#try-with-resources)
- [Assertions](#assertions)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Exceptions

An **exception** is an object that describes an unexpected event. When something goes wrong, Java **throws** an exception object. If nothing handles it, the program (or thread) stops and prints a **stack trace**. If code **catches** it, the program can recover or fail gracefully instead of crashing.

```java
public class Divider {
    public static int divide(int a, int b) {
        return a / b; // throws ArithmeticException if b == 0
    }

    public static void main(String[] args) {
        try {
            System.out.println(divide(10, 0));
        } catch (ArithmeticException e) {
            System.out.println("Cannot divide by zero: " + e.getMessage());
        }
    }
}
```

### The Throwable Hierarchy

Every exception in Java is a subclass of `Throwable`. There are three important branches: `Error`, `Exception`, and inside `Exception`, `RuntimeException`.

```
Throwable
├── Error                          (serious, usually unrecoverable JVM problems)
│   ├── OutOfMemoryError
│   ├── StackOverflowError
│   └── VirtualMachineError
│
└── Exception                      (things application code can reasonably handle)
    ├── IOException                (checked)
    ├── SQLException                (checked)
    ├── InterruptedException        (checked)
    │
    └── RuntimeException            (unchecked)
        ├── NullPointerException
        ├── IllegalArgumentException
        │     └── NumberFormatException
        ├── IllegalStateException
        ├── IndexOutOfBoundsException
        │     ├── ArrayIndexOutOfBoundsException
        │     └── StringIndexOutOfBoundsException
        ├── ClassCastException
        ├── ArithmeticException
        └── UnsupportedOperationException
```

- `Error` — things like `OutOfMemoryError` or `StackOverflowError`. These signal that the JVM itself is in trouble. Application code almost never catches these, because there is usually nothing useful you can do.
- `Exception` — problems your application should anticipate and possibly recover from, such as a missing file.
- `RuntimeException` — a subclass of `Exception` for programming errors, like calling a method on `null`. These are **unchecked** (explained below).

`Throwable` itself is rarely used directly. Catching `Throwable` is even broader than catching `Exception` — it also captures `Error`, which is almost never what you want.

```java
// Avoid: this also swallows OutOfMemoryError and StackOverflowError
try {
    riskyOperation();
} catch (Throwable t) {
    log.error("Something went wrong", t);
}
```

### Stack Traces and How to Read Them

A **stack trace** is a printed list of method calls that were active when the exception was created. It reads top to bottom: the top line is where the exception happened, and each line below is "called from here."

```java
public class Chain {
    public static void main(String[] args) {
        step1();
    }
    static void step1() { step2(); }
    static void step2() { step3(); }
    static void step3() { throw new IllegalStateException("boom"); }
}
```

Output:

```
Exception in thread "main" java.lang.IllegalStateException: boom
	at Chain.step3(Chain.java:7)
	at Chain.step2(Chain.java:6)
	at Chain.step1(Chain.java:5)
	at Chain.main(Chain.java:2)
```

How to read it:

1. **First line** — exception class and message. This tells you *what* went wrong.
2. **`at` lines** — the call stack at the moment of the throw, most recent call first. `Chain.step3(Chain.java:7)` means line 7 of `Chain.java`, inside method `step3`.
3. **Caused by** — if present, a chained exception (see below). Always read the *innermost* "Caused by" first; that is usually the real root cause.
4. **`... N more`** — Java collapses stack frames that are identical to a previous trace, to save space, when printing a chained exception.

```java
try {
    loadConfig();
} catch (IOException e) {
    throw new RuntimeException("Failed to start application", e);
}
```

```
Exception in thread "main" java.lang.RuntimeException: Failed to start application
	at App.main(App.java:12)
Caused by: java.io.FileNotFoundException: config.yml (No such file or directory)
	at java.base/java.io.FileInputStream.open0(Native Method)
	... 5 more
```

## Checked vs Unchecked Exceptions

Java splits exceptions into two categories based on whether the compiler forces you to deal with them.

| | Checked | Unchecked |
|---|---|---|
| Base class | `Exception` (not `RuntimeException`) | `RuntimeException` or `Error` |
| Compiler enforcement | Must be caught or declared with `throws` | No compiler enforcement |
| Typical cause | External conditions (file missing, network down) | Programming bugs (null access, bad argument) |
| Examples | `IOException`, `SQLException`, `InterruptedException` | `NullPointerException`, `IllegalArgumentException`, `IndexOutOfBoundsException` |
| Should caller recover? | Often yes — retry, fallback, ask user again | Usually no — fix the bug instead |

```java
import java.io.*;

public class CheckedExample {
    // Checked exception: caller MUST catch it or declare "throws IOException"
    public static String readFirstLine(String path) throws IOException {
        try (BufferedReader reader = new BufferedReader(new FileReader(path))) {
            return reader.readLine();
        }
    }
}
```

```java
public class UncheckedExample {
    // Unchecked: compiler does not force the caller to do anything
    public static int parsePositive(String value) {
        int n = Integer.parseInt(value);       // may throw NumberFormatException
        if (n < 0) {
            throw new IllegalArgumentException("Value must be positive: " + n);
        }
        return n;
    }
}
```

### When to Use Which

| Situation | Use |
|---|---|
| Caller can realistically recover (retry, prompt again, use a default) | Checked exception |
| The failure is a bug in the calling code (bad argument, wrong state) | Unchecked exception |
| Library/API boundary where you want to force callers to think about failure | Checked exception |
| Internal code where wrapping every call in `try/catch` would just add noise | Unchecked exception |

Modern Java style (post Java 8, especially with streams and lambdas) leans toward **unchecked** exceptions, because checked exceptions do not compose well with functional interfaces (a `Function<T, R>` cannot declare `throws IOException`). This is why many modern libraries (e.g., `UncheckedIOException`) provide unchecked wrappers around checked ones.

```java
import java.io.UncheckedIOException;
import java.io.IOException;
import java.nio.file.*;
import java.util.List;
import java.util.stream.Stream;

public class StreamsAndCheckedExceptions {
    public static List<String> linesOf(Path path) {
        try (Stream<String> lines = Files.lines(path)) {
            return lines.toList();
        } catch (IOException e) {
            // Files.lines already throws unchecked UncheckedIOException lazily during iteration,
            // but the try-with-resources open call itself is still checked, so we wrap it too.
            throw new UncheckedIOException(e);
        }
    }
}
```

## Exception Handling Best Practices

Handling exceptions well is more about discipline than syntax. This section covers the patterns reviewers look for.

### `finally` Semantics

Code in a `finally` block always runs, whether the `try` succeeded, threw, or returned. It is the right place for cleanup that must happen no matter what.

```java
public class FinallyDemo {
    static int compute() {
        try {
            System.out.println("try");
            throw new RuntimeException("fail");
        } finally {
            System.out.println("finally always runs");
        }
    }
}
```

### How `return` in `finally` Swallows Exceptions

This is a classic trap: if `finally` itself contains a `return` (or a `throw`), it **overrides** whatever the `try` or `catch` block was about to do — including silently discarding an in-flight exception.

```java
// BROKEN: the exception disappears without a trace
public class SwallowedException {
    static int risky() {
        try {
            throw new IllegalStateException("real problem");
        } finally {
            return 42; // this return wins; the exception above is discarded
        }
    }

    public static void main(String[] args) {
        System.out.println(risky()); // prints 42, exception is gone forever
    }
}
```

```java
// FIXED: never put a return (or throw) inside finally
public class NoSwallowedException {
    static int risky() {
        try {
            throw new IllegalStateException("real problem");
        } finally {
            System.out.println("cleanup only, no return here");
        }
    }
}
```

Rule of thumb: `finally` blocks should only do cleanup (closing resources, releasing locks, logging). They should never `return`, `break`, `continue`, or `throw`.

### Multi-Catch

When several exception types need the same handling, `catch` can list them with `|` instead of duplicating the block.

```java
public class MultiCatchDemo {
    static void parse(String input) {
        try {
            int value = Integer.parseInt(input);
            System.out.println(100 / value);
        } catch (NumberFormatException | ArithmeticException e) {
            System.out.println("Invalid input or division error: " + e.getMessage());
        }
    }
}
```

Note: the caught variable (`e` here) is effectively `final`, and its static type is the least upper bound of the listed types — you cannot reassign it, and you cannot catch a type and its subtype together in the same multi-catch (the compiler will reject that as redundant).

### Rethrow with Precise Type Inference

Since Java 7, if a `catch` block only rethrows the caught exception (without reassigning it to a broader type), the compiler tracks the **precise** type that was actually thrown, not just the declared catch type. This lets a method declare `throws IOException, SQLException` instead of the broader `throws Exception`.

```java
public class PreciseRethrow {
    static void doWork(boolean useDb) throws IOException, SQLException {
        try {
            if (useDb) {
                throw new SQLException("db error");
            } else {
                throw new IOException("io error");
            }
        } catch (Exception e) {
            // e is caught as Exception, but the compiler knows only
            // IOException or SQLException could actually reach here,
            // so this rethrow is allowed without "throws Exception".
            throw e;
        }
    }
}
```

If you reassign `e` inside the catch block, the compiler loses this precision and falls back to the declared catch type.

### Exception Chaining (Cause)

When you catch a low-level exception and throw a higher-level one, always pass the original as the **cause**. This preserves the full stack trace instead of hiding the real reason.

```java
// BROKEN: original cause is lost
public class LostCause {
    static void loadUser(String id) {
        try {
            fetchFromDatabase(id);
        } catch (SQLException e) {
            throw new RuntimeException("Could not load user " + id); // cause dropped!
        }
    }
    static void fetchFromDatabase(String id) throws SQLException {
        throw new SQLException("connection refused");
    }
}
```

```java
// FIXED: chain the cause
public class PreservedCause {
    static void loadUser(String id) {
        try {
            fetchFromDatabase(id);
        } catch (SQLException e) {
            throw new RuntimeException("Could not load user " + id, e); // cause preserved
        }
    }
    static void fetchFromDatabase(String id) throws SQLException {
        throw new SQLException("connection refused");
    }
}
```

You can also inspect the cause chain programmatically with `getCause()`, and Java 9+ adds `Throwable.getSuppressed()` for suppressed exceptions (covered later).

```java
try {
    PreservedCause.loadUser("42");
} catch (RuntimeException e) {
    Throwable cause = e.getCause();
    System.out.println("Root cause: " + cause);
}
```

### Custom Exception Design

Design custom exceptions when the standard library types don't communicate enough domain meaning. Keep them simple, immutable, and always provide constructors that support chaining.

```java
public class InsufficientFundsException extends Exception {
    private final BigDecimal shortfall;

    public InsufficientFundsException(String message, BigDecimal shortfall) {
        super(message);
        this.shortfall = shortfall;
    }

    public InsufficientFundsException(String message, BigDecimal shortfall, Throwable cause) {
        super(message, cause);
        this.shortfall = shortfall;
    }

    public BigDecimal getShortfall() {
        return shortfall;
    }
}
```

Guidelines for custom exceptions:

- Extend `Exception` for checked, recoverable business errors (e.g., `InsufficientFundsException`); extend `RuntimeException` for programming errors or when using functional/stream code.
- Always provide a `(String message, Throwable cause)` constructor for chaining.
- Add extra fields (like `shortfall` above) only when calling code actually needs them programmatically — don't just stuff everything into the message string.
- Avoid deep exception hierarchies. One or two custom types per module is usually enough.
- Don't make exceptions mutable; they should describe a moment in time.

### `NullPointerException` Helpful Messages (JDK 14+)

Since JDK 14 (stabilized as a default from JDK 15), the JVM can generate a **helpful NPE message** that names exactly which variable or method call was null, instead of just a bare stack trace line.

```java
public class HelpfulNpeDemo {
    record Address(String city) {}
    record Person(Address address) {}

    public static void main(String[] args) {
        Person person = new Person(null);
        System.out.println(person.address().city()); // NPE here
    }
}
```

Without the feature you'd just see `NullPointerException` with no detail. With helpful NPE messages enabled (default since Java 15), you get:

```
Exception in thread "main" java.lang.NullPointerException:
    Cannot invoke "HelpfulNpeDemo$Address.city()" because the return value of
    "HelpfulNpeDemo$Person.address()" is null
	at HelpfulNpeDemo.main(HelpfulNpeDemo.java:8)
```

This tells you precisely which call returned `null`, which is invaluable when a line has several chained calls. If you're on an older JVM or it's disabled, enable it with `-XX:+ShowCodeDetailsInExceptionMessages` (unnecessary on modern JDKs, where it's on by default).

### Suppressed Exceptions

When a `try-with-resources` block throws an exception in the body *and* closing a resource also throws, Java doesn't discard either one. The body's exception is thrown as the primary exception, and the close-time exception is attached to it as a **suppressed exception**.

```java
public class SuppressedDemo {
    static class Resource implements AutoCloseable {
        public void use() {
            throw new RuntimeException("failure during use");
        }
        @Override
        public void close() {
            throw new RuntimeException("failure during close");
        }
    }

    public static void main(String[] args) {
        try (Resource r = new Resource()) {
            r.use();
        } catch (RuntimeException e) {
            System.out.println("Primary: " + e.getMessage());
            for (Throwable suppressed : e.getSuppressed()) {
                System.out.println("Suppressed: " + suppressed.getMessage());
            }
        }
    }
}
```

Output:

```
Primary: failure during use
Suppressed: failure during close
```

Without try-with-resources, if you manually close a resource inside `finally`, the close-time exception would simply *replace* the original one — losing the real cause. Suppressed exceptions solve exactly that problem.

## Try-with-Resources

`try-with-resources` (introduced in Java 7) automatically closes resources for you, even if an exception is thrown, without needing a manual `finally` block.

```java
// BROKEN: manual close, easy to get wrong, and swallows exceptions from use()
public class ManualClose {
    static void copy(String from, String to) throws IOException {
        FileInputStream in = null;
        FileOutputStream out = null;
        try {
            in = new FileInputStream(from);
            out = new FileOutputStream(to);
            in.transferTo(out);
        } finally {
            if (out != null) out.close(); // if this throws, exception from in.close() below is lost
            if (in != null) in.close();
        }
    }
}
```

```java
// FIXED: try-with-resources, both resources closed automatically, in reverse order
public class TryWithResourcesDemo {
    static void copy(String from, String to) throws IOException {
        try (FileInputStream in = new FileInputStream(from);
             FileOutputStream out = new FileOutputStream(to)) {
            in.transferTo(out);
        }
    }
}
```

### Desugaring: What the Compiler Actually Generates

The compiler rewrites the try-with-resources block into ordinary `try/finally` code with null-checks and suppressed-exception wiring. Roughly, `copy` above desugars to something like this:

```java
static void copy(String from, String to) throws IOException {
    FileInputStream in = new FileInputStream(from);
    try {
        FileOutputStream out = new FileOutputStream(to);
        try {
            in.transferTo(out);
        } finally {
            if (out != null) {
                out.close(); // any exception here becomes suppressed if 'try' already threw
            }
        }
    } finally {
        if (in != null) {
            in.close();
        }
    }
}
```

Key detail: this is why any exception thrown while closing becomes a **suppressed** exception rather than replacing the original — the generated code catches the primary exception, tries to close, and calls `addSuppressed` on failure before rethrowing.

### Resource Close Order

Resources declared in a try-with-resources header are closed in the **reverse** order of declaration — last declared, first closed. This mirrors how you'd unwind nested `finally` blocks by hand.

```java
public class CloseOrderDemo {
    record Loud(String name) implements AutoCloseable {
        Loud open() { System.out.println("open " + name); return this; }
        @Override public void close() { System.out.println("close " + name); }
    }

    public static void main(String[] args) {
        try (Loud a = new Loud("A").open();
             Loud b = new Loud("B").open();
             Loud c = new Loud("C").open()) {
            System.out.println("body");
        }
    }
}
```

Output:

```
open A
open B
open C
body
close C
close B
close A
```

### Effectively-Final Resources (Java 9+)

Since Java 9, you can use an already-declared effectively-final variable directly in the try-with-resources header, without redeclaring it.

```java
public class ExistingResource {
    static void process() throws IOException {
        BufferedReader reader = new BufferedReader(new FileReader("data.txt"));
        try (reader) { // just reference it, no re-declaration needed
            System.out.println(reader.readLine());
        }
    }
}
```

### `AutoCloseable` vs `Closeable`

| | `AutoCloseable` | `Closeable` |
|---|---|---|
| Introduced | Java 7 | Java 5 (retrofitted to extend `AutoCloseable` in Java 7) |
| `close()` signature | `void close() throws Exception` | `void close() throws IOException` |
| Exception type | Any `Exception` | Only `IOException` (narrower) |
| Idempotent close required? | Not guaranteed by contract | Yes — calling `close()` more than once must be a no-op |
| Typical use | Any resource: locks, DB connections, custom types | I/O-specific types (streams, readers, writers) |
| Can be used in try-with-resources? | Yes | Yes (it extends `AutoCloseable`) |

```java
public class LockResource implements AutoCloseable {
    private final java.util.concurrent.locks.Lock lock;

    public LockResource(java.util.concurrent.locks.Lock lock) {
        this.lock = lock;
        lock.lock();
    }

    @Override
    public void close() { // narrows the throws clause to nothing, which is fine
        lock.unlock();
    }
}
```

```java
public static void withLock(java.util.concurrent.locks.Lock lock, Runnable action) {
    try (LockResource guard = new LockResource(lock)) {
        action.run();
    }
}
```

Prefer `Closeable` for I/O-flavored classes so callers get the narrower, more specific `IOException` instead of a generic `Exception`. Use `AutoCloseable` for everything else (locks, custom pooled resources, transactions).

## Assertions

An **assertion** is a statement that checks something you believe must always be true. If it's false, the JVM throws an `AssertionError`. Assertions are for catching *your own bugs* during development and testing — they are not a validation mechanism for public APIs.

```java
public class AssertionDemo {
    static int half(int even) {
        assert even % 2 == 0 : "Expected an even number but got " + even;
        return even / 2;
    }
}
```

### The `-ea` Flag

Assertions are **disabled by default** at runtime for performance reasons. You must explicitly enable them with `-ea` (or `-enableassertions`) when running the JVM:

```bash
java -ea com.example.AssertionDemo
```

Because assertions can be silently switched off, code must never depend on them running. If `-ea` is off, the `assert` line is skipped entirely — no side effect happens at all.

### Why Assertions Must Not Have Side Effects

If an assertion is disabled, its expression is **never evaluated**. Any side effect inside it simply won't happen in production, creating a bug that only shows up when assertions are off — which is the default.

```java
import java.util.List;

public class SideEffectAssertion {
    static void process(List<String> items) {
        // BROKEN: removeDuplicates() only runs when -ea is enabled!
        assert removeDuplicates(items);
        System.out.println(items);
    }

    static boolean removeDuplicates(List<String> items) {
        // pretend this mutates 'items' and returns true
        return true;
    }
}
```

```java
import java.util.List;

public class NoSideEffectAssertion {
    static void process(List<String> items) {
        removeDuplicates(items); // always runs, regardless of -ea
        assert !hasDuplicates(items) : "Duplicates remain after cleanup"; // pure check, no side effect
        System.out.println(items);
    }

    static void removeDuplicates(List<String> items) { /* mutates items */ }
    static boolean hasDuplicates(List<String> items) { return false; }
}
```

### Why Assertions Must Not Validate Public API Arguments

Because assertions can be disabled, they are the wrong tool for checking arguments on a **public** method — anyone calling your API from outside might run with assertions off, and your validation would silently vanish, letting bad input through.

```java
// BROKEN: public API validation via assert — disappears when -ea is off
public class AccountBroken {
    private double balance;

    public void withdraw(double amount) {
        assert amount > 0 : "amount must be positive"; // NOT enforced by default!
        balance -= amount;
    }
}
```

```java
// FIXED: public API validation should throw a real, unconditional exception
public class AccountFixed {
    private double balance;

    public void withdraw(double amount) {
        if (amount <= 0) {
            throw new IllegalArgumentException("amount must be positive: " + amount);
        }
        balance -= amount;
    }
}
```

### Where Assertions Belong

| Use case | Right tool |
|---|---|
| Checking a public method's arguments | `if (...) throw new IllegalArgumentException(...)` |
| Checking internal invariants only your own code can violate (e.g., "this private helper should never see a negative index here") | `assert` |
| Documenting an assumption for future maintainers, checked only during testing | `assert` |
| Guarding against `null` from an external caller | Explicit `null` check + exception (e.g., `Objects.requireNonNull`) |
| Verifying test expectations | Use a test framework's assertions (e.g., JUnit's `assertEquals`), not the `assert` keyword |

```java
public class InvariantExample {
    private int index(int size, int position) {
        int result = position % size;
        // Internal invariant: our own arithmetic guarantees this is always >= 0.
        // Safe to use assert here because it's checking OUR logic, not caller input.
        assert result >= 0 : "computed negative index: " + result;
        return result;
    }
}
```

## Common Code-Review Interview Pitfalls

1. **Catching `Exception` (or `Throwable`) and doing nothing (`catch (Exception e) {}`).**
   Why it matters: it hides every failure, including bugs, making the system silently wrong instead of loudly failing. It's one of the fastest ways to fail a code review.
   ```java
   // Before
   try { save(record); } catch (Exception e) {}
   // After
   try { save(record); } catch (IOException e) { throw new UncheckedIOException("Failed to save record", e); }
   ```

2. **`return` inside a `finally` block, silently discarding an exception.**
   Why it matters: the caller thinks the operation succeeded and never learns about the real failure.
   ```java
   // Before
   finally { return result; }
   // After
   finally { cleanup(); } // no return/throw in finally
   ```

3. **Losing the cause when wrapping exceptions.**
   Why it matters: without the cause, the stack trace stops at the wrapping point and the real root cause is unrecoverable in logs.
   ```java
   // Before
   throw new RuntimeException("failed");
   // After
   throw new RuntimeException("failed", e);
   ```

4. **Logging an exception and then rethrowing it (double reporting).**
   Why it matters: the same error gets logged multiple times up the call stack, flooding logs and confusing on-call engineers about how many times it actually happened. Log at the boundary where you handle it, or rethrow — never both at every layer.
   ```java
   // Before
   catch (IOException e) { log.error("failed", e); throw e; }
   // After
   catch (IOException e) { throw new ServiceException("Upload failed", e); } // log once, at the top-level handler
   ```

5. **Catching `InterruptedException` and swallowing it without restoring the interrupt flag.**
   Why it matters: `Thread.sleep`/`wait`/blocking calls clear the interrupt flag when they throw; if you don't restore it, the thread's owning framework (e.g., an executor) can no longer tell the thread was asked to stop, breaking graceful shutdown.
   ```java
   // Before
   try { Thread.sleep(1000); } catch (InterruptedException e) { /* ignored */ }
   // After
   try { Thread.sleep(1000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
   ```

6. **Using exceptions for normal control flow.**
   Why it matters: exceptions capture a full stack trace and are expensive; using them to signal ordinary conditions (like "not found") hurts performance and readability.
   ```java
   // Before
   try { return map.get(key); } catch (NullPointerException e) { return defaultValue; }
   // After
   return map.getOrDefault(key, defaultValue);
   ```

7. **Throwing generic exceptions (`throw new Exception("error")` or `RuntimeException`) instead of a specific, meaningful type.**
   Why it matters: callers can't distinguish failure modes to handle them differently, and generic catches become tempting, widening the blast radius of `catch` blocks.
   ```java
   // Before
   throw new RuntimeException("user not found");
   // After
   throw new NoSuchElementException("No user with id " + id);
   ```

8. **Validating public API arguments with `assert` instead of a real check.**
   Why it matters: assertions are off by default in production (`-ea` not set), so the validation silently disappears, letting invalid state propagate deep into the system before it fails somewhere confusing.
   ```java
   // Before
   assert amount > 0;
   // After
   if (amount <= 0) throw new IllegalArgumentException("amount must be positive: " + amount);
   ```

9. **Putting side effects inside an `assert` expression.**
   Why it matters: when assertions are disabled, the expression is never evaluated, so that side effect silently stops happening — a correctness bug that appears only in production.
   ```java
   // Before
   assert list.remove(item);
   // After
   boolean removed = list.remove(item);
   assert removed : "expected item to be present";
   ```

10. **Not closing resources, or closing them manually instead of using try-with-resources.**
    Why it matters: manual `finally`-based closing is verbose and easy to get subtly wrong (e.g., an exception from `close()` masking the real exception, or a missing null-check causing a `NullPointerException` during cleanup).
    ```java
    // Before
    FileInputStream in = new FileInputStream(path);
    try { use(in); } finally { in.close(); }
    // After
    try (FileInputStream in = new FileInputStream(path)) { use(in); }
    ```

11. **Throwing checked exceptions from lambdas passed to standard functional interfaces, causing awkward wrapping or compile errors.**
    Why it matters: `Function`, `Consumer`, etc. don't declare `throws`, so checked exceptions inside lambdas must be caught locally or wrapped — forgetting this leads to compile errors or ugly ad hoc wrapping scattered across the codebase.
    ```java
    // Before (does not compile: readAllBytes throws IOException)
    paths.stream().map(p -> Files.readAllBytes(p));
    // After
    paths.stream().map(p -> {
        try { return Files.readAllBytes(p); }
        catch (IOException e) { throw new UncheckedIOException(e); }
    });
    ```

12. **Catching a broad type just to rethrow a narrower, unrelated one, losing the original stack trace.**
    Why it matters: creating a brand-new exception without chaining the original throws away the actual failure location, making the bug much harder to diagnose from logs alone.
    ```java
    // Before
    catch (Exception e) { throw new BusinessException("failed"); }
    // After
    catch (Exception e) { throw new BusinessException("failed", e); }
    ```

13. **Declaring `throws Exception` on a method signature instead of the specific checked exceptions it actually throws.**
    Why it matters: it forces every caller to catch the overly broad `Exception`, defeating the whole purpose of checked exceptions — communicating precisely what can go wrong.
    ```java
    // Before
    void process() throws Exception { ... }
    // After
    void process() throws IOException, SQLException { ... }
    ```

14. **Comparing exception messages (`e.getMessage().contains(...)`) to detect a specific failure instead of catching a specific type.**
    Why it matters: messages are not part of the API contract and can change between JDK versions or library updates, silently breaking the check.
    ```java
    // Before
    catch (Exception e) { if (e.getMessage().contains("not found")) { ... } }
    // After
    catch (NoSuchElementException e) { ... }
    ```

15. **Ignoring suppressed exceptions when debugging try-with-resources failures.**
    Why it matters: if closing a resource also fails, that information is attached as a suppressed exception on the primary one; skipping `getSuppressed()` during triage can hide a second, independent root cause (e.g., a leaked connection on top of the original error).
    ```java
    // Before
    catch (Exception e) { log.error("Failed", e); } // suppressed causes buried in nested trace
    // After
    catch (Exception e) {
        log.error("Failed", e);
        for (Throwable s : e.getSuppressed()) log.error("Also failed while closing", s);
    }
    ```
