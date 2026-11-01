package com.portabletak.takptt.takx;

import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Component;
import java.awt.Cursor;
import java.awt.Dimension;
import java.awt.FlowLayout;
import java.awt.Font;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.awt.event.ActionEvent;
import java.awt.event.KeyEvent;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.util.List;
import java.util.stream.Collectors;

import javax.swing.AbstractAction;
import javax.swing.BorderFactory;
import javax.swing.Box;
import javax.swing.BoxLayout;
import javax.swing.JCheckBox;
import javax.swing.JComboBox;
import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JSpinner;
import javax.swing.KeyStroke;
import javax.swing.ScrollPaneConstants;
import javax.swing.SpinnerNumberModel;
import javax.swing.SwingConstants;
import javax.swing.Timer;

/**
 * The TAK PTT panel: status, the PTT ring (mouse, or Space while the panel has focus), the channel
 * list with RX and TX picks, and the settings.
 */
final class TakPttPanel extends JPanel {
    private static final Font MONO = new Font(Font.MONOSPACED, Font.BOLD, 11);
    private static final String HOLD = "Hold to talk", TAP = "Tap on, tap off";

    private final JLabel status = new JLabel(" ", SwingConstants.CENTER), detail = new JLabel(" ", SwingConstants.CENTER);
    private final PttRing ring = new PttRing();
    private final JPanel channels = new JPanel();
    private final JComboBox<String> mode = new JComboBox<>(new String[]{HOLD, TAP});
    private final JSpinner timeout = new JSpinner(new SpinnerNumberModel(60, 20, 90, 5));
    private final JCheckBox tones = new JCheckBox("Tones"), rejoin = new JCheckBox("Go back to my last channel");
    private final Timer spaceUp;
    private VoiceEngine engine;
    private String shownRows = "";
    private boolean updating, spaceHeld;

    TakPttPanel() {
        super(new BorderLayout(0, 6));
        setBackground(Theme.BG);
        setBorder(BorderFactory.createEmptyBorder(8, 8, 8, 8));
        // Linux repeats a held key as release + press pairs: a release only counts if no press follows.
        spaceUp = new Timer(40, e -> {
            spaceHeld = false;
            if (engine != null) engine.pttUp();
        });
        spaceUp.setRepeats(false);

        JPanel top = new JPanel();
        top.setOpaque(false);
        top.setLayout(new BoxLayout(top, BoxLayout.Y_AXIS));
        for (JLabel l : new JLabel[]{status, detail}) {
            l.setAlignmentX(Component.CENTER_ALIGNMENT);
            top.add(l);
        }
        status.setFont(MONO);
        status.setForeground(Theme.TEXT);
        detail.setFont(MONO.deriveFont(Font.PLAIN));
        detail.setForeground(Theme.SUB);
        ring.setAlignmentX(Component.CENTER_ALIGNMENT);
        top.add(Box.createVerticalStrut(6));
        top.add(ring);
        add(top, BorderLayout.NORTH);

        channels.setBackground(Theme.BG);
        channels.setLayout(new BoxLayout(channels, BoxLayout.Y_AXIS));
        JPanel pad = new JPanel(new BorderLayout());
        pad.setBackground(Theme.BG);
        pad.add(channels, BorderLayout.NORTH);
        JScrollPane scroll = new JScrollPane(pad, ScrollPaneConstants.VERTICAL_SCROLLBAR_AS_NEEDED,
                ScrollPaneConstants.HORIZONTAL_SCROLLBAR_NEVER);
        scroll.setBorder(BorderFactory.createEmptyBorder());
        scroll.getVerticalScrollBar().setUnitIncrement(16);
        add(scroll, BorderLayout.CENTER);

        JPanel settings = new JPanel(new GridBagLayout());
        settings.setOpaque(false);
        GridBagConstraints c = new GridBagConstraints();
        c.insets = new Insets(2, 2, 2, 2);
        c.anchor = GridBagConstraints.WEST;
        c.gridx = 0;
        c.gridy = 0;
        settings.add(label("Talk"), c);
        c.gridx = 1;
        settings.add(mode, c);
        c.gridx = 0;
        c.gridy = 1;
        settings.add(label("Time limit (s)"), c);
        c.gridx = 1;
        settings.add(timeout, c);
        c.gridx = 0;
        c.gridy = 2;
        c.gridwidth = 2;
        for (JCheckBox b : new JCheckBox[]{tones, rejoin}) {
            b.setOpaque(false);
            b.setForeground(Theme.TEXT);
        }
        settings.add(tones, c);
        c.gridy = 3;
        settings.add(rejoin, c);
        add(settings, BorderLayout.SOUTH);
        spaceKeys();
    }

