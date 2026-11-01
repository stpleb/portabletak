package com.portabletak.takptt.takx;

import java.io.ByteArrayOutputStream;
import java.io.IOException;

/**
 * Mumble's voice packets (the legacy format, which every Murmur still speaks to a client that says
 * it is version 1.4). Going out: header, sequence, size, Opus. Coming in: header, sender's
 * session, sequence, size, Opus. The top three header bits are the codec, the low five the target.
 */
final class VoicePacket {
    static final int OPUS = 4, LAST = 0x2000;

    final int session, target;
    final long sequence;
    final byte[] opus;
    final boolean last;

    private VoicePacket(int session, int target, long sequence, byte[] opus, boolean last) {
        this.session = session;
        this.target = target;
        this.sequence = sequence;
        this.opus = opus;
        this.last = last;
    }

    static byte[] encode(int target, long sequence, byte[] opus, int len, boolean last) {
        ByteArrayOutputStream o = new ByteArrayOutputStream(len + 12);
        o.write((OPUS << 5) | (target & 0x1F));
        putVarint(o, sequence);
        putVarint(o, last ? len | LAST : len);
        o.write(opus, 0, len);
        return o.toByteArray();
    }

    /** The packet, or null when it is not Opus (Mumble's UDP ping, or an old codec). */
    static VoicePacket decode(byte[] b) throws IOException {
        if (b.length < 1 || (b[0] & 0xFF) >> 5 != OPUS) return null;
        int target = b[0] & 0x1F;
        int[] at = {1};
        long session = getVarint(b, at), sequence = getVarint(b, at), size = getVarint(b, at);
        int len = (int) (size & 0x1FFF);
        if (at[0] + len > b.length) throw new IOException("voice packet cut short");
        byte[] opus = new byte[len];
        System.arraycopy(b, at[0], opus, 0, len);
        return new VoicePacket((int) session, target, sequence, opus, (size & LAST) != 0);
    }

    static void putVarint(ByteArrayOutputStream o, long v) {
        if ((v & 0x8000000000000000L) != 0 && (~v) < 4) {
            o.write(0xFC | (int) ~v);
            return;
        }
        if (v < 0) {
            o.write(0xF8);
            v = ~v;
        }
        if (v < 0x80) {
            o.write((int) v);
        } else if (v < 0x4000) {
            o.write(0x80 | (int) (v >> 8));
            o.write((int) v);
        } else if (v < 0x200000) {
            o.write(0xC0 | (int) (v >> 16));
            o.write((int) (v >> 8));
            o.write((int) v);
        } else if (v < 0x10000000) {
            o.write(0xE0 | (int) (v >> 24));
            o.write((int) (v >> 16));
            o.write((int) (v >> 8));
            o.write((int) v);
        } else if (v < 0x100000000L) {
            o.write(0xF0);
            for (int s = 24; s >= 0; s -= 8) o.write((int) (v >> s));
        } else {
            o.write(0xF4);
            for (int s = 56; s >= 0; s -= 8) o.write((int) (v >> s));
        }
    }

    static long getVarint(byte[] b, int[] at) throws IOException {
        int c = next(b, at);
        if ((c & 0x80) == 0) return c;
        if ((c & 0xC0) == 0x80) return ((long) (c & 0x3F) << 8) | next(b, at);
        if ((c & 0xE0) == 0xC0) return ((long) (c & 0x1F) << 16) | (next(b, at) << 8) | next(b, at);
        if ((c & 0xF0) == 0xE0) return ((long) (c & 0x0F) << 24) | (next(b, at) << 16) | (next(b, at) << 8) | next(b, at);
        if ((c & 0xFC) == 0xF0) return bigEndian(b, at, 4);
        if ((c & 0xFC) == 0xF4) return bigEndian(b, at, 8);
        if ((c & 0xFC) == 0xF8) return ~getVarint(b, at);
        return ~(long) (c & 0x03);
    }

    private static long bigEndian(byte[] b, int[] at, int n) throws IOException {
        long v = 0;
        for (int i = 0; i < n; i++) v = (v << 8) | next(b, at);
        return v;
    }

    private static int next(byte[] b, int[] at) throws IOException {
        if (at[0] >= b.length) throw new IOException("voice packet cut short");
        return b[at[0]++] & 0xFF;
    }
}
