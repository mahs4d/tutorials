package com.mahs4d.tinykv.wal.codec;

import com.mahs4d.tinykv.wal.WalRecord;

import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

public final class SimpleWalCodec implements WalCodec {
    @Override
    public String encode(WalRecord record) {
        return switch (record) {
            case WalRecord.Put(String key, String value) -> "PUT\t" + urlEncode(key) + "\t" + urlEncode(value);
            case WalRecord.Delete(String key) -> "DEL\t" + urlEncode(key);
        };
    }

    @Override
    public WalRecord decode(String encodedRecord) {
        String[] parts = encodedRecord.split("\t");
        return switch (parts[0]) {
            case "PUT" -> new WalRecord.Put(urlDecode(parts[1]), urlDecode(parts[2]));
            case "DEL" -> new WalRecord.Delete(urlDecode(parts[1]));
            default -> throw new IllegalArgumentException("Invalid WAL line: " + encodedRecord);
        };
    }

    private String urlEncode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private String urlDecode(String value) {
        return URLDecoder.decode(value, StandardCharsets.UTF_8);
    }
}
