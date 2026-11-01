package com.portabletak.takptt.plugin;

import org.json.JSONArray;
import org.json.JSONObject;
import org.spongycastle.jce.provider.BouncyCastleProvider;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.security.KeyStore;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;

/**
 * The PortableTAK voice history (the recorder on the voice server), at
 * https://<TAK server>:8443/voice/. Signs in with the TAK certificate and trusts only the TAK
 * server's certificate authority. Clips are kept in the phone's cache to play again offline.
 */
final class VoiceHistory {
    static final class Clip {
        final long id;
        final String channel, sender;
        final double start, duration;
        final Double lat, lon;

        Clip(JSONObject o) {
            id = o.optLong("id");
            channel = o.optString("channel");
            sender = o.optString("sender");
            start = o.optDouble("start");
            duration = o.optDouble("duration");
            lat = o.isNull("lat") ? null : o.optDouble("lat");
            lon = o.isNull("lon") ? null : o.optDouble("lon");
        }
    }

    private final File cacheDir;
    /** Testing only: another port for the history API than the TAK server's 8443. */
    static volatile int port = 8443;

    VoiceHistory(File cacheDir) {
        this.cacheDir = new File(cacheDir, "takptt-clips");
        this.cacheDir.mkdirs();
    }

    private static SSLContext tls(TakVoice tak) throws Exception {
        KeyStore keys = KeyStore.getInstance("PKCS12", new BouncyCastleProvider());
        keys.load(new ByteArrayInputStream(tak.certificate), tak.certificatePassword.toCharArray());
        KeyManagerFactory kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        kmf.init(keys, tak.certificatePassword.toCharArray());
        KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
        trust.load(null, null);
        int n = 0;
        for (X509Certificate c : tak.trusted)
            trust.setCertificateEntry("ca" + (n++), c);
        TrustManagerFactory tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        tmf.init(trust);
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(kmf.getKeyManagers(), tmf.getTrustManagers(), null);
        return ctx;
    }

    private static HttpsURLConnection open(TakVoice tak, String path) throws Exception {
        HttpsURLConnection c = (HttpsURLConnection) new URL("https://" + tak.host + ":" + port + "/voice/" + path).openConnection();
        c.setSSLSocketFactory(tls(tak).getSocketFactory());
        // The server is pinned by its certificate authority (the TAK server's own); its
        // certificate may not carry the address the phone uses (Tailscale, a mesh IP...).
        c.setHostnameVerifier((host, session) -> true);
        c.setConnectTimeout(8000);
        c.setReadTimeout(15000);
        return c;
    }

    private static byte[] read(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        for (int n; (n = in.read(buf)) > 0; )
            out.write(buf, 0, n);
        return out.toByteArray();
    }

    /** Recent clips, newest first. Also saved, so the list shows without a connection. */
    List<Clip> fetch(TakVoice tak) throws Exception {
        HttpsURLConnection c = open(tak, "clips");
        int code = c.getResponseCode();
        if (code == 404)
            throw new Exception("this server has no voice history yet");
        if (code != 200)
            throw new Exception("voice history: HTTP " + code);
        String json = new String(read(c.getInputStream()), "UTF-8");
        try (OutputStream out = new FileOutputStream(new File(cacheDir, "list.json"))) {
            out.write(json.getBytes("UTF-8"));
        }
        return parse(json);
    }

    /** The list saved by the last fetch (for offline use). */
    List<Clip> cached() {
        try (InputStream in = new java.io.FileInputStream(new File(cacheDir, "list.json"))) {
            return parse(new String(read(in), "UTF-8"));
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }

    private static List<Clip> parse(String json) throws Exception {
        JSONArray a = new JSONObject(json).getJSONArray("clips");
        List<Clip> out = new ArrayList<>();
        for (int i = 0; i < a.length(); i++)
            out.add(new Clip(a.getJSONObject(i)));
        return out;
    }

    /** Mesh keys for every channel (to keep talking over the local network if the server is lost). */
    android.os.Bundle meshKeys(TakVoice tak) throws Exception {
        HttpsURLConnection c = open(tak, "keys");
        if (c.getResponseCode() != 200)
            throw new Exception("mesh keys: HTTP " + c.getResponseCode());
        String json = new String(read(c.getInputStream()), "UTF-8");
        new JSONObject(json).getJSONArray("epochs");          // must be this week's format
        android.os.Bundle b = new android.os.Bundle();
        b.putString(Proto.MESH_JSON, json);
        return b;
    }

    File file(Clip clip) {
        return new File(cacheDir, clip.id + ".opus");
    }

    /** The clip's audio file, downloaded once (tak may be null when offline). */
    File download(TakVoice tak, Clip clip) throws Exception {
        File f = file(clip);
        if (f.exists() && f.length() > 0)
            return f;
        if (tak == null)
            throw new Exception("not downloaded yet, and no connection");
        HttpsURLConnection c = open(tak, "clips/" + clip.id + ".opus");
        if (c.getResponseCode() != 200)
            throw new Exception("clip: HTTP " + c.getResponseCode());
        File tmp = new File(cacheDir, clip.id + ".part");
        try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(tmp)) {
            out.write(read(in));
        }
        if (!tmp.renameTo(f))
            throw new Exception("could not save the clip");
        return f;
    }

    /** Drop cached clips the server no longer lists (past its retention). */
    void trim(List<Clip> current) {
        java.util.Set<String> keep = new java.util.HashSet<>();
        for (Clip c : current)
            keep.add(c.id + ".opus");
        File[] files = cacheDir.listFiles();
        if (files != null)
            for (File f : files)
                if (f.getName().endsWith(".opus") && !keep.contains(f.getName()))
                    f.delete();
    }
}
