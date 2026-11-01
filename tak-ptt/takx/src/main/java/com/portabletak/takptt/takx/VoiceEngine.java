package com.portabletak.takptt.takx;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.prefs.Preferences;

import io.github.jaredmdobson.concentus.OpusApplication;
import io.github.jaredmdobson.concentus.OpusEncoder;
import io.github.jaredmdobson.concentus.OpusSignal;

/**
 * The voice engine, the desktop counterpart of the ATAK plugin's VoiceService, with the same
 * behaviour: hold or tap to talk, a talk-permit tone before the microphone opens, a time limit with
 * a warning chirp 5 seconds before, RX and TX picks kept by channel name, full duplex.
 *
 * All state lives on one thread (like Android's main handler); public methods post to it.
 */
final class VoiceEngine implements MumbleClient.Listener {
    interface Listener {
        void changed(State state);
    }

    /** One channel row for the screen. */
    static final class Row {
        final int id, people;
        final String name;
        final boolean here, active, rx, tx;

        Row(int id, String name, int people, boolean here, boolean active, boolean rx, boolean tx) {
            this.id = id;
            this.name = name;
            this.people = people;
            this.here = here;
            this.active = active;
            this.rx = rx;
            this.tx = tx;
        }

        String key() { return id + "|" + name + "|" + people + "|" + here + "|" + active + "|" + rx + "|" + tx; }
    }

    /** What the screen shows. */
    static final class State {
        boolean connected, connecting, talking;
        String serverName, channel, receiving, error, note;
        String mode;
        int timeout;
        boolean tones, rejoin;
        List<Row> rows = Collections.emptyList();
    }

    static final String MODE_HOLD = "hold", MODE_LATCH = "latch";
    static final String LOBBY = "LOBBY";
    private static final int RECORDER_ID = 99999;   // the history recorder (99901-99905: the OpenMANET bridge)
    private static final int TARGET = 5;            // our voice target for talking on several channels
    private static final int BITRATE = 40000;       // as the phones
    private static final long TALKING_MS = 400;     // a user counts as talking this long after a packet
    private static final long RETRY_MS = 5000, RETRY_STARTING_MS = 10000;

