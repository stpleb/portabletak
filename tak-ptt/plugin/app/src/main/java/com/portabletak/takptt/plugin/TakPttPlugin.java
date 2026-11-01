package com.portabletak.takptt.plugin;

import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;

import com.atak.plugins.impl.PluginContextProvider;
import com.atakmap.android.maps.MapView;

import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;

import gov.tak.api.plugin.IPlugin;
import gov.tak.api.plugin.IServiceController;
import gov.tak.api.ui.IHostUIService;
import gov.tak.api.ui.Pane;
import gov.tak.api.ui.PaneBuilder;
import gov.tak.api.ui.ToolbarItem;
import gov.tak.api.ui.ToolbarItemAdapter;
import gov.tak.platform.marshal.MarshalManager;

/**
 * TAK PTT: push-to-talk voice over Mumble for ATAK. This is the plugin's screen inside ATAK (same
 * layout as the ManetNode plugin's PTT tab, in green): the PTT ring, the server's channels as
 * tiles (tap to join, "+ NEW" to make one) and a status line. The voice itself runs in the
 * plugin's own process (VoiceService) so it keeps working with ATAK in the background.
 *
 * Nothing to set up: the voice server is the TAK server ATAK is connected to (port 64738), and the
 * TAK certificate ATAK enrolled is the voice identity (TakVoice). Tap the connection pill to see
 * where voice is signed in; long-press it to point voice at another server for testing. SET holds
 * the PTT options (hold or tap, time limit, tones, floating button, volume key).
 */
public class TakPttPlugin implements IPlugin {

    private static final String TAG = "TakPtt";

    IServiceController serviceController;
    Context pluginContext;
    IHostUIService uiService;
    ToolbarItem toolbarItem;
    Pane mainPane;

    private final Handler main = new Handler(Looper.getMainLooper());
    private VoiceClient voice;
    private PttButton ptt;
    private ChannelList channelList;
    private TextView channelsLabel;
    private boolean manetView;                      // CHANNELS shows the OpenMANET talk groups
    private int currentTab = -1;
    private HistoryView history;
    private TextView link, foot, onChannel, sos;
    private final TextView[] tabs = new TextView[3];
    private final View[] pages = new View[3];
    private float d;
    private Bundle state = new Bundle();
    private boolean askedMic, autoConnected, wasConnected;
    private long keysFetched;

    public TakPttPlugin(IServiceController serviceController) {
        this.serviceController = serviceController;
        final PluginContextProvider ctxProvider = serviceController.getService(PluginContextProvider.class);
        if (ctxProvider != null) {
            pluginContext = ctxProvider.getPluginContext();
            pluginContext.setTheme(R.style.ATAKPluginTheme);
        }
        uiService = serviceController.getService(IHostUIService.class);
        // The identifier must stay the same so ATAK finds the button after the user moves it.
        toolbarItem = new ToolbarItem.Builder(
                pluginContext.getString(R.string.app_name),
                MarshalManager.marshal(
                        pluginContext.getResources().getDrawable(R.drawable.ic_toolbar),
                        android.graphics.drawable.Drawable.class,
                        gov.tak.api.commons.graphics.Bitmap.class))
                .setListener(new ToolbarItemAdapter() {
                    @Override
                    public void onClick(ToolbarItem item) {
                        showPane();
                    }
                }).setIdentifier(pluginContext.getPackageName())
                .build();
    }

    @Override
    public void onStart() {
        if (uiService != null)
            uiService.addToolbarItem(toolbarItem);
        // Voice comes up with ATAK, so PTT works without opening this screen.
        voice = new VoiceClient(atak(), this::onState);
        voice.bind();
        main.post(sendPosition);
    }

    /** Mesh keys from the voice server, kept by the voice service for when the server is lost. */
    private void fetchMeshKeys() {
        keysFetched = System.currentTimeMillis();
        new Thread(() -> {
            try {
                TakVoice tak = TakVoice.fromAtak();
                Bundle b = new VoiceHistory(atak().getCacheDir()).meshKeys(tak);
                main.post(() -> voice.send(Proto.MESH_KEYS, 0, b));
            } catch (Exception e) {
                Log.w(TAG, "mesh keys: " + e.getMessage());
            }
        }, "takptt-keys").start();
    }

    /** This phone's position, for the voice history (sent to the recorder as you key up). */
    private final Runnable sendPosition = new Runnable() {
        @Override public void run() {
            try {
                com.atakmap.android.maps.Marker self = MapView.getMapView().getSelfMarker();
                if (self != null && self.getPoint() != null && self.getPoint().isValid()) {
                    Bundle b = new Bundle();
                    b.putDouble(Proto.LAT, self.getPoint().getLatitude());
                    b.putDouble(Proto.LON, self.getPoint().getLongitude());
                    voice.send(Proto.POSITION, 0, b);
                }
            } catch (Exception ignored) {
            }
            main.postDelayed(this, 20000);
        }
    };

    @Override
    public void onStop() {
        main.removeCallbacks(sendPosition);
        if (history != null)
            history.dispose();
        if (voice != null)
            voice.unbind();
        if (uiService != null)
            uiService.removeToolbarItem(toolbarItem);
    }

    // ------------------------------------------------------------------ helpers

    private Context atak() { return MapView.getMapView().getContext(); }

    private SharedPreferences prefs() { return atak().getSharedPreferences("takptt", Context.MODE_PRIVATE); }

    private String override() { return prefs().getString("override", ""); }

