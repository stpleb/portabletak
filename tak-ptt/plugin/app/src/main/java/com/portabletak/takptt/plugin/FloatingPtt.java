package com.portabletak.takptt.plugin;

import android.annotation.SuppressLint;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.LinearLayout;

/**
 * The on-screen PTT button, drawn over every app (ATAK in the background included). Pressing
 * the round button talks at once; the small grip above it moves the button around the screen.
 */
final class FloatingPtt {
    interface Press {
        void down();

        void up();
    }

    private final Context ctx;
    private final Press press;
    private final WindowManager wm;
    private final SharedPreferences prefs;
    private final float d;
    private LinearLayout root;
    private Disc disc;
    private WindowManager.LayoutParams lp;
    private int size;

    FloatingPtt(Context ctx, Press press) {
        this.ctx = ctx;
        this.press = press;
        this.wm = ctx.getSystemService(WindowManager.class);
        this.prefs = ctx.getSharedPreferences("takptt-voice", Context.MODE_PRIVATE);
        this.d = ctx.getResources().getDisplayMetrics().density;
        // Turning the phone can leave the saved spot off the screen (y = 1250 upright is below a
        // 1080-high landscape screen): bring it back in.
        ctx.registerComponentCallbacks(new android.content.ComponentCallbacks() {
            @Override public void onConfigurationChanged(android.content.res.Configuration c) {
                new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(FloatingPtt.this::keepOnScreen, 300);
            }

            @Override public void onLowMemory() { }
        });
    }

    private int dp(float v) { return Math.round(v * d); }

    void show(int color, boolean latch, int sizeDp) {
        if (root != null && sizeDp != size)
            hide();                                   // new size: build it again
        size = sizeDp;
        if (root == null)
            create();
        disc.set(color, latch);
        keepOnScreen();
    }

    /** Moves the button fully onto the screen if it is (partly) off it. */
    private void keepOnScreen() {
        if (root == null || lp == null) return;
        int w, h;
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            android.graphics.Rect b = wm.getCurrentWindowMetrics().getBounds();
            w = b.width();
            h = b.height();
        } else {
            android.util.DisplayMetrics m = ctx.getResources().getDisplayMetrics();
            w = m.widthPixels;
            h = m.heightPixels;
        }
        int bw = root.getWidth() > 0 ? root.getWidth() : dp(size);
        int bh = root.getHeight() > 0 ? root.getHeight() : dp(size + 18);
        int x = Math.max(0, Math.min(lp.x, w - bw)), y = Math.max(0, Math.min(lp.y, h - bh));
        if (x == lp.x && y == lp.y) return;
        lp.x = x;
        lp.y = y;
        try {
            wm.updateViewLayout(root, lp);
        } catch (Exception ignored) {
        }
    }

    void hide() {
        if (root != null) {
            try {
                wm.removeView(root);
            } catch (Exception ignored) {
            }
            root = null;
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private void create() {
        root = new LinearLayout(ctx);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_HORIZONTAL);

        View grip = new View(ctx);
        grip.setBackground(Theme.box(0xCC09110C, Theme.LINE, d));
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(dp(44), dp(14));
        gp.bottomMargin = dp(4);
        root.addView(grip, gp);

        disc = new Disc(ctx);
        root.addView(disc, new LinearLayout.LayoutParams(dp(size), dp(size)));

        lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.START;
        lp.x = prefs.getInt("floatX", dp(16));
        lp.y = prefs.getInt("floatY", dp(160));

        grip.setOnTouchListener(new View.OnTouchListener() {
            float sx, sy;
            int ox, oy;

            @Override
            public boolean onTouch(View v, MotionEvent e) {
                switch (e.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                        sx = e.getRawX();
                        sy = e.getRawY();
                        ox = lp.x;
                        oy = lp.y;
                        return true;
                    case MotionEvent.ACTION_MOVE:
                        lp.x = ox + Math.round(e.getRawX() - sx);
                        lp.y = oy + Math.round(e.getRawY() - sy);
                        wm.updateViewLayout(root, lp);
                        return true;
                    case MotionEvent.ACTION_UP:
                        prefs.edit().putInt("floatX", lp.x).putInt("floatY", lp.y).apply();
                        return true;
                }
                return false;
            }
        });
        disc.setOnTouchListener((v, e) -> {
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    disc.pressed = true;
                    disc.invalidate();
                    press.down();
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    disc.pressed = false;
                    disc.invalidate();
                    press.up();
                    return true;
            }
            return false;
        });
        wm.addView(root, lp);
    }

    /** The round button: a ring in the state color around "PTT". */
    private final class Disc extends View {
        private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint ring = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
        private int color = Theme.MUTED;
        private boolean latch;
        boolean pressed;

        Disc(Context c) {
            super(c);
            ring.setStyle(Paint.Style.STROKE);
            ring.setStrokeWidth(dp(4));
            text.setTextAlign(Paint.Align.CENTER);
            text.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
        }

        void set(int c, boolean l) {
            if (c != color || l != latch) {
                color = c;
                latch = l;
                invalidate();
            }
        }

        @Override
        protected void onDraw(Canvas c) {
            float r = Math.min(getWidth(), getHeight()) / 2f;
            fill.setColor(pressed ? 0xF0101C14 : 0xE0050A07);
            c.drawCircle(r, r, r - dp(2), fill);
            ring.setColor(color);
            c.drawCircle(r, r, r - dp(4), ring);
            text.setColor(color);
            float k = size / 76f;                     // text grows with the button
            text.setTextSize(dp(17) * k);
            c.drawText("PTT", r, r + dp(6) * k, text);
            if (latch) {
                text.setTextSize(dp(8) * k);
                c.drawText("TAP", r, r + dp(19) * k, text);
            }
        }
    }
}
