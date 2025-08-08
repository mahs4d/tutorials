# 9. Date, Time & Localization

Dates, times, and text that must work in more than one country trip up even experienced Java developers. This chapter covers the modern `java.time` API, how to format and parse dates safely, and how to write code that behaves correctly for users in different languages, countries, and time zones. By the end you should be able to spot the classic review-time mistakes: mutable date bugs, wrong time zone assumptions, locale-sensitive string comparisons, and formatter thread-safety issues.

## Table of Contents

- [Date and Time API (java.time)](#date-and-time-api-javatime)
  - [Why the old `Date`/`Calendar`/`SimpleDateFormat` were replaced](#why-the-old-datecalendarsimpledateformat-were-replaced)
  - [The core types](#the-core-types)
  - [Immutability and the fluent `plus`/`minus`/`with` API](#immutability-and-the-fluent-plusminuswith-api)
  - [`TemporalAdjusters`](#temporaladjusters)
  - [`ChronoUnit.between`](#chronounitbetween)
  - [`Clock` and testable time](#clock-and-testable-time)
  - [DST transitions: gaps and overlaps](#dst-transitions-gaps-and-overlaps)
  - [Comparing instants vs local times](#comparing-instants-vs-local-times)
  - [Storing time in a database](#storing-time-in-a-database)
- [Formatting and Parsing](#formatting-and-parsing)
  - [`DateTimeFormatter` predefined formats](#datetimeformatter-predefined-formats)
  - [`ofPattern` and custom patterns](#ofpattern-and-custom-patterns)
  - [`ofLocalizedDate` and locale-aware styles](#oflocalizeddate-and-locale-aware-styles)
  - [`DateTimeFormatterBuilder`](#datetimeformatterbuilder)
  - [Strict vs lenient `ResolverStyle`](#strict-vs-lenient-resolverstyle)
  - [ISO-8601](#iso-8601)
  - [The `YYYY` vs `yyyy` week-year bug](#the-yyyy-vs-yyyy-week-year-bug)
  - [Parsing failures and `DateTimeParseException`](#parsing-failures-and-datetimeparseexception)
  - [Thread safety: `DateTimeFormatter` vs `SimpleDateFormat`](#thread-safety-datetimeformatter-vs-simpledateformat)
- [Internationalization (i18n)](#internationalization-i18n)
  - [`Locale`](#locale)
  - [`ResourceBundle` and property files](#resourcebundle-and-property-files)
  - [`MessageFormat`: plurals and argument indexes](#messageformat-plurals-and-argument-indexes)
  - [`NumberFormat` and `Currency`](#numberformat-and-currency)
  - [Collation with `Collator`](#collation-with-collator)
  - [`String.format` with a `Locale`](#stringformat-with-a-locale)
  - [`toUpperCase()` and the Turkish dotless-i problem](#touppercase-and-the-turkish-dotless-i-problem)
  - [Character encoding and UTF-8 as the default charset](#character-encoding-and-utf-8-as-the-default-charset)
  - [`Normalizer` for Unicode](#normalizer-for-unicode)
  - [Right-to-left text, code points, and grapheme clusters](#right-to-left-text-code-points-and-grapheme-clusters)
- [Localization (l10n)](#localization-l10n)
  - [Organizing bundles and fallback rules](#organizing-bundles-and-fallback-rules)
  - [Pseudo-localization](#pseudo-localization)
  - [Testing with `-Duser.language`](#testing-with--duserlanguage)
  - [CLDR as the default locale data provider](#cldr-as-the-default-locale-data-provider)
- [Common Code-Review Interview Pitfalls](#common-code-review-interview-pitfalls)

---

## Date and Time API (java.time)

### Why the old `Date`/`Calendar`/`SimpleDateFormat` were replaced

Before Java 8, date and time handling used `java.util.Date`, `java.util.Calendar`, and `java.text.SimpleDateFormat`. These classes had deep design problems:

- **Mutable**: A `Date` object can be changed after creation. Passing one to another method risks it being silently mutated.
- **Confusing month numbering**: Months in `Calendar` are zero-based (January is `0`), a constant source of off-by-one bugs.
- **Not thread-safe**: `SimpleDateFormat` is not safe to share across threads, but people often make it a `static` field "for performance" and get corrupted output under load.
- **Poor API design**: `Date` mixes up "a point in time" with "a calendar date," which are conceptually different things.
- **No clear time zone model**: It was easy to write code that silently used the JVM's default time zone.

`java.time` (JSR-310, led by Stephen Colebourne, the author of Joda-Time) fixed all of this: every type is immutable, thread-safe, and clearly named for what it represents.

```java
import java.util.Date;
import java.util.Calendar;

public class LegacyProblems {
    public static void main(String[] args) {
        Date date = new Date();
        Calendar cal = Calendar.getInstance();
        cal.set(2026, Calendar.JANUARY, 15); // JANUARY == 0, easy to get wrong
        System.out.println(cal.getTime());
        // Output (example): Thu Jan 15 00:00:00 UTC 2026

        // Mutation trap: passing "date" elsewhere lets other code change it.
        Date copy = date;
        copy.setTime(0L); // also changes the original "date" reference!
        System.out.println(date.getTime());
        // Output: 0  <-- surprising, "date" was mutated through "copy"
    }
}
```

| Legacy type | Modern replacement | Notes |
|---|---|---|
| `java.util.Date` | `Instant` (point in time) or `LocalDateTime` (no zone) | `Date` conflated "instant" and "calendar" concepts |
| `java.util.Calendar` | `ZonedDateTime` | Zero-based months gone; clear zone handling |
| `java.util.GregorianCalendar` | `ZonedDateTime` / `LocalDate` | |
| `java.text.SimpleDateFormat` | `DateTimeFormatter` | Immutable and thread-safe |
| `java.sql.Date` / `Timestamp` | `LocalDate` / `Instant` (via JDBC 4.2+ `getObject`) | Modern JDBC drivers support `java.time` directly |
| `TimeZone` | `ZoneId` / `ZoneOffset` | |

### The core types

`java.time` splits "date and time" into precise, single-purpose types. Picking the right one is a common review topic.

| Type | Represents | Example use case |
|---|---|---|
| `LocalDate` | Date only, no time, no zone | Birthdate, invoice date |
| `LocalTime` | Time only, no date, no zone | Store opening time (09:00) |
| `LocalDateTime` | Date + time, no zone | "Meeting at 2026-08-07T14:00" in an unspecified zone |
| `ZonedDateTime` | Date + time + full time zone rules (`ZoneId`) | Flight departure time in `Europe/Amsterdam` |
| `OffsetDateTime` | Date + time + fixed UTC offset (no DST rules) | Timestamps in APIs / logs that need an offset but not a full zone |
| `Instant` | A point on the machine timeline (nanoseconds since epoch) | Event timestamps, "now" for logging |
| `Duration` | Amount of time in seconds/nanoseconds | "Timeout after 30 seconds" |
| `Period` | Amount of time in years/months/days | "Subscription lasts 1 year" |
| `Year` | A calendar year, e.g. 2026 | Leap-year checks |
| `YearMonth` | Year + month, no day | Credit card expiry (MM/YYYY) |
| `MonthDay` | Month + day, no year | Recurring anniversary, without a year |
| `DayOfWeek` | Enum: MONDAY..SUNDAY | "Is this a business day?" |
| `ZoneId` | A geographical/political time zone, e.g. `Europe/Berlin` | Zone-aware scheduling |
| `ZoneOffset` | A fixed offset from UTC, e.g. `+02:00` | Offset math without DST rules |

```java
import java.time.*;

public class CoreTypesDemo {
    public static void main(String[] args) {
        LocalDate date = LocalDate.of(2026, 8, 7);
        LocalTime time = LocalTime.of(14, 30);
        LocalDateTime dateTime = LocalDateTime.of(date, time);
        ZonedDateTime zoned = dateTime.atZone(ZoneId.of("Europe/Amsterdam"));
        OffsetDateTime offset = zoned.toOffsetDateTime();
        Instant instant = zoned.toInstant();

        System.out.println(date);      // 2026-08-07
        System.out.println(time);      // 14:30
        System.out.println(dateTime);  // 2026-08-07T14:30
        System.out.println(zoned);     // 2026-08-07T14:30+02:00[Europe/Amsterdam]
        System.out.println(offset);    // 2026-08-07T14:30+02:00
        System.out.println(instant);   // 2026-08-07T12:30:00Z

        Duration meetingLength = Duration.ofMinutes(45);
        Period subscription = Period.ofYears(1);
        System.out.println(meetingLength); // PT45M
        System.out.println(subscription);  // P1Y

        YearMonth expiry = YearMonth.of(2027, 12);
        MonthDay anniversary = MonthDay.of(6, 15);
        DayOfWeek dow = date.getDayOfWeek();
        System.out.println(expiry);      // 2027-12
        System.out.println(anniversary); // --06-15
        System.out.println(dow);         // FRIDAY
    }
}
```

### Immutability and the fluent `plus`/`minus`/`with` API

Every `java.time` type is immutable: methods like `plusDays`, `minusMonths`, and `withYear` return a **new** object instead of mutating the original. This removes an entire category of bugs where a shared date gets silently changed. It also means you must always use the return value — a very common code-review pitfall is calling `date.plusDays(1);` and discarding the result.

```java
import java.time.LocalDate;

public class ImmutabilityDemo {
    public static void main(String[] args) {
        LocalDate original = LocalDate.of(2026, 8, 7);

        LocalDate nextWeek = original.plusWeeks(1);
        LocalDate lastYear = original.minusYears(1);
        LocalDate changedYear = original.withYear(2030);

        System.out.println(original);    // 2026-08-07  (unchanged!)
        System.out.println(nextWeek);    // 2026-08-14
        System.out.println(lastYear);    // 2025-08-07
        System.out.println(changedYear); // 2030-08-07

        // Fluent chaining - each call returns a new instance
        LocalDate result = original.plusMonths(2).minusDays(3).withDayOfMonth(1);
        System.out.println(result); // 2026-10-01
    }
}
```

### `TemporalAdjusters`

`TemporalAdjusters` provides ready-made rules for common "find this special date" queries, like "next Monday" or "last day of the month." You can also write your own adjuster as a lambda.

```java
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.temporal.TemporalAdjusters;

public class AdjustersDemo {
    public static void main(String[] args) {
        LocalDate date = LocalDate.of(2026, 8, 7); // a Friday

        LocalDate nextMonday = date.with(TemporalAdjusters.next(DayOfWeek.MONDAY));
        LocalDate lastDayOfMonth = date.with(TemporalAdjusters.lastDayOfMonth());
        LocalDate firstDayOfNextMonth = date.with(TemporalAdjusters.firstDayOfNextMonth());

        System.out.println(nextMonday);          // 2026-08-10
        System.out.println(lastDayOfMonth);       // 2026-08-31
        System.out.println(firstDayOfNextMonth);  // 2026-09-01

        // Custom adjuster: move to the next even day of the month
        LocalDate nextEvenDay = date.with(temporal -> {
            LocalDate d = LocalDate.from(temporal);
            do {
                d = d.plusDays(1);
            } while (d.getDayOfMonth() % 2 != 0);
            return d;
        });
        System.out.println(nextEvenDay); // 2026-08-08
    }
}
```

### `ChronoUnit.between`

`ChronoUnit` lets you measure the difference between two temporal objects in a specific unit (days, hours, years, etc.). It is often clearer than manually computing `Period` or `Duration` when you just need a single number.

```java
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;

public class ChronoUnitDemo {
    public static void main(String[] args) {
        LocalDate start = LocalDate.of(2026, 1, 1);
        LocalDate end = LocalDate.of(2026, 8, 7);

        long daysBetween = ChronoUnit.DAYS.between(start, end);
        long monthsBetween = ChronoUnit.MONTHS.between(start, end);

        System.out.println(daysBetween);   // 218
        System.out.println(monthsBetween); // 7

        LocalDateTime t1 = LocalDateTime.of(2026, 8, 7, 9, 0);
        LocalDateTime t2 = LocalDateTime.of(2026, 8, 7, 17, 30);
        long minutesWorked = ChronoUnit.MINUTES.between(t1, t2);
        System.out.println(minutesWorked); // 510
    }
}
```

### `Clock` and testable time

Calling `LocalDate.now()` or `Instant.now()` directly inside business logic makes that logic hard to unit test — the result changes every time you run it. `Clock` is an abstraction over "the current time" that you can inject and replace with a fixed clock in tests.

```java
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;

public class ClockDemo {

    // Production code depends on a Clock, not on LocalDate.now() directly
    static class InvoiceService {
        private final Clock clock;

        InvoiceService(Clock clock) {
            this.clock = clock;
        }

        LocalDate today() {
            return LocalDate.now(clock);
        }
    }

    public static void main(String[] args) {
        // Real usage: system clock
        InvoiceService realService = new InvoiceService(Clock.systemUTC());
        System.out.println(realService.today()); // e.g. 2026-08-07

        // Test usage: fixed clock, deterministic and repeatable
        Clock fixedClock = Clock.fixed(
                Instant.parse("2026-01-01T00:00:00Z"), ZoneOffset.UTC);
        InvoiceService testService = new InvoiceService(fixedClock);
        System.out.println(testService.today()); // 2026-01-01 (always, in tests)
    }
}
```

### DST transitions: gaps and overlaps

Daylight Saving Time (DST) creates two tricky situations in local time:

- **Gap**: Clocks jump forward, and some local times never occur (e.g. 02:30 does not exist the night clocks spring forward).
- **Overlap**: Clocks jump backward, and some local times occur twice (e.g. 02:30 happens twice the night clocks fall back).

`ZonedDateTime` resolves these automatically but silently — it is important to know the behavior instead of assuming "one hour is always one hour."

```java
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

public class DstDemo {
    public static void main(String[] args) {
        ZoneId amsterdam = ZoneId.of("Europe/Amsterdam");

        // Gap: 2026-03-29 02:30 does not exist in Europe/Amsterdam (spring forward at 02:00 -> 03:00)
        LocalDateTime gapTime = LocalDateTime.of(2026, 3, 29, 2, 30);
        ZonedDateTime resolvedGap = gapTime.atZone(amsterdam);
        System.out.println(resolvedGap);
        // Output: 2026-03-29T03:30+02:00[Europe/Amsterdam]  <-- shifted forward, not what you typed!

        // Overlap: 2026-10-25 02:30 happens twice (fall back at 03:00 -> 02:00)
        LocalDateTime overlapTime = LocalDateTime.of(2026, 10, 25, 2, 30);
        ZonedDateTime firstOccurrence = overlapTime.atZone(amsterdam);
        System.out.println(firstOccurrence);
        // Output: 2026-10-25T02:30+02:00[Europe/Amsterdam] (the earlier of the two offsets, by default)

        // Adding a "day" across a DST boundary: still 24h wall-clock days, but may be 23 or 25 actual hours.
        ZonedDateTime beforeDst = ZonedDateTime.of(2026, 3, 28, 12, 0, 0, 0, amsterdam);
        ZonedDateTime afterOneDay = beforeDst.plusDays(1);
        System.out.println(afterOneDay); // 2026-03-29T12:00+02:00[Europe/Amsterdam] - only 23 real hours passed
    }
}
```

### Comparing instants vs local times

`Instant` represents an absolute point on the universal timeline; two instants can always be safely compared regardless of where they came from. `LocalDateTime` has no time zone information at all, so comparing two `LocalDateTime` values only tells you which "wall clock reading" is earlier — it says nothing about which one happened first in reality if they came from different zones.

```java
import java.time.*;

public class ComparisonDemo {
    public static void main(String[] args) {
        ZonedDateTime tokyoMeeting =
                ZonedDateTime.of(2026, 8, 7, 22, 0, 0, 0, ZoneId.of("Asia/Tokyo"));
        ZonedDateTime amsterdamMeeting =
                ZonedDateTime.of(2026, 8, 7, 16, 0, 0, 0, ZoneId.of("Europe/Amsterdam"));

        // Wrong-ish: comparing LocalDateTime ignores the zone entirely
        boolean localLooksLater = tokyoMeeting.toLocalDateTime()
                .isAfter(amsterdamMeeting.toLocalDateTime());
        System.out.println(localLooksLater); // true (22:00 > 16:00) - misleading!

        // Correct: compare the actual instants
        boolean instantIsLater = tokyoMeeting.toInstant().isAfter(amsterdamMeeting.toInstant());
        System.out.println(instantIsLater); // false - Tokyo 22:00 JST happens BEFORE Amsterdam 16:00 CEST

        System.out.println(tokyoMeeting.toInstant());     // 2026-08-07T13:00:00Z
        System.out.println(amsterdamMeeting.toInstant()); // 2026-08-07T14:00:00Z
    }
}
```

### Storing time in a database

The safest rule: **store instants in UTC, and store the original time zone separately if you need to display local wall-clock time later.** A raw `LocalDateTime` alone loses the information needed to know "when" that really was in absolute terms; a `ZonedDateTime`'s zone rules can even change in the future (governments do change DST rules).

| What you need to store | Recommended column type(s) |
|---|---|
| "This event happened at an exact moment" | `TIMESTAMP WITH TIME ZONE` (stored internally as UTC), mapped to `Instant` or `OffsetDateTime` |
| "This event happened at an exact moment, and I must show the original local time later" | UTC instant column + a separate `zone_id` (String) column |
| "A calendar date with no time meaning" (birthday, deadline date) | `DATE`, mapped to `LocalDate` |
| "A recurring local time with no date" (store opens at 9am, always local) | `TIME`, mapped to `LocalTime` |

```java
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZonedDateTime;

public class DbStorageExample {

    // What you'd persist for an appointment booked by a user in a given zone
    record AppointmentRecord(Instant utcInstant, String zoneId) {

        ZonedDateTime toLocalDisplayTime() {
            return utcInstant.atZone(ZoneId.of(zoneId));
        }
    }

    public static void main(String[] args) {
        ZonedDateTime bookedAt =
                ZonedDateTime.of(2026, 8, 7, 15, 0, 0, 0, ZoneId.of("America/New_York"));

        AppointmentRecord record = new AppointmentRecord(bookedAt.toInstant(), "America/New_York");

        System.out.println(record.utcInstant()); // 2026-08-07T19:00:00Z
        System.out.println(record.toLocalDisplayTime());
        // Output: 2026-08-07T15:00-04:00[America/New_York]
    }
}
```

---

## Formatting and Parsing

### `DateTimeFormatter` predefined formats

`DateTimeFormatter` ships with several constants for standard formats, most notably the ISO family. These are good defaults for machine-to-machine communication (logs, APIs, file names).

```java
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

public class PredefinedFormatsDemo {
    public static void main(String[] args) {
        LocalDate date = LocalDate.of(2026, 8, 7);
        LocalDateTime dateTime = LocalDateTime.of(2026, 8, 7, 14, 30, 15);

        System.out.println(date.format(DateTimeFormatter.ISO_LOCAL_DATE));
        // Output: 2026-08-07

        System.out.println(dateTime.format(DateTimeFormatter.ISO_LOCAL_DATE_TIME));
        // Output: 2026-08-07T14:30:15

        System.out.println(dateTime.format(DateTimeFormatter.ISO_DATE_TIME));
        // Output: 2026-08-07T14:30:15 (no offset here because LocalDateTime has no zone)

        System.out.println(dateTime.format(DateTimeFormatter.BASIC_ISO_DATE));
        // Output: 20260807
    }
}
```

### `ofPattern` and custom patterns

`DateTimeFormatter.ofPattern` lets you define your own layout using pattern letters (borrowed conceptually from `SimpleDateFormat`, but not identical — see the `YYYY` pitfall below).

```java
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

public class OfPatternDemo {
    public static void main(String[] args) {
        LocalDateTime dt = LocalDateTime.of(2026, 8, 7, 14, 30, 15);

        DateTimeFormatter fmt1 = DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm:ss");
        DateTimeFormatter fmt2 = DateTimeFormatter.ofPattern("EEEE, MMMM d, yyyy", Locale.ENGLISH);

        System.out.println(dt.format(fmt1)); // 07/08/2026 14:30:15
        System.out.println(dt.format(fmt2)); // Friday, August 7, 2026
    }
}
```

| Pattern letter | Meaning | Example |
|---|---|---|
| `y` | Year of era | `2026` |
| `M` | Month of year | `08` or `Aug` (with `MMM`) |
| `d` | Day of month | `07` |
| `E` | Day of week | `Fri` or `Friday` (with `EEEE`) |
| `H` | Hour (0-23) | `14` |
| `h` | Hour (1-12), needs `a` for AM/PM | `02 PM` |
| `m` | Minute | `30` |
| `s` | Second | `15` |
| `S` | Fraction of second | `123` |
| `z` | Time zone name | `CEST` |
| `Z` | Zone offset | `+0200` |
| `X` | ISO-8601 offset | `+02:00` |

### `ofLocalizedDate` and locale-aware styles

Instead of hand-rolling a pattern, `ofLocalizedDate`, `ofLocalizedTime`, and `ofLocalizedDateTime` produce formats that automatically match the conventions of a given `Locale` (order of day/month/year, separators, month names).

```java
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.FormatStyle;
import java.util.Locale;

public class LocalizedFormatDemo {
    public static void main(String[] args) {
        LocalDate date = LocalDate.of(2026, 8, 7);

        DateTimeFormatter us = DateTimeFormatter.ofLocalizedDate(FormatStyle.LONG).withLocale(Locale.US);
        DateTimeFormatter de = DateTimeFormatter.ofLocalizedDate(FormatStyle.LONG).withLocale(Locale.GERMANY);
        DateTimeFormatter jp = DateTimeFormatter.ofLocalizedDate(FormatStyle.LONG).withLocale(Locale.JAPAN);

        System.out.println(date.format(us)); // August 7, 2026
        System.out.println(date.format(de)); // 7. August 2026
        System.out.println(date.format(jp)); // 2026年8月7日
    }
}
```

### `DateTimeFormatterBuilder`

For complex or conditional formats — optional sections, case-insensitive parsing, custom text lookups — `DateTimeFormatterBuilder` gives fine-grained control beyond a simple pattern string.

```java
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeFormatterBuilder;
import java.time.temporal.ChronoField;
import java.util.Locale;

public class FormatterBuilderDemo {
    public static void main(String[] args) {
        DateTimeFormatter formatter = new DateTimeFormatterBuilder()
                .appendValue(ChronoField.YEAR, 4)
                .appendLiteral('-')
                .appendValue(ChronoField.MONTH_OF_YEAR, 2)
                .appendLiteral('-')
                .appendValue(ChronoField.DAY_OF_MONTH, 2)
                .optionalStart()
                .appendLiteral(" (")
                .appendText(ChronoField.DAY_OF_WEEK)
                .appendLiteral(')')
                .optionalEnd()
                .toFormatter(Locale.ENGLISH);

        LocalDate date = LocalDate.of(2026, 8, 7);
        System.out.println(date.format(formatter));
        // Output: 2026-08-07 (Friday)
    }
}
```

### Strict vs lenient `ResolverStyle`

`ResolverStyle` controls how aggressively the parser accepts out-of-range or ambiguous field values. `STRICT` rejects anything invalid, `SMART` (the default) applies sensible corrections, and `LENIENT` allows arithmetic overflow into adjacent fields.

```java
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.ResolverStyle;

public class ResolverStyleDemo {
    public static void main(String[] args) {
        DateTimeFormatter strict = DateTimeFormatter.ofPattern("yyyy-MM-dd")
                .withResolverStyle(ResolverStyle.STRICT);
        DateTimeFormatter smart = DateTimeFormatter.ofPattern("yyyy-MM-dd")
                .withResolverStyle(ResolverStyle.SMART); // default
        DateTimeFormatter lenient = DateTimeFormatter.ofPattern("yyyy-MM-dd")
                .withResolverStyle(ResolverStyle.LENIENT);

        // "2026-02-30" - February never has 30 days
        try {
            LocalDate.parse("2026-02-30", strict);
        } catch (Exception e) {
            System.out.println("STRICT fails: " + e.getMessage());
            // Output: STRICT fails: Invalid date 'FEBRUARY 30'
        }

        try {
            LocalDate.parse("2026-02-30", smart);
        } catch (Exception e) {
            System.out.println("SMART fails: " + e.getMessage());
            // Output: SMART fails: Invalid date 'FEBRUARY 30'
        }

        LocalDate lenientResult = LocalDate.parse("2026-02-30", lenient);
        System.out.println(lenientResult);
        // Output: 2026-03-02  (overflowed into March)
    }
}
```

### ISO-8601

ISO-8601 is the international standard for representing dates and times as text (`2026-08-07T14:30:15+02:00`). It is unambiguous, sortable as a plain string, and the default choice for APIs, logs, and config files. `java.time`'s `toString()` methods and `DateTimeFormatter.ISO_*` constants follow it by default.

```java
import java.time.OffsetDateTime;
import java.time.ZoneOffset;

public class Iso8601Demo {
    public static void main(String[] args) {
        OffsetDateTime odt = OffsetDateTime.of(2026, 8, 7, 14, 30, 15, 0, ZoneOffset.ofHours(2));

        System.out.println(odt); // 2026-08-07T14:30:15+02:00 (ISO-8601, no formatter needed)

        OffsetDateTime parsedBack = OffsetDateTime.parse("2026-08-07T14:30:15+02:00");
        System.out.println(parsedBack.equals(odt)); // true
    }
}
```

### The `YYYY` vs `yyyy` week-year bug

This is a famous, easy-to-miss bug. Lowercase `yyyy` is the **calendar year**. Uppercase `YYYY` is the **week-based year** (ISO week-year, used together with week-of-year `w`). Near the start or end of a year, they can disagree, silently shifting dates by a year.

```java
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;

public class WeekYearBugDemo {
    public static void main(String[] args) {
        // 2027-01-01 is a Friday, and it belongs to ISO week 53 of year 2026
        LocalDate date = LocalDate.of(2027, 1, 1);

        DateTimeFormatter withLowercaseY = DateTimeFormatter.ofPattern("yyyy-MM-dd");
        DateTimeFormatter withUppercaseY = DateTimeFormatter.ofPattern("YYYY-MM-dd");

        System.out.println(date.format(withLowercaseY)); // 2027-01-01 (correct calendar date)
        System.out.println(date.format(withUppercaseY)); // 2026-01-01 (!) week-year, NOT what most people expect

        // Rule of thumb for code review: always use lowercase yyyy unless
        // you are deliberately building an ISO week-date format (with 'w').
    }
}
```

### Parsing failures and `DateTimeParseException`

Any failed parse throws an unchecked `DateTimeParseException`. Because it's unchecked, it is easy to forget to handle at the boundary where you accept external, untrusted date strings (user input, CSV files, third-party APIs).

```java
import java.time.LocalDate;
import java.time.format.DateTimeParseException;

public class ParseFailureDemo {
    public static void main(String[] args) {
        String[] inputs = {"2026-08-07", "07/08/2026", "not-a-date"};

        for (String input : inputs) {
            try {
                LocalDate parsed = LocalDate.parse(input); // expects ISO_LOCAL_DATE by default
                System.out.println("Parsed: " + parsed);
            } catch (DateTimeParseException e) {
                System.out.println("Failed to parse '" + input + "': " + e.getMessage());
            }
        }
        // Output:
        // Parsed: 2026-08-07
        // Failed to parse '07/08/2026': Text '07/08/2026' could not be parsed at index 2
        // Failed to parse 'not-a-date': Text 'not-a-date' could not be parsed at index 0
    }
}
```

### Thread safety: `DateTimeFormatter` vs `SimpleDateFormat`

`SimpleDateFormat` is **not thread-safe** — internally it uses a mutable `Calendar` field, so two threads calling `format()` on the same instance concurrently can corrupt each other's results. `DateTimeFormatter` is **immutable and thread-safe**, so it is safe (and recommended) to share a single instance as a `static final` constant.

```java
import java.text.SimpleDateFormat;
import java.time.format.DateTimeFormatter;
import java.util.Date;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class ThreadSafetyDemo {

    // UNSAFE: sharing a SimpleDateFormat across threads can produce garbled output or exceptions
    private static final SimpleDateFormat UNSAFE_FORMAT = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");

    // SAFE: DateTimeFormatter is immutable, sharing it is fine and idiomatic
    private static final DateTimeFormatter SAFE_FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    public static void main(String[] args) throws InterruptedException {
        ExecutorService pool = Executors.newFixedThreadPool(4);
        for (int i = 0; i < 4; i++) {
            pool.submit(() -> {
                // Calling UNSAFE_FORMAT.format(new Date()) here concurrently
                // may throw NumberFormatException or return a corrupted string.
                String safeResult = java.time.LocalDateTime.now().format(SAFE_FORMAT);
                System.out.println(Thread.currentThread().getName() + ": " + safeResult);
            });
        }
        pool.shutdown();
        // Fix for legacy code: wrap SimpleDateFormat in a ThreadLocal, or just switch to DateTimeFormatter.
    }
}
```

---

## Internationalization (i18n)

Internationalization ("i18n" — 18 letters between "i" and "n") is designing software so it *can* be adapted to different languages and regions without code changes. Localization ("l10n") is the actual act of adapting it for one specific locale (translating text, formatting dates, etc.).

### `Locale`

A `Locale` identifies a language, optionally a country/region, and optionally a variant. It drives almost every locale-sensitive API in the JDK.

```java
import java.util.Locale;

public class LocaleDemo {
    public static void main(String[] args) {
        Locale usEnglish = Locale.US;                       // language=en, country=US
        Locale german = Locale.GERMANY;                     // language=de, country=DE
        Locale custom = new Locale("en", "IN");              // English as used in India
        Locale fromTag = Locale.forLanguageTag("pt-BR");      // Brazilian Portuguese, BCP 47 tag
        Locale root = Locale.ROOT;                            // no language/country - a neutral, culture-independent locale

        System.out.println(usEnglish.getLanguage() + "-" + usEnglish.getCountry()); // en-US
        System.out.println(german.getDisplayName(Locale.US));                        // German (Germany)
        System.out.println(fromTag.getDisplayName());                                // Portuguese (Brazil)
        System.out.println(root);
        // Output: (empty string) - Locale.ROOT has no language, country, or variant

        // Locale.ROOT is the right choice for locale-independent operations,
        // like normalizing internal identifiers or keys, not user-facing text.
        System.out.println("HELLO".toLowerCase(Locale.ROOT)); // hello
    }
}
```

### `ResourceBundle` and property files

`ResourceBundle` loads translated text from `.properties` files named by locale, such as `Messages_en.properties`, `Messages_de.properties`, and `Messages.properties` (the default fallback).

```
# Messages.properties (default/fallback)
greeting=Hello, {0}!

# Messages_de.properties
greeting=Hallo, {0}!

# Messages_fr.properties
greeting=Bonjour, {0} !
```

```java
import java.text.MessageFormat;
import java.util.Locale;
import java.util.ResourceBundle;

public class ResourceBundleDemo {
    public static void main(String[] args) {
        ResourceBundle enBundle = ResourceBundle.getBundle("Messages", Locale.ENGLISH);
        ResourceBundle deBundle = ResourceBundle.getBundle("Messages", Locale.GERMAN);

        String enGreeting = MessageFormat.format(enBundle.getString("greeting"), "Alice");
        String deGreeting = MessageFormat.format(deBundle.getString("greeting"), "Alice");

        System.out.println(enGreeting); // Hello, Alice!
        System.out.println(deGreeting); // Hallo, Alice!

        // Requesting a locale with no matching file falls back to Messages.properties
        ResourceBundle jaBundle = ResourceBundle.getBundle("Messages", Locale.JAPANESE);
        System.out.println(jaBundle.getString("greeting")); // Hello, {0}! (fallback, untranslated placeholder)
    }
}
```

### `MessageFormat`: plurals and argument indexes

`MessageFormat` supports positional arguments (`{0}`, `{1}`) that can be reordered per language, and a `choice`/`plural`-style syntax for picking text based on a number. This matters because different languages pluralize differently (some languages have more than two plural forms).

```java
import java.text.MessageFormat;
import java.util.Locale;

public class MessageFormatDemo {
    public static void main(String[] args) {
        // Argument indexes let translators reorder words for grammar reasons
        String pattern = "{0} sent {1} a message.";       // English word order
        String patternGerman = "{1} bekam eine Nachricht von {0}.";  // reordered for German

        System.out.println(MessageFormat.format(pattern, "Alice", "Bob"));
        // Output: Alice sent Bob a message.
        System.out.println(MessageFormat.format(patternGerman, "Alice", "Bob"));
        // Output: Bob bekam eine Nachricht von Alice.

        // Simple pluralization using choice format
        MessageFormat itemsFormat = new MessageFormat(
                "There {0,choice,0#are no items|1#is one item|1<are {0,number,integer} items}.",
                Locale.ENGLISH);

        System.out.println(itemsFormat.format(new Object[]{0})); // There are no items.
        System.out.println(itemsFormat.format(new Object[]{1})); // There is one item.
        System.out.println(itemsFormat.format(new Object[]{5})); // There are 5 items.
        // Note: for serious plural-rule handling (many languages have 3-6 plural forms),
        // prefer a library that implements full CLDR plural rules (e.g. ICU4J).
    }
}
```

### `NumberFormat` and `Currency`

Numbers and currency amounts are formatted very differently across locales — decimal separators, grouping separators, and currency symbol placement all vary.

```java
import java.text.NumberFormat;
import java.util.Currency;
import java.util.Locale;

public class NumberFormatDemo {
    public static void main(String[] args) {
        double amount = 1234567.891;

        NumberFormat us = NumberFormat.getNumberInstance(Locale.US);
        NumberFormat de = NumberFormat.getNumberInstance(Locale.GERMANY);

        System.out.println(us.format(amount)); // 1,234,567.891
        System.out.println(de.format(amount)); // 1.234.567,891

        NumberFormat usCurrency = NumberFormat.getCurrencyInstance(Locale.US);
        NumberFormat jpCurrency = NumberFormat.getCurrencyInstance(Locale.JAPAN);
        System.out.println(usCurrency.format(1234.5)); // $1,234.50
        System.out.println(jpCurrency.format(1234.5)); // ￥1,235  (Yen has no minor unit, gets rounded)

        Currency eur = Currency.getInstance("EUR");
        System.out.println(eur.getSymbol(Locale.GERMANY)); // €
        System.out.println(eur.getDefaultFractionDigits());  // 2
    }
}
```

### Collation with `Collator`

Sorting text alphabetically is locale-dependent. A plain `String.compareTo` sorts by raw UTF-16 code unit values, which does not match human alphabetical order in most languages. `Collator` sorts the way a native speaker of a given locale would expect.

```java
import java.text.Collator;
import java.util.Arrays;
import java.util.Locale;

public class CollatorDemo {
    public static void main(String[] args) {
        String[] words = {"Apfel", "Äpfel", "banane", "Banane"}; // Ä is U+00C4

        String[] naturalSort = words.clone();
        Arrays.sort(naturalSort); // plain code-unit comparison

        String[] germanSort = words.clone();
        Collator germanCollator = Collator.getInstance(Locale.GERMANY);
        Arrays.sort(germanSort, germanCollator);

        System.out.println(Arrays.toString(naturalSort));
        // Output: [Apfel, Banane, banane, Äpfel]  <-- uppercase/accents sort oddly by raw code points
        System.out.println(Arrays.toString(germanSort));
        // Output: [Apfel, Äpfel, banane, Banane]  <-- matches how a German speaker expects it sorted
    }
}
```

### `String.format` with a `Locale`

`String.format` (and `Formatter`/`printf`) is locale-sensitive for things like decimal points and digit grouping. Always pass an explicit `Locale` in server-side or shared code — otherwise it silently uses the JVM's default locale, which can differ between environments.

```java
import java.util.Locale;

public class StringFormatLocaleDemo {
    public static void main(String[] args) {
        double price = 1234.5;

        String defaultResult = String.format("%,.2f", price);      // uses JVM default locale - risky!
        String usResult = String.format(Locale.US, "%,.2f", price);
        String deResult = String.format(Locale.GERMANY, "%,.2f", price);

        System.out.println(usResult); // 1,234.50
        System.out.println(deResult); // 1.234,50
        // "defaultResult" could be either of the above depending on the machine's default locale -
        // this is a classic source of "works on my machine" bugs in server-side code.
    }
}
```

### `toUpperCase()` and the Turkish dotless-i problem

Calling `toUpperCase()` or `toLowerCase()` without a `Locale` uses the JVM's default locale. In Turkish (and Azerbaijani), the letter `i` uppercases to `İ` (dotted capital I), not the ASCII `I` most code expects — and lowercase `I` becomes `ı` (dotless). This breaks case-insensitive comparisons against constants like `"ID"` or `"HTTP"` when the default locale is Turkish.

```java
import java.util.Locale;

public class TurkishLocaleBugDemo {
    public static void main(String[] args) {
        String input = "id";

        String upperTurkish = input.toUpperCase(new Locale("tr", "TR"));
        String upperRoot = input.toUpperCase(Locale.ROOT);

        System.out.println(upperTurkish); // İD  (dotted capital İ, not plain "I")
        System.out.println(upperRoot);    // ID  (plain ASCII, as expected)

        System.out.println(upperTurkish.equals("ID")); // false - a real bug if this runs on a Turkish-locale server!
        System.out.println(upperRoot.equals("ID"));     // true

        // Rule for code review: for internal/programmatic comparisons (protocol keywords,
        // enum names, HTTP headers), always use toUpperCase(Locale.ROOT) / toLowerCase(Locale.ROOT),
        // or better, String.equalsIgnoreCase() when you just need a comparison.
    }
}
```

### Character encoding and UTF-8 as the default charset

Historically, `String.getBytes()`, `new String(bytes)`, `FileReader`, and similar APIs used the **platform default charset** if none was specified — which could be UTF-8 on Linux but something else (like Windows-1252) on Windows, causing garbled text ("mojibake") when code moved between environments. Since **JDK 18** ([JEP 400](https://openjdk.org/jeps/400)), the JVM's default charset is **always UTF-8**, regardless of OS or locale, unless explicitly overridden with the `file.encoding` system property.

```java
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;

public class DefaultCharsetDemo {
    public static void main(String[] args) {
        System.out.println(Charset.defaultCharset());
        // Output (JDK 18+): UTF-8   (was platform-dependent before JDK 18)

        String text = "café ☕";
        byte[] defaultBytes = text.getBytes();                 // uses default charset - UTF-8 on JDK 18+
        byte[] explicitBytes = text.getBytes(StandardCharsets.UTF_8); // always explicit, safest for review

        System.out.println(defaultBytes.length);  // 8 (matches UTF-8 encoding)
        System.out.println(explicitBytes.length); // 8

        // Best practice even on JDK 18+: always pass an explicit Charset for I/O boundaries
        // (files, network, serialization) so behavior never depends on JVM defaults or -Dfile.encoding.
    }
}
```

### `Normalizer` for Unicode

The same visible character can sometimes be represented by different sequences of Unicode code points — for example, "é" can be one precomposed code point (`U+00E9`) or an "e" followed by a combining accent (`U+0065 U+0301`). These compare as unequal with plain `.equals()` even though they look identical. `java.text.Normalizer` converts text into a consistent form before comparing or storing it.

```java
import java.text.Normalizer;

public class NormalizerDemo {
    public static void main(String[] args) {
        String precomposed = "café";        // "café" using single code point U+00E9
        String decomposed = "café";        // "café" using 'e' + combining acute accent U+0301

        System.out.println(precomposed.equals(decomposed)); // false - look identical, but different bytes!

        String normalizedA = Normalizer.normalize(precomposed, Normalizer.Form.NFC);
        String normalizedB = Normalizer.normalize(decomposed, Normalizer.Form.NFC);

        System.out.println(normalizedA.equals(normalizedB)); // true - now safe to compare

        // NFC = canonical composition (preferred for storage/display)
        // NFD = canonical decomposition (useful for stripping accents, searching)
        String stripped = Normalizer.normalize(precomposed, Normalizer.Form.NFD)
                .replaceAll("\\p{M}", ""); // remove combining marks
        System.out.println(stripped); // cafe
    }
}
```

### Right-to-left text, code points, and grapheme clusters

Some languages (Arabic, Hebrew) are written right-to-left (RTL). Java strings store text left-to-right in memory regardless of visual direction — rendering direction is a display concern (handled by UI toolkits, not the `String` class itself). A separate, very common bug source: `String.length()` counts UTF-16 *code units*, not visible characters. Emoji, some CJK (Chinese/Japanese/Korean) characters, and combining accents can each take more than one code unit, and a single visible "character" (grapheme cluster) can be made of multiple code points.

```java
import java.text.BreakIterator;
import java.util.Locale;

public class TextLengthDemo {
    public static void main(String[] args) {
        String flagEmoji = "🇳🇱"; // the Netherlands flag emoji, one visible glyph
        String familyEmoji = "👨‍👩‍👧"; // family emoji, one glyph

        System.out.println(flagEmoji.length());              // 4 (UTF-16 code units, NOT 1!)
        System.out.println(flagEmoji.codePointCount(0, flagEmoji.length())); // 2 (code points, still not 1)

        // BreakIterator finds actual user-perceived "characters" (grapheme clusters)
        BreakIterator iterator = BreakIterator.getCharacterInstance(Locale.ROOT);
        iterator.setText(familyEmoji);
        int graphemeCount = 0;
        int boundary = iterator.first();
        while (boundary != BreakIterator.DONE) {
            int next = iterator.next();
            if (next != BreakIterator.DONE) graphemeCount++;
            boundary = next;
        }
        System.out.println(graphemeCount); // 1 (one visible "character" made of several code points)

        // Rule for code review: never assume String.length() == "number of visible characters"
        // when the input may contain emoji, combining marks, or characters outside the Basic
        // Multilingual Plane. Use codePointCount() or BreakIterator depending on what you truly need.
    }
}
```

---

## Localization (l10n)

### Organizing bundles and fallback rules

A typical project has one base bundle plus one file per supported locale, sometimes split by variant (language, then language+country). `ResourceBundle` looks up a candidate locale chain from most to least specific, and falls back to the base bundle if nothing else matches.

```
Messages.properties          <- default / fallback (usually English)
Messages_en.properties       <- English (falls back to default automatically if same content)
Messages_en_GB.properties    <- British English overrides (e.g. "colour" vs "color")
Messages_de.properties       <- German
Messages_de_CH.properties    <- Swiss German overrides
Messages_fr.properties       <- French
```

```java
import java.util.Locale;
import java.util.ResourceBundle;

public class BundleFallbackDemo {
    public static void main(String[] args) {
        // Requesting en_GB will look for: Messages_en_GB -> Messages_en -> Messages (base)
        ResourceBundle bundle = ResourceBundle.getBundle("Messages", new Locale("en", "GB"));
        System.out.println(bundle.getLocale());
        // Output depends on which files actually exist; falls back gracefully instead of throwing.

        // Requesting a totally unsupported locale (e.g. Icelandic) still works,
        // it just falls all the way back to the base Messages.properties file.
        ResourceBundle fallback = ResourceBundle.getBundle("Messages", new Locale("is", "IS"));
        System.out.println(fallback.getBaseBundleName()); // Messages
    }
}
```

| Requested locale | Lookup order (most to least specific) |
|---|---|
| `de_CH` (German, Switzerland) | `Messages_de_CH` -> `Messages_de` -> `Messages` |
| `en_GB` (English, UK) | `Messages_en_GB` -> `Messages_en` -> `Messages` |
| `is_IS` (Icelandic, Iceland) - no matching files | `Messages_is_IS` -> `Messages_is` -> `Messages` (base) |

### Pseudo-localization

Pseudo-localization is a testing technique: you generate a fake "translation" that expands text length, adds accented characters, and wraps strings with markers, without doing real translation. It quickly reveals UI bugs (truncated text, hardcoded string concatenation, untranslated strings) before real translators get involved.

```java
import java.util.Locale;
import java.util.ResourceBundle;

public class PseudoLocalizationDemo {

    // A crude pseudo-localizer: wraps text and pads length by ~40%, which is
    // roughly how much German/French text tends to expand versus English.
    static String pseudoLocalize(String text) {
        String expanded = text.replace("a", "àà").replace("e", "éé").replace("o", "òò");
        return "[" + expanded + "]";
    }

    public static void main(String[] args) {
        ResourceBundle bundle = ResourceBundle.getBundle("Messages", Locale.ENGLISH);
        String original = bundle.getString("greeting"); // "Hello, {0}!"

        System.out.println(pseudoLocalize(original));
        // Output: [Héééllòò, {0}!]

        // If the UI truncates this or breaks layout, it will likely break for real
        // translations too (German and Finnish strings are often 30-50% longer than English).
    }
}
```

### Testing with `-Duser.language`

You can override the JVM's default locale from the command line without changing any code, which is very useful for manual and automated locale testing.

```java
import java.util.Locale;

public class DefaultLocaleDemo {
    public static void main(String[] args) {
        System.out.println("Default locale: " + Locale.getDefault());
        // Run with:   java -Duser.language=de -Duser.country=DE DefaultLocaleDemo
        // Output:     Default locale: de_DE

        // Run with:   java -Duser.language=tr -Duser.country=TR DefaultLocaleDemo
        // Output:     Default locale: tr_TR
        // (useful to specifically reproduce the Turkish dotless-i bug shown earlier)
    }
}
```

```java
// A small JUnit-style test that temporarily swaps the default locale to verify behavior.
import java.util.Locale;
import org.junit.jupiter.api.*;

class LocaleSensitiveTest {
    private Locale originalLocale;

    @BeforeEach
    void saveLocale() {
        originalLocale = Locale.getDefault();
    }

    @AfterEach
    void restoreLocale() {
        Locale.setDefault(originalLocale); // always restore - global state affects other tests!
    }

    @Test
    void formatsCorrectlyUnderGermanLocale() {
        Locale.setDefault(new Locale("de", "DE"));
        String result = String.format("%,.2f", 1234.5);
        Assertions.assertEquals("1.234,50", result);
    }
}
```

### CLDR as the default locale data provider

CLDR (Unicode Common Locale Data Repository) is the industry-standard source of locale data — date/time patterns, number formats, currency symbols, plural rules, and more — maintained collaboratively (contributors include Google, Apple, Microsoft, IBM, and others). Since **JDK 9**, the JDK uses **CLDR** as the default locale data provider instead of its own older, less complete data (JRE-specific data, sometimes called "COMPAT"). This changed some formatting output compared to Java 8 — a real source of subtle behavior differences when upgrading old codebases.

```java
import java.text.NumberFormat;
import java.util.Locale;

public class CldrProviderDemo {
    public static void main(String[] args) {
        System.out.println(System.getProperty("java.locale.providers"));
        // Output (JDK 9+): null by default (meaning CLDR is used implicitly), or "CLDR" if explicitly set

        NumberFormat currency = NumberFormat.getCurrencyInstance(Locale.US);
        System.out.println(currency.format(1234.5));
        // Output (CLDR, JDK 9+): $1,234.50
        // On JDK 8 with the old COMPAT provider, some locales format currency slightly
        // differently (e.g. different symbol placement or spacing for certain locales).

        // You can force the old JDK 8 behavior (not recommended, mostly for migration debugging):
        // java -Djava.locale.providers=COMPAT,CLDR CldrProviderDemo
    }
}
```

---

## Common Code-Review Interview Pitfalls

1. **Discarding the result of an immutable `plus`/`minus`/`with` call.**
   Why it matters: `java.time` types are immutable — the call does nothing unless you capture the return value. This is the single most common `java.time` bug.
   ```java
   // Before (bug): does nothing, "deadline" is unchanged
   deadline.plusDays(7);

   // After (fixed): capture the new value
   deadline = deadline.plusDays(7);
   ```

2. **Sharing a mutable `SimpleDateFormat` as a `static` field across threads.**
   Why it matters: `SimpleDateFormat` is not thread-safe; concurrent `format()`/`parse()` calls can corrupt results or throw exceptions.
   ```java
   // Before (bug): shared mutable state
   static final SimpleDateFormat FMT = new SimpleDateFormat("yyyy-MM-dd");

   // After (fixed): DateTimeFormatter is immutable and thread-safe
   static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd");
   ```

3. **Storing `LocalDateTime` for events that need an absolute point in time.**
   Why it matters: `LocalDateTime` has no zone, so "2026-08-07T14:00" is ambiguous across regions and can't be safely compared or converted.
   ```java
   // Before (bug): ambiguous, no zone info
   LocalDateTime eventTime = LocalDateTime.now();

   // After (fixed): store an absolute instant, plus the zone if you need to redisplay locally
   Instant eventInstant = Instant.now();
   String zoneId = "Europe/Amsterdam";
   ```

4. **Using `YYYY` (week-year) instead of `yyyy` (calendar year) in a pattern.**
   Why it matters: near year boundaries, the week-based year can differ from the calendar year, silently shifting dates by one year.
   ```java
   // Before (bug): week-year, wrong for calendar dates
   DateTimeFormatter.ofPattern("YYYY-MM-dd");

   // After (fixed): calendar year
   DateTimeFormatter.ofPattern("yyyy-MM-dd");
   ```

5. **Calling `toUpperCase()`/`toLowerCase()` without a `Locale` for internal comparisons.**
   Why it matters: the Turkish locale (and others) can transform `i`/`I` unexpectedly, breaking comparisons against ASCII constants.
   ```java
   // Before (bug): depends on default locale
   if (input.toUpperCase().equals("ID")) { ... }

   // After (fixed): locale-independent
   if (input.equalsIgnoreCase("ID")) { ... }
   ```

6. **Calling `.now()` directly inside business logic instead of injecting a `Clock`.**
   Why it matters: hardcoded "now" calls make unit tests flaky or impossible to make deterministic.
   ```java
   // Before (hard to test)
   LocalDate today = LocalDate.now();

   // After (testable)
   LocalDate today = LocalDate.now(clock); // clock injected, Clock.fixed(...) in tests
   ```

7. **Assuming "add one day" always adds exactly 24 hours.**
   Why it matters: DST transitions mean a calendar day can be 23 or 25 real hours; use `Duration` for exact elapsed time, `Period`/`plusDays` for calendar-based reasoning.
   ```java
   // Before (bug): assumes 24 real hours always pass
   Instant next = instant.plus(Duration.ofDays(1));

   // After (fixed): use calendar semantics when you mean "the next calendar day"
   ZonedDateTime next = zonedDateTime.plusDays(1);
   ```

8. **Comparing `LocalDateTime` values that originated from different time zones.**
   Why it matters: `LocalDateTime` ignores zone offsets entirely, so "later on the clock" does not mean "later in reality."
   ```java
   // Before (bug): compares wall-clock time, ignoring zone
   boolean isLater = a.toLocalDateTime().isAfter(b.toLocalDateTime());

   // After (fixed): compare actual instants
   boolean isLater = a.toInstant().isAfter(b.toInstant());
   ```

9. **Letting `String.format`/`NumberFormat` use the JVM default locale in shared/server code.**
   Why it matters: default locale can differ between machines and environments, causing inconsistent output ("works on my machine" bugs, wrong decimal separators sent to clients).
   ```java
   // Before (bug): implicit, environment-dependent
   String.format("%,.2f", price);

   // After (fixed): explicit locale
   String.format(Locale.US, "%,.2f", price);
   ```

10. **Not handling `DateTimeParseException` at boundaries that accept external date strings.**
    Why it matters: it's an unchecked exception; forgetting to catch it lets malformed user/API input crash a request instead of returning a clean validation error.
    ```java
    // Before (bug): unhandled, crashes on bad input
    LocalDate date = LocalDate.parse(userInput);

    // After (fixed): handled explicitly
    try {
        LocalDate date = LocalDate.parse(userInput);
    } catch (DateTimeParseException e) {
        throw new InvalidRequestException("Invalid date: " + userInput);
    }
    ```

11. **Using `String.length()` to validate or truncate text that may contain emoji or combining characters.**
    Why it matters: `length()` counts UTF-16 code units, not visible characters, so limits can cut a string mid-character or reject valid short input.
    ```java
    // Before (bug): may split a surrogate pair or combining sequence
    String truncated = input.substring(0, Math.min(input.length(), 10));

    // After (better): use code point aware truncation, or a grapheme-aware
    // BreakIterator for user-facing character counts
    int end = input.offsetByCodePoints(0, Math.min(10, input.codePointCount(0, input.length())));
    String truncated = input.substring(0, end);
    ```

12. **Comparing Unicode text with `.equals()` without normalizing first.**
    Why it matters: visually identical strings can have different code point sequences (precomposed vs. decomposed accents), causing "duplicate" entries or failed lookups.
    ```java
    // Before (bug): may treat identical-looking text as different
    if (nameFromUserA.equals(nameFromUserB)) { ... }

    // After (fixed): normalize first
    if (Normalizer.normalize(nameFromUserA, Normalizer.Form.NFC)
            .equals(Normalizer.normalize(nameFromUserB, Normalizer.Form.NFC))) { ... }
    ```

13. **Assuming the platform default charset is always UTF-8 on any JDK version.**
    Why it matters: before JDK 18, the default charset was platform-dependent; code relying on the implicit default can misbehave when moved to another OS or an older JDK.
    ```java
    // Before (risky on JDK < 18 / non-UTF-8 platforms): implicit charset
    byte[] bytes = text.getBytes();

    // After (fixed): always explicit, safe on any JDK version
    byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
    ```

14. **Hardcoding date/number formats instead of using locale-aware formatters for user-facing text.**
    Why it matters: users expect dates and numbers formatted the way their own locale/region does, not a fixed pattern chosen by the developer.
    ```java
    // Before (bug): fixed pattern regardless of user's locale
    DateTimeFormatter.ofPattern("MM/dd/yyyy");

    // After (fixed): adapts to the user's locale automatically
    DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(userLocale);
    ```

15. **Sorting user-facing text with `String.compareTo` instead of a locale-aware `Collator`.**
    Why it matters: plain code-unit comparison does not match human alphabetical order for accented letters, case, or non-Latin scripts, producing confusing sort order in UIs.
    ```java
    // Before (bug): raw code-unit sort, wrong for most human languages
    Arrays.sort(names);

    // After (fixed): locale-aware collation
    Arrays.sort(names, Collator.getInstance(userLocale));
    ```
