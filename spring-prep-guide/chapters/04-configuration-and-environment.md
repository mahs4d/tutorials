# 4. Configuration & Environment

## Overview

Every real application needs to behave differently depending on where it runs. A database URL on your laptop is not the same as the one in production. Spring Boot solves this with a flexible configuration system: you can put settings in files, environment variables, command-line arguments, or code, and Spring merges them all together using a predictable set of rules. Understanding this system is essential because misconfigured applications are one of the most common causes of "it worked on my machine" bugs. It's also a favorite interview topic because it touches design patterns (binding, validation), the framework's core (`Environment`), and everyday DevOps concerns (secrets, profiles).

## application.properties

`application.properties` is the classic Spring Boot configuration file. It's a plain text file of `key=value` pairs, loaded automatically from `src/main/resources`. Think of it as a big settings sheet that Spring reads on startup and uses to configure beans, ports, database connections, logging, and your own custom values.

```properties
# src/main/resources/application.properties
server.port=8080
spring.application.name=orders-service

spring.datasource.url=jdbc:postgresql://localhost:5432/orders
spring.datasource.username=orders_user
spring.datasource.password=change-me

logging.level.root=INFO
logging.level.com.example.orders=DEBUG

app.feature.new-checkout-enabled=true
app.max-retry-attempts=3
```

- File name matters: Spring Boot looks for `application.properties` (or `application.yml`) by convention.
- Keys are dot-separated and hierarchical (`spring.datasource.url`).
- Comments start with `#`.
- You can have multiple property files per profile, e.g. `application-dev.properties`.

## application.yml

YAML (`application.yml`) is an alternative format that expresses the same hierarchy using indentation instead of repeating dotted prefixes. Many teams prefer it because nested configuration is easier to read. Spring Boot supports both formats out of the box — you just can't mix `.properties` and `.yml` for the *same* file name in the *same* location without one being ignored in favor of the other's precedence rules (they can coexist, but Boot picks whichever files it finds).

```yaml
# src/main/resources/application.yml
server:
  port: 8080

spring:
  application:
    name: orders-service
  datasource:
    url: jdbc:postgresql://localhost:5432/orders
    username: orders_user
    password: change-me

logging:
  level:
    root: INFO
    com.example.orders: DEBUG

app:
  feature:
    new-checkout-enabled: true
  max-retry-attempts: 3
```

A big YAML-specific feature is **multi-document files**, using `---` to separate profile-specific blocks in a single file:

```yaml
spring:
  config:
    activate:
      on-profile: dev
server:
  port: 8081
---
spring:
  config:
    activate:
      on-profile: prod
server:
  port: 80
```

| Aspect | `.properties` | `.yml` |
|---|---|---|
| Syntax | Flat `key=value` | Nested, indentation-based |
| Readability for deep nesting | Repetitive | Cleaner |
| Multi-document (profile blocks in one file) | Not supported | Supported via `---` |
| Comments | `#` | `#` |
| Lists | `app.servers[0]=a`, `app.servers[1]=b` | `app.servers:` with `- a` / `- b` |
| Common pitfall | Verbose keys | Indentation errors break parsing |

Pick one format per project and stay consistent — mixing both is confusing for the team.

## @Value

`@Value` injects a single configuration value directly into a field, constructor parameter, or setter, using a `${...}` placeholder. It's the simplest way to read one property, but it doesn't scale well to reading many related values.

```java
@Component
public class RetryConfig {

    @Value("${app.max-retry-attempts:3}")
    private int maxRetryAttempts;

    @Value("${app.feature.new-checkout-enabled}")
    private boolean newCheckoutEnabled;

    public int getMaxRetryAttempts() {
        return maxRetryAttempts;
    }
}
```

- `${app.max-retry-attempts:3}` — the `:3` after the colon is a **default value** used if the property is missing.
- `@Value` also supports Spring Expression Language (SpEL): `@Value("#{2 * 10}")`.
- Works on constructor parameters too, which is friendlier for testing:

```java
@Component
public class GreetingService {

    private final String greetingPrefix;

    public GreetingService(@Value("${app.greeting-prefix:Hello}") String greetingPrefix) {
        this.greetingPrefix = greetingPrefix;
    }
}
```

- Downsides: no built-in validation, easy to typo the property key (fails silently or throws at startup depending on default), and scattered `@Value` fields across many classes make configuration hard to see at a glance.

## @ConfigurationProperties

