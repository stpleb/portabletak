package com.portabletak.takptt.plugin;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.os.Process;
import android.os.RemoteException;
import android.provider.Settings;
import android.util.Log;
import android.view.KeyEvent;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Set;
import java.util.Iterator;
import java.util.List;

import se.lublin.humla.Constants;
import se.lublin.humla.HumlaService;
import se.lublin.humla.exception.AudioException;
import se.lublin.humla.model.IChannel;
import se.lublin.humla.model.IUser;
import se.lublin.humla.model.Server;
import se.lublin.humla.model.TalkState;
import se.lublin.humla.net.HumlaTCPMessageType;
import se.lublin.humla.protobuf.Mumble;
import se.lublin.humla.util.HumlaException;
import se.lublin.humla.util.HumlaObserver;

/**
 * The voice engine: Humla's Mumble client as a foreground service in this plugin's own process,
 * so it keeps the microphone and keeps running while ATAK is in the background. ATAK's side of
 * the plugin (VoiceClient) talks to it over a Messenger; only ATAK may use it.
 *
 * Push to talk comes from the plugin's screen in ATAK, the floating button (FloatingPtt) and
 * hardware keys (KeyService). Hold mode talks while pressed; latch mode starts and stops with a
 * press. A talk-permit tone plays before the microphone opens, and talking is cut off after the
 * time limit (a warning chirp 5 seconds before).
 */
public class VoiceService extends HumlaService {
    static final String TAG = "TakPttVoice";
    static final String BIND = Proto.BIND;
    private static final String NOTIFY_CHANNEL = "takptt-voice";
    private static final int NOTIFY_ID = 64738;

    /** For KeyService and SetupActivity, which run in the same process. */
    static volatile VoiceService instance;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final List<Messenger> clients = new ArrayList<>();
    private Messenger messenger;
    private SharedPreferences prefs;
    private FloatingPtt floating;
    private PttCalls calls;
    private MeshLink mesh;
    private boolean meshActive;
    private long downSince;                     // when the server connection was lost (0: connected)
    private String meshReceiving;
    private ManetLink manet;
    private Transcriber liveText;                   // talk-group transmissions heard, to text
    private static final int OM_LOG_MAX = 30;
    private int manetReceiving;                     // OpenMANET talk group someone is talking on, or 0
    private int meshLogCount;
    private boolean onHold;                     // a cellular call has TAK PTT on hold
    private static final int WARM_MS = 5000;    // keep the call (audio route) this long after traffic

    private boolean connected, connecting, keyed, talking, untrusted, foreground, foregroundMic;
    private boolean fromTak;
    private String serverName, receiving, error, note, pendingChannel;
    private boolean pendingTemporary;
    private Bundle lastConnect;
    private Runnable pendingConnect;            // a connect waiting for the old connection to close

    // ------------------------------------------------------------------ lifecycle

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        prefs = getSharedPreferences("takptt-voice", MODE_PRIVATE);
        PttCalls.route = prefs.getString(Proto.ROUTE, Proto.ROUTE_AUTO);
        messenger = new Messenger(new Handler(Looper.getMainLooper(), this::handle));
        registerObserver(observer);
        floating = new FloatingPtt(this, new FloatingPtt.Press() {
            @Override public void down() { pttDown(); }
            @Override public void up() { pttUp(); }
        });
        // Redraw (floating button, state in ATAK) as soon as the user grants "appear on top" or
        // turns the PTT key setting on or off.
        getSystemService(android.app.AppOpsManager.class).startWatchingMode(
                android.app.AppOpsManager.OPSTR_SYSTEM_ALERT_WINDOW, getPackageName(),
                (op, pkg) -> pushState());
        getContentResolver().registerContentObserver(
                Settings.Secure.getUriFor(Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES), false,
                new android.database.ContentObserver(main) {
                    @Override public void onChange(boolean self) { pushState(); }
                });
        mesh = new MeshLink(this, (who, channel) -> main.post(() -> {
            meshReceiving = who == null ? null : who + (channel.equals(currentName()) ? "" : " · " + channel);
            pushState();
        }));
        loadMeshKeys();
        main.postDelayed(meshCheck, 2000);
        manet = new ManetLink(this, new ManetLink.Listener() {
            @Override public void onReceiving(int g) {
                main.post(() -> { manetReceiving = g; pushState(); });
            }

            @Override public void onHeard(int g, short[] pcm, int n) {
                short[] copy = java.util.Arrays.copyOf(pcm, n);
                main.post(() -> transcribeHeard(g, copy));
            }
        });
        if (Transcriber.available(this)) liveText = new Transcriber(this, null);
        mesh.manet = manet;
        applyManet();
        try {
            calls = new PttCalls(this, new PttCalls.Listener() {
                @Override public void onCallInterrupted() { main.post(() -> holdForCall(true)); }
                @Override public void onCallResumed() { main.post(() -> holdForCall(false)); }
                @Override public void onCallAudio(boolean inCall) { main.post(() -> callAudio(inCall)); }
            });
        } catch (Exception e) {
            Log.w(TAG, "Telecom not available: " + e);
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        if (BIND.equals(intent.getAction()))
            return messenger.getBinder();
        return super.onBind(intent);
    }

    @Override
    public void onDestroy() {
        instance = null;
        if (calls != null)
            calls.unregister();
        floating.hide();
        manet.stop();
        if (liveText != null) liveText.dispose();
        super.onDestroy();
    }

    // ------------------------------------------------------------------ messages from ATAK

    /** ATAK's package names (CIV and MIL builds); no other app may drive the voice service. */
    private static final java.util.Set<String> ATAK = new java.util.HashSet<>(java.util.Arrays.asList(
            "com.atakmap.app.civ", "com.atakmap.app.mil", "com.atakmap.app"));

    /** Only ATAK (and this plugin itself) may drive the voice service. */
    private boolean allowed(int uid) {
        if (uid == Process.myUid())
            return true;
        String[] pkgs = getPackageManager().getPackagesForUid(uid);
        if (pkgs != null)
            for (String p : pkgs)
                if (ATAK.contains(p))
                    return true;
        return false;
    }

