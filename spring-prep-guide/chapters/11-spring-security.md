# 11. Spring Security

## Overview

Spring Security is the framework that decides two things for every request that hits your application: "who are you?" (authentication) and "are you allowed to do this?" (authorization). It works by wrapping your application in a chain of servlet filters that inspect, and sometimes reject, requests before they ever reach your controllers. Since Spring Security 6 (which ships with Spring Boot 3), the configuration style changed completely: the old `WebSecurityConfigurerAdapter` class is gone, replaced by a functional, lambda-based DSL built around `SecurityFilterChain` beans. This chapter walks through the modern way of securing a Spring Boot application, from simple username/password login to JWT-based stateless APIs and OAuth2/OpenID Connect. Treat security as a series of small, well-understood building blocks — once you know how the filter chain works, everything else (CSRF, CORS, method security, JWTs) is just a filter or an interceptor plugged into that same pipeline.

## Security Fundamentals

Two words drive everything in this chapter:

- **Authentication** — proving who you are. Like showing your ID card at a building's front desk.
- **Authorization** — proving you're allowed to do something. Like your ID card only opening the doors of floors you have clearance for.

Spring Security models these with a small set of core abstractions:

| Concept | What it represents |
|---|---|
| `Authentication` | An object holding "who" made the request (principal), their credentials, and their granted authorities. |
| `SecurityContext` | A holder that carries the current `Authentication` for the duration of a request. |
| `SecurityContextHolder` | A static accessor to the `SecurityContext` (thread-local by default). |
| `GrantedAuthority` | A single permission/role string, e.g. `ROLE_ADMIN` or `SCOPE_read`. |
| `AuthenticationManager` | Takes an unauthenticated `Authentication` and returns an authenticated one, or throws. |
| `AuthenticationProvider` | Does the actual verification work (e.g. checks a password against a hash). |
| `UserDetailsService` | Loads user data (username, password hash, authorities) from storage. |

A minimal mental model:

```
Request → Filter Chain → (authenticate) → SecurityContext filled →
          (authorize) → Controller → Response
```

Everything downstream — filters, method security, CSRF, CORS — is built on top of these few objects. Once you can answer "where does the `Authentication` object come from, and what's inside it?" for any given setup, you understand that setup.

## Authentication

Authentication is the process of verifying identity — typically a username and password, but it could be a JWT, an API key, an OAuth2 token, or a client certificate. Spring Security supports many authentication *mechanisms* (how credentials arrive) and many authentication *sources* (where valid credentials are checked against).

The simplest example is form-based login with an in-memory user, useful for demos and tests:

```java
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public UserDetailsService userDetailsService(PasswordEncoder encoder) {
        UserDetails user = User.builder()
                .username("alice")
                .password(encoder.encode("password123"))
                .roles("USER")
                .build();
        return new InMemoryUserDetailsManager(user);
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .authorizeHttpRequests(auth -> auth
                .anyRequest().authenticated()
            )
            .formLogin(Customizer.withDefaults());
        return http.build();
    }
}
```

What happens on login:

1. The `UsernamePasswordAuthenticationFilter` intercepts the login POST.
2. It builds an unauthenticated `UsernamePasswordAuthenticationToken` and hands it to the `AuthenticationManager`.
3. A `DaoAuthenticationProvider` calls your `UserDetailsService`, then compares passwords using the `PasswordEncoder`.
4. On success, a fully-populated `Authentication` is stored in the `SecurityContext` (and, for session-based apps, in the HTTP session).

Common authentication mechanisms you'll meet in real projects:

- **Form login** — browser-based, session cookie afterward.
- **HTTP Basic** — credentials in the `Authorization` header, base64-encoded, no session; simple but rarely used for browsers.
- **Bearer token (JWT/OAuth2)** — the standard for stateless APIs and SPAs.
- **API keys** — a custom header checked by a custom filter.

```http
GET /api/orders HTTP/1.1
Host: api.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.abc123signature
```

## Authorization

Authorization decides, after you're known, what you're allowed to touch. Spring Security offers two layers of authorization:

- **URL-based** — decide access per HTTP endpoint, configured in the `SecurityFilterChain`.
- **Method-based** — decide access per Java method call, using annotations like `@PreAuthorize` (covered later in Method Security).

URL-based authorization uses `authorizeHttpRequests` with `requestMatchers`:

```java
@Bean
public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http
        .authorizeHttpRequests(auth -> auth
            .requestMatchers("/api/public/**").permitAll()
            .requestMatchers("/api/admin/**").hasRole("ADMIN")
            .requestMatchers(HttpMethod.POST, "/api/orders").hasAuthority("SCOPE_write")
            .requestMatchers("/api/orders/**").hasAnyRole("USER", "ADMIN")
            .anyRequest().authenticated()
        );
    return http.build();
}
```

Key rules to remember:

- Rules are evaluated **top to bottom**; the first matching `requestMatchers` wins. Put specific rules before general ones.
- `anyRequest()` should always be the **last** rule — it's a catch-all.
- `permitAll()` opens a path to everyone, including anonymous users — use it sparingly and deliberately.
- `denyAll()` blocks a path entirely; useful for explicitly closing off internal endpoints.