`@ConfigurationProperties` binds a whole group of related properties to a single Java object (a "POJO", plain old Java object) at once, instead of one `@Value` per field. This is the recommended approach once you have more than one or two related settings — it's type-safe, testable, and self-documenting.

```java
@ConfigurationProperties(prefix = "app.feature")
public class FeatureProperties {

    private boolean newCheckoutEnabled;
    private int maxRetryAttempts = 3; // default value

    public boolean isNewCheckoutEnabled() {
        return newCheckoutEnabled;
    }

    public void setNewCheckoutEnabled(boolean newCheckoutEnabled) {
        this.newCheckoutEnabled = newCheckoutEnabled;
    }

    public int getMaxRetryAttempts() {
        return maxRetryAttempts;
    }

    public void setMaxRetryAttempts(int maxRetryAttempts) {
        this.maxRetryAttempts = maxRetryAttempts;
    }
}
```

Register it either by scanning or by using it directly:

```java
@SpringBootApplication
@ConfigurationPropertiesScan // finds all @ConfigurationProperties classes on the classpath
public class OrdersApplication {
    public static void main(String[] args) {
        SpringApplication.run(OrdersApplication.class, args);
    }
}
```

Since Spring Boot 2.2+/3.x, you can also use **Java records** for immutable configuration — this is the modern, preferred style:

```java
@ConfigurationProperties(prefix = "app.datasource")
public record DataSourceProperties(String url, String username, String password, int poolSize) {
}
```

To actually use `@ConfigurationProperties`, enable it explicitly (unless you use `@ConfigurationPropertiesScan`):

```java
@Configuration
@EnableConfigurationProperties(FeatureProperties.class)
public class AppConfig {
}
```

| Aspect | `@Value` | `@ConfigurationProperties` |
|---|---|---|
| Binds | One value at a time | A whole object graph |
| Type safety | Manual per field | Strong, via getters/setters or record components |
| Validation | Not supported directly | Supported via `@Validated` + Bean Validation |
| Relaxed binding | Limited | Full support |
| Testability | Harder to mock | Easy — just construct the POJO |
| Best for | One-off, simple values | Grouped, structured configuration |

## Relaxed Binding

Relaxed binding is Spring Boot's forgiving matching rule between property names and Java field names. You don't need the property key to match the Java field character-for-character; Spring normalizes both sides before comparing. This matters because environment variables can't contain dots or mixed case, but your Java fields use camelCase.

For a field named `newCheckoutEnabled`, all of these resolve to the same property:

```properties
app.feature.new-checkout-enabled=true
app.feature.newCheckoutEnabled=true
app.feature.NEW_CHECKOUT_ENABLED=true
```

```bash
# As an environment variable (dots and case are normalized)
export APP_FEATURE_NEWCHECKOUTENABLED=true
```

- Kebab-case (`new-checkout-enabled`) is the recommended style in `.properties`/`.yml` files.
- Environment variables must be UPPER_SNAKE_CASE with underscores instead of dots.
- Relaxed binding applies to `@ConfigurationProperties`; it's more limited for plain `@Value("${...}")`.
- This is exactly why containerized apps (Docker, Kubernetes) can configure Spring Boot purely through environment variables without ever touching a properties file.

## Property Validation

Bean Validation (`jakarta.validation`, formerly `javax.validation`) can validate `@ConfigurationProperties` objects at startup, so a misconfigured app fails fast with a clear error instead of misbehaving at runtime.

```java
@ConfigurationProperties(prefix = "app.mail")
@Validated
public class MailProperties {

    @NotBlank
    private String host;

    @Min(1)
    @Max(65535)
    private int port;

    @Email
    private String fromAddress;

    // getters and setters omitted for brevity
}
```

With a record:

```java
@ConfigurationProperties(prefix = "app.mail")
@Validated
public record MailProperties(
        @NotBlank String host,
        @Min(1) @Max(65535) int port,
        @Email String fromAddress) {
}
```

If `app.mail.host` is missing, the application fails to start with a message like:

```
Description:

Binding to target org.example.MailProperties failed:

    Property: app.mail.host
    Value: null
    Reason: must not be blank
```

- Requires the `spring-boot-starter-validation` dependency on the classpath.
- `@Validated` triggers validation on startup binding — without it, constraints are silently ignored.
- Failing fast here is far better than discovering a blank SMTP host in production logs three hours later.

## Environment API

The `Environment` abstraction is Spring's unified view over *all* property sources — files, env vars, system properties, command-line args — merged into one queryable object. It's the low-level mechanism that `@Value` and `@ConfigurationProperties` are built on top of.

