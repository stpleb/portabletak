package com.portabletak.takptt.takx;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLHandshakeException;
import javax.net.ssl.SSLSocket;

/**
 * A small Mumble client over TLS. Voice goes through the TLS connection (Mumble's UDP tunnel), the
 * same way the phones run over Tailscale, so it needs one TCP port and no firewall changes.
 * One object is one connection: after onClosed, make a new one.
 */
final class MumbleClient {
    interface Listener {
        /** Signed in: the channel and user lists are complete. */
        void onSynced();

        /** The channel list or who is where changed. */
        void onChanged();

        void onVoice(VoicePacket packet);

        void onDenied(int type, String reason);

        void onClosed(Closed why);
    }

    enum CloseReason { HANDSHAKE, REJECTED, KICKED, NETWORK, ASKED }

    static final class Closed {
        final CloseReason reason;
        final int rejectType;
        final String text;

        Closed(CloseReason reason, int rejectType, String text) {
            this.reason = reason;
            this.rejectType = rejectType;
            this.text = text;
        }
    }

    static final class Channel {
        final int id;
        volatile int parent = -1;
        volatile String name = "";

        Channel(int id) { this.id = id; }
    }

    static final class User {
        final int session;
        volatile int userId = -1, channel;
        volatile String name = "";

        User(int session) { this.session = session; }
    }

    // Mumble message types and the Reject types the engine looks at
    private static final int VERSION = 0, TUNNEL = 1, AUTHENTICATE = 2, PING = 3, REJECT = 4, SERVER_SYNC = 5,
            CHANNEL_REMOVE = 6, CHANNEL_STATE = 7, USER_REMOVE = 8, USER_STATE = 9, TEXT = 11,
            PERMISSION_DENIED = 12, VOICE_TARGET = 19;
    static final int REJECT_WRONG_USER_PW = 3, REJECT_WRONG_SERVER_PW = 4, REJECT_USERNAME_IN_USE = 5;
    private static final int CLIENT_VERSION = (1 << 16) | (4 << 8);     // 1.4.0: the legacy voice format
    private static final int MAX_MESSAGE = 8 * 1024 * 1024;
    private static final int PING_MS = 15000, READ_TIMEOUT_MS = 45000, CONNECT_TIMEOUT_MS = 10000;

    private final String host, username;
    private final int port;
    private final SSLContext tls;
    private final Listener listener;
    private final Map<Integer, Channel> channels = new ConcurrentHashMap<>();
    private final Map<Integer, User> users = new ConcurrentHashMap<>();
    private final Object sendLock = new Object();

    private volatile SSLSocket socket;
    private volatile DataOutputStream out;
    private volatile boolean closing, synced;
    private volatile int session = -1;
    private volatile Closed closed;

    MumbleClient(String host, int port, String username, SSLContext tls, Listener listener) {
        this.host = host;
        this.port = port;
        this.username = username;
        this.tls = tls;
        this.listener = listener;
    }

    void start() {
        Thread t = new Thread(this::run, "takptt-mumble");
        t.setDaemon(true);
        t.start();
    }

    void close() {
        closing = true;
        SSLSocket s = socket;
        if (s != null) try { s.close(); } catch (IOException ignored) { }
    }

    boolean isSynced() { return synced; }

    int session() { return session; }

    Collection<Channel> channels() { return channels.values(); }

    Collection<User> users() { return users.values(); }

    Channel channel(int id) { return channels.get(id); }

    User user(int session) { return users.get(session); }

    /** The channel we are in, or -1 before sign-in. */
    int here() {
        User me = users.get(session);
        return me == null ? -1 : me.channel;
    }

    // ------------------------------------------------------------------ sending

    void join(int channelId) throws IOException {
        send(USER_STATE, new Pb().uint(1, session).uint(5, channelId).done());
    }

    void createChannel(int parent, String name, boolean temporary) throws IOException {
        send(CHANNEL_STATE, new Pb().uint(2, parent).str(3, name).bool(8, temporary).done());
    }

    /** Mumble channel listeners: hear these channels without joining them. */
    void listen(Collection<Integer> add, Collection<Integer> remove) throws IOException {
        Pb m = new Pb().uint(1, session);
        for (int id : add) m.uint(21, id);
        for (int id : remove) m.uint(22, id);
        send(USER_STATE, m.done());
    }

    void voiceTarget(int id, Collection<Integer> channelIds) throws IOException {
        Pb m = new Pb().uint(1, id);
        for (int c : channelIds) m.bytes(2, new Pb().uint(2, c).done());
        send(VOICE_TARGET, m.done());
    }

    void textTo(int toSession, String text) throws IOException {
        send(TEXT, new Pb().uint(2, toSession).str(5, text).done());
    }

    void voice(byte[] packet) throws IOException { send(TUNNEL, packet); }

    private void send(int type, byte[] body) throws IOException {
        DataOutputStream o = out;
        if (o == null) throw new IOException("not connected");
        synchronized (sendLock) {
            o.writeShort(type);
            o.writeInt(body.length);
            o.write(body);
            o.flush();
        }
    }

    // ------------------------------------------------------------------ the connection

