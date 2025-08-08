package com.mahs4d.tinykv.wal.codec;

import com.mahs4d.tinykv.wal.WalRecord;

public interface WalCodec {
    String encode(WalRecord record);

    WalRecord decode(String encodedRecord);
}
