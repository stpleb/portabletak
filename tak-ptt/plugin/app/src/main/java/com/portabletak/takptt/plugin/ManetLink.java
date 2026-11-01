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
import java.security.SecureRandom;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

import se.lublin.humla.audio.javacpp.Opus;

/**
 * OpenMANET talk groups: voice in OpenMANET's own comms format, so TAK PTT talks with OpenMANET
 * nodes, the ManetNode plugin and the ATAK Voice plugin over the mesh, with or without a voice
 * server. Opus 48 kHz mono, 20 ms frames, RTP payload type 111, sent to the talk group's
 * multicast addresses (ADDRESSES) and port (talk group 1 = port 38801, 2 = 38803 ... 5 = 38809).
 *
 * Talk groups are not encrypted: anyone on the mesh can listen.
 */
final class ManetLink {
    interface Listener {
        /** Someone is talking on talk group g, or g = 0 when all are quiet. */
        void onReceiving(int group);

        /** One transmission heard on talk group g ended: its audio (48 kHz mono), for transcripts. */
        default void onHeard(int group, short[] pcm, int n) { }
    }

    /** openmanetd (a node's web PTT and audio modules), then the ManetNode plugin: sent to both,
     *  heard on both, a packet heard twice counts once. */
    static final String[] ADDRESSES = {"239.192.41.1", "239.41.0.1"};
    static final int[] PORTS = {38801, 38803, 38805, 38807, 38809};
    static final int GROUPS = PORTS.length;
    private static final String TAG = "TakPttVoice";
    private static final int RATE = 48000, FRAME = 960, PT_OPUS = 111, TTL = 32;
    private static final long QUIET_MS = 600;
    private static final int KEEP_MAX = RATE * 60;      // keep at most a minute of a transmission

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
    private final int ssrc = new SecureRandom().nextInt();
    private final Map<Integer, Thread> rx = new HashMap<>();
    private final Map<Integer, MulticastSocket> rxSocks = new HashMap<>();
    private final long[] lastHeard = new long[GROUPS + 1];
    private volatile Set<Integer> talkOn = Collections.emptySet();
    private WifiManager.MulticastLock lock;
    private int showing;

    // transmit
    private volatile boolean transmitting;
    private MulticastSocket txSock;
    private Pointer enc;
    private final short[] pending = new short[FRAME];
    private int pendingLen;
    private int seq = new SecureRandom().nextInt(0xFFFF);
    private long ts = new SecureRandom().nextInt() & 0xFFFFFFFFL;
    private boolean firstPacket;

    ManetLink(Context ctx, Listener listener) {
        this.ctx = ctx;
        this.listener = listener;
    }

    static int port(int group) { return PORTS[group - 1]; }

    /** Listen on these talk groups (1 to 5) and talk on those; empty sets turn it all off. */
    synchronized void setGroups(Set<Integer> hear, Set<Integer> talk) {
        talkOn = new HashSet<>(talk);
        for (Iterator<Map.Entry<Integer, Thread>> it = rx.entrySet().iterator(); it.hasNext(); ) {
            int g = it.next().getKey();
            if (!hear.contains(g)) {
                MulticastSocket s = rxSocks.remove(g);
                if (s != null) s.close();
                it.remove();
            }
        }
        for (int g : hear) {
            if (g < 1 || g > GROUPS || rx.containsKey(g)) continue;
            Thread t = new Thread(() -> receive(g), "takptt-manet-rx-" + g);
            rx.put(g, t);
            t.start();
        }
        boolean want = !hear.isEmpty() || !talk.isEmpty();
        if (want && lock == null) {
            WifiManager wifi = ctx.getApplicationContext().getSystemService(WifiManager.class);
            lock = wifi.createMulticastLock("takptt-manet");
            lock.setReferenceCounted(false);
            lock.acquire();
        } else if (!want && lock != null) {
            if (lock.isHeld()) lock.release();
            lock = null;
        }
    }

    boolean canTalk() { return !talkOn.isEmpty(); }

    /** True while someone was heard on talk group g in the last moment. */
    boolean active(int g) { return System.currentTimeMillis() - lastHeard[g] < QUIET_MS; }