| Method | Meaning |
|---|---|
| `permitAll()` | No authentication needed |
| `denyAll()` | Always rejected |
| `authenticated()` | Any logged-in user |
| `hasRole("X")` | User must have authority `ROLE_X` |
| `hasAuthority("X")` | User must have the exact authority `X` |
| `hasAnyRole("A","B")` | User has `ROLE_A` or `ROLE_B` |

## Security Filter Chain

Spring Security is implemented as a chain of `javax.servlet.Filter`s (technically `jakarta.servlet.Filter` in Spring Boot 3), all wrapped inside one `FilterChainProxy` that the servlet container sees as a single filter. Each filter in the chain has one job — CSRF checking, authentication, exception translation, authorization — and passes the request along if it's satisfied.

```
                     ┌─────────────────────────────────────────┐
   HTTP Request  →   │           FilterChainProxy               │
                     │                                           │
                     │  1. SecurityContextHolderFilter           │
                     │  2. CorsFilter                            │
                     │  3. CsrfFilter                            │
                     │  4. UsernamePasswordAuthenticationFilter  │  ← form login
                     │     (or BearerTokenAuthenticationFilter,  │  ← JWT / OAuth2
                     │      BasicAuthenticationFilter, etc.)     │
                     │  5. ExceptionTranslationFilter            │
                     │  6. AuthorizationFilter                   │
                     │                                           │
                     └─────────────────────────────────────────┘
                                     ↓
                              DispatcherServlet → Controller
```

- **`SecurityContextHolderFilter`** loads/clears the `SecurityContext` for the request thread.
- **`CorsFilter`** handles cross-origin preflight and headers.
- **`CsrfFilter`** validates the anti-CSRF token on state-changing requests (if enabled).
- **Authentication filters** (one or more, depending on config) try to authenticate the request.
- **`ExceptionTranslationFilter`** catches `AuthenticationException`/`AccessDeniedException` and converts them into HTTP 401/403 responses or redirects.
- **`AuthorizationFilter`** (the modern replacement for `FilterSecurityInterceptor`) does the final `authorizeHttpRequests` check, normally the *last* filter in the chain.

You can register multiple `SecurityFilterChain` beans, each matched to different paths, giving you different rules for, say, `/api/**` (stateless JWT) versus everything else (session-based):

```java
@Bean
@Order(1)
public SecurityFilterChain apiFilterChain(HttpSecurity http) throws Exception {
    http
        .securityMatcher("/api/**")
        .csrf(csrf -> csrf.disable())
        .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
        .oauth2ResourceServer(oauth2 -> oauth2.jwt(Customizer.withDefaults()))
        .authorizeHttpRequests(auth -> auth.anyRequest().authenticated());
    return http.build();
}

@Bean
@Order(2)
public SecurityFilterChain webFilterChain(HttpSecurity http) throws Exception {
    http
        .authorizeHttpRequests(auth -> auth.anyRequest().authenticated())
        .formLogin(Customizer.withDefaults());
    return http.build();
}
```

Lower `@Order` value runs first; the first chain whose `securityMatcher` pattern matches the request "claims" it.

## Password Encoding

Never store or compare plaintext passwords. Spring Security's `PasswordEncoder` abstraction hashes passwords on registration and verifies them on login using a one-way, slow, salted hash function.

```java
@Bean
public PasswordEncoder passwordEncoder() {
    return new BCryptPasswordEncoder(); // strength defaults to 10
}
```

```java
@Service
public class RegistrationService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    public RegistrationService(UserRepository userRepository, PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
    }

    public void register(String username, String rawPassword) {
        String hash = passwordEncoder.encode(rawPassword);
        userRepository.save(new AppUser(username, hash));
    }
}
```

Verification is automatic inside `DaoAuthenticationProvider` — it calls `passwordEncoder.matches(rawPassword, storedHash)`. You should never manually compare hashes with `.equals()`.

| Encoder | Notes |
|---|---|
| `BCryptPasswordEncoder` | Industry standard default, adaptive cost, built-in salt. |
| `Argon2PasswordEncoder` | Winner of the Password Hashing Competition; memory-hard, good against GPU cracking. |
| `Pbkdf2PasswordEncoder` | FIPS-friendly, widely accepted in regulated environments. |
| `NoOpPasswordEncoder` | Plaintext — **for tests only, never production.** |
| `DelegatingPasswordEncoder` | The default returned by `PasswordEncoderFactories.createDelegatingPasswordEncoder()`; prefixes the hash with `{bcrypt}`, `{argon2}` etc. so multiple algorithms can coexist during migrations. |

Password storage checklist:

- Use `BCrypt` or `Argon2`, never MD5 or SHA-1/SHA-256 alone (too fast, no built-in salting semantics for passwords).
- Let the encoder generate the salt; don't roll your own.
- Increase cost factor as hardware gets faster; `BCryptPasswordEncoder` lets old hashes stay valid while new ones use a higher strength.

## UserDetailsService

`UserDetailsService` is the bridge between Spring Security and your actual user storage — a database, an LDAP directory, a legacy system, whatever. It has one method:

```java
public interface UserDetailsService {
    UserDetails loadUserByUsername(String username) throws UsernameNotFoundException;
}
```

