package com.mahs4d.tinykv.wal;

public sealed interface WalRecord permits WalRecord.Put, WalRecord.Delete {
    String key();

    record Put(String key, String value) implements WalRecord {
    }

    record Delete(String key) implements WalRecord {
    }
}
