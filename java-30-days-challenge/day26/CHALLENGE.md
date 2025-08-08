# Day 26: Security with Spring Security & JWT

| | |
|---|---|
| 🏗️ **Project** | **AuthGate** — a Spring Security + JWT secured API |
| ☕ **Java & language skills** | SecurityFilterChain config, JWT encode/decode, BCrypt, UserDetails, @PreAuthorize |
| 🧰 **Library / tool** | Spring Security + JWT (OAuth2 resource server) |
| 🗄️ **DB / distributed-systems concept** | AuthN vs AuthZ & stateless tokens for horizontal scale |
| 📊 **Difficulty** | Hard |

---

## Concept primer

### 1. The Spring Security filter chain

Spring Security is, at its core, **one `Filter`** (`FilterChainProxy`, registered in the servlet container under the name `springSecurityFilterChain`) that delegates to an *ordered list* of internal security filters. Every HTTP request passes through this chain **before** it ever reaches your `@RestController`.

```
HTTP request
   │
   ▼
[ Servlet container ]
   │
   ▼
DelegatingFilterProxy ──► FilterChainProxy ──► SecurityFilterChain
                                                  │
        ┌─────────────────────────────────────────┼─────────────────────────────┐
        ▼                 ▼                  ▼      ▼              ▼               ▼
  CorsFilter   ...  BearerTokenAuth   Authorization   ExceptionTranslation  ...  (more)
                    Filter (reads      Filter (checks                       
                    the JWT)           @PreAuthorize / rules)               
        │
        ▼ (only if every filter lets it through)
   DispatcherServlet ──► your OrderController
```

Each filter has one job. A few that matter today:

- **`CorsFilter`** — answers browser CORS preflight (`OPTIONS`) and stamps the right headers. Runs early.
- **`BearerTokenAuthenticationFilter`** — pulls the `Authorization: Bearer <jwt>` header, hands it to a `JwtDecoder`, and on success builds an `Authentication` and stores it in the `SecurityContextHolder`. This is **authentication**.
- **`AuthorizationFilter`** — once an `Authentication` exists, checks whether *this principal* is allowed to hit *this URL/method*. This is **authorization**.
- **`ExceptionTranslationFilter`** — turns a missing/failed authN into **401 Unauthorized**, and a present-but-insufficient authZ into **403 Forbidden**. Memorize that distinction; it shows up in every interview.

A `SecurityFilterChain` **bean** is how you, in modern Spring (6.x / Boot 3.x), declaratively assemble this chain. There is **no more `WebSecurityConfigurerAdapter`** — it was deprecated in 5.7 and deleted in 6.0. You return a bean from an `HttpSecurity` builder instead.

### 2. Authentication vs authorization

These are two separate questions, answered by two separate filters, and conflating them is the most common security bug there is.

| | Authentication (authN) | Authorization (authZ) |
|---|---|---|
| Question | *Who are you?* | *Are you allowed to do this?* |
| Input | credentials / a token | an already-authenticated principal + its authorities |
| Output | an `Authentication` in the `SecurityContext` | allow or deny |
| Failure status | **401 Unauthorized** | **403 Forbidden** |
| Today's mechanism | verify the JWT signature + expiry | `@PreAuthorize("hasRole('ADMIN')")` |

**401 means "I don't know who you are"** (no token, expired token, bad signature). **403 means "I know exactly who you are, and you may not do this"** (a `USER` calling a delete endpoint). Returning the wrong one leaks information and confuses clients.

### 3. Sessions vs stateless JWT — why JWT scales horizontally (callback to Day 16)

Classic web auth uses **server-side sessions**: on login the server creates a session object, stores it in memory, and hands the browser a `JSESSIONID` cookie. Every later request carries that opaque cookie; the server looks the session up to know who you are.

This is **exactly the local-state problem from Day 15/16**. On Day 16 your Caffeine cache lived *inside one JVM heap*, so a second instance had no idea what the first cached. A session is the same shape of bug: the session lives in *one* node's memory.

```
            ┌──────── load balancer ────────┐
            │              │                 │
        ┌───▼───┐      ┌───▼───┐         ┌───▼───┐
        │ node A│      │ node B│         │ node C│
        │sess123│      │  (none)│        │  (none)│   ← login hit A; B and C
        └───────┘      └────────┘        └────────┘     never saw session123
```

