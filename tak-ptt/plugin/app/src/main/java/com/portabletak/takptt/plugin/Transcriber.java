package com.portabletak.takptt.plugin;

import android.content.Context;
import android.content.Intent;
import android.media.AudioFormat;
import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Locale;

/**
 * Speech to text for recorded transmissions, on the phone: Android's on-device recognizer (no
 * internet, nothing leaves the phone) fed the clip's audio instead of the microphone. One clip at a
 * time; results are kept in a small file so each clip is only done once.
 */
final class Transcriber {
    interface Done {
        /** text is "" when nothing was recognized, null when it failed. */
        void onTranscript(String key, String text);
    }

    private static final String TAG = "TakPtt";
    private static final int RATE = 16000;

    private final Context ctx;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final File store;
    private final JSONObject saved;
    private final ArrayDeque<Object[]> queue = new ArrayDeque<>();
    private final java.util.Set<String> pending = new java.util.HashSet<>();
    /** Clips that gave no words this session (not saved: a later try may do better). */
    private final java.util.Set<String> empty = new java.util.HashSet<>();
    private SpeechRecognizer rec;
    private boolean busy;

    Transcriber(Context ctx, File dir) {
        this.ctx = ctx;
        this.store = dir == null ? null : new File(dir, "takptt-transcripts-1.json");   // null: keep nothing
        JSONObject j;
        try (FileInputStream in = new FileInputStream(store)) {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            for (int n; (n = in.read(buf)) > 0; ) b.write(buf, 0, n);
            j = new JSONObject(b.toString("UTF-8"));
        } catch (Exception e) {
            j = new JSONObject();
        }
        saved = j;
    }

