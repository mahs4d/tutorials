# 18. Networking & Security

Almost every real application talks to something over a network — a database, another service, a browser, a file on another machine. This chapter covers Java's networking building blocks, from raw sockets to the modern HTTP Client, then moves into running external processes safely, and finishes with security fundamentals and cryptography. Security bugs are some of the most expensive bugs a reviewer can miss, so this chapter leans heavily on **anti-pattern → fix** pairs: code that looks fine but is exploitable, next to the version that isn't. We target Java 21+ throughout.

## Table of Contents

- [Networking (java.net)](#networking-javanet)
- [HTTP Client API](#http-client-api)
- [Process API](#process-api)
- [Security Basics](#security-basics)
- [Cryptography APIs](#cryptography-apis)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## Networking (java.net)

The `java.net` package is Java's low-level toolkit for network communication. It has been in the JDK since Java 1.0, so a lot of application code still uses it directly, even though higher-level APIs (like the HTTP Client, covered next) are usually a better choice for talking HTTP.

### InetAddress — representing a host

An **IP address** is a numeric address for a machine on a network (like `192.0.2.1`). `InetAddress` represents one, and can resolve a **hostname** (a human-readable name like `example.com`) into an IP address via **DNS** (Domain Name System — the internet's phonebook that maps names to addresses).

```java
import java.net.InetAddress;
import java.net.UnknownHostException;

public class HostLookup {
    public static void main(String[] args) throws UnknownHostException {
        InetAddress address = InetAddress.getByName("example.com");
        System.out.println(address.getHostName());    // example.com
        System.out.println(address.getHostAddress());  // the resolved IP, e.g. 93.184.216.34
    }
}
```

DNS lookups are **blocking network calls**. Calling `getByName` in a hot loop, or on every request, can silently stall your application if DNS is slow — cache the result if the host rarely changes.

### Socket / ServerSocket — TCP communication

A **socket** is one endpoint of a two-way network connection. **TCP** (Transmission Control Protocol) is a reliable, ordered, connection-based protocol — think of it as a phone call: you dial, both sides confirm the connection, then you can talk in either direction until someone hangs up. `ServerSocket` listens for incoming connections; `Socket` represents one established connection (either the server's side of an accepted connection, or the client's side).

Here is a tiny **echo server** — it reads a line of text from a client and sends the same line back — plus a client that talks to it.

```java
// EchoServer.java
import java.io.*;
import java.net.ServerSocket;
import java.net.Socket;

public class EchoServer {
    public static void main(String[] args) throws IOException {
        try (ServerSocket serverSocket = new ServerSocket(5000)) {
            System.out.println("Echo server listening on port 5000");
            while (true) {
                try (Socket client = serverSocket.accept();
                     BufferedReader in = new BufferedReader(
                             new InputStreamReader(client.getInputStream()));
                     PrintWriter out = new PrintWriter(client.getOutputStream(), true)) {

                    String line = in.readLine();
                    if (line != null) {
                        out.println("Echo: " + line);
                    }
                } // client socket closed automatically, connection ends
            }
        }
    }
}
```

```java
// EchoClient.java
import java.io.*;
import java.net.Socket;
import java.time.Duration;

public class EchoClient {
    public static void main(String[] args) throws IOException {
        try (Socket socket = new Socket()) {
            socket.connect(new java.net.InetSocketAddress("localhost", 5000), 3000); // connect timeout: 3s
            socket.setSoTimeout(3000); // read timeout: 3s

            PrintWriter out = new PrintWriter(socket.getOutputStream(), true);
            BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream()));

            out.println("Hello, server!");
            System.out.println("Server replied: " + in.readLine());
        }
    }
}
```

Each connected `Socket` gets its own thread (or virtual thread) in a simple server like this; production servers usually use non-blocking I/O or a thread pool to handle thousands of clients without one thread per connection.

### DatagramSocket — UDP communication

**UDP** (User Datagram Protocol) is connectionless and unreliable: you send a **packet** (called a **datagram**) and hope it arrives, with no guarantee of order or delivery. Think of it as mailing postcards instead of making a phone call — cheaper, faster, but some might get lost. It's used where occasional loss is acceptable and low latency matters more, such as video streaming, DNS, or game state updates.

```java
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;

public class UdpSender {
    public static void main(String[] args) throws Exception {
        try (DatagramSocket socket = new DatagramSocket()) {
            byte[] data = "ping".getBytes();
            DatagramPacket packet = new DatagramPacket(
                    data, data.length, new InetSocketAddress("localhost", 6000));
            socket.send(packet);
        }
    }
}

public class UdpReceiver {
    public static void main(String[] args) throws Exception {
        try (DatagramSocket socket = new DatagramSocket(6000)) {
            byte[] buffer = new byte[1024];
            DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
            socket.receive(packet); // blocks until a packet arrives
            String message = new String(packet.getData(), 0, packet.getLength());
            System.out.println("Received: " + message);
        }
    }
}
```

### URL / URI — and why `URL.equals` is a trap

`URI` (Uniform Resource Identifier) is just a **syntax** for identifying a resource — it does no network I/O. `URL` (Uniform Resource Locator) is a URI that also knows how to *fetch* the resource, and that extra behavior is where the trouble starts.

**`URL.equals()` and `URL.hashCode()` perform a DNS lookup** to compare hosts, because the spec says two URLs are "equal" only if they resolve to the same IP address. This means comparing two `URL` objects — or worse, putting them in a `HashSet` or using them as `HashMap` keys — can trigger a real network call, block on slow or unreachable DNS, and even give different results at different times if DNS changes.

```java
// Anti-pattern: URLs in a HashSet trigger silent DNS lookups on every add/contains
import java.net.URL;
import java.util.HashSet;
import java.util.Set;

Set<URL> seen = new HashSet<>();
seen.add(new URL("https://example.com/a"));      // may block on DNS
boolean visited = seen.contains(new URL("https://example.com/b")); // may block on DNS too
```

```java
// Fix: use URI, which never touches the network
import java.net.URI;
import java.util.HashSet;
import java.util.Set;

Set<URI> seen = new HashSet<>();
seen.add(URI.create("https://example.com/a"));
boolean visited = seen.contains(URI.create("https://example.com/b")); // pure string/scheme comparison
```

Rule of thumb: use `URI` for parsing, comparing, and storing. Only convert to `URL` (via `uri.toURL()`) at the last moment, right before you actually need to open a connection.

### URLConnection / HttpURLConnection — the legacy way

Before the HTTP Client API (next section), Java code opened HTTP connections through `URL.openConnection()`, which returns a `URLConnection` (or, for HTTP, an `HttpURLConnection`). It's still present in the JDK and you'll see it in older codebases, but it's clunky: manual stream handling, awkward redirect and header handling, and no built-in HTTP/2 or async support. Treat it as **legacy** — prefer the HTTP Client API for new code.

```java
import java.net.HttpURLConnection;
import java.net.URL;
import java.io.*;

public class LegacyGet {
    public static void main(String[] args) throws IOException {
        URL url = new URL("https://example.com/api/status");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(3000); // connect timeout: 3s
        conn.setReadTimeout(3000);    // read timeout: 3s

        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(conn.getInputStream()))) {
            String body = reader.lines().reduce("", (a, b) -> a + b);
            System.out.println("Status: " + conn.getResponseCode() + " body: " + body);
        } finally {
            conn.disconnect();
        }
    }
}
```

### Timeouts — the setting a missing default punishes you for

Neither `Socket`, `HttpURLConnection`, nor (as we'll see) `HttpClient` has a sane default timeout baked in for every operation. Two timeouts matter:

- **Connect timeout** — how long to wait while establishing the TCP connection.
- **Socket/read timeout** (`soTimeout`) — how long to wait for data once connected, before giving up on a read.

```java
// Anti-pattern: no timeouts at all
Socket socket = new Socket("flaky-host", 443); // may hang forever if the host never responds

// Fix: always set both
Socket socket = new Socket();
socket.connect(new InetSocketAddress("flaky-host", 443), 3000); // connect timeout
socket.setSoTimeout(5000); // read timeout
```

Why this is a real production incident, not a theoretical concern: if a downstream service hangs (network partition, overloaded server, firewall silently dropping packets), a thread with no timeout can block **forever**. Multiply that by every request thread calling the same downstream service, and the whole application thread pool fills up with stuck threads — a **cascading failure** where one slow dependency takes down an entire service, even though the dependency never returned an error. Always set explicit connect and read timeouts on every network call.

### SocketChannel — non-blocking I/O

`SocketChannel` (from `java.nio.channels`) is a **non-blocking** alternative to `Socket`: instead of a thread parking until data arrives, you register interest in an event (readable, writable, connectable) with a `Selector`, and one thread can monitor many channels at once.

```java
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.SocketChannel;

public class NonBlockingClient {
    public static void main(String[] args) throws Exception {
        try (SocketChannel channel = SocketChannel.open()) {
            channel.configureBlocking(false);
            channel.connect(new InetSocketAddress("example.com", 80));

            while (!channel.finishConnect()) {
                // could do other work here instead of spinning
            }

            ByteBuffer buffer = ByteBuffer.wrap("GET / HTTP/1.0\r\n\r\n".getBytes());
            channel.write(buffer);
        }
    }
}
```

Non-blocking I/O is what lets a server handle thousands of connections with a handful of threads, instead of one thread per connection. It's more complex to write correctly, which is why frameworks (Netty, and Java's own virtual threads for simpler blocking-style code) exist to hide the complexity.

**Unix domain sockets** (JDK 16+): `SocketChannel` and `ServerSocketChannel` can also bind to a Unix domain socket (a file-path-based socket for fast local inter-process communication, no network stack involved) via `UnixDomainSocketAddress.of(path)` — useful for talking to a sidecar process on the same machine without going through TCP/IP.

## HTTP Client API

Since Java 11, the JDK ships a modern `java.net.http.HttpClient` — a proper HTTP/1.1 and HTTP/2 client with synchronous and asynchronous modes, replacing the awkward `HttpURLConnection` for new code.

### Building a client

`HttpClient` instances are **immutable and thread-safe** once built, and expensive to create (they manage connection pools). Build one and **reuse it** across your whole application instead of creating a new client per request.

```java
import java.net.ProxySelector;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.concurrent.Executors;

HttpClient client = HttpClient.newBuilder()
        .version(HttpClient.Version.HTTP_2)                 // prefer HTTP/2, falls back to 1.1
        .connectTimeout(Duration.ofSeconds(5))              // connection timeout
        .followRedirects(HttpClient.Redirect.NORMAL)        // follow redirects, except HTTPS -> HTTP
        .proxy(ProxySelector.of(new java.net.InetSocketAddress("proxy.internal", 8080)))
        .executor(Executors.newVirtualThreadPerTaskExecutor()) // custom executor for async callbacks
        .build();
```

### Building requests

`HttpRequest` is also immutable; build one per call with `HttpRequest.newBuilder()`. `BodyPublishers` supplies the request body for methods like `POST` and `PUT`.

```java
import java.net.URI;
import java.net.http.HttpRequest;
import java.net.http.HttpRequest.BodyPublishers;
import java.time.Duration;

// GET
HttpRequest getRequest = HttpRequest.newBuilder()
        .uri(URI.create("https://api.example.com/users/42"))
        .timeout(Duration.ofSeconds(10)) // per-request timeout, overrides client default
        .header("Accept", "application/json")
        .GET()
        .build();

// POST with a JSON body
HttpRequest postRequest = HttpRequest.newBuilder()
        .uri(URI.create("https://api.example.com/users"))
        .timeout(Duration.ofSeconds(10))
        .header("Content-Type", "application/json")
        .POST(BodyPublishers.ofString("{\"name\":\"Ada\"}"))
        .build();
```

### Sending — sync vs async

`send` blocks the calling thread until the response arrives. `sendAsync` returns immediately with a `CompletableFuture<HttpResponse<T>>`, letting you compose follow-up work without blocking.

```java
import java.net.http.HttpResponse;
import java.net.http.HttpResponse.BodyHandlers;

// Synchronous
HttpResponse<String> response = client.send(getRequest, BodyHandlers.ofString());
System.out.println(response.statusCode() + ": " + response.body());
```

```java
// Asynchronous, with a CompletableFuture pipeline
client.sendAsync(getRequest, BodyHandlers.ofString())
        .thenApply(HttpResponse::body)
        .thenAccept(System.out::println)
        .exceptionally(ex -> {
            System.err.println("Request failed: " + ex.getMessage());
            return null;
        });
```

`BodyHandlers` controls how the response body is consumed:

- `BodyHandlers.ofString()` — whole body as a `String`; fine for small JSON responses.
- `BodyHandlers.ofLines()` — a `Stream<String>` of lines; good for line-delimited data.
- `BodyHandlers.ofInputStream()` — raw `InputStream`; lets you stream large responses without loading them fully into memory.
- `BodyHandlers.ofFile(Path)` — writes the body directly to disk; ideal for downloads.

```java
// Streaming a large download straight to disk instead of buffering it in memory
HttpRequest downloadRequest = HttpRequest.newBuilder()
        .uri(URI.create("https://example.com/large-file.zip"))
        .build();

HttpResponse<java.nio.file.Path> saved = client.send(
        downloadRequest, BodyHandlers.ofFile(java.nio.file.Path.of("large-file.zip")));
System.out.println("Saved to " + saved.body());
```

### HTTP/2 multiplexing

HTTP/2 allows many logical requests to share a single TCP connection at once (**multiplexing**), instead of opening a new connection — or queuing — per request as HTTP/1.1 often does. `HttpClient` negotiates HTTP/2 automatically when the server supports it (via ALPN during the TLS handshake); you don't need to change any request code. This is one reason to prefer a shared `HttpClient` instance: reusing it lets the connection pool actually multiplex requests instead of paying a new handshake cost every time.

### Error handling and status codes

`HttpClient` only throws exceptions for things like connection failures, timeouts, or protocol errors. **HTTP error status codes (4xx, 5xx) are not exceptions** — they come back as a normal `HttpResponse` that you must check yourself.

```java
// Anti-pattern: assuming a response without an exception means success
HttpResponse<String> response = client.send(getRequest, BodyHandlers.ofString());
processUser(response.body()); // might be a 404 or 500 error page, not user JSON!
```

```java
// Fix: check the status code explicitly
HttpResponse<String> response = client.send(getRequest, BodyHandlers.ofString());
int status = response.statusCode();
if (status >= 200 && status < 300) {
    processUser(response.body());
} else if (status == 404) {
    throw new NoSuchElementException("User not found");
} else {
    throw new IllegalStateException("Unexpected status " + status + ": " + response.body());
}
```

Also handle exceptions from `send`/`sendAsync`: `java.net.http.HttpTimeoutException` for timeouts, `java.net.ConnectException` for connection failures, and `java.io.IOException` more generally.

### Custom SSLContext

By default, `HttpClient` uses the JVM's default `SSLContext`, which trusts the standard public **certificate authorities** (organizations that vouch for a server's identity — see the Security Basics section). To connect to a server with a private/internal certificate authority, supply a custom `SSLContext` built from your own **trust store** (a keystore holding the certificates you trust) — never a trust-all context (shown as an anti-pattern later in this chapter).

```java
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import java.security.KeyStore;

KeyStore trustStore = KeyStore.getInstance("PKCS12");
try (var in = java.nio.file.Files.newInputStream(java.nio.file.Path.of("internal-ca.p12"))) {
    trustStore.load(in, "changeit".toCharArray());
}
TrustManagerFactory tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
tmf.init(trustStore);

SSLContext sslContext = SSLContext.getInstance("TLSv1.3");
sslContext.init(null, tmf.getTrustManagers(), null);

HttpClient secureClient = HttpClient.newBuilder()
        .sslContext(sslContext)
        .build();
```

### WebSocket support

`HttpClient` also builds **WebSocket** connections (a persistent, full-duplex connection for real-time, bidirectional messaging — unlike request/response HTTP) via `HttpClient.newWebSocketBuilder()`.

```java
import java.net.http.WebSocket;
import java.net.URI;
import java.util.concurrent.CompletionStage;

WebSocket.Listener listener = new WebSocket.Listener() {
    @Override
    public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
        System.out.println("Received: " + data);
        webSocket.request(1);
        return null;
    }
};

WebSocket webSocket = client.newWebSocketBuilder()
        .buildAsync(URI.create("wss://example.com/socket"), listener)
        .join();

webSocket.sendText("hello", true);
```

### AutoCloseable in JDK 21+

Since Java 21, `HttpClient` implements `AutoCloseable`, so you can use it in a try-with-resources block to release its resources (connection pools, background threads) deterministically — useful for short-lived clients, though for a long-lived shared client you'd typically just let it live for the application's lifetime and never close it.

```java
try (HttpClient shortLivedClient = HttpClient.newHttpClient()) {
    HttpResponse<String> response = shortLivedClient.send(getRequest, BodyHandlers.ofString());
    System.out.println(response.body());
} // client resources released here
```

## Process API

Sometimes your Java application needs to run an external program — a shell script, `ffmpeg`, `git`, a native tool. The **Process API** (`ProcessBuilder`, `Process`, `ProcessHandle`) lets you launch and manage external processes.

### ProcessBuilder — launching a process safely

`ProcessBuilder` takes the command as a **list of strings**: the program name, then each argument as a separate list element. This is critical for security — see the anti-pattern below.

```java
// Anti-pattern: building a shell command string from user input — COMMAND INJECTION
String filename = userInput; // attacker-controlled, e.g. "; rm -rf / #"
Process process = Runtime.getRuntime().exec("ls -l " + filename);
```

If `filename` is `"foo; rm -rf /"`, and this string is ever passed through a shell (e.g. via `bash -c`), the shell happily runs `rm -rf /` as a second command. This class of bug is called **command injection**: attacker input escapes its intended role as *data* and gets interpreted as *code* (here, shell syntax).

```java
// Fix: pass arguments as a list — no shell parsing, no injection
import java.util.List;

ProcessBuilder builder = new ProcessBuilder(List.of("ls", "-l", filename));
Process process = builder.start();
```

Because each argument is a separate list element, `filename` is passed to the `ls` program as a single literal argument — `;`, `&&`, backticks, and other shell metacharacters have no special meaning here. **Never** build a command by concatenating a string and handing it to a shell (`bash -c "..."`, `sh -c "..."`, or string-based `exec`) when any part of it comes from user input.

### Redirecting IO

By default, a child process's stdin/stdout/stderr are separate pipes your Java code must read or write. `redirectOutput`, `redirectError`, and `redirectInput` connect them directly to files, or `inheritIO()` connects them straight to the parent JVM's own console — handy for CLI tools where you just want to see the child's output live.

```java
ProcessBuilder builder = new ProcessBuilder("ping", "-c", "3", "example.com");
builder.inheritIO(); // child's stdout/stderr appear directly in this program's console
Process process = builder.start();
int exitCode = process.waitFor();
```

```java
ProcessBuilder builder = new ProcessBuilder("mytool", "--verbose");
builder.redirectOutput(new java.io.File("output.log"));
builder.redirectError(ProcessBuilder.Redirect.INHERIT); // errors still show in console
Process process = builder.start();
```

### Reading stdout/stderr without deadlocking

If you don't redirect a stream and don't read it, the child process's output buffer can fill up. Once full, the child blocks trying to write more output — and if your Java code is meanwhile blocked in `waitFor()` waiting for the child to exit, you get a **deadlock**: each side waiting on the other forever.

```java
// Anti-pattern: reading stdout only after waitFor() — can deadlock if stderr fills up first
Process process = new ProcessBuilder("noisy-tool").start();
int exitCode = process.waitFor(); // may hang forever if noisy-tool blocks on a full stderr buffer
String output = new String(process.getInputStream().readAllBytes());
```

```java
// Fix: drain both streams concurrently, in separate threads, before waiting
Process process = new ProcessBuilder("noisy-tool").start();

Thread outputReader = Thread.ofVirtual().start(() -> {
    try (var in = process.getInputStream()) {
        System.out.write(in.readAllBytes());
    } catch (java.io.IOException e) { /* log */ }
});
Thread errorReader = Thread.ofVirtual().start(() -> {
    try (var err = process.getErrorStream()) {
        System.err.write(err.readAllBytes());
    } catch (java.io.IOException e) { /* log */ }
});

int exitCode = process.waitFor();
outputReader.join();
errorReader.join();
```

An easier fix, when you don't need the streams programmatically, is `ProcessBuilder.redirectErrorStream(true)` (merges stderr into stdout — one stream to read) or `inheritIO()` — both avoid the deadlock entirely by not leaving an unread pipe.

### waitFor with a timeout

Waiting forever for a child process is as risky as a network call with no timeout — a hung external tool can hang your JVM's thread indefinitely.

```java
import java.util.concurrent.TimeUnit;

Process process = new ProcessBuilder("slow-tool").start();
boolean finished = process.waitFor(30, TimeUnit.SECONDS);
if (!finished) {
    process.destroyForcibly(); // give up and kill it
    throw new IllegalStateException("slow-tool did not finish in time");
}
int exitCode = process.exitValue();
```

### ProcessHandle (JDK 9+)

`ProcessHandle` gives you information about *any* process — not just ones you started — by PID (process ID), including the current JVM's own process, without needing native code.

```java
ProcessHandle current = ProcessHandle.current();
System.out.println("My PID: " + current.pid());

Process child = new ProcessBuilder("sleep", "60").start();
ProcessHandle handle = child.toHandle();

handle.info().command().ifPresent(cmd -> System.out.println("Command: " + cmd));
handle.info().startInstant().ifPresent(t -> System.out.println("Started: " + t));

// List all child processes of the current JVM
ProcessHandle.current().children().forEach(h ->
        System.out.println("Child PID: " + h.pid()));

// React when the process exits, without blocking the current thread
handle.onExit().thenAccept(h ->
        System.out.println("Process " + h.pid() + " exited with status " + h.exitValue()));
```

### destroy vs destroyForcibly

`destroy()` requests a **graceful** termination — on most platforms this sends `SIGTERM` on Unix-like systems, letting the process clean up (close files, flush buffers) before exiting. `destroyForcibly()` requests an **immediate, forceful** kill (`SIGKILL`-like), which the process cannot intercept or clean up after.

```java
Process process = new ProcessBuilder("my-daemon").start();
process.destroy(); // ask nicely first
if (!process.waitFor(5, TimeUnit.SECONDS)) {
    process.destroyForcibly(); // it ignored us — force it
}
```

Prefer `destroy()` first and escalate to `destroyForcibly()` only if the process doesn't respond in a reasonable time, so well-behaved processes get a chance to shut down cleanly.

## Security Basics

Security in Java isn't a separate skill from writing correct code — most security bugs are just correctness bugs where the attacker controls the input. This section is deliberately defensive: every insecure pattern shown here is labelled **Anti-pattern** and immediately followed by the fix, for recognizing and rejecting these patterns in review — not for building them.

### Principle of least privilege

Give code, users, and processes the **minimum** access they need to do their job, nothing more. A reporting service that only reads data should use a database account with read-only permissions, not the same admin account used for migrations. A file-processing service should run as a low-privilege OS user, not `root`. When something does go wrong — and eventually something will — least privilege limits the damage.

### Input validation

**Never trust input from outside your program's trust boundary** — HTTP requests, file uploads, environment variables, even data from another internal service you don't fully control. Validate shape, type, range, and length *before* using the value, ideally as close to the entry point as possible.

```java
// Anti-pattern: using a user-supplied value without validation
int pageSize = Integer.parseInt(request.getParameter("pageSize"));
List<Item> items = repository.fetch(pageSize); // attacker sends pageSize=2000000000

// Fix: validate range before use
int pageSize = Integer.parseInt(request.getParameter("pageSize"));
if (pageSize < 1 || pageSize > 100) {
    throw new IllegalArgumentException("pageSize must be between 1 and 100");
}
List<Item> items = repository.fetch(pageSize);
```

### SQL injection

**SQL injection** happens when user input is concatenated directly into a SQL query string, letting an attacker change the query's *meaning* instead of just supplying a value.

```java
// Anti-pattern: string concatenation builds attacker-controlled SQL
String username = request.getParameter("username"); // e.g. "' OR '1'='1"
String sql = "SELECT * FROM users WHERE username = '" + username + "'";
ResultSet rs = statement.executeQuery(sql); // returns ALL users, auth bypass
```

```java
// Fix: PreparedStatement with bound parameters — input is always treated as data
String sql = "SELECT * FROM users WHERE username = ?";
try (PreparedStatement ps = connection.prepareStatement(sql)) {
    ps.setString(1, username);
    ResultSet rs = ps.executeQuery();
}
```

`PreparedStatement` sends the query structure and the parameter values separately to the database, so the database never re-parses attacker input as SQL syntax.

### Command injection

Covered in detail in the Process API section above: never build a shell command string from untrusted input. Always pass arguments as a list to `ProcessBuilder`, and avoid invoking a shell (`bash -c`, `sh -c`) with concatenated input at all.

### Path traversal

**Path traversal** (or "directory traversal") happens when user input controls a file path and contains sequences like `../` that escape the intended directory.

```java
// Anti-pattern: filename from the client used directly in a file path
String filename = request.getParameter("file"); // e.g. "../../etc/passwd"
File file = new File("/var/app/uploads/" + filename);
byte[] contents = Files.readAllBytes(file.toPath()); // reads outside uploads/
```

```java
// Fix: resolve, normalize, and verify the result stays within the allowed directory
Path uploadsDir = Path.of("/var/app/uploads").toRealPath();
Path requested = uploadsDir.resolve(filename).normalize();

if (!requested.startsWith(uploadsDir)) {
    throw new SecurityException("Invalid file path: " + filename);
}
byte[] contents = Files.readAllBytes(requested);
```

`resolve` + `normalize` collapses `..` segments, and the `startsWith` check ensures the final path is still inside the intended base directory before touching the filesystem.

### XXE — XML External Entity injection

XML parsers, by default, may resolve **external entities** — references inside an XML document that pull in content from a file or URL. If an attacker controls the XML being parsed, they can use this to read local files or make the server issue outbound requests (**XXE**, XML External Entity injection).

```java
// Anti-pattern: default DocumentBuilderFactory resolves external entities
String maliciousXml = """
    <?xml version="1.0"?>
    <!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
    <foo>&xxe;</foo>
    """;

DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
DocumentBuilder builder = factory.newDocumentBuilder();
Document doc = builder.parse(new ByteArrayInputStream(maliciousXml.getBytes())); // leaks /etc/passwd
```

```java
// Fix: disable DTDs and external entities explicitly
DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
factory.setXIncludeAware(false);
factory.setExpandEntityReferences(false);

DocumentBuilder builder = factory.newDocumentBuilder();
Document doc = builder.parse(new ByteArrayInputStream(maliciousXml.getBytes())); // throws, DTD rejected
```

The same idea applies to `SAXParserFactory`, `XMLInputFactory` (StAX), and `TransformerFactory` — each has equivalent feature flags to disable DTDs and external entities. When parsing XML from an untrusted source, disable them all.

### Insecure deserialization

Java's built-in serialization (`ObjectInputStream.readObject()`) reconstructs arbitrary objects from a byte stream, including running code in constructors, `readObject` overrides, and finalizers along the way. Deserializing **untrusted** data can let an attacker construct object graphs that trigger unintended — sometimes arbitrary — code execution (a well-known class of "gadget chain" attacks).

```java
// Anti-pattern: deserializing bytes received from a network client
ObjectInputStream in = new ObjectInputStream(socket.getInputStream());
Object payload = in.readObject(); // attacker fully controls the class graph being built
```

```java
// Fix: use a safe, explicit data format instead (e.g. JSON with a known schema),
// or if Java serialization is unavoidable, filter allowed classes
ObjectInputStream in = new ObjectInputStream(socket.getInputStream());
in.setObjectInputFilter(filterInfo -> {
    Class<?> clazz = filterInfo.serialClass();
    if (clazz != null && !clazz.getName().startsWith("com.myapp.dto.")) {
        return ObjectInputFilter.Status.REJECTED;
    }
    return ObjectInputFilter.Status.UNDECIDED;
});
Object payload = in.readObject(); // only com.myapp.dto.* classes are allowed through
```

The best fix is usually to avoid Java's native serialization for untrusted data entirely — use a safe format like JSON with a strict schema and a library that only builds known DTOs, never arbitrary classes.

### SSRF — Server-Side Request Forgery

**SSRF** happens when your server makes an outbound HTTP request to a URL that is (directly or indirectly) controlled by the attacker, letting them reach internal-only systems (metadata endpoints, admin panels, internal services) that would otherwise be unreachable from outside.

```java
// Anti-pattern: fetching a user-supplied URL with no restriction
String imageUrl = request.getParameter("avatarUrl"); // e.g. "http://169.254.169.254/latest/meta-data/"
HttpResponse<byte[]> response = client.send(
        HttpRequest.newBuilder(URI.create(imageUrl)).build(), BodyHandlers.ofByteArray());
```

```java
// Fix: validate against an allow-list of hosts/schemes, and resolve+check the IP before connecting
private static final Set<String> ALLOWED_HOSTS = Set.of("images.trusted-cdn.com");

URI uri = URI.create(imageUrl);
if (!"https".equals(uri.getScheme()) || !ALLOWED_HOSTS.contains(uri.getHost())) {
    throw new SecurityException("URL not allowed: " + imageUrl);
}
InetAddress resolved = InetAddress.getByName(uri.getHost());
if (resolved.isLoopbackAddress() || resolved.isSiteLocalAddress() || resolved.isLinkLocalAddress()) {
    throw new SecurityException("URL resolves to a disallowed internal address");
}
HttpResponse<byte[]> response = client.send(
        HttpRequest.newBuilder(uri).build(), BodyHandlers.ofByteArray());
```

Note that an allow-list check on the hostname alone isn't quite enough — DNS can be manipulated (**DNS rebinding**) between the check and the actual request, so high-value systems also pin the resolved IP or route such requests through an isolated network egress proxy.

### Hard-coded secrets

Credentials, API keys, and encryption keys committed to source code end up in version control history forever, visible to anyone with repo access (and often, accidentally, on a public GitHub mirror).

```java
// Anti-pattern: secret baked into source code
private static final String DB_PASSWORD = "SuperSecret123!";
private static final String API_KEY = "sk_live_51H8x...";
```

```java
// Fix: load secrets from the environment or a secrets manager at runtime
String dbPassword = System.getenv("DB_PASSWORD");
String apiKey = secretsManagerClient.getSecret("payments/api-key");
```

### Logging sensitive data

Logs are often shipped to third-party aggregators, kept for years, and readable by many engineers — a bad place for passwords, tokens, or full card numbers to end up.

```java
// Anti-pattern: logging the full request, including sensitive fields
log.info("Login attempt: {}", loginRequest); // toString() includes the raw password

// Fix: log only what's needed, and redact the rest
log.info("Login attempt for user: {}", loginRequest.username());
```

### char[] vs String for passwords

`String` is **immutable** and Java's string pool (or just normal GC timing) can keep a copy of a password in memory for an unpredictable, possibly long time — there's no way to proactively erase it. A `char[]` can be overwritten with zeros the moment you're done with it, shrinking the window where the password sits in memory in plaintext. This is why `JPasswordField.getPassword()` and JDBC's `Connection` APIs that accept passwords use `char[]`.

```java
// Anti-pattern: password as a String — cannot be wiped from memory on demand
String password = new String(passwordBytes);
authenticate(username, password);
// password may live in memory long after this point, out of your control

// Fix: char[] can be explicitly cleared right after use
char[] password = readPasswordFromInput();
try {
    authenticate(username, password);
} finally {
    java.util.Arrays.fill(password, '\0'); // overwrite in memory
}
```

### SecureRandom vs Random

`java.util.Random` is a fast **pseudo-random number generator (PRNG)** seeded predictably — given the seed (or even a handful of outputs), an attacker can often predict every future value. It must never be used for anything security-sensitive: tokens, session IDs, password reset codes, cryptographic keys. `SecureRandom` uses a **cryptographically secure PRNG (CSPRNG)**, designed so past output reveals nothing about future output.

```java
// Anti-pattern: predictable token generation
Random random = new Random();
String resetToken = Long.toHexString(random.nextLong()); // guessable by an attacker

// Fix: SecureRandom for anything security-sensitive
SecureRandom secureRandom = new SecureRandom();
byte[] tokenBytes = new byte[32];
secureRandom.nextBytes(tokenBytes);
String resetToken = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(tokenBytes);
```

### Constant-time comparison

Comparing two secrets (e.g. an HMAC signature, an API key) with `String.equals` or `Arrays.equals` stops at the **first mismatched byte** — that tiny timing difference, measured over enough repeated attempts, can leak the correct value one byte at a time (a **timing attack**).

```java
// Anti-pattern: early-exit comparison leaks timing information
if (!providedSignature.equals(expectedSignature)) {
    throw new SecurityException("Invalid signature");
}

// Fix: constant-time comparison — always examines every byte
byte[] provided = providedSignature.getBytes(StandardCharsets.UTF_8);
byte[] expected = expectedSignature.getBytes(StandardCharsets.UTF_8);
if (!MessageDigest.isEqual(provided, expected)) {
    throw new SecurityException("Invalid signature");
}
```

### TLS defaults and certificate validation

**TLS** (Transport Layer Security, the successor to SSL) provides encryption and — critically — **authentication**: the client verifies the server's identity through its **certificate**, issued and signed by a trusted **certificate authority (CA)**. Skipping that verification defeats the entire point of TLS.

```java
// Anti-pattern: a trust-all TrustManager disables certificate validation entirely
TrustManager[] trustAllCerts = new TrustManager[] {
    new X509TrustManager() {
        public void checkClientTrusted(X509Certificate[] chain, String authType) {} // accepts anything
        public void checkServerTrusted(X509Certificate[] chain, String authType) {} // accepts anything
        public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
    }
};
SSLContext sslContext = SSLContext.getInstance("TLS");
sslContext.init(null, trustAllCerts, new java.security.SecureRandom());
// Any server, including an attacker performing a man-in-the-middle attack, is now "trusted"
```

```java
// Fix: use the platform default trust managers (or a specific, real trust store)
SSLContext sslContext = SSLContext.getInstance("TLSv1.3");
sslContext.init(null, null, null); // null TrustManager[] => use the JVM's default, real CA trust store
```

If you need to trust a private/internal CA, build a real `TrustManagerFactory` from a keystore containing that CA's certificate (as shown in the HTTP Client section's `SSLContext` example) — never disable validation to work around a certificate problem, even "temporarily."

### The Security Manager (JEP 411)

The **Security Manager** was Java's mechanism for sandboxing code — restricting what a piece of code could do (file access, network access, etc.) via fine-grained permissions. As of **JEP 411** (Java 17), it is deprecated for removal, and it has been fully removed in Java 24. If you inherit code that relies on `SecurityManager`/`AccessController`, plan to migrate away from it — modern isolation is typically done at the process, container, or OS level instead (containers, seccomp profiles, dedicated low-privilege OS users), not inside the JVM.

### Dependency vulnerabilities

Most real-world Java vulnerabilities (Log4Shell being the most infamous recent example) come from **third-party libraries**, not code you wrote. Practices that matter in review:

- Keep an inventory of dependencies and their versions (a **Software Bill of Materials**, or SBOM).
- Run a dependency vulnerability scanner (e.g. OWASP Dependency-Check, `mvn versions:display-dependency-updates`, GitHub Dependabot) as part of CI.
- Pin versions and update regularly — being a few major versions behind makes upgrading (and patching a critical CVE) much riskier and slower.
- Don't pull in a whole library for one function if you can avoid the dependency weight and attack surface.

## Cryptography APIs

Java's cryptography support is split into the **JCA** (Java Cryptography Architecture — the framework: engine classes like `MessageDigest`, `Cipher`, `Signature`) and the **JCE** (Java Cryptography Extension — historically export-restricted extensions, now merged into the JDK). Both are **provider-based**: you ask for an algorithm by name (e.g. `"AES/GCM/NoPadding"`), and a registered **Provider** (the built-in SunJCE, or a third-party one like Bouncy Castle) supplies the actual implementation. This lets you swap implementations without changing application code.

```java
import java.security.Provider;
import java.security.Security;

for (Provider provider : Security.getProviders()) {
    System.out.println(provider.getName());
}
```

### MessageDigest — hashing

A **hash function** takes input of any size and produces a fixed-size fingerprint (**digest**). It's one-way: you can't recover the input from the digest. Use it for integrity checks (e.g. "did this file change?"), never for passwords (see below).

```java
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.nio.charset.StandardCharsets;
import java.util.HexFormat;

public class HashExample {
    public static String sha256(String input) throws NoSuchAlgorithmException {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] hash = digest.digest(input.getBytes(StandardCharsets.UTF_8));
        return HexFormat.of().formatHex(hash);
    }
}
```

### Password hashing — PBKDF2, and why plain hashing isn't enough

**Never store passwords in plaintext, and never hash them with a plain, fast hash like MD5 or SHA-1/SHA-256 alone.** Fast hashes let an attacker who steals your password database try billions of guesses per second on cheap GPU hardware. Password hashing needs to be **slow and tunable** on purpose, and use a per-password **salt** (random data mixed in) so identical passwords don't produce identical hashes.

```java
// Anti-pattern: a fast, unsalted hash for passwords
MessageDigest digest = MessageDigest.getInstance("SHA-256");
byte[] hash = digest.digest(password.getBytes(StandardCharsets.UTF_8));
// Crackable at billions of attempts/second with commodity GPUs; identical passwords -> identical hashes
```

```java
// Fix: PBKDF2 via SecretKeyFactory — salted, and deliberately slow (many iterations)
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.security.SecureRandom;

public class PasswordHasher {
    public static byte[] hash(char[] password, byte[] salt) throws Exception {
        PBEKeySpec spec = new PBEKeySpec(password, salt, 210_000, 256); // iterations, key length in bits
        SecretKeyFactory factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256");
        return factory.generateSecret(spec).getEncoded();
    }

    public static byte[] newSalt() {
        byte[] salt = new byte[16];
        new SecureRandom().nextBytes(salt);
        return salt;
    }
}
```

Store the salt alongside the hash (it's not secret) and re-derive using the same salt and iteration count to verify a login attempt. In practice, **bcrypt** or **argon2** (via a library, since the JDK doesn't ship them natively) are usually preferred over PBKDF2 for new systems — they're purpose-built for password hashing with better resistance to GPU/ASIC cracking. Whatever you choose: never MD5, never plain SHA-1/SHA-256, never unsalted.

### Cipher — AES-GCM done correctly

`Cipher` performs **encryption** (reversible scrambling with a key) and decryption. **AES-GCM** (Advanced Encryption Standard, Galois/Counter Mode) is the recommended default: it's fast, and it's an **authenticated** mode — it detects tampering, not just confidentiality.

The single most common mistake: **ECB mode**. ECB encrypts each fixed-size block independently, so identical plaintext blocks always produce identical ciphertext blocks — patterns in the input leak straight through, famously visible if you ECB-encrypt an image.

```java
// Anti-pattern: AES in ECB mode — identical plaintext blocks -> identical ciphertext blocks
Cipher cipher = Cipher.getInstance("AES/ECB/PKCS5Padding");
cipher.init(Cipher.ENCRYPT_MODE, secretKey);
byte[] ciphertext = cipher.doFinal(plaintext); // patterns in plaintext are visible in ciphertext
```

```java
// Fix: AES-GCM with a fresh random IV (nonce) for every single encryption
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import java.security.SecureRandom;

public class AesGcmExample {
    private static final int IV_LENGTH_BYTES = 12;
    private static final int TAG_LENGTH_BITS = 128;

    public static byte[] encrypt(byte[] plaintext, SecretKey key) throws Exception {
        byte[] iv = new byte[IV_LENGTH_BYTES];
        new SecureRandom().nextBytes(iv); // MUST be unique per encryption with the same key

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(TAG_LENGTH_BITS, iv));
        byte[] ciphertext = cipher.doFinal(plaintext);

        // Prepend the IV so decrypt() can read it back — the IV itself is not secret
        byte[] result = new byte[iv.length + ciphertext.length];
        System.arraycopy(iv, 0, result, 0, iv.length);
        System.arraycopy(ciphertext, 0, result, iv.length, ciphertext.length);
        return result;
    }

    public static byte[] decrypt(byte[] ivAndCiphertext, SecretKey key) throws Exception {
        byte[] iv = java.util.Arrays.copyOfRange(ivAndCiphertext, 0, IV_LENGTH_BYTES);
        byte[] ciphertext = java.util.Arrays.copyOfRange(ivAndCiphertext, IV_LENGTH_BYTES, ivAndCiphertext.length);

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(TAG_LENGTH_BITS, iv));
        return cipher.doFinal(ciphertext); // throws AEADBadTagException if tampered
    }
}
```

The **IV** (Initialization Vector, also called a nonce) must be unique for every encryption performed with the same key — reusing an IV with GCM catastrophically breaks its security guarantees, potentially revealing the plaintext and letting an attacker forge messages. Generating it fresh with `SecureRandom` every time, as above, is the standard safe approach.

### KeyGenerator / KeyPairGenerator

`KeyGenerator` creates **symmetric** keys (the same key encrypts and decrypts, used with `Cipher` above). `KeyPairGenerator` creates **asymmetric** key pairs (a public key anyone can use to encrypt or verify, and a private key only the owner holds, to decrypt or sign).

```java
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import java.security.KeyPair;
import java.security.KeyPairGenerator;

// Symmetric AES key
KeyGenerator keyGen = KeyGenerator.getInstance("AES");
keyGen.init(256); // key size in bits
SecretKey aesKey = keyGen.generateKey();

// Asymmetric key pair
KeyPairGenerator keyPairGen = KeyPairGenerator.getInstance("RSA");
keyPairGen.initialize(3072); // modulus size in bits
KeyPair keyPair = keyPairGen.generateKeyPair();
```

### Signature — proving authenticity and integrity

A **digital signature** lets the holder of a private key sign data so that anyone with the corresponding public key can verify it came from them and wasn't altered — the asymmetric-key analogue of the HMAC/`MessageDigest.isEqual` comparison shown earlier.

```java
import java.security.Signature;
import java.security.PrivateKey;
import java.security.PublicKey;

// Signing
Signature signer = Signature.getInstance("SHA256withRSA");
signer.initSign(privateKey);
signer.update(data);
byte[] signatureBytes = signer.sign();

// Verifying
Signature verifier = Signature.getInstance("SHA256withRSA");
verifier.initVerify(publicKey);
verifier.update(data);
boolean valid = verifier.verify(signatureBytes);
```

### KeyStore — storing keys and certificates

A `KeyStore` is a protected container for keys and certificates, typically backed by a file (`PKCS12` format is the modern standard; the older proprietary `JKS` format still appears in legacy code).

```java
import java.security.KeyStore;

KeyStore keyStore = KeyStore.getInstance("PKCS12");
try (var in = java.nio.file.Files.newInputStream(java.nio.file.Path.of("app-keys.p12"))) {
    keyStore.load(in, "keystorePassword".toCharArray());
}
java.security.cert.Certificate cert = keyStore.getCertificate("my-alias");
```

### SecureRandom seeding

`SecureRandom` seeds itself automatically from the operating system's entropy source (e.g. `/dev/urandom` on Linux) — you generally should **not** manually reseed it with a predictable value, which would undermine its unpredictability.

```java
// Anti-pattern: reseeding with a predictable value defeats the purpose
SecureRandom random = new SecureRandom();
random.setSeed(System.currentTimeMillis()); // now guessable from the approximate time

// Fix: just use the default seeding — it's already good
SecureRandom random = new SecureRandom();
byte[] output = new byte[32];
random.nextBytes(output);
```

### Algorithm do / don't

| Purpose | Do use | Don't use | Why |
|---|---|---|---|
| Hashing (integrity) | SHA-256, SHA-3 | MD5, SHA-1 | MD5 and SHA-1 have known collision weaknesses |
| Password hashing | bcrypt, argon2, PBKDF2 (many iterations) | MD5, SHA-1, plain SHA-256, unsalted anything | fast hashes are crackable at massive scale; no salt means identical inputs collide |
| Symmetric encryption | AES-GCM, AES-256 | DES, 3DES, RC4, AES-ECB | DES/RC4 are broken; ECB leaks patterns |
| Asymmetric encryption/signing | RSA (≥3072-bit), ECDSA (P-256/P-384), Ed25519 | RSA (<2048-bit) | short keys are brute-forceable with modern hardware |
| Random values (security use) | `SecureRandom` | `java.util.Random`, `Math.random()` | predictable PRNGs leak future/past values |
| Transport security | TLS 1.2 / TLS 1.3 | SSL 2.0/3.0, TLS 1.0/1.1 | old SSL/TLS versions have known protocol flaws |
| Key comparison | `MessageDigest.isEqual` | `==`, `Arrays.equals`, `String.equals` | early-exit comparisons leak timing information |

## Common Code-Review Interview Pitfalls

1. **Opening a socket, `HttpURLConnection`, or `HttpClient` request with no connect/read timeout.**
   Why it matters: a single unresponsive dependency can hang a thread forever, and enough hung threads exhaust the whole pool — a real production outage, not a theoretical risk.
   ```java
   // Before
   Socket socket = new Socket("host", 443);
   // After
   Socket socket = new Socket();
   socket.connect(new InetSocketAddress("host", 443), 3000);
   socket.setSoTimeout(5000);
   ```

2. **Storing `URL` objects in a `HashSet`/`HashMap` or comparing them with `equals`.**
   Why it matters: `URL.equals`/`hashCode` perform a blocking DNS lookup, which can silently stall the application and even give inconsistent results if DNS changes.
   ```java
   // Before
   Set<URL> cache = new HashSet<>();
   // After
   Set<URI> cache = new HashSet<>(); // URI never touches the network
   ```

3. **Creating a brand-new `HttpClient` for every request instead of sharing one instance.**
   Why it matters: `HttpClient` manages connection pools; recreating it per request throws away pooling and HTTP/2 multiplexing benefits and adds unnecessary handshake overhead.
   ```java
   // Before
   HttpResponse<String> r = HttpClient.newHttpClient().send(req, BodyHandlers.ofString());
   // After
   private static final HttpClient CLIENT = HttpClient.newHttpClient(); // built once, reused everywhere
   ```

4. **Treating any non-thrown `HttpClient` response as a success.**
   Why it matters: 4xx/5xx responses don't throw exceptions in the HTTP Client API — skipping the status-code check means error bodies get processed as if they were valid data.
   ```java
   // Before
   processUser(response.body());
   // After
   if (response.statusCode() / 100 != 2) throw new IllegalStateException("HTTP " + response.statusCode());
   processUser(response.body());
   ```

5. **Building a shell command by string concatenation with untrusted input.**
   Why it matters: this is textbook command injection — shell metacharacters in the input let an attacker run arbitrary commands.
   ```java
   // Before
   Runtime.getRuntime().exec("ls " + userInput);
   // After
   new ProcessBuilder("ls", userInput).start(); // arguments never parsed by a shell
   ```

6. **Calling `process.waitFor()` before draining stdout/stderr, risking a deadlock.**
   Why it matters: if the child's output buffer fills up while your code is blocked waiting for exit, both sides wait on each other forever.
   ```java
   // Before
   process.waitFor();
   String out = new String(process.getInputStream().readAllBytes());
   // After
   // drain stdout/stderr concurrently in separate threads, then waitFor()
   ```

7. **Calling `Process.waitFor()` with no timeout.**
   Why it matters: a hung external process (bad input, infinite loop, deadlock) can block the calling thread indefinitely.
   ```java
   // Before
   process.waitFor();
   // After
   if (!process.waitFor(30, TimeUnit.SECONDS)) process.destroyForcibly();
   ```

8. **Building SQL by string concatenation instead of using `PreparedStatement`.**
   Why it matters: this is SQL injection — one of the most common and most damaging vulnerabilities, capable of leaking or destroying an entire database.
   ```java
   // Before
   "SELECT * FROM users WHERE name = '" + name + "'"
   // After
   PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE name = ?");
   ps.setString(1, name);
   ```

9. **Parsing untrusted XML with a default `DocumentBuilderFactory`/`SAXParserFactory`.**
   Why it matters: default XML parsers resolve external entities, opening the door to XXE attacks that read local files or trigger SSRF.
   ```java
   // Before
   DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(untrustedInput);
   // After
   factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
   // ... plus disabling external general/parameter entities, then parse
   ```

10. **Using a trust-all `TrustManager` (or `HostnameVerifier` that always returns true) "to get past a certificate error."**
    Why it matters: this disables TLS's core security guarantee, making the connection trivially vulnerable to a man-in-the-middle attack.
    ```java
    // Before
    public void checkServerTrusted(X509Certificate[] chain, String authType) {} // trusts anything
    // After
    // use the JVM's default trust managers, or a real trust store containing your CA
    sslContext.init(null, null, null);
    ```

11. **Hashing passwords with a single fast pass of SHA-256 (or MD5/SHA-1) instead of a slow, salted algorithm.**
    Why it matters: fast hashes let attackers who steal the password table brute-force billions of guesses per second on cheap hardware.
    ```java
    // Before
    MessageDigest.getInstance("SHA-256").digest(password.getBytes());
    // After
    SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(
            new PBEKeySpec(password, salt, 210_000, 256));
    ```

12. **Using AES in ECB mode.**
    Why it matters: ECB leaks structural patterns in the plaintext because identical blocks always encrypt to identical ciphertext — it provides much weaker confidentiality than intended.
    ```java
    // Before
    Cipher.getInstance("AES/ECB/PKCS5Padding");
    // After
    Cipher.getInstance("AES/GCM/NoPadding"); // with a fresh random IV per encryption
    ```

13. **Reusing the same IV/nonce for multiple AES-GCM encryptions with the same key.**
    Why it matters: IV reuse with GCM catastrophically breaks confidentiality and integrity guarantees — it can expose plaintext and allow forged ciphertexts.
    ```java
    // Before
    static final byte[] FIXED_IV = new byte[12]; // reused every call
    // After
    byte[] iv = new byte[12];
    new SecureRandom().nextBytes(iv); // fresh IV, generated per encryption
    ```

14. **Using `java.util.Random` (or `Math.random()`) to generate tokens, session IDs, or keys.**
    Why it matters: these PRNGs are predictable; an attacker who observes some outputs (or knows the seed) can often predict others, defeating the token's purpose.
    ```java
    // Before
    new Random().nextLong();
    // After
    new SecureRandom().nextBytes(new byte[32]);
    ```

15. **Comparing secrets (signatures, tokens, MACs) with `equals` instead of a constant-time comparison.**
    Why it matters: `equals`/`Arrays.equals` exit early on the first mismatch, leaking timing information an attacker can use to guess the secret byte by byte.
    ```java
    // Before
    if (!hmac.equals(expected)) throw new SecurityException();
    // After
    if (!MessageDigest.isEqual(hmac.getBytes(), expected.getBytes())) throw new SecurityException();
    ```

16. **Storing passwords in a `String` instead of a `char[]`, or logging request/response objects that contain secrets.**
    Why it matters: `String` immutability means the password can linger in memory unpredictably with no way to wipe it, and logging full objects routinely leaks credentials into long-lived, widely-read log stores.
    ```java
    // Before
    log.info("Login attempt: {}", loginRequest); // toString() includes password field
    // After
    log.info("Login attempt for user: {}", loginRequest.username());
    ```

17. **Fetching a user-supplied URL directly, with no host allow-list (SSRF).**
    Why it matters: without restriction, an attacker can direct your server to make requests to internal-only endpoints (cloud metadata services, admin panels) it would otherwise never reach.
    ```java
    // Before
    client.send(HttpRequest.newBuilder(URI.create(userSuppliedUrl)).build(), handler);
    // After
    if (!ALLOWED_HOSTS.contains(URI.create(userSuppliedUrl).getHost())) throw new SecurityException();
    ```

18. **Hard-coding API keys, passwords, or encryption keys as string literals in source code.**
    Why it matters: secrets committed to version control are effectively permanent and visible to anyone with repository access, including in old commits after "removal."
    ```java
    // Before
    private static final String API_KEY = "sk_live_51H8x...";
    // After
    String apiKey = System.getenv("API_KEY"); // or a secrets manager, injected at deploy time
    ```
