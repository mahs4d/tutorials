# 3. Spring Boot Fundamentals

## Overview

Spring Boot is a layer on top of the Spring Framework that removes most of the manual setup work Spring used to require. Instead of hand-wiring XML configuration files or writing dozens of `@Bean` methods, you add a few dependencies and Spring Boot configures sensible defaults for you. It bundles a web server inside your application, exposes simple ways to override configuration per environment, and gets you from "empty project" to "running REST API" in minutes. Understanding how Spring Boot decides what to configure — and how to override it — is one of the most commonly tested areas in Spring interviews, because it separates people who copy-pasted a tutorial from people who understand what is happening under the hood. This chapter walks through the building blocks: auto-configuration, starters, the CLI and Initializr tooling, embedded servers, project structure, configuration sources, profiles, and the runner interfaces used for startup logic.

## What is Spring Boot?

Spring Boot is **not a replacement** for the Spring Framework — it is a convention-over-configuration wrapper around it. Think of the Spring Framework as a box of high-quality car parts (engine, wheels, wiring), and Spring Boot as the factory that assembles a working car from those parts using sensible defaults, so you don't have to bolt everything together yourself.

Before Spring Boot, a typical Spring web project needed:

- An XML (or Java-based) configuration file wiring the `DispatcherServlet`.
- A separately installed and configured application server (Tomcat, Jetty).
- Manual dependency version management to avoid conflicting library versions.

Spring Boot solves all three:

- **Auto-configuration** wires beans automatically based on what's on the classpath.
- **Embedded servers** ship inside your JAR — no external Tomcat install needed.
- **Starter dependencies** bundle compatible versions of related libraries.

```java
// A complete, runnable Spring Boot web application
package com.example.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@SpringBootApplication
@RestController
public class DemoApplication {

    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }

    @GetMapping("/hello")
    public String hello() {
        return "Hello, Spring Boot!";
    }
}
```

Run this with `mvn spring-boot:run` and you have a working HTTP server on port 8080 — no Tomcat installation, no XML, no manual `DispatcherServlet` registration.

- Spring Boot is built **on top of** Spring, Spring Data, Spring Security, etc. — it doesn't replace them.
- It favors **convention over configuration**: sensible defaults you can override.
- It's the standard way to build new Spring applications since roughly 2014.

## Auto Configuration

Auto-configuration is the mechanism that looks at what's on your classpath (and what beans you've already defined) and automatically registers beans you'd otherwise have to configure by hand. If it sees the H2 database driver on the classpath and no `DataSource` bean defined, it creates an in-memory `DataSource` for you. If it sees Spring MVC, it configures a `DispatcherServlet`, a `ViewResolver`, message converters, and more.

It works through classes annotated with `@AutoConfiguration` (in older versions, `@Configuration` combined with `@EnableAutoConfiguration`), which are registered in a file at `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports` inside each starter JAR. Spring Boot reads this file at startup and conditionally applies each configuration class.

The "conditional" part is key — auto-configuration classes are guarded by annotations like:

| Annotation | Applies bean when... |
|---|---|
| `@ConditionalOnClass` | A given class is present on the classpath |
| `@ConditionalOnMissingBean` | No bean of that type already exists |
| `@ConditionalOnProperty` | A specific property is set (and optionally has a specific value) |
| `@ConditionalOnWebApplication` | The app is a web application |
| `@ConditionalOnBean` | Another specific bean already exists in the context |

```java
// Simplified version of how Spring Boot's DataSourceAutoConfiguration works
@AutoConfiguration
@ConditionalOnClass(DataSource.class)
@ConditionalOnMissingBean(DataSource.class)
public class MyDataSourceAutoConfiguration {

    @Bean
    public DataSource dataSource() {
        return DataSourceBuilder.create()
                .url("jdbc:h2:mem:testdb")
                .driverClassName("org.h2.Driver")
                .build();
    }
}
```

Because of `@ConditionalOnMissingBean`, if *you* define your own `DataSource` bean, Spring Boot's auto-configuration silently backs off and uses yours instead. This "auto-configure unless you say otherwise" pattern is the core idea to remember.

To see exactly which auto-configurations fired (and which were skipped, and why), run your app with:

```bash
java -jar demo.jar --debug
```

This prints an auto-configuration report listing "Positive matches" and "Negative matches" with the reasons.

