# 22. Spring Boot Internals

## Overview

Spring Boot feels like magic: you add a dependency, run `main()`, and a working web server appears with no XML and almost no configuration. This chapter opens the hood and shows you the gears. You will learn how auto-configuration decides what to create, how conditions turn features on and off, how a "starter" jar is actually built, and what happens, in order, from the moment you call `SpringApplication.run()` to the moment your app prints "Started Application in 1.234 seconds". Interviewers love this topic because it separates people who *use* Spring Boot from people who *understand* it — knowing the exact sequence of events, and being able to name the classes involved, is a strong signal of real experience. Nothing here is exotic; it is the same Spring container you already know, wrapped in a very disciplined bootstrapping process.

## Auto Configuration Internals

**Auto-configuration** is Spring Boot's way of guessing which beans you need based on what is on your classpath, and registering them for you — but only if you have not already defined them yourself.

Think of it like a smart furniture delivery service: if it sees you have an empty living room and a box labelled "sofa" in your inventory (classpath), it wheels in a default sofa. If you already put your own sofa there, it leaves your sofa alone.

The chain of annotations that makes this happen:

```
@SpringBootApplication
   └── @SpringBootConfiguration   (a specialised @Configuration)
   └── @EnableAutoConfiguration
           └── @Import(AutoConfigurationImportSelector.class)
   └── @ComponentScan
```

- `@SpringBootApplication` is a meta-annotation — a shortcut for three other annotations.
- `@EnableAutoConfiguration` is the switch that turns auto-configuration on.
- `AutoConfigurationImportSelector` is a `DeferredImportSelector`. During `@Configuration` class processing, Spring calls it to get a list of extra configuration classes to import — these are the auto-configuration classes.

**Where does the list of candidates come from?**

This is the detail that trips people up, especially anyone who learned Spring Boot 2.x. In **Spring Boot 3.x**, auto-configuration classes are declared in a plain text file:

```
META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports
```

One fully-qualified class name per line, for example:

```
org.springframework.boot.autoconfigure.web.servlet.DispatcherServletAutoConfiguration
org.springframework.boot.autoconfigure.web.servlet.ServletWebServerFactoryAutoConfiguration
org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration
org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration
```

> **Accuracy note:** In Spring Boot 1.x/2.x, auto-configuration classes were listed under the key `org.springframework.boot.autoconfigure.EnableAutoConfiguration` inside `META-INF/spring.factories`. In Spring Boot 3.x that mechanism is **removed for auto-configuration** (it still exists for a few unrelated SPI hooks, but auto-config classes specifically must use the `.imports` file). If you see a tutorial using `spring.factories` for auto-configuration on Boot 3, it is outdated.

Every class listed gets loaded, then filtered by its `@Conditional...` annotations (see next section). Only the ones whose conditions pass actually register beans.

**Excluding auto-configuration**

Sometimes Boot guesses wrong, or you want to configure something manually:

```java
@SpringBootApplication(exclude = { DataSourceAutoConfiguration.class })
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
```

Or from `application.properties`, useful when you cannot edit the annotation (e.g. a class in a shared parent module):

```properties
spring.autoconfigure.exclude=org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration
```

**Debugging what actually ran**

Run your app with `--debug` and Spring Boot prints a `ConditionEvaluationReport` at startup: every auto-configuration class, split into "Positive matches" (applied) and "Negative matches" (skipped, with the exact reason).

```bash
java -jar myapp.jar --debug
```

Sample (trimmed) output:

```
=========================
AUTO-CONFIGURATION REPORT
=========================

Positive matches:
-----------------
   DataSourceAutoConfiguration matched:
      - @ConditionalOnClass found required class 'javax.sql.DataSource' (OnClassCondition)

Negative matches:
-----------------
   RabbitAutoConfiguration:
      Did not match:
         - @ConditionalOnClass did not find required class 'org.springframework.amqp.rabbit.connection.ConnectionFactory' (OnClassCondition)
```

This report is your single best debugging tool when "a bean I expected isn't there" or "a bean I didn't expect appeared."

## Conditional Annotations

Every auto-configuration class is essentially a pile of `if` statements expressed declaratively. Spring Boot supplies a family of `@ConditionalOnXxx` annotations, all built on top of the plain Spring `@Conditional` mechanism.