The user logs in on node A, the load balancer routes their next request to node B, and B doesn't have `session123` — they're logged out. Your options have always been ugly:

- **Sticky sessions** — pin a user to one node. Kills load balancing and dies when that node restarts.
- **Shared session store** — push sessions into Redis (Spring Session). This works (it's the Day 16 *shared cache* answer), but now **every authenticated request is a network round-trip to Redis**, and Redis is a new single point of failure and a scaling bottleneck.

**Stateless JWT** sidesteps the whole thing. The server holds **no** session. After login it hands the client a **signed token** that *carries the identity and roles inside it*. On each request the client sends the token; any node validates it by **checking the signature locally** — no shared store, no round-trip, no stickiness.

```
        ┌───▼───┐      ┌───▼───┐         ┌───▼───┐
        │ node A│      │ node B│         │ node C│   every node holds the SAME
        │ secret│      │ secret│         │ secret│   signing key → any node can
        └───────┘      └───────┘         └───────┘   verify the SAME token
```

The state moved **out of the cluster and into the token**. That's why JWT scales horizontally: adding a node costs nothing because nodes share no auth state — only the verification key. The trade-off (and it's a real one) is that you can no longer cheaply *revoke* a token, because there's nothing central to delete. We'll deal with that in "going deeper".

### 4. JWT structure & signature verification

A JWT is three base64url-encoded segments joined by dots: `header.payload.signature`.

```
eyJhbGciOiJIUzI1NiJ9 . eyJzdWIiOiJhbGljZSIsInJvbGVzIjpbIlJPTEVfQURNSU4iXSwiZXhwIjoxNzE4NTQ3MjAwfQ . 3Vq...sig
└──── header ───────┘  └─────────────────────── payload (claims) ────────────────────────────┘  └─ signature ─┘
```

- **Header** — the algorithm, e.g. `{"alg":"HS256","typ":"JWT"}`.
- **Payload (claims)** — JSON: `sub` (subject = who), `exp` (expiry, epoch seconds), `iat` (issued-at), `iss` (issuer), plus custom claims like our `roles`. **Base64 is encoding, not encryption** — anyone can decode and read the payload. This is the single most important JWT fact: **a JWT is signed, not secret.**
- **Signature** — `HMAC-SHA256( base64url(header) + "." + base64url(payload), secret )`. For `HS256` the same secret signs and verifies (symmetric).

**Verification** is: recompute the signature over the received `header.payload` using the secret, and constant-time-compare it to the received signature. If they match, the token is **authentic** (signed by someone holding the secret) and **untampered** (changing one byte of the payload changes the signature). Then check `exp` hasn't passed. No DB hit, no network. That local check is the entire scaling story.

### 5. BCrypt — how you store passwords

You **never** store a password, not even encrypted (encryption is reversible). You store a **one-way hash**, and not a fast one. BCrypt is purpose-built for passwords:

- **Salted** — a random salt is generated per password and baked into the output string, so two users with password `hunter2` get *different* hashes (defeats rainbow tables). You don't manage the salt; it's embedded.
- **Slow / adaptive** — a *work factor* (cost, default 10 → 2^10 rounds) makes each hash deliberately expensive. You tune the cost upward as hardware gets faster, so brute-forcing stays painful for decades.

A BCrypt hash is self-describing: `$2a$10$N9qo8uLOickgx2ZMRZoMyeIjZAgcfl7p92ldGxad68LJZdL17lhWy` → algorithm `$2a$`, cost `10`, then salt+hash. Spring's `BCryptPasswordEncoder.matches(raw, stored)` reads the cost and salt back out of the stored string and recomputes. **Login is: load the user, `passwordEncoder.matches(...)`, and only on success issue a JWT.**

### 6. Roles, scopes, and least privilege

