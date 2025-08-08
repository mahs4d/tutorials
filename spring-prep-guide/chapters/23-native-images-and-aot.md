# 23. Native Images & AOT

## Overview

Normal Java runs on the JVM: your `.class` files hold bytecode, and the JVM's Just-In-Time (JIT) compiler translates the hot parts into machine code *while the program runs*. That's why Java apps start slow-ish and get faster the longer they run — the JIT is still warming up. Ahead-Of-Time (AOT) compilation flips this: all the translation to machine code happens *before* you ever run the app, at build time. The result is a **native image** — a standalone executable that doesn't need a JVM installed at all.

Why would you want that? Startup time drops from seconds to milliseconds, memory footprint shrinks dramatically (no JIT compiler, no bytecode interpreter sitting in RAM), and both make native images a great fit for serverless functions and Kubernetes workloads that scale to zero and need to boot instantly under load. The catch: you give up the JIT's ability to keep optimizing a long-running process based on real runtime behavior, so peak throughput after a long warm-up can be lower on native. You also give up a lot of Java's dynamic flexibility — reflection, dynamic class loading, and dynamic proxies all need to be known about *in advance*, which is where Spring's AOT engine and "hints" come in. Build times get much longer too, and debugging tooling is less mature than the JVM's.

## GraalVM

**GraalVM** is a special JDK distribution built by Oracle that can do something a normal JDK can't: compile a Java application all the way down to a native, self-contained executable. It ships two things you care about here:

- **`native-image`** — a build tool that performs "closed-world" static analysis of your whole application (your code, its dependencies, and the JDK classes it touches) and emits a native binary.
- **SubstrateVM** — the tiny runtime embedded inside that binary. It replaces the full JVM with a minimal substrate that handles garbage collection, thread scheduling, and other low-level runtime concerns, but is far lighter than a full JVM.

The key idea behind `native-image` is the **closed-world assumption**: at build time, the tool assumes it can see *every* class that will ever be used, and it aggressively removes (or "dead-code-eliminates") anything it can prove is unreachable. This is how it gets such small, fast-starting binaries — but it also means anything the analysis *can't* see (reflection, dynamically loaded classes, resources loaded by string paths) will silently not exist in the final binary, and will blow up at runtime instead of at compile time.

Think of it like packing for a trip where the suitcase is sealed at the airport: if you didn't put something in before sealing it, it's not coming with you, and you won't find out until you need it.

Installing GraalVM via SDKMAN (the easiest route on Linux/macOS):

```bash
# List available GraalVM distributions
sdk list java | grep -i graal

# Install GraalVM for JDK 21 (Community Edition, "-graalce" suffix)
sdk install java 21.0.2-graalce

# Use it for the current shell
sdk use java 21.0.2-graalce

# Confirm native-image is available
native-image --version
```

On some distributions `native-image` is a separate component you install via `gu`:

```bash
gu install native-image
```

JVM vs native image, with concrete illustrative numbers for a typical small Spring Boot REST service (exact figures vary by app size, hardware, and JDK/GraalVM version — treat these as order-of-magnitude, not guarantees):

| Aspect | JVM (JIT) | Native Image (AOT) |
|---|---|---|
| Cold startup time | ~1.5-3 s | ~0.05-0.1 s (50-100 ms) |
| RSS memory at startup | ~150-300 MB | ~30-80 MB |
| Peak throughput (sustained load, no PGO) | Baseline (100%) | ~70-90% of JVM, sometimes lower on CPU-bound hot loops |
| Peak throughput (with PGO) | Baseline (100%) | Often close to JVM, sometimes on par |
| Build time (typical Boot app) | ~10-30 s (`mvn package`) | ~2-6 minutes (`native:compile`) |
| Image size | ~20-40 MB JAR (JVM install shared, ~300 MB, across many apps) | ~60-120 MB single self-contained binary |
| Recommended CI build memory | ~1-2 GB | ~4-8 GB |
| Dynamic features (reflection, agents, hot class reload) | Full support | Limited, must be declared ahead of time |
| Debugging tools | Mature (profilers, JFR, debuggers, hot-reload) | Improving but still less mature |

## Spring AOT

Spring Boot 3 ships its own **AOT engine**, separate from GraalVM's own analysis. It runs at *build time*, before `native-image` (or even before running on the plain JVM), and its job is to remove as much runtime guesswork from Spring's startup process as possible.

