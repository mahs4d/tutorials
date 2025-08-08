package com.mahs4d.tinykv;

import com.mahs4d.tinykv.kv.TinyKvStore;
import com.mahs4d.tinykv.wal.FileWal;
import com.mahs4d.tinykv.wal.Wal;
import com.mahs4d.tinykv.wal.codec.SimpleWalCodec;
import com.mahs4d.tinykv.wal.codec.WalCodec;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class TinyKvStoreTest {
    @Test
    void stateSurvivesReOpen(@TempDir Path tempDir) throws Exception {
        Path walPath = tempDir.resolve("wal.log");
        WalCodec walCodec = new SimpleWalCodec();

        try (Wal wal = new FileWal(walPath, walCodec)) {
            var store = new TinyKvStore(wal);

            store.put("a", "1");
            store.put("b", "2");
            store.delete("a");
            store.put("b", "22"); // overwrite
        }

        try (Wal wal = new FileWal(walPath, walCodec)) {
            var reopened = new TinyKvStore(wal);

            assertEquals(1, reopened.size());
            assertTrue(reopened.get("a").isEmpty(), "deleted key must stay gone");
            assertEquals("22", reopened.get("b").orElse(null), "last write wins");
        }
    }

    @Test
    void replayIsIdempotent(@TempDir Path tempDir) throws Exception {
        Path walPath = tempDir.resolve("wal.log");
        WalCodec walCodec = new SimpleWalCodec();

        try (Wal wal = new FileWal(walPath, walCodec)) {
            var store = new TinyKvStore(wal);
            store.put("k", "v");
        }

        try (Wal wal = new FileWal(walPath, walCodec)) {
            var replay = new TinyKvStore(wal);
            assertEquals(1, replay.size());
        }

        try (Wal wal = new FileWal(walPath, walCodec)) {
            var replay = new TinyKvStore(wal);
            assertEquals(1, replay.size());
            assertEquals("v", replay.get("k").orElse(null));
        }
    }
}
