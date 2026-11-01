package com.portabletak.takptt.plugin;

import android.annotation.SuppressLint;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.net.wifi.WifiManager;
import android.util.Log;

import com.googlecode.javacpp.IntPointer;
import com.googlecode.javacpp.Pointer;

import java.net.DatagramPacket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.MulticastSocket;
import java.net.NetworkInterface;
import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

import se.lublin.humla.audio.javacpp.Opus;

/**
 * Mesh failover: voice straight between phones on the local network (OpenMANET or Wi-Fi) when the
 * voice server cannot be reached. Each 20 ms Opus frame goes out as one UDP multicast packet per
 * channel, encrypted with that channel's key (AES-256-GCM; the keys come from the voice server
 * while it is reachable and are kept on the phone). Only phones holding a channel's key can hear it.
 *
 * Packet: "TPM2" | key tag (8) | nonce (12) | encrypted { send time (8, ms) | sequence (4) |
 * flags (1, bit 0: last) | name length (1) | name | Opus frame } | GCM tag (16). The header is
 * authenticated too. Packets more than 30 s from this phone's clock are dropped, so recorded
 * packets cannot be played back later.
 */
final class MeshLink {
    interface Listener {
        /** Someone is talking (name, channel), or null when the mesh is quiet. */
        void onReceiving(String who, String channel);
    }

    private static final String TAG = "TakPttVoice";
    private static final byte[] MAGIC = {'T', 'P', 'M', '2'};
    private static final long MAX_SKEW_MS = 30000;
    private static final int RATE = 48000, FRAME = 960;           // 20 ms
    private static final SecureRandom RANDOM = new SecureRandom();

    static {
        // Load Opus's native code (and with it JavaCPP's pointers) before the first IntPointer;
        // without a voice server connection Humla has not loaded it yet.
        try {
            Class.forName("se.lublin.humla.audio.javacpp.Opus");
        } catch (ClassNotFoundException ignored) {
        }
    }

    private final Context ctx;
    private final Listener listener;
    /** One week's keys (the server sends this week's and next week's). */
    static final class Epoch {
        final long from, until;                                    // ms
        final Map<String, byte[]> keys;                            // channel -> key

        Epoch(long from, long until, Map<String, byte[]> keys) {
            this.from = from;
            this.until = until;
            this.keys = keys;
        }
    }

    private final java.util.List<Epoch> epochs = new java.util.ArrayList<>();
    private final Map<String, String> tags = new HashMap<>();      // tag (hex) -> channel
    private final Map<String, byte[]> tagKeys = new HashMap<>();   // tag (hex) -> key
    private volatile Set<String> heard = Collections.emptySet();
    private volatile Set<String> talkOn = Collections.emptySet();
    private volatile String self = "";
    private volatile boolean running, transmitting;
    boolean loopback;                                              // testing: also hear yourself
    volatile ManetLink manet;                                      // also gets the microphone's frames
    private InetAddress group;
    private int port;
    private MulticastSocket sock;
    private WifiManager.MulticastLock lock;
    private Thread rx, tx;

    MeshLink(Context ctx, Listener listener) {
        this.ctx = ctx;
        this.listener = listener;
    }

    // ------------------------------------------------------------------ keys and channels

