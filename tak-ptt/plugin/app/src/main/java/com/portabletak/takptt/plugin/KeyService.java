package com.portabletak.takptt.plugin;

import android.accessibilityservice.AccessibilityService;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;

/**
 * Hardware PTT keys (the volume key chosen in settings). Android gives key presses to apps in
 * the background only through an accessibility service, so this one only watches keys: it does
 * not read the screen. The user turns it on once in Android's accessibility settings.
 */
public class KeyService extends AccessibilityService {
    @Override
    protected boolean onKeyEvent(KeyEvent event) {
        VoiceService voice = VoiceService.instance;
        boolean used = voice != null && voice.onHardwareKey(event);
        if (event.getKeyCode() == KeyEvent.KEYCODE_VOLUME_DOWN || event.getKeyCode() == KeyEvent.KEYCODE_VOLUME_UP)
            android.util.Log.d("TakPttVoice", "key " + event.getKeyCode() + " action " + event.getAction() + (used ? " (PTT)" : ""));
        return used;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
    }

    @Override
    public void onInterrupt() {
    }
}
