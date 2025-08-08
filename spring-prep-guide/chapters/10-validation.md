# 10. Validation

## Overview

Validation is how an application makes sure the data it receives is actually usable before it acts on it. Instead of scattering `if (name == null) throw ...` checks everywhere, Spring applications use **Bean Validation**: a declarative, annotation-based way of saying "this field must not be blank" or "this number must be positive." The framework then checks these rules for you at the right moment — when a web request comes in, when a method is called, or when you manually trigger a check. This chapter walks through the standard annotations, how to write your own rules, how to validate several fields together, and how to turn failed validation into a clean error response for API clients. By the end you should be able to explain the difference between `@Valid` and `@Validated`, and between the two main validation exceptions Spring throws.

## Bean Validation

**Bean Validation** is a Java specification (not a Spring feature) that defines a standard set of annotations for describing constraints on fields, method parameters, and return values. Think of it as a checklist you attach directly to your data class: instead of writing validation logic by hand, you *declare* the rules, and a validation engine reads the annotations and enforces them for you.

The specification itself is just annotations and interfaces (`jakarta.validation.*`). It needs an **implementation** to actually do the checking. The reference implementation almost everyone uses is **Hibernate Validator** (nothing to do with Hibernate ORM — it's a separate library that happens to come from the same project).

```java
public class RegisterRequest {

    @NotBlank
    private String username;

    @Email
    private String email;

    @Min(18)
    private int age;
}
```

To use Bean Validation in a Spring Boot project you need one dependency:

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-validation</artifactId>
</dependency>
```

This starter pulls in Hibernate Validator and wires it into Spring MVC automatically. Without it, `@Valid` annotations are silently ignored (or you get a startup error if you're missing the API too) — this is one of the most common "why isn't my validation working" bugs.

Key vocabulary you'll see throughout this chapter:

| Term | Meaning |
|---|---|
| **Constraint** | A single rule, expressed as an annotation, e.g. `@NotNull` |
| **Constraint annotation** | The annotation itself, e.g. `@Size(min = 2, max = 30)` |
| **ConstraintValidator** | The class that contains the actual Java logic behind a constraint |
| **Validator** | The engine that runs constraints against an object and collects violations |
| **ConstraintViolation** | A single failure: which property, which message, which invalid value |

## Jakarta Validation

**Jakarta Validation** is simply the current name of the Bean Validation specification. For years it was called **Java Bean Validation** and lived under the `javax.validation.*` package. When Java EE moved to the Eclipse Foundation and was rebranded **Jakarta EE**, the packages were renamed from `javax.*` to `jakarta.*`. Spring Boot 3.x and Spring Framework 6.x moved entirely to Jakarta EE 9+, so **all validation imports must use `jakarta.validation.*`**, not `javax.validation.*`.

| | Spring Boot 2.x | Spring Boot 3.x |
|---|---|---|
| Package prefix | `javax.validation.*` | `jakarta.validation.*` |
| Spec version | Bean Validation 2.0 | Jakarta Validation 3.0 |
| Servlet API | `javax.servlet.*` | `jakarta.servlet.*` |

```java
// Spring Boot 3.x — correct
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Email;

// Spring Boot 2.x — legacy, will NOT compile against Boot 3
import javax.validation.constraints.NotNull;
```

If you copy a code snippet from an old tutorial and get confusing "cannot find symbol" errors, check the import package first — it is almost always a leftover `javax.*` import.

The specification defines two closely related artifacts:

- `jakarta.validation:jakarta.validation-api` — the annotations and interfaces (the "contract").
- `org.hibernate.validator:hibernate-validator` — the actual engine that implements the contract.

Spring Boot's `spring-boot-starter-validation` bundles both, so in practice you rarely add them individually.

You can also inject a `Validator` bean directly if you want to validate an object programmatically, outside of a web request:

```java
import jakarta.validation.Validator;
import jakarta.validation.ConstraintViolation;
import java.util.Set;

@Service
public class ImportService {

    private final Validator validator;

    public ImportService(Validator validator) {
        this.validator = validator;
    }

    public void importRow(RegisterRequest request) {
        Set<ConstraintViolation<RegisterRequest>> violations = validator.validate(request);
        if (!violations.isEmpty()) {
            throw new IllegalArgumentException("Invalid row: " + violations);
        }
        // proceed with valid data
    }
}
```

### Reference table of built-in constraints

| Annotation | What it checks | Applies to |
|---|---|---|
| `@NotNull` | Value must not be `null` (empty string/collection is fine) | Any type |
| `@NotEmpty` | Must not be `null` and must have length/size > 0 | `String`, `Collection`, `Map`, arrays |
| `@NotBlank` | Must not be `null` and, after trimming, must contain at least one non-whitespace character | `String` (and `CharSequence`) |
| `@Size(min=, max=)` | Length/size must be within range | `String`, `Collection`, `Map`, arrays |
| `@Min(value)` | Numeric value must be ≥ value | Numeric types, `BigDecimal`, `BigInteger` |
| `@Max(value)` | Numeric value must be ≤ value | Numeric types, `BigDecimal`, `BigInteger` |
| `@Positive` | Must be > 0 | Numeric types |
| `@PositiveOrZero` | Must be ≥ 0 | Numeric types |
| `@Negative` | Must be < 0 | Numeric types |
| `@NegativeOrZero` | Must be ≤ 0 | Numeric types |
| `@Email` | Must look like a valid email address | `String` |
| `@Pattern(regexp=)` | Must match a regular expression | `String` |
| `@Past` | Date/time must be in the past | `LocalDate`, `LocalDateTime`, `Date`, etc. |
| `@PastOrPresent` | Must be in the past or right now | Date/time types |
| `@Future` | Must be in the future | Date/time types |
| `@FutureOrPresent` | Must be in the future or right now | Date/time types |
| `@Digits(integer=, fraction=)` | Limits number of digits before/after decimal point | Numeric types |
| `@AssertTrue` | Value must be `true` | `boolean` / `Boolean` |
| `@AssertFalse` | Value must be `false` | `boolean` / `Boolean` |
| `@Null` | Value must be `null` | Any type |
| `@Valid` | Cascades validation into a nested object/collection element | Any type, esp. nested DTOs |

### `@Valid` vs `@Validated`

These two look similar and are frequently confused:

| | `@Valid` | `@Validated` |
|---|---|---|
| Package | `jakarta.validation.Valid` | `org.springframework.validation.annotation.Validated` |
| Origin | Standard Jakarta Validation annotation | Spring-specific annotation |
| Supports groups? | No | Yes — `@Validated(GroupA.class)` |
| Used on | Method parameters (to trigger validation), fields (to cascade into nested objects) | Classes (to enable method-level validation), or parameters when you need a group |
| Typical use | `@PostMapping` controller parameter; nested DTO field | `@Service` class + `@Validated` to enable parameter/return validation via AOP |

Rule of thumb: **use `@Valid` for request bodies and nested objects; use `@Validated` on a class when you want Spring to validate plain method parameters (not just `@RequestBody`), or when you need validation groups.**

## Custom Validators

Sometimes the built-in constraints aren't enough — you need a rule specific to your domain, like "this string must be a valid product SKU" or "this password must satisfy our complexity policy." Bean Validation lets you build your own constraint that looks exactly like `@NotNull` from the outside, but runs your own Java logic.

A custom constraint has two pieces:

1. **The annotation** — declares the constraint and points to a validator class.
2. **The `ConstraintValidator`** — contains the actual check.

```java
// PhoneNumber.java — the annotation
import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import java.lang.annotation.*;

@Target({ ElementType.FIELD, ElementType.PARAMETER })
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = PhoneNumberValidator.class)
public @interface PhoneNumber {

