# 4. Generics and Type System

Generics let you write code that works with different types while keeping type safety at compile time. They are one of the most interview-heavy topics in Java because the compiler does a lot of invisible work — and that invisible work (erasure) causes surprising rules that trip up even experienced developers. This chapter builds up from basic generic classes to the trickiest interview questions: erasure, bridge methods, wildcards, and why arrays and generics disagree about variance.

By the end, you should be able to explain *why* a rule exists, not just recite it. That is what separates a strong code-review answer from a memorized one.

- [Generics](#generics)
  - [Why generics exist](#why-generics-exist)
  - [Generic classes](#generic-classes)
  - [Generic methods](#generic-methods)
  - [Bounded type parameters](#bounded-type-parameters)
  - [Multiple bounds](#multiple-bounds)
  - [Recursive generic bounds](#recursive-generic-bounds-t-extends-comparablet)
  - [Raw types and unchecked warnings](#raw-types-and-unchecked-warnings)
  - [Generic type inference and the diamond operator](#generic-type-inference-and-the-diamond-operator)
  - [`var` with generics](#var-with-generics)
  - [Generics and `Optional` / streams](#generics-and-optional--streams)
- [Type Erasure](#type-erasure)
  - [What erasure does at the bytecode level](#what-erasure-does-at-the-bytecode-level)
  - [Bridge methods](#bridge-methods)
  - [Why you cannot do `new T[]`](#why-you-cannot-do-new-t)
  - [Why you cannot do `instanceof List<String>`](#why-you-cannot-do-instanceof-liststring)
  - [Why you cannot have static generic fields](#why-you-cannot-have-static-generic-fields)
  - [Heap pollution and `@SafeVarargs`](#heap-pollution-and-safevarargs)
- [Wildcards (extends, super)](#wildcards-extends-super)
  - [Unbounded wildcards `<?>`](#unbounded-wildcards-)
  - [Upper-bounded wildcards `<? extends T>`](#upper-bounded-wildcards--extends-t)
  - [Lower-bounded wildcards `<? super T>`](#lower-bounded-wildcards--super-t)
  - [PECS: Producer Extends, Consumer Super](#pecs-producer-extends-consumer-super)
  - [`List<?>` vs `List<Object>` vs raw `List`](#list-vs-listobject-vs-raw-list)
- [Covariance and Contravariance](#covariance-and-contravariance)
  - [Arrays are covariant](#arrays-are-covariant)
  - [Generics are invariant](#generics-are-invariant)
  - [`ArrayStoreException` demo](#arraystoreexception-demo)
  - [Method return type covariance](#method-return-type-covariance)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Generics

**Generics** let a class, interface, or method work with a *type parameter* — a placeholder for a real type that gets filled in later. Think of a type parameter like a function argument, except it stands for a type (like `String` or `Integer`) instead of a value.

### Why generics exist

Before Java 5 (2004), collections held plain `Object` references. You had to cast everything, and mistakes only showed up at runtime.

```java
// Pre-generics style (Java 1.4 and earlier) — do not write this today
List oldList = new ArrayList();
oldList.add("hello");
oldList.add(42); // no compile-time check, mixed types allowed

String s = (String) oldList.get(1); // compiles fine...
// ...but throws java.lang.ClassCastException at runtime
```

Generics move that error from runtime to compile time:

```java
List<String> names = new ArrayList<>();
names.add("Ada");
// names.add(42); // compile error: incompatible types: int cannot be converted to String
String first = names.get(0); // no cast needed
```

That is the whole point of generics: **catch type mistakes earlier, and remove casts**.

### Generic classes

A generic class declares one or more type parameters in angle brackets after the class name. By convention, type parameters are single, uppercase letters: `T` (Type), `E` (Element), `K`/`V` (Key/Value), `R` (Result).

```java
public class Box<T> {
    private T content;

    public void set(T content) {
        this.content = content;
    }

    public T get() {
        return content;
    }
}
```

Usage:

```java
Box<String> stringBox = new Box<>();
stringBox.set("hello");
String value = stringBox.get(); // no cast

Box<Integer> intBox = new Box<>();
intBox.set(5);
// intBox.set("oops"); // compile error: incompatible types: String cannot be converted to Integer
```

A class can have multiple type parameters:

```java
public class Pair<K, V> {
    private final K key;
    private final V value;

    public Pair(K key, V value) {
        this.key = key;
        this.value = value;
    }

    public K getKey()   { return key; }
    public V getValue() { return value; }
}

Pair<String, Integer> ageOf = new Pair<>("Grace", 85);
```

### Generic methods

A method can introduce its own type parameter, independent of the class it lives in. The type parameter is declared right before the return type.

```java
public class Utils {
    // <T> here is the method's own type parameter
    public static <T> T firstNonNull(T a, T b) {
        return a != null ? a : b;
    }

    // Works with any two types that share a common supertype
    public static <T> List<T> listOf(T a, T b) {
        List<T> list = new ArrayList<>();
        list.add(a);
        list.add(b);
        return list;
    }
}

String result = Utils.firstNonNull(null, "fallback"); // T inferred as String
List<Integer> nums = Utils.listOf(1, 2);               // T inferred as Integer
```

You rarely need to spell out the type parameter explicitly — the compiler infers it from the arguments. But you can be explicit if inference fails:

```java
List<String> empty = Utils.<String>listOf(null, null);
```

### Bounded type parameters

Sometimes you want to restrict what types are allowed. An **upper bound** (`extends`) says "T must be this type or a subtype of it." This lets you call methods from the bound inside the generic code.

```java
// Without a bound, you cannot call numeric methods on T
public class NumberBox<T> {
    private T value;
    // double asDouble() { return value.doubleValue(); } // compile error: cannot find symbol
}

// With an upper bound, Number's methods become available
public class BoundedNumberBox<T extends Number> {
    private T value;

    public BoundedNumberBox(T value) {
        this.value = value;
    }

    public double asDouble() {
        return value.doubleValue(); // OK — every Number has doubleValue()
    }
}

BoundedNumberBox<Integer> b1 = new BoundedNumberBox<>(5);
BoundedNumberBox<Double> b2 = new BoundedNumberBox<>(5.5);
// BoundedNumberBox<String> b3 = new BoundedNumberBox<>("x");
// compile error: String is not a subtype of Number
```

Note that `extends` here means "extends or implements" — it works for both classes and interfaces.

### Multiple bounds

A type parameter can have more than one bound: at most one class, plus any number of interfaces, joined with `&`. The class (if present) must come first.

```java
interface Named {
    String name();
}

// T must be a Number AND implement Named
static <T extends Number & Named> void printLabeled(T value) {
    System.out.println(value.name() + " = " + value.doubleValue());
}

// class Weird<T extends Named & Number> {} // compile error: class types must come first
```

### Recursive generic bounds (`<T extends Comparable<T>>`)

This pattern shows up constantly in interviews: a type parameter bounded by a generic type that refers back to itself. It says "T must be comparable to other T's."

```java
public static <T extends Comparable<T>> T max(List<T> list) {
    T best = list.get(0);
    for (T item : list) {
        if (item.compareTo(best) > 0) {
            best = item;
        }
    }
    return best;
}

List<Integer> nums = List.of(3, 7, 2);
Integer top = max(nums); // works, Integer implements Comparable<Integer>

class Point { int x, y; } // does NOT implement Comparable
List<Point> points = List.of(new Point());
// max(points); // compile error: Point is not a valid substitute for T extends Comparable<T>
```

Why not just `<T extends Comparable>` (raw)? Because then `compareTo` would accept any `Object`, losing type safety. The recursive form `Comparable<T>` guarantees you can only compare `T` to other `T`. This exact signature is how `Collections.max` is declared in the JDK.

### Raw types and unchecked warnings

A **raw type** is a generic class used without its type argument, e.g. `List` instead of `List<String>`. Raw types exist only for backward compatibility with pre-Java-5 code. Using them today throws away all compile-time type checking.

```java
@SuppressWarnings("unchecked")
List raw = new ArrayList();     // raw type — legal but discouraged
raw.add("a string");
raw.add(99);                    // no error here, but it's a landmine

List<String> typed = raw;       // unchecked warning: unchecked conversion
for (String s : typed) {        // compiles, but...
    System.out.println(s.length());
}
// Throws java.lang.ClassCastException at the Integer 99,
// because the compiler inserts a hidden (String) cast on read.
```

The compiler warning here is `unchecked conversion` — it means "I cannot verify this is actually a `List<String>`; trust me." Never silence these warnings without understanding why they are safe (see `@SafeVarargs` below for a legitimate case).

### Generic type inference and the diamond operator

Before Java 7, you had to repeat the type argument on both sides:

```java
Map<String, List<Integer>> map = new HashMap<String, List<Integer>>(); // verbose, pre-Java-7
```

The **diamond operator** `<>` (Java 7+) tells the compiler to infer the type argument from the left-hand side (target type):

```java
Map<String, List<Integer>> map = new HashMap<>(); // compiler infers <String, List<Integer>>
```

Inference also works across method calls and generic methods, and Java 10+ can combine it with `var` for local variables (see below). Inference has limits — it works left-to-right and top-down, so it cannot look *forward* into how you use the result:

```java
// Compiler cannot infer T just from an empty list with no target type
var list = List.of(); // inferred as List<Object>, probably not what you wanted

List<String> strings = List.of(); // OK: target type String is known here
```

### `var` with generics

`var` (Java 10+) infers the *declared* type from the initializer — it does not create a raw or wildcard type, and it does not weaken generics. `var` is not "dynamic typing"; the type is fixed at compile time, just not written out.

```java
var names = new ArrayList<String>(); // names has static type ArrayList<String>
names.add("Linus");
// names.add(42); // compile error: incompatible types: int cannot be converted to String
```

Caution: `var` needs a type on the right to infer from. You cannot use `var` with the diamond alone:

```java
// var list = new ArrayList<>(); // compiles, but infers ArrayList<Object> — loses precision
var list = new ArrayList<String>(); // be explicit on one side
```

Rule of thumb for code review: if `var` combined with `<>` makes the element type unclear at the call site, prefer spelling out the generic type.

### Generics and `Optional` / streams

`Optional<T>` and the Streams API (`Stream<T>`) are generic types that lean heavily on inference and bounded wildcards internally. They are covered fully in Chapter 5 and Chapter 8, but a couple of generics-specific points belong here.

```java
Optional<String> maybeName = Optional.of("Ada");

// map() is a generic method: <U> Optional<U> map(Function<? super T, ? extends U> mapper)
Optional<Integer> length = maybeName.map(String::length);

List<String> names = List.of("Ada", "Grace", "Linus");
List<Integer> lengths = names.stream()
        .map(String::length)   // Stream<String> -> Stream<Integer>, T inferred per stage
        .toList();              // Java 16+ shorthand for collect(Collectors.toList())
```

Notice `Function<? super T, ? extends U>` in `Optional.map`'s real signature — that is the PECS wildcard pattern explained later in this chapter. Streams use the same pattern throughout (`Collector<? super T, A, R>`, etc.) so that a pipeline can accept producers and consumers of *related* types, not just exact matches.

## Type Erasure

**Type erasure** is how the Java compiler implements generics: type parameters exist only in source code and are checked at compile time, then *erased* (removed) before bytecode is generated. At runtime, there is no `List<String>` — there is only `List`, with a compiler-inserted cast wherever you read an element.

Erasure exists for one reason: **backward compatibility**. Java 5 needed generic collections to run on a JVM whose bytecode format did not change, and to interoperate with pre-generics `.class` files already in production. Erasure made that possible at the cost of some runtime type information.

### What erasure does at the bytecode level

Given this source:

```java
public class Box<T> {
    private T value;

    public void set(T value) {
        this.value = value;
    }

    public T get() {
        return value;
    }
}
```

The compiler erases `T` to its bound — `Object`, since `T` has no bound here — and produces bytecode roughly equivalent to:

```java
// What the erased bytecode is equivalent to (not real source you write)
public class Box {
    private Object value;

    public void set(Object value) {
        this.value = value;
    }

    public Object get() {
        return value;
    }
}
```

Anywhere you call `box.get()` from code that expects `String`, the compiler inserts a cast:

```java
Box<String> box = new Box<>();
box.set("hi");
String s = box.get();
// Compiled roughly as:
// String s = (String) box.get();
```

You can verify this yourself with `javap -c`:

```bash
javac Box.java
javap -c Box.class
# Look at the set(Ljava/lang/Object;)V and get()Ljava/lang/Object; signatures —
# there is no T anywhere in the compiled descriptor.
```

If `T` is bounded, e.g. `<T extends Number>`, erasure uses the bound instead of `Object`:

```java
public class NumberBox<T extends Number> {
    private T value;
    public T get() { return value; }
}
// Erased: private Number value; public Number get() { return value; }
```

### Bridge methods

Erasure creates a subtle problem with overriding. Consider a generic interface and a class that implements it with a concrete type:

```java
interface Container<T> {
    void set(T value);
}

class StringContainer implements Container<String> {
    @Override
    public void set(String value) {
        System.out.println("Set: " + value);
    }
}
```

After erasure, `Container<T>.set` becomes `set(Object)`. But `StringContainer.set(String)` does not have the same erased signature as `set(Object)` — so it would not actually override the interface method at the bytecode level, breaking polymorphism.

The compiler fixes this by generating a hidden **bridge method**: a synthetic `set(Object)` method in `StringContainer` that casts and delegates to `set(String)`.

```java
// What the compiler generates behind the scenes (not something you write)
class StringContainer implements Container<String> {
    public void set(String value) { /* real body */ }

    // synthetic bridge method, added automatically
    public void set(Object value) {
        set((String) value); // cast, then delegate
    }
}
```

You can see this with reflection — `StringContainer.class.getDeclaredMethods()` lists two `set` methods, and `Method.isBridge()` is `true` for the synthetic one. This is a classic "gotcha" interview question: "why does my class have two `set` methods when I only wrote one?"

### Why you cannot do `new T[]`

You cannot create an array of a generic type parameter directly, because array creation needs a real, runtime-known component type — and after erasure, `T` is gone.

```java
public class Stack<T> {
    // private T[] items = new T[10];
    // compile error: generic array creation

    @SuppressWarnings("unchecked")
    private T[] items = (T[]) new Object[10]; // common workaround, still an unchecked cast
}
```

The workaround (`(T[]) new Object[10]`) compiles but is unsafe: the actual runtime array is `Object[]`, not `T[]`. If you ever leak that array reference out and someone assigns it to a more specific array variable, you get a `ClassCastException` later. The safer alternative, when you truly need a typed array, is to require a `Class<T>` token from the caller and use `java.util.reflect.Array.newInstance`:

```java
@SuppressWarnings("unchecked")
public static <T> T[] newArray(Class<T> type, int size) {
    return (T[]) java.lang.reflect.Array.newInstance(type, size);
}

String[] strings = newArray(String.class, 5); // works, and is actually a String[] at runtime
```

### Why you cannot do `instanceof List<String>`

`instanceof` is a runtime check, and after erasure there is no runtime distinction between `List<String>` and `List<Integer>` — both are just `List`.

```java
List<String> list = new ArrayList<>();

// if (list instanceof List<String>) { } // compile error: illegal generic type for instanceof

if (list instanceof List<?>) { // OK — unbounded wildcard is allowed
    System.out.println("it's some kind of List");
}

if (list instanceof ArrayList<String> al) { // still compile error, same reason
    // ...
}
```

Only the unbounded wildcard form (`List<?>`) or the raw form (`List`) is legal with `instanceof`, because those do not claim any specific type argument that would need a runtime check.

### Why you cannot have static generic fields

A `static` field belongs to the class itself, and a class exists once at runtime, regardless of how many different type arguments (`Box<String>`, `Box<Integer>`, ...) are used in source code. Since erasure removes `T` from the class entirely, there is nothing for a static field of type `T` to mean.

```java
public class Box<T> {
    // private static T defaultValue; // compile error: non-static type variable T cannot be referenced from a static context

    private static Object defaultValue; // fine — just use Object, or a concrete type
}
```

Static generic *methods* are fine (`static <T> T identity(T t)`), because the method itself introduces a fresh type parameter each time it is called — it does not depend on the class's erased type parameter.

### Heap pollution and `@SafeVarargs`

**Heap pollution** happens when a variable of a parameterized type refers to an object that is not actually of that parameterized type — usually caused by mixing raw types, unchecked casts, or varargs with generics.

```java
static void dangerous(List<String>... lists) { // varargs of a generic type — allowed, but risky
    Object[] array = lists;       // varargs is implemented as an array, arrays are covariant (see below)
    array[0] = List.of(42);       // legal at the array level: List<Integer> stored where List<String> expected
    String s = lists[0].get(0);   // compiles fine...
    // ...throws ClassCastException at runtime: Integer cannot be cast to String
}
```

This compiles only with an `unchecked` warning at the declaration site, because `T...` varargs create a hidden array of a generic type — exactly the "arrays are covariant, generics are not" mismatch. If you are certain your varargs method never stores anything unsafe into that array (i.e., it only *reads* from it), you can suppress the warning with `@SafeVarargs`:

```java
@SafeVarargs // a promise to the compiler AND to readers: this method is safe
static <T> List<T> listOf(T... items) {
    return Arrays.asList(items); // only reads from the array, never writes into it
}
```

`@SafeVarargs` is only allowed on `static` or `final` methods, `private` instance methods (Java 9+), and constructors — never on a regular overridable instance method, because a subclass could override it with unsafe behavior, breaking the promise.

```java
// @SafeVarargs
// void notAllowed(List<String>... lists) { } // compile error if the method is a non-final, non-private instance method
```

## Wildcards (extends, super)

A **wildcard** (`?`) represents an unknown type argument. Wildcards exist to make generic APIs more flexible when you do not need to know or care about the exact type argument — only what you can do with it (read vs. write).

### Unbounded wildcards `<?>`

`<?>` means "a list of some type, I don't know or care which." Use it when your code only needs methods that do not depend on the type argument at all (e.g., `size()`, `clear()`).

```java
static void printSize(List<?> list) {
    System.out.println("Size: " + list.size()); // fine, size() doesn't depend on T
}

static void printAll(List<?> list) {
    for (Object o : list) {   // you can only read elements as Object
        System.out.println(o);
    }
    // list.add("x"); // compile error: no way to prove String matches the unknown type
}
```

### Upper-bounded wildcards `<? extends T>`

`<? extends Number>` means "a list of some unknown type that is `Number` or a subtype of `Number`." You can safely **read** elements out as `Number`, but you cannot safely **add** to it (the compiler cannot know the exact subtype, so any insert could violate it).

```java
static double sumAll(List<? extends Number> numbers) {
    double total = 0;
    for (Number n : numbers) { // reading is safe — every element IS-A Number
        total += n.doubleValue();
    }
    // numbers.add(1);       // compile error: cannot add to a List<? extends Number>
    // numbers.add(1.0);     // compile error, same reason
    return total;
}

List<Integer> ints = List.of(1, 2, 3);
List<Double> doubles = List.of(1.0, 2.0);
sumAll(ints);    // OK
sumAll(doubles); // OK — this flexibility is the whole point
```

### Lower-bounded wildcards `<? super T>`

`<? super Integer>` means "a list of some unknown type that is `Integer` or a supertype of `Integer`." You can safely **write** `Integer`s into it, but reading only gives you `Object` (the compiler only knows the list holds *at least* `Integer`-compatible items, so it cannot promise a more specific type back).

```java
static void addNumbers(List<? super Integer> list) {
    list.add(1);   // safe — any supertype of Integer can hold an Integer
    list.add(2);
    // Integer first = list.get(0); // compile error: get() returns Object, not Integer
    Object first = list.get(0);     // this is all you get back
}

List<Number> numbers = new ArrayList<>();
List<Object> objects = new ArrayList<>();
addNumbers(numbers); // OK, Number is a supertype of Integer
addNumbers(objects); // OK, Object is a supertype of Integer
```

### PECS: Producer Extends, Consumer Super

**PECS** is the mnemonic for choosing the right wildcard: if a parameterized type *produces* (you read from it), use `extends`. If it *consumes* (you write into it), use `super`. If it does both, use no wildcard at all (an exact type).

| Role | Wildcard | You can read | You can write |
|---|---|---|---|
| Producer only | `? extends T` | Yes, as `T` | No (compile error) |
| Consumer only | `? super T` | Only as `Object` | Yes, `T` and subtypes |
| Both | exact type `T` (no wildcard) | Yes, as `T` | Yes, as `T` |
| Neither (rare) | `?` (unbounded) | Only as `Object` | No (compile error, except `null`) |

The JDK's own `Collections.copy` is the textbook PECS example:

```java
// Real JDK signature:
// public static <T> void copy(List<? super T> dest, List<? extends T> src)
public static <T> void copy(List<? super T> dest, List<? extends T> src) {
    for (int i = 0; i < src.size(); i++) {
        dest.set(i, src.get(i)); // read (produce) from src, write (consume) into dest
    }
}

List<Object> dest = new ArrayList<>(List.of(0, 0, 0));
List<Integer> src = List.of(1, 2, 3);
copy(dest, src); // src produces Integers, dest consumes them as Objects — PECS in action
```

Without PECS, `copy` would need to be `copy(List<T> dest, List<T> src)`, forcing both lists to hold the *exact* same type — you could not copy `List<Integer>` into `List<Object>`, even though that is perfectly safe.

### `List<?>` vs `List<Object>` vs raw `List`

These three look similar but behave very differently. This is a favorite whiteboard question.

| Expression | Meaning | Can you `add(String)`? | Can you `add(Object)`? | Can you assign `List<String>` to it? |
|---|---|---|---|---|
| `List<?>` | list of *some* unknown, fixed type | No | No | Yes |
| `List<Object>` | list whose element type IS `Object` | Yes | Yes | No (compile error) |
| `List` (raw) | no compile-time type checking at all | Yes (unchecked) | Yes (unchecked) | Yes (with unchecked warning) |

```java
List<String> strings = new ArrayList<>(List.of("a", "b"));

List<?> wildcard = strings;      // OK, ? matches any type argument
// wildcard.add("c");             // compile error: unknown type, can't prove safety

List<Object> objects = strings;  // compile error: incompatible types
// The compiler will not let this compile:
// error: incompatible types: List<String> cannot be converted to List<Object>

List raw = strings;              // compiles, with an "unchecked" nature — no error, no warning here
raw.add(42);                     // compiles! but corrupts the underlying List<String> at runtime
// strings.get(2); // throws ClassCastException — the "42" is really an Integer, cast to String fails
```

That last example is exactly why raw types are dangerous: they let you silently break the type-safety promise that generics exist to enforce.

## Covariance and Contravariance

**Covariance** means "a subtype relationship is preserved": if `Cat` is a subtype of `Animal`, then `Cat[]` is treated as a subtype of `Animal[]`. **Contravariance** is the reverse direction (used for function parameters, e.g. `? super T`). **Invariance** means no such relationship exists at all — `List<Cat>` and `List<Animal>` are completely unrelated types, even though `Cat` and `Animal` are related.

Java arrays are covariant. Java generics are invariant. This mismatch is one of the most important things to understand for a code-review interview, because it explains half of the wildcard rules above.

### Arrays are covariant

```java
Object[] objects = new String[3]; // legal: String[] IS-A Object[]
objects[0] = "hello";              // fine
```

This is convenient, but it means the *compile-time* type of the array reference (`Object[]`) does not match its *actual runtime* type (`String[]`). The JVM has to check every array write at runtime to protect against violations.

### Generics are invariant

```java
// List<Object> objects = new ArrayList<String>();
// compile error: incompatible types: ArrayList<String> cannot be converted to List<Object>
```

Even though `String` IS-A `Object`, `List<String>` is NOT a `List<Object>`. This is invariance, and it is deliberate — generics were designed to catch the exact mistake that array covariance allows to slip through to runtime (see next section). If you need covariant-like behavior for generics, that is exactly what `? extends T` wildcards are for.

### `ArrayStoreException` demo

Because arrays check types at runtime, a covariant array write that violates the actual element type fails with `ArrayStoreException` — at runtime, not compile time.

```java
Object[] objects = new String[3]; // compiles: array covariance
objects[0] = "safe";               // OK, actually a String
objects[1] = 42;                   // compiles (Integer IS-A Object)...
// ...but throws java.lang.ArrayStoreException at runtime,
// because the actual array is a String[] and 42 is not a String.
```

Generics avoid this entire class of bug at compile time:

```java
List<String> list = new ArrayList<>();
list.add("safe");
// list.add(42); // compile error: incompatible types — caught here, never reaches runtime
```

This is the single clearest argument for "generics over arrays" in a code review: **prefer `List<T>` to `T[]`** in APIs whenever possible, because the failure mode moves from a runtime exception to a compile error.

| Aspect | Arrays | Generics |
|---|---|---|
| Variance | Covariant (`String[]` IS-A `Object[]`) | Invariant (`List<String>` is NOT a `List<Object>`) |
| Type check timing | Runtime (per-element store check) | Compile time (erased after that) |
| Failure mode on mismatch | `ArrayStoreException` at runtime | Compile error, or none if avoided by design |
| Runtime type info | Preserved (`array.getClass()` knows `String[]`) | Erased (no `List<String>.class`) |

### Method return type covariance

There is a second, unrelated meaning of "covariance" that shows up in overriding: an overriding method's return type may be a *subtype* of the overridden method's return type. This is legal and has nothing to do with type erasure — it is about the class hierarchy, not generics.

```java
class Animal {
    Animal reproduce() { return new Animal(); }
}

class Cat extends Animal {
    @Override
    Cat reproduce() { return new Cat(); } // covariant return type — legal since Java 5
}
```

Interview tip: do not confuse this (covariant return types, a class-hierarchy feature) with generic type variance (`List<Cat>` vs `List<Animal>`, which is invariant unless you use wildcards). They are two different "covariance" concepts that happen to share a name.

## Common Code-Review Interview Pitfalls

1. **Using raw types "to make the compiler stop complaining."**
   Why it matters: raw types disable all generic type checking, turning compile-time errors into runtime `ClassCastException`s.
   ```java
   // Before
   List list = new ArrayList();
   list.add("a");
   list.add(1); // silently allowed

   // After
   List<String> list = new ArrayList<>();
   list.add("a");
   // list.add(1); // compile error — caught immediately
   ```

2. **Suppressing `unchecked` warnings without verifying safety.**
   Why it matters: `@SuppressWarnings("unchecked")` is a promise to the compiler that you manually verified type safety. Blind suppression hides real bugs.
   ```java
   // Before — suppressed without justification
   @SuppressWarnings("unchecked")
   List<String> list = (List<String>) someRawList;

   // After — scoped narrowly, with a comment explaining why it's safe
   @SuppressWarnings("unchecked") // safe: someRawList is populated exclusively by addString() above
   List<String> list = (List<String>) someRawList;
   ```

3. **Designing an API with `T[]` instead of `List<T>`.**
   Why it matters: arrays are covariant and only check types at runtime (`ArrayStoreException`); `List<T>` is invariant and checked at compile time.
   ```java
   // Before
   void process(Number[] values) { values[0] = 3.14; } // ArrayStoreException risk if called with Integer[]

   // After
   void process(List<? extends Number> values) { /* read-only access is compile-time safe */ }
   ```

4. **Using `? extends T` on a parameter you need to write into.**
   Why it matters: `extends` wildcards are producer-only; the compiler will reject `add()` calls, and "fixing" it by removing the wildcard silently loses flexibility.
   ```java
   // Before
   void fill(List<? extends Number> list) {
       // list.add(1); // compile error
   }

   // After — use super if you need to write, or an exact type if you need both
   void fill(List<? super Number> list) {
       list.add(1); // OK
   }
   ```

5. **Assuming `List<String>` and `List<Object>` are interchangeable.**
   Why it matters: generics are invariant; this is one of the most common "why won't this compile" confusions.
   ```java
   // Before
   // List<Object> objs = new ArrayList<String>(); // compile error

   // After — use a wildcard if you only need read access
   List<? extends Object> objs = new ArrayList<String>(); // fine, though rarely useful since Object adds nothing
   ```

6. **Trying `instanceof List<String>` or catching a specific generic exception type per type argument.**
   Why it matters: type erasure removes the type argument at runtime; the JVM cannot check it.
   ```java
   // Before
   // if (obj instanceof List<String>) { } // compile error

   // After
   if (obj instanceof List<?> list && !list.isEmpty() && list.get(0) instanceof String) {
       // manually verify the first element's runtime type instead
   }
   ```

7. **Forgetting that overloaded generic methods can collide after erasure.**
   Why it matters: two overloads that differ only by type argument have the *same* erased signature and will not compile.
   ```java
   // Before
   // void process(List<String> list) { }
   // void process(List<Integer> list) { } // compile error: both erase to process(List)

   // After — differentiate by method name or take a wrapper/marker type
   void processStrings(List<String> list) { }
   void processIntegers(List<Integer> list) { }
   ```

8. **Writing an unsafe generic varargs method without `@SafeVarargs`, or worse, adding `@SafeVarargs` to an unsafe one.**
   Why it matters: `@SafeVarargs` is a promise; misusing it hides real heap pollution bugs, and omitting it on a genuinely safe method just produces noisy warnings for every caller.
   ```java
   // Before — unsafe, and no annotation to flag the risk
   static <T> void addAll(List<T> list, T... items) {
       Object[] array = items;
       array[0] = "corrupt"; // writes an unrelated type into the array — heap pollution
   }

   // After — either make it actually safe (read-only) and annotate, or take a List parameter instead
   @SafeVarargs
   static <T> List<T> listOf(T... items) {
       return List.of(items); // read-only, never stores into items
   }
   ```

9. **Expecting a static field or method to "remember" the type argument per instance.**
   Why it matters: erasure means there is one class, one set of static members, shared across all parameterizations (`Box<String>`, `Box<Integer>`, ...).
   ```java
   // Before
   class Box<T> {
       // static T lastCreated; // compile error
   }

   // After
   class Box<T> {
       static Object lastCreated; // shared across all Box<...> instances, by design
   }
   ```

10. **Not recognizing bridge methods when reading decompiled/reflected code.**
    Why it matters: reviewers who see two overloaded-looking methods with the same name after implementing a generic interface may think it's a bug, when it's a compiler-generated bridge method required for correct polymorphism.
    ```java
    interface Container<T> { void set(T value); }
    class StringContainer implements Container<String> {
        public void set(String value) { /* ... */ }
        // compiler silently also generates: public void set(Object value) { set((String) value); }
    }
    ```

11. **Using a recursive bound incorrectly, e.g. `<T extends Comparable>` (raw) instead of `<T extends Comparable<T>>`.**
    Why it matters: the raw bound loses type safety — `compareTo` accepts any `Object`, so mismatched comparisons compile and fail at runtime instead of compile time.
    ```java
    // Before
    static <T extends Comparable> T max(List<T> list) { /* ... */ } // raw Comparable, weak guarantee

    // After
    static <T extends Comparable<T>> T max(List<T> list) { /* ... */ } // T can only compare to T
    ```

12. **Confusing `var` with type erasure or loss of type safety.**
    Why it matters: `var` infers a concrete compile-time type from the initializer; it is not related to generics' runtime erasure and does not weaken checking.
    ```java
    // Before — reviewer flags this as "unsafe, dynamic typing"
    var names = new ArrayList<String>();

    // After — clarify: names has static type ArrayList<String>, exactly as if written out
    ArrayList<String> names = new ArrayList<>(); // equivalent compile-time behavior
    ```

13. **Relying on `List.of()` or an empty generic literal without a target type.**
    Why it matters: without a target type, the compiler infers the most general type (often `Object`), which silently defeats type checking downstream.
    ```java
    // Before
    var empty = List.of(); // inferred as List<Object>

    // After — give the compiler a target type
    List<String> empty = List.of(); // inferred correctly as List<String>
    ```

14. **Treating `PECS` as optional API polish instead of a correctness tool.**
    Why it matters: skipping `extends`/`super` on library-style methods forces callers into exact-type matches, breaking otherwise-valid calls and causing unnecessary casts or `List.copyOf()` calls at call sites.
    ```java
    // Before — overly strict, exact type required
    static double sum(List<Double> values) { /* ... */ }
    // sum(List.of(1, 2, 3)); // compile error: List<Integer> is not List<Double>

    // After — PECS: this is a producer, so use extends
    static double sum(List<? extends Number> values) { /* ... */ }
    // sum(List.of(1, 2, 3)); // now compiles
    ```

15. **Assuming array covariance is "safe" because it compiles.**
    Why it matters: covariant array writes that violate the actual runtime type compile cleanly but throw `ArrayStoreException` in production — often only under rarely-hit code paths, making it a nasty review-time-invisible bug.
    ```java
    // Before
    Object[] items = new String[3];
    items[0] = 42; // compiles, throws ArrayStoreException at runtime

    // After — use a generic collection so the mistake is caught at compile time
    List<String> items = new ArrayList<>();
    // items.add(42); // compile error, caught immediately
    ```
