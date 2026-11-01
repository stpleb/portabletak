package com.portabletak.takptt.plugin;

import android.content.Context;
import android.graphics.Typeface;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.List;

/**
 * The server's channels as a list, one row each: the name (tap to move into that channel), how
 * many people are in it, a dot while someone talks, and RX / TX buttons. RX listens to a
 * channel without joining it; TX picks the channels a transmission goes to (several at once).
 * The channel you are in is marked HERE and is always heard. A "+ NEW CHANNEL" row ends the list.
 */
final class ChannelList extends LinearLayout {
    static final class Channel {
        final int id, users;
        final String name;
        final boolean here, active, rx, tx;

        Channel(int id, String name, int users, boolean here, boolean active, boolean rx, boolean tx) {
            this.id = id;
            this.name = name;
            this.users = users;
            this.here = here;
            this.active = active;
            this.rx = rx;
            this.tx = tx;
        }
    }

    interface Listener {
        void join(Channel c);

        void rx(Channel c, boolean on);

        void tx(Channel c, boolean on);

        void create();
    }

    private final float d;
    private Listener listener;
    private boolean manet;

    ChannelList(Context ctx) {
        super(ctx);
        setOrientation(VERTICAL);
        d = ctx.getResources().getDisplayMetrics().density;
    }

    private int dp(float v) { return Math.round(v * d); }

    void setListener(Listener l) { listener = l; }

    private TextView toggle(String text, boolean on, boolean locked, boolean active) {
        TextView t = new TextView(getContext());
        t.setText(text);
        t.setTextSize(12);
        t.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        t.setGravity(Gravity.CENTER);
        t.setLetterSpacing(0.1f);
        int color = active ? Theme.AMBER : text.equals("TX") ? Theme.RED : manet ? Theme.CYAN : Theme.GREEN;
        t.setTextColor(on ? color : Theme.MUTED);
        t.setBackground(Theme.box(on ? (color & 0x00FFFFFF) | 0x22000000 : Theme.PANEL,
                on ? color : Theme.LINE, d));
        t.setAlpha(locked ? 0.6f : 1f);
        return t;
    }

    /** OpenMANET talk groups (manet) or the server's channels. */
    void setChannels(List<Channel> list, boolean manet) {
        this.manet = manet;
        removeAllViews();
        for (Channel c : list) {
            LinearLayout row = new LinearLayout(getContext());
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(10), dp(6), dp(6), dp(6));
            row.setBackground(Theme.box(c.here ? 0x1432E07A : Theme.PANEL, c.here ? Theme.GREEN : Theme.LINE, d));

            TextView dot = new TextView(getContext());
            dot.setText("●");
            dot.setTextSize(10);
            dot.setTextColor(c.active ? Theme.AMBER : 0x00000000);
            if (manet) row.setBackground(Theme.box(c.tx ? 0x1400E5FF : Theme.PANEL, c.tx ? Theme.CYAN : 0x4700E5FF, d));
            dot.setPadding(0, 0, dp(6), 0);
            row.addView(dot);

            LinearLayout names = new LinearLayout(getContext());
            names.setOrientation(VERTICAL);
            TextView name = new TextView(getContext());
            name.setText(c.name);
            name.setTextSize(14);
            name.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD));
            name.setTextColor(c.here ? Theme.GREEN : Theme.TEXT);
            name.setSingleLine(true);
            names.addView(name);
            TextView sub = new TextView(getContext());
            sub.setText(manet ? "OPENMANET · PORT " + ManetLink.port(c.id)
                    : (c.here ? "HERE · " : "") + c.users + (c.users == 1 ? " person" : " people"));
            sub.setTextSize(10);
            sub.setTypeface(Typeface.MONOSPACE);
            sub.setTextColor(Theme.SUB);
            names.addView(sub);
            names.setOnClickListener(v -> { if (listener != null) listener.join(c); });
            row.addView(names, new LayoutParams(0, -2, 1));

            TextView rx = toggle("RX", c.rx, c.here, c.active && c.rx);
            rx.setOnClickListener(v -> {
                if (c.here) return;                     // the channel you are in is always heard
                if (listener != null) listener.rx(c, !c.rx);
            });
            LayoutParams lp = new LayoutParams(dp(52), dp(34));
            lp.leftMargin = dp(6);
            row.addView(rx, lp);
            TextView tx = toggle("TX", c.tx, false, false);
            tx.setOnClickListener(v -> { if (listener != null) listener.tx(c, !c.tx); });
            LayoutParams tp = new LayoutParams(dp(52), dp(34));
            tp.leftMargin = dp(6);
            row.addView(tx, tp);

            LayoutParams rp = new LayoutParams(-1, -2);
            rp.bottomMargin = dp(6);
            addView(row, rp);
        }
        if (manet) return;                              // talk groups are fixed: no new ones
        TextView add = new TextView(getContext());
        add.setText("+ NEW CHANNEL");
        add.setTextSize(13);
        add.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        add.setLetterSpacing(0.1f);
        add.setTextColor(Theme.GREEN);
        add.setGravity(Gravity.CENTER);
        add.setPadding(0, dp(10), 0, dp(10));
        add.setBackground(Theme.box(Theme.PANEL, Theme.LINE, d));
        add.setOnClickListener(v -> { if (listener != null) listener.create(); });
        addView(add, new LayoutParams(-1, -2));
    }
}
