package com.mahs4d.hashindex;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ThreadLocalRandom;

import static org.junit.jupiter.api.Assertions.*;

@DisplayName("HashIndex<K,V>")
public class HashIndexTest {
    private HashIndex<String, Integer> index;

    @BeforeEach
    void setUp() {
        index = new HashIndex<>();
    }

    @Test
    @DisplayName("a new index is empty")
    void newIndexIsEmpty() {
        assertTrue(index.isEmpty());
        assertEquals(0, index.getSize());
        assertNull(index.get("missing"));
        assertFalse(index.containsKey("missing"));
    }

    @Test
    @DisplayName("put then get returns the stored value")
    void putThenGet() {
        assertNull(index.put("alice", 123));
        assertEquals(123, index.get("alice"));
        assertEquals(1, index.getSize());
        assertTrue(index.containsKey("alice"));
    }

    @Test
    @DisplayName("put with an existing key updates and returns the old value")
    void putUpdatesExistingKey() {
        index.put("k", 10);
        Integer previous = index.put("k", 20);
        assertEquals(10, previous);
        assertEquals(20, index.get("k"));
        assertEquals(1, index.getSize(), "updating must NOT increase size");
    }

    @Test
    @DisplayName("remove deletes the entry and returns its value")
    void removeEntry() {
        index.put("a", 1);
        index.put("b", 2);

        assertEquals(1, index.remove("a"));
        assertNull(index.get("a"));
        assertFalse(index.containsKey("a"));
        assertEquals(1, index.getSize());

        assertNull(index.remove("a"), "removing a missing key returns null");
        assertNull(index.remove("never-there"));
    }

    @Test
    @DisplayName("supports a single null key")
    void supportsNullKey() {
        index.put(null, 99);
        assertEquals(99, index.get(null));
        assertTrue(index.containsKey(null));

        index.put(null, 100);
        assertEquals(100, index.get(null));
        assertEquals(1, index.getSize());
    }

    @ParameterizedTest(name = "key=\"{0}\" survives a round-trip")
    @ValueSource(strings = {"", "a", "Z", "hello world", "péché", "🦆"})
    void roundTripsArbitraryStringKeys(String key) {
        index.put(key, 7);
        assertEquals(7, index.get(key));
    }

    @ParameterizedTest(name = "{0} -> {1}")
    @CsvSource({
            "one,   1",
            "two,   2",
            "three, 3",
            "four,  4"
    })
    void storesEachPair(String word, int number) {
        index.put(word, number);
        assertEquals(number, index.get(word));
    }

    private record SameHashKey(int id) {
        @Override
        public int hashCode() {
            return 42;
        }
    }

    @Test
    @DisplayName("handles total hash collisions correctly via chaining")
    void handlesCollisions() {
        HashIndex<SameHashKey, Integer> index = new HashIndex<>();
        int n = 50;
        for (int i = 0; i < n; i++) {
            index.put(new SameHashKey(i), i * 10);
        }
        assertEquals(n, index.getSize());
        for (int i = 0; i < n; i++) {
            assertEquals(i * 10, index.get(new SameHashKey(i)), "every colliding key must still be findable");
        }

        assertEquals(250, index.remove(new SameHashKey(25)));
        assertNull(index.get(new SameHashKey(25)));
        assertEquals(n - 1, index.getSize());
        assertEquals(0, index.get(new SameHashKey(0)));
        assertEquals(490, index.get(new SameHashKey(49)));
    }

    @Test
    @DisplayName("resizes (grows capacity) as it fills, without losing entries")
    void resizesWithoutDataLoss() {
        HashIndex<Integer, Integer> index = new HashIndex<>();
        int startCap = index.getCapacity();          // 16 by default

        for (int i = 0; i < 1000; i++) {
            index.put(i, i * i);
        }

        assertTrue(index.getCapacity() > startCap, "capacity should have grown");
        assertEquals(1000, index.getSize());
        for (int i = 0; i < 1000; i++) {
            assertEquals(i * i, index.get(i), "entry " + i + " survived rehashing");
        }
    }

    @Test
    @DisplayName("behaves identically to java.util.HashMap under random ops")
    void matchesJavaHashMap() {
        HashIndex<Integer, Integer> mine = new HashIndex<>();
        Map<Integer, Integer> ref = new HashMap<>();
        var rnd = ThreadLocalRandom.current();

        for (int i = 0; i < 20_000; i++) {
            int key = rnd.nextInt(500);   // small key space -> lots of collisions & updates
            int op = rnd.nextInt(3);
            switch (op) {
                case 0 -> {                            // put
                    int v = rnd.nextInt();
                    assertEquals(ref.put(key, v), mine.put(key, v));
                }
                case 1 -> assertEquals(ref.get(key), mine.get(key));     // get
                case 2 -> assertEquals(ref.remove(key), mine.remove(key)); // remove
                default -> throw new AssertionError();
            }
            assertEquals(ref.size(), mine.getSize());
        }

        // Final full reconciliation.
        List<Integer> myKeys = mine.getKeys();
        assertEquals(ref.size(), myKeys.size());
        for (Integer k : myKeys) {
            assertEquals(ref.get(k), mine.get(k));
        }
        // Make the unused import meaningful and assert no phantom keys.
        for (Integer k : ref.keySet()) {
            assertTrue(Objects.equals(ref.get(k), mine.get(k)));
        }
    }
}