- Auto-configuration classes live inside starter/autoconfigure JARs, not your code.
- They always back off if you provide your own bean of the same type.
- `--debug` (or `debug=true` in properties) shows the full auto-configuration report.
- You can exclude a specific auto-configuration with `@SpringBootApplication(exclude = DataSourceAutoConfiguration.class)`.

## Starter Dependencies

A **starter** is a single dependency that pulls in a curated, version-compatible set of libraries for a specific purpose. Instead of manually choosing compatible versions of Spring MVC, Jackson, Tomcat, and validation libraries, you add one starter and Spring Boot's dependency management (via the parent POM or BOM) picks compatible versions for all of them.

```xml
<!-- pom.xml -->
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.5</version>
</parent>

<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-test</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>
```

Notice there are no `<version>` tags on the starters — the parent POM manages those versions for you, guaranteeing they are mutually compatible.

Common starters you'll see constantly:

| Starter | Purpose |
|---|---|
| `spring-boot-starter-web` | Build REST/MVC web apps with embedded Tomcat |
| `spring-boot-starter-webflux` | Reactive web apps with embedded Netty |
| `spring-boot-starter-data-jpa` | JPA/Hibernate + Spring Data repositories |
| `spring-boot-starter-security` | Authentication and authorization |
| `spring-boot-starter-test` | JUnit 5, Mockito, AssertJ, Spring Test |
| `spring-boot-starter-actuator` | Production-ready metrics/health endpoints |
| `spring-boot-starter-validation` | Bean Validation (Jakarta Validation) |

- A starter has (almost) no code itself — it's just a `pom.xml`/`build.gradle` dependency list.
- Naming convention: `spring-boot-starter-*` for official ones, `*-spring-boot-starter` for third-party ones (e.g. `mybatis-spring-boot-starter`).
- Using the parent POM (or importing the BOM) is what gives you version-free dependency declarations.

## Spring Boot CLI

The Spring Boot CLI (Command Line Interface) is a command-line tool that lets you run Groovy or Java scripts as Spring Boot applications without a full build setup. It's mostly used for quick prototypes, spikes, or demos — production projects almost always use Maven or Gradle instead.

```bash
# Install via SDKMAN (common approach)
sdk install springboot

# Run a Groovy script directly — no pom.xml needed
spring run hello.groovy
```

```groovy
// hello.groovy
@RestController
class HelloController {
    @RequestMapping("/")
    String home() {
        "Hello from the Spring Boot CLI!"
    }
}
```

The CLI automatically resolves the `@RestController` import and grabs the needed dependencies behind the scenes, so a two-line script becomes a running web app.

- Mostly a historical / prototyping tool today — most real projects skip it entirely.
- Useful for interview trivia ("what is the Spring Boot CLI"), but rarely used day-to-day.
- Other CLI-adjacent commands: `spring init` can scaffold a project (similar to Spring Initializr) directly from the terminal.

## Spring Initializr

Spring Initializr (start.spring.io) is a web-based (and API-based) project generator. You pick your build tool (Maven/Gradle), language (Java/Kotlin/Groovy), Spring Boot version, and the starters/dependencies you need, and it generates a ready-to-import project skeleton — correct folder structure, a working `pom.xml`/`build.gradle`, and a main application class.

```bash
# Generate a project from the terminal using curl, no browser needed
curl https://start.spring.io/starter.zip \
  -d dependencies=web,data-jpa,postgresql \
  -d type=maven-project \
  -d javaVersion=17 \
  -d bootVersion=3.2.5 \
  -o demo.zip

unzip demo.zip -d demo
```

Most IDEs (IntelliJ IDEA, VS Code with the Spring extension, Spring Tool Suite) embed Spring Initializr directly in their "New Project" wizard, so you rarely need to visit the website manually.

- It is the standard, recommended way to start a **new** Spring Boot project.
- Lets you lock in a specific Spring Boot version and Java version up front.
- Generates a `.gitignore`, a placeholder test class, and (optionally) a `Dockerfile` / `application.properties`.

## Embedded Servers

"Embedded server" means the HTTP server (Tomcat, Jetty, or Undertow) runs *inside* your application's JVM process, packaged inside your executable JAR — you don't install or configure a separate server. This is one of Spring Boot's biggest quality-of-life wins over classic Spring, where you had to build a WAR file and deploy it into an externally managed Tomcat.

