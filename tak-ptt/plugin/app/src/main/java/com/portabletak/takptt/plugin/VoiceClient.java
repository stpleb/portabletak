package com.portabletak.takptt.plugin;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.os.RemoteException;
import android.util.Log;

import java.util.ArrayList;
import java.util.List;

/**
 * ATAK's side of the voice service: binds to VoiceService in this plugin's own process, sends it
 * commands and passes its state updates to the plugin's screen.
 */
final class VoiceClient {
    interface Listener {
        void onState(Bundle state);
    }

    private static final String TAG = "TakPtt";
    private final Context ctx;
    private final Listener listener;
    private final Messenger inbox;
    private final List<Message> queued = new ArrayList<>();
    private Messenger service;
    private boolean bound;

    VoiceClient(Context atak, Listener listener) {
        this.ctx = atak;
        this.listener = listener;
        this.inbox = new Messenger(new Handler(Looper.getMainLooper(), m -> {
            if (m.what == Proto.STATE)
                listener.onState(m.getData());
            return true;
        }));
    }

    private final ServiceConnection conn = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            service = new Messenger(binder);
            Log.i(TAG, "voice service bound");
            send(Proto.REGISTER, 0, null);
            for (Message m : queued)
                deliver(m);
            queued.clear();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            // The voice process stopped; Android starts it again and calls onServiceConnected.
            service = null;
            Log.w(TAG, "voice service lost");
        }
    };

    void bind() {
        if (bound)
            return;
        Intent i = new Intent(Proto.BIND).setComponent(new ComponentName(Proto.PACKAGE, Proto.SERVICE));
        int flags = Context.BIND_AUTO_CREATE;
        if (Build.VERSION.SDK_INT >= 29)
            flags |= Context.BIND_INCLUDE_CAPABILITIES;     // the service may use the mic while ATAK is open
        bound = ctx.bindService(i, conn, flags);
        if (!bound)
            Log.e(TAG, "could not bind the voice service");
    }

    void unbind() {
        if (!bound)
            return;
        send(Proto.UNREGISTER, 0, null);
        ctx.unbindService(conn);
        bound = false;
        service = null;
    }

    void send(int what, int arg1, Bundle data) {
        send(what, arg1, 0, data);
    }

    void send(int what, int arg1, int arg2, Bundle data) {
        Message m = Message.obtain(null, what, arg1, arg2);
        m.replyTo = inbox;
        if (data != null)
            m.setData(data);
        if (service == null) {
            if (what != Proto.REGISTER && what != Proto.UNREGISTER)
                queued.add(m);
            return;
        }
        deliver(m);
    }

    private void deliver(Message m) {
        try {
            service.send(m);
        } catch (RemoteException e) {
            Log.w(TAG, "voice service send", e);
        }
    }

    /** Ask the user for a permission the voice service needs (SetupActivity). */
    void ask(String what) {
        Intent i = new Intent().setComponent(new ComponentName(Proto.PACKAGE, Proto.SETUP))
                .putExtra(Proto.ASK, what)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_HISTORY);
        ctx.startActivity(i);
    }
}