Normally, Spring Boot starts up by scanning the classpath, evaluating `@Conditional` annotations, and building the `BeanFactory` all through reflection, at runtime, every single time the app boots. Spring's AOT engine instead does that work *once*, at build time, and generates plain Java source code that does the equivalent bean registration directly — no reflection, no conditional re-evaluation, no classpath scanning. It also generates the metadata GraalVM's own tool needs:

- `reflect-config.json` — which classes/methods/fields need reflective access.
- `resource-config.json` — which classpath resources (templates, `.properties`, `messages.properties`, etc.) must be bundled into the image.
- `proxy-config.json` — which JDK dynamic proxy interfaces need to be generated ahead of time.

You can trigger this step directly without going all the way to a native binary, which is handy for inspecting what Spring generates:

```bash
# Maven
mvn spring-boot:process-aot

# Gradle
./gradlew processAot
```

After running it, look under `target/spring-aot/main/` (Maven) or `build/generated/aotSources` (Gradle) — you'll find generated classes like `MyApplication__ApplicationContextInitializer.java` plus the JSON hint files under `resources/META-INF/native-image/`. Opening one of these generated classes is a great way to actually *see* what "no reflection at startup" means in practice — it reads like ordinary, boring Java: `context.registerBean(MyService.class, ...)` calls, one per bean, instead of a reflective scan.

A subtlety worth remembering for interviews: Spring AOT isn't *only* for native images. You can enable the same AOT-generated startup path on a plain JVM, which gives you faster Spring Boot startup even without compiling to native:

```properties
# application.properties
spring.aot.enabled=true
```

```bash
# Or as a launch flag
java -Dspring.aot.enabled=true -jar myapp.jar
```

This is a cheap way to get a chunk of the startup-time win without touching GraalVM at all — worth trying before committing to a full native pipeline.

## Native Compilation

`spring-boot-starter-parent` (Maven) or the Spring Boot Gradle plugin already wires up everything needed to produce a native image — you don't need to hand-configure the GraalVM Maven/Gradle plugin yourself, just activate the `native` profile Spring Boot provides.

**Maven — a realistic full `pom.xml` with the `native` profile:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.2.5</version>
        <relativePath/>
    </parent>

    <groupId>com.example</groupId>
    <artifactId>myapp</artifactId>
    <version>0.0.1-SNAPSHOT</version>

    <properties>
        <java.version>21</java.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-actuator</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>

    <profiles>
        <profile>
            <id>native</id>
            <build>
                <plugins>
                    <plugin>
                        <groupId>org.springframework.boot</groupId>
                        <artifactId>spring-boot-maven-plugin</artifactId>
                        <configuration>
                            <image>
                                <builder>paketobuildpacks/builder-jammy-tiny:latest</builder>
                            </image>
                        </configuration>
                    </plugin>
                    <plugin>
                        <groupId>org.graalvm.buildtools</groupId>
                        <artifactId>native-maven-plugin</artifactId>
                        <configuration>
                            <buildArgs>
                                <buildArg>--gc=G1</buildArg>
                                <buildArg>-H:+ReportExceptionStackTraces</buildArg>
                            </buildArgs>
                        </configuration>
                        <executions>
                            <execution>
                                <id>build-native</id>
                                <goals>
                                    <goal>compile-no-fork</goal>
                                </goals>
                                <phase>package</phase>
                            </execution>
                        </executions>
                    </plugin>
                </plugins>
            </build>
        </profile>
    </profiles>
</project>
```

```bash
# Requires GraalVM to be the active JDK (JAVA_HOME points to it)
mvn -Pnative native:compile

# Run the resulting binary directly - no JVM involved
./target/myapp
```

**Gradle — a realistic full `build.gradle.kts`:**

```kotlin
// build.gradle.kts
plugins {
    java
    id("org.springframework.boot") version "3.2.5"
    id("io.spring.dependency-management") version "1.1.4"
    id("org.graalvm.buildtools.native") version "0.10.1"
}

group = "com.example"
version = "0.0.1-SNAPSHOT"

java {
    sourceCompatibility = JavaVersion.VERSION_21
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    testImplementation("org.springframework.boot:spring-boot-starter-test")
}

graalvmNative {
    binaries {
        named("main") {
            imageName.set("myapp")
            buildArgs.add("--gc=G1")
            buildArgs.add("-H:+ReportExceptionStackTraces")
        }
    }
    // Runs the full test suite against a native binary too - slow, but catches
    // native-only failures before they reach production.
    testSupport = true
}

