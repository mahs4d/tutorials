# 16. Reflection & Runtime Features

Most Java code is written to be checked and wired together at **compile time**: you call a method, the compiler verifies it exists, and the JVM runs it. **Reflection** and its related APIs flip that around — they let code inspect and call other code *while the program is running*, even if the exact class or method wasn't known when it was compiled. This is how frameworks like Spring, Hibernate, and Jackson do their "magic": they look at your classes, fields, and annotations at runtime and build behavior around them. This chapter covers reflection itself, annotations (the metadata reflection often reads), annotation processing (a compile-time alternative), dynamic proxies, the newer and faster method handle and VarHandle APIs, what replaced the old `Unsafe` class, and the Service Provider Interface (SPI) mechanism for plugin-style architectures. We target Java 21+ throughout.

## Table of Contents

- [Reflection](#reflection)
- [Annotations](#annotations)
- [Annotation Processing](#annotation-processing)
- [Dynamic Proxies](#dynamic-proxies)
- [Method Handles](#method-handles)
- [VarHandles](#varhandles)
- [Unsafe Alternatives](#unsafe-alternatives)
- [Service Provider Interface (SPI)](#service-provider-interface-spi)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Reflection

**Reflection** is the ability of code to examine and manipulate classes, fields, methods, and constructors at runtime, using objects instead of source-code syntax. The entry point is almost always a `Class<?>` object, which represents a loaded class inside the JVM. Every object has exactly one `Class` object describing its type.

There are three common ways to get a `Class` object:

```java
// 1. From a class literal — known at compile time, checked by the compiler
Class<String> byLiteral = String.class;

// 2. From an instance you already have
String s = "hello";
Class<?> byInstance = s.getClass();

// 3. By name, at runtime — this is the "dynamic" one, used by frameworks
Class<?> byName = Class.forName("java.lang.String");
```

`Class.forName` is special: it is the classic way to load a class whose name is only known as a `String` (for example, read from a config file or a JDBC driver property). It also triggers static initialization of that class.

### Inspecting Members: Fields, Methods, Constructors

Once you have a `Class` object, you can list its members.

```java
import java.lang.reflect.*;

public class Inspector {
    static class Person {
        private String name;
        public Person(String name) { this.name = name; }
        public String greet() { return "Hi, " + name; }
        private void secret() { }
    }

    public static void main(String[] args) throws Exception {
        Class<?> clazz = Person.class;

        for (Field f : clazz.getDeclaredFields()) {
            System.out.println("field: " + f.getName() + " : " + f.getType());
        }
        for (Method m : clazz.getDeclaredMethods()) {
            System.out.println("method: " + m.getName());
        }
        for (Constructor<?> c : clazz.getDeclaredConstructors()) {
            System.out.println("constructor: " + c);
        }
    }
}
```

### `getDeclaredX` vs `getX`

This is one of the most commonly confused pairs in reflection:

| Method | Includes inherited members? | Includes private/protected members? |
|---|---|---|
| `getFields()` / `getMethods()` / `getConstructors()` | Yes (public members from superclasses/interfaces too) | No — public only |
| `getDeclaredFields()` / `getDeclaredMethods()` / `getDeclaredConstructors()` | No — only members declared directly in this class | Yes — all of them, regardless of visibility |

Rule of thumb: use `getDeclaredX` when you need to see *everything this exact class wrote*, including private fields. Use `getX` when you want the public API surface, including what it inherited.

### Instantiating and Invoking

```java
import java.lang.reflect.*;

public class ReflectiveCall {
    public static void main(String[] args) throws Exception {
        Class<?> clazz = Class.forName("java.util.ArrayList");

        // Instantiate via a constructor
        Constructor<?> ctor = clazz.getDeclaredConstructor();
        Object list = ctor.newInstance();

        // Invoke a method reflectively
        Method add = clazz.getMethod("add", Object.class);
        add.invoke(list, "hello");

        Method size = clazz.getMethod("size");
        System.out.println(size.invoke(list)); // 1
    }
}
```

### `setAccessible(true)` and Strong Encapsulation (JDK 16/17+)

Reflection can normally only call public members. To reach private ones, you call `setAccessible(true)` on the `Field`, `Method`, or `Constructor` object, which tells the JVM to skip its usual access checks.

```java
Field nameField = Person.class.getDeclaredField("name");
nameField.setAccessible(true);
Person p = new Person("Ada");
System.out.println(nameField.get(p)); // "Ada" — bypassing private
```

Since JDK 9, the Java Platform Module System (JPMS) introduced **strong encapsulation**: modules can hide their internal packages so that even reflection cannot break in, unless the module explicitly "opens" that package. Starting with **JDK 16 (illegal access denied by default) and fully enforced by JDK 17**, calling `setAccessible(true)` on a member of an internal JDK class (like something in `java.util` internals) throws an `InaccessibleObjectException` instead of just printing a warning as it did in JDK 9–15.

```
Exception in thread "main" java.lang.reflect.InaccessibleObjectException:
Unable to make field private final byte[] java.lang.String.value accessible:
module java.base does not "opens java.lang" to unnamed module
```

To allow it anyway, you must explicitly open the package, either in `module-info.java`:

```java
module my.app {
    // grants reflective access to internal fields at runtime only
    opens com.internal.pkg to some.other.module;
}
```

or via a JVM flag at launch time, which is the common escape hatch for libraries (like older Mockito or Jackson versions) that still reflect into JDK internals:

```
java --add-opens java.base/java.lang=ALL-UNNAMED -jar app.jar
```

This matters in real projects: upgrading from Java 8/11 to 17+ is a common source of `InaccessibleObjectException` failures in libraries that reflect into JDK internals.

### Performance Cost

Reflective calls are slower than direct calls because the JVM must perform access checks, resolve the target dynamically, and (for `invoke`) box/unbox arguments through `Object[]`. The JIT compiler also has a much harder time inlining and optimizing through `Method.invoke`. In hot paths (tight loops, request handling), reflection can be 10-100x slower than a direct call. Frameworks mitigate this by caching `Method`/`Field` objects (looking them up once, not per call) and by preferring `MethodHandle` (covered later) which the JIT can optimize almost as well as direct calls.

### Generic Type Info at Runtime

Because of **type erasure** (see the Generics chapter), a `List<String>` and a `List<Integer>` look identical at runtime — the generic type argument is gone from the object. However, reflection can still recover generic type information that was declared on a **field, method, or superclass**, because that information is stored in the class file, not on the runtime object.

```java
import java.lang.reflect.*;
import java.util.*;

class StringListHolder {
    private List<String> names;
}

public class GenericInspection {
    public static void main(String[] args) throws Exception {
        Field f = StringListHolder.class.getDeclaredField("names");
        Type genericType = f.getGenericType(); // java.util.List<java.lang.String>
        if (genericType instanceof ParameterizedType pt) {
            System.out.println(pt.getActualTypeArguments()[0]); // class java.lang.String
        }
    }
}
```

This trick — capturing a generic type through a subclass so it survives erasure — is called the **TypeToken pattern** (also called "super type tokens"), and it is how libraries like Gson and Guice let you say "give me a `List<String>` at runtime":

```java
import java.lang.reflect.*;

public abstract class TypeToken<T> {
    private final Type type;

    protected TypeToken() {
        // getGenericSuperclass() sees TypeToken<List<String>> because
        // we captured it in an anonymous subclass below
        ParameterizedType pt = (ParameterizedType) getClass().getGenericSuperclass();
        this.type = pt.getActualTypeArguments()[0];
    }

    public Type getType() { return type; }
}

// usage: an anonymous subclass "bakes in" the type argument into the class file
TypeToken<List<String>> token = new TypeToken<List<String>>() {};
System.out.println(token.getType()); // java.util.List<java.lang.String>
```

### When Reflection Is Legitimate vs a Smell

Legitimate uses:
- **Frameworks and dependency injection** (Spring, Guice) — wiring objects together without every class knowing about every other class.
- **Serialization libraries** (Jackson, Gson) — reading/writing arbitrary object fields generically.
- **Testing tools** (Mockito, JUnit) — creating mocks and invoking test methods discovered by annotation.
- **ORMs** (Hibernate/JPA) — mapping database columns to entity fields.

Code smell / red flag in a code review:
- Application (non-framework) business logic reaching into another class's private fields via reflection instead of adding a getter or constructor parameter. This usually signals a design problem — two classes are too tightly coupled, or an API is missing.
- Reflection used to bypass `final` or visibility rules "just to make the compiler happy" instead of fixing the underlying design.

## Annotations

An **annotation** is metadata attached to code — a class, method, field, or parameter — that does nothing by itself. It only has an effect if something (the compiler, a runtime library, or an annotation processor) reads it and acts on it.

### Declaring an Annotation

```java
import java.lang.annotation.*;

@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface Timed {
    String label() default "";
}
```

Usage:

```java
public class OrderService {
    @Timed(label = "checkout")
    public void checkout() { /* ... */ }
}
```

### Meta-Annotations

Meta-annotations are annotations that describe *other annotations*. The main ones:

- **`@Retention`** — how long the annotation is kept.
- **`@Target`** — what kinds of declarations it can be placed on (methods, fields, types, parameters...).
- **`@Inherited`** — if placed on an annotation, subclasses automatically "inherit" it from their superclass (only applies to class-level annotations, not methods or fields).
- **`@Repeatable`** — allows the same annotation to be applied more than once to the same element.
- **`@Documented`** — includes the annotation in generated Javadoc.

```java
@Inherited
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.TYPE)
public @interface Auditable { }

@Auditable
class Base { }

class Derived extends Base { } // Derived.class.isAnnotationPresent(Auditable.class) == true
```

```java
import java.lang.annotation.*;

@Retention(RetentionPolicy.RUNTIME)
public @interface Schedules {
    Schedule[] value();
}

@Retention(RetentionPolicy.RUNTIME)
@Repeatable(Schedules.class) // container annotation required for @Repeatable
public @interface Schedule {
    String cron();
}

class Job {
    @Schedule(cron = "0 0 * * *")
    @Schedule(cron = "0 12 * * *")
    void run() { }
}
```

### Reading Annotations at Runtime

Only annotations with `RetentionPolicy.RUNTIME` are visible via reflection.

```java
import java.lang.reflect.*;

public class TimedRunner {
    public static void main(String[] args) throws Exception {
        Method m = OrderService.class.getMethod("checkout");
        if (m.isAnnotationPresent(Timed.class)) {
            Timed timed = m.getAnnotation(Timed.class);
            System.out.println("Label: " + timed.label()); // "checkout"
        }
    }
}
```

### Retention Policies

| Policy | Kept in `.class` file? | Visible to reflection at runtime? | Typical use |
|---|---|---|---|
| `SOURCE` | No — discarded by the compiler | No | Compile-time-only checks, e.g. `@Override`, Lombok's markers, annotation processors that only need source info |
| `CLASS` (default if unspecified) | Yes | No — JVM doesn't load it into memory for reflection | Bytecode-level tools (e.g. some bytecode weavers); rarely used directly by application code |
| `RUNTIME` | Yes | Yes | Frameworks that inspect annotations via reflection at runtime, e.g. `@Autowired`, `@Test`, `@Entity` |

### Type Annotations (JDK 8+)

Since Java 8, annotations can also be placed on **type uses**, not just declarations — for example, on a generic type argument, a cast, or a `throws` clause. This is mainly used by static analysis tools (like the Checker Framework) to catch bugs such as null-pointer risks before runtime.

```java
import java.lang.annotation.*;

@Target(ElementType.TYPE_USE)
@interface NonNull { }

public class TypeAnnotationDemo {
    // annotation on the type argument, not the field itself
    private List<@NonNull String> names;

    // annotation on a cast
    Object o = "text";
    String s = (@NonNull String) o;
}
```

## Annotation Processing

**Annotation processing** happens at *compile time*, not runtime. An annotation processor is a plug-in for the compiler (`javac`) that reads annotations in your source code and can generate new source files (or report errors), all before the program ever runs.

### The `Processor` Interface and `AbstractProcessor`

```java
import javax.annotation.processing.*;
import javax.lang.model.SourceVersion;
import javax.lang.model.element.*;
import java.util.Set;

@SupportedAnnotationTypes("com.example.GenerateBuilder")
@SupportedSourceVersion(SourceVersion.RELEASE_21)
public class BuilderProcessor extends AbstractProcessor {

    @Override
    public boolean process(Set<? extends TypeElement> annotations, RoundEnvironment roundEnv) {
        for (Element element : roundEnv.getElementsAnnotatedWith(GenerateBuilder.class)) {
            TypeElement classElement = (TypeElement) element;
            generateBuilderClass(classElement);
        }
        return true; // true = "I claimed these annotations, don't let other processors see them"
    }

    private void generateBuilderClass(TypeElement classElement) {
        // Use processingEnv.getFiler() to write a new .java file (shown below)
    }
}
```

### Rounds

Annotation processing runs in **rounds**. Round 1 processes the original source files. If a processor generates new source files in round 1, `javac` runs another round to process *those* generated files too (since they might themselves have annotations). This repeats until no new files are generated. `RoundEnvironment.processingOver()` tells a processor when the final round has finished, useful for cleanup or validation that should only happen once.

### The `Filer`: Generating Code

The `Filer` API (`processingEnv.getFiler()`) is how a processor writes new source or resource files during compilation.

```java
import javax.annotation.processing.Filer;
import javax.tools.JavaFileObject;
import java.io.Writer;

void writeGeneratedClass(String packageName, String className, String content) throws Exception {
    Filer filer = processingEnv.getFiler();
    JavaFileObject file = filer.createSourceFile(packageName + "." + className);
    try (Writer writer = file.openWriter()) {
        writer.write(content);
    }
}
```

### Registering a Processor via `META-INF/services`

For `javac` to discover your processor automatically, register it as a service (the same SPI mechanism covered later in this chapter):

```
src/main/resources/META-INF/services/javax.annotation.processing.Processor
```

containing one line:

```
com.example.BuilderProcessor
```

Modern projects usually use Google's `auto-service` library to generate this file automatically via `@AutoService(Processor.class)`.

### Real-World Examples

- **Lombok** — generates getters, setters, constructors, `equals`/`hashCode`, and builders from annotations like `@Data` and `@Builder`. (Technically it hooks into javac in a slightly unofficial way, modifying the in-memory AST rather than just generating new files, but conceptually it's the same idea.)
- **MapStruct** — generates type-safe mapping code between DTOs and entities from an `@Mapper` interface, avoiding hand-written or reflective mapping code.
- **Dagger** — generates dependency-injection wiring code at compile time instead of using runtime reflection like Spring/Guice.
- **Immutables** — generates immutable value classes from an `@Value.Immutable`-annotated abstract class or interface.

### Why Compile-Time Beats Runtime Reflection

- **Performance**: generated code is plain Java, as fast as hand-written code — no reflective lookups at runtime.
- **Fail fast**: errors are caught at compile time (`javac` fails the build) instead of surfacing as a `RuntimeException` in production.
- **Works with strong encapsulation**: no `setAccessible` calls, so no fights with the module system or native-image tools like GraalVM, which struggle with runtime reflection.
- **IDE support**: generated code is visible, debuggable, and step-through-able, unlike a black-box reflective call.

## Dynamic Proxies

A **dynamic proxy** is an object, generated at runtime, that implements one or more interfaces and forwards every method call to a handler you provide. It lets you add cross-cutting behavior (logging, timing, security checks, retries) around any interface without writing a wrapper class by hand for each one.

### `Proxy.newProxyInstance` + `InvocationHandler`

```java
import java.lang.reflect.*;

interface OrderService {
    void placeOrder(String item);
    int getOrderCount();
}

class OrderServiceImpl implements OrderService {
    private int count = 0;
    public void placeOrder(String item) {
        count++;
        System.out.println("Order placed: " + item);
    }
    public int getOrderCount() { return count; }
}

class LoggingTimingHandler implements InvocationHandler {
    private final Object target;

    LoggingTimingHandler(Object target) { this.target = target; }

    @Override
    public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
        long start = System.nanoTime();
        System.out.println("Calling " + method.getName());
        try {
            return method.invoke(target, args); // forward to the real object
        } finally {
            long elapsedMicros = (System.nanoTime() - start) / 1000;
            System.out.println(method.getName() + " took " + elapsedMicros + " microseconds");
        }
    }
}

public class ProxyDemo {
    public static void main(String[] args) {
        OrderService real = new OrderServiceImpl();

        OrderService proxy = (OrderService) Proxy.newProxyInstance(
                OrderService.class.getClassLoader(),
                new Class<?>[] { OrderService.class },
                new LoggingTimingHandler(real));

        proxy.placeOrder("Laptop");
        System.out.println("Count: " + proxy.getOrderCount());
    }
}
```

Output looks like:

```
Calling placeOrder
Order placed: Laptop
placeOrder took 12 microseconds
Calling getOrderCount
getOrderCount took 3 microseconds
Count: 1
```

### Interface-Only Limitation

`java.lang.reflect.Proxy` can only proxy **interfaces**, not concrete classes. This is a fundamental JDK limitation — the generated proxy class implements the given interfaces, it cannot extend an arbitrary class. If you need to proxy a class with no interface (common with plain Spring `@Service` classes that don't implement one), you need a bytecode-generation library instead.

### How Frameworks Use This

- **Spring AOP**: when the target bean implements at least one interface, Spring uses **JDK dynamic proxies** (exactly the mechanism above) to implement things like `@Transactional`, `@Cacheable`, and custom aspects. When the bean has no interface (or `proxyTargetClass=true` is set), Spring falls back to **CGLIB** (bundled, repackaged inside `spring-core`), which generates a real subclass at runtime that overrides methods — this works on concrete classes but can't proxy `final` classes or `final` methods.
- **ByteBuddy** is a modern, actively maintained alternative to CGLIB for generating subclasses/bytecode at runtime; it's used internally by Mockito (for mocking classes, not just interfaces) and newer versions of Hibernate.

| Mechanism | Can proxy interfaces? | Can proxy concrete classes? | Notes |
|---|---|---|---|
| JDK `Proxy` | Yes | No | Built into the JDK, no dependency needed |
| CGLIB | Yes (via subclass) | Yes, except `final` classes/methods | Bundled inside Spring; effectively unmaintained upstream |
| ByteBuddy | Yes | Yes, except `final` classes/methods | Modern, actively maintained, used by Mockito |

## Method Handles

A **method handle** is a typed, directly executable reference to an underlying method, constructor, or field access, created through the `java.lang.invoke` package. Method handles were introduced in Java 7 specifically to be a faster, more type-safe alternative to reflection for performance-sensitive dynamic dispatch — and the JVM itself uses them internally to implement lambdas and `invokedynamic`.

### `Lookup`, `MethodType`, and Finding a Handle

```java
import java.lang.invoke.*;

public class MethodHandleDemo {
    public static void main(String[] args) throws Throwable {
        MethodHandles.Lookup lookup = MethodHandles.lookup();

        // MethodType describes the signature: return type first, then parameter types
        MethodType stringLength = MethodType.methodType(int.class);
        MethodHandle lengthHandle = lookup.findVirtual(String.class, "length", stringLength);

        int len = (int) lengthHandle.invokeExact("hello"); // 5

        // findStatic for static methods
        MethodType parseIntType = MethodType.methodType(int.class, String.class);
        MethodHandle parseInt = lookup.findStatic(Integer.class, "parseInt", parseIntType);
        int n = (int) parseInt.invokeExact("42"); // 42

        System.out.println(len + n); // 47
    }
}
```

### `invokeExact` vs `invoke`

- **`invokeExact`** requires the argument and return types at the call site to match the handle's `MethodType` *exactly* — no widening, no boxing conversions. If they don't match, it throws `WrongMethodTypeException`. This is the fast path.
- **`invoke`** is more forgiving: it performs the same kind of adaptation reflection would (widening primitives, boxing/unboxing, casting), at a small extra cost.

```java
MethodHandle mh = MethodHandles.lookup()
        .findStatic(Math.class, "max", MethodType.methodType(int.class, int.class, int.class));

int a = (int) mh.invokeExact(3, 5);        // works: types match exactly
// Object r = mh.invoke((Object) 3, (Object) 5); // invoke() would adapt this automatically
```

### Why Method Handles Are Faster Than Reflection

- `Method.invoke` always boxes arguments into an `Object[]` and does a security/access check on every call (unless cached).
- A `MethodHandle`, once resolved, behaves much more like a direct call to the JIT compiler — it can be inlined, and the JVM has special bytecode support (`invokedynamic`) and call-site optimization for it. Repeated calls through the same `MethodHandle` get progressively faster as the JIT specializes them, approaching the speed of a hand-written call.

### `LambdaMetafactory`: How Lambdas Are Actually Implemented

When you write a lambda expression, the compiler does **not** generate an anonymous inner class (unlike pre-Java-8 anonymous classes). Instead, it emits an `invokedynamic` instruction whose bootstrap method is `LambdaMetafactory.metafactory`. At runtime, the first time that lambda expression is executed, the JVM calls the metafactory, which uses method handles to generate a small hidden class implementing the target functional interface, and then caches it. Every subsequent execution of that same lambda reuses the cached class.

```java
Runnable r = () -> System.out.println("hi");
```

compiles roughly to bytecode equivalent to:

```java
// conceptual illustration — not what you write, what the JVM does internally
CallSite site = LambdaMetafactory.metafactory(
        lookup,
        "run",                                  // the functional interface's method name
        MethodType.methodType(Runnable.class),  // factory signature
        MethodType.methodType(void.class),      // signature of Runnable.run()
        implementationMethodHandle,              // handle pointing at the lambda body
        MethodType.methodType(void.class));
Runnable r = (Runnable) site.getTarget().invokeExact();
```

This is why lambdas are much cheaper than people expect: no class file per lambda at compile time (just one small generated class at runtime, cached), and calling a lambda is close to a direct method call once the JIT has warmed up — much faster than a reflective dispatch.

## VarHandles

A **`VarHandle`** (Java 9+) is a typed reference to a variable — a field, an array element, or an off-heap memory location — that supports fine-grained control over how it's read and written, including atomic and memory-ordering operations. It was introduced to replace two older mechanisms: `sun.misc.Unsafe`'s direct memory-access methods, and the `java.util.concurrent.atomic.AtomicXFieldUpdater` classes (`AtomicIntegerFieldUpdater`, `AtomicLongFieldUpdater`, `AtomicReferenceFieldUpdater`), both of which had awkward or unsafe APIs.

### Getting a VarHandle

```java
import java.lang.invoke.*;

public class Counter {
    private volatile int count;

    private static final VarHandle COUNT_HANDLE;
    static {
        try {
            COUNT_HANDLE = MethodHandles.lookup()
                    .findVarHandle(Counter.class, "count", int.class);
        } catch (ReflectiveOperationException e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    public boolean incrementIfBelow(int max) {
        int current = (int) COUNT_HANDLE.getVolatile(this);
        while (current < max) {
            // atomically set count = current + 1, only if it's still == current
            if (COUNT_HANDLE.compareAndSet(this, current, current + 1)) {
                return true;
            }
            current = (int) COUNT_HANDLE.getVolatile(this);
        }
        return false;
    }
}
```

### Access Modes

`VarHandle` exposes several families of access, each with different performance/visibility trade-offs — from weakest/fastest to strongest/slowest guarantee:

| Access mode | Guarantee | Typical method |
|---|---|---|
| **Plain** | No atomicity, no ordering guarantee beyond normal Java semantics — like a plain field read/write | `get`, `set` |
| **Opaque** | Atomic for that single variable, but no ordering relative to other variables (no happens-before) | `getOpaque`, `setOpaque` |
| **Acquire/Release** | One-directional memory fence — `acquire` prevents later reads/writes from moving before it; `release` prevents earlier ones from moving after it | `getAcquire`, `setRelease` |
| **Volatile** | Full happens-before ordering in both directions, same as a `volatile` field | `getVolatile`, `setVolatile` |

```java
// same variable, four different consistency guarantees
int plain    = (int) COUNT_HANDLE.get(counterInstance);
int opaque   = (int) COUNT_HANDLE.getOpaque(counterInstance);
int acquire  = (int) COUNT_HANDLE.getAcquire(counterInstance);
int volatileRead = (int) COUNT_HANDLE.getVolatile(counterInstance);
```

### `compareAndSet` and Array Access

```java
import java.lang.invoke.*;

public class ArrayHandleDemo {
    public static void main(String[] args) {
        int[] array = new int[10];
        VarHandle arrayHandle = MethodHandles.arrayElementVarHandle(int[].class);

        arrayHandle.setVolatile(array, 3, 42);
        boolean updated = arrayHandle.compareAndSet(array, 3, 42, 100);
        System.out.println(updated + " " + array[3]); // true 100
    }
}
```

### Memory Fences

For advanced lock-free algorithms, `VarHandle` also exposes standalone fence methods that don't touch a specific variable but establish ordering for the surrounding code: `VarHandle.acquireFence()`, `releaseFence()`, `fullFence()`, and `loadLoadFence()`/`storeStoreFence()`. These map closely to CPU-level memory barrier instructions and are rarely needed outside of writing custom concurrency primitives (most application code should just use `java.util.concurrent` classes instead).

## Unsafe Alternatives

`sun.misc.Unsafe` was an internal, undocumented JDK class that gave direct access to raw memory operations: allocating memory outside the heap, reading/writing at arbitrary memory offsets, compare-and-swap on fields by offset, throwing exceptions without declaring them, and even instantiating objects while skipping constructors. It was never part of the official Java API — it existed only because early `java.util.concurrent` and other JDK-internal code needed low-level primitives that didn't exist anywhere else yet, and it "leaked" out because it was technically public.

### Why It's Being Removed

- It bypasses the type system and safety checks the JVM is supposed to guarantee, so misuse can crash the JVM instead of throwing a normal exception.
- It made **strong encapsulation** (from JPMS) meaningless for anyone using it, since it can reach into any object's memory layout directly.
- **JEP 471** (targeted for a recent JDK release) deprecates and eventually removes `Unsafe`'s memory-access methods for removal, pushing everyone toward the supported replacements below.
- **JEP 498** relates to further restricting/warning about deep reflective and unsafe memory access as part of the broader multi-release effort to close off "back door" access to internals (following on from JEP 403's strong encapsulation of JDK internals started in JDK 17).

The practical effect for a codebase: any dependency still calling `Unsafe.putInt`, `Unsafe.allocateInstance`, etc., will eventually stop working on newer JDKs, and code reviewers should flag any *new* direct use of `sun.misc.Unsafe` in application code as a serious red flag.

### Modern Replacements

| Old `Unsafe` use case | Modern replacement |
|---|---|
| Atomic field access, CAS on a field by offset | `VarHandle` (via `MethodHandles.lookup().findVarHandle(...)`) |
| Fast, checked-exception-free-ish reflective invocation | `MethodHandle` |
| Off-heap memory allocation, pointers, native calls | **Foreign Function & Memory API** (`java.lang.foreign`, finalized in Java 22, usable as a preview from Java 19+) |
| Running cleanup logic when an object becomes unreachable (replacing `finalize()` and ad hoc `Unsafe`-based tricks) | `java.lang.ref.Cleaner` |
| Creating a class at runtime without exposing it as a discoverable, loadable, or reflectively accessible named class (used internally for lambdas) | **Hidden classes** (`MethodHandles.Lookup.defineHiddenClass`, Java 15+) |

```java
import java.lang.foreign.*;

public class ForeignMemoryDemo {
    public static void main(String[] args) {
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment segment = arena.allocate(4); // 4 bytes, off-heap
            segment.set(ValueLayout.JAVA_INT, 0, 42);
            System.out.println(segment.get(ValueLayout.JAVA_INT, 0)); // 42
        } // memory is freed automatically when the arena closes
    }
}
```

```java
import java.lang.ref.Cleaner;

public class ResourceHolder implements AutoCloseable {
    private static final Cleaner CLEANER = Cleaner.create();

    private final Cleaner.Cleanable cleanable;

    public ResourceHolder() {
        // register cleanup logic to run if close() is never called and the object is GC'd
        this.cleanable = CLEANER.register(this, () -> System.out.println("cleaning up native resource"));
    }

    @Override
    public void close() {
        cleanable.clean(); // idempotent — safe to call once explicitly, GC won't run it twice
    }
}
```

## Service Provider Interface (SPI)

**SPI** is a pattern built into the JDK for writing pluggable applications: you define an interface (the "service"), other jars provide implementations (the "providers"), and your application discovers those implementations at runtime **without knowing their class names in advance**. This is how JDBC drivers, `Charset` providers, and many logging bridges plug themselves into applications.

### `ServiceLoader` and `META-INF/services`

The classic mechanism, working since Java 6, is a text file under `META-INF/services/` named exactly after the fully-qualified interface name, containing one implementation class name per line.

**1. Define the service interface:**

```java
package com.example.spi;

public interface GreetingProvider {
    String greet(String name);
}
```

**2. Write two implementations, in the same or different jars:**

```java
package com.example.spi.impl;
import com.example.spi.GreetingProvider;

public class FormalGreetingProvider implements GreetingProvider {
    public String greet(String name) { return "Good day, " + name + "."; }
}
```

```java
package com.example.spi.impl;
import com.example.spi.GreetingProvider;

public class CasualGreetingProvider implements GreetingProvider {
    public String greet(String name) { return "Hey " + name + "!"; }
}
```

**3. Register both in a resource file:**

```
src/main/resources/META-INF/services/com.example.spi.GreetingProvider
```

containing:

```
com.example.spi.impl.FormalGreetingProvider
com.example.spi.impl.CasualGreetingProvider
```

**4. Load and use them:**

```java
import java.util.ServiceLoader;
import com.example.spi.GreetingProvider;

public class SpiDemo {
    public static void main(String[] args) {
        ServiceLoader<GreetingProvider> loader = ServiceLoader.load(GreetingProvider.class);
        for (GreetingProvider provider : loader) {
            System.out.println(provider.greet("Ada"));
        }
    }
}
```

Output:

```
Good day, Ada.
Hey Ada!
```

Neither the interface nor `SpiDemo` needed to know the implementation class names — adding a third provider jar to the classpath, with its own `META-INF/services` entry, would make it show up automatically, with no code changes.

### The Module System Way: `provides ... with`

If you're using JPMS (the Java Module System), the equivalent declaration goes in `module-info.java` instead of a text file, and is checked at compile time:

```java
// in the module that declares the service interface
module com.example.spi {
    exports com.example.spi;
    uses com.example.spi.GreetingProvider;
}
```

```java
// in the module that provides an implementation
module com.example.spi.impl {
    requires com.example.spi;
    provides com.example.spi.GreetingProvider
        with com.example.spi.impl.FormalGreetingProvider, com.example.spi.impl.CasualGreetingProvider;
}
```

`ServiceLoader.load(...)` works exactly the same way at the call site — it transparently checks both `module-info.java` declarations and legacy `META-INF/services` files.

### Real JDK Uses

- **JDBC drivers**: since JDBC 4.0, drivers register themselves via `META-INF/services/java.sql.Driver`, which is why modern code doesn't need `Class.forName("com.mysql.cj.jdbc.Driver")` anymore — `DriverManager` uses `ServiceLoader` internally to find it just by having the jar on the classpath.
- **`java.nio.charset.spi.CharsetProvider`**: lets libraries add custom character encodings that `Charset.forName(...)` can then find.
- **Logging bridges**: SLF4J and the Java Logging API use SPI-like discovery to find and bind the actual logging backend (Logback, Log4j2, etc.) at startup, without the application code referencing a specific logging implementation directly.

## Common Code-Review Interview Pitfalls

1. **Using `Class.forName(...)` or reflection for a class that's already known at compile time.**
   Why it matters: this throws away compile-time type safety and IDE support for no benefit — reflection should be reserved for cases where the target type genuinely isn't known until runtime.
   ```java
   // Before
   Object svc = Class.forName("com.example.OrderService").getDeclaredConstructor().newInstance();
   // After
   OrderService svc = new OrderService();
   ```

2. **Calling `setAccessible(true)` on a field or method just to avoid adding a getter/constructor parameter.**
   Why it matters: it silently breaks encapsulation, is fragile across refactors (renaming the field breaks it with no compile error, just a runtime `NoSuchFieldException`), and will fail outright with `InaccessibleObjectException` on modularized JDK internals starting with Java 17.
   ```java
   // Before
   Field f = obj.getClass().getDeclaredField("balance");
   f.setAccessible(true);
   double balance = (double) f.get(obj);
   // After
   double balance = obj.getBalance();
   ```

3. **Not caching `Method`/`Field`/`Constructor` lookups that happen inside a loop or a hot request path.**
   Why it matters: `getDeclaredMethod`, `getField`, etc. are relatively expensive lookups; doing them on every call instead of once at startup can dominate the cost of an otherwise fast method.
   ```java
   // Before
   for (Order o : orders) {
       Method m = o.getClass().getMethod("getTotal");
       sum += (double) m.invoke(o);
   }
   // After
   Method m = Order.class.getMethod("getTotal"); // looked up once, outside the loop
   for (Order o : orders) sum += (double) m.invoke(o);
   ```

4. **Assuming `RetentionPolicy.CLASS` (the default) annotations are readable via reflection.**
   Why it matters: only `RUNTIME`-retained annotations are visible to `getAnnotation(...)`; forgetting `@Retention(RetentionPolicy.RUNTIME)` makes `isAnnotationPresent` silently return `false` at runtime with no compile error.
   ```java
   // Before — no @Retention at all defaults to CLASS, invisible at runtime
   public @interface Auditable { }
   // After
   @Retention(RetentionPolicy.RUNTIME)
   public @interface Auditable { }
   ```

5. **Trying to create a JDK dynamic proxy for a class instead of an interface.**
   Why it matters: `java.lang.reflect.Proxy` only supports interfaces; attempting to pass a concrete class to `newProxyInstance` throws `IllegalArgumentException` — the candidate needs to know to reach for CGLIB/ByteBuddy (or Spring's `proxyTargetClass=true`) instead.
   ```java
   // Before — throws IllegalArgumentException at runtime
   Proxy.newProxyInstance(cl, new Class<?>[]{ OrderServiceImpl.class }, handler);
   // After — proxy the interface it implements
   Proxy.newProxyInstance(cl, new Class<?>[]{ OrderService.class }, handler);
   ```

6. **Forgetting that a Spring `@Transactional` (or similar AOP-advised) method call from *within the same class* bypasses the proxy.**
   Why it matters: AOP proxies (whether JDK or CGLIB) only intercept calls that come in through the proxy object from the *outside*; an internal `this.otherMethod()` call skips the proxy entirely, silently disabling the transaction, caching, or security check.
   ```java
   // Before — internal call bypasses the transactional proxy
   public void placeOrder() {
       this.chargeCard(); // @Transactional on chargeCard() has no effect here
   }
   // After — inject/call through the bean so the proxy intercepts it
   public void placeOrder() {
       orderServiceSelfProxy.chargeCard();
   }
   ```

7. **Mixing up `invoke` and `invokeExact` on a `MethodHandle` and expecting automatic type adaptation.**
   Why it matters: `invokeExact` requires the exact `MethodType`, including matching primitive vs. boxed types; a mismatch throws `WrongMethodTypeException` at runtime instead of a compile error, which is easy to miss in review since the code compiles fine.
   ```java
   // Before — mh's MethodType is (int)int, throws WrongMethodTypeException
   Object result = mh.invokeExact((Object) 5);
   // After
   int result = (int) mh.invokeExact(5);
   ```

8. **Using `AtomicIntegerFieldUpdater`/`AtomicLongFieldUpdater` in new code instead of `VarHandle`.**
   Why it matters: the field-updater classes are older, more error-prone (the field must be exactly `volatile` and non-private relative to the updater's caller, with easy-to-miss reflective setup), and `VarHandle` supersedes them with clearer semantics and better performance.
   ```java
   // Before
   private static final AtomicIntegerFieldUpdater<Counter> UPDATER =
       AtomicIntegerFieldUpdater.newUpdater(Counter.class, "count");
   // After
   private static final VarHandle COUNT_HANDLE;
   static {
       try { COUNT_HANDLE = MethodHandles.lookup().findVarHandle(Counter.class, "count", int.class); }
       catch (ReflectiveOperationException e) { throw new ExceptionInInitializerError(e); }
   }
   ```

9. **Using `getVolatile`/`setVolatile` on a `VarHandle` when a weaker (and cheaper) access mode like `getOpaque` or plain access would suffice.**
   Why it matters: full volatile semantics impose a memory fence on every access; if the algorithm doesn't actually need cross-thread ordering guarantees for that particular read, using the strongest mode by default is a needless performance cost, and interviewers look for awareness that access modes are a real trade-off, not just decoration.
   ```java
   // Before — full fence on every read, even for a value only this thread touches
   int v = (int) handle.getVolatile(obj);
   // After — plain access when there's no cross-thread visibility requirement
   int v = (int) handle.get(obj);
   ```

10. **Any new code calling `sun.misc.Unsafe` directly.**
    Why it matters: `Unsafe` is an unsupported internal API being deprecated for removal (JEP 471/498); it bypasses the type system, breaks under strong encapsulation, and has fully supported modern replacements — flagging this in review prevents a maintenance/upgrade landmine.
    ```java
    // Before
    unsafe.putInt(obj, offset, 42);
    // After
    intHandle.set(obj, 42); // via VarHandle
    ```

11. **Registering a `ServiceLoader` provider without a public no-arg constructor.**
    Why it matters: `ServiceLoader` instantiates providers reflectively via a public no-arg constructor by default; a provider with only a parameterized constructor (or a private one) fails at load time with `ServiceConfigurationError`, often discovered only in production when that particular provider is finally needed.
    ```java
    // Before — no no-arg constructor, ServiceConfigurationError at load time
    public class DbGreetingProvider implements GreetingProvider {
        public DbGreetingProvider(DataSource ds) { ... }
    }
    // After — provide a public no-arg constructor, or a static "provider()" factory method
    public class DbGreetingProvider implements GreetingProvider {
        public DbGreetingProvider() { this.ds = DataSourceRegistry.getDefault(); }
    }
    ```

12. **Mismatched or misspelled `META-INF/services` file name vs. the actual interface's fully-qualified name.**
    Why it matters: the file name must exactly match the service interface's fully-qualified class name; a typo or a stale name after a package rename means `ServiceLoader.load(...)` silently finds zero providers instead of failing loudly, which is confusing to debug.
    ```
    # Before — file named for the old package after a refactor
    META-INF/services/com.example.old.GreetingProvider
    # After — matches the current interface location
    META-INF/services/com.example.spi.GreetingProvider
    ```

13. **Writing a custom annotation processor (or Lombok-style tool) that mutates behavior at runtime via reflection when compile-time code generation would work and be far faster.**
    Why it matters: reflection-based runtime frameworks pay a lookup/invocation cost on every use and fight the module system's strong encapsulation; compile-time generation (like MapStruct, Dagger, Immutables) produces plain, fast, debuggable Java with the same errors caught at build time instead of at runtime.
    ```java
    // Before — reflective field copy at runtime, every call
    for (Field f : Dto.class.getDeclaredFields()) { f.setAccessible(true); ... }
    // After — a MapStruct-generated mapper compiled ahead of time
    Dto dto = DtoMapper.INSTANCE.toDto(entity);
    ```

14. **Assuming `@Inherited` propagates an annotation across methods or fields, not just class hierarchies.**
    Why it matters: `@Inherited` only affects whether a *class-level* annotation is visible on subclasses via `getAnnotation`; it has no effect on method or field annotations, so code that expects an overridden method to "inherit" an annotation from the parent's method will get `null` back.
    ```java
    // Before — expecting method-level @Inherited to work
    @Retention(RetentionPolicy.RUNTIME)
    @Inherited
    @Target(ElementType.METHOD)
    @interface Cached { }
    // Derived.class.getMethod("compute").getAnnotation(Cached.class) still returns null
    // After — re-annotate the overriding method explicitly, or check the superclass method too
    class Derived extends Base {
        @Cached
        @Override
        void compute() { ... }
    }
    ```

15. **Ignoring `Proxy.isProxyClass(...)` / unwrapping needs when logging, serializing, or type-checking an object that might be a dynamic proxy.**
    Why it matters: a dynamic proxy's `getClass()` returns a synthetic proxy class, not the real implementation type; code that does `instanceof SomeConcreteClass` or relies on `getClass().getSimpleName()` for logging can behave unexpectedly when handed a proxied bean (common with Spring AOP-advised beans).
    ```java
    // Before — logs a generated proxy class name like "$Proxy42", not the real service
    log.info("Handler: {}", handler.getClass().getSimpleName());
    // After — use the interface, or AopUtils.getTargetClass(handler) in Spring
    log.info("Handler: {}", AopUtils.getTargetClass(handler).getSimpleName());
    ```
