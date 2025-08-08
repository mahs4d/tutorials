# 25. Build & Deployment

## Overview

A Spring Boot application starts life as source code on a developer's laptop and ends up as a running process serving traffic somewhere else — a server, a container, a cluster. Getting from one end to the other is the job of a **build tool** (Maven or Gradle), which compiles code, resolves dependencies, and packages everything into a single runnable artifact called a **fat jar**. That jar is then wrapped inside a **container image** (usually built with Docker or Cloud Native Buildpacks) so it behaves the same way on every machine, regardless of what is or isn't installed there. From there, **Docker Compose** helps you run the app together with its dependencies (a database, a cache) on your own machine, and **Kubernetes** takes over running many copies of that same image reliably in production, restarting them, scaling them, and routing traffic to them. **Environment variables** are the thread that ties it all together — the same image, unmodified, behaves differently in dev, staging, and production purely based on what configuration is injected into it at runtime. This chapter walks through each link in that chain.

## Maven

Maven is a build tool that answers two questions for a Java project: "what do I depend on?" and "how do I turn my source code into something runnable?" You describe your project in an XML file called `pom.xml` (Project Object Model), and Maven downloads the dependencies you list, compiles your code, runs your tests, and packages the result — all following a standard, predictable sequence of steps called the **build lifecycle**.

Think of Maven's lifecycle like an assembly line: your code moves through fixed stations in order, and each station depends on the ones before it having finished successfully.

### Lifecycle phases

| Phase | What happens |
|---|---|
| `validate` | Checks the project structure and `pom.xml` are correct |
| `compile` | Compiles main Java source code |
| `test` | Runs unit tests (via Surefire plugin) |
| `package` | Bundles compiled code into a jar/war |
| `verify` | Runs integration checks (e.g., Failsafe integration tests) on the packaged artifact |
| `install` | Copies the artifact into your local `~/.m2` repository so other local projects can use it |
| `deploy` | Uploads the artifact to a remote repository (e.g., Nexus, Artifactory) for others to use |

Running a later phase automatically runs all earlier ones. `mvn package` will compile and test first. `mvn install` will also package first.

### A complete `pom.xml`

Spring Boot projects typically start from the `spring-boot-starter-parent`, which pins plugin versions and dependency versions for you:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                              https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.3.4</version>
        <relativePath/>
    </parent>

    <groupId>com.example</groupId>
    <artifactId>order-service</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <java.version>17</java.version>
    </properties>

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
            <groupId>org.postgresql</groupId>
            <artifactId>postgresql</artifactId>
            <scope>runtime</scope>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-devtools</artifactId>
            <scope>runtime</scope>
            <optional>true</optional>
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
</project>
```

### Parent POM vs. BOM import

Using `spring-boot-starter-parent` is convenient but forces your `pom.xml` to inherit from it, which is a problem if your company already has its own corporate parent POM (you can only extend one parent). The alternative is to **not** use the Spring Boot parent, and instead import Spring Boot's **BOM** (Bill of Materials) — a dependency-version list — inside `dependencyManagement`:

```xml
<parent>
    <groupId>com.mycompany</groupId>
    <artifactId>corporate-parent</artifactId>
    <version>4.2.0</version>
</parent>

<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-dependencies</artifactId>
            <version>3.3.4</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>
```

This gives you the same version alignment for Spring dependencies without giving up your own parent. You do lose the default plugin configuration and `properties` shortcuts the starter parent provides, so you may need to configure the `spring-boot-maven-plugin` explicitly.

| Approach | Pros | Cons |
|---|---|---|
| `spring-boot-starter-parent` | Zero-config, sensible defaults, plugin versions pre-set | Can only have one parent — conflicts with a corporate parent |
| BOM import via `dependencyManagement` | Works alongside any parent | You must configure some plugin defaults yourself |

### Dependency scopes

| Scope | Available at compile time | Available at runtime | Packaged in jar | Typical use |
|---|---|---|---|---|
| `compile` (default) | Yes | Yes | Yes | Normal library dependency |
| `provided` | Yes | No (assumed provided by environment) | No | Servlet API in a WAR deployed to an app server |
| `runtime` | No | Yes | Yes | JDBC drivers, `devtools` |
| `test` | Test only | Test only | No | JUnit, `spring-boot-starter-test` |
| `system` | Yes | No | No | Rare — local jar not in a repository |

### The Maven Wrapper

The **Maven Wrapper** (`mvnw` / `mvnw.cmd`) is a small script checked into your repository that downloads the exact Maven version your project needs, so nobody has to install Maven manually or fight version mismatches. Spring Initializr generates it for you by default.

```bash
# Generate/update the wrapper (if not already present)
mvn wrapper:wrapper

