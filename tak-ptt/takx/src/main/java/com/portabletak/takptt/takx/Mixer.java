package com.portabletak.takptt.takx;

import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

import io.github.jaredmdobson.concentus.OpusDecoder;

/**
 * Plays everyone at once (full duplex, like the phones): each talker has an Opus decoder and a
 * short queue, and a thread mixes the queues and any tone into the speaker every 20 ms. A talker
 * starts playing once 40 ms is queued, to ride out network jitter, and the queue is kept to
 * 200 ms so a stall does not build up delay.
 */
final class Mixer {
    private static final int START_FRAMES = 2, MAX_FRAMES = 10;
    private static final long IDLE_MS = 5000;              // keep the sound device open this long after traffic

    private static final class Talker {
        final OpusDecoder decoder;
        final ArrayDeque<short[]> frames = new ArrayDeque<>();
        boolean playing, ended;

        Talker() throws Exception { decoder = new OpusDecoder(Audio.RATE, 1); }
    }

    private final Audio audio;
    private final Map<Integer, Talker> talkers = new HashMap<>();
    private final ArrayDeque<short[]> tone = new ArrayDeque<>();
    private volatile boolean running;
    private volatile String error;
    private Thread thread;

    Mixer(Audio audio) { this.audio = audio; }

    /** The last speaker problem, or null. */
    String error() { return error; }

    void add(int session, byte[] opus, boolean last) {
        synchronized (this) {
            try {
                Talker t = talkers.get(session);
                if (t == null) talkers.put(session, t = new Talker());
                if (opus.length > 0) {
                    short[] pcm = new short[Audio.FRAME];
                    int n = t.decoder.decode(opus, 0, opus.length, pcm, 0, Audio.FRAME, false);
                    if (n > 0) t.frames.add(pcm);
                    while (t.frames.size() > MAX_FRAMES) t.frames.poll();
                }
                if (last) t.ended = true;
            } catch (Exception e) {
                talkers.remove(session);                    // a bad packet: the next one starts clean
            }
        }
        wake();
    }

    void remove(int session) {
        synchronized (this) { talkers.remove(session); }
    }

    /** Queue a tone and return how long it plays. */
    int play(int[][] steps) {
        short[] pcm = Tones.render(steps);
        synchronized (this) {
            for (int i = 0; i < pcm.length; i += Audio.FRAME) {
                short[] f = new short[Audio.FRAME];
                System.arraycopy(pcm, i, f, 0, Math.min(Audio.FRAME, pcm.length - i));
                tone.add(f);
            }
        }
        wake();
        return Tones.lengthMs(steps);
    }

    void stop() {
        running = false;
        Thread t;
        synchronized (this) {
            t = thread;
            talkers.clear();
            tone.clear();
        }
        if (t != null) t.interrupt();
    }

    private synchronized void wake() {
        if (running) return;
        running = true;
        thread = new Thread(this::run, "takptt-mixer");
        thread.setDaemon(true);
        thread.start();
    }

    private void run() {
        try (Audio.Speaker speaker = audio.openSpeaker()) {
            error = null;
            short[] mix = new short[Audio.FRAME];
            long lastSound = System.currentTimeMillis();
            while (running) {
                if (mixFrame(mix)) lastSound = System.currentTimeMillis();
                else if (System.currentTimeMillis() - lastSound > IDLE_MS && idle()) break;
                speaker.write(mix);
            }
        } catch (Exception e) {
            if (running) error = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        }
        synchronized (this) {
            running = false;
            thread = null;
            if (talkers.values().stream().anyMatch(t -> !t.frames.isEmpty()) || !tone.isEmpty()) wake();
        }
    }

    private synchronized boolean idle() {
        return tone.isEmpty() && talkers.values().stream().allMatch(t -> t.frames.isEmpty());
    }

    /** One frame of everything queued; true when there was any sound. */
    private synchronized boolean mixFrame(short[] mix) {
        int[] sum = new int[mix.length];
        boolean any = false;
        short[] t = tone.poll();
        if (t != null) {
            any = true;
            for (int i = 0; i < mix.length; i++) sum[i] += t[i];
        }
        for (Iterator<Talker> it = talkers.values().iterator(); it.hasNext(); ) {
            Talker talker = it.next();
            if (!talker.playing && (talker.frames.size() >= START_FRAMES || talker.ended)) talker.playing = true;
            short[] f = talker.playing ? talker.frames.poll() : null;
            if (f != null) {
                any = true;
                for (int i = 0; i < mix.length; i++) sum[i] += f[i];
            } else if (talker.ended) {
                it.remove();
            } else {
                talker.playing = false;                     // ran dry: build up the 40 ms again
            }
        }
        for (int i = 0; i < mix.length; i++)
            mix[i] = (short) Math.max(Short.MIN_VALUE, Math.min(Short.MAX_VALUE, sum[i]));
        return any;
    }
}
