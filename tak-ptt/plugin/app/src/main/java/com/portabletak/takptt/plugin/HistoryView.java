package com.portabletak.takptt.plugin;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;
import android.text.format.DateUtils;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;

import com.atakmap.android.maps.MapEvent;
import com.atakmap.android.maps.MapEventDispatcher;
import com.atakmap.android.maps.MapGroup;
import com.atakmap.android.maps.MapItem;
import com.atakmap.android.maps.MapView;
import com.atakmap.android.maps.Marker;
import com.atakmap.android.util.ATAKUtilities;
import com.atakmap.coremap.maps.assets.Icon;
import com.atakmap.coremap.maps.coords.GeoPoint;

import java.io.File;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * The HISTORY tab: recent transmissions from the PortableTAK voice recorder, newest first, with
 * who sent them, on which channel, when and for how long; unheard ones have a dot. Tap to play;
 * the bar below has play/pause and a scrub bar, and PLAY UNHEARD plays the unheard ones in
 * order. Live traffic always wins: playback pauses while someone is talking or you transmit, and
 * goes on after. MAP shows clips with a location as markers on this phone's map only (not sent
 * to anyone); tap a marker to play it. TEXT shows what was said under each clip, worked out on
 * this phone (Transcriber), and above the clips the OpenMANET talk-group transmissions this phone
 * heard, as text (from the voice service).
 */
final class HistoryView extends LinearLayout {
    interface Source {
        /** The TAK connection to sign in with, or null if not available (offline). */
        TakVoice tak();
    }

    private static final String TAG = "TakPtt";
    private static final String GROUP = "TAK PTT voice";
    private static final String MARKER = "takptt-clip-";

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Source source;
    private final VoiceHistory history;
    private final SharedPreferences prefs;
    private final float d;
    private final LinearLayout list;
    private final TextView status, mapToggle, unheardButton, clearButton, textToggle;
    private final Transcriber transcriber;
    private final LinearLayout player;
    private final TextView playPause, nowPlaying, playTime;
    private final SeekBar scrub;
    private List<VoiceHistory.Clip> clips = new ArrayList<>();
    private MediaPlayer mp;
    private VoiceHistory.Clip playing;
    private final List<VoiceHistory.Clip> queue = new ArrayList<>();
    private boolean pausedForLive, visible, loading;
    /** Told when TEXT is switched (so the voice service transcribes talk groups too, or stops). */
    java.util.function.Consumer<Boolean> onTextToggle;
    private ArrayList<String> heardLog = new ArrayList<>();