# Use it instead of a globally-installed mvn
./mvnw clean verify
./mvnw spring-boot:run
```

Because `./mvnw` is committed to git, CI servers and every teammate build with the identical Maven version — no "works on my machine" surprises.

### Resolving dependency conflicts with `dependency:tree`

Two libraries can pull in different versions of the same transitive dependency (a "diamond dependency" problem). Maven picks one automatically (nearest-wins), but that's not always the version you want.

```bash
./mvnw dependency:tree
```

```
[INFO] com.example:order-service:jar:1.0.0
[INFO] +- org.springframework.boot:spring-boot-starter-web:jar:3.3.4:compile
[INFO] |  +- org.springframework.boot:spring-boot-starter-json:jar:3.3.4:compile
[INFO] |  |  \- com.fasterxml.jackson.core:jackson-databind:jar:2.17.2:compile
[INFO] +- com.somevendor:legacy-client:jar:2.0.0:compile
[INFO]    \- com.fasterxml.jackson.core:jackson-databind:jar:2.9.10:compile (version selected from constraint)
```

If the wrong version wins, force the one you want with an explicit `<dependency>` entry (Maven's "nearest declaration" rule) or an `<exclusion>` on the offending transitive dependency.

## Gradle

Gradle is a build tool like Maven, but it uses a real programming language (Groovy or Kotlin) instead of XML to describe the build, and it's generally faster because it caches build outputs and only rebuilds what actually changed (incremental builds). Many teams pick Gradle for larger, more customized builds; Maven is often preferred for its simplicity and convention-over-configuration.

### A complete `build.gradle.kts`

```kotlin
plugins {
    java
    id("org.springframework.boot") version "3.3.4"
    id("io.spring.dependency-management") version "1.1.6"
}

group = "com.example"
version = "1.0.0"

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(17))
    }
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")
    runtimeOnly("org.postgresql:postgresql")
    developmentOnly("org.springframework.boot:spring-boot-devtools")
    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.withType<Test> {
    useJUnitPlatform()
}
```

The `org.springframework.boot` plugin adds tasks like `bootJar` and `bootRun`. The `io.spring.dependency-management` plugin gives you the same BOM-style version alignment that Maven's parent POM provides — you don't need to hunt for compatible versions of every Spring dependency yourself.

### Configurations (Gradle's equivalent of scopes)

| Configuration | Meaning |
|---|---|
| `implementation` | Available at compile and runtime, but hidden from consumers of your jar (the default choice) |
| `api` | Like `implementation`, but exposed to consumers too (only relevant for libraries, needs the `java-library` plugin) |
| `runtimeOnly` | Available only at runtime, not compile time (e.g., a JDBC driver) |
| `compileOnly` | Available only at compile time, not packaged, not on the runtime classpath (e.g., annotation-only libraries) |
| `developmentOnly` | Only used at development time and excluded from a produced jar/image (e.g., `devtools`) |
| `testImplementation` | Like `implementation`, but only for test source code |
| `testRuntimeOnly` | Only for running tests |

### The Gradle Wrapper

```bash
# Generate the wrapper (bundles a specific Gradle version with the project)
gradle wrapper --gradle-version 8.10

./gradlew clean build
./gradlew bootRun
```

Just like Maven's wrapper, `./gradlew` guarantees everyone (and CI) uses the same Gradle version, without a global install.

### Maven vs. Gradle

| Aspect | Maven | Gradle |
|---|---|---|
| Configuration language | XML (`pom.xml`) | Groovy or Kotlin DSL (`build.gradle` / `build.gradle.kts`) |
| Build speed | Slower, always re-runs everything unless configured otherwise | Faster — incremental builds and build caching by default |
| Learning curve | Simple, very convention-driven | More flexible, but more concepts (tasks, configurations) |
| Dependency management | `dependencyManagement` / parent POM | `io.spring.dependency-management` plugin or version catalogs |
| Ecosystem maturity | Extremely mature, huge plugin ecosystem | Mature, dominant in Android and increasingly common elsewhere |
| Typical choice | Enterprises wanting simplicity/stability | Teams wanting speed and custom build logic |

Both are fully supported by Spring Boot and Spring Initializr — pick one and standardize on it per project.

## Spring Boot Maven Plugin

The `spring-boot-maven-plugin` is what turns a normal Maven jar into a Spring Boot **fat jar** and gives you developer-convenience goals.

```xml
<plugin>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-maven-plugin</artifactId>
    <configuration>
        <mainClass>com.example.orderservice.OrderServiceApplication</mainClass>
        <excludes>
            <exclude>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-devtools</artifactId>
            </exclude>
        </excludes>
    </configuration>
    <executions>
        <execution>
            <goals>
                <goal>build-info</goal>
            </goals>
        </execution>
    </executions>