| Annotation | Matches when... |
|---|---|
| `@ConditionalOnClass` | The given class is present on the classpath |
| `@ConditionalOnMissingClass` | The given class is **not** present on the classpath |
| `@ConditionalOnBean` | A bean of the given type (or name) already exists in the context |
| `@ConditionalOnMissingBean` | No bean of the given type (or name) exists yet |
| `@ConditionalOnProperty` | A given property exists / equals a given value / is not `false` |
| `@ConditionalOnResource` | A given classpath resource (e.g. a file) exists |
| `@ConditionalOnWebApplication` | The app is a web app (servlet or reactive) |
| `@ConditionalOnExpression` | A SpEL expression evaluates to `true` |
| `@ConditionalOnJava` | The running JVM version matches a range |
| `@ConditionalOnCloudPlatform` | The app is detected as running on a specific cloud platform (e.g. `CLOUD_FOUNDRY`, `KUBERNETES`) |

Example combining several of them, similar to real auto-configuration code:

```java
@AutoConfiguration
@ConditionalOnClass(DataSource.class)
@ConditionalOnWebApplication
@ConditionalOnProperty(prefix = "app.cache", name = "enabled", havingValue = "true", matchIfMissing = true)
public class MyCacheAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    public CacheManager cacheManager() {
        return new ConcurrentMapCacheManager();
    }
}
```

Read this as: "Only wire in a cache manager if we're a web app, `DataSource` is on the classpath, the property `app.cache.enabled` is `true` (or absent), **and** the user hasn't already defined their own `CacheManager` bean."

**Writing a custom condition**

If none of the built-ins fit, implement `Condition` (or extend `SpringBootCondition` for nicer log messages):

```java
public class OnRunningInDockerCondition extends SpringBootCondition {

    @Override
    public ConditionOutcome getMatchOutcome(ConditionContext context, AnnotatedTypeMetadata metadata) {
        boolean dockerEnv = Files.exists(Path.of("/.dockerenv"));
        return dockerEnv
                ? ConditionOutcome.match("Running inside a Docker container")
                : ConditionOutcome.noMatch("No /.dockerenv file found");
    }
}
```

```java
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Conditional(OnRunningInDockerCondition.class)
public @interface ConditionalOnDocker {
}
```

**Why `@ConditionalOnMissingBean` ordering matters**

`@ConditionalOnMissingBean` only sees beans that have **already been registered** at the point it is evaluated. Auto-configuration classes are processed in a specific order (see next section). If your custom auto-configuration runs *before* the one it's trying to defer to, its `@ConditionalOnMissingBean` check will pass even though a bean is *about to* be created later — resulting in two competing beans, or a confusing "why did my default get skipped/created" bug. This is exactly why `@AutoConfigureOrder`, `@AutoConfigureBefore`, and `@AutoConfigureAfter` exist.

## Auto Configuration Ordering

Because many auto-configuration classes touch the same infrastructure (e.g. multiple classes want to configure a `DataSource`), order matters. Spring Boot gives you three tools:

| Annotation | Purpose |
|---|---|
| `@AutoConfigureOrder` | Coarse-grained ordering, same idea as `@Order`, lower value = earlier |
| `@AutoConfigureBefore` | "Process me before these specific auto-configuration classes" |
| `@AutoConfigureAfter` | "Process me after these specific auto-configuration classes" |

```java
@AutoConfiguration
@AutoConfigureAfter(DataSourceAutoConfiguration.class)
@ConditionalOnBean(DataSource.class)
public class MyJdbcTemplateAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    public JdbcTemplate jdbcTemplate(DataSource dataSource) {
        return new JdbcTemplate(dataSource);
    }
}
```

This says: "wait until `DataSourceAutoConfiguration` has had its chance to run, so that by the time I check `@ConditionalOnBean(DataSource.class)`, the answer is reliable."

**The golden rule: user beans always win.**

```
                 ┌────────────────────────────┐
                 │  Your @Configuration beans  │   ← processed first
                 └──────────────┬─────────────┘
                                │
                                ▼
                 ┌────────────────────────────┐
                 │   Auto-configuration beans  │   ← processed LAST
                 │  (@ConditionalOnMissingBean) │
                 └────────────────────────────┘
```

Regardless of ordering annotations between auto-configuration classes, **all** auto-configuration is deferred until after your own `@Configuration` classes have registered their beans (`AutoConfigurationImportSelector` implements `DeferredImportSelector` specifically to guarantee this). That is why `@ConditionalOnMissingBean` reliably detects beans you defined yourself, no matter what order your classes happen to be scanned in.