- A **role** is a coarse identity grouping (`ROLE_ADMIN`, `ROLE_USER`). In Spring an authority prefixed `ROLE_` is a "role"; `hasRole('ADMIN')` matches authority `ROLE_ADMIN` (Spring adds the prefix for you).
- A **scope** is a fine-grained permission, classically OAuth2 (`orders:read`, `orders:write`). The resource server maps a `scope`/`scp` claim to `SCOPE_*` authorities.
- **Principle of least privilege** — every principal gets the *minimum* authority needed and nothing more. A normal customer can read and place orders; only an `ADMIN` can delete one. You enforce this declaratively with `@PreAuthorize` so the rule sits *next to the method it protects*.

### 7. JWT pitfalls — and the why behind each

- **Don't put secrets in the payload.** It's readable base64. No SSNs, no internal IDs you don't want leaked, no PII beyond what identity needs.
- **Revocation is hard.** A valid signed token is valid until `exp` — there's no central record to delete (that's the price of statelessness). Mitigation: **short-lived access tokens** (5–15 min) + a separate **refresh token**, and/or a **denylist** of revoked `jti`s in Redis (which reintroduces a lookup, but only for logout/ban, not every request).
- **Never accept `alg: none`.** Early JWT libraries let an attacker set the algorithm to `none` and ship an unsigned token. Spring's Nimbus decoder rejects this, but know the attack.
- **HS256 vs RS256.** `HS256` is symmetric — *every* service that verifies must hold the *signing* secret, so any of them could also *forge* tokens. `RS256` is asymmetric — the auth server signs with a **private** key and resource servers verify with the **public** key, so they can verify without being able to forge. For one app HS256 is fine; for many services, prefer RS256. We use HS256 today for simplicity and call out the upgrade.
- **Validate `exp`, `iss`, `aud`.** A token issued for a different audience or issuer must be rejected.
- **Don't store JWTs in `localStorage` for browser apps** (XSS can steal them); prefer `HttpOnly` cookies or in-memory. For service-to-service (our curl world) the `Authorization` header is correct.

---

## Prerequisites

You're continuing the orders Spring Boot app from **Days 10–14** (JPA `Order` entity, `OrderRepository`, `OrderController`, Bean Validation). You need JDK 17+, Maven, and the running app.

### Maven dependencies

Add to `pom.xml`:

```xml
<dependencies>
    <!-- existing: spring-boot-starter-web, -data-jpa, -validation, flyway, the DB driver ... -->

    <!-- The filter chain, BCrypt, UserDetails, @PreAuthorize -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-security</artifactId>
    </dependency>

    <!-- JwtDecoder/JwtEncoder, BearerTokenAuthenticationFilter (Nimbus comes transitively) -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-oauth2-resource-server</artifactId>
    </dependency>

    <!-- Test support for security (optional but recommended) -->
    <dependency>
        <groupId>org.springframework.security</groupId>
        <artifactId>spring-security-test</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>
```

> The instant you add `spring-boot-starter-security`, Spring Boot **locks down every endpoint** and prints a generated password to the console. That's the auto-config "secure by default." We're about to replace it with our own chain.

### Key / secret config

For HS256 we need a symmetric secret. **Never hard-code it; never commit it.** Put it in config and override from an environment variable in production.

`src/main/resources/application.yml`:

```yaml
app:
  jwt:
    # MUST be >= 256 bits (32 bytes) for HS256. Generate one, don't reuse this.
    #   openssl rand -base64 48
    secret: ${JWT_SECRET:Zr8kP2sV5xQ9wL3nT6yB1cF4hJ7mD0aE8gK2uR5oI9tW3zX6vN1qS4dG7bM0pC2}
    # access-token lifetime
    ttl-minutes: 15
```

---

## 🛠️ Project Walkthrough — AuthGate

Roll up your sleeves: from here you build the security layer hands-on, step by step, then exercise it end-to-end with `curl`.

## Step-by-step

### Step 1 — Users with BCrypt-hashed passwords

We'll keep it focused: a tiny `AppUser` entity and an in-memory bootstrap of two users. (In a real app these live in a `users` table with a Flyway migration from Day 13.)