</plugin>
```

### Key goals

| Goal | What it does |
|---|---|
| `repackage` | Runs automatically during `package` — takes the plain jar Maven produced and repackages it into an executable fat jar, keeping the original as `*.jar.original` |
| `spring-boot:run` | Runs the application directly from source, without packaging — great for local development |
| `spring-boot:build-image` | Builds an OCI container image using Cloud Native Buildpacks, with no Dockerfile needed |
| `build-info` | Generates `META-INF/build-info.properties` with build time, version, etc., consumed by the Actuator `/actuator/info` endpoint |

```bash
# Run locally with a specific profile active
./mvnw spring-boot:run -Dspring-boot.run.profiles=dev

# Build a container image with buildpacks, no Dockerfile
./mvnw spring-boot:build-image -Dspring-boot.build-image.imageName=example/order-service:1.0.0
```

Excluding `devtools` from `repackage` (as shown above) matters because devtools is meant only for local development — it enables live reload and relaxed caching, and you never want that logic running against production traffic.

## Spring Boot Gradle Plugin

The `org.springframework.boot` Gradle plugin does the equivalent job for Gradle builds: it replaces the plain `jar` task's output with an executable one via `bootJar`.

```kotlin
tasks.named<org.springframework.boot.gradle.tasks.bundling.BootJar>("bootJar") {
    archiveFileName.set("order-service.jar")
    mainClass.set("com.example.orderservice.OrderServiceApplication")
}

springBoot {
    buildInfo()
}
```

### Key tasks

| Task | What it does |
|---|---|
| `bootJar` | Packages an executable fat jar (Gradle's version of `repackage`) |
| `bootRun` | Runs the app directly from compiled classes, for local development |
| `bootBuildImage` | Builds a container image via Cloud Native Buildpacks, no Dockerfile required |
| `springBoot { buildInfo() }` | Generates the same `build-info.properties` that the Actuator `/actuator/info` endpoint reads |

```bash
# Run with a profile active
./gradlew bootRun --args='--spring.profiles.active=dev'