    String message() default "{com.example.constraints.PhoneNumber.message}";

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
```

```java
// PhoneNumberValidator.java — the logic
import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;

public class PhoneNumberValidator implements ConstraintValidator<PhoneNumber, String> {

    private static final String PATTERN = "^\\+?[0-9]{7,15}$";

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        if (value == null) {
            return true; // let @NotNull handle nullness separately
        }
        return value.matches(PATTERN);
    }
}
```

```properties
# src/main/resources/ValidationMessages.properties
com.example.constraints.PhoneNumber.message=Phone number must be 7-15 digits, optionally starting with '+'
```

Usage on a DTO:

```java
public class ContactRequest {

    @PhoneNumber
    private String phone;
}
```

Notes worth remembering:

- Bean Validation looks for `ValidationMessages.properties` on the classpath automatically — no extra configuration needed for the message bundle.
- Returning `true` for `null` inside your validator is a common convention: it lets you compose constraints (`@NotNull @PhoneNumber`) instead of duplicating null-checks everywhere.
- If your validator needs a Spring bean (e.g. a repository, to check uniqueness), just autowire it — Spring registers `ConstraintValidator` implementations as beans when you use `spring-boot-starter-validation`, so `@Autowired` works normally inside the validator class.

```java
public class UniqueEmailValidator implements ConstraintValidator<UniqueEmail, String> {