```java
package com.example.orders.security;

import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Configuration
public class UserConfig {

    /** BCrypt: salted, slow, adaptive. Cost 10 by default; bump for prod hardware. */
    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder(); // == new BCryptPasswordEncoder(10)
    }

    /**
     * Two demo users. Passwords are stored ONLY as BCrypt hashes — note we encode
     * at bootstrap; in real life you persist the hash and never see the raw value again.
     */
    @Bean
    public UserDetailsService userDetailsService(PasswordEncoder encoder) {
        Map<String, UserDetails> store = new ConcurrentHashMap<>();

        store.put("alice", User.withUsername("alice")
                .password(encoder.encode("alice-pw"))
                .authorities(authorities("ROLE_ADMIN", "ROLE_USER"))
                .build());

        store.put("bob", User.withUsername("bob")
                .password(encoder.encode("bob-pw"))
                .authorities(authorities("ROLE_USER"))
                .build());

        return username -> {
            UserDetails u = store.get(username);
            if (u == null) throw new UsernameNotFoundException(username);
            return u;
        };
    }

    private static List<GrantedAuthority> authorities(String... roles) {
        return java.util.Arrays.stream(roles)
                .map(SimpleGrantedAuthority::new)
                .map(a -> (GrantedAuthority) a)
                .toList();
    }
}
```

Key points: **alice** is `ADMIN` + `USER`; **bob** is only `USER`. Passwords exist only as BCrypt hashes in memory.

### Step 2 — The JWT encoder & decoder (HS256, shared secret)

These two beans are the whole crypto surface. `JwtEncoder` mints tokens at login; `JwtDecoder` verifies them on every protected request (it's what the resource server's filter calls).

```java
package com.example.orders.security;

import com.nimbusds.jose.jwk.source.ImmutableSecret;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.*;

import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;

@Configuration
public class JwtConfig {

    private final byte[] secret;

    public JwtConfig(@Value("${app.jwt.secret}") String secret) {
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
    }

    private SecretKeySpec key() {
        // "HmacSHA256" matches HS256
        return new SecretKeySpec(secret, "HmacSHA256");
    }

    /** Signs new tokens at /login. */
    @Bean
    public JwtEncoder jwtEncoder() {
        return new NimbusJwtEncoder(new ImmutableSecret<>(key()));
    }

    /**
     * Verifies the signature + standard claims on every protected request.
     * This is the bean the BearerTokenAuthenticationFilter uses.
     */
    @Bean
    public JwtDecoder jwtDecoder() {
        return NimbusJwtDecoder.withSecretKey(key())
                .macAlgorithm(MacAlgorithm.HS256)
                .build();
        // The decoder validates the signature and `exp` automatically.
    }
}
```

> To swap to **RS256** later you'd generate an RSA keypair, build the encoder from the private JWK and the decoder with `NimbusJwtDecoder.withPublicKey(...)`. The rest of the app is unchanged — that's the point of using the standard resource-server abstraction.

### Step 3 — The `SecurityFilterChain` bean (stateless, JWT resource server)

This is the heart of the day. We assemble the chain: stateless, CSRF off (API), CORS on, public `/auth/login`, everything else authenticated, and the **OAuth2 resource server with JWT** wired in. We also map our custom `roles` claim to Spring authorities.

```java
package com.example.orders.security;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import java.util.List;

@Configuration
@EnableMethodSecurity   // turns on @PreAuthorize / @PostAuthorize
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            // CORS: API is called from browsers on other origins; configure explicitly (see bean below)
            .cors(Customizer.withDefaults())

            // CSRF protects browser FORM/cookie flows. We are a STATELESS token API:
            // no session cookie to ride, so there's no CSRF vector to protect — disable it.
            // (If you ever store the JWT in a cookie, you must turn this back on.)
            .csrf(csrf -> csrf.disable())

            // No HttpSession at all. The identity lives in the token, not the server.
            .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))

            .authorizeHttpRequests(auth -> auth
                // login must be reachable WITHOUT a token
                .requestMatchers(HttpMethod.POST, "/auth/login").permitAll()
                // allow CORS preflight through
                .requestMatchers(HttpMethod.OPTIONS, "/**").permitAll()
                // everything else needs a valid JWT
                .anyRequest().authenticated()
            )

            // Turn this app into an OAuth2 resource server that trusts our JWTs.
            // This installs the BearerTokenAuthenticationFilter, which uses the JwtDecoder bean.
            .oauth2ResourceServer(oauth2 -> oauth2
                .jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthConverter()))
            );

        return http.build();
    }

    /**
     * By default the resource server maps the `scope`/`scp` claim to SCOPE_* authorities.
     * We issue a custom `roles` claim (a list like ["ROLE_ADMIN","ROLE_USER"]), so we teach
     * the converter to read it verbatim (no prefix added).
     */
    private JwtAuthenticationConverter jwtAuthConverter() {
        JwtGrantedAuthoritiesConverter authorities = new JwtGrantedAuthoritiesConverter();
        authorities.setAuthoritiesClaimName("roles");
        authorities.setAuthorityPrefix("");   // our claim already contains "ROLE_..."

        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(authorities);
        return converter;
    }

    /** Explicit CORS for a real SPA origin. Tighten this in production. */
    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration cfg = new CorsConfiguration();
        cfg.setAllowedOrigins(List.of("http://localhost:3000"));
        cfg.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
        cfg.setAllowedHeaders(List.of("Authorization", "Content-Type"));
        cfg.setAllowCredentials(true);

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", cfg);
        return source;
    }
}
```

