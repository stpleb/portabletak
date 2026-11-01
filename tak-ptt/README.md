# TAK PTT

Push-to-talk voice for ATAK over Mumble, made for PortableTAK. Nothing to set up: TAK PTT uses the TAK
server ATAK is connected to and signs in with the TAK certificate ATAK enrolled, so there are no extra
accounts or passwords.

Plan: `TAK-PTT-Plugin-Plan.md` (kept with the user). Test results per phase: `TESTLOG.md`.

## What it does

- **Talk**: hold the PTT ring to talk, or tap to start and tap to stop. A talk-permit beep plays once the
  audio is ready; a time limit (20 to 90 s, warning chirp 5 s before) ends long transmissions.
- **Channels**: the server's channels as a list. Tap a name to move there, make new ones, and per channel:
  **RX** to listen without joining, **TX** to talk on several channels at once.
- **History**: every transmission of the last 7 days, with who, when, how long and where it was sent from.
  Play, pause, scrub, play all unheard; live traffic always pauses playback; clips are cached for offline
  replay; optional markers on your own map.
- **With ATAK in the background**: a floating PTT button over other apps, and the volume key as PTT.
- **Phone behavior**: traffic runs as short Android Telecom calls (music pauses and comes back, cellular calls
  put TAK PTT on hold), on the loudspeaker unless a headset is connected, with the phone's echo cancelling.
- **Full duplex**: several people can talk at once.

## How it is built

| Part | Where | What |
|---|---|---|
| Plugin screen | `plugin/` (`TakPttPlugin`, in ATAK's process) | PTT, CHANNELS and HISTORY tabs, settings; reads the TAK connection and certificate (`TakVoice`) |
| Voice service | `plugin/` (`VoiceService`, the plugin's own process) | Humla Mumble client as a foreground service (microphone), PTT logic, tones, Telecom calls (`PttCalls`), floating button, key service |
| Humla | `third_party/humla` + `humla-takptt.patch` | Mumble client library (GPL-3.0); patched for loading its codecs, audio mode, and Mumble 1.4 channel listening |
| Sign-in check | `server/murmur/takptt_auth.py` | Murmur Ice authenticator: TAK certificates from the server's CA with an active account, or PortableTAK passwords |
| Recorder | `server/recorder/takptt_recorder.py` | Listen-only Mumble client that saves each transmission as an Ogg Opus clip, plus the clip API |

ATAK's own foreground service is location-only and ATAK cannot draw over other apps, so the voice engine
runs in the plugin's own process; ATAK's side talks to it over a Messenger, and only ATAK may use it.

On PortableTAK the server parts ship in the node package (`chroot/voice/`, `share/murmur/`) and run under
`bin/portabletak-voice`, which starts and stops with PortableTAK. Voice history is served at
`https://<server>:8443/voice/` to phones with a PortableTAK certificate.

## Building

1. ATAK SDK (ATAK-MIL 5.8.0.8) and JDK 17+; `plugin/local.properties` points at them (`sdk.dir`, `sdk.path`,
   `takdev.plugin`).
2. Humla: `third_party/humla` is Humla at the commit in `third_party/humla-commit.txt` with
   `third_party/humla-takptt.patch` applied. Build it in the `quite/android-sdk-ndk:10` image
   (`./gradlew assembleRelease`) and copy `build/outputs/aar/src-release.aar` to `plugin/app/libs/humla.aar`.
3. `cd plugin && ./gradlew assembleCivDebug` (development builds run on the developer build of ATAK; release
   builds go through TAK signing).

## Testing

`tools/voicebot.py` (Docker image `takptt-voicebot`) is a second Mumble user: it records what it hears and plays
a tone every few seconds (`QUIET=1` records only, `CHANNEL=` joins a channel, `CERT`/`KEY` sign in with a
certificate). `server/murmur/Dockerfile.test` runs Murmur with the sign-in check for local tests.

## License

GPL-3.0 (Humla is GPL-3.0).
