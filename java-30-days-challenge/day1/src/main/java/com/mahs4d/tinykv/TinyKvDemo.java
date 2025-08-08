package com.mahs4d.tinykv;

import com.mahs4d.tinykv.kv.KvStore;
import com.mahs4d.tinykv.kv.TinyKvStore;
import com.mahs4d.tinykv.wal.FileWal;
import com.mahs4d.tinykv.wal.Wal;
import com.mahs4d.tinykv.wal.codec.SimpleWalCodec;
import com.mahs4d.tinykv.wal.codec.WalCodec;

import java.nio.file.Path;
import java.util.Optional;

public class TinyKvDemo {
    public static void main(String[] args) throws Exception {
        Path walPath = Path.of("wal.log");
        WalCodec walCodec = new SimpleWalCodec();

        try(Wal wal = new FileWal(walPath, walCodec)) {
            KvStore kvStore = new TinyKvStore(wal);

            System.out.println("Opened store. Current size: " + kvStore.size());
            kvStore.put("user:1", "Ada Lovelace");
            kvStore.put("user:2", "Alan Turing");
            kvStore.put("lang", "Java");
            kvStore.delete("lang");          // changed our mind
            kvStore.put("user:2", "Grace Hopper"); // overwrite

            print(kvStore, "user:1");
            print(kvStore, "user:2");
            print(kvStore, "lang");
        }

        try (Wal wal = new FileWal(walPath, walCodec)) {
            KvStore kvStore = new TinyKvStore(wal);

            System.out.println("\n--- Reopened (simulating restart after crash) ---");
            System.out.println("Recovered size = " + kvStore.size());
            print(kvStore, "user:1");
            print(kvStore, "user:2");
            print(kvStore, "lang");   // should be gone — the DELETE replayed
        }
    }

    private static void print(KvStore store, String key) {
        Optional<String> v = store.get(key);
        System.out.printf("  %-8s -> %s%n", key, v.orElse("<none>"));
    }
}
