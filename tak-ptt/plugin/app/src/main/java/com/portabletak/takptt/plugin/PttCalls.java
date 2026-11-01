package com.portabletak.takptt.plugin;

import android.content.ComponentName;
import android.content.Context;
import android.net.Uri;
import android.os.Bundle;
import android.telecom.CallAudioState;
import android.telecom.Connection;
import android.telecom.ConnectionRequest;
import android.telecom.ConnectionService;
import android.telecom.DisconnectCause;
import android.telecom.PhoneAccount;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;
import android.util.Log;

/**
 * Radio traffic as short self-managed calls through Android's Telecom service, so Android treats
 * talking and listening like a call: music pauses and comes back after, the audio switches to
 * the communication mode (with the phone's echo cancelling), and a cellular call puts TAK PTT on
 * hold instead of mixing with it. A call starts with the first transmission or reception and
 * ends once the channel has been quiet for a few seconds (the warm window).
 */
final class PttCalls {
    interface Listener {
        /** Android put TAK PTT on hold or ended its call (a cellular call, usually). */
        void onCallInterrupted();

        void onCallResumed();

        /** The call is up (call audio and echo cancelling can start) or over. */
        void onCallAudio(boolean inCall);
    }

    private static final String TAG = "TakPttVoice";
    static volatile PttCalls instance;
    /** Where call audio goes: Proto.ROUTE_AUTO, ROUTE_SPEAKER or ROUTE_EARPIECE. */
    static volatile String route = Proto.ROUTE_AUTO;

    private final Context ctx;
    private final TelecomManager telecom;
    private final PhoneAccountHandle handle;
    private final Listener listener;
    private Call call;
    private boolean placing;

    PttCalls(Context ctx, Listener listener) {
        this.ctx = ctx;
        this.listener = listener;
        this.telecom = ctx.getSystemService(TelecomManager.class);
        this.handle = new PhoneAccountHandle(new ComponentName(ctx, Service.class), "takptt");
        telecom.registerPhoneAccount(PhoneAccount.builder(handle, "TAK PTT")
                .setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED)
                .addSupportedUriScheme("takptt")
                .build());
        instance = this;
    }

    /** True while another app's call (a cellular call) is going on. */
    boolean otherCallActive() {
        try {
            if (!telecom.isInCall())
                return false;
            return call == null || call.getState() == Connection.STATE_HOLDING;
        } catch (SecurityException e) {
            return false;
        }
    }

    boolean active() {
        return call != null;
    }

    /** Start the call for a burst of traffic, unless one is up or on its way. */
    void begin() {
        if (call != null || placing)
            return;
        if (!telecom.isOutgoingCallPermitted(handle)) {
            Log.w(TAG, "Telecom: no call allowed now");
            return;
        }
        placing = true;
        Bundle extras = new Bundle();
        extras.putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, handle);
        try {
            telecom.placeCall(Uri.fromParts("takptt", "voice", null), extras);
        } catch (SecurityException e) {
            placing = false;
            Log.w(TAG, "Telecom: " + e);
        }
    }

    /** End the call (the channel has been quiet for the warm window). */
    void end() {
        placing = false;
        if (call != null) {
            Call c = call;
            call = null;
            c.setDisconnected(new DisconnectCause(DisconnectCause.LOCAL));
            c.destroy();
            listener.onCallAudio(false);
        }
    }

    /** The route setting changed: apply it to a call that is up. */
    void applyRoute() {
        Call c = call;
        if (c != null && c.getCallAudioState() != null) {
            int want = c.wanted(c.getCallAudioState());
            if (want != c.getCallAudioState().getRoute())
                c.setAudioRoute(want);
        }
    }

    void unregister() {
        end();
        telecom.unregisterPhoneAccount(handle);
        instance = null;
    }

    private void attached(Call c) {
        placing = false;
        call = c;
    }

    private void routed(Call c) {
        if (call == c && !c.routed) {
            c.routed = true;
            listener.onCallAudio(true);
        }
    }

    private void lost(Call c, boolean hold) {
        if (call != c)
            return;
        if (!hold) {
            call = null;
            listener.onCallAudio(false);
        }
        listener.onCallInterrupted();
    }

    /** One stretch of radio traffic. */
    static final class Call extends Connection {
        private final PttCalls owner;
        boolean routed;

        Call(PttCalls owner) {
            this.owner = owner;
            setConnectionProperties(PROPERTY_SELF_MANAGED);
            setConnectionCapabilities(CAPABILITY_SUPPORT_HOLD | CAPABILITY_HOLD);
            setAudioModeIsVoip(true);
            setAddress(Uri.fromParts("takptt", "voice", null), TelecomManager.PRESENTATION_ALLOWED);
            setCallerDisplayName("TAK PTT", TelecomManager.PRESENTATION_ALLOWED);
        }

        /**
         * Automatic: the loudspeaker like a radio, unless a headset or Bluetooth audio is there.
         * Or always the loudspeaker, or always the earpiece (settings).
         */
        @Override public void onCallAudioStateChanged(CallAudioState state) {
            int want = wanted(state);
            if (want != state.getRoute() && (state.getSupportedRouteMask() & want) != 0)
                setAudioRoute(want);
            else
                owner.routed(this);                 // on its final route: start call audio
        }

        int wanted(CallAudioState state) {
            if (Proto.ROUTE_SPEAKER.equals(route)) return CallAudioState.ROUTE_SPEAKER;
            if (Proto.ROUTE_EARPIECE.equals(route)) return CallAudioState.ROUTE_EARPIECE;
            int headset = CallAudioState.ROUTE_BLUETOOTH | CallAudioState.ROUTE_WIRED_HEADSET;
            if ((state.getSupportedRouteMask() & headset) != 0)
                return state.getRoute() == CallAudioState.ROUTE_EARPIECE || state.getRoute() == CallAudioState.ROUTE_SPEAKER
                        ? ((state.getSupportedRouteMask() & CallAudioState.ROUTE_BLUETOOTH) != 0
                                ? CallAudioState.ROUTE_BLUETOOTH : CallAudioState.ROUTE_WIRED_HEADSET)
                        : state.getRoute();
            return CallAudioState.ROUTE_SPEAKER;
        }

        @Override public void onHold() {
            setOnHold();
            owner.lost(this, true);
        }

        @Override public void onUnhold() {
            setActive();
            owner.listener.onCallResumed();
        }

        @Override public void onDisconnect() {
            setDisconnected(new DisconnectCause(DisconnectCause.LOCAL));
            destroy();
            owner.lost(this, false);
        }

        @Override public void onAbort() {
            onDisconnect();
        }
    }

    /** Telecom's side: hands out the calls PttCalls asks for. */
    public static final class Service extends ConnectionService {
        @Override
        public Connection onCreateOutgoingConnection(PhoneAccountHandle account, ConnectionRequest request) {
            PttCalls owner = instance;
            if (owner == null)
                return Connection.createFailedConnection(new DisconnectCause(DisconnectCause.ERROR));
            Call c = new Call(owner);
            c.setActive();
            owner.attached(c);
            Log.i(TAG, "Telecom: call on");
            return c;
        }

        @Override
        public void onCreateOutgoingConnectionFailed(PhoneAccountHandle account, ConnectionRequest request) {
            PttCalls owner = instance;
            if (owner != null)
                owner.placing = false;
            Log.w(TAG, "Telecom: call refused");
        }
    }
}