tasks.test {
    useJUnitPlatform()
}
```

```bash
./gradlew nativeCompile
./build/native/nativeCompile/myapp
```

**No local GraalVM needed — Cloud Native Buildpacks:**

If you don't want to install GraalVM at all, Spring Boot can build a native *container image* using Paketo Buildpacks, which download and run GraalVM inside an isolated build container for you. You just need Docker.

```bash
mvn spring-boot:build-image -Pnative
# or
./gradlew bootBuildImage --imageName=myapp:native
```

This produces a ready-to-run container image (small, distroless-based) containing your native executable:

```bash
docker run --rm -p 8080:8080 myapp:native
```

Notice how fast it comes up compared to the equivalent `docker run myapp:jvm` — that instant startup is the whole point.

**If you'd rather write the Dockerfile yourself**, a multi-stage build keeps the GraalVM toolchain out of your final runtime image — the build stage is heavy (GraalVM + build-time RAM), the runtime stage is tiny:

```dockerfile
# Stage 1: build the native binary using a GraalVM JDK image
FROM ghcr.io/graalvm/native-image-community:21 AS builder

WORKDIR /workspace

# Copy only build files first for better layer caching
COPY mvnw .
COPY .mvn .mvn
COPY pom.xml .
RUN ./mvnw dependency:go-offline -B

COPY src src
RUN ./mvnw -Pnative native:compile -DskipTests

# Stage 2: copy just the binary into a minimal distroless runtime image
FROM gcr.io/distroless/base-nossl-debian12 AS runtime

WORKDIR /app
COPY --from=builder /workspace/target/myapp /app/myapp

EXPOSE 8080
ENTRYPOINT ["/app/myapp"]
```

```bash
docker build -t myapp:native .
docker run --rm -p 8080:8080 myapp:native
```

## Reflection Hints

Java reflection lets code inspect and invoke classes, methods, and fields it didn't know about at compile time — think `Class.forName("com.example.SomeClass")` or a JSON library reading a POJO's fields by name. Under the closed-world assumption, `native-image` has already decided, once and for all, exactly which classes/methods/fields exist reflectively in the binary. If your code reflects into something that wasn't registered, you don't get a compile error — you get a runtime crash, often just in production, because your local JVM tests never exercised the native path.

Spring gives you a few ways to register reflective access:

**`@RegisterReflectionForBinding`** — tell Spring "this type (and everything it needs for serialization/binding) must be reflectively accessible":

```java
import org.springframework.aot.hint.annotation.RegisterReflectionForBinding;

@RegisterReflectionForBinding(CustomerDto.class)
@Service
public class ReportGenerator {
    // Jackson will need reflective access to CustomerDto's fields at runtime
}
```

**`@Reflective`** — a lower-level, meta-annotation building block that framework and library authors use to mark that an annotation itself implies reflective needs (most app developers won't reach for this directly, but you'll see it if you dig into Spring's own annotations like `@EventListener`).

**Hand-written `reflect-config.json`** — for third-party libraries Spring doesn't know about, you can supply the raw GraalVM hint format yourself. Here's a more complete, realistic example covering a legacy mapper class and a DTO used only through reflection-based serialization:

```json
[
  {
    "name": "com.thirdparty.LegacyMapper",
    "allDeclaredConstructors": true,
    "allDeclaredMethods": true,
    "allDeclaredFields": true
  },
  {
    "name": "com.example.dto.InvoiceDto",
    "methods": [
      { "name": "<init>", "parameterTypes": [] },
      { "name": "getAmount", "parameterTypes": [] },
      { "name": "setAmount", "parameterTypes": ["java.math.BigDecimal"] }
    ],
    "fields": [
      { "name": "amount" },
      { "name": "currency" }
    ]
  },
  {
    "name": "com.example.dto.InvoiceDto[]",
    "allDeclaredConstructors": true
  }
]
```

Place it at `src/main/resources/META-INF/native-image/<group>/<artifact>/reflect-config.json` and `native-image` will pick it up automatically during the build.

## Runtime Hints

`RuntimeHintsRegistrar` is Spring's programmatic, type-safe way to describe the same kind of information as those JSON files — reflection, resources, serialization, proxies, and JNI (Java Native Interface, for calling native/C code) — but in Java, so it's checked by the compiler and easy to unit test.

```java
import org.springframework.aot.hint.RuntimeHints;
import org.springframework.aot.hint.RuntimeHintsRegistrar;
import org.springframework.aot.hint.MemberCategory;

