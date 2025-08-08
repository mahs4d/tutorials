# 5. Spring MVC

## Overview

Spring MVC is the web framework inside the Spring ecosystem that lets you build web applications and REST APIs in Java. MVC stands for **Model-View-Controller**, a design pattern that separates an application into three concerns: data (Model), presentation (View), and the logic that connects them (Controller). Spring MVC handles the plumbing of receiving an HTTP request, routing it to the right piece of your code, converting data formats like JSON, and sending back a response. It matters for interviews because almost every Spring Boot job involves building or consuming REST APIs, and Spring MVC is the layer where that happens. Understanding how a request flows through Spring MVC — from the servlet container to your controller method and back — is one of the most commonly probed areas in Spring interviews.

## MVC Architecture

The MVC pattern splits responsibilities into three layers so that each part can change independently.

- **Model**: the data of your application. Usually plain Java objects (POJOs) or DTOs (Data Transfer Objects) that hold information like a `User` or `Order`.
- **View**: how the data is presented. In a traditional web app this could be an HTML page (rendered with Thymeleaf or JSP). In a REST API, the "view" is usually just JSON.
- **Controller**: receives the request, talks to the service/business layer, and decides what data (model) and what view to return.

Think of a restaurant: the **Controller** is the waiter who takes your order and coordinates with the kitchen. The **Model** is the food itself. The **View** is how the food is plated and presented to you.

```java
@Controller
public class GreetingController {

    @GetMapping("/greeting")
    public String greeting(@RequestParam(defaultValue = "World") String name, Model model) {
        model.addAttribute("name", name); // Model: data passed to the view
        return "greeting"; // View: name of the template to render (greeting.html)
    }
}
```

- In a **traditional MVC app**, controllers return a *view name* (a template) that gets rendered as HTML.
- In a **REST API**, controllers return *data* directly (JSON/XML), skipping HTML rendering entirely. This is the more common style in modern backend interviews.

| Layer | Responsibility | Example |
|---|---|---|
| Model | Holds application data | `User`, `OrderDto` |
| View | Renders data for the user | Thymeleaf template, JSON body |
| Controller | Routes requests, orchestrates logic | `@Controller`, `@RestController` |

## DispatcherServlet

The `DispatcherServlet` is the **front controller** of Spring MVC — a single entry point that every HTTP request passes through before reaching your code. Spring Boot auto-configures it for you; you rarely write it yourself. It's the "traffic cop" that decides which controller method should handle an incoming request, and it also manages exception handling, view resolution, and converting your return values into an HTTP response.

Here's the flow of a request through Spring MVC:

```
                 ┌─────────────────────────────────────────────┐
                 │              Servlet Container                │
                 │              (embedded Tomcat)                 │
                 └───────────────────────┬─────────────────────┘
                                          │  HTTP request
                                          ▼
                              ┌───────────────────────┐
                              │   DispatcherServlet     │
                              │   (front controller)    │
                              └───────────┬───────────┘
                                          │ 1. asks HandlerMapping
                                          ▼
                              ┌───────────────────────┐
                              │    HandlerMapping        │
                              │ "which controller method │
                              │   matches this URL?"     │
                              └───────────┬───────────┘
                                          │ 2. returns handler + interceptors
                                          ▼
                              ┌───────────────────────┐
                              │    HandlerAdapter        │
                              │  invokes the controller  │
                              └───────────┬───────────┘
                                          │ 3. calls your @Controller method
                                          ▼
                              ┌───────────────────────┐
                              │   Your Controller        │
                              │  (business logic call)   │
                              └───────────┬───────────┘
                                          │ 4. returns Model + View name
                                          │    (or a @ResponseBody object)
                                          ▼
                              ┌───────────────────────┐
                              │    ViewResolver          │
                              │ (skipped for REST APIs)  │
                              └───────────┬───────────┘
                                          │ 5. renders view (HTML) OR
                                          │    HttpMessageConverter writes JSON
                                          ▼
                              ┌───────────────────────┐
                              │      HTTP Response        │
                              └───────────────────────┘
```