A note on **CORS vs authorization** — they answer different questions. CORS is a *browser* mechanism that decides whether JavaScript on origin X is *allowed by the browser* to read a response from origin Y. It is **not** a server-side access control; `curl` ignores CORS entirely. Authorization (`@PreAuthorize`, the rules above) is the real gate.

### Step 4 — The login / token-issuing endpoint

`/auth/login` is `permitAll`. It authenticates the credentials against `UserDetailsService` + BCrypt, and on success mints a signed JWT carrying `sub` and `roles`.

```java
package com.example.orders.security;

import jakarta.validation.constraints.NotBlank;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import static org.springframework.http.HttpStatus.UNAUTHORIZED;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;

@RestController
@RequestMapping("/auth")
public class AuthController {

    private final UserDetailsService users;
    private final PasswordEncoder encoder;
    private final JwtEncoder jwtEncoder;
    private final long ttlMinutes;

    public AuthController(UserDetailsService users,
                          PasswordEncoder encoder,
                          JwtEncoder jwtEncoder,
                          @org.springframework.beans.factory.annotation.Value("${app.jwt.ttl-minutes}") long ttlMinutes) {
        this.users = users;
        this.encoder = encoder;
        this.jwtEncoder = jwtEncoder;
        this.ttlMinutes = ttlMinutes;
    }

    public record LoginRequest(@NotBlank String username, @NotBlank String password) {}
    public record TokenResponse(String accessToken, String tokenType, long expiresInSeconds) {}

    @PostMapping("/login")
    public ResponseEntity<TokenResponse> login(@org.springframework.validation.annotation.Validated
                                               @RequestBody LoginRequest req) {
        UserDetails user;
        try {
            user = users.loadUserByUsername(req.username());
        } catch (UsernameNotFoundException e) {
            // Same 401 for unknown user and bad password — don't reveal which.
            throw new ResponseStatusException(UNAUTHORIZED, "Bad credentials");
        }
        if (!encoder.matches(req.password(), user.getPassword())) {
            throw new ResponseStatusException(UNAUTHORIZED, "Bad credentials");
        }

        Instant now = Instant.now();
        long ttlSeconds = ttlMinutes * 60;

        List<String> roles = user.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)   // e.g. "ROLE_ADMIN"
                .toList();

        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer("orders-service")
                .issuedAt(now)
                .expiresAt(now.plus(ttlMinutes, ChronoUnit.MINUTES))
                .subject(user.getUsername())
                .claim("roles", roles)
                .build();

        JwsHeader header = JwsHeader.with(org.springframework.security.oauth2.jose.jws.MacAlgorithm.HS256).build();
        String token = jwtEncoder.encode(JwtEncoderParameters.from(header, claims)).getTokenValue();

        return ResponseEntity.ok(new TokenResponse(token, "Bearer", ttlSeconds));
    }
}
```

