# 21. Build & Documentation

A reviewer rarely gets to pick the build tool on an existing project, but they are expected to read a `pom.xml` or `build.gradle.kts` fluently, spot a dependency-scope mistake, and know when a Javadoc comment is padding rather than documentation. This chapter covers Maven and Gradle in depth — enough to read, modify, and debug real build files — and Javadoc, the API-contract documentation every reviewer is expected to enforce. We target Java 21+, Maven 3.9+, and Gradle 8.x throughout.

## Table of Contents

- [Maven](#maven)
- [Gradle](#gradle)
- [Javadoc](#javadoc)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Maven

Maven is a **declarative, convention-over-configuration** build tool. You describe *what* your project is (its coordinates, dependencies, packaging) in an XML file called `pom.xml` (Project Object Model), and Maven figures out *how* to build it by running a fixed, well-known **lifecycle**. Compare this to Gradle, which is closer to a scripting/task-graph model — see the [Maven vs Gradle](#maven-vs-gradle-comparison) table later in this chapter.

### Convention Over Configuration and the Standard Directory Layout

Maven assumes a standard project layout so that a plain `pom.xml` with no extra configuration already knows where to find source, tests, and resources:

```
my-app/
├── pom.xml
├── src/
│   ├── main/
│   │   ├── java/            # application source (.java)
│   │   └── resources/       # files copied onto the classpath as-is
│   └── test/
│       ├── java/            # test source
│       └── resources/       # test-only resources
└── target/                  # generated output — never commit this
    ├── classes/
    ├── test-classes/
    └── my-app-1.0.0.jar
```

Because this layout is a convention, plugins do not need to be told where anything is. Deviating from it (e.g., putting sources in `source/`) means every plugin that touches source directories must be reconfigured — almost never worth it.

### A Full Annotated `pom.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                              http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <!-- Coordinates: uniquely identify this artifact in any repository -->
  <groupId>com.example.orders</groupId>
  <artifactId>orders-service</artifactId>
  <version>1.4.0-SNAPSHOT</version>
  <packaging>jar</packaging>       <!-- jar (default), war, pom, ear ... -->

  <name>Orders Service</name>
  <description>REST API for managing customer orders.</description>

  <!-- Properties: reusable values, also read by many plugins -->
  <properties>
    <maven.compiler.release>21</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <junit.version>5.10.2</junit.version>
  </properties>

  <!-- dependencyManagement declares *versions/scopes* without adding the
       dependency to the build. Children/modules inherit the version but
       must still declare the dependency themselves. This is how BOMs work. -->
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-dependencies</artifactId>
        <version>3.3.0</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>

  <!-- Actual dependencies used by this module -->
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
      <!-- no <version> needed: it comes from the imported BOM above -->
    </dependency>

    <dependency>
      <groupId>org.projectlombok</groupId>
      <artifactId>lombok</artifactId>
      <version>1.18.32</version>
      <scope>provided</scope>
    </dependency>

    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>${junit.version}</version>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <finalName>${project.artifactId}</finalName>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-compiler-plugin</artifactId>
        <version>3.13.0</version>
        <configuration>
          <release>${maven.compiler.release}</release>
        </configuration>
      </plugin>

      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
      </plugin>
    </plugins>
  </build>

  <!-- Profiles: conditionally activated configuration -->
  <profiles>
    <profile>
      <id>ci</id>
      <activation>
        <property><name>env.CI</name></property>
      </activation>
      <properties>
        <maven.test.failure.ignore>false</maven.test.failure.ignore>
      </properties>
    </profile>
  </profiles>
</project>
```

### Coordinates: groupId / artifactId / version, SNAPSHOT vs Release

Every artifact in a Maven repository is uniquely addressed by three (sometimes four) coordinates:

| Coordinate | Meaning | Example |
|---|---|---|
| `groupId` | Organization/namespace, usually a reversed domain | `com.example.orders` |
| `artifactId` | The specific module/artifact name | `orders-service` |
| `version` | The version of that artifact | `1.4.0`, `1.4.0-SNAPSHOT` |
| `packaging` | Artifact type (optional, defaults to `jar`) | `jar`, `war`, `pom` |
| `classifier` | Distinguishes artifacts with the same GAV | `sources`, `javadoc` |

**SNAPSHOT vs release**: a version ending in `-SNAPSHOT` (e.g., `1.4.0-SNAPSHOT`) is a mutable, in-progress version — Maven will re-check the remote repository for a newer snapshot on every build (or per the repository's update policy) because the artifact behind that version string can change. A version without `-SNAPSHOT` (e.g., `1.4.0`) is a **release**: immutable, published once, never overwritten. Deploying the same release version twice to a repository that enforces immutability will fail — that's intentional, it protects reproducibility. Application `pom.xml` files should never depend on a `-SNAPSHOT` artifact when released to production; it means the build is not reproducible.

### The Build Lifecycle: Phases, Goals, and Plugins

Maven has three built-in **lifecycles**: `default` (build and deploy the artifact), `clean` (remove `target/`), and `site` (generate documentation site). The `default` lifecycle is the one developers use constantly, and it's a fixed, ordered sequence of **phases**:

```
validate → initialize → generate-sources → process-sources → generate-resources
→ process-resources → compile → process-classes → generate-test-sources
→ process-test-sources → generate-test-resources → process-test-resources
→ test-compile → test → prepare-package → package → pre-integration-test
→ integration-test → post-integration-test → verify → install → deploy
```

The phases most people actually type at the command line:

| Phase | What it does |
|---|---|
| `validate` | Checks the project is correct and all information is available |
| `compile` | Compiles main source code |
| `test` | Runs unit tests (via Surefire) — does not package anything |
| `package` | Packages compiled code into a jar/war |
| `verify` | Runs checks on the package, including integration tests (via Failsafe) |
| `install` | Installs the package into the **local** repository (`~/.m2/repository`), for use by other local projects |
| `deploy` | Copies the package to a **remote** repository, for sharing with other developers/CI |

Key mental model: **running a phase runs every phase before it too**. `mvn install` runs validate → ... → package → verify → install, in order.

A **goal** is the actual unit of work — a specific piece of functionality provided by a **plugin**, written as `plugin:goal` (e.g., `compiler:compile`, `surefire:test`, `dependency:tree`). Phases don't *do* anything by themselves; they are just named hooks that plugin goals are **bound** to.

```
Phase        Bound goal (default binding)          Plugin
---------    ------------------------------------   ---------------------------
compile   →  compiler:compile                    →  maven-compiler-plugin
test      →  surefire:test                       →  maven-surefire-plugin
package   →  jar:jar                             →  maven-jar-plugin
install   →  install:install                     →  maven-install-plugin
deploy    →  deploy:deploy                       →  maven-deploy-plugin
```

You can also invoke a goal directly without running a whole phase chain, useful for diagnostics:

```bash
mvn dependency:tree
mvn compiler:compile
mvn versions:display-dependency-updates
```

**Plugin vs goal vs phase, in one sentence each:**
- A **phase** is a named step in the lifecycle (`compile`, `test`, `package`).
- A **plugin** is a jar containing one or more executable goals (`maven-surefire-plugin`).
- A **goal** is the actual task a plugin performs, optionally bound to a phase (`surefire:test` is bound to the `test` phase).

### Dependency Scopes

| Scope | On compile classpath | On test classpath | On runtime classpath | Packaged in artifact | Typical use |
|---|---|---|---|---|---|
| `compile` (default) | Yes | Yes | Yes | Yes | Normal library dependency |
| `provided` | Yes | Yes | No | No | Servlet API, Lombok — supplied by the container/JDK at runtime |
| `runtime` | No | Yes | Yes | Yes | JDBC drivers — needed at runtime, not to compile against |
| `test` | No | Yes | No | No | JUnit, Mockito, AssertJ |
| `system` | Yes | Yes | No | No | Local jar via explicit `<systemPath>` — avoid, not portable |
| `import` | n/a | n/a | n/a | n/a | Only valid in `<dependencyManagement>`, `type=pom` — pulls in a BOM's managed versions |

```xml
<dependency>
  <groupId>org.postgresql</groupId>
  <artifactId>postgresql</artifactId>
  <version>42.7.3</version>
  <scope>runtime</scope>
</dependency>
```

A common review flag: marking a dependency `compile` when it's only needed at runtime (bloats the compile classpath and leaks transitively to consumers), or forgetting `provided` for a servlet container API that will already be on the classpath when deployed, causing a duplicate/incompatible class at deploy time.

### Transitive Dependencies, Conflict Resolution, and `dependency:tree`

Maven pulls in **transitive dependencies** automatically — if your dependency depends on something, you get that something too, without declaring it. This is convenient but is also the #1 source of "it works on my machine" classpath bugs.

When two versions of the same artifact are pulled in transitively at different depths, Maven uses **nearest-wins** (also called "nearest definition") resolution: the dependency declared closest to your project in the dependency graph wins, regardless of version number. If two dependencies are at the *same* depth, the first one declared in the POM wins.

```
your-app
├── lib-a:1.0 → depends on commons-lang3:3.9
└── lib-b:1.0 → depends on commons-lang3:3.14
```

Here commons-lang3 is at the same depth (both direct children of `lib-a`/`lib-b`, which are depth 1, so commons-lang3 is depth 2 either way) — the version resolved is whichever is declared first in your POM's dependency list. This is exactly why nearest-wins can silently pick an old, buggy, or incompatible version. Never trust it blindly — always inspect the tree:

```bash
mvn dependency:tree
mvn dependency:tree -Dincludes=commons-lang3
mvn dependency:tree -Dverbose   # shows the versions that lost, and why
```

To force a specific version regardless of what transitive resolution would pick, either declare the dependency directly (direct declarations always win over transitive ones) or use `dependencyManagement` (see below), or exclude the unwanted transitive dependency:

```xml
<dependency>
  <groupId>com.example</groupId>
  <artifactId>lib-a</artifactId>
  <version>1.0</version>
  <exclusions>
    <exclusion>
      <groupId>org.apache.commons</groupId>
      <artifactId>commons-lang3</artifactId>
    </exclusion>
  </exclusions>
</dependency>
```

### `dependencyManagement` and BOMs

`dependencyManagement` centralizes version (and scope/exclusion) declarations **without adding the dependency to the build**. A child module or a plugin still needs a bare `<dependency>` (no version) to actually pull the artifact in; the version comes from the management block. This is how you avoid version drift across a multi-module project.

A **BOM** (Bill of Materials) is a `pom`-packaged artifact whose sole purpose is a big `dependencyManagement` block of mutually-tested versions (Spring Boot's, Jackson's, and the AWS SDK's BOMs are common examples). Importing one gives you a consistent, tested set of versions for an entire ecosystem in one line:

```xml
<dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson</groupId>
      <artifactId>jackson-bom</artifactId>
      <version>2.17.1</version>
      <type>pom</type>
      <scope>import</scope>
    </dependency>
  </dependencies>
</dependencyManagement>

<dependencies>
  <!-- version omitted on purpose: comes from the imported BOM -->
  <dependency>
    <groupId>com.fasterxml.jackson.core</groupId>
    <artifactId>jackson-databind</artifactId>
  </dependency>
</dependencies>
```

### Parent POMs and Multi-Module Builds

A **parent POM** lets multiple modules share configuration (properties, `dependencyManagement`, plugin versions) via inheritance:

```xml
<!-- child module's pom.xml -->
<parent>
  <groupId>com.example</groupId>
  <artifactId>orders-parent</artifactId>
  <version>1.0.0</version>
  <relativePath>../pom.xml</relativePath>
</parent>
<artifactId>orders-service</artifactId>
```

A **multi-module** (aggregator) build is a parent `pom.xml` with `packaging=pom` and a `<modules>` list; building the parent builds every listed module in dependency order:

```xml
<groupId>com.example</groupId>
<artifactId>orders-parent</artifactId>
<version>1.0.0</version>
<packaging>pom</packaging>

<modules>
  <module>orders-api</module>
  <module>orders-service</module>
  <module>orders-persistence</module>
</modules>
```

```bash
mvn install                          # builds all modules, in dependency order
mvn -pl orders-service -am install   # build only orders-service + its dependents
```

A module can be both a *child* (inherits from a parent) and independently list its own dependencies — parent and aggregator are separate, orthogonal concepts, even though the same `pom.xml` often plays both roles.

### Profiles

**Profiles** activate additional configuration conditionally — by an explicit flag, an OS, a JDK version, a file's presence, or a system/environment property. Common uses: switching database connection properties for local vs CI, enabling static analysis only on CI, or activating a native-image build only on demand.

```xml
<profiles>
  <profile>
    <id>integration-tests</id>
    <build>
      <plugins>
        <plugin>
          <groupId>org.apache.maven.plugins</groupId>
          <artifactId>maven-failsafe-plugin</artifactId>
          <executions>
            <execution>
              <goals><goal>integration-test</goal><goal>verify</goal></goals>
            </execution>
          </executions>
        </plugin>
      </plugins>
    </build>
  </profile>
</profiles>
```

```bash
mvn verify -Pintegration-tests
```

### Properties

Properties are `${key}`-substitutable values, declared in `<properties>`, usable anywhere in the POM (versions, plugin config) and readable by many plugins directly (e.g., `maven.compiler.release`, `project.build.sourceEncoding`). Maven also exposes implicit properties like `${project.version}` and `${project.basedir}`.

```xml
<properties>
  <maven.compiler.release>21</maven.compiler.release>
  <spring.version>6.1.8</spring.version>
</properties>
```

### Common Plugins

| Plugin | Purpose |
|---|---|
| `maven-compiler-plugin` | Compiles Java; use `<release>21</release>` (not the deprecated `source`/`target` pair) so both the language level *and* the bootclasspath match Java 21 exactly |
| `maven-surefire-plugin` | Runs **unit** tests during the `test` phase |
| `maven-failsafe-plugin` | Runs **integration** tests during `integration-test`/`verify`; failures don't stop the build until `verify`, so post-integration-test cleanup still runs |
| `maven-shade-plugin` | Builds an uber/fat jar by unpacking dependency classes and merging them into one jar; supports relocating packages to avoid classpath collisions |
| `maven-assembly-plugin` | Also builds fat jars/distribution archives, but merges by concatenation, not by understanding class content — less safe for merging `META-INF/services` files than shade's transformers |
| `jacoco-maven-plugin` | Code coverage instrumentation and reporting |
| `maven-enforcer-plugin` | Fails the build if rules are violated — e.g., banned dependencies, minimum Maven/JDK version, no duplicate classes |
| `versions-maven-plugin` | Reports and can update outdated dependency/plugin versions (`versions:display-dependency-updates`) |

```xml
<plugin>
  <groupId>org.apache.maven.plugins</groupId>
  <artifactId>maven-compiler-plugin</artifactId>
  <configuration>
    <release>21</release>
    <parameters>true</parameters>   <!-- keep parameter names for reflection -->
  </configuration>
</plugin>

<plugin>
  <groupId>org.apache.maven.plugins</groupId>
  <artifactId>maven-shade-plugin</artifactId>
  <version>3.6.0</version>
  <executions>
    <execution>
      <phase>package</phase>
      <goals><goal>shade</goal></goals>
      <configuration>
        <transformers>
          <transformer implementation="org.apache.maven.plugins.shade.resource.ManifestResourceTransformer">
            <mainClass>com.example.orders.Main</mainClass>
          </transformer>
        </transformers>
      </configuration>
    </execution>
  </executions>
</plugin>

<plugin>
  <groupId>org.apache.maven.plugins</groupId>
  <artifactId>maven-enforcer-plugin</artifactId>
  <executions>
    <execution>
      <goals><goal>enforce</goal></goals>
      <configuration>
        <rules>
          <requireMavenVersion><version>[3.9,)</version></requireMavenVersion>
          <requireJavaVersion><version>[21,)</version></requireJavaVersion>
          <bannedDependencies>
            <excludes><exclude>log4j:log4j</exclude></excludes>
          </bannedDependencies>
        </rules>
      </configuration>
    </execution>
  </executions>
</plugin>
```

### The Local Repository and Offline Mode

Maven caches every artifact it downloads under `~/.m2/repository`, keyed by GAV coordinates. `mvn install` places your own module's artifact there too, so sibling projects can depend on it without a remote repository round-trip.

**Reproducible builds**: pin exact versions (avoid version ranges like `[1.0,2.0)`, which resolve differently over time), pin plugin versions explicitly (an unpinned plugin resolves to "latest" the first time it's used and then locks to whatever was cached — inconsistent across machines/CI), and prefer `-SNAPSHOT`-free dependencies for anything released. Maven also supports a `reproducible` build mode via `<project.build.outputTimestamp>` so two builds of the same source produce byte-identical jars (same file timestamps inside the archive).

```bash
mvn -o package        # offline: fail fast if anything is missing from ~/.m2, don't hit the network
mvn -o dependency:tree # useful to sanity-check nothing is silently redownloaded
```

### Skipping Tests: `-DskipTests` vs `-Dmaven.test.skip`

These are **not** the same thing, and mixing them up is a classic review flag:

| Flag | Compiles test sources? | Runs tests? |
|---|---|---|
| `-DskipTests` | Yes | No |
| `-Dmaven.test.skip=true` | No | No |

```bash
mvn install -DskipTests            # tests are compiled (catches compile errors) but not run
mvn install -Dmaven.test.skip=true # test sources aren't even compiled — fastest, but hides broken tests
```

Using `-Dmaven.test.skip=true` in CI can let a broken test file merge unnoticed, since it never even compiles until someone runs tests locally.

### The Maven Wrapper

The **Maven Wrapper** (`mvnw` / `mvnw.cmd`) pins the exact Maven version a project needs, generated once with `mvn wrapper:wrapper` and committed to the repository. Anyone cloning the repo runs `./mvnw` and gets a reproducible Maven version automatically downloaded — no "works with Maven 3.9 but the CI box has 3.6" surprises.

```bash
mvn wrapper:wrapper -Dmaven=3.9.6   # generate the wrapper, pin version 3.9.6
./mvnw clean verify                  # anyone can build without installing Maven at all
```

Commit `mvnw`, `mvnw.cmd`, and `.mvn/wrapper/maven-wrapper.properties` — never `.gitignore` them.

### Command Cheat-Sheet

```bash
mvn clean                         # delete target/
mvn compile                       # compile main sources
mvn test                          # run unit tests
mvn package                       # produce jar/war
mvn verify                        # run integration tests + checks
mvn install                       # install into ~/.m2
mvn deploy                        # publish to remote repository

mvn dependency:tree               # show resolved dependency graph
mvn dependency:analyze            # find unused/undeclared direct dependencies
mvn versions:display-dependency-updates   # find outdated dependency versions
mvn help:effective-pom            # print the fully resolved/merged POM

mvn -pl module-a -am install       # build module-a plus everything it depends on
mvn -pl module-a -amd install       # build module-a plus everything that depends on it
mvn -T 4 install                   # build with 4 threads (parallel modules)
mvn -X verify                      # debug/verbose logging
mvn -q verify                      # quiet output, errors only
```

## Gradle

Gradle is a general-purpose build tool built around a **task graph** rather than a fixed lifecycle: you (or a plugin) define **tasks** and their dependencies, and Gradle figures out which tasks need to run and in what order, skipping anything whose inputs haven't changed. Build scripts are code (Groovy or Kotlin), not declarative XML, which makes Gradle more flexible but also easier to write build logic that's hard to reason about — a common review concern.

### Groovy DSL vs Kotlin DSL

Both DSLs configure the same underlying model. Kotlin DSL (`build.gradle.kts`) gets full IDE type-checking and autocompletion; Groovy DSL (`build.gradle`) is more concise and was the historical default. Since Gradle 8.x, Kotlin DSL is the recommended default for new projects.

```groovy
// build.gradle (Groovy DSL)
plugins {
    id 'java'
    id 'application'
}

repositories {
    mavenCentral()
}

dependencies {
    implementation 'com.google.guava:guava:33.2.1-jre'
    testImplementation platform('org.junit:junit-bom:5.10.2')
    testImplementation 'org.junit.jupiter:junit-jupiter'
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

application {
    mainClass = 'com.example.App'
}

test {
    useJUnitPlatform()
}
```

```kotlin
// build.gradle.kts (Kotlin DSL) — same build, statically typed
plugins {
    java
    application
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("com.google.guava:guava:33.2.1-jre")
    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(21))
    }
}

application {
    mainClass.set("com.example.App")
}

tasks.test {
    useJUnitPlatform()
}
```

### An Annotated `build.gradle.kts`

```kotlin
plugins {
    id("java-library")               // produces api()/implementation() distinction
    id("jacoco")                     // coverage
    id("com.github.spotbugs") version "6.0.18"
}

group = "com.example.orders"
version = "1.4.0-SNAPSHOT"

repositories {
    mavenCentral()
}

dependencies {
    // api: exposed to consumers of THIS library on their compile classpath
    api("com.fasterxml.jackson.core:jackson-databind:2.17.1")

    // implementation: internal detail, NOT leaked to consumers
    implementation("org.apache.commons:commons-lang3:3.14.0")

    // compileOnly: needed to compile, but must be supplied at runtime by the caller
    compileOnly("org.projectlombok:lombok:1.18.32")
    annotationProcessor("org.projectlombok:lombok:1.18.32")

    // runtimeOnly: needed at runtime, not visible at compile time
    runtimeOnly("org.postgresql:postgresql:42.7.3")

    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testImplementation("org.assertj:assertj-core:3.26.0")
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(21))
    }
    withSourcesJar()
    withJavadocJar()
}

tasks.test {
    useJUnitPlatform()
    maxParallelForks = Runtime.getRuntime().availableProcessors() / 2
}

tasks.jacocoTestReport {
    dependsOn(tasks.test)
}
```

### The `plugins {}` Block

The `plugins {}` block applies **binary plugins** by ID, resolved from the Gradle Plugin Portal (or `mavenCentral()` for some). It replaces the older, discouraged `apply plugin: 'java'` / buildscript-classpath style, because it lets Gradle resolve and version-check plugins before any script code runs, enabling better IDE support and the plugin version-management shown below.

```kotlin
plugins {
    java
    id("org.springframework.boot") version "3.3.0"
    id("io.spring.dependency-management") version "1.1.5"
}
```

### The Task Graph and Incremental Builds

Every unit of work in Gradle is a **task** (`compileJava`, `test`, `jar`, ...). Tasks declare **inputs** and **outputs**; Gradle hashes them, and if neither changed since the last run, the task is reported `UP-TO-DATE` and skipped entirely — this is **incremental build**. Running `./gradlew build` twice in a row with no source changes does almost nothing the second time.

```bash
$ ./gradlew build
> Task :compileJava
> Task :processResources UP-TO-DATE
> Task :classes
> Task :test
> Task :jar
> Task :build

BUILD SUCCESSFUL
```

You can inspect why a task ran (or didn't) and visualize the dependency graph:

```bash
./gradlew build --dry-run          # print the task plan without executing anything
./gradlew test --rerun             # force a task to rerun even if UP-TO-DATE
```

### Configuration Cache and Build Cache

These are two distinct, complementary optimizations, and mixing them up is a common misunderstanding:

- **Configuration cache**: caches the *result of evaluating the build scripts themselves* (the task graph), so subsequent invocations skip re-running the configuration phase. Speeds up every single build, even a clean one, because it avoids re-parsing/re-executing Groovy/Kotlin build logic. Enable with `org.gradle.configuration-cache=true` in `gradle.properties`.
- **Build cache**: caches *task outputs* keyed by task input hash, and can restore an already-computed output on a *different* machine (with a shared remote cache) or a different branch, skipping execution entirely — not just "up to date" within one workspace, but genuinely retrieved from cache. Enable with `org.gradle.caching=true`.

```properties
# gradle.properties
org.gradle.configuration-cache=true
org.gradle.caching=true
org.gradle.parallel=true
```

```bash
./gradlew build --build-cache      # explicitly opt in to the build cache for this invocation
```

### Dependency Configurations

| Configuration | Compile classpath (consumer sees it?) | Runtime classpath | Typical use |
|---|---|---|---|
| `api` | Yes — leaks to consumers | Yes | Types that appear in your public method signatures |
| `implementation` | No — internal only | Yes | Everything else; the default, safest choice |
| `compileOnly` | Yes (this module only) | No | Annotation-only or container-supplied deps (Lombok, servlet API) |
| `runtimeOnly` | No | Yes | JDBC drivers, logging backends |
| `testImplementation` | Test compile only | Test runtime | JUnit, Mockito |
| `annotationProcessor` | n/a (processor classpath) | No | Lombok, MapStruct, Dagger processors |

**Why `implementation` beats the old `compile`**: the deprecated `compile` configuration put every dependency on the *consumer's* compile classpath transitively, whether or not the consumer's code actually referenced it. That meant bumping an internal-only dependency's version in a library forced every downstream module to recompile, even though nothing in the public API changed. `implementation` hides internal dependencies from consumers entirely, which (a) shrinks consumers' compile classpaths, (b) means changing an internal dependency no longer forces downstream recompilation, and (c) makes accidental leakage of internal types through public APIs a compile error for consumers, rather than something that silently works until the dependency changes.

```kotlin
dependencies {
    // Consumer of this module can call methods that return ObjectMapper directly:
    api("com.fasterxml.jackson.databind:jackson-databind:2.17.1")

    // Used internally only, e.g., for a private helper class — invisible to consumers:
    implementation("org.apache.commons:commons-lang3:3.14.0")
}
```

### Version Catalogs (`libs.versions.toml`)

A **version catalog** centralizes dependency coordinates and versions in one TOML file, shared across all subprojects, with type-safe accessors generated for both DSLs:

```toml
# gradle/libs.versions.toml
[versions]
junit = "5.10.2"
jackson = "2.17.1"

[libraries]
junit-jupiter = { module = "org.junit.jupiter:junit-jupiter", version.ref = "junit" }
jackson-databind = { module = "com.fasterxml.jackson.core:jackson-databind", version.ref = "jackson" }

[bundles]
testing = ["junit-jupiter"]

[plugins]
spring-boot = { id = "org.springframework.boot", version = "3.3.0" }
```

```kotlin
// build.gradle.kts — type-safe accessors generated from the catalog above
plugins {
    alias(libs.plugins.spring.boot)
}

dependencies {
    implementation(libs.jackson.databind)
    testImplementation(libs.bundles.testing)
}
```

This is the Gradle equivalent of a Maven BOM plus centralized `<properties>`: one file, one place to bump a version, autocomplete in the IDE, and no version drift between subprojects.

### Multi-Project Builds and `settings.gradle.kts`

`settings.gradle.kts` (at the repo root) declares which subprojects participate in the build — this is what makes a directory a "project" at all, before any `build.gradle.kts` is even read.

```kotlin
// settings.gradle.kts
rootProject.name = "orders-platform"

include("orders-api", "orders-service", "orders-persistence")

dependencyResolutionManagement {
    repositories {
        mavenCentral()
    }
}
```

A subproject depends on another subproject with the `project()` accessor, not a version string:

```kotlin
// orders-service/build.gradle.kts
dependencies {
    implementation(project(":orders-api"))
}
```

```bash
./gradlew :orders-service:build     # build a single subproject
./gradlew build                     # build everything from the root
```

### Custom Tasks

```kotlin
tasks.register("printVersion") {
    doLast {
        println("Building version ${project.version}")
    }
}

tasks.register<Copy>("copyConfig") {
    from("src/main/resources/config")
    into(layout.buildDirectory.dir("config"))
}

tasks.named("build") {
    dependsOn("printVersion")
}
```

Custom tasks with typed inputs/outputs (rather than `doLast {}` closures) can participate correctly in incremental build and the build cache — a plain `doLast` task has no declared inputs/outputs and Gradle can never mark it `UP-TO-DATE`.

### The Gradle Wrapper

Exactly like Maven's wrapper, `gradlew`/`gradlew.bat` pin the exact Gradle version a project was built and tested with, downloaded on demand.

```bash
gradle wrapper --gradle-version 8.8   # (re)generate the wrapper for version 8.8
./gradlew build                        # anyone can build without installing Gradle
```

**Always commit the wrapper** (`gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.jar`, `gradle/wrapper/gradle-wrapper.properties`). Without it, a teammate or CI runner using a different globally-installed Gradle version can produce a different, non-reproducible build — or fail outright on a script feature that only exists in a newer/older Gradle.

### Diagnostics: `--scan`, `--dry-run`, `dependencies`

```bash
./gradlew build --scan          # publish a shareable, detailed build report (timings, deprecations, task graph)
./gradlew build --dry-run       # show which tasks WOULD run, without running them
./gradlew dependencies          # print the resolved dependency tree for all configurations
./gradlew dependencies --configuration runtimeClasspath   # scope it to one configuration
./gradlew :orders-service:dependencyInsight --dependency commons-lang3   # why is this version chosen?
```

### Maven vs Gradle Comparison

| Aspect | Maven | Gradle |
|---|---|---|
| Config style | Declarative XML | Code (Groovy or Kotlin DSL) |
| Build model | Fixed lifecycle of phases | Flexible task graph |
| Incremental builds | Limited (plugin-dependent) | First-class, built into every task |
| Build/config caching | No built-in equivalent | Configuration cache + (remote) build cache |
| Multi-module | Parent POM + `<modules>` | `settings.gradle.kts` + subprojects |
| Dependency management | `dependencyManagement` + BOM import | Version catalogs (`libs.versions.toml`) |
| Learning curve | Lower — one way to do things | Higher — scripting flexibility cuts both ways |
| Ecosystem | Extremely mature, huge plugin base, Java/Enterprise-centric | Dominant for Android; strong in polyglot/large monorepos |
| Performance on large builds | Slower on very large multi-module builds | Generally faster due to caching/incrementality |
| Debuggability | Very predictable, easy to reason about from the POM alone | Custom Groovy/Kotlin logic can be hard to review/debug |

**When to pick which**: choose Maven for straightforward, mostly-declarative enterprise Java projects where predictability and a large, stable plugin ecosystem matter more than raw build speed, or when the team values "one obvious way to configure things." Choose Gradle for large monorepos, Android (mandatory), polyglot builds, or when build performance (incremental/cached builds) matters enough to justify the extra scripting flexibility and the review overhead that comes with it.

## Javadoc

Javadoc is the documentation format baked into the Java language itself: specially-formatted comments (`/** ... */`) immediately before a declaration, parsed by the `javadoc` tool (and IDEs) into structured API documentation. In code review, Javadoc quality is judged on one criterion above all others: **does it describe the contract, not the implementation?**

### The Comment Format

A Javadoc comment starts with `/**` (two asterisks), ends with `*/`, and sits directly above the declaration it documents — a class, interface, method, constructor, or field. Every other `*`-prefixed line is conventional but not required by the parser.

```java
/**
 * Converts a monetary amount between currencies using the exchange rate
 * in effect at the time this method is called.
 *
 * @param amount the amount to convert; must be non-negative
 * @param from the source currency
 * @param to the target currency
 * @return the converted amount, rounded to the target currency's
 *         standard number of decimal places
 * @throws IllegalArgumentException if {@code amount} is negative
 * @throws CurrencyUnavailableException if no exchange rate is available
 *         for the given currency pair
 */
public BigDecimal convert(BigDecimal amount, Currency from, Currency to) {
    ...
}
```

### Standard Tags

| Tag | Where used | Purpose |
|---|---|---|
| `@param` | Methods, constructors | Describes a parameter — name plus meaning/constraints |
| `@return` | Methods (non-`void`) | Describes the return value and its meaning |
| `@throws` (or `@exception`) | Methods, constructors | Describes when/why an exception is thrown |
| `@see` | Anywhere | Cross-reference to a related class/method/URL |
| `@since` | Classes, methods | Version this API first appeared in |
| `@deprecated` | Classes, methods, fields | Marks the API as obsolete; should always explain the replacement |
| `@author` | Classes | Author name(s) — increasingly omitted in favor of VCS history |
| `@serial` | Fields (of `Serializable` classes) | Documents the serialized form of a field for the serialization contract |

```java
/**
 * Legacy price calculator.
 *
 * @deprecated Use {@link PricingEngine#calculate(Order)} instead; this
 *             class does not account for regional tax rules and will be
 *             removed in 3.0.
 */
@Deprecated(since = "2.4", forRemoval = true)
public class LegacyPriceCalculator {
    ...
}
```

Note the pairing convention: `@deprecated` in the Javadoc comment (human-readable explanation) should always accompany the `@Deprecated` annotation (machine-readable, drives compiler warnings) — one without the other is an incomplete deprecation.

### Inline Tags

Inline tags are embedded inside the flowing text of a description, wrapped in `{@ ... }`.

| Inline tag | Purpose |
|---|---|
| `{@link Type#member}` | Clickable cross-reference, rendered as a link |
| `{@linkplain Type#member}` | Same as `{@link}` but rendered as plain text, not code font |
| `{@code text}` | Renders as code font, and — critically — HTML-escapes its content, so `<`, `>`, `&` are safe |
| `{@literal text}` | Escapes HTML like `{@code}` but does *not* apply code font |
| `{@value #FIELD}` | Inlines the value of a static final constant |
| `{@inheritDoc}` | Pulls the Javadoc text from the overridden/implemented method |
| `{@snippet ...}` | (JDK 18+) Embeds a validated, syntax-highlighted code example |

```java
/**
 * The default timeout, in milliseconds: {@value #DEFAULT_TIMEOUT_MS}.
 *
 * <p>Use {@code Duration.ofMillis(timeout)} to convert to a {@link java.time.Duration}
 * if you need a {@link java.time.Duration}-based API instead.
 *
 * <p>Generic type bound example (needs {@literal @literal}, not {@code @code},
 * because {@code List<String>} would be parsed as an HTML tag otherwise):
 * a raw {@literal List<String>} is unsafe.
 */
public static final int DEFAULT_TIMEOUT_MS = 5000;
```

```java
/**
 * {@inheritDoc}
 *
 * <p>This implementation additionally logs every call at DEBUG level.
 */
@Override
public void process(Order order) {
    ...
}
```

The `{@snippet}` tag (JEP 413, finalized JDK 18) replaces the old `<pre>{@code ...}</pre>` idiom with something the `javadoc` tool can actually validate and highlight:

```java
/**
 * Builds an immutable list.
 *
 * {@snippet lang = "java" :
 * List<String> names = List.of("Ada", "Grace", "Linus");
 * names.forEach(System.out::println);
 * }
 */
public static <T> List<T> immutableListOf(T... items) { ... }
```

### The First-Sentence Summary Rule

The **first sentence** of a Javadoc comment (up to the first period followed by whitespace or a line break, in the classic algorithm — recent `javadoc` also respects an explicit `{@summary ...}` tag) becomes the **summary**: what shows up in package/class overview tables, method-summary tables, and search results. This means:

- Front-load the single most important fact about the method in that first sentence — assume it's the *only* sentence some readers will ever see.
- Avoid a stray period inside the first sentence (e.g., in "e.g." or a versioned class name) — it truncates the summary at the wrong place.

```java
/**
 * Returns the customer's preferred shipping address, or the billing
 * address if none is set. Falls back further to {@code null} if the
 * customer record itself has no addresses at all.
 */
public Address preferredShippingAddress() { ... }
```

```java
// Bad: the period after "e.g" truncates the rendered summary to
// "Formats a duration, e." — nonsensical in the summary table.
/**
 * Formats a duration, e.g. "3h 12m", using the default locale.
 */
```

### Documenting the Contract, Not the Implementation

The most important review distinction in this chapter: Javadoc documents the **contract** — what callers can rely on — not *how* the current implementation happens to work. Implementation details belong in inline `//` comments inside the method body, if anywhere, because they can change without breaking any caller; contract statements cannot.

```java
// Bad: describes the current implementation, which callers should never depend on
/**
 * Looks up the user by scanning a HashMap keyed by user ID.
 */
public User findById(long id) { ... }

// Good: describes the contract — inputs, outputs, and failure behavior
/**
 * Returns the user with the given ID.
 *
 * @param id the user ID; must be positive
 * @return the matching user
 * @throws NoSuchElementException if no user with this ID exists
 */
public User findById(long id) { ... }
```

If the class later switches from a `HashMap` to a database call, the first comment becomes a lie that nobody remembers to fix; the second comment remains true regardless of the storage mechanism.

### Documenting Nullability, Thread Safety, and Side Effects

These three properties are exactly the ones that cannot be inferred from a method signature alone in Java (pre-nullability-annotations, and even with them, the *reasoning* still benefits from prose), and they are exactly what reviewers look for missing:

```java
/**
 * Finds a cached session for the given token.
 *
 * <p><b>Nullability:</b> returns {@code null} if no session is cached
 * for the token; never throws for an unknown token.
 *
 * <p><b>Thread safety:</b> this method is safe to call concurrently
 * from multiple threads without external synchronization.
 *
 * <p><b>Side effects:</b> refreshes the token's last-access timestamp
 * as a side effect of a successful lookup, which resets its expiry.
 *
 * @param token the session token
 * @return the cached {@link Session}, or {@code null} if not found
 */
public Session findSession(String token) { ... }
```

Where the project uses JSR-305 / `org.jspecify` annotations (`@Nullable`, `@NonNull`), the annotation is machine-checkable and should be preferred over prose alone — but prose explaining *why* something can be null (e.g., "null before the account is activated") still adds value an annotation cannot.

### `package-info.java` and `module-info.java` Docs

Package-level and module-level Javadoc live in dedicated files, not attached to any class, because a package/module is not itself a class.

```java
// package-info.java
/**
 * Order pricing and discount calculation.
 *
 * <p>Classes in this package are responsible for computing the final
 * price of an {@link com.example.orders.model.Order}, including tax and
 * promotional discounts. Nothing in this package performs persistence
 * or network I/O.
 *
 * @since 1.0
 */
package com.example.orders.pricing;
```

```java
// module-info.java
/**
 * Defines the order-processing service and its public API.
 *
 * <p>Only {@code com.example.orders.api} is exported; all other packages
 * are internal implementation details and not accessible to other modules.
 */
module com.example.orders {
    requires java.sql;
    exports com.example.orders.api;
}
```

### HTML in Javadoc and Markdown Javadoc (JEP 467, JDK 23+)

Classic Javadoc comments are parsed as HTML fragments, so structuring text requires literal HTML tags — `<p>` for paragraphs, `<ul>`/`<li>` for lists, `<b>` for bold. This is verbose and easy to get wrong (unclosed tags silently break rendering, or worse, get swallowed by `-Xdoclint`, discussed next).

```java
/**
 * Represents an order line item.
 *
 * <p>Each line item has:
 * <ul>
 *   <li>a {@code productId}</li>
 *   <li>a {@code quantity}, always positive</li>
 *   <li>a {@code unitPrice}</li>
 * </ul>
 */
public class LineItem { ... }
```

**JEP 467** (finalized in JDK 23) allows Javadoc comments to be written in **Markdown** instead, using `///` line comments rather than `/** */` blocks:

```java
/// Represents an order line item.
///
/// Each line item has:
/// - a `productId`
/// - a `quantity`, always positive
/// - a `unitPrice`
///
/// See [LineItem#total()] for how the extended price is computed.
public class LineItem {
    /// Returns the extended price: `unitPrice * quantity`.
    public BigDecimal total() { ... }
}
```

Markdown Javadoc is substantially easier to read in raw source form (in an IDE or a diff) and eliminates most of the HTML-escaping foot-guns, at the cost of requiring JDK 23+ tooling to render.

### Generating Javadoc

```bash
# Directly with the javadoc tool
javadoc -d docs -sourcepath src/main/java -subpackages com.example.orders

# Maven
mvn javadoc:javadoc          # generates target/site/apidocs
mvn javadoc:jar               # packages it as a javadoc.jar (needed to publish to Maven Central)

# Gradle
./gradlew javadoc             # generates build/docs/javadoc
```

```xml
<!-- pom.xml: enforce doc quality and produce a javadoc jar for publishing -->
<plugin>
  <groupId>org.apache.maven.plugins</groupId>
  <artifactId>maven-javadoc-plugin</artifactId>
  <version>3.8.0</version>
  <configuration>
    <doclint>all,-missing</doclint>  <!-- validate HTML/tags; don't require every element documented -->
    <failOnWarning>true</failOnWarning>
  </configuration>
  <executions>
    <execution>
      <id>attach-javadocs</id>
      <goals><goal>jar</goal></goals>
    </execution>
  </executions>
</plugin>
```

```kotlin
// build.gradle.kts
tasks.javadoc {
    (options as StandardJavadocDocletOptions).apply {
        addStringOption("Xdoclint:all,-missing", "-quiet")
    }
}
```

### `-Xdoclint` and Treating Doc Warnings as Errors

`-Xdoclint` is the `javadoc` tool's built-in linter: it flags malformed HTML, `{@link}` references to symbols that don't exist, missing `@param`/`@return`/`@throws` tags, and other structural problems, without needing a separate static-analysis tool. Since JDK 8, `javac` itself also runs a subset of doclint checks and can emit warnings during **compilation**, not just during Javadoc generation.

```bash
javadoc -Xdoclint:all -d docs -sourcepath src/main/java com.example.orders
javac -Xdoclint:all -d out src/main/java/com/example/orders/*.java
```

Common `-Xdoclint` groups you can enable/disable independently: `accessibility`, `html`, `missing`, `reference`, `syntax`. A pragmatic default that reviewers commonly ask for is `all,-missing` — validate everything that's present, but don't force every single public element to have a comment (that requirement tends to produce exactly the restating-the-name comments discussed below).

Treating doc warnings as build errors (`failOnWarning` in Maven, or `-Werror`-equivalent doclint options in Gradle) prevents doc rot: a broken `{@link}` to a renamed method, or a `<p>` tag that was never closed, stays broken forever unless something makes the build fail on it.

### Publishing Javadoc

For a library, Javadoc is typically published alongside the release artifact as a `-javadoc.jar` classifier (required by Maven Central), and/or as a static HTML site (e.g., via GitHub Pages) generated by CI on release. `javadoc.io` can render any artifact already on Maven Central without any extra publishing step, by resolving the `-javadoc.jar` classifier directly.

```bash
mvn javadoc:jar deploy    # produces and deploys my-lib-1.4.0-javadoc.jar alongside the main jar
```

### When a Javadoc Comment Is Worse Than No Comment

A Javadoc comment that merely restates the method/parameter name in English adds visual noise, gives the false impression that the method is documented, and — worst of all — will drift out of sync with the real behavior over time because nobody feels the need to update "obvious" prose. It is strictly worse than no comment, because a missing comment at least invites the reader to go read the code.

```java
// Bad: pure noise, restates the signature
/**
 * Gets the name.
 * @return the name
 */
public String getName() { return name; }

// Bad: restates behavior visible from the method body one line below,
// adds nothing about the contract (can it return null? is it idempotent?)
/**
 * Sets the value.
 * @param value the value
 */
public void setValue(int value) { this.value = value; }
```

```java
// Good: worth documenting, because the contract isn't obvious from the signature
/**
 * Returns the display name shown in the UI, falling back to the account's
 * email address if the user has never set one.
 *
 * @return the display name; never {@code null}
 */
public String getName() { return name != null ? name : email; }
```

A simple review heuristic: if you deleted the Javadoc comment and could still write the exact same sentence just by reading the method name and signature, the comment is not documenting anything and should either be deleted or expanded to cover the actual contract (nullability, exceptions, thread-safety, or a non-obvious side effect).

## Common Code-Review Interview Pitfalls

1. **Committing `target/` or `build/` output directories to version control.**
   Why it matters: generated artifacts bloat the repository, go stale relative to source, and cause merge noise; they should always be build-tool-generated, never hand-edited or tracked.
   ```
   # .gitignore
   target/
   build/
   ```

2. **Not committing the Maven/Gradle Wrapper (`mvnw`/`gradlew`).**
   Why it matters: without a pinned wrapper, "works on my machine" becomes literal — a teammate or CI runner with a different globally installed build-tool version can produce a different, sometimes-broken build.
   ```bash
   mvn wrapper:wrapper -Dmaven=3.9.6   # generate and commit mvnw, mvnw.cmd, .mvn/
   ```

3. **Using a version range (`[1.0,2.0)`) instead of a pinned version for a dependency.**
   Why it matters: the resolved version can silently change between builds as new releases are published, breaking reproducibility — the exact opposite of what a lockfile-free ecosystem needs to stay predictable.
   ```xml
   <!-- Before -->
   <version>[1.0,2.0)</version>
   <!-- After -->
   <version>1.4.2</version>
   ```

4. **Declaring a dependency as `compile`/`api` when it's only used internally.**
   Why it matters: it leaks the dependency onto every consumer's compile classpath, forcing consumers to recompile when an internal-only dependency changes version, and can leak internal types through the public API by accident.
   ```kotlin
   // Before
   api("org.apache.commons:commons-lang3:3.14.0")
   // After — used only inside this module's private helpers
   implementation("org.apache.commons:commons-lang3:3.14.0")
   ```

5. **Using `-Dmaven.test.skip=true` in CI instead of `-DskipTests` (or, worse, skipping tests routinely at all).**
   Why it matters: `maven.test.skip` doesn't even compile the test sources, so a broken test file can merge without anyone noticing until someone runs tests locally — a much worse failure mode than merely not running passing tests.
   ```bash
   # Before (hides compile errors in tests)
   mvn install -Dmaven.test.skip=true
   # After (at least verifies tests compile)
   mvn install -DskipTests
   ```

6. **Relying on Maven's nearest-wins transitive resolution without checking `dependency:tree`.**
   Why it matters: nearest-wins picks a version based on graph depth, not correctness — it can silently select an old or vulnerable version of a transitive dependency, and the only way to know is to actually look.
   ```bash
   mvn dependency:tree -Dverbose
   ```

7. **Writing a Javadoc comment that restates the method name instead of documenting the contract.**
   Why it matters: it looks like documentation without providing any information beyond the signature, and gives false confidence that behavior (nullability, exceptions, thread-safety) is documented when it isn't.
   ```java
   // Before
   /** Gets the name. @return the name */
   public String getName() { ... }
   // After
   /** Returns the display name, or the email address if none was set. */
   public String getName() { ... }
   ```

8. **Javadoc describing the current implementation instead of the caller-visible contract.**
   Why it matters: implementation-describing comments become lies the moment the implementation changes (e.g., swapping a `HashMap` for a database call), and nobody remembers to update prose that isn't checked by the compiler.
   ```java
   // Before
   /** Looks up the user by scanning an in-memory HashMap. */
   // After
   /** Returns the user with the given ID. @throws NoSuchElementException if not found */
   ```

9. **A broken `{@link}` reference to a renamed or deleted method/class.**
   Why it matters: it silently degrades documentation quality (a dead link in generated docs) and usually indicates the comment wasn't updated alongside a refactor; `-Xdoclint:reference` catches this automatically if enabled and enforced in CI.
   ```bash
   javadoc -Xdoclint:reference -d docs -sourcepath src/main/java com.example.orders
   ```

10. **Mixing up `provided`/`compileOnly` with `runtime`/`runtimeOnly`, causing a `NoClassDefFoundError` at deploy time or a bloated fat jar.**
    Why it matters: `provided`/`compileOnly` dependencies (e.g., servlet API, Lombok) are intentionally excluded from the packaged artifact because the runtime environment supplies them — packaging them anyway (wrong scope) can cause classloading conflicts; the reverse mistake (using `provided` for something actually needed at runtime) causes a missing-class crash in production.
    ```xml
    <!-- Before: servlet-api is needed only to compile against, container supplies it -->
    <scope>compile</scope>
    <!-- After -->
    <scope>provided</scope>
    ```

11. **Using `maven-assembly-plugin` (or an unconfigured fat jar) for a Spring Boot / service-loader-heavy app instead of `maven-shade-plugin` with transformers.**
    Why it matters: assembly's naive concatenation can silently drop or corrupt merged `META-INF/services` files (used by `ServiceLoader`, JDBC drivers, logging backends), producing runtime failures that only show up when the fat jar is actually run, not when it's built.
    ```xml
    <transformer implementation="org.apache.maven.plugins.shade.resource.ServicesResourceTransformer"/>
    ```

12. **A custom Gradle task using `doLast {}` with no declared inputs/outputs, breaking incremental build and the build cache.**
    Why it matters: Gradle can only skip a task as `UP-TO-DATE` or restore it from cache if it knows what the task's inputs and outputs are; an undeclared `doLast` closure always reruns, silently making every build slower than it needs to be.
    ```kotlin
    // Before: always reruns, never cacheable
    tasks.register("generateDocs") { doLast { /* ... */ } }
    // After: typed task with declared @InputFiles/@OutputDirectory
    tasks.register<GenerateDocsTask>("generateDocs")
    ```

13. **Manually keeping dependency versions in sync across Maven modules or Gradle subprojects instead of using a BOM / version catalog.**
    Why it matters: without a single source of truth for versions, modules drift apart over time, producing exactly the kind of version conflicts `dependency:tree`/`dependencyInsight` are needed to untangle later — the bug is prevented far more cheaply than it's debugged.
    ```toml
    # gradle/libs.versions.toml — one place to bump a version for every subproject
    [versions]
    jackson = "2.17.1"
    ```

14. **Declaring `<release>` inconsistently across modules (or using the old `source`/`target` pair instead of `release`).**
    Why it matters: `source`/`target` alone can compile against a *newer* JDK's bootclasspath while claiming an older language level, letting code accidentally reference APIs unavailable on the actual target runtime; `<release>` locks both together and fails the build if that happens.
    ```xml
    <!-- Before -->
    <source>17</source>
    <target>17</target>
    <!-- After -->
    <release>21</release>
    ```

15. **Suppressing all Javadoc/doclint warnings globally instead of fixing or narrowly excusing them.**
    Why it matters: a blanket `-Xdoclint:none` (or `doclint=none`) hides genuinely broken documentation (bad HTML, dead `{@link}`s, missing `@throws` for a checked exception) right alongside harmless style nitpicks, defeating the entire purpose of running the linter in the first place.
    ```xml
    <!-- Before: disables everything -->
    <doclint>none</doclint>
    <!-- After: validates structure/links, only relaxes the "every element must have a comment" rule -->
    <doclint>all,-missing</doclint>
    ```