Key points:

- `DispatcherServlet` is registered automatically by Spring Boot's auto-configuration (`spring-boot-starter-web`).
- It delegates URL-to-method matching to `HandlerMapping` implementations.
- It delegates the actual invocation to `HandlerAdapter` implementations.
- For REST APIs, the "view resolving" step is replaced by `HttpMessageConverter`s that directly serialize the return value to JSON/XML.

```properties
# You can customize the DispatcherServlet's URL mapping (rarely needed)
spring.mvc.servlet.path=/api
```

## Controllers

A **controller** is a Java class annotated to tell Spring "this class handles incoming web requests." There are two main flavors:

- `@Controller`: for traditional apps that return view names (HTML pages).
- `@RestController`: for REST APIs that return data directly (covered in its own section below).

```java
@Controller
@RequestMapping("/books")
public class BookViewController {

    private final BookService bookService;

    public BookViewController(BookService bookService) {
        this.bookService = bookService;
    }

    @GetMapping("/{id}")
    public String bookDetails(@PathVariable Long id, Model model) {
        model.addAttribute("book", bookService.findById(id));
        return "book-details"; // resolves to templates/book-details.html
    }
}
```

Key points:

- Controllers should be **thin**. They receive input, delegate to a service layer, and return output. Business logic belongs in services, not controllers.
- Spring detects controllers via **component scanning** — `@Controller` is itself a specialization of `@Component`.
- Constructor injection (shown above) is preferred over field injection for testability.

## Request Mapping

`@RequestMapping` and its shortcuts tell Spring which URL (and HTTP method) a controller method should handle.

```java
@RestController
@RequestMapping("/api/v1/books") // shared base path for all methods below
public class BookController {

    @GetMapping             // GET /api/v1/books
    public List<Book> findAll() { ... }

    @GetMapping("/{id}")    // GET /api/v1/books/{id}
    public Book findById(@PathVariable Long id) { ... }

    @PostMapping            // POST /api/v1/books
    public Book create(@RequestBody Book book) { ... }

    @PutMapping("/{id}")    // PUT /api/v1/books/{id}
    public Book update(@PathVariable Long id, @RequestBody Book book) { ... }

    @DeleteMapping("/{id}") // DELETE /api/v1/books/{id}
    public void delete(@PathVariable Long id) { ... }
}
```

- `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping` are shorthand for `@RequestMapping(method = ...)`.
- You can also narrow mappings by headers, params, or media type:

```java
@GetMapping(path = "/{id}", produces = "application/json")
public Book findById(@PathVariable Long id) { ... }
```

| Annotation | HTTP Method | Typical Use |
|---|---|---|
| `@GetMapping` | GET | Fetch a resource |
| `@PostMapping` | POST | Create a resource |
| `@PutMapping` | PUT | Replace a resource entirely |
| `@PatchMapping` | PATCH | Partially update a resource |
| `@DeleteMapping` | DELETE | Remove a resource |

## Path Variables

**Path variables** are placeholders inside the URL path itself, like `/books/{id}`. They let a single mapping handle many different URLs, with the changing part extracted as a value.

```java
@GetMapping("/orders/{orderId}/items/{itemId}")
public Item getItem(@PathVariable Long orderId, @PathVariable Long itemId) {
    return orderService.getItem(orderId, itemId);
}
```

- If the parameter name matches the `{placeholder}` name exactly, you can omit the value: `@PathVariable Long orderId` matches `{orderId}`.
- If names differ, specify it explicitly: `@PathVariable("id") Long bookId`.
- Path variables are part of the URL structure — use them for **identifying a resource** (e.g. `/books/42`), not for optional or filtering data.

```java
@GetMapping("/users/{userId}")
public User getUser(@PathVariable("userId") String id) {
    return userService.findById(id);
}
```

## Request Parameters