    private final UserRepository userRepository;

    public UniqueEmailValidator(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    @Override
    public boolean isValid(String email, ConstraintValidatorContext context) {
        return email == null || !userRepository.existsByEmail(email);
    }
}
```

## Groups

By default, every constraint on a class is checked every time you validate it. But sometimes the same DTO is reused in different situations with different rules — for example, an `id` field that must be `null` on creation but must not be `null` on update. **Validation groups** let you tag constraints so only a subset of them run for a given operation.

A group is just a marker interface — it carries no code, only a name to group by.

```java
public interface OnCreate {}
public interface OnUpdate {}
```

```java
public class UserRequest {

    @Null(groups = OnCreate.class)
    @NotNull(groups = OnUpdate.class)
    private Long id;

    @NotBlank(groups = { OnCreate.class, OnUpdate.class })
    private String name;

    @Email(groups = { OnCreate.class, OnUpdate.class })
    private String email;
}
```

To trigger a specific group, use `@Validated` (not plain `@Valid`, since `@Valid` cannot carry groups) on the controller method parameter:

```java
@RestController
@RequestMapping("/users")
public class UserController {

    @PostMapping
    public ResponseEntity<Void> create(@Validated(OnCreate.class) @RequestBody UserRequest request) {
        // ...
        return ResponseEntity.ok().build();
    }

    @PutMapping("/{id}")
    public ResponseEntity<Void> update(@Validated(OnUpdate.class) @RequestBody UserRequest request) {
        // ...
        return ResponseEntity.ok().build();
    }
}
```

Practical guidance:

- Constraints with **no** `groups` attribute belong to the implicit `Default` group, which always runs unless you explicitly restrict validation to another group.
- Groups are great for "same DTO, different lifecycle stage" scenarios. If you find yourself creating many groups for many unrelated rules, it's usually a sign you should just split the DTO into separate classes (e.g. `CreateUserRequest` and `UpdateUserRequest`) — simpler and easier to read.
- Groups can be combined into a hierarchy using `@GroupSequence` if you need ordered validation (stop at first failing group), but this is rarely needed for typical CRUD APIs.

## Cross-field Validation

Some rules need more than one field to make sense — "end date must be after start date," or "confirm password must match password." A single-field constraint like `@NotBlank` can't see other fields, so you need either a **class-level constraint** or the simpler `@AssertTrue` trick.

### Option 1: class-level custom constraint

```java
// DateRangeValid.java
import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import java.lang.annotation.*;

@Target({ ElementType.TYPE })
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = DateRangeValidator.class)
public @interface DateRangeValid {

    String message() default "End date must be after start date";

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
```

```java
// DateRangeValidator.java
import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;

public class DateRangeValidator implements ConstraintValidator<DateRangeValid, BookingRequest> {

