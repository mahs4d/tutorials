# Core Preparation Book

Each topic below is a self-contained chapter in [`chapters/`](chapters/). Every chapter
explains the subtopics in plain language with runnable Java examples and ends with a
**Common Code-Review Interview Pitfalls** section.

## Reading it in the browser

Open [`index.html`](index.html) — just double-click it, no server or internet needed.
It gives you the whole book with a chapter list, syntax highlighting, full-text search,
per-section navigation, dark mode, and a **read/unread checkbox for every chapter**.
Progress is stored in your browser's `localStorage`, so it survives reloads.

| | |
|---|---|
| `/` | focus search |
| `[` / `]` | previous / next chapter |
| `m` | mark the current chapter read |

After editing or adding a chapter, re-bundle the text so the offline page picks it up:

```bash
python3 build.py
```

(When served over `http://` — e.g. `python3 -m http.server` — the page reads the
Markdown files directly and you can skip that step.)

| # | Chapter | File |
|---|---------|------|
| 1 | Java Fundamentals | [01-java-fundamentals.md](chapters/01-java-fundamentals.md) |
| 2 | Object-Oriented Programming | [02-object-oriented-programming.md](chapters/02-object-oriented-programming.md) |
| 3 | Modern Java Language Features | [03-modern-java-language-features.md](chapters/03-modern-java-language-features.md) |
| 4 | Generics and Type System | [04-generics-and-type-system.md](chapters/04-generics-and-type-system.md) |
| 5 | Object Class & Common APIs | [05-object-class-and-common-apis.md](chapters/05-object-class-and-common-apis.md) |
| 6 | Exception Handling | [06-exception-handling.md](chapters/06-exception-handling.md) |
| 7 | Collections Framework | [07-collections-framework.md](chapters/07-collections-framework.md) |
| 8 | Functional Programming | [08-functional-programming.md](chapters/08-functional-programming.md) |
| 9 | Date, Time & Localization | [09-date-time-and-localization.md](chapters/09-date-time-and-localization.md) |
| 10 | File I/O | [10-file-io.md](chapters/10-file-io.md) |
| 11 | JVM Fundamentals | [11-jvm-fundamentals.md](chapters/11-jvm-fundamentals.md) |
| 12 | Memory Management | [12-memory-management.md](chapters/12-memory-management.md) |
| 13 | Concurrency (Core) | [13-concurrency-core.md](chapters/13-concurrency-core.md) |
| 14 | Concurrency (Advanced) | [14-concurrency-advanced.md](chapters/14-concurrency-advanced.md) |
| 15 | Modern Concurrency (Java 21+) | [15-modern-concurrency.md](chapters/15-modern-concurrency.md) |
| 16 | Reflection & Runtime Features | [16-reflection-and-runtime-features.md](chapters/16-reflection-and-runtime-features.md) |
| 17 | Modules & Native Interoperability | [17-modules-and-native-interoperability.md](chapters/17-modules-and-native-interoperability.md) |
| 18 | Networking & Security | [18-networking-and-security.md](chapters/18-networking-and-security.md) |
| 19 | Software Engineering Best Practices | [19-software-engineering-best-practices.md](chapters/19-software-engineering-best-practices.md) |
| 20 | Testing | [20-testing.md](chapters/20-testing.md) |
| 21 | Build & Documentation | [21-build-and-documentation.md](chapters/21-build-and-documentation.md) |
| 22 | Code-Review Interview Focused Topics | [22-code-review-interview-focused-topics.md](chapters/22-code-review-interview-focused-topics.md) |

## Topics

### 1. Java Fundamentals
Primitive Data Types
Variables, Scope, and Lifetime
Operators and Expressions
Control Flow Statements
Methods and Parameter Passing
Arrays
Strings, StringBuilder, and StringBuffer
Autoboxing and Unboxing
Varargs
Local Variable Type Inference (var)

### 2. Object-Oriented Programming
Classes and Objects
Constructors
Encapsulation
Inheritance
Polymorphism
Abstraction
Method Overloading
Method Overriding
Access Modifiers
Static Members
Final Keyword
This and Super
Nested, Inner, Local, and Anonymous Classes