**Request parameters** are the `?key=value` pairs appended to a URL's query string, or form fields in a POST body. Use them for optional data, filters, or pagination — things that don't identify a specific resource.

```java
// GET /books/search?title=Spring&page=0&size=20
@GetMapping("/books/search")
public List<Book> search(
        @RequestParam String title,
        @RequestParam(defaultValue = "0") int page,
        @RequestParam(defaultValue = "20") int size,
        @RequestParam(required = false) String author) {
    return bookService.search(title, author, page, size);
}
```

Key points:

- `required = false` (or providing a `defaultValue`) makes a parameter optional; otherwise Spring throws a `400 Bad Request` if it's missing.
- Use `Map<String, String>` with `@RequestParam` to grab all parameters dynamically: `@RequestParam Map<String, String> allParams`.
- Multi-value parameters (e.g. `?tag=java&tag=spring`) can be bound to a `List<String>`.

| Feature | Path Variable | Request Parameter |
|---|---|---|
| Location | Part of the URL path (`/books/5`) | Query string (`?id=5`) |
| Use case | Identify a specific resource | Filter, search, pagination, optional data |
| Required by default | Yes | Yes (unless marked optional) |

## Request Body

`@RequestBody` tells Spring to take the raw HTTP request body (usually JSON) and convert it into a Java object automatically, using a message converter (Jackson by default).

```java
public record CreateBookRequest(String title, String author, int pages) {}

@PostMapping("/books")
public Book createBook(@RequestBody CreateBookRequest request) {
    return bookService.create(request);
}
```

Example incoming JSON that gets deserialized into `CreateBookRequest`:

```json
{
  "title": "Effective Java",
  "author": "Joshua Bloch",
  "pages": 412
}
```

Key points:

- Only **one** `@RequestBody` parameter is allowed per method — the body can only be read once.
- Jackson (via `spring-boot-starter-web`) handles JSON ↔ Java conversion automatically; you don't write parsing code.
- Combine with `@Valid` to trigger bean validation on the incoming object (see Validation section).

## Response Body

`@ResponseBody` tells Spring: "don't treat this return value as a view name — serialize it directly into the HTTP response body" (typically as JSON).

```java
@Controller
public class BookApiController {

    @GetMapping("/books/{id}")
    @ResponseBody
    public Book getBook(@PathVariable Long id) {
        return bookService.findById(id); // written directly as JSON, not resolved as a view
    }
}
```

- Without `@ResponseBody`, Spring would try to resolve the returned `Book` object as a *view name*, which would fail or behave unexpectedly.
- `@RestController` (below) applies `@ResponseBody` to every method automatically, so you rarely write `@ResponseBody` explicitly in modern code.

## REST Controllers

`@RestController` is a convenience annotation that combines `@Controller` + `@ResponseBody`. Every method's return value is written straight to the HTTP response body — perfect for APIs that speak JSON.

```java
@RestController
@RequestMapping("/api/v1/books")
public class BookRestController {

    private final BookService bookService;

    public BookRestController(BookService bookService) {
        this.bookService = bookService;
    }

    @GetMapping
    public List<Book> getAllBooks() {
        return bookService.findAll();
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Book createBook(@Valid @RequestBody CreateBookRequest request) {
        return bookService.create(request);
    }
}
```

- Use `@RestController` for JSON/XML APIs — this is the standard choice for microservices and most modern backend work.
- Use plain `@Controller` when you need to render server-side HTML views.

| | `@Controller` | `@RestController` |
|---|---|---|
| Return value meaning | View name (unless `@ResponseBody` added) | Data written directly to response body |
| Typical use | Server-rendered HTML (Thymeleaf, JSP) | REST/JSON APIs |
| Equivalent to | — | `@Controller` + `@ResponseBody` |

## HTTP Methods

REST APIs are built around standard HTTP methods, each with a conventional meaning. Following these conventions is what makes an API "RESTful" and predictable to consumers.

