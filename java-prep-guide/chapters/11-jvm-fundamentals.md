# 11. JVM Fundamentals

Every Java program you review eventually runs on the JVM (Java Virtual Machine), so understanding what happens between `javac` and a running thread is essential for spotting real bugs — not just style issues. This chapter walks through the JDK/JRE/JVM split, how source becomes bytecode and gets loaded and run, how the JIT (Just-In-Time compiler) makes hot code fast, how class loaders isolate and share code, and what the Java Memory Model actually guarantees about threads seeing each other's writes. All examples target Java 21+ on HotSpot, the default JVM shipped in OpenJDK.

## Table of Contents

- [JDK, JRE, JVM Architecture](#jdk-jre-jvm-architecture)
- [Compilation, Class Loading, and Execution](#compilation-class-loading-and-execution)
- [Bytecode](#bytecode)
- [JIT Compilation](#jit-compilation)
- [Class Loaders](#class-loaders)
- [Java Memory Model (JMM)](#java-memory-model-jmm)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## JDK, JRE, JVM Architecture

Three acronyms get thrown around loosely. Here is what each one actually is.

| Term | Full name | What it contains |
|---|---|---|
| JVM | Java Virtual Machine | The engine that executes bytecode: class loader, runtime memory areas, interpreter, JIT compiler, garbage collector. |
| JRE | Java Runtime Environment | JVM + core class libraries (`java.lang`, `java.util`, ...) needed to *run* compiled programs. No compiler. |
| JDK | Java Development Kit | JRE + development tools: `javac` (compiler), `javap` (disassembler), `jar`, `jlink`, `jshell`, debuggers, etc. Needed to *build* programs. |

**Important nuance for Java 11+:** the JRE as a separately downloadable, separately installed artifact was removed starting with Java 11. Oracle and OpenJDK now ship only the JDK. If you need a smaller, runtime-only image (the old JRE's job), you build one yourself with `jlink`, which assembles a custom minimal runtime image containing only the modules your application actually uses.

```bash
# Build a custom "JRE-like" runtime image containing only java.base and java.sql
jlink --add-modules java.base,java.sql \
      --output my-custom-runtime \
      --strip-debug --no-header-files --no-man-pages

# Run your app with it instead of a full JDK
my-custom-runtime/bin/java -jar app.jar
```

### JVM runtime data areas

When the JVM starts, it carves up memory into distinct regions. Some are shared by all threads, some exist one-per-thread. Getting this picture straight is the foundation for understanding `OutOfMemoryError` variants, thread-safety, and GC behavior later.

```
                     JVM PROCESS
   ┌────────────────────────────────────────────────────────────┐
   │  SHARED (one per JVM instance)                             │
   │                                                            │
   │   ┌─────────────────────┐   ┌─────────────────────────┐    │
   │   │        HEAP         │   │       METASPACE         │    │
   │   │ objects, arrays,    │   │ class metadata, method  │    │
   │   │ instance fields     │   │ bytecode, constant pool │    │
   │   │ (Young + Old gen)   │   │ (native memory, not     │    │
   │   │ GC-managed          │   │  bound by -Xmx)         │    │
   │   └─────────────────────┘   └─────────────────────────┘    │
   │                                                            │
   │   ┌─────────────────────────────────────────────────┐      │
   │   │              CODE CACHE                          │      │
   │   │  JIT-compiled native machine code (C1/C2 output) │      │
   │   └─────────────────────────────────────────────────┘      │
   ├────────────────────────────────────────────────────────────┤
   │  PER-THREAD (one set per Java thread)                      │
   │                                                            │
   │  Thread A                    Thread B                      │
   │  ┌───────────────┐           ┌───────────────┐             │
   │  │  JVM STACK    │           │  JVM STACK    │             │
   │  │ frames: local │           │ frames: local │             │
   │  │ vars, operand │           │ vars, operand │             │
   │  │ stack, refs   │           │ stack, refs   │             │
   │  ├───────────────┤           ├───────────────┤             │
   │  │  PC REGISTER  │           │  PC REGISTER  │             │
   │  │ next bytecode │           │ next bytecode │             │
   │  │ instruction   │           │ instruction   │             │
   │  ├───────────────┤           ├───────────────┤             │
   │  │ NATIVE METHOD │           │ NATIVE METHOD │             │
   │  │ STACK (JNI)   │           │ STACK (JNI)   │             │
   │  └───────────────┘           └───────────────┘             │
   └────────────────────────────────────────────────────────────┘
```

| Region | Shared or per-thread | Holds | Overflow error |
|---|---|---|---|
| Heap | Shared | All objects and arrays, instance fields | `OutOfMemoryError: Java heap space` |
| Metaspace | Shared | Class metadata, method bytecode, constant pools (replaced PermGen since Java 8) | `OutOfMemoryError: Metaspace` |
| Code Cache | Shared | Native machine code produced by the JIT | `OutOfMemoryError: CodeCache` (rare, tune `-XX:ReservedCodeCacheSize`) |
| JVM Stack | Per-thread | Stack frames: local variables, operand stack, return addresses | `StackOverflowError` |
| PC Register | Per-thread | Address of the currently executing bytecode instruction | n/a |
| Native Method Stack | Per-thread | State for native (JNI) method calls | `StackOverflowError` (native) |

A quick mental model: **heap is for objects, stacks are for method calls, metaspace is for the classes themselves.** Local primitive variables and object *references* live on the stack; the objects they point to live on the heap.

```java
void example() {
    int x = 42;                 // primitive lives on this thread's stack frame
    StringBuilder sb = new StringBuilder(); // "sb" reference on stack,
                                             // the StringBuilder object itself on the heap
}
```

## Compilation, Class Loading, and Execution

Getting from a `.java` file to running code has five distinct stages. Interview reviewers care whether you know *when* things like static initializers actually fire, because that is where subtle bugs (and infinite loops, and `NoClassDefFoundError`s from failed static blocks) hide.

```
 source.java --javac--> source.class (bytecode)
       │
       ▼
 ┌───────────┐   ┌────────────────────────────┐   ┌────────────┐   ┌──────────┐
 │  LOADING  │→  │         LINKING            │→  │INITIALIZATION│→ │ EXECUTION│
 │ find      │   │  1. Verify (bytecode is    │   │ run static  │   │ run main │
 │ bytes,    │   │     safe & well-formed)    │   │ initializers│   │ or invoked│
 │ create    │   │  2. Prepare (allocate      │   │ and static  │   │ method   │
 │ Class obj │   │     static fields, defaults)│   │ blocks, top │   │           │
 │           │   │  3. Resolve (symbolic refs │   │ to bottom   │   │           │
 │           │   │     → direct references,   │   │             │   │           │
 │           │   │     can be lazy)           │   │             │   │           │
 └───────────┘   └────────────────────────────┘   └────────────┘   └──────────┘
```

1. **Compilation (`javac`):** Human-readable `.java` source is compiled into platform-independent bytecode stored in a `.class` file.
2. **Loading:** A class loader locates the bytes (from disk, a JAR, or the network) and creates a `java.lang.Class` object representing the type in the JVM.
3. **Linking**, which has three sub-steps:
   - **Verify** — the bytecode verifier checks the class file is structurally valid and doesn't violate JVM safety rules (no stack underflows, no illegal casts, etc). This is what stops hand-crafted or corrupted bytecode from crashing the JVM.
   - **Prepare** — static fields are allocated and set to default values (`0`, `null`, `false`) — *not* their initializer expressions yet.
   - **Resolve** — symbolic references (like `"com/foo/Bar"`) in the constant pool are resolved to actual memory addresses/direct references. HotSpot resolves most of this lazily, on first use.
4. **Initialization:** Static initializer blocks and static field initializer expressions run, **in textual order, top to bottom**, exactly once per class, the first time the class is "actively used."
5. **Execution:** The JVM starts interpreting (and later JIT-compiling) the bytecode of the entry point (`main`, or whatever invoked the class).

### When does initialization actually trigger?

A class is only initialized on first **active use** — not simply on loading. Active use includes:
- Creating an instance (`new`).
- Calling a static method.
- Accessing/assigning a static field (except a `static final` compile-time constant).
- Reflection (`Class.forName(name)` with default `initialize=true`).
- Initializing a subclass (forces superclass init first).

```java
class Parent {
    static { System.out.println("Parent init"); }
}

class Child extends Parent {
    static { System.out.println("Child init"); }
    static int VALUE = 10;
}

public class Demo {
    public static void main(String[] args) {
        System.out.println("before");
        int v = Child.VALUE;   // triggers Parent init, THEN Child init
        System.out.println("after: " + v);
    }
}
```

Output:
```
before
Parent init
Child init
after: 10
```

Notice: merely referencing `Child` in the source doesn't run anything. Initialization is deferred until `Child.VALUE` is actually touched, and superclasses always initialize before subclasses.

```bash
# See the class-loading and initialization events for a run
java -Xlog:class+init=info Demo
```

## Bytecode

Bytecode is the JVM's instruction set — a compact, platform-independent set of opcodes that `javac` emits instead of native machine code. The JVM's execution model is a **stack machine**: most instructions pop operands off an "operand stack" (part of the current stack frame) and push results back, rather than working with named CPU registers.

### A small example

```java
public class MathOps {
    public int add(int a, int b) {
        return a + b;
    }

    public static int square(int x) {
        return x * x;
    }
}
```

Compile it and disassemble with `javap`:

```bash
javac MathOps.java
javap -c MathOps.class
```

Output (trimmed to the two methods):

```
  public int add(int, int);
    Code:
       0: iload_1        // push local var 1 (a) onto operand stack
       1: iload_2        // push local var 2 (b) onto operand stack
       2: iadd            // pop both, push their sum
       3: ireturn          // pop and return the top of the operand stack

  public static int square(int);
    Code:
       0: iload_0        // push local var 0 (x) — note: no "this" slot for static methods
       1: iload_0
       2: imul
       3: ireturn
```

### The operand stack model

Each method invocation gets its own **stack frame**, containing:
- **Local variable slots** (`local var 0`, `1`, `2`, ...) — parameters and local variables. For instance methods, slot 0 is always `this`; static methods have no such slot, which is why `square`'s `x` is slot 0 while `add`'s `a`/`b` are slots 1/2 (slot 0 is `this`).
- **The operand stack** — a small scratch stack instructions push to and pop from. `iload_1` pushes; `iadd` pops two ints and pushes their sum; `ireturn` pops the return value.

### Common opcodes

| Opcode | Meaning |
|---|---|
| `iload_n` / `aload_n` | Push int / object-reference local var `n` onto operand stack |
| `istore_n` / `astore_n` | Pop top of stack into int / object-reference local var `n` |
| `iadd`, `imul`, `isub` | Pop two ints, push arithmetic result |
| `getfield` / `putfield` | Read/write an instance field |
| `getstatic` / `putstatic` | Read/write a static field |
| `invokevirtual` | Call an instance method using dynamic dispatch (normal polymorphic call — most common) |
| `invokestatic` | Call a static method (no receiver, resolved at compile time) |
| `invokespecial` | Call a constructor, a private method, or a superclass method via `super.foo()` — non-virtual, resolved statically |
| `invokeinterface` | Call a method through an interface reference (needs extra runtime lookup vs `invokevirtual`) |
| `invokedynamic` | Defer the call-site linking logic to a "bootstrap method" resolved at first execution — powers lambdas, method references, and string concatenation |

```bash
# Disassemble with line numbers and the constant pool too
javap -c -p -v MathOps.class
```

### The constant pool

Every `.class` file has a **constant pool**: a table of literals, class/method/field names, and symbolic references that the bytecode indexes into instead of embedding raw strings and numbers inline. `javap -v` prints it:

```
Constant pool:
   #1 = Methodref          #6.#15         // java/lang/Object."<init>":()V
   #2 = Class              #16            // MathOps
   #3 = Utf8               add
   ...
```

An instruction like `invokevirtual #7` doesn't carry a class/method name directly — it carries an index into this table, which is resolved to an actual method address during linking (the "Resolve" step described earlier).

### `invokedynamic`: lambdas and string concatenation

Before Java 8, every method call site was one of the four `invoke*` opcodes above, each resolved to a fixed target. `invokedynamic` (added in Java 7, exploited heavily from Java 8 onward) instead calls a **bootstrap method** the first time a call site executes; that bootstrap method decides — and caches — how the call should actually be linked. This lets `javac` avoid generating a synthetic inner class for every single lambda.

```java
Runnable r = () -> System.out.println("hi");
```

```bash
javap -c -p Demo.class
```
```
  0: invokedynamic #7,  0    // InvokeDynamic #0:run:()Ljava/lang/Runnable;
  5: astore_1
```

The actual lambda body is compiled into a private synthetic method, and `invokedynamic` + `LambdaMetafactory` wire up a `Runnable` instance pointing at it lazily, at first call.

Since Java 9, string concatenation (`"a" + b + "c"`) also compiles to `invokedynamic` calling `StringConcatFactory`, instead of the old pattern of allocating a `StringBuilder` and chaining `.append()` calls:

```java
String s = "Count: " + count;
```
```
  invokedynamic #2,  0   // InvokeDynamic #0:makeConcatWithConstants:(I)Ljava/lang/String;
```

```bash
# Try it yourself
echo 'public class Cat { String go(int n){ return "n=" + n; } }' > Cat.java
javac Cat.java && javap -c -p Cat.java
```

## JIT Compilation

Bytecode is portable but interpreting it instruction-by-instruction is slow. HotSpot (the default JVM) starts by **interpreting** bytecode, then compiles the "hot" (frequently executed) parts to native machine code on the fly — hence *Just-In-Time*.

### Tiered compilation: interpreter → C1 → C2

```
   bytecode
      │
      ▼
 ┌───────────┐   method called often     ┌────────────┐   still hot,
 │INTERPRETER│ ───────────────────────▶  │  C1 (client)│ ──────────────▶ ┌────────────┐
 │ (slow, no │   (thousands of calls)    │  fast compile│  long-running   │ C2 (server) │
 │ warm-up)  │                           │  light opt   │  method         │ heavy opt,  │
 └───────────┘                           └────────────┘                  │ aggressive  │
                                                                          │ inlining    │
                                                                          └────────────┘
```

- **Interpreter** — executes bytecode directly, one instruction at a time. Starts instantly, no compile delay, but slow per-iteration.
- **C1 ("client compiler")** — compiles quickly with light optimization. Good for short-lived or moderately-hot code; gets you off the interpreter fast.
- **C2 ("server compiler")** — compiles slowly but produces highly optimized native code: aggressive inlining, loop unrolling, escape analysis, etc. Reserved for genuinely hot methods.

Modern HotSpot uses **tiered compilation** by default: code moves interpreter → C1 (with several sub-tiers of profiling) → C2, escalating only if a method really is called a lot.

### Hot spots and OSR

The JVM counts method invocations and *loop back-edges* (loop iterations). Once a counter crosses a threshold, the method is queued for JIT compilation. If a **single long-running loop** inside a method gets hot before the method itself is recompiled, HotSpot can do **On-Stack Replacement (OSR)**: it JIT-compiles just that loop and swaps the interpreter's execution *in place*, mid-method, onto the compiled version — without waiting for the method to be called again.

```java
long sum(int n) {
    long s = 0;
    for (int i = 0; i < n; i++) {   // if n is huge, this loop alone can trigger OSR
        s += i;
    }
    return s;
}
```

### Inlining and deoptimization

**Inlining** replaces a call site with the callee's body, eliminating call overhead and opening the door to further optimization (e.g., dead-code elimination once constants propagate through). C2 inlines aggressively, including through virtual calls when it can prove (via profiling) there's effectively only one implementation in practice.

**Deoptimization** is the safety valve: if an assumption the JIT baked in turns out false at runtime (e.g., a second implementation of an interface finally shows up, invalidating a "monomorphic" inlining bet), the JVM discards the compiled code for that method, falls back to the interpreter, and re-profiles. This is invisible in correctness terms — Java's semantics never change — but it does explain sudden latency blips in production right after a "cold" code path executes for the first time.

```java
interface Shape { double area(); }
class Circle implements Shape { public double area() { return 3.14; } }
class Square implements Shape { public double area() { return 4.0; } }

// If callers only ever pass Circle for a long time, C2 may speculate
// "this call site is monomorphic" and inline Circle.area() directly.
// The moment a Square shows up, that assumption breaks -> deoptimization.
double totalArea(List<Shape> shapes) {
    double total = 0;
    for (Shape s : shapes) total += s.area();
    return total;
}
```

### Why microbenchmarks need JMH and warm-up

A naive "time it in a loop with `System.nanoTime()`" benchmark is almost always wrong, because:
- The **interpreter** runs the first iterations, which are much slower than the eventual steady state.
- **JIT compilation happens on background threads mid-benchmark**, so timings from early iterations mix cold and hot code.
- **Dead-code elimination** can let the JIT discard a whole "benchmarked" computation if its result is never used.

[JMH](https://github.com/openjdk/jmh) (Java Microbenchmark Harness) solves this by running explicit **warm-up iterations** (to let JIT compilation stabilize) before measuring, and by using tricks (blackholes, forcing consumption of results) to prevent dead-code elimination from making your "fast" code fast simply because it does nothing.

```java
@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.NANOSECONDS)
@Warmup(iterations = 5, time = 1)
@Measurement(iterations = 5, time = 1)
@Fork(1)
public class StringBenchmark {
    @Benchmark
    public String concat() {
        return "a" + "b" + "c";
    }
}
```

### Observing the JIT

```bash
# Print every JIT compilation event as it happens (method, tier, time)
java -XX:+PrintCompilation MyApp

# Typical lines:
#    1     3       3       java.lang.String::hashCode (60 bytes)
#  100    45       4       MyApp::hotLoop (120 bytes)   made not entrant   <- deopt!
```

`made not entrant` in the output is the visible sign of a deoptimization — that compiled version was thrown away.

### AOT and CDS in one paragraph

The JIT's warm-up cost is a real problem for short-lived processes (CLIs, serverless functions, fast-restarting microservices). Two HotSpot features help: **CDS (Class Data Sharing)**, which pre-parses and serializes class metadata into a shared archive (`-Xshare:dump` / `-XX:SharedArchiveFile`) so subsequent JVM startups skip re-parsing those classes, and its evolution **AppCDS / dynamic CDS archives**, which extend this to application classes, not just JDK classes. Separately, **Project Leyden** and tools like GraalVM Native Image push further into full **AOT (Ahead-Of-Time) compilation**, compiling to native machine code before the process even starts, trading some peak-throughput JIT optimizations for near-instant startup — a trade-off worth naming in an interview when startup latency comes up.

```bash
# Create and use an AppCDS archive
java -Xshare:off -XX:ArchiveClassesAtExit=app.jsa -jar app.jar
java -XX:SharedArchiveFile=app.jsa -jar app.jar
```

## Class Loaders

A **class loader** is the object responsible for turning a class name into a `Class` object — locating the bytecode and defining it into the JVM. HotSpot has a built-in hierarchy of three:

```
   Bootstrap ClassLoader   (native, written in C++, no Java superclass)
        loads: java.base and other core JDK modules (java.lang.*, java.util.*)
           │
           ▼ parent
   Platform ClassLoader     (was "Extension" loader before Java 9)
        loads: other JDK-provided modules not in java.base
           │
           ▼ parent
   Application ClassLoader  (a.k.a. "system" class loader)
        loads: your application's classes from the classpath / module path
```

### Parent delegation

By default, when a class loader is asked to load a class, it **first asks its parent** to try, and only attempts to load it itself if the parent fails (`ClassNotFoundException`). This means core JDK classes like `java.lang.String` are always loaded by the bootstrap loader — even if you name your own class `java.lang.String`, the delegation model prevents your version from shadowing the real one (and the JVM outright rejects defining classes in the `java.*` package from a non-bootstrap loader).

```java
public class WhichLoader {
    public static void main(String[] args) {
        System.out.println(String.class.getClassLoader());          // null (bootstrap)
        System.out.println(WhichLoader.class.getClassLoader());     // AppClassLoader
        System.out.println(WhichLoader.class.getClassLoader().getParent()); // PlatformClassLoader
    }
}
```

### Custom class loaders

Frameworks that need to load code dynamically — plugin systems, application servers deploying multiple WARs, dependency-shading/isolation tools — write custom class loaders, usually by extending `ClassLoader` or `URLClassLoader`.

```java
public class PluginClassLoader extends URLClassLoader {
    public PluginClassLoader(URL[] pluginJars, ClassLoader parent) {
        super(pluginJars, parent);
    }
    // Override findClass()/loadClass() to change lookup order,
    // e.g. "child-first" delegation for plugin isolation (as Tomcat's
    // webapp classloader does, breaking strict parent-first order
    // deliberately, on purpose, for isolation).
}
```

```bash
# Load and run a class from an arbitrary jar at runtime, illustrating dynamic loading
java -cp plugin.jar -Djava.system.class.loader=com.example.PluginClassLoader Main
```

### `ClassNotFoundException` vs `NoClassDefFoundError`

These are commonly confused in code review and interviews:

| | `ClassNotFoundException` | `NoClassDefFoundError` |
|---|---|---|
| Type | Checked `Exception` | `Error` (subclass of `LinkageError`) |
| When | You explicitly ask to load a class by name — `Class.forName(...)`, or a custom class loader's `loadClass()` — and it isn't found. | A class was **available and successfully compiled against**, but is **missing at runtime**, OR the class was found once but its **static initializer threw**, poisoning it for all future references. |
| Typical cause | Wrong string in `Class.forName`, missing JDBC driver, typo'd reflection lookup. | Jar removed from classpath after compiling against it; a previous load attempt failed (e.g. static init threw an exception) so the JVM marks the class permanently unusable. |

```java
try {
    Class.forName("com.example.MissingDriver"); // -> ClassNotFoundException if absent
} catch (ClassNotFoundException e) {
    // handle: driver jar not on classpath
}
```

```java
class Broken {
    static int x = 1 / 0;   // ArithmeticException during static init
}

class UsesBroken {
    void run() {
        new Broken(); // first call: ExceptionInInitializerError
        new Broken(); // second call in this or another method: NoClassDefFoundError
                       // (class is now permanently "erroneous")
    }
}
```

### Class identity = (name, loader)

The JVM treats two classes as **the same type only if both the fully-qualified name AND the defining class loader match**. Load the "same" `.class` bytes through two different loaders and you get two distinct, mutually incompatible `Class` objects — a frequent source of baffling `ClassCastException`s with messages like `com.example.Foo cannot be cast to com.example.Foo`.

```java
Class<?> a = loader1.loadClass("com.example.Foo");
Class<?> b = loader2.loadClass("com.example.Foo");
System.out.println(a == b);              // false!
System.out.println(a.equals(b));          // false!
// Casting an instance loaded by loader1 to the type loaded by loader2 throws
// ClassCastException even though the source code is byte-for-byte identical.
```

### Classloader leaks in application servers

Servlet containers like Tomcat give **each deployed webapp its own class loader**, so redeploying a WAR without restarting the whole server means loading a fresh set of classes. If anything outside the webapp — a thread the app started, a static field in a JDK class, a `ThreadLocal`, JDBC driver registries — keeps a reference to a class (or an instance of a class) from the old webapp's loader, **the entire old class loader, and every class and object it ever loaded, cannot be garbage collected**. Symptoms: `Metaspace` usage climbing on every redeploy, eventually `OutOfMemoryError: Metaspace`. Common causes and fixes:

```java
// LEAK: a thread started by the webapp keeps running after undeploy,
// its Thread object (loaded by the webapp's classloader) stays reachable
// from the JVM's global thread list -> webapp classloader can never be freed.
Thread worker = new Thread(this::pollForever);
worker.setDaemon(true);
worker.start();
// Fix: register a ServletContextListener.contextDestroyed() hook that
// signals the thread to stop and joins it before the webapp is unloaded.
```

### JPMS impact (the module system)

Since Java 9, the **Java Platform Module System (JPMS)** adds a layer on top of classloaders: modules declare explicit dependencies (`requires`) and exposed packages (`exports`) in a `module-info.java`. This affects class loading in two practical ways reviewers should know: (1) **strong encapsulation** — internal packages that aren't `exported` are inaccessible via reflection from outside the module by default, even if they were `public`, breaking some old reflection-heavy frameworks; (2) the platform's own bootstrap/platform/application loaders were re-organized around modules (e.g. `java.base` is the module the bootstrap loader owns), and each named module effectively gets a layer that still resolves through the same parent-delegation-style rules for unnamed/automatic modules on the classpath.

```java
// module-info.java
module com.example.app {
    requires java.sql;
    exports com.example.app.api;   // only this package is visible to other modules
    // com.example.app.internal is NOT exported -> inaccessible outside the module,
    // even via setAccessible(true) reflection, unless "opens" is declared.
}
```

## Java Memory Model (JMM)

The Java Memory Model is the specification (JLS Chapter 17) that answers a deceptively hard question: **when a thread writes a value, when is another thread guaranteed to see it?** Without a memory model, compilers and CPUs are free to reorder, cache, and optimize your code in ways that are safe for a single thread but disastrous once multiple threads are involved.

### Reordering by compiler and CPU

Both the JIT compiler and the CPU are allowed to reorder instructions and cache values in registers, as long as a **single thread** can't observe the difference. But another thread, watching from the outside, absolutely can.

```java
class Reorder {
    int a = 0;
    boolean flag = false;

    void writer() {
        a = 42;        // (1)
        flag = true;   // (2) — compiler/CPU may reorder (1) and (2)!
    }

    void reader() {
        if (flag) {
            System.out.println(a); // could print 0, not 42, without synchronization
        }
    }
}
```

Each CPU core also has its own **cache**, separate from main memory. A write by one thread might sit in that core's cache/store-buffer without being flushed to main memory (or the shared cache level other cores see) for an unbounded amount of time, from the other thread's perspective, unless something forces synchronization.

### Happens-before

The JMM defines correctness in terms of a **happens-before** relationship: if action X happens-before action Y, every effect of X (every write) is guaranteed visible to Y. Without an established happens-before edge between two threads' actions, there is **no guarantee** the second thread ever sees the first thread's writes — not "might be slow," but genuinely unspecified/broken behavior.

Key sources of happens-before edges:
- A `synchronized` block's unlock happens-before a later thread's lock on the *same monitor*.
- A `volatile` field write happens-before every subsequent `volatile` read of that same field.
- `Thread.start()` happens-before anything the started thread does.
- Everything a thread does happens-before another thread observes it has finished, via `Thread.join()`.
- Writes to `final` fields performed in a constructor happen-before any thread that gets a correctly-published reference to that object.

### `volatile`

`volatile` gives you **visibility** (every read sees the latest write, no stale caching) and prevents certain compiler/CPU reorderings around that field, but it does **not** give you atomicity or mutual exclusion for compound operations.

```java
class FlagExample {
    private volatile boolean running = true;

    void stop() { running = false; }               // thread A

    void loop() {
        while (running) {                            // thread B
            // do work
        }
    }
}
```

Without `volatile` here, the JIT is legally allowed to hoist `running` into a register in `loop()` (since single-threaded reasoning says it never changes inside the loop body), producing an **infinite loop that never sees `stop()`'s write** — this is the concrete broken-visibility bug interviewers love to ask about.

```java
// BROKEN: no volatile, no synchronization -> may spin forever on some JVMs/JITs
class BrokenFlag {
    private boolean running = true;
    void stop() { running = false; }
    void loop() { while (running) { /* spin */ } }
}
```

`volatile` does **not** make increments atomic:

```java
private volatile int counter = 0;
void increment() { counter++; }  // STILL a race: read, add, write are 3 separate steps
// Fix: use AtomicInteger, or synchronize the whole read-modify-write.
```

### `final` field freeze semantics

If an object is constructed with a `final` field, and the reference to that object is not leaked out of the constructor before it finishes, then **any thread that later obtains a reference to that object is guaranteed to see the fully-initialized value of the `final` field** — without needing any additional synchronization. This is the JMM's "safe construction via final fields" guarantee.

```java
final class Point {
    final int x, y;
    Point(int x, int y) { this.x = x; this.y = y; }
}
// Any thread that receives a Point reference through a properly published
// channel (see below) will see correct, fully-initialized x and y —
// guaranteed by final field "freeze" semantics, even with zero synchronization
// on the reading side.
```

### Safe publication

"Safe publication" means making an object visible to other threads in a way the JMM actually guarantees is safe — not just "it usually works." Common safe publication mechanisms:
- Assigning the reference to a `volatile` or `static final` field.
- Storing it into a properly locked field (`synchronized`).
- Putting it into a thread-safe collection like `ConcurrentHashMap` or `BlockingQueue`, whose own internal synchronization carries the happens-before edge.
- Initializing it as a `static` field during class initialization (class-init has its own locking guarantees).

```java
// UNSAFE publication: another thread might see a half-constructed object
public class Holder {
    public static Helper helper;
    public static void init() {
        helper = new Helper(); // another thread reading `helper` non-null
                                 // isn't guaranteed to see a fully built Helper
                                 // unless Helper's fields are all final, or
                                 // helper is volatile/static-final.
    }
}
```

```java
// SAFE publication via volatile
public class Holder {
    private static volatile Helper helper;
    public static Helper getHelper() {
        if (helper == null) {
            synchronized (Holder.class) {
                if (helper == null) helper = new Helper();
            }
        }
        return helper;
    }
}
```

### Data races vs race conditions

These terms are related but not identical, and mixing them up in review comments is a common tell:

| | Data race | Race condition |
|---|---|---|
| Definition | Two threads access the same memory location, at least one write, with no happens-before ordering between them. | Program's correctness depends on timing/interleaving of operations, regardless of memory visibility. |
| Scope | A specific JMM-defined term about memory access without synchronization. | A general concurrency term about outcome depending on execution order. |
| Example | Non-volatile `boolean running` read/written by two threads. | Two threads both doing "check-then-act" on a bank balance with a lock each holds separately — well-synchronized memory-wise but still logically racy (TOCTOU). |

A program can have a race condition without a data race (properly synchronized but logically wrong ordering), and a data race almost always implies undefined/unreliable behavior even if it "seems to work" in testing.

### `synchronized`: two jobs, not one

`synchronized` is often described only as "locking," but it actually does **two separate jobs** at once, and code reviewers should check both are needed (or that neither is skippable):

1. **Mutual exclusion** — only one thread can hold a given monitor at a time, preventing concurrent execution of the guarded block.
2. **Visibility** — entering a `synchronized` block establishes a happens-before edge with the *previous* thread that held (and released) the same lock, guaranteeing you see all of its writes made inside that lock, not just the fact that it finished.

```java
class Counter {
    private int count = 0;
    private final Object lock = new Object();

    void increment() {
        synchronized (lock) {
            count++;              // mutual exclusion: no lost updates
        }
    }

    int get() {
        synchronized (lock) {     // visibility: guaranteed to see latest count,
            return count;         // even without this being volatile
        }
    }
}
```

A frequent review mistake: locking on `increment()` but reading `count` directly without synchronization elsewhere in `get()`. That reintroduces the visibility problem even though updates themselves are safely serialized — the lock's visibility guarantee only applies to threads that *also* go through the same lock.

```bash
# See lock contention / thread states live, useful when reviewing concurrency claims
jcmd <pid> Thread.print | grep -A3 "waiting to lock"
```

## Common Code-Review Interview Pitfalls

1. **Assuming the JRE still exists as a separate download.** Since Java 11, there is no standalone JRE artifact; reviewers sometimes still ask candidates to "install the JRE" — the correct modern answer is a full JDK, or a `jlink`-built custom runtime image.
   ```bash
   jlink --add-modules java.base --output tiny-runtime
   ```

2. **Confusing heap `OutOfMemoryError` with metaspace/native exhaustion.** `-Xmx` only bounds the heap; unbounded class generation (common with proxy-heavy frameworks, or dynamically generated classes never unloaded) exhausts Metaspace instead, which needs `-XX:MaxMetaspaceSize` to even surface as a clean error instead of eating all system memory.
   ```bash
   java -Xmx512m -XX:MaxMetaspaceSize=256m -jar app.jar
   ```

3. **Believing `static` blocks run at class-load time.** They run at class **initialization**, which is deferred to first active use — code that assumes a static block has "already run" just because a class was referenced (e.g. in a type declaration or import) is wrong.
   ```java
   static { System.out.println("I might run much later than you think"); }
   ```

4. **Treating `invokevirtual` and `invokestatic` as interchangeable in reasoning about polymorphism.** A private or static "helper" method resolved via `invokestatic`/`invokespecial` cannot be overridden — reviewers should flag any comment claiming a private method "overrides" a superclass method; it just shadows/hides it.
   ```java
   class Base { private void helper() {} }      // invokespecial, not virtual dispatch
   class Sub extends Base { private void helper() {} } // unrelated method, NOT an override
   ```

5. **Expecting `+` string concatenation to always allocate a `StringBuilder`.** Since Java 9, javac lowers concatenation to `invokedynamic` + `StringConcatFactory`; reviewers shouldn't ding code for "missing an explicit `StringBuilder`" in simple concatenation — the compiler already optimizes it, though a raw loop with `+=` inside still has quadratic-cost risk and *should* use an explicit `StringBuilder`.
   ```java
   String s = "";
   for (String part : parts) s += part;  // still O(n^2) — use StringBuilder here
   ```

6. **Trusting raw loop-based microbenchmarks in a PR's "performance proof."** Without JIT warm-up and dead-code-elimination guards, a `System.nanoTime()` loop can report numbers dominated by interpreter overhead or optimized-away code. Ask for JMH results instead.
   ```java
   @Warmup(iterations = 5) @Measurement(iterations = 5)
   public class MyBenchmark { @Benchmark public int compute() { return heavy(); } }
   ```

7. **Assuming a hot method is always running JIT-compiled code.** A method can look identical in two profiling runs yet perform very differently if one run deoptimized it (e.g. a newly-loaded subclass broke a monomorphic call-site assumption). Look for `made not entrant` in `-XX:+PrintCompilation` output before blaming "the algorithm."

8. **Mixing up `ClassNotFoundException` and `NoClassDefFoundError` in exception-handling review.** Catching `ClassNotFoundException` around `Class.forName` won't help if the real problem is a static initializer that already threw once — that manifests later as `NoClassDefFoundError`, which is an `Error`, not caught by typical `Exception` handlers.
   ```java
   try { Class.forName("x.Y"); }
   catch (ClassNotFoundException e) { /* won't catch a poisoned class! */ }
   ```

9. **Comparing classes loaded by different class loaders with `==` or `instanceof` and being surprised by failures.** In app-server or plugin architectures, "the same" class loaded twice by different loaders are different types to the JVM. A `ClassCastException` with identical-looking type names on both sides is the signature symptom — check for multiple loaders before assuming a bug in the cast logic itself.

10. **Overlooking classloader leaks from long-lived threads or `ThreadLocal`s in app-server deployments.** A background thread or `ThreadLocal` value that outlives a hot-redeploy pins the entire old webapp class loader in Metaspace. Reviewers should ask: "who stops this thread / clears this ThreadLocal on shutdown?"
   ```java
   contextDestroyed(ServletContextEvent e) { worker.interrupt(); threadLocal.remove(); }
   ```

11. **Reading or writing a shared flag/field across threads with no `volatile`, lock, or other happens-before edge.** This is the textbook broken-visibility bug — it can compile, pass single-threaded tests, and still spin forever or read stale values in production, because nothing in the JMM obligates the writer's update to ever become visible to the reader.
    ```java
    boolean running = true;       // missing volatile
    void loop() { while (running) {} }   // may never see stop()'s write
    ```

12. **Believing `volatile` makes compound operations atomic.** `volatile int counter; counter++;` is still a three-step read-modify-write race between threads — visibility is guaranteed, atomicity is not. Look for `AtomicInteger`/`AtomicLong` or a lock instead.

13. **Publishing a partially-constructed object reference without a `final`/`volatile`/lock-based safe-publication path.** The classic double-checked-locking-without-volatile singleton bug: another thread can see a non-null but not-yet-fully-initialized instance.
    ```java
    private static Helper instance; // missing volatile — double-checked locking is broken here
    ```

14. **Conflating "synchronized gives mutual exclusion" with "synchronized on this block automatically makes everything else visible too."** Only accesses that go through the *same* lock get the happens-before guarantee. A `synchronized` setter paired with an unsynchronized getter on the same field is a subtle, easy-to-miss review flag — it looks safe but isn't.

15. **Calling something a "data race" when it's really a logic-level "race condition" (or vice-versa) in review comments.** A well-synchronized (no data race) check-then-act sequence can still be logically racy (TOCTOU) if the check and act aren't combined atomically — that's a race condition, not a JMM data race, and the fix (atomic compound operations / higher-level locking) is different from "just add `volatile`."
    ```java
    if (!map.containsKey(k)) map.put(k, v); // race condition even if map is a ConcurrentHashMap
    // Fix: map.putIfAbsent(k, v);
    ```