public class ReportingRuntimeHints implements RuntimeHintsRegistrar {

    @Override
    public void registerHints(RuntimeHints hints, ClassLoader classLoader) {
        // Reflection on a DTO used by a third-party serializer
        hints.reflection().registerType(CustomerDto.class,
                MemberCategory.INVOKE_DECLARED_CONSTRUCTORS,
                MemberCategory.INVOKE_DECLARED_METHODS,
                MemberCategory.DECLARED_FIELDS);

        // A classpath resource that must be bundled into the image
        hints.resources().registerPattern("reports/*.ftl");
        hints.resources().registerPattern("i18n/messages*.properties");

        // A JDK dynamic proxy interface used by a legacy integration
        hints.proxies().registerJdkProxy(LegacyReportService.class);

        // Java serialization support for a class
        hints.serialization().registerType(ReportSnapshot.class);

        // A JNI-bound native method call (rare, but shows up with some
        // crypto or compression libraries)
        hints.jni().registerType(NativeCompressor.class,
                MemberCategory.INVOKE_DECLARED_METHODS);
    }
}
```

Register it with `@ImportRuntimeHints` on any `@Configuration` class (or the main application class):

```java
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.ImportRuntimeHints;

@SpringBootApplication
@ImportRuntimeHints(ReportingRuntimeHints.class)
public class MyApplication {

    public static void main(String[] args) {
        SpringApplication.run(MyApplication.class, args);
    }
}
```

**The tracing agent — the "just watch what happens" shortcut.** Writing hints by hand for every class is tedious and error-prone. GraalVM ships a **tracing agent** you attach to a normal JVM run: it watches every reflective call, every resource load, every proxy creation that actually happens, and writes out the matching `reflect-config.json` / `resource-config.json` / `proxy-config.json` files for you.

```bash
java -agentlib:native-image-agent=config-output-dir=src/main/resources/META-INF/native-image/com.example/myapp \
     -jar target/myapp.jar
```

Run your app (or, better, your full integration test suite) with the agent attached, exercise every code path you care about, then stop the app — the config files are now populated based on real, observed behavior. The catch: the agent only records what it *sees*, so untested code paths still won't be covered. Treat this as a starting point, not a substitute for understanding your reflection usage.

A more realistic setup runs the agent during your Maven/Gradle integration-test phase, so the config is generated from real Spring context boot plus real HTTP calls rather than a manual poke-around:

```bash
# Run the full integration test suite with the agent attached, merging
# into any existing config rather than overwriting it
mvn -DargLine="-agentlib:native-image-agent=config-merge-dir=src/main/resources/META-INF/native-image/com.example/myapp" \
    verify
```

**Testing hints** — Spring provides `RuntimeHintsPredicates` so you can assert, in a plain JVM unit test, that your registrar actually covers what you think it does, *before* you spend ten minutes on a native build to find out:

```java
import org.springframework.aot.hint.RuntimeHints;
import org.springframework.aot.hint.predicate.RuntimeHintsPredicates;
import static org.assertj.core.api.Assertions.assertThat;

class ReportingRuntimeHintsTests {

    @Test
    void registersReflectionForCustomerDto() {
        RuntimeHints hints = new RuntimeHints();
        new ReportingRuntimeHints().registerHints(hints, getClass().getClassLoader());

        assertThat(RuntimeHintsPredicates.reflection()
                .onType(CustomerDto.class))
                .accepts(hints);
    }

    @Test
    void registersResourcePatternForTemplates() {
        RuntimeHints hints = new RuntimeHints();
        new ReportingRuntimeHints().registerHints(hints, getClass().getClassLoader());

        assertThat(RuntimeHintsPredicates.resource()
                .forResource("reports/invoice.ftl"))
                .accepts(hints);
    }
}
```

### Debugging a native image failure: a walkthrough

Here's the failure pattern almost everyone hits the first time they go native, and how to work through it.

**1. The binary starts, then blows up on a specific request.** You see something like:

```text
Exception in thread "main" java.lang.ClassNotFoundException: com.example.dto.InvoiceDto
    at ... (SubstrateVM stack trace, much shorter than a normal JVM one)