    HistoryView(Context ctx, Source source) {
        super(ctx);
        setOrientation(VERTICAL);
        this.source = source;
        this.d = ctx.getResources().getDisplayMetrics().density;
        this.history = new VoiceHistory(ctx.getCacheDir());
        this.prefs = ctx.getSharedPreferences("takptt", Context.MODE_PRIVATE);
        Context app = MapView.getMapView().getContext();
        this.transcriber = Transcriber.available(app) ? new Transcriber(app, ctx.getCacheDir()) : null;

        LinearLayout bar = new LinearLayout(ctx);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(0, dp(6), 0, dp(6));
        status = label(ctx, "", 11, Theme.SUB);
        status.setPadding(0, dp(10), 0, 0);
        addView(status);
        bar.setGravity(Gravity.CENTER_VERTICAL | Gravity.RIGHT);
        unheardButton = chip(ctx, "UNHEARD");
        unheardButton.setOnClickListener(v -> playUnheard());
        bar.addView(unheardButton);
        mapToggle = chip(ctx, "MAP");
        mapToggle.setOnClickListener(v -> {
            prefs.edit().putBoolean("historyMap", !prefs.getBoolean("historyMap", false)).apply();
            showOnMap();
        });
        LayoutParams mp2 = new LayoutParams(-2, -2);
        mp2.leftMargin = dp(4);
        bar.addView(mapToggle, mp2);
        textToggle = chip(ctx, "TEXT");
        textToggle.setOnClickListener(v -> {
            if (transcriber == null) {
                toast("This phone has no offline speech recognition (Android 13 or later with on-device speech)");
                return;
            }
            prefs.edit().putBoolean("transcripts", !transcripts()).apply();
            if (onTextToggle != null) onTextToggle.accept(transcripts());
            render();
            transcribeAll();
        });
        LayoutParams tp = new LayoutParams(-2, -2);
        tp.leftMargin = dp(4);
        bar.addView(textToggle, tp);
        clearButton = chip(ctx, "CLEAR");
        clearButton.setOnClickListener(v -> askClear());
        LayoutParams cp = new LayoutParams(-2, -2);
        cp.leftMargin = dp(4);
        bar.addView(clearButton, cp);
        addView(bar);

        player = new LinearLayout(ctx);
        player.setOrientation(VERTICAL);
        player.setPadding(dp(10), dp(8), dp(10), dp(8));
        player.setBackground(Theme.box(0x1432E07A, Theme.GREEN, d));
        LinearLayout prow = new LinearLayout(ctx);
        prow.setGravity(Gravity.CENTER_VERTICAL);
        playPause = chip(ctx, "❚❚");
        playPause.setOnClickListener(v -> togglePause());
        prow.addView(playPause);
        nowPlaying = label(ctx, "", 12, Theme.TEXT);
        nowPlaying.setPadding(dp(10), 0, 0, 0);
        prow.addView(nowPlaying, new LayoutParams(0, -2, 1));
        playTime = label(ctx, "", 11, Theme.SUB);
        prow.addView(playTime);
        player.addView(prow);
        scrub = new SeekBar(ctx);
        scrub.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar s, int p, boolean user) {
                if (user && mp != null) mp.seekTo(p);
            }
            @Override public void onStartTrackingTouch(SeekBar s) { }
            @Override public void onStopTrackingTouch(SeekBar s) { }
        });
        player.addView(scrub);
        player.setVisibility(GONE);
        LayoutParams pp = new LayoutParams(-1, -2);
        pp.bottomMargin = dp(8);
        addView(player, pp);

        list = new LinearLayout(ctx);
        list.setOrientation(VERTICAL);
        addView(list);

        MapView.getMapView().getMapEventDispatcher().addMapEventListener(MapEvent.ITEM_CLICK, markerClick);
        clips = shown(history.cached());
        render();
        transcribeAll();
    }

    private boolean transcripts() { return transcriber != null && prefs.getBoolean("transcripts", true); }

    /** Turn the newest clips into text, one at a time, downloading them first if needed. */
    private void transcribeAll() {
        if (!transcripts()) return;
        int n = 0;
        for (VoiceHistory.Clip c : clips) {
            if (n++ >= 25) break;
            String key = String.valueOf(c.id);
            if (transcriber.get(key) != null || transcriber.pending(key)) continue;
            new Thread(() -> {
                try {
                    File f = history.download(source.tak(), c);
                    main.post(() -> {
                        transcriber.add(key, f, (k, text) -> render());
                        render();
                    });
                } catch (Exception e) {
                    Log.w(TAG, "transcript download: " + e);
                }
            }, "takptt-clip-text").start();
        }
    }

    /** Clips after the last CLEAR on this phone (the server keeps them for everyone else). */
    private List<VoiceHistory.Clip> shown(List<VoiceHistory.Clip> all) {
        double cleared = Double.longBitsToDouble(prefs.getLong("historyCleared", 0));
        List<VoiceHistory.Clip> out = new ArrayList<>();
        for (VoiceHistory.Clip c : all)
            if (c.start > cleared) out.add(c);
        return out;
    }

    private void askClear() {
        if (clips.isEmpty()) return;
        new android.app.AlertDialog.Builder(MapView.getMapView().getContext())
                .setTitle("Clear history?")
                .setMessage("Removes the listed transmissions and saved recordings from this phone. "
                        + "The voice server still keeps them for everyone else.")
                .setNegativeButton("Keep", null)
                .setPositiveButton("Clear", (dlg, w) -> clear())
                .show();
    }

    private void clear() {
        double newest = 0;
        for (VoiceHistory.Clip c : clips) newest = Math.max(newest, c.start);
        prefs.edit().putLong("historyCleared", Double.doubleToLongBits(newest)).apply();
        queue.clear();
        stopPlayer();
        playing = null;
        player.setVisibility(GONE);
        history.trim(new ArrayList<>());
        clips = new ArrayList<>();
        status.setText("LAST 7 DAYS · 0 CLIPS");
        render();
    }

    private int dp(float v) { return Math.round(v * d); }

    private TextView label(Context ctx, String text, int size, int color) {
        TextView t = new TextView(ctx);
        t.setText(text);
        t.setTextSize(size);
        t.setTextColor(color);
        return t;
    }

    private TextView chip(Context ctx, String text) {
        TextView t = label(ctx, text, 11, Theme.GREEN);
        t.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        t.setLetterSpacing(0.05f);
        t.setSingleLine(true);
        t.setPadding(dp(7), dp(6), dp(7), dp(6));
        t.setBackground(Theme.box(Theme.PANEL, Theme.LINE, d));
        return t;
    }

    private void toast(String s) { main.post(() -> Toast.makeText(getContext(), s, Toast.LENGTH_SHORT).show()); }

    private Set<String> heard() { return new HashSet<>(prefs.getStringSet("heard", new HashSet<>())); }

    private void markHeard(VoiceHistory.Clip c) {
        Set<String> h = heard();
        h.add(String.valueOf(c.id));
        prefs.edit().putStringSet("heard", h).apply();
    }

    // ------------------------------------------------------------------ loading

    /** The tab is shown (refresh now and every 15 s) or hidden. */
    void setShown(boolean shown) {
        visible = shown;
        if (shown) refresh();
    }

    private final Runnable autoRefresh = this::refresh;

    void refresh() {
        main.removeCallbacks(autoRefresh);
        if (visible) main.postDelayed(autoRefresh, 15000);
        if (loading) return;
        loading = true;
        new Thread(() -> {
            String err = null;
            List<VoiceHistory.Clip> got = null;
            try {
                TakVoice tak = source.tak();
                if (tak == null) throw new Exception("offline: showing saved history");
                got = shown(history.fetch(tak));
                history.trim(got);
            } catch (Exception e) {
                err = e.getMessage();
                Log.w(TAG, "history: " + e);
            }
            List<VoiceHistory.Clip> g = got;
            String fe = err;
            main.post(() -> {
                loading = false;
                if (g != null) clips = g;
                status.setText(fe != null ? "SERVER OUT OF REACH · SAVED " + clips.size()
                        : "LAST 7 DAYS · " + clips.size() + " CLIPS");
                render();
                transcribeAll();
            });
        }, "takptt-history").start();
    }

    /** Talk-group transmissions this phone heard and turned into text ("millis|group|text"). */
    void setTalkGroupLog(ArrayList<String> log) {
        if (log == null) log = new ArrayList<>();
        if (log.equals(heardLog)) return;
        heardLog = log;
        render();
    }

    private void renderHeard(Context ctx) {
        if (heardLog.isEmpty() || !transcripts()) return;
        TextView head = label(ctx, "OPENMANET TALK GROUPS \u00b7 HEARD ON THIS PHONE", 11, Theme.CYAN);
        head.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        head.setPadding(0, dp(2), 0, dp(6));
        list.addView(head);
        int shown = 0;
        for (String entry : heardLog) {
            String[] e = entry.split("\\|", 3);
            if (e.length < 3) continue;
            if (shown++ >= 8) break;
            LinearLayout row = new LinearLayout(ctx);
            row.setOrientation(VERTICAL);
            row.setPadding(dp(10), dp(6), dp(10), dp(6));
            row.setBackground(Theme.box(Theme.PANEL, 0x4700E5FF, d));
            row.addView(label(ctx, "CH " + e[1] + " \u00b7 " + ago(Long.parseLong(e[0]) / 1000.0), 11, Theme.SUB));
            TextView t = label(ctx, "\u201c" + e[2] + "\u201d", 13, Theme.TEXT);
            t.setPadding(0, dp(2), 0, 0);
            row.addView(t);
            LayoutParams rp = new LayoutParams(-1, -2);
            rp.bottomMargin = dp(6);
            list.addView(row, rp);
        }
        TextView head2 = label(ctx, "VOICE SERVER", 11, Theme.SUB);
        head2.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        head2.setPadding(0, dp(6), 0, dp(6));
        list.addView(head2);
    }

    // ------------------------------------------------------------------ list

    private void render() {
        list.removeAllViews();
        Set<String> heard = heard();
        int unheard = 0;
        Context ctx = getContext();
        renderHeard(ctx);
        if (clips.isEmpty()) {
            TextView empty = label(ctx, "No recorded transmissions yet", 14, Theme.TEXT);
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(0, dp(16), 0, dp(4));
            list.addView(empty);
            TextView sub = label(ctx, "Transmissions on the voice server are kept for 7 days and listed here "
                    + "with who sent them, when and from where.", 12, Theme.SUB);
            sub.setGravity(Gravity.CENTER);
            list.addView(sub);
        }
        for (VoiceHistory.Clip c : clips) {
            boolean isHeard = heard.contains(String.valueOf(c.id));
            if (!isHeard) unheard++;
            boolean now = playing != null && playing.id == c.id;
            LinearLayout row = new LinearLayout(ctx);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(10), dp(7), dp(10), dp(7));
            row.setBackground(Theme.box(now ? 0x1432E07A : Theme.PANEL, now ? Theme.GREEN : Theme.LINE, d));
            TextView dot = label(ctx, "●", 10, isHeard ? 0x00000000 : Theme.GREEN);
            dot.setPadding(0, 0, dp(8), 0);
            row.addView(dot);
            LinearLayout text = new LinearLayout(ctx);
            text.setOrientation(VERTICAL);
            TextView who = label(ctx, c.sender, 14, Theme.TEXT);
            who.setTypeface(Typeface.DEFAULT_BOLD);
            text.addView(who);
            TextView sub = label(ctx, c.channel + " · " + ago(c.start) + (c.lat != null ? " · 📍" : ""), 11, Theme.SUB);
            text.addView(sub);
            if (transcripts()) {
                String said = transcriber.get(String.valueOf(c.id));
                String line = said != null ? (said.isEmpty() ? null : "\u201c" + said + "\u201d")
                        : transcriber.pending(String.valueOf(c.id)) ? "transcribing\u2026" : null;
                if (line != null) {
                    TextView t = label(ctx, line, 13, said != null ? Theme.TEXT : Theme.MUTED);
                    t.setPadding(0, dp(3), 0, 0);
                    if (said == null) t.setTypeface(Typeface.MONOSPACE);
                    text.addView(t);
                }
            }
            row.addView(text, new LayoutParams(0, -2, 1));
            TextView len = label(ctx, String.format(Locale.US, "%.1f s", c.duration), 12, Theme.SUB);
            len.setTypeface(Typeface.MONOSPACE);
            row.addView(len);
            row.setOnClickListener(v -> { queue.clear(); play(c); });
            LayoutParams rp = new LayoutParams(-1, -2);
            rp.bottomMargin = dp(6);
            list.addView(row, rp);
        }
        unheardButton.setAlpha(unheard > 0 ? 1f : 0.4f);
        clearButton.setAlpha(clips.isEmpty() ? 0.4f : 1f);
        boolean map = prefs.getBoolean("historyMap", false);
        mapToggle.setText(map ? "MAP ON" : "MAP");
        mapToggle.setTextColor(map ? Theme.GREEN : Theme.SUB);
        textToggle.setText(transcripts() ? "TEXT ON" : "TEXT");
        textToggle.setTextColor(transcripts() ? Theme.GREEN : Theme.SUB);
        showOnMap();
    }

    private static String ago(double start) {
        return DateUtils.getRelativeTimeSpanString((long) (start * 1000), System.currentTimeMillis(),
                DateUtils.MINUTE_IN_MILLIS, DateUtils.FORMAT_ABBREV_RELATIVE).toString();
    }

    // ------------------------------------------------------------------ playback

    private void playUnheard() {
        Set<String> heard = heard();
        queue.clear();
        for (int i = clips.size() - 1; i >= 0; i--)          // oldest first
            if (!heard.contains(String.valueOf(clips.get(i).id)))
                queue.add(clips.get(i));
        if (queue.isEmpty()) { toast("Nothing unheard"); return; }
        play(queue.remove(0));
    }

    void play(VoiceHistory.Clip c) {
        stopPlayer();
        playing = c;
        nowPlaying.setText(c.sender + " · " + c.channel);
        player.setVisibility(VISIBLE);
        render();
        new Thread(() -> {
            try {
                File f = history.download(source.tak(), c);
                main.post(() -> start(c, f));
            } catch (Exception e) {
                toast("Could not play: " + e.getMessage());
                main.post(() -> { playing = null; player.setVisibility(GONE); render(); });
            }
        }, "takptt-clip").start();
    }

    private void start(VoiceHistory.Clip c, File f) {
        if (playing != c) return;
        try {
            mp = new MediaPlayer();
            mp.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());
            mp.setDataSource(f.getAbsolutePath());
            mp.setOnCompletionListener(p -> {
                markHeard(c);
                stopPlayer();
                if (!queue.isEmpty()) play(queue.remove(0));
                else { playing = null; player.setVisibility(GONE); render(); }
            });
            mp.prepare();
            scrub.setMax(mp.getDuration());
            if (!pausedForLive) mp.start();
            playPause.setText(pausedForLive ? "▶" : "❚❚");
            tick.run();
        } catch (Exception e) {
            toast("Could not play: " + e.getMessage());
            stopPlayer();
        }
    }

    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (mp == null) return;
            try {
                scrub.setProgress(mp.getCurrentPosition());
                playTime.setText(String.format(Locale.US, "%.1f / %.1f s",
                        mp.getCurrentPosition() / 1000f, mp.getDuration() / 1000f));
            } catch (Exception ignored) {
            }
            main.postDelayed(this, 200);
        }
    };

    private void togglePause() {
        if (mp == null) return;
        if (mp.isPlaying()) { mp.pause(); playPause.setText("▶"); }
        else if (!pausedForLive) { mp.start(); playPause.setText("❚❚"); }
    }

    private void stopPlayer() {
        main.removeCallbacks(tick);
        if (mp != null) {
            try { mp.release(); } catch (Exception ignored) { }
            mp = null;
        }
    }

    /** Live traffic (someone talking, or you transmitting): pause playback, go on after. */
    void setLive(boolean live) {
        if (live == pausedForLive) return;
        pausedForLive = live;
        if (mp == null) return;
        if (live && mp.isPlaying()) { mp.pause(); playPause.setText("▶"); }
        else if (!live) { mp.start(); playPause.setText("❚❚"); }
    }

    // ------------------------------------------------------------------ map markers

    private final MapEventDispatcher.MapEventDispatchListener markerClick = event -> {
        MapItem item = event.getItem();
        if (item == null || item.getUID() == null || !item.getUID().startsWith(MARKER)) return;
        long id = Long.parseLong(item.getUID().substring(MARKER.length()));
        for (VoiceHistory.Clip c : clips)
            if (c.id == id) { queue.clear(); play(c); return; }
    };

    private MapGroup group() {
        MapGroup root = MapView.getMapView().getRootGroup();
        MapGroup g = root.findMapGroup(GROUP);
        return g != null ? g : root.addGroup(GROUP);
    }

    /** Markers where each clip was sent from (this phone's map only; nothing is sent out). */
    private void showOnMap() {
        try {
            MapGroup g = group();
            g.clearItems();
            if (!prefs.getBoolean("historyMap", false)) return;
            for (VoiceHistory.Clip c : clips) {
                if (c.lat == null || c.lon == null || !GeoPoint.isValid(c.lat, c.lon)) continue;
                Marker m = new Marker(MARKER + c.id);
                m.setPoint(new GeoPoint(c.lat, c.lon));
                m.setType("b-m-p-s-m");
                m.setMetaBoolean("adapt_marker_icon", false);                     // keep this icon, not the type's tiny dot
                m.setIcon(new Icon.Builder().setImageUri(0, ATAKUtilities.getResourceUri(getContext(), R.drawable.takptt_marker))
                        .setSize(48, 48).setAnchor(24, 24).build());
                m.setTitle(c.sender + " · " + c.channel + " · " + ago(c.start));
                m.setMetaString("callsign", c.sender + " " + ago(c.start));
                m.setMetaBoolean("editable", false);
                m.setMetaBoolean("movable", false);
                m.setMetaBoolean("addToObjList", false);
                m.setMetaBoolean("archive", false);
                m.setShowLabel(true);
                g.addItem(m);
            }
        } catch (Exception e) {
            Log.w(TAG, "history markers: " + e);
        }
    }

    void dispose() {
        stopPlayer();
        if (transcriber != null) transcriber.dispose();
        main.removeCallbacks(autoRefresh);
        try {
            MapView.getMapView().getMapEventDispatcher().removeMapEventListener(MapEvent.ITEM_CLICK, markerClick);
            group().clearItems();
        } catch (Exception ignored) {
        }
    }
}
