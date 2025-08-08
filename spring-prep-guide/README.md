# Spring Preparation Book

## Topics

### 1. Spring Fundamentals
What is Spring?
Spring Ecosystem Overview
Inversion of Control (IoC)
Dependency Injection (DI)
Bean Lifecycle
Bean Scopes
Spring Container
BeanFactory vs ApplicationContext
Configuration Metadata
XML Configuration
Java Configuration
Annotation-based Configuration

### 2. Dependency Injection & Bean Management
@Component
@Service
@Repository
@Controller
@Configuration
@Bean
@Autowired
Constructor Injection
Field Injection
Setter Injection
@Primary
@Qualifier
Lazy Initialization
Bean Profiles
Conditional Beans
Circular Dependencies
Bean Post Processors

### 3. Spring Boot Fundamentals
What is Spring Boot?
Auto Configuration
Starter Dependencies
Spring Boot CLI
Spring Initializr
Embedded Servers
Spring Boot Application Structure
@SpringBootApplication
Externalized Configuration
Configuration Properties
YAML vs Properties
Profiles
CommandLineRunner
ApplicationRunner

### 4. Configuration & Environment
application.properties
application.yml
@Value
@ConfigurationProperties
Relaxed Binding
Property Validation
Environment API
Profiles
Profile Groups
Configuration Precedence
Secrets Management

### 5. Spring MVC
MVC Architecture
DispatcherServlet
Controllers
Request Mapping
Path Variables
Request Parameters
Request Body
Response Body
REST Controllers
HTTP Methods
ResponseEntity
Content Negotiation
Validation
BindingResult
Exception Handling
Controller Advice
Message Converters
Interceptors
Filters

### 6. REST API Design
REST Principles
Resource Design
DTOs
Entity vs DTO
Validation
Pagination
Sorting
Filtering
HATEOAS
Versioning
Error Responses
Problem Details (RFC 9457)
Idempotency
OpenAPI / Swagger

### 7. Spring Data
Spring Data Overview
Repository Pattern
CrudRepository
PagingAndSortingRepository
JpaRepository
Derived Queries
Query Methods
JPQL
Native Queries
Specifications
Query by Example
Pagination
Sorting
Projections
Auditing

### 8. Spring & JPA
JPA Fundamentals
Entity Lifecycle
Persistence Context
Hibernate Basics
Entity Mapping
Relationships
Cascade Types
Fetch Types
Lazy Loading
Eager Loading
N+1 Problem
Entity Graphs
Transactions
Dirty Checking
Optimistic Locking
Pessimistic Locking
Caching
Batch Operations

### 9. Transactions
Transaction Management
Declarative Transactions
@Transactional
Propagation
Isolation Levels
Rollback Rules
Read-only Transactions
Nested Transactions
Transaction Synchronization

### 10. Validation
Bean Validation
Jakarta Validation
Custom Validators
Groups
Cross-field Validation
Method Validation

### 11. Spring Security
Security Fundamentals
Authentication
Authorization
Security Filter Chain
Password Encoding
UserDetailsService
JWT Authentication
OAuth2
OpenID Connect
Method Security
CSRF
CORS
Session Management
Security Headers
Resource Server
Client Credentials
Role vs Authority

### 12. Spring AOP
Aspect-Oriented Programming
Join Points
Pointcuts
Advice Types
Aspects
Proxy-based AOP
Common Use Cases
Custom Annotations

### 13. Spring Boot Testing
Testing Overview
Unit Testing
Integration Testing
Slice Tests
@SpringBootTest
@WebMvcTest
@DataJpaTest
@MockBean
Mockito
Testcontainers
Embedded Databases
MockMvc
WebTestClient

### 14. Spring Boot Actuator
Health Endpoints
Metrics
Info Endpoint
Custom Health Indicators
Micrometer
Prometheus
Grafana
Tracing

### 15. Logging
SLF4J
Logback
Log Levels
Structured Logging
MDC
Correlation IDs
Request Logging

### 16. Caching
Spring Cache
Cache Abstraction
Cache Managers
Redis Cache
Caffeine Cache
Cache Eviction
Cache Synchronization

### 17. Scheduling & Async
Scheduling
@Scheduled
Cron Expressions
Async Processing
@Async
Async Executors
CompletableFuture Integration

### 18. Messaging
Spring Events
Application Events
Kafka
RabbitMQ
JMS
Dead Letter Queues
Retry
Event-Driven Architecture

### 19. Reactive Spring
Reactive Programming
Reactor
Mono
Flux
Backpressure
Spring WebFlux
Functional Endpoints
Reactive Security
Reactive Data Access

### 20. Cloud & Distributed Systems
Spring Cloud Overview
Config Server
Service Discovery
Eureka
Consul
Gateway
OpenFeign
Circuit Breaker
Resilience4j
Retry
Rate Limiting
Distributed Configuration

### 21. Observability
Micrometer
Distributed Tracing
OpenTelemetry
Zipkin
Jaeger
Health Monitoring
Metrics Collection

### 22. Spring Boot Internals
Auto Configuration Internals
Conditional Annotations
Auto Configuration Ordering
Starter Creation
Bean Loading Process
Environment Processing
Configuration Binding Internals
Embedded Servlet Container
Spring Boot Lifecycle

### 23. Native Images & AOT
GraalVM
Spring AOT
Native Compilation
Reflection Hints
Runtime Hints
Native Image Optimization

### 24. Performance
Startup Optimization
Lazy Initialization
Bean Optimization
Connection Pooling
HikariCP
HTTP Performance
Database Performance
JVM Tuning for Spring
Memory Optimization

### 25. Build & Deployment
Maven
Gradle
Spring Boot Maven Plugin
Spring Boot Gradle Plugin
Fat JARs
Layered JARs
Docker
Docker Compose
Kubernetes Basics
Environment Variables

### 26. Best Practices
Clean Architecture
Layered Architecture
Hexagonal Architecture
Dependency Inversion
DTO Mapping
Exception Handling
Validation Strategy
Configuration Management
Package Organization
API Versioning
Security Best Practices
Performance Best Practices

### 27. Common Code Review Topics
Constructor Injection vs Field Injection
Proper Transaction Boundaries
N+1 Query Detection
Lazy Loading Pitfalls
Entity vs DTO Separation
Exception Handling
Logging Best Practices
Validation Placement
Security Misconfigurations
Bean Scope Issues
Circular Dependencies
Configuration Smells
Testability
Thread Safety
Performance Bottlenecks