```java
@Component
public class StartupLogger implements ApplicationRunner {

    private final Environment environment;

    public StartupLogger(Environment environment) {
        this.environment = environment;
    }

    @Override
    public void run(ApplicationArguments args) {
        String port = environment.getProperty("server.port", "8080");
        boolean isDevProfile = environment.acceptsProfiles(Profiles.of("dev"));
        System.out.println("Running on port " + port + ", dev profile active: " + isDevProfile);
    }
}
```

Useful methods:

- `environment.getProperty("key")` — returns `null` if missing.
- `environment.getProperty("key", "default")` — with a fallback.
- `environment.getRequiredProperty("key")` — throws if missing.
- `environment.getActiveProfiles()` — array of currently active profile names.
- `environment.acceptsProfiles(Profiles.of("dev | staging"))` — profile expressions.

You can also access it directly by implementing `EnvironmentAware`, or inject it anywhere as a bean — Spring provides it automatically.

## Profiles

Profiles let you activate different beans and different configuration values depending on the environment (dev, test, staging, prod) without changing code. A bean marked `@Profile("dev")` only gets created when the `dev` profile is active.

```java
@Configuration
public class DataSourceConfig {

    @Bean
    @Profile("dev")
    public DataSource devDataSource() {
        return new EmbeddedDatabaseBuilder()
                .setType(EmbeddedDatabaseType.H2)
                .build();
    }

    @Bean
    @Profile("prod")
    public DataSource prodDataSource(DataSourceProperties props) {
        HikariDataSource dataSource = new HikariDataSource();
        dataSource.setJdbcUrl(props.url());
        dataSource.setUsername(props.username());
        dataSource.setPassword(props.password());
        return dataSource;
    }
}
```

Profile-specific property files override the base file:

```
application.properties          <- always loaded
application-dev.properties      <- loaded only when "dev" profile is active
application-prod.properties     <- loaded only when "prod" profile is active
```

Activate a profile several ways:

```bash
# Command-line argument
java -jar app.jar --spring.profiles.active=dev

# Environment variable
export SPRING_PROFILES_ACTIVE=prod

# In application.properties (not usually recommended for the active profile itself)
spring.profiles.active=dev
```

```java
@Service
@Profile("!prod") // active in every profile except prod
public class FakePaymentGateway implements PaymentGateway {
    // returns canned responses for local/dev testing
}
```

- Multiple profiles can be active at once: `--spring.profiles.active=dev,debug`.
- `@Profile` can be combined with logical expressions: `"dev | staging"`, `"!prod"`.
- Profiles apply to `@Component`, `@Configuration`, and `@Bean` methods.

## Profile Groups

Profile groups (introduced in Spring Boot 2.4+) let you bundle several profiles under one umbrella name, so activating a single group turns on all of them together. This avoids having to remember and type a long list of profiles every time.

```yaml
# application.yml
spring:
  profiles:
    group:
      production:
        - prod
        - metrics
        - audit-logging
```

Now activating `production` automatically activates `prod`, `metrics`, and `audit-logging` too:

```bash
java -jar app.jar --spring.profiles.active=production
```

- Great for keeping cross-cutting profiles (like `metrics` or `audit-logging`) consistently bundled with environment profiles.
- Reduces copy-paste errors across deployment scripts.
- Group definitions themselves are not profile-specific — they must live in the base `application.yml`/`.properties` (not in `application-prod.yml`), since the file that defines the group must be loaded before Spring even knows which profiles to look for.

## Configuration Precedence

When the same property is defined in multiple places, Spring Boot needs a deterministic rule to decide which one wins. Higher in the list below always overrides lower. This is one of the most frequently tested interview facts, so it's worth memorizing the shape even if not every exact rank.

From **highest to lowest** precedence (Spring Boot 3.x):

1. **Devtools global settings** (`$HOME/.config/spring-boot/devtools.properties`) — only when devtools is active.
2. **`@TestPropertySource`** annotations on tests.
3. **`properties` attribute on test annotations** (e.g. `@SpringBootTest(properties = ...)`).
4. **Command-line arguments** (e.g. `--server.port=9090`).
5. **Properties from `SPRING_APPLICATION_JSON`** (an inline JSON blob in an env var or system property).
6. **`ServletConfig` init parameters**.
7. **`ServletContext` init parameters**.
8. **JNDI attributes** from `java:comp/env`.
9. **Java System properties** (`System.getProperties()`, e.g. `-Dserver.port=9090`).
10. **OS environment variables**.
11. **`RandomValuePropertySource`** (for `random.*` properties).
12. **Profile-specific application properties outside your packaged jar** (`application-{profile}.properties/yml`).
13. **Profile-specific application properties packaged inside your jar**.
14. **Application properties outside your packaged jar** (`application.properties/yml`).
15. **Application properties packaged inside your jar**.
16. **`@PropertySource` annotations** on `@Configuration` classes.
17. **Default properties** (set via `SpringApplication.setDefaultProperties`).

