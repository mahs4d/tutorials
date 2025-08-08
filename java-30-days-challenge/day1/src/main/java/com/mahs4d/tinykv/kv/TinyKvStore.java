package com.mahs4d.tinykv.kv;

import com.mahs4d.tinykv.wal.Wal;
import com.mahs4d.tinykv.wal.WalRecord;

import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

public class TinyKvStore implements KvStore {
    private final Wal wal;
    private final Map<String, String> kv;

    public TinyKvStore(Wal wal) {
        this.wal = wal;
        this.kv = new HashMap<>();

        replay();
    }

    private void replay() {
        for(WalRecord record : wal.getAllRecords()) {
            switch (record) {
                case WalRecord.Put(String key, String value) -> kv.put(key, value);
                case WalRecord.Delete(String key) -> kv.remove(key);
            }
        }
    }

    @Override
    public Optional<String> get(String key) {
        return Optional.ofNullable(kv.get(key));
    }

    @Override
    public void put(String key, String value) {
        WalRecord record = new WalRecord.Put(key, value);
        wal.append(record);
        kv.put(key, value);
    }

    @Override
    public void delete(String key) {
        WalRecord record = new WalRecord.Delete(key);
        wal.append(record);
        kv.remove(key);
    }

    @Override
    public int size() {
        return kv.size();
    }
}