Notes: identical 401 for "no such user" and "wrong password" (don't leak which accounts exist); `roles` is a plain list claim the resource server converter reads back; `exp` is set from the configured TTL.

### Step 5 — Enforce roles on the orders controller with `@PreAuthorize`

Now protect the existing `OrderController` from Day 10. Reads/creates need any authenticated user; **delete is ADMIN-only** — least privilege in one annotation.

```java
package com.example.orders.web;

import com.example.orders.domain.Order;
import com.example.orders.service.OrderService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

import java.net.URI;
import java.util.List;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService service;

    public OrderController(OrderService service) {
        this.service = service;
    }

    // Any authenticated principal (USER or ADMIN). Authenticated-only is already
    // enforced by the filter chain; this is explicit and self-documenting.
    @GetMapping
    @PreAuthorize("hasAnyRole('USER','ADMIN')")
    public List<Order> list() {
        return service.findAll();
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAnyRole('USER','ADMIN')")
    public Order get(@PathVariable long id) {
        return service.findById(id);
    }

    // A normal user may place an order. `jwt.getSubject()` is the username from `sub`.
    @PostMapping
    @PreAuthorize("hasRole('USER')")
    public ResponseEntity<Order> create(@Valid @RequestBody Order order,
                                        @AuthenticationPrincipal Jwt jwt) {
        Order saved = service.create(order, jwt.getSubject());
        return ResponseEntity.created(URI.create("/orders/" + saved.getId())).body(saved);
    }

    // ADMIN only. A USER hitting this gets 403, enforced BEFORE the method body runs.
    @DeleteMapping("/{id}")
    @PreAuthorize("hasRole('ADMIN')")
    public ResponseEntity<Void> delete(@PathVariable long id) {
        service.delete(id);
        return ResponseEntity.noContent().build();
    }
}
```

`@PreAuthorize` is evaluated by a method-security AOP interceptor **before** the method runs, using the `Authentication` the JWT filter already placed in the `SecurityContext`. `hasRole('ADMIN')` checks for authority `ROLE_ADMIN` — which is exactly what we packed into the `roles` claim and the converter restored. `@AuthenticationPrincipal Jwt jwt` injects the decoded token, so the body can attribute the order to `jwt.getSubject()`.

---

## Run it — curl walkthrough

Start the app. Then:

### 1. Log in as bob (USER) → get a token

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"bob","password":"bob-pw"}' | sed -E 's/.*"accessToken":"([^"]+)".*/\1/')
echo "$TOKEN"
```

Expected: a `200 OK` with a three-segment `eyJ...` string. Paste it into [jwt.io](https://jwt.io) and watch it decode `sub: bob`, `roles: ["ROLE_USER"]`, `exp`. (Notice you can read it **without** the secret — signed, not secret.)

### 2. Call a protected endpoint with the token → 200

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/orders \
  -H "Authorization: Bearer $TOKEN"
# → 200
```

### 3. Call it with NO token → 401

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/orders
# → 401   (authentication missing — "I don't know who you are")
```

### 4. bob (USER) tries to DELETE → 403 Forbidden

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE http://localhost:8080/orders/1 \
  -H "Authorization: Bearer $TOKEN"
# → 403   (authenticated, but not authorized — "I know you, you may not do this")
```

### 5. Log in as alice (ADMIN) and DELETE → 204

```bash
ADMIN=$(curl -s -X POST http://localhost:8080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"alice-pw"}' | sed -E 's/.*"accessToken":"([^"]+)".*/\1/')

curl -s -o /dev/null -w "%{http_code}\n" -X DELETE http://localhost:8080/orders/1 \
  -H "Authorization: Bearer $ADMIN"
# → 204 No Content   (ADMIN may delete)
```

### 6. Tamper test — flip one character in the token → 401

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/orders \
  -H "Authorization: Bearer ${TOKEN}x"
