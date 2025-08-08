# 19. Software Engineering Best Practices

Writing code that *compiles* is easy. Writing code that a teammate can read, change, and trust six months from now is the actual job. This chapter is a practical tour of the habits, principles, and patterns that Java reviewers look for: clean code rules, SOLID, the highest-value tips from *Effective Java*, the design patterns that keep showing up in interviews, API design guidelines, and the code smells that get flagged in almost every pull request. We target Java 21+ and use records, sealed types, and pattern matching wherever they simplify the classic advice.

## Table of Contents

- [Clean Code in Java](#clean-code-in-java)
- [SOLID Principles](#solid-principles)
- [Effective Java Best Practices](#effective-java-best-practices)
- [Design Patterns in Java](#design-patterns-in-java)
- [API Design Best Practices](#api-design-best-practices)
- [Common Code Smells](#common-code-smells)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Clean Code in Java

"Clean code" means code that is easy to read, easy to change, and does what its name says. It is not about being clever. A reviewer should be able to understand a method in a few seconds, without running it.

### Naming

Names are the cheapest form of documentation. A good name removes the need for a comment.

```java
// Before: what is d? what is 86400?
int d = 86400;
boolean f = u.getA() > d;

// After: intention is obvious without a comment
int secondsPerDay = 86_400;
boolean accountIsOverdue = user.getAccountAgeInSeconds() > secondsPerDay;
```

Rules of thumb:
- Classes and variables: nouns (`OrderService`, `pendingInvoices`).
- Methods: verbs (`calculateTotal`, `isValid`).
- Booleans: read like a yes/no question (`isEmpty`, `hasPermission`, `canRetry`).
- Avoid abbreviations that only the author understands (`usrRegSvc` → `userRegistrationService`).
- Avoid names that lie. A `List<Order> orderList` that later becomes a `Set` is now a lying name — just call it `orders`.

### Function Size and Single Level of Abstraction

A function should do one thing, and every line inside it should be at roughly the same "zoom level." Mixing high-level steps ("place order") with low-level details (string parsing, raw loops) in one method forces the reader to context-switch constantly.

```java
// Before: one method, three levels of abstraction mixed together
public void placeOrder(Order order) {
    // high-level: validate
    if (order.getItems().isEmpty()) {
        throw new IllegalArgumentException("Order has no items");
    }
    // low-level: compute total by hand
    BigDecimal total = BigDecimal.ZERO;
    for (OrderItem item : order.getItems()) {
        total = total.add(item.getPrice().multiply(BigDecimal.valueOf(item.getQuantity())));
    }
    // low-level: talk to payment gateway
    HttpURLConnection conn = null;
    try {
        conn = (HttpURLConnection) new URL("https://pay.example.com").openConnection();
        conn.setDoOutput(true);
        // ... write request body, read response ...
    } catch (IOException e) {
        throw new PaymentException(e);
    }
    // high-level: persist
    orderRepository.save(order);
}
```

```java
// After: each method reads at one level of abstraction
public void placeOrder(Order order) {
    validate(order);
    BigDecimal total = order.calculateTotal();
    paymentGateway.charge(order.getCustomerId(), total);
    orderRepository.save(order);
}

private void validate(Order order) {
    if (order.getItems().isEmpty()) {
        throw new IllegalArgumentException("Order has no items");
    }
}
```

The payment call and the total calculation moved into their own well-named units (`paymentGateway.charge`, `order.calculateTotal`). `placeOrder` now reads like a short story: validate, total, charge, save.

### Argument Count

More than three or four parameters is a warning sign. Callers can pass arguments in the wrong order, and the call site becomes unreadable.

```java
// Before: which boolean is which? easy to swap
createUser("Ana", "ana@example.com", true, false, true);

// After: a small parameter object makes the call self-explanatory
record UserCreationRequest(String name, String email, boolean sendWelcomeEmail,
                            boolean requireEmailVerification, boolean isAdmin) {}

createUser(new UserCreationRequest("Ana", "ana@example.com", true, false, true));
```

Even better, use named construction so the call site itself documents what each flag means:

```java
createUser(UserCreationRequest.builder()
        .name("Ana")
        .email("ana@example.com")
        .sendWelcomeEmail(true)
        .requireEmailVerification(false)
        .isAdmin(true)
        .build());
```

### Boolean Flag Parameters

A boolean parameter often means the method secretly does two different things. That is a violation of "one thing per function" and it is easy to misread at the call site.

```java
// Before: what does "true" mean here? you have to open the method to find out
void printReport(Report report, boolean detailed) {
    if (detailed) {
        printFullReport(report);
    } else {
        printSummary(report);
    }
}
printReport(report, true);
```

```java
// After: split into two clearly named methods
void printDetailedReport(Report report) { ... }
void printSummaryReport(Report report) { ... }

printReport.printDetailedReport(report);
```

If the flag really represents a meaningful choice (not just "two code paths bolted together"), prefer an enum — it is self-documenting and the compiler catches typos that a `boolean` never would.

```java
enum ReportDetail { SUMMARY, DETAILED }

void printReport(Report report, ReportDetail detail) {
    switch (detail) {
        case SUMMARY -> printSummary(report);
        case DETAILED -> printFullReport(report);
    }
}
```

### Avoiding Deep Nesting With Guard Clauses

Deeply nested `if` blocks are hard to follow because the reader has to hold every condition in their head at once. A **guard clause** — an early `return`/`throw` for the exceptional case — flattens the happy path.

```java
// Before: the "real" logic is buried three levels deep
public BigDecimal calculateDiscount(Customer customer, Order order) {
    if (customer != null) {
        if (customer.isActive()) {
            if (order.getTotal().compareTo(BigDecimal.ZERO) > 0) {
                return order.getTotal().multiply(customer.getDiscountRate());
            } else {
                return BigDecimal.ZERO;
            }
        } else {
            return BigDecimal.ZERO;
        }
    } else {
        return BigDecimal.ZERO;
    }
}
```

```java
// After: guard clauses handle the edge cases up front, happy path is flat
public BigDecimal calculateDiscount(Customer customer, Order order) {
    if (customer == null || !customer.isActive()) {
        return BigDecimal.ZERO;
    }
    if (order.getTotal().compareTo(BigDecimal.ZERO) <= 0) {
        return BigDecimal.ZERO;
    }
    return order.getTotal().multiply(customer.getDiscountRate());
}
```

### Comments That Explain Why, Not What

A comment that repeats what the code already says is noise. A useful comment explains a decision the code *cannot* express: why an unusual approach was chosen, a business rule, or a workaround for a bug.

```java
// Before: comment adds nothing the code doesn't already say
// increment i by 1
i++;

// loop through all users
for (User user : users) { ... }
```

```java
// After: the comment carries information the code cannot
// Retry with exponential backoff: the payment provider rate-limits
// bursts and returns 429 without a Retry-After header (see INC-4821).
for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
        return paymentClient.charge(request);
    } catch (RateLimitException e) {
        sleep(backoffMillis(attempt));
    }
}
```

### Dead Code

Dead code — commented-out blocks, unused methods, unreachable branches — costs nothing to write but confuses every future reader, who has to figure out whether it is safe to delete. Version control already remembers old code; delete it instead of commenting it out.

```java
// Before
public int calculateShipping(Order order) {
    // int oldRate = order.getWeight() * 2;
    // return oldRate + BASE_FEE;
    return order.getWeight() * NEW_RATE + BASE_FEE;
}

// After
public int calculateShipping(Order order) {
    return order.getWeight() * NEW_RATE + BASE_FEE;
}
```

### Magic Numbers

A magic number is a literal whose meaning is not obvious from context. Give it a name.

```java
// Before: why 0.075? why 3?
BigDecimal tax = price.multiply(BigDecimal.valueOf(0.075));
if (failedAttempts >= 3) lockAccount();

// After
private static final BigDecimal SALES_TAX_RATE = BigDecimal.valueOf(0.075);
private static final int MAX_LOGIN_ATTEMPTS = 3;

BigDecimal tax = price.multiply(SALES_TAX_RATE);
if (failedAttempts >= MAX_LOGIN_ATTEMPTS) lockAccount();
```

### Consistent Formatting

Consistent formatting (indentation, brace style, import ordering) is not about aesthetics — it removes noise from diffs and code review. A file that mixes tabs and spaces, or reformats unrelated lines in a "fix a typo" PR, hides the real change.

```java
// Before: inconsistent brace style and spacing makes diffs noisy
public void run(){
  if(ready)
  {
      doWork();
  }
    else { skip(); }
}
```

```java
// After: one consistent style (enforced by a formatter like Spotless/google-java-format)
public void run() {
    if (ready) {
        doWork();
    } else {
        skip();
    }
}
```

In real teams this is automated with a formatter and a CI check, not manual discipline — humans are inconsistent, tools are not.

### Small Classes

A class should have a short, focused responsibility. If you cannot describe what a class does in one sentence without using "and," it is probably doing too much.

```java
// Before: one class doing validation, persistence, email, and formatting
class UserManager {
    void validate(User u) { ... }
    void save(User u) { ... }
    void sendWelcomeEmail(User u) { ... }
    String formatForDisplay(User u) { ... }
}
```

```java
// After: each responsibility gets its own small class
class UserValidator { void validate(User u) { ... } }
class UserRepository { void save(User u) { ... } }
class WelcomeEmailSender { void send(User u) { ... } }
class UserFormatter { String formatForDisplay(User u) { ... } }
```

This is exactly the Single Responsibility Principle, covered next.

## SOLID Principles

**SOLID** is an acronym for five design principles that make object-oriented code easier to maintain and extend. Each one has a classic violation and a classic fix.

### Single Responsibility Principle (SRP)

A class should have only one reason to change. If a class mixes unrelated concerns, a change to one concern risks breaking the other.

```java
// Violation: Invoice mixes business data, persistence, and printing
class Invoice {
    private List<LineItem> items;

    BigDecimal calculateTotal() {
        return items.stream().map(LineItem::price).reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    void saveToDatabase() {
        // JDBC code directly inside the domain class
    }

    void printToConsole() {
        System.out.println("Invoice total: " + calculateTotal());
    }
}
```

```java
// Fixed: each class has exactly one reason to change
class Invoice {
    private final List<LineItem> items;

    BigDecimal calculateTotal() {
        return items.stream().map(LineItem::price).reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    List<LineItem> items() { return items; }
}

class InvoiceRepository {
    void save(Invoice invoice) { /* JDBC code lives here */ }
}

class InvoicePrinter {
    void print(Invoice invoice) {
        System.out.println("Invoice total: " + invoice.calculateTotal());
    }
}
```

Now a change to the database schema touches only `InvoiceRepository`; a change to print formatting touches only `InvoicePrinter`. `Invoice` itself only changes when the business rules for an invoice change.

### Open/Closed Principle (OCP)

Classes should be open for extension but closed for modification. You should be able to add new behavior without editing existing, already-tested code.

```java
// Violation: adding a new shape means editing this method every time
class AreaCalculator {
    double area(Object shape) {
        if (shape instanceof Circle c) {
            return Math.PI * c.radius() * c.radius();
        } else if (shape instanceof Rectangle r) {
            return r.width() * r.height();
        }
        // every new shape adds another branch here
        throw new IllegalArgumentException("Unknown shape");
    }
}
```

```java
// Fixed: new shapes extend the abstraction, AreaCalculator never changes
interface Shape {
    double area();
}

record Circle(double radius) implements Shape {
    public double area() { return Math.PI * radius * radius; }
}

record Rectangle(double width, double height) implements Shape {
    public double area() { return width * height; }
}

class AreaCalculator {
    double area(Shape shape) {
        return shape.area();
    }
}

// Adding Triangle later requires zero changes to AreaCalculator:
record Triangle(double base, double height) implements Shape {
    public double area() { return 0.5 * base * height; }
}
```

Java 21's sealed interfaces give you the best of both worlds when you *do* want the compiler to force you to handle every case (e.g. exhaustive `switch`), which is useful when the set of variants is meant to be closed rather than open — see the Design Patterns section for a Visitor-style example.

### Liskov Substitution Principle (LSP)

Subtypes must be usable anywhere their supertype is expected, without breaking the caller's assumptions. The classic textbook violation is `Square extends Rectangle`.

```java
// Violation: Square breaks the Rectangle contract (setWidth no longer only
// changes width, it silently changes height too)
class Rectangle {
    protected int width, height;
    void setWidth(int w) { this.width = w; }
    void setHeight(int h) { this.height = h; }
    int area() { return width * height; }
}

class Square extends Rectangle {
    @Override void setWidth(int w) { width = w; height = w; }
    @Override void setHeight(int h) { width = h; height = h; }
}

void resize(Rectangle r) {
    r.setWidth(5);
    r.setHeight(10);
    assert r.area() == 50; // fails for Square! area() is 100
}
```

```java
// Fixed: don't force an is-a relationship that doesn't hold behaviorally.
// Model the shared behavior with a common interface instead of inheritance.
interface Shape {
    int area();
}

final class Rectangle implements Shape {
    private final int width, height;
    Rectangle(int width, int height) { this.width = width; this.height = height; }
    public int area() { return width * height; }
}

final class Square implements Shape {
    private final int side;
    Square(int side) { this.side = side; }
    public int area() { return side * side; }
}
```

`Square` is no longer forced to honor a mutable-rectangle contract it cannot actually satisfy. If you must keep an inheritance hierarchy, the rule is: a subclass must not weaken postconditions, strengthen preconditions, or throw new exceptions the caller of the base type does not expect.

### Interface Segregation Principle (ISP)

Clients should not be forced to depend on methods they do not use. Large, "fat" interfaces force implementers to write dummy/unsupported methods.

```java
// Violation: a read-only report can't sanely implement print()
interface Machine {
    void print(Document d);
    void scan(Document d);
    void fax(Document d);
}

class OldPrinter implements Machine {
    public void print(Document d) { /* ok */ }
    public void scan(Document d) { throw new UnsupportedOperationException(); }
    public void fax(Document d) { throw new UnsupportedOperationException(); }
}
```

```java
// Fixed: split into small, focused interfaces
interface Printer { void print(Document d); }
interface Scanner { void scan(Document d); }
interface Fax { void fax(Document d); }

class OldPrinter implements Printer {
    public void print(Document d) { /* ok */ }
}

class AllInOnePrinter implements Printer, Scanner, Fax {
    public void print(Document d) { ... }
    public void scan(Document d) { ... }
    public void fax(Document d) { ... }
}
```

`OldPrinter` now implements exactly what it can support. No dummy methods, no `UnsupportedOperationException` traps for callers.

### Dependency Inversion Principle (DIP)

High-level code should depend on abstractions, not on concrete low-level implementations. This is what makes classes testable and swappable.

```java
// Violation: OrderService is hard-wired to a concrete SMTP sender.
// You cannot test it without sending real emails, and you cannot swap
// providers without editing OrderService.
class SmtpEmailSender {
    void send(String to, String body) { /* talks to an SMTP server */ }
}

class OrderService {
    private final SmtpEmailSender emailSender = new SmtpEmailSender();

    void placeOrder(Order order) {
        // ... business logic ...
        emailSender.send(order.getCustomerEmail(), "Order confirmed");
    }
}
```

```java
// Fixed: OrderService depends on an interface; the concrete
// implementation is injected from outside (constructor injection).
interface NotificationSender {
    void send(String to, String body);
}

class SmtpEmailSender implements NotificationSender {
    public void send(String to, String body) { /* SMTP */ }
}

class SmsSender implements NotificationSender {
    public void send(String to, String body) { /* SMS gateway */ }
}

class OrderService {
    private final NotificationSender notificationSender;

    OrderService(NotificationSender notificationSender) {
        this.notificationSender = notificationSender;
    }

    void placeOrder(Order order) {
        // ... business logic ...
        notificationSender.send(order.getCustomerEmail(), "Order confirmed");
    }
}

// In tests:
OrderService service = new OrderService(new NotificationSender() {
    public void send(String to, String body) { /* record call, assert on it */ }
});
```

`OrderService` no longer knows or cares whether emails are sent over SMTP, SMS, or a mock in a test. This is the foundation dependency-injection frameworks (Spring, Guice, CDI) are built on.

## Effective Java Best Practices

Joshua Bloch's *Effective Java* is the most-quoted Java book in code review. Below are roughly twenty of its highest-value items, each with a one-paragraph explanation and a short example.

### 1. Prefer static factory methods over constructors

A static factory method can have a descriptive name, is not required to create a new object every call, and can return a subtype. A constructor always has the same name as the class and always returns exactly that type.

```java
class Connection {
    private Connection() {}

    // Name explains intent; a constructor could not do this.
    public static Connection newTcpConnection(String host, int port) { ... return new Connection(); }
    public static Connection newInMemoryConnection() { ... return new Connection(); }
}
```

### 2. Use a builder when a constructor would need many parameters

Telescoping constructors (many overloads with increasing parameter counts) are error-prone and hard to read at the call site. A builder makes each parameter self-documenting.

```java
class Pizza {
    private final int size;
    private final boolean cheese;
    private final boolean pepperoni;

    private Pizza(Builder b) {
        this.size = b.size; this.cheese = b.cheese; this.pepperoni = b.pepperoni;
    }

    static class Builder {
        private final int size;
        private boolean cheese;
        private boolean pepperoni;

        Builder(int size) { this.size = size; }
        Builder cheese(boolean value) { this.cheese = value; return this; }
        Builder pepperoni(boolean value) { this.pepperoni = value; return this; }
        Pizza build() { return new Pizza(this); }
    }
}

Pizza pizza = new Pizza.Builder(12).cheese(true).pepperoni(true).build();
```

### 3. Use a single-element enum for singletons

An enum singleton gets serialization safety and reflection-attack resistance for free, which a hand-rolled singleton does not.

```java
enum AppConfig {
    INSTANCE;
    private final Properties props = loadProperties();
    Properties properties() { return props; }
}

AppConfig.INSTANCE.properties();
```

### 4. Prefer dependency injection over hardwiring dependencies

Hardwiring a concrete dependency (`new SmtpEmailSender()`) inside a class, as shown in the DIP example above, makes that class impossible to unit test in isolation. Pass dependencies into the constructor instead.

```java
// Hardwired — cannot substitute a fake in tests
class Report { private final Formatter formatter = new PdfFormatter(); }

// Injected — testable and swappable
class Report {
    private final Formatter formatter;
    Report(Formatter formatter) { this.formatter = formatter; }
}
```

### 5. Avoid finalizers and the old `Object.finalize()`; use `Cleaner` or try-with-resources

Finalizers run at an unpredictable time (or never), can hurt performance, and can even resurrect objects. Try-with-resources with `AutoCloseable` is the correct default; `java.lang.ref.Cleaner` is the safety-net alternative when you need last-resort cleanup.

```java
class FileHandle implements AutoCloseable {
    private final RandomAccessFile file;
    FileHandle(String path) throws IOException { file = new RandomAccessFile(path, "r"); }
    public void close() throws IOException { file.close(); }
}

try (FileHandle handle = new FileHandle("data.bin")) {
    // use handle
} // closed deterministically, no finalizer needed
```

### 6. Obey the `equals`/`hashCode` contract

If you override `equals`, you must override `hashCode` so that equal objects have equal hash codes — otherwise the object breaks in `HashMap`/`HashSet`. Since Java 16, a `record` generates both correctly for you.

```java
// Manual, error-prone version
class Point {
    final int x, y;
    Point(int x, int y) { this.x = x; this.y = y; }
    @Override public boolean equals(Object o) {
        return o instanceof Point p && p.x == x && p.y == y;
    }
    @Override public int hashCode() { return Objects.hash(x, y); }
}

// Modern version: equals/hashCode/toString generated correctly, always
record Point(int x, int y) {}
```

### 7. Minimize mutability

Immutable objects are simpler to reason about, are automatically thread-safe, and can be freely shared. Make fields `final`, do not provide mutators, and ensure the class cannot be extended in a way that breaks invariants.

```java
// Mutable — a caller can corrupt shared state
class Money {
    private BigDecimal amount;
    void setAmount(BigDecimal amount) { this.amount = amount; }
}

// Immutable — every "change" returns a new instance
final class Money {
    private final BigDecimal amount;
    Money(BigDecimal amount) { this.amount = amount; }
    Money plus(Money other) { return new Money(this.amount.add(other.amount)); }
}
```

### 8. Favor composition over inheritance

Inheritance couples a subclass to the internal implementation details of its superclass; a change in the superclass can silently break subclasses ("fragile base class" problem). Composition — holding a reference to another object and delegating to it — is more flexible.

```java
// Inheritance: ArrayList's internals leak through; size() may not
// behave as expected if addAll() is implemented in terms of add()
class InstrumentedSet<E> extends HashSet<E> {
    int addCount = 0;
    @Override public boolean add(E e) { addCount++; return super.add(e); }
    @Override public boolean addAll(Collection<? extends E> c) {
        addCount += c.size();
        return super.addAll(c); // may double-count if addAll calls add() internally
    }
}

// Composition: wrap a Set, forward calls, no fragile-base-class risk
class InstrumentedSet<E> {
    private final Set<E> delegate;
    int addCount = 0;
    InstrumentedSet(Set<E> delegate) { this.delegate = delegate; }
    boolean add(E e) { addCount++; return delegate.add(e); }
    boolean addAll(Collection<? extends E> c) { addCount += c.size(); return delegate.addAll(c); }
}
```

### 9. Design and document for inheritance, or prohibit it

If a class is not explicitly designed to be extended (documented "self-use" behavior, no calls from constructors to overridable methods), mark it `final` or make its constructors package-private. Otherwise you invite the fragile-base-class problems from item 8.

```java
// Dangerous: superclass constructor calls an overridable method;
// subclass fields are not yet initialized when overridden() runs
class Base {
    Base() { overridden(); }
    void overridden() {}
}

// Safe: prevent inheritance entirely when it wasn't designed for it
final class Utility {
    private Utility() {}
    static int square(int x) { return x * x; }
}
```

### 10. Prefer interfaces to abstract classes for defining types

A class can implement multiple interfaces but extend only one abstract class. Interfaces (with default methods) usually give you enough shared behavior without giving up multiple inheritance of type.

```java
interface Flyer { default void takeOff() { System.out.println("Taking off"); } }
interface Swimmer { default void dive() { System.out.println("Diving"); } }

// A Duck can be both — impossible with two abstract base classes
class Duck implements Flyer, Swimmer {}
```

### 11. Prefer lists to arrays

Arrays are covariant and not truly type-safe at runtime (`ArrayStoreException`), and they don't play well with generics. Prefer `List<T>`.

```java
// Arrays: compiles, fails at runtime
Object[] objects = new String[1];
objects[0] = 42; // throws ArrayStoreException at runtime

// Lists: caught at compile time instead
List<String> strings = new ArrayList<>();
// strings.add(42); // does not compile
```

### 12. Favor generics, and use bounded wildcards to increase API flexibility

Generics give compile-time type safety instead of runtime `ClassCastException`. Bounded wildcards (`? extends`, `? super`) let an API accept a wider range of argument types (the "PECS" rule: **P**roducer **E**xtends, **C**onsumer **S**uper).

```java
// Without wildcards: only List<Number> works, List<Integer> is rejected
void printAll(List<Number> list) { list.forEach(System.out::println); }

// With a bounded wildcard: any subtype of Number works too (producer -> extends)
void printAll(List<? extends Number> list) { list.forEach(System.out::println); }

// Consumer -> super: this can accept a destination for any supertype of Integer
void addIntegers(List<? super Integer> destination) { destination.add(1); destination.add(2); }
```

### 13. Prefer enums to `int` constants

`int` constants ("int enum pattern") give no type safety and no useful `toString()`. Enums are type-checked, printable, and can carry behavior.

```java
// int constants — nothing stops you from passing the wrong "kind" of int
class Season { static final int SPRING = 0, SUMMER = 1, FALL = 2, WINTER = 3; }
void plant(int season) { ... }
plant(Season.SUMMER); // fine
plant(42);            // compiles, wrong at runtime

// enum — type-checked, self-documenting, switch-friendly
enum Season { SPRING, SUMMER, FALL, WINTER }
void plant(Season season) { ... }
```

### 14. Return empty collections or arrays, never `null`

Returning `null` for "no results" forces every caller to null-check, and someone eventually forgets and gets a `NullPointerException`. Return an empty collection instead.

```java
// Before: every caller must remember to null-check
List<Order> findOrders(String customerId) {
    return orders.isEmpty() ? null : orders;
}

// After: caller can always safely iterate
List<Order> findOrders(String customerId) {
    return orders.isEmpty() ? Collections.emptyList() : orders;
}
```

### 15. Use `Optional` judiciously — mainly as a return type, not everywhere

`Optional<T>` is great for a method's return type when "no result" is a normal, expected outcome. It is a poor fit for fields, method parameters, or collection elements — those usages just add wrapping overhead without benefit.

```java
// Good use: return type communicates "might be absent"
Optional<User> findByEmail(String email) { ... }

Optional<User> user = findByEmail("a@example.com");
user.ifPresentOrElse(this::greet, () -> log.warn("not found"));

// Bad use: Optional as a field or parameter just adds ceremony
class Order {
    private Optional<String> couponCode; // avoid — use null or a default value/empty string instead
}
```

### 16. Minimize the scope of local variables

Declare a variable as close as possible to where it is first used, and in the narrowest block possible. This shrinks the window in which a reader has to track its value.

```java
// Before: i is declared far from where the loop actually needs it
int i;
// ... 20 lines of unrelated code ...
for (i = 0; i < items.size(); i++) { process(items.get(i)); }

// After: declared right where it's needed, scoped to the loop
for (int i = 0; i < items.size(); i++) { process(items.get(i)); }
```

### 17. Prefer `for-each` loops to traditional index-based `for` loops

`for-each` removes the off-by-one and index-mutation bugs that plague manual index loops, and it works uniformly across arrays, lists, and any `Iterable`.

```java
// Index-based: easy to get the bound wrong
for (int i = 0; i <= items.size(); i++) { // bug: should be <
    process(items.get(i));
}

// for-each: no index to get wrong
for (Item item : items) {
    process(item);
}
```

### 18. Beware the performance cost of the `String` concatenation `+` operator in loops

Each `+` on strings inside a loop creates a new `String` object, giving O(n²) behavior for n concatenations. Use `StringBuilder` for loops.

```java
// Before: O(n^2) — a new String is allocated on every iteration
String result = "";
for (String word : words) {
    result += word + " ";
}

// After: O(n) — one growable buffer
StringBuilder sb = new StringBuilder();
for (String word : words) {
    sb.append(word).append(' ');
}
String result = sb.toString();
```

### 19. Refer to objects by their interface, not their implementation type

Declaring variables and parameters using the interface type (`List`, `Map`, `Set`) instead of the concrete class (`ArrayList`, `HashMap`) makes it trivial to swap implementations later.

```java
// Before: tied to ArrayList everywhere
ArrayList<String> names = new ArrayList<>();
void printNames(ArrayList<String> names) { ... }

// After: depends only on the List contract
List<String> names = new ArrayList<>();
void printNames(List<String> names) { ... }
// Switching to LinkedList later requires changing only one line
```

### 20. Prefer primitives to boxed primitives when you don't need `null`

Boxed types (`Integer`, `Long`, `Double`) allow `null`, incur autoboxing overhead, and `==` compares references (not values) once outside the small cached integer range — a classic bug source. Use primitives unless you specifically need nullability or generics.

```java
// Boxed: subtle == bug and unnecessary allocation
Integer a = 1000;
Integer b = 1000;
System.out.println(a == b); // false! outside the -128..127 cache range

// Primitive: no boxing, no reference-equality trap
int a = 1000;
int b = 1000;
System.out.println(a == b); // true, compares values
```

### 21. Document thread safety

A class's Javadoc should state whether it is thread-safe, conditionally thread-safe, or not thread-safe at all. Without this, callers either over-synchronize (hurting performance) or under-synchronize (causing bugs).

```java
/**
 * Thread-safe: all mutating methods are synchronized internally.
 * Callers do not need external locking.
 */
class Counter {
    private int count;
    synchronized void increment() { count++; }
    synchronized int get() { return count; }
}

/**
 * Not thread-safe. Callers must provide external synchronization
 * if this instance is shared across threads.
 */
class Buffer { ... }
```

### 22. Prefer executors and the `java.util.concurrent` framework to raw threads

Creating raw `Thread` objects by hand gives you no lifecycle management, no bounded resource usage, and no easy way to wait for results. `ExecutorService` (and Java 21's virtual threads) handle pooling, scheduling, and shutdown correctly.

```java
// Before: unmanaged raw thread, no pooling, fire-and-forget
new Thread(() -> processOrder(order)).start();

// After: managed executor, bounded resources, results and errors handled
try (ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor()) {
    Future<Receipt> future = executor.submit(() -> processOrder(order));
    Receipt receipt = future.get();
}
```

## Design Patterns in Java

A **design pattern** is a proven, reusable solution to a common design problem. Patterns are grouped into three families: **creational** (object creation), **structural** (composing classes/objects), and **behavioral** (communication between objects).

| Pattern | Family | When to use | Modern Java alternative |
|---|---|---|---|
| Builder | Creational | Many constructor parameters, optional fields | Record with a builder, or named factory methods |
| Factory Method | Creational | Subclass decides which concrete type to create | Static factory methods (item 1 above) |
| Abstract Factory | Creational | Create families of related objects | Dependency injection container |
| Singleton | Creational | Exactly one shared instance | Enum singleton, or DI-managed scope |
| Strategy | Behavioral | Swap an algorithm at runtime | Lambda / method reference implementing a functional interface |
| Template Method | Behavioral | Fixed skeleton, customizable steps | Default methods on an interface, or a `Consumer`/`Function` parameter |
| Observer | Behavioral | Notify many listeners of a change | `java.util.function` callbacks, event bus, `Flow`/reactive streams |
| Decorator | Structural | Add behavior without changing the class | Wrapping with lambdas, `Stream` pipelines |
| Adapter | Structural | Make an incompatible interface fit | A thin wrapper class or method reference |
| Facade | Structural | Simplify a complex subsystem behind one API | A service class exposing a narrow API |
| Proxy | Structural | Control access (lazy load, security, remote) | Dynamic proxies, AOP frameworks (Spring AOP) |
| Command | Behavioral | Encapsulate a request as an object | `Runnable`/`Callable` lambdas |
| Chain of Responsibility | Behavioral | Pass a request along a chain of handlers | `Stream` of handlers, servlet filter chains |
| Iterator | Behavioral | Traverse a collection without exposing internals | Built into `Iterable`, enhanced for-each |
| State | Behavioral | Behavior changes with internal state | Sealed interface + `switch` pattern matching |
| Visitor | Behavioral | Operate over a closed set of types | Sealed interface + exhaustive `switch` pattern matching |

### Builder

```java
class Computer {
    private final String cpu;
    private final int ramGb;
    private final boolean hasSsd;

    private Computer(Builder b) {
        this.cpu = b.cpu; this.ramGb = b.ramGb; this.hasSsd = b.hasSsd;
    }

    static class Builder {
        private String cpu = "generic-cpu";
        private int ramGb = 8;
        private boolean hasSsd = true;

        Builder cpu(String cpu) { this.cpu = cpu; return this; }
        Builder ramGb(int ramGb) { this.ramGb = ramGb; return this; }
        Builder hasSsd(boolean hasSsd) { this.hasSsd = hasSsd; return this; }
        Computer build() { return new Computer(this); }
    }
}

Computer gamingPc = new Computer.Builder().cpu("i9").ramGb(32).hasSsd(true).build();
```

### Factory Method

```java
interface Notification {
    void send(String message);
}

class EmailNotification implements Notification {
    public void send(String message) { System.out.println("Email: " + message); }
}

class SmsNotification implements Notification {
    public void send(String message) { System.out.println("SMS: " + message); }
}

class NotificationFactory {
    static Notification create(String type) {
        return switch (type) {
            case "email" -> new EmailNotification();
            case "sms" -> new SmsNotification();
            default -> throw new IllegalArgumentException("Unknown type: " + type);
        };
    }
}

Notification n = NotificationFactory.create("email");
n.send("Your order has shipped");
```

### Abstract Factory

```java
interface Button { void render(); }
interface Checkbox { void render(); }

interface UiFactory {
    Button createButton();
    Checkbox createCheckbox();
}

class DarkThemeFactory implements UiFactory {
    public Button createButton() { return () -> System.out.println("Dark button"); }
    public Checkbox createCheckbox() { return () -> System.out.println("Dark checkbox"); }
}

class LightThemeFactory implements UiFactory {
    public Button createButton() { return () -> System.out.println("Light button"); }
    public Checkbox createCheckbox() { return () -> System.out.println("Light checkbox"); }
}

// Note: Button/Checkbox declared as functional interfaces here for brevity via lambdas
void renderUi(UiFactory factory) {
    factory.createButton().render();
    factory.createCheckbox().render();
}

renderUi(new DarkThemeFactory()); // switching the whole family is one line
```

### Singleton

```java
// Classic non-enum singleton (shown for comparison — avoid in new code)
class LegacyConfig {
    private static LegacyConfig instance;
    private LegacyConfig() {}
    static synchronized LegacyConfig getInstance() {
        if (instance == null) instance = new LegacyConfig();
        return instance;
    }
}

// Modern replacement: enum singleton — serialization-safe, thread-safe for free
enum AppConfig {
    INSTANCE;
    private final Map<String, String> settings = new ConcurrentHashMap<>();
    String get(String key) { return settings.get(key); }
}

AppConfig.INSTANCE.get("timeout");
```

### Strategy

```java
interface DiscountStrategy {
    BigDecimal apply(BigDecimal price);
}

class PercentageDiscount implements DiscountStrategy {
    private final BigDecimal percent;
    PercentageDiscount(BigDecimal percent) { this.percent = percent; }
    public BigDecimal apply(BigDecimal price) { return price.subtract(price.multiply(percent)); }
}

class Checkout {
    private final DiscountStrategy strategy;
    Checkout(DiscountStrategy strategy) { this.strategy = strategy; }
    BigDecimal total(BigDecimal price) { return strategy.apply(price); }
}

// Classic: a class implementing the interface
Checkout classic = new Checkout(new PercentageDiscount(BigDecimal.valueOf(0.10)));

// Modern replacement: DiscountStrategy is a functional interface, so a lambda works directly
Checkout modern = new Checkout(price -> price.subtract(price.multiply(BigDecimal.valueOf(0.10))));
```

### Template Method

```java
abstract class DataExporter {
    // Template method: fixed skeleton
    final void export() {
        openConnection();
        writeHeader();
        writeRows();
        closeConnection();
    }
    void openConnection() { System.out.println("Opening"); }
    void closeConnection() { System.out.println("Closing"); }
    abstract void writeHeader();
    abstract void writeRows();
}

class CsvExporter extends DataExporter {
    void writeHeader() { System.out.println("id,name"); }
    void writeRows() { System.out.println("1,Ana"); }
}

// Modern alternative for simple cases: pass the customizable steps as lambdas
void export(Runnable writeHeader, Runnable writeRows) {
    System.out.println("Opening");
    writeHeader.run();
    writeRows.run();
    System.out.println("Closing");
}
export(() -> System.out.println("id,name"), () -> System.out.println("1,Ana"));
```

### Observer

```java
interface OrderListener {
    void onOrderPlaced(Order order);
}

class OrderService {
    private final List<OrderListener> listeners = new ArrayList<>();
    void addListener(OrderListener listener) { listeners.add(listener); }
    void placeOrder(Order order) {
        // ... business logic ...
        listeners.forEach(l -> l.onOrderPlaced(order));
    }
}

// Modern replacement: OrderListener is a functional interface, use lambdas directly
OrderService service = new OrderService();
service.addListener(order -> System.out.println("Send confirmation email for " + order));
service.addListener(order -> System.out.println("Update analytics for " + order));
```

### Decorator

```java
interface Coffee {
    BigDecimal cost();
    String description();
}

class SimpleCoffee implements Coffee {
    public BigDecimal cost() { return BigDecimal.valueOf(2.0); }
    public String description() { return "Coffee"; }
}

abstract class CoffeeDecorator implements Coffee {
    protected final Coffee delegate;
    CoffeeDecorator(Coffee delegate) { this.delegate = delegate; }
}

class MilkDecorator extends CoffeeDecorator {
    MilkDecorator(Coffee delegate) { super(delegate); }
    public BigDecimal cost() { return delegate.cost().add(BigDecimal.valueOf(0.5)); }
    public String description() { return delegate.description() + " + Milk"; }
}

Coffee order = new MilkDecorator(new SimpleCoffee());
System.out.println(order.description() + ": $" + order.cost()); // Coffee + Milk: $2.5
```

### Adapter

```java
// Third-party class we cannot change, with an incompatible interface
class LegacyXmlParser {
    String parseXml(String xml) { return "parsed:" + xml; }
}

// The interface our code actually wants to use
interface JsonParser {
    String parseJson(String json);
}

// Adapter bridges the two
class XmlToJsonAdapter implements JsonParser {
    private final LegacyXmlParser legacy = new LegacyXmlParser();
    public String parseJson(String json) {
        String xmlEquivalent = convertJsonToXml(json);
        return legacy.parseXml(xmlEquivalent);
    }
    private String convertJsonToXml(String json) { return "<xml>" + json + "</xml>"; }
}
```

### Facade

```java
// A complex subsystem with many moving parts
class InventoryService { boolean reserve(String sku, int qty) { return true; } }
class PaymentService { void charge(String customerId, BigDecimal amount) {} }
class ShippingService { void schedule(String orderId) {} }

// Facade: one simple entry point that hides the subsystem's complexity
class OrderFacade {
    private final InventoryService inventory = new InventoryService();
    private final PaymentService payment = new PaymentService();
    private final ShippingService shipping = new ShippingService();

    void placeOrder(String orderId, String sku, int qty, String customerId, BigDecimal amount) {
        inventory.reserve(sku, qty);
        payment.charge(customerId, amount);
        shipping.schedule(orderId);
    }
}

new OrderFacade().placeOrder("O-1", "SKU-42", 1, "C-9", BigDecimal.valueOf(29.99));
```

### Proxy

```java
interface ImageLoader {
    void display();
}

class RealImage implements ImageLoader {
    private final String path;
    RealImage(String path) { this.path = path; loadFromDisk(); }
    private void loadFromDisk() { System.out.println("Loading " + path); }
    public void display() { System.out.println("Displaying " + path); }
}

// Proxy: defers the expensive load until display() is actually called
class LazyImageProxy implements ImageLoader {
    private final String path;
    private RealImage real;
    LazyImageProxy(String path) { this.path = path; }
    public void display() {
        if (real == null) {
            real = new RealImage(path); // loaded only on first use
        }
        real.display();
    }
}
```

### Command

```java
interface Command {
    void execute();
}

class LightOnCommand implements Command {
    public void execute() { System.out.println("Light on"); }
}

class RemoteControl {
    private final List<Command> history = new ArrayList<>();
    void submit(Command command) {
        command.execute();
        history.add(command);
    }
}

// Classic: a class implementing Command
new RemoteControl().submit(new LightOnCommand());

// Modern replacement: Command is a functional interface, lambda works directly
new RemoteControl().submit(() -> System.out.println("Light on"));
```

### Chain of Responsibility

```java
abstract class SupportHandler {
    protected SupportHandler next;
    SupportHandler setNext(SupportHandler next) { this.next = next; return next; }
    abstract void handle(String issue);
}

class Tier1Support extends SupportHandler {
    void handle(String issue) {
        if (issue.equals("password-reset")) {
            System.out.println("Tier1 handled: " + issue);
        } else if (next != null) {
            next.handle(issue);
        }
    }
}

class Tier2Support extends SupportHandler {
    void handle(String issue) {
        System.out.println("Tier2 handled: " + issue);
    }
}

SupportHandler chain = new Tier1Support();
chain.setNext(new Tier2Support());
chain.handle("database-outage"); // falls through to Tier2Support
```

### Iterator

```java
class NameCollection implements Iterable<String> {
    private final String[] names;
    NameCollection(String... names) { this.names = names; }

    public Iterator<String> iterator() {
        return new Iterator<>() {
            private int index = 0;
            public boolean hasNext() { return index < names.length; }
            public String next() { return names[index++]; }
        };
    }
}

for (String name : new NameCollection("Ana", "Bo", "Cy")) {
    System.out.println(name); // for-each works because we implement Iterable
}
```

### State

```java
// Modern approach: sealed interface + exhaustive switch pattern matching
sealed interface OrderState permits Placed, Shipped, Delivered {}
record Placed() implements OrderState {}
record Shipped(String trackingNumber) implements OrderState {}
record Delivered(LocalDate date) implements OrderState {}

String describe(OrderState state) {
    return switch (state) {
        case Placed p -> "Order placed";
        case Shipped s -> "Shipped, tracking: " + s.trackingNumber();
        case Delivered d -> "Delivered on " + d.date();
        // no default needed — the compiler knows these are the only three states
    };
}
```

### Visitor — Replaced by Sealed Interfaces + Pattern Matching

The classic Visitor pattern exists to let you add new *operations* over a fixed set of types without editing those types (double-dispatch through `accept`/`visit` methods). In modern Java, if the set of types is genuinely closed, a sealed hierarchy plus an exhaustive `switch` achieves the same goal with far less boilerplate — and the compiler enforces exhaustiveness for you.

```java
sealed interface Shape permits Circle, Square, Triangle {}
record Circle(double radius) implements Shape {}
record Square(double side) implements Shape {}
record Triangle(double base, double height) implements Shape {}

double area(Shape shape) {
    return switch (shape) {
        case Circle c -> Math.PI * c.radius() * c.radius();
        case Square s -> s.side() * s.side();
        case Triangle t -> 0.5 * t.base() * t.height();
    };
}

// Adding a new operation (e.g. perimeter) is just a new function with its own switch —
// no accept()/visit() plumbing needed on Shape itself.
```

## API Design Best Practices

Designing a public API — a library, a service interface, a set of public classes — is different from writing internal code: once other people depend on it, every change is a potential breaking change.

### Naming

Follow existing JDK conventions so your API feels familiar: `getX`/`isX` for accessors, verbs for actions, plural nouns for collections.

```java
// Inconsistent, surprising names
class UserApi {
    User fetchUser(String id) { ... }      // getUser would match convention
    boolean userActive(String id) { ... }  // isUserActive reads as a question
    List<User> userList() { ... }          // users() is more idiomatic
}

// Convention-following names
class UserApi {
    User getUser(String id) { ... }
    boolean isUserActive(String id) { ... }
    List<User> users() { ... }
}
```

### Minimal Surface Area

Every public method, class, and field is a promise to callers. Expose only what is needed; keep everything else package-private or private. A smaller API is easier to learn, test, and evolve.

```java
// Before: implementation details leak into the public API
public class OrderProcessor {
    public void validateOrder(Order order) { ... }
    public void calculateTax(Order order) { ... }
    public void chargeCard(Order order) { ... }
    public void process(Order order) {
        validateOrder(order); calculateTax(order); chargeCard(order);
    }
}

// After: only the one operation callers need is public
public class OrderProcessor {
    public void process(Order order) {
        validateOrder(order); calculateTax(order); chargeCard(order);
    }
    private void validateOrder(Order order) { ... }
    private void calculateTax(Order order) { ... }
    private void chargeCard(Order order) { ... }
}
```

### Immutability by Default

Return types and parameters exposed by a public API should default to immutable unless there is a specific reason for mutability. This prevents a caller from mutating internal state through a returned reference.

```java
// Before: caller can mutate the list backing your internal state
public class Team {
    private final List<String> members = new ArrayList<>();
    public List<String> getMembers() { return members; } // leaks a mutable reference
}

// After: return an immutable view/copy
public class Team {
    private final List<String> members = new ArrayList<>();
    public List<String> getMembers() { return List.copyOf(members); }
}
```

### Nulls at Boundaries

Public API boundaries should be explicit about null: either forbid it (and fail fast) or explicitly document that it is allowed. Silent acceptance of `null` pushes the bug downstream to a confusing `NullPointerException` far from the real cause.

```java
public void registerUser(String email, String referralCode) {
    Objects.requireNonNull(email, "email must not be null");
    // referralCode is allowed to be null — document it clearly
    // referralCode: nullable, meaning "no referral"
    ...
}
```

### `Optional` for Returns Only

As in the Effective Java section, restrict `Optional` to return types. Do not accept `Optional` as a method parameter in a public API — it forces callers to wrap values just to call your method, and it is not `Serializable`.

```java
// Avoid: forces every caller to wrap a value in Optional just to call this
public void setDiscount(Optional<BigDecimal> discount) { ... }

// Prefer: an overload, or a sentinel/nullable value with a nullability contract
public void setDiscount(BigDecimal discount) { ... } // null = no discount, documented
public Optional<BigDecimal> getDiscount() { ... }     // Optional is fine as a return type
```

### Parameter Validation and Fail Fast

Validate arguments at the top of a public method and throw immediately with a clear message. Failing fast at the boundary is far easier to debug than a mysterious failure deep inside the call stack.

```java
public BigDecimal calculateInterest(BigDecimal principal, double rate, int years) {
    if (principal.signum() < 0) {
        throw new IllegalArgumentException("principal must not be negative: " + principal);
    }
    if (years < 0) {
        throw new IllegalArgumentException("years must not be negative: " + years);
    }
    return principal.multiply(BigDecimal.valueOf(rate * years));
}
```

### Overload Sparingly

Too many overloads confuse callers about which one gets called, especially with autoboxing, varargs, or null arguments. Prefer distinct, clearly named methods when behavior meaningfully differs.

```java
// Confusing: which overload does log(null) call? Ambiguous / surprising.
void log(String message) { ... }
void log(String message, Throwable t) { ... }
void log(Object message) { ... }

// Clearer: distinct names remove all ambiguity
void logMessage(String message) { ... }
void logError(String message, Throwable t) { ... }
```

### Exception Contracts

Document exactly which exceptions a public method can throw, and keep that contract stable — it is part of your API. Prefer specific, well-named exceptions over generic ones (see Exception Handling chapter for full details).

```java
/**
 * @throws UserNotFoundException if no user exists with the given id
 * @throws IllegalArgumentException if id is blank
 */
public User getUser(String id) throws UserNotFoundException { ... }
```

### Backwards Compatibility: Binary vs Source Compatibility

**Source compatible** means existing client code still *compiles* against the new version. **Binary compatible** means existing *compiled* `.class` files still run against the new version without recompiling. Adding a new overload is usually both; removing a public method breaks both; changing a method's return type (even to a subtype) can break binary compatibility even though source may still compile.

```java
// Adding an overload: source and binary compatible — old callers unaffected
public void save(Order order) { ... }
public void save(Order order, boolean async) { ... } // new, additive

// Removing/renaming a public method: breaks both source and binary compatibility
// public void save(Order order) { ... }  // deleting this breaks every existing caller
```

### `@Deprecated(since, forRemoval)`

Deprecate before removing. `since` documents when the replacement became available; `forRemoval = true` tells consumers this really is going away, not just "prefer the alternative."

```java
/**
 * @deprecated use {@link #save(Order, boolean)} instead.
 */
@Deprecated(since = "2.3", forRemoval = true)
public void save(Order order) {
    save(order, false);
}

public void save(Order order, boolean async) { ... }
```

### Versioning

Follow semantic versioning (`MAJOR.MINOR.PATCH`): increment `MAJOR` for breaking changes, `MINOR` for backwards-compatible additions, `PATCH` for backwards-compatible bug fixes. This lets consumers set dependency ranges with confidence.

```
1.4.2 -> 1.5.0   // new feature, backwards compatible (MINOR bump)
1.5.0 -> 1.5.1   // bug fix only (PATCH bump)
1.5.1 -> 2.0.0   // removed a deprecated method (MAJOR bump, breaking)
```

### Builders for Wide Constructors

As covered in Clean Code and Effective Java, expose a builder (or static factory with named parameters via a record) instead of a constructor with many parameters — this is doubly important in a public API, where the constructor signature becomes a long-term commitment.

```java
public class HttpRequestConfig {
    // A public constructor with 8 parameters would be locked in forever.
    // A builder can add optional settings later without breaking callers.
    public static Builder builder() { return new Builder(); }

    public static final class Builder {
        private Duration timeout = Duration.ofSeconds(30);
        private int maxRetries = 3;
        public Builder timeout(Duration timeout) { this.timeout = timeout; return this; }
        public Builder maxRetries(int maxRetries) { this.maxRetries = maxRetries; return this; }
        public HttpRequestConfig build() { return new HttpRequestConfig(this); }
    }
    // private fields/constructor omitted for brevity
}
```

### Documenting Thread Safety and Nullability

Every public class and method should document whether it is thread-safe and whether parameters/return values may be `null`. This is not optional polish — it directly determines how a caller is allowed to use the API.

```java
/**
 * Thread-safe cache. Safe to share a single instance across threads.
 *
 * @param key must not be null
 * @return the cached value, or {@code null} if absent (this method never throws for a cache miss)
 */
public V get(K key) { ... }
```

## Common Code Smells

A **code smell** is a surface signal that deeper design trouble might be lurking — not a bug by itself, but a warning sign worth investigating.

| Smell | Why it hurts | Refactoring |
|---|---|---|
| Long method | Hard to understand, test, and reuse pieces of | Extract method, guard clauses |
| Large class / God object | Too many responsibilities, high change risk | Split by responsibility (SRP) |
| Primitive obsession | Loses domain meaning and validation | Introduce small value types / records |
| Feature envy | A method cares more about another class's data than its own | Move the method to the class it envies |
| Data clumps | The same group of fields/params travels together everywhere | Extract a parameter object |
| Shotgun surgery | One logical change requires edits across many classes | Consolidate the responsibility into one place |
| Anemic domain model | Objects are just data bags; logic lives elsewhere | Move behavior into the domain objects |
| Boolean parameters | Ambiguous call sites, hidden branching | Split methods, or use an enum |
| Deep inheritance | Fragile, hard to trace behavior across many levels | Favor composition |
| Utility-class dumping ground | Unrelated static methods with no cohesion | Split into focused, named classes |
| Stringly-typed code | No compile-time safety for what is really a fixed set of values | Use enums or dedicated types |
| Exception as control flow | Exceptions are slow and obscure normal logic paths | Use normal conditionals / `Optional` |
| Static abuse | Hidden global state, hard to test | Instance fields + dependency injection |
| Over-mocking | Tests couple to implementation, not behavior | Test through the public behavior/contract |

### Long Method

```java
// Before: one long method mixing many steps
void processOrder(Order order) {
    if (order.getItems().isEmpty()) throw new IllegalArgumentException("empty order");
    BigDecimal total = BigDecimal.ZERO;
    for (OrderItem item : order.getItems()) {
        total = total.add(item.getPrice().multiply(BigDecimal.valueOf(item.getQuantity())));
    }
    if (order.getCoupon() != null) {
        total = total.subtract(total.multiply(order.getCoupon().rate()));
    }
    inventory.reserve(order);
    payment.charge(order.getCustomerId(), total);
    shipping.schedule(order);
}
```

```java
// After: extracted into small, named steps
void processOrder(Order order) {
    validate(order);
    BigDecimal total = calculateTotal(order);
    inventory.reserve(order);
    payment.charge(order.getCustomerId(), total);
    shipping.schedule(order);
}
```

### Large Class / God Object

```java
// Before: one class does everything for the whole application
class ApplicationManager {
    void authenticateUser(String u, String p) { ... }
    void sendEmail(String to, String body) { ... }
    void generateReport() { ... }
    void backupDatabase() { ... }
}
```

```java
// After: split by responsibility
class AuthService { void authenticate(String u, String p) { ... } }
class EmailService { void send(String to, String body) { ... } }
class ReportService { void generate() { ... } }
class BackupService { void backupDatabase() { ... } }
```

### Primitive Obsession

```java
// Before: a raw String can hold any garbage, no validation, no meaning
void sendEmail(String email) {
    if (!email.contains("@")) throw new IllegalArgumentException("bad email");
    ...
}

// After: a small value type validates itself and carries meaning everywhere
record EmailAddress(String value) {
    EmailAddress {
        if (!value.contains("@")) throw new IllegalArgumentException("bad email: " + value);
    }
}

void sendEmail(EmailAddress email) { ... } // impossible to pass an invalid one
```

### Feature Envy

```java
// Before: OrderPrinter reaches deep into Order's internals to compute something
// that arguably belongs on Order itself
class OrderPrinter {
    void print(Order order) {
        BigDecimal total = BigDecimal.ZERO;
        for (OrderItem item : order.getItems()) {
            total = total.add(item.getPrice().multiply(BigDecimal.valueOf(item.getQuantity())));
        }
        System.out.println("Total: " + total);
    }
}
```

```java
// After: the computation moves to the class that owns the data
class Order {
    BigDecimal calculateTotal() {
        return items.stream()
                .map(i -> i.getPrice().multiply(BigDecimal.valueOf(i.getQuantity())))
                .reduce(BigDecimal.ZERO, BigDecimal::add);
    }
}

class OrderPrinter {
    void print(Order order) {
        System.out.println("Total: " + order.calculateTotal());
    }
}
```

### Data Clumps

```java
// Before: street/city/zip always travel together as separate params
void shipOrder(String street, String city, String zip, Order order) { ... }
void billCustomer(String street, String city, String zip, Customer customer) { ... }
```

```java
// After: extracted into a single cohesive type
record Address(String street, String city, String zip) {}

void shipOrder(Address address, Order order) { ... }
void billCustomer(Address address, Customer customer) { ... }
```

### Shotgun Surgery

```java
// Before: "tax rate" logic is duplicated in three unrelated classes.
// Changing the tax rule means hunting down and editing all three.
class InvoiceService { BigDecimal tax(BigDecimal amount) { return amount.multiply(BigDecimal.valueOf(0.08)); } }
class ReceiptService { BigDecimal tax(BigDecimal amount) { return amount.multiply(BigDecimal.valueOf(0.08)); } }
class ReportService  { BigDecimal tax(BigDecimal amount) { return amount.multiply(BigDecimal.valueOf(0.08)); } }
```

```java
// After: one authoritative place to change
class TaxCalculator {
    private static final BigDecimal RATE = BigDecimal.valueOf(0.08);
    BigDecimal tax(BigDecimal amount) { return amount.multiply(RATE); }
}
// InvoiceService, ReceiptService, ReportService all delegate to TaxCalculator
```

### Anemic Domain Model

```java
// Before: Order is just a data bag; all logic lives in a separate "service"
class Order {
    List<OrderItem> items;
    List<OrderItem> getItems() { return items; }
    void setItems(List<OrderItem> items) { this.items = items; }
}

class OrderService {
    BigDecimal calculateTotal(Order order) {
        return order.getItems().stream()
                .map(i -> i.getPrice().multiply(BigDecimal.valueOf(i.getQuantity())))
                .reduce(BigDecimal.ZERO, BigDecimal::add);
    }
}
```

```java
// After: behavior moves into the domain object it belongs to
class Order {
    private final List<OrderItem> items;
    Order(List<OrderItem> items) { this.items = items; }

    BigDecimal calculateTotal() {
        return items.stream()
                .map(i -> i.getPrice().multiply(BigDecimal.valueOf(i.getQuantity())))
                .reduce(BigDecimal.ZERO, BigDecimal::add);
    }
}
```

### Boolean Parameters

```java
// Before
void search(String query, boolean caseSensitive) { ... }
search("Java", true); // true meaning what, exactly, at a glance?
```

```java
// After: an enum documents itself at the call site
enum CaseMode { SENSITIVE, INSENSITIVE }
void search(String query, CaseMode caseMode) { ... }
search("Java", CaseMode.SENSITIVE);
```

### Deep Inheritance

```java
// Before: five levels deep, unclear which ancestor defines what
class Animal {}
class Mammal extends Animal {}
class Carnivore extends Mammal {}
class Feline extends Carnivore {}
class Cat extends Feline {}
```

```java
// After: flatten using composition — combine behaviors instead of stacking classes
interface Diet { void eat(); }
interface Movement { void move(); }

class Cat {
    private final Diet diet;
    private final Movement movement;
    Cat(Diet diet, Movement movement) { this.diet = diet; this.movement = movement; }
}
```

### Utility-Class Dumping Ground

```java
// Before: unrelated static methods dumped into one "Utils" class
class Utils {
    static String formatDate(LocalDate d) { ... }
    static BigDecimal calculateTax(BigDecimal amount) { ... }
    static boolean isValidEmail(String s) { ... }
}
```

```java
// After: each concern gets its own focused, named home
class DateFormatting { static String format(LocalDate d) { ... } }
class TaxCalculator { static BigDecimal calculate(BigDecimal amount) { ... } }
class EmailValidator { static boolean isValid(String s) { ... } }
```

### Stringly-Typed Code

```java
// Before: a "status" that is really a fixed set of values, stored as a raw String
void updateStatus(String status) {
    if (status.equals("PENDING")) { ... }
    else if (status.equals("Pending")) { ... } // typo/case variant silently does nothing
}
```

```java
// After: an enum makes invalid values impossible to represent
enum OrderStatus { PENDING, SHIPPED, DELIVERED, CANCELLED }
void updateStatus(OrderStatus status) {
    switch (status) {
        case PENDING -> ...;
        case SHIPPED -> ...;
        default -> ...;
    }
}
```

### Exception as Control Flow

```java
// Before: uses an exception to detect "not found" during normal, expected flow
int findIndex(List<String> list, String target) {
    try {
        for (int i = 0; i < list.size(); i++) {
            if (list.get(i).equals(target)) throw new FoundException(i);
        }
    } catch (FoundException e) {
        return e.index;
    }
    return -1;
}
```

```java
// After: a normal conditional; exceptions are reserved for actual errors
int findIndex(List<String> list, String target) {
    for (int i = 0; i < list.size(); i++) {
        if (list.get(i).equals(target)) return i;
    }
    return -1;
}
```

### Static Abuse

```java
// Before: hidden global mutable state via static fields — hard to test in isolation
class OrderCounter {
    static int count = 0;
    static void increment() { count++; }
}
```

```java
// After: instance state, injected where needed, easy to isolate in tests
class OrderCounter {
    private int count = 0;
    void increment() { count++; }
    int get() { return count; }
}
```

### Over-Mocking

```java
// Before: test mocks every internal collaborator and asserts on call order,
// so it breaks the moment implementation details change even if behavior doesn't
@Test
void placeOrder_callsCollaboratorsInOrder() {
    OrderService service = new OrderService(mockInventory, mockPayment, mockShipping);
    service.placeOrder(order);
    InOrder inOrder = inOrder(mockInventory, mockPayment, mockShipping);
    inOrder.verify(mockInventory).reserve(order);
    inOrder.verify(mockPayment).charge(any(), any());
    inOrder.verify(mockShipping).schedule(order);
}
```

```java
// After: test the observable behavior/contract, not the internal wiring
@Test
void placeOrder_reservesInventoryAndChargesCustomer() {
    OrderService service = new OrderService(realInventory, fakePayment, fakeShipping);
    service.placeOrder(order);
    assertThat(realInventory.isReserved(order)).isTrue();
    assertThat(fakePayment.chargedAmountFor(order)).isEqualTo(order.calculateTotal());
}
```

## Common Code-Review Interview Pitfalls

1. **Method does more than one thing, mixing abstraction levels.**
   Why it matters: it forces the reader to jump between "what" and "how," making the method hard to skim and hard to test in isolation.
   ```java
   // Flag: validation, calculation, and I/O all inline in one method
   void placeOrder(Order o) { /* validate + compute + call HTTP + save */ }
   ```

2. **Boolean parameter with no context at the call site.**
   Why it matters: `foo(x, true)` tells the reader nothing about what `true` means without opening the method.
   ```java
   printReport(report, true); // true = ??? — prefer an enum or split methods
   ```

3. **`equals()` overridden without `hashCode()`.**
   Why it matters: breaks `HashMap`/`HashSet` — objects that are "equal" end up in different buckets and lookups silently fail.
   ```java
   class Point { boolean equals(Object o) { ... } /* no hashCode()! */ }
   ```

4. **Public method returns `null` instead of an empty collection.**
   Why it matters: pushes a mandatory null-check onto every caller; someone eventually forgets and gets an NPE.
   ```java
   List<Order> findOrders(String id) { return orders.isEmpty() ? null : orders; }
   ```

5. **New class extends a concrete class purely for code reuse.**
   Why it matters: couples the subclass to the superclass's internals, which is exactly the fragile-base-class problem LSP and "favor composition" warn about.
   ```java
   class InstrumentedList extends ArrayList<String> { /* leaks ArrayList internals */ }
   ```

6. **Singleton implemented with a lazy-checked static field instead of an enum.**
   Why it matters: hand-rolled singletons are vulnerable to reflection and serialization attacks that recreate a second instance; enum singletons are not.
   ```java
   class Config { private static Config instance; static Config getInstance() { ... } }
   ```

7. **`Optional` used as a field or method parameter.**
   Why it matters: `Optional` was designed as a return type; using it for fields/parameters adds wrapping overhead and is not serializable.
   ```java
   class Order { Optional<String> couponCode; } // flag: should be a plain nullable String
   ```

8. **String concatenation with `+` inside a loop.**
   Why it matters: quietly O(n²) — each iteration allocates a brand-new `String`; use `StringBuilder`.
   ```java
   String s = "";
   for (String w : words) s += w; // flag
   ```

9. **Magic numbers or literals with no named meaning.**
   Why it matters: the reader cannot tell if `3` is a retry count, a threshold, or an index without digging through history.
   ```java
   if (attempts >= 3) lockAccount(); // what does 3 mean, and why 3?
   ```

10. **Catching a broad exception and swallowing it, or catching `Throwable`.**
    Why it matters: hides real bugs, and catching `Throwable` also swallows JVM-level `Error`s that should never be caught by application code.
    ```java
    try { risky(); } catch (Exception e) { /* nothing */ } // flag
    ```

11. **New public API with a constructor that has more than four parameters.**
    Why it matters: callers can silently swap two same-typed arguments (e.g. two `int`s) with no compiler warning; a builder or record removes the ambiguity.
    ```java
    new User("Ana", "ana@x.com", true, false, 30, "US"); // flag: use a builder
    ```

12. **Exposing a mutable internal collection through a public getter.**
    Why it matters: any caller can mutate the object's internal state from outside, breaking encapsulation and invariants.
    ```java
    public List<String> getMembers() { return members; } // flag: return List.copyOf(members)
    ```

13. **Removing or renaming a public method without deprecation.**
    Why it matters: breaks both source and binary compatibility for every downstream consumer with no warning period.
    ```java
    // Flag if this method existed in a previous public release and just disappears
    // public void save(Order order) { ... }
    ```

14. **Deep nested `if` blocks instead of guard clauses.**
    Why it matters: the reader must hold multiple nested conditions in their head at once to find the actual logic.
    ```java
    if (a != null) { if (a.isValid()) { if (b > 0) { doWork(); } } } // flag
    ```

15. **A "Manager"/"Helper"/"Utils" class that keeps growing with unrelated static methods.**
    Why it matters: it becomes a God object with no cohesion, and every unrelated change collides in the same file.
    ```java
    class Utils { static String formatDate(...) {} static void chargeCard(...) {} } // flag
    ```

16. **Design pattern boilerplate (Strategy/Command/Observer as full classes) where a lambda would do.**
    Why it matters: in modern Java, a one-line lambda implementing a functional interface is clearer than a whole extra class file for simple cases — flag unnecessary ceremony.
    ```java
    class AlwaysApprove implements DiscountStrategy { public BigDecimal apply(BigDecimal p) { return p; } }
    // Prefer: DiscountStrategy noDiscount = price -> price;
    ```

17. **Using a raw `String`/`int` where a fixed set of values exists (stringly-typed code).**
    Why it matters: no compile-time safety — typos like `"Pending"` vs `"PENDING"` compile fine and fail silently at runtime.
    ```java
    void setStatus(String status) { ... } // flag: should be an enum OrderStatus
    ```

18. **Test mocks every collaborator and asserts call order instead of testing behavior.**
    Why it matters: the test breaks whenever internal wiring changes, even if the externally observable behavior is unchanged — a maintenance burden with no correctness benefit.
    ```java
    verify(mockA).method(); verify(mockB).method(); // flag if this is the entire assertion
    ```