```bash
# Building and running is just:
mvn clean package
java -jar target/demo-0.0.1-SNAPSHOT.jar
# Tomcat starts INSIDE this JVM process, listening on port 8080
```

Spring Boot picks Tomcat by default when you use `spring-boot-starter-web`. Swapping servers is just a dependency change:

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
    <!-- Exclude the default embedded container -->
    <exclusions>
        <exclusion>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-tomcat</artifactId>
        </exclusion>
    </exclusions>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-undertow</artifactId>
</dependency>
```

| Server | Model | Good for |
|---|---|---|
| Tomcat | Thread-per-request (default) | General-purpose apps; most common choice |
| Jetty | Thread-per-request, lightweight | Small footprint, embedded devices |
| Undertow | Non-blocking I/O, low memory | High throughput, lower memory usage |
| Netty | Fully reactive (used with WebFlux) | Reactive/non-blocking apps (Mono/Flux) |

Common configuration tweaks in `application.properties`:

```properties
server.port=8443
server.servlet.context-path=/api
server.tomcat.max-threads=200
```

- "Embedded" = the server library is a JVM dependency, started programmatically by your app, not an external process you deploy into.
- You package a self-contained **fat JAR** ("uber JAR") that includes the server — `java -jar app.jar` is all you need to run it.
- You can still deploy a WAR to an external server if needed (legacy/enterprise requirement), but it's the exception, not the rule.

## Spring Boot Application Structure

Spring Boot projects follow a consistent, opinionated folder layout (Maven's "standard directory layout"), which makes any Spring Boot codebase easy to navigate once you know the convention.

```
my-app/
├── pom.xml                             # or build.gradle
├── src/
│   ├── main/
│   │   ├── java/
│   │   │   └── com/example/demo/
│   │   │       ├── DemoApplication.java     # main class, @SpringBootApplication
│   │   │       ├── controller/
│   │   │       │   └── UserController.java
│   │   │       ├── service/
│   │   │       │   └── UserService.java
│   │   │       ├── repository/
│   │   │       │   └── UserRepository.java
│   │   │       └── model/
│   │   │           └── User.java
│   │   └── resources/
│   │       ├── application.properties       # or application.yml
│   │       ├── application-dev.yml
│   │       ├── static/                       # served as-is (CSS, JS, images)
│   │       └── templates/                    # server-rendered views (Thymeleaf, etc.)
│   └── test/
│       └── java/
│           └── com/example/demo/
│               └── DemoApplicationTests.java
└── target/                              # build output (generated, not committed)
```

Key rules that come from this convention:

- The main application class should sit in the **root package** (e.g. `com.example.demo`) above everything else, because component scanning starts from that package and scans downward by default.
- `src/main/resources` holds configuration and static assets, not Java code.
- `src/test/java` mirrors the `src/main/java` package structure for easy test discovery.

```java
// DemoApplication.java sits at com.example.demo (the root package)
// so @ComponentScan (implied by @SpringBootApplication) can find
// controller/, service/, repository/ automatically without extra config.
package com.example.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class DemoApplication {
    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }
}
```

## @SpringBootApplication

`@SpringBootApplication` is a convenience meta-annotation — a single annotation that bundles together three others you'd otherwise have to apply individually:

```java
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Documented
@Inherited
@SpringBootConfiguration
@EnableAutoConfiguration
@ComponentScan(excludeFilters = { ... })
public @interface SpringBootApplication {
    // ...
}
```

| Component annotation | What it does |
|---|---|
| `@SpringBootConfiguration` | A specialization of `@Configuration` — marks this class as a source of bean definitions |
| `@EnableAutoConfiguration` | Turns on the auto-configuration mechanism described earlier |
| `@ComponentScan` | Scans the current package and sub-packages for `@Component`, `@Service`, `@Repository`, `@Controller`, etc. |

```java
package com.example.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class DemoApplication {
    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }
}
```

You can customize what it scans if your classes live outside the default package:

```java
@SpringBootApplication(scanBasePackages = {"com.example.demo", "com.example.shared"})
public class DemoApplication {
    // ...
}
```

- It's a **meta-annotation**: using it is functionally the same as stacking all three annotations manually.
- `SpringApplication.run(...)` is what actually bootstraps the `ApplicationContext`, starts the embedded server, and triggers auto-configuration.
- Only one class per application should carry `@SpringBootApplication`, typically placed in the root package.

## Externalized Configuration

Externalized configuration means your application's settings (database URLs, ports, feature flags, API keys) live *outside* the compiled code, so you can change behavior between environments (local, staging, production) without recompiling. Spring Boot supports many sources, and merges them using a well-defined precedence order.

From highest to lowest priority (partial, most commonly tested subset):

1. Command-line arguments (`--server.port=9090`)
2. `SPRING_APPLICATION_JSON` environment variable
3. JNDI attributes
4. Java System properties (`-Dserver.port=9090`)
5. OS environment variables (`SERVER_PORT=9090`)
6. Profile-specific `application-{profile}.properties/yml` outside the packaged JAR
7. Profile-specific `application-{profile}.properties/yml` inside the packaged JAR
8. Application `application.properties/yml` outside the packaged JAR
9. Application `application.properties/yml` inside the packaged JAR (your `src/main/resources`)
10. `@PropertySource` annotations on `@Configuration` classes
11. Default properties (`SpringApplication.setDefaultProperties`)

```bash
# Command-line arguments win over everything defined in application.properties
java -jar demo.jar --server.port=9090 --spring.profiles.active=prod
```

```bash
# Environment variables use SCREAMING_SNAKE_CASE, mapped to relaxed binding
export SPRING_DATASOURCE_URL=jdbc:postgresql://prod-db:5432/app
export SPRING_DATASOURCE_PASSWORD=secret
java -jar demo.jar
```

This is called **relaxed binding**: `server.port`, `SERVER_PORT`, and `--server.port` all bind to the same property, so the same logical setting can come from properties files, environment variables, or CLI flags interchangeably.

- Externalizing config lets the exact same build artifact (JAR) run correctly in dev, staging, and prod.
- Never hardcode secrets in `application.properties` committed to git — use environment variables or a secrets manager instead.
- Command-line args and environment variables are the standard way to override config in containers (Docker/Kubernetes).

## Configuration Properties

Spring Boot lets you bind configuration values directly onto strongly-typed Java objects instead of reading raw strings scattered around your codebase with `@Value`. The `@ConfigurationProperties` annotation maps a prefix of properties onto fields of a POJO (Plain Old Java Object).

```properties
# application.properties
app.mail.host=smtp.example.com
app.mail.port=587
app.mail.retry-count=3
app.mail.enabled=true
```

```java
package com.example.demo.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;