    @Override
    public boolean isValid(BookingRequest request, ConstraintValidatorContext context) {
        if (request.getStartDate() == null || request.getEndDate() == null) {
            return true; // let @NotNull handle missing values
        }
        boolean valid = request.getEndDate().isAfter(request.getStartDate());
        if (!valid) {
            // Attach the error to a specific field instead of the whole object
            context.disableDefaultConstraintViolation();
            context.buildConstraintViolationWithTemplate("End date must be after start date")
                    .addPropertyNode("endDate")
                    .addConstraintViolation();
        }
        return valid;
    }
}
```

```java
@DateRangeValid
public class BookingRequest {

    @NotNull
    private LocalDate startDate;

    @NotNull
    private LocalDate endDate;

    // getters/setters
}
```

### Option 2: `@AssertTrue` on a derived boolean

For a quick one-off check, put the comparison logic in a private method on the DTO itself and expose it as a boolean getter — Bean Validation treats any `isXxx()`/`getXxx()` method returning `boolean`/`Boolean` as a validatable "property."

```java
public class PasswordChangeRequest {

    @NotBlank
    private String password;

    @NotBlank
    private String confirmPassword;

    @AssertTrue(message = "Passwords must match")
    private boolean isPasswordConfirmed() {
        return password != null && password.equals(confirmPassword);
    }
}
```

| Approach | Best for | Downside |
|---|---|---|
| Class-level `@Constraint` | Reusable rules, precise error targeting via `addPropertyNode` | More boilerplate (annotation + validator class) |
| `@AssertTrue` private method | Quick, one-off checks inside a single DTO | Error is less specific; harder to reuse across DTOs |

## Method Validation

So far every example validated a request body. **Method validation** extends the same idea to *any* Spring-managed bean method — service methods, `@Component` methods, repository methods — by putting constraints directly on parameters and return values.

Two ingredients are required:

1. `@Validated` on the **class** (this tells Spring to wrap the bean with an AOP proxy that intercepts method calls and checks constraints).
2. Constraint annotations on the **method parameters** and/or the **method itself** (for the return value).

```java
import jakarta.validation.constraints.*;
import org.springframework.validation.annotation.Validated;
import org.springframework.stereotype.Service;

@Service
@Validated
public class PricingService {

    public BigDecimal calculateDiscount(
            @NotNull @Positive BigDecimal price,
            @Min(0) @Max(100) int percentage) {
        return price.subtract(price.multiply(BigDecimal.valueOf(percentage / 100.0)));
    }

    @NotNull
    public BigDecimal findBasePrice(@NotBlank String sku) {
        // ...
        return null; // would trigger the @NotNull on the return value!
    }
}
```

Calling `calculateDiscount(null, 50)` or `calculateDiscount(price, 150)` throws a `ConstraintViolationException` immediately, before the method body even runs any business logic.

### `ConstraintViolationException` vs `MethodArgumentNotValidException`

This distinction trips up a lot of developers, and it matters because the two exceptions need different `@ExceptionHandler` methods.

| | `ConstraintViolationException` | `MethodArgumentNotValidException` |
|---|---|---|
| Package | `jakarta.validation.ConstraintViolationException` | `org.springframework.web.bind.MethodArgumentNotValidException` |
| Thrown when | `@Validated` method validation fails (plain parameters/return values), or manual `validator.validate()` calls that you re-throw | `@Valid`/`@Validated` fails on a `@RequestBody` or `@ModelAttribute` in a Spring MVC controller |
| Thrown by | Bean Validation itself / Spring AOP interceptor | Spring MVC's argument resolver |
| Contains | `Set<ConstraintViolation<?>>` | `BindingResult` with `FieldError` / `ObjectError` list |
| Typical trigger | Service-layer method call | Controller endpoint receiving a request body |

```java
@Service
@Validated
public class OrderService {
    public void placeOrder(@NotNull @Valid OrderRequest request) {
        // if request is null -> ConstraintViolationException
    }
}

@RestController
public class OrderController {
    @PostMapping("/orders")
    public void placeOrder(@Valid @RequestBody OrderRequest request) {
        // if request body fails validation -> MethodArgumentNotValidException
    }
}
```

Both exceptions ultimately mean "validation failed," but you must handle them separately (or handle both) in your global exception handler, shown next.

### Turning violations into a clean JSON error response

Letting a raw exception bubble up to the client produces an ugly, inconsistent 500-style response. A `@RestControllerAdvice` centralizes this into one consistent shape.

```java
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import jakarta.validation.ConstraintViolationException;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@RestControllerAdvice
public class ValidationExceptionHandler {