A typical JPA-backed implementation:

```java
@Service
public class JpaUserDetailsService implements UserDetailsService {

    private final UserRepository userRepository;

    public JpaUserDetailsService(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    @Override
    public UserDetails loadUserByUsername(String username) {
        AppUser user = userRepository.findByUsername(username)
                .orElseThrow(() -> new UsernameNotFoundException("User not found"));

        return User.builder()
                .username(user.getUsername())
                .password(user.getPasswordHash())
                .authorities(user.getRoles().stream()
                        .map(role -> new SimpleGrantedAuthority("ROLE_" + role))
                        .toList())
                .accountLocked(!user.isActive())
                .build();
    }
}
```

Spring Security auto-wires this bean into a `DaoAuthenticationProvider` for you as long as it's the only `UserDetailsService` and `PasswordEncoder` bean present — no extra config needed for the simple case.

`UserDetails` also carries account status flags that `DaoAuthenticationProvider` checks automatically:

- `isEnabled()` — is the account active?
- `isAccountNonExpired()` — has the account expired?
- `isCredentialsNonExpired()` — does the password need a forced reset?
- `isAccountNonLocked()` — was the account locked (e.g. after failed attempts)?

If any return `false`, login fails with a specific `AccountStatusException`, even if the password was correct.

## JWT Authentication

JWT (JSON Web Token) is a compact, self-contained token format: a base64url-encoded header, payload ("claims"), and a cryptographic signature, separated by dots. Because the token carries its own claims and signature, a server can verify it **without a database lookup or server-side session** — this is what makes JWTs the backbone of stateless API authentication.

```
eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSIsInJvbGVzIjpbIlVTRVIiXSwiZXhwIjoxNzM2MjAwMDAwfQ.dQw4w9WgXcQ
└──────── header ────────┘ └──────────────── payload (claims) ─────────────────┘ └── signature ──┘
```

### Recommended approach: `oauth2ResourceServer` with JWT validation

Spring Security ships a resource-server module purpose-built for verifying JWTs (signature, expiry, issuer) without you writing any parsing code:

```java
@Bean
public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http
        .csrf(csrf -> csrf.disable())
        .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
        .authorizeHttpRequests(auth -> auth
            .requestMatchers("/api/public/**").permitAll()
            .anyRequest().authenticated()
        )
        .oauth2ResourceServer(oauth2 -> oauth2
            .jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthenticationConverter()))
        );
    return http.build();
}

@Bean
public JwtAuthenticationConverter jwtAuthenticationConverter() {
    JwtGrantedAuthoritiesConverter authoritiesConverter = new JwtGrantedAuthoritiesConverter();
    authoritiesConverter.setAuthorityPrefix("ROLE_");
    authoritiesConverter.setAuthoritiesClaimName("roles");

    JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
    converter.setJwtGrantedAuthoritiesConverter(authoritiesConverter);
    return converter;
}
```

```yaml
spring:
  security:
    oauth2:
      resourceserver:
        jwt:
          issuer-uri: https://auth.example.com/realms/myrealm
          # or, without an issuer/discovery endpoint:
          # jwk-set-uri: https://auth.example.com/.well-known/jwks.json
```

This does signature verification, expiry checking, issuer checking, and clock-skew handling for you, using keys fetched from the identity provider's JWK endpoint. This is the path you should use in production — it's battle-tested and handles edge cases (key rotation, algorithm confusion attacks) that are easy to get wrong by hand.

### Hand-rolled filter (for learning only)

Writing your own filter shows exactly what's happening under the hood, which is valuable for interviews, but it means you own every edge case: key rotation, algorithm allow-listing, clock skew, revocation.

```java
public class JwtAuthFilter extends OncePerRequestFilter {

    private final JwtDecoder jwtDecoder; // wraps a verified signing key

    public JwtAuthFilter(JwtDecoder jwtDecoder) {
        this.jwtDecoder = jwtDecoder;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                     HttpServletResponse response,
                                     FilterChain chain) throws ServletException, IOException {
        String header = request.getHeader(HttpHeaders.AUTHORIZATION);

        if (header != null && header.startsWith("Bearer ")) {
            try {
                Jwt jwt = jwtDecoder.decode(header.substring(7)); // verifies signature + expiry

                List<GrantedAuthority> authorities = ((List<?>) jwt.getClaims().getOrDefault("roles", List.of()))
                        .stream()
                        .map(role -> new SimpleGrantedAuthority("ROLE_" + role))
                        .collect(Collectors.toList());

                Authentication auth = new UsernamePasswordAuthenticationToken(
                        jwt.getSubject(), null, authorities);
                SecurityContextHolder.getContext().setAuthentication(auth);
            } catch (JwtException ex) {
                // invalid signature, expired token, etc. — leave context empty, request stays unauthenticated
            }
        }

        chain.doFilter(request, response);
    }
}
```

```java
http.addFilterBefore(jwtAuthFilter, UsernamePasswordAuthenticationFilter.class);
```

**Trade-off:** the hand-rolled filter is great for understanding the mechanics, but the built-in `oauth2ResourceServer(...).jwt(...)` support already handles JWK key rotation, algorithm confusion protection, and clock skew correctly — reinventing it is extra surface area for bugs and security holes. Use the built-in path in real projects; keep the hand-rolled version in your back pocket for interviews and debugging.