@Validated
@ConfigurationProperties(prefix = "app.mail")
public class MailProperties {

    @NotBlank
    private String host;
    private int port;
    private int retryCount;
    private boolean enabled;

    // getters and setters (or use a Java record for immutable properties)

    public String getHost() { return host; }
    public void setHost(String host) { this.host = host; }
    public int getPort() { return port; }
    public void setPort(int port) { this.port = port; }
    public int getRetryCount() { return retryCount; }
    public void setRetryCount(int retryCount) { this.retryCount = retryCount; }
    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
}
```

```java
// Register it explicitly (or annotate MailProperties with @Component)
package com.example.demo;

import com.example.demo.config.MailProperties;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties(MailProperties.class)
public class DemoApplication {
    // ...
}
```

Since Java 16+ / Spring Boot 2.6+, you can (and should, for new code) use immutable **records** instead of a mutable POJO:

```java
@ConfigurationProperties(prefix = "app.mail")
public record MailProperties(String host, int port, int retryCount, boolean enabled) {
}
```

| Feature | `@Value("${...}")` | `@ConfigurationProperties` |
|---|---|---|
| Binds multiple related properties | No — one expression at a time | Yes — an entire object graph at once |
| Supports relaxed binding (`retry-count` → `retryCount`) | No | Yes |
| Supports validation (`@NotBlank`, `@Min`, etc.) | No | Yes |
| Type-safe / IDE-autocomplete friendly | Limited | Yes |
| Good for one-off values | Yes | Overkill |

- `@ConfigurationProperties` is the recommended approach for anything with more than one or two related settings.
- Add `spring-boot-configuration-processor` as a dependency to get IDE autocomplete and documentation for your custom properties.
- Naming automatically converts kebab-case (`retry-count`) in files to camelCase (`retryCount`) in Java — this is "relaxed binding."

## YAML vs Properties

Spring Boot supports two main file formats for configuration: `.properties` (flat key-value pairs) and `.yml`/`.yaml` (hierarchical, indentation-based). Both are read and merged the same way internally — it's purely a matter of syntax and readability.

```properties
# application.properties
spring.datasource.url=jdbc:postgresql://localhost:5432/app
spring.datasource.username=admin
spring.datasource.hikari.maximum-pool-size=10
server.port=8080
logging.level.com.example=DEBUG
```

```yaml
# application.yml — equivalent to the properties file above
spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/app
    username: admin
    hikari:
      maximum-pool-size: 10