| Method | Purpose | Idempotent? | Has Body? |
|---|---|---|---|
| GET | Read a resource | Yes | No |
| POST | Create a new resource | No | Yes |
| PUT | Replace a resource completely | Yes | Yes |
| PATCH | Partially update a resource | No (usually) | Yes |
| DELETE | Remove a resource | Yes | No (usually) |

**Idempotent** means calling it multiple times has the same effect as calling it once. Deleting the same resource twice still ends with it deleted — that's idempotent. Creating a resource twice with POST usually creates two resources — that's not idempotent.

```java
@RestController
@RequestMapping("/api/v1/orders")
public class OrderController {

    @PutMapping("/{id}")
    public Order replace(@PathVariable Long id, @RequestBody Order order) {
        return orderService.replace(id, order); // idempotent: same result if called twice
    }

    @PatchMapping("/{id}")
    public Order updateStatus(@PathVariable Long id, @RequestBody Map<String, String> updates) {
        return orderService.updateStatus(id, updates.get("status"));
    }
}
```

## ResponseEntity

`ResponseEntity<T>` gives you full control over the HTTP response: status code, headers, and body — all in one object. Plain return types (like `Book`) always return `200 OK`; `ResponseEntity` lets you return `201`, `404`, `204`, etc., based on logic.

```java
@RestController
@RequestMapping("/api/v1/books")
public class BookController {

    private final BookService bookService;

    public BookController(BookService bookService) {
        this.bookService = bookService;
    }

    @GetMapping("/{id}")
    public ResponseEntity<Book> getBook(@PathVariable Long id) {
        return bookService.findById(id)
                .map(ResponseEntity::ok)                       // 200 OK with body
                .orElse(ResponseEntity.notFound().build());     // 404 Not Found, no body
    }

    @PostMapping
    public ResponseEntity<Book> createBook(@Valid @RequestBody CreateBookRequest request) {
        Book created = bookService.create(request);
        URI location = URI.create("/api/v1/books/" + created.getId());
        return ResponseEntity.created(location).body(created); // 201 Created + Location header
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> deleteBook(@PathVariable Long id) {
        bookService.delete(id);
        return ResponseEntity.noContent().build(); // 204 No Content
    }
}
```

Key points:

- Prefer `ResponseEntity` whenever the status code should vary based on the outcome (found vs. not found, created vs. error).
- Common builder methods: `ResponseEntity.ok(body)`, `.created(uri)`, `.noContent()`, `.notFound()`, `.badRequest()`, `.status(HttpStatus.X)`.
- `ResponseEntity` works with any body type — a plain POJO, `List`, `Void`, or your custom API-error object.

## Content Negotiation

**Content negotiation** is how the client and server agree on the *format* of the response (JSON, XML, etc.) and the *language*. The client typically requests a format via the `Accept` header, and Spring picks a matching `HttpMessageConverter`.

```java
@GetMapping(value = "/books/{id}", produces = { MediaType.APPLICATION_JSON_VALUE, MediaType.APPLICATION_XML_VALUE })
public Book getBook(@PathVariable Long id) {
    return bookService.findById(id);
}
```

```bash
# Ask for JSON
curl -H "Accept: application/json" http://localhost:8080/books/1

# Ask for XML (requires an XML message converter, e.g. Jackson's XML module, on the classpath)
curl -H "Accept: application/xml" http://localhost:8080/books/1
```

Key points:

- By default, Spring Boot REST APIs negotiate based on the `Accept` header, defaulting to JSON if nothing else matches.
- `produces` on a mapping restricts which media types that endpoint can respond with.
- `consumes` restricts which media types an endpoint accepts as input:

```java
@PostMapping(value = "/books", consumes = MediaType.APPLICATION_JSON_VALUE)
public Book createBook(@RequestBody CreateBookRequest request) { ... }
```

## Validation

