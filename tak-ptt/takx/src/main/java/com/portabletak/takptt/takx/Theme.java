package com.portabletak.takptt.takx;

import java.awt.Color;

/** TAK PTT colours: green theme on a near-black background, as in the ATAK plugin. */
final class Theme {
    static final Color BG = new Color(0x050A07), PANEL = new Color(0x09110C), PANEL_DARK = new Color(0x07100F);
    static final Color LINE = new Color(0x1F, 0x5C, 0x3A);
    static final Color GREEN = new Color(0x32E07A);     // ready, selected channel, accents
    static final Color RED = new Color(0xFF4D4D);       // transmitting
    static final Color AMBER = new Color(0xFFB84D);     // receiving
    static final Color MUTED = new Color(0x4E5C55);     // off / not connected
    static final Color TEXT = new Color(0xDDEBE2), SUB = new Color(0x8FA79A);

    private Theme() {
    }
}