server:
  port: 8080
logging:
  level:
    com.example: DEBUG
```

| Aspect | `.properties` | `.yml` |
|---|---|---|
| Syntax | Flat `key=value` lines | Indentation-based hierarchy |
| Readability for nested config | Repetitive prefixes on every line | Much cleaner, no repeated prefixes |
| Lists | `app.servers[0]=host1`, `app.servers[1]=host2` | Native `- host1` / `- host2` list syntax |
| Multiple profiles in one file | Not supported (needs separate files) | Supported via `---` document separators |
| Comment syntax | `#` | `#` |
| Parsing pitfalls | Very few | Indentation errors, tabs not allowed, YAML treats `no`/`yes`/`on`/`off` as booleans |

```yaml
# YAML lets you define multiple profiles in ONE file using `---`
spring:
  application:
    name: demo
---
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

- Both formats are functionally equivalent — pick one per project and stay consistent.
- YAML is generally preferred for larger, nested configurations because it avoids repeating prefixes.
- YAML has quirks: it's whitespace-sensitive (spaces only, no tabs), and unquoted `yes`/`no`/`true`/`false`/`on`/`off` are parsed as booleans, which can silently break string values like a country code `NO` (Norway).

## Profiles

Profiles let you activate different sets of beans and configuration values depending on the environment (dev, test, staging, prod) without changing code. A profile is just a named label; when it's "active," Spring Boot loads matching `application-{profile}.properties/yml` files and activates any `@Profile`-annotated beans.

```yaml
# application.yml (base/shared config, always loaded)
spring:
  application:
    name: demo
```

```yaml
# application-dev.yml
spring:
  datasource:
    url: jdbc:h2:mem:devdb
logging:
  level:
    root: DEBUG
```

```yaml
# application-prod.yml
spring:
  datasource:
    url: jdbc:postgresql://prod-db:5432/app
logging:
  level:
    root: WARN
```

Activate a profile via, in order of convenience:

```bash
# Command-line argument
java -jar demo.jar --spring.profiles.active=prod

# Environment variable (common in Docker/Kubernetes)
export SPRING_PROFILES_ACTIVE=prod

# JVM system property
java -Dspring.profiles.active=prod -jar demo.jar
```

You can also gate entire `@Bean` definitions or `@Component` classes with `@Profile`:

```java
package com.example.demo.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

@Configuration
public class NotificationConfig {

    @Bean
    @Profile("dev")
    public NotificationService fakeNotificationService() {
        return message -> System.out.println("DEV notification: " + message);
    }

    @Bean
    @Profile("prod")
    public NotificationService realNotificationService() {
        return message -> sendViaEmailProvider(message);
    }

    private void sendViaEmailProvider(String message) {
        // real integration here
    }
}
```

- Multiple profiles can be active at once: `--spring.profiles.active=prod,eu-region`.
- `@Profile("!dev")` activates a bean when the `dev` profile is **not** active.
- `spring.profiles.default` sets a fallback profile used only when no profile is explicitly activated.
- Common mistake: forgetting to set an active profile in production and silently running with `dev` defaults (e.g. an in-memory database).

## CommandLineRunner

`CommandLineRunner` is a functional interface Spring Boot looks for after the application context is fully started. Any bean implementing it gets its `run(String... args)` method called automatically, with the raw command-line arguments passed in as an array of strings. It's commonly used for one-off startup tasks: seeding data, printing diagnostics, or kicking off a batch job.

```java
package com.example.demo;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

@Component
public class DataSeeder implements CommandLineRunner {

