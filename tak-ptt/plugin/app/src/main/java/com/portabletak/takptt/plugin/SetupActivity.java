package com.portabletak.takptt.plugin;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;

import java.util.ArrayList;
import java.util.List;

/**
 * A see-through screen that asks the user for what the voice service needs: the microphone
 * (and notifications), drawing the floating PTT button over other apps, or the accessibility
 * setting for PTT keys. Opened from the plugin's screen in ATAK.
 */
public class SetupActivity extends Activity {
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        String ask = getIntent().getStringExtra(Proto.ASK);
        if (Proto.ASK_OVERLAY.equals(ask)) {
            startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName())));
            finish();
        } else if (Proto.ASK_KEYS.equals(ask)) {
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            finish();
        } else {
            List<String> perms = new ArrayList<>();
            perms.add(Manifest.permission.RECORD_AUDIO);
            if (Build.VERSION.SDK_INT >= 33)
                perms.add(Manifest.permission.POST_NOTIFICATIONS);
            requestPermissions(perms.toArray(new String[0]), 1);
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        // While this screen is showing, the voice service may also start its foreground service.
        VoiceService voice = VoiceService.instance;
        if (voice != null)
            voice.permissionsChanged();
        finish();
    }
}
