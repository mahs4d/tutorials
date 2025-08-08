# 8. Functional Programming

Java added lambdas and the Streams API in Java 8, and it has kept building on them ever since. These features let you write code that describes *what* you want to happen instead of *how* to loop and mutate to get there. This style is called "functional programming" — you pass behavior around as values, instead of only passing data. This chapter is a fast, practical tour of the pieces you need to read and write this kind of code confidently, and to catch the subtle mistakes reviewers are expected to flag.

All examples target Java 21+.

## Table of Contents

- [Functional Interfaces](#functional-interfaces)
- [Lambda Expressions](#lambda-expressions)
- [Method References](#method-references)
- [Streams API](#streams-api)
- [Collectors](#collectors)
- [Spliterator](#spliterator)
- [Stream Performance](#stream-performance)
- [Parallel Streams](#parallel-streams)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Functional Interfaces

A **functional interface** is an interface with exactly one abstract method (often called a "SAM" — single abstract method). It can have any number of `default` or `static` methods too — those don't count. Java uses functional interfaces as the "shape" that a lambda expression or method reference plugs into.

The `@FunctionalInterface` annotation is optional, but you should always add it to interfaces you design for this purpose. It doesn't change runtime behavior — it just tells the compiler "this interface must have exactly one abstract method." If someone later adds a second abstract method, the build fails immediately instead of breaking lambdas elsewhere in the codebase at a mysterious call site.

```java
@FunctionalInterface
interface Validator<T> {
    boolean isValid(T value);

    // default methods are allowed - they don't break the "single abstract method" rule
    default Validator<T> negate() {
        return value -> !isValid(value);
    }
}
```

If you try to add a second abstract method, the compiler rejects it:

```java
@FunctionalInterface
interface Broken<T> {
    boolean isValid(T value);
    boolean isInvalid(T value); // compile error: Broken is not a functional interface
}
```

### The `java.util.function` catalogue

Java ships a standard set of general-purpose functional interfaces in `java.util.function` so you rarely need to declare your own. Learning this table is worth it — reviewers expect you to reach for the right one instead of inventing a custom interface every time.

| Interface | Abstract method | Signature (conceptually) | Typical use |
|---|---|---|---|
| `Function<T,R>` | `apply` | `T -> R` | Transform a value |
| `BiFunction<T,U,R>` | `apply` | `(T, U) -> R` | Combine two values into one |
| `Supplier<T>` | `get` | `() -> T` | Produce/lazily create a value |
| `Consumer<T>` | `accept` | `T -> void` | Do something with a value, no result |
| `BiConsumer<T,U>` | `accept` | `(T, U) -> void` | Do something with two values |
| `Predicate<T>` | `test` | `T -> boolean` | Yes/no check on a value |
| `BiPredicate<T,U>` | `test` | `(T, U) -> boolean` | Yes/no check on two values |
| `UnaryOperator<T>` | `apply` | `T -> T` | `Function<T,T>` specialization, same input/output type |
| `BinaryOperator<T>` | `apply` | `(T, T) -> T` | `BiFunction<T,T,T>` specialization, used in `reduce` |

Boxing `int`/`long`/`double` into `Integer`/`Long`/`Double` costs memory and CPU (see [Stream Performance](#stream-performance)). To avoid it, the JDK also provides primitive specializations:

| Interface | Purpose |
|---|---|
| `IntFunction<R>`, `LongFunction<R>`, `DoubleFunction<R>` | `int/long/double -> R` |
| `ToIntFunction<T>`, `ToLongFunction<T>`, `ToDoubleFunction<T>` | `T -> int/long/double` |
| `IntUnaryOperator`, `LongUnaryOperator`, `DoubleUnaryOperator` | `int -> int`, etc. |
| `IntBinaryOperator`, `LongBinaryOperator`, `DoubleBinaryOperator` | `(int, int) -> int`, etc. |
| `IntPredicate`, `LongPredicate`, `DoublePredicate` | `int -> boolean`, etc. |
| `IntConsumer`, `LongConsumer`, `DoubleConsumer` | `int -> void`, etc. |
| `IntSupplier`, `LongSupplier`, `DoubleSupplier` | `() -> int`, etc. |
| `ObjIntConsumer<T>`, `ObjLongConsumer<T>`, `ObjDoubleConsumer<T>` | `(T, int) -> void`, etc. |

```java
import java.util.function.*;

Function<String, Integer> length = String::length;
BiFunction<Integer, Integer, Integer> add = Integer::sum;
Supplier<List<String>> newList = ArrayList::new;
Consumer<String> print = System.out::println;
Predicate<String> isBlank = String::isBlank;
UnaryOperator<String> upper = String::toUpperCase;
BinaryOperator<Integer> max = Integer::max;
IntPredicate isEven = n -> n % 2 == 0;

System.out.println(length.apply("hello"));   // 5
System.out.println(add.apply(2, 3));         // 5
System.out.println(isEven.test(4));          // true
```

### Writing your own

Reach for a custom functional interface only when none of the standard ones fit — usually because you need a *named*, self-documenting method, or you need to throw a checked exception.

```java
@FunctionalInterface
interface RowMapper<T> {
    T map(ResultSet row) throws SQLException; // checked exception allowed here
}

RowMapper<String> nameMapper = row -> row.getString("name");
```

## Lambda Expressions

A **lambda expression** is an anonymous, inline implementation of a functional interface's single method. Syntax ranges from terse to explicit:

```java
Runnable r1 = () -> System.out.println("run");                 // no params
Function<Integer, Integer> square = x -> x * x;                // one param, inferred type
Function<Integer, Integer> square2 = (Integer x) -> x * x;      // one param, explicit type
BiFunction<Integer, Integer, Integer> add = (a, b) -> a + b;    // two params, block-less
BiFunction<Integer, Integer, Integer> addBlock = (a, b) -> {    // block body, needs return
    int sum = a + b;
    return sum;
};
```

Rules of thumb:
- Parentheses around a single inferred parameter are optional: `x -> x * x` and `(x) -> x * x` are the same.
- If you type one parameter, type them all (`(a, b)` or `(int a, int b)`, not `(a, int b)`).
- A block body (`{ ... }`) needs an explicit `return` (unless the return type is `void`); an expression body does not.

### Effectively-final capture

A lambda can read local variables from its enclosing scope, but only if those variables are **effectively final** — assigned exactly once, even without the `final` keyword. This is called "capturing" a variable.

```java
int factor = 10; // effectively final: never reassigned
Function<Integer, Integer> scale = x -> x * factor; // OK, captures factor

int counter = 0;
Runnable bad = () -> {
    // counter++; // compile error: counter is not effectively final
};
```

Why the restriction exists: the lambda might run later, or on another thread. Java captures a *snapshot* of the variable's value (for primitives/references) rather than a live reference to the variable itself, so it forbids anything that could make that snapshot stale or ambiguous. If you need a mutable counter across calls, wrap it in a container:

```java
int[] counter = {0};
Runnable increment = () -> counter[0]++; // OK: counter (the array reference) never changes
increment.run();
increment.run();
System.out.println(counter[0]); // 2
```

Fields (instance or static) are exempt from this rule — only *local* variables and parameters must be effectively final.

### `this` semantics vs anonymous classes

A lambda does **not** introduce its own `this`. Inside a lambda, `this` refers to the enclosing instance — exactly as if the lambda's body were pasted directly into the surrounding method. An anonymous inner class, by contrast, introduces a new `this` that refers to the anonymous class instance itself.

```java
class Counter {
    int value = 0;

    Runnable lambdaVersion() {
        return () -> {
            this.value++;          // 'this' is the Counter instance
            System.out.println(this.getClass()); // class Counter
        };
    }

    Runnable anonymousVersion() {
        return new Runnable() {
            int value = 100; // shadows Counter.value inside this class
            @Override
            public void run() {
                this.value++;      // 'this' is the anonymous Runnable
                System.out.println(this.getClass()); // class Counter$1 (anonymous)
            }
        };
    }
}
```

This matters in code review: if you convert an anonymous class to a lambda, double check any use of `this` — the meaning can change and silently start referring to a different object.

### No checked exceptions

None of the standard `java.util.function` interfaces declare `throws`. If the method you're calling inside a lambda throws a checked exception, you must catch it, wrap it in an unchecked exception, or use a custom functional interface that declares `throws`.

```java
List<String> paths = List.of("a.txt", "b.txt");

// Function<String, byte[]> reader = Files::readAllBytes; // won't compile: readAllBytes throws IOException

Function<String, byte[]> reader = path -> {
    try {
        return Files.readAllBytes(Path.of(path));
    } catch (IOException e) {
        throw new UncheckedIOException(e); // wrap checked -> unchecked
    }
};
```

## Method References

A **method reference** is shorthand for a lambda that just calls one existing method. It uses the `::` operator. It's not a separate concept from lambdas — the compiler converts it to the same kind of functional-interface implementation. Use it whenever a lambda would do nothing but forward its argument(s) to an existing method.

| Kind | Syntax | Equivalent lambda | Example |
|---|---|---|---|
| Static method | `ClassName::staticMethod` | `(args) -> ClassName.staticMethod(args)` | `Integer::parseInt` |
| Bound instance method | `instance::method` | `(args) -> instance.method(args)` | `myList::add` |
| Unbound instance method | `ClassName::instanceMethod` | `(obj, args) -> obj.instanceMethod(args)` | `String::toUpperCase` |
| Constructor | `ClassName::new` | `(args) -> new ClassName(args)` | `ArrayList::new` |

```java
// 1. Static method reference
Function<String, Integer> parse = Integer::parseInt;
System.out.println(parse.apply("42")); // 42

// 2. Bound instance method reference - the receiver is a specific, already-existing object
List<String> names = new ArrayList<>();
Consumer<String> addToNames = names::add;
addToNames.accept("Ada");
System.out.println(names); // [Ada]

// 3. Unbound instance method reference - the receiver becomes the first parameter
Function<String, String> toUpper = String::toUpperCase;
System.out.println(toUpper.apply("hi")); // HI

BiFunction<String, String, Boolean> startsWith = String::startsWith;
System.out.println(startsWith.apply("hello", "he")); // true

// 4. Constructor reference
Supplier<List<String>> listFactory = ArrayList::new;
List<String> fresh = listFactory.get();

Function<String, StringBuilder> sbFactory = StringBuilder::new;
System.out.println(sbFactory.apply("go").append("!")); // go!
```

A quick way to tell #2 from #3: if the part before `::` is a *variable* (an existing object), it's bound. If it's a *type name*, it's unbound and the receiver becomes the method's first parameter.

## Streams API

A **stream** is a sequence of elements that supports functional-style operations — `map`, `filter`, `reduce`, and so on — computed in a pipeline. A stream is *not* a data structure; it doesn't store elements. It's a view over a source (a collection, an array, an I/O channel, a generator function) that computes results on demand.

### Creating streams

```java
Stream<String> fromValues = Stream.of("a", "b", "c");
Stream<String> fromList = List.of("a", "b", "c").stream();
Stream<String> fromArray = Arrays.stream(new String[]{"a", "b", "c"});
Stream<String> empty = Stream.empty();
IntStream fromRange = IntStream.range(0, 5);        // 0,1,2,3,4
IntStream fromRangeClosed = IntStream.rangeClosed(1, 5); // 1,2,3,4,5
```

### Intermediate vs terminal operations

**Intermediate operations** (`map`, `filter`, `sorted`, ...) return a new stream and are *lazy* — they don't run anything by themselves. **Terminal operations** (`collect`, `forEach`, `reduce`, `count`, ...) trigger the pipeline to actually execute and produce a result or side effect. A stream pipeline with no terminal operation does nothing at all.

```java
Stream<String> pipeline = List.of("banana", "apple", "cherry").stream()
    .filter(s -> s.length() > 5)  // nothing happens yet
    .map(String::toUpperCase);    // still nothing happens

List<String> result = pipeline.collect(Collectors.toList()); // NOW it runs
System.out.println(result); // [BANANA, CHERRY]
```

### Laziness and short-circuiting

Because intermediate ops are lazy, the pipeline processes one element at a time, end-to-end, rather than materializing an intermediate list at every stage. Combined with **short-circuiting** operations (`findFirst`, `anyMatch`, `limit`, ...), this means a stream can stop early without touching the rest of the source.

```java
Optional<Integer> firstBig = Stream.of(1, 2, 3, 4, 5, 6)
    .peek(n -> System.out.println("checking " + n))
    .filter(n -> n > 3)
    .findFirst();
// Output:
// checking 1
// checking 2
// checking 3
// checking 4
// (stops here - findFirst is short-circuiting)
System.out.println(firstBig); // Optional[4]
```

### `map`, `filter`, `flatMap`, `mapMulti`

- `map`: transform each element 1-to-1.
- `filter`: keep elements matching a `Predicate`.
- `flatMap`: transform each element into a *stream*, then flatten all those streams into one (1-to-many, or 1-to-zero).
- `mapMulti` (Java 16+): an imperative alternative to `flatMap` for cases where building an intermediate stream is wasteful — you push zero or more results into a consumer yourself.

```java
List<List<Integer>> nested = List.of(List.of(1, 2), List.of(3, 4), List.of());

List<Integer> flat = nested.stream()
    .flatMap(List::stream)
    .toList();
System.out.println(flat); // [1, 2, 3, 4]

List<Integer> viaMapMulti = nested.stream()
    .<Integer>mapMulti((list, consumer) -> list.forEach(consumer))
    .toList();
System.out.println(viaMapMulti); // [1, 2, 3, 4]
```

### `sorted`, `distinct`

```java
List<String> words = List.of("pear", "fig", "apple", "fig", "date");

List<String> sortedDistinct = words.stream()
    .distinct()                        // relies on equals()/hashCode()
    .sorted(Comparator.comparing(String::length).thenComparing(Comparator.naturalOrder()))
    .toList();
System.out.println(sortedDistinct); // [fig, date, pear, apple]
```

`sorted()` on a stream is a *stateful* intermediate op — it must buffer the whole stream before it can emit anything, so it doesn't play well with infinite streams unless combined with `limit` first.

### `limit`/`skip`, `takeWhile`/`dropWhile`

`limit`/`skip` cut by *position*. `takeWhile`/`dropWhile` (Java 9+) cut by a *predicate*, and both stop evaluating the predicate as soon as it flips (they assume, but do not enforce, that the input is suitably ordered for the use case).

```java
List<Integer> nums = List.of(1, 2, 3, 10, 4, 5);

System.out.println(nums.stream().skip(1).limit(2).toList());      // [2, 3]
System.out.println(nums.stream().takeWhile(n -> n < 5).toList()); // [1, 2, 3] - stops at 10
System.out.println(nums.stream().dropWhile(n -> n < 5).toList()); // [10, 4, 5] - drops until first >= 5, then keeps rest
```

### `peek`

`peek` lets you look at each element as it flows through, without transforming it. It exists for debugging a pipeline — **not** for side effects that affect the program's result. Two reasons:
1. If the stream is short-circuited (e.g., a later `findFirst`), `peek` may not run for every element.
2. Implementations are free to skip calling `peek` at all if they can prove the pipeline's result doesn't depend on it (an optimization the JDK explicitly reserves the right to make).

```java
// Fine: debugging only, no logic depends on it
long count = Stream.of("a", "bb", "ccc")
    .peek(s -> System.out.println("seen: " + s))
    .count(); // JDK may actually SKIP peek entirely here since count doesn't need the elements!
```

### `reduce`

`reduce` folds a stream down to a single value using a combining function.

```java
List<Integer> nums = List.of(1, 2, 3, 4, 5);

int sum = nums.stream().reduce(0, Integer::sum);               // identity + accumulator
System.out.println(sum); // 15

Optional<Integer> product = nums.stream().reduce((a, b) -> a * b); // no identity -> Optional
System.out.println(product); // Optional[120]

// Three-arg form: identity, accumulator, combiner (combiner used only when running in parallel)
int totalLength = Stream.of("a", "bb", "ccc")
    .reduce(0, (acc, s) -> acc + s.length(), Integer::sum);
System.out.println(totalLength); // 6
```

### `findFirst` vs `findAny`

Both are short-circuiting and return `Optional<T>`. `findFirst` always returns the first matching element in encounter order. `findAny` may return *any* matching element — it exists so that a parallel stream can return as soon as one thread finds a match, without waiting to determine which was truly "first." On a sequential stream they usually behave the same, but only `findFirst` is guaranteed to.

```java
List<Integer> nums = List.of(1, 2, 3, 4, 5);

Optional<Integer> first = nums.parallelStream().filter(n -> n % 2 == 0).findFirst(); // always 2
Optional<Integer> any = nums.parallelStream().filter(n -> n % 2 == 0).findAny();     // 2 or 4, whichever thread finished first
```

### Primitive streams

`IntStream`, `LongStream`, `DoubleStream` avoid boxing overhead and add numeric operations (`sum`, `average`, `max`, `min`, `summaryStatistics`). Convert between object streams and primitive streams with `mapToInt`/`mapToObj`/`boxed`.

```java
int[] values = {3, 1, 4, 1, 5, 9};

IntSummaryStatistics stats = IntStream.of(values).summaryStatistics();
System.out.println(stats.getSum());     // 23
System.out.println(stats.getAverage()); // 3.8333...
System.out.println(stats.getMax());     // 9

List<Integer> boxed = IntStream.of(values).boxed().toList(); // [3, 1, 4, 1, 5, 9]

List<String> words = List.of("a", "bb", "ccc");
int totalChars = words.stream().mapToInt(String::length).sum(); // 6
```

### `Stream.iterate`/`Stream.generate`

Both produce potentially *infinite* streams — always pair them with `limit`, or with `takeWhile` on `iterate`, or you'll hang the program.

```java
List<Integer> powersOfTwo = Stream.iterate(1, n -> n * 2)
    .limit(5)
    .toList();
System.out.println(powersOfTwo); // [1, 2, 4, 8, 16]

// Java 9+ three-arg iterate: seed, hasNext predicate (built-in short-circuit), next function
List<Integer> below100 = Stream.iterate(1, n -> n < 100, n -> n * 2).toList();
System.out.println(below100); // [1, 2, 4, 8, 16, 32, 64]

Stream<Double> randoms = Stream.generate(Math::random).limit(3);
```

### `Optional` interop

Streams and `Optional` are designed to work together. `findFirst`, `reduce` (no identity), `max`, `min` all return `Optional`. `Optional` itself has a `stream()` method (0 or 1 element), handy for flattening a stream of `Optional`s.

```java
List<Optional<String>> maybeNames = List.of(Optional.of("Ada"), Optional.empty(), Optional.of("Linus"));

List<String> presentOnly = maybeNames.stream()
    .flatMap(Optional::stream) // Optional.stream() turns Optional<T> into Stream<T> of 0 or 1 elements
    .toList();
System.out.println(presentOnly); // [Ada, Linus]
```

### Streams are single-use

A stream can be consumed by a terminal operation exactly once. Reusing a stream throws `IllegalStateException`. If you need to run the pipeline twice, build it from the source again.

```java
Stream<String> stream = Stream.of("a", "b", "c");
stream.forEach(System.out::println);
// stream.count(); // throws IllegalStateException: stream has already been operated upon or closed

// Correct: create a new stream from the source each time
List<String> source = List.of("a", "b", "c");
source.stream().forEach(System.out::println);
long count = source.stream().count();
```

## Collectors

A **collector** is a recipe for how to accumulate the elements of a stream into a result — a `List`, a `Map`, a single summary number, a `String`, or anything else. You use one via the terminal `collect(...)` operation. `Collectors` is the factory class of built-in recipes.

### `toList`/`toSet`/`toMap`

```java
List<String> list = Stream.of("a", "b", "a").collect(Collectors.toList()); // mutable, allows duplicates
List<String> immutableList = Stream.of("a", "b", "a").toList();            // Java 16+ shortcut, unmodifiable

Set<String> set = Stream.of("a", "b", "a").collect(Collectors.toSet()); // [a, b] - duplicates dropped

Map<String, Integer> byLength = Stream.of("fig", "date", "kiwi")
    .collect(Collectors.toMap(Function.identity(), String::length));
System.out.println(byLength); // {date=4, fig=3, kiwi=4} (order not guaranteed)
```

`Collectors.toMap` throws `IllegalStateException` if two elements produce the same key — because it has no idea how you'd like the conflict resolved. Fix it by supplying a merge function.

```java
List<String> words = List.of("fig", "kiwi", "date", "pear"); // "fig" and "pear" both length 4? no - kiwi/date/pear length 4

// This throws: two words with the same length collide as keys
// Map<Integer, String> byLen = words.stream()
//     .collect(Collectors.toMap(String::length, w -> w)); // IllegalStateException: Duplicate key 4

// Fix: provide a merge function to resolve collisions
Map<Integer, String> byLenFixed = words.stream()
    .collect(Collectors.toMap(String::length, w -> w, (existing, incoming) -> existing + "," + incoming));
System.out.println(byLenFixed); // {3=fig, 4=kiwi,date,pear}
```

### `groupingBy` with downstream collectors

`groupingBy` buckets elements by a classifier function into a `Map<K, List<T>>` by default. Pass a **downstream collector** as a second argument to summarize each bucket instead of just listing it.

```java
record Employee(String department, String name, double salary) {}

List<Employee> employees = List.of(
    new Employee("Eng", "Ada", 9000),
    new Employee("Eng", "Linus", 9500),
    new Employee("Sales", "Grace", 7000)
);

Map<String, List<String>> namesByDept = employees.stream()
    .collect(Collectors.groupingBy(Employee::department, Collectors.mapping(Employee::name, Collectors.toList())));
System.out.println(namesByDept); // {Eng=[Ada, Linus], Sales=[Grace]}

Map<String, Double> totalSalaryByDept = employees.stream()
    .collect(Collectors.groupingBy(Employee::department, Collectors.summingDouble(Employee::salary)));
System.out.println(totalSalaryByDept); // {Eng=18500.0, Sales=7000.0}
```

### `partitioningBy`

A special case of grouping into exactly two buckets: `true` and `false`. Both keys always appear, even if a bucket is empty.

```java
Map<Boolean, List<Integer>> evenOdd = IntStream.rangeClosed(1, 6).boxed()
    .collect(Collectors.partitioningBy(n -> n % 2 == 0));
System.out.println(evenOdd); // {false=[1, 3, 5], true=[2, 4, 6]}
```

### `joining`, `counting`, `summingInt`, `averagingDouble`

```java
String csv = Stream.of("a", "b", "c").collect(Collectors.joining(", ", "[", "]"));
System.out.println(csv); // [a, b, c]

long count = Stream.of("a", "b", "c").collect(Collectors.counting());       // 3
int totalLen = Stream.of("aa", "b", "ccc").collect(Collectors.summingInt(String::length)); // 6
double avgLen = Stream.of("aa", "b", "ccc").collect(Collectors.averagingDouble(String::length)); // 2.0
```

### `mapping`, `flatMapping`, `filtering`

These are **downstream collectors** — meant to be nested inside `groupingBy`/`partitioningBy` to reshape or pre-process each bucket before the final collector runs.

```java
record Order(String customer, List<String> items) {}

List<Order> orders = List.of(
    new Order("Ada", List.of("pen", "paper")),
    new Order("Ada", List.of("stapler")),
    new Order("Grace", List.of("pen"))
);

// flatMapping: flatten each customer's nested item lists into one flat set
Map<String, Set<String>> itemsByCustomer = orders.stream()
    .collect(Collectors.groupingBy(Order::customer,
             Collectors.flatMapping(o -> o.items().stream(), Collectors.toSet())));
System.out.println(itemsByCustomer); // {Ada=[pen, paper, stapler], Grace=[pen]}

// filtering: keep only orders with more than one item, per customer, before collecting
Map<String, List<Order>> multiItemOrdersByCustomer = orders.stream()
    .collect(Collectors.groupingBy(Order::customer,
             Collectors.filtering(o -> o.items().size() > 1, Collectors.toList())));
System.out.println(multiItemOrdersByCustomer); // {Ada=[Order[customer=Ada, items=[pen, paper]]], Grace=[]}
```

Note the difference from `stream().filter(...)` *before* `groupingBy`: filtering before grouping can make a whole group disappear; `Collectors.filtering` keeps the group key present (as shown above, `Grace=[]` still shows up).

### `teeing`

`teeing` (Java 12+) runs a stream through *two* collectors simultaneously and merges their results with a `BiFunction`. Useful when you need two different summaries from a single pass.

```java
record MinMax(int min, int max) {}

MinMax result = Stream.of(4, 1, 7, 3, 9, 2)
    .collect(Collectors.teeing(
        Collectors.minBy(Integer::compareTo),
        Collectors.maxBy(Integer::compareTo),
        (min, max) -> new MinMax(min.orElseThrow(), max.orElseThrow())
    ));
System.out.println(result); // MinMax[min=1, max=9]
```

### `collectingAndThen`

Wraps a collector and applies a finishing transformation to its result — commonly used to make a mutable result immutable.

```java
List<String> immutableUpper = Stream.of("b", "a", "c")
    .collect(Collectors.collectingAndThen(Collectors.toList(), Collections::unmodifiableList));
// immutableUpper.add("x"); // throws UnsupportedOperationException
```

### Writing a custom Collector

`Collector<T, A, R>` needs four ingredients: a **supplier** (create the mutable accumulator), an **accumulator** (fold one element in), a **combiner** (merge two accumulators — used in parallel streams), and a **finisher** (convert the accumulator to the final result).

```java
Collector<String, StringBuilder, String> customJoiner = Collector.of(
    StringBuilder::new,                    // supplier
    (sb, s) -> sb.append(s).append('|'),   // accumulator
    (sb1, sb2) -> sb1.append(sb2),         // combiner (for parallel merging)
    StringBuilder::toString                // finisher
);

String result = Stream.of("a", "b", "c").collect(customJoiner);
System.out.println(result); // a|b|c|
```

For most cases, though, composing the built-in collectors (as shown above) is clearer and less error-prone than hand-rolling one.

## Spliterator

A **`Spliterator`** ("splitable iterator") is the low-level engine that powers both sequential and parallel stream traversal. You rarely write one yourself, but understanding it explains *why* some sources parallelize well and others don't.

- `tryAdvance(Consumer<? super T> action)`: process the next element if there is one and return `true`; return `false` if the source is exhausted. This is how sequential traversal works — one element at a time, like `Iterator.next()` but pushing the value into a callback instead of returning it.
- `trySplit()`: attempt to carve off a chunk of the remaining elements into a *new* `Spliterator` that a different thread can process, returning `null` if the source can't (or won't) be split further. This is the method parallel streams call to divide work across the common fork/join pool.
- **Characteristics**: a bitmask describing properties of the source — `ORDERED`, `SORTED`, `SIZED` (size known up front), `SUBSIZED` (splits also report exact size), `DISTINCT`, `NONNULL`, `IMMUTABLE`, `CONCURRENT`. Stream operations use these as hints to skip unnecessary work — e.g., `distinct()` on a stream whose spliterator reports `DISTINCT` is a no-op.

```java
List<Integer> data = List.of(1, 2, 3, 4, 5, 6, 7, 8);
Spliterator<Integer> spliterator = data.spliterator();

System.out.println(spliterator.estimateSize()); // 8
System.out.println(spliterator.characteristics() & Spliterator.SIZED); // non-zero: SIZED is set

Spliterator<Integer> firstHalf = spliterator.trySplit(); // spliterator now covers the second half
firstHalf.forEachRemaining(n -> System.out.print(n + " ")); // 1 2 3 4
System.out.println();
spliterator.forEachRemaining(n -> System.out.print(n + " ")); // 5 6 7 8
```

### When you'd implement one

You'd write a custom `Spliterator` when you're exposing a custom data source as a `Stream` — for example, streaming rows from a specialized data structure (a skip list, a memory-mapped file, a custom ring buffer) and you want that stream to parallelize efficiently. You'd implement `trySplit()` to divide the source in a way that respects its actual structure, and report accurate characteristics so downstream operations can optimize correctly. Getting `SIZED`/`SUBSIZED` wrong, for instance, can make parallel splitting produce badly unbalanced chunks.

```java
class RangeSpliterator implements Spliterator<Integer> {
    private int current;
    private final int end;

    RangeSpliterator(int start, int end) {
        this.current = start;
        this.end = end;
    }

    @Override
    public boolean tryAdvance(Consumer<? super Integer> action) {
        if (current >= end) return false;
        action.accept(current++);
        return true;
    }

    @Override
    public Spliterator<Integer> trySplit() {
        int remaining = end - current;
        if (remaining < 2) return null; // too small to split
        int mid = current + remaining / 2;
        Spliterator<Integer> firstHalf = new RangeSpliterator(current, mid);
        this.current = mid; // this spliterator keeps the second half
        return firstHalf;
    }

    @Override
    public long estimateSize() { return end - current; }

    @Override
    public int characteristics() {
        return ORDERED | SIZED | SUBSIZED | IMMUTABLE | NONNULL;
    }
}

Stream<Integer> customStream = StreamSupport.stream(new RangeSpliterator(0, 10), false);
System.out.println(customStream.mapToInt(Integer::intValue).sum()); // 45
```

## Stream Performance

Streams read nicely, but they are not free. Every stage adds a virtual call, and for simple operations on already-in-memory data, a classic `for` loop is usually faster and, more importantly, has fewer surprising costs.

### When streams are slower than loops

For small collections or trivial operations, the overhead of building the pipeline (lambda allocation, boxing, virtual dispatch through each stage) can outweigh the benefit of readability. In hot paths — code that runs millions of times per second — measure before assuming a stream refactor is free.

```java
int[] data = {1, 2, 3, 4, 5};

// Loop: simple, no allocation beyond the primitive array
int sumLoop = 0;
for (int n : data) sumLoop += n;

// Stream: clean, but goes through IntStream machinery + a lambda for a trivial task
int sumStream = IntStream.of(data).sum();
```

Both are fine here — the point is that for tiny, simple aggregations, a loop is not something to apologize for in review.

### Boxing costs

`Stream<Integer>` stores boxed `Integer` objects; every `int` you put into it gets wrapped in an object, and every arithmetic operation on it may need to unbox, compute, and re-box. `IntStream`/`LongStream`/`DoubleStream` avoid this entirely by keeping primitives unboxed through the pipeline.

```java
List<Integer> numbers = List.of(1, 2, 3, 4, 5);

// Boxes every element into Integer, unboxes for the sum
int slow = numbers.stream().reduce(0, Integer::sum);

// No boxing at all - stays as int the whole way
int fast = numbers.stream().mapToInt(Integer::intValue).sum();
```

### Splittability of sources

Parallel streams rely on `trySplit()` dividing work into roughly equal, cheaply-computed chunks (see [Spliterator](#spliterator)). Not all sources split well:

- **Good**: `ArrayList`, arrays, `IntStream.range` — random access means splitting a range in half is O(1) and produces balanced chunks.
- **Bad**: `LinkedList`, `Iterator`-based sources, `Stream.iterate` — no random access, so splitting requires walking elements one at a time, which defeats the purpose, or can't be split at all.

```java
List<Integer> arrayList = new ArrayList<>(List.of(1, 2, 3, 4, 5, 6, 7, 8));
List<Integer> linkedList = new LinkedList<>(arrayList);

arrayList.parallelStream().forEach(n -> {});  // splits into balanced chunks cheaply - scales well
linkedList.parallelStream().forEach(n -> {}); // trySplit degrades to linear traversal - little/no benefit
```

### The common `ForkJoinPool` and why blocking is dangerous

`parallelStream()` doesn't spin up its own threads — by default it submits work to the JVM-wide **common `ForkJoinPool`**, sized to `Runtime.getRuntime().availableProcessors() - 1` worker threads. That pool is shared by *every* parallel stream in the whole application (and by anything else using `ForkJoinPool.commonPool()` directly, including `CompletableFuture`'s async methods).

If a task inside a parallel stream **blocks** — a network call, a JDBC query, a `Thread.sleep`, waiting on a lock — it occupies one of those few shared worker threads and can starve unrelated parallel work elsewhere in the app. Since the pool is small and shared, one badly-behaved parallel stream can quietly degrade performance for the whole application, not just itself.

```java
// Dangerous: blocking I/O inside a parallel stream ties up common pool threads
List<String> urls = List.of("https://a", "https://b", "https://c");
List<String> bodies = urls.parallelStream()
    .map(url -> httpClient.get(url)) // blocks a shared ForkJoinPool worker thread per call
    .toList();

// Safer: use a dedicated executor for blocking work, keep the common pool free
ExecutorService ioPool = Executors.newFixedThreadPool(10);
List<CompletableFuture<String>> futures = urls.stream()
    .map(url -> CompletableFuture.supplyAsync(() -> httpClient.get(url), ioPool))
    .toList();
List<String> results = futures.stream().map(CompletableFuture::join).toList();
```

### Stateful lambdas and shared mutable state

Passing a lambda that mutates shared state into a stream operation — especially a parallel one — is a race condition waiting to happen. Streams give no guarantee about thread-safety of your own captured state; that's on you.

```java
// BROKEN: ArrayList is not thread-safe, and multiple threads add() concurrently
List<Integer> results = new ArrayList<>();
IntStream.range(0, 10_000).parallel().forEach(results::add); // corrupts internal state, may throw or lose elements

// FIXED: let the stream own the accumulation via a proper collector
List<Integer> results2 = IntStream.range(0, 10_000).parallel().boxed().collect(Collectors.toList());
```

The rule: lambdas passed to stream operations should be **stateless** with respect to shared mutable data. Let `collect`/`reduce` do the accumulating — those are designed to combine partial results safely across threads.

### `forEach` vs `forEachOrdered`

On a parallel stream, `forEach` makes **no promise about order** — elements are processed as threads happen to finish, which is faster but unpredictable. `forEachOrdered` forces processing back into encounter order, which reintroduces most of the coordination overhead you were trying to avoid by going parallel.

```java
List<Integer> nums = List.of(1, 2, 3, 4, 5);

nums.parallelStream().forEach(System.out::println);        // order NOT guaranteed, e.g. 3 1 4 2 5
nums.parallelStream().forEachOrdered(System.out::println); // always 1 2 3 4 5, but loses most parallel speedup
```

If you need parallel speed and ordered output, prefer collecting to a `List` (which preserves encounter order) over forcing `forEachOrdered`.

## Parallel Streams

A **parallel stream** splits the source, processes chunks on multiple threads from the common `ForkJoinPool`, then merges the partial results. `stream()` and `parallelStream()` build the *same* pipeline API — the parallel-ness is just a flag, toggled with `.parallel()` / `.sequential()` at any point in the chain (the *last* call before the terminal operation wins).

```java
List<Integer> nums = IntStream.rangeClosed(1, 20).boxed().toList();

int sumSequential = nums.stream().mapToInt(Integer::intValue).sum();
int sumParallel = nums.parallelStream().mapToInt(Integer::intValue).sum();
System.out.println(sumSequential == sumParallel); // true - same result, different execution
```

### When parallel actually pays off — the "N × Q" rule of thumb

Parallelism has fixed costs: splitting the source, scheduling tasks, merging results. It only pays off when the *total work* is large enough to amortize those costs. A widely used heuristic (from Brian Goetz and the JDK's own stream design notes) is:

> **N × Q > 10,000** (roughly), where **N** = number of elements and **Q** = cost of processing one element.

- Large **N**, cheap **Q** (e.g., summing a million `int`s): parallel can help, but boxing and splitting overhead may eat the gain — measure.
- Small **N**, expensive **Q** (e.g., 50 items, each needing a slow computation): parallel can help a lot, since 50 splits handled by several threads still swamps the coordination cost.
- Small **N**, cheap **Q** (e.g., summing 10 numbers): parallel almost always loses — pure overhead, no upside.
- Large **N**, expensive **Q**: parallel usually wins clearly.

```java
// Small N, cheap Q: parallel overhead dominates - don't bother
int tinySum = IntStream.rangeClosed(1, 10).parallel().sum(); // slower than sequential in practice

// Large N, expensive Q per element: parallel is likely to help
List<Long> primesNear = LongStream.rangeClosed(2, 2_000_000)
    .parallel()
    .filter(BigIntegerUtil::isPrimeSlowCheck) // pretend this is CPU-heavy per element
    .boxed()
    .toList();
```

Always benchmark with something like JMH before trusting intuition — modern JITs and cache effects can make the "obvious" answer wrong, especially at small N. Never guess in a code review either way; ask for a benchmark or a clear justification when a PR reaches for `.parallel()`.

## Common Code-Review Interview Pitfalls

1. **Missing `@FunctionalInterface`, so a second abstract method sneaks in later.**
   Why it matters: without the annotation, the compiler won't stop a teammate from breaking every lambda that implements the interface.
   ```java
   // Before
   interface Handler<T> { void handle(T t); }

   // After
   @FunctionalInterface
   interface Handler<T> { void handle(T t); }
   ```

2. **Capturing a mutable loop variable or field expecting per-iteration semantics.**
   Why it matters: lambdas capture variables, not values in the "live" sense for fields, so shared mutable state read later can be wrong or racy.
   ```java
   // Before - all callbacks share and read the mutable field at call time, likely all seeing the final value
   for (int i = 0; i < tasks.size(); i++) {
       this.currentIndex = i;
       tasks.get(i).setCallback(() -> process(this.currentIndex));
   }

   // After - capture an effectively-final local per iteration
   for (int i = 0; i < tasks.size(); i++) {
       int index = i; // effectively final, one per iteration
       tasks.get(i).setCallback(() -> process(index));
   }
   ```

3. **Using `peek` to drive actual program logic instead of debugging.**
   Why it matters: `peek` may be skipped by the JDK if the result doesn't depend on it, and short-circuiting can stop it from ever running for all elements.
   ```java
   // Before - relies on peek to populate a list as a side effect
   List<String> seen = new ArrayList<>();
   long count = names.stream().peek(seen::add).count(); // may not run peek at all!

   // After - do the real work in map/forEach, not peek
   List<String> seen2 = names.stream().collect(Collectors.toList());
   ```

4. **Mutating shared state from inside a parallel stream lambda.**
   Why it matters: non-thread-safe collections corrupt under concurrent `add`, causing silent data loss or exceptions.
   ```java
   // Before
   List<Integer> out = new ArrayList<>();
   data.parallelStream().forEach(out::add); // race condition

   // After
   List<Integer> out2 = data.parallelStream().collect(Collectors.toList());
   ```

5. **Reusing a stream after a terminal operation.**
   Why it matters: streams are single-use; a second terminal call throws `IllegalStateException` at runtime, not compile time.
   ```java
   // Before
   Stream<String> s = items.stream();
   s.forEach(System.out::println);
   long n = s.count(); // IllegalStateException

   // After
   long n2 = items.stream().count(); // build a fresh stream from the source
   ```

6. **Calling `.parallel()` on a small or cheap workload "for speed."**
   Why it matters: fork/join scheduling and merge overhead usually outweighs the gain unless N × Q is large; it can actually be slower and it steals threads from the shared pool.
   ```java
   // Before
   int sum = IntStream.rangeClosed(1, 20).parallel().sum(); // pure overhead

   // After
   int sum2 = IntStream.rangeClosed(1, 20).sum();
   ```

7. **Blocking I/O inside a parallel stream.**
   Why it matters: it occupies threads in the shared common `ForkJoinPool`, potentially starving unrelated parallel work elsewhere in the JVM.
   ```java
   // Before
   List<String> bodies = urls.parallelStream().map(httpClient::get).toList();

   // After
   ExecutorService ioPool = Executors.newFixedThreadPool(10);
   List<String> bodies2 = urls.stream()
       .map(u -> CompletableFuture.supplyAsync(() -> httpClient.get(u), ioPool))
       .toList().stream().map(CompletableFuture::join).toList();
   ```

8. **Assuming `Collectors.toMap` silently overwrites on duplicate keys.**
   Why it matters: it throws `IllegalStateException` at runtime instead — a merge function is required if duplicates are possible.
   ```java
   // Before
   Map<Integer, String> byLen = words.stream().collect(Collectors.toMap(String::length, w -> w));
   // throws on duplicate length

   // After
   Map<Integer, String> byLen2 = words.stream()
       .collect(Collectors.toMap(String::length, w -> w, (a, b) -> a)); // keep first on conflict
   ```

9. **Filtering before `groupingBy` when you meant to filter within each group.**
   Why it matters: filtering first can make a whole key disappear from the resulting map, changing downstream logic that expects every key to be present.
   ```java
   // Before - Sales might vanish entirely if no order has >1 item
   Map<String, List<Order>> m = orders.stream()
       .filter(o -> o.items().size() > 1)
       .collect(Collectors.groupingBy(Order::customer));

   // After - every customer key is still present, possibly with an empty list
   Map<String, List<Order>> m2 = orders.stream()
       .collect(Collectors.groupingBy(Order::customer,
           Collectors.filtering(o -> o.items().size() > 1, Collectors.toList())));
   ```

10. **Wrapping a checked exception badly, or swallowing it, inside a lambda.**
    Why it matters: standard functional interfaces don't declare `throws`; a careless catch can hide real failures instead of surfacing them.
    ```java
    // Before - swallows the real error and returns garbage
    Function<String, byte[]> reader = path -> {
        try { return Files.readAllBytes(Path.of(path)); }
        catch (IOException e) { return new byte[0]; }
    };

    // After - preserve the failure, just change its exception type
    Function<String, byte[]> reader2 = path -> {
        try { return Files.readAllBytes(Path.of(path)); }
        catch (IOException e) { throw new UncheckedIOException(e); }
    };
    ```

11. **Confusing `findFirst` and `findAny` in code that depends on order.**
    Why it matters: `findAny` gives no ordering guarantee, especially in parallel; using it where determinism matters introduces flaky behavior.
    ```java
    // Before - flaky in tests/parallel runs, expects the smallest even number
    Optional<Integer> firstEven = nums.parallelStream().filter(n -> n % 2 == 0).findAny();

    // After
    Optional<Integer> firstEven2 = nums.parallelStream().filter(n -> n % 2 == 0).findFirst();
    ```

12. **Using an infinite `Stream.iterate`/`Stream.generate` without a bound.**
    Why it matters: without `limit` or a `takeWhile`/predicate form, the pipeline never terminates and hangs the thread.
    ```java
    // Before
    List<Integer> powers = Stream.iterate(1, n -> n * 2).toList(); // hangs forever

    // After
    List<Integer> powers2 = Stream.iterate(1, n -> n * 2).limit(10).toList();
    ```

13. **Boxing unnecessarily in numeric pipelines.**
    Why it matters: `Stream<Integer>` allocates and unboxes on every element; `IntStream` avoids it and is measurably faster on large data.
    ```java
    // Before
    int total = list.stream().reduce(0, Integer::sum);

    // After
    int total2 = list.stream().mapToInt(Integer::intValue).sum();
    ```

14. **Confusing anonymous-class `this` with lambda `this` after a refactor.**
    Why it matters: converting an anonymous class to a lambda changes what `this` refers to, which can silently break field access or logging that relied on the anonymous class's own identity.
    ```java
    // Before - 'this' refers to the anonymous Runnable
    Runnable r = new Runnable() {
        public void run() { log(this.getClass().getSimpleName()); }
    };

    // After (lambda) - 'this' now refers to the ENCLOSING instance, not the lambda
    Runnable r2 = () -> log(this.getClass().getSimpleName()); // meaning changed - verify this is intended
    ```

15. **Parallelizing a poorly-splittable source and expecting a speedup.**
    Why it matters: `LinkedList`/iterator-backed sources can't split efficiently, so `parallelStream()` on them often gives little to no benefit while still paying coordination overhead.
    ```java
    // Before
    LinkedList<Integer> data = new LinkedList<>(bigList);
    data.parallelStream().map(this::heavyWork).toList(); // splitting degrades to near-linear walk

    // After - use a splittable source
    List<Integer> data2 = new ArrayList<>(bigList);
    data2.parallelStream().map(this::heavyWork).toList();
    ```

16. **Relying on `forEach` order in a parallel stream.**
    Why it matters: parallel `forEach` makes no ordering guarantee; code that assumes sequential-looking output will be flaky under load.
    ```java
    // Before - assumes output prints 1..N in order
    nums.parallelStream().forEach(System.out::println);

    // After - if order matters, either go sequential or collect then iterate
    List<Integer> ordered = nums.parallelStream().map(n -> n * 2).toList(); // preserves encounter order
    ordered.forEach(System.out::println);
    ```

17. **Writing an overly clever one-liner stream pipeline that's hard to review.**
    Why it matters: dense chained pipelines with side effects buried in `map`/`peek` are hard to test and hide bugs; reviewers should push back on readability, not just correctness.
    ```java
    // Before - hard to review, mixes transformation with hidden side effects
    List<String> r = data.stream().peek(this::audit).map(this::transform).filter(Objects::nonNull)
        .peek(this::cache).sorted().distinct().toList();

    // After - explicit steps, side effects named and separated
    List<String> transformed = data.stream().map(this::transform).filter(Objects::nonNull).toList();
    transformed.forEach(this::audit);
    List<String> r2 = transformed.stream().sorted().distinct().toList();
    ```

18. **Treating a custom `Collector`'s combiner as optional in a stream that might run in parallel.**
    Why it matters: the combiner is only exercised under parallel execution; a broken or missing combiner passes all sequential tests but produces wrong results the moment someone adds `.parallel()`.
    ```java
    // Before - combiner just picks one side, silently dropping data when split
    Collector<String, StringBuilder, String> broken = Collector.of(
        StringBuilder::new,
        (sb, s) -> sb.append(s),
        (sb1, sb2) -> sb1,      // BUG: drops sb2's contents entirely on merge
        StringBuilder::toString);

    // After - combiner actually merges both partial results
    Collector<String, StringBuilder, String> fixed = Collector.of(
        StringBuilder::new,
        (sb, s) -> sb.append(s),
        (sb1, sb2) -> sb1.append(sb2), // correct merge
        StringBuilder::toString);
    ```