```

or, for a method that exists but wasn't registered for reflective invocation:

```text
java.lang.NoSuchMethodException: com.example.dto.InvoiceDto.setAmount(java.math.BigDecimal)
```

These are the two classic symptoms of the closed-world assumption biting you: the class or member genuinely exists in your source, but `native-image` stripped it out (or never made it reflectively callable) because nothing it could statically see proved it was needed.

**2. Reproduce the failing path with the tracing agent, on the JVM.** Rather than guessing which hint is missing, run the same request against a plain-JVM build with the agent attached:

```bash
java -agentlib:native-image-agent=config-output-dir=/tmp/agent-out -jar target/myapp.jar
# In another terminal, hit the endpoint that failed in native
curl -X POST http://localhost:8080/invoices -d '{"amount": 10.50}'
```

Stop the app and inspect `/tmp/agent-out/reflect-config.json` — the entry for `InvoiceDto` (or whatever class was missing) is now there, generated from real, observed behavior instead of a guess.

**3. Decide whether to hand-copy the entry or fix it at the source.** For your own code, prefer `@RegisterReflectionForBinding` or a `RuntimeHintsRegistrar` — it's type-checked and shows up in code review. For third-party jars, copying the relevant block from the agent's `reflect-config.json` into your own `META-INF/native-image/.../reflect-config.json` is the normal path.

**4. Lock the fix in with a `RuntimeHintsPredicates` test**, so a future refactor that removes the registration is caught by `mvn test` on a plain JVM, long before anyone needs to run a five-minute native build to notice.

**5. Rebuild native and re-run the exact request that failed.** If it now succeeds, also re-run your broader integration suite against the native binary (Gradle's `testSupport = true` from the `graalvmNative` block runs your tests against the native binary directly) — fixing one missing hint often reveals a second one hiding right behind it.

### GraalVM tracing-agent and `native-image` flags worth knowing

| Flag | Used with | What it does |
|---|---|---|
| `-agentlib:native-image-agent=config-output-dir=<dir>` | `java` | Records reflection/resource/proxy/JNI usage seen during this run, writes fresh config files |
| `-agentlib:native-image-agent=config-merge-dir=<dir>` | `java` | Merges newly observed usage into existing config files instead of overwriting them |
| `-H:ConfigurationFileDirectories=<dir>` | `native-image` | Points the build at a directory of hand-written or agent-generated hint JSON files |
| `--initialize-at-build-time[=<classes>]` | `native-image` | Runs class static initializers at build time instead of at startup |
| `--initialize-at-run-time=<classes>` | `native-image` | Forces given classes to initialize at runtime (opt back out of build-time init) |
| `--gc=G1` / `--gc=serial` / `--gc=epsilon` | `native-image` | Selects which garbage collector gets compiled into the binary |
| `-Os` | `native-image` | Optimizes for smaller image size over raw speed |
| `-O2` / `-O3` | `native-image` | Optimizes for speed (roughly `-O2` is the default) |
| `--pgo=<profile>.iprof` | `native-image` | Feeds a captured profile back in for profile-guided optimization |
| `-H:+StaticExecutableWithDynamicLibC` | `native-image` | Mostly-static linking (static except `libc`) |
| `--static` | `native-image` | Fully static binary (needs a static `libc`, e.g. musl) |
| `-H:+ReportExceptionStackTraces` | `native-image` | Prints fuller stack traces for build-time analysis errors |
| `--verbose` | `native-image` | Prints detailed build progress — useful when a build hangs or fails without a clear reason |

## Native Image Optimization

Once your app actually builds and runs as a native image, there's a second round of tuning available to close the gap with the JVM's peak throughput and shrink the image further.

- **PGO (Profile-Guided Optimization)** — run your app under a profiling build, capture an `.iprof` file describing which code paths are hot, then feed that back into the real `native-image` build so the compiler optimizes the paths that matter most. This is the single biggest lever for closing the "native is slower at peak throughput" gap, but it's been Oracle GraalVM Enterprise/subscription territory more than the free Community Edition historically, so check current licensing.
- **`-Os`** — optimize for image size instead of raw speed, useful for size-constrained environments like small containers or edge devices.
- **G1 vs Serial GC in native** — the native image defaults to a simple **Serial GC**, good for small, short-lived, low-memory workloads (typical serverless function). For bigger, longer-running services you can opt into **G1 GC** for better throughput on multi-core machines: `--gc=G1`.
- **Static / mostly-static linking** — bundle the C library dependencies into the binary itself instead of relying on the host's shared libraries. Fully static binaries are the most portable (great for `FROM scratch` containers) but not always available on every OS/libc combination; "mostly-static" links everything except `libc`, a good middle ground.
- **Distroless base images** — run the native binary inside a container image with no shell, no package manager, nothing except the OS libraries the binary truly needs. Smaller attack surface, smaller image.
- **Build-time initialization** — you can tell `native-image` to run certain class initializers *at build time* rather than at startup (`--initialize-at-build-time=com.example.MyClass`), baking their state into the binary. Great for startup speed; dangerous for anything that captures environment-specific state (like a random seed, a clock, or a config value) that should really be decided at runtime.
- **CDS / AppCDS as the cheaper alternative** — Class Data Sharing (and Application Class Data Sharing) let a *regular JVM* pre-parse and cache class metadata to disk, then memory-map it back in on the next startup. It's not as fast as native, but it needs zero code changes, no closed-world constraints, and no loss of full reflection/dynamic-proxy support — often "good enough" if your real goal is just faster JVM boot.
- **Project Leyden and CRaC — context, not action items.** Project Leyden is OpenJDK's long-term effort to bring AOT-style benefits (faster startup, smaller footprint) into the *standard* JDK, without GraalVM's closed-world trade-offs. CRaC (Coordinated Restore at Checkpoint) takes a different angle: snapshot a fully warmed-up, already-running JVM process to disk, then restore from that snapshot in milliseconds instead of starting cold. Both are worth mentioning in an interview as "where the ecosystem is heading" — neither replaces GraalVM native images today, but both aim to make some of AOT's benefits available without giving up the full JVM.

```bash
# Example: G1 GC, mostly-static linking, build-time init of a config class
native-image \
  --gc=G1 \
  -H:+StaticExecutableWithDynamicLibC \
  --initialize-at-build-time=com.example.config.StaticDefaults \
  -jar target/myapp.jar