## OAuth2

OAuth2 is a *delegated authorization* protocol: it lets a user grant a third-party application limited access to their resources on another service, without ever handing over their password to that third-party app. Think of it like a hotel key card system — the front desk (authorization server) issues you a card (access token) that opens specific doors (scopes) for a limited time, without giving you the master key.

Core roles:

| Role | Example |
|---|---|
| Resource Owner | The end user |
| Client | Your application requesting access |
| Authorization Server | Issues tokens (e.g. Keycloak, Okta, Auth0, Google) |
| Resource Server | The API that holds the protected data and validates tokens |

The most common flow today is **Authorization Code with PKCE**:

```
1. User clicks "Login" in your app
2. App redirects browser to Authorization Server's /authorize endpoint
3. User logs in and consents
4. Authorization Server redirects back with an authorization code
5. Your app exchanges the code (+ PKCE verifier) for tokens at /token
6. Authorization Server returns: access_token, refresh_token, (id_token if OIDC)
```

Spring Boot's OAuth2 Client support configures this almost entirely through properties:

```yaml
spring:
  security:
    oauth2:
      client:
        registration:
          github:
            client-id: your-client-id
            client-secret: your-client-secret
            scope: read:user
        provider:
          github:
            authorization-uri: https://github.com/login/oauth/authorize
            token-uri: https://github.com/login/oauth/access_token
            user-info-uri: https://api.github.com/user
```

```java
@Bean
public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http
        .authorizeHttpRequests(auth -> auth.anyRequest().authenticated())
        .oauth2Login(Customizer.withDefaults()); // enables the "Login with GitHub" flow
    return http.build();
}
```

OAuth2 defines several **grant types** (ways of getting a token); know the shapes at a glance:

| Grant type | Use case |
|---|---|
| Authorization Code (+ PKCE) | Web/mobile apps with a user present |
| Client Credentials | Service-to-service, no user involved |
| Refresh Token | Getting a new access token without re-login |
| ~~Implicit~~ / ~~Password~~ | Deprecated — avoid in new designs |

## OpenID Connect

OAuth2 answers "what can this client access?" — it does not define a standard way to say "who is this user?" **OpenID Connect (OIDC)** is a thin identity layer built on top of OAuth2 that fills that gap. It adds:

- An **ID Token** — a JWT containing user identity claims (`sub`, `name`, `email`, etc.), separate from the access token.
- A standard `/userinfo` endpoint to fetch profile details.
- A discovery document (`/.well-known/openid-configuration`) so clients can auto-configure endpoints and keys.

```
Access Token  → "here is what you're allowed to do"      (authorization)
ID Token      → "here is who just logged in"             (authentication)
```

Spring Boot recognizes OIDC providers automatically via the discovery endpoint:

```yaml
spring:
  security:
    oauth2:
      client:
        provider:
          okta:
            issuer-uri: https://dev-1234.okta.com/oauth2/default
        registration:
          okta:
            client-id: your-client-id
            client-secret: your-client-secret
            scope: openid, profile, email
```

Because `scope: openid` is present, Spring Boot treats this as an OIDC login and automatically validates the ID token's signature, issuer, audience, and expiry, then exposes the claims as an `OidcUser`:

```java
@GetMapping("/me")
public String me(@AuthenticationPrincipal OidcUser oidcUser) {
    return "Hello " + oidcUser.getFullName() + " (" + oidcUser.getEmail() + ")";
}
```

| | OAuth2 | OpenID Connect |
|---|---|---|
| Purpose | Authorization (access) | Authentication (identity) |
| Token | Access token (opaque or JWT) | ID token (always a JWT) |
| Standard user info | Not defined | `/userinfo` endpoint + standard claims |
| Analogy | Key card for doors | Key card **and** photo ID badge |

## Method Security

URL-based rules only see the HTTP request; sometimes you want authorization decisions closer to your business logic — e.g. "only the account owner or an admin can view this record." Spring Security 6 enables this with `@EnableMethodSecurity` (the replacement for the old `@EnableGlobalMethodSecurity`) and annotations powered by Spring Expression Language (SpEL).

```java
@Configuration
@EnableMethodSecurity // defaults: prePostEnabled = true
public class MethodSecurityConfig {
}
```

```java
@Service
public class AccountService {

    @PreAuthorize("hasRole('ADMIN')")
    public void deleteAccount(Long accountId) {
        // only admins reach this line
    }

    @PreAuthorize("#username == authentication.name or hasRole('ADMIN')")
    public Account getAccount(String username) {
        return accountRepository.findByUsername(username);
    }

    @PostAuthorize("returnObject.owner == authentication.name")
    public Document loadDocument(Long id) {
        return documentRepository.findById(id).orElseThrow();
    }

    @PreFilter("filterObject.owner == authentication.name")
    public void archiveAll(List<Document> documents) { /* ... */ }

    @PostFilter("filterObject.owner == authentication.name")
    public List<Document> listAllDocuments() {
        return documentRepository.findAll();
    }
}
```