## Starter Creation

A **starter** is just a Maven/Gradle dependency that pulls in a library plus its auto-configuration plus sensible defaults, so that adding one line to your build file gives you a working feature. Spring Boot's naming convention splits the concern into two artifacts:

| Module | Contains |
|---|---|
| `xxx-spring-boot-autoconfigure` | The actual `@AutoConfiguration` classes, `@ConfigurationProperties` classes, the `.imports` file |
| `xxx-spring-boot-starter` | An (almost) empty POM/build file that just declares dependencies: the library itself + the autoconfigure module |

Official Spring modules use `spring-boot-starter-xxx`; **third-party** starters should use `xxx-spring-boot-starter` (name first) to avoid implying they're official.

**Step-by-step: build "acme-spring-boot-starter"**

**1. Configuration properties class**

```java
@ConfigurationProperties(prefix = "acme")
public record AcmeProperties(
        String greeting,
        boolean enabled,
        Duration timeout
) {
    public AcmeProperties {
        if (timeout == null) {
            timeout = Duration.ofSeconds(5);
        }
    }
}
```

**2. Auto-configuration class**

```java
@AutoConfiguration
@ConditionalOnClass(AcmeService.class)
@EnableConfigurationProperties(AcmeProperties.class)
public class AcmeAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    public AcmeService acmeService(AcmeProperties properties) {
        return new AcmeService(properties.greeting(), properties.timeout());
    }
}
```

**3. The imports file** (module: `acme-spring-boot-autoconfigure`)

`src/main/resources/META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`:

```
com.example.acme.autoconfigure.AcmeAutoConfiguration
```

**4. Configuration metadata for IDE auto-complete**

Add the annotation processor so IntelliJ/VS Code show property descriptions and type hints when developers type `acme.` in `application.yml`:

Maven (`pom.xml` for the autoconfigure module):

```xml
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-autoconfigure</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-configuration-processor</artifactId>
        <optional>true</optional>
    </dependency>
</dependencies>
```

Gradle (`build.gradle`):

```groovy
dependencies {
    implementation 'org.springframework.boot:spring-boot-autoconfigure'
    annotationProcessor 'org.springframework.boot:spring-boot-configuration-processor'
}
```

This processor generates `META-INF/spring-configuration-metadata.json` automatically at compile time — you never hand-write it.

**5. The starter module** (`acme-spring-boot-starter/pom.xml`) — deliberately empty besides dependencies:

```xml
<project>
    <modelVersion>4.0.0</modelVersion>
    <artifactId>acme-spring-boot-starter</artifactId>

    <dependencies>
        <dependency>
            <groupId>com.example.acme</groupId>
            <artifactId>acme-spring-boot-autoconfigure</artifactId>
        </dependency>
        <dependency>
            <groupId>com.example.acme</groupId>
            <artifactId>acme-core</artifactId>
        </dependency>
    </dependencies>
</project>
```

Consumers of your starter now just add:

```xml
<dependency>
    <groupId>com.example.acme</groupId>
    <artifactId>acme-spring-boot-starter</artifactId>
    <version>1.0.0</version>
</dependency>
```

...and set `acme.greeting=Hello` in their `application.properties`. No `@Import`, no manual bean wiring.

## Bean Loading Process

Once Spring decides *which* configuration classes to use (your own plus the auto-configuration winners), it has to turn annotated classes into actual objects. This happens in clearly separated phases.

```
1. ConfigurationClassPostProcessor runs
   → parses @Configuration / @ComponentScan / @Import / @Bean methods
   → registers a BeanDefinition (a "recipe", not an object yet) for each bean

2. Other BeanFactoryPostProcessors run
   → can still add/modify/remove BeanDefinitions
   → e.g. PropertySourcesPlaceholderConfigurer resolves ${...} in definitions

3. Bean instantiation begins (one bean at a time, resolving dependencies)
   → constructor called (or factory method)
   → dependencies injected (constructor / field / setter)

4. BeanPostProcessor#postProcessBeforeInitialization
   → e.g. @Autowired/@Value field injection happens here (AutowiredAnnotationBeanPostProcessor)

5. Initialization callbacks, in this order:
   a) @PostConstruct method
   b) InitializingBean#afterPropertiesSet()
   c) custom init-method (if declared)

6. BeanPostProcessor#postProcessAfterInitialization
   → e.g. AOP proxies are wrapped around the bean HERE

7. Bean is ready and placed in the singleton cache
```