**Validation** checks that incoming data meets business rules (e.g. a field isn't blank, an email is well-formed) before your code processes it. Spring integrates with the **Bean Validation** (Jakarta Validation) standard via annotations on your DTO fields.

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-validation</artifactId>
</dependency>
```

```java
public record CreateBookRequest(

        @NotBlank(message = "Title is required")
        String title,

        @NotBlank
        @Size(max = 100, message = "Author name must be under 100 characters")
        String author,

        @Min(value = 1, message = "Pages must be at least 1")
        int pages,

        @Email
        String contactEmail
) {}
```

```java
@PostMapping("/books")
public ResponseEntity<Book> createBook(@Valid @RequestBody CreateBookRequest request) {
    Book book = bookService.create(request);
    return ResponseEntity.status(HttpStatus.CREATED).body(book);
}
```

- `@Valid` triggers validation on the annotated object. If validation fails, Spring throws `MethodArgumentNotValidException` (handled centrally — see Exception Handling).
- Common annotations: `@NotNull`, `@NotBlank`, `@NotEmpty`, `@Size`, `@Min`, `@Max`, `@Email`, `@Pattern`, `@Positive`.
- Validate `@PathVariable` / `@RequestParam` at the class level with `@Validated` (from `org.springframework.validation.annotation`) plus constraints directly on the parameter.

## BindingResult

`BindingResult` lets you capture validation errors **inside your controller method** instead of letting Spring throw an exception automatically. It must appear **immediately after** the `@Valid`/`@Validated` object in the method signature.

```java
@PostMapping("/books")
public ResponseEntity<?> createBook(@Valid @RequestBody CreateBookRequest request,
                                     BindingResult bindingResult) {
    if (bindingResult.hasErrors()) {
        List<String> errors = bindingResult.getFieldErrors().stream()
                .map(err -> err.getField() + ": " + err.getDefaultMessage())
                .toList();
        return ResponseEntity.badRequest().body(errors);
    }
    Book book = bookService.create(request);
    return ResponseEntity.status(HttpStatus.CREATED).body(book);
}
```

Key points:

- If `BindingResult` is present, Spring does **not** throw an exception on validation failure — it's your job to check `hasErrors()` and respond accordingly.
- If `BindingResult` is **absent**, Spring throws `MethodArgumentNotValidException`, which you typically handle globally with `@ExceptionHandler` / `@ControllerAdvice` (see below). This global approach is generally preferred for REST APIs — it keeps controllers clean.
- Order matters: `@Valid Object arg, BindingResult result` — swapping or separating them with another parameter breaks binding.

## Exception Handling

Rather than wrapping every controller method in try/catch, Spring MVC lets you define centralized exception handlers with `@ExceptionHandler`. This keeps controllers focused on the happy path.

```java
@RestController
@RequestMapping("/api/v1/books")
public class BookController {

    private final BookService bookService;

    public BookController(BookService bookService) {
        this.bookService = bookService;
    }

    @GetMapping("/{id}")
    public Book getBook(@PathVariable Long id) {
        return bookService.findById(id)
                .orElseThrow(() -> new BookNotFoundException(id)); // custom exception
    }

    @ExceptionHandler(BookNotFoundException.class)
    public ResponseEntity<ApiError> handleNotFound(BookNotFoundException ex) {
        ApiError error = new ApiError(HttpStatus.NOT_FOUND.value(), ex.getMessage());
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(error);
    }
}

record ApiError(int status, String message) {}
```

- `@ExceptionHandler` methods defined **inside** a controller only handle exceptions thrown from that controller.
- To handle exceptions across **all** controllers, move the handler into a `@ControllerAdvice` class (next section) — this is the recommended pattern for real applications.
- Spring Boot 3 also offers `ProblemDetail` (RFC 7807) as a standard error response shape:

```java
@ExceptionHandler(BookNotFoundException.class)
public ProblemDetail handleNotFound(BookNotFoundException ex) {
    ProblemDetail problem = ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, ex.getMessage());
    problem.setTitle("Book Not Found");
    return problem;
}
```

## Controller Advice

`@ControllerAdvice` (or `@RestControllerAdvice` for REST APIs) marks a class as a **global** handler that applies to every controller in the application. It's the standard place to centralize exception handling, avoiding repeated `@ExceptionHandler` methods scattered across controllers.

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BookNotFoundException.class)
    public ResponseEntity<ApiError> handleNotFound(BookNotFoundException ex) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new ApiError(404, ex.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, String>> handleValidation(MethodArgumentNotValidException ex) {
        Map<String, String> errors = new HashMap<>();
        ex.getBindingResult().getFieldErrors()
                .forEach(err -> errors.put(err.getField(), err.getDefaultMessage()));
        return ResponseEntity.badRequest().body(errors);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> handleGeneric(Exception ex) {
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(new ApiError(500, "Unexpected error occurred"));
    }
}
```

