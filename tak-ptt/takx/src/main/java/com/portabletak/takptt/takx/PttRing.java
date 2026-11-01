package com.portabletak.takptt.takx;

import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.awt.geom.Arc2D;
import java.awt.geom.Ellipse2D;

import javax.swing.JComponent;
import javax.swing.Timer;

/**
 * The PTT button, the same design as the ATAK plugin's: a ring of rounded segments around a circle
 * with "PTT" and the state. Ready = green, talking = red with the ring pulsing, receiving = amber
 * with the ring sweeping, off = grey.
 */
final class PttRing extends JComponent {
    interface Press { void down(); void up(); }

    private static final int SEGMENTS = 36;
    private Color colour = Theme.MUTED;
    private String state = "";
    private boolean animate, held;
    private Press press;
    private final Timer ticks = new Timer(40, e -> repaint());

    PttRing() {
        setPreferredSize(new Dimension(240, 240));
        setMinimumSize(new Dimension(160, 160));
        setFocusable(true);
        addMouseListener(new MouseAdapter() {
            @Override public void mousePressed(MouseEvent e) {
                requestFocusInWindow();
                double dx = e.getX() - getWidth() / 2.0, dy = e.getY() - getHeight() / 2.0;
                if (Math.hypot(dx, dy) > Math.min(getWidth(), getHeight()) / 2.0) return;
                held = true;
                if (press != null) press.down();
                repaint();
            }

            @Override public void mouseReleased(MouseEvent e) {
                if (!held) return;
                held = false;
                if (press != null) press.up();
                repaint();
            }
        });
    }

    void setPress(Press p) { press = p; }

    /** Colour of the ring and text, the line under "PTT", and whether the ring moves. */
    void show(Color colour, String state, boolean animate) {
        this.colour = colour;
        this.state = state;
        this.animate = animate;
        if (animate) ticks.start(); else ticks.stop();
        repaint();
    }

    @Override
    public void removeNotify() {
        ticks.stop();
        super.removeNotify();
    }

    private static Color alpha(Color c, int a) { return new Color(c.getRed(), c.getGreen(), c.getBlue(), Math.max(0, Math.min(255, a))); }

    @Override
    protected void paintComponent(Graphics graphics) {
        Graphics2D g = (Graphics2D) graphics.create();
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        // Everything is a fraction of the size: thick segmented ring, inner ring at 78% of it, text inside.
        float s = Math.min(getWidth(), getHeight()), cx = getWidth() / 2f, cy = getHeight() / 2f;
        float segW = s * 0.06f, outer = s / 2 - segW / 2 - s * 0.01f, inner = outer * 0.78f;
        boolean off = colour.equals(Theme.MUTED);
        double phase = (System.currentTimeMillis() % 1200) / 1200.0;
        circle(g, alpha(colour, held ? 60 : 22), cx, cy, inner + s * 0.02f);
        circle(g, Theme.PANEL_DARK, cx, cy, inner - s * 0.01f);
        g.setStroke(new BasicStroke(segW, BasicStroke.CAP_ROUND, BasicStroke.JOIN_ROUND));
        float sweep = 360f / SEGMENTS, gap = (float) (360 * (segW * 1.15f) / (2 * Math.PI * outer));
        for (int i = 0; i < SEGMENTS; i++) {
            int a;
            if (!animate) a = off ? 90 : 220;
            else if (colour.equals(Theme.RED)) a = (int) (150 + 105 * Math.abs(Math.sin(Math.PI * phase * 2)));    // pulse
            else {                                                                                                  // sweep
                double pos = ((double) i / SEGMENTS - phase + 1) % 1.0;
                a = (int) (60 + 195 * Math.pow(1 - pos, 3));
            }
            g.setColor(alpha(colour, a));
            g.draw(new Arc2D.Float(cx - outer, cy - outer, outer * 2, outer * 2, 90 - i * sweep - gap / 2,
                    -Math.max(1, sweep - gap), Arc2D.OPEN));
        }
        g.setStroke(new BasicStroke(Math.max(2, s * 0.014f)));
        g.setColor(alpha(colour, off ? 120 : 255));
        g.draw(new Ellipse2D.Float(cx - inner, cy - inner, inner * 2, inner * 2));
        g.setColor(off ? Theme.SUB : colour);
        g.setFont(new Font(Font.SANS_SERIF, Font.BOLD, Math.round(inner * 0.5f)));
        centred(g, "PTT", cx, cy + inner * 0.12f);
        g.setColor(Theme.SUB);
        Font small = new Font(Font.MONOSPACED, Font.PLAIN, Math.max(9, Math.round(inner * 0.13f)));
        g.setFont(small);
        int w = g.getFontMetrics().stringWidth(state);                 // keep the state line inside the ring
        if (w > inner * 1.5f) g.setFont(small.deriveFont(small.getSize2D() * inner * 1.5f / w));
        centred(g, state, cx, cy + inner * 0.45f);
        g.dispose();
    }

    private static void circle(Graphics2D g, Color c, float cx, float cy, float r) {
        g.setColor(c);
        g.fill(new Ellipse2D.Float(cx - r, cy - r, r * 2, r * 2));
    }

    private static void centred(Graphics2D g, String text, float cx, float baseline) {
        FontMetrics m = g.getFontMetrics();
        g.drawString(text, cx - m.stringWidth(text) / 2f, baseline);
    }
}
