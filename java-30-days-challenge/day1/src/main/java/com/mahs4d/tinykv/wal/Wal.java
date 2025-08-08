package com.mahs4d.tinykv.wal;

public interface Wal extends AutoCloseable {
    void append(WalRecord record);

    Iterable<WalRecord> getAllRecords();
}