    private static JLabel label(String text) {
        JLabel l = new JLabel(text);
        l.setForeground(Theme.SUB);
        return l;
    }

    private void spaceKeys() {
        getInputMap(WHEN_ANCESTOR_OF_FOCUSED_COMPONENT).put(KeyStroke.getKeyStroke(KeyEvent.VK_SPACE, 0, false), "ptt-down");
        getInputMap(WHEN_ANCESTOR_OF_FOCUSED_COMPONENT).put(KeyStroke.getKeyStroke(KeyEvent.VK_SPACE, 0, true), "ptt-up");
        getActionMap().put("ptt-down", new AbstractAction() {
            @Override public void actionPerformed(ActionEvent e) {
                spaceUp.stop();
                if (spaceHeld || engine == null) return;
                spaceHeld = true;
                engine.pttDown();
            }
        });
        getActionMap().put("ptt-up", new AbstractAction() {
            @Override public void actionPerformed(ActionEvent e) { spaceUp.restart(); }
        });
    }

    void attach(VoiceEngine engine) {
        this.engine = engine;
        ring.setPress(new PttRing.Press() {
            @Override public void down() { engine.pttDown(); }
            @Override public void up() { engine.pttUp(); }
        });
        mode.addActionListener(e -> { if (!updating) engine.setMode(mode.getSelectedItem() == TAP ? VoiceEngine.MODE_LATCH : VoiceEngine.MODE_HOLD); });
        timeout.addChangeListener(e -> { if (!updating) engine.setTimeout((Integer) timeout.getValue()); });
        tones.addActionListener(e -> engine.setTones(tones.isSelected()));
        rejoin.addActionListener(e -> engine.setRejoin(rejoin.isSelected()));
    }

    /** On the event thread. */
    void show(VoiceEngine.State s) {
        if (s.talking) ring.show(Theme.RED, "TRANSMITTING", true);
        else if (s.receiving != null) ring.show(Theme.AMBER, s.receiving, true);
        else if (s.connected) ring.show(Theme.GREEN, VoiceEngine.MODE_LATCH.equals(s.mode) ? "TAP TO TALK" : "HOLD TO TALK", false);
        else ring.show(Theme.MUTED, s.connecting ? "CONNECTING" : "OFFLINE", false);
        status.setText(s.connected ? s.channel + " · " + s.serverName : s.connecting ? "Connecting to " + s.serverName : "Voice off");
        String line = s.note != null ? s.note : s.error;
        if (line != null) detail.setText("<html><div style='text-align:center'>" + line.replace("&", "&amp;").replace("<", "&lt;") + "</div></html>");
        else if (s.connected) detail.setText(" ");
        updating = true;
        mode.setSelectedItem(VoiceEngine.MODE_LATCH.equals(s.mode) ? TAP : HOLD);
        timeout.setValue(s.timeout);
        tones.setSelected(s.tones);
        rejoin.setSelected(s.rejoin);
        updating = false;
        String key = s.rows.stream().map(VoiceEngine.Row::key).collect(Collectors.joining("\n"));
        if (!key.equals(shownRows)) {
            shownRows = key;
            rows(s.rows, s.connected);
        }
    }

