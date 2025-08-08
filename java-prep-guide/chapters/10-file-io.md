# 10. File I/O

Every real program eventually has to read or write something outside its own memory: a config file, a log, a network payload, a saved object. Java gives you three overlapping toolkits for this — the old `java.io` streams, the newer NIO/NIO.2 (`java.nio`) APIs, and Java's built-in object serialization. Each one solves a slightly different problem, and each one has sharp edges that show up constantly in code review. This chapter walks through all three, plus the modern `Path`/`Files` API that has mostly replaced `java.io.File`.

**Table of Contents**

- [java.io (File I/O)](#javaio-file-io)
- [NIO and NIO.2](#nio-and-nio2)
- [Paths and Files](#paths-and-files)
- [Serialization](#serialization)
- [Externalizable](#externalizable)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

## java.io (File I/O)

`java.io` is the original Java I/O library, dating back to Java 1.0. It is *stream-based*: you read or write one chunk at a time, in order, from start to end. There is no random access and no built-in buffering — you add both yourself.

The most important split in `java.io` is **byte streams vs. character streams**.

- **Byte streams** (`InputStream` / `OutputStream`) move raw bytes (`0`–`255`). Use them for binary data: images, zip files, network sockets, serialized objects.
- **Character streams** (`Reader` / `Writer`) move `char` values (text). Internally they still read/write bytes, but they convert bytes to characters using a **charset** (a character encoding table, e.g. UTF-8). Use them for text files.

Mixing the two up is a classic bug: reading text with a byte stream and hand-rolling your own encoding logic, or reading binary data with a character stream and corrupting bytes during "encoding conversion" that binary data was never meant to go through.

### The class hierarchy

```text
                     byte streams                         character streams

               InputStream (abstract)                 Reader (abstract)
               /      |        \                      /      |       \
FileInputStream  ByteArrayInputStream  BufferedInputStream   FileReader  StringReader  BufferedReader
      |                                       (decorator)         |                       (decorator)
      +-- wrapped by InputStreamReader --------------------------> (bridges byte -> char)

              OutputStream (abstract)                 Writer (abstract)
               /      |        \                      /      |       \
FileOutputStream  ByteArrayOutputStream  BufferedOutputStream  FileWriter  StringWriter  BufferedWriter
      |                                       (decorator)         |                       (decorator)
      +-- wrapped by OutputStreamWriter -------------------------> (bridges char -> byte)
```

`InputStreamReader` and `OutputStreamWriter` are the bridge classes: they wrap a byte stream and a charset, and expose it as a character stream. `FileReader` and `FileWriter` are just convenience subclasses of that bridge, hardwired to a `File` — historically they used the *platform default charset*, which is exactly why you should avoid their no-charset constructors (see below).

### InputStream / OutputStream / Reader / Writer

These four abstract classes are the roots of everything in `java.io`.

```java
import java.io.*;
import java.nio.charset.StandardCharsets;

public class BasicStreams {
    public static void main(String[] args) throws IOException {
        // Byte stream: read raw bytes from a file
        try (InputStream in = new FileInputStream("data.bin")) {
            int b;
            while ((b = in.read()) != -1) {   // read() returns -1 at end of stream
                // process one byte at a time (slow without buffering, see below)
            }
        }

        // Character stream: read text, one char at a time
        try (Reader reader = new InputStreamReader(
                new FileInputStream("notes.txt"), StandardCharsets.UTF_8)) {
            int c;
            while ((c = reader.read()) != -1) {
                // process one character
            }
        }
    }
}
```

### The decorator pattern

`java.io` is built almost entirely on the **decorator pattern**: small classes that wrap another stream and add one piece of behavior, without changing the type. You "stack" decorators to combine behaviors.

```java
import java.io.*;
import java.nio.charset.StandardCharsets;

// FileInputStream: raw bytes from disk
// -> wrapped by BufferedInputStream: adds an internal buffer (fewer OS calls)
// -> wrapped by InputStreamReader: converts bytes to chars using UTF-8
// -> wrapped by BufferedReader: adds line-based reading (readLine())
try (BufferedReader reader = new BufferedReader(
        new InputStreamReader(
                new BufferedInputStream(new FileInputStream("report.txt")),
                StandardCharsets.UTF_8))) {

    String line;
    while ((line = reader.readLine()) != null) {
        System.out.println(line);
    }
}
```

Each layer only knows about the layer directly underneath it. That is the whole point of the pattern: `BufferedReader` does not care whether its underlying `Reader` is a file, a socket, or an in-memory string.

### Why buffering matters

Every unbuffered `read()`/`write()` call on a `FileInputStream` or `FileOutputStream` typically triggers a system call to the OS. System calls are expensive compared to in-memory operations — doing one per byte is enormously wasteful.

```java
import java.io.*;

// Bad: one system call per byte. On a 1 MB file, that's ~1,000,000 syscalls.
try (InputStream in = new FileInputStream("big.dat")) {
    int b;
    while ((b = in.read()) != -1) {
        // ...
    }
}

// Good: BufferedInputStream reads a large chunk (default 8 KB) into memory once,
// then serves read() calls from that in-memory buffer. Far fewer syscalls.
try (InputStream in = new BufferedInputStream(new FileInputStream("big.dat"))) {
    int b;
    while ((b = in.read()) != -1) {
        // ...
    }
}
```

The rule of thumb in review: **any raw `FileInputStream`/`FileOutputStream`/`FileReader`/`FileWriter` that isn't wrapped in a `Buffered*` decorator is a performance smell**, unless the code already reads/writes in large chunks itself (e.g., `readAllBytes()`).

### Always specify a charset

Text is just bytes plus an agreement on how to decode them. If you don't specify a charset, Java uses the **platform default charset**, which depends on the OS, locale, and JVM flags. That means the exact same code can produce different (or garbled) output on different machines — a Linux CI server, a developer's macOS laptop, and a Windows box might all disagree.

```java
import java.io.*;
import java.nio.charset.StandardCharsets;

// Bad: charset is whatever the platform happens to default to (risky, not portable)
Writer badWriter = new FileWriter("out.txt");

// Good: charset is explicit and reproducible everywhere
Writer goodWriter = new OutputStreamWriter(
        new FileOutputStream("out.txt"), StandardCharsets.UTF_8);

// Java 11+ convenience: FileReader/FileWriter overloads that accept a Charset directly
Writer goodWriterJava11 = new FileWriter("out.txt", StandardCharsets.UTF_8);
```

Since Java 18, the JVM's default charset for most contexts is UTF-8 (JEP 400), which fixes a lot of historical foot-guns. But relying on "the default happens to be UTF-8 now" is still fragile — a reviewer should still flag missing explicit charsets, since the JVM can still be launched with `-Dfile.encoding=...` overriding it, and library code should never assume the caller's environment.

### File and its weak error reporting

`java.io.File` represents a path on the filesystem — but it is notorious for **silently returning `false` instead of throwing an exception** when something goes wrong. This makes bugs hard to diagnose because you get no information about *why* an operation failed.

```java
import java.io.File;

File file = new File("/some/protected/path/data.txt");

boolean deleted = file.delete();
if (!deleted) {
    // Was it "file doesn't exist"? "Permission denied"? "Directory not empty"?
    // File.delete() gives you NO clue. You just get `false`.
    System.out.println("Delete failed, but we don't know why.");
}

boolean created = file.mkdir();
if (!created) {
    // Same problem: could be "parent doesn't exist", "already exists",
    // "permission denied" — all collapse into a single boolean.
    System.out.println("mkdir failed, reason unknown.");
}
```

This is one of the concrete reasons `java.nio.file.Files` (covered below) exists: `Files.delete()` and `Files.createDirectory()` throw specific, informative exceptions (`NoSuchFileException`, `FileAlreadyExistsException`, `AccessDeniedException`, `DirectoryNotEmptyException`) instead of a flat boolean.

### try-with-resources for streams

All `java.io` streams implement `Closeable` (which extends `AutoCloseable`), so they work with `try-with-resources`. Forgetting to close a stream leaks file handles or sockets — on a long-running server this eventually exhausts OS file-descriptor limits.

```java
import java.io.*;
import java.nio.charset.StandardCharsets;

// Bad: if readLine() throws, the stream never gets closed
BufferedReader reader = new BufferedReader(
        new InputStreamReader(new FileInputStream("data.txt"), StandardCharsets.UTF_8));
String line = reader.readLine();
reader.close(); // never reached if an exception is thrown above

// Good: try-with-resources guarantees close(), even on exception,
// and closes multiple resources in reverse order of declaration
try (FileInputStream fis = new FileInputStream("data.txt");
     InputStreamReader isr = new InputStreamReader(fis, StandardCharsets.UTF_8);
     BufferedReader reader2 = new BufferedReader(isr)) {

    String firstLine = reader2.readLine();
    System.out.println(firstLine);
} // fis, isr, and reader2 are all closed automatically here
```

## NIO and NIO.2

"NIO" (New I/O, `java.nio`, added in Java 1.4) and "NIO.2" (the `java.nio.file` package, added in Java 7) are a different model from `java.io`. Instead of a one-directional stream of bytes, NIO works with **buffers** and **channels**, and supports non-blocking and memory-mapped I/O. NIO.2 additionally gave us the modern `Path`/`Files` API, covered in the next section.

### Buffers and channels

A **channel** (`FileChannel`, `SocketChannel`, ...) is a connection to an I/O source — a file, a socket. Unlike a `Stream`, a channel can be read from *and* written to, and (for files) supports random access via `position()`.

A **buffer** (`ByteBuffer`, `CharBuffer`, ...) is a fixed-size block of memory that channels read into and write from. You don't read byte-by-byte with NIO; you read a whole chunk into a buffer, then process the buffer.

```java
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

public class ChannelBufferExample {
    public static void main(String[] args) throws IOException {
        Path path = Path.of("data.bin");

        try (FileChannel channel = FileChannel.open(path, StandardOpenOption.READ)) {
            ByteBuffer buffer = ByteBuffer.allocate(1024); // 1 KB heap buffer

            int bytesRead = channel.read(buffer); // fills the buffer, returns count (-1 = EOF)
            System.out.println("Read " + bytesRead + " bytes");
        }
    }
}
```

### ByteBuffer: position, limit, flip, clear

A `ByteBuffer` tracks three cursors you must understand to use it correctly:

- **capacity** — total size, fixed at creation.
- **position** — where the next read/write happens; advances as you go.
- **limit** — how far you're allowed to read/write; can't go past it.

```java
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;

ByteBuffer buffer = ByteBuffer.allocate(128);

// --- WRITE mode ---
buffer.put("hello".getBytes(StandardCharsets.UTF_8));
// position = 5 (just wrote 5 bytes), limit = 128 (capacity)

// flip(): switch from writing to reading.
// Sets limit = current position, then resets position = 0.
buffer.flip();
// position = 0, limit = 5 -- ready to read exactly the 5 bytes we wrote

// --- READ mode ---
byte[] data = new byte[buffer.remaining()]; // remaining() = limit - position
buffer.get(data);
System.out.println(new String(data, StandardCharsets.UTF_8)); // "hello"
// position = 5, limit = 5

// clear(): reset for writing again.
// Sets position = 0, limit = capacity. Does NOT erase old data, just forgets it.
buffer.clear();
// position = 0, limit = 128 -- ready to write fresh data
```

Forgetting to call `flip()` before reading is one of the most common NIO bugs: you'll try to read starting at `position` (wherever writing left it) up to `limit` (still the full capacity), which usually reads garbage or nothing useful.

### Direct vs. heap buffers

```java
import java.nio.ByteBuffer;

ByteBuffer heapBuffer = ByteBuffer.allocate(1024);       // lives in JVM heap
ByteBuffer directBuffer = ByteBuffer.allocateDirect(1024); // lives outside the JVM heap
```

| | Heap buffer (`allocate`) | Direct buffer (`allocateDirect`) |
|---|---|---|
| Memory location | JVM heap | Native (OS) memory, outside GC heap |
| Allocation cost | Cheap, fast | More expensive to allocate/free |
| I/O performance | JVM may copy to a temp native buffer before an OS call | Can be passed straight to the OS, no extra copy |
| Best for | Small, short-lived, frequently allocated buffers | Large, long-lived buffers reused for repeated I/O |
| GC impact | Collected normally | Freed via cleaner mechanisms, not standard GC |

For most application code, heap buffers are fine and simpler. Direct buffers pay off for high-throughput I/O (e.g., network servers, bulk file copies) where you reuse the same buffer many times.

### Memory-mapped files

`FileChannel.map()` maps a file's contents directly into memory, so you can treat file bytes like an in-memory array — the OS handles paging data in and out as you access it. This is great for large files you need random access into (e.g., a large binary index) without reading the whole thing.

```java
import java.io.IOException;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

try (FileChannel channel = FileChannel.open(
        Path.of("large-file.dat"), StandardOpenOption.READ)) {

    MappedByteBuffer mapped = channel.map(
            FileChannel.MapMode.READ_ONLY, 0, channel.size());

    // Access any byte at any offset directly -- OS pages it in as needed
    byte firstByte = mapped.get(0);
    byte lastByte = mapped.get((int) channel.size() - 1);
}
```

Caveats: mapped regions are capped by addressable memory (practically, ~2 GB per mapping on many JVMs due to `int` indexing), and unmapping is not guaranteed to happen promptly — on some platforms the mapped file may stay locked until the buffer is garbage collected.

### Selector and non-blocking I/O

Regular blocking I/O ties up one thread per connection: the thread calls `read()` and just sits there until data arrives. That doesn't scale to thousands of connections. NIO's `Selector` lets a **single thread monitor many channels at once**, and only wakes up to handle the channels that are actually ready (readable, writable, or have a new connection) — this is the classic "one thread, many sockets" event-loop model used by things like Netty. In modern Java, this specific problem is often solved more simply with **virtual threads** (Project Loom, Java 21) instead of hand-rolled `Selector` loops, but understanding `Selector` still matters for reading legacy networking code and frameworks built on it.

### FileChannel.transferTo (zero-copy)

`transferTo` can copy data directly between channels, and on many operating systems this happens via a **zero-copy** OS call — the data never has to pass through a Java buffer in user space at all.

```java
import java.io.IOException;
import java.nio.channels.FileChannel;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

try (FileChannel source = FileChannel.open(Path.of("source.dat"), StandardOpenOption.READ);
     FileChannel destination = FileChannel.open(
             Path.of("destination.dat"),
             StandardOpenOption.CREATE, StandardOpenOption.WRITE)) {

    long transferred = 0;
    long size = source.size();
    while (transferred < size) {
        transferred += source.transferTo(transferred, size - transferred, destination);
    }
}
```

For plain file-to-file copies, though, `Files.copy(Path, Path, ...)` (NIO.2) is simpler and just as efficient in practice — reach for `transferTo` mainly when you're moving data channel-to-channel (e.g., file to socket) rather than file to file.

### When NIO actually helps

| Use case | Better fit |
|---|---|
| Simple file read/write in a small tool or CLI | `java.io` / `Files` (NIO.2) — simpler code |
| Large file, need random access to specific offsets | NIO `FileChannel` + memory mapping |
| High-throughput network server, many connections | NIO `Selector`, or virtual threads |
| Bulk copy between files or channels | `FileChannel.transferTo` / `Files.copy` |
| Everyday text/config file processing | `Files.readString`, `Files.lines` (NIO.2) |

**Reviewer's rule of thumb**: don't reach for raw `ByteBuffer`/`Channel`/`Selector` code unless there's a real reason (performance-critical I/O, non-blocking networking, memory-mapped access). For ordinary file reading and writing, NIO.2's `Files` API (next section) is simpler, less error-prone, and just as fast.

## Paths and Files

NIO.2 (Java 7+) introduced `java.nio.file.Path` and `java.nio.file.Files` to replace most uses of `java.io.File`. `Path` represents a filesystem location (it doesn't have to exist); `Files` is a utility class full of static methods that operate on `Path`s.

| | `java.io.File` | NIO.2 (`Path` / `Files`) |
|---|---|---|
| Error reporting | Booleans (`false` on failure, no reason) | Specific exceptions (`NoSuchFileException`, etc.) |
| Symbolic link support | Poor / inconsistent | First-class (`Files.isSymbolicLink`, etc.) |
| File attributes | Limited (`lastModified`, `length`) | Rich (`BasicFileAttributes`, POSIX permissions, owner) |
| Directory walking | Manual recursion | `Files.walk`, `Files.walkFileTree` |
| Watching for changes | Not supported | `WatchService` |
| Reading whole file as text | Manual stream wiring | `Files.readString` (one line) |
| Extensibility | Tied to default filesystem | Pluggable filesystem providers (e.g., zip, in-memory) |

### Creating and manipulating Paths

```java
import java.nio.file.Path;
import java.nio.file.Paths;

// Two equivalent ways to build a Path. Path.of() is preferred since Java 11
// (Paths.get() still works and is common in older code / examples).
Path p1 = Paths.get("data", "input.csv");
Path p2 = Path.of("data", "input.csv");

System.out.println(p1.equals(p2)); // true

Path base = Path.of("/home/user/project");
Path resolved = base.resolve("src/Main.java");
System.out.println(resolved); // /home/user/project/src/Main.java

Path relative = base.relativize(Path.of("/home/user/other/lib.jar"));
System.out.println(relative); // ../other/lib.jar

Path messy = Path.of("/home/user/./project/../project/src");
System.out.println(messy.normalize()); // /home/user/project/src

Path relPath = Path.of("relative/path");
System.out.println(relPath.toAbsolutePath()); // resolved against the current working directory
```

- `resolve` — joins a path onto another (like appending a subpath).
- `relativize` — computes the relative path needed to get from one path to another.
- `normalize` — removes redundant `.` and `..` segments *syntactically*, without touching the filesystem.
- `toAbsolutePath` — anchors a relative path to the current working directory (still doesn't check if it exists).

### Reading and writing whole files

```java
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;

Path file = Path.of("notes.txt");

// Read the entire file as one String (Java 11+)
String content = Files.readString(file, StandardCharsets.UTF_8);

// Write a String to a file, creating or truncating it (Java 11+)
Files.writeString(file, "Hello, NIO.2!\n", StandardCharsets.UTF_8);

// Read all lines into a List<String> -- fine for small/medium files
List<String> lines = Files.readAllLines(file, StandardCharsets.UTF_8);

// Stream lines lazily -- for large files, so you don't load everything into memory.
// IMPORTANT: Files.lines() opens a file handle that MUST be closed.
try (Stream<String> lineStream = Files.lines(file, StandardCharsets.UTF_8)) {
    long count = lineStream.filter(line -> !line.isBlank()).count();
    System.out.println("Non-blank lines: " + count);
} // stream (and its underlying file handle) closed here
```

`Files.lines()` returns a lazily-evaluated `Stream<String>` backed by an open file descriptor. If you don't close it (via try-with-resources, since `Stream` implements `AutoCloseable`), the file handle leaks — this is easy to miss because a `Stream` doesn't *look* like a resource the way an `InputStream` obviously does.

```java
import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

// Files.newBufferedReader gives you a fully-buffered Reader with a chosen charset,
// useful when you want line-by-line control instead of a Stream or a full readAllLines()
try (BufferedReader reader = Files.newBufferedReader(Path.of("big.log"), StandardCharsets.UTF_8)) {
    String line;
    while ((line = reader.readLine()) != null) {
        // process one line at a time, low memory overhead
    }
}
```

### Copy, move, delete

```java
import java.io.IOException;
import java.nio.file.CopyOption;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

Path source = Path.of("report.pdf");
Path target = Path.of("backup/report.pdf");

// Copy, overwriting the target if it already exists
Files.copy(source, target, StandardCopyOption.REPLACE_EXISTING);

// Move (rename) a file; ATOMIC_MOVE guarantees no partial/half-written result is ever visible
Files.move(source, target, StandardCopyOption.ATOMIC_MOVE);

// Delete: throws NoSuchFileException if missing (unlike File.delete()'s silent `false`)
Files.delete(target);

// deleteIfExists: same, but does nothing (no exception) if the file is already gone
Files.deleteIfExists(target);
```

### Files.walk vs. Files.walkFileTree

Both traverse a directory tree, but they differ in flexibility and resource handling.

```java
import java.io.IOException;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.stream.Stream;

// Files.walk: returns a lazy Stream<Path> of every file/dir under root.
// Simple for filtering/collecting, but MUST be closed (it holds an open directory handle).
try (Stream<Path> paths = Files.walk(Path.of("src"))) {
    paths.filter(p -> p.toString().endsWith(".java"))
         .forEach(System.out::println);
}

// Files.walkFileTree: callback-based (visitor pattern), more control,
// e.g. skipping whole subtrees, handling errors per-file, computing directory sizes.
Files.walkFileTree(Path.of("src"), new SimpleFileVisitor<Path>() {
    @Override
    public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) {
        if (file.toString().endsWith(".class")) {
            return FileVisitResult.CONTINUE; // could delete, count, etc.
        }
        return FileVisitResult.CONTINUE;
    }

    @Override
    public FileVisitResult visitFileFailed(Path file, IOException exc) {
        System.err.println("Could not visit: " + file + " (" + exc + ")");
        return FileVisitResult.CONTINUE; // don't abort the whole walk on one bad file
    }
});
```

Use `Files.walk` for quick stream-style filtering/collecting. Use `Files.walkFileTree` when you need fine-grained control (skip directories, react to errors per file, avoid loading the whole path list into a stream pipeline first).

### Temp files, DirectoryStream, WatchService, attributes

```java
import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Set;

// Secure temp file: created with a random name, and on POSIX systems Files.createTempFile
// sets permissions so only the owner can read/write it (0600) -- avoids TOCTOU race
// conditions and world-readable secrets that java.io.File.createTempFile historically allowed.
Path tempFile = Files.createTempFile("upload-", ".tmp");
System.out.println(tempFile); // e.g. /tmp/upload-1234567890.tmp

// DirectoryStream: lazily iterate direct children of a directory (not recursive),
// with an optional glob filter. Must be closed -- holds a directory handle.
try (DirectoryStream<Path> stream = Files.newDirectoryStream(Path.of("."), "*.java")) {
    for (Path entry : stream) {
        System.out.println(entry.getFileName());
    }
}

// PosixFilePermissions: set exact rwx bits when creating a file, useful for
// "this file must only be readable by its owner" security requirements
Set<PosixFilePermission> perms = PosixFilePermissions.fromString("rw-------");
FileAttribute<Set<PosixFilePermission>> attr = PosixFilePermissions.asFileAttribute(perms);
Path secretFile = Files.createFile(Path.of("secret.key"), attr);
```

```java
import java.io.IOException;
import java.nio.file.*;

// WatchService: get notified when files in a directory change, without polling.
// Common use: reload config when a file on disk is edited.
try (WatchService watchService = FileSystems.getDefault().newWatchService()) {
    Path dir = Path.of("config");
    dir.register(watchService,
            StandardWatchEventKinds.ENTRY_CREATE,
            StandardWatchEventKinds.ENTRY_MODIFY,
            StandardWatchEventKinds.ENTRY_DELETE);

    WatchKey key = watchService.take(); // blocks until an event occurs
    for (WatchEvent<?> event : key.pollEvents()) {
        System.out.println(event.kind() + ": " + event.context());
    }
    key.reset(); // must call this to keep receiving events on this key
}
```

### Atomic writes: temp file + ATOMIC_MOVE

A common bug: writing directly to a target file. If the process crashes mid-write, readers can see a half-written, corrupted file. The safe pattern is to write to a temp file first, then atomically rename it into place — readers only ever see the old complete file or the new complete file, never a partial one.

```java
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

public class AtomicWrite {
    public static void writeAtomically(Path target, String content) throws IOException {
        // temp file in the SAME directory as target (atomic move requires same filesystem)
        Path tempFile = Files.createTempFile(target.getParent(), "tmp-", ".swap");
        try {
            Files.writeString(tempFile, content, StandardCharsets.UTF_8);
            // ATOMIC_MOVE: the OS guarantees this rename is indivisible --
            // any reader sees either the fully-old or fully-new file, never a mix
            Files.move(tempFile, target,
                    StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(tempFile); // cleanup if the move never happened
        }
    }
}
```

### Path traversal security (`../`)

If any part of a file path comes from user input, an attacker can inject `../` segments to escape the intended directory and read or write files elsewhere on the system — a classic **path traversal** vulnerability.

```java
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

// Bad: naively trusts user input, allows "../../../etc/passwd" style attacks
public byte[] readUserFileUnsafe(String userSuppliedName) throws IOException {
    Path path = Path.of("/var/app/uploads").resolve(userSuppliedName);
    return Files.readAllBytes(path); // could read ANY file the process can access
}

// Good: resolve, normalize, then verify the result is still inside the allowed root
public byte[] readUserFileSafe(String userSuppliedName) throws IOException {
    Path root = Path.of("/var/app/uploads").toRealPath(); // resolves symlinks too
    Path requested = root.resolve(userSuppliedName).normalize();

    if (!requested.startsWith(root)) {
        throw new SecurityException("Path traversal attempt: " + userSuppliedName);
    }
    return Files.readAllBytes(requested);
}
```

The check must happen **after** `resolve` + `normalize` (and ideally after resolving symlinks with `toRealPath()`), never before — normalizing first and checking after is the part reviewers most often see done wrong or skipped entirely.

## Serialization

**Serialization** converts an object (and everything it references — its **object graph**) into a byte stream, so it can be saved to disk or sent over a network. **Deserialization** reverses the process, rebuilding the object graph from bytes.

```java
import java.io.*;

record Point(int x, int y) implements Serializable {}

public class SerializationBasics {
    public static void main(String[] args) throws IOException, ClassNotFoundException {
        Point point = new Point(3, 4);

        // Serialize to a byte array (could just as easily be a FileOutputStream)
        byte[] bytes;
        try (ByteArrayOutputStream baos = new ByteArrayOutputStream();
             ObjectOutputStream oos = new ObjectOutputStream(baos)) {
            oos.writeObject(point);
            bytes = baos.toByteArray();
        }

        // Deserialize back into an object
        try (ObjectInputStream ois = new ObjectInputStream(new ByteArrayInputStream(bytes))) {
            Point restored = (Point) ois.readObject();
            System.out.println(restored); // Point[x=3, y=4]
        }
    }
}
```

To be serializable, a class just implements the empty marker interface `Serializable` — there are no methods to implement. The JVM uses reflection to walk the object's fields and write them out.

### serialVersionUID

Every `Serializable` class has an implicit **version ID** used to check that a serialized object matches the class trying to deserialize it. If you don't declare it explicitly, the JVM computes one automatically from the class's structure (fields, methods, etc.) — which means **any change to the class**, even something as harmless as adding a method, can change the computed ID and break deserialization of old data with an `InvalidClassException`.

```java
import java.io.Serializable;

public class User implements Serializable {
    // Always declare this explicitly. It lets you evolve the class
    // (add fields, add methods) without silently breaking old serialized data.
    private static final long serialVersionUID = 1L;

    private String username;
    private int age;

    // getters/setters omitted
}
```

**Rule for review**: any `Serializable` class without an explicit `serialVersionUID` is a red flag — it works today but is a landmine for future changes.

### transient

Marking a field `transient` excludes it from serialization. Use it for fields that shouldn't (or can't) be persisted: caches, derived/computed values, `Thread`/`Socket`/`Connection` handles, secrets that shouldn't be written to disk.

```java
import java.io.Serializable;

public class Session implements Serializable {
    private static final long serialVersionUID = 1L;

    private String sessionId;
    private transient String cachedDisplayName; // recomputed on demand, not persisted
    private transient char[] temporaryToken;    // sensitive; must never hit disk

    // after deserialization, cachedDisplayName and temporaryToken are null/zero-valued,
    // NOT whatever they held before serialization
}
```

After deserialization, `transient` fields are reset to their default value (`null`, `0`, `false`, etc.) — the constructor is **not** called during deserialization, so you can't rely on constructor logic to re-populate them; use `readObject` (below) if you need to recompute them.

### Custom writeObject / readObject

You can hook into the serialization process by declaring `private` methods with these exact signatures. The JVM calls them via reflection if present.

```java
import java.io.*;

public class Account implements Serializable {
    private static final long serialVersionUID = 1L;

    private String owner;
    private transient double cachedBalance; // recomputed after load, not stored directly

    private void writeObject(ObjectOutputStream out) throws IOException {
        out.defaultWriteObject(); // writes all non-transient fields normally
        out.writeDouble(computeAuditedBalance()); // then write something extra
    }

    private void readObject(ObjectInputStream in) throws IOException, ClassNotFoundException {
        in.defaultReadObject(); // reads all non-transient fields normally
        this.cachedBalance = in.readDouble(); // then read the extra value back
    }

    private double computeAuditedBalance() {
        return 0.0; // placeholder for real logic
    }
}
```

Typical uses: encrypting a sensitive field before writing it out (and decrypting on read), validating invariants after loading untrusted data, or handling fields that aren't naturally serializable (e.g., writing a `Map`'s contents manually instead of the map object itself).

### writeReplace and readResolve

These let a class substitute a *different* object during serialization (`writeReplace`) or deserialization (`readResolve`) — commonly used to enforce singletons or to serialize a stable "proxy" representation instead of the real object.

```java
import java.io.*;

public class Singleton implements Serializable {
    private static final long serialVersionUID = 1L;

    public static final Singleton INSTANCE = new Singleton();

    private Singleton() {}

    // Without readResolve, deserialization would create a SECOND, distinct
    // instance via reflection, breaking the singleton guarantee.
    protected Object readResolve() {
        return INSTANCE; // replace the deserialized instance with the canonical one
    }
}
```

`enum` types are serialization-safe singletons for free (the JVM guarantees enum constants deserialize to the same instance) — that's one more reason the enum singleton pattern is generally preferred over a hand-rolled one.

### Why Java serialization is a security disaster

`ObjectInputStream.readObject()` does something dangerous by design: it instantiates classes and calls code (`readObject`, constructors of superclasses in some cases, `readResolve`, etc.) **based purely on bytes that came from the input** — before your application logic gets any say in whether that data should be trusted.

If an attacker controls the byte stream, they can craft input referencing classes already on your classpath whose `readObject`/`finalize`/other methods have *side effects* — chaining several such classes together forms a **gadget chain** that can lead to arbitrary code execution, denial of service, or data exfiltration, all before your code ever sees the resulting object. This class of vulnerability caused real, high-severity CVEs in major frameworks throughout the 2010s (e.g., in Apache Commons Collections, WebLogic, Jenkins) and is why security guidance now generally treats **deserializing untrustworthy data with native Java serialization as unsafe by default.**

```java
import java.io.*;

// DANGEROUS: never deserialize bytes from an untrusted source (network request,
// user upload, unauthenticated input) with plain ObjectInputStream.
public Object deserializeUntrusted(byte[] attackerControlledBytes) throws Exception {
    try (ObjectInputStream ois = new ObjectInputStream(
            new ByteArrayInputStream(attackerControlledBytes))) {
        return ois.readObject(); // could trigger a gadget chain before you ever inspect it
    }
}
```

### Serialization filters (ObjectInputFilter, JDK 9+)

If you must use Java serialization (e.g., legacy systems), `ObjectInputFilter` lets you restrict *which classes* are allowed to be deserialized, rejecting everything else before it's fully constructed.

```java
import java.io.*;

ObjectInputFilter filter = ObjectInputFilter.Config.createFilter(
        "com.example.model.*;java.base/*;!*"); // allow our package + JDK basics, reject all else

try (ObjectInputStream ois = new ObjectInputStream(new FileInputStream("data.ser"))) {
    ois.setObjectInputFilter(filter);
    Object result = ois.readObject(); // throws InvalidClassException if a disallowed class appears
}
```

You can also set a process-wide default filter via the `jdk.serialFilter` system property or security property, so every `ObjectInputStream` in the JVM is protected even in code you don't control.

### The modern recommendation

Given the risks and the format's other downsides (JVM-only, brittle across class changes, opaque binary format that's hard to debug or audit), the strong modern recommendation is:

**Avoid Java's built-in `Serializable` for anything crossing a trust boundary (network, files from outside your process, another service). Prefer JSON (Jackson, Gson) or a schema-based binary format (Protocol Buffers, Avro) instead.**

```java
// Instead of: implements Serializable + ObjectOutputStream/ObjectInputStream
// Prefer something like (using a JSON library):
//
//   String json = objectMapper.writeValueAsString(user);
//   User user = objectMapper.readValue(json, User.class);
//
// JSON/protobuf are language-agnostic, human-inspectable (JSON) or
// schema-validated (protobuf), and don't execute arbitrary code on parse.
```

`Serializable` still shows up legitimately for things that never leave a single trusted JVM process — e.g., objects passed between nodes in a cluster you fully control, or short-lived in-memory caching frameworks — but even there, many teams now default to a safer format out of caution and long-term maintainability.

## Externalizable

`Externalizable` is an alternative to `Serializable` that extends it and hands you **full manual control** over the byte format — you write every field yourself, in `writeExternal`, and read every field yourself, in `readExternal`.

```java
import java.io.*;

public class Product implements Externalizable {
    private String name;
    private double price;

    // Externalizable REQUIRES a public no-arg constructor.
    // The JVM calls this constructor first, THEN calls readExternal()
    // to populate the fields -- unlike Serializable, which uses reflection
    // to bypass the constructor entirely.
    public Product() {}

    public Product(String name, double price) {
        this.name = name;
        this.price = price;
    }

    @Override
    public void writeExternal(ObjectOutput out) throws IOException {
        out.writeUTF(name);
        out.writeDouble(price);
    }

    @Override
    public void readExternal(ObjectInput in) throws IOException {
        name = in.readUTF();
        price = in.readDouble();
    }

    @Override
    public String toString() {
        return "Product{name='" + name + "', price=" + price + '}';
    }
}
```

```java
import java.io.*;

public class ExternalizableDemo {
    public static void main(String[] args) throws IOException, ClassNotFoundException {
        Product product = new Product("Widget", 9.99);

        byte[] bytes;
        try (ByteArrayOutputStream baos = new ByteArrayOutputStream();
             ObjectOutputStream oos = new ObjectOutputStream(baos)) {
            oos.writeObject(product);
            bytes = baos.toByteArray();
        }

        try (ObjectInputStream ois = new ObjectInputStream(new ByteArrayInputStream(bytes))) {
            Product restored = (Product) ois.readObject(); // calls Product() then readExternal()
            System.out.println(restored); // Product{name='Widget', price=9.99}
        }
    }
}
```

### Serializable vs. Externalizable

| | `Serializable` | `Externalizable` |
|---|---|---|
| Implementation effort | None (marker interface) — JVM uses reflection | Must implement `writeExternal`/`readExternal` yourself |
| No-arg constructor | Not required (fields set via reflection, bypassing constructors) | **Required** and must be `public` |
| Performance | Slower — reflection over all fields | Can be faster — you control exactly what's written, no reflection over field metadata |
| Format control | Default format mirrors class layout; can customize via `writeObject`/`readObject` | Total control — you decide every byte |
| Versioning safety | `serialVersionUID` + default mechanism handles a lot automatically | You must handle format evolution entirely yourself |
| Risk of mistakes | Lower — JVM does the heavy lifting | Higher — a forgotten field, wrong read order, or missing no-arg constructor breaks everything silently or loudly |
| Typical use | Most day-to-day cases (when serialization is genuinely needed) | Performance-critical or space-constrained scenarios, or when you need a stable, hand-controlled wire format |

The trade-off in one sentence: `Externalizable` gives you speed and control, but takes away every safety net — get the field order wrong between `writeExternal` and `readExternal`, and you'll silently read corrupted data with no compiler or JVM warning.

## Common Code-Review Interview Pitfalls

1. **Not closing streams / using try-with-resources.**
   *Why it matters*: leaked file handles or sockets eventually exhaust OS limits, causing mysterious failures under load.
   ```java
   // Before
   FileInputStream in = new FileInputStream("data.txt");
   // ... in.close() forgotten, or skipped if an exception is thrown above it

   // After
   try (FileInputStream in = new FileInputStream("data.txt")) {
       // ...
   }
   ```

2. **Using platform-default charset instead of an explicit one.**
   *Why it matters*: the exact same code produces different bytes/text on different machines or JVM configurations.
   ```java
   // Before
   Writer w = new FileWriter("out.txt");

   // After
   Writer w = new FileWriter("out.txt", StandardCharsets.UTF_8);
   ```

3. **Reading byte-by-byte or line-by-line without buffering.**
   *Why it matters*: each unbuffered call can trigger a system call — brutal performance hit on large files.
   ```java
   // Before
   InputStream in = new FileInputStream("big.dat");

   // After
   InputStream in = new BufferedInputStream(new FileInputStream("big.dat"));
   ```

4. **Trusting `File.delete()` / `File.mkdir()` booleans without understanding they hide the failure reason.**
   *Why it matters*: silent `false` gives no diagnostic information; bugs become "it just doesn't work" reports.
   ```java
   // Before
   boolean ok = file.delete();

   // After
   Files.delete(path); // throws NoSuchFileException, AccessDeniedException, etc.
   ```

5. **Not closing `Files.lines()` or `Files.walk()` streams.**
   *Why it matters*: these hold an open file/directory handle under the hood even though they "look like" a plain in-memory `Stream`.
   ```java
   // Before
   Files.lines(path).forEach(System.out::println); // handle never released

   // After
   try (Stream<String> lines = Files.lines(path)) {
       lines.forEach(System.out::println);
   }
   ```

6. **Building file paths from user input without validating against path traversal (`../`).**
   *Why it matters*: lets attackers read/write arbitrary files outside the intended directory.
   ```java
   // Before
   Path p = Path.of("/uploads").resolve(userInput);

   // After
   Path root = Path.of("/uploads").toRealPath();
   Path p = root.resolve(userInput).normalize();
   if (!p.startsWith(root)) throw new SecurityException("Traversal attempt");
   ```

7. **Writing directly to a target file instead of write-temp-then-atomic-move.**
   *Why it matters*: a crash mid-write leaves a corrupted, half-written file that readers may see.
   ```java
   // Before
   Files.writeString(target, content);

   // After
   Path tmp = Files.createTempFile(target.getParent(), "tmp-", ".swap");
   Files.writeString(tmp, content);
   Files.move(tmp, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
   ```

8. **`Serializable` class with no explicit `serialVersionUID`.**
   *Why it matters*: any future change to the class can change the auto-computed ID, breaking deserialization of previously-saved data.
   ```java
   // Before
   class User implements Serializable { String name; }

   // After
   class User implements Serializable {
       private static final long serialVersionUID = 1L;
       String name;
   }
   ```

9. **Deserializing untrusted input with plain `ObjectInputStream`.**
   *Why it matters*: this is the entry point for deserialization gadget-chain attacks — potential remote code execution.
   ```java
   // Before
   Object obj = new ObjectInputStream(untrustedInputStream).readObject();

   // After: use a JSON/protobuf library instead, or at minimum add an ObjectInputFilter
   ObjectInputStream ois = new ObjectInputStream(untrustedInputStream);
   ois.setObjectInputFilter(ObjectInputFilter.Config.createFilter("com.example.*;!*"));
   Object obj = ois.readObject();
   ```

10. **Forgetting sensitive fields need `transient`.**
    *Why it matters*: secrets, tokens, or passwords silently end up serialized to disk or over the wire.
    ```java
    // Before
    class Session implements Serializable { String authToken; }

    // After
    class Session implements Serializable { transient String authToken; }
    ```

11. **`Externalizable` class missing the required public no-arg constructor.**
    *Why it matters*: deserialization throws `InvalidClassException` at runtime — the class *looks* correct until you actually try to deserialize it.
    ```java
    // Before
    class Product implements Externalizable {
        Product(String name) { this.name = name; } // only constructor

    // After
        public Product() {}              // required
        Product(String name) { this.name = name; }
    ```

12. **Mismatched field order between `writeExternal` and `readExternal`.**
    *Why it matters*: `Externalizable` has zero safety net — a swapped read order silently produces corrupted objects with no exception.
    ```java
    // Before: writeExternal writes name then price, readExternal reads price then name
    out.writeUTF(name); out.writeDouble(price);
    price = in.readDouble(); name = in.readUTF(); // WRONG ORDER

    // After: keep read/write order identical, ideally documented
    out.writeUTF(name); out.writeDouble(price);
    name = in.readUTF(); price = in.readDouble();
    ```

13. **Using `ByteBuffer` without calling `flip()` before reading.**
    *Why it matters*: reading resumes from wherever `position` was left after writing, producing empty or garbage results instead of the intended data.
    ```java
    // Before
    buffer.put(data);
    buffer.get(result); // reads from wrong position, likely returns nothing useful

    // After
    buffer.put(data);
    buffer.flip(); // limit = position, position = 0
    buffer.get(result);
    ```

14. **Using `java.io.File.createTempFile` for sensitive data instead of NIO.2's secure temp file creation.**
    *Why it matters*: `File.createTempFile` predictable/world-readable permissions on some platforms invite race conditions and information leaks; `Files.createTempFile` sets owner-only permissions on POSIX systems by default.
    ```java
    // Before
    File temp = File.createTempFile("upload", ".tmp");

    // After
    Path temp = Files.createTempFile("upload-", ".tmp");
    ```

15. **Choosing `Files.walk` (or recursive `File` listing) when directory traversal needs error handling or early termination per subtree.**
    *Why it matters*: a single unreadable subdirectory can blow up an entire `Stream` pipeline with no way to skip just that branch; `walkFileTree` lets you react per-file/per-directory and continue.
    ```java
    // Before
    Files.walk(root).forEach(this::process); // one bad file aborts everything

    // After
    Files.walkFileTree(root, new SimpleFileVisitor<Path>() {
        @Override
        public FileVisitResult visitFileFailed(Path file, IOException exc) {
            log.warn("Skipping unreadable file: {}", file);
            return FileVisitResult.CONTINUE;
        }
        @Override
        public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) {
            process(file);
            return FileVisitResult.CONTINUE;
        }
    });
    ```