ASCII diagram of the same flow, bean-per-bean:

```
BeanDefinition registered
         │
         ▼
  Constructor / factory method called ──► raw instance
         │
         ▼
  Dependency injection (@Autowired fields/setters)
         │
         ▼
  BeanPostProcessor.postProcessBeforeInitialization()
         │
         ▼
  @PostConstruct → InitializingBean.afterPropertiesSet() → init-method
         │
         ▼
  BeanPostProcessor.postProcessAfterInitialization()   (AOP proxy created here)
         │
         ▼
  Fully initialized bean, ready to use
```

A `BeanFactoryPostProcessor` operates on **definitions** (metadata) before any bean is instantiated. A `BeanPostProcessor` operates on **instances**, once objects exist. Mixing these up is a very common interview trip-up:

| | Operates on | Typical use |
|---|---|---|
| `BeanFactoryPostProcessor` | `BeanDefinition` metadata | Property placeholder resolution, registering extra definitions |
| `BeanPostProcessor` | Live bean instances | `@Autowired` processing, AOP proxying, `@PostConstruct` handling |

## Environment Processing

Before a single bean is created, Spring Boot must figure out *where all the configuration comes from* — command line args, environment variables, `application.yml`, profile-specific files, config servers, and so on. This is the job of the `Environment` and the classes that populate it.

**`EnvironmentPostProcessor`**

An SPI hook that lets you (or Spring Boot itself) mutate the `Environment` very early, before the `ApplicationContext` even exists. Register it via a `.imports`-style file:

`META-INF/spring/org.springframework.boot.env.EnvironmentPostProcessor.imports`:
```
com.example.MyEnvironmentPostProcessor
```

```java
public class MyEnvironmentPostProcessor implements EnvironmentPostProcessor {

    @Override
    public void postProcessEnvironment(ConfigurableEnvironment environment, SpringApplication application) {
        Map<String, Object> extra = Map.of("feature.betaEnabled", "true");
        environment.getPropertySources()
                   .addFirst(new MapPropertySource("betaFlags", extra));
    }
}
```

**`ConfigDataEnvironmentPostProcessor`**

Since **Spring Boot 2.4**, loading of `application.properties`/`.yml` files (including profile variants, `spring.config.import`, and multi-document YAML) is handled by the **ConfigData API**, orchestrated by `ConfigDataEnvironmentPostProcessor`. It replaced the older, more ad-hoc `application.properties` loading logic and made things like importing extra config files explicit and predictable:

```yaml
spring:
  config:
    import: "optional:configserver:https://config.example.com"
  profiles:
    active: dev
---
spring:
  config:
    activate:
      on-profile: dev
server:
  port: 8081
```

**`PropertySource` ordering**

Boot layers property sources with a strict precedence — higher in the list wins:

```
1. Command-line arguments               (--server.port=9090)
2. JVM system properties                (-Dserver.port=9090)
3. OS environment variables             (SERVER_PORT=9090)
4. application-{profile}.yml / .properties
5. application.yml / .properties
6. @PropertySource on @Configuration classes
7. Default properties (SpringApplication.setDefaultProperties)
```

**`SpringApplication` listeners**

You can hook into the earliest lifecycle events by registering a `SpringApplicationRunListener` via `META-INF/spring.factories` (this SPI, unlike auto-configuration, still uses `spring.factories` in Boot 3) or simply add `ApplicationListener` beans:

```java
public class TimingListener implements ApplicationListener<ApplicationStartingEvent> {
    @Override
    public void onApplicationEvent(ApplicationStartingEvent event) {
        System.out.println("JVM boot to Spring Boot handoff starting...");
    }
}
```

## Configuration Binding Internals

"Binding" is how a flat string like `acme.timeout=5s` in a properties file turns into a typed `Duration` field on a Java object. The engine behind `@ConfigurationProperties` (and `Binder.get(environment).bind(...)` if you use it directly) does three things: normalize names, find a source, and convert types.

**`ConfigurationPropertySource` and relaxed binding**

Boot wraps every `PropertySource` in a `ConfigurationPropertySource`, which understands **relaxed binding** — several naming styles all map to the same property:

| In your class | Matches all of these in config |
|---|---|
| `firstName` | `first-name`, `first_name`, `firstName`, `FIRST_NAME` |

```yaml
acme:
  first-name: Ada     # kebab-case is the recommended style in files
```

```properties
ACME_FIRST_NAME=Ada
```

```bash
export ACME_FIRSTNAME=Ada   # environment variables: uppercase, no separators
```

All three resolve to the same Java field `firstName`. This is essential for supporting environment variables cleanly, since shells don't allow dots or dashes.

**`Binder`**

The `Binder` class is the low-level engine. `@ConfigurationProperties` is really just a convenient, annotation-driven wrapper around it:

```java
Binder binder = Binder.get(environment);
AcmeProperties props = binder.bind("acme", AcmeProperties.class).orElse(new AcmeProperties());
```

**`ConversionService`**

Once the right raw string is found, Boot uses an extended `ConversionService` (registering many `Converter`/`Formatter` beans, plus Boot-specific ones like `DurationConverter`, `DataSizeConverter`) to turn `"5s"` into `Duration.ofSeconds(5)`, `"10MB"` into a `DataSize`, comma-separated strings into `List<String>`, and so on.

```java
@ConfigurationProperties(prefix = "acme.cache")
public class CacheProps {
    private DataSize maxSize;     // "10MB" -> DataSize
    private Duration ttl;         // "30m"  -> Duration
    private List<String> regions; // "eu,us" -> List.of("eu", "us")
    // getters/setters
}
```

**Constructor binding**

Modern `@ConfigurationProperties` classes are usually **immutable records**, and Boot detects a single non-default constructor automatically — no extra annotation needed on the class itself in Boot 3 (the old `@ConstructorBinding` annotation is still supported but rarely required now):

```java
@ConfigurationProperties(prefix = "acme")
public record AcmeProperties(String greeting, Duration timeout, boolean enabled) {
}
```

Rules of thumb:

- One constructor + immutable fields → Boot infers **constructor binding** automatically.
- A no-args constructor with setters → Boot uses classic **JavaBean binding** (setter injection after construction).
- `record` types are always constructor-bound, since they have no setters.

## Embedded Servlet Container

Classic Java web apps needed you to install Tomcat, then deploy a WAR into it. Spring Boot flips this: the server is a **library dependency**, embedded inside your own runnable JAR, started programmatically as part of context startup.

**Key classes**

| Class | Role |
|---|---|
| `ServletWebServerApplicationContext` | An `ApplicationContext` subtype that knows how to bootstrap and hold a running servlet container |
| `ServletWebServerFactory` | Abstraction for "create me a running web server on this port" — implemented by `TomcatServletWebServerFactory`, `JettyServletWebServerFactory`, `UndertowServletWebServerFactory` |
| `WebServerFactoryCustomizer` | Hook to tweak factory settings (ports, SSL, connectors) before the server starts |
| `ServletContextInitializer` | Callback used to register servlets/filters/listeners into the `ServletContext` programmatically (replaces `web.xml`) |

```
    ApplicationContext refresh()
             │
             ▼
  ServletWebServerApplicationContext detects a
  ServletWebServerFactory bean in the context
             │
             ▼
     factory.getWebServer(initializers...)
             │
             ▼
   ┌─────────────┬─────────────┬─────────────┐
   │   Tomcat     │    Jetty    │  Undertow   │
   └─────────────┴─────────────┴─────────────┘
             │
             ▼
        server.start()   ← real socket opens, port is listening
```

**Swapping servers**

Tomcat is the default. Switching to Jetty or Undertow is a dependency change, not a code change:

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
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

`ServletWebServerFactoryAutoConfiguration` uses `@ConditionalOnClass` checks against each server's marker class to decide which factory bean to register — exactly the auto-configuration mechanism from earlier sections, just applied to a bigger piece of infrastructure.

**Customizing the embedded server**

```java
@Component
public class PortCustomizer implements WebServerFactoryCustomizer<ConfigurableServletWebServerFactory> {

    @Override
    public void customize(ConfigurableServletWebServerFactory factory) {
        factory.setPort(9090);
        factory.addInitializers((ServletContextInitializer) servletContext ->
                servletContext.setInitParameter("customParam", "value"));
    }
}
```