    /** Testing only: the override "host:port, history-port" may name a port for voice history. */
    private void applyHistoryPort() {
        String o = override();
        int comma = o.indexOf(',');
        int port = 8443;
        if (comma >= 0) {
            String rest = o.substring(comma + 1).replace("loop", "").replace(",", "").trim();
            try { port = Integer.parseInt(rest); } catch (NumberFormatException ignored) { }
        }
        VoiceHistory.port = port;
        if (voice != null) {
            Bundle b = new Bundle();
            b.putBoolean(Proto.MESH_LOOP, o.contains("loop"));       // testing: hear yourself on the mesh
            voice.send(Proto.SETTINGS, 0, b);
        }
    }

    private int dp(float v) { return Math.round(v * d); }

    private void toast(String text) { main.post(() -> Toast.makeText(atak(), text, Toast.LENGTH_SHORT).show()); }

    private boolean on(String key) { return state.getBoolean(key); }

    private TextView pill(Context ctx) {
        TextView t = new TextView(ctx);
        t.setTextSize(12);
        t.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        t.setLetterSpacing(0.12f);
        t.setPadding(dp(14), dp(8), dp(14), dp(8));
        return t;
    }

    // ------------------------------------------------------------------ state from the voice service

    private void onState(Bundle s) {
        state = s;
        if (history != null) {
            history.setLive(on(Proto.TALKING) || s.getString(Proto.RECEIVING) != null);
            history.setTalkGroupLog(s.getStringArrayList(Proto.OM_LOG));
        }
        String note = s.getString(Proto.CH_NOTE);
        if (note != null)
            toast(note);
        boolean nowConnected = on(Proto.CONNECTED);
        if (nowConnected && (!wasConnected || System.currentTimeMillis() - keysFetched > 600000))
            fetchMeshKeys();
        wasConnected = nowConnected;
        if (!autoConnected && !on(Proto.CONNECTED) && !on(Proto.CONNECTING)) {
            autoConnected = true;
            connect();
        }
        refresh();
    }

    // ------------------------------------------------------------------ pane

    private void showPane() {
        if (mainPane == null)
            mainPane = new PaneBuilder(buildView())
                    .setMetaValue(Pane.RELATIVE_LOCATION, Pane.Location.Default)
                    .setMetaValue(Pane.PREFERRED_WIDTH_RATIO, 0.5D)
                    .setMetaValue(Pane.PREFERRED_HEIGHT_RATIO, 0.5D)
                    .build();
        if (!uiService.isPaneVisible(mainPane))
            uiService.showPane(mainPane, null);
        if (!on(Proto.CONNECTED) && !on(Proto.CONNECTING))
            connect();
    }

    private View buildView() {
        Context ctx = atak();
        d = ctx.getResources().getDisplayMetrics().density;
        // The header and tabs stay at the top and the status line at the bottom; only the
        // section between them scrolls. The PTT ring takes the room left, so it is always whole.
        LinearLayout root = new LinearLayout(ctx);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Theme.BG);
        root.setPadding(dp(12), dp(10), dp(12), dp(8));
        LinearLayout box = root;                                   // fixed parts go here
        ScrollView scroll = new ScrollView(ctx);
        scroll.setFillViewport(true);
        LinearLayout content = new LinearLayout(ctx);
        content.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(content, new ScrollView.LayoutParams(-1, -1));

