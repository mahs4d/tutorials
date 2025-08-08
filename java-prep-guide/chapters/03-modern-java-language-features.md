# 3. Modern Java Language Features

Java has changed a lot since Java 8. Newer versions added features that make code shorter, safer, and easier to read. This chapter covers the features you are most likely to see in a modern codebase or be asked about in a code-review interview. Each section explains what the feature is, which JDK version it shipped in, and shows a small, realistic example.

- [Enums](#enums)
- [Records](#records)
- [Sealed Classes and Interfaces](#sealed-classes-and-interfaces)
- [Interfaces (default, static, private methods)](#interfaces-default-static-private-methods)
- [Abstract Classes](#abstract-classes)
- [Pattern Matching (instanceof, switch)](#pattern-matching-instanceof-switch)
- [Primitive Types in Patterns](#primitive-types-in-patterns)
- [Switch Expressions](#switch-expressions)
- [Text Blocks](#text-blocks)
- [String Templates (Preview)](#string-templates-preview)
- [Unnamed Variables and Patterns](#unnamed-variables-and-patterns)
- [Value-Based Classes](#value-based-classes)
- [Preview and Incubator Features](#preview-and-incubator-features)
- [Deprecated and Removed Features Across Java Versions](#deprecated-and-removed-features-across-java-versions)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

---

## Enums

An **enum** (short for "enumeration") is a type with a fixed set of constant values. Use an enum when a variable can only be one of a small, known list of options, like days of the week or order statuses.

Enums in Java are full classes. They can have fields, constructors, methods, and they can even implement interfaces. This is more powerful than enums in many other languages.

```java
public enum OrderStatus {
    NEW,
    PAID,
    SHIPPED,
    DELIVERED,
    CANCELLED
}
```

Enums can carry data and behavior:

```java
public enum Planet {
    MERCURY(3.303e+23, 2.4397e6),
    VENUS(4.869e+24, 6.0518e6),
    EARTH(5.976e+24, 6.37814e6);

    private final double mass;   // in kilograms
    private final double radius; // in meters

    Planet(double mass, double radius) {
        this.mass = mass;
        this.radius = radius;
    }

    double surfaceGravity() {
        final double G = 6.67300E-11;
        return G * mass / (radius * radius);
    }
}

// Usage:
double g = Planet.EARTH.surfaceGravity(); // ~9.8
```

Enums can also have abstract methods, where each constant provides its own implementation:

```java
public enum Operation {
    PLUS {
        public int apply(int a, int b) { return a + b; }
    },
    MINUS {
        public int apply(int a, int b) { return a - b; }
    };

    public abstract int apply(int a, int b);
}

// Usage:
int result = Operation.PLUS.apply(2, 3); // 5
```

Key facts every reviewer should know:

| Fact | Detail |
|---|---|
| Since | Java 5 |
| Base class | Implicitly extends `java.lang.Enum` (cannot extend anything else) |
| Interfaces | Can implement one or more interfaces |
| Comparison | Always safe to compare with `==` (constants are singletons) |
| Switch | Enums work great in `switch` statements and expressions |
| Serialization | Enum serialization is handled specially and is safe by default |
| `values()` / `valueOf()` | Auto-generated static methods to list constants or parse from a string |

```java
for (OrderStatus s : OrderStatus.values()) {
    System.out.println(s); // NEW, PAID, SHIPPED, DELIVERED, CANCELLED
}

OrderStatus s = OrderStatus.valueOf("PAID"); // throws IllegalArgumentException if not found
```

---

## Records

A **record** is a compact way to declare an immutable data class. Before records, developers had to hand-write (or generate) a constructor, getters, `equals()`, `hashCode()`, and `toString()` for simple "data holder" classes. Records generate all of that automatically.

Records were previewed in Java 14/15 and finalized as a standard feature in **Java 16**.

```java
public record Point(int x, int y) { }

// The compiler generates:
// - a constructor Point(int x, int y)
// - accessors x() and y() (note: no "get" prefix)
// - equals(), hashCode(), toString()

Point p1 = new Point(1, 2);
Point p2 = new Point(1, 2);

System.out.println(p1);          // Point[x=1, y=2]
System.out.println(p1.x());      // 1
System.out.println(p1.equals(p2)); // true
```

You can add validation with a **compact constructor**, plus extra methods and static factories:

```java
public record Range(int min, int max) {

    // Compact constructor: runs before fields are assigned.
    public Range {
        if (min > max) {
            throw new IllegalArgumentException("min must be <= max");
        }
    }

    public int length() {
        return max - min;
    }

    public static Range of(int min, int max) {
        return new Range(min, max);
    }
}

Range r = Range.of(1, 10);
System.out.println(r.length()); // 9
```

Records vs. regular classes:

| Aspect | Record | Regular class |
|---|---|---|
| Fields | All fields are `private final` automatically | You choose mutability |
| Accessors | Auto-generated (`x()`, not `getX()`) | You write them, often `getX()` |
| `equals`/`hashCode`/`toString` | Auto-generated from all fields | You write them (or use IDE/Lombok) |
| Inheritance | Cannot extend another class (implicitly extends `Record`) | Can extend any class |
| Can implement interfaces | Yes | Yes |
| Mutability | Always immutable (fields cannot change) | Can be mutable or immutable |
| Best use case | Simple immutable data carriers (DTOs, value objects) | Entities with behavior, mutable state, or inheritance |

Records can implement interfaces, which is useful with sealed types (see next section):

```java
public interface Shape {
    double area();
}

public record Circle(double radius) implements Shape {
    public double area() {
        return Math.PI * radius * radius;
    }
}
```

Records can also be **generic**:

```java
public record Pair<A, B>(A first, B second) { }

Pair<String, Integer> pair = new Pair<>("age", 30);
```

---

## Sealed Classes and Interfaces

A **sealed** class or interface restricts which other classes or interfaces may extend or implement it. This gives you controlled inheritance: you (the author) decide the exact, closed list of subtypes. Sealed types were finalized in **Java 17**.

Why this matters: with a sealed hierarchy, the compiler (and tools like `switch`) knows every possible subtype. This enables **exhaustiveness checking** — the compiler can tell you if you forgot to handle a case.

```java
public sealed interface Shape permits Circle, Square, Rectangle { }

public record Circle(double radius) implements Shape { }
public record Square(double side) implements Shape { }
public record Rectangle(double width, double height) implements Shape { }
```

If the permitted subtypes are in the same file, you can drop the `permits` clause — the compiler figures it out:

```java
public sealed interface Payment {
    record CreditCard(String number) implements Payment { }
    record BankTransfer(String iban) implements Payment { }
    record Cash() implements Payment { }
}
```

A permitted subclass must be declared as one of: `final`, `sealed` (with its own permitted subtypes), or `non-sealed` (reopens the hierarchy for further extension).

```java
public sealed class Vehicle permits Car, Truck { }

public final class Car extends Vehicle { }          // no further subclassing
public non-sealed class Truck extends Vehicle { }   // anyone can extend Truck now
```

Sealed vs `final` vs open (normal) inheritance:

| Modifier | Who can extend it | Typical use |
|---|---|---|
| (none, open) | Anyone | Flexible libraries, frameworks |
| `final` | No one | Utility classes, immutable value types |
| `sealed` | Only the classes listed in `permits` (or same file/package) | Closed hierarchies you want exhaustive `switch` over |
| `non-sealed` | Anyone (used to reopen a branch of a sealed hierarchy) | Escape hatch inside a sealed hierarchy |

Sealed types shine with pattern matching in `switch` (covered below):

```java
static double area(Shape shape) {
    return switch (shape) {
        case Circle c -> Math.PI * c.radius() * c.radius();
        case Square s -> s.side() * s.side();
        case Rectangle r -> r.width() * r.height();
        // no "default" needed: compiler knows these are ALL the cases
    };
}
```

---

## Interfaces (default, static, private methods)

Classic interfaces (before Java 8) could only declare abstract methods — no bodies allowed. Modern interfaces can now include real code.

| Method type | Since | Has a body? | Can be overridden? | Purpose |
|---|---|---|---|---|
| `abstract` (plain) | Java 1 | No | Yes (must be implemented) | The contract |
| `default` | Java 8 | Yes | Yes | Provide a default implementation so existing implementers don't break |
| `static` | Java 8 | Yes | No (belongs to the interface itself) | Utility/helper methods related to the interface |
| `private` | Java 9 | Yes | No | Share code between default/static methods without exposing it |

```java
public interface Vehicle {

    // Abstract method: every implementer must define this.
    void drive();

    // Default method: implementers get this for free, but may override it.
    default void honk() {
        System.out.println("Beep beep!");
    }

    // Static method: called on the interface itself, e.g. Vehicle.create(...)
    static Vehicle create(String type) {
        return switch (type) {
            case "car" -> new Car();
            default -> throw new IllegalArgumentException("Unknown type: " + type);
        };
    }

    // Private method (Java 9+): helper reused by default methods below.
    private void log(String action) {
        System.out.println("[Vehicle] " + action);
    }

    default void startTrip() {
        log("starting trip");
        drive();
    }
}

class Car implements Vehicle {
    public void drive() {
        System.out.println("Car is driving");
    }
}
```

Why this matters for code review: `default` methods let library authors add new methods to an interface **without breaking existing implementations** (this is exactly why `Collection` and `Map` gained many `default` methods over the years, like `forEach` and `getOrDefault`). Watch out, though — if a class implements two interfaces with the same `default` method signature, the class must override it to resolve the conflict, or the code will not compile.

```java
interface A { default String name() { return "A"; } }
interface B { default String name() { return "B"; } }

class C implements A, B {
    // Must override: ambiguous otherwise.
    public String name() {
        return A.super.name() + B.super.name(); // "AB"
    }
}
```

---

## Abstract Classes

An **abstract class** cannot be instantiated directly. It exists to be extended. It can mix fully implemented methods with abstract ones that subclasses must fill in. Abstract classes have existed since Java 1, but it's worth revisiting how they compare to modern interfaces since both can now hold real code.

```java
public abstract class Employee {

    protected final String name;

    protected Employee(String name) {
        this.name = name;
    }

    // Concrete method: shared by all subclasses.
    public String greet() {
        return "Hello, my name is " + name;
    }

    // Abstract method: each subclass must define its own pay calculation.
    public abstract double calculatePay();
}

public class Manager extends Employee {
    private final double baseSalary;
    private final double bonus;

    public Manager(String name, double baseSalary, double bonus) {
        super(name);
        this.baseSalary = baseSalary;
        this.bonus = bonus;
    }

    @Override
    public double calculatePay() {
        return baseSalary + bonus;
    }
}
```

Abstract class vs. interface — the modern comparison:

| Aspect | Abstract class | Interface |
|---|---|---|
| Instance fields | Yes, any visibility | No instance fields (only `static final` constants) |
| Constructors | Yes | No |
| Multiple inheritance | A class can extend only **one** abstract class | A class can implement **many** interfaces |
| Method bodies | Any method can have a body | `default`, `static`, `private` can have bodies; plain methods cannot |
| Access modifiers on methods | `public`, `protected`, `private` all allowed | Methods are implicitly `public` (except `private` helper methods) |
| State/encapsulation | Good fit — designed to hold and manage state | Poor fit — designed to describe capability/contract |
| When to use | "Is-a" relationship with shared state and shared code | "Can-do" capability, especially across unrelated classes |

Rule of thumb for reviews: if two classes need to **share fields or a constructor**, lean toward an abstract class. If you're just describing a **capability** (e.g. `Comparable`, `Runnable`, `Serializable`), use an interface.

---

## Pattern Matching (instanceof, switch)

**Pattern matching** lets you check the shape or type of a value and extract its parts in one step, instead of checking the type and then manually casting.

### `instanceof` pattern matching (Java 16, finalized)

Before:

```java
if (obj instanceof String) {
    String s = (String) obj; // manual, error-prone cast
    System.out.println(s.length());
}
```

After — the cast is automatic, and the variable `s` is only in scope where it makes sense:

```java
if (obj instanceof String s) {
    System.out.println(s.length()); // "s" is already a String here
}
```

You can combine it with extra conditions:

```java
if (obj instanceof String s && !s.isBlank()) {
    System.out.println("Non-blank string: " + s);
}
```

### `switch` pattern matching (Java 21, finalized)

Pattern matching for `switch` lets you match on type (and, with records, on structure) directly in a `switch`.

```java
sealed interface Shape permits Circle, Square { }
record Circle(double radius) implements Shape { }
record Square(double side) implements Shape { }

static String describe(Shape shape) {
    return switch (shape) {
        case Circle c when c.radius() > 100 -> "huge circle";   // guarded pattern
        case Circle c                        -> "circle r=" + c.radius();
        case Square s                        -> "square side=" + s.side();
    };
}
```

**Record patterns** (Java 21) let you destructure a record right in the pattern, pulling out its components:

```java
record Point(int x, int y) { }
record Line(Point start, Point end) { }

static String describe(Object obj) {
    return switch (obj) {
        // Nested destructuring: pull x1,y1,x2,y2 straight out of the Line.
        case Line(Point(var x1, var y1), Point(var x2, var y2)) ->
            "Line from (%d,%d) to (%d,%d)".formatted(x1, y1, x2, y2);
        case Point(var x, var y) -> "Point(%d,%d)".formatted(x, y);
        default -> "unknown";
    };
}
```

Timeline summary:

| Feature | Preview since | Finalized in |
|---|---|---|
| `instanceof` pattern matching | Java 14 | Java 16 |
| `switch` pattern matching (type patterns, guards) | Java 17 | Java 21 |
| Record patterns (destructuring) | Java 19 | Java 21 |

---

## Primitive Types in Patterns

Historically, patterns (in `instanceof` and `switch`) only worked with reference types (objects), not primitives like `int` or `double`. **Primitive type patterns** extend pattern matching to primitives, including inside record patterns.

This feature is a **preview feature starting in JDK 23** (JEP 455, "Primitive Types in Patterns, `instanceof`, and `switch"). It is still evolving — as of Java 25 it remains a preview feature (re-previewed as JEP 507). You must compile and run with `--enable-preview` to use it.

```java
// Requires: javac --release 23 --enable-preview Main.java
//           java --enable-preview Main

Object obj = 42;

// instanceof with a primitive pattern (preview):
if (obj instanceof int i) {
    System.out.println("It's an int: " + i);
}
```

It also removes an old rough edge: `switch` on primitives couldn't easily be combined with ranges or exact numeric matching against boxed types. With primitive patterns, you get natural, type-safe matching:

```java
static String classify(Object o) {
    return switch (o) {
        case int i when i < 0    -> "negative int";
        case int i               -> "non-negative int: " + i;
        case long l               -> "a long: " + l;
        case double d             -> "a double: " + d;
        default                   -> "something else";
    };
}
```

Because this is preview, do not rely on it in production code yet; the syntax could still change before final release. Always check the JDK release notes before using it on a real project.

```java
// Compiling without --enable-preview gives an error like:
// error: primitive patterns in switch and instanceof are a preview feature
//        and are disabled by default.
```

---

## Switch Expressions

A traditional `switch` is a **statement**: it doesn't produce a value, and it needs `break` to avoid falling through to the next case. A **switch expression** (finalized in **Java 14**) produces a value directly, uses `->` (arrow) syntax, and has no fall-through.

```java
// Old style: switch STATEMENT
int day = 3;
String name;
switch (day) {
    case 1:
        name = "Monday";
        break;
    case 2:
        name = "Tuesday";
        break;
    default:
        name = "Unknown";
        break;
}
```

```java
// New style: switch EXPRESSION
int day = 3;
String name = switch (day) {
    case 1 -> "Monday";
    case 2 -> "Tuesday";
    default -> "Unknown";
};
```

Multiple case labels can share one arrow:

```java
String category = switch (day) {
    case 1, 2, 3, 4, 5 -> "Weekday";
    case 6, 7 -> "Weekend";
    default -> "Invalid";
};
```

When a case needs more than one statement, use a block and `yield` to return the value:

```java
int score = 85;
String grade = switch (score / 10) {
    case 10, 9 -> "A";
    case 8 -> {
        System.out.println("Good job!"); // side effect allowed
        yield "B";
    }
    default -> "C or below";
};
```

Switch statement vs. switch expression:

| Aspect | `switch` statement | `switch` expression |
|---|---|---|
| Produces a value | No | Yes |
| Syntax | `case X:` with `break` | `case X ->` (arrow) or block with `yield` |
| Fall-through | Yes, unless you add `break` | No fall-through |
| Exhaustiveness check (enums/sealed types) | Not enforced | Compiler can enforce it — missing cases won't compile |
| Since | Java 1 | Java 14 |

Exhaustiveness with `enum` or `sealed` types is a big win for reviewers: the compiler forces you to handle every case (or add a `default`), catching bugs when a new enum constant is added later but a `switch` elsewhere wasn't updated.

```java
enum TrafficLight { RED, YELLOW, GREEN }

static String action(TrafficLight light) {
    // No default needed: compiler verifies all 3 enum values are covered.
    return switch (light) {
        case RED -> "Stop";
        case YELLOW -> "Slow down";
        case GREEN -> "Go";
    };
}
```

---

## Text Blocks

A **text block** is a multi-line string literal. It removes the need for `\n` and string concatenation when writing multi-line text like JSON, SQL, or HTML. Text blocks were finalized in **Java 15**.

A text block starts and ends with `"""` (three double quotes), and the opening `"""` must be followed by a line break.

```java
String json = """
        {
          "name": "Ada",
          "role": "Engineer"
        }
        """;

System.out.println(json);
// Output:
// {
//   "name": "Ada",
//   "role": "Engineer"
// }
```

Before text blocks, the same JSON needed manual escaping:

```java
String jsonOld = "{\n" +
                 "  \"name\": \"Ada\",\n" +
                 "  \"role\": \"Engineer\"\n" +
                 "}\n";
```

Indentation rules: the compiler looks at the **least-indented line** (including the closing `"""`) and strips that much leading whitespace from every line. This means the position of the closing `"""` controls the indentation of the output.

```java
String sql = """
    SELECT id, name
    FROM employees
    WHERE department = 'Engineering'
    """;
```

You can still use escape sequences, and a few special ones help control whitespace:

```java
String noTrailingNewline = """
        Line one
        Line two\
        """; // "\" at end of line joins it with the next line (no newline inserted)

String withTrailingSpaces = """
        Trailing spaces matter here.   \s
        """; // \s forces a space that would otherwise be trimmed
```

You can also combine text blocks with `String.format` or `formatted`:

```java
String template = """
        Hello, %s!
        You have %d new messages.
        """;
String message = template.formatted("Ada", 5);
```

---

## String Templates (Preview)

**String templates** were a proposed feature to embed expressions directly inside string literals, similar to JavaScript template literals or Kotlin string interpolation. They used the syntax `STR."Hello \{name}"`.

```java
// Example of the PREVIEWED (now withdrawn) syntax — do not use in real code:
// String name = "Ada";
// int count = 5;
// String message = STR."Hello \{name}, you have \{count} messages.";
```

**Important — be accurate about status:** String templates were previewed in **Java 21** (JEP 430) and again, with refinements, in **Java 22** (JEP 459). However, based on developer feedback, the OpenJDK team decided the design needed more work, and the feature was **withdrawn** — it did not ship as a preview in Java 23, and there is no finalized version as of Java 25. If you see `STR."..."` syntax in an article or an older tutorial, know that it will not compile on any released JDK.

Until a template mechanism (re-)ships, the standard ways to build formatted strings remain:

```java
String name = "Ada";
int count = 5;

// String concatenation
String a = "Hello, " + name + ", you have " + count + " messages.";

// String.format / formatted (classic, since Java 5 / Java 15)
String b = "Hello, %s, you have %d messages.".formatted(name, count);

// Text block + formatted (Java 15+)
String c = """
        Hello, %s, you have %d messages.
        """.formatted(name, count);
```

Code-review takeaway: if a candidate claims string templates are "a stable Java feature," that is a **red flag** — the honest answer is "previewed twice, then withdrawn; not currently available."

---

## Unnamed Variables and Patterns

Sometimes you must declare a variable (for a lambda parameter, a `catch` block, or a pattern component) that you never actually use. The **unnamed variable**, written as a single underscore `_`, lets you say "I don't need this" explicitly. Finalized in **Java 22** (JEP 456).

```java
map.forEach((key, _) -> System.out.println(key)); // value is unused
```

```java
try {
    riskyOperation();
} catch (IOException _) {
    // We don't care about the exception details, just that it failed.
    System.out.println("Operation failed");
}
```

Unnamed patterns work well with record deconstruction when you only care about some components:

```java
record Point3D(int x, int y, int z) { }

static String flatten(Point3D p) {
    // We only care about x and y; z is discarded.
    if (p instanceof Point3D(var x, var y, var _)) {
        return "(" + x + ", " + y + ")";
    }
    return "invalid";
}
```

You can also use `_` for a loop variable you never read:

```java
int count = 0;
for (var _ : someList) {
    count++; // we only care how many elements there are
}
```

Why this matters in review: unlike an unused variable named `ignored` or `e`, the unnamed variable `_` is a language-level signal — some static analysis tools and the compiler itself understand "this is intentionally unused," reducing false-positive "unused variable" warnings and making intent explicit to the next reader.

---

## Value-Based Classes

A **value-based class** is a class whose instances are treated as pure values, not as objects with identity. `Integer`, `LocalDate`, `Optional`, and (since Java 16) `record` types are all examples. The JDK documentation defines conventions for such classes; they're a design pattern/contract, not a language keyword.

Rules for a value-based class (per the JDK's `java.lang.doc-files/ValueBased.html` conventions):

1. Fields are `final`, and the object is immutable.
2. The class is `final` (or effectively behaves that way) — no subclassing that adds identity-sensitive behavior.
3. Instances are considered interchangeable if `equals()` says they're equal — you should never rely on `==` or synchronize on them.
4. The class does not expose an identity-sensitive API (no exposed lock, no `==`-based contracts).
5. Instances are typically created via static factory methods, not always via visible constructors (e.g. `Optional.of(...)`, `LocalDate.of(...)`).

```java
// Integer is value-based: NEVER rely on identity ("==") for comparison.
Integer a = 100;
Integer b = 100;
System.out.println(a == b);      // true (small int cache, but DO NOT rely on this)

Integer c = 200;
Integer d = 200;
System.out.println(c == d);      // false! Outside the cached range (-128..127)
System.out.println(c.equals(d)); // true — this is the correct way to compare
```

```java
// Records are value-based by nature.
public record Money(String currency, long cents) { }

Money price1 = new Money("EUR", 1999);
Money price2 = new Money("EUR", 1999);

System.out.println(price1 == price2);      // false — different instances
System.out.println(price1.equals(price2)); // true — same value
```

Because value-based instances may be interchangeable, the JDK explicitly warns against **synchronizing on them**:

```java
Integer lock = 42;
synchronized (lock) { // BAD: value-based classes may be cached/shared/pooled
    // ...
}
```

Use a dedicated `Object` or `ReentrantLock` for locking instead:

```java
private final Object lock = new Object();

synchronized (lock) {
    // safe: this object has real identity and is never shared
}
```

---

## Preview and Incubator Features

The JDK ships experimental features in two ways, so developers can try them before they become permanent:

| Mechanism | What it means | How to enable |
|---|---|---|
| **Preview feature** | A complete, fully-specified language or API feature, but not yet guaranteed stable. Might change or be withdrawn based on feedback. | Compile and run with `--enable-preview` (plus `--release <N>` when compiling) |
| **Incubator module** | An API delivered in a `jdk.incubator.*` module, for APIs that need real-world testing before joining the standard `java.*` API. | Add `--add-modules jdk.incubator.<name>` |

Preview features must be explicitly enabled on **both** compilation and execution, and the produced `.class` files are marked so they cannot run on a JDK that isn't the exact same preview-enabled version.

```bash
# Compiling code that uses a preview feature (e.g. primitive patterns, JDK 23):
javac --release 23 --enable-preview Main.java

# Running it:
java --enable-preview Main
```

Examples of features that went through the preview process (with outcomes):

| Feature | Preview JDKs | Outcome |
|---|---|---|
| Records | 14, 15 | Finalized in 16 |
| Sealed classes | 15, 16 | Finalized in 17 |
| Pattern matching for `switch` | 17, 18, 19, 20 | Finalized in 21 |
| Record patterns | 19, 20 | Finalized in 21 |
| Virtual threads | 19, 20 | Finalized in 21 |
| Structured concurrency | 21, 22, 23, 24 | Still preview as of Java 25 |
| String templates | 21, 22 | **Withdrawn** — not shipped |
| Unnamed variables & patterns | 21 | Finalized in 22 |
| Primitive types in patterns | 23 (JEP 455), 24 (JEP 488) | Still preview as of Java 25 (re-previewed as JEP 507) |

Incubator module examples:

```java
// Vector API (jdk.incubator.vector) — SIMD-style computation, still incubating
// across many releases (first incubated in Java 16, still incubating in Java 25).
import jdk.incubator.vector.*;

// Foreign Function & Memory API started as incubator/preview (Java 17-21)
// and was finalized as a standard feature in Java 22.
```

Code-review guidance: preview and incubator features are **not recommended for production code** unless the team has explicitly decided to accept the risk (API changes, or the feature being withdrawn entirely, as happened with string templates). Flag any use of `--enable-preview` in a build file as something that needs a deliberate, documented decision.

---

## Deprecated and Removed Features Across Java Versions

Java evolves by deprecating old APIs before removing them, usually giving developers multiple releases of warning. **Deprecated** means "still works, but don't use it — it may be removed." **Removed** means "it's actually gone; the code won't compile or run."

```java
@Deprecated(since = "9", forRemoval = true)
public void oldMethod() {
    // ...
}
```

```java
// Calling a deprecated method produces a compiler warning:
obj.oldMethod();
// warning: [deprecation] oldMethod() in Foo has been deprecated and marked for removal
```

Notable deprecations and removals:

| Feature | Status | Since / Removed in | Why |
|---|---|---|---|
| `Object.finalize()` | Deprecated for removal | Deprecated in Java 9, deprecated-for-removal in Java 18 | Unpredictable timing, GC overhead, security issues. Use `try-with-resources` or `Cleaner` instead. |
| Security Manager (`SecurityManager`, `System.setSecurityManager`) | Removed | Deprecated for removal in Java 17, removed in Java 24 | Rarely used correctly in practice, and complicated the JVM. No replacement — sandboxing should happen outside the JVM (containers, OS-level controls). |
| Applets (`java.applet`) | Removed | Deprecated in Java 9, removed in Java 17 | Browsers dropped plugin support (NPAPI); applets became unusable. |
| `Thread.stop()`, `Thread.suspend()`, `Thread.resume()` | Deprecated for removal | Deprecated since Java 1.2, still present but strongly discouraged | Inherently unsafe — can leave shared state in a corrupted, half-updated condition. Use cooperative cancellation (`volatile` flags, `interrupt()`) instead. |
| `new Integer(...)`, `new Long(...)`, `new Boolean(...)`, etc. (boxed-type constructors) | Deprecated for removal | Deprecated in Java 9 | Wastes memory by creating unnecessary objects; `valueOf(...)` (or autoboxing) reuses cached instances for common values. |
| Nashorn JavaScript engine (`javax.script`, `jjs`) | Removed | Deprecated in Java 11, removed in Java 15 | Fell behind modern JavaScript (ES6+); GraalVM's JS engine is the modern replacement. |
| `Runtime.exec(String)` (single-string overload usage patterns) | Still present, but risky usage patterns discouraged | Ongoing guidance, not removed | Splitting a single command string on spaces is fragile and can be an injection risk; prefer the `String[]` / `ProcessBuilder` form with explicit arguments. |
| Java Web Start / `javaws` | Removed | Removed in Java 11 (was part of Oracle JDK) | Superseded by other deployment approaches; browser plugin model became obsolete. |
| CORBA and Java EE modules (`java.corba`, `java.xml.ws`, `java.xml.bind`, etc.) | Removed | Deprecated in Java 9, removed in Java 11 | Moved out of the JDK; available as separate Maven/Gradle dependencies if still needed. |
| `finalization` mechanism as a whole (the `Finalizable` protocol) | Disabled by default, planned for removal | Disabled by default via `--finalization=disabled` option available since Java 18, deprecated for removal since Java 18 | Same reasons as `finalize()` above — the whole mechanism is being phased out. |
| Biased Locking (JVM internal optimization) | Removed | Deprecated in Java 15, removed in Java 18 | Modern GC and JIT improvements made the added complexity not worth it. |
| `com.sun.security.auth` classes / old JAAS internals | Deprecated | Various | Prefer supported `javax.security.auth` public APIs. |

```java
// BAD (deprecated boxed constructor):
Integer wasteful = new Integer(42); // creates a brand-new object every time

// GOOD (uses the internal cache for common values):
Integer efficient = Integer.valueOf(42);
Integer autoboxed = 42; // compiler calls Integer.valueOf() automatically
```

```java
// BAD (Thread.stop is unsafe — can corrupt shared state mid-update):
// worker.stop();

// GOOD (cooperative cancellation):
class Worker implements Runnable {
    private volatile boolean running = true;

    public void run() {
        while (running) {
            // do work
        }
    }

    public void shutdown() {
        running = false; // worker checks this flag and exits cleanly
    }
}
```

Code review tip: when you see `@Deprecated` in your own project's code (not a library), check the Javadoc for `forRemoval = true` and a `since` version. That tells you how urgent the migration is — `forRemoval = true` means it is scheduled to actually disappear.

---

## Common Code-Review Interview Pitfalls

1. **Using `==` to compare boxed types or records instead of `equals()`.**
   Why it matters: `Integer`, `Long`, and record instances are value-based; identity comparison is misleading and can pass by accident for small cached values, then fail in production for larger ones.
   ```java
   // Before (bug hiding in plain sight)
   Integer a = 1000, b = 1000;
   if (a == b) { /* false! outside Integer cache range */ }

   // After
   if (a.equals(b)) { /* correctly true */ }
   ```

2. **Forgetting `permits` doesn't limit exhaustiveness — missing a `default` on a non-sealed `switch`.**
   Why it matters: only sealed hierarchies and enums give you compiler-enforced exhaustiveness. A `switch` over a plain interface or class still needs a `default`, or it won't compile as an expression.
   ```java
   // Before: compile error, no default and Shape is not sealed
   // String s = switch (shape) { case Circle c -> "circle"; };

   // After: either seal Shape, or add a default
   String s = switch (shape) {
       case Circle c -> "circle";
       default -> "other";
   };
   ```

3. **Assuming records are always the right replacement for a class.**
   Why it matters: records cannot extend another class, and all fields are immutable. If a candidate needs inheritance or mutable state, a record is the wrong tool.
   ```java
   // Before: trying to add mutable state to a record — won't compile
   // record Counter(int count) { void increment() { count++; } } // ERROR

   // After: use a regular class for mutable state
   class Counter {
       private int count;
       void increment() { count++; }
   }
   ```

4. **Believing string templates (`STR."..."`) are usable today.**
   Why it matters: this feature was previewed in Java 21/22 and then withdrawn. Code using `STR."..."` will not compile on any released JDK, including 21 through 25.
   ```java
   // Before (does not compile on any released JDK):
   // String msg = STR."Hello \{name}";

   // After (works today):
   String msg = "Hello %s".formatted(name);
   ```

5. **Shipping preview-feature code without `--enable-preview` documented in the build.**
   Why it matters: preview class files are tagged and will fail to run on a JDK that isn't the exact matching preview-enabled version — this can break CI/CD or production silently.
   ```java
   // Before: build script has no mention of preview, but code uses primitive patterns
   // (compiles locally with a preview-enabled IDE, fails in CI)

   // After: explicit and documented
   // javac --release 23 --enable-preview Main.java
   // java --enable-preview Main
   ```

6. **Synchronizing on a value-based class instance (e.g. `Integer`, `Long`, boxed values).**
   Why it matters: value-based instances may be cached or otherwise shared; two "different" locks might secretly be the same object, or a future JDK might change caching behavior, causing subtle deadlocks or lost synchronization.
   ```java
   // Before
   Integer lock = 1;
   synchronized (lock) { /* risky: cached instance could be shared elsewhere */ }

   // After
   private final Object lock = new Object();
   synchronized (lock) { /* safe: unique identity, never reused */ }
   ```

7. **Using `Thread.stop()` or relying on `finalize()` for cleanup.**
   Why it matters: both are deprecated for removal and unsafe/unreliable. Reviewers should flag any use immediately.
   ```java
   // Before
   // worker.stop(); // can corrupt shared state
   // protected void finalize() { resource.close(); } // timing not guaranteed

   // After
   worker.requestShutdown(); // cooperative flag
   try (var resource = openResource()) { /* auto-closed */ }
   ```

8. **Treating `default` interface methods as free multiple inheritance without checking for conflicts.**
   Why it matters: implementing two interfaces with clashing `default` methods forces the implementing class to resolve the conflict explicitly, or the code won't compile — this is easy to overlook when interfaces evolve independently.
   ```java
   // Before: compile error if both A and B declare default String name()
   // class C implements A, B { }

   // After: explicitly resolve
   class C implements A, B {
       public String name() { return A.super.name(); }
   }
   ```

9. **Forgetting that text block indentation depends on the closing `"""` position.**
   Why it matters: moving the closing delimiter changes the output's leading whitespace — a common source of "why does my JSON/SQL have extra spaces" bugs.
   ```java
   // Before: closing """ at column 0 adds no stripping, keeping unwanted indentation
   String s = """
           line1
   """;

   // After: align closing delimiter with the intended left margin
   String s = """
           line1
           """;
   ```

10. **Using an unnamed variable `_` and then trying to read it.**
    Why it matters: `_` is not a normal variable — the compiler forbids referencing it after declaration. Reusing multiple `_` in the same scope is fine (that's the point), but reading it is not.
    ```java
    // Before: compile error
    // map.forEach((key, _) -> System.out.println(_)); // ERROR: cannot use _ as a value

    // After
    map.forEach((key, _) -> System.out.println(key));
    ```

11. **Assuming an enum's `ordinal()` is a stable identifier for persistence.**
    Why it matters: `ordinal()` reflects declaration order. Reordering or inserting a new constant silently shifts every ordinal after it, corrupting any stored data (database rows, serialized files) that relied on the old numbering.
    ```java
    // Before: storing ordinal() as a DB value
    // int code = status.ordinal(); // fragile: breaks if enum order changes

    // After: store an explicit, stable code
    enum OrderStatus {
        NEW(1), PAID(2), SHIPPED(3);
        final int code;
        OrderStatus(int code) { this.code = code; }
    }
    ```

12. **Marking a class `sealed` but forgetting `permits` subclasses must each declare `final`, `sealed`, or `non-sealed`.**
    Why it matters: this is a compile error waiting to happen for anyone extending a sealed type for the first time, and reviewers should know it's mandatory, not optional style.
    ```java
    // Before: compile error — Car doesn't declare its inheritance modifier
    // sealed class Vehicle permits Car { }
    // class Car extends Vehicle { } // ERROR

    // After
    sealed class Vehicle permits Car { }
    final class Car extends Vehicle { }
    ```

13. **Confusing "deprecated" with "removed" and assuming old code will "just still work."**
    Why it matters: some features (Security Manager, Applets, Nashorn, CORBA modules) are fully removed, not just discouraged. Code or dependencies relying on them will fail to build or run on modern JDKs, which matters a lot when reviewing an upgrade PR.
    ```java
    // Before (Java 8-era code, will NOT run on Java 24+):
    // System.setSecurityManager(new SecurityManager()); // REMOVED in Java 24

    // After: use OS/container-level sandboxing instead
    // (no direct JVM replacement — redesign the isolation strategy)
    ```

14. **Using `new Integer(...)` / `new Boolean(...)` style boxed constructors in new code.**
    Why it matters: these constructors are deprecated for removal and always allocate a new object, defeating the JVM's integer cache and wasting memory for no benefit.
    ```java
    // Before
    Boolean flag = new Boolean(true); // deprecated, wasteful

    // After
    Boolean flag = Boolean.TRUE; // or plain `true` with autoboxing
    ```

15. **Overusing guarded patterns (`case X when ...`) instead of simple boolean logic, hurting readability.**
    Why it matters: pattern matching in `switch` is powerful, but stacking many `when` clauses with complex conditions can make a `switch` harder to read than a plain `if/else` chain — reviewers should ask whether the pattern actually clarifies intent.
    ```java
    // Before: hard to scan, conditions bury the intent
    String result = switch (shape) {
        case Circle c when c.radius() > 100 && c.radius() < 1000 && isValid(c) -> "big circle";
        default -> "other";
    };

    // After: extract the condition to a named method for clarity
    String result = switch (shape) {
        case Circle c when isBigValidCircle(c) -> "big circle";
        default -> "other";
    };
    ```
