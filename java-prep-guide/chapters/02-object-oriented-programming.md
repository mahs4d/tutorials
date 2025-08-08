# 2. Object-Oriented Programming

Object-Oriented Programming (OOP) is a way of organizing code around "objects" instead of just functions and data. Java is built on OOP from the ground up, so almost every code review question about design quality traces back to these basics. This chapter walks through the core OOP building blocks in Java 21+, with small, realistic examples and the "gotchas" that trip people up in interviews and in real pull requests.

## Table of Contents

- [Classes and Objects](#classes-and-objects)
- [Constructors](#constructors)
- [Encapsulation](#encapsulation)
- [Inheritance](#inheritance)
- [Polymorphism](#polymorphism)
- [Abstraction](#abstraction)
- [Method Overloading](#method-overloading)
- [Method Overriding](#method-overriding)
- [Access Modifiers](#access-modifiers)
- [Static Members](#static-members)
- [Final Keyword](#final-keyword)
- [This and Super](#this-and-super)
- [Nested, Inner, Local, and Anonymous Classes](#nested-inner-local-and-anonymous-classes)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Classes and Objects

A **class** is a blueprint. It describes what data (fields) and behavior (methods) something has, but it is not itself a "thing" you can use directly. An **object** is an actual instance created from that blueprint, living in memory, with its own copy of the fields (unless they are `static`, see [Static Members](#static-members)).

Think of `Account` as the blueprint for a bank account, and each customer's actual account as an object built from that blueprint.

```java
public class Account {
    private String ownerName;
    private double balance;

    public Account(String ownerName, double balance) {
        this.ownerName = ownerName;
        this.balance = balance;
    }

    public void deposit(double amount) {
        balance += amount;
    }

    public double getBalance() {
        return balance;
    }
}

public class Bank {
    public static void main(String[] args) {
        Account aliceAccount = new Account("Alice", 100.0);
        Account bobAccount = new Account("Bob", 50.0);

        aliceAccount.deposit(25.0);

        System.out.println(aliceAccount.getBalance()); // 125.0
        System.out.println(bobAccount.getBalance());   // 50.0 (independent object)
    }
}
```

Each `new Account(...)` call creates a separate object with its own `balance`. Changing `aliceAccount` never affects `bobAccount`. That independence is the whole point of objects: state is bundled with the behavior that operates on it.

**Gotcha: object identity vs. object equality.** Two different objects can have the same data but are still not "the same object" in memory.

```java
Account a1 = new Account("Alice", 100.0);
Account a2 = new Account("Alice", 100.0);

System.out.println(a1 == a2);       // false: different objects in memory
System.out.println(a1.equals(a2));  // true only if equals() is overridden;
                                     // otherwise also false (default Object.equals uses ==)
```

`==` compares references (memory addresses), not content. Unless a class overrides `equals()`, `.equals()` behaves exactly like `==`. This is covered more in the "Object Class & Common APIs" chapter, but it starts here with basic object identity.

## Constructors

A **constructor** is a special method that runs when an object is created with `new`. It has the same name as the class, no return type (not even `void`), and its job is to put the object into a valid starting state.

```java
public class Order {
    private final String orderId;
    private final List<String> items;
    private double total;

    // No-args constructor
    public Order() {
        this("ORD-0000"); // calls the other constructor below
    }

    // Constructor with a parameter
    public Order(String orderId) {
        this.orderId = orderId;
        this.items = new ArrayList<>();
        this.total = 0.0;
    }
}
```

If you don't write any constructor, Java gives you a free **default constructor** with no arguments. The moment you write any constructor yourself, that free one disappears.

```java
public class Shape {
    private String color;
    // No constructor written here.
}

// Elsewhere:
Shape s = new Shape(); // Works: compiler-generated default constructor
```

```java
public class Shape {
    private String color;

    public Shape(String color) {
        this.color = color;
    }
    // No no-args constructor written, and the default one is gone now.
}

// Elsewhere:
Shape s = new Shape(); // Compile error: no matching constructor
```

**Constructor chaining** means one constructor calls another using `this(...)`, so shared setup logic lives in one place. A constructor can also call a parent class constructor using `super(...)`. If you don't call `super(...)` explicitly, Java inserts an implicit no-argument `super()` call as the very first line.

```java
public class Vehicle {
    protected String plateNumber;

    public Vehicle(String plateNumber) {
        this.plateNumber = plateNumber;
        System.out.println("Vehicle created: " + plateNumber);
    }
}

public class Car extends Vehicle {
    private int doors;

    public Car(String plateNumber, int doors) {
        super(plateNumber); // must be the first statement
        this.doors = doors;
        System.out.println("Car created with " + doors + " doors");
    }
}

// new Car("XYZ-123", 4);
// Output:
// Vehicle created: XYZ-123
// Car created with 4 doors
```

**Gotcha: calling an overridable method from a constructor.** If a constructor calls a method that a subclass overrides, the subclass version can run *before* the subclass has finished initializing its own fields.

```java
public class Shape {
    public Shape() {
        draw(); // calling an overridable method during construction
    }

    public void draw() {
        System.out.println("Drawing a generic shape");
    }
}

public class Circle extends Shape {
    private int radius = 5;

    @Override
    public void draw() {
        // radius may not be assigned yet when this runs from Shape's constructor!
        System.out.println("Drawing circle with radius " + radius);
    }
}

// new Circle();
// Output: Drawing circle with radius 0
// (Shape's constructor runs first and calls draw(), but Circle's field
//  initializer "radius = 5" hasn't executed yet.)
```

The fix: avoid calling overridable (non-`private`, non-`final`, non-`static`) methods from a constructor, or mark the method `final` if it must be called during construction.

## Encapsulation

**Encapsulation** means hiding the internal state of an object and only exposing it through controlled methods. Fields are usually `private`, and public **getters/setters** (or no setters at all, for immutable data) control access. This protects invariants — rules that must always be true, like "balance can never go negative."

```java
public class Account {
    private double balance;

    public Account(double initialBalance) {
        if (initialBalance < 0) {
            throw new IllegalArgumentException("Initial balance cannot be negative");
        }
        this.balance = initialBalance;
    }

    public double getBalance() {
        return balance;
    }

    public void withdraw(double amount) {
        if (amount > balance) {
            throw new IllegalStateException("Insufficient funds");
        }
        balance -= amount;
    }
}
```

Without encapsulation, any code could do `account.balance = -500;` directly and break the invariant. With a `private` field, the only way to change `balance` is through `withdraw()`, which enforces the rule.

**Gotcha: a getter that leaks a mutable field breaks encapsulation even though the field is `private`.**

```java
public class Order {
    private final List<String> items = new ArrayList<>();

    public List<String> getItems() {
        return items; // returns the real internal list!
    }
}

Order order = new Order();
order.getItems().add("Hacked item"); // mutates internal state from outside
```

The fix is a **defensive copy**, or returning an unmodifiable view:

```java
public List<String> getItems() {
    return List.copyOf(items); // caller gets a snapshot, can't mutate internals
}
```

## Inheritance

**Inheritance** lets one class (the subclass) reuse and extend the fields and methods of another class (the superclass), using the `extends` keyword. It models an "is-a" relationship: a `Car` **is a** `Vehicle`.

```java
public class Vehicle {
    protected String plateNumber;
    protected int speed;

    public Vehicle(String plateNumber) {
        this.plateNumber = plateNumber;
    }

    public void accelerate(int amount) {
        speed += amount;
    }

    public String describe() {
        return "Vehicle " + plateNumber + " at speed " + speed;
    }
}

public class Car extends Vehicle {
    private int doors;

    public Car(String plateNumber, int doors) {
        super(plateNumber);
        this.doors = doors;
    }

    // Inherits accelerate() and describe() for free
}

Car car = new Car("XYZ-123", 4);
car.accelerate(30);
System.out.println(car.describe()); // Vehicle XYZ-123 at speed 30
```

Java only allows **single inheritance** for classes: a class can `extends` only one other class. This avoids the "diamond problem" (ambiguity when two parent classes define the same method). Java does allow a class to implement multiple interfaces, since interfaces (mostly) don't carry conflicting state.

```java
public class Car extends Vehicle implements Insurable, Sellable {
    // one superclass, multiple interfaces — this is fine
}
```

**Gotcha: field hiding.** Unlike methods, fields in Java are not polymorphic. If a subclass declares a field with the same name as a superclass field, both fields exist — which one you see depends on the *declared type* of the reference, not the actual object type.

```java
public class Vehicle {
    protected int speed = 10;
}

public class Car extends Vehicle {
    protected int speed = 20; // hides Vehicle's speed, does NOT override it
}

Vehicle v = new Car();
System.out.println(v.speed); // 10 (uses Vehicle's field, based on reference type!)

Car c = new Car();
System.out.println(c.speed); // 20
```

This is a classic code-review red flag: shadowing fields across a class hierarchy is confusing and error-prone. Prefer accessing state through methods, not fields, across inheritance boundaries.

## Polymorphism

**Polymorphism** ("many forms") means a single reference type can point to different actual object types, and calling a method on it runs the correct version for the *actual* object — decided at runtime. This is also called **dynamic dispatch** or **runtime polymorphism**.

```java
public abstract class Shape {
    public abstract double area();
}

public class Circle extends Shape {
    private final double radius;
    public Circle(double radius) { this.radius = radius; }
    @Override
    public double area() { return Math.PI * radius * radius; }
}

public class Rectangle extends Shape {
    private final double width, height;
    public Rectangle(double width, double height) {
        this.width = width;
        this.height = height;
    }
    @Override
    public double area() { return width * height; }
}

public class AreaCalculator {
    public static void main(String[] args) {
        List<Shape> shapes = List.of(new Circle(2), new Rectangle(3, 4));
        for (Shape shape : shapes) {
            System.out.println(shape.area()); // correct area() runs for each real type
        }
        // Output:
        // 12.566370614359172
        // 12.0
    }
}
```

The loop variable is declared as `Shape`, but each call to `area()` runs the actual subclass's implementation. The calling code doesn't need `if (shape instanceof Circle) ...` checks — that's the benefit of polymorphism.

There are two flavors people mention in interviews:

| Type | Also called | Resolved when | Example |
|---|---|---|---|
| Compile-time polymorphism | Static binding | At compile time, by looking at parameter types | Method overloading |
| Runtime polymorphism | Dynamic binding | At runtime, by looking at the actual object | Method overriding |

**Gotcha: `static` methods are not polymorphic.** They are resolved by the *reference type*, not the actual object — this is called **method hiding**, not overriding.

```java
public class Vehicle {
    public static String category() { return "Generic Vehicle"; }
}

public class Car extends Vehicle {
    public static String category() { return "Car"; } // hides, not overrides
}

Vehicle v = new Car();
System.out.println(v.category()); // "Generic Vehicle" — decided by reference type!
```

## Abstraction

**Abstraction** means exposing *what* something does while hiding *how* it does it. In Java this is done with `abstract` classes and interfaces. An `abstract` class can have some implemented methods and some unimplemented (`abstract`) ones; it cannot be instantiated directly.

```java
public abstract class PaymentMethod {
    // Abstract method: no body, subclasses must implement it
    public abstract boolean authorize(double amount);

    // Concrete method: shared logic for all payment methods
    public final void pay(double amount) {
        if (authorize(amount)) {
            System.out.println("Paid " + amount);
        } else {
            System.out.println("Payment declined");
        }
    }
}

public class CreditCardPayment extends PaymentMethod {
    @Override
    public boolean authorize(double amount) {
        return amount <= 5000; // simplified rule
    }
}

PaymentMethod payment = new CreditCardPayment();
payment.pay(1200); // Paid 1200.0
```

```java
PaymentMethod payment = new PaymentMethod(); // Compile error: PaymentMethod is abstract
```

Interfaces (covered fully in the "Modern Java Language Features" chapter) are a purer form of abstraction: historically 100% abstract, though modern Java interfaces can also have `default`, `static`, and `private` methods.

```java
public interface Discountable {
    double applyDiscount(double price);
}

public class Order implements Discountable {
    @Override
    public double applyDiscount(double price) {
        return price * 0.9; // 10% off
    }
}
```

**Rule of thumb for code review:** use an abstract class when subclasses share state or common implementation; use an interface when you're describing a capability that unrelated classes might implement (`Comparable`, `Discountable`, `Serializable`).

## Method Overloading

**Method overloading** means defining multiple methods with the *same name* but *different parameter lists* (different type, number, or order of parameters) in the same class. It is resolved at **compile time** based on the arguments' declared types — this is why it's called compile-time (static) polymorphism.

```java
public class Invoice {
    public double calculateTotal(double price, int quantity) {
        return price * quantity;
    }

    public double calculateTotal(double price, int quantity, double discount) {
        return price * quantity * (1 - discount);
    }

    public double calculateTotal(double price, int quantity, double discount, double taxRate) {
        return price * quantity * (1 - discount) * (1 + taxRate);
    }
}

Invoice invoice = new Invoice();
System.out.println(invoice.calculateTotal(10.0, 3));             // 30.0
System.out.println(invoice.calculateTotal(10.0, 3, 0.1));        // 27.0
```

You cannot overload by return type alone — the parameter list must differ.

```java
public int process(String data) { return 1; }
public double process(String data) { return 1.0; } // Compile error: same signature
```

**Gotcha: overload resolution with autoboxing and varargs.** Java picks the *most specific* match, and it prefers, in order: (1) exact match with widening of primitives, (2) autoboxing/unboxing, (3) varargs. This can surprise people.

```java
public class Logger {
    public void log(int code) {
        System.out.println("int version: " + code);
    }

    public void log(Integer code) {
        System.out.println("Integer version: " + code);
    }

    public void log(Object... codes) {
        System.out.println("varargs version");
    }
}

Logger logger = new Logger();
logger.log(5); // "int version: 5"
// Java prefers the exact primitive match over autoboxing to Integer,
// and prefers both of those over varargs.
```

```java
public class Calculator {
    public void print(long value)   { System.out.println("long: " + value); }
    public void print(Integer value) { System.out.println("Integer: " + value); }

    public static void main(String[] args) {
        int x = 5;
        new Calculator().print(x);
        // Output: "long: 5"
        // Widening (int -> long) is preferred over autoboxing (int -> Integer)!
    }
}
```

This is a frequent code-review discussion point: overloads that mix primitives, wrapper types, and varargs are a maintenance hazard because the "obviously correct" overload isn't always the one that's chosen.

## Method Overriding

**Method overriding** means a subclass provides its own implementation of a method already defined in its superclass, with the *same signature*. It is resolved at **runtime** based on the actual object type — runtime (dynamic) polymorphism. Always use the `@Override` annotation; it makes the compiler check that you're actually overriding something.

```java
public class Shape {
    public double area() {
        return 0.0;
    }

    @Override
    public String toString() {
        return "Shape with area " + area();
    }
}

public class Square extends Shape {
    private final double side;
    public Square(double side) { this.side = side; }

    @Override
    public double area() {
        return side * side;
    }
}

Shape shape = new Square(4);
System.out.println(shape.area());     // 16.0
System.out.println(shape);            // Shape with area 16.0 (toString overridden indirectly via area())
```

Overriding rules the compiler enforces:
- Same method name and parameter list.
- Return type must be the same or a **covariant** (narrower) subtype.
- Access modifier can stay the same or become *more* visible, never less visible.
- Cannot override a method marked `final` or `static` (that becomes hiding, not overriding — see [Static Members](#static-members)).

| | Method Overloading | Method Overriding |
|---|---|---|
| Same method name? | Yes | Yes |
| Parameter list | Must differ | Must be identical |
| Return type | Can differ freely | Must be same or covariant |
| Resolved | Compile time (static binding) | Runtime (dynamic binding) |
| Relationship | Same class (or same class hierarchy) | Superclass / subclass |
| Access modifier | Can differ freely | Cannot be more restrictive |

**Gotcha: narrowing the access modifier when overriding is a compile error.**

```java
public class Vehicle {
    public void start() {
        System.out.println("Vehicle starting");
    }
}

public class Car extends Vehicle {
    @Override
    protected void start() { // Compile error: cannot reduce visibility from public to protected
        System.out.println("Car starting");
    }
}
```

**Gotcha: `@Override` catches signature mistakes early.**

```java
public class Shape {
    public double area() { return 0.0; }
}

public class Circle extends Shape {
    // Typo: overload instead of override, silently creates a NEW method
    public double area(double scale) {
        return 3.14 * scale;
    }
}
// Without @Override, this compiles but never overrides Shape.area().
// Adding @Override to area(double scale) would immediately fail to compile,
// revealing the mistake.
```

## Access Modifiers

Access modifiers control which code can see a class, field, method, or constructor. Java has four levels:

| Modifier | Same class | Same package | Subclass (different package) | Everywhere |
|---|---|---|---|---|
| `private` | Yes | No | No | No |
| (no modifier / package-private) | Yes | Yes | No | No |
| `protected` | Yes | Yes | Yes | No |
| `public` | Yes | Yes | Yes | Yes |

```java
package com.bank.account;

public class Account {
    private double balance;         // only inside Account itself
    String accountType;             // package-private: visible to classes in com.bank.account
    protected String branchCode;    // visible in package + subclasses anywhere
    public String ownerName;        // visible everywhere
}
```

```java
package com.bank.account;

public class SavingsAccount extends Account {
    public void printBranch() {
        System.out.println(branchCode); // OK: protected, and this is a subclass
        // System.out.println(balance); // Compile error: balance is private to Account
    }
}
```

```java
package com.bank.reporting;

public class ReportGenerator {
    public void printOwner(Account account) {
        System.out.println(account.ownerName); // OK: public
        // System.out.println(account.branchCode); // Compile error: different package, not a subclass
    }
}
```

**Gotcha: `protected` in a different package is only accessible through inheritance, not through an arbitrary reference.**

```java
package com.bank.reporting;

import com.bank.account.Account;

public class AuditTool extends Account {
    public void check(Account other) {
        this.branchCode = "B01";        // OK: accessing through "this" (the subclass instance)
        // other.branchCode = "B02";    // Compile error: accessing through another Account reference
    }
}
```

The rule of thumb code reviewers apply: **use the most restrictive modifier that still works.** Start with `private`, widen only when there's a real need. Overly public fields and methods make future refactoring risky because you don't know who depends on them.

## Static Members

`static` members belong to the **class itself**, not to any particular object. There is exactly one copy, shared by all instances.

```java
public class Account {
    private static int totalAccountsCreated = 0; // shared across all Account objects
    private final String ownerName;

    public Account(String ownerName) {
        this.ownerName = ownerName;
        totalAccountsCreated++;
    }

    public static int getTotalAccountsCreated() {
        return totalAccountsCreated;
    }
}

new Account("Alice");
new Account("Bob");
new Account("Carol");

System.out.println(Account.getTotalAccountsCreated()); // 3
```

Common uses of `static`:
- **Static fields**: shared/global state per class, like counters or constants.
- **Static methods**: utility logic that doesn't need object state, e.g. `Math.sqrt(x)`.
- **Static nested classes**: see [Nested, Inner, Local, and Anonymous Classes](#nested-inner-local-and-anonymous-classes).
- **Static initializer blocks**: run once when the class is first loaded.

```java
public class Configuration {
    public static final String ENVIRONMENT;

    static {
        // Runs once, when the class is loaded — good for expensive one-time setup
        ENVIRONMENT = System.getenv().getOrDefault("APP_ENV", "development");
        System.out.println("Configuration loaded for: " + ENVIRONMENT);
    }
}
```

A `static` method cannot directly access instance (non-static) fields or methods, because there's no specific object to work on.

```java
public class Account {
    private double balance = 100;

    public static void printBalance() {
        // System.out.println(balance); // Compile error: no instance to read balance from
    }
}
```

**Gotcha: static methods are inherited but hidden, not overridden** (also shown earlier in [Polymorphism](#polymorphism)). Calling a static method through an instance reference is legal but misleading — code reviewers should flag it.

```java
public class ReportUtil {
    public static void printHeader() {
        System.out.println("=== Report ===");
    }
}

ReportUtil util = new ReportUtil();
util.printHeader(); // Works, but should be called as ReportUtil.printHeader()
```

**Gotcha: mutable static state is a shared, hidden dependency** — dangerous in multi-threaded code and hard to test because it persists between tests.

```java
public class IdGenerator {
    private static int nextId = 1; // shared mutable state!

    public static int next() {
        return nextId++; // not thread-safe: race condition under concurrent access
    }
}
```

## Final Keyword

`final` means "cannot be changed again," but what exactly it locks down depends on where it's applied:

| Applied to | Meaning |
|---|---|
| `final` variable/field | Value (or object reference) can only be assigned once |
| `final` method | Cannot be overridden by subclasses |
| `final` class | Cannot be extended (no subclasses at all) |

```java
public final class ImmutablePoint { // cannot be subclassed
    private final int x; // must be assigned exactly once (constructor or declaration)
    private final int y;

    public ImmutablePoint(int x, int y) {
        this.x = x;
        this.y = y;
    }

    public final int getX() { // cannot be overridden, even though the class already forbids subclassing
        return x;
    }
}
```

```java
public class Vehicle {
    protected final int maxSpeed;

    public Vehicle(int maxSpeed) {
        this.maxSpeed = maxSpeed; // OK: first (and only) assignment
    }

    public void boost() {
        // this.maxSpeed = maxSpeed + 10; // Compile error: final field can't be reassigned
    }
}
```

```java
public class Vehicle {
    public final void identify() {
        System.out.println("I am a vehicle");
    }
}

public class Car extends Vehicle {
    @Override
    public void identify() { // Compile error: cannot override a final method
        System.out.println("I am a car");
    }
}
```

**Gotcha: `final` on a reference only locks the reference, not the object's contents.** A `final` field can still point to a mutable object whose internal state changes.

```java
public class Order {
    private final List<String> items = new ArrayList<>();

    public void addItem(String item) {
        items.add(item); // legal! We're mutating the List, not reassigning "items"
    }

    public void replaceList() {
        // items = new ArrayList<>(); // Compile error: can't reassign a final field
    }
}
```

This is a common interview trap: `final` does **not** mean immutable. True immutability requires the class itself to prevent internal mutation too (see the "Immutability" topic in the "Object Class & Common APIs" chapter).

## This and Super

`this` refers to the **current object** — the one whose method or constructor is currently executing. `super` refers to the **immediate superclass**, used to call its constructor or access members it defines.

```java
public class Vehicle {
    protected String plateNumber;

    public Vehicle(String plateNumber) {
        this.plateNumber = plateNumber;
    }

    public String describe() {
        return "Vehicle " + plateNumber;
    }
}

public class Car extends Vehicle {
    private String plateNumber; // shadows Vehicle's field — usually a bad idea, shown here for illustration

    public Car(String plateNumber) {
        super(plateNumber);       // calls Vehicle's constructor
        this.plateNumber = plateNumber; // "this" disambiguates the field from the parameter
    }

    @Override
    public String describe() {
        return super.describe() + " (Car version, plate=" + this.plateNumber + ")";
    }
}

Car car = new Car("ABC-999");
System.out.println(car.describe());
// Output: Vehicle ABC-999 (Car version, plate=ABC-999)
```

Common uses of `this`:
- Disambiguate a field from a constructor/method parameter with the same name (`this.balance = balance;`).
- Call another constructor in the same class (`this(...)` — constructor chaining, see [Constructors](#constructors)).
- Pass the current object as an argument (`someMethod(this)`).
- Return the current object for method chaining (`return this;`).

```java
public class InvoiceBuilder {
    private double amount;
    private String currency;

    public InvoiceBuilder withAmount(double amount) {
        this.amount = amount;
        return this; // enables chaining
    }

    public InvoiceBuilder withCurrency(String currency) {
        this.currency = currency;
        return this;
    }
}

InvoiceBuilder builder = new InvoiceBuilder()
    .withAmount(99.99)
    .withCurrency("EUR"); // fluent chaining thanks to "return this;"
```

Common uses of `super`:
- Call the parent constructor (`super(...)` — must be the first statement in a constructor).
- Call the parent's version of an overridden method (`super.describe()`).
- Access a parent field that's shadowed by a subclass field.

**Gotcha: `super` only reaches one level up.** There's no `super.super.method()` in Java — if `Car extends Vehicle extends Machine`, `Car` can call `super.describe()` to reach `Vehicle`'s version, but not `Machine`'s directly (unless `Vehicle` doesn't override it, in which case `super.describe()` naturally falls through to `Machine`'s implementation).

## Nested, Inner, Local, and Anonymous Classes

Java allows classes defined inside other classes or even inside methods. They're grouped into four kinds:

| Kind | Defined | Needs outer instance? | Typical use |
|---|---|---|---|
| Static nested class | Inside a class, marked `static` | No | Grouping a helper class that doesn't need outer state |
| Inner class | Inside a class, not `static` | Yes (holds implicit reference to outer object) | Tight coupling with an outer object's state |
| Local class | Inside a method body | Yes, if the method is an instance method | One-off helper used only within a method |
| Anonymous class | Inline, no name, usually implementing an interface or extending a class | Depends on context | Quick one-time implementation, e.g. a callback |

**Static nested class** — behaves like a top-level class, just namespaced inside another for organization. Does not hold a reference to an outer instance.

```java
public class Order {
    private final List<LineItem> lineItems = new ArrayList<>();

    public static class LineItem { // static nested class
        private final String productName;
        private final int quantity;

        public LineItem(String productName, int quantity) {
            this.productName = productName;
            this.quantity = quantity;
        }
    }

    public void addLineItem(String productName, int quantity) {
        lineItems.add(new LineItem(productName, quantity));
    }
}

// Created without needing an Order instance:
Order.LineItem item = new Order.LineItem("Widget", 3);
```

**Inner class (non-static)** — tied to a specific outer object; it can access the outer object's fields directly, even `private` ones.

```java
public class Order {
    private double total;
    private final List<String> discountLog = new ArrayList<>();

    public class DiscountApplier { // inner (non-static) class
        public void apply(double percentage) {
            total = total * (1 - percentage); // accesses outer field directly
            discountLog.add("Applied " + (percentage * 100) + "% discount");
        }
    }
}

Order order = new Order();
Order.DiscountApplier applier = order.new DiscountApplier(); // needs an outer instance
applier.apply(0.1);
```

**Local class** — declared inside a method, visible only within that method. Useful when a helper type is needed just for one algorithm and shouldn't leak outside it.

```java
public class ReportGenerator {
    public List<String> generateSummaries(List<Double> amounts) {
        class Formatter { // local class, scoped to this method
            String format(double amount) {
                return String.format("$%.2f", amount);
            }
        }

        Formatter formatter = new Formatter();
        List<String> summaries = new ArrayList<>();
        for (double amount : amounts) {
            summaries.add(formatter.format(amount));
        }
        return summaries;
    }
}
```

**Anonymous class** — a class with no name, defined and instantiated in a single expression. Common before lambdas existed, and still used for interfaces with more than one method, or when you need to keep extra state.

```java
public interface DiscountRule {
    double apply(double price);
}

public class CheckoutService {
    public double checkout(double price, DiscountRule rule) {
        return rule.apply(price);
    }

    public void run() {
        double finalPrice = checkout(100.0, new DiscountRule() { // anonymous class
            @Override
            public double apply(double price) {
                return price * 0.85; // 15% off
            }
        });
        System.out.println(finalPrice); // 85.0
    }
}
```

For a single-method (functional) interface like `DiscountRule`, a **lambda expression** is usually preferred over an anonymous class in modern Java — it's shorter and equally clear:

```java
double finalPrice = checkout(100.0, price -> price * 0.85);
```

**Gotcha: a non-static inner class secretly holds a reference to its outer instance**, which can cause **memory leaks** — the outer object can't be garbage collected while the inner object is still reachable (e.g., stored in a long-lived collection or passed to another thread).

```java
public class Order {
    private final byte[] largePayload = new byte[10_000_000]; // 10 MB

    public class Listener { // inner class — implicitly holds a reference to the enclosing Order
        void onEvent() {
            System.out.println("Event for order with payload size " + largePayload.length);
        }
    }
}

// If a Listener instance is stored somewhere long-lived (e.g., a static list
// or an event bus), the entire enclosing Order — including its 10 MB payload —
// stays reachable and cannot be garbage collected, even if nobody needs the Order anymore.
```

The fix: prefer a `static` nested class (optionally taking any needed outer data explicitly as a constructor parameter) when you don't truly need access to the outer instance's state.

```java
public class Order {
    private final byte[] largePayload = new byte[10_000_000];

    public static class Listener { // static: no hidden reference to Order
        void onEvent() {
            System.out.println("Event received");
        }
    }
}
```

**Gotcha: local and anonymous classes can only capture `effectively final` local variables** — variables that are never reassigned after initialization.

```java
public List<Runnable> createTasks() {
    List<Runnable> tasks = new ArrayList<>();
    for (int i = 0; i < 3; i++) {
        int taskNumber = i; // effectively final: assigned once per loop iteration
        tasks.add(new Runnable() {
            @Override
            public void run() {
                System.out.println("Task " + taskNumber); // OK: captures effectively final variable
            }
        });
    }
    return tasks;
}
```

```java
public Runnable createTask() {
    int counter = 0;
    Runnable task = () -> System.out.println(counter);
    counter = 5; // Compile error: "counter" is captured by the lambda, so it must stay effectively final
    return task;
}
```

## Common Code-Review Interview Pitfalls

1. **Public mutable fields instead of encapsulated access.** Why it matters: callers can corrupt an object's invariants directly, bypassing any validation.
   ```java
   // Before
   public class Account { public double balance; }
   // After
   public class Account {
       private double balance;
       public void withdraw(double amount) { /* validate, then mutate */ }
   }
   ```

2. **Getters that return direct references to mutable internal collections/objects.** Why it matters: callers can mutate "private" state from outside, silently breaking encapsulation.
   ```java
   // Before
   public List<String> getItems() { return items; }
   // After
   public List<String> getItems() { return List.copyOf(items); }
   ```

3. **Calling an overridable instance method from a constructor.** Why it matters: the subclass's fields may not be initialized yet when the overridden method runs, producing wrong values (e.g., `0` or `null`).
   ```java
   // Before: Shape() calls draw(), Circle overrides draw() using a field set after super() runs
   // After: mark draw() final, or move the call out of the constructor into an init method
   ```

4. **Confusing method overloading with overriding, especially with a missing `@Override`.** Why it matters: a typo in parameters silently creates a new overload instead of overriding, and the bug hides until runtime.
   ```java
   // Before (no annotation, silent overload)
   public double area(double scale) { ... }
   // After (compiler enforces correctness)
   @Override
   public double area() { ... }
   ```

5. **Field hiding across a class hierarchy.** Why it matters: which field you read depends on the *reference type*, not the actual object — a frequent source of confusing bugs.
   ```java
   // Before: Car declares its own "speed" field that shadows Vehicle.speed
   // After: don't redeclare inherited fields; add new fields with distinct names, or use methods
   ```

6. **Treating `static` methods as polymorphic.** Why it matters: `static` methods are hidden, not overridden — calling them through a superclass-typed reference always uses the reference's declared type, not the runtime type.
   ```java
   Vehicle v = new Car();
   v.category(); // resolves using Vehicle's static method, surprising reviewers expecting Car's
   ```

7. **Assuming `final` means immutable.** Why it matters: `final` only prevents reassigning the reference; the referenced object (e.g., a `List`) can still be mutated internally.
   ```java
   private final List<String> items = new ArrayList<>();
   items.add("x"); // still allowed, even though "items" is final
   ```

8. **Overly broad access modifiers (`public` by default).** Why it matters: wide visibility becomes a long-term maintenance liability — every public member is part of your API contract and hard to change later.
   ```java
   // Before
   public double balance;
   // After
   private double balance;
   public double getBalance() { return balance; }
   ```

9. **Non-static inner classes causing memory leaks.** Why it matters: an inner class instance implicitly holds a reference to its enclosing object, keeping it alive longer than expected if stored somewhere long-lived.
   ```java
   // Before
   public class Listener { /* implicit outer reference */ }
   // After
   public static class Listener { /* no implicit outer reference */ }
   ```

10. **Overload resolution ambiguity with autoboxing and varargs.** Why it matters: adding a new overload can silently change which overload existing calls resolve to, without any compile error.
    ```java
    void log(int code) { ... }
    void log(Integer code) { ... }
    void log(Object... codes) { ... }
    // log(5) always prefers int over Integer over varargs — surprising to reviewers unfamiliar with the resolution order
    ```

11. **Narrowing visibility when overriding (or trying to).** Why it matters: it's a compile error, but the intent (restricting subclass behavior) usually signals a deeper design problem — maybe the method shouldn't be overridable at all.
    ```java
    // Before: attempting to override public start() as protected — fails to compile
    // After: keep the same or wider access, or reconsider whether the base method should be public
    ```

12. **Mutable `static` fields used as shared/global state.** Why it matters: they're a hidden dependency between unrelated pieces of code, cause race conditions in multi-threaded contexts, and leak state between unit tests.
    ```java
    // Before
    private static int nextId = 1; // shared, unsynchronized
    // After
    private static final AtomicInteger nextId = new AtomicInteger(1);
    ```

13. **No-args constructor silently disappearing once another constructor is added.** Why it matters: code (or a framework needing a default constructor, e.g. some serializers) that relied on `new Foo()` breaks at compile or runtime.
    ```java
    // Before: only Shape(String color) exists
    // After: explicitly add Shape() if a no-args constructor is still needed
    ```

14. **Using an abstract class where a simple interface would do (or vice versa).** Why it matters: abstract classes force single inheritance and couple unrelated types; interfaces should be preferred for pure capability contracts, abstract classes for shared implementation/state.
    ```java
    // Before: abstract class Discountable with no shared state or implementation
    // After: interface Discountable { double applyDiscount(double price); }
    ```

15. **Anonymous classes used where a lambda would be clearer.** Why it matters: for single-method functional interfaces, an anonymous class is verbose boilerplate compared to a lambda, and reviewers should flag it as an easy simplification.
    ```java
    // Before
    new DiscountRule() {
        @Override
        public double apply(double price) { return price * 0.85; }
    };
    // After
    (double price) -> price * 0.85;
    ```