    static String tag(byte[] key) {
        try {
            byte[] h = MessageDigest.getInstance("SHA-256").digest(concat("takptt-tag".getBytes(), key));
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 8; i++) sb.append(String.format("%02x", h[i]));
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    synchronized void setKeys(String groupAddress, int groupPort, java.util.List<Epoch> weeks) {
        try {
            group = InetAddress.getByName(groupAddress);
        } catch (Throwable e) {
            Log.w(TAG, "mesh group: " + e);
        }
        port = groupPort;
        epochs.clear();
        tags.clear();
        tagKeys.clear();
        epochs.addAll(weeks);
        for (Epoch w : weeks)
            for (Map.Entry<String, byte[]> e : w.keys.entrySet()) {
                String t = tag(e.getValue());
                tags.put(t, e.getKey());
                tagKeys.put(t, e.getValue());
            }
    }

    /** The key to send with on a channel now (this week's), or null. */
    private synchronized byte[] sendKey(String channel) {
        long now = System.currentTimeMillis();
        for (Epoch w : epochs)
            if (now >= w.from && now < w.until && w.keys.containsKey(channel))
                return w.keys.get(channel);
        return null;
    }

    /** True while there are keys for this week (a phone away from the server for over a week has none). */
    synchronized boolean hasKeys() {
        long now = System.currentTimeMillis();
        for (Epoch w : epochs)
            if (now >= w.from && now < w.until && !w.keys.isEmpty())
                return group != null;
        return false;
    }

    /** Channels to hear (yours plus RX) and to talk on (TX), and your name. */
    void setChannels(Set<String> hear, Set<String> talk, String name) {
        heard = new HashSet<>(hear);
        talkOn = new HashSet<>(talk);
        self = name == null ? "" : name;
    }

    boolean isRunning() { return running; }

    // ------------------------------------------------------------------ packets

    static byte[] concat(byte[]... parts) {
        int n = 0;
        for (byte[] p : parts) n += p.length;
        byte[] out = new byte[n];
        int i = 0;
        for (byte[] p : parts) { System.arraycopy(p, 0, out, i, p.length); i += p.length; }
        return out;
    }

    static byte[] hex(String s) {
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return b;
    }

    /** One encrypted packet (package-private for tests). */
    static byte[] seal(byte[] key, int seq, boolean last, String name, byte[] opus, int opusLen) throws Exception {
        byte[] nonce = new byte[12];
        RANDOM.nextBytes(nonce);
        byte[] header = concat(MAGIC, hex(tag(key)), nonce);
        byte[] nm = name.getBytes("UTF-8");
        if (nm.length > 255) nm = Arrays.copyOf(nm, 255);
        ByteBuffer plain = ByteBuffer.allocate(8 + 4 + 1 + 1 + nm.length + opusLen);
        plain.putLong(System.currentTimeMillis()).putInt(seq).put((byte) (last ? 1 : 0)).put((byte) nm.length).put(nm).put(opus, 0, opusLen);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, nonce));
        c.updateAAD(header);
        return concat(header, c.doFinal(plain.array()));
    }

    static final class Opened {
        final int seq;
        final boolean last;
        final String name;
        final byte[] opus;

        Opened(int seq, boolean last, String name, byte[] opus) {
            this.seq = seq;
            this.last = last;
            this.name = name;
            this.opus = opus;
        }
    }

    /** Decrypt a packet with the key for its tag; null if it is not ours or was tampered with. */
    static Opened open(byte[] data, int len, byte[] key) {
        try {
            if (len < 4 + 8 + 12 + 16 + 14 || data[0] != 'T' || data[1] != 'P' || data[2] != 'M' || data[3] != '2')
                return null;
            byte[] header = Arrays.copyOfRange(data, 0, 24);
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"),
                    new GCMParameterSpec(128, Arrays.copyOfRange(data, 12, 24)));
            c.updateAAD(header);
            ByteBuffer p = ByteBuffer.wrap(c.doFinal(data, 24, len - 24));
            if (Math.abs(System.currentTimeMillis() - p.getLong()) > MAX_SKEW_MS)
                return null;                                 // too old (or a replay)
            int seq = p.getInt();
            boolean last = (p.get() & 1) != 0;
            byte[] nm = new byte[p.get() & 0xFF];
            p.get(nm);
            byte[] opus = new byte[p.remaining()];
            p.get(opus);
            return new Opened(seq, last, new String(nm, "UTF-8"), opus);
        } catch (Exception e) {
            return null;
        }
    }

    static String packetTag(byte[] data, int len) {
        if (len < 12) return null;
        StringBuilder sb = new StringBuilder();
        for (int i = 4; i < 12; i++) sb.append(String.format("%02x", data[i]));
        return sb.toString();
    }

    // ------------------------------------------------------------------ running

    /** Join the multicast group on Wi-Fi and start listening. */
    synchronized void start() {
        if (running || !hasKeys()) return;
        try {
            WifiManager wifi = ctx.getApplicationContext().getSystemService(WifiManager.class);
            lock = wifi.createMulticastLock("takptt-mesh");
            lock.setReferenceCounted(false);
            lock.acquire();
            sock = new MulticastSocket(port);
            sock.setTimeToLive(4);                       // a few mesh hops
            sock.setLoopbackMode(!loopback);             // true turns loopback OFF
            NetworkInterface nif = wifiInterface();
            if (nif != null) {
                sock.setNetworkInterface(nif);
                sock.joinGroup(new InetSocketAddress(group, port), nif);
            } else {
                sock.joinGroup(group);
            }
            running = true;
            rx = new Thread(this::receiveLoop, "takptt-mesh-rx");
            rx.start();
            Log.i(TAG, "mesh on: " + group.getHostAddress() + ":" + port + " on " + (nif != null ? nif.getName() : "default")
                    + ", " + tags.size() + " channel keys");
        } catch (Exception e) {
            Log.w(TAG, "mesh start: " + e);
            stop();
        }
    }

    synchronized void stop() {
        running = false;
        transmitting = false;
        if (sock != null) sock.close();
        sock = null;
        if (lock != null && lock.isHeld()) lock.release();
        Log.i(TAG, "mesh off");
    }

    private static NetworkInterface wifiInterface() {
        try {
            for (NetworkInterface n : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!n.isUp() || n.isLoopback() || !n.supportsMulticast()) continue;
                if (!n.getName().startsWith("wlan") && !n.getName().startsWith("eth")) continue;
                for (InetAddress a : Collections.list(n.getInetAddresses()))
                    if (a instanceof Inet4Address) return n;
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    // ------------------------------------------------------------------ transmit

    @SuppressLint("MissingPermission")
    void startTalking() {
        if (!running || transmitting) return;
        transmitting = true;
        tx = new Thread(() -> {
            AudioRecord rec = null;
            Pointer enc = null;
            // Sequence numbers keep climbing across transmissions (20 ms steps of the clock), so a
            // new transmission is never taken for a repeat of the last one.
            int seq = (int) ((System.currentTimeMillis() / 20) & 0x3FFFFFFF);
            try {
                int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                rec = new AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE,
                        AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min, FRAME * 8));
                IntPointer err = new IntPointer(1);
                enc = Opus.opus_encoder_create(RATE, 1, Opus.OPUS_APPLICATION_VOIP, err);
                Opus.opus_encoder_ctl(enc, Opus.OPUS_SET_BITRATE_REQUEST, 24000);
                short[] pcm = new short[FRAME];
                byte[] out = new byte[1024];
                rec.startRecording();
                while (transmitting && running) {
                    int got = 0;
                    while (got < FRAME && transmitting) {
                        int n = rec.read(pcm, got, FRAME - got);
                        if (n <= 0) break;
                        got += n;
                    }
                    if (got < FRAME) break;
                    ManetLink m = manet;
                    if (m != null) m.feed(pcm, FRAME);
                    int len = Opus.opus_encode(enc, pcm, FRAME, out, out.length);
                    if (len > 0) send(seq++, false, out, len);
                }
                send(seq, true, new byte[0], 0);           // the end of this transmission
            } catch (Exception e) {
                Log.w(TAG, "mesh talk: " + e);
            } finally {
                if (rec != null) { try { rec.stop(); } catch (Exception ignored) { } rec.release(); }
                if (enc != null) Opus.opus_encoder_destroy(enc);
            }
        }, "takptt-mesh-tx");
        tx.start();
    }

    void stopTalking() {
        transmitting = false;
    }

    private void send(int seq, boolean last, byte[] opus, int len) throws Exception {
        MulticastSocket s = sock;
        if (s == null) return;
        for (String ch : talkOn) {
            byte[] key = sendKey(ch);
            if (key == null) continue;
            byte[] p = seal(key, seq, last, self, opus, len);
            s.send(new DatagramPacket(p, p.length, group, port));
        }
    }

    // ------------------------------------------------------------------ receive

    private static final class Speaker {
        Pointer dec;
        AudioTrack track;
        int lastSeq = -1;
        long lastHeard;
        String channel;
        int frames;
    }

    private void receiveLoop() {
        byte[] buf = new byte[2048];
        Map<String, Speaker> speakers = new HashMap<>();
        // Highest sequence seen per sender and channel, kept 60 s (longer than the 30 s clock
        // window), so a recorded packet played back is never taken as new.
        Map<String, long[]> highest = new HashMap<>();             // id -> {seq, last seen ms}
        short[] pcm = new short[FRAME * 6];
        ByteBuffer direct = ByteBuffer.allocateDirect(2048);
        String showing = null;
        try {
            sock.setSoTimeout(200);
        } catch (Exception ignored) {
        }
        while (running) {
            MulticastSocket s = sock;
            if (s == null) break;
            DatagramPacket dp = new DatagramPacket(buf, buf.length);
            boolean got = false;
            try {
                s.receive(dp);
                got = true;
            } catch (java.net.SocketTimeoutException ignored) {
            } catch (Exception e) {
                if (running) Log.w(TAG, "mesh receive: " + e);
                break;
            }
            long now = System.currentTimeMillis();
            if (got) {
                String t = packetTag(buf, dp.getLength());
                String ch;
                byte[] key;
                synchronized (this) {
                    ch = t == null ? null : tags.get(t);
                    key = t == null ? null : tagKeys.get(t);
                }
                if (key != null && heard.contains(ch)) {
                    Opened o = open(buf, dp.getLength(), key);
                    if (o != null && (loopback || !o.name.equals(self))) {
                        String id = o.name + "@" + ch;
                        Speaker sp = speakers.get(id);
                        if (sp == null) {
                            sp = new Speaker();
                            IntPointer err = new IntPointer(1);
                            sp.dec = Opus.opus_decoder_create(RATE, 1, err);
                            sp.track = new AudioTrack.Builder()
                                    .setAudioAttributes(new AudioAttributes.Builder()
                                            .setUsage(AudioAttributes.USAGE_MEDIA)
                                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                                    .setAudioFormat(new AudioFormat.Builder().setSampleRate(RATE)
                                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                                    .setBufferSizeInBytes(FRAME * 2 * 10).build();
                            sp.track.play();
                            sp.channel = ch;
                            speakers.put(id, sp);
                        }
                        long[] hw = highest.get(id);
                        if (hw == null || o.seq > hw[0]) {                       // drop repeats and replays
                            highest.put(id, new long[]{o.seq, now});
                            sp.lastSeq = o.seq;
                            sp.lastHeard = now;
                            if (o.opus.length > 0) {
                                direct.clear();
                                direct.put(o.opus);
                                direct.flip();
                                int n = Opus.opus_decode(sp.dec, direct, o.opus.length, pcm, pcm.length, 0);
                                if (n > 0) { sp.track.write(pcm, 0, n); sp.frames++; }
                            }
                            if (showing == null || !showing.equals(id)) {
                                showing = id;
                                listener.onReceiving(o.name, ch);
                            }
                            if (o.last) sp.lastHeard = 0;
                        }
                    }
                }
            }
            for (Iterator<Map.Entry<String, long[]>> it = highest.entrySet().iterator(); it.hasNext(); )
                if (now - it.next().getValue()[1] > 60000) it.remove();
            // Speakers quiet for 600 ms are done.
            for (Iterator<Map.Entry<String, Speaker>> it = speakers.entrySet().iterator(); it.hasNext(); ) {
                Map.Entry<String, Speaker> e = it.next();
                if (now - e.getValue().lastHeard > 600) {
                    Speaker sp = e.getValue();
                    Log.i(TAG, "mesh: heard " + e.getKey() + ", " + (sp.frames * 20) + " ms of audio");
                    try { sp.track.stop(); } catch (Exception ignored) { }
                    sp.track.release();
                    Opus.opus_decoder_destroy(sp.dec);
                    it.remove();
                    if (e.getKey().equals(showing)) {
                        showing = null;
                        listener.onReceiving(null, null);
                    }
                }
            }
        }
        for (Speaker sp : speakers.values()) {
            try { sp.track.stop(); } catch (Exception ignored) { }
            sp.track.release();
            Opus.opus_decoder_destroy(sp.dec);
        }
    }
}
