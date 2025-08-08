# 30-Day Java & Spring Mastery Plan

A 30-day, project-based learning plan to master **Java** and the **Spring framework** to a senior-engineer level. Each day is a focused, roughly 1-hour project that builds on the previous days, so concepts compound as you go.

Every day teaches three things at once:

- **A Java / language skill** — modern Java idioms, concurrency, the JVM, and more.
- **A database or distributed-systems concept** — WALs, B-Trees, replication, consensus, and friends.
- **A common library or tool** — the everyday tooling of the Java ecosystem (Maven, Spring, testing, etc.).

## Structure

```
.
├── index.html        # Interactive plan viewer (the place to start)
├── day1/
│   └── CHALLENGE.md  # Day 1 challenge: topics, difficulty, step-by-step guide
├── day2/
│   └── CHALLENGE.md
...
└── day30/
    └── CHALLENGE.md
```

Each `dayX/CHALLENGE.md` begins with a header summarizing the project, the Java skills, the DB/distributed-systems concept, the library/tool, and a difficulty rating — followed by detailed, step-by-step instructions and concept primers.

## Getting started

1. Open `index.html` in your browser for a LeetCode-style view of the plan. The list of days is on the left; click a day to load its challenge, and mark days as done as you finish them.

   Because the page loads the Markdown files dynamically, serve the folder over HTTP rather than opening the file directly:

   ```bash
   python3 -m http.server 8000
   # then visit http://localhost:8000
   ```

2. Or just read any `dayX/CHALLENGE.md` directly and start building.

## Requirements

- **JDK 21** or later
- **Maven** (introduced and set up on Day 1)