    /** Whether this phone can turn speech into text offline (Android 13 or later). */
    static boolean available(Context ctx) {
        return Build.VERSION.SDK_INT >= 33 && SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx);
    }

    /** The saved transcript, or null if not done yet. */
    String get(String key) {
        return saved.has(key) ? saved.optString(key) : empty.contains(key) ? "" : null;
    }

    boolean pending(String key) { return pending.contains(key); }

    /** Queue a clip file (Ogg Opus as recorded by the voice server). Main thread. */
    void add(String key, File clip, Done done) {
        if (get(key) != null || pending.contains(key)) return;
        pending.add(key);
        queue.add(new Object[]{key, clip, null, done});
        next();
    }

    /** Queue raw 48 kHz mono audio (a live transmission heard on a talk group). Main thread. */
    void add(String key, short[] pcm48k, int n, Done done) {
        if (get(key) != null || pending.contains(key)) return;
        pending.add(key);
        queue.add(new Object[]{key, null, downsample(pcm48k, n), done});
        next();
    }

    private void next() {
        if (busy || queue.isEmpty()) return;
        busy = true;
        Object[] job = queue.poll();
        String key = (String) job[0];
        Done done = (Done) job[3];
        new Thread(() -> {
            short[] pcm;
            try {
                pcm = job[2] != null ? (short[]) job[2] : decode((File) job[1]);
            } catch (Exception e) {
                Log.w(TAG, "transcribe decode: " + e);
                main.post(() -> finish(key, null, done));
                return;
            }
            main.post(() -> recognize(key, pcm, done));
        }, "takptt-transcribe").start();
    }

    private void finish(String key, String text, Done done) {
        pending.remove(key);
        Log.i(TAG, "transcript " + key + ": " + (text == null ? "failed" : text.isEmpty() ? "no words" : text.length() + " chars"));
        if (text != null && text.isEmpty()) empty.add(key);
        else if (text != null && store != null) {
            try {
                saved.put(key, text);
                try (OutputStream out = new FileOutputStream(store)) {
                    out.write(saved.toString().getBytes("UTF-8"));
                }
            } catch (Exception e) {
                Log.w(TAG, "transcripts: " + e);
            }
        }
        done.onTranscript(key, text);
        // Let the recognizer wind down before the next clip.
        main.postDelayed(() -> { busy = false; next(); }, 300);
    }

    // ------------------------------------------------------------------ recognizing

    private void recognize(String key, short[] pcm, Done done) {
        if (pcm.length < RATE / 4) { finish(key, "", done); return; }      // under 0.25 s
        ParcelFileDescriptor[] pipe;
        try {
            // A fresh recognizer per clip: one reused straight after a session ends answers the
            // next with "no match" at once.
            if (rec != null) rec.destroy();
            rec = SpeechRecognizer.createOnDeviceSpeechRecognizer(ctx);
            pipe = ParcelFileDescriptor.createPipe();
        } catch (Exception e) {
            Log.w(TAG, "transcribe: " + e);
            finish(key, null, done);
            return;
        }
        final boolean[] over = {false};
        Runnable timeout = () -> {
            if (over[0]) return;
            over[0] = true;
            try { rec.cancel(); } catch (Exception ignored) { }
            finish(key, null, done);
        };
        main.postDelayed(timeout, 15000 + pcm.length * 1000L / RATE);
        StringBuilder text = new StringBuilder();
        rec.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle b) { }
            @Override public void onBeginningOfSpeech() { }
            @Override public void onRmsChanged(float v) { }
            @Override public void onBufferReceived(byte[] b) { }
            @Override public void onEndOfSpeech() { }
            @Override public void onPartialResults(Bundle b) { }
            @Override public void onEvent(int t, Bundle b) { }

            @Override public void onSegmentResults(Bundle b) {
                ArrayList<String> r = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (r != null && !r.isEmpty() && !r.get(0).trim().isEmpty())
                    text.append(text.length() > 0 ? " " : "").append(r.get(0).trim());
            }

            @Override public void onEndOfSegmentedSession() { end(); }

            @Override public void onResults(Bundle b) {
                ArrayList<String> r = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (r != null && !r.isEmpty() && !r.get(0).trim().isEmpty())
                    text.append(text.length() > 0 ? " " : "").append(r.get(0).trim());
                end();
            }

            @Override public void onError(int error) {
                // No match / speech timeout: nothing was said that it could make out. Anything
                // else (busy, disconnected: another app or process using the recognizer) is a
                // failure, tried again on the next refresh.
                if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) end();
                else {
                    Log.w(TAG, "transcribe error " + error);
                    if (over[0]) return;
                    over[0] = true;
                    main.removeCallbacks(timeout);
                    finish(key, null, done);
                }
            }

            private void end() {
                if (over[0]) return;
                over[0] = true;
                main.removeCallbacks(timeout);
                finish(key, sentence(text.toString()), done);
            }
        });
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag());
        i.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true);
        i.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, pipe[0]);
        i.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, AudioFormat.ENCODING_PCM_16BIT);
        i.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1);
        i.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, RATE);
        i.putExtra(RecognizerIntent.EXTRA_SEGMENTED_SESSION, RecognizerIntent.EXTRA_AUDIO_SOURCE);
        rec.startListening(i);
        ParcelFileDescriptor read = pipe[0], write = pipe[1];
        new Thread(() -> {
            // The clip, then a second of silence so the recognizer knows it ended.
            ByteBuffer b = ByteBuffer.allocate((pcm.length + RATE) * 2).order(ByteOrder.LITTLE_ENDIAN);
            for (short s : pcm) b.putShort(s);
            try (OutputStream out = new ParcelFileDescriptor.AutoCloseOutputStream(write)) {
                out.write(b.array());
            } catch (Exception e) {
                Log.w(TAG, "transcribe feed: " + e);
            }
            try { read.close(); } catch (Exception ignored) { }
        }, "takptt-transcribe-feed").start();
    }

    private static String sentence(String s) {
        s = s.trim();
        if (s.isEmpty()) return s;
        return Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }

    // ------------------------------------------------------------------ audio

    /** An Ogg Opus clip to 16 kHz mono. */
    private static short[] decode(File f) throws Exception {
        MediaExtractor ex = new MediaExtractor();
        ex.setDataSource(f.getAbsolutePath());
        ex.selectTrack(0);
        MediaFormat fmt = ex.getTrackFormat(0);
        int rate = fmt.getInteger(MediaFormat.KEY_SAMPLE_RATE);
        int channels = fmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
        MediaCodec codec = MediaCodec.createDecoderByType(fmt.getString(MediaFormat.KEY_MIME));
        codec.configure(fmt, null, null, 0);
        codec.start();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        boolean inDone = false, outDone = false;
        try {
            while (!outDone) {
                if (!inDone) {
                    int ix = codec.dequeueInputBuffer(10000);
                    if (ix >= 0) {
                        int n = ex.readSampleData(codec.getInputBuffer(ix), 0);
                        if (n < 0) {
                            codec.queueInputBuffer(ix, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inDone = true;
                        } else {
                            codec.queueInputBuffer(ix, 0, n, ex.getSampleTime(), 0);
                            ex.advance();
                        }
                    }
                }
                int ox = codec.dequeueOutputBuffer(info, 10000);
                if (ox >= 0) {
                    ByteBuffer ob = codec.getOutputBuffer(ox);
                    byte[] chunk = new byte[info.size];
                    ob.position(info.offset);
                    ob.get(chunk);
                    out.write(chunk);
                    codec.releaseOutputBuffer(ox, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) outDone = true;
                } else if (ox == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat of = codec.getOutputFormat();
                    rate = of.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                    channels = of.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                }
            }
        } finally {
            codec.stop();
            codec.release();
            ex.release();
        }
        ShortBuffer16 pcm = new ShortBuffer16(out.toByteArray());
        short[] mono = new short[pcm.length / channels];
        for (int i = 0; i < mono.length; i++) {
            int sum = 0;
            for (int c = 0; c < channels; c++) sum += pcm.get(i * channels + c);
            mono[i] = (short) (sum / channels);
        }
        return resample(mono, mono.length, rate);
    }

    private static final class ShortBuffer16 {
        final ByteBuffer b;
        final int length;

        ShortBuffer16(byte[] bytes) {
            b = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
            length = bytes.length / 2;
        }

        short get(int i) { return b.getShort(i * 2); }
    }

    private static short[] downsample(short[] pcm48k, int n) { return resample(pcm48k, n, 48000); }

    /** To 16 kHz by averaging (48 kHz: groups of three), enough for speech. */
    private static short[] resample(short[] in, int n, int rate) {
        if (rate == RATE) return java.util.Arrays.copyOf(in, n);
        double step = rate / (double) RATE;
        int len = (int) (n / step);
        short[] out = new short[len];
        for (int i = 0; i < len; i++) {
            int a = (int) (i * step), z = Math.min(n, (int) ((i + 1) * step));
            int sum = 0;
            for (int k = a; k < z; k++) sum += in[k];
            out[i] = (short) (z > a ? sum / (z - a) : in[Math.min(a, n - 1)]);
        }
        return out;
    }

    void dispose() {
        queue.clear();
        if (rec != null) {
            try { rec.destroy(); } catch (Exception ignored) { }
            rec = null;
        }
    }
}