    synchronized void stop() {
        setGroups(Collections.emptySet(), Collections.emptySet());
        stopTalking();
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

    /**
     * Start sending on the picked talk groups. With ownMic the link records the microphone itself
     * (no voice server); otherwise the caller passes the microphone's frames to feed().
     */
    @SuppressLint("MissingPermission")
    synchronized void startTalking(boolean ownMic) {
        if (transmitting || talkOn.isEmpty()) return;
        try {
            txSock = new MulticastSocket();
            NetworkInterface nif = wifiInterface();
            if (nif != null) txSock.setNetworkInterface(nif);
            txSock.setTimeToLive(TTL);
            txSock.setLoopbackMode(true);                           // true turns loopback OFF
            IntPointer err = new IntPointer(1);
            enc = Opus.opus_encoder_create(RATE, 1, Opus.OPUS_APPLICATION_VOIP, err);
            Opus.opus_encoder_ctl(enc, Opus.OPUS_SET_BITRATE_REQUEST, 32000);
        } catch (Exception e) {
            Log.w(TAG, "manet talk: " + e);
            closeTx();
            return;
        }
        pendingLen = 0;
        firstPacket = true;
        transmitting = true;
        Log.i(TAG, "transmitting on OpenMANET talk groups " + talkOn);
        if (!ownMic) return;
        new Thread(() -> {
            AudioRecord rec = null;
            try {
                int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                rec = new AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE,
                        AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min, FRAME * 8));
                short[] pcm = new short[FRAME];
                rec.startRecording();
                while (transmitting) {
                    int n = rec.read(pcm, 0, FRAME);
                    if (n <= 0) break;
                    feed(pcm, n);
                }
            } catch (Exception e) {
                Log.w(TAG, "manet mic: " + e);
            } finally {
                if (rec != null) { try { rec.stop(); } catch (Exception ignored) { } rec.release(); }
            }
        }, "takptt-manet-mic").start();
    }

    /** Microphone frames (48 kHz mono); sent as 20 ms Opus packets. */
    synchronized void feed(short[] frame, int n) {
        if (!transmitting) return;
        int i = 0;
        while (i < n) {
            int take = Math.min(FRAME - pendingLen, n - i);
            System.arraycopy(frame, i, pending, pendingLen, take);
            pendingLen += take;
            i += take;
            if (pendingLen == FRAME) {
                pendingLen = 0;
                byte[] pkt = new byte[1500];
                byte[] out = new byte[1200];
                int len = Opus.opus_encode(enc, pending, FRAME, out, out.length);
                if (len <= 0) continue;
                pkt[0] = (byte) 0x80;
                pkt[1] = (byte) ((firstPacket ? 0x80 : 0) | PT_OPUS);
                pkt[2] = (byte) (seq >> 8);
                pkt[3] = (byte) seq;
                pkt[4] = (byte) (ts >> 24);
                pkt[5] = (byte) (ts >> 16);
                pkt[6] = (byte) (ts >> 8);
                pkt[7] = (byte) ts;
                pkt[8] = (byte) (ssrc >> 24);
                pkt[9] = (byte) (ssrc >> 16);
                pkt[10] = (byte) (ssrc >> 8);
                pkt[11] = (byte) ssrc;
                System.arraycopy(out, 0, pkt, 12, len);
                try {
                    for (String a : ADDRESSES) {
                        InetAddress group = InetAddress.getByName(a);
                        for (int g : talkOn)
                            txSock.send(new DatagramPacket(pkt, 12 + len, group, port(g)));
                    }
                } catch (Exception e) {
                    Log.w(TAG, "manet send: " + e);
                }
                firstPacket = false;
                seq = (seq + 1) & 0xFFFF;
                ts = (ts + FRAME) & 0xFFFFFFFFL;
            }
        }
    }

    synchronized void stopTalking() {
        if (!transmitting) return;
        transmitting = false;
        closeTx();
        Log.i(TAG, "OpenMANET transmit off");
    }

    private void closeTx() {
        if (txSock != null) txSock.close();
        txSock = null;
        if (enc != null) Opus.opus_encoder_destroy(enc);
        enc = null;
    }

    // ------------------------------------------------------------------ receive

    /** Offset of the Opus payload in an RTP packet, or -1 if it is not RTP payload type 111. */
    static int rtpPayload(byte[] b, int len) {
        if (len < 13 || (b[0] & 0xC0) != 0x80 || (b[1] & 0x7F) != PT_OPUS) return -1;
        int off = 12 + 4 * (b[0] & 0x0F);
        if ((b[0] & 0x10) != 0) {
            if (len < off + 4) return -1;
            off += 4 + 4 * (((b[off + 2] & 0xFF) << 8) | (b[off + 3] & 0xFF));
        }
        int pad = (b[0] & 0x20) != 0 ? (b[len - 1] & 0xFF) : 0;
        return off < len - pad ? off : -1;
    }

    private static final class Speaker {
        Pointer dec;
        AudioTrack track;
        long lastHeard;
        int lastSeq = -1;
        short[] kept = new short[RATE * 4];
        int keptN;

        void keep(short[] pcm, int n) {
            if (keptN + n > KEEP_MAX) return;
            if (keptN + n > kept.length) kept = java.util.Arrays.copyOf(kept, Math.min(KEEP_MAX, kept.length * 2 + n));
            System.arraycopy(pcm, 0, kept, keptN, n);
            keptN += n;
        }
    }

    private void receive(int g) {
        Thread me = Thread.currentThread();
        while (isListening(g, me)) {
            Map<Integer, Speaker> speakers = new HashMap<>();
            MulticastSocket s = null;
            try {
                s = new MulticastSocket(null);
                s.setReuseAddress(true);
                s.bind(new InetSocketAddress(port(g)));
                s.setSoTimeout(200);
                NetworkInterface nif = wifiInterface();
                for (String a : ADDRESSES) {
                    InetAddress group = InetAddress.getByName(a);
                    if (nif != null) s.joinGroup(new InetSocketAddress(group, port(g)), nif);
                    else s.joinGroup(group);
                }
                synchronized (this) {
                    if (rx.get(g) != me) break;
                    rxSocks.put(g, s);
                }
                Log.i(TAG, "OpenMANET talk group " + g + " on: " + String.join(", ", ADDRESSES) + " port " + port(g));
                byte[] buf = new byte[2048];
                short[] pcm = new short[FRAME * 6];
                ByteBuffer direct = ByteBuffer.allocateDirect(2048);
                while (isListening(g, me)) {
                    DatagramPacket p = new DatagramPacket(buf, buf.length);
                    boolean got = false;
                    try {
                        s.receive(p);
                        got = true;
                    } catch (java.net.SocketTimeoutException ignored) {
                    }
                    long now = System.currentTimeMillis();
                    if (got) {
                        int off = rtpPayload(buf, p.getLength());
                        int from = ByteBuffer.wrap(buf, 8, 4).getInt();
                        if (off >= 0 && from != ssrc) {
                            Speaker sp = speakers.get(from);
                            if (sp == null) {
                                sp = new Speaker();
                                sp.dec = Opus.opus_decoder_create(RATE, 1, new IntPointer(1));
                                sp.track = new AudioTrack.Builder()
                                        .setAudioAttributes(new AudioAttributes.Builder()
                                                .setUsage(AudioAttributes.USAGE_MEDIA)
                                                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                                        .setAudioFormat(new AudioFormat.Builder().setSampleRate(RATE)
                                                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                                                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                                        .setBufferSizeInBytes(FRAME * 2 * 10).build();
                                sp.track.play();
                                speakers.put(from, sp);
                            }
                            int rseq = ((buf[2] & 0xFF) << 8) | (buf[3] & 0xFF);
                            if (sp.lastSeq >= 0 && !(((rseq - sp.lastSeq) & 0xFFFF) > 0 && ((rseq - sp.lastSeq) & 0xFFFF) < 0x8000))
                                continue;                   // the same packet from the other address
                            sp.lastSeq = rseq;
                            sp.lastHeard = now;
                            lastHeard[g] = now;
                            int len = p.getLength() - off;
                            direct.clear();
                            direct.put(buf, off, len);
                            direct.flip();
                            int n = Opus.opus_decode(sp.dec, direct, len, pcm, pcm.length, 0);
                            if (n > 0) {
                                sp.track.write(pcm, 0, n);
                                sp.keep(pcm, n);
                            }
                            heard(g);
                        }
                    }
                    for (Iterator<Map.Entry<Integer, Speaker>> it = speakers.entrySet().iterator(); it.hasNext(); ) {
                        Speaker sp = it.next().getValue();
                        if (now - sp.lastHeard > QUIET_MS) {
                            listener.onHeard(g, sp.kept, sp.keptN);
                            release(sp);
                            it.remove();
                        }
                    }
                    if (speakers.isEmpty()) quiet(g);
                }
            } catch (Exception e) {
                if (isListening(g, me)) {
                    Log.w(TAG, "OpenMANET talk group " + g + ": " + e);
                    try { Thread.sleep(3000); } catch (InterruptedException ie) { break; }
                }
            } finally {
                for (Speaker sp : speakers.values()) release(sp);
                if (s != null) s.close();
                quiet(g);
            }
        }
        Log.i(TAG, "OpenMANET talk group " + g + " off");
    }

    private synchronized boolean isListening(int g, Thread t) { return rx.get(g) == t; }

    private static void release(Speaker sp) {
        try { sp.track.stop(); } catch (Exception ignored) { }
        sp.track.release();
        Opus.opus_decoder_destroy(sp.dec);
    }

    private synchronized void heard(int g) {
        if (showing == g) return;
        showing = g;
        listener.onReceiving(g);
    }

    private synchronized void quiet(int g) {
        if (showing != g) return;
        showing = 0;
        listener.onReceiving(0);
    }
}
