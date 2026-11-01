package com.portabletak.takptt.plugin;

/**
 * Messages between the plugin's screen in ATAK (VoiceClient, in ATAK's process) and the voice
 * service (VoiceService, in this plugin's own process), plus the keys used in their Bundles.
 */
final class Proto {
    private Proto() {
    }

    static final String PACKAGE = "com.portabletak.takptt.plugin";
    static final String SERVICE = PACKAGE + ".VoiceService";
    static final String SETUP = PACKAGE + ".SetupActivity";
    static final String BIND = PACKAGE + ".VOICE";      // bind action for VoiceService

    // ATAK -> service
    static final int REGISTER = 1;          // replyTo gets STATE messages
    static final int UNREGISTER = 2;
    static final int CONNECT = 3;           // data: connection keys below
    static final int DISCONNECT = 4;
    static final int PTT_DOWN = 5;
    static final int PTT_UP = 6;
    static final int JOIN = 7;              // arg1: channel id
    static final int CREATE = 8;            // data: NAME, TEMPORARY
    static final int SETTINGS = 9;          // data: settings keys below
    static final int SET_RX = 10;           // arg1: channel id, arg2: 1 listen / 0 stop
    static final int SET_TX = 11;           // arg1: channel id, arg2: 1 talk on it / 0 not
    static final int POSITION = 12;         // data: LAT, LON (this phone, from ATAK)
    static final int MESH_KEYS = 13;        // data: MESH_JSON (the voice server's /voice/keys answer)
    static final int SET_OM_RX = 14;        // arg1: OpenMANET talk group (1 to 5), arg2: 1 listen / 0 stop
    static final int SET_OM_TX = 15;        // arg1: OpenMANET talk group, arg2: 1 talk on it / 0 not

    // service -> ATAK
    static final int STATE = 100;

    // CONNECT
    static final String HOST = "host";
    static final String PORT = "port";
    static final String CALLSIGN = "callsign";
    static final String SERVER_NAME = "serverName";
    static final String USER = "user";              // TAK user name (certificate), or null
    static final String FROM_TAK = "fromTak";
    static final String CERT = "cert";              // PKCS12 bytes, or none for a test identity
    static final String CERT_PASSWORD = "certPassword";
    static final String TRUSTED = "trusted";        // ArrayList<byte[]> of DER CA certificates

    // MESH_KEYS
    static final String MESH_JSON = "meshJson";
    static final String MESH = "mesh";                  // setting: mesh failover on (default) / off; state: mesh active
    static final String MESH_LOOP = "meshLoop";         // testing: hear yourself on the mesh

    // POSITION
    static final String LAT = "lat";
    static final String LON = "lon";

    /** The voice history recorder's user id on the PortableTAK voice server (hidden in lists). */
    static final int RECORDER_ID = 99999;

    // CREATE
    static final String NAME = "name";
    static final String TEMPORARY = "temporary";

    // SETTINGS (also kept by the service)
    static final String MODE = "mode";              // MODE_HOLD or MODE_LATCH
    static final String MODE_HOLD = "hold";
    static final String MODE_LATCH = "latch";
    static final String TIMEOUT = "timeout";        // seconds of transmitting before cutoff, 20 to 90
    static final String FLOATING = "floating";      // on-screen PTT button over other apps
    static final String FLOAT_SIZE = "floatSize";   // its size in dp (FLOAT_SMALL, FLOAT_MEDIUM, FLOAT_LARGE)
    static final int FLOAT_SMALL = 68, FLOAT_MEDIUM = 88, FLOAT_LARGE = 112;
    static final String VOLUME_KEY = "volumeKey";   // KEY_OFF, KEY_DOWN or KEY_UP
    static final String KEY_OFF = "off";
    static final String KEY_DOWN = "down";
    static final String KEY_UP = "up";
    static final String TONES = "tones";
    static final String ROUTE = "route";            // ROUTE_AUTO, ROUTE_SPEAKER or ROUTE_EARPIECE
    static final String ROUTE_AUTO = "auto";        // a headset if one is connected, else the loudspeaker
    static final String ROUTE_SPEAKER = "speaker";
    static final String ROUTE_EARPIECE = "earpiece";
    static final String REJOIN = "rejoin";          // go back to the last channel when connecting
    static final String CLEAR_PICKS = "clearPicks"; // forget all RX and TX picks

    // STATE
    static final String CONNECTED = "connected";
    static final String CONNECTING = "connecting";
    static final String TALKING = "talking";
    static final String RECEIVING = "receiving";    // name of who is talking, or null
    static final String ERROR = "error";
    static final String CHANNEL = "channel";        // current channel name
    static final String CH_IDS = "chIds";
    static final String CH_NAMES = "chNames";
    static final String CH_USERS = "chUsers";
    static final String CH_CURRENT = "chCurrent";
    static final String CH_ACTIVE = "chActive";
    static final String CH_RX = "chRx";             // channel ids heard (the current one, plus listened)
    static final String CH_TX = "chTx";             // channel ids a transmission goes to
    static final String CH_NOTE = "chNote";         // a message to show once (channel made, refused...)
    static final String MIC_ALLOWED = "micAllowed";
    static final String OVERLAY_ALLOWED = "overlayAllowed";
    static final String KEYS_ALLOWED = "keysAllowed";
    static final String OM_RX = "omRx";             // OpenMANET talk groups heard
    static final String OM_TX = "omTx";             // OpenMANET talk groups a transmission goes to
    static final String OM_ACTIVE = "omActive";     // talk groups someone is talking on now
    static final String TRANSCRIPTS = "transcripts"; // setting and state: speech to text on this phone
    static final String OM_LOG = "omLog";           // talk-group transmissions heard here, as text:
                                                    // "millis|group|text", newest first

    // SetupActivity extra: what to ask the user for
    static final String ASK = "ask";
    static final String ASK_MIC = "mic";
    static final String ASK_OVERLAY = "overlay";
    static final String ASK_KEYS = "keys";
}