    private boolean handle(Message m) {
        if (!allowed(m.sendingUid)) {
            Log.w(TAG, "refused a message from uid " + m.sendingUid);
            return true;
        }
        switch (m.what) {
            case Proto.REGISTER:
                if (m.replyTo != null && !clients.contains(m.replyTo))
                    clients.add(m.replyTo);
                pushState();
                break;
            case Proto.UNREGISTER:
                clients.remove(m.replyTo);
                break;
            case Proto.CONNECT:
                connectFrom(m.getData());
                break;
            case Proto.DISCONNECT:
                lastConnect = null;
                stopTalking();
                disconnect();
                leaveForeground();
                break;
            case Proto.PTT_DOWN:
                pttDown();
                break;
            case Proto.PTT_UP:
                pttUp();
                break;
            case Proto.JOIN:
                if (isSynchronized()) {
                    joinChannel(m.arg1);
                    setPick("rx", m.arg1, false);       // now in it: no need to listen
                    main.postDelayed(() -> { applyListening(); applyTarget(); pushState(); }, 300);
                }
                break;
            case Proto.CREATE:
                if (isSynchronized()) {
                    pendingChannel = m.getData().getString(Proto.NAME);
                    pendingTemporary = false;
                    createChannel(getRootChannel().getId(), pendingChannel, "", 0, false);
                }
                break;
            case Proto.SETTINGS:
                saveSettings(m.getData());
                break;
            case Proto.POSITION:
                lat = m.getData().getDouble(Proto.LAT);
                lon = m.getData().getDouble(Proto.LON);
                hasPosition = true;
                break;
            case Proto.MESH_KEYS:
                saveMeshKeys(m.getData());
                break;
            case Proto.SET_RX:
                setPick("rx", m.arg1, m.arg2 == 1);
                applyListening();
                pushState();
                break;
            case Proto.SET_TX:
                setPick("tx", m.arg1, m.arg2 == 1);
                applyTarget();
                pushState();
                break;
            case Proto.SET_OM_RX:
            case Proto.SET_OM_TX: {
                String kind = m.what == Proto.SET_OM_RX ? "omRx" : "omTx";
                Set<String> s = new HashSet<>(prefs.getStringSet(kind, new HashSet<>()));
                if (m.arg2 == 1) s.add(String.valueOf(m.arg1)); else s.remove(String.valueOf(m.arg1));
                prefs.edit().putStringSet(kind, s).apply();
                applyManet();
                pushState();
                break;
            }
        }
        return true;
    }

    // ------------------------------------------------------------------ settings

    String mode() { return prefs.getString(Proto.MODE, Proto.MODE_HOLD); }

    int timeoutSeconds() { return Math.max(20, Math.min(90, prefs.getInt(Proto.TIMEOUT, 60))); }

    boolean tones() { return prefs.getBoolean(Proto.TONES, true); }

    String volumeKey() { return prefs.getString(Proto.VOLUME_KEY, Proto.KEY_OFF); }

    boolean floatingWanted() { return prefs.getBoolean(Proto.FLOATING, false); }

    int floatSize() { return prefs.getInt(Proto.FLOAT_SIZE, Proto.FLOAT_MEDIUM); }

    // ------------------------------------------------------------------ talk-group transcripts

    private boolean transcripts() { return liveText != null && prefs.getBoolean(Proto.TRANSCRIPTS, true); }

    /** Talk-group transmissions heard here, newest first: "millis|group|text". */
    private ArrayList<String> omLog() {
        ArrayList<String> out = new ArrayList<>();
        try {
            org.json.JSONArray a = new org.json.JSONArray(prefs.getString(Proto.OM_LOG, "[]"));
            for (int i = 0; i < a.length(); i++) out.add(a.getString(i));
        } catch (Exception ignored) {
        }
        return out;
    }

    private void transcribeHeard(int g, short[] pcm) {
        if (!transcripts() || pcm.length < 48000 / 2) return;          // under half a second
        long at = System.currentTimeMillis();
        liveText.add("om-" + at, pcm, pcm.length, (key, text) -> {
            if (text == null || text.isEmpty()) return;
            ArrayList<String> log = omLog();
            log.add(0, at + "|" + g + "|" + text);
            while (log.size() > OM_LOG_MAX) log.remove(log.size() - 1);
            prefs.edit().putString(Proto.OM_LOG, new org.json.JSONArray(log).toString()).apply();
            pushState();
        });
    }

    private void saveSettings(Bundle b) {
        SharedPreferences.Editor e = prefs.edit();
        if (b.containsKey(Proto.MODE)) e.putString(Proto.MODE, b.getString(Proto.MODE));
        if (b.containsKey(Proto.TIMEOUT)) e.putInt(Proto.TIMEOUT, b.getInt(Proto.TIMEOUT));
        if (b.containsKey(Proto.TONES)) e.putBoolean(Proto.TONES, b.getBoolean(Proto.TONES));
        if (b.containsKey(Proto.VOLUME_KEY)) e.putString(Proto.VOLUME_KEY, b.getString(Proto.VOLUME_KEY));
        if (b.containsKey(Proto.FLOATING)) e.putBoolean(Proto.FLOATING, b.getBoolean(Proto.FLOATING));
        if (b.containsKey(Proto.FLOAT_SIZE)) e.putInt(Proto.FLOAT_SIZE, b.getInt(Proto.FLOAT_SIZE));
        if (b.containsKey(Proto.ROUTE)) e.putString(Proto.ROUTE, b.getString(Proto.ROUTE));
        if (b.containsKey(Proto.REJOIN)) e.putBoolean(Proto.REJOIN, b.getBoolean(Proto.REJOIN));
        if (b.containsKey(Proto.MESH)) e.putBoolean(Proto.MESH, b.getBoolean(Proto.MESH));
        if (b.containsKey(Proto.MESH_LOOP)) e.putBoolean(Proto.MESH_LOOP, b.getBoolean(Proto.MESH_LOOP));
        if (b.containsKey(Proto.TRANSCRIPTS)) e.putBoolean(Proto.TRANSCRIPTS, b.getBoolean(Proto.TRANSCRIPTS));
        if (b.getBoolean(Proto.CLEAR_PICKS)) {
            e.remove("rx").remove("tx").putBoolean("tx.here", true);
        }
        e.apply();
        PttCalls.route = prefs.getString(Proto.ROUTE, Proto.ROUTE_AUTO);
        if (calls != null) calls.applyRoute();
        if (b.getBoolean(Proto.CLEAR_PICKS)) { applyListening(); applyTarget(); }
        if (Proto.MODE_HOLD.equals(mode()) && keyed && !talking)
            stopTalking();
        pushState();
    }

    boolean micAllowed() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    boolean overlayAllowed() {
        return Settings.canDrawOverlays(this);
    }

