package com.portabletak.takptt.takx;

import java.io.ByteArrayOutputStream;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Predicate;
import java.util.prefs.Preferences;

/**
 * Runs the real engine against a real Murmur, with a made-up microphone and speaker, so voice can be
 * checked without TAKX or sound hardware. See run-e2e.sh, which starts the server and the voice bot.
 *
 *   VoiceE2E HOST PORT DIR    (DIR holds alice.p12, rogue.p12, trust.p12, rogue-trust.p12)
 */
public final class VoiceE2E {
    private static int failures;

    /** A 440 Hz tone at the pace of a real microphone. */
    private static final class FakeAudio implements Audio {
        final List<short[]> heard = new ArrayList<>();
        volatile int micFrames;

        @Override public Mic openMic() {
            return new Mic() {
                long n, next = System.currentTimeMillis();

                @Override public boolean read(short[] frame) throws Exception {
                    long wait = next - System.currentTimeMillis();
                    if (wait > 0) Thread.sleep(wait);
                    next += 20;
                    for (int i = 0; i < frame.length; i++, n++)
                        frame[i] = (short) (9000 * Math.sin(2 * Math.PI * 440 * n / RATE));
                    micFrames++;
                    return true;
                }

                @Override public void close() { }
            };
        }

        @Override public Speaker openSpeaker() {
            return new Speaker() {
                @Override public void write(short[] frame) throws Exception {
                    synchronized (heard) { heard.add(frame.clone()); }
                    Thread.sleep(20);
                }

                @Override public void close() { }
            };
        }

        /** Strongest stretch heard: { peak, frequency in Hz }. */
        double[] loudest() {
            synchronized (heard) {
                int peak = 0, crossings = 0, loudFrames = 0;
                short prev = 0;
                for (short[] f : heard) {
                    int p = 0;
                    for (short s : f) p = Math.max(p, Math.abs(s));
                    peak = Math.max(peak, p);
                    if (p < 2000) continue;
                    loudFrames++;
                    for (short s : f) {
                        if (prev < 0 && s >= 0) crossings++;
                        prev = s;
                    }
                }
                double seconds = loudFrames * 0.02;
                return new double[]{peak, seconds == 0 ? 0 : crossings / seconds};
            }
        }
    }

    private static final class Run {
        final AtomicReference<VoiceEngine.State> state = new AtomicReference<>();
        final FakeAudio audio = new FakeAudio();
        final Preferences prefs;
        final VoiceEngine engine;

        Run(String node) throws Exception {
            prefs = Preferences.userRoot().node("takptt-e2e/" + node);
            prefs.clear();
            prefs.putBoolean("tones", false);
            engine = new VoiceEngine(audio, prefs, state::set);
        }

        boolean await(Predicate<VoiceEngine.State> p, long ms) throws Exception {
            long end = System.currentTimeMillis() + ms;
            while (System.currentTimeMillis() < end) {
                VoiceEngine.State s = state.get();
                if (s != null && p.test(s)) return true;
                Thread.sleep(50);
            }
            return false;
        }

        void stop() throws Exception {
            engine.shutdown();
            prefs.removeNode();
        }
    }

    private static void check(String what, boolean ok, String detail) {
        System.out.println((ok ? "PASS " : "FAIL ") + what + (detail.isEmpty() ? "" : ": " + detail));
        if (!ok) failures++;
    }

    private static byte[] file(String dir, String name) throws Exception { return Files.readAllBytes(Paths.get(dir, name)); }

    private static VoiceLogin login(String host, int port, String dir, String client, String trust) throws Exception {
        return VoiceLogin.create("Test TAK", host, port, file(dir, client), "atakatak", file(dir, trust), "atakatak");
    }

    private static void packets() throws Exception {
        long[] values = {0, 1, 127, 128, 16383, 16384, 2097151, 2097152, 268435455, 268435456, 4294967295L, 4294967296L, -1, -2, -4, -5, -300};
        boolean ok = true;
        for (long v : values) {
            ByteArrayOutputStream o = new ByteArrayOutputStream();
            VoicePacket.putVarint(o, v);
            ok &= VoicePacket.getVarint(o.toByteArray(), new int[]{0}) == v;
        }
        check("voice number format round trip", ok, "");
        // Hand-built incoming packet: Opus, target 0, session 7, sequence 300, 3 bytes, last.
        VoicePacket p = VoicePacket.decode(new byte[]{(byte) 0x80, 7, (byte) 0x81, 0x2C, (byte) 0xA0, 0x03, 1, 2, 3});
        check("incoming voice packet", p != null && p.session == 7 && p.sequence == 300 && p.opus.length == 3 && p.last, "");
        byte[] out = VoicePacket.encode(5, 4, new byte[]{9, 9}, 2, false);
        check("outgoing voice packet", out[0] == (byte) 0x85 && out[1] == 4 && out[2] == 2 && out.length == 5, "");
        check("control message round trip", Pb.parse(new Pb().uint(1, 300).str(3, "Alpha").bool(8, true).done()).text(3, "").equals("Alpha"), "");
    }

