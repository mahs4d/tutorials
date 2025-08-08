# 17. Modules & Native Interoperability

Java code does not live in a vacuum. It is organized into **packages**, packages are (optionally) organized into **modules**, and sometimes Java code needs to reach outside the JVM entirely to call **native code** or squeeze more performance out of the CPU with **vectorized** operations. This chapter walks from the oldest and most universal organizational unit (the package, present since Java 1.0) to the newest, still-incubating performance API (the Vector API). Along the way we cover the Java Platform Module System (JPMS, Java 9+) in depth, and the modern replacement for JNI: the Foreign Function & Memory API (finalized in JDK 22). We target Java 21+ throughout, calling out version-specific behavior where it matters.

- [Packages](#packages)
  - [Naming Conventions and Reverse-Domain Rules](#naming-conventions-and-reverse-domain-rules)
  - [The `package` and `import` Statements](#the-package-and-import-statements)
  - [Static Imports](#static-imports)
  - [Wildcard Imports](#wildcard-imports)
  - [The Default (Unnamed) Package](#the-default-unnamed-package)
  - [Package-Private Visibility as a Design Tool](#package-private-visibility-as-a-design-tool)
  - [`package-info.java`](#package-infojava)
  - [Split Packages](#split-packages)
  - [Directory-to-Package Mapping](#directory-to-package-mapping)
  - [Packages vs Modules](#packages-vs-modules)
- [Java Platform Module System (JPMS)](#java-platform-module-system-jpms)
  - [Problems JPMS Solves](#problems-jpms-solves)
  - [`module-info.java` Basics](#module-infojava-basics)
  - [`requires`, `requires transitive`, `requires static`](#requires-requires-transitive-requires-static)
  - [`exports` and `exports ... to`](#exports-and-exports--to)
  - [`opens` and `opens ... to`](#opens-and-opens--to)
  - [`uses` and `provides ... with`](#uses-and-provides--with)
  - [Automatic Modules and the Unnamed Module](#automatic-modules-and-the-unnamed-module)
  - [Module Path vs Class Path](#module-path-vs-class-path)
  - [Readability and Accessibility Rules](#readability-and-accessibility-rules)
  - [`jlink`, `jdeps`, and `jmod`](#jlink-jdeps-and-jmod)
  - [Strong Encapsulation and `--add-exports`/`--add-opens`](#strong-encapsulation-and---add-exports--add-opens)
  - [Worked Multi-Module Example](#worked-multi-module-example)
  - [Migration Strategy](#migration-strategy)
  - [Why Many Projects Still Don't Use JPMS](#why-many-projects-still-dont-use-jpms)
- [Foreign Function & Memory API](#foreign-function--memory-api)
  - [From JNI to FFM: Why It Changed](#from-jni-to-ffm-why-it-changed)
  - [`Arena` and Deterministic Deallocation](#arena-and-deterministic-deallocation)
  - [`MemorySegment`, `MemoryLayout`, `ValueLayout`](#memorysegment-memorylayout-valuelayout)
  - [`VarHandle` Access into Segments](#varhandle-access-into-segments)
  - [Calling a C Function: `Linker`, `SymbolLookup`, `FunctionDescriptor`](#calling-a-c-function-linker-symbollookup-functiondescriptor)
  - [Upcalls: Java Callback into C](#upcalls-java-callback-into-c)
  - [Struct Layouts](#struct-layouts)
  - [`jextract`](#jextract)
  - [`--enable-native-access` and Restricted Methods](#--enable-native-access-and-restricted-methods)
  - [FFM vs JNI Comparison Table](#ffm-vs-jni-comparison-table)
- [Vector API (Incubator)](#vector-api-incubator)
  - [Why It's Still Incubating](#why-its-still-incubating)
  - [`VectorSpecies`, Lanes, and Masks](#vectorspecies-lanes-and-masks)
  - [Enabling the Incubator Module](#enabling-the-incubator-module)
  - [Worked Example: Vectorized Dot Product vs Scalar](#worked-example-vectorized-dot-product-vs-scalar)
  - [The Loop-Plus-Tail Idiom](#the-loop-plus-tail-idiom)
  - [Dependence on Project Valhalla](#dependence-on-project-valhalla)
  - [Production Readiness Caveat](#production-readiness-caveat)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Packages

A **package** is a namespace that groups related classes and interfaces together, and it has been part of Java since version 1.0. Packages solve name collisions (two libraries can each have a `Logger` class as long as they're in different packages) and provide a coarse-grained visibility boundary through package-private access.

### Naming Conventions and Reverse-Domain Rules

Package names are conventionally all-lowercase and follow the **reversed internet domain name** of the organization that owns the code, to guarantee global uniqueness without a central registry.

```java
// Domain: teampicnic.com -> reversed -> com.teampicnic
package com.teampicnic.orders.billing;

public class InvoiceCalculator {
    // ...
}
```

| Rule | Example | Reason |
|---|---|---|
| All lowercase | `com.teampicnic.orders`, not `com.TeamPicnic.Orders` | Avoids case-sensitivity clashes on some filesystems and mixed-case ambiguity |
| Reverse domain as prefix | `com.example.app` for `example.com` | Guarantees uniqueness without a central registry |
| No Java reserved words as segments | Never `com.example.class` | `class`, `int`, `package` etc. are illegal identifiers |
| Avoid underscores/digits at segment start | `v2.api` is illegal; use `apiv2` or `api.v2` | Java identifier rules apply to each segment |
| Segments describe increasingly specific scope | `com.teampicnic.orders.billing.tax` | Mirrors directory structure and narrows responsibility |

Since JDK 11, `com.sun.tools.javac` and similar internal packages remind us why the convention matters: the JDK itself uses `java.*` and `javax.*`, reserved prefixes that application code must never use, to avoid colliding with future JDK classes.

### The `package` and `import` Statements

The `package` declaration must be the first non-comment line in a source file, and a file can declare at most one package.

```java
package com.teampicnic.orders.billing;

import java.math.BigDecimal;
import java.time.LocalDate;
import com.teampicnic.orders.catalog.Product;

public class Invoice {
    private final Product product;
    private final BigDecimal amount;
    private final LocalDate issuedOn;

    public Invoice(Product product, BigDecimal amount, LocalDate issuedOn) {
        this.product = product;
        this.amount = amount;
        this.issuedOn = issuedOn;
    }
}
```

`import` is purely a compile-time convenience: it lets you use a simple name (`Product`) instead of the fully qualified name (`com.teampicnic.orders.catalog.Product`) everywhere in the file. It has **zero runtime cost** — the compiled bytecode always references fully qualified names, and `import` statements produce nothing in the `.class` file.

Two classes are automatically visible without any `import`:

- Everything in `java.lang` (e.g., `String`, `Object`, `Math`) is imported implicitly.
- Classes in the *same package* as the current file need no import at all.

### Static Imports

A **static import** brings static members (fields or methods) into scope so you can use them unqualified, without the enclosing class name.

```java
import static java.util.Collections.emptyList;
import static java.lang.Math.PI;
import static java.lang.Math.pow;

public class CircleMath {
    public static double area(double radius) {
        return PI * pow(radius, 2); // no "Math." prefix needed
    }

    public static <T> java.util.List<T> noResults() {
        return emptyList(); // no "Collections." prefix needed
    }
}
```

Static imports are genuinely useful for well-known idioms — `assertEquals` in JUnit tests, `Math` constants, or `Collectors` factory methods in stream pipelines — where the qualifying class name adds noise without adding clarity.

**When they hurt readability:** overusing static imports removes the reader's ability to tell, at a glance, *where a symbol comes from*. A bare `of(1, 2, 3)` could be `List.of`, `Set.of`, `Stream.of`, or a custom factory — the reader has to scroll up to the imports to find out.

```java
// Avoid: which "of" is this? Which "process" is this?
import static com.teampicnic.orders.OrderUtils.*;
import static com.teampicnic.billing.BillingUtils.*;

public class Reconciler {
    void run() {
        var order = of(42);       // OrderUtils.of or BillingUtils.of? Have to check imports.
        process(order);           // ambiguous at a glance
    }
}
```

Rule of thumb: static-import only widely recognized, unambiguous helpers (`Collectors.*`, test assertion libraries, `Math.*`), and avoid it for your own domain classes where the qualifying name carries meaning.

### Wildcard Imports

`import com.teampicnic.orders.*;` imports every top-level type directly in that package (not sub-packages).

```java
// Wildcard: convenient, but hides exactly what's being pulled in
import com.teampicnic.orders.*;

public class OrderProcessor {
    Order create() { return new Order(); }
}
```

```java
// Explicit: IDE-generated, unambiguous, and diffs cleanly in code review
import com.teampicnic.orders.Order;

public class OrderProcessor {
    Order create() { return new Order(); }
}
```

| | Wildcard `import pkg.*;` | Explicit `import pkg.Type;` |
|---|---|---|
| Compile-time cost | Identical — resolved once at compile time either way | Identical |
| Readability | Reader must infer which types are actually used | Every used type is listed |
| Merge conflicts | Fewer lines, but ambiguous diffs | More lines, but precise diffs |
| Name collisions | Can silently break when the package adds a new type with a name you already use | Immune — each import is explicit |
| Common tooling default | Most style guides (Google Java Style, and most IDEs by default) disallow it | Preferred by nearly every style guide |

Wildcard imports are almost universally discouraged in professional style guides and in code review, precisely because they trade a small amount of typing for a real risk: if the imported package later adds a class with the same simple name as something else you're using, your code can silently start resolving to the wrong type, or fail to compile with an ambiguity error far from where the real problem is.

### The Default (Unnamed) Package

A source file with no `package` statement lives in the **default (unnamed) package**.

```java
// No package declaration at all — lives in the default package
public class QuickTest {
    public static void main(String[] args) {
        System.out.println("Hello from the default package");
    }
}
```

**Why to never use it in real code:**

1. **It cannot be imported.** Classes in the default package are invisible to any class that belongs to a named package, since `import` requires a package-qualified name.
2. **It cannot participate in JPMS.** A module cannot export the default package, so any type there is entirely inaccessible from a modular application.
3. **Name collisions are almost guaranteed** as soon as the project grows — two unrelated classes both wanting to be called `Utils` will collide immediately with no namespace to separate them.
4. **Build tools and JavaDoc treat it as second class** — many tools assume every meaningful class has a package and behave oddly (or refuse to process the class at all) otherwise.

The default package is acceptable for a one-off scratch file (`Test.java` you compile and delete) and nothing else. Every class that will be checked in should declare a package.

### Package-Private Visibility as a Design Tool

When no access modifier is written, a member or top-level class has **package-private** (also called *default*) visibility: accessible only to code in the exact same package.

```java
package com.teampicnic.orders.billing;

public class InvoiceService {          // public API entry point
    private final TaxCalculator tax = new TaxCalculator();

    public java.math.BigDecimal totalFor(Invoice invoice) {
        return tax.calculate(invoice); // package-private class used internally
    }
}

class TaxCalculator {                  // package-private: not part of the public API
    java.math.BigDecimal calculate(Invoice invoice) {
        return invoice.amount().multiply(java.math.BigDecimal.valueOf(1.2));
    }
}
```

This is a deliberate design tool, not just "the case where you forgot `public`." Keeping helper classes package-private lets you refactor their internals freely — rename methods, change constructors, delete the class entirely — without breaking any code outside the package, because the compiler guarantees no outside code could have depended on it. This is the same idea JPMS later formalizes at a coarser grain with `exports`: expose the minimum surface area needed, and hide the rest.

A common pattern: one `public` class or interface per package acting as the facade, and everything else package-private, wired together only within that package.

### `package-info.java`

`package-info.java` is a special file, one per package, that holds package-level Javadoc and package-level annotations. It contains no class body — just an optional Javadoc comment, an optional set of annotations, and the `package` statement.

```java
/**
 * Billing calculations for customer invoices, including tax and currency
 * conversion rules. All monetary values use {@link java.math.BigDecimal}
 * with {@code RoundingMode.HALF_UP} unless documented otherwise.
 *
 * @since 2.3
 */
@NonNullApi // hypothetical package-wide annotation, e.g. from Spring or a custom checker
package com.teampicnic.orders.billing;

import com.teampicnic.orders.NonNullApi;
```

Two practical uses reviewers should recognize:

- **Documentation** — Javadoc generated for the package overview page comes from here, not from any regular class's comment.
- **Package-wide annotations** — a `@NonNullApi`-style annotation (like Spring's `org.springframework.lang.NonNullApi`) applied here means "every parameter and return type in this package is non-null unless explicitly marked `@Nullable`," saving you from annotating every single method individually.

### Split Packages

A **split package** occurs when the *same* package name is provided by more than one JAR (or module) on the classpath/module path. On the plain classpath this "just works" in a fragile way — classes get merged from whichever JAR the classloader finds first, in an order that's often undocumered and build-tool-dependent.

```
lib-a.jar
  com/example/util/StringHelper.class

lib-b.jar
  com/example/util/StringHelper.class   <- same package AND same class name, different code!
```

On the classpath, whichever JAR appears first wins silently — no error, no warning, just whichever version happened to load, which is exactly the kind of bug that's nearly impossible to diagnose from a stack trace alone.

**JPMS makes split packages a hard error.** Two named modules are not permitted to export the same package; the module system refuses to resolve at launch:

```
Error occurred during initialization of boot layer
java.lang.module.ResolutionException: Module lib.a and module lib.b export
package com.example.util to module myapp
```

This is a deliberate trade-off: JPMS trades classpath flexibility for a hard compile/launch-time guarantee that a package's contents are unambiguous. It is one of the most common real-world blockers when modularizing an existing multi-JAR project — you often discover, for the first time, that two of your dependencies quietly ship overlapping packages.

### Directory-to-Package Mapping

The Java compiler and JVM require that a class's package name **exactly mirror its directory path** relative to a source or class root.

```
src/
└── main/
    └── java/
        └── com/
            └── teampicnic/
                └── orders/
                    └── billing/
                        ├── Invoice.java          -> package com.teampicnic.orders.billing;
                        └── InvoiceService.java   -> package com.teampicnic.orders.billing;
```

```bash
# Compiling from the source root, mirroring the package structure, produces
# matching directories under the output root:
javac -d out $(find src/main/java -name "*.java")

# out/com/teampicnic/orders/billing/Invoice.class
# out/com/teampicnic/orders/billing/InvoiceService.class
```

This mapping is not a convention the compiler merely prefers — it is enforced. If `Invoice.java` inside a directory called `billing/` declared `package com.teampicnic.orders.tax;`, `javac` would fail to compile it (or, on the classpath at runtime, the class simply couldn't be found where the classloader expects it).

### Packages vs Modules

Packages and modules solve related but distinct problems, and mixing them up is a common interview stumbling point.

| | Package | Module |
|---|---|---|
| Introduced | Java 1.0 | Java 9 |
| Unit of | Namespace + package-private visibility | Deployment, strong encapsulation, and dependency declaration |
| Declared in | `package` statement at top of each `.java` file | Single `module-info.java` per module (a JAR-level concern) |
| Visibility granularity | Fine-grained (per-class, per-member) | Coarse-grained (per-package, via `exports`/`opens`) |
| Can exist without the other? | Yes — packages predate and work fully without modules | A module is *composed of* one or more packages; it can't exist without them |
| Enforces uniqueness across dependencies? | No (split packages silently merge on the classpath) | Yes (split packages are a hard error) |
| Reflection access to internals | Always allowed (subject to `SecurityManager`, now removed) | Blocked unless the package is `opens`-ed |

A module *contains* packages; it never replaces them. You still write `package com.teampicnic.orders.billing;` at the top of every file exactly as before — JPMS adds one more file, `module-info.java`, that declares which of those packages the module exposes to the outside world.

## Java Platform Module System (JPMS)

JPMS (JSR 376, delivered in Java 9 via Project Jigsaw) introduced modules: named, self-describing units that declare their dependencies and their public surface explicitly, instead of relying on an implicit, all-or-nothing classpath.

### Problems JPMS Solves

| Problem | Pre-JPMS reality | JPMS fix |
|---|---|---|
| **Classpath hell** | Every JAR's classes are dumped into one flat namespace; duplicate/conflicting versions silently shadow each other; no dependency graph is declared anywhere | Modules declare explicit `requires`; the module system builds and validates a real dependency graph at launch, failing fast on missing or conflicting modules |
| **No strong encapsulation** | `public` meant public to *everyone* on the classpath, forever, even for classes meant as "internal use only" (`sun.misc.Unsafe`, `com.sun.*`) | `exports` controls which packages are visible outside the module at compile time *and* runtime; unexported packages are invisible, full stop |
| **Monolithic JDK** | The entire JDK shipped as one giant `rt.jar`, so even a "Hello World" needed the full runtime, and internal JDK classes were reachable via reflection by anyone | The JDK itself is split into ~70 modules (`java.base`, `java.sql`, `java.desktop`, ...); `jlink` builds a runtime image with only the modules you actually need |
| **Reflection reaching into internals** | Any code could call `setAccessible(true)` and reach into private JDK fields/methods with no declared contract | `opens` is required for deep reflection; without it, `setAccessible` throws `InaccessibleObjectException` |

### `module-info.java` Basics

Every module has exactly one `module-info.java` at the root of its source tree (not inside any package — it sits directly under the module's source root, alongside the top-level package directories).

```java
module com.teampicnic.billing {
    requires java.sql;
    exports com.teampicnic.billing.api;
}
```

- The module name (`com.teampicnic.billing`) conventionally follows the same reverse-domain convention as packages, but it is a **separate namespace** from package names — a module is not required to contain a package of the exact same name, though it's a common and readable convention to have one "root" package matching the module name.
- Everything inside the module's packages is invisible outside the module **unless** explicitly `exports`-ed.

### `requires`, `requires transitive`, `requires static`

`requires` declares a compile-time and run-time dependency on another module.

```java
module com.teampicnic.billing {
    requires java.sql;                     // plain dependency
    requires transitive com.teampicnic.core; // re-exported to consumers of this module
    requires static com.teampicnic.testkit;  // compile-time only, optional at runtime
}
```

| Form | Meaning | Typical use |
|---|---|---|
| `requires X;` | This module needs `X` to compile and run. | The normal case for a real dependency |
| `requires transitive X;` | This module needs `X`, **and** any module that `requires com.teampicnic.billing` automatically also gets readability to `X` without declaring it itself. | Your module's public API exposes types from `X` (e.g., a method returns `java.sql.Connection`), so callers need `X` too |
| `requires static X;` | This module needs `X` only to **compile**; it is optional at runtime — if `X` is absent when running, the module still loads, but any code path that actually touches `X`'s types fails at that point. | Compile-time-only annotations (like `org.jetbrains.annotations.Nullable`) that aren't needed once compiled |

`requires transitive` is the module-system equivalent of a Maven "compile-scope, exposed" dependency: if `Invoice.getConnection()` returns a `java.sql.Connection`, then any caller needs `java.sql` on their module path too — `requires transitive java.sql;` in your module spares every consumer from having to add that `requires` themselves.

### `exports` and `exports ... to`

`exports` makes a package's `public` types accessible (at compile time and runtime) to any module that `requires` this module.

```java
module com.teampicnic.billing {
    exports com.teampicnic.billing.api;                     // visible to everyone
    exports com.teampicnic.billing.internal to com.teampicnic.billing.tests; // visible only to named module(s)
}
```

- Plain `exports pkg;` — anyone who `requires` your module can see `pkg`'s public types.
- **Qualified export** `exports pkg to moduleA, moduleB;` — only the named modules can see it. This is how you can share internal-but-not-fully-public code with, say, your own test module or a sibling module, without making it part of your general public API.

Packages that are *not* exported at all are invisible outside the module even though their classes may be `public` — this is the core of JPMS's strong encapsulation, and it is enforced by the compiler and the runtime, not just convention.

### `opens` and `opens ... to`

`opens` is about **reflection**, not compile-time linking. A package can be exported (visible, linkable, callable normally) without being opened, and vice versa.

```java
module com.teampicnic.billing {
    exports com.teampicnic.billing.api;
    opens com.teampicnic.billing.model;                 // deep reflection allowed for everyone
    opens com.teampicnic.billing.internal to com.fasterxml.jackson.databind; // reflection allowed only for Jackson
}
```

| | `exports` | `opens` |
|---|---|---|
| Grants normal compile-time + runtime access (calling public methods, `new`ing public classes) | Yes | No |
| Grants deep reflection (`setAccessible(true)` on private members) | No | Yes |
| Typical need | Library public API | JSON/serialization frameworks (Jackson, Gson), dependency injection (Spring, Hibernate) that reflectively construct and populate your classes, including private fields |

Frameworks like Hibernate and Jackson need `opens` (not just `exports`) on your model/entity packages, because they use reflection to set private fields directly. A module can also be declared `open` entirely — `open module com.teampicnic.billing { requires java.sql; exports com.teampicnic.billing.api; }` — which opens *every* package in the module to reflection at once, a pragmatic shortcut when a module is mostly framework-managed POJOs and enumerating each package individually isn't worth the ceremony.

### `uses` and `provides ... with`

These two directives implement the **`ServiceLoader`** pattern at the module level — a consumer declares it *uses* a service interface, and a provider module declares it *provides* an implementation, without either module needing to know about the other directly.

```java
// Module: com.teampicnic.billing.api  (declares the service interface)
module com.teampicnic.billing.api {
    exports com.teampicnic.billing.api;
}
```

```java
package com.teampicnic.billing.api;

public interface TaxRuleProvider {
    java.math.BigDecimal rateFor(String countryCode);
}
```

```java
// Module: com.teampicnic.billing.eu  (provides an implementation)
module com.teampicnic.billing.eu {
    requires com.teampicnic.billing.api;
    provides com.teampicnic.billing.api.TaxRuleProvider
        with com.teampicnic.billing.eu.EuTaxRuleProvider;
}
```

```java
// Module: com.teampicnic.billing.engine  (consumes the service)
module com.teampicnic.billing.engine {
    requires com.teampicnic.billing.api;
    uses com.teampicnic.billing.api.TaxRuleProvider;
}
```

```java
package com.teampicnic.billing.engine;

import com.teampicnic.billing.api.TaxRuleProvider;
import java.util.ServiceLoader;

public class TaxEngine {
    public java.math.BigDecimal calculate(String countryCode) {
        ServiceLoader<TaxRuleProvider> loader = ServiceLoader.load(TaxRuleProvider.class);
        for (TaxRuleProvider provider : loader) {
            return provider.rateFor(countryCode);
        }
        throw new IllegalStateException("No TaxRuleProvider found on the module path");
    }
}
```

`uses` and `provides` predate JPMS as an idea (`ServiceLoader` and `META-INF/services/` files existed since Java 6), but JPMS makes the wiring declarative and verifiable at compile time: the module system checks that a `provides` implementation actually implements the declared interface and that consumers legitimately declare `uses` for what they load, instead of relying on a text file in `META-INF/services/` that nothing validates.

### Automatic Modules and the Unnamed Module

Not every JAR on your module path has a `module-info.class`. JPMS handles this with two fallback mechanisms:

| | Automatic module | Unnamed module |
|---|---|---|
| What it is | A plain JAR (no `module-info.class`) placed on the **module path** | Anything placed on the plain **classpath** when running a modular application |
| Module name | Derived from the JAR filename (e.g., `guava-33.0.0.jar` -> `guava`), or from `Automatic-Module-Name` in `META-INF/MANIFEST.MF` if present | Has no name; cannot be `required` by name |
| What it exports | Everything (all packages, to everyone) — no encapsulation | Everything, to anyone else in the unnamed module |
| What it reads | Reads every other module, automatically (`requires` any named module works without listing it) | Reads all named modules and the rest of the classpath |
| Typical use | Bridging a legacy, non-modular JAR into a modular application | Legacy applications that haven't adopted JPMS at all |

Automatic modules are the "duct tape" that made JPMS adoption practical: the vast majority of the Java ecosystem's JARs in 2017 had no module descriptor, and automatic modules let a modular application depend on them immediately, with the tradeoff that automatic modules provide none of JPMS's actual encapsulation guarantees.

```bash
# guava-33.0.0.jar has no module-info.class, but placing it on the module path
# turns it into the automatic module "com.google.common" (from its
# Automatic-Module-Name manifest entry), usable via a plain "requires":
java -p libs:out -m com.teampicnic.app/com.teampicnic.app.Main
```

### Module Path vs Class Path

| | Class path (`-cp` / `-classpath`) | Module path (`-p` / `--module-path`) |
|---|---|---|
| Unit | Flat bag of `.class` files and JARs | Named modules, each with its own boundary |
| Duplicate packages | Silently merged/shadowed (split package risk) | Hard resolution error |
| Encapsulation | None — everything `public` is visible to everyone | Enforced — only `exports`-ed packages are visible |
| Declares dependencies? | No — implicit, whatever happens to be on the path | Yes — explicit `requires` graph, validated at launch |
| Can mix the two? | N/A | Yes — a "hybrid" application can use both simultaneously, with classpath JARs landing in the unnamed module |

```bash
# Classic classpath launch — flat, no module resolution at all
java -cp "libs/*:out" com.teampicnic.app.Main

# Modular launch — module path, resolved and validated against module-info.java
java -p "libs:out" -m com.teampicnic.app/com.teampicnic.app.Main
```

### Readability and Accessibility Rules

JPMS access checks layer two independent questions on top of each other:

1. **Readability** — does module A `requires` module B (directly, or transitively via `requires transitive`)? If not, A cannot see *any* type in B, no matter how public.
2. **Accessibility** — assuming A reads B, is the specific package inside B `exports`-ed (for normal use) or `opens`-ed (for reflection) to A?

```
Module A --requires--> Module B
                          |
                          +-- exports com.b.api        (A can use com.b.api's public types normally)
                          +-- (com.b.internal not exported -> invisible to A, even though B "has" it)
```

A useful mental model: readability is "can I even see this module exists," and accessibility is "given that I can see it, which of its packages am I allowed to touch, and how (normal use vs. reflection)."

```java
module com.teampicnic.app {
    requires com.teampicnic.billing; // readability: app can now see billing's exported packages
}
```

If `com.teampicnic.billing` only exports `com.teampicnic.billing.api`, then `com.teampicnic.app` compiles fine against `Invoice` (which lives in `.api`), but a reference to `com.teampicnic.billing.internal.TaxCalculator` fails to compile with `package com.teampicnic.billing.internal is not visible` — even though `app` legitimately `requires billing`.

### `jlink`, `jdeps`, and `jmod`

Three CLI tools work together around JPMS: `jdeps` for analysis, `jmod` for packaging, and `jlink` for producing a minimal custom runtime.

```bash
# jdeps: what does this JAR actually depend on, and is it already modular?
jdeps --print-module-deps app.jar          # e.g. java.base,java.sql,java.logging
jdeps --jdk-internals app.jar              # flags usage of JDK-internal (encapsulated) APIs

# jmod: package a module into the .jmod format -- mainly used for JDK modules
# and modules bundling native libraries; regular app modules ship as plain JARs.
jmod create --class-path out/com.teampicnic.billing --module-version 1.0 billing.jmod

# jlink: assemble a minimal, self-contained runtime image with only the
# modules your app needs -- no full JDK required on the deployment target.
jlink --module-path out:$JAVA_HOME/jmods \
      --add-modules com.teampicnic.app \
      --output custom-runtime \
      --strip-debug --no-header-files --no-man-pages --compress=2

./custom-runtime/bin/java -m com.teampicnic.app/com.teampicnic.app.Main
```

`jlink`'s output is a real win for container images: instead of a full ~300MB JDK base image, you ship only the ~40-60MB of modules your app actually touches, shrinking both image size and attack surface.

### Strong Encapsulation and `--add-exports`/`--add-opens`

Since Java 16 (JEP 396), strong encapsulation of JDK internals is **on by default** — code can no longer reflectively reach into packages like `jdk.internal.misc` or `sun.nio.ch` unless the module system is explicitly told to allow it. Libraries that relied on these internals (older versions of Lombok, Mockito, various serialization frameworks) had to add explicit flags or be updated.

```bash
# --add-exports: grants normal (non-reflective) access to an otherwise-unexported
# package, from a specific module to a specific module.
java --add-exports java.base/jdk.internal.misc=com.teampicnic.app -p out -m com.teampicnic.app/com.teampicnic.app.Main

# --add-opens: grants deep reflection access to an otherwise-closed package.
java --add-opens java.base/java.lang=ALL-UNNAMED -p out -m com.teampicnic.app/com.teampicnic.app.Main
```

`ALL-UNNAMED` means "grant this to the unnamed module," i.e., to whatever is on the plain classpath — this is the flag combination you'll most often see in the wild when a classpath-based library (Mockito, older Jackson, testing frameworks) needs deep reflection into `java.lang` or `java.util` internals to do bytecode manipulation or field injection.

These are explicitly called **escape hatches**: they exist so real-world code that isn't ready for strong encapsulation can keep running, not as a long-term architectural pattern. Starting in newer JDKs, using them (or performing "restricted" native/reflective operations without them) triggers warnings, and the trend is toward requiring them more strictly with each release — treat every `--add-opens` in a build file as a line item to eventually remove, not a permanent fixture.

### Worked Multi-Module Example

Two modules: a library module that exposes a `Greeter`, and an application module that consumes it.

```
project/
├── com.teampicnic.greetings/
│   ├── module-info.java
│   └── com/
│       └── teampicnic/
│           └── greetings/
│               └── Greeter.java
└── com.teampicnic.app/
    ├── module-info.java
    └── com/
        └── teampicnic/
            └── app/
                └── Main.java
```

```java
// project/com.teampicnic.greetings/module-info.java
module com.teampicnic.greetings {
    exports com.teampicnic.greetings;
}
```

```java
// project/com.teampicnic.greetings/com/teampicnic/greetings/Greeter.java
package com.teampicnic.greetings;

public class Greeter {
    public String greet(String name) {
        return "Hello, " + name + "!";
    }
}
```

```java
// project/com.teampicnic.app/module-info.java
module com.teampicnic.app {
    requires com.teampicnic.greetings;
}
```

```java
// project/com.teampicnic.app/com/teampicnic/app/Main.java
package com.teampicnic.app;

import com.teampicnic.greetings.Greeter;

public class Main {
    public static void main(String[] args) {
        System.out.println(new Greeter().greet("code review"));
    }
}
```

Compile both modules using `--module-source-path`, which lets `javac` resolve inter-module dependencies from source in one pass:

```bash
mkdir -p out

javac -d out \
      --module-source-path project \
      $(find project -name "*.java")

# out/com.teampicnic.greetings/com/teampicnic/greetings/Greeter.class
# out/com.teampicnic.app/com/teampicnic/app/Main.class
```

Run it directly off the module path:

```bash
java -p out -m com.teampicnic.app/com.teampicnic.app.Main
# Hello, code review!
```

Build a minimal custom runtime with `jlink`:

```bash
jlink --module-path out:$JAVA_HOME/jmods \
      --add-modules com.teampicnic.app \
      --launcher greet=com.teampicnic.app/com.teampicnic.app.Main \
      --output custom-runtime

./custom-runtime/bin/greet
# Hello, code review!
```

The `--launcher` flag is a nice `jlink` feature reviewers sometimes miss: it generates a native-feeling launcher script (`custom-runtime/bin/greet`) so end users never need to know or type the module/main-class combination.

### Migration Strategy

Two broad strategies exist for turning an existing multi-JAR, classpath-based application into a set of real JPMS modules.

| Strategy | Approach | Pros | Cons |
|---|---|---|---|
| **Bottom-up** | Modularize the lowest-level, most-depended-upon libraries first (utility/core JARs), then work up toward the application | Each step is small and independently verifiable; leaf libraries have the fewest dependencies to reconcile | Slow — the application itself stays non-modular for a long time; benefits (strong encapsulation for the app) arrive last |
| **Top-down** | Add a `module-info.java` to the application first, treating every dependency as an automatic module, then gradually convert dependencies to real modules one at a time | Gets the app onto the module path (and gets `jlink` working) quickly | Automatic modules provide none of JPMS's actual safety, so early gains are mostly tooling-level (custom runtimes), not encapsulation |

In practice, most real migrations are a hybrid: start top-down to get the application itself running on the module path (with `jdeps` used heavily to find hidden internal-API dependencies first), then chip away bottom-up at whichever dependencies are most painful — usually anything relying on `sun.misc.Unsafe`, reflection into `java.lang`, or shading/relocation tricks that conflict with module boundaries.

```bash
# jdeps is the standard first step of any migration: find out what a JAR
# actually needs before writing a single module-info.java.
jdeps --generate-module-info out-modules/ legacy-app.jar
```

`jdeps --generate-module-info` will even scaffold a best-effort `module-info.java` for you based on observed bytecode dependencies, which is a good starting draft — but always needs manual review, since it can't infer intent (e.g., which packages are truly meant to be public API versus which merely happen to be used across a package boundary today).

### Why Many Projects Still Don't Use JPMS

Despite being available since 2017, most application codebases (as opposed to the JDK itself, and a handful of major libraries) never adopt module descriptors, and that is a reasonable, defensible choice:

- **Split packages in the existing dependency tree** are extremely common (two transitive dependencies exporting the same package) and can require upgrading, forking, or shading dependencies just to get past module resolution errors — for zero new feature value.
- **Spring, Hibernate, and most application frameworks** still work perfectly well on the plain classpath, and JPMS's encapsulation benefits matter far less inside a single deployable application than for library authors shipping to unrelated consumers.
- **Build tool support** has historically been fiddly, with friction around test compilation, `--add-opens` requirements for mocking frameworks, and IDE tooling lag.
- **The main payoff for most teams is `jlink`** (smaller container images), which is achievable alongside simpler tools (slim base images, ArchUnit-style architecture tests) without ever writing a `module-info.java`.

A defensible interview answer: JPMS is essential for JDK maintainers and library authors guaranteeing encapsulation across organizational boundaries; for a typical internal application, the same goals are often reachable more cheaply through build-tool module boundaries and architecture linting — so skipping it is not automatically a red flag.

## Foreign Function & Memory API

The Foreign Function & Memory API (FFM API, `java.lang.foreign` package) was finalized in **JDK 22 via JEP 454**, after multiple rounds of incubation and preview (starting as the Foreign-Memory Access API in JDK 14). It lets Java code allocate and manipulate off-heap memory and call native (C) functions **without writing any native code, without JNI headers, and without `Unsafe`**.

### From JNI to FFM: Why It Changed

JNI (Java Native Interface, since Java 1.1) required writing actual C glue code, compiling it into a platform-specific shared library, and loading it with `System.loadLibrary`. `Unsafe` and `ByteBuffer` (direct buffers) let you get *some* off-heap access from pure Java, but both had serious limitations.

```java
// The old ByteBuffer / Unsafe world: off-heap memory, but no safe deallocation
// story, no structured layout description, and no way to call arbitrary
// native functions without JNI.
java.nio.ByteBuffer direct = java.nio.ByteBuffer.allocateDirect(1024);
direct.putInt(0, 42);
int value = direct.getInt(0);
// No explicit "free" -- direct buffers are reclaimed only by GC + a Cleaner,
// with no deterministic timing and no way to call a C function using this memory.
```

FFM replaces this with a small set of pure-Java classes: `Arena` for lifecycle-managed allocation, `MemorySegment` for typed, bounds-checked pointers, `MemoryLayout`/`ValueLayout` for describing native data shapes, and `Linker` for calling and being called by native functions — all without JNI's C compilation step.

### `Arena` and Deterministic Deallocation

An `Arena` controls the **lifetime** of the off-heap memory it allocates. All memory allocated through an arena becomes invalid the moment the arena is closed — accessing it afterward throws `IllegalStateException` rather than corrupting memory or segfaulting, which is the safety guarantee JNI/`Unsafe` never gave you.

| Arena kind | Created with | Lifetime | Thread confinement |
|---|---|---|---|
| Confined | `Arena.ofConfined()` | Explicit `close()` (use try-with-resources) | Only the owning thread may access or close it |
| Shared | `Arena.ofShared()` | Explicit `close()` | Any thread may access it; any thread may close it |
| Auto | `Arena.ofAuto()` | Garbage-collected — no `close()` at all | Any thread |
| Global | `Arena.global()` | Never freed — lives for the whole JVM process | Any thread |

```java
import java.lang.foreign.Arena;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.ValueLayout;

public class ArenaDemo {
    public static void main(String[] args) {
        try (Arena arena = Arena.ofConfined()) { // single-threaded, closed like a file handle
            MemorySegment segment = arena.allocate(ValueLayout.JAVA_INT.byteSize() * 4);
            for (int i = 0; i < 4; i++) {
                segment.setAtIndex(ValueLayout.JAVA_INT, i, i * i);
            }
            for (int i = 0; i < 4; i++) {
                System.out.println(segment.getAtIndex(ValueLayout.JAVA_INT, i));
            }
        } // memory is deterministically freed right here

        // Using 'segment' past this point throws IllegalStateException instead of
        // corrupting memory or segfaulting -- the core safety win over Unsafe.
    }
}
```

Try-with-resources is the idiomatic way to use a confined arena, mirroring exactly how you'd manage a `FileInputStream` — the "close what you open" instinct from Chapter 6 transfers directly.

### `MemorySegment`, `MemoryLayout`, `ValueLayout`

A `MemorySegment` is a typed, bounds-checked view over a contiguous region of memory — either off-heap (native) or backed by an on-heap Java array.

```java
import java.lang.foreign.MemorySegment;
import java.lang.foreign.ValueLayout;

public class SegmentDemo {
    public static void main(String[] args) {
        // Wrap an existing Java array as a MemorySegment (heap segment, no allocation)
        int[] javaArray = {10, 20, 30};
        MemorySegment heapSegment = MemorySegment.ofArray(javaArray);
        System.out.println(heapSegment.getAtIndex(ValueLayout.JAVA_INT, 1)); // 20

        // Out-of-bounds access throws IndexOutOfBoundsException, not a crash
        try {
            heapSegment.getAtIndex(ValueLayout.JAVA_INT, 10);
        } catch (IndexOutOfBoundsException e) {
            System.out.println("Bounds-checked: " + e.getMessage());
        }
    }
}
```

`MemoryLayout` describes the *shape* of memory (its size, alignment, and — for structs — named fields), independent of any specific segment. `ValueLayout` is the family of primitive layouts (`JAVA_INT`, `JAVA_LONG`, `JAVA_DOUBLE`, `ADDRESS`, ...) used to describe how raw bytes should be interpreted.

```java
import java.lang.foreign.ValueLayout;

// ValueLayout constants describe primitive C types in platform-independent Java code:
ValueLayout.OfInt    javaInt    = ValueLayout.JAVA_INT;    // 4-byte int
ValueLayout.OfLong   javaLong   = ValueLayout.JAVA_LONG;   // 8-byte long
ValueLayout.OfDouble javaDouble = ValueLayout.JAVA_DOUBLE; // 8-byte double
AddressLayout        pointer    = ValueLayout.ADDRESS;     // native pointer (4 or 8 bytes)
```

### `VarHandle` Access into Segments

A `VarHandle` obtained from a layout provides typed, potentially volatile/atomic access into a `MemorySegment` at a computed offset — the FFM equivalent of a field accessor, but for off-heap memory.

```java
import java.lang.foreign.Arena;
import java.lang.foreign.MemoryLayout;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.ValueLayout;
import java.lang.invoke.VarHandle;

public class VarHandleDemo {
    public static void main(String[] args) {
        MemoryLayout intArrayLayout = MemoryLayout.sequenceLayout(4, ValueLayout.JAVA_INT);
        VarHandle elementHandle = intArrayLayout.varHandle(
            MemoryLayout.PathElement.sequenceElement());

        try (Arena arena = Arena.ofConfined()) {
            MemorySegment segment = arena.allocate(intArrayLayout);
            for (int i = 0; i < 4; i++) {
                elementHandle.set(segment, 0L, (long) i, i * 10); // (segment, base offset, index, value)
            }
            for (int i = 0; i < 4; i++) {
                System.out.println(elementHandle.get(segment, 0L, (long) i));
            }
        }
    }
}
```

`VarHandle` access is how the FFM API lets you do fine-grained, layout-aware reads/writes (including atomic operations like `compareAndSet` on off-heap memory) without ever writing native code — the layout describes the shape once, and the handle gives you type-safe, bounds-checked access forever after.

### Calling a C Function: `Linker`, `SymbolLookup`, `FunctionDescriptor`

Three pieces cooperate to call a native function: `SymbolLookup` finds the function's address, `FunctionDescriptor` describes its signature, and `Linker` produces a `MethodHandle` (a **downcall handle**) that invokes it.

```java
import java.lang.foreign.Arena;
import java.lang.foreign.FunctionDescriptor;
import java.lang.foreign.Linker;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.SymbolLookup;
import java.lang.foreign.ValueLayout;
import java.lang.invoke.MethodHandle;

public class StrlenDemo {
    public static void main(String[] args) throws Throwable {
        Linker linker = Linker.nativeLinker();
        SymbolLookup stdlib = linker.defaultLookup(); // the process's standard C library

        // strlen(const char *s) -> size_t   (size_t maps to a native long on 64-bit platforms)
        MethodHandle strlen = linker.downcallHandle(
            stdlib.find("strlen").orElseThrow(() -> new NoSuchElementException("strlen not found")),
            FunctionDescriptor.of(ValueLayout.JAVA_LONG, ValueLayout.ADDRESS)
        );

        try (Arena arena = Arena.ofConfined()) {
            MemorySegment cString = arena.allocateUtf8String("Hello, native world!");
            long length = (long) strlen.invoke(cString);
            System.out.println("Length: " + length); // 20
        }
    }
}
```

A no-argument function follows the same recipe with an empty argument list in the descriptor — `getpid()` (returns the OS process ID) needs only `FunctionDescriptor.of(ValueLayout.JAVA_INT)` and `getpid.invoke()` with no arguments. Running either of these requires the native-access flag described below, and both compile/run as ordinary Java — no `javac -h`, no C compiler, no shared library build step, which is the headline improvement over JNI.

### Upcalls: Java Callback into C

An **upcall** is the reverse direction: native code calling back into Java, such as passing a Java method as a C comparator function to `qsort`.

```java
import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.MethodType;
import java.util.Arrays;

public class UpcallDemo {
    // This Java method will be exposed to native code as a C function pointer.
    static int compareInts(MemorySegment a, MemorySegment b) {
        int x = a.get(ValueLayout.JAVA_INT, 0);
        int y = b.get(ValueLayout.JAVA_INT, 0);
        return Integer.compare(x, y);
    }

    public static void main(String[] args) throws Throwable {
        Linker linker = Linker.nativeLinker();
        MethodHandle comparatorHandle = MethodHandles.lookup().findStatic(
            UpcallDemo.class, "compareInts",
            MethodType.methodType(int.class, MemorySegment.class, MemorySegment.class));

        try (Arena arena = Arena.ofConfined()) {
            // Wrap the Java method as a native function pointer ("upcall stub").
            MemorySegment comparatorStub = linker.upcallStub(
                comparatorHandle,
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.ADDRESS, ValueLayout.ADDRESS),
                arena);

            MethodHandle qsort = linker.downcallHandle(
                linker.defaultLookup().find("qsort").orElseThrow(),
                FunctionDescriptor.ofVoid(ValueLayout.ADDRESS, ValueLayout.JAVA_LONG,
                                          ValueLayout.JAVA_LONG, ValueLayout.ADDRESS));

            MemorySegment array = arena.allocateArray(ValueLayout.JAVA_INT, 5, 3, 1, 4, 2);
            qsort.invoke(array, 5L, ValueLayout.JAVA_INT.byteSize(), comparatorStub);

            int[] sorted = array.toArray(ValueLayout.JAVA_INT);
            System.out.println(Arrays.toString(sorted)); // [1, 2, 3, 4, 5]
        }
    }
}
```

The upcall stub (`comparatorStub`) is itself a `MemorySegment` — a real native function pointer that C code (`qsort`, in this case) can call directly, entirely unaware it's actually calling back into the JVM.

### Struct Layouts

`MemoryLayout.structLayout(...)` describes a C `struct` shape, with named fields accessed via `VarHandle`s derived from path elements — no manual offset arithmetic required.

```java
import java.lang.foreign.*;
import java.lang.invoke.VarHandle;

public class StructDemo {
    public static void main(String[] args) {
        // Equivalent to: struct Point { int x; int y; };
        StructLayout pointLayout = MemoryLayout.structLayout(
            ValueLayout.JAVA_INT.withName("x"),
            ValueLayout.JAVA_INT.withName("y")
        );

        VarHandle xHandle = pointLayout.varHandle(MemoryLayout.PathElement.groupElement("x"));
        VarHandle yHandle = pointLayout.varHandle(MemoryLayout.PathElement.groupElement("y"));

        try (Arena arena = Arena.ofConfined()) {
            MemorySegment point = arena.allocate(pointLayout);
            xHandle.set(point, 0L, 10);
            yHandle.set(point, 0L, 20);

            System.out.println("x=" + xHandle.get(point, 0L) + ", y=" + yHandle.get(point, 0L));
        }
    }
}
```

`pointLayout.byteSize()` and `pointLayout.byteOffset(PathElement.groupElement("y"))` are computed automatically from the declared fields (respecting natural alignment/padding rules), which is exactly the error-prone bookkeeping JNI forced you to do by hand in C headers.

### `jextract`

Writing every `FunctionDescriptor` and struct layout by hand for a large C library (say, all of `libcurl` or `sqlite3`) would be tedious and error-prone. **`jextract`** is a separate tool (distributed alongside OpenJDK builds, not part of the JDK proper) that parses a C header file and mechanically generates the Java FFM bindings — `MethodHandle`s, layouts, and constants — for every declared function and struct.

```bash
# jextract reads a C header and generates ready-to-use Java FFM bindings,
# so you don't hand-write FunctionDescriptors for every function in sqlite3.h.
jextract --output src/generated \
         --target-package com.teampicnic.native_.sqlite \
         /usr/include/sqlite3.h
```

The generated code is ordinary Java calling the same `Linker`/`MemorySegment` machinery shown above — `jextract` is a code generator for boilerplate, not a different runtime mechanism.

### `--enable-native-access` and Restricted Methods

Native memory access and native calls are **restricted methods**: operations the JVM cannot fully verify are memory-safe, because they cross into native code or raw memory. Since JDK 22, using them prints a runtime warning unless the module (or the unnamed module, for classpath code) is explicitly granted native access; **JDK 24 tightens this further** — by default, restricted native operations throw rather than merely warn, unless access is explicitly enabled.

```bash
# JDK 22/23: without this flag, restricted FFM operations print a warning to stderr:
#   WARNING: A restricted method in java.lang.foreign.SymbolLookup has been called
# JDK 24+: without this flag, restricted operations THROW at the call site instead.
java --enable-native-access=com.teampicnic.app -p out -m com.teampicnic.app/com.teampicnic.app.Main

# For classpath (unnamed-module) code:
java --enable-native-access=ALL-UNNAMED -cp out com.teampicnic.app.Main
```

This mirrors the philosophy of `--add-opens`/`--add-exports` for JPMS internals: native access is powerful enough to corrupt memory or crash the JVM outright (an off-by-one `MemorySegment` write is still possible if you compute an out-of-bounds *but still confined-looking* offset, and an entirely wrong `FunctionDescriptor` for a real native function is undefined behavior), so the JVM requires an explicit, auditable opt-in rather than allowing it silently by default.

### FFM vs JNI Comparison Table

| | JNI | Foreign Function & Memory API |
|---|---|---|
| Requires writing C/C++ glue code | Yes (`.c`/`.cpp` files, `javah`/`javac -h` header generation) | No — pure Java |
| Requires a native compiler toolchain | Yes | No |
| Memory safety | None — raw pointers, manual bookkeeping, easy to crash the JVM | Bounds-checked `MemorySegment`s, arena-scoped lifetimes, `IllegalStateException` instead of memory corruption on misuse |
| Deallocation model | Manual, via native code (`free`) or `finalize()`-era hacks | Deterministic (`Arena.close()`/try-with-resources), GC-tied (`ofAuto`), or process-lifetime (`global`) |
| Calling native functions | Requires a pre-built, pre-loaded shared library exposing JNI-specific function signatures | Directly calls existing native libraries (`libc`, `libsqlite3`, etc.) with no JNI-specific wrapper needed |
| Callbacks into Java | Supported, but verbose and error-prone (`JNIEnv*` juggling) | `Linker.upcallStub` — a first-class, type-checked Java construct |
| Struct/layout description | Manual offset math in C headers, kept in sync by hand | `MemoryLayout`/`StructLayout`, computed automatically |
| Tooling for large libraries | Hand-written wrappers, or third-party generators | `jextract` generates bindings from C headers |
| Finalized in | Java 1.1 (1997) | JDK 22 (2024), JEP 454 |
| Governing flag | None (implicit trust) | `--enable-native-access`, restricted-method warnings/errors |

## Vector API (Incubator)

The Vector API (`jdk.incubator.vector`) provides a portable way to express **SIMD** (Single Instruction, Multiple Data) computations in pure Java, letting the JIT compile them down to CPU vector instructions (AVX, SVE, NEON, ...) instead of relying entirely on auto-vectorization heuristics.

### Why It's Still Incubating

The Vector API has been re-incubated in **every JDK release since JDK 16** — as of **JDK 25, it is on its 10th incubation round** — because its final shape depends on language and JVM features that are not yet finished, primarily **Project Valhalla**'s value types. An incubator module (JEP 11) is the JDK's formal mechanism for shipping an API that is real and usable, but explicitly *not yet a permanent commitment*: its package name, method signatures, and semantics can still change between releases without the usual deprecation cycle.

| Signal | What it tells a reviewer |
|---|---|
| Package is `jdk.incubator.vector`, not `java.util.vector` or similar | Not a supported, permanent API yet |
| Requires `--add-modules jdk.incubator.vector` explicitly | The JDK itself is telling you "opt in, at your own risk" |
| Re-incubated ~10 times across ~10 releases | The API shape is still actively changing release to release |
| No compatibility guarantee across JDK versions | Code compiled against one JDK's incubator module may not run unchanged against the next |

### `VectorSpecies`, Lanes, and Masks

A `VectorSpecies<E>` describes a concrete vector "shape": the element type and the number of **lanes** (elements processed per vector operation), which the JVM picks based on the actual CPU's hardware vector width at runtime.

```java
import jdk.incubator.vector.FloatVector;
import jdk.incubator.vector.VectorSpecies;

public class SpeciesDemo {
    public static void main(String[] args) {
        // SPECIES_PREFERRED picks the widest vector shape the current CPU supports
        // for float (e.g., 8 lanes on a 256-bit AVX2 machine, 16 on 512-bit AVX-512).
        VectorSpecies<Float> species = FloatVector.SPECIES_PREFERRED;
        System.out.println("Lanes: " + species.length());
    }
}
```

A **mask** is a per-lane boolean that selectively enables or disables lanes for an operation — essential for handling array lengths that aren't an exact multiple of the vector width, or for conditional (branchless) computation.

```java
import jdk.incubator.vector.FloatVector;
import jdk.incubator.vector.VectorMask;
import jdk.incubator.vector.VectorSpecies;

public class MaskDemo {
    static final VectorSpecies<Float> SPECIES = FloatVector.SPECIES_PREFERRED;

    static void clampNegativesToZero(float[] data) {
        int i = 0;
        int upperBound = SPECIES.loopBound(data.length);
        for (; i < upperBound; i += SPECIES.length()) {
            var v = FloatVector.fromArray(SPECIES, data, i);
            VectorMask<Float> negative = v.compare(jdk.incubator.vector.VectorOperators.LT, 0f);
            v.blend(0f, negative).intoArray(data, i); // lanes matching the mask become 0
        }
        for (; i < data.length; i++) { // scalar tail
            if (data[i] < 0f) data[i] = 0f;
        }
    }
}
```

### Enabling the Incubator Module

Incubator modules must be explicitly requested at both compile time and run time; they are never on by default.

```bash
javac --add-modules jdk.incubator.vector --release 21 VectorDotProduct.java
java  --add-modules jdk.incubator.vector VectorDotProduct
```

Omitting `--add-modules jdk.incubator.vector` produces a compile error (`package jdk.incubator.vector is not visible`) — this is the same JPMS accessibility machinery from earlier in the chapter, deliberately used here to make incubating APIs opt-in and impossible to depend on by accident.

### Worked Example: Vectorized Dot Product vs Scalar

```java
// Scalar baseline: processes one float pair per loop iteration.
public class DotProductScalar {
    static float dotProduct(float[] a, float[] b) {
        float sum = 0f;
        for (int i = 0; i < a.length; i++) {
            sum += a[i] * b[i];
        }
        return sum;
    }
}
```

```java
// Vectorized version: processes SPECIES.length() float pairs per loop iteration,
// falling back to a scalar tail loop for the remainder.
import jdk.incubator.vector.FloatVector;
import jdk.incubator.vector.VectorSpecies;
import jdk.incubator.vector.VectorOperators;

public class DotProductVector {
    static final VectorSpecies<Float> SPECIES = FloatVector.SPECIES_PREFERRED;

    static float dotProduct(float[] a, float[] b) {
        float sum = 0f;
        int i = 0;
        int upperBound = SPECIES.loopBound(a.length); // largest multiple of the lane count <= a.length

        for (; i < upperBound; i += SPECIES.length()) {
            FloatVector va = FloatVector.fromArray(SPECIES, a, i);
            FloatVector vb = FloatVector.fromArray(SPECIES, b, i);
            sum += va.mul(vb).reduceLanes(VectorOperators.ADD);
        }

        for (; i < a.length; i++) { // tail: leftover elements that don't fill a full vector
            sum += a[i] * b[i];
        }
        return sum;
    }
}
```

The vectorized version does not change the *result* — both compute the same mathematical dot product — it changes how many multiplications the CPU performs per instruction. On a machine with 256-bit AVX2 and `float` (4 bytes), `SPECIES.length()` is typically 8, so each loop iteration does the work of 8 scalar iterations in roughly the same number of cycles.

### The Loop-Plus-Tail Idiom

Almost every Vector API loop follows the same two-phase shape: a **vectorized main loop** that processes full lanes, followed by a **scalar tail loop** that handles whatever's left over when the array length isn't an exact multiple of the vector width.

```java
static void scaleInPlace(float[] data, float factor) {
    VectorSpecies<Float> species = FloatVector.SPECIES_PREFERRED;
    int i = 0;
    int upperBound = species.loopBound(data.length);

    // Main loop: full vector width each iteration
    for (; i < upperBound; i += species.length()) {
        FloatVector v = FloatVector.fromArray(species, data, i);
        v.mul(factor).intoArray(data, i);
    }

    // Tail loop: whatever's left (0 to species.length() - 1 elements)
    for (; i < data.length; i++) {
        data[i] *= factor;
    }
}
```

An alternative to a separate tail loop is a **masked final iteration**, using `SPECIES.indexInRange(i, data.length)` to build a mask that disables out-of-bounds lanes so the whole array can be processed with vector operations alone, at the cost of a mask-compare on every iteration (including the ones that don't need it):

```java
for (int i = 0; i < data.length; i += species.length()) {
    var mask = species.indexInRange(i, data.length);
    FloatVector v = FloatVector.fromArray(species, data, i, mask);
    v.mul(factor).intoArray(data, i, mask);
}
```

Both patterns are idiomatic; the explicit tail loop is generally easier to read and slightly faster for the common (non-final) iterations, while the masked version is more compact and avoids duplicating the operation logic.

### Dependence on Project Valhalla

The Vector API's classes (`FloatVector`, `IntVector`, etc.) are, today, ordinary heap-allocated objects — every vector operation risks JIT-dependent allocation and indirection overhead that a hand-written intrinsic wouldn't have. **Project Valhalla**'s value types (formerly "inline classes") are designed to let the JVM represent a `FloatVector` as a flat, register-resident sequence of primitives with no object header and no heap allocation at all, the same way a CPU's SIMD register actually works. The Vector API's final, non-incubating form is expected to be built on top of Valhalla value types once they ship — which is precisely why the API keeps changing shape release over release: its designers are deliberately keeping the door open for a completely different underlying representation.

### Production Readiness Caveat

Given the above, the Vector API should be treated, in a code review, the same way you'd treat any other incubator or preview feature:

- It requires `--add-modules jdk.incubator.vector` on every build and run command, forever, until it graduates — a permanent build-configuration burden.
- Its behavior and API surface are not covered by Java's usual strict backward-compatibility guarantees; upgrading the JDK can require source changes.
- Performance-critical vectorization needs (image processing, ML inference, scientific computing) are, today, more commonly delivered via a well-tested native library called through the FFM API described earlier in this chapter, or via a JIT that already does reasonably good auto-vectorization for simple loops without any special API at all.
- **Recommendation for production code**: avoid the Vector API in shipped production systems until it exits incubation; it is excellent to know for interviews and to experiment with, but a reviewer should flag a `jdk.incubator.vector` import in application code as a real risk, not a style nit.

## Common Code-Review Interview Pitfalls

1. **Using a wildcard import (`import com.example.*;`) in reviewed code.**
   Why it matters: it hides exactly which types are in use, risks silent breakage if the package later adds a colliding name, and is rejected by nearly every style guide.
   ```java
   // Before
   import com.teampicnic.orders.*;
   // After
   import com.teampicnic.orders.Order;
   import com.teampicnic.orders.OrderStatus;
   ```

2. **Leaving a scratch class in the default (unnamed) package and shipping it.**
   Why it matters: it cannot be imported by named-package code, cannot participate in JPMS at all, and invites name collisions as the codebase grows.
   ```java
   // Before
   public class Utils { /* no package statement */ }
   // After
   package com.teampicnic.common;
   public class Utils { }
   ```

3. **Confusing `exports` with `opens` when a framework needs reflection.**
   Why it matters: Jackson/Hibernate/Spring need deep reflective field access; `exports` alone grants normal compile-time visibility but still throws `InaccessibleObjectException` on `setAccessible(true)`.
   ```java
   // Before (Jackson fails to deserialize private fields at runtime)
   module com.teampicnic.billing { exports com.teampicnic.billing.model; }
   // After
   module com.teampicnic.billing { opens com.teampicnic.billing.model; }
   ```

4. **Forgetting `requires transitive` when a public API returns a type from another module.**
   Why it matters: without it, every consumer of your module must separately `requires` the dependency your API leaks, breaking encapsulation and causing confusing "package not visible" errors far from the real cause.
   ```java
   // Before: billing.api returns java.sql.Connection but doesn't re-export java.sql
   module com.teampicnic.billing { requires java.sql; }
   // After
   module com.teampicnic.billing { requires transitive java.sql; }
   ```

5. **Adding `--add-opens`/`--add-exports` flags to "make the error go away" without understanding why they were needed.**
   Why it matters: these are escape hatches around intentional strong encapsulation; scattering them across build scripts accumulates hidden, unreviewed access to JDK internals that can break on the next JDK upgrade.
   ```bash
   # Before: blanket, undocumented flag added to silence a warning
   java --add-opens java.base/java.lang=ALL-UNNAMED -jar app.jar
   # After: scoped to the specific module that actually needs it, with a
   # comment explaining which dependency requires it and a plan to remove it
   java --add-opens java.base/java.lang=com.teampicnic.legacybridge -jar app.jar
   ```

6. **Assuming every project must adopt JPMS "because it's modern."**
   Why it matters: for a typical single-deployable application (as opposed to a widely distributed library), the migration cost (split packages, `--add-opens` for test/mocking frameworks) often outweighs the benefit; flagging the *absence* of `module-info.java` as a defect, on its own, is not a strong review comment.
   ```
   Review comment to avoid: "This project has no module-info.java, please modularize it."
   Better: ask whether the team's actual pain point (image size? internal
   encapsulation?) is better solved by jlink + a slimmer base image, or by
   architecture-linting tools, before demanding a full JPMS migration.
   ```

7. **Calling native functions via the FFM API without wrapping allocation in try-with-resources.**
   Why it matters: memory allocated by an `Arena.ofConfined()`/`ofShared()` is not freed until `close()` runs; forgetting it leaks native memory exactly like forgetting to close a file handle, except it won't show up in heap dumps.
   ```java
   // Before
   Arena arena = Arena.ofConfined();
   MemorySegment s = arena.allocate(1024);
   // ... arena never closed, memory leaks for the arena's whole ("forever") lifetime
   // After
   try (Arena arena = Arena.ofConfined()) {
       MemorySegment s = arena.allocate(1024);
   }
   ```

8. **Passing a `FunctionDescriptor` that doesn't match the real native function signature.**
   Why it matters: unlike a normal Java type mismatch, this is not caught by the compiler or even reliably at runtime — it produces undefined behavior (corrupted stack, wrong results, or a JVM crash) because the FFM API trusts the descriptor you provide.
   ```java
   // Before: strlen actually returns size_t (8 bytes on 64-bit), declared as 4-byte int
   FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.ADDRESS);
   // After
   FunctionDescriptor.of(ValueLayout.JAVA_LONG, ValueLayout.ADDRESS);
   ```

9. **Running FFM downcalls/upcalls without `--enable-native-access` and treating the resulting warning (or, on JDK 24+, exception) as noise to suppress.**
   Why it matters: the warning/error exists because restricted operations can crash the JVM or corrupt memory; suppressing it without understanding the risk removes a deliberate safety signal instead of addressing it.
   ```bash
   # Before: warning ignored / logging turned down to hide it
   java -p out -m com.teampicnic.app/com.teampicnic.app.Main 2>/dev/null
   # After: explicit, scoped, and documented opt-in
   java --enable-native-access=com.teampicnic.app -p out -m com.teampicnic.app/com.teampicnic.app.Main
   ```

10. **Shipping `jdk.incubator.vector` code in a production code path.**
    Why it matters: incubator modules have no backward-compatibility guarantee and can change or disappear between JDK releases; a production dependency on one means every JDK upgrade is a potential source-breaking event.
    ```java
    // Before: production hot path depends on the Vector API directly
    import jdk.incubator.vector.FloatVector;
    // After: keep vectorization behind a well-tested native library called
    // via the finalized FFM API, or rely on JIT auto-vectorization, until
    // the Vector API graduates out of incubation
    ```

11. **Writing a Vector API loop with no scalar tail (or mask) for leftover elements.**
    Why it matters: if the array length isn't an exact multiple of `SPECIES.length()`, a naive vectorized loop either throws an out-of-bounds exception or silently skips the trailing elements, producing wrong results.
    ```java
    // Before: silently drops elements past the last full vector
    for (int i = 0; i < a.length; i += SPECIES.length()) {
        FloatVector.fromArray(SPECIES, a, i).mul(2f).intoArray(a, i);
    }
    // After
    int upperBound = SPECIES.loopBound(a.length);
    int i = 0;
    for (; i < upperBound; i += SPECIES.length()) { /* vectorized */ }
    for (; i < a.length; i++) { a[i] *= 2f; } // tail
    ```

12. **Believing `opens`/`exports` provide security against a determined attacker.**
    Why it matters: JPMS encapsulation is a compile-time and linkage-time boundary for well-behaved code, not a security sandbox — `--add-opens`, native access, or an already-loaded reflective handle can bypass it, and it must never be the sole line of defense for genuinely sensitive internals.
    ```
    Review comment: "We don't export the credentials package, so it's secure."
    Correction: JPMS encapsulation prevents accidental coupling between
    modules, not deliberate bypass; sensitive data still needs its own
    access controls (encryption at rest, proper secret management), independent
    of module boundaries.
    ```

13. **Introducing a split package while adding a new dependency to a modularized project.**
    Why it matters: two modules exporting the same package name fail module resolution at launch with a hard-to-diagnose `ResolutionException`, often only discovered in CI or production, not at compile time on the developer's machine if only one of the two JARs was present locally.
    ```
    Error: Module A and module B export package com.example.util to module C
    Fix: shade/relocate one of the offending JARs, upgrade to a version that
    renamed the package, or exclude the duplicate dependency.
    ```

14. **Treating an automatic module (a plain JAR on the module path) as if it had real JPMS encapsulation.**
    Why it matters: automatic modules export every package to everyone and read every other module, so `requires` on one gives no actual safety guarantee — code review should not assume "it's on the module path" implies "its internals are protected."
    ```
    Review comment to avoid: "guava is modularized now, so its internals are encapsulated."
    Correction: guava (via Automatic-Module-Name) is an automatic module —
    all of its packages remain fully open; only a real module-info.class
    with explicit exports would encapsulate anything.
    ```

15. **Reviewing `package-private` classes as if they were dead code because "nothing outside the package uses them."**
    Why it matters: package-private visibility is a deliberate design boundary, not an accident — flagging an unused-outside-package helper class as "should be deleted" without checking whether it's the intended internal implementation of a package's public facade produces a wrong, noisy review comment.
    ```java
    // Correct read: TaxCalculator below is intentionally hidden; it's InvoiceService's
    // private implementation detail, not "orphaned" code, even though no import
    // statement anywhere references it directly.
    class TaxCalculator { /* used only by InvoiceService in the same package */ }
    ```