| Annotation | Evaluated | Typical use |
|---|---|---|
| `@PreAuthorize` | Before method runs | Block the call entirely (most common) |
| `@PostAuthorize` | After method runs, checks return value | "Can this user see *this specific* result?" |
| `@PreFilter` | Before, filters a collection argument | Trim an incoming list to allowed items |
| `@PostFilter` | After, filters a collection return value | Trim a returned list to allowed items |

Method security is implemented with Spring AOP proxies. That has one crucial consequence: **it only works through the proxy, i.e. on calls made *into* the bean from outside** — a private method calling another method on `this` bypasses the proxy and skips the check entirely (see the Pitfalls section).

## CSRF

Cross-Site Request Forgery (CSRF) tricks a logged-in user's browser into submitting a request the user never intended — e.g. a hidden auto-submitting form on a malicious site that POSTs to `your-bank.com/transfer` while the victim's session cookie rides along automatically. Spring Security protects against this by requiring a secret, per-session CSRF token on state-changing requests (POST/PUT/PATCH/DELETE), which an attacker's page cannot know or forge.

```java
@Bean
public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http
        .csrf(csrf -> csrf.csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse()))
        .authorizeHttpRequests(auth -> auth.anyRequest().authenticated());
    return http.build();
}
```

A form that includes the CSRF token (Thymeleaf auto-injects it):

```html
<form method="post" action="/transfer">
    <input type="hidden" name="_csrf" th:value="${_csrf.token}"/>
    <input type="text" name="amount"/>
    <button type="submit">Transfer</button>
</form>
```

### When you need it vs. when you don't

| Scenario | CSRF needed? | Why |
|---|---|---|
| Server-rendered app with cookie-based sessions | **Yes** | Browser sends the session cookie automatically on any request, forged or not. |
| SPA using cookie-based auth | **Yes** | Same reason — cookies are attached automatically by the browser. |
| Stateless REST API using `Authorization: Bearer <token>` | **No** (usually) | Tokens are attached manually by JS, not auto-sent by the browser, so a forged cross-site form can't include them. |
| Public, unauthenticated GET endpoints | **No** | CSRF only matters for state-changing, authenticated requests. |

```java
// Typical stateless JWT API — safe to disable, since there's no session cookie to hijack
http
    .csrf(csrf -> csrf.disable())
    .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
```

Disabling CSRF is only safe *because* the API is also stateless and uses header-based tokens instead of cookies — disabling it on a cookie/session-based app is a real vulnerability, not a shortcut (see Pitfalls).

## CORS

Cross-Origin Resource Sharing (CORS) is a **browser-enforced** rule that stops JavaScript on `site-a.com` from reading responses from `api-b.com` unless `api-b.com` explicitly allows it. It is not a server-side attack defense by itself — it protects users by restricting what browsers permit scripts to read, and the server declares the policy via response headers.

```java
@Bean
public CorsConfigurationSource corsConfigurationSource() {
    CorsConfiguration config = new CorsConfiguration();
    config.setAllowedOrigins(List.of("https://app.example.com"));
    config.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE"));
    config.setAllowedHeaders(List.of("Authorization", "Content-Type"));
    config.setAllowCredentials(true);

    UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
    source.registerCorsConfiguration("/api/**", config);
    return source;
}

@Bean
public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http
        .cors(cors -> cors.configurationSource(corsConfigurationSource()))
        .authorizeHttpRequests(auth -> auth.anyRequest().authenticated());
    return http.build();
}
```

**Dangerous combination — do not do this:**

```java
// ❌ Wildcard origin + credentials: browsers actually reject this combo,
// and if you work around it, you've built an open door for any website
// to make authenticated requests using the victim's cookies/tokens.
config.setAllowedOrigins(List.of("*"));
config.setAllowCredentials(true);
```

The CORS spec explicitly forbids `Access-Control-Allow-Origin: *` together with `Access-Control-Allow-Credentials: true` — browsers will block it. If you need credentials, list explicit origins:

```java
// ✅ explicit allow-list — credentials only shared with trusted origins
config.setAllowedOrigins(List.of("https://app.example.com", "https://admin.example.com"));
config.setAllowCredentials(true);
```

## Session Management

For server-rendered apps and traditional web logins, Spring Security tracks the authenticated user in an HTTP session, tied to the browser via a `JSESSIONID` cookie. For stateless APIs, you explicitly tell Spring Security never to create or use sessions.

```java
@Bean
public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http
        .sessionManagement(session -> session
            .sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED) // default
            .maximumSessions(1)                                      // one active session per user
            .maxSessionsPreventsLogin(false)                         // new login kicks out the old session
        );
    return http.build();
}
```

| Policy | Meaning |
|---|---|
| `ALWAYS` | Always create a session, even if not needed. |
| `IF_REQUIRED` | Create one only when needed (default, typical for form login). |
| `NEVER` | Use a session if one already exists, but never create a new one. |
| `STATELESS` | Never create or use a session at all — every request must self-authenticate (JWT/OAuth2 APIs). |

For a stateless JWT API, this is the standard pairing:

```java
http.sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
```