    public static void main(String[] a) throws Exception {
        String host = a[0], dir = a[2];
        int port = Integer.parseInt(a[1]);
        packets();

        Run bad = new Run("wrongca");
        bad.engine.connect(login(host, port, dir, "alice.p12", "rogue-trust.p12"), "TAKXTEST");
        check("voice server from another CA refused", bad.await(s -> s.error != null && s.error.startsWith("Refused"), 8000), String.valueOf(bad.state.get().error));
        bad.stop();

        Run rogue = new Run("roguecert");
        rogue.engine.connect(login(host, port, dir, "rogue.p12", "trust.p12"), "ROGUE");
        check("client certificate from another CA refused", rogue.await(s -> s.error != null && !s.connected && !s.connecting, 10000), String.valueOf(rogue.state.get().error));
        check("rogue not signed in", !rogue.state.get().connected, "");
        rogue.stop();

        Run run = new Run("main");
        run.engine.connect(login(host, port, dir, "alice.p12", "trust.p12"), "TAKXTEST");
        check("signs in with the TAK certificate", run.await(s -> s.connected && !s.rows.isEmpty(), 15000), String.valueOf(run.state.get().error));
        check("channel list arrives, root shown as LOBBY", run.state.get().rows.stream().anyMatch(r -> r.here && r.name.equals("LOBBY")), "");

        run.engine.create("E2E Team");
        boolean moved = run.await(s -> "E2E Team".equals(s.channel), 5000);
        check("makes a channel and moves into it", moved, String.valueOf(run.state.get().channel));
        if (moved) {
            check("rejoin remembers the channel", "E2E Team".equals(run.prefs.get("last", "")), "");
            run.engine.join(0);
            check("moves back to the lobby", run.await(s -> "LOBBY".equals(s.channel), 5000), "");
        }

        check("hears the bot's tone", run.await(s -> run.audio.loudest()[0] > 3000, 12000),
                "peak " + (int) run.audio.loudest()[0] + ", " + (int) run.audio.loudest()[1] + " Hz");
        double[] heard = run.audio.loudest();
        check("the tone is the bot's 660 Hz", Math.abs(heard[1] - 660) < 40, (int) heard[1] + " Hz");
        check("shows who is talking", run.await(s -> s.receiving != null, 8000), String.valueOf(run.state.get().receiving));

        run.engine.pttDown();
        check("hold to talk transmits", run.await(s -> s.talking, 2000), "");
        Thread.sleep(4000);
        run.engine.pttUp();
        check("release stops", run.await(s -> !s.talking, 2000), "");
        check("microphone ran for the press", run.audio.micFrames > 150, run.audio.micFrames + " frames");

        run.engine.setMode(VoiceEngine.MODE_LATCH);
        Thread.sleep(150);
        run.engine.pttDown();
        run.await(s -> s.talking, 1000);
        run.engine.pttUp();
        Thread.sleep(300);
        check("tap mode keeps talking after release", run.state.get().talking, "");
        run.engine.pttDown();
        check("second tap stops", run.await(s -> !s.talking, 2000), "");
        run.engine.setMode(VoiceEngine.MODE_HOLD);

        run.engine.setTimeout(20);                              // the shortest limit
        Thread.sleep(150);
        run.engine.pttDown();
        boolean cut = run.await(s -> s.note != null && s.note.startsWith("Talk time limit"), 23000);
        check("time limit cuts off talking", cut && !run.state.get().talking, String.valueOf(run.state.get().note));

        Run second = new Run("second");
        second.engine.connect(login(host, port, dir, "alice.p12", "trust.p12"), "TAKXTEST2");
        boolean took = second.await(s -> s.connected, 15000);
        boolean kicked = run.await(s -> s.error != null && s.error.contains("another device"), 8000);
        check("same user elsewhere: server allows one session", took, "");
        check("the first device stops instead of fighting for it", kicked, String.valueOf(run.state.get().error));
        Thread.sleep(7000);
        check("second session stays up, no reconnect storm", second.state.get().connected, "");
        run.stop();
        second.stop();

        System.out.println(failures == 0 ? "ALL PASSED" : failures + " FAILED");
        System.exit(failures == 0 ? 0 : 1);
    }
}