        LinearLayout head = new LinearLayout(ctx);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = new TextView(ctx);
        title.setText("TAK PTT");
        title.setTextSize(16);
        title.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD));
        title.setLetterSpacing(0.1f);
        title.setTextColor(Theme.GREEN);
        head.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        TextView set = pill(ctx);
        set.setText("SET");
        set.setTextColor(Theme.SUB);
        set.setBackground(Theme.box(Theme.PANEL, Theme.LINE, d));
        set.setOnClickListener(v -> editSettings());
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(-2, -2);
        sp.rightMargin = dp(8);
        head.addView(set, sp);
        link = pill(ctx);
        link.setOnClickListener(v -> showServer());
        link.setOnLongClickListener(v -> { editOverride(); return true; });
        head.addView(link);
        box.addView(head);

        // Tabs: PTT (the talk button), CHANNELS (pick or make one), HISTORY (past transmissions).
        LinearLayout tabBar = new LinearLayout(ctx);
        String[] names = {"PTT", "CHANNELS", "HISTORY"};
        for (int i = 0; i < 3; i++) {
            final int page = i;
            TextView t = pill(ctx);
            t.setText(names[i]);
            t.setGravity(Gravity.CENTER);
            t.setOnClickListener(v -> showTab(page));
            LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(0, -2, 1);
            if (i < 2) tp.rightMargin = dp(6);
            tabBar.addView(t, tp);
            tabs[i] = t;
        }
        LinearLayout.LayoutParams tbp = new LinearLayout.LayoutParams(-1, -2);
        tbp.topMargin = dp(10);
        box.addView(tabBar, tbp);
        box.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        // PTT page
        LinearLayout pttPage = new LinearLayout(ctx);
        pttPage.setOrientation(LinearLayout.VERTICAL);
        onChannel = new TextView(ctx);
        onChannel.setTextSize(15);
        onChannel.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        onChannel.setLetterSpacing(0.06f);
        onChannel.setLineSpacing(dp(2), 1f);
        onChannel.setTextColor(Theme.GREEN);
        onChannel.setPadding(dp(4), dp(6), dp(4), dp(4));
        onChannel.setMaxWidth(dp(118));                 // stays clear of the ring
        onChannel.setOnClickListener(v -> showTab(1));
        ptt = new PttButton(ctx);
        ptt.setPress(new PttButton.Press() {
            @Override public boolean down() {
                if (!on(Proto.MIC_ALLOWED) && on(Proto.CONNECTED)) {
                    voice.ask(Proto.ASK_MIC);
                    return false;
                }
                voice.send(Proto.PTT_DOWN, 0, null);
                return true;
            }

            @Override public void up() {
                voice.send(Proto.PTT_UP, 0, null);
            }
        });
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(-1, 0, 1);
        bp.topMargin = dp(12);                          // the same gap as below the ring
        ptt.setMinimumHeight(dp(120));
        // SOS is a small circle in the ring's spare bottom-left corner, so the ring keeps its size.
        android.widget.FrameLayout ring = new android.widget.FrameLayout(ctx);
        ring.addView(ptt, new android.widget.FrameLayout.LayoutParams(-1, -1));
        // The channel line sits in the top-left corner, so the ring gets the full height.
        ring.addView(onChannel, new android.widget.FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.LEFT));
        sos = new TextView(ctx);
        sos.setGravity(Gravity.CENTER);
        sos.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        sos.setOnTouchListener(sosTouch);
        android.widget.FrameLayout.LayoutParams sp2 = new android.widget.FrameLayout.LayoutParams(dp(58), dp(58),
                Gravity.BOTTOM | Gravity.LEFT);
        ring.addView(sos, sp2);
        showSos();
        pttPage.addView(ring, bp);
        pages[0] = pttPage;
        content.addView(pttPage, new LinearLayout.LayoutParams(-1, 0, 1));

        // CHANNELS page
        LinearLayout chPage = new LinearLayout(ctx);
        chPage.setOrientation(LinearLayout.VERTICAL);
        TextView label = new TextView(ctx);
        channelsLabel = label;
        label.setText("TAP NAME TO MOVE · RX LISTEN · TX TALK");
        label.setTextSize(11);
        label.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        label.setLetterSpacing(0.1f);
        label.setTextColor(Theme.SUB);
        label.setPadding(dp(4), dp(12), 0, dp(4));
        chPage.addView(label);
        channelList = new ChannelList(ctx);
        channelList.setListener(new ChannelList.Listener() {
            @Override public void join(ChannelList.Channel c) {
                if (manetView) return;
                if (!on(Proto.CONNECTED)) return;
                if (c.here) { toast("You are in " + c.name); return; }
                voice.send(Proto.JOIN, c.id, null);
                toast("Moved to " + c.name);
            }

            @Override public void rx(ChannelList.Channel c, boolean listen) {
                voice.send(manetView ? Proto.SET_OM_RX : Proto.SET_RX, c.id, listen ? 1 : 0, null);
            }

            @Override public void tx(ChannelList.Channel c, boolean talk) {
                voice.send(manetView ? Proto.SET_OM_TX : Proto.SET_TX, c.id, talk ? 1 : 0, null);
            }

            @Override public void create() {
                if (!on(Proto.CONNECTED)) { toast("Connect first"); return; }
                newChannel();
            }
        });
        chPage.addView(channelList, new LinearLayout.LayoutParams(-1, -2));
        pages[1] = chPage;
        content.addView(chPage, new LinearLayout.LayoutParams(-1, -2));

        // HISTORY page: transmissions saved by the PortableTAK voice recorder
        history = new HistoryView(ctx, pluginContext, () -> {
            try {
                return TakVoice.fromAtak();
            } catch (Exception e) {
                return null;
            }
        });
        history.onTextToggle = on -> {
            Bundle b = new Bundle();
            b.putBoolean(Proto.TRANSCRIPTS, on);
            voice.send(Proto.SETTINGS, 0, b);
        };
        View hPage = history;
        pages[2] = hPage;
        content.addView(hPage, new LinearLayout.LayoutParams(-1, -2));

        foot = new TextView(ctx);
        foot.setTextSize(11);
        foot.setTypeface(Typeface.MONOSPACE);
        foot.setTextColor(Theme.SUB);
        foot.setGravity(Gravity.CENTER);
        foot.setPadding(0, dp(8), 0, 0);
        foot.setOnClickListener(v -> {
            if (!on(Proto.MIC_ALLOWED)) voice.ask(Proto.ASK_MIC);
        });
        box.addView(foot);
        manetView = prefs().getBoolean("manetView", false);
        showTab(prefs().getInt("tab", 0));
        refresh();
        return root;
    }

    // The corner label: a small caption, the channel in green, then any extras on their own lines.
    private CharSequence channelLabel(boolean connected, String channel, int rx, int tx, String om) {
        android.text.SpannableStringBuilder b = new android.text.SpannableStringBuilder();
        if (connected) {
            caption(b, "TALKING ON", Theme.SUB);
            b.append(channel).append(" \u203a");
            if (rx > 1 || tx != 1)
                caption(b.append('\n'), "RX " + rx + " \u00b7 TX " + tx, Theme.SUB);
        }
        if (om != null) {
            if (b.length() > 0) b.append('\n');
            caption(b, "OPENMANET", Theme.CYAN);
            int start = b.length();
            b.append(om).append(connected ? "" : " \u203a");
            b.setSpan(new android.text.style.ForegroundColorSpan(Theme.CYAN), start, b.length(), 0);
            b.setSpan(new android.text.style.RelativeSizeSpan(0.85f), start, b.length(), 0);
        }
        return b;
    }

    /** Talk groups in a few characters: "CH 2", "CH 1-3", "CH 1,4", "ALL CH". */
    private static String groupsLabel(List<Integer> groups) {
        List<Integer> g = new ArrayList<>(groups);
        java.util.Collections.sort(g);
        if (g.size() == ManetLink.GROUPS) return "ALL CH";
        StringBuilder b = new StringBuilder("CH\u00a0");
        for (int i = 0; i < g.size(); ) {
            int j = i;
            while (j + 1 < g.size() && g.get(j + 1) == g.get(j) + 1) j++;
            if (i > 0) b.append(',');
            b.append(g.get(i));
            if (j > i) b.append(j > i + 1 ? "-" : ",").append(g.get(j));
            i = j + 1;
        }
        return b.toString();
    }

    private static void caption(android.text.SpannableStringBuilder b, String text, int color) {
        int start = b.length();
        b.append(text);
        b.setSpan(new android.text.style.RelativeSizeSpan(0.62f), start, b.length(), 0);
        b.setSpan(new android.text.style.ForegroundColorSpan(color), start, b.length(), 0);
        b.setSpan(new android.text.style.ScaleXSpan(1.1f), start, b.length(), 0);
        if (!text.startsWith("RX")) b.append('\n');
    }

    // ------------------------------------------------------------------ SOS (ATAK emergency alert)

    private static final int SOS_HOLD_MS = 3000;

    private boolean emergencyOn() {
        try {
            Boolean on = com.atakmap.android.emergency.tool.EmergencyManager.getInstance().isEmergencyOn();
            return on != null && on;
        } catch (Exception e) {
            return false;
        }
    }

    private void showSos() {
        if (sos == null) return;
        boolean on = emergencyOn();
        sos.setText(on ? "CANCEL\nSOS" : "SOS");
        sos.setTextSize(on ? 10 : 14);
        sos.setTextColor(Theme.RED);
        sos.setBackground(circle(on ? 0x33FF4D4D : Theme.PANEL));
    }

    private android.graphics.drawable.GradientDrawable circle(int fill) {
        android.graphics.drawable.GradientDrawable g = new android.graphics.drawable.GradientDrawable();
        g.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        g.setColor(fill);
        g.setStroke(dp(2), Theme.RED);
        return g;
    }

    private final Runnable sosFire = () -> {
        try {
            com.atakmap.android.emergency.tool.EmergencyManager em =
                    com.atakmap.android.emergency.tool.EmergencyManager.getInstance();
            em.setEmergencyType(com.atakmap.android.emergency.tool.EmergencyType.NineOneOne);
            em.initiateRepeat(com.atakmap.android.emergency.tool.EmergencyType.NineOneOne, true);
            toast("Emergency alert sent (911)");
            Log.i(TAG, "SOS: emergency alert on");
        } catch (Exception e) {
            toast("Could not send the emergency alert: " + e.getMessage());
        }
        showSos();
    };

    private final Runnable sosCount = new Runnable() {
        @Override public void run() {
            if (sosStart == 0) return;
            long held = System.currentTimeMillis() - sosStart;
            if (held >= SOS_HOLD_MS) { sosStart = 0; sosFire.run(); return; }
            sos.setText(String.valueOf((int) Math.ceil((SOS_HOLD_MS - held) / 1000.0)));
            sos.setTextSize(20);
            main.postDelayed(this, 100);
        }
    };

    private long sosStart;

    /** Hold SOS for 3 s to send ATAK's 911 alert; while one is on, tap to cancel it. */
    private final View.OnTouchListener sosTouch = (v, e) -> {
        switch (e.getActionMasked()) {
            case android.view.MotionEvent.ACTION_DOWN:
                if (emergencyOn()) {
                    new AlertDialog.Builder(atak()).setTitle("Cancel the emergency alert?")
                            .setNegativeButton("Keep it on", null)
                            .setPositiveButton("Cancel alert", (dlg, w) -> {
                                try {
                                    com.atakmap.android.emergency.tool.EmergencyManager.getInstance().cancelRepeat(
                                            com.atakmap.android.emergency.tool.EmergencyType.NineOneOne, true);
                                    Log.i(TAG, "SOS: emergency alert cancelled");
                                } catch (Exception ex) {
                                    toast("Could not cancel: " + ex.getMessage());
                                }
                                showSos();
                            }).show();
                    return true;
                }
                sosStart = System.currentTimeMillis();
                sosCount.run();
                toast("Keep holding SOS for 3 seconds to send the emergency alert");
                return true;
            case android.view.MotionEvent.ACTION_UP:
            case android.view.MotionEvent.ACTION_CANCEL:
                if (sosStart != 0) { sosStart = 0; showSos(); }
                return true;
        }
        return false;
    };

    /** Show a tab; tapping CHANNELS again switches between the server's channels and OpenMANET's talk groups. */
    private void showTab(int page) {
        if (page == 1 && currentTab == 1)
            manetView = !manetView;
        currentTab = page;
        for (int i = 0; i < 3; i++) {
            boolean sel = i == page;
            pages[i].setVisibility(sel ? View.VISIBLE : View.GONE);
            if (i == 2 && history != null)
                history.setShown(sel);
            int accent = i == 1 && manetView ? Theme.CYAN : Theme.GREEN;
            tabs[i].setTextColor(sel ? accent : i == 1 && manetView ? Theme.CYAN : Theme.SUB);
            tabs[i].setBackground(Theme.box(sel ? (accent & 0x00FFFFFF) | 0x1A000000 : Theme.PANEL,
                    sel ? accent : i == 1 && manetView ? 0x4700E5FF : Theme.LINE, d));
        }
        tabs[1].setText(manetView ? "OPENMANET" : "CHANNELS");
        tabs[1].setSingleLine(true);
        tabs[1].setTextSize(manetView ? 11 : 12);          // OPENMANET is a letter longer: keep it on one line
        tabs[1].setLetterSpacing(manetView ? 0.06f : 0.12f);
        channelsLabel.setText(manetView ? "OPENMANET TALK GROUPS · NOT ENCRYPTED" : "TAP NAME TO MOVE · RX LISTEN · TX TALK");
        channelsLabel.setTextColor(manetView ? Theme.CYAN : Theme.SUB);
        prefs().edit().putInt("tab", page).putBoolean("manetView", manetView).apply();
        refresh();
    }

    private boolean omTxPicked() {
        ArrayList<Integer> tx = state.getIntegerArrayList(Proto.OM_TX);
        return tx != null && !tx.isEmpty();
    }

    /** Redraw the button, pill, channels and status line from the voice service's state. */
    private void refresh() {
        main.post(() -> {
            if (ptt == null) return;
            boolean connected = on(Proto.CONNECTED), mesh = on(Proto.MESH);
            String receiving = state.getString(Proto.RECEIVING);
            String server = state.getString(Proto.SERVER_NAME);
            String error = state.getString(Proto.ERROR);
            boolean latch = Proto.MODE_LATCH.equals(state.getString(Proto.MODE));
            if (on(Proto.TALKING)) ptt.show(Theme.RED, latch ? "TRANSMITTING · TAP TO STOP" : "TRANSMITTING", true);
            else if (receiving != null) ptt.show(Theme.AMBER, "RECEIVING · " + receiving, true);
            else if (connected) ptt.show(Theme.GREEN, latch ? "TAP TO TALK" : "HOLD TO TALK", false);
            else if (mesh) ptt.show(Theme.GREEN, (latch ? "TAP TO TALK" : "HOLD TO TALK") + " · MESH", false);
            else if (omTxPicked()) ptt.show(Theme.CYAN, (latch ? "TAP TO TALK" : "HOLD TO TALK") + " · OPENMANET", false);
            else ptt.show(Theme.MUTED, on(Proto.CONNECTING) ? "CONNECTING" : "NOT CONNECTED", false);
            link.setText(connected ? "● CONNECTED" : mesh ? "◆ MESH" : "○ OFFLINE");
            link.setTextColor(connected ? Theme.GREEN : mesh ? Theme.AMBER : Theme.SUB);
            link.setBackground(Theme.box(connected ? 0x1A32E07A : mesh ? 0x1AFFB84D : Theme.PANEL,
                    connected ? Theme.GREEN : mesh ? Theme.AMBER : Theme.LINE, d));

            List<ChannelList.Channel> list = new ArrayList<>();
            ArrayList<Integer> ids = state.getIntegerArrayList(Proto.CH_IDS);
            int txCount = 0, rxCount = 0;
            if (connected && ids != null) {
                ArrayList<String> names = state.getStringArrayList(Proto.CH_NAMES);
                ArrayList<Integer> users = state.getIntegerArrayList(Proto.CH_USERS);
                ArrayList<Integer> active = state.getIntegerArrayList(Proto.CH_ACTIVE);
                ArrayList<Integer> rx = state.getIntegerArrayList(Proto.CH_RX);
                ArrayList<Integer> tx = state.getIntegerArrayList(Proto.CH_TX);
                int cur = state.getInt(Proto.CH_CURRENT);
                for (int i = 0; i < ids.size(); i++) {
                    int id = ids.get(i);
                    boolean r = rx != null && rx.contains(id), t = tx != null && tx.contains(id);
                    if (r) rxCount++;
                    if (t) txCount++;
                    list.add(new ChannelList.Channel(id, names.get(i), users.get(i), id == cur, active.get(i) == 1, r, t));
                }
            }
            ArrayList<Integer> omRx = state.getIntegerArrayList(Proto.OM_RX), omTx = state.getIntegerArrayList(Proto.OM_TX),
                    omActive = state.getIntegerArrayList(Proto.OM_ACTIVE);
            boolean omTalk = omTx != null && !omTx.isEmpty();
            if (manetView) {
                List<ChannelList.Channel> groups = new ArrayList<>();
                for (int g = 1; g <= ManetLink.GROUPS; g++)
                    groups.add(new ChannelList.Channel(g, "CH " + g, 0, false,
                            omActive != null && omActive.contains(g),
                            omRx != null && omRx.contains(g), omTx != null && omTx.contains(g)));
                channelList.setChannels(groups, true);
            } else {
                channelList.setChannels(list, false);
            }

            showSos();
            onChannel.setText(channelLabel(connected, state.getString(Proto.CHANNEL, ""), rxCount, txCount,
                    omTalk ? groupsLabel(omTx) : null));
            String status;
            if (connected && !on(Proto.MIC_ALLOWED)) status = "Tap here to allow the microphone";
            else if (connected) status = "Talking on " + state.getString(Proto.CHANNEL) + " · " + server;
            else if (mesh) status = "Server out of reach · local network (mesh)";
            else if (omTalk) status = "No voice server · talking on OpenMANET";
            else if (error != null) status = error;
            else status = "Connecting to " + (server != null ? server : "voice");
            String[] said = latestHeard(state.getStringArrayList(Proto.OM_LOG));
            if (said != null) status = "CH " + said[1] + " \u00b7 \u201c" + said[2] + "\u201d";
            foot.setText(status);
            foot.setTextColor(said != null ? Theme.CYAN : Theme.SUB);
            foot.setMaxLines(2);
            foot.setEllipsize(android.text.TextUtils.TruncateAt.END);
            if (connected && !on(Proto.MIC_ALLOWED) && !askedMic) {
                askedMic = true;
                voice.ask(Proto.ASK_MIC);
            }
        });
    }

    private final Runnable clearHeard = () -> { if (state != null) onState(state); };

    /** The newest talk-group transcript if it is under 20 s old: {millis, group, text}. */
    private String[] latestHeard(ArrayList<String> log) {
        if (log == null || log.isEmpty()) return null;
        String[] e = log.get(0).split("\\|", 3);
        if (e.length < 3) return null;
        long age = System.currentTimeMillis() - Long.parseLong(e[0]);
        if (age > 20000) return null;
        main.removeCallbacks(clearHeard);
        main.postDelayed(clearHeard, 20000 - age + 100);
        return e;
    }

    // ------------------------------------------------------------------ dialogs

    private void showServer() {
        String server = state.getString(Proto.SERVER_NAME);
        String error = state.getString(Proto.ERROR);
        String msg;
        if (server == null)
            msg = error != null ? error : "Not set up yet.";
        else
            msg = "Server: " + server + "\n"
                    + (on(Proto.FROM_TAK) ? "Signed in with your TAK certificate (" + prefs().getString("user", "") + ")"
                                          : "Test override, signed in with this phone's own certificate") + "\n"
                    + (on(Proto.CONNECTED) ? "Connected" : (error != null ? error : "Connecting..."));
        new AlertDialog.Builder(atak()).setTitle("Voice server").setMessage(msg)
                .setNegativeButton("Close", null)
                .setNeutralButton(on(Proto.CONNECTED) ? "Disconnect" : null,
                        (dlg, w) -> voice.send(Proto.DISCONNECT, 0, null))
                .setPositiveButton("Reconnect", (dlg, w) -> connect())
                .show();
    }

    /** Testing only: point voice at another server (blank: the TAK server, as normal). */
    private void editOverride() {
        Context ctx = atak();
        EditText server = new EditText(ctx);
        server.setHint("Blank: use the TAK server (testing: host:port, history port)");
        server.setText(override());
        server.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        new AlertDialog.Builder(ctx).setTitle("Voice server override (testing)").setView(server)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Save", (dlg, w) -> {
                    prefs().edit().putString("override", server.getText().toString().trim()).apply();
                    connect();
                })
                .show();
    }

    private void newChannel() {
        Context ctx = atak();
        EditText name = new EditText(ctx);
        name.setHint("Channel name");
        new AlertDialog.Builder(ctx).setTitle("New channel").setView(name)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Create", (dlg, w) -> {
                    String n = name.getText().toString().trim();
                    if (n.isEmpty()) return;
                    Bundle b = new Bundle();
                    b.putString(Proto.NAME, n);
                    voice.send(Proto.CREATE, 0, b);
                }).show();
    }

    private TextView heading(Context ctx, String text) {
        TextView t = new TextView(ctx);
        t.setText(text);
        t.setTextSize(13);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, dp(12), 0, dp(2));
        return t;
    }

    private RadioButton radio(Context ctx, RadioGroup g, String text, boolean checked) {
        RadioButton r = new RadioButton(ctx);
        r.setText(text);
        r.setId(View.generateViewId());
        g.addView(r);
        r.setChecked(checked);
        return r;
    }

    /** Settings in three sections: TX and RX, Channels, Preferences. */
    private void editSettings() {
        Context ctx = atak();
        LinearLayout outer = new LinearLayout(ctx);
        outer.setOrientation(LinearLayout.VERTICAL);
        LinearLayout tabBar = new LinearLayout(ctx);
        tabBar.setPadding(dp(16), dp(8), dp(16), 0);
        outer.addView(tabBar);
        ScrollView scroll = new ScrollView(ctx);
        outer.addView(scroll, new LinearLayout.LayoutParams(-1, -2));
        LinearLayout[] sections = new LinearLayout[3];
        for (int i = 0; i < 3; i++) {
            sections[i] = new LinearLayout(ctx);
            sections[i].setOrientation(LinearLayout.VERTICAL);
            sections[i].setPadding(dp(20), dp(4), dp(20), dp(8));
        }
        LinearLayout box = new LinearLayout(ctx);
        box.setOrientation(LinearLayout.VERTICAL);
        for (LinearLayout sec : sections) box.addView(sec);
        scroll.addView(box);
        String[] titles = {"TX AND RX", "CHANNELS", "PREFERENCES"};
        TextView[] chips = new TextView[3];
        for (int i = 0; i < 3; i++) {
            final int page = i;
            TextView t = pill(ctx);
            t.setText(titles[i]);
            t.setTextSize(11);
            t.setGravity(Gravity.CENTER);
            t.setPadding(dp(6), dp(8), dp(6), dp(8));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -2, 1);
            if (i < 2) lp.rightMargin = dp(6);
            tabBar.addView(t, lp);
            chips[i] = t;
            t.setOnClickListener(v -> {
                for (int j = 0; j < 3; j++) {
                    sections[j].setVisibility(j == page ? View.VISIBLE : View.GONE);
                    chips[j].setTextColor(j == page ? Theme.GREEN : Theme.SUB);
                    chips[j].setBackground(Theme.box(j == page ? 0x1A32E07A : Theme.PANEL, j == page ? Theme.GREEN : Theme.LINE, d));
                }
            });
        }

        // TX and RX
        LinearLayout tx = sections[0];
        tx.addView(heading(ctx, "Push to talk"));
        RadioGroup mode = new RadioGroup(ctx);
        boolean latch = Proto.MODE_LATCH.equals(state.getString(Proto.MODE));
        radio(ctx, mode, "Hold to talk", !latch);
        RadioButton tap = radio(ctx, mode, "Tap to start, tap to stop", latch);
        tx.addView(mode);
        int timeout = state.getInt(Proto.TIMEOUT, 60);
        TextView limit = heading(ctx, "Talk time limit: " + timeout + " s");
        tx.addView(limit);
        SeekBar bar = new SeekBar(ctx);
        bar.setMax(14);                                  // 20 to 90 seconds in 5 second steps
        bar.setProgress((timeout - 20) / 5);
        bar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar s, int p, boolean user) {
                limit.setText("Talk time limit: " + (20 + p * 5) + " s");
            }
            @Override public void onStartTrackingTouch(SeekBar s) { }
            @Override public void onStopTrackingTouch(SeekBar s) { }
        });
        tx.addView(bar);
        CheckBox tones = new CheckBox(ctx);
        tones.setText("Tones (talk permit, time limit, errors)");
        tones.setChecked(state.getBoolean(Proto.TONES, true));
        tx.addView(tones);
        tx.addView(heading(ctx, "Audio out"));
        RadioGroup route = new RadioGroup(ctx);
        String r = state.getString(Proto.ROUTE, Proto.ROUTE_AUTO);
        radio(ctx, route, "Automatic (headset when connected, else loudspeaker)", Proto.ROUTE_AUTO.equals(r));
        RadioButton speaker = radio(ctx, route, "Always the loudspeaker", Proto.ROUTE_SPEAKER.equals(r));
        RadioButton earpiece = radio(ctx, route, "Earpiece (hold the phone to your ear)", Proto.ROUTE_EARPIECE.equals(r));
        tx.addView(route);

        // Channels
        LinearLayout ch = sections[1];
        CheckBox rejoin = new CheckBox(ctx);
        rejoin.setText("Go back to my last channel when connecting");
        rejoin.setChecked(state.getBoolean(Proto.REJOIN, true));
        ch.addView(rejoin);
        TextView help = new TextView(ctx);
        help.setText("Channels come from the voice server and update by themselves. Pick RX (listen) and TX (talk) "
                + "per channel in the CHANNELS tab.");
        help.setTextSize(12);
        help.setPadding(0, dp(8), 0, dp(4));
        ch.addView(help);
        CheckBox clear = new CheckBox(ctx);
        clear.setText("Clear all RX and TX picks (talk and listen on my channel only)");
        ch.addView(clear);

        // Preferences
        LinearLayout pref = sections[2];
        pref.addView(heading(ctx, "Buttons outside this screen"));
        CheckBox floating = new CheckBox(ctx);
        floating.setText("Floating PTT button (over other apps too)");
        floating.setChecked(on(Proto.FLOATING));
        pref.addView(floating);
        TextView sizeLabel = new TextView(ctx);
        sizeLabel.setText("Floating button size");
        sizeLabel.setPadding(dp(32), 0, 0, 0);
        pref.addView(sizeLabel);
        RadioGroup size = new RadioGroup(ctx);
        size.setOrientation(RadioGroup.HORIZONTAL);
        size.setPadding(dp(28), 0, 0, 0);
        int fs = state.getInt(Proto.FLOAT_SIZE, Proto.FLOAT_MEDIUM);
        RadioButton small = radio(ctx, size, "Small", fs == Proto.FLOAT_SMALL);
        radio(ctx, size, "Medium", fs == Proto.FLOAT_MEDIUM);
        RadioButton large = radio(ctx, size, "Large", fs == Proto.FLOAT_LARGE);
        pref.addView(size);
        TextView keyLabel = new TextView(ctx);
        keyLabel.setText("Volume key as PTT");
        keyLabel.setPadding(0, dp(6), 0, 0);
        pref.addView(keyLabel);
        RadioGroup key = new RadioGroup(ctx);
        String k = state.getString(Proto.VOLUME_KEY, Proto.KEY_OFF);
        radio(ctx, key, "Off", Proto.KEY_OFF.equals(k));
        RadioButton keyDown = radio(ctx, key, "Volume down", Proto.KEY_DOWN.equals(k));
        RadioButton keyUp = radio(ctx, key, "Volume up", Proto.KEY_UP.equals(k));
        pref.addView(key);
        pref.addView(heading(ctx, "Voice history"));
        CheckBox map = new CheckBox(ctx);
        map.setText("Show history on my map (only on this phone)");
        map.setChecked(prefs().getBoolean("historyMap", false));
        pref.addView(map);
        pref.addView(heading(ctx, "Mesh failover"));
        CheckBox meshBox = new CheckBox(ctx);
        meshBox.setText("When the server is out of reach, talk over the local network (encrypted)");
        meshBox.setChecked(state.getBoolean("meshOn", true));
        pref.addView(meshBox);
        pref.addView(heading(ctx, "Voice server"));
        TextView server = new TextView(ctx);
        server.setText("Set from your TAK server connection. Testing override: long-press the connection pill.");
        server.setTextSize(12);
        pref.addView(server);

        chips[0].performClick();
        new AlertDialog.Builder(ctx).setTitle("TAK PTT settings").setView(outer)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Save", (dlg, w) -> {
                    Bundle b = new Bundle();
                    b.putString(Proto.MODE, tap.isChecked() ? Proto.MODE_LATCH : Proto.MODE_HOLD);
                    b.putInt(Proto.TIMEOUT, 20 + bar.getProgress() * 5);
                    b.putBoolean(Proto.TONES, tones.isChecked());
                    b.putString(Proto.ROUTE, speaker.isChecked() ? Proto.ROUTE_SPEAKER
                            : earpiece.isChecked() ? Proto.ROUTE_EARPIECE : Proto.ROUTE_AUTO);
                    b.putBoolean(Proto.REJOIN, rejoin.isChecked());
                    b.putBoolean(Proto.CLEAR_PICKS, clear.isChecked());
                    b.putBoolean(Proto.MESH, meshBox.isChecked());
                    b.putBoolean(Proto.FLOATING, floating.isChecked());
                    b.putInt(Proto.FLOAT_SIZE, small.isChecked() ? Proto.FLOAT_SMALL
                            : large.isChecked() ? Proto.FLOAT_LARGE : Proto.FLOAT_MEDIUM);
                    String vk = keyDown.isChecked() ? Proto.KEY_DOWN : keyUp.isChecked() ? Proto.KEY_UP : Proto.KEY_OFF;
                    b.putString(Proto.VOLUME_KEY, vk);
                    voice.send(Proto.SETTINGS, 0, b);
                    if (map.isChecked() != prefs().getBoolean("historyMap", false)) {
                        prefs().edit().putBoolean("historyMap", map.isChecked()).apply();
                        if (history != null) history.refresh();
                    }
                    if (floating.isChecked() && !on(Proto.OVERLAY_ALLOWED)) {
                        toast("Allow TAK PTT to display over other apps");
                        voice.ask(Proto.ASK_OVERLAY);
                    } else if (!Proto.KEY_OFF.equals(vk) && !on(Proto.KEYS_ALLOWED)) {
                        new AlertDialog.Builder(ctx).setTitle("Volume key PTT")
                                .setMessage("Android lets an app use the volume keys in the background only "
                                        + "through an accessibility setting. On the next screen, open "
                                        + "Installed apps (or Downloaded apps), then \"TAK PTT buttons\", and turn it on. "
                                        + "It only watches the PTT key.")
                                .setNegativeButton("Not now", null)
                                .setPositiveButton("Open settings", (d2, w2) -> voice.ask(Proto.ASK_KEYS))
                                .show();
                    }
                })
                .show();
    }

    // ------------------------------------------------------------------ connection

    /** Read the TAK connection in ATAK (slow: not on the UI thread) and hand it to the voice service. */
    private void connect() {
        applyHistoryPort();
        String raw = override();
        final String over = raw.indexOf(',') >= 0 ? raw.substring(0, raw.indexOf(',')).trim() : raw;
        String callsign = MapView.getMapView().getDeviceCallsign();
        new Thread(() -> {
            TakVoice tak = null;
            String setupError = null;
            try {
                tak = TakVoice.fromAtak();
            } catch (TakVoice.SetupException e) {
                setupError = e.getMessage();
            } catch (Exception e) {
                Log.e(TAG, "TAK setup", e);
                setupError = "Could not read the TAK certificates: " + e.getMessage();
            }
            if (tak == null && over.isEmpty()) {
                String msg = setupError;
                main.post(() -> { state.putString(Proto.ERROR, msg); refresh(); });
                return;
            }
            Bundle b = new Bundle();
            String host = tak != null ? tak.host : null;
            int port = TakVoice.PORT;
            if (!over.isEmpty()) {
                host = over;
                int colon = over.lastIndexOf(':');
                if (colon > 0 && over.indexOf(':') == colon) {
                    host = over.substring(0, colon);
                    try {
                        port = Integer.parseInt(over.substring(colon + 1));
                    } catch (NumberFormatException e) {
                        main.post(() -> { state.putString(Proto.ERROR, "The override's port must be a number"); refresh(); });
                        return;
                    }
                }
            }
            b.putString(Proto.HOST, host);
            b.putInt(Proto.PORT, port);
            b.putString(Proto.CALLSIGN, callsign);
            b.putBoolean(Proto.FROM_TAK, tak != null);
            b.putString(Proto.SERVER_NAME, over.isEmpty() ? tak.serverName : over);
            if (tak != null) {
                b.putString(Proto.USER, tak.user);
                b.putByteArray(Proto.CERT, tak.certificate);
                b.putString(Proto.CERT_PASSWORD, tak.certificatePassword);
                ArrayList<byte[]> der = new ArrayList<>();
                try {
                    for (X509Certificate c : tak.trusted)
                        der.add(c.getEncoded());
                } catch (Exception e) {
                    Log.e(TAG, "trust store", e);
                }
                b.putSerializable(Proto.TRUSTED, der);
                prefs().edit().putString("user", tak.user).apply();
            }
            main.post(() -> voice.send(Proto.CONNECT, 0, b));
        }, "takptt-setup").start();
    }
}