    private void run() {
        Thread pinger = null;
        try {
            SSLSocket s = (SSLSocket) tls.getSocketFactory().createSocket();
            socket = s;
            s.connect(new InetSocketAddress(host, port), CONNECT_TIMEOUT_MS);
            s.setTcpNoDelay(true);
            s.setSoTimeout(READ_TIMEOUT_MS);
            s.startHandshake();
            out = new DataOutputStream(new BufferedOutputStream(s.getOutputStream()));
            DataInputStream in = new DataInputStream(new BufferedInputStream(s.getInputStream()));
            send(VERSION, new Pb().uint(1, CLIENT_VERSION).str(2, "TAK PTT for TAKX")
                    .str(3, System.getProperty("os.name", "")).str(4, System.getProperty("os.version", "")).done());
            send(AUTHENTICATE, new Pb().str(1, username).bool(5, true).done());
            pinger = new Thread(this::pings, "takptt-ping");
            pinger.setDaemon(true);
            pinger.start();
            while (true) {
                int type = in.readUnsignedShort(), len = in.readInt();
                if (len < 0 || len > MAX_MESSAGE) throw new IOException("message too long");
                byte[] body = new byte[len];
                in.readFully(body);
                handle(type, body);
            }
        } catch (SSLHandshakeException e) {
            finish(new Closed(CloseReason.HANDSHAKE, 0, e.getMessage()));
        } catch (EOFException e) {
            finish(new Closed(CloseReason.NETWORK, 0, "The server closed the connection"));
        } catch (Exception e) {
            finish(new Closed(CloseReason.NETWORK, 0, e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage()));
        } finally {
            if (pinger != null) pinger.interrupt();
            synced = false;
            SSLSocket s = socket;
            if (s != null) try { s.close(); } catch (IOException ignored) { }
            listener.onClosed(closing ? new Closed(CloseReason.ASKED, 0, "") : closed);
        }
    }

    /** The first reason wins: a Reject comes before the socket closes. */
    private void finish(Closed why) {
        if (closed == null) closed = why;
    }

    private void pings() {
        try {
            while (true) {
                send(PING, new Pb().uint(1, System.currentTimeMillis()).done());
                Thread.sleep(PING_MS);
            }
        } catch (InterruptedException | IOException ignored) {
            // the reader sees the failure too
        }
    }

    private void handle(int type, byte[] body) throws IOException {
        switch (type) {
            case TUNNEL: {
                VoicePacket p = VoicePacket.decode(body);
                if (p != null) listener.onVoice(p);
                break;
            }
            case REJECT: {
                Pb.Msg m = Pb.parse(body);
                finish(new Closed(CloseReason.REJECTED, (int) m.num(1, 0), m.text(2, "")));
                close(false);
                break;
            }
            case SERVER_SYNC:
                session = (int) Pb.parse(body).num(1, -1);
                synced = true;
                listener.onSynced();
                break;
            case CHANNEL_STATE: {
                Pb.Msg m = Pb.parse(body);
                int id = (int) m.num(1, -1);
                if (id < 0) break;
                Channel c = channels.computeIfAbsent(id, Channel::new);
                if (m.has(2)) c.parent = (int) m.num(2, -1);
                if (m.has(3)) c.name = m.text(3, "");
                if (synced) listener.onChanged();
                break;
            }
            case CHANNEL_REMOVE:
                channels.remove((int) Pb.parse(body).num(1, -1));
                if (synced) listener.onChanged();
                break;
            case USER_STATE: {
                Pb.Msg m = Pb.parse(body);
                if (!m.has(1)) break;
                User u = users.computeIfAbsent((int) m.num(1, -1), User::new);
                if (m.has(3)) u.name = m.text(3, "");
                if (m.has(4)) u.userId = (int) m.num(4, -1);
                if (m.has(5)) u.channel = (int) m.num(5, 0);
                if (synced) listener.onChanged();
                break;
            }
            case USER_REMOVE: {
                Pb.Msg m = Pb.parse(body);
                int who = (int) m.num(1, -1);
                users.remove(who);
                if (who == session) {
                    finish(new Closed(CloseReason.KICKED, 0, m.text(3, "")));
                    close(false);
                } else if (synced) {
                    listener.onChanged();
                }
                break;
            }
            case PERMISSION_DENIED: {
                Pb.Msg m = Pb.parse(body);
                listener.onDenied((int) m.num(5, 0), m.text(4, ""));
                break;
            }
            default:
                break;
        }
    }

    /** Close from the reader itself, keeping the reason already set. */
    private void close(boolean asked) throws IOException {
        closing = asked;
        throw new EOFException();
    }

    /** The channel tree in order, the root first. */
    List<Channel> tree() {
        List<Channel> out = new ArrayList<>();
        Channel root = channels.get(0);
        if (root != null) addTree(root, out);
        return out;
    }

    private void addTree(Channel c, List<Channel> list) {
        list.add(c);
        List<Channel> subs = new ArrayList<>();
        for (Channel s : channels.values())
            if (s.parent == c.id && s.id != c.id) subs.add(s);
        subs.sort((a, b) -> a.name.compareToIgnoreCase(b.name));
        for (Channel s : subs) addTree(s, list);
    }
}
