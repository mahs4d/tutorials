# 20. Testing

Code review is not just about the production code in the diff — it is at least as much about the tests that accompany it. A reviewer who cannot tell *what* a test proves, or who spots a test that mocks its way around the actual bug, will (and should) block the PR. This chapter covers JUnit 5 in depth — the framework almost every Java shop uses today — and Mockito, the de-facto mocking library, with an emphasis on the patterns and anti-patterns that come up constantly in review. We target JUnit Jupiter 5.10+ and Mockito 5.x on Java 21+.

## Table of Contents

- [Testing Fundamentals (JUnit 5)](#testing-fundamentals-junit-5)
- [Mocking (Mockito)](#mocking-mockito)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Testing Fundamentals (JUnit 5)

### Why We Test, and the Test Pyramid

Tests exist to answer one question cheaply and repeatedly: **does the system still behave correctly after this change?** Without automated tests, that question can only be answered by manual verification (slow, inconsistent, easily skipped) or by production incidents (expensive, embarrassing). A good test suite turns "did I break anything?" into a command that runs in seconds and reports the answer precisely.

The **test pyramid** is a mental model for how to allocate testing effort:

```
        ▲
       / \        End-to-End (E2E) tests
      /   \        - few, slow, brittle, high confidence
     /-----\
    /       \      Integration tests
   /         \      - moderate count, hit real DB/HTTP/queue boundaries
  /-----------\
 /             \    Unit tests
/---------------\    - many, fast, isolated, cheap to write and run
```

- **Unit tests** exercise a single class or a small cluster of classes in isolation (often with collaborators mocked or faked). They should be the vast majority of your suite: fast (milliseconds), deterministic, and pinpoint the exact failure.
- **Integration tests** verify that your code talks correctly to a real (or realistic) database, message broker, HTTP client, etc. Fewer of these — they are slower and more fragile — but essential because unit tests alone cannot catch a wrong SQL query or a misconfigured serializer.
- **End-to-end tests** drive the whole system as a black box (e.g., through its public API or UI). Very few of these: they are slow, flaky, and expensive to maintain, but they catch wiring problems nothing else can.

A reviewer's rule of thumb: if a PR adds a new branch of logic and the diff has no matching unit test, that is a legitimate request-changes comment. If a PR tries to verify business logic exclusively through a slow end-to-end test, that is also worth challenging — push the assertion down the pyramid.

### AAA / Given-When-Then Structure

Every well-written test should have three clearly separated parts, whether or not you label them:

- **Arrange / Given** — set up the object under test and its inputs.
- **Act / When** — perform the one action being tested.
- **Assert / Then** — check the outcome.

```java
@Test
void discount_appliesTenPercentForPremiumCustomer() {
    // Arrange (Given)
    Customer premiumCustomer = new Customer("alice", CustomerTier.PREMIUM);
    PricingService pricingService = new PricingService();

    // Act (When)
    BigDecimal finalPrice = pricingService.priceFor(premiumCustomer, new BigDecimal("100.00"));

    // Assert (Then)
    assertEquals(new BigDecimal("90.00"), finalPrice);
}
```

Keep each test to **one logical action** and, ideally, **one reason to fail**. A test with five unrelated `assertEquals` calls checking five unrelated behaviors is a maintenance headache: when it fails, you have to read the whole body to figure out which behavior broke. Splitting it into five small tests (or using `assertAll`, covered below, when the assertions really are about a single outcome) gives you a failure message that already tells you what broke.

### Naming Tests So Failures Read Like Sentences

A test name is documentation that never goes stale — if it does, the test breaks and tells you. Favor names that describe **behavior**, not implementation, and that read naturally when a report lists a failing test.

Common conventions:

| Style | Example |
|---|---|
| `methodName_condition_expectedResult` | `withdraw_insufficientFunds_throwsIllegalStateException` |
| Given-When-Then in the name | `givenEmptyCart_whenCheckout_thenThrowsEmptyCartException` |
| Sentence-like with backticks (`@DisplayName`) | `"withdraw() throws when balance is insufficient"` |

```java
// Weak: tells you nothing when it's in a failure list
@Test
void test1() { ... }

// Better: reads like a specification
@Test
void withdraw_insufficientFunds_throwsIllegalStateException() { ... }

// Best when paired with @DisplayName for human-readable reports
@Test
@DisplayName("withdraw() throws IllegalStateException when balance is insufficient")
void withdraw_insufficientFunds_throwsIllegalStateException() { ... }
```

Avoid names like `testWithdraw` or `shouldWork` — they force the reader to open the method body to learn anything, defeating the purpose of a descriptive test suite.

### JUnit 5 Architecture: Platform, Jupiter, Vintage

JUnit 5 is not one library but three:

- **JUnit Platform** — the foundation. It discovers and runs tests via the `TestEngine` API, and is what build tools (Maven Surefire, Gradle) and IDEs actually talk to.
- **JUnit Jupiter** — the new programming model and extension API (`@Test`, `@BeforeEach`, `assertEquals`, extensions, etc.). This is "JUnit 5" as most people mean it.
- **JUnit Vintage** — a `TestEngine` that runs old JUnit 3/4 tests on the JUnit Platform, so you can migrate incrementally instead of rewriting everything at once.

```
JUnit Platform  (test discovery + execution, IDE/build tool integration)
   ├── JUnit Jupiter Engine   → runs @Test-annotated JUnit 5 tests
   └── JUnit Vintage Engine   → runs legacy JUnit 3/4 tests
```

This separation is why you can mix legacy `@org.junit.Test` (JUnit 4) tests and modern `@org.junit.jupiter.api.Test` (JUnit 5) tests in the same module during a migration — both engines plug into the same platform.

#### JUnit 4 → JUnit 5 Annotation Mapping

| JUnit 4 | JUnit 5 (Jupiter) | Notes |
|---|---|---|
| `@Test` | `@Test` | JUnit 5's `@Test` no longer supports `expected=`/`timeout=` attributes |
| `@Before` | `@BeforeEach` | |
| `@After` | `@AfterEach` | |
| `@BeforeClass` | `@BeforeAll` | Must be `static` unless `@TestInstance(PER_CLASS)` |
| `@AfterClass` | `@AfterAll` | Must be `static` unless `@TestInstance(PER_CLASS)` |
| `@Ignore` | `@Disabled` | |
| `@Category(Foo.class)` | `@Tag("foo")` | String-based, no marker interface needed |
| `@RunWith(...)` | `@ExtendWith(...)` | Composable — you can stack multiple extensions |
| `@Rule` / `@ClassRule` | `Extension` implementations | More granular lifecycle hooks (see Extensions below) |
| `@Test(expected = X.class)` | `assertThrows(X.class, () -> ...)` | Returns the exception so you can assert on its message too |
| `Assume.assumeTrue(...)` | `Assumptions.assumeTrue(...)` | Same idea, new package |

### Maven and Gradle Setup

**Maven** (`pom.xml`) — the BOM keeps all Jupiter artifacts on matching versions:

```xml
<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.junit</groupId>
            <artifactId>junit-bom</artifactId>
            <version>5.10.2</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>

<dependencies>
    <dependency>
        <groupId>org.junit.jupiter</groupId>
        <artifactId>junit-jupiter</artifactId>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>org.mockito</groupId>
        <artifactId>mockito-junit-jupiter</artifactId>
        <version>5.11.0</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>org.assertj</groupId>
        <artifactId>assertj-core</artifactId>
        <version>3.25.3</version>
        <scope>test</scope>
    </dependency>
</dependencies>

<build>
    <plugins>
        <plugin>
            <groupId>org.apache.maven.plugins</groupId>
            <artifactId>maven-surefire-plugin</artifactId>
            <version>3.2.5</version>
        </plugin>
    </plugins>
</build>
```

**Gradle** (`build.gradle.kts`):

```kotlin
dependencies {
    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testImplementation("org.mockito:mockito-junit-jupiter:5.11.0")
    testImplementation("org.assertj:assertj-core:3.25.3")
}

tasks.test {
    useJUnitPlatform()
}
```

Forgetting `useJUnitPlatform()` in Gradle (or the Surefire dependency alignment in Maven) is a classic "why are my tests not running at all" bug — the build reports success with zero tests executed, which is worse than a failure because it hides silently.

### Core Annotations and Lifecycle

```java
import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;

class OrderCalculatorTest {

    private OrderCalculator calculator;

    @BeforeAll
    static void setUpClass() {
        System.out.println("Runs once before any test in this class");
    }

    @BeforeEach
    void setUp() {
        calculator = new OrderCalculator();
        System.out.println("Runs before every single test method");
    }

    @Test
    @DisplayName("total() sums line item prices")
    void total_sumsLineItemPrices() {
        calculator.addLine("widget", 2, new BigDecimal("5.00"));
        calculator.addLine("gadget", 1, new BigDecimal("12.50"));

        assertEquals(new BigDecimal("22.50"), calculator.total());
    }

    @AfterEach
    void tearDown() {
        System.out.println("Runs after every single test method");
    }

    @AfterAll
    static void tearDownClass() {
        System.out.println("Runs once after all tests in this class");
    }
}
```

By default, JUnit creates a **new instance of the test class for every test method** (`@TestInstance(Lifecycle.PER_METHOD)`), which is why `@BeforeAll`/`@AfterAll` must be `static` — there is no single instance to hold state across tests. This also means each test starts from a completely fresh object graph, preventing one test's mutated state from leaking into another — a common source of order-dependent flakiness in older frameworks.

You can opt into one shared instance per test class:

```java
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ExpensiveSetupTest {

    private ExpensiveResource resource;

    @BeforeAll
    void setUp() {           // no longer needs to be static
        resource = new ExpensiveResource();
    }

    @Test
    void usesSharedResource() {
        assertNotNull(resource);
    }
}
```

Use `PER_CLASS` sparingly — it is convenient for genuinely expensive, read-only setup, but it reintroduces the risk of tests sharing mutable state, the exact problem `PER_METHOD` was designed to avoid. If you use it, make sure the shared object is either immutable or reset in `@BeforeEach`.

### Assertions

Jupiter's built-in `Assertions` class covers the common cases:

```java
import static org.junit.jupiter.api.Assertions.*;

@Test
void assertionShowcase() {
    assertEquals(4, 2 + 2);
    assertTrue(List.of(1, 2, 3).contains(2));
    assertFalse(List.of(1, 2, 3).isEmpty());
    assertNull(findUser("missing"));
    assertNotNull(findUser("alice"));

    assertIterableEquals(List.of(1, 2, 3), computeSequence());

    IllegalArgumentException ex = assertThrows(
        IllegalArgumentException.class,
        () -> new Account(new BigDecimal("-1"))
    );
    assertEquals("initial balance must not be negative", ex.getMessage());

    assertTimeout(Duration.ofMillis(200), () -> slowButBoundedOperation());

    // Groups related checks: ALL run even if one fails, and all failures are reported together
    Order order = buildOrder();
    assertAll("order invariants",
        () -> assertEquals("ORD-1", order.id()),
        () -> assertEquals(OrderStatus.PENDING, order.status()),
        () -> assertFalse(order.lineItems().isEmpty())
    );
}
```

`assertAll` is the key one reviewers look for: without it, three separate `assertEquals` calls stop at the first failure, hiding whether the second and third would also have failed. `assertAll` runs every supplied executable and reports every failure together — much more useful when triaging a broken test.

`assertTimeout` runs the code on the *same* thread and waits for it to finish (so a hang will still block the test thread); `assertTimeoutPreemptively` runs it on a separate thread and aborts at the deadline, but can leave background work running and has subtle interactions with `ThreadLocal`s — prefer `assertTimeout` unless you specifically need preemptive cancellation.

#### AssertJ as a Fluent Alternative

JUnit's static assertions are fine, but they read backwards (`assertEquals(expected, actual)`) and don't chain well. **AssertJ** provides a fluent, chainable, and far more readable API, and is the de-facto standard paired with JUnit 5 in most professional codebases:

```java
import static org.assertj.core.api.Assertions.assertThat;

@Test
void total_sumsLineItemPrices_assertj() {
    calculator.addLine("widget", 2, new BigDecimal("5.00"));
    calculator.addLine("gadget", 1, new BigDecimal("12.50"));

    assertThat(calculator.total()).isEqualByComparingTo("22.50");

    assertThat(calculator.lineItems())
        .hasSize(2)
        .extracting("name")
        .containsExactly("widget", "gadget");

    assertThatThrownBy(() -> calculator.addLine("bad", -1, BigDecimal.TEN))
        .isInstanceOf(IllegalArgumentException.class)
        .hasMessageContaining("quantity");
}
```

AssertJ's chained assertions produce much richer failure messages out of the box (e.g., showing exactly which elements differ in a collection), and `assertThatThrownBy` combined with `.hasMessageContaining(...)` is more expressive than `assertThrows` + a separate `assertEquals` on the message.

### Nested Tests

`@Nested` groups related tests inside inner classes, which is useful for expressing "given this context, these things should be true" structures and produces much clearer test reports:

```java
import org.junit.jupiter.api.*;
import static org.junit.jupiter.api.Assertions.*;

class BankAccountTest {

    private BankAccount account;

    @BeforeEach
    void setUp() {
        account = new BankAccount("acc-1", new BigDecimal("100.00"));
    }

    @Nested
    @DisplayName("when the account has a positive balance")
    class WithPositiveBalance {

        @Test
        @DisplayName("withdraw() succeeds for amounts within balance")
        void withdraw_withinBalance_succeeds() {
            account.withdraw(new BigDecimal("40.00"));
            assertEquals(new BigDecimal("60.00"), account.balance());
        }

        @Test
        @DisplayName("withdraw() throws for amounts above balance")
        void withdraw_aboveBalance_throws() {
            assertThrows(InsufficientFundsException.class,
                () -> account.withdraw(new BigDecimal("500.00")));
        }
    }

    @Nested
    @DisplayName("when the account is frozen")
    class WhenFrozen {

        @BeforeEach
        void freeze() {
            account.freeze();
        }

        @Test
        @DisplayName("withdraw() throws regardless of balance")
        void withdraw_whenFrozen_throws() {
            assertThrows(AccountFrozenException.class,
                () -> account.withdraw(new BigDecimal("1.00")));
        }
    }
}
```

Each `@Nested` class gets its own `@BeforeEach` chain (outer then inner), so `WhenFrozen.freeze()` runs after the outer `setUp()`, letting you build up scenario-specific state incrementally.

### Tags and Filtering

`@Tag` lets you categorize tests (e.g., `slow`, `integration`, `smoke`) and then include or exclude categories at build time without touching test code:

```java
@Tag("integration")
class OrderRepositoryIntegrationTest {

    @Test
    @Tag("slow")
    void savesAndReloadsOrder() {
        // hits a real (test) database
    }
}
```

Maven Surefire:

```xml
<plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-surefire-plugin</artifactId>
    <configuration>
        <excludedGroups>slow, integration</excludedGroups>
    </configuration>
</plugin>
```

Gradle:

```kotlin
tasks.test {
    useJUnitPlatform {
        excludeTags("slow", "integration")
    }
}
```

This is how most CI pipelines run a fast "unit only" stage on every push and reserve a slower "integration" stage for merge queues or nightly runs.

### Disabling Tests

```java
@Test
@Disabled("Flaky due to JDK-XXXX clock resolution bug on CI; see TICKET-1234")
void timeSensitiveTest() { ... }
```

A reviewer should always ask **why** a test is disabled and whether there is a tracked ticket — `@Disabled` with no reason or follow-up is a silent hole in coverage that tends to be forgotten forever.

### Assumptions

`Assumptions.assumeTrue(...)` aborts (not fails) a test when a precondition isn't met — useful for environment-dependent tests you don't want counted as failures:

```java
import static org.junit.jupiter.api.Assumptions.*;

@Test
void nativeLibraryTest() {
    assumeTrue(System.getProperty("os.name").toLowerCase().contains("linux"),
        "This test only runs on Linux");

    // ... test that depends on a Linux-only native library
}
```

If the assumption fails, JUnit marks the test as **skipped**, not failed — it will not turn a CI build red, but it also will not silently pass, so it still shows up in the report.

### Parameterized Tests

Parameterized tests let you run the same test logic against many inputs without copy-pasting the method.

```java
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;
import static org.junit.jupiter.api.Assertions.*;

class PasswordValidatorTest {

    private final PasswordValidator validator = new PasswordValidator();

    @ParameterizedTest
    @ValueSource(strings = { "abc", "12345", "        ", "" })
    void isValid_rejectsShortOrBlankPasswords(String candidate) {
        assertFalse(validator.isValid(candidate));
    }

    @ParameterizedTest
    @CsvSource({
        "Password1!, true",
        "password1!, false",   // no uppercase
        "PASSWORD1!, false",   // no lowercase
        "Password!,  false",  // no digit
        "Password1,  false"   // no symbol
    })
    void isValid_enforcesComplexityRules(String candidate, boolean expected) {
        assertEquals(expected, validator.isValid(candidate));
    }

    @ParameterizedTest
    @MethodSource("complexPasswordCases")
    void isValid_matchesMethodSourceCases(String candidate, boolean expected) {
        assertEquals(expected, validator.isValid(candidate));
    }

    static Stream<Arguments> complexPasswordCases() {
        return Stream.of(
            Arguments.of("Correct1!Horse", true),
            Arguments.of("nope", false),
            Arguments.of("Tr0ub4dor&3", true)
        );
    }

    @ParameterizedTest
    @EnumSource(value = PasswordStrength.class, names = "WEAK", mode = EnumSource.Mode.EXCLUDE)
    void isValid_acceptsNonWeakStrengths(PasswordStrength strength) {
        assertTrue(validator.isValid(strength.samplePassword()));
    }

    @ParameterizedTest
    @ArgumentsSource(SqlInjectionPayloadsProvider.class)
    void isValid_rejectsSqlInjectionLikePayloads(String payload) {
        assertFalse(validator.isValid(payload));
    }

    static class SqlInjectionPayloadsProvider implements ArgumentsProvider {
        @Override
        public Stream<? extends Arguments> provideArguments(ExtensionContext context) {
            return Stream.of("' OR '1'='1", "'; DROP TABLE users; --")
                          .map(Arguments::of);
        }
    }
}
```

| Source | Best for |
|---|---|
| `@ValueSource` | A flat list of one primitive/String argument each |
| `@CsvSource` | A handful of inline multi-argument rows |
| `@MethodSource` | Complex objects, or cases shared across multiple test methods |
| `@EnumSource` | Iterating over some or all values of an enum |
| `@ArgumentsSource` | A reusable, custom provider you want to share across test classes |

Parameterized tests are one of the highest-leverage tools in the whole framework: a reviewer seeing five nearly identical `@Test` methods that differ only in input/output values should almost always suggest collapsing them into one `@ParameterizedTest`.

### Repeated Tests

`@RepeatedTest` runs the same test multiple times — useful for surfacing flakiness in something that has an element of non-determinism (e.g., relies on system time, randomness, or concurrency) during investigation, though it is not a substitute for fixing the non-determinism.

```java
@RepeatedTest(10)
void idGenerator_neverProducesDuplicateIds(RepetitionInfo info) {
    String id = IdGenerator.next();
    assertTrue(seenIds.add(id), "Duplicate on repetition " + info.getCurrentRepetition());
}
```

### Dynamic Tests with `@TestFactory`

Regular `@Test` methods are fixed at compile time. `@TestFactory` lets you *generate* tests at runtime, returning a `Stream`/`Collection`/`Iterable` of `DynamicTest`:

```java
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;
import static org.junit.jupiter.api.DynamicTest.dynamicTest;

class FizzBuzzDynamicTest {

    @TestFactory
    Stream<DynamicTest> fizzBuzzCases() {
        Map<Integer, String> expectations = Map.of(
            1, "1", 3, "Fizz", 5, "Buzz", 15, "FizzBuzz"
        );

        return expectations.entrySet().stream()
            .map(entry -> dynamicTest(
                "fizzBuzz(" + entry.getKey() + ") == " + entry.getValue(),
                () -> assertEquals(entry.getValue(), FizzBuzz.of(entry.getKey()))
            ));
    }
}
```

Dynamic tests are less common than parameterized tests in day-to-day code but show up when test cases themselves need to be computed (e.g., generated from a spec file or a database of fixtures).

### Extensions

Jupiter replaces JUnit 4's `@Rule`/`@RunWith` with a single, composable `Extension` model, activated with `@ExtendWith`.

```java
class LoggingTest {
    // Third-party or shared extensions plug in the same way Mockito's extension does
}
```

A custom extension that times every test and logs slow ones:

```java
import org.junit.jupiter.api.extension.*;

public class TimingExtension implements BeforeTestExecutionCallback, AfterTestExecutionCallback {

    private static final ExtensionContext.Namespace NAMESPACE =
        ExtensionContext.Namespace.create(TimingExtension.class);

    @Override
    public void beforeTestExecution(ExtensionContext context) {
        getStore(context).put("start", System.nanoTime());
    }

    @Override
    public void afterTestExecution(ExtensionContext context) {
        long start = getStore(context).remove("start", long.class);
        long durationMs = (System.nanoTime() - start) / 1_000_000;
        if (durationMs > 100) {
            System.out.printf("SLOW TEST [%s]: %d ms%n", context.getDisplayName(), durationMs);
        }
    }

    private ExtensionContext.Store getStore(ExtensionContext context) {
        return context.getStore(NAMESPACE);
    }
}

@ExtendWith(TimingExtension.class)
class ReportGeneratorTest {

    @Test
    void generatesMonthlyReport() {
        // ...
    }
}
```

`@TempDir` is a built-in extension for tests that need a real filesystem directory that is automatically created before the test and deleted after:

```java
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.*;

class CsvExporterTest {

    @Test
    void export_writesCsvFileWithHeader(@TempDir Path tempDir) throws IOException {
        Path outputFile = tempDir.resolve("export.csv");

        new CsvExporter().export(List.of(new Row("a", 1)), outputFile);

        List<String> lines = Files.readAllLines(outputFile);
        assertEquals("name,value", lines.get(0));
        assertEquals("a,1", lines.get(1));
    }
}
```

`@TempDir` removes the classic "tests that write to `/tmp` and never clean up, eventually filling the CI disk" problem entirely.

### Testing Exceptions

Always assert on both the exception **type** and enough of the **message/state** to know the right branch was hit — asserting only the type can pass even if the wrong condition threw it.

```java
@Test
void transfer_negativeAmount_throwsWithDescriptiveMessage() {
    Account source = new Account("A", new BigDecimal("100"));
    Account target = new Account("B", new BigDecimal("0"));

    IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
        () -> transferService.transfer(source, target, new BigDecimal("-10")));

    assertEquals("transfer amount must be positive: -10", ex.getMessage());
}
```

### Testing Time with an Injected `Clock`

Code that calls `Instant.now()`, `LocalDate.now()`, or `System.currentTimeMillis()` directly is untestable without sleeping or mocking static methods. The fix is to depend on `java.time.Clock` and inject it, so tests can supply a fixed clock.

```java
public class SubscriptionService {

    private final Clock clock;

    public SubscriptionService(Clock clock) {
        this.clock = clock;
    }

    public boolean isExpired(Subscription subscription) {
        return subscription.expiresAt().isBefore(LocalDate.now(clock));
    }
}
```

```java
class SubscriptionServiceTest {

    @Test
    void isExpired_returnsTrue_whenExpiryIsInThePast() {
        Clock fixedClock = Clock.fixed(
            Instant.parse("2026-08-07T00:00:00Z"), ZoneOffset.UTC);
        SubscriptionService service = new SubscriptionService(fixedClock);

        Subscription expired = new Subscription(LocalDate.parse("2026-08-01"));

        assertTrue(service.isExpired(expired));
    }

    @Test
    void isExpired_returnsFalse_whenExpiryIsInTheFuture() {
        Clock fixedClock = Clock.fixed(
            Instant.parse("2026-08-07T00:00:00Z"), ZoneOffset.UTC);
        SubscriptionService service = new SubscriptionService(fixedClock);

        Subscription active = new Subscription(LocalDate.parse("2026-12-31"));

        assertFalse(service.isExpired(active));
    }
}
```

This one pattern eliminates an entire category of flaky, date-dependent tests (the ones that mysteriously fail once a year around New Year's Eve, or that pass locally but fail on CI in a different timezone).

### Testing Concurrency — and Why `Thread.sleep` Is a Smell

A test that does this is a ticking time bomb:

```java
// Bad: racy and slow
@Test
void taskRunsAsynchronously() throws InterruptedException {
    AtomicBoolean ran = new AtomicBoolean(false);
    executor.submit(() -> ran.set(true));

    Thread.sleep(500); // hope 500ms is enough... until CI is under load

    assertTrue(ran.get());
}
```

`Thread.sleep` in a concurrency test encodes a guess about timing. It is either wasteful (sleeping far longer than necessary, slowing down the whole suite) or unreliable (too short under CI load, causing intermittent failures — flakiness). The fix is to wait for the actual event, not for a fixed amount of wall-clock time.

**`CountDownLatch`** — signal exactly when the work is done:

```java
@Test
void taskRunsAsynchronously_withLatch() throws InterruptedException {
    CountDownLatch latch = new CountDownLatch(1);
    AtomicBoolean ran = new AtomicBoolean(false);

    executor.submit(() -> {
        ran.set(true);
        latch.countDown();
    });

    boolean completedInTime = latch.await(2, TimeUnit.SECONDS);

    assertTrue(completedInTime, "task did not complete within timeout");
    assertTrue(ran.get());
}
```

**Awaitility** — for polling a condition that eventually becomes true (e.g., an async cache being populated), which reads far better than a hand-rolled polling loop:

```java
import static org.awaitility.Awaitility.await;
import java.time.Duration;

@Test
void cache_eventuallyContainsWarmedEntries() {
    cacheWarmer.warmAsync();

    await().atMost(Duration.ofSeconds(3))
           .pollInterval(Duration.ofMillis(50))
           .until(() -> cache.get("key") != null);

    assertEquals("value", cache.get("key"));
}
```

Both approaches wait for the *minimum* necessary time and fail fast with a clear timeout message, instead of guessing a sleep duration that is either too short (flaky) or too long (slow suite).

### Test Doubles Vocabulary

"Mock" is used loosely in everyday speech, but in precise testing vocabulary there are five distinct kinds of test doubles:

| Term | What it does | Verifies behavior? | Typical use |
|---|---|---|---|
| **Dummy** | A placeholder passed to satisfy a parameter list; never actually used | No | `new Service(null, dummyLogger)` when `dummyLogger` is never called |
| **Stub** | Returns canned answers to calls made during the test | No | `when(repo.findById(1)).thenReturn(user)` |
| **Fake** | A lightweight *working* implementation, not production-grade (e.g., in-memory DB) | Indirectly, through real behavior | `InMemoryUserRepository implements UserRepository` |
| **Spy** | A real object with some methods overridden/recorded, or a mock that records calls on a real instance | Yes (records calls, can also stub) | `Mockito.spy(realService)` |
| **Mock** | A dynamically generated object programmed with expectations, used to verify interactions | Yes (that's its purpose) | `verify(emailSender).send(any())` |

The line between stub and mock is about **intent**: a stub just feeds data into the test (state verification — you check the *result*), while a mock is used to verify that specific interactions happened (behavior verification — you check the *calls*). Mockito's `mock()` can be used as either, depending on whether you call `verify(...)` on it.

### Code Coverage — and Why 100% Is Not the Goal

Coverage tools (JaCoCo, for example) report the percentage of lines/branches executed by the test suite. Coverage is a **necessary but not sufficient** signal:

- Low coverage on business-critical code is a real red flag — it means there is a good chance that logic has never been exercised by a test.
- High coverage tells you code *ran*, not that it was *checked*. A test with no assertions can hit 100% of a method's lines and prove nothing.

```java
// Executes every line (100% "covered") but asserts nothing meaningful
@Test
void processOrder_doesNotThrow() {
    orderService.process(order); // "coverage" achieved, but no behavior verified
}
```

Chasing 100% coverage as a target also incentivizes testing trivial code (getters, `toString()`, generated code) at the expense of time that should go to edge cases and error paths. A better review question than "what's the coverage number?" is "does this test actually assert the behavior that matters, including the failure paths?"

### Flaky Tests

A **flaky test** passes and fails intermittently with no code change — the single most corrosive thing to trust in a test suite, because a team that has learned to click "rerun" on red builds has effectively stopped trusting CI.

Common causes:

- **Time dependence** — using `LocalDate.now()`/`Instant.now()` directly instead of an injected `Clock` (see above).
- **Shared mutable state** — static fields, singletons, or a shared database row mutated by tests running in parallel or in a different order than expected.
- **Real concurrency/timing assumptions** — `Thread.sleep` races, or asserting order between two async operations without synchronization.
- **External dependencies** — hitting a real network service, real clock-based expiry, or an unseeded random number generator.
- **Test order dependence** — a test that only passes because an earlier test happened to leave the system in the right state; JUnit does not guarantee method execution order, so this can flip at any time.
- **Resource leaks across tests** — a thread pool, file handle, or connection left open by one test that starves the next one.

The fix is almost always to remove the non-determinism (inject a clock, replace `sleep` with a latch, isolate state per test, seed random generators) rather than to add retries — retries hide the bug, they don't fix it.

### A Full Realistic Test Class

Putting several of the above together for a small service that computes shipping fees:

```java
// Production code
public class ShippingFeeCalculator {

    private static final BigDecimal FREE_SHIPPING_THRESHOLD = new BigDecimal("50.00");
    private static final BigDecimal STANDARD_FEE = new BigDecimal("4.99");

    public BigDecimal feeFor(BigDecimal orderTotal, ShippingSpeed speed) {
        if (orderTotal == null) {
            throw new IllegalArgumentException("orderTotal must not be null");
        }
        if (orderTotal.compareTo(BigDecimal.ZERO) < 0) {
            throw new IllegalArgumentException("orderTotal must not be negative: " + orderTotal);
        }

        if (orderTotal.compareTo(FREE_SHIPPING_THRESHOLD) >= 0 && speed == ShippingSpeed.STANDARD) {
            return BigDecimal.ZERO;
        }

        return switch (speed) {
            case STANDARD -> STANDARD_FEE;
            case EXPRESS -> STANDARD_FEE.multiply(new BigDecimal("2"));
            case OVERNIGHT -> STANDARD_FEE.multiply(new BigDecimal("4"));
        };
    }
}
```

```java
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.*;

@DisplayName("ShippingFeeCalculator")
class ShippingFeeCalculatorTest {

    private final ShippingFeeCalculator calculator = new ShippingFeeCalculator();

    @Nested
    @DisplayName("input validation")
    class InputValidation {

        @Test
        @DisplayName("throws when orderTotal is null")
        void feeFor_nullOrderTotal_throws() {
            IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
                () -> calculator.feeFor(null, ShippingSpeed.STANDARD));
            assertEquals("orderTotal must not be null", ex.getMessage());
        }

        @Test
        @DisplayName("throws when orderTotal is negative")
        void feeFor_negativeOrderTotal_throws() {
            assertThrows(IllegalArgumentException.class,
                () -> calculator.feeFor(new BigDecimal("-1.00"), ShippingSpeed.STANDARD));
        }
    }

    @Nested
    @DisplayName("standard shipping")
    class StandardShipping {

        @Test
        @DisplayName("is free at or above the free-shipping threshold")
        void feeFor_atThreshold_isFree() {
            BigDecimal fee = calculator.feeFor(new BigDecimal("50.00"), ShippingSpeed.STANDARD);
            assertEquals(BigDecimal.ZERO, fee);
        }

        @Test
        @DisplayName("charges the standard fee below the threshold")
        void feeFor_belowThreshold_chargesStandardFee() {
            BigDecimal fee = calculator.feeFor(new BigDecimal("49.99"), ShippingSpeed.STANDARD);
            assertEquals(new BigDecimal("4.99"), fee);
        }
    }

    @ParameterizedTest(name = "{0} order with {1} shipping costs {2}")
    @CsvSource({
        "10.00, EXPRESS,   9.98",
        "10.00, OVERNIGHT, 19.96",
        "60.00, EXPRESS,   9.98",   // free-shipping discount only applies to STANDARD
        "60.00, OVERNIGHT, 19.96"
    })
    @DisplayName("express and overnight fees ignore the free-shipping threshold")
    void feeFor_expressAndOvernight_neverFree(BigDecimal total, ShippingSpeed speed, BigDecimal expectedFee) {
        assertEquals(expectedFee, calculator.feeFor(total, speed));
    }
}
```

This class demonstrates: `@Nested` grouping by scenario, `@DisplayName` for readable reports, a parameterized test for the combinatorial cases, and assertions that check both the *type* and the *content* of thrown exceptions.

## Mocking (Mockito)

### What to Mock and What NOT to Mock

Mocking is a tool for isolating the class under test from its **collaborators with real-world side effects**: databases, HTTP clients, message queues, clocks, random number generators, email senders. It is not a tool for isolating a class from everything it touches.

**Do mock:**
- Repositories/DAOs (avoid hitting a real database in a unit test)
- External service clients (payment gateway, email provider, third-party API)
- Anything with side effects you don't want during a test (file system, network)

**Do NOT mock:**
- **Value objects / DTOs** (`Money`, `Address`, records, simple POJOs). These have no behavior worth faking — just construct a real instance. Mocking a value object produces an object whose `equals()`/`getters` are unpredictable stubs instead of real values, which actively makes the test harder to trust.

```java
// Bad: mocking a plain value object
Money mockMoney = mock(Money.class);
when(mockMoney.amount()).thenReturn(new BigDecimal("10.00"));

// Good: it's just data — construct it
Money money = new Money(new BigDecimal("10.00"), "USD");
```

- **Types you don't own** (JDK classes, third-party library classes like `HttpClient`, `Connection`, `File`). Mocking framework internals couples your tests to implementation details of a library that can change between versions, and often the type isn't even designed to be mocked (`final` classes/methods, complex internal state). Instead, **wrap it** in a small interface you own, and mock *that* interface.

```java
// Bad: mocking java.time.Clock's cousin, a third-party HttpClient directly everywhere
HttpClient mockClient = mock(HttpClient.class);
when(mockClient.send(any(), any())).thenReturn(fakeResponse());

// Good: define a narrow seam you control
public interface WeatherClient {
    WeatherReport fetch(String city);
}
// production implementation wraps the real HttpClient/SDK
// test code mocks WeatherClient, a one-method interface you own
WeatherClient mockClient = mock(WeatherClient.class);
when(mockClient.fetch("Berlin")).thenReturn(new WeatherReport(18, "cloudy"));
```

- **The class under test itself.** If you find yourself mocking half the methods on the very class you're testing, that's a sign the class needs to be split, not mocked around.

### Creating Mocks: `mock()`, `@Mock`, `@InjectMocks`, `@Spy`

The programmatic form:

```java
UserRepository userRepository = mock(UserRepository.class);
```

The annotation form, wired up by `MockitoExtension` (Mockito's JUnit 5 integration):

```java
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.Spy;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.*;

@ExtendWith(MockitoExtension.class)
class UserRegistrationServiceTest {

    @Mock
    private UserRepository userRepository;

    @Mock
    private EmailSender emailSender;

    @Spy
    private AuditLog auditLog = new AuditLog(); // real object, selectively overridable

    @InjectMocks
    private UserRegistrationService registrationService; // Mockito injects the mocks above via constructor

    @Test
    void register_newEmail_savesUserAndSendsWelcomeEmail() {
        when(userRepository.existsByEmail("alice@example.com")).thenReturn(false);

        registrationService.register("alice@example.com", "Alice");

        verify(userRepository).save(any(User.class));
        verify(emailSender).sendWelcomeEmail("alice@example.com");
    }
}
```

`@InjectMocks` tries constructor injection first (preferred — it fails loudly at compile time if the constructor shape changes), then setter injection, then field injection. If `UserRegistrationService` has a single constructor taking `UserRepository`, `EmailSender`, and `AuditLog`, Mockito matches the `@Mock`/`@Spy` fields to those parameters by type.

`@Spy` wraps a **real** object: calls go to the real implementation unless you explicitly stub them, and Mockito still records every call for `verify(...)`. Use a spy when you want most of the real behavior but need to intercept one troublesome method (e.g., a legacy class that does real I/O in one method you want to fake out for the test).

### Stubbing

**`when().thenReturn(...)`** is the everyday form for stubbing non-void methods on a mock:

```java
when(userRepository.findById(1L)).thenReturn(Optional.of(new User(1L, "Alice")));
```

**`thenThrow`** stubs an exception:

```java
when(paymentGateway.charge(any())).thenThrow(new PaymentDeclinedException("insufficient funds"));
```

**`thenAnswer`** for computed/dynamic responses based on the actual arguments:

```java
when(userRepository.save(any(User.class))).thenAnswer(invocation -> {
    User user = invocation.getArgument(0);
    return user.withId(42L); // simulate the DB assigning an id
});
```

**Consecutive returns** — different answers on successive calls, useful for simulating retry logic:

```java
when(flakyClient.fetch())
    .thenThrow(new IOException("timeout"))
    .thenReturn("success"); // first call throws, second call (and beyond) returns "success"
```

### `doReturn`/`doThrow`/`doNothing().when(...)` — and When You Must Use It

The `when(mock.method()).thenReturn(...)` form has a hard limitation: it actually *calls* the real method on the mock first to figure out what to stub. That is invisible for a plain mock (Mockito intercepts the call before any real code runs), but it breaks down in two situations:

1. **Spies** — because a spy *does* run real code, `when(spy.riskyMethod()).thenReturn(...)` would execute the real (possibly side-effecting or exception-throwing) method before Mockito even gets to stub it.
2. **Void methods** — `when(...)` needs a return value to chain off of; a void method call has none, so there is nothing to pass to `when(...)`.

The `do...().when(mock)` family avoids this by stubbing without invoking the real method first:

```java
@Spy
private OrderValidator validator = new OrderValidator(); // real object; validate() does real, expensive checks

@Test
void skipsExpensiveValidationInThisTest() {
    doReturn(true).when(validator).isPreValidated(any()); // safe on a spy; when(validator...) would run the real check first

    // ...
}

@Mock
private NotificationService notificationService;

@Test
void notify_whenServiceUnavailable_doesNothingInsteadOfThrowing() {
    doThrow(new ServiceUnavailableException()).when(notificationService).notifyUser(any()); // void method

    orderService.completeOrder(order); // should swallow the notification failure gracefully

    verify(notificationService).notifyUser(order.customerId());
}

@Test
void suppressesAuditLoggingDuringThisScenario() {
    doNothing().when(auditLog).record(any()); // void method, explicit no-op
    // ...
}
```

Rule of thumb for reviewers: `when(...).thenReturn(...)` for normal mocks and non-void methods; `doX().when(...)` whenever the target is a **spy** or the method is **void**.

### Argument Matchers

Matchers let you stub/verify based on argument *shape* rather than an exact instance:

```java
when(userRepository.findByEmail(anyString())).thenReturn(Optional.empty());
when(userRepository.findById(eq(1L))).thenReturn(Optional.of(alice));
when(pricingEngine.priceFor(argThat(order -> order.items().size() > 5)))
    .thenReturn(new BigDecimal("0.00")); // free for large orders, expressed as a custom predicate
```

**The critical rule: you cannot mix raw values and matchers in the same call.** If *any* argument uses a matcher, *every* argument in that call must use a matcher (use `eq(...)` to wrap literal values).

```java
// Throws InvalidUseOfMatchersException at runtime
when(userService.updateEmail(1L, anyString())).thenReturn(true);

// Correct: wrap the literal in eq(...) once any() is used elsewhere
when(userService.updateEmail(eq(1L), anyString())).thenReturn(true);
```

This is a very common copy-paste mistake in review — a matcher gets added to one argument while a neighboring literal is left bare, and the test blows up with a confusing runtime exception instead of a compile error.

### Verification

Stubbing controls what a mock *returns*; verification checks what was *called*.

```java
verify(emailSender).sendWelcomeEmail("alice@example.com");      // called exactly once, with this argument
verify(emailSender, times(2)).sendReminder(any());               // called exactly twice
verify(emailSender, never()).sendWelcomeEmail("blocked@example.com");
verify(emailSender, atLeast(1)).flush();
verify(emailSender, atMost(3)).retry();

InOrder inOrder = inOrder(userRepository, emailSender);
inOrder.verify(userRepository).save(any(User.class));
inOrder.verify(emailSender).sendWelcomeEmail(anyString());       // save() must happen before the email is sent

verify(userRepository).save(any(User.class));
verify(emailSender).sendWelcomeEmail(anyString());
verifyNoMoreInteractions(userRepository, emailSender);            // fails if any OTHER call happened on these mocks
```

`verifyNoMoreInteractions` is a strong assertion — it fails the test if the code under test called anything on that mock beyond what you've already verified. It's a good way to lock down that a method does *exactly* what you expect and nothing extra, but overusing it on every mock in every test makes tests brittle against harmless refactors (e.g., adding a debug-log call). Use it selectively, on the interactions that genuinely matter.

### ArgumentCaptor

When you need to inspect the *value* an argument had, not just that a call happened, capture it:

```java
@Captor
private ArgumentCaptor<User> userCaptor;

@Test
void register_savesUserWithNormalizedEmail() {
    registrationService.register("  Alice@Example.com ", "Alice");

    verify(userRepository).save(userCaptor.capture());
    User savedUser = userCaptor.getValue();

    assertEquals("alice@example.com", savedUser.email()); // trimmed and lower-cased
}
```

`ArgumentCaptor` is essential when a mock's method returns `void` (or nothing you need) but you still want to assert on exactly what was passed in — verification alone tells you a call happened; the captor tells you what it looked like.

### `RETURNS_DEEP_STUBS` and Why It's a Smell

Mockito can auto-generate stubs for chained calls:

```java
// Works, but...
OrderContext context = mock(OrderContext.class, Mockito.RETURNS_DEEP_STUBS);
when(context.getCustomer().getAddress().getCountry()).thenReturn("DE");
```

This "solves" a symptom while hiding the real problem: a chain like `context.getCustomer().getAddress().getCountry()` violates the **Law of Demeter** — the code under test is reaching three objects deep into someone else's internals. `RETURNS_DEEP_STUBS` makes that chain *mockable*, but it does not make it *good design*. The test passing this way is a strong signal that the production code should instead depend directly on a `Country` (passed in, or obtained through a single well-named method like `context.getCustomerCountry()`), which would need no deep stub at all.

### Strict Stubs and `UnnecessaryStubbingException`

`MockitoExtension` runs in **strict stubbing** mode by default: if you stub something that the test never actually calls, the test fails with `UnnecessaryStubbingException` (or, depending on version/settings, a warning that fails on `Mockito.validateMockitoUsage()`), rather than silently passing.

```java
@ExtendWith(MockitoExtension.class)
class DiscountServiceTest {

    @Mock
    private CouponRepository couponRepository;

    @Test
    void appliesDiscount() {
        when(couponRepository.findByCode("SAVE10")).thenReturn(Optional.of(new Coupon(10)));
        // If discountService never actually calls couponRepository.findByCode("SAVE10")
        // (e.g., because the test forgot to pass a coupon code), this line is flagged
        // as an UnnecessaryStubbingException — a leftover stub from a copy-pasted test.
        ...
    }
}
```

This is a deliberately helpful failure: unused stubs accumulate in copy-pasted tests and quietly rot, giving a false sense of what's actually being exercised. Treat `UnnecessaryStubbingException` as a signal to delete the stub or fix the test, never to silence it with `lenient()` unless there's a genuinely good reason (e.g., a shared `@BeforeEach` stub that only some tests in the class need).

```java
lenient().when(couponRepository.findByCode(any())).thenReturn(Optional.empty()); // opt out of strict checking, deliberately
```

### Mocking Static Methods

Mockito 3.4+ supports mocking static methods via `mockStatic`, using inline mocking:

```java
try (MockedStatic<Instant> mockedInstant = Mockito.mockStatic(Instant.class)) {
    mockedInstant.when(Instant::now).thenReturn(Instant.parse("2026-08-07T00:00:00Z"));

    Report report = reportGenerator.generateDailyReport();

    assertEquals(LocalDate.parse("2026-08-07"), report.date());
}
```

This works, and the try-with-resources scoping (the static mock is only active inside the block) is important to prevent it from leaking into other tests. But **needing this at all is usually a design smell**: it means the production code called a static method directly (`Instant.now()`, a static utility, a singleton accessor) instead of depending on an injectable abstraction. The preferred fix, shown earlier in this chapter, is to inject a `Clock` (or wrap the static call behind an interface you own) — `mockStatic` should be a last resort for code you cannot easily refactor (e.g., a legacy static utility from a third-party library), not a first choice for new code.

### `MockedConstruction`

Similarly, `mockConstruction` intercepts `new SomeType(...)` calls made *inside* the code under test, redirecting them to a mock — useful when legacy code directly instantiates a collaborator instead of receiving it via dependency injection:

```java
try (MockedConstruction<PdfRenderer> mocked = Mockito.mockConstruction(PdfRenderer.class,
        (mockRenderer, context) -> when(mockRenderer.render(any())).thenReturn(new byte[] { 1, 2, 3 }))) {

    byte[] pdf = invoiceService.generatePdf(invoice); // internally does `new PdfRenderer()`

    assertArrayEquals(new byte[] { 1, 2, 3 }, pdf);
}
```

Like `mockStatic`, treat this as a stopgap while working with un-refactorable legacy code, not a pattern to reach for in new code — the sustainable fix is to inject `PdfRenderer` (or a factory for it) so tests can simply pass in a mock or fake.

### BDDMockito

`BDDMockito` provides `given`/`willReturn`/`then` aliases that read more naturally in Given-When-Then style tests — purely stylistic, functionally identical to the plain Mockito API:

```java
import static org.mockito.BDDMockito.*;

@Test
void register_newEmail_savesUserAndSendsWelcomeEmail_bdd() {
    // Given
    given(userRepository.existsByEmail("alice@example.com")).willReturn(false);

    // When
    registrationService.register("alice@example.com", "Alice");

    // Then
    then(userRepository).should().save(any(User.class));
    then(emailSender).should().sendWelcomeEmail("alice@example.com");
}
```

Teams pick one style (plain Mockito or BDDMockito) and stay consistent — mixing `when(...)` and `given(...)` in the same codebase is a minor but real readability nit reviewers flag.

### A Full Realistic Example: Service with a Repository and a Clock

```java
// Production code
public class LoanEligibilityService {

    private final LoanRepository loanRepository;
    private final Clock clock;

    public LoanEligibilityService(LoanRepository loanRepository, Clock clock) {
        this.loanRepository = loanRepository;
        this.clock = clock;
    }

    public EligibilityResult checkEligibility(String customerId) {
        List<Loan> activeLoans = loanRepository.findActiveLoansFor(customerId);

        boolean hasOverdueLoan = activeLoans.stream()
            .anyMatch(loan -> loan.dueDate().isBefore(LocalDate.now(clock)));

        if (hasOverdueLoan) {
            return EligibilityResult.denied("has an overdue loan");
        }
        if (activeLoans.size() >= 3) {
            return EligibilityResult.denied("too many active loans");
        }
        return EligibilityResult.approved();
    }
}
```

```java
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.*;
import java.util.List;

import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.*;

@ExtendWith(MockitoExtension.class)
class LoanEligibilityServiceTest {

    private static final Clock FIXED_CLOCK =
        Clock.fixed(Instant.parse("2026-08-07T00:00:00Z"), ZoneOffset.UTC);

    @Mock
    private LoanRepository loanRepository;

    private LoanEligibilityService service;

    @org.junit.jupiter.api.BeforeEach
    void setUp() {
        service = new LoanEligibilityService(loanRepository, FIXED_CLOCK);
    }

    @Test
    void checkEligibility_noActiveLoans_isApproved() {
        when(loanRepository.findActiveLoansFor("cust-1")).thenReturn(List.of());

        EligibilityResult result = service.checkEligibility("cust-1");

        assertTrue(result.approved());
    }

    @Test
    void checkEligibility_hasOverdueLoan_isDenied() {
        Loan overdue = new Loan("loan-1", LocalDate.parse("2026-08-01")); // before fixed "now"
        when(loanRepository.findActiveLoansFor("cust-1")).thenReturn(List.of(overdue));

        EligibilityResult result = service.checkEligibility("cust-1");

        assertFalse(result.approved());
        assertEquals("has an overdue loan", result.reason());
    }

    @Test
    void checkEligibility_threeActiveLoans_isDenied() {
        List<Loan> loans = List.of(
            new Loan("loan-1", LocalDate.parse("2026-12-01")),
            new Loan("loan-2", LocalDate.parse("2026-12-01")),
            new Loan("loan-3", LocalDate.parse("2026-12-01"))
        );
        when(loanRepository.findActiveLoansFor("cust-1")).thenReturn(loans);

        EligibilityResult result = service.checkEligibility("cust-1");

        assertFalse(result.approved());
        assertEquals("too many active loans", result.reason());
    }

    @Test
    void checkEligibility_queriesRepositoryExactlyOnceForTheGivenCustomer() {
        when(loanRepository.findActiveLoansFor(anyString())).thenReturn(List.of());

        service.checkEligibility("cust-42");

        verify(loanRepository, times(1)).findActiveLoansFor("cust-42");
        verifyNoMoreInteractions(loanRepository);
    }
}
```

Notice the fixed `Clock` makes the "overdue" scenario completely deterministic — no reliance on the real system date, so this test will pass identically today and five years from now.

### Over-Mocking as an Anti-Pattern: Prefer a Fake

Mocking every collaborator, including simple, deterministic ones, produces tests that verify *wiring* instead of *behavior* — they pass even when the real logic is subtly wrong, because every answer was hand-fed by `when(...)`.

**Before — over-mocked, brittle, and barely tests anything real:**

```java
@ExtendWith(MockitoExtension.class)
class ShoppingCartServiceOverMockedTest {

    @Mock
    private InventoryRepository inventoryRepository; // in-memory-friendly; doesn't need mocking

    @Mock
    private PricingRepository pricingRepository;      // same

    @InjectMocks
    private ShoppingCartService cartService;

    @Test
    void addItem_computesTotalCorrectly() {
        when(inventoryRepository.hasStock("sku-1", 2)).thenReturn(true);
        when(pricingRepository.priceOf("sku-1")).thenReturn(new BigDecimal("9.99"));

        cartService.addItem("sku-1", 2);

        // This assertion mostly re-proves the mock's own stubbed values, not the real
        // multiplication logic inside ShoppingCartService — a bug in the multiplication
        // could still pass if the stubs happen to line up.
        assertEquals(new BigDecimal("19.98"), cartService.total());
    }
}
```

Both `InventoryRepository` and `PricingRepository` are simple, deterministic, side-effect-free lookups — exactly the kind of collaborator better served by a real (if simplified) in-memory implementation than a mock.

**After — a real in-memory fake exercises the actual logic:**

```java
class InMemoryInventoryRepository implements InventoryRepository {
    private final Map<String, Integer> stock = new HashMap<>();

    void setStock(String sku, int quantity) { stock.put(sku, quantity); }

    @Override
    public boolean hasStock(String sku, int quantity) {
        return stock.getOrDefault(sku, 0) >= quantity;
    }
}

class InMemoryPricingRepository implements PricingRepository {
    private final Map<String, BigDecimal> prices = new HashMap<>();

    void setPrice(String sku, BigDecimal price) { prices.put(sku, price); }

    @Override
    public BigDecimal priceOf(String sku) {
        return prices.getOrDefault(sku, BigDecimal.ZERO);
    }
}

class ShoppingCartServiceFakeTest {

    private final InMemoryInventoryRepository inventoryRepository = new InMemoryInventoryRepository();
    private final InMemoryPricingRepository pricingRepository = new InMemoryPricingRepository();
    private final ShoppingCartService cartService =
        new ShoppingCartService(inventoryRepository, pricingRepository);

    @Test
    void addItem_computesTotalCorrectly() {
        inventoryRepository.setStock("sku-1", 5);
        pricingRepository.setPrice("sku-1", new BigDecimal("9.99"));

        cartService.addItem("sku-1", 2);

        assertEquals(new BigDecimal("19.98"), cartService.total());
    }

    @Test
    void addItem_insufficientStock_throws() {
        inventoryRepository.setStock("sku-1", 1);

        assertThrows(InsufficientStockException.class, () -> cartService.addItem("sku-1", 2));
    }
}
```

The fake-based test is no more verbose, needs zero mocking annotations, reads like plain Java, and — crucially — actually exercises real lookup logic (`getOrDefault`, map storage) instead of blindly returning whatever was stubbed. Reserve mocks for the collaborators that genuinely have side effects (network, disk, email, payment) worth avoiding in a unit test; reach for a small fake for everything else.

| | Mock | Stub | Fake | Spy |
|---|---|---|---|---|
| Real logic runs? | No | No | Yes (simplified) | Yes, unless overridden |
| Primary purpose | Verify interactions | Feed canned data | Realistic lightweight substitute | Observe + selectively override |
| Created with | `mock()` / `@Mock` | `mock()` + `when()` | Hand-written class implementing the interface | `spy()` / `@Spy` |
| Review red flag | Excessive `verifyNoMoreInteractions` on trivial calls | Stubbing something never exercised | None — often *reduces* smell | Spying on the class under test itself |

## Common Code-Review Interview Pitfalls

1. **Tests with no assertions ("coverage theater").**
   Why it matters: the test executes code and reports green, but proves nothing — a regression can slip through untouched.
   ```java
   // Before
   @Test
   void processOrder_works() { orderService.process(order); }
   // After
   @Test
   void processOrder_marksOrderAsShipped() {
       orderService.process(order);
       assertEquals(OrderStatus.SHIPPED, order.status());
   }
   ```

2. **Meaningless test names (`test1`, `testFoo`, `shouldWork`).**
   Why it matters: a failing test in a CI report should be diagnosable from its name alone; vague names force everyone to open the source to understand what broke.
   ```java
   // Before
   @Test void test1() { ... }
   // After
   @Test void withdraw_insufficientFunds_throwsIllegalStateException() { ... }
   ```

3. **Using `Thread.sleep` to wait for async work instead of a latch or Awaitility.**
   Why it matters: fixed sleeps are either wastefully slow or flaky under load — a frequent source of "works on my machine, fails on CI."
   ```java
   // Before
   Thread.sleep(500); assertTrue(flag.get());
   // After
   assertTrue(latch.await(2, TimeUnit.SECONDS)); assertTrue(flag.get());
   ```

4. **Calling `LocalDate.now()`/`Instant.now()` directly in production code instead of injecting a `Clock`.**
   Why it matters: makes date/time logic untestable deterministically, leading to tests that only fail once a year or in a different timezone.
   ```java
   // Before
   boolean expired = expiry.isBefore(LocalDate.now());
   // After
   boolean expired = expiry.isBefore(LocalDate.now(clock)); // clock injected in constructor
   ```

5. **Mixing raw argument values and Mockito matchers in the same call.**
   Why it matters: throws `InvalidUseOfMatchersException` at runtime instead of failing at compile time — a confusing failure mode for anyone unfamiliar with the rule.
   ```java
   // Before
   when(userService.updateEmail(1L, anyString())).thenReturn(true);
   // After
   when(userService.updateEmail(eq(1L), anyString())).thenReturn(true);
   ```

6. **Using `when(...).thenReturn(...)` on a spy for a method with real side effects.**
   Why it matters: the real method runs first (before Mockito applies the stub), triggering whatever expensive/exceptional behavior you were trying to avoid.
   ```java
   // Before
   when(spyValidator.expensiveCheck(order)).thenReturn(true);
   // After
   doReturn(true).when(spyValidator).expensiveCheck(order);
   ```

7. **Mocking value objects/DTOs instead of just constructing them.**
   Why it matters: produces an object whose getters return arbitrary stubbed values instead of real data, weakening the test's connection to reality and adding boilerplate for no benefit.
   ```java
   // Before
   Money mockMoney = mock(Money.class); when(mockMoney.amount()).thenReturn(TEN);
   // After
   Money money = new Money(TEN, "USD");
   ```

8. **Over-mocking simple, deterministic collaborators instead of using a real in-memory fake.**
   Why it matters: the test ends up verifying that stubs return what they were told to return, not that the real logic (calculations, branching) is correct.
   ```java
   // Before
   when(pricingRepository.priceOf("sku-1")).thenReturn(new BigDecimal("9.99"));
   // After
   pricingRepository.setPrice("sku-1", new BigDecimal("9.99")); // InMemoryPricingRepository fake
   ```

9. **`@Disabled` with no reason string or tracked follow-up ticket.**
   Why it matters: skipped tests are easy to forget forever, silently eroding coverage without anyone noticing.
   ```java
   // Before
   @Disabled
   @Test void flakyTest() { ... }
   // After
   @Disabled("Flaky under load; see TICKET-1234")
   @Test void flakyTest() { ... }
   ```

10. **Reaching for `RETURNS_DEEP_STUBS` instead of fixing a Law-of-Demeter violation.**
    Why it matters: it makes a bad dependency chain (`a.getB().getC().getD()`) testable without questioning why the code reaches three objects deep into someone else's internals.
    ```java
    // Before
    when(context.getCustomer().getAddress().getCountry()).thenReturn("DE");
    // After
    when(context.getCustomerCountry()).thenReturn("DE"); // narrower, single-hop dependency
    ```

11. **Asserting only the exception type, ignoring the message/state.**
    Why it matters: the wrong code path can throw the same exception type for a different reason, and the test would still pass, masking a real bug.
    ```java
    // Before
    assertThrows(IllegalArgumentException.class, () -> service.transfer(-10));
    // After
    var ex = assertThrows(IllegalArgumentException.class, () -> service.transfer(-10));
    assertEquals("transfer amount must be positive: -10", ex.getMessage());
    ```

12. **Chasing 100% line coverage instead of testing edge cases and failure paths.**
    Why it matters: coverage percentage measures execution, not verification; time spent testing trivial getters is time not spent on the branch that actually breaks in production.
    ```java
    // Before: test exists purely to hit the line
    @Test void getName_doesNotThrow() { assertNotNull(user.getName()); }
    // After: test the behavior that matters
    @Test void withdraw_exactBalance_leavesZeroBalance() { ... }
    ```

13. **Ignoring `UnnecessaryStubbingException` by sprinkling `lenient()` everywhere.**
    Why it matters: strict stubbing exists to catch leftover, copy-pasted stubs that no longer reflect what the test exercises; blanket `lenient()` defeats that safety net.
    ```java
    // Before
    lenient().when(repo.findAll()).thenReturn(List.of()); // added to silence a warning, never investigated
    // After
    // delete the unused stub, or confirm and document why it's legitimately conditional
    ```

14. **One giant test method covering five unrelated scenarios instead of `@ParameterizedTest` or separate tests.**
    Why it matters: a failure only says "somewhere in this 80-line test something broke," instead of pinpointing which scenario regressed.
    ```java
    // Before
    @Test void allDiscountScenarios() { /* 5 unrelated asserts */ }
    // After
    @ParameterizedTest
    @CsvSource({"REGULAR,0", "PREMIUM,10", "VIP,20"})
    void discount_variesByTier(CustomerTier tier, int expectedPercent) { ... }
    ```

15. **Depending on test execution order (state leaking between tests via static/shared fields).**
    Why it matters: JUnit does not guarantee method order; a suite that only passes in one order is a latent flaky-test time bomb waiting for a JUnit version bump or parallel execution to expose it.
    ```java
    // Before
    static User sharedUser; // set in test A, read in test B
    // After
    @BeforeEach void setUp() { user = new User(...); } // fresh per test
    ```

16. **Mocking a type you don't own (JDK/third-party class) instead of wrapping it behind your own seam.**
    Why it matters: couples tests to a library's internal shape, breaks across library upgrades, and often fights `final` classes/methods that resist mocking cleanly.
    ```java
    // Before
    HttpClient mockClient = mock(HttpClient.class); // fragile, tightly coupled to the SDK
    // After
    WeatherClient mockClient = mock(WeatherClient.class); // your own one-method interface
    ```

17. **`mockStatic`/`mockConstruction` used as a first choice for new code instead of dependency injection.**
    Why it matters: static mocking is a workaround for code that can't be injected; reaching for it in new code perpetuates hard-to-test designs instead of fixing them.
    ```java
    // Before
    try (MockedStatic<Instant> m = mockStatic(Instant.class)) { m.when(Instant::now).thenReturn(fixed); ... }
    // After
    // inject java.time.Clock into the constructor and use Clock.fixed(...) in the test
    ```

18. **Blanket `verifyNoMoreInteractions` on every mock in every test, making refactors brittle.**
    Why it matters: adding an unrelated, harmless call (e.g., a new debug log) breaks unrelated tests, training the team to treat verification failures as noise instead of signal.
    ```java
    // Before
    verifyNoMoreInteractions(userRepository, emailSender, auditLog, metricsClient); // on every test
    // After
    verify(emailSender).sendWelcomeEmail(anyString()); // verify only the interactions that matter to this test
    ```
