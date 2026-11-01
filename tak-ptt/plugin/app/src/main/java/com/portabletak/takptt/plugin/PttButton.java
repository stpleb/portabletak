package com.portabletak.takptt.plugin;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.MotionEvent;
import android.view.View;

/**
 * The PTT button (same design as the ManetNode plugin's): a ring of rounded segments around a circle
 * with "PTT" and the state. Ready = green, talking = red with the ring pulsing, receiving = amber with
 * the ring sweeping, off = grey. Hold to talk.
 */
final class PttButton extends View {
    interface Press { boolean down(); void up(); }

    private static final int SEGMENTS = 36;
    private final float d;
    private final Paint seg = new Paint(Paint.ANTI_ALIAS_FLAG), ring = new Paint(Paint.ANTI_ALIAS_FLAG),
            glow = new Paint(Paint.ANTI_ALIAS_FLAG), big = new Paint(Paint.ANTI_ALIAS_FLAG), sub = new Paint(Paint.ANTI_ALIAS_FLAG);
    private int colour = Theme.MUTED;
    private String state = "";
    private boolean animate, held;
    private Press press;

    PttButton(Context ctx) {
        super(ctx);
        d = ctx.getResources().getDisplayMetrics().density;
        seg.setStyle(Paint.Style.STROKE);
        seg.setStrokeCap(Paint.Cap.ROUND);
        ring.setStyle(Paint.Style.STROKE);
        big.setTextAlign(Paint.Align.CENTER);
        big.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD));
        sub.setTextAlign(Paint.Align.CENTER);
        sub.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.NORMAL));
        sub.setLetterSpacing(0.12f);
    }

    void setPress(Press p) { press = p; }

    /** The ring fits the space it is given, up to 260 dp across. */
    private float size() { return Math.min(Math.min(getWidth(), getHeight()), 260 * d); }

    /** Colour of the ring and text, the line under "PTT", and whether the ring moves. */
    void show(int colour, String state, boolean animate) {
        this.colour = colour;
        this.state = state;
        this.animate = animate;
        invalidate();
    }

    @Override
    protected void onMeasure(int w, int h) {
        int width = MeasureSpec.getSize(w);
        // Asked for its natural size (inside a scroll view), ask for little: the pane then gives
        // the ring whatever height is left, so the whole button is on screen.
        int height = MeasureSpec.getMode(h) == MeasureSpec.UNSPECIFIED ? Math.round(120 * d) : MeasureSpec.getSize(h);
        setMeasuredDimension(width, height);
    }

    @Override
    protected void onDraw(Canvas c) {
        float s = size(), cx = getWidth() / 2f, cy = getHeight() / 2f;
        float segW = s * 0.06f, outer = s / 2 - segW / 2 - s * 0.01f, inner = outer * 0.78f;
        long now = System.currentTimeMillis();
        glow.setColor(colour);
        glow.setAlpha(held ? 60 : 22);
        c.drawCircle(cx, cy, inner + s * 0.02f, glow);
        glow.setColor(Theme.BG);
        glow.setAlpha(255);
        c.drawCircle(cx, cy, inner - s * 0.01f, glow);
        seg.setStrokeWidth(segW);
        float sweep = 360f / SEGMENTS, gap = 360f * (segW * 0.55f) / (float) (2 * Math.PI * outer);
        RectF box = new RectF(cx - outer, cy - outer, cx + outer, cy + outer);
        double phase = (now % 1200) / 1200.0;
        for (int i = 0; i < SEGMENTS; i++) {
            int a;
            if (!animate) a = colour == Theme.MUTED ? 90 : 220;
            else if (colour == Theme.RED) a = (int) (150 + 105 * Math.abs(Math.sin(Math.PI * (phase * 2))));   // pulse
            else {                                                                                       // sweep
                double pos = ((double) i / SEGMENTS - phase + 1) % 1.0;
                a = (int) (60 + 195 * Math.pow(1 - pos, 3));
            }
            seg.setColor(colour);
            seg.setAlpha(a);
            c.drawArc(box, -90 + i * sweep + gap / 2, Math.max(1, sweep - gap), false, seg);
        }
        ring.setColor(colour);
        ring.setAlpha(colour == Theme.MUTED ? 120 : 255);
        ring.setStrokeWidth(Math.max(2 * d, s * 0.014f));
        c.drawCircle(cx, cy, inner, ring);
        big.setColor(colour == Theme.MUTED ? Theme.SUB : colour);
        big.setTextSize(inner * 0.5f);
        c.drawText("PTT", cx, cy + inner * 0.12f, big);
        sub.setColor(Theme.SUB);
        sub.setTextSize(inner * 0.13f);
        float w = sub.measureText(state);
        if (w > inner * 1.5f) sub.setTextSize(sub.getTextSize() * inner * 1.5f / w);
        c.drawText(state, cx, cy + inner * 0.45f, sub);
        if (animate) postInvalidateDelayed(40);
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        float dx = e.getX() - getWidth() / 2f, dy = e.getY() - getHeight() / 2f;
        boolean inside = Math.hypot(dx, dy) <= size() / 2f;
        switch (e.getAction()) {
            case MotionEvent.ACTION_DOWN:
                if (!inside) return false;
                held = press != null && press.down();
                if (held) performHapticFeedback(HAPTIC_FEEDBACK_ENABLED);
                invalidate();
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                if (held && press != null) press.up();
                held = false;
                invalidate();
                performClick();
                return true;
            default:
                return true;
        }
    }

    @Override
    public boolean performClick() { return super.performClick(); }
}