    private void rows(List<VoiceEngine.Row> list, boolean connected) {
        channels.removeAll();
        for (VoiceEngine.Row r : list) {
            JPanel row = new JPanel(new BorderLayout(6, 0));
            row.setBackground(r.here ? new Color(0x0E2015) : Theme.PANEL);
            row.setBorder(BorderFactory.createCompoundBorder(
                    BorderFactory.createEmptyBorder(0, 0, 4, 0),
                    BorderFactory.createCompoundBorder(BorderFactory.createLineBorder(r.here ? Theme.GREEN : Theme.LINE),
                            BorderFactory.createEmptyBorder(4, 8, 4, 4))));
            JLabel name = new JLabel("<html><b>" + (r.active ? "<font color='#FFB84D'>● </font>" : "")
                    + r.name.replace("&", "&amp;").replace("<", "&lt;") + "</b><br><font size='2' color='#8FA79A'>"
                    + (r.here ? "HERE · " : "") + r.people + (r.people == 1 ? " person" : " people") + "</font></html>");
            name.setForeground(r.here ? Theme.GREEN : Theme.TEXT);
            name.setCursor(Cursor.getPredefinedCursor(Cursor.HAND_CURSOR));
            name.addMouseListener(new MouseAdapter() {
                @Override public void mouseClicked(MouseEvent e) { engine.join(r.id); }
            });
            row.add(name, BorderLayout.CENTER);
            JPanel picks = new JPanel(new FlowLayout(FlowLayout.RIGHT, 4, 0));
            picks.setOpaque(false);
            picks.add(toggle("RX", r.rx, r.here, r.active && r.rx ? Theme.AMBER : Theme.GREEN, () -> engine.setRx(r.id, !r.rx)));
            picks.add(toggle("TX", r.tx, false, Theme.RED, () -> engine.setTx(r.id, !r.tx)));
            row.add(picks, BorderLayout.EAST);
            row.setMaximumSize(new Dimension(Integer.MAX_VALUE, row.getPreferredSize().height));
            row.setAlignmentX(Component.LEFT_ALIGNMENT);
            channels.add(row);
        }
        if (connected) {
            JLabel add = new JLabel("+ NEW CHANNEL", SwingConstants.CENTER);
            add.setFont(MONO);
            add.setForeground(Theme.GREEN);
            add.setBorder(BorderFactory.createCompoundBorder(BorderFactory.createLineBorder(Theme.LINE),
                    BorderFactory.createEmptyBorder(6, 8, 6, 8)));
            add.setCursor(Cursor.getPredefinedCursor(Cursor.HAND_CURSOR));
            add.setAlignmentX(Component.LEFT_ALIGNMENT);
            add.setMaximumSize(new Dimension(Integer.MAX_VALUE, add.getPreferredSize().height));
            add.addMouseListener(new MouseAdapter() {
                @Override public void mouseClicked(MouseEvent e) {
                    String name = JOptionPane.showInputDialog(TakPttPanel.this, "Channel name", "New channel", JOptionPane.PLAIN_MESSAGE);
                    if (name != null && !name.trim().isEmpty()) engine.create(name.trim());
                }
            });
            channels.add(add);
        }
        channels.revalidate();
        channels.repaint();
    }

    private static JComponent toggle(String text, boolean on, boolean locked, Color colour, Runnable click) {
        JLabel t = new JLabel(text, SwingConstants.CENTER);
        t.setFont(MONO);
        t.setForeground(on ? colour : Theme.MUTED);
        t.setBorder(BorderFactory.createCompoundBorder(BorderFactory.createLineBorder(on ? colour : Theme.LINE),
                BorderFactory.createEmptyBorder(4, 8, 4, 8)));
        if (!locked) {                                  // the channel you are in is always heard
            t.setCursor(Cursor.getPredefinedCursor(Cursor.HAND_CURSOR));
            t.addMouseListener(new MouseAdapter() {
                @Override public void mouseClicked(MouseEvent e) { click.run(); }
            });
        }
        return t;
    }
}