    @Override
    public void run(String... args) throws Exception {
        System.out.println("Application started with args: " + String.join(", ", args));
        // e.g. seed reference data into the database here
    }
}
```

You can also register one inline as a `@Bean`, which is handy for quick scripts:

```java
@Bean
public CommandLineRunner seedDatabase(UserRepository userRepository) {
    return args -> {
        if (userRepository.count() == 0) {
            userRepository.save(new User("admin", "admin@example.com"));
        }
    };
}
```

- Runs **once**, immediately after `SpringApplication.run()` finishes starting the context — not on every request.
- If multiple `CommandLineRunner` beans exist, order them with `@Order` (lower value runs first) or by implementing `Ordered`.
- Receives raw, unparsed command-line arguments as `String[]` — no automatic parsing into flags/values.

## ApplicationRunner

`ApplicationRunner` does the same job as `CommandLineRunner` — run startup logic once the context is ready — but hands you a parsed `ApplicationArguments` object instead of a raw `String[]`. That object lets you query option names and values (e.g. `--server.port=8080`) without manually splitting strings.

```java
package com.example.demo;

import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

@Component
public class StartupDiagnostics implements ApplicationRunner {

    @Override
    public void run(ApplicationArguments args) throws Exception {
        if (args.containsOption("import")) {
            System.out.println("Import mode enabled, file: " + args.getOptionValues("import"));
        }
        System.out.println("Non-option args: " + args.getNonOptionArgs());
    }
}
```

| Feature | `CommandLineRunner` | `ApplicationRunner` |
|---|---|---|
| Argument type received | Raw `String... args` | Parsed `ApplicationArguments` |
| Distinguishes `--key=value` options from plain args | No, manual parsing needed | Yes, built in |
| When it runs | After context startup, before `run()` returns | Same — after context startup, before `run()` returns |
| Ordering support | `@Order` / `Ordered` | `@Order` / `Ordered` |
| Typical use case | Simple scripts, quick prototypes | Anything that needs to inspect `--flag=value` style args |

- Both interfaces run at the same point in the lifecycle — the choice between them is purely about how convenient the argument-handling API is for your case.
- If you have several runners of both types, Spring Boot orders **all** of them together using `@Order`, regardless of interface.
- Neither should be used for long-running background work — they block application startup until `run()` returns; use `@Async` or a dedicated scheduler/thread for long tasks.

## Common Code Review / Interview Pitfalls

- **Committing secrets in `application.properties`.** Database passwords or API keys checked into git are a security incident waiting to happen. Fix: use environment variables, a `.env` file excluded from git, or a secrets manager (Vault, AWS Secrets Manager), and keep only placeholders/defaults in the committed file.

  ```properties
  # ❌ bad
  spring.datasource.password=SuperSecret123

  # ✅ good
  spring.datasource.password=${DB_PASSWORD}
  ```

- **Placing the main class in a leaf package.** If `@SpringBootApplication` sits in `com.example.demo.controller` instead of `com.example.demo`, component scanning won't find sibling packages like `service` or `repository`, and beans silently go missing. Fix: always put the main class in the top-level root package.

- **Overusing `@Value` for structured config.** Dozens of scattered `@Value("${...}")` fields are hard to test, don't validate, and don't support relaxed binding well. Fix: group related settings into a `@ConfigurationProperties` class instead.

  ```java
  // ❌ bad — repeated, unvalidated, scattered
  @Value("${app.mail.host}") private String host;
  @Value("${app.mail.port}") private int port;

  // ✅ good — one cohesive, validated, type-safe object
  @ConfigurationProperties(prefix = "app.mail")
  public record MailProperties(String host, int port) {}
  ```

- **Excluding auto-configuration without understanding why.** Slapping `@SpringBootApplication(exclude = SecurityAutoConfiguration.class)` to "make an error go away" often masks a real misconfiguration (e.g. missing a required bean) rather than fixing it. Fix: run with `--debug` first to see *why* a given auto-configuration applied, then address the root cause.

- **Not setting an explicit active profile in production.** Deploying without `SPRING_PROFILES_ACTIVE=prod` set means the app may silently fall back to dev defaults (e.g. an in-memory H2 database), losing data on every restart. Fix: always set the profile explicitly via environment variable in deployment manifests.

- **Doing long-running work inside `CommandLineRunner`/`ApplicationRunner`.** Since these block until `run()` returns, a slow task (e.g. calling a flaky external API) delays application startup and health checks. Fix: kick off long tasks asynchronously (`@Async`, a `TaskExecutor`, or a scheduler) instead of running them synchronously in the runner.

  ```java
  // ❌ bad — blocks app startup for a long external call
  @Override
  public void run(String... args) {
      externalApiClient.syncAllRecords(); // takes 5 minutes
  }

  // ✅ good — fire and continue
  @Override
  public void run(String... args) {
      taskExecutor.execute(() -> externalApiClient.syncAllRecords());
  }
  ```

- **Mixing `.properties` and `.yml` for the same keys.** Having both `application.properties` and `application.yml` in the same module with overlapping keys creates confusing precedence and hard-to-trace bugs. Fix: pick exactly one format per project.

- **Assuming `.yml` booleans/strings are always safe.** Unquoted values like `country: NO` get parsed as `false` by the YAML spec, not the string `"NO"`. Fix: quote ambiguous scalar values (`country: "NO"`).

- **Forgetting `spring-boot-starter-parent` (or the BOM) and hardcoding library versions.** Manually pinning versions of Jackson, Tomcat, or Hibernate independently of Spring Boot's managed versions risks incompatible combinations and hard-to-diagnose runtime errors. Fix: inherit from `spring-boot-starter-parent` or import `spring-boot-dependencies` as a BOM, and omit explicit versions on managed dependencies.

- **Depending on `spring-boot-starter-web` and `spring-boot-starter-webflux` together without intent.** Both starters bring in conflicting web stacks (servlet vs. reactive), and Spring Boot's auto-detection of which one to configure can produce confusing behavior. Fix: pick one stack per application unless you deliberately need both (e.g. a reactive WebClient inside an MVC app, which is fine — just don't add both *starters*).

- **Treating `@ConfigurationProperties` fields as always required.** Without validation annotations, a missing or malformed property silently binds to `null`/`0` instead of failing fast at startup. Fix: annotate the properties class with `@Validated` and use Jakarta Bean Validation constraints (`@NotBlank`, `@Min`, etc.) so misconfiguration fails immediately with a clear error.

- **Not knowing the externalized configuration precedence order.** Debugging "why isn't my property override taking effect" is common when a lower-priority source (e.g. `application.yml`) is expected to beat a higher-priority one (e.g. a command-line flag) — it never will. Fix: know the precedence order (CLI args > env vars > profile-specific files > `application.yml`) and check for overrides at higher levels first.

- **Confusing "embedded server" with "no server config needed."** Some reviewers assume an embedded Tomcat means you can't tune thread pools, timeouts, or ports — leading to unnecessary custom `WebServerFactoryCustomizer` code for things that are simple properties. Fix: check `server.*` properties (`server.tomcat.max-threads`, `server.port`, etc.) before writing custom Java configuration.

## Quick Recap

- **Spring Boot** = Spring Framework + opinionated defaults + embedded server + starter dependencies, making project setup fast.
- **Auto-configuration** conditionally registers beans based on classpath contents and always backs off if you define your own bean (`@ConditionalOnMissingBean`). Use `--debug` to see the report.
- **Starters** are dependency bundles (`spring-boot-starter-*`) with no version numbers needed thanks to the parent POM/BOM.
- **Spring Boot CLI** runs Groovy/Java scripts as mini Spring apps — mostly a prototyping tool today.
- **Spring Initializr** (start.spring.io) is the standard way to scaffold new projects with the right starters and versions.
- **Embedded servers** (Tomcat/Jetty/Undertow/Netty) ship inside your fat JAR — `java -jar app.jar` is enough to run it, no external server install needed.
- **Project structure** follows Maven's standard layout; keep the main class in the top-level root package for component scanning to work.
- **`@SpringBootApplication`** = `@SpringBootConfiguration` + `@EnableAutoConfiguration` + `@ComponentScan`, all in one annotation.
- **Externalized configuration** lets one build artifact run in any environment; precedence order matters (CLI args and env vars beat files).
- **`@ConfigurationProperties`** binds groups of related settings onto type-safe objects (POJOs or records), with relaxed binding and optional validation — prefer it over scattered `@Value`.
- **YAML vs properties**: same capability, different syntax; YAML is better for nested config but is whitespace- and type-sensitive.
- **Profiles** (`spring.profiles.active`) swap configuration and beans per environment via `application-{profile}.yml` and `@Profile`.
- **`CommandLineRunner`** and **`ApplicationRunner`** both run once at startup after the context is ready; the only difference is raw `String[]` vs. parsed `ApplicationArguments`. Never block them with long-running work.