# → 401   (signature verification fails; the byte you changed breaks the HMAC)
```

### 7. Expiry test

Set `app.jwt.ttl-minutes: 0` (or a few seconds via a custom value), log in, wait past `exp`, and reuse the token: you get **401** — the decoder rejects an expired token without any server-side state.

**Summary of expected status codes**

| Scenario | Status |
|---|---|
| Valid login | 200 |
| Wrong/unknown credentials | 401 |
| Protected call, valid token | 200 |
| Protected call, no token | 401 |
| Protected call, tampered/expired token | 401 |
| USER calls ADMIN-only DELETE | 403 |
| ADMIN calls DELETE | 204 |

---

## 🚀 Going Deeper & Next Steps

## Going deeper / senior-level notes

- **Refresh tokens.** Keep access tokens short (5–15 min) so a leaked one expires fast. Issue a long-lived **refresh token** (opaque, *stored server-side* so it can be revoked) that the client exchanges for a new access token at `/auth/refresh`. This re-introduces a controlled bit of server state, but only on refresh, not on every request — you keep the scaling win and regain revocability.

- **Token revocation / denylist.** Pure JWT has no logout. To force-invalidate (logout, ban, password change), keep a **denylist** of revoked token IDs (`jti`) in **Redis** (Day 16) with a TTL equal to the token's remaining lifetime; the resource server checks it. This is a deliberate trade: you give back a little statelessness to gain revocation. Alternatively, bump a per-user `tokenVersion` and embed it as a claim.

- **HS256 vs RS256, again.** With many services, HS256's shared secret means *any* service can forge tokens — a blast-radius problem. **RS256** (or `ES256`) lets a single auth server hold the **private** signing key while every resource server verifies with the **public** key (often fetched from a **JWKS** endpoint, `/.well-known/jwks.json`, with automatic key rotation). Spring's `NimbusJwtDecoder.withJwkSetUri(...)` does exactly this.

- **OAuth2 / OIDC for real.** In production you usually don't hand-roll `/auth/login`. You delegate to an **identity provider** (Keycloak, Auth0, Okta, Entra ID, Cognito). Your app stays a *resource server* — you keep almost exactly the `oauth2ResourceServer` config from Step 3 and just point `spring.security.oauth2.resourceserver.jwt.issuer-uri` at the IdP. **OIDC** adds an identity layer (the `id_token`) on top of OAuth2's authorization layer.

- **mTLS for service-to-service.** Between internal services, **mutual TLS** authenticates both ends at the transport layer with client certs — often combined with JWTs (mTLS proves the *service* identity, the JWT carries the *user/scope*). Service meshes (Istio, Linkerd) automate this.

- **Secret rotation.** A symmetric HS256 secret (or RSA keypair) must be rotatable without downtime. Support **multiple valid verification keys** at once (key by `kid` in the JWT header) so you can roll: introduce a new key for *signing*, keep accepting the old one for *verification* until all old tokens expire, then retire it. JWKS endpoints make this routine; a single hard-coded secret makes it an outage.

- **Defense in depth.** The filter chain rules and `@PreAuthorize` are belt-and-suspenders on purpose — URL-level rules catch the broad strokes, method security guards the actual business operation even if a future controller refactor changes the URL mapping.

---

## Stretch goals

1. **Add a refresh-token flow.** Issue an opaque refresh token at login (store its hash in a `refresh_tokens` table via a Flyway migration), add `POST /auth/refresh` that validates it and mints a fresh access token, and a `POST /auth/logout` that deletes it. Prove that logout actually stops new access tokens from being issued.
2. **Switch HS256 → RS256.** Generate an RSA keypair, build the encoder from the private key and the decoder from the public key, and confirm the curl walkthrough still passes unchanged. Then expose a `/.well-known/jwks.json` so a *second* service could verify your tokens without sharing the private key.
3. **Redis denylist for revocation (ties to Day 16).** Add a `jti` claim, a `POST /auth/revoke/{jti}` admin endpoint that writes the `jti` to Redis with a TTL = remaining token life, and a small custom validator (`OAuth2TokenValidator<Jwt>`) that rejects denylisted tokens. Measure the per-request cost you just added.
4. **Scope-based authorization.** Add an `orders:read` / `orders:write` scope claim alongside roles, switch `@PreAuthorize` to `hasAuthority('SCOPE_orders:write')`, and discuss when fine-grained scopes beat coarse roles.

---

## Day 27 teaser

You can now prove *who* a caller is and *what* they may do — but a perfectly authorized client can still hammer you into the ground. **Day 27: Rate Limiting.** We'll meter requests per-principal (using the very JWT subject you just wired up) with token-bucket / leaky-bucket algorithms, back the counters in Redis so the limit holds *across all instances* (the Day 16 shared-state lesson, again), return proper `429 Too Many Requests` with `Retry-After`, and discuss fairness, bursting, and protecting that `/auth/login` endpoint from brute force.