### 3. Modern Java Language Features
Enums
Records
Sealed Classes and Interfaces
Interfaces (default, static, private methods)
Abstract Classes
Pattern Matching (instanceof, switch)
Primitive Types in Patterns
Switch Expressions
Text Blocks
String Templates (Preview)
Unnamed Variables and Patterns
Value-Based Classes
Preview and Incubator Features
Deprecated and Removed Features Across Java Versions

### 4. Generics and Type System
Generics
Type Erasure
Wildcards (extends, super)
Covariance and Contravariance

### 5. Object Class & Common APIs
equals(), hashCode(), and toString()
Object Cloning
Optional
Comparator and Comparable
Immutability
Defensive Copying

### 6. Exception Handling
Exceptions
Checked vs Unchecked Exceptions
Exception Handling Best Practices
Try-with-Resources
Assertions

### 7. Collections Framework
Collections Framework Overview
Collection Interface
List
Set
Queue
Deque
Map
ArrayList
LinkedList
HashMap
LinkedHashMap
TreeMap
HashSet
LinkedHashSet
TreeSet
PriorityQueue
ArrayDeque
IdentityHashMap
WeakHashMap
EnumMap
EnumSet
Immutable Collections (List.of())
Concurrent Collections

### 8. Functional Programming
Functional Interfaces
Lambda Expressions
Method References
Streams API
Collectors
Spliterator
Stream Performance
Parallel Streams

### 9. Date, Time & Localization
Date and Time API (java.time)
Formatting and Parsing
Internationalization (i18n)
Localization (l10n)

### 10. File I/O
File I/O (java.io)
NIO and NIO.2
Paths and Files
Serialization
Externalizable

### 11. JVM Fundamentals
JDK, JRE, JVM Architecture
Compilation, Class Loading, and Execution
Bytecode
JIT Compilation
Class Loaders
Java Memory Model (JMM)

### 12. Memory Management
Garbage Collection
Serial GC
Parallel GC
G1 GC
ZGC
Shenandoah
Memory Leaks
Escape Analysis
JVM Flags and Diagnostics
Garbage Collection Tuning
JVM Performance Tuning
Profiling and Monitoring
JFR
JMC
jcmd
jmap
jstack

### 13. Concurrency (Core)
Threads
Runnable and Callable
Thread Lifecycle
Synchronization
Intrinsic Locks
ReentrantLock
ReadWriteLock
StampedLock
Volatile
Atomic Classes
ThreadLocal
Happens-Before Relationship
Visibility, Atomicity, and Ordering

### 14. Concurrency (Advanced)
Executors Framework
ForkJoinPool
CompletableFuture
BlockingQueue
ConcurrentHashMap Internals
CountDownLatch
CyclicBarrier
Phaser
Semaphore
Exchanger

### 15. Modern Concurrency (Java 21+)
Virtual Threads (Project Loom)
Structured Concurrency
Scoped Values

### 16. Reflection & Runtime Features
Reflection
Annotations
Annotation Processing
Dynamic Proxies
Method Handles
VarHandles
Unsafe Alternatives
Service Provider Interface (SPI)

### 17. Modules & Native Interoperability
Packages
Java Platform Module System (JPMS)
Foreign Function & Memory API
Vector API (Incubator)

### 18. Networking & Security
Networking (java.net)
HTTP Client API
Process API
Security Basics
Cryptography APIs

### 19. Software Engineering Best Practices
Clean Code in Java
SOLID Principles
Effective Java Best Practices
Design Patterns in Java
API Design Best Practices
Common Code Smells

### 20. Testing
Testing Fundamentals (JUnit 5)
Mocking (Mockito)

### 21. Build & Documentation
Maven
Gradle
Javadoc

### 22. Code-Review Interview Focused Topics 
Common Java Interview Pitfalls
Code Review Scenarios
Performance Optimization
Thread Safety Review
Collections Selection
Exception Design
API Design Review
Memory & GC Review
Concurrency Review
Modern Java Feature Usage