- `@RestControllerAdvice` = `@ControllerAdvice` + `@ResponseBody`, just like `@RestController` combines `@Controller` + `@ResponseBody`.
- You can scope advice to specific packages or annotations: `@ControllerAdvice(basePackages = "com.example.api")`.
- Order handlers from **most specific exception to most generic** — Spring picks the closest match, but a catch-all `Exception` handler is a good safety net.

## Message Converters

`HttpMessageConverter`s are the components that convert Java objects to HTTP response bodies (and request bodies back to Java objects). This is what makes `@RequestBody`/`@ResponseBody` "just work" without you writing JSON parsing code.

- **Deserialization**: JSON in the request body → Java object (used for `@RequestBody`).
- **Serialization**: Java object → JSON in the response body (used for `@ResponseBody` / `@RestController`).

```java
@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Override
    public void extendMessageConverters(List<HttpMessageConverter<?>> converters) {
        MappingJackson2HttpMessageConverter jsonConverter = new MappingJackson2HttpMessageConverter();
        ObjectMapper mapper = jsonConverter.getObjectMapper();
        mapper.registerModule(new JavaTimeModule()); // proper LocalDate/LocalDateTime handling
        converters.add(0, jsonConverter);
    }
}
```

Key points:

- `MappingJackson2HttpMessageConverter` (backed by Jackson) is the default for JSON — included automatically with `spring-boot-starter-web`.
- Other built-in converters handle `String`, byte arrays, forms (`application/x-www-form-urlencoded`), and more.
- You can register custom converters (e.g. for Protobuf or CSV) via `WebMvcConfigurer.extendMessageConverters()`.
- If the `Content-Type` of an incoming request doesn't match any converter, Spring responds with `415 Unsupported Media Type`.

## Interceptors

An **interceptor** is a Spring MVC-specific hook that runs code *before* and *after* a controller method executes (and after the view is rendered). Use interceptors for cross-cutting, MVC-aware concerns like logging request-handling time, checking custom headers, or auth checks that need access to the *handler* (the actual controller method being called).

```java
public class TimingInterceptor implements HandlerInterceptor {

    private static final Logger log = LoggerFactory.getLogger(TimingInterceptor.class);

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        request.setAttribute("startTime", System.currentTimeMillis());
        return true; // return false to stop the request from reaching the controller
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                                 Object handler, Exception ex) {
        long start = (long) request.getAttribute("startTime");
        log.info("{} took {}ms", request.getRequestURI(), System.currentTimeMillis() - start);
    }
}
```

```java
@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new TimingInterceptor())
                .addPathPatterns("/api/**")
                .excludePathPatterns("/api/health");
    }
}
```

- Interceptor lifecycle methods: `preHandle` (before controller), `postHandle` (after controller, before view rendering), `afterCompletion` (after the full request, even if an exception occurred).
- Interceptors are **Spring MVC-aware** — they know about the handler method, so you can inspect annotations on it (e.g. a custom `@RequiresRole` annotation).

## Filters

A **filter** is a Servlet-API construct (`jakarta.servlet.Filter`) that sits in front of the entire servlet container — it runs *before* Spring MVC even starts processing (before `DispatcherServlet`). Filters are framework-agnostic; they don't know anything about controllers or handler methods. Use them for low-level, technical concerns like CORS, authentication token checks, request/response logging, or compressing responses.