    private final Audio audio;
    private final Preferences prefs;
    private final Listener listener;
    private final Mixer mixer;
    private final ScheduledExecutorService main = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "takptt-voice");
        t.setDaemon(true);
        return t;
    });

    private VoiceLogin login;
    private String username;
    private MumbleClient client;
    private boolean connected, connecting, keyed, talking, micPending;
    private String error, note, pendingChannel;
    private boolean pendingTemporary;
    private final Set<Integer> listened = new HashSet<>();
    private final Map<Integer, Long> heardAt = new HashMap<>();     // session to its last voice packet
    private ScheduledFuture<?> openMic, warn, cutoff, retry, activity;
    private volatile long txStop;                                   // bumped to end the microphone thread
    private int voiceTarget;

    VoiceEngine(Audio audio, Preferences prefs, Listener listener) {
        this.audio = audio;
        this.prefs = prefs;
        this.listener = listener;
        this.mixer = new Mixer(audio);
    }

    // ------------------------------------------------------------------ what the screen calls

    void connect(VoiceLogin l, String callsign) {
        main.execute(() -> {
            if (l == login && (connected || connecting)) return;
            login = l;
            username = callsign == null || callsign.isEmpty() ? l.user : callsign;
            error = null;
            open();
        });
    }

    void disconnect() {
        main.execute(() -> {
            login = null;
            cancel(retry);
            stopTalking();
            closeClient();
            connected = connecting = false;
            error = null;
            push();
        });
    }

    /** No TAK connection to use; shown until connect is called. */
    void problem(String message) {
        main.execute(() -> {
            error = message;
            push();
        });
    }

    void shutdown() {
        main.execute(() -> {
            login = null;
            cancel(retry);
            stopTalking();
            closeClient();
            mixer.stop();
            main.shutdown();
        });
    }

    void pttDown() {
        main.execute(() -> {
            if (keyed && MODE_LATCH.equals(mode())) { stopTalking(); return; }
            if (keyed) return;
            if (client == null || !client.isSynced()) {
                tone(connecting ? Tones.NO_CHANNEL : Tones.DENY);
                note = connecting ? "Still connecting" : "Not connected to voice";
                push();
                return;
            }
            if (txIds().isEmpty()) {
                tone(Tones.NO_CHANNEL);
                note = "No TX channel picked";
                push();
                return;
            }
            applyTarget();
            keyed = true;
            micPending = true;
            int delay = tones() ? mixer.play(Tones.PERMIT) + 60 : 0;   // open the mic after the tone has played
            openMic = main.schedule(this::startTalking, delay, TimeUnit.MILLISECONDS);
            push();
        });
    }

    /** A release ends talking in hold mode (tap mode ignores releases). */
    void pttUp() {
        main.execute(() -> {
            if (!MODE_LATCH.equals(mode())) stopTalking();
        });
    }

    void join(int channelId) {
        main.execute(() -> {
            if (client == null || !client.isSynced()) return;
            setPick("rx", channelId, false);                // now in it: no need to listen
            sendSafely(() -> client.join(channelId));
        });
    }

    void create(String name) {
        main.execute(() -> {
            if (client == null || !client.isSynced()) return;
            pendingChannel = name;
            pendingTemporary = false;
            sendSafely(() -> client.createChannel(0, name, false));
        });
    }

    void setRx(int channelId, boolean on) {
        main.execute(() -> {
            setPick("rx", channelId, on);
            applyListening();
            push();
        });
    }

    void setTx(int channelId, boolean on) {
        main.execute(() -> {
            setPick("tx", channelId, on);
            applyTarget();
            push();
        });
    }

    void setMode(String mode) {
        main.execute(() -> {
            prefs.put("mode", mode);
            if (MODE_HOLD.equals(mode) && keyed && !talking) stopTalking();
            push();
        });
    }

    void setTimeout(int seconds) { main.execute(() -> { prefs.putInt("timeout", seconds); push(); }); }

    void setTones(boolean on) { main.execute(() -> { prefs.putBoolean("tones", on); push(); }); }

    void setRejoin(boolean on) { main.execute(() -> { prefs.putBoolean("rejoin", on); push(); }); }

    void clearPicks() {
        main.execute(() -> {
            prefs.remove("rx");
            prefs.remove("tx");
            prefs.putBoolean("tx.here", true);
            applyListening();
            applyTarget();
            push();
        });
    }

    // ------------------------------------------------------------------ settings

    private String mode() { return prefs.get("mode", MODE_HOLD); }

    private int timeoutSeconds() { return Math.max(20, Math.min(90, prefs.getInt("timeout", 60))); }

    private boolean tones() { return prefs.getBoolean("tones", true); }

    private void tone(int[][] steps) { if (tones()) mixer.play(steps); }

    // ------------------------------------------------------------------ connecting

    private void open() {
        cancel(retry);
        closeClient();
        if (login == null) return;
        connecting = true;
        connected = false;
        client = new MumbleClient(login.host, login.port, username, login.tls, this);
        client.start();
        push();
    }

    private void closeClient() {
        if (client != null) client.close();
        client = null;
        listened.clear();
        heardAt.clear();
    }

    private void retry(long ms) {
        cancel(retry);
        retry = main.schedule(this::open, ms, TimeUnit.MILLISECONDS);
    }

    @Override
    public void onSynced() {
        MumbleClient from = current();
        main.execute(() -> {
            if (from != client) return;
            connected = true;
            connecting = false;
            error = null;
            listened.clear();                               // a new session: listen and target again
            voiceTarget = 0;
            rejoin();
            applyListening();
            applyTarget();
            if (activity == null || activity.isDone())
                activity = main.scheduleWithFixedDelay(this::checkActivity, 200, 200, TimeUnit.MILLISECONDS);
            push();
        });
    }

    @Override
    public void onChanged() {
        main.execute(() -> {
            if (client == null || !client.isSynced()) return;
            madeChannel();
            rememberChannel();
            applyListening();
            applyTarget();
            push();
        });
    }

    @Override
    public void onClosed(MumbleClient.Closed why) {
        main.execute(() -> closed(why));
    }

    private void closed(MumbleClient.Closed why) {
        if (why.reason == MumbleClient.CloseReason.ASKED) return;   // we closed it; a new one may be running
        boolean wasTalking = talking || keyed;
        connected = connecting = false;
        stopTalking();
        if (wasTalking) tone(Tones.INTERRUPT);
        client = null;
        heardAt.clear();
        switch (why.reason) {
            case HANDSHAKE:
                // Only a voice server with a certificate from the TAK server's authority is trusted.
                error = "Refused: the voice server's certificate is not from your TAK server";
                break;
            case KICKED:
                // Murmur lets one voice ID in once: retrying here would throw the other device off.
                error = "Voice is signed in on another device with this user" + (why.text.isEmpty() ? "" : " (" + why.text + ")")
                        + ". Reconnect to take it back.";
                break;
            case REJECTED:
                if (why.rejectType == MumbleClient.REJECT_WRONG_SERVER_PW) {
                    // PortableTAK lets nobody in while its sign-in check is starting or restarting.
                    error = "Voice server starting, trying again";
                    retry(RETRY_STARTING_MS);
                } else if (why.rejectType == MumbleClient.REJECT_WRONG_USER_PW) {
                    error = "Voice sign-in refused. Ask your PortableTAK admin to check your account.";
                } else {
                    error = "Voice sign-in refused: " + why.text;
                }
                break;
            default:
                error = "Voice disconnected, trying again: " + why.text;
                if (login != null) retry(RETRY_MS);
        }
        push();
    }

    // ------------------------------------------------------------------ push to talk

    private void startTalking() {
        micPending = false;
        if (!keyed || client == null) return;
        talking = true;
        long id = ++txStop;
        MumbleClient c = client;
        int target = voiceTarget;
        Thread t = new Thread(() -> transmit(c, target, id), "takptt-mic");
        t.setDaemon(true);
        t.start();
        sendPosition();
        int limit = timeoutSeconds();
        warn = main.schedule(() -> { if (talking) tone(Tones.WARNING); }, limit - 5L, TimeUnit.SECONDS);
        cutoff = main.schedule(() -> {
            if (!talking) return;
            stopTalking();
            tone(Tones.CUTOFF);
            note = "Talk time limit (" + timeoutSeconds() + " s) reached";
            push();
        }, limit, TimeUnit.SECONDS);
        push();
    }

    private void stopTalking() {
        keyed = false;
        micPending = false;
        cancel(openMic);
        cancel(warn);
        cancel(cutoff);
        if (talking) txStop++;
        talking = false;
        push();
    }

    /** Microphone to server, on its own thread until stopTalking moves txStop on. */
    private void transmit(MumbleClient c, int target, long id) {
        try (Audio.Mic mic = audio.openMic()) {
            OpusEncoder enc = new OpusEncoder(Audio.RATE, 1, OpusApplication.OPUS_APPLICATION_VOIP);
            enc.setBitrate(BITRATE);
            enc.setSignalType(OpusSignal.OPUS_SIGNAL_VOICE);
            short[] pcm = new short[Audio.FRAME];
            byte[] opus = new byte[1275];
            long sequence = 0;
            while (txStop == id) {
                if (!mic.read(pcm)) throw new IllegalStateException("The microphone stopped");
                boolean last = txStop != id;
                int n = enc.encode(pcm, 0, Audio.FRAME, opus, 0, opus.length);
                c.voice(VoicePacket.encode(target, sequence, opus, n, last));
                sequence += 2;                              // counted in 10 ms steps
                if (last) return;
            }
            int n = enc.encode(new short[Audio.FRAME], 0, Audio.FRAME, opus, 0, opus.length);
            c.voice(VoicePacket.encode(target, sequence, opus, n, true));   // tells receivers we stopped
        } catch (Exception e) {
            String why = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            main.execute(() -> {
                if (txStop != id) return;
                stopTalking();
                tone(Tones.DENY);
                note = "Microphone not available: " + why;
                push();
            });
        }
    }

    /** The history recorder's location message is not sent: voice history shows no place for TAKX users yet. */
    private void sendPosition() {
    }

    // ------------------------------------------------------------------ receiving

    @Override
    public void onVoice(VoicePacket p) {
        mixer.add(p.session, p.opus, p.last);
        main.execute(() -> {
            boolean fresh = !heardAt.containsKey(p.session);
            heardAt.put(p.session, System.currentTimeMillis());
            if (fresh) push();
        });
    }

    private void checkActivity() {
        long now = System.currentTimeMillis();
        boolean gone = heardAt.values().removeIf(t -> now - t > TALKING_MS);
        String mixError = mixer.error();
        if (mixError != null && !mixError.equals(error)) {
            error = "Speaker not available: " + mixError;
            gone = true;
        }
        if (gone) push();
    }

    @Override
    public void onDenied(int type, String reason) {
        main.execute(() -> {
            if (pendingChannel != null) {
                if (!pendingTemporary) {
                    // This server lets users make only temporary channels (gone when empty).
                    pendingTemporary = true;
                    note = "Making " + pendingChannel + " as a temporary channel";
                    sendSafely(() -> client.createChannel(0, pendingChannel, true));
                } else {
                    pendingChannel = null;
                    note = "This server does not let you make channels";
                }
            } else {
                note = "Not allowed: " + reason;
            }
            push();
        });
    }

    // ------------------------------------------------------------------ RX / TX channels

    /*
     * Besides the channel you are in, you can listen to other channels (RX) and talk to several
     * channels at once (TX). The picks are kept by channel name, so they come back after a
     * reconnect. Talking on the channel you are in is the "tx.here" pick (on by default).
     */

    private MumbleClient current() { return client; }

    private static String name(MumbleClient.Channel c) { return c.id == 0 ? LOBBY : c.name; }

    private void madeChannel() {
        if (pendingChannel == null) return;
        for (MumbleClient.Channel c : client.channels())
            if (pendingChannel.equals(c.name)) {
                note = "Made " + c.name;
                pendingChannel = null;
                int id = c.id;
                sendSafely(() -> client.join(id));
                return;
            }
    }

    private void rememberChannel() {
        MumbleClient.Channel c = client.channel(client.here());
        if (c != null) prefs.put("last", name(c));
    }

    /** Go back to the channel you were last in (setting, on by default). */
    private void rejoin() {
        if (!prefs.getBoolean("rejoin", true)) return;
        String last = prefs.get("last", null);
        if (last == null) return;
        for (MumbleClient.Channel c : client.tree())
            if (last.equals(name(c)) && c.id != client.here()) {
                sendSafely(() -> client.join(c.id));
                return;
            }
    }

    private Set<String> picks(String kind) {
        String s = prefs.get(kind, "");
        return s.isEmpty() ? new LinkedHashSet<>() : new LinkedHashSet<>(Arrays.asList(s.split("\n")));
    }

    private void setPick(String kind, int channelId, boolean on) {
        if (client == null) return;
        MumbleClient.Channel c = client.channel(channelId);
        if (c == null) return;
        if (c.id == client.here() && kind.equals("tx")) {
            prefs.putBoolean("tx.here", on);
            return;
        }
        Set<String> s = picks(kind);
        if (on) s.add(name(c)); else s.remove(name(c));
        prefs.put(kind, String.join("\n", s));
    }

    private boolean txHere() { return prefs.getBoolean("tx.here", true); }

    private List<Integer> txIds() {
        List<Integer> ids = new ArrayList<>();
        if (client == null || !client.isSynced()) return ids;
        int here = client.here();
        if (txHere()) ids.add(here);
        Set<String> tx = picks("tx");
        for (MumbleClient.Channel c : client.tree())
            if (c.id != here && tx.contains(name(c))) ids.add(c.id);
        return ids;
    }

    /** Tell the server which channels to listen to (Mumble channel listeners). */
    private void applyListening() {
        if (client == null || !client.isSynced()) return;
        Set<String> rx = picks("rx");
        int here = client.here();
        Set<Integer> want = new HashSet<>();
        for (MumbleClient.Channel c : client.tree())
            if (c.id != here && rx.contains(name(c))) want.add(c.id);
        List<Integer> add = new ArrayList<>(), remove = new ArrayList<>();
        for (int id : want) if (!listened.contains(id)) add.add(id);
        for (int id : listened) if (!want.contains(id)) remove.add(id);
        if (add.isEmpty() && remove.isEmpty()) return;
        sendSafely(() -> client.listen(add, remove));
        listened.clear();
        listened.addAll(want);
    }

    /** Talk on the current channel only (normal), or on several channels through a voice target. */
    private void applyTarget() {
        if (client == null || !client.isSynced()) return;
        List<Integer> ids = txIds();
        if (ids.size() == 1 && ids.get(0) == client.here()) {
            voiceTarget = 0;
            return;
        }
        voiceTarget = TARGET;
        sendSafely(() -> client.voiceTarget(TARGET, ids));
    }

    private interface Send { void run() throws Exception; }

    /** A failed send means the connection is going; onClosed handles that. */
    private void sendSafely(Send s) {
        try {
            s.run();
        } catch (Exception ignored) {
        }
    }

    // ------------------------------------------------------------------ state for the screen

    private void push() {
        State s = new State();
        s.connected = connected;
        s.connecting = connecting;
        s.talking = talking || keyed;
        s.serverName = login == null ? null : login.serverName;
        s.error = error;
        s.note = note;
        note = null;
        s.mode = mode();
        s.timeout = timeoutSeconds();
        s.tones = tones();
        s.rejoin = prefs.getBoolean("rejoin", true);
        if (client != null && client.isSynced()) {
            int me = client.session(), here = client.here();
            Map<Integer, Integer> people = new HashMap<>();
            Set<Integer> active = new HashSet<>();
            String who = null;
            for (MumbleClient.User u : client.users()) {
                if (u.userId == RECORDER_ID || (u.userId > 99900 && u.userId <= 99905)) continue;   // not people
                people.merge(u.channel, 1, Integer::sum);
                if (u.session == me || !heardAt.containsKey(u.session)) continue;
                active.add(u.channel);
                MumbleClient.Channel c = client.channel(u.channel);
                who = u.channel == here || c == null ? u.name : u.name + " · " + name(c);
            }
            s.receiving = who;
            MumbleClient.Channel cur = client.channel(here);
            s.channel = cur == null ? "" : name(cur);
            Set<String> rx = picks("rx");
            List<Integer> tx = txIds();
            List<Row> rows = new ArrayList<>();
            for (MumbleClient.Channel c : client.tree())
                rows.add(new Row(c.id, name(c), people.getOrDefault(c.id, 0), c.id == here, active.contains(c.id),
                        c.id == here || rx.contains(name(c)), tx.contains(c.id)));
            s.rows = rows;
        }
        listener.changed(s);
    }

    private static void cancel(ScheduledFuture<?> f) {
        if (f != null) f.cancel(false);
    }
}