```

A quick side-by-side of the "cheaper alternative" options, since interviewers like asking "would you actually need full native for this?":

| Approach | Code changes needed | Startup win | Keeps full reflection/dynamic proxies | Build complexity |
|---|---|---|---|---|
| Plain JVM | None | None (baseline) | Yes | None |
| CDS / AppCDS | None | Moderate | Yes | Low |
| `spring.aot.enabled=true` (JVM) | Little/none | Small-moderate | Yes | Low |
| GraalVM native image | Hints for reflection/resources/proxies | Large | No — must be declared ahead of time | High |

## Common Code Review / Interview Pitfalls

- **Dynamic `Class.forName` / reflection with no hints.** Compiles fine, passes JVM tests, then throws `ClassNotFoundException` only in the native binary because the closed-world analysis never saw it.
- **Runtime classpath scanning** (e.g., a hand-rolled `@ComponentScan`-like mechanism that walks jars at startup) — native images don't have a classpath to scan at runtime the way the JVM does; this pattern needs to move to build time or be replaced with explicit registration.
- **Dynamic proxies without proxy hints.** Any interface proxied via `Proxy.newProxyInstance` (or a library that does this under the hood) needs a `proxies()` hint; forgetting it causes a runtime failure the moment the proxy is created.
- **Missing resource files.** Templates, `.properties`, `messages.properties`, and other classpath resources are excluded from the image by default unless registered — the app builds, starts, and then can't find its own template files.
- **`@Value` on a field of a CGLIB-proxied class.** Field injection through a dynamically generated proxy subclass can behave unexpectedly under AOT since Spring's AOT engine prefers generating explicit bean-registration code over runtime proxy magic; prefer constructor injection, which AOT handles more predictably.
- **Conditional beans resolving differently at build time vs runtime.** Spring AOT evaluates most `@Conditional` logic *once*, at build time, and bakes the result into generated code. If your conditions depend on an environment variable, active profile, or system property that differs between build machine and production, you can end up with the wrong beans wired in — profiles effectively get "baked in" at build time in the AOT-processed path.
- **Libraries that aren't native-friendly.** Anything relying heavily on runtime bytecode generation, unregistered reflection, or JVM-specific tricks (some ORMs, some mocking libraries, certain agents) may not work under `native-image` without extra configuration or may not work at all.
- **Huge build times and CI memory blowing up.** `native-image` performs whole-program static analysis and can need several GB of RAM and many minutes per build; CI runners sized for normal JAR builds may OOM or time out.
- **Assuming native is automatically faster at peak throughput.** It usually isn't, especially under sustained load, unless you've applied PGO — the JIT's ability to specialize based on live profiling data is a real advantage the JIT keeps and native gives up.
- **No JVM fallback test suite.** Teams sometimes test only on native and lose the ability to quickly bisect "is this a Spring bug or a native-image quirk" — keep running the full test suite on the plain JVM too.
- **`Locale`, timezone, and charset defaults baked in at build time.** Native images can capture the *build machine's* default locale/timezone/charset unless you're careful, causing subtly wrong date formatting or encoding in production if the build environment differs from production.
- **Forgetting `spring-boot:process-aot` output review.** Not checking the generated `reflect-config.json`/`resource-config.json` before shipping means surprises are discovered in production instead of in code review.
- **Treating the tracing agent's output as complete.** It only records what actually executed during that run; untested branches (error paths, rarely-hit conditionals) silently have no hints.
- **Skipping `RuntimeHintsPredicates` tests.** Hints tend to rot silently as code evolves — a refactor removes the class a hint referenced, or renames a method, and nobody notices until the next native build fails, minutes later, far from the change that caused it.
- **Not budgeting extra CI/build time for native in release pipelines.** Teams that bolt native builds onto existing pipelines without adjusting timeouts or runner sizing get flaky, hard-to-reproduce CI failures that look unrelated to native at all.

## Quick Recap

- **JIT vs AOT**: JIT compiles while running (slow start, great long-run throughput); AOT compiles before running (fast start, low memory, weaker peak throughput without extra tuning).
- **GraalVM** = special JDK + `native-image` tool + SubstrateVM runtime; works under a **closed-world assumption** — anything not visible at build time doesn't exist at runtime.
- **Spring AOT** runs at build time: evaluates conditions once, generates bean-registration Java source (no runtime reflection needed for the container itself), and emits `reflect-config.json` / `resource-config.json` / `proxy-config.json`. Works on the plain JVM too via `spring.aot.enabled=true`.
- **Building native**: `mvn -Pnative native:compile` / `gradle nativeCompile` (needs local GraalVM), or `mvn spring-boot:build-image` (needs only Docker, via Paketo Buildpacks), or a hand-written multi-stage Dockerfile.
- **Reflection hints**: `@RegisterReflectionForBinding`, `@Reflective`, or manual `reflect-config.json` for anything reflective the framework doesn't already know about.
- **Runtime hints**: implement `RuntimeHintsRegistrar`, wire it with `@ImportRuntimeHints`, generate a first draft with the **tracing agent** (`-agentlib:native-image-agent=...`), verify with `RuntimeHintsPredicates` in ordinary unit tests.
- **Debugging failures**: a `ClassNotFoundException`/`NoSuchMethodException` in the native binary almost always means a missing hint — reproduce on the JVM with the tracing agent, copy or codify the missing entry, lock it in with a `RuntimeHintsPredicates` test, rebuild.
- **Optimization knobs**: PGO for throughput, `-Os` for size, G1 vs Serial GC, static/mostly-static linking, distroless base images, careful use of build-time initialization.
- **Cheaper alternative**: CDS/AppCDS gets you faster JVM startup with zero code changes and none of native's constraints — try this first if native feels like overkill.
- **On the horizon**: Project Leyden (bringing AOT benefits into the standard JDK) and CRaC (checkpoint/restore a warmed-up JVM) — good context to mention, not yet drop-in replacements.
- **When NOT to go native** — reach for the plain JVM (optionally with CDS or `spring.aot.enabled=true`) instead when:
  - The service is long-running and throughput-critical, where the JIT's runtime-profile-driven optimization matters more than startup time.
  - The app leans heavily on reflection-heavy or non-native-friendly libraries you can't easily fix or replace.
  - The team doesn't have CI budget for multi-minute, multi-GB-RAM native builds.
  - The project is early-stage and the extra build complexity and debugging overhead isn't worth it yet.
  - Fast local iteration during development matters more than fast cold start in production.
  - You mainly need faster startup, not a standalone binary — CDS/AppCDS or `spring.aot.enabled=true` may get you most of the win with none of the constraints.