Practical takeaways from this order:

- **Command-line args beat everything you'd normally set in a file** — handy for one-off overrides, dangerous if someone pastes a stray `--` flag in a script.
- **External files beat packaged (inside-the-jar) files** — so an `application.yml` sitting next to your jar on a server overrides the one baked in at build time.
- **Profile-specific always beats non-profile-specific**, regardless of packaging.
- Environment variables outrank plain properties files, which is exactly why container orchestration (Docker/Kubernetes ConfigMaps/Secrets) works so well with Spring Boot.

```bash
# Example: three sources define server.port. Command line wins -> app runs on 9999.
# application.yml:      server.port: 8080
# env var:               SERVER_PORT=8888
java -jar app.jar --server.port=9999
```

## Secrets Management

Secrets (passwords, API keys, tokens) should never be hardcoded or committed to version control in plaintext. Spring Boot doesn't force one specific approach, but it gives you the building blocks to plug in whichever secrets store your infrastructure uses.

Common strategies:

- **Environment variables injected at deploy time** (from Kubernetes Secrets, Docker `--env-file`, CI/CD variables) — simplest, works everywhere.
- **Externalized config server** — e.g. Spring Cloud Config Server backed by a Git repo with encrypted values.
- **Dedicated secret stores** — HashiCorp Vault (`spring-cloud-vault`), AWS Secrets Manager, Azure Key Vault, GCP Secret Manager — fetched at startup, never stored on disk.
- **`.gitignore`'d local override files** — e.g. `application-local.properties` for a developer's own machine, never committed.

```yaml
# application.yml — reference the secret via a placeholder, never inline the value
spring:
  datasource:
    username: ${DB_USERNAME}
    password: ${DB_PASSWORD}
```

```bash
# Injected by the deployment platform, not stored in the repo
export DB_USERNAME=orders_user
export DB_PASSWORD='S3cur3-P@ssw0rd!'
```

Example using Spring Cloud Vault-style configuration (illustrative):

```yaml
spring:
  cloud:
    vault:
      uri: https://vault.internal.example.com:8200
      authentication: TOKEN
      token: ${VAULT_TOKEN}
  config:
    import: vault://secret/orders-service
```

- Never log full configuration objects that contain secrets — mask or exclude sensitive fields.
- Rotate secrets regularly; environment-variable/Vault-based approaches make rotation possible without rebuilding the jar.
- Use `.gitignore` for any local file that might contain a real credential, and add a `.properties.example` template instead.
- Spring Boot's `/actuator/env` endpoint automatically sanitizes values whose keys look sensitive (containing `password`, `secret`, `key`, `token`, etc.) by masking them, but this is a safety net, not a substitute for proper secrets storage.

## Common Code Review / Interview Pitfalls

- **Hardcoding secrets in `application.properties` and committing them.** This leaks credentials into git history forever, even if removed later. Fix: use environment variables or a secrets manager, and keep only placeholders in version control.

  ❌
  ```properties
  spring.datasource.password=SuperSecret123
  ```
  ✅
  ```properties
  spring.datasource.password=${DB_PASSWORD}
  ```

- **Using `@Value` for a large group of related settings instead of `@ConfigurationProperties`.** It scatters configuration across many classes and skips relaxed binding/validation benefits. Fix: group related settings into one `@ConfigurationProperties` class or record.

- **Forgetting `@Validated` on a `@ConfigurationProperties` class.** Bean Validation annotations like `@NotBlank` are silently ignored without it, so invalid config passes startup unnoticed. Fix: always add `@Validated` when you add constraint annotations.

- **Typo-ing a property key in `@Value` with no default and no validation.** The field silently ends up `null`/`0`/`false` and fails later at runtime, far from the real cause. Fix: prefer `@ConfigurationProperties` with validation, or supply a sensible default and log a warning if unset.

  ❌
  ```java
  @Value("${app.max-retryattempts}") // typo, doesn't match app.max-retry-attempts
  private int maxRetryAttempts;
  ```
  ✅
  ```java
  @Value("${app.max-retry-attempts:3}")
  private int maxRetryAttempts;
  ```

