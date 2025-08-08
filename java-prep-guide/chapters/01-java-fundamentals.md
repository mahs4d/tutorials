# 1. Java Fundamentals

Every code review starts with the basics: how data is stored, how variables behave, and how control flow is structured. Get these wrong and everything built on top of them — collections, streams, concurrency — inherits the bug. This chapter is a fast refresher on the building blocks of Java, written for someone who already knows the language but wants to review it sharply before a code-review-style interview. Examples target Java 21+, with notes where older or newer versions differ.

## Table of Contents

- [Primitive Data Types](#primitive-data-types)
- [Variables, Scope, and Lifetime](#variables-scope-and-lifetime)
- [Operators and Expressions](#operators-and-expressions)
- [Control Flow Statements](#control-flow-statements)
- [Methods and Parameter Passing](#methods-and-parameter-passing)
- [Arrays](#arrays)
- [Strings, StringBuilder, and StringBuffer](#strings-stringbuilder-and-stringbuffer)
- [Autoboxing and Unboxing](#autoboxing-and-unboxing)
- [Varargs](#varargs)
- [Local Variable Type Inference (var)](#local-variable-type-inference-var)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Primitive Data Types

A **primitive type** is a basic building block for data — not an object, just a raw value stored directly in memory (on the stack, or inline inside an object). Java has eight of them. They're fast and predictable because there's no object overhead: no header, no pointer indirection, no garbage collection tracking.

| Type | Size | Range | Default value |
|---|---|---|---|
| `byte` | 8 bits | -128 to 127 | `0` |
| `short` | 16 bits | -32,768 to 32,767 | `0` |
| `int` | 32 bits | -2,147,483,648 to 2,147,483,647 | `0` |
| `long` | 64 bits | ~-9.2×10^18 to 9.2×10^18 | `0L` |
| `float` | 32 bits | ~±3.4×10^38 (7 sig. digits) | `0.0f` |
| `double` | 64 bits | ~±1.8×10^308 (15 sig. digits) | `0.0d` |
| `char` | 16 bits | 0 to 65,535 (unsigned, UTF-16 code unit) | `'\u0000'` |
| `boolean` | JVM-dependent (not specified) | `true` / `false` | `false` |

```java
public class PrimitiveBasics {
    public static void main(String[] args) {
        byte smallCount = 100;
        int userId = 4_500_231;          // underscores improve readability
        long totalBytes = 9_000_000_000L; // 'L' suffix required beyond int range
        double price = 19.99;
        char grade = 'A';
        boolean isActive = true;

        System.out.println(userId);      // prints: 4500231
        System.out.println((int) grade); // prints: 65 (ASCII code for 'A')
    }
}
```

**Gotcha — integer overflow is silent.** Primitives wrap around instead of throwing an exception:

```java
int max = Integer.MAX_VALUE;
System.out.println(max + 1); // prints: -2147483648 (wraps around, no error!)
```

**Gotcha — floating-point is not exact.** `float` and `double` use binary fractions, so they can't represent every decimal value exactly. This matters a lot for money-related code.

```java
double a = 0.1;
double b = 0.2;
System.out.println(a + b);          // prints: 0.30000000000000004
System.out.println(a + b == 0.3);   // prints: false

// Use BigDecimal for money or exact decimal math
java.math.BigDecimal x = new java.math.BigDecimal("0.1");
java.math.BigDecimal y = new java.math.BigDecimal("0.2");
System.out.println(x.add(y));       // prints: 0.3
```

## Variables, Scope, and Lifetime

A **variable** is a named storage location. **Scope** is *where in the code* a variable's name is visible. **Lifetime** is *how long* the variable's storage actually exists in memory. These two often line up, but not always — that's where bugs hide.

```java
public class ScopeExample {
    static int instanceCounter = 0; // class (static) scope: lives for the whole program

    public static void main(String[] args) {
        int outer = 10; // method scope: lives for the duration of main()

        if (outer > 5) {
            int inner = 20; // block scope: only visible inside this if-block
            System.out.println(outer + inner); // prints: 30
        }

        // System.out.println(inner); // COMPILE ERROR: inner is out of scope here

        for (int i = 0; i < 3; i++) {
            int loopLocal = i * i; // re-created fresh on every iteration
            System.out.println(loopLocal); // prints: 0, then 1, then 4
        }
    }
}
```

Local variables live on the **stack** (or are inlined by the JVM) and disappear when their block ends. Instance fields live as long as their object does, tracked by the **heap** and garbage collector. Static fields live as long as the class is loaded — effectively the whole program run.

**Gotcha — shadowing.** A variable declared in an inner scope can hide (shadow) one with the same name in an outer scope, which is legal for local variables but easy to misread:

```java
public class Shadowing {
    static int value = 100;

    public static void main(String[] args) {
        int value = 5; // shadows the static field within main()
        System.out.println(value);       // prints: 5
        System.out.println(Shadowing.value); // prints: 100 (explicit access to the field)
    }
}
```

**Gotcha — variables must be definitely assigned before use.** Java won't let you read a local variable that might not have been initialized on every path:

```java
int result;
if (Math.random() > 0.5) {
    result = 1;
}
// System.out.println(result); // COMPILE ERROR: result might not have been initialized
```

## Operators and Expressions

An **operator** combines values into an **expression** that produces a new value. Java groups them into arithmetic, relational, logical, bitwise, assignment, and ternary categories. Precedence and evaluation order matter for review because subtle mistakes often hide in dense one-liners.

```java
public class Operators {
    public static void main(String[] args) {
        int a = 7, b = 2;

        System.out.println(a / b);   // prints: 3  (integer division truncates)
        System.out.println(a % b);   // prints: 1  (remainder)
        System.out.println((double) a / b); // prints: 3.5 (cast forces floating-point division)

        boolean eligible = (a > 5) && (b < 10); // logical AND
        System.out.println(eligible); // prints: true

        int shifted = 1 << 4; // bitwise left shift: 1 * 2^4
        System.out.println(shifted); // prints: 16

        String label = (a > b) ? "a wins" : "b wins"; // ternary operator
        System.out.println(label); // prints: a wins
    }
}
```

**Short-circuit evaluation.** `&&` and `||` stop evaluating as soon as the result is known. `&` and `|` (single character, also usable on booleans) always evaluate both sides. This distinction matters when the right-hand side has side effects or could throw:

```java
String input = null;
if (input != null && input.length() > 0) { // safe: length() never called on null
    System.out.println("non-empty");
}

// if (input != null & input.length() > 0) // would throw NullPointerException!
```

**Gotcha — integer division truncates toward zero.**

```java
System.out.println(-7 / 2);  // prints: -3 (not -4; truncates toward zero)
System.out.println(-7 % 2);  // prints: -1 (sign follows the dividend)
```

**Gotcha — mixing `++`/`--` inside larger expressions is legal but hard to read.**

```java
int i = 5;
int j = i++ + ++i; // i++ uses 5, then i becomes 6; ++i makes it 7 and uses 7
System.out.println(j); // prints: 12
System.out.println(i); // prints: 7
```

Avoid writing code like this in production — it compiles fine but reviewers should flag it for clarity.

## Control Flow Statements

**Control flow** statements decide which code runs, and how many times. Java's core set is `if`/`else`, `switch`, `for`, `while`, `do-while`, plus `break`, `continue`, and (since Java 14) the **switch expression**.

```java
public class ControlFlow {
    public static void main(String[] args) {
        int score = 82;

        // Classic if/else chain
        String grade;
        if (score >= 90) {
            grade = "A";
        } else if (score >= 80) {
            grade = "B";
        } else {
            grade = "C";
        }
        System.out.println(grade); // prints: B

        // Traditional switch statement (fall-through by default)
        switch (grade) {
            case "A":
                System.out.println("Excellent");
                break; // without break, execution falls into the next case!
            case "B":
                System.out.println("Good");
                break;
            default:
                System.out.println("Keep trying");
        }

        // Modern switch expression (Java 14+): no fall-through, returns a value
        String message = switch (grade) {
            case "A" -> "Excellent";
            case "B" -> "Good";
            default -> "Keep trying";
        };
        System.out.println(message); // prints: Good

        // Loops
        for (int i = 0; i < 3; i++) {
            System.out.print(i + " "); // prints: 0 1 2
        }
        System.out.println();

        int n = 3;
        while (n > 0) {
            System.out.print(n + " "); // prints: 3 2 1
            n--;
        }
        System.out.println();

        // do-while always runs the body at least once
        int attempts = 0;
        do {
            attempts++;
        } while (attempts < 0); // condition false immediately, but body ran once
        System.out.println(attempts); // prints: 1
    }
}
```

**Gotcha — switch fall-through.** Forgetting `break` in a classic `switch` statement lets execution continue into the next case:

```java
int day = 6;
switch (day) {
    case 6:
    case 7:
        System.out.println("Weekend");
        break;
    default:
        System.out.println("Weekday");
}
// prints: Weekend (cases 6 and 7 intentionally share the same code — this is fine)
```

Prefer the arrow-style `switch` expression in new code: it removes fall-through entirely and forces every path to return a value (the compiler checks exhaustiveness for enums and sealed types).

**Gotcha — labeled break/continue.** Rarely used, but useful for breaking out of nested loops:

```java
outer:
for (int i = 0; i < 3; i++) {
    for (int j = 0; j < 3; j++) {
        if (j == 1) {
            continue outer; // skips to the next i, not just the next j
        }
        System.out.println(i + "," + j);
    }
}
// prints: 0,0  1,0  2,0
```

## Methods and Parameter Passing

A **method** is a named, reusable block of code. Java is famous for one rule that trips up almost every interview candidate: **Java is always pass-by-value.** What gets copied differs for primitives versus objects, and that difference explains most of the confusion.

- For **primitives**, the value itself is copied. Changes inside the method never affect the caller's variable.
- For **objects**, the *reference* (the "address" pointing to the object) is copied — not the object itself. The method can use that reference to mutate the object's internal state, but it cannot make the caller's variable point to a different object.

```java
public class ParameterPassing {

    static void incrementPrimitive(int value) {
        value = value + 1; // only modifies the local copy
    }

    static void appendToList(java.util.List<String> list) {
        list.add("added inside method"); // mutates the SAME object the caller sees
    }

    static void reassignReference(java.util.List<String> list) {
        list = new java.util.ArrayList<>(); // rebinds the LOCAL copy of the reference only
        list.add("this is invisible to the caller");
    }

    public static void main(String[] args) {
        int number = 5;
        incrementPrimitive(number);
        System.out.println(number); // prints: 5 (unchanged)

        java.util.List<String> names = new java.util.ArrayList<>();
        names.add("Alice");
        appendToList(names);
        System.out.println(names); // prints: [Alice, added inside method]

        reassignReference(names);
        System.out.println(names); // prints: [Alice, added inside method] (unchanged!)
    }
}
```

Think of a reference like a piece of paper with a house address written on it. Passing it to a method hands over a *photocopy* of that paper. The method can walk to the house and rearrange the furniture (mutate the object) — everyone with a copy of the address sees the new furniture. But if the method writes a *different* address on its photocopy, that only affects its own slip of paper; your original paper still points to the old house.

**Gotcha — "pass by reference" is a myth in Java.** There is no way to make a method change which object a caller's variable points to, without returning a new value:

```java
static void swap(String a, String b) {
    String temp = a;
    a = b;
    b = temp; // only swaps the local copies
}

String x = "first";
String y = "second";
swap(x, y);
System.out.println(x + " " + y); // prints: first second (NOT swapped)
```

**Method overloading** lets multiple methods share a name if their parameter lists differ. It's resolved at compile time based on the declared (static) types of the arguments.

```java
static void log(int code) {
    System.out.println("int overload: " + code);
}

static void log(String message) {
    System.out.println("String overload: " + message);
}

log(404);      // prints: int overload: 404
log("Error");  // prints: String overload: Error
```

## Arrays

An **array** is a fixed-size, ordered container of elements of the same type, stored contiguously in memory. Once created, its length can never change — that's the key trade-off against dynamic structures like `ArrayList`.

```java
public class ArrayBasics {
    public static void main(String[] args) {
        int[] scores = new int[3];      // all elements default to 0
        scores[0] = 90;
        scores[1] = 75;
        scores[2] = 88;

        int[] literalScores = {90, 75, 88}; // shorthand array literal

        System.out.println(scores.length); // prints: 3
        System.out.println(java.util.Arrays.toString(scores)); // prints: [90, 75, 88]

        // 2D array: an array of arrays
        int[][] grid = {
            {1, 2, 3},
            {4, 5, 6}
        };
        System.out.println(grid[1][2]); // prints: 6

        // Iterating
        for (int score : literalScores) {
            System.out.print(score + " "); // prints: 90 75 88
        }
        System.out.println();
    }
}
```

**Gotcha — arrays of objects default to `null`, not empty objects.**

```java
String[] names = new String[3];
System.out.println(names[0]); // prints: null
// names[0].length(); // throws NullPointerException
```

**Gotcha — `ArrayIndexOutOfBoundsException` is a runtime error, not a compile-time one.**

```java
int[] values = {1, 2, 3};
// System.out.println(values[3]); // throws: ArrayIndexOutOfBoundsException: Index 3 out of bounds for length 3
```

**Gotcha — arrays are covariant, which can cause a runtime exception.** `String[]` is a subtype of `Object[]`, so you can assign one to the other — but the JVM still remembers the real element type and enforces it at runtime:

```java
Object[] objects = new String[3]; // legal: array covariance
// objects[0] = Integer.valueOf(42); // throws: ArrayStoreException at runtime
```

**Gotcha — `==` compares array references, not contents; `equals()` on arrays does too.** Use `Arrays.equals()` for content comparison:

```java
int[] a = {1, 2, 3};
int[] b = {1, 2, 3};
System.out.println(a == b);                       // prints: false
System.out.println(a.equals(b));                  // prints: false (Object.equals, reference check)
System.out.println(java.util.Arrays.equals(a, b)); // prints: true
```

## Strings, StringBuilder, and StringBuffer

A `String` in Java is **immutable** — once created, its contents can never change. Every "modification" (`concat`, `substring`, `replace`, `+`) actually creates a brand-new `String` object. This is why heavy string manipulation should avoid `String` and use `StringBuilder` or `StringBuffer` instead — mutable, resizable character containers.

```java
public class StringBasics {
    public static void main(String[] args) {
        String greeting = "Hello";
        String upper = greeting.toUpperCase(); // returns a NEW String
        System.out.println(greeting); // prints: Hello (unchanged)
        System.out.println(upper);    // prints: HELLO

        // StringBuilder: mutable, efficient for repeated concatenation
        StringBuilder sb = new StringBuilder();
        for (int i = 1; i <= 3; i++) {
            sb.append("item").append(i).append(",");
        }
        sb.deleteCharAt(sb.length() - 1); // remove trailing comma
        System.out.println(sb.toString()); // prints: item1,item2,item3
    }
}
```

**The string pool.** String literals are interned (cached and reused) in a special pool. Two literals with the same content share the same object; objects created with `new String(...)` do not.

```java
String a = "cache";
String b = "cache";
System.out.println(a == b); // prints: true (same pooled literal)

String c = new String("cache");
System.out.println(a == c);          // prints: false (different object on the heap)
System.out.println(a.equals(c));     // prints: true (same content)
```

**Rule of thumb:** always compare strings with `.equals()`, never `==`, unless you specifically know you're comparing interned constants.

| Feature | `String` | `StringBuilder` | `StringBuffer` |
|---|---|---|---|
| Mutable? | No | Yes | Yes |
| Thread-safe? | Yes (immutable objects are inherently safe) | No | Yes (synchronized methods) |
| Performance | Slow for repeated edits (new object each time) | Fast | Slower than `StringBuilder` due to locking |
| Typical use | Fixed or rarely-changed text | Single-threaded string building | Shared mutable buffer across threads (rare today) |
| Introduced | Java 1.0 | Java 1.5 | Java 1.0 |

In modern code, `StringBuffer` is mostly legacy. If you don't need cross-thread safety (and usually you don't, since string building is typically local to one method), use `StringBuilder`.

**Gotcha — `+` in a loop is quietly expensive.** The compiler optimizes a single chained `+` expression into a `StringBuilder` automatically, but each iteration of a loop using `+=` on a `String` creates a brand-new object every time:

```java
String result = "";
for (int i = 0; i < 5; i++) {
    result += i; // creates a new String object on every iteration — O(n^2) overall
}
System.out.println(result); // prints: 01234
// Better: use a StringBuilder and call .append(i) in the loop
```

**Gotcha — text blocks (Java 15+)** make multi-line strings much cleaner, but indentation is significant:

```java
String json = """
    {
      "name": "Ada"
    }
    """;
System.out.println(json.strip());
// prints:
// {
//   "name": "Ada"
// }
```

## Autoboxing and Unboxing

**Autoboxing** is the automatic conversion of a primitive value into its corresponding **wrapper object** (e.g., `int` → `Integer`). **Unboxing** is the reverse. The compiler inserts these conversions for you, which is convenient — but it hides real object creation and real `NullPointerException` risk.

| Primitive | Wrapper class |
|---|---|
| `byte` | `Byte` |
| `short` | `Short` |
| `int` | `Integer` |
| `long` | `Long` |
| `float` | `Float` |
| `double` | `Double` |
| `char` | `Character` |
| `boolean` | `Boolean` |

```java
public class BoxingBasics {
    public static void main(String[] args) {
        int primitiveValue = 42;
        Integer boxed = primitiveValue;     // autoboxing: int -> Integer
        int unboxed = boxed;                // unboxing: Integer -> int

        java.util.List<Integer> numbers = new java.util.ArrayList<>();
        numbers.add(10); // autoboxed to Integer before being stored
        int first = numbers.get(0); // unboxed back to int
        System.out.println(first); // prints: 10
    }
}
```

**Gotcha — unboxing `null` throws `NullPointerException`.** This is one of the most common review findings in real codebases:

```java
Integer count = null;
// int total = count + 1; // throws NullPointerException during unboxing!

Map<String, Integer> inventory = new HashMap<>();
// int qty = inventory.get("widget"); // NPE if "widget" isn't in the map (get returns null)
int qty = inventory.getOrDefault("widget", 0); // safe
```

**Gotcha — the Integer cache means `==` sometimes "works" and sometimes doesn't.** The JVM caches boxed `Integer` values from -128 to 127. Comparing cached values with `==` accidentally passes; comparing larger values fails:

```java
Integer a = 100;
Integer b = 100;
System.out.println(a == b); // prints: true (both pulled from the internal cache)

Integer c = 200;
Integer d = 200;
System.out.println(c == d); // prints: false (outside cache range, different objects)

System.out.println(c.equals(d)); // prints: true (always correct — compare wrapper objects with equals)
```

**Gotcha — mixing primitives and wrappers in arithmetic can silently unbox and throw.** Also, autoboxing in tight loops (e.g., `Long sum = 0L; sum += i;` inside a million-iteration loop) creates a huge number of short-lived wrapper objects, hurting performance. Prefer primitive accumulator variables in hot loops.

## Varargs

**Varargs** (variable-length arguments) let a method accept zero or more arguments of a type without the caller having to build an array explicitly. Internally, the compiler packages the arguments into an array. The syntax is `Type... name`, and it must be the *last* parameter in the method signature.

```java
public class VarargsBasics {

    static int sum(int... numbers) {
        int total = 0;
        for (int n : numbers) {
            total += n;
        }
        return total;
    }

    public static void main(String[] args) {
        System.out.println(sum());           // prints: 0  (zero arguments is allowed)
        System.out.println(sum(5));           // prints: 5
        System.out.println(sum(1, 2, 3, 4));   // prints: 10

        int[] existingArray = {10, 20, 30};
        System.out.println(sum(existingArray)); // prints: 60 (an array can be passed directly)
    }
}
```

You've already used varargs if you've called `String.format(...)`, `List.of(...)`, or `System.out.printf(...)`.

**Gotcha — varargs and overload resolution can be ambiguous or surprising.** A specific-arity overload is always preferred over the varargs version when both match:

```java
static void report(int a, int b) {
    System.out.println("specific overload");
}

static void report(int... values) {
    System.out.println("varargs overload");
}

report(1, 2); // prints: specific overload (exact match wins over varargs)
report(1, 2, 3); // prints: varargs overload (no exact match exists)
```

**Gotcha — mixing varargs with generics risks heap pollution.** The compiler warns about "unchecked generics array creation" because a varargs array of a generic type can be assigned to `Object[]` and have incompatible elements inserted, breaking type safety at runtime. Annotate such methods with `@SafeVarargs` only if you've verified the method doesn't misuse the array.

```java
@SafeVarargs
static <T> java.util.List<T> listOf(T... items) {
    return java.util.Arrays.asList(items);
}
```

**Gotcha — an ambiguous null argument.** Passing `null` directly to a varargs method is ambiguous to a human reader (and sometimes to the compiler, if multiple overloads exist) — it's treated as a `null` *array*, not a single `null` element:

```java
static void printAll(String... items) {
    System.out.println(items == null ? "null array" : items.length + " items");
}

printAll(null); // prints: null array (the whole array reference is null)
printAll((String) null); // prints: 1 items (a single-element array containing null)
```

## Local Variable Type Inference (var)

Since Java 10, the `var` keyword lets the compiler infer a local variable's type from the right-hand side of its initialization. It is **not** dynamic typing — the type is fixed at compile time, just written implicitly. `var` only works for local variables with an initializer; it cannot be used for fields, method parameters, or return types.

```java
public class VarBasics {
    public static void main(String[] args) {
        var name = "Alice";                 // inferred as String
        var count = 10;                      // inferred as int
        var prices = new java.util.ArrayList<Double>(); // inferred as ArrayList<Double>
        prices.add(19.99);

        // name = 42; // COMPILE ERROR: name's type is fixed as String, not dynamic

        for (var price : prices) { // var also works in for-each loops
            System.out.println(price); // prints: 19.99
        }
    }
}
```

**Gotcha — `var` requires an initializer, and can't infer from `null` alone.**

```java
// var x; // COMPILE ERROR: cannot infer type without an initializer
// var y = null; // COMPILE ERROR: cannot infer type from null
var z = (String) null; // legal: explicit cast tells the compiler the type
```

**Gotcha — `var` can obscure the actual type in a review, hurting readability.** This compiles but is a common review comment:

```java
var result = service.process(request); // what type is "result"? Not obvious from this line alone.
```

`var` is best used when the type is already obvious from the right-hand side (e.g., `var list = new ArrayList<String>();`) and best avoided when it hides meaningful information (e.g., the return type of a method call the reader doesn't already know).

**Gotcha — `var` with diamond operator loses generic type information if there's no explicit type argument.**

```java
var list = new ArrayList<>(); // inferred as ArrayList<Object>, NOT the type you might expect
list.add("text");
list.add(42); // compiles fine — the list is untyped (Object), losing compile-time safety
```

## Common Code-Review Interview Pitfalls

1. **Using `==` to compare `String` or wrapper objects instead of `.equals()`.** Why it matters: `==` compares references (or cached instances), not logical content, and fails unpredictably outside the small-integer cache or literal pool.
   ```java
   // Before
   if (userInput == "admin") { ... }
   // After
   if ("admin".equals(userInput)) { ... }
   ```

2. **Unboxing a possibly-null wrapper type.** Why it matters: it throws `NullPointerException` at the unboxing point, often far from where the `null` originated, making the bug hard to trace.
   ```java
   // Before
   int total = getCachedCount(id) + 1; // getCachedCount may return null
   // After
   int total = Optional.ofNullable(getCachedCount(id)).orElse(0) + 1;
   ```

3. **Building strings with `+=` inside a loop.** Why it matters: each iteration allocates a new `String`, turning an O(n) loop into O(n²) work.
   ```java
   // Before
   String csv = "";
   for (String s : items) csv += s + ",";
   // After
   StringBuilder csv = new StringBuilder();
   for (String s : items) csv.append(s).append(",");
   ```

4. **Forgetting `break` in a classic `switch` statement.** Why it matters: execution silently falls through into the next case, running unintended code.
   ```java
   // Before
   switch (status) {
       case ACTIVE: enable(); // falls through to PENDING's code too!
       case PENDING: notify();
   }
   // After: add break, or better, use a switch expression with -> arrows
   ```

5. **Assuming a method can reassign the caller's object reference.** Why it matters: Java is pass-by-value for references, so reassigning a parameter inside a method never affects the caller's variable — this is a frequent source of "why didn't my fix take effect" bugs.
   ```java
   // Before (does nothing useful)
   static void reset(List<String> list) { list = new ArrayList<>(); }
   // After
   static List<String> reset(List<String> list) { return new ArrayList<>(); }
   ```

6. **Relying on floating-point (`double`/`float`) for monetary or exact decimal calculations.** Why it matters: binary floating-point can't represent many decimal fractions exactly, causing rounding errors that compound over many operations.
   ```java
   // Before
   double total = 0.1 + 0.2; // 0.30000000000000004
   // After
   BigDecimal total = new BigDecimal("0.1").add(new BigDecimal("0.2")); // 0.3
   ```

7. **Ignoring silent integer overflow.** Why it matters: arithmetic that exceeds a type's range wraps around without any warning or exception, producing corrupted values that pass silently into further logic.
   ```java
   // Before
   int total = bigValue1 * bigValue2; // may silently overflow
   // After
   long total = Math.multiplyExact((long) bigValue1, bigValue2); // throws on overflow
   ```

8. **Using `var` where the inferred type isn't obvious to the reader.** Why it matters: it trades a small typing convenience for reduced readability, especially with method calls whose return type isn't well known.
   ```java
   // Before
   var result = repository.fetch(id); // unclear type at a glance
   // After
   CustomerRecord result = repository.fetch(id);
   ```

9. **Comparing or checking array contents with `==` or `.equals()`.** Why it matters: arrays don't override `equals()`, so both operators check reference identity, not element-by-element equality — a classic false-negative bug.
   ```java
   // Before
   if (expectedBytes.equals(actualBytes)) { ... } // always compares references
   // After
   if (Arrays.equals(expectedBytes, actualBytes)) { ... }
   ```

10. **Passing a single `null` to a varargs parameter and expecting one null element.** Why it matters: an untyped `null` is interpreted as "no array at all," not "an array containing one null," which can cause a `NullPointerException` inside the method instead of the intended behavior.
    ```java
    // Before
    printAll(null); // passes a null array reference
    // After
    printAll((String) null); // passes a one-element array: { null }
    ```

11. **Not checking for `ArrayIndexOutOfBoundsException` risk on user-controlled indices.** Why it matters: array bounds are only checked at runtime, and code review should catch any index math derived from external input before it reaches the array access.
    ```java
    // Before
    process(items[requestedIndex]); // requestedIndex comes straight from a request param
    // After
    if (requestedIndex >= 0 && requestedIndex < items.length) process(items[requestedIndex]);
    ```

12. **Using `StringBuffer` by default instead of `StringBuilder`.** Why it matters: `StringBuffer`'s synchronization overhead is wasted when the builder never leaves a single thread — which is the vast majority of real-world usage.
    ```java
    // Before
    StringBuffer sb = new StringBuffer();
    // After
    StringBuilder sb = new StringBuilder(); // same API, no locking cost
    ```

13. **Autoboxing wrapper types inside performance-critical loops.** Why it matters: each boxing operation allocates a short-lived object, adding GC pressure that's easy to miss because the code looks like plain arithmetic.
    ```java
    // Before
    Long sum = 0L;
    for (int i = 0; i < 1_000_000; i++) sum += i; // boxes/unboxes every iteration
    // After
    long sum = 0L;
    for (int i = 0; i < 1_000_000; i++) sum += i; // stays primitive
    ```

14. **Relying on operator precedence in dense expressions without parentheses.** Why it matters: mixing `&&`/`||`, bitwise operators, or chained `++`/`--` without parentheses makes intent ambiguous to a reviewer, even when the compiler's answer is well-defined.
    ```java
    // Before
    if (a || b && c) { ... } // precedence is correct but not obvious to a reader
    // After
    if (a || (b && c)) { ... }
    ```

15. **Declaring `var` with the diamond operator and no type witness.** Why it matters: `var list = new ArrayList<>();` infers `ArrayList<Object>`, silently discarding the compile-time type safety generics are supposed to provide.
    ```java
    // Before
    var names = new ArrayList<>(); // ArrayList<Object> — accepts anything
    // After
    var names = new ArrayList<String>(); // ArrayList<String> — type-safe
    ```