    boolean keysAllowed() {
        String on = Settings.Secure.getString(getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        return on != null && on.contains(Proto.PACKAGE + "/");
    }

    /** SetupActivity calls this after the user answers a permission request. */
    void permissionsChanged() {
        if (lastConnect != null && (!foreground || (micAllowed() && !foregroundMic)))
            enterForeground();                           // add the microphone type now it is allowed
        pushState();
    }

    // ------------------------------------------------------------------ connecting

    private void connectFrom(Bundle b) {
        lastConnect = b;
        untrusted = false;
        error = null;
        fromTak = b.getBoolean(Proto.FROM_TAK);
        serverName = b.getString(Proto.SERVER_NAME);
        connecting = true;
        enterForeground();
        pushState();
        final File identity = new File(getFilesDir(), "takptt-identity.p12");
        final File takTrust = new File(getFilesDir(), "takptt-tak-trust.bks");
        final File tofuTrust = new File(getFilesDir(), "takptt-trust.bks");
        new Thread(() -> {
            byte[] cert = b.getByteArray(Proto.CERT);
            String certPassword = b.getString(Proto.CERT_PASSWORD, "");
            String trust = null;
            try {
                if (fromTak) {
                    @SuppressWarnings("unchecked")
                    ArrayList<byte[]> der = (ArrayList<byte[]>) b.getSerializable(Proto.TRUSTED);
                    List<X509Certificate> cas = new ArrayList<>();
                    CertificateFactory cf = CertificateFactory.getInstance("X.509");
                    if (der != null)
                        for (byte[] d : der)
                            cas.add((X509Certificate) cf.generateCertificate(new ByteArrayInputStream(d)));
                    TrustStore.write(takTrust, cas);
                    trust = takTrust.getAbsolutePath();
                } else {
                    if (cert == null) {
                        cert = Identity.load(identity);
                        certPassword = Identity.PASSWORD;
                    }
                    trust = tofuTrust.exists() ? tofuTrust.getAbsolutePath() : null;
                }
            } catch (Exception e) {
                Log.e(TAG, "setup", e);
                String msg = "Could not set up the certificates: " + e.getMessage();
                main.post(() -> { error = msg; connecting = false; pushState(); });
                return;
            }
            final byte[] fc = cert;
            final String fpw = certPassword, ftrust = trust;
            Runnable go = () -> {
                pendingConnect = null;
                if (lastConnect != b)
                    return;                     // a newer connect came in
                try {
                    String host = b.getString(Proto.HOST);
                    int port = b.getInt(Proto.PORT, TakVoice.PORT);
                    Log.i(TAG, "connecting to " + host + ":" + port + " as " + b.getString(Proto.CALLSIGN)
                            + (fromTak ? " (TAK certificate " + b.getString(Proto.USER) + ")" : ""));
                    connectTo(host, port, b.getString(Proto.CALLSIGN), ftrust, TrustStore.PASSWORD, fc, fpw);
                } catch (Exception e) {
                    Log.e(TAG, "connect", e);
                    error = "Could not connect: " + e.getMessage();
                    connecting = false;
                    pushState();
                }
            };
            main.post(() -> {
                stopTalking();
                if (isConnectionEstablished()) {
                    // Wait for the old connection to close first (or 2 s), or it takes the new one down.
                    pendingConnect = go;
                    disconnect();
                    main.postDelayed(() -> { if (pendingConnect == go) { pendingConnect = null; go.run(); } }, 2000);
                } else {
                    go.run();
                }
            });
        }, "takptt-setup").start();
    }

    /** Connect with push-to-talk and Opus voice (Humla needs every audio setting given). */
    private void connectTo(String host, int port, String username, String trustStore, String trustStorePassword,
                           byte[] certificate, String certificatePassword) throws AudioException {
        Bundle extras = new Bundle();
        extras.putParcelable(EXTRAS_SERVER, new Server(-1, "PortableTAK", host, port, username, ""));
        extras.putInt(EXTRAS_TRANSMIT_MODE, Constants.TRANSMIT_PUSH_TO_TALK);
        extras.putBoolean(EXTRAS_USE_OPUS, true);
        extras.putString(EXTRAS_CLIENT_NAME, "TAK PTT");
        extras.putBoolean(EXTRAS_AUTO_RECONNECT, true);
        extras.putInt(EXTRAS_AUTO_RECONNECT_DELAY, 5000);           // ms between tries when the server is down
        extras.putStringArrayList(EXTRAS_ACCESS_TOKENS, new ArrayList<>());
        extras.putInt(EXTRAS_INPUT_RATE, 48000);
        extras.putInt(EXTRAS_INPUT_QUALITY, 40000);                  // Opus bit rate
        extras.putInt(EXTRAS_FRAMES_PER_PACKET, 2);                   // 20 ms of audio per packet
        audioExtras(extras, calls != null && calls.active());
        extras.putFloat(EXTRAS_AMPLITUDE_BOOST, 1.0f);
        extras.putFloat(EXTRAS_DETECTION_THRESHOLD, 0.5f);
        extras.putBoolean(EXTRAS_HALF_DUPLEX, false);                 // full duplex
        extras.putBoolean(EXTRAS_ENABLE_PREPROCESSOR, true);
        extras.putBoolean(EXTRAS_FORCE_TCP, overTailscale(host));
        extras.putBoolean(EXTRAS_USE_TOR, false);
        if (trustStore != null) {
            extras.putString(EXTRAS_TRUST_STORE, trustStore);
            extras.putString(EXTRAS_TRUST_STORE_PASSWORD, trustStorePassword);
            extras.putString(EXTRAS_TRUST_STORE_FORMAT, "BKS");
        }
        if (certificate != null) {
            extras.putByteArray(EXTRAS_CERTIFICATE, certificate);
            extras.putString(EXTRAS_CERTIFICATE_PASSWORD, certificatePassword);
        }
        configureExtras(extras);
        connect();
    }

    /**
     * Tailscale: voice over TCP. On a server with Tailscale's userspace networking, voice sent back
     * over UDP never reached the phones, though it arrived on Wi-Fi.
     */
    static boolean overTailscale(String host) {
        if (host == null) return false;
        String h = host.toLowerCase(java.util.Locale.ROOT);
        if (h.endsWith(".ts.net") || h.endsWith(".ts.net.")) return true;
        String[] p = h.split("\\.");
        if (p.length != 4) return false;
        try {
            int a = Integer.parseInt(p[0]), b = Integer.parseInt(p[1]);
            return a == 100 && b >= 64 && b <= 127;         // Tailscale's 100.64.0.0/10
        } catch (NumberFormatException e) {
            return false;
        }
    }

    /**
     * During a Telecom call: call audio with the phone's echo cancelling and noise suppression.
     * Otherwise: normal media audio (call audio outside a call would come out of the earpiece).
     */
    private static void audioExtras(Bundle extras, boolean inCall) {
        extras.putInt(EXTRAS_AUDIO_SOURCE, inCall ? android.media.MediaRecorder.AudioSource.VOICE_COMMUNICATION
                : android.media.MediaRecorder.AudioSource.MIC);
        extras.putInt(EXTRAS_AUDIO_STREAM, inCall ? android.media.AudioManager.STREAM_VOICE_CALL
                : android.media.AudioManager.STREAM_MUSIC);
        extras.putString(EXTRAS_ECHO_CANCELLATION_METHOD, inCall ? "system" : "none");
    }

    /** Rebuild Humla's audio for the call starting or ending (it starts its streams again). */
    private void callAudio(boolean inCall) {
        if (!isConnectionEstablished())
            return;
        Bundle e = new Bundle();
        audioExtras(e, inCall);
        try {
            configureExtras(e);
            applyTarget();                          // the new audio starts with no voice target
            if (talking)
                setTalkingState(true);              // keep transmitting on the new microphone stream
            Log.i(TAG, inCall ? "call audio on" : "call audio off");
            callAudioReady = inCall;
            if (inCall && keyed && !talking)
                main.post(permit);                  // a press was waiting for call audio
        } catch (Exception ex) {
            Log.w(TAG, "call audio", ex);
        }
    }

    // ------------------------------------------------------------------ foreground service

    private void enterForeground() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm.getNotificationChannel(NOTIFY_CHANNEL) == null)
            nm.createNotificationChannel(new NotificationChannel(NOTIFY_CHANNEL, "TAK PTT voice",
                    NotificationManager.IMPORTANCE_LOW));
        int type = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK;
        if (micAllowed())
            type |= ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;
        try {
            startForegroundService(new Intent(this, VoiceService.class));
            if (Build.VERSION.SDK_INT >= 29)
                startForeground(NOTIFY_ID, notification(), type);
            else
                startForeground(NOTIFY_ID, notification());
            foreground = true;
            foregroundMic = micAllowed();
            Log.i(TAG, "foreground service on (microphone " + foregroundMic + ")");
        } catch (Exception e) {
            // Android allows this only while the user is in TAK PTT or ATAK; SetupActivity retries.
            Log.w(TAG, "foreground service refused: " + e);
            foreground = false;
        }
    }

    private void leaveForeground() {
        if (foreground) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            foreground = false;
        }
    }

    private Notification notification() {
        String text;
        if (talking) text = "Transmitting";
        else if (receiving != null) text = "Receiving: " + receiving;
        else if (connected) text = "On " + channelName() + " · " + serverName;
        else text = error != null ? error : "Connecting to " + serverName;
        return new Notification.Builder(this, NOTIFY_CHANNEL)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle("TAK PTT")
                .setContentText(text)
                .setOngoing(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .build();
    }

    // ------------------------------------------------------------------ push to talk

    private Runnable openMic, warn, cutoff;

    /** A PTT press: in hold mode start talking; in latch mode start or stop. */
    void pttDown() {
        if (keyed && Proto.MODE_LATCH.equals(mode())) {
            stopTalking();
            return;
        }
        if (keyed)
            return;
        if (!isSynchronized() && meshActive) {
            meshDown();
            return;
        }
        if (!isSynchronized() && manet.canTalk()) {
            manetDown();
            return;
        }
        if (!isSynchronized()) {
            if (tones()) Tones.play(connecting ? Tones.NO_CHANNEL : Tones.DENY);
            note = connecting ? "Still connecting" : "Not connected to voice";
            pushState();
            return;
        }
        if (!micAllowed()) {
            if (tones()) Tones.play(Tones.DENY);
            note = "Allow the microphone for TAK PTT";
            pushState();
            return;
        }
        if (onHold || (calls != null && calls.otherCallActive())) {
            if (tones()) Tones.play(Tones.DENY);
            note = "On a phone call";
            pushState();
            return;
        }
        if (!foregroundMic)
            enterForeground();                           // keeps the mic if ATAK goes to the background
        if (txIds().isEmpty() && manet.canTalk()) {
            manetDown();
            return;
        }
        if (txIds().isEmpty()) {
            if (tones()) Tones.play(Tones.NO_CHANNEL);
            note = "No TX channel picked";
            pushState();
            return;
        }
        applyTarget();
        keyed = true;
        main.removeCallbacks(endCall);
        if (calls != null && !callAudioReady) {
            // Cold start: the talk permit waits until Android has switched to call audio (and a
            // Bluetooth headset's audio is up), so no words are lost; at most 2 s.
            calls.begin();
            main.postDelayed(permit, 2000);
        } else {
            permit.run();
        }
        pushState();
    }

    private boolean callAudioReady, micPending;
    private double lat, lon;
    private boolean hasPosition;

    /** Tell the voice history recorder where we are as we key up (shown with the clip). */
    private void sendPosition() {
        if (!hasPosition || !isSynchronized()) return;
        try {
            for (IUser u : getRootChannel().getUsers())
                if (u.getUserId() == Proto.RECORDER_ID) {
                    sendUserTextMessage(u.getSession(), String.format(java.util.Locale.US,
                            "takptt-pos %.6f,%.6f", lat, lon));
                    return;
                }
        } catch (Exception e) {
            Log.w(TAG, "position: " + e);
        }
    }

    /** The talk permit: the tone, then the microphone opens once the tone is over. */
    private final Runnable permit = () -> {
        main.removeCallbacks(this.permit);
        if (!keyed || talking || micPending)
            return;
        micPending = true;
        openMic = () -> {
            micPending = false;
            if (!keyed) return;
            setTalkingState(true);
            talking = true;
            if (manet.canTalk()) {
                manet.startTalking(false);
                se.lublin.humla.protocol.AudioHandler.sPcmTap = manet::feed;
            }
            Log.i(TAG, "transmitting");
            sendPosition();
            int limit = timeoutSeconds();
            warn = () -> { if (talking && tones()) Tones.play(Tones.WARNING); };
            cutoff = () -> {
                if (!talking) return;
                stopTalking();
                if (tones()) Tones.play(Tones.CUTOFF);
                note = "Talk time limit (" + timeoutSeconds() + " s) reached";
                pushState();
            };
            main.postDelayed(warn, (limit - 5) * 1000L);
            main.postDelayed(cutoff, limit * 1000L);
            pushState();
        };
        int delay = 0;
        if (tones()) {
            Tones.play(Tones.PERMIT);
            delay = Tones.lengthMs(Tones.PERMIT) + 60;   // open the mic after the tone has played
        }
        main.postDelayed(openMic, delay);
    };

    /** A PTT release: ends talking in hold mode (latch mode ignores releases). */
    void pttUp() {
        if (Proto.MODE_LATCH.equals(mode()))
            return;
        stopTalking();
    }

    // ------------------------------------------------------------------ mesh failover

    private String currentName() {
        if (isSynchronized()) {
            try { return name(getSessionChannel()); } catch (Exception ignored) { }
        }
        return prefs.getString("last", "LOBBY");
    }

    private Set<String> meshHeard() {
        Set<String> s = picks("rx");
        s.add(currentName());
        return s;
    }

    private Set<String> meshTalk() {
        Set<String> s = picks("tx");
        if (txHere()) s.add(currentName()); else s.remove(currentName());
        return s;
    }

    private String callsign() {
        return lastConnect != null ? lastConnect.getString(Proto.CALLSIGN, "") : "";
    }

    /** A PTT press on the mesh (the server is out of reach). */
    private void meshDown() {
        if (keyed && Proto.MODE_LATCH.equals(mode())) { stopTalking(); return; }
        if (keyed) return;
        if (!micAllowed()) { if (tones()) Tones.play(Tones.DENY); return; }
        if (meshTalk().isEmpty()) { if (tones()) Tones.play(Tones.NO_CHANNEL); note = "No TX channel picked"; pushState(); return; }
        keyed = true;
        mesh.setChannels(meshHeard(), meshTalk(), callsign());
        int delay = 0;
        if (tones()) { Tones.play(Tones.PERMIT); delay = Tones.lengthMs(Tones.PERMIT) + 60; }
        micPending = true;
        openMic = () -> {
            micPending = false;
            if (!keyed) return;
            manet.startTalking(false);
            mesh.startTalking();
            talking = true;
            Log.i(TAG, "transmitting on the mesh " + meshTalk());
            warn = () -> { if (talking && tones()) Tones.play(Tones.WARNING); };
            cutoff = () -> {
                if (!talking) return;
                stopTalking();
                if (tones()) Tones.play(Tones.CUTOFF);
                note = "Talk time limit (" + timeoutSeconds() + " s) reached";
                pushState();
            };
            main.postDelayed(warn, (timeoutSeconds() - 5) * 1000L);
            main.postDelayed(cutoff, timeoutSeconds() * 1000L);
            pushState();
        };
        main.postDelayed(openMic, delay);
        pushState();
    }

    /** Every 2 s: use the mesh after the server has been out of reach for 6 s, and stop once it is back. */
    private final Runnable meshCheck = new Runnable() {
        @Override public void run() {
            boolean want = prefs.getBoolean(Proto.MESH, true) && lastConnect != null && !isSynchronized()
                    && downSince != 0 && System.currentTimeMillis() - downSince > 6000 && mesh.hasKeys();
            if (!want && !meshActive && !isSynchronized() && ++meshLogCount % 5 == 0)
                Log.d(TAG, "mesh not used: on=" + prefs.getBoolean(Proto.MESH, true) + " session=" + (lastConnect != null)
                        + " down=" + (downSince == 0 ? 0 : (System.currentTimeMillis() - downSince) / 1000) + "s keys=" + mesh.hasKeys());
            if (want && !meshActive) {
                mesh.loopback = prefs.getBoolean(Proto.MESH_LOOP, false);
                mesh.setChannels(meshHeard(), meshTalk(), callsign());
                mesh.start();
                meshActive = mesh.isRunning();
                if (meshActive) {
                    note = "Server out of reach: talking over the local network (mesh)";
                    pushState();
                }
            } else if (!want && meshActive) {
                if (talking) stopTalking();
                mesh.stop();
                meshActive = false;
                meshReceiving = null;
                pushState();
            } else if (meshActive) {
                mesh.setChannels(meshHeard(), meshTalk(), callsign());
            }
            main.postDelayed(this, 2000);
        }
    };

    /** The server's current keys replace the kept ones (old weeks' keys are dropped). */
    private void saveMeshKeys(Bundle b) {
        String json = b.getString(Proto.MESH_JSON);
        if (json == null) return;
        prefs.edit().putString("meshKeys", json).apply();
        loadMeshKeys();
    }

    private void loadMeshKeys() {
        String json = prefs.getString("meshKeys", null);
        if (json == null) return;
        try {
            org.json.JSONObject o = new org.json.JSONObject(json);
            org.json.JSONArray epochs = o.getJSONArray("epochs");
            List<MeshLink.Epoch> list = new ArrayList<>();
            for (int i = 0; i < epochs.length(); i++) {
                org.json.JSONObject e = epochs.getJSONObject(i);
                org.json.JSONObject k = e.getJSONObject("keys");
                java.util.Map<String, byte[]> map = new java.util.HashMap<>();
                for (java.util.Iterator<String> it = k.keys(); it.hasNext(); ) {
                    String name = it.next();
                    map.put(name, android.util.Base64.decode(k.getString(name), android.util.Base64.NO_WRAP));
                }
                list.add(new MeshLink.Epoch(e.getLong("from") * 1000, e.getLong("until") * 1000, map));
            }
            mesh.setKeys(o.getString("group"), o.getInt("port"), list);
            Log.i(TAG, "mesh keys loaded: " + list.size() + " weeks, ready " + mesh.hasKeys());
        } catch (Exception e) {
            Log.w(TAG, "mesh keys: " + e);          // an older format: wait for the next fetch
        }
    }

    /** A PTT press with only OpenMANET talk groups to talk on (no voice server, or no TX channel). */
    private void manetDown() {
        if (keyed && Proto.MODE_LATCH.equals(mode())) { stopTalking(); return; }
        if (keyed) return;
        if (!micAllowed()) {
            if (tones()) Tones.play(Tones.DENY);
            note = "Allow the microphone for TAK PTT";
            pushState();
            return;
        }
        if (!foregroundMic)
            enterForeground();                           // keeps the mic if ATAK goes to the background
        keyed = true;
        int delay = 0;
        if (tones()) { Tones.play(Tones.PERMIT); delay = Tones.lengthMs(Tones.PERMIT) + 60; }
        micPending = true;
        openMic = () -> {
            micPending = false;
            if (!keyed) return;
            manet.startTalking(true);
            talking = true;
            warn = () -> { if (talking && tones()) Tones.play(Tones.WARNING); };
            cutoff = () -> {
                if (!talking) return;
                stopTalking();
                if (tones()) Tones.play(Tones.CUTOFF);
                note = "Talk time limit (" + timeoutSeconds() + " s) reached";
                pushState();
            };
            main.postDelayed(warn, (timeoutSeconds() - 5) * 1000L);
            main.postDelayed(cutoff, timeoutSeconds() * 1000L);
            pushState();
        };
        main.postDelayed(openMic, delay);
        pushState();
    }

    private Set<Integer> manetPicks(String kind) {
        Set<Integer> out = new HashSet<>();
        for (String g : prefs.getStringSet(kind, new HashSet<>()))
            try { out.add(Integer.parseInt(g)); } catch (NumberFormatException ignored) { }
        return out;
    }

    /** Listen on the picked talk groups (and the ones talked on), and talk on the TX ones. */
    private void applyManet() {
        Set<Integer> tx = manetPicks("omTx"), rx = manetPicks("omRx");
        rx.addAll(tx);
        manet.setGroups(rx, tx);
    }

    private void stopTalking() {
        se.lublin.humla.protocol.AudioHandler.sPcmTap = null;
        manet.stopTalking();
        keyed = false;
        if (openMic != null) main.removeCallbacks(openMic);
        main.removeCallbacks(permit);
        micPending = false;
        if (warn != null) main.removeCallbacks(warn);
        if (cutoff != null) main.removeCallbacks(cutoff);
        if (talking) {
            if (meshActive) {
                mesh.stopTalking();
            } else {
                try {
                    setTalkingState(false);
                } catch (Exception ignored) {
                }
            }
            Log.i(TAG, "transmit off");
        }
        talking = false;
        warmThenEnd();
        pushState();
    }

    private final Runnable endCall = () -> {
        if (!keyed && !talking && receiving == null && calls != null)
            calls.end();
    };

    /** End the call after the warm window, if the channel stays quiet. */
    private void warmThenEnd() {
        main.removeCallbacks(endCall);
        main.postDelayed(endCall, WARM_MS);
    }

    /** A cellular call put TAK PTT on hold (or the hold ended): stop talking, mute listening. */
    private void holdForCall(boolean hold) {
        onHold = hold;
        if (hold) {
            boolean wasTalking = talking || keyed;
            stopTalking();
            if (wasTalking && tones())
                Tones.play(Tones.INTERRUPT);
            note = "Paused for a phone call";
        }
        try {
            HumlaSession().setSelfMuteDeafState(hold, hold);
        } catch (Exception ignored) {
        }
        Log.i(TAG, hold ? "on hold for a phone call" : "back from hold");
        main.removeCallbacks(checkHold);
        if (hold)
            main.postDelayed(checkHold, 2000);
        pushState();
    }

    /** Come back from hold once the phone call is over (Android may end TAK PTT's call instead of holding it). */
    private final Runnable checkHold = () -> {
        if (!onHold)
            return;
        if (calls != null && calls.otherCallActive())
            main.postDelayed(this.checkHold, 2000);
        else
            holdForCall(false);
    };

    /** From KeyService: volume keys (when turned on) work as PTT while connected. */
    boolean onHardwareKey(KeyEvent e) {
        String which = volumeKey();
        int code = Proto.KEY_DOWN.equals(which) ? KeyEvent.KEYCODE_VOLUME_DOWN
                : Proto.KEY_UP.equals(which) ? KeyEvent.KEYCODE_VOLUME_UP : -1;
        if (code == -1 || e.getKeyCode() != code || !isSynchronized())
            return false;
        if (e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() == 0)
            main.post(this::pttDown);
        else if (e.getAction() == KeyEvent.ACTION_UP)
            main.post(this::pttUp);
        return true;                                    // the volume does not change
    }

    // ------------------------------------------------------------------ RX / TX channels

    /*
     * Besides the channel you are in, you can listen to other channels (RX) and talk to several
     * channels at once (TX). The picks are kept by channel name, so they come back after a
     * reconnect. Talking on the channel you are in is the "tx.here" pick (on by default).
     */

    private static String name(IChannel ch) { return ch.getParent() == null ? "LOBBY" : ch.getName(); }

    /** Go back to the channel you were last in (setting, on by default). */
    private void rejoin() {
        if (!isSynchronized() || !prefs.getBoolean(Proto.REJOIN, true)) return;
        String last = prefs.getString("last", null);
        if (last == null || last.equals(name(getSessionChannel()))) return;
        List<Integer> all = new ArrayList<>();
        collectIds(getRootChannel(), all);
        for (int id : all) {
            IChannel ch = getChannel(id);
            if (ch != null && last.equals(name(ch))) {
                joinChannel(id);
                Log.i(TAG, "back to " + last);
                return;
            }
        }
    }

    private Set<String> picks(String kind) {
        return new HashSet<>(prefs.getStringSet(kind, new HashSet<>()));
    }

    private void setPick(String kind, int channelId, boolean on) {
        IChannel ch = isSynchronized() ? getChannel(channelId) : null;
        if (ch == null) return;
        boolean here = ch.getId() == getSessionChannel().getId();
        if (here && kind.equals("tx")) {
            prefs.edit().putBoolean("tx.here", on).apply();
            return;
        }
        Set<String> s = picks(kind);
        if (on) s.add(name(ch)); else s.remove(name(ch));
        prefs.edit().putStringSet(kind, s).apply();
    }

    private boolean txHere() { return prefs.getBoolean("tx.here", true); }

    /** Ids of the channels you hear: the one you are in, plus the listened ones. */
    private List<Integer> heard() {
        List<Integer> ids = new ArrayList<>();
        if (!isSynchronized()) return ids;
        int here = getSessionChannel().getId();
        ids.add(here);
        for (int id : listened)
            if (id != here) ids.add(id);
        return ids;
    }

    /** Ids of the channels a transmission goes to. */
    private List<Integer> txIds() {
        List<Integer> ids = new ArrayList<>();
        if (!isSynchronized()) return ids;
        int here = getSessionChannel().getId();
        if (txHere()) ids.add(here);
        Set<String> tx = picks("tx");
        List<Integer> all = new ArrayList<>();
        collectIds(getRootChannel(), all);
        for (int id : all) {
            IChannel ch = getChannel(id);
            if (id != here && ch != null && tx.contains(name(ch))) ids.add(id);
        }
        return ids;
    }

    private void collectIds(IChannel ch, List<Integer> out) {
        out.add(ch.getId());
        for (IChannel sub : ch.getSubchannels()) collectIds(sub, out);
    }

    private final Set<Integer> listened = new HashSet<>();

    /** Tell the server which channels to listen to (Mumble channel listeners). */
    private void applyListening() {
        if (!isSynchronized()) return;
        Set<String> rx = picks("rx");
        int here = getSessionChannel().getId();
        Set<Integer> want = new HashSet<>();
        List<Integer> all = new ArrayList<>();
        collectIds(getRootChannel(), all);
        for (int id : all) {
            IChannel ch = getChannel(id);
            if (id != here && ch != null && rx.contains(name(ch))) want.add(id);
        }
        Mumble.UserState.Builder b = Mumble.UserState.newBuilder().setSession(getSessionId());
        boolean change = false;
        for (int id : want) if (!listened.contains(id)) { b.addListeningChannelAdd(id); change = true; }
        for (int id : listened) if (!want.contains(id)) { b.addListeningChannelRemove(id); change = true; }
        if (change) {
            getConnection().sendTCPMessage(b.build(), HumlaTCPMessageType.UserState);
            Log.i(TAG, "listening to " + want);
        }
        listened.clear();
        listened.addAll(want);
    }

    private static final int TARGET = 5;          // our voice target for talking on several channels

    /** Talk on the current channel only (normal), or on several channels through a voice target. */
    private void applyTarget() {
        if (!isSynchronized()) return;
        List<Integer> ids = txIds();
        int here = getSessionChannel().getId();
        try {
            if (ids.size() == 1 && ids.get(0) == here) {
                setVoiceTargetId((byte) 0);
                return;
            }
            Mumble.VoiceTarget.Builder vt = Mumble.VoiceTarget.newBuilder().setId(TARGET);
            for (int id : ids)
                vt.addTargets(Mumble.VoiceTarget.Target.newBuilder().setChannelId(id).build());
            getConnection().sendTCPMessage(vt.build(), HumlaTCPMessageType.VoiceTarget);
            setVoiceTargetId((byte) TARGET);
            Log.i(TAG, "talking on " + ids);
        } catch (Exception e) {
            Log.w(TAG, "voice target: " + e);      // audio not up yet; set again on the next press
        }
    }

    // ------------------------------------------------------------------ state for ATAK

    private String channelName() {
        try {
            IChannel c = getSessionChannel();
            return c.getParent() == null ? "LOBBY" : c.getName();
        } catch (Exception e) {
            return "";
        }
    }

    private void updateReceiving() {
        String who = null;
        if (isSynchronized()) {
            try {
                int me = getSessionId();
                int here = getSessionChannel().getId();
                List<Integer> heard = heard(), all = new ArrayList<>();
                collectIds(getRootChannel(), all);
                // Every channel, not just the heard ones: someone talking to one of ours with a TX
                // pick stays in their own channel, and the server only sends audio we should hear.
                for (int id : all) {
                    IChannel ch = getChannel(id);
                    if (ch == null) continue;
                    for (IUser u : ch.getUsers())
                        if (u.getSession() != me && (u.getTalkState() == TalkState.TALKING
                                || u.getTalkState() == TalkState.SHOUTING || u.getTalkState() == TalkState.WHISPERING))
                            who = id != here && heard.contains(id) ? u.getName() + " · " + name(ch) : u.getName();
                }
            } catch (Exception ignored) {
            }
        }
        if (who != null && receiving == null && calls != null && !onHold) {
            main.removeCallbacks(endCall);
            if (calls.otherCallActive()) {
                holdForCall(true);                      // a phone call is on: do not play over it
                return;
            }
            calls.begin();
        } else if (who == null && receiving != null) {
            warmThenEnd();
        }
        receiving = who;
        pushState();
    }

    private void collect(IChannel ch, int currentId, ArrayList<Integer> ids, ArrayList<String> names,
                         ArrayList<Integer> users, ArrayList<Integer> active) {
        boolean talkingHere = false;
        int people = 0;
        for (IUser u : ch.getUsers()) {
            if (u.getUserId() == Proto.RECORDER_ID || (u.getUserId() > 99900 && u.getUserId() <= 99905))
                continue;                               // the history recorder and OpenMANET bridge are not people
            people++;
            // Humla shows audio from a listened channel or a voice target as WHISPERING.
            if (u.getTalkState() == TalkState.TALKING || u.getTalkState() == TalkState.SHOUTING
                    || u.getTalkState() == TalkState.WHISPERING)
                talkingHere = true;
        }
        ids.add(ch.getId());
        names.add(ch.getParent() == null ? "LOBBY" : ch.getName());
        users.add(people);
        active.add(talkingHere ? 1 : 0);
        for (IChannel sub : ch.getSubchannels())
            collect(sub, currentId, ids, names, users, active);
    }

    Bundle state() {
        Bundle s = new Bundle();
        s.putBoolean(Proto.CONNECTED, connected);
        s.putBoolean(Proto.CONNECTING, connecting);
        s.putBoolean(Proto.TALKING, talking || keyed);
        s.putString(Proto.RECEIVING, receiving);
        s.putString(Proto.ERROR, error);
        s.putString(Proto.SERVER_NAME, serverName);
        s.putBoolean(Proto.FROM_TAK, fromTak);
        s.putBoolean(Proto.MIC_ALLOWED, micAllowed());
        s.putBoolean(Proto.OVERLAY_ALLOWED, overlayAllowed());
        s.putBoolean(Proto.KEYS_ALLOWED, keysAllowed());
        s.putString(Proto.MODE, mode());
        s.putInt(Proto.TIMEOUT, timeoutSeconds());
        s.putBoolean(Proto.TONES, tones());
        s.putString(Proto.VOLUME_KEY, volumeKey());
        s.putBoolean(Proto.FLOATING, floatingWanted());
        s.putInt(Proto.FLOAT_SIZE, floatSize());
        s.putString(Proto.ROUTE, prefs.getString(Proto.ROUTE, Proto.ROUTE_AUTO));
        s.putBoolean(Proto.REJOIN, prefs.getBoolean(Proto.REJOIN, true));
        s.putBoolean("meshOn", prefs.getBoolean(Proto.MESH, true));
        s.putBoolean(Proto.MESH, meshActive);
        if (meshActive && meshReceiving != null)
            s.putString(Proto.RECEIVING, meshReceiving);
        if (manetReceiving > 0 && s.getString(Proto.RECEIVING) == null)
            s.putString(Proto.RECEIVING, "OpenMANET CH " + manetReceiving);
        ArrayList<Integer> omRx = new ArrayList<>(manetPicks("omRx")), omTx = new ArrayList<>(manetPicks("omTx"));
        for (int g : omTx) if (!omRx.contains(g)) omRx.add(g);
        ArrayList<Integer> omActive = new ArrayList<>();
        for (int g = 1; g <= ManetLink.GROUPS; g++) if (manet.active(g)) omActive.add(g);
        s.putIntegerArrayList(Proto.OM_RX, omRx);
        s.putIntegerArrayList(Proto.OM_TX, omTx);
        s.putIntegerArrayList(Proto.OM_ACTIVE, omActive);
        s.putBoolean(Proto.TRANSCRIPTS, transcripts());
        s.putStringArrayList(Proto.OM_LOG, omLog());
        if (note != null) {
            s.putString(Proto.CH_NOTE, note);
            note = null;
        }
        if (isSynchronized()) {
            try {
                IChannel cur = getSessionChannel();
                s.putString(Proto.CHANNEL, channelName());
                ArrayList<Integer> ids = new ArrayList<>(), users = new ArrayList<>(), active = new ArrayList<>();
                ArrayList<String> names = new ArrayList<>();
                collect(getRootChannel(), cur.getId(), ids, names, users, active);
                s.putIntegerArrayList(Proto.CH_IDS, ids);
                s.putStringArrayList(Proto.CH_NAMES, names);
                s.putIntegerArrayList(Proto.CH_USERS, users);
                s.putIntegerArrayList(Proto.CH_ACTIVE, active);
                s.putInt(Proto.CH_CURRENT, cur.getId());
                s.putIntegerArrayList(Proto.CH_RX, new ArrayList<>(heard()));
                s.putIntegerArrayList(Proto.CH_TX, new ArrayList<>(txIds()));
            } catch (Exception ignored) {
            }
        }
        return s;
    }

    private void pushState() {
        main.post(() -> {
            Bundle s = state();
            for (Iterator<Messenger> it = clients.iterator(); it.hasNext(); ) {
                Message m = Message.obtain(null, Proto.STATE);
                m.setData(new Bundle(s));
                try {
                    it.next().send(m);
                } catch (RemoteException e) {
                    it.remove();                        // that ATAK is gone
                }
            }
            boolean hearing = receiving != null || (meshActive && meshReceiving != null) || manetReceiving > 0;
            boolean ready = connected || meshActive || manet.canTalk();
            int color = talking || keyed ? Theme.RED : hearing ? Theme.AMBER : ready ? Theme.GREEN : Theme.MUTED;
            if (floatingWanted() && overlayAllowed() && (ready || connecting))
                floating.show(color, Proto.MODE_LATCH.equals(mode()), floatSize());
            else
                floating.hide();
            if (foreground)
                getSystemService(NotificationManager.class).notify(NOTIFY_ID, notification());
        });
    }

    // ------------------------------------------------------------------ Humla events

    private final HumlaObserver observer = new HumlaObserver() {
        @Override public void onConnected() {
            downSince = 0;
            connected = true;
            connecting = false;
            error = null;
            Log.i(TAG, "connected");
            listened.clear();                      // a new session: listen and target again
            main.postDelayed(() -> { rejoin(); applyListening(); applyTarget(); pushState(); }, 500);
            pushState();
        }

        @Override public void onDisconnected(HumlaException e) {
            if (downSince == 0) downSince = System.currentTimeMillis();
            if (pendingConnect != null) {
                Runnable go = pendingConnect;
                pendingConnect = null;              // run it once, even if this is reported twice
                main.post(go);                      // a reconnect was waiting for this
                return;
            }
            boolean wasTalking = talking || keyed;
            connected = false;
            connecting = false;
            stopTalking();
            if (wasTalking && tones())
                Tones.play(Tones.INTERRUPT);
            receiving = null;
            if (!untrusted) {
                String why = e != null ? String.valueOf(e.getMessage()) : "";
                if (why.contains("Wrong certificate or password"))
                    error = fromTak ? "Voice sign-in refused. Ask your PortableTAK admin to check your account."
                                    : "Voice sign-in refused by " + serverName;
                else if (why.contains("Invalid server password") && lastConnect != null) {
                    // PortableTAK lets nobody in while its sign-in check is starting or restarting:
                    // not a refusal, so try again (Humla only retries network errors).
                    error = "Voice server starting, trying again";
                    Bundle again = lastConnect;
                    main.postDelayed(() -> { if (lastConnect == again && !connected && !connecting) connectFrom(again); }, 10000);
                } else
                    error = e != null ? "Disconnected: " + why : "Disconnected";
            }
            Log.i(TAG, "disconnected: " + error);
            pushState();
        }

        @Override public void onTLSHandshakeFailed(X509Certificate[] chain) {
            if (fromTak) {
                // Only a voice server with a certificate from the TAK server's authority is trusted.
                untrusted = true;
                error = "Refused: the voice server's certificate is not from your TAK server";
                Log.w(TAG, error);
                disconnect();
                pushState();
                return;
            }
            // Test override without a TAK server: trust this server's certificate on first connect.
            try {
                TrustStore.add(new File(getFilesDir(), "takptt-trust.bks"), chain[0]);
                if (lastConnect != null)
                    main.postDelayed(() -> connectFrom(lastConnect), 500);
            } catch (Exception ex) {
                error = "Could not trust the server: " + ex.getMessage();
                pushState();
            }
        }

        @Override public void onChannelAdded(IChannel channel) {
            if (pendingChannel != null && pendingChannel.equals(channel.getName())) {
                pendingChannel = null;
                joinChannel(channel.getId());
                note = "Made " + channel.getName();
            }
            pushState();
        }

        @Override public void onChannelRemoved(IChannel channel) { pushState(); }

        @Override public void onChannelStateUpdated(IChannel channel) { pushState(); }

        @Override public void onUserJoinedChannel(IUser user, IChannel newChannel, IChannel oldChannel) {
            try {
                if (user.getSession() == getSessionId() && newChannel != null) {
                    prefs.edit().putString("last", name(newChannel)).apply();   // for rejoin
                    main.postDelayed(() -> { applyListening(); applyTarget(); pushState(); }, 200);
                }
            } catch (Exception ignored) {
            }
            updateReceiving();
        }

        @Override public void onUserConnected(IUser user) { pushState(); }

        @Override public void onUserRemoved(IUser user, String reason) { updateReceiving(); }

        @Override public void onUserTalkStateUpdated(IUser user) { updateReceiving(); }

        @Override public void onPermissionDenied(String reason) {
            if (pendingChannel != null) {
                if (!pendingTemporary) {
                    // This server lets users make only temporary channels (gone when empty).
                    pendingTemporary = true;
                    createChannel(getRootChannel().getId(), pendingChannel, "", 0, true);
                    note = "Making " + pendingChannel + " as a temporary channel";
                } else {
                    pendingChannel = null;
                    note = "This server does not let you make channels";
                }
            } else {
                note = "Not allowed: " + reason;
            }
            pushState();
        }
    };
}