- **Mixing `application.properties` and `application.yml` inconsistently across a team.** It confuses new contributors about where to look and can cause silent duplication. Fix: pick one format for the whole project and document it.

- **Putting `spring.profiles.active` inside a profile-specific file (e.g. `application-prod.properties`).** This has no effect since the profile file is only loaded *after* the active profile is already determined. Fix: set the active profile via environment variable, command-line argument, or the base `application.yml`, never inside a profile-specific file.

- **Defining profile groups inside a profile-specific properties file instead of the base file.** Since profile groups must be known before profile resolution happens, defining them in `application-prod.yml` is silently ignored. Fix: always define `spring.profiles.group.*` in the base `application.yml`.

- **Assuming command-line arguments and file-based properties have the same precedence.** This leads to confusing "it works locally but not in the deployed script" bugs. Fix: know the precedence order — command-line args and env vars outrank properties files.

- **Relying on `@Profile("prod")` beans without a fallback/default bean for missing profile activation.** If no profile is active, "prod-only" beans won't exist and the context fails to start, or worse, silently uses the wrong bean. Fix: define a sensible default profile bean or use `spring.profiles.default`.

- **Logging the entire `Environment` or a `@ConfigurationProperties` object that contains secrets.** Password/token fields end up in log files, which are often less protected than the secrets store itself. Fix: override `toString()` to mask sensitive fields, or avoid logging config objects entirely.

  ❌
  ```java
  log.info("Loaded config: {}", mailProperties); // toString() leaks password
  ```
  ✅
  ```java
  log.info("Loaded mail config for host={}", mailProperties.host());
  ```

- **Using inconsistent casing/format for property keys (camelCase in one file, kebab-case in another).** While relaxed binding tolerates this, it makes the codebase inconsistent and harder to grep. Fix: standardize on kebab-case in properties/YAML files as Spring's own documentation recommends.

- **Not adding `spring-boot-starter-validation` and expecting `@NotBlank`/`@Min` to work.** Without the dependency, the validation annotations are on the classpath conceptually but Boot has no validator to invoke, so nothing is checked. Fix: add the starter and confirm validation actually triggers a startup failure for bad input.

- **Assuming `@ConfigurationProperties` fields bind without registering the class.** Just annotating a class with `@ConfigurationProperties` does nothing on its own — it must be picked up via `@ConfigurationPropertiesScan`, `@EnableConfigurationProperties`, or component scanning as a `@Component`. Fix: explicitly enable/scan it and verify with a quick startup test.

- **Treating `/actuator/env` masking as full secret protection.** Sanitization is pattern-based on key names; a secret stored under a differently named key (e.g. `app.internalToken`) may not be masked automatically. Fix: don't rely on Actuator sanitization alone — control who can access the endpoint at all, and use `management.endpoint.env.show-values` settings carefully.

## Quick Recap

- `application.properties` and `application.yml` are the two standard file formats; pick one and be consistent.
- `@Value` injects single values with optional SpEL and defaults (`${key:default}`); fine for one-offs, weak for groups of settings.
- `@ConfigurationProperties` binds a whole group of properties to a POJO or record — type-safe, testable, supports validation. Prefer it over many `@Value` fields.
- Relaxed binding lets `kebab-case`, `camelCase`, and `UPPER_SNAKE_CASE` (env vars) all map to the same property — this is why containers can configure Spring purely with env vars.
- Bean Validation (`@NotBlank`, `@Min`, etc.) plus `@Validated` on `@ConfigurationProperties` makes bad config fail fast at startup instead of misbehaving later.
- `Environment` is the underlying API that unifies every property source; `@Value` and `@ConfigurationProperties` are built on top of it.
- Profiles (`@Profile`, `application-{profile}.yml`, `spring.profiles.active`) let you swap beans/config per environment without code changes.
- Profile groups bundle multiple profiles under one name, but must be declared in the base config file, never a profile-specific one.
- Configuration precedence (high to low, key points): command-line args > env vars/system properties > profile-specific external files > profile-specific packaged files > external application files > packaged application files > `@PropertySource` > defaults.
- Secrets belong in environment variables, a config server, or a dedicated secrets manager (Vault, AWS/Azure/GCP secret stores) — never hardcoded or committed in plaintext.
- Actuator's `/env` masking is a helpful safety net, not a substitute for real secrets management.
