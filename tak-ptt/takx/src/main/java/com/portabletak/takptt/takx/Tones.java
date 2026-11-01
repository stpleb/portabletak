package com.portabletak.takptt.takx;

/**
 * Radio tones, all made in code (no recorded audio): each is a list of (frequency Hz,
 * milliseconds) steps, frequency 0 being silence, with short fades so they do not click.
 * The same tones as the ATAK plugin.
 */
final class Tones {
    private static final int RATE = 48000;

    /** Talk permit: two quick rising beeps, then the microphone opens. */
    static final int[][] PERMIT = {{880, 55}, {0, 25}, {1320, 70}};
    /** Deny: a low buzz (not connected, or the server refused). */
    static final int[][] DENY = {{330, 120}, {0, 40}, {330, 120}};
    /** No channel to talk on yet (still connecting). */
    static final int[][] NO_CHANNEL = {{520, 90}, {0, 50}, {440, 90}, {0, 50}, {370, 120}};
    /** Talking ends soon: a short double chirp. */
    static final int[][] WARNING = {{1600, 40}, {0, 60}, {1600, 40}};
    /** Talking was cut off by the time limit: a falling tone. */
    static final int[][] CUTOFF = {{900, 90}, {700, 90}, {500, 160}};
    /** Talking was cut off by something else (connection lost). */
    static final int[][] INTERRUPT = {{700, 70}, {0, 30}, {700, 70}, {0, 30}, {700, 70}};

    private Tones() {
    }

    static int lengthMs(int[][] steps) {
        int ms = 0;
        for (int[] s : steps)
            ms += s[1];
        return ms;
    }

    static short[] render(int[][] steps) {
        short[] out = new short[RATE * lengthMs(steps) / 1000];
        int i = 0;
        double phase = 0;
        int fade = RATE / 400;                       // 2.5 ms
        for (int[] s : steps) {
            int n = RATE * s[1] / 1000;
            for (int k = 0; k < n && i < out.length; k++, i++) {
                if (s[0] == 0) {
                    out[i] = 0;
                    continue;
                }
                double env = Math.min(1.0, Math.min(k, n - 1 - k) / (double) fade);
                phase += 2 * Math.PI * s[0] / RATE;
                out[i] = (short) (Math.sin(phase) * 0.35 * 32767 * env);
            }
        }
        return out;
    }
}