Session fixation protection (issuing a fresh session ID after login, so an attacker who fixed a pre-login session ID can't hijack the post-login session) is enabled by default — you rarely need to touch it, but know that it exists:

```java
http.sessionManagement(session -> session
    .sessionFixation(sessionFixation -> sessionFixation.migrateSession()) // default
);
```

## Security Headers

Spring Security automatically adds several defensive HTTP response headers out of the box. They don't replace authentication/authorization — they instruct the *browser* to behave more defensively (block content sniffing, refuse to be framed, etc.).

```java
@Bean
public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http
        .headers(headers -> headers
            .contentTypeOptions(Customizer.withDefaults())         // X-Content-Type-Options: nosniff
            .frameOptions(frame -> frame.deny())                   // X-Frame-Options: DENY
            .httpStrictTransportSecurity(hsts -> hsts
                .includeSubDomains(true)
                .maxAgeInSeconds(31536000)
            )
            .contentSecurityPolicy(csp -> csp
                .policyDirectives("default-src 'self'; frame-ancestors 'none'")
            )
        );
    return http.build();
}
```

| Header | Protects against |
|---|---|
| `X-Content-Type-Options: nosniff` | Browsers guessing content types (MIME sniffing attacks) |
| `X-Frame-Options: DENY` | Your page being embedded in an `<iframe>` (clickjacking) |
| `Strict-Transport-Security` | Downgrading HTTPS connections to HTTP |
| `Content-Security-Policy` | Injected scripts running from untrusted sources (XSS mitigation) |
| `Cache-Control` | Sensitive authenticated pages being cached by shared caches |

Most of these are **on by default** in Spring Security 6 for typical setups; you mainly need to reach for the `headers(...)` DSL when you want to *add* a Content-Security-Policy or *tighten* an existing default.

## Resource Server

A "resource server" is the API that actually holds protected data and is responsible for validating incoming tokens — as opposed to the authorization server, which issues them. In Spring, this is the `oauth2ResourceServer` DSL you've already seen for JWTs, but it's worth calling out as its own concept because interviewers often ask "what's the difference between a resource server and an authorization server?"

```java
@Bean
public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
    http
        .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
        .authorizeHttpRequests(auth -> auth
            .requestMatchers("/api/orders/**").hasAuthority("SCOPE_orders.read")
            .anyRequest().authenticated()
        )
        .oauth2ResourceServer(oauth2 -> oauth2
            .jwt(Customizer.withDefaults())
        );
    return http.build();
}
```

```yaml
spring:
  security:
    oauth2:
      resourceserver:
        jwt:
          issuer-uri: https://auth.example.com/realms/myrealm
```

Resource servers can also validate **opaque tokens** (a random string with no embedded claims) by asking the authorization server to introspect it on every request:

```yaml
spring:
  security:
    oauth2:
      resourceserver:
        opaquetoken:
          introspection-uri: https://auth.example.com/introspect
          client-id: resource-server-client
          client-secret: resource-server-secret
```

| | JWT validation | Opaque token introspection |
|---|---|---|
| Verification | Local — check signature with a cached public key | Remote — call the auth server on every request |
| Latency | Fast, no network call per request | Extra network round trip per request |
| Revocation | Hard — token is valid until it expires | Easy — auth server can say "no longer valid" immediately |

## Client Credentials

The **Client Credentials** grant is OAuth2's answer to service-to-service authentication — no human, no browser redirect, no consent screen. A backend service (the "client") authenticates directly with the authorization server using its own client ID and secret, and gets back an access token representing *itself*, not a user.

```yaml
spring:
  security:
    oauth2:
      client:
        registration:
          billing-service:
            provider: keycloak
            client-id: billing-service
            client-secret: super-secret-value
            authorization-grant-type: client_credentials
            scope: billing.read, billing.write
        provider:
          keycloak:
            token-uri: https://auth.example.com/realms/myrealm/protocol/openid-connect/token
```

Using `WebClient` with an OAuth2-aware filter to automatically attach a fresh token to outgoing calls:

```java
@Bean
public WebClient billingWebClient(OAuth2AuthorizedClientManager manager) {
    ServletOAuth2AuthorizedClientExchangeFilterFunction oauth2 =
            new ServletOAuth2AuthorizedClientExchangeFilterFunction(manager);
    oauth2.setDefaultClientRegistrationId("billing-service");

    return WebClient.builder()
            .apply(oauth2.oauth2Configuration())
            .baseUrl("https://billing.internal.example.com")
            .build();
}
```

Client credentials is the right tool when:

- A batch job needs to call another microservice's API.
- Two backend systems talk to each other with no user in the loop.
- You want short-lived, automatically-rotated service tokens instead of a static, forever-valid API key.

It is the wrong tool when a real user is involved — in that case use Authorization Code (+ PKCE) so the user actually authenticates and consents.

## Role vs Authority

This trips up almost everyone at least once. `GrantedAuthority` is the fundamental unit of "what you can do" — it's just a string, e.g. `ROLE_ADMIN`, `SCOPE_read`, `PERM_delete_invoice`. A "role" is simply a *convention*: an authority string prefixed with `ROLE_`.

```java
// Under the hood, a role is just an authority with a special prefix
new SimpleGrantedAuthority("ROLE_ADMIN"); // treated as role "ADMIN"
new SimpleGrantedAuthority("SCOPE_read"); // treated as a plain authority, NOT a role
```

`hasRole("ADMIN")` is sugar that automatically adds the `ROLE_` prefix before comparing. That means these two lines are equivalent:

```java
.requestMatchers("/admin/**").hasRole("ADMIN")          // Spring adds "ROLE_" for you
.requestMatchers("/admin/**").hasAuthority("ROLE_ADMIN") // you add it manually
```

| | `hasRole("ADMIN")` | `hasAuthority("ROLE_ADMIN")` |
|---|---|---|
| Prefix handling | Automatic — Spring prepends `ROLE_` | Manual — you must type `ROLE_` yourself |
| If you write `hasRole("ROLE_ADMIN")` | **Bug** — becomes `ROLE_ROLE_ADMIN`, never matches | N/A |
| If you write `hasAuthority("ADMIN")` (no prefix) | N/A | **Bug** — won't match an authority actually stored as `ROLE_ADMIN` |
| Use for | Coarse-grained roles (USER, ADMIN, MANAGER) | Fine-grained permissions/scopes (`orders.read`, `SCOPE_write`) |

Practical rule of thumb:

- Use `hasRole` / `hasAnyRole` for broad role-based checks — never type the `ROLE_` prefix yourself when using these.
- Use `hasAuthority` / `hasAnyAuthority` for fine-grained permissions or OAuth2 scopes (which conventionally use a `SCOPE_` prefix, not `ROLE_`).
- Whatever you choose, make sure the authorities loaded by your `UserDetailsService` or JWT converter actually use the prefix your authorization rules expect.

## Common Code Review / Interview Pitfalls

- ❌ **Disabling CSRF blindly, "because errors":**
  ```java
  http.csrf(csrf -> csrf.disable()); // done on a cookie-session web app
  ```
  Why it's a problem: removes forgery protection from an app that's actually vulnerable (uses cookies), just to silence a 403.
  ✅ Fix: only disable CSRF for genuinely stateless, token-based APIs; keep it enabled for session/cookie-based apps.

- ❌ **`permitAll()` on the wrong path pattern:**
  ```java
  .requestMatchers("/api/**").permitAll() // meant "/api/public/**"
  ```
  Why: accidentally exposes the entire API, including admin and user data endpoints, to anonymous users.
  ✅ Fix: scope `permitAll()` as narrowly as possible, and put it before broader authenticated rules, e.g. `/api/public/**`.

- ❌ **Plaintext or weak password hashing:**
  ```java
  return NoOpPasswordEncoder.getInstance(); // "just for now"
  ```
  Why: any database leak instantly exposes every user's real password.
  ✅ Fix: `new BCryptPasswordEncoder()` (or Argon2) in every environment, including local dev.

- ❌ **Secrets committed in `application.yml`:**
  ```yaml
  spring:
    security:
      oauth2:
        client:
          registration:
            github:
              client-secret: ghp_abc123realSecretHere
  ```
  Why: secrets end up in version control history forever, readable by anyone with repo access.
  ✅ Fix: externalize via environment variables or a secrets manager: `client-secret: ${GITHUB_CLIENT_SECRET}`.

- ❌ **JWT signature never actually verified:**
  ```java
  // parsing claims without checking the signature at all
  Jwt jwt = JwtHelper.decode(token); // some libs let you "decode" without "verify"
  ```
  Why: an attacker can hand-craft any token with any claims (e.g. `role: ADMIN`) and it will be trusted.
  ✅ Fix: always go through a verifying `JwtDecoder` / `oauth2ResourceServer().jwt(...)`, never a "decode-only" call path.

- ❌ **Accepting `"alg": "none"` or algorithm confusion:**
  ```json
  { "alg": "none", "typ": "JWT" }
  ```
  Why: some libraries historically allowed unsigned tokens or let an attacker switch a symmetric secret check into an asymmetric one, forging valid signatures.
  ✅ Fix: explicitly configure and pin the expected signing algorithm(s); rely on Spring's `NimbusJwtDecoder`, which rejects `none` and enforces the configured algorithm.

- ❌ **Tokens with no expiry, or absurdly long expiry:**
  ```java
  .claim("exp", Instant.now().plus(Duration.ofDays(3650)))
  ```
  Why: a stolen token stays valid essentially forever.
  ✅ Fix: short-lived access tokens (minutes to a couple hours) plus a refresh token flow for renewal.

- ❌ **Wildcard CORS origin combined with credentials:**
  ```java
  config.setAllowedOrigins(List.of("*"));
  config.setAllowCredentials(true);
  ```
  Why: (as covered above) opens authenticated endpoints to any website that can run JS in a victim's browser; browsers reject this combo, but relaxed/hand-rolled configs sometimes work around it dangerously.
  ✅ Fix: explicit origin allow-list when credentials are involved.

- ❌ **`@PreAuthorize` on a self-invoked method:**
  ```java
  @Service
  public class AccountService {
      public void handle(Long id) {
          deleteAccount(id); // internal call — bypasses the Spring AOP proxy!
      }

      @PreAuthorize("hasRole('ADMIN')")
      public void deleteAccount(Long id) { ... }
  }
  ```
  Why: method security is proxy-based; calling `this.deleteAccount(id)` from within the same bean never goes through the proxy, so the check silently never runs.
  ✅ Fix: move the secured method to a separate bean and call it through the injected proxy, or use `AopContext.currentProxy()` (self-injection is cleaner).

- ❌ **Exposing full entities/roles in JWTs or API responses:**
  ```java
  return jwtEncoder.encode(JwtEncoderParameters.from(JwsHeader.with(...).build(),
      JwtClaimsSet.builder().claim("user", entireUserEntity).build())); // includes password hash!
  ```
  Why: tokens are often stored client-side (localStorage, mobile storage) and can leak; bloated tokens also grow every request's header size.
  ✅ Fix: include only the minimal claims needed (`sub`, `roles`, `exp`) — never password hashes, internal IDs meant to stay private, or full entity graphs.

- ❌ **Missing HTTPS in production:**
  ```java
  // no redirect-to-HTTPS, no HSTS, tokens/cookies flow over plain HTTP
  ```
  Why: credentials, session cookies, and bearer tokens are trivially sniffable on the network.
  ✅ Fix: enforce HTTPS at the load balancer/gateway and enable HSTS: `http.headers(headers -> headers.httpStrictTransportSecurity(Customizer.withDefaults()));` plus `requiresChannel(channel -> channel.anyRequest().requiresSecure())` where applicable.

- ❌ **Verbose auth error messages that leak user existence:**
  ```java
  throw new BadCredentialsException("No user found with username: " + username);
  ```
  Why: lets an attacker enumerate valid usernames/emails by watching which error message comes back.
  ✅ Fix: return the same generic message ("Invalid username or password") regardless of whether the username exists or the password was wrong.

- ❌ **Wrong ordering of `requestMatchers` rules:**
  ```java
  .authorizeHttpRequests(auth -> auth
      .anyRequest().authenticated()          // catches everything first!
      .requestMatchers("/api/public/**").permitAll() // never reached
  )
  ```
  Why: rules are matched top-to-bottom; once `anyRequest()` matches, later, more specific rules are dead code.
  ✅ Fix: always order from most specific to least specific, with `anyRequest()` last.

- ❌ **Confusing `hasRole` and `hasAuthority` prefixes:**
  ```java
  .requestMatchers("/admin/**").hasRole("ROLE_ADMIN") // double prefix bug
  ```
  Why: `hasRole` already adds `ROLE_`, so this checks for `ROLE_ROLE_ADMIN`, which nothing has — access is silently always denied.
  ✅ Fix: `hasRole("ADMIN")` (no prefix) or `hasAuthority("ROLE_ADMIN")` (full prefix) — never both.

- ❌ **Storing JWTs in `localStorage` for browser apps:**
  ```javascript
  localStorage.setItem("token", accessToken); // readable by any injected script
  ```
  Why: any successful XSS on the page can read `localStorage` and steal the token outright.
  ✅ Fix: prefer `HttpOnly`, `Secure`, `SameSite=Strict` cookies for browser-based session-like tokens, or accept the XSS risk consciously with strong CSP as a mitigating control.

## Quick Recap

- **Authentication** = who you are; **authorization** = what you're allowed to do.
- Spring Security 6 uses `SecurityFilterChain` beans and the lambda DSL — `WebSecurityConfigurerAdapter` is gone.
- The request flows through a chain of filters: context loading → CORS → CSRF → authentication → exception translation → authorization (`AuthorizationFilter`), then reaches your controller.
- Use `authorizeHttpRequests` + `requestMatchers`, most-specific rule first, `anyRequest()` last.
- Always hash passwords with `BCryptPasswordEncoder` or `Argon2PasswordEncoder` — never plaintext, never `NoOpPasswordEncoder` outside tests.
- `UserDetailsService.loadUserByUsername(...)` is your one hook into custom user storage.
- For JWTs, prefer `oauth2ResourceServer(oauth2 -> oauth2.jwt(...))` over hand-rolled `OncePerRequestFilter` parsing in production; the hand-rolled version is for learning/debugging.
- OAuth2 handles delegated **authorization**; OpenID Connect adds standardized **authentication** (the ID token) on top.
- `@EnableMethodSecurity` + `@PreAuthorize`/`@PostAuthorize`/`@PreFilter`/`@PostFilter` secure individual methods — but only through the Spring AOP proxy, not self-invoked calls.
- CSRF protection matters for cookie/session-based apps; it's usually safe to disable for stateless, bearer-token APIs.
- CORS is a browser-enforced rule set by response headers — never combine `allowedOrigins("*")` with `allowCredentials(true)`.
- Use `SessionCreationPolicy.STATELESS` for token-based APIs; leave session management as-is for classic form login apps.
- Spring Security adds sane default security headers (`X-Frame-Options`, `X-Content-Type-Options`, HSTS); extend them with the `headers(...)` DSL when you need CSP.
- A resource server *validates* tokens; an authorization server *issues* them.
- Client Credentials grant is for service-to-service auth with no user involved — don't use it when a real human should be authenticating.
- `hasRole("X")` auto-prefixes with `ROLE_`; `hasAuthority("X")` requires the exact string, prefix included if there is one — don't mix the two conventions.