    // From @Valid on @RequestBody in controllers
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> handleBodyValidation(MethodArgumentNotValidException ex) {
        List<FieldErrorDetail> errors = ex.getBindingResult().getFieldErrors().stream()
                .map(fe -> new FieldErrorDetail(fe.getField(), fe.getDefaultMessage()))
                .collect(Collectors.toList());

        ErrorResponse body = new ErrorResponse(
                Instant.now(),
                HttpStatus.BAD_REQUEST.value(),
                "Validation failed",
                errors);

        return ResponseEntity.badRequest().body(body);
    }

    // From @Validated method validation in services/components
    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ErrorResponse> handleMethodValidation(ConstraintViolationException ex) {
        List<FieldErrorDetail> errors = ex.getConstraintViolations().stream()
                .map(v -> new FieldErrorDetail(v.getPropertyPath().toString(), v.getMessage()))
                .collect(Collectors.toList());

        ErrorResponse body = new ErrorResponse(
                Instant.now(),
                HttpStatus.BAD_REQUEST.value(),
                "Validation failed",
                errors);

        return ResponseEntity.badRequest().body(body);
    }

    public record ErrorResponse(
            Instant timestamp,
            int status,
            String message,
            List<FieldErrorDetail> errors) {}

    public record FieldErrorDetail(String field, String message) {}
}
```

Example response body produced for a failed `RegisterRequest`:

```json
{
  "timestamp": "2026-08-07T09:15:30Z",
  "status": 400,
  "message": "Validation failed",
  "errors": [
    { "field": "username", "message": "must not be blank" },
    { "field": "email", "message": "must be a well-formed email address" }
  ]
}
```

This gives every client a predictable, machine-parseable shape regardless of whether the failure came from a controller-level `@Valid` body or a service-level `@Validated` method.

## Common Code Review / Interview Pitfalls

- **Forgetting `@Valid` on the controller parameter.** Without it, Spring never triggers validation on the request body — the annotations on the DTO are just inert metadata.
  ❌ `public ResponseEntity<Void> create(@RequestBody UserRequest request)`
  ✅ `public ResponseEntity<Void> create(@Valid @RequestBody UserRequest request)`

- **`@NotNull` on a `String` when `@NotBlank` was intended.** `@NotNull` accepts `""` and `"   "` as valid, which usually isn't what you want for names, emails, etc.
  ❌ `@NotNull private String name;` (passes for `""`)
  ✅ `@NotBlank private String name;` (rejects `""` and whitespace-only)

- **Missing `@Valid` on nested objects.** Validation does not automatically cascade into nested DTOs — you must add `@Valid` on the field itself, or the nested object's constraints are silently skipped.
  ❌ `private Address address;`
  ✅ `@Valid private Address address;`

- **Missing `@Valid` on collection elements.** Same problem, one level deeper — a `List<Item>` needs `@Valid` to check each `Item`, not just that the list itself is non-null.
  ❌ `@NotEmpty private List<Item> items;`
  ✅ `@NotEmpty private List<@Valid Item> items;`

- **Validating inconsistently between controller and service.** If the controller validates a DTO but a service method that's called from multiple places (batch jobs, another service, a scheduled task) skips validation, invalid data can still slip through non-HTTP entry points. Put `@Validated` + constraints on the service method too if it can be invoked outside the web layer.

- **Stuffing business rules into bean validation constraints.** Bean Validation is for structural/format checks (blank, size, pattern). Rules like "user cannot place an order if their account is suspended" require a database lookup and belong in a service method with a proper domain exception — not a custom `ConstraintValidator` that quietly does a repository call. It works, but it's harder to test, mixes concerns, and makes constraints slow.

- **Confusing `@Valid` and `@Validated`.** `@Valid` cannot specify groups and does not enable method-level validation on a class by itself. If you need group-based validation, or you're validating plain (non-body) method parameters, you need `@Validated`.
  ❌ `@Valid(OnCreate.class)` — does not compile, `@Valid` has no attributes.
  ✅ `@Validated(OnCreate.class)`

- **Forgetting `@Validated` on the class for method validation.** Adding constraint annotations to a service method's parameters does nothing unless the class itself is annotated `@Validated` — that annotation is what triggers the AOP proxy that intercepts the call.
  ❌ `@Service public class PricingService { public void charge(@NotNull BigDecimal amount) {...} }` — never validated.
  ✅ `@Service @Validated public class PricingService { ... }`

- **`spring-boot-starter-validation` not on the classpath.** Without this starter, `@Valid`/`@Validated` annotations are present but nothing actually checks them, and there's often no obvious startup error — it just silently does nothing (or fails cryptically depending on version). Always confirm the dependency is declared, especially in a fresh project or a module that only recently added validated DTOs.

- **Catching only one of `MethodArgumentNotValidException` / `ConstraintViolationException`.** If your `@RestControllerAdvice` only handles `MethodArgumentNotValidException`, failures from `@Validated` service methods will leak out as generic 500 errors instead of clean 400 responses. Handle both.

- **Relying on default error messages in production APIs.** Default messages like "must not be null" are fine for internal tools but rarely match the tone or language of a public API. Set a custom `message` on important constraints and back it with `ValidationMessages.properties` for consistency and i18n.

- **Returning the whole entity/exception in the error response.** Returning `ex.toString()` or a raw `ConstraintViolation` object can leak internal class names or stack traces to clients. Map to a small, deliberate DTO (`field` + `message`) as shown in the `@RestControllerAdvice` example.

- **Assuming `@Size` works on numbers.** `@Size` only applies to `String`, `Collection`, `Map`, and arrays — for numeric ranges use `@Min`/`@Max` or `@Digits`. Putting `@Size` on an `int` field is a compile error, but it's easy to reach for the wrong annotation out of habit.

- **Validating a `null` object passed into a custom cross-field validator.** If your class-level `ConstraintValidator` doesn't guard against the whole object (or individual fields) being `null`, you get a `NullPointerException` instead of a clean violation. Always null-check inside `isValid()` and delegate nullness to the field-level `@NotNull`.

## Quick Recap

- Bean Validation is a specification; Hibernate Validator is the implementation Spring Boot uses by default.
- Add `spring-boot-starter-validation` or annotations silently do nothing.
- Spring Boot 3.x uses `jakarta.validation.*`, not `javax.validation.*`.
- `@Valid` triggers validation and cascades into nested objects/collection elements; it cannot express groups.
- `@Validated` is Spring's own annotation: it supports groups and, placed on a class, enables method-level validation via an AOP proxy.
- Use `@NotBlank` for meaningful strings, `@NotEmpty` for collections/strings that must have content, `@NotNull` when emptiness is fine but absence isn't.
- Write custom constraints as an annotation + a `ConstraintValidator<Annotation, Type>`, with messages in `ValidationMessages.properties`.
- Groups (marker interfaces) let the same DTO apply different rules for different operations (create vs. update); prefer separate DTOs if groups start multiplying.
- Cross-field rules need either a class-level `@Constraint` (precise, reusable) or a simple `@AssertTrue` derived boolean (quick, DTO-local).
- Method validation needs `@Validated` on the class plus constraints on parameters/return type; failures throw `ConstraintViolationException`, while controller body validation failures throw `MethodArgumentNotValidException`.
- Handle both exception types in a `@RestControllerAdvice` to produce one consistent JSON error shape for API clients.
- Keep bean validation for structural checks; keep business rules in the service layer.
