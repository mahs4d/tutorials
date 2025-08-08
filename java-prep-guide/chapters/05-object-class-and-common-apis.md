# 5. Object Class & Common APIs

Every class in Java, whether you write it or not, is a child of `java.lang.Object`. That means every object you create already has a few methods built in: `equals()`, `hashCode()`, `toString()`, `clone()`, and others. In code review, bugs in these "invisible" methods are some of the most common findings — a broken `equals()` can silently corrupt a `HashMap`, and a missing defensive copy can let external code mutate your "immutable" class. This chapter walks through these APIs in depth, plus the related tools (`Optional`, `Comparator`, immutability patterns) that reviewers check every day.

## Table of Contents

- [equals(), hashCode(), and toString()](#equals-hashcode-and-tostring)
- [Object Cloning](#object-cloning)
- [Optional](#optional)
- [Comparator and Comparable](#comparator-and-comparable)
- [Immutability](#immutability)
- [Defensive Copying](#defensive-copying)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## equals(), hashCode(), and toString()

`Object` gives every class three methods for free:

- `equals(Object other)` — decides if two objects are "equal".
- `hashCode()` — returns an `int` "fingerprint" used by hash-based collections (`HashMap`, `HashSet`).
- `toString()` — returns a human-readable `String` representation, used by logging, debuggers, and string concatenation.

The default implementations (inherited straight from `Object`) compare **identity** (are they the same object in memory?) and print the class name plus a memory-based hash, like `com.acme.User@1b6d3586`. That is almost never what you want for a data-carrying class.

```java
class User {
    private final String email;
    User(String email) { this.email = email; }
}

User a = new User("a@acme.com");
User b = new User("a@acme.com");

System.out.println(a.equals(b)); // false! Different objects, same data.
System.out.println(a);           // User@1b6d3586 — useless in logs.
```

### The equals() contract

If you override `equals()`, you must honor a strict contract. It is defined in the Javadoc of `Object.equals` and every reviewer should be able to recite it:

| Property | Meaning |
|---|---|
| Reflexive | `x.equals(x)` must be `true`. |
| Symmetric | If `x.equals(y)` is `true`, then `y.equals(x)` must also be `true`. |
| Transitive | If `x.equals(y)` and `y.equals(z)` are `true`, then `x.equals(z)` must be `true`. |
| Consistent | Calling `x.equals(y)` repeatedly returns the same result, as long as neither object changed. |
| Null comparison | `x.equals(null)` must return `false` — never throw a `NullPointerException`. |

A typical, correct `equals()`/`hashCode()` pair:

```java
import java.util.Objects;

final class Point {
    private final int x;
    private final int y;

    Point(int x, int y) {
        this.x = x;
        this.y = y;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;                 // reflexive shortcut
        if (o == null || getClass() != o.getClass()) return false; // null-safe + type check
        Point other = (Point) o;
        return x == other.x && y == other.y;
    }

    @Override
    public int hashCode() {
        return Objects.hash(x, y);
    }

    @Override
    public String toString() {
        return "Point{x=" + x + ", y=" + y + "}";
    }
}
```

### The hashCode() contract, and why it must match equals()

The rule is simple but easy to forget: **if two objects are equal according to `equals()`, they must have the same `hashCode()`.** The reverse is not required — unequal objects are *allowed* to share a hash code (a "hash collision"), but equal objects can never disagree on their hash.

```java
// BROKEN: overrides equals() but not hashCode()
class Bad {
    private final String id;
    Bad(String id) { this.id = id; }

    @Override
    public boolean equals(Object o) {
        return o instanceof Bad b && b.id.equals(id);
    }
    // hashCode() NOT overridden — still identity-based!
}

Set<Bad> set = new HashSet<>();
set.add(new Bad("x"));
System.out.println(set.contains(new Bad("x"))); // false! Different hash buckets.
```

`HashSet` and `HashMap` first look at `hashCode()` to find the right "bucket," then use `equals()` inside that bucket. If `hashCode()` is inconsistent with `equals()`, two "equal" objects can land in different buckets and the collection will never find one using the other. This is one of the most common code-review findings: **"you overrode equals() but not hashCode()."** Modern IDEs and static analyzers (and the compiler with certain lint flags) will warn about this.

### Mutable fields as HashMap keys — a classic bug

Never use a mutable object as a `HashMap` key (or `HashSet` element) if its `hashCode()` depends on fields that can change after insertion.

```java
class MutableKey {
    private int value;
    MutableKey(int value) { this.value = value; }
    void setValue(int value) { this.value = value; }

    @Override
    public boolean equals(Object o) {
        return o instanceof MutableKey k && k.value == value;
    }
    @Override
    public int hashCode() {
        return Integer.hashCode(value);
    }
}

Map<MutableKey, String> map = new HashMap<>();
MutableKey key = new MutableKey(1);
map.put(key, "hello");

key.setValue(2);                 // mutate the key AFTER insertion
System.out.println(map.get(key)); // null! It's stored in the bucket for hashCode(1),
                                   // but now key.hashCode() == 2.
```

**Rule of thumb:** keys used in hash-based collections should be immutable, or at least never mutated while stored as a key. This is why `String`, `Integer`, records, and enums are popular map keys — they are immutable.

### getClass() vs instanceof — the symmetry trap with inheritance

There are two common ways to type-check inside `equals()`, and they behave differently with subclassing:

| Approach | Behavior | Risk |
|---|---|---|
| `getClass() != o.getClass()` | Only equal to objects of the *exact same* class. | Safe from symmetry bugs, but two logically-equal instances of different subclasses are never equal. |
| `o instanceof MyClass` | Equal to any subclass instance too. | Can break **symmetry** if a subclass adds fields and also overrides `equals()`. |

```java
class Point {
    int x, y;
    Point(int x, int y) { this.x = x; this.y = y; }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof Point p)) return false; // instanceof-based
        return x == p.x && y == p.y;
    }
}

class ColorPoint extends Point {
    String color;
    ColorPoint(int x, int y, String color) {
        super(x, y);
        this.color = color;
    }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof ColorPoint cp)) return false;
        return super.equals(cp) && color.equals(cp.color);
    }
}

Point p = new Point(1, 2);
ColorPoint cp = new ColorPoint(1, 2, "red");

System.out.println(p.equals(cp));  // true  -> cp IS a Point with matching x,y
System.out.println(cp.equals(p));  // false -> p is not a ColorPoint
// SYMMETRY BROKEN: p.equals(cp) != cp.equals(p)
```

Effective Java's advice (Joshua Bloch): **favor composition over inheritance for value classes**, or use `getClass()` equality when subclassing is possible, or make the class `final`. If a class is designed to be extended, consider giving it no `equals()`/`hashCode()` at all, or documenting clearly that subclasses must not add fields relevant to equality.

```java
// SAFER: getClass() check avoids the symmetry problem
@Override
public boolean equals(Object o) {
    if (this == o) return true;
    if (o == null || getClass() != o.getClass()) return false;
    Point p = (Point) o;
    return x == p.x && y == p.y;
}
```

### Objects.equals and Objects.hash — null-safe helpers

`java.util.Objects` provides small utilities that remove a lot of boilerplate and, importantly, handle `null` correctly:

```java
import java.util.Objects;

class Person {
    private final String name;   // could be null
    private final Integer age;   // could be null

    Person(String name, Integer age) {
        this.name = name;
        this.age = age;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof Person p)) return false;
        return Objects.equals(name, p.name) && Objects.equals(age, p.age);
    }

    @Override
    public int hashCode() {
        return Objects.hash(name, age); // null-safe, handles any number of fields
    }
}
```

Without `Objects.equals`, comparing potentially-null fields with `name.equals(p.name)` risks a `NullPointerException`. `Objects.equals(a, b)` returns `true` if both are `null`, `false` if only one is `null`, and delegates to `a.equals(b)` otherwise.

### Records give you equals/hashCode/toString for free

Since Java 16 (standard), `record` types auto-generate `equals()`, `hashCode()`, and `toString()` based on all their components, and the generated code is correct by construction (using `getClass()` semantics, so it is symmetric).

```java
record Point(int x, int y) {}

Point a = new Point(1, 2);
Point b = new Point(1, 2);

System.out.println(a.equals(b)); // true
System.out.println(a);           // Point[x=1, y=2]
System.out.println(a.hashCode() == b.hashCode()); // true
```

In a code review, if you see a hand-written data class with manually written `equals()`/`hashCode()`/`toString()`/getters and no extra behavior, a very reasonable comment is: **"could this be a record?"**

### IDE-generated equals/hashCode

IntelliJ, Eclipse, and `lombok`'s `@EqualsAndHashCode` all generate correct, contract-following code — but they generate it based on a snapshot of the fields *at generation time*. Common review issue: a field is added later and the developer forgets to regenerate `equals()`/`hashCode()`, so the new field is silently ignored in comparisons.

```java
// Generated when the class had only `id`. Later, `status` was added but equals() was
// never regenerated:
class Order {
    private final String id;
    private String status; // added later, NOT included below

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof Order order)) return false;
        return Objects.equals(id, order.id); // status is silently ignored!
    }
    @Override
    public int hashCode() {
        return Objects.hash(id);
    }
}
```

Whether that is a bug depends on intent — sometimes you *want* equality by ID only (e.g., entity semantics). But it must be a deliberate choice, documented in a comment, not an oversight.

## Object Cloning

`Object` declares a `protected` method called `clone()` that is supposed to create a copy of an object. In practice, it is widely considered one of Java's worst-designed APIs, and most style guides (including Effective Java) recommend avoiding it.

### Why Cloneable is broken

To use `clone()`, a class must implement the empty marker interface `Cloneable` — but `Cloneable` does not declare a `clone()` method itself. It just flips a hidden flag that `Object.clone()` checks at runtime. If you forget `implements Cloneable`, calling `clone()` throws `CloneNotSupportedException` — a checked exception, discovered only at runtime, not compile time.

```java
class Box implements Cloneable {
    private int size;
    Box(int size) { this.size = size; }

    @Override
    public Box clone() {
        try {
            return (Box) super.clone(); // field-by-field shallow copy
        } catch (CloneNotSupportedException e) {
            throw new AssertionError(e); // "can't happen" since we implement Cloneable
        }
    }
}
```

Problems with this design:

- `Cloneable`/`clone()` bypass constructors entirely — `super.clone()` does a raw memory copy, so invariants enforced in your constructor are never re-checked.
- The contract is vague and unenforceable by the compiler (no `@Override`-style safety net for correctness).
- Subclassing is fragile: if a subclass adds fields, it must remember to also override `clone()` correctly, and `final` fields cannot be "fixed up" after `super.clone()`.
- Checked exception (`CloneNotSupportedException`) makes calling code messy even though, in a correct implementation, it never actually happens.

### Shallow copy vs deep copy

`Object.clone()` (and any naive copy) performs a **shallow copy**: primitive fields are duplicated, but reference fields (objects, arrays, collections) still point to the *same* underlying object. A **deep copy** recursively copies referenced objects too, so the two copies share no mutable state.

```java
class Team implements Cloneable {
    private String name;
    private List<String> members; // reference field

    Team(String name, List<String> members) {
        this.name = name;
        this.members = members;
    }

    @Override
    public Team clone() {
        try {
            Team copy = (Team) super.clone(); // shallow: `members` list is SHARED
            return copy;
        } catch (CloneNotSupportedException e) {
            throw new AssertionError(e);
        }
    }
}

List<String> members = new ArrayList<>(List.of("Alice", "Bob"));
Team original = new Team("Red", members);
Team shallow = original.clone();

shallow.members.add("Eve"); // mutates the list shared by BOTH teams!
System.out.println(original.members); // [Alice, Bob, Eve] — surprise mutation
```

A correct deep copy must clone (or defensively copy) every mutable reference field:

```java
@Override
public Team clone() {
    try {
        Team copy = (Team) super.clone();
        copy.members = new ArrayList<>(this.members); // deep-ish copy of the list
        return copy;
    } catch (CloneNotSupportedException e) {
        throw new AssertionError(e);
    }
}
```

### The better alternative: copy constructors and static factories

Most experienced reviewers push back on `clone()` in favor of a **copy constructor** or a **static factory method**. Both are simpler, do not throw checked exceptions, run through normal constructor logic (so invariants are validated), and work fine with `final` fields.

```java
final class Team {
    private final String name;
    private final List<String> members;

    Team(String name, List<String> members) {
        this.name = name;
        this.members = new ArrayList<>(members); // defensive copy, see later section
    }

    // Copy constructor
    Team(Team other) {
        this(other.name, other.members);
    }

    // Static factory alternative
    static Team copyOf(Team other) {
        return new Team(other.name, other.members);
    }
}

Team original = new Team("Red", List.of("Alice", "Bob"));
Team copy = new Team(original); // clean, no checked exceptions, no Cloneable dance
```

| Approach | Pros | Cons |
|---|---|---|
| `Object.clone()` / `Cloneable` | Built into the language | Bypasses constructors, checked exception, fragile with subclasses and `final` fields |
| Copy constructor | Simple, runs constructor logic, works with `final` fields | Must be written per class |
| Static factory (`copyOf`) | Same benefits, can return an interface type or cache instances | Same as above |

**Interview takeaway:** if asked "how do you copy an object in Java," the strong answer is "prefer a copy constructor or static factory; avoid `Cloneable` because it's fundamentally broken."

## Optional

`java.util.Optional<T>` is a container that either holds a value or is empty. It was added in Java 8 specifically to make **absence of a value explicit in a method's return type**, instead of relying on `null` and hoping callers remember to check.

```java
import java.util.Optional;

Optional<String> findUserEmail(long userId) {
    User user = repository.findById(userId); // may not exist
    return user == null ? Optional.empty() : Optional.of(user.getEmail());
}
```

Callers are nudged (though not forced) to handle the empty case instead of risking a `NullPointerException`:

```java
Optional<String> email = findUserEmail(42);

String display = email.orElse("no email on file");
```

### map, flatMap, filter

`Optional` supports a small functional pipeline, similar to `Stream`:

```java
Optional<String> email = findUserEmail(42);

// map: transform the value if present, otherwise stay empty
Optional<Integer> domainLength = email.map(e -> e.split("@")[1].length());

// filter: keep the value only if it matches a predicate
Optional<String> corporateEmail = email.filter(e -> e.endsWith("@acme.com"));

// flatMap: like map, but the function itself returns an Optional (avoids Optional<Optional<T>>)
Optional<User> findUserByEmail(String email) { /* ... */ return Optional.empty(); }

Optional<User> user = email.flatMap(this::findUserByEmail);
```

```java
// Without flatMap, map would produce a nested Optional<Optional<User>> — awkward:
Optional<Optional<User>> nested = email.map(this::findUserByEmail); // wrong shape
```

### orElse vs orElseGet vs orElseThrow

These three "unwrap" an `Optional`, but they differ in when their argument runs — a frequent code-review gotcha:

| Method | Argument | When the argument runs |
|---|---|---|
| `orElse(T value)` | A plain value | **Always evaluated**, even if the `Optional` is present |
| `orElseGet(Supplier<T>)` | A lazy supplier | Only called if the `Optional` is empty |
| `orElseThrow(Supplier<X>)` | An exception supplier | Only called (and thrown) if the `Optional` is empty |

```java
// BROKEN-ish: orElse() always builds the fallback, even when not needed.
String email = findUserEmail(42).orElse(buildExpensiveDefault()); // buildExpensiveDefault() ALWAYS runs

// FIXED: orElseGet() only calls the supplier when the Optional is empty.
String email2 = findUserEmail(42).orElseGet(this::buildExpensiveDefault);
```

For a simple constant like `"unknown"`, `orElse` is fine and slightly clearer. The rule: **use `orElse` for cheap constants, `orElseGet` for anything expensive or side-effecting.**

```java
User user = repository.findById(42)
        .orElseThrow(() -> new NoSuchElementException("User 42 not found"));
```

`orElseThrow()` with no argument throws `NoSuchElementException`; the overload with a `Supplier<X>` lets you throw a domain-specific exception.

### ifPresent, ifPresentOrElse, and Optional in a switch/pattern context

```java
findUserEmail(42).ifPresent(email -> System.out.println("Found: " + email));

findUserEmail(42).ifPresentOrElse(
        email -> System.out.println("Found: " + email),
        () -> System.out.println("No email found")
);
```

### What Optional is NOT for

This is the part reviewers check most: `Optional` was designed for **return types**, not for fields, parameters, or collection elements.

```java
// BAD: Optional as a field — adds boxing overhead, complicates serialization,
// and Optional itself is not Serializable.
class User {
    private Optional<String> nickname; // avoid
}

// BAD: Optional as a method parameter — forces every caller to wrap values,
// and null is still possible anyway (Optional can itself be null!).
void updateNickname(Optional<String> nickname) { /* avoid */ }

// BAD: Optional inside a collection — just omit the entry, or use an empty value.
List<Optional<String>> names; // avoid
```

Better alternatives:

```java
// GOOD: plain nullable field, document with @Nullable or just a comment.
class User {
    private String nickname; // may be null
}

// GOOD: overload instead of Optional parameter.
void updateNickname(String nickname) { /* ... */ }

// GOOD: absence in a collection is just "not in the list" or an empty String.
List<String> names; // simply don't add a null/absent entry
```

**Interview one-liner:** "`Optional` is for values a method *returns* that might not exist — never for fields, parameters, or collections. It's a return-type tool, not a general null-replacement."

## Comparator and Comparable

Java has two interfaces for ordering objects, and mixing them up is a common review comment.

| Interface | Method | Where the logic lives | How many orderings? |
|---|---|---|---|
| `Comparable<T>` | `compareTo(T o)` | Inside the class itself | One "natural order" |
| `Comparator<T>` | `compare(T a, T b)` | External, standalone | As many as you want |

### The compareTo contract

`compareTo` must return a negative number, zero, or a positive number, meaning "less than," "equal to," or "greater than" respectively. Like `equals()`, it has a formal contract:

- **Sign consistency:** `x.compareTo(y)` and `y.compareTo(x)` must have opposite signs (or both be zero).
- **Transitivity:** if `x.compareTo(y) > 0` and `y.compareTo(z) > 0`, then `x.compareTo(z) > 0`.
- **Consistency with equals (strongly recommended, not strictly required):** `x.compareTo(y) == 0` should imply `x.equals(y)`. If not, document it — `TreeSet`/`TreeMap` use `compareTo` for equality, *not* `equals()`, so violating this causes confusing duplicate-removal behavior.

```java
final class Money implements Comparable<Money> {
    private final long cents;
    Money(long cents) { this.cents = cents; }

    @Override
    public int compareTo(Money other) {
        return Long.compare(this.cents, other.cents); // safe, no overflow
    }
}
```

### The integer-subtraction overflow bug

A very common — and very wrong — shortcut is subtracting two numbers to get the comparison result:

```java
// BROKEN: integer overflow can flip the sign!
class Score implements Comparable<Score> {
    private final int value;
    Score(int value) { this.value = value; }

    @Override
    public int compareTo(Score other) {
        return this.value - other.value; // overflow risk
    }
}

Score a = new Score(Integer.MAX_VALUE);   // 2147483647
Score b = new Score(-1);
System.out.println(a.compareTo(b)); // expected positive, but overflows to negative!
```

Because `Integer.MAX_VALUE - (-1)` overflows past `Integer.MAX_VALUE` and wraps around to a negative number, `compareTo` reports `a < b` when actually `a > b`. This breaks sorting and binary search silently, often only on large or negative inputs — exactly the kind of bug that slips past small tests.

```java
// FIXED: use the boxed type's static compare method — never overflows.
@Override
public int compareTo(Score other) {
    return Integer.compare(this.value, other.value);
}
```

This applies to `Integer.compare`, `Long.compare`, `Double.compare`, etc. **Rule for reviewers: any `compareTo`/`compare` that uses `a - b` on `int`/`long` fields should be flagged.**

### Comparator.comparing, thenComparing, reversed

`Comparator` has static and default methods (since Java 8) that make building complex orderings declarative instead of hand-written `if`/`else` chains:

```java
record Employee(String name, String department, int salary) {}

List<Employee> employees = new ArrayList<>(List.of(
    new Employee("Alice", "Engineering", 90000),
    new Employee("Bob",   "Engineering", 95000),
    new Employee("Carol", "Sales",       80000)
));

Comparator<Employee> byDeptThenSalaryDesc =
        Comparator.comparing(Employee::department)
                   .thenComparing(Comparator.comparing(Employee::salary).reversed());

employees.sort(byDeptThenSalaryDesc);
// Engineering: Bob (95000), Alice (90000); then Sales: Carol (80000)
```

Read it left to right: sort by department first; for ties, sort by salary, descending.

```java
// Manual equivalent — verbose and error-prone by comparison:
Comparator<Employee> manual = (e1, e2) -> {
    int deptCompare = e1.department().compareTo(e2.department());
    if (deptCompare != 0) return deptCompare;
    return Integer.compare(e2.salary(), e1.salary()); // reversed manually
};
```

### naturalOrder and nullsFirst / nullsLast

```java
List<String> names = new ArrayList<>(List.of("Bob", "alice", "Carol"));
names.sort(Comparator.naturalOrder()); // uses String's own compareTo (case-sensitive)

// Null-safe sorting: nulls can crash a naive comparator with NPE.
List<String> withNulls = new ArrayList<>(List.of("Bob", null, "Alice"));

// BROKEN: NullPointerException on the null element.
// withNulls.sort(Comparator.naturalOrder());

// FIXED: push nulls to the front (or use nullsLast to push them to the end).
withNulls.sort(Comparator.nullsFirst(Comparator.naturalOrder()));
```

### Comparable vs Comparator — when to use which

```java
// Comparable: ONE natural ordering, baked into the class.
class Invoice implements Comparable<Invoice> {
    private final LocalDate dueDate;
    @Override
    public int compareTo(Invoice other) {
        return this.dueDate.compareTo(other.dueDate); // "natural" order = due date
    }
}

// Comparator: as many EXTRA orderings as you need, defined externally.
Comparator<Invoice> byAmountDesc = Comparator.comparing(Invoice::getAmount).reversed();
Comparator<Invoice> byCustomerName = Comparator.comparing(Invoice::getCustomerName);
```

**Interview one-liner:** "`Comparable` defines a type's single natural order and lives inside the class; `Comparator` defines external, swappable orderings, and you can have as many as you like."

## Immutability

An **immutable** object is one whose observable state never changes after construction. Once built, it stays exactly as it is — no setters, no mutation, ever. Immutable objects are automatically thread-safe (no synchronization needed), easier to reason about, and safe to share and cache. `String`, `Integer`, `LocalDate`, and records (when built carefully) are all immutable or close to it.

### Recipe for a truly immutable class

1. Make the class `final` (or use only private constructors) so it cannot be subclassed to add mutable state or override behavior.
2. Make every field `private` and `final`.
3. Do not provide setters or any other mutator methods.
4. If a field is a mutable type (array, `List`, `Date`, mutable custom object), make a **defensive copy** on the way in (constructor) and on the way out (getter). Covered in detail in the next section.

```java
import java.util.List;

final class ImmutableInvoice {
    private final String id;
    private final List<String> items;
    private final int totalCents;

    ImmutableInvoice(String id, List<String> items, int totalCents) {
        this.id = id;
        this.items = List.copyOf(items); // defensive copy + unmodifiable
        this.totalCents = totalCents;
    }

    String getId() { return id; }
    int getTotalCents() { return totalCents; }
    List<String> getItems() { return items; } // safe: List.copyOf is already immutable
}
```

```java
// Usage: attempts to mutate the exposed list fail loudly, not silently.
ImmutableInvoice invoice = new ImmutableInvoice("INV-1", List.of("Widget"), 999);
invoice.getItems().add("Sneaky item"); // throws UnsupportedOperationException
```

### Records and immutability

`record` gives you `private final` fields and no setters automatically — but it does **not** automatically give you deep immutability. If a record component is a mutable type, you still need to defend it yourself:

```java
// Looks immutable, but ISN'T fully:
record Invoice(String id, List<String> items) {}

List<String> mutable = new ArrayList<>(List.of("Widget"));
Invoice invoice = new Invoice("INV-1", mutable);
mutable.add("Sneaky item"); // mutates the record's internal state from outside!
System.out.println(invoice.items()); // [Widget, Sneaky item]
```

```java
// FIXED: use a compact constructor to defensively copy.
record Invoice(String id, List<String> items) {
    Invoice(String id, List<String> items) {
        this.id = id;
        this.items = List.copyOf(items); // defends against caller mutation
    }
}
```

### Immutability and performance / thread-safety trade-offs

| Benefit | Cost |
|---|---|
| Naturally thread-safe, no locks needed | Every "change" allocates a new object |
| Safe to cache, share, use as map keys | Can be wasteful for large objects mutated frequently in a loop |
| Simpler to reason about, fewer invariant bugs | Requires discipline (defensive copies) for every mutable field |

For a value that changes frequently inside a hot loop (e.g., a running total), a mutable local variable or builder is usually fine — immutability shines for objects that are shared across threads or passed around as stable values (DTOs, config, keys, events).

## Defensive Copying

**Defensive copying** means making an independent copy of a mutable object at a trust boundary, so that neither side can affect the other through shared references. Without it, "immutable" classes can be mutated indirectly by whoever handed you the object — or whoever you handed the object to.

### The two places you need it: constructor AND getter

A very common code-review finding is defending only *one* side (usually the constructor) and forgetting the other (the getter).

```java
import java.util.ArrayList;
import java.util.Date;
import java.util.List;

// BROKEN: no defensive copies anywhere.
final class Trip {
    private final Date startDate;   // Date is mutable!
    private final List<String> stops;

    Trip(Date startDate, List<String> stops) {
        this.startDate = startDate; // caller's Date object is stored directly
        this.stops = stops;         // caller's List is stored directly
    }

    Date getStartDate() { return startDate; } // returns the SAME mutable Date
    List<String> getStops() { return stops; }  // returns the SAME mutable List
}

Date d = new Date();
List<String> stops = new ArrayList<>(List.of("Paris"));
Trip trip = new Trip(d, stops);

d.setTime(0);              // mutates the Trip's startDate from OUTSIDE
stops.add("Berlin");       // mutates the Trip's stops from OUTSIDE

trip.getStartDate().setTime(123456789L); // mutates the Trip's internal Date via the GETTER
```

```java
// FIXED: defend both directions.
final class Trip {
    private final Date startDate;
    private final List<String> stops;

    Trip(Date startDate, List<String> stops) {
        this.startDate = new Date(startDate.getTime()); // copy IN
        this.stops = new ArrayList<>(stops);              // copy IN
    }

    Date getStartDate() {
        return new Date(startDate.getTime()); // copy OUT
    }

    List<String> getStops() {
        return List.copyOf(stops); // copy OUT, and unmodifiable too
    }
}
```

Now, mutating the caller's original `Date`/`List` after construction — or mutating whatever the getter returns — has zero effect on the `Trip`'s internal state.

### List.copyOf vs unmodifiable views vs true immutable copies

These three look similar but behave very differently, and mixing them up is a frequent bug:

| Technique | Independent from source? | Can it be mutated directly? |
|---|---|---|
| `Collections.unmodifiableList(list)` | No — it's a **view**; changes to the original `list` still show through | No (throws on direct mutation attempts) |
| `List.copyOf(list)` | Yes — makes an actual independent copy | No (immutable) |
| `new ArrayList<>(list)` | Yes — independent copy | Yes (still mutable) |

```java
List<String> source = new ArrayList<>(List.of("A", "B"));

List<String> view = Collections.unmodifiableList(source);
source.add("C");
System.out.println(view); // [A, B, C] — the view is NOT independent, it just blocks writes

List<String> copy = List.copyOf(source);
source.add("D");
System.out.println(copy); // [A, B, C] — the copy IS independent
```

```java
// BROKEN: developer thinks unmodifiableList() protects against upstream mutation.
class Roster {
    private final List<String> names;
    Roster(List<String> names) {
        this.names = Collections.unmodifiableList(names); // still a VIEW over the caller's list!
    }
}

List<String> mutable = new ArrayList<>(List.of("Alice"));
Roster roster = new Roster(mutable);
mutable.add("Mallory"); // Roster's "immutable" view now includes "Mallory" too!
```

```java
// FIXED: copy first, THEN wrap (or just use List.copyOf, which does both).
class Roster {
    private final List<String> names;
    Roster(List<String> names) {
        this.names = List.copyOf(names); // independent AND unmodifiable
    }
}
```

**Rule of thumb:** `unmodifiableXxx()` prevents *your* code (or the caller of your getter) from mutating a collection through that particular reference, but it does nothing to stop the *original owner* of the backing collection from mutating it. Use it for a getter when you already own an independent copy; use `copyOf` (or a manual copy) when you need true independence.

### Arrays are always a leak vector

Arrays are mutable in Java, and unlike `List`, there is no built-in "unmodifiable array" wrapper. Any method that returns or accepts an array is a defensive-copying hotspot.

```java
// BROKEN: exposes the internal array directly.
class PasswordPolicy {
    private final char[] allowedSymbols;
    PasswordPolicy(char[] allowedSymbols) {
        this.allowedSymbols = allowedSymbols; // stores caller's array directly
    }
    char[] getAllowedSymbols() { return allowedSymbols; } // returns internal array directly
}

char[] symbols = {'!', '@', '#'};
PasswordPolicy policy = new PasswordPolicy(symbols);
policy.getAllowedSymbols()[0] = 'X'; // mutates internal state via the returned array!
symbols[1] = 'Y';                    // mutates internal state via the original reference!
```

```java
// FIXED: clone() on the way in AND on the way out.
class PasswordPolicy {
    private final char[] allowedSymbols;
    PasswordPolicy(char[] allowedSymbols) {
        this.allowedSymbols = allowedSymbols.clone(); // array clone() is a fast shallow copy
    }
    char[] getAllowedSymbols() {
        return allowedSymbols.clone();
    }
}
```

(Note: `array.clone()` is one of the few reasonable uses of `clone()` in Java — arrays have well-defined, non-broken clone semantics, unlike ordinary objects.)

### Dates are a classic leak vector too

`java.util.Date` is mutable (it has `setTime()`), which is one of the reasons the legacy date API is considered a design mistake. The modern `java.time` types (`LocalDate`, `Instant`, `LocalDateTime`, etc.) are all immutable, so **switching to `java.time` eliminates this entire category of bug** — no defensive copy is needed because there is nothing to mutate.

```java
import java.time.LocalDate;

// GOOD: no defensive copying needed, because LocalDate is immutable.
final class Membership {
    private final LocalDate startDate;
    Membership(LocalDate startDate) {
        this.startDate = startDate; // safe: LocalDate can't be mutated by anyone
    }
    LocalDate getStartDate() {
        return startDate; // safe: caller can't mutate it either
    }
}
```

**Interview one-liner:** "Defensive copying is only necessary for mutable types. The best fix is often to avoid mutable types altogether — prefer `java.time` over `Date`, and `List.copyOf`/records over hand-rolled mutable collections."

## Common Code-Review Interview Pitfalls

1. **Overriding `equals()` without `hashCode()`.**
   Why it matters: breaks the equals/hashCode contract; the object silently "disappears" from `HashMap`/`HashSet` lookups.
   ```java
   // Before: equals() overridden, hashCode() left default (identity-based)
   @Override public boolean equals(Object o) { return o instanceof Id i && i.value == value; }

   // After: keep them in sync
   @Override public int hashCode() { return Integer.hashCode(value); }
   ```

2. **Using a mutable field as a `HashMap`/`HashSet` key's basis for `hashCode()`.**
   Why it matters: mutating the key after insertion makes it unfindable — a silent data-loss bug.
   ```java
   // Before: key.setStatus("DONE") after map.put(key, ...) -> map.get(key) returns null
   // After: use an immutable id (String/UUID/record) as the key instead of the mutable entity
   ```

3. **`instanceof`-based `equals()` in a class hierarchy where subclasses add fields.**
   Why it matters: breaks symmetry (`a.equals(b) != b.equals(a)`), causing inconsistent behavior in collections.
   ```java
   // Before: if (!(o instanceof Point p)) ... // ColorPoint subclass breaks symmetry
   // After: if (o == null || getClass() != o.getClass()) return false; // or make the class final
   ```

4. **Integer subtraction inside `compareTo`/`compare`.**
   Why it matters: overflows silently for large or negative values, corrupting sort order.
   ```java
   // Before: return this.value - other.value;
   // After:  return Integer.compare(this.value, other.value);
   ```

5. **Using `Cloneable`/`Object.clone()` for copying.**
   Why it matters: bypasses constructors (invariants unchecked), fragile with `final` fields and subclassing, throws a checked exception unnecessarily.
   ```java
   // Before: class Team implements Cloneable { ... super.clone() ... }
   // After:  Team(Team other) { this(other.name, other.members); } // copy constructor
   ```

6. **Shallow-copying an object that has mutable reference fields.**
   Why it matters: the "copy" secretly shares mutable state with the original — mutating one affects the other.
   ```java
   // Before: Team copy = (Team) super.clone(); // members list is SHARED
   // After:  copy.members = new ArrayList<>(this.members); // deep-ish copy
   ```

7. **`Optional` used as a field, parameter, or inside a collection.**
   Why it matters: adds overhead, is not `Serializable`, and `Optional` itself can be `null` — defeating its purpose.
   ```java
   // Before: void updateNickname(Optional<String> nickname)
   // After:  void updateNickname(String nickname) // nullable, documented
   ```

8. **Calling `orElse(expensiveCall())` instead of `orElseGet(...)`.**
   Why it matters: `orElse`'s argument is always evaluated eagerly, even when the `Optional` is present — wasted work or unwanted side effects.
   ```java
   // Before: opt.orElse(buildExpensiveDefault());
   // After:  opt.orElseGet(this::buildExpensiveDefault);
   ```

9. **Exposing an internal mutable collection or array directly from a getter.**
   Why it matters: callers can mutate "encapsulated" internal state from outside the class, breaking invariants silently.
   ```java
   // Before: List<String> getItems() { return items; }
   // After:  List<String> getItems() { return List.copyOf(items); }
   ```

10. **Storing a caller-provided mutable object directly in the constructor without copying.**
    Why it matters: the caller can mutate your object's "immutable" internal state after construction, from their original reference.
    ```java
    // Before: this.startDate = startDate; // Date is mutable
    // After:  this.startDate = new Date(startDate.getTime()); // defensive copy in
    ```

11. **Confusing `Collections.unmodifiableList()` (a view) with a true immutable copy.**
    Why it matters: an unmodifiable *view* still reflects changes made to the original backing collection — it does not provide independence.
    ```java
    // Before: this.names = Collections.unmodifiableList(names); // still a view over caller's list
    // After:  this.names = List.copyOf(names); // independent AND unmodifiable
    ```

12. **`compareTo` that is inconsistent with `equals()`, used with `TreeSet`/`TreeMap`.**
    Why it matters: `TreeSet`/`TreeMap` use `compareTo` (not `equals()`) to decide duplicates, so "equal" elements by `equals()` can both end up in the set if `compareTo` disagrees — or vice versa, distinct elements silently vanish as "duplicates."
    ```java
    // Before: compareTo compares only `lastName`, but equals() compares full name -> surprises in TreeSet
    // After:  make compareTo and equals() agree, or clearly document the difference
    ```

13. **Sorting a list that may contain `null` with `Comparator.naturalOrder()` directly.**
    Why it matters: throws `NullPointerException` at runtime instead of handling nulls predictably.
    ```java
    // Before: list.sort(Comparator.naturalOrder());
    // After:  list.sort(Comparator.nullsFirst(Comparator.naturalOrder()));
    ```

14. **Forgetting to regenerate IDE/Lombok-generated `equals()`/`hashCode()` after adding a field.**
    Why it matters: the new field is silently excluded from equality checks, causing subtly wrong deduplication or map behavior.
    ```java
    // Before: `status` field added, but equals()/hashCode() still only reference `id`
    // After:  regenerate, or intentionally document ID-only equality semantics
    ```

15. **Hand-writing a data class (fields, constructor, getters, `equals`/`hashCode`/`toString`) that could be a `record`.**
    Why it matters: more boilerplate to maintain, more surface area for the bugs above (missing `hashCode`, stale generated code); records get correct semantics for free.
    ```java
    // Before: class Point { private final int x, y; /* ~30 lines of boilerplate */ }
    // After:  record Point(int x, int y) {}
    ```
