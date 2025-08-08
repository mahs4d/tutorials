package com.mahs4d.tinykv.kv;

import java.util.Optional;

public interface KvStore {
    Optional<String> get(String key);

    void put(String key, String value);

    void delete(String key);

    int size();
}