```yaml
server:
  port: 8443
  ssl:
    enabled: true
    key-store: classpath:keystore.p12
    key-store-password: changeit
```

## Spring Boot Lifecycle

`SpringApplication.run()` is a single method call, but under it is a strict, well-defined sequence of phases and events. Knowing this sequence lets you pick the *right* extension point instead of guessing.

```java
public static void main(String[] args) {
    SpringApplication.run(Application.class, args);
}
```

**Step by step**

```
1.  Create SpringApplication instance
    - detect application type (SERVLET / REACTIVE / NONE)
    - locate SpringApplicationRunListeners (from spring.factories)

2.  listeners.starting()               → ApplicationStartingEvent
       (logging not yet configured, environment not yet ready)

3.  Prepare Environment
       (parse args, load config files, run EnvironmentPostProcessors)
    listeners.environmentPrepared()    → ApplicationEnvironmentPreparedEvent

4.  Create ApplicationContext
       (ServletWebServerApplicationContext, ReactiveWebServerApplicationContext, or
        AnnotationConfigApplicationContext)
    listeners.contextPrepared()        → ApplicationContextInitializedEvent

5.  Load sources into context
       (register the primary @SpringBootApplication class as a bean definition)
    listeners.contextLoaded()          → ApplicationPreparedEvent

6.  context.refresh()
       - BeanFactoryPostProcessors, BeanPostProcessors
       - all singleton beans instantiated (see Bean Loading Process)
       - embedded web server started (see Embedded Servlet Container)
    (fires the standard Spring ContextRefreshedEvent internally)

7.  listeners.started()                → ApplicationStartedEvent

8.  Run runners, in this order:
       - ApplicationRunner beans
       - CommandLineRunner beans

9.  listeners.ready()                  → ApplicationReadyEvent
       (app is fully up — good place for health checks / readiness probes)

   ── If ANYTHING above throws ──
   listeners.failed()                  → ApplicationFailedEvent
       (context is closed, exception rethrown, process typically exits)
```

Event summary table:

| Event | When | Typical use |
|---|---|---|
| `ApplicationStartingEvent` | Very first thing, before Environment exists | Bootstrap logging config |
| `ApplicationEnvironmentPreparedEvent` | Environment ready, context not yet created | Inspect/modify properties |
| `ApplicationContextInitializedEvent` | Context created, not yet loaded | Add `ApplicationContextInitializer` logic |
| `ApplicationPreparedEvent` | Bean definitions loaded, not refreshed | Last chance before bean instantiation |
| `ApplicationStartedEvent` | Context refreshed, runners not yet run | Post-refresh, pre-runner hooks |
| `ApplicationReadyEvent` | Everything done, including runners | Signal "ready to serve traffic" |
| `ApplicationFailedEvent` | Any exception during startup | Custom failure logging/alerting |

**Runners example**

```java
@Component
public class WarmupRunner implements ApplicationRunner {
    @Override
    public void run(ApplicationArguments args) {
        System.out.println("Cache warmed up, args: " + args.getOptionNames());
    }
}
```

**Graceful shutdown**

Since Spring Boot 2.3, embedded servers support a graceful shutdown window: stop accepting *new* requests, but let in-flight requests finish before the JVM exits.

```yaml
server:
  shutdown: graceful

spring:
  lifecycle:
    timeout-per-shutdown-phase: 30s
```

Internally, a `SIGTERM` (e.g. from Kubernetes stopping a pod) triggers `ConfigurableApplicationContext.close()`, which:

1. Publishes a `ContextClosedEvent`.
2. Calls `@PreDestroy` methods and `DisposableBean.destroy()` on beans, in reverse-dependency order.
3. Tells the web server to stop accepting new connections and wait for active ones to drain (up to the configured timeout).
4. Fully shuts down the server socket and exits.

## Common Code Review / Interview Pitfalls

