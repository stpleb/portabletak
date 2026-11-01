package com.portabletak.takptt.plugin;

import android.graphics.drawable.GradientDrawable;

/** TAK PTT colours: green theme on a near-black background. */
final class Theme {
    static final int BG = 0xFF050A07, PANEL = 0xFF09110C, LINE = 0x4732E07A, LINE_SOFT = 0x1A32E07A;
    static final int GREEN = 0xFF32E07A;     // ready, selected channel, accents
    static final int RED = 0xFFFF4D4D;       // transmitting
    static final int AMBER = 0xFFFFB84D;     // receiving
    static final int CYAN = 0xFF00E5FF;      // OpenMANET talk groups (the ManetNode plugin's colour)
    static final int MUTED = 0xFF4E5C55;     // off / not connected
    static final int TEXT = 0xFFDDEBE2, SUB = 0xFF8FA79A;

    private Theme() {
    }

    static GradientDrawable box(int fill, int stroke, float density) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setStroke(Math.max(1, Math.round(density)), stroke);
        g.setCornerRadius(6 * density);
        return g;
    }
}