# Build a container image, no Dockerfile
./gradlew bootBuildImage --imageName=example/order-service:1.0.0
```

Note that `bootJar` and the plain `jar` task both exist; if you only need the executable jar, you can disable the plain one with `tasks.jar { enabled = false }` to avoid producing two artifacts.

## Fat JARs

A normal jar is just a zip file of `.class` files with a manifest. It assumes all its dependencies are already on the classpath somewhere else — which is fine for a library, but useless for "just run this app." A **fat jar** (also called an "uber jar") solves this by bundling the application code *and* every dependency jar it needs into one single file, so `java -jar app.jar` is all that's required to run it, on any machine with a JVM.

Spring Boot's fat jar isn't a naive "unzip everything and mash it together" jar (which causes filename collisions between dependencies). Instead it uses a **nested jar** layout:

```
app.jar
├── META-INF/
│   └── MANIFEST.MF          # Main-Class: org.springframework.boot.loader.launch.JarLauncher
├── org/springframework/boot/loader/   # Boot's own tiny bootstrap classloader
├── BOOT-INF/
│   ├── classes/              # Your compiled application classes and resources
│   └── lib/                  # Every dependency jar, kept whole, un-exploded
```

- **`BOOT-INF/classes`** — your own `.class` files and `application.properties`, exactly as they'd sit on a normal classpath.
- **`BOOT-INF/lib`** — each dependency as its own jar file, untouched, so there's no risk of two libraries' files clobbering each other.
- **`org/springframework/boot/loader`** — a minimal set of Boot's own classes, the only thing a plain `java -jar` command can see directly.

### Why a plain jar won't run

If you try to unzip and repack a fat jar naively, the JVM's normal classloader has no idea how to reach into `BOOT-INF/lib/*.jar` — the standard classloading mechanism only understands flat directories and jars-of-classes, not jars-of-jars. That's why Spring Boot's manifest doesn't point `Main-Class` at your `@SpringBootApplication` class directly. It points to `JarLauncher`.

- **`JarLauncher`** is Boot's own tiny bootstrap class. When the JVM starts, it runs first, builds a special classloader that knows how to read `BOOT-INF/classes` and every jar inside `BOOT-INF/lib`, and only *then* invokes your real `main()` method.

```bash
# This works — JarLauncher does the classloading magic first
java -jar order-service.jar

# Passing app args and JVM options together
java -Xmx512m -jar order-service.jar --spring.profiles.active=prod
```

### WAR deployment — the legacy alternative

Before "just run the jar" became the default, Java web apps were packaged as **WAR** files (Web Application Archive) and deployed into an external servlet container like Tomcat or WebSphere that was already installed on the server. Spring Boot still supports this (`packaging: war` plus extending `SpringBootServletInitializer`) for organizations with existing app-server infrastructure, but it's considered legacy for new projects — bundling an embedded server inside an executable jar is simpler, is easier to containerize, and avoids version mismatches between your app and a shared container.

| | Executable (fat) JAR | WAR on external servlet container |
|---|---|---|
| Server | Embedded (Tomcat/Jetty/Undertow bundled in) | External, shared, pre-installed |
| Run command | `java -jar app.jar` | Deploy file into container's `webapps/` folder |
| Isolation | One process per app | Multiple apps can share one container instance |
| Modern default | Yes | No — legacy/enterprise use only |

## Layered JARs

A fat jar is convenient to run, but it's a poor fit for building efficient container images: every single code change, however small, produces a completely new jar file, and if you copy that whole jar into a Docker image as one blob, Docker has to re-upload and re-store the *entire thing* — dependencies included — on every deploy. **Layered jars** fix this by splitting the fat jar's contents into logical groups (layers) that change at different rates, so Docker can cache the slow-changing ones.

By default (Spring Boot 2.3+), the plugin already produces a jar with an internal index describing these layers — no extra configuration is required.

### `layers.idx`

Inside the jar, a file called `BOOT-INF/layers.idx` lists which files belong to which layer, in order from least likely to change to most likely to change:

| Layer | Typical contents | Change frequency |
|---|---|---|
| `dependencies` | Third-party libraries whose version you rarely bump | Rarely |
| `spring-boot-loader` | Boot's own loader classes | Almost never |
| `snapshot-dependencies` | `SNAPSHOT` dependencies (change more often than releases) | Occasionally |
| `application` | Your own compiled classes and resources | Every commit |

### Extracting layers

```bash
# Spring Boot 3.3+: the unified jarmode syntax
java -Djarmode=tools -jar order-service.jar extract

# Older syntax (Spring Boot 2.3 - 3.2)
java -Djarmode=layertools -jar order-service.jar extract
```

This produces separate directories (`dependencies/`, `spring-boot-loader/`, `snapshot-dependencies/`, `application/`) that a Dockerfile can `COPY` one at a time.

### Multi-stage Dockerfile exploiting layer caching

```dockerfile
# Stage 1: extract the fat jar into layers
FROM eclipse-temurin:21-jre-alpine AS builder
WORKDIR /application
COPY target/order-service.jar app.jar
RUN java -Djarmode=tools -jar app.jar extract --destination extracted

# Stage 2: assemble the final, lean image
FROM eclipse-temurin:21-jre-alpine
WORKDIR /application

COPY --from=builder application/spring-boot-loader/ ./
COPY --from=builder application/dependencies/ ./
COPY --from=builder application/snapshot-dependencies/ ./
COPY --from=builder application/application/ ./

ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
```

Because Docker caches each `COPY` instruction as its own layer, and the `dependencies/` layer's contents rarely change between builds, Docker reuses the cached layer and skips re-uploading it to the registry. Only the small `application/` layer (your actual code) gets re-pushed on every deploy — turning multi-hundred-megabyte pushes into multi-kilobyte ones for routine code changes.

## Docker

**Docker** packages your application together with everything it needs to run — the JVM, OS libraries, config — into a single, portable unit called a **container image**. A container built from that image runs identically on your laptop, in CI, and in production, because it doesn't rely on whatever happens to be installed on the host machine (an analogy: it's a shipping container, not a truck bed — the same box works on any ship, train, or crane).

### Production-grade multi-stage Dockerfile

```dockerfile
# ---- Build stage ----
FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /workspace
COPY mvnw pom.xml ./
COPY .mvn/ .mvn/
RUN ./mvnw dependency:go-offline
COPY src ./src
RUN ./mvnw clean package -DskipTests

# ---- Runtime stage ----
FROM eclipse-temurin:21-jre-alpine
WORKDIR /application

# Run as a non-root user — don't let the app process own root inside the container
RUN addgroup -S spring && adduser -S spring -G spring
USER spring:spring

COPY --from=build /workspace/target/order-service.jar app.jar

# Let the JVM size its heap relative to the container's memory limit, not the host's
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75.0 -XX:InitialRAMPercentage=50.0"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD wget -qO- http://localhost:8080/actuator/health/liveness || exit 1

ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
```

Key production details:

- **JRE, not JDK, in the final stage.** The JDK includes compilers and debugging tools you don't need at runtime; a JRE-only base image is dramatically smaller and has a smaller attack surface.
- **Non-root user.** If an attacker escapes your application into the container's OS layer, running as root would hand them the container's root user for free.
- **`MaxRAMPercentage`, not a fixed `-Xmx`.** Containers report a memory limit (e.g., 512Mi) to the JVM (modern JVMs are container-aware), and sizing the heap as a *percentage* of that limit means the same image behaves correctly whether it's deployed with 512Mi or 2Gi, without editing the Dockerfile.
- **`HEALTHCHECK`.** Lets Docker (and orchestrators reading the image) know whether the process inside is actually healthy, not just "still running."

### `.dockerignore`

Without a `.dockerignore`, Docker copies your entire build context (including `.git`, `target/`, IDE files, and possibly secrets) into the build, slowing builds and risking leaks.

```
.git
.gitignore
target/
build/
*.class
.idea/
*.iml
.env
.mvn/wrapper/maven-wrapper.jar
Dockerfile
.dockerignore
```

### Cloud Native Buildpacks — the no-Dockerfile option

Writing and maintaining a correct, secure Dockerfile is real work. **Cloud Native Buildpacks** are a standard for automatically turning source code into a secure, optimized container image without you writing any Dockerfile at all — Spring Boot ships this capability directly in its build plugins.

```bash
# Maven
./mvnw spring-boot:build-image -Dspring-boot.build-image.imageName=example/order-service:1.0.0

# Gradle
./gradlew bootBuildImage --imageName=example/order-service:1.0.0
```

Buildpacks automatically produce a layered image (similar structure to the manual layered-jar approach above), pick a suitable JRE, and apply security patches to the base image over time when you rebuild — all without you touching Docker syntax. Many teams use buildpacks for application images and reserve hand-written Dockerfiles for cases needing extra OS packages or custom setup.

## Docker Compose

**Docker Compose** lets you describe a group of containers — your app, a database, a cache — in one YAML file and start them all together with one command. This is the standard way to reproduce a "production-like" environment on a laptop.

### `compose.yaml`

```yaml
services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://postgres:5432/orders
      SPRING_DATASOURCE_USERNAME: orders_user
      SPRING_DATASOURCE_PASSWORD: orders_pass
      SPRING_DATA_REDIS_HOST: redis
      SPRING_PROFILES_ACTIVE: docker
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: orders
      POSTGRES_USER: orders_user
      POSTGRES_PASSWORD: orders_pass
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U orders_user -d orders"]
      interval: 5s
      timeout: 3s
      retries: 5
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pgdata:
```

```bash
docker compose up --build
docker compose down -v   # stop and remove volumes too
```

The `depends_on: condition: service_healthy` combination is important: without it, Compose only waits for the *container process* to start, not for Postgres to actually be ready to accept connections — a common source of flaky "connection refused" errors on first boot.

### `spring-boot-docker-compose` — automatic dev-time startup

Since Spring Boot 3.1, adding the `spring-boot-docker-compose` dependency lets your app **automatically start** the services listed in a `compose.yaml` file sitting next to your project when you run it locally — no manual `docker compose up` needed, and no manual configuration of connection URLs, since Boot detects the running containers and wires up the connection properties for you.

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-docker-compose</artifactId>
    <scope>runtime</scope>
    <optional>true</optional>
</dependency>
```

This is a development convenience only — it's marked `optional`/`runtime` and is meant to stay out of production images. In production, the database and cache are already running as separate managed services (RDS, ElastiCache, a Kubernetes-managed Postgres, etc.), so there's nothing for Boot to "start."

## Kubernetes Basics

**Kubernetes** ("k8s") is a system for running many containers reliably across a cluster of machines. Instead of you manually starting containers and restarting them when they crash, you describe the *desired state* ("I want 3 copies of this image running, always") and Kubernetes continuously works to keep reality matching that description.

The building blocks that matter most for a Spring Boot service:

| Resource | Purpose |
|---|---|
| **Deployment** | Declares how many replicas (copies) of your app's container should run, and how to roll out updates |
| **Service** | A stable network address that load-balances traffic across the currently-running Pod replicas |
| **ConfigMap** | Non-secret configuration values, injected as environment variables or files |
| **Secret** | Sensitive values (passwords, API keys), stored and injected similarly to a ConfigMap but base64-encoded and access-restricted |
| **Ingress** | Routes external HTTP(S) traffic into the cluster and to the right Service, usually with a hostname/path |

### Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
spec:
  replicas: 3
  selector:
    matchLabels:
      app: order-service
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 1
  template:
    metadata:
      labels:
        app: order-service
    spec:
      terminationGracePeriodSeconds: 45
      containers:
        - name: order-service
          image: registry.example.com/order-service:1.4.2
          ports:
            - containerPort: 8080
          envFrom:
            - configMapRef:
                name: order-service-config
            - secretRef:
                name: order-service-secrets
          resources:
            requests:
              cpu: "250m"
              memory: "512Mi"
            limits:
              cpu: "1"
              memory: "512Mi"
          startupProbe:
            httpGet:
              path: /actuator/health
              port: 8080
            failureThreshold: 30
            periodSeconds: 2
          readinessProbe:
            httpGet:
              path: /actuator/health/readiness
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /actuator/health/liveness
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 15
```

### Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: order-service
spec:
  selector:
    app: order-service
  ports:
    - port: 80
      targetPort: 8080
```

### ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: order-service-config
data:
  SPRING_PROFILES_ACTIVE: "production"
  SPRING_DATASOURCE_URL: "jdbc:postgresql://order-db:5432/orders"
  LOGGING_LEVEL_COM_EXAMPLE: "INFO"
```

### Secret

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: order-service-secrets
type: Opaque
stringData:
  SPRING_DATASOURCE_PASSWORD: "s3cr3t-value"
  SPRING_DATASOURCE_USERNAME: "orders_user"
```

Secrets in plain Kubernetes are only base64-encoded (not encrypted) by default, so real production setups typically pair this with a secrets manager (Vault, cloud KMS, sealed-secrets) rather than committing raw Secret YAML to git.

### Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: order-service-ingress
spec:
  rules:
    - host: orders.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: order-service
                port:
                  number: 80
```

### Probes, wired to Actuator

| Probe | Question it answers | Actuator endpoint |
|---|---|---|
| **Startup** | "Has the app finished starting up yet?" (gives slow-starting apps room before liveness kicks in) | `/actuator/health` |
| **Readiness** | "Is this instance ready to receive traffic right now?" (removes it from the Service's load balancing if not) | `/actuator/health/readiness` |
| **Liveness** | "Is this instance stuck/deadlocked and needs restarting?" | `/actuator/health/liveness` |

Enable the readiness/liveness groups with:

```yaml
management:
  endpoint:
    health:
      probes:
        enabled: true
```

Point probes at a lightweight, dedicated health path — not a heavy `/actuator/health` check that itself queries the database on every single probe hit (probes fire every few seconds; that adds real load).

### Resource requests/limits vs. JVM heap

- **`requests`** is what Kubernetes guarantees and uses for scheduling decisions (which node has room).
- **`limits`** is the hard ceiling — exceed the memory limit and the container gets OOMKilled; exceed the CPU limit and it gets throttled, not killed.
- The JVM's heap (sized via `-XX:MaxRAMPercentage`, as shown earlier) must stay comfortably under the *memory limit*, because the JVM also needs room for thread stacks, metaspace, and off-heap buffers — setting the heap equal to the container's memory limit is a common cause of mysterious OOMKills.

### Graceful shutdown

When Kubernetes wants to remove a Pod (scaling down, rolling update, node drain), it sends a `SIGTERM` and then waits up to `terminationGracePeriodSeconds` before force-killing with `SIGKILL`. Spring Boot's **graceful shutdown** feature uses that window to stop accepting new requests while letting in-flight ones finish:

```yaml
server:
  shutdown: graceful

spring:
  lifecycle:
    timeout-per-shutdown-phase: 30s
```

```yaml
spec:
  terminationGracePeriodSeconds: 45   # must be >= the app's own shutdown timeout, with margin
```

Without this, a rolling deploy can cut off requests mid-flight — a customer's in-progress checkout request simply dies.

### Rolling updates

A **rolling update** replaces old Pods with new ones gradually rather than all at once, so the service never goes fully down during a deploy. `maxUnavailable` controls how many old Pods can be down at once during the rollout; `maxSurge` controls how many *extra* new Pods can be started above the desired replica count while the rollout is in progress. Combined with readiness probes, Kubernetes won't route traffic to a new Pod until it reports itself ready — so a bad new version that fails its readiness check simply never receives traffic, and the rollout can be halted or rolled back before real damage is done.

## Environment Variables

**Environment variables** are key-value pairs the operating system passes into a process at startup. Spring Boot reads them automatically and maps them onto configuration properties through a mechanism called **relaxed binding** — you don't need an exact property-name match, because shell environments can't contain dots or mixed case the way Java property names can.

### Relaxed binding

| Property in `application.yaml` | Equivalent environment variable |
|---|---|
| `spring.datasource.url` | `SPRING_DATASOURCE_URL` |
| `spring.data.redis.host` | `SPRING_DATA_REDIS_HOST` |
| `server.port` | `SERVER_PORT` |
| `my.custom.feature-flag` | `MY_CUSTOM_FEATURE_FLAG` |

The rule of thumb: lowercase-with-dots becomes UPPERCASE_WITH_UNDERSCORES, and camelCase/kebab-case segments also collapse to underscores.

### Common environment variables

```bash
# Which profile(s) are active — swaps in application-<profile>.yaml
export SPRING_PROFILES_ACTIVE=production

# Datasource, relaxed-bound from property to env var
export SPRING_DATASOURCE_URL=jdbc:postgresql://db.internal:5432/orders
export SPRING_DATASOURCE_USERNAME=orders_user
export SPRING_DATASOURCE_PASSWORD=s3cr3t

# JVM-level options picked up automatically by any `java` invocation, without editing entrypoints
export JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75.0 -Dfile.encoding=UTF-8"
```

`JAVA_TOOL_OPTIONS` is special: it's read by the JVM itself (before Spring even starts), so it's a convenient place to inject options that must apply regardless of how the jar is launched, without modifying a Dockerfile's `ENTRYPOINT`.

### The 12-factor app principle

The [12-factor app](https://12factor.net/config) methodology's third factor states: **store config in the environment, not in code.** The same built artifact should run unmodified in dev, staging, and production — only the environment variables injected around it change.

- Config that varies between environments (URLs, credentials, feature flags) should never be hardcoded in `application.yaml` or, worse, in Java code.
- Config that's the same everywhere (which starters are on the classpath, business logic) belongs in code.

### Why environment variables beat baked-in config

| Baked-in config (e.g., separate images per environment) | Environment variables |
|---|---|
| Must rebuild the image to change a URL or flag | Change happens by editing a ConfigMap/Secret — no rebuild |
| Risk of drift between "the jar tested in staging" and "the jar deployed to prod" | The exact same image/jar is promoted through every environment |
| Secrets sometimes end up committed into config files inside the image | Secrets injected at runtime, never stored in the image |
| Harder to audit "what changed between deploys" | Config changes are visible in the orchestrator's own history (e.g., `kubectl rollout history`) |

This is precisely why the Kubernetes example above injects `SPRING_DATASOURCE_URL` via a ConfigMap and `SPRING_DATASOURCE_PASSWORD` via a Secret rather than compiling them into the jar — the same container image is reused unmodified across every environment.

## Common Code Review / Interview Pitfalls

- **Secrets baked into the image or committed to git.** Passwords and API keys in `application.yaml`, a Dockerfile `ENV`, or a Kubernetes Secret YAML checked into a public repo are all effectively public. Inject secrets at runtime from a secrets manager or orchestrator Secret store.
- **Running the container as root.** If there's no `USER` instruction (or equivalent), a container escape hands an attacker root. Always create and switch to an unprivileged user in the Dockerfile.
- **Using the `latest` tag in production.** `myapp:latest` is not reproducible — you can't tell which code is actually running, and a rollback becomes guesswork. Always deploy immutable, versioned tags (e.g., a git SHA or semantic version).
- **JDK instead of JRE as the runtime base image.** This ships compilers, debuggers, and build tools you'll never use at runtime, bloating the image and widening the attack surface for no benefit.
- **No `.dockerignore`.** Without one, `.git`, build directories, and local secrets can leak into the build context and sometimes into the final image layers.
- **Copying the fat jar in a single `COPY` instruction.** This throws away Docker's layer cache — every code change forces a full re-upload of all dependencies. Use layered jars (or buildpacks) so dependency layers stay cached.
- **No resource limits, or a JVM heap set above the container's memory limit.** Missing `limits` lets one runaway Pod starve its neighbors; a heap sized at or above the container limit causes OOMKills that look like random crashes.
- **No readiness probe, so traffic hits a cold app.** Without readiness gating, Kubernetes routes requests to Pods that are still initializing (loading caches, warming connection pools), producing errors right after every deploy.
- **Probes pointing at a heavyweight health endpoint.** A liveness/readiness check that queries the database on every poll (every few seconds) adds needless load and can cause cascading failures if the database is briefly slow.
- **No graceful shutdown, so in-flight requests get killed mid-deploy.** Without `server.shutdown: graceful` and a matching `terminationGracePeriodSeconds`, rolling updates silently drop customer requests.
- **`mvn clean install` (or worse, `deploy`) run casually in CI instead of `verify`.** `install`/`deploy` push artifacts to shared repositories as a side effect; CI pipelines should generally run `verify` and only `deploy` from an explicit release step, keeping builds reproducible and side-effect-free.
- **Version ranges or no dependency locking.** A `pom.xml`/`build.gradle` that allows a floating version range (or a Gradle build with no lock file) means the exact same source can produce a different artifact tomorrow — a reproducibility and supply-chain risk.
- **Shipping `devtools` to production.** If it's not excluded from `repackage`/`bootJar` (or scoped as `developmentOnly`/`optional`), you get live-reload machinery and relaxed security running against real traffic.
- **Building the image on a developer's laptop instead of in CI.** Laptop builds vary by local Docker version, cached layers, and even architecture (Apple Silicon vs. x86), producing images that don't match what was tested. CI should be the only path to a deployable image.
- **`SNAPSHOT` dependencies in a release build.** A `SNAPSHOT` version can change contents without changing its version number, so a "1.0.0" release built against a `SNAPSHOT` dependency isn't reproducible and can silently differ between builds.

## Quick Recap

- **Maven** and **Gradle** both compile, test, and package your app; Maven uses XML and a fixed lifecycle, Gradle uses Groovy/Kotlin and incremental tasks.
- Prefer the **wrapper** (`./mvnw` / `./gradlew`) so every machine builds with the same tool version.
- Use `dependency:tree` (Maven) or Gradle's dependency insight tools to resolve version conflicts between transitive dependencies.
- Choose the **Spring Boot starter parent** for simplicity, or import the **BOM** into `dependencyManagement` when you already have a different parent POM.
- The **Spring Boot Maven/Gradle plugins** turn a normal jar into an executable **fat jar** (`repackage` / `bootJar`), let you run locally (`spring-boot:run` / `bootRun`), and build container images with no Dockerfile (`build-image` / `bootBuildImage`).
- A fat jar's `BOOT-INF/classes` + `BOOT-INF/lib` + `JarLauncher` layout is why `java -jar` works — a naively-merged jar would not.
- **Layered jars** split the fat jar into cache-friendly chunks (`dependencies`, `application`, etc.) so Docker only re-uploads what changed.
- A good **Dockerfile** is multi-stage, uses a JRE base, runs as non-root, sizes the JVM heap as a percentage of the container's memory, and defines a `HEALTHCHECK`.
- **Docker Compose** spins up your app plus its dependencies (Postgres, Redis) locally with healthchecks and `depends_on`; `spring-boot-docker-compose` automates this for local dev.
- **Kubernetes** keeps a declared number of replicas running via a Deployment, load-balances via a Service, injects config/secrets via ConfigMaps/Secrets, and routes external traffic via Ingress — all wired to Actuator health probes.
- Configure **graceful shutdown** and a sufficient `terminationGracePeriodSeconds` so rolling updates don't kill in-flight requests.
- **Environment variables**, via Spring's relaxed binding (`SPRING_DATASOURCE_URL`, `SPRING_PROFILES_ACTIVE`), let the exact same built image behave correctly in every environment — the core of 12-factor configuration.
- Never bake secrets or environment-specific values into the image itself — inject them at runtime instead.
