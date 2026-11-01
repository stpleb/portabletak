package com.portabletak.takptt.takx;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Just enough protobuf for Mumble's control messages. Written by hand so the plugin carries no
 * protobuf library that could clash with the one inside TAKX.
 */
final class Pb {
    private final ByteArrayOutputStream out = new ByteArrayOutputStream();

    Pb uint(int field, long value) {
        varint(field << 3);
        varint(value);
        return this;
    }

    Pb bool(int field, boolean value) { return uint(field, value ? 1 : 0); }

    Pb str(int field, String value) { return bytes(field, value.getBytes(StandardCharsets.UTF_8)); }

    Pb bytes(int field, byte[] value) {
        varint((field << 3) | 2);
        varint(value.length);
        out.write(value, 0, value.length);
        return this;
    }

    byte[] done() { return out.toByteArray(); }

    private void varint(long v) {
        while ((v & ~0x7FL) != 0) {
            out.write((int) (v & 0x7F) | 0x80);
            v >>>= 7;
        }
        out.write((int) v);
    }

    /** A parsed message: field number to its values (Long for numbers, byte[] for text and bytes). */
    static final class Msg {
        private final Map<Integer, List<Object>> fields = new HashMap<>();

        boolean has(int field) { return fields.containsKey(field); }

        long num(int field, long otherwise) {
            List<Object> v = fields.get(field);
            return v == null ? otherwise : (Long) v.get(v.size() - 1);
        }

        boolean bool(int field) { return num(field, 0) != 0; }

        String text(int field, String otherwise) {
            List<Object> v = fields.get(field);
            return v == null ? otherwise : new String((byte[]) v.get(v.size() - 1), StandardCharsets.UTF_8);
        }
    }

    static Msg parse(byte[] b) throws IOException {
        Msg m = new Msg();
        int[] at = {0};
        while (at[0] < b.length) {
            long tag = readVarint(b, at);
            int field = (int) (tag >>> 3), wire = (int) (tag & 7);
            Object value;
            switch (wire) {
                case 0:
                    value = readVarint(b, at);
                    break;
                case 1:
                    at[0] += 8;
                    continue;
                case 2: {
                    int n = (int) readVarint(b, at);
                    if (n < 0 || at[0] + n > b.length) throw new IOException("bad length in message");
                    byte[] v = new byte[n];
                    System.arraycopy(b, at[0], v, 0, n);
                    at[0] += n;
                    value = v;
                    break;
                }
                case 5:
                    at[0] += 4;
                    continue;
                default:
                    throw new IOException("bad field type in message");
            }
            m.fields.computeIfAbsent(field, k -> new ArrayList<>()).add(value);
        }
        return m;
    }

    private static long readVarint(byte[] b, int[] at) throws IOException {
        long v = 0;
        for (int shift = 0; shift < 64; shift += 7) {
            if (at[0] >= b.length) throw new IOException("message cut short");
            int c = b[at[0]++] & 0xFF;
            v |= (long) (c & 0x7F) << shift;
            if ((c & 0x80) == 0) return v;
        }
        throw new IOException("bad number in message");
    }
}