```java
@Component
@Order(1)
public class RequestLoggingFilter implements Filter {

    private static final Logger log = LoggerFactory.getLogger(RequestLoggingFilter.class);

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        HttpServletRequest httpRequest = (HttpServletRequest) request;
        long start = System.currentTimeMillis();

        chain.doFilter(request, response); // pass control to the next filter / servlet

        log.info("{} {} - {}ms", httpRequest.getMethod(), httpRequest.getRequestURI(),
                System.currentTimeMillis() - start);
    }
}
```

Registering a filter explicitly (if not using `@Component` + auto-detection):

```java
@Configuration
public class FilterConfig {

    @Bean
    public FilterRegistrationBean<RequestLoggingFilter> loggingFilter() {
        FilterRegistrationBean<RequestLoggingFilter> registrationBean = new FilterRegistrationBean<>();
        registrationBean.setFilter(new RequestLoggingFilter());
        registrationBean.addUrlPatterns("/api/*");
        registrationBean.setOrder(1);
        return registrationBean;
    }
}
```

### Interceptors vs. Filters

| Aspect | Filter | Interceptor |
|---|---|---|
| Defined by | Servlet API (`jakarta.servlet.Filter`) | Spring MVC (`HandlerInterceptor`) |
| Runs relative to `DispatcherServlet` | Before and after (wraps the whole servlet call) | Only within `DispatcherServlet`'s processing |
| Aware of the controller/handler method | No | Yes (can inspect the target method and its annotations) |
| Typical use cases | CORS, authentication, compression, raw request/response logging | Logging with handler context, auth tied to annotations, modifying the `Model` |
| Framework dependency | Works in any servlet-based app, not just Spring | Spring MVC-specific |
| Configuration | `FilterRegistrationBean` or `@Component` | `WebMvcConfigurer.addInterceptors()` |

- **Rule of thumb**: if the logic needs to know *which controller method* is about to run, use an interceptor. If it's purely about the raw HTTP request/response (security headers, CORS, gzip), use a filter.

## Common Code Review / Interview Pitfalls

- **Fat controllers with business logic inline.** Controllers should route and delegate, not contain business rules — this makes logic hard to test and reuse.
  ❌
  ```java
  @PostMapping("/orders")
  public Order create(@RequestBody Order order) {
      if (order.getItems().isEmpty()) throw new RuntimeException("empty");
      double total = order.getItems().stream().mapToDouble(Item::getPrice).sum();
      order.setTotal(total);
      return orderRepository.save(order);
  }
  ```
  ✅
  ```java
  @PostMapping("/orders")
  public Order create(@RequestBody CreateOrderRequest request) {
      return orderService.create(request); // logic lives in the service layer
  }
  ```

- **Returning raw entities instead of DTOs.** Exposing JPA entities directly leaks internal fields (like password hashes or lazy-loaded relations) and couples your API to your database schema. Fix: map entities to dedicated response DTOs.

- **Using `@RequestParam` when a value identifies a resource.** `/books?id=5` instead of `/books/5` breaks REST conventions and confuses API consumers. Fix: use `@PathVariable` for resource identifiers.

- **Missing `@Valid` on request bodies.** Without it, invalid data (blank titles, negative prices) reaches the service/database layer unchecked. Fix: annotate DTO fields with Bean Validation constraints and add `@Valid` in the controller.
  ❌ `public Book create(@RequestBody CreateBookRequest request)`
  ✅ `public Book create(@Valid @RequestBody CreateBookRequest request)`

- **Letting validation exceptions leak as raw 500 errors.** Without a global handler, `MethodArgumentNotValidException` can produce an ugly, inconsistent stack-trace response. Fix: handle it in a `@RestControllerAdvice` and return a clean `400` with field-level messages.

- **Scattering `try/catch` and `@ExceptionHandler` across every controller.** This duplicates error-formatting logic and produces inconsistent error responses across the API. Fix: centralize with a single `@RestControllerAdvice`.

