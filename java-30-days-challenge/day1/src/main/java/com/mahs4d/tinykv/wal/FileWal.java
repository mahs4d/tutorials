package com.mahs4d.tinykv.wal;

import com.mahs4d.tinykv.wal.codec.WalCodec;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.channels.Channels;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.LinkedList;
import java.util.List;

public final class FileWal implements Wal {
    private final Path path;
    private final WalCodec codec;
    private final FileChannel writeChannel;
    private final BufferedWriter writer;

    public FileWal(Path path, WalCodec codec) {
        this.path = path;
        this.codec = codec;

        try {
            this.writeChannel = FileChannel.open(path, StandardOpenOption.CREATE, StandardOpenOption.WRITE, StandardOpenOption.APPEND);
            this.writer = new BufferedWriter(Channels.newWriter(writeChannel, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to open WAL file `" + path + "`", e);
        }
    }

    @Override
    public void append(WalRecord record) {
        try {
            writer.write(codec.encode(record));
            writer.newLine();
            writer.flush();
            writeChannel.force(true);
        } catch (IOException e) {
            throw new UncheckedIOException("WAL append failed", e);
        }
    }

    @Override
    public Iterable<WalRecord> getAllRecords() {
        List<WalRecord> records = new LinkedList<>();
        if (!Files.exists(path)) {
            return records;
        }

        try {
            for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
                if (line.isBlank()) {
                    continue;
                }

                WalRecord record = codec.decode(line);
                records.add(record);
            }
        } catch (IOException e) {
            throw new UncheckedIOException("WAL append failed", e);
        }
        return records;
    }

    @Override
    public void close() throws Exception {
        try {
            writer.close();
            writeChannel.close();
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to close WAL", e);
        }
    }
}