- Placing `@SpringBootApplication` in a leaf package (e.g. `com.example.app.config`) so `@ComponentScan`'s default base package misses sibling packages like `com.example.app.service` — beans silently never get registered.
- Adding explicit `@ComponentScan(basePackages = "com.example")` that overlaps with the default scan on `@SpringBootApplication`, causing the same classes to be scanned twice (usually harmless with `@Component`, but a real problem if it re-registers manually created `@Bean` definitions and triggers "bean already defined" errors).
- Component-scanning a package so broad it pulls in third-party or generated code that happens to carry Spring annotations, registering beans you never intended to manage.
- Defining a `@Bean` with the same type/name as an auto-configured one (e.g. your own `ObjectMapper`) and not realizing it *silently* replaces or conflicts with the auto-configured bean — always check whether the auto-config bean is `@ConditionalOnMissingBean` (yours quietly wins) or not (you may get a duplicate-bean error, or worse, both exist and only one is wired in by luck of injection order).
- Writing your own `@AutoConfiguration` that uses `@ConditionalOnBean` referencing a bean from another library's auto-configuration, without an explicit `@AutoConfigureAfter` — if your class is processed first, the bean doesn't exist *yet*, and the condition fails even though it "should" have matched.
- Registering auto-configuration through `META-INF/spring.factories` under `EnableAutoConfiguration` in a Spring Boot 3 project — this key is ignored in Boot 3; you must use `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`.
- Shipping a starter without `spring-boot-configuration-processor` on the annotation processor path, so `spring-configuration-metadata.json` is never generated and consumers get no IDE autocomplete or type checking for your custom properties.
- Doing expensive work (HTTP calls, large file reads, cache warming) inside `@PostConstruct`, which runs synchronously during context refresh and directly delays application startup and readiness probes.
- Letting a `CommandLineRunner`/`ApplicationRunner` throw an uncaught exception — this fails the whole application context and triggers `ApplicationFailedEvent`, killing an otherwise healthy app over what might be a non-critical warm-up task.
- Assuming you can freely redefine an existing bean name — bean definition overriding is **disabled by default since Spring Boot 2.1**; redefining a bean with the same name now throws `BeanDefinitionOverrideException` unless you explicitly set `spring.main.allow-bean-definition-overriding=true`.
- Forgetting that `@ConditionalOnMissingBean` checks are evaluated during context loading, and assuming they see beans that are only registered *later* by another slow-to-process auto-configuration class — ordering annotations exist precisely to prevent this class of bug.
- Excluding an auto-configuration class by the wrong fully-qualified name (a common copy-paste error after a Boot version upgrade renames/moves classes), so the exclusion silently does nothing.
- Treating `ApplicationReadyEvent` and `ApplicationStartedEvent` as interchangeable — runners have not executed yet at `ApplicationStartedEvent`, so code depending on runner side effects should listen for `ApplicationReadyEvent` instead.

## Quick Recap

- `@SpringBootApplication` = `@SpringBootConfiguration` + `@EnableAutoConfiguration` + `@ComponentScan`.
- Auto-configuration classes are listed in `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports` (Boot 3.x) — not `spring.factories`.
- `AutoConfigurationImportSelector` is a `DeferredImportSelector`, so auto-config always runs **after** your own `@Configuration` classes — your beans always win.
- `@Conditional...` annotations gate whether an auto-configuration class or bean activates; `@ConditionalOnMissingBean` is the main mechanism that lets user beans override defaults.
- `@AutoConfigureBefore` / `@AutoConfigureAfter` / `@AutoConfigureOrder` control ordering *between* auto-configuration classes so conditional checks see accurate state.
- A starter = `xxx-spring-boot-starter` (dependency glue) + `xxx-spring-boot-autoconfigure` (actual logic); use `spring-boot-configuration-processor` for IDE metadata.
- Bean creation order: `BeanDefinition` registration → `BeanFactoryPostProcessor` → instantiate/inject → `BeanPostProcessor` (before) → `@PostConstruct`/`InitializingBean` → `BeanPostProcessor` (after, AOP proxying happens here) → ready.
- Environment setup uses `EnvironmentPostProcessor` and, since Boot 2.4, the ConfigData API (`ConfigDataEnvironmentPostProcessor`) to load and layer property sources with a strict precedence order.
- `@ConfigurationProperties` binding uses relaxed name matching, a `Binder`, and an extended `ConversionService`; records get constructor binding automatically.
- The embedded server (Tomcat/Jetty/Undertow) is just a library dependency selected via `@ConditionalOnClass`; swap it by changing starters, not code.
- `SpringApplication.run()` fires a strict event sequence: `Starting` → `EnvironmentPrepared` → `ContextInitialized` → context refresh → `Started` → runners → `Ready` (or `Failed` on any exception).
- Graceful shutdown (`server.shutdown: graceful`) drains in-flight requests before the JVM exits on `SIGTERM`.