- **Forgetting `BindingResult` must immediately follow the validated argument.** If another parameter sits between `@Valid Object` and `BindingResult`, Spring throws an error at startup or runtime instead of capturing validation errors. Fix: keep them adjacent: `(@Valid @RequestBody Foo foo, BindingResult result, ...)`.

- **Returning `200 OK` for every response regardless of outcome.** Always returning `200` (even for "not found" or "created") misleads API consumers and breaks HTTP semantics. Fix: use `ResponseEntity` with appropriate status codes (`201 Created`, `404 Not Found`, `204 No Content`).

- **Not distinguishing PUT and PATCH semantics.** Using PUT for partial updates (or vice versa) confuses clients about whether omitted fields get cleared or preserved. Fix: use PUT for full replacement, PATCH for partial updates, and document which fields are required for each.

- **Putting security/authorization logic only in a filter or only in an interceptor inconsistently.** Mixing the two without a clear rule leads to duplicated or missed checks. Fix: pick one layer per concern — typically Spring Security filters for authentication, and method-level or interceptor checks only for things that need handler context.

- **Ignoring content negotiation and hardcoding JSON assumptions.** Not setting `produces`/`consumes` can cause `406 Not Acceptable` or `415 Unsupported Media Type` errors that are hard to diagnose later. Fix: explicitly declare supported media types on endpoints that need it.

- **Catch-all exception handler that swallows details needed for debugging.** Returning only `"Unexpected error occurred"` with no logging makes production issues nearly impossible to diagnose. Fix: log the full exception server-side (with a correlation/trace ID) even while returning a generic message to the client.

- **Blocking, slow logic inside an `HandlerInterceptor.preHandle`.** Since interceptors run synchronously in the request thread, a slow call there (like an unindexed DB query) slows down every matching request. Fix: keep interceptor logic fast; push expensive work to async processing or caching.

- **Not setting an explicit `@Order` on multiple filters.** Unordered filters can run in an unpredictable sequence, causing subtle bugs (e.g., a logging filter running before an auth filter sets the security context). Fix: use `@Order` or `FilterRegistrationBean.setOrder()` to make the sequence explicit.

## Quick Recap

- **MVC** separates Model (data), View (presentation), Controller (routing/coordination).
- **DispatcherServlet** is the front controller: every request passes through it, which delegates to `HandlerMapping` and `HandlerAdapter`, then to your controller, then to a view or message converter.
- `@Controller` returns view names; `@RestController` (`@Controller` + `@ResponseBody`) returns data directly as JSON/XML.
- `@RequestMapping` (and shortcuts `@GetMapping`/`@PostMapping`/etc.) map URLs and HTTP methods to controller methods.
- **`@PathVariable`** identifies a specific resource in the URL (`/books/5`); **`@RequestParam`** is for optional filters/query data (`?title=Spring`).
- **`@RequestBody`** deserializes the incoming body into a Java object; **`@ResponseBody`** serializes the return value into the response body — both powered by `HttpMessageConverter`s (Jackson for JSON).
- **`ResponseEntity`** gives full control over status code, headers, and body — use it whenever the response varies by outcome.
- **Content negotiation** uses the `Accept`/`Content-Type` headers plus `produces`/`consumes` to pick the right format.
- **Validation** (`@Valid` + Bean Validation annotations) checks incoming data; **`BindingResult`** lets you handle validation errors manually instead of throwing an exception.
- **`@ExceptionHandler`** + **`@RestControllerAdvice`** centralize error handling across the whole application — the standard, clean way to manage errors in REST APIs.
- **Filters** are Servlet-API-level, run before/around the whole `DispatcherServlet`, and don't know about controllers — good for CORS, auth, logging.
- **Interceptors** are Spring MVC-specific, run within `DispatcherServlet` processing, and know about the target handler method — good for handler-aware cross-cutting logic.
- Keep controllers thin: parse input, delegate to services, shape the response — leave business logic out of the web layer.
