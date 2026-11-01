# TAK PTT for TAKX

Push-to-talk voice inside TAKX, on the same Mumble voice server as TAK PTT on ATAK and WinTAK. TAKX plugins
are Java 17 and Swing, so this is a separate build from the WinTAK plugin, with the same design and behaviour.
See `../wintak/README.md` for what it does and what is not included yet.

## Building and installing

With TAKX installed, set the properties at the top of `build.gradle` for your install, then:

```bash
./gradlew install
```

`tools/build-offline.sh` builds the jar without TAKX, against `stubs/`: the few API signatures the plugin uses,
copied from the TAKX 5.8 Javadoc. Prefer the Gradle build, which uses the real API. Note that the Gradle wrapper
needs copying from the SDK's sample plugin (`takx-raven-plugin-offline`), and JDK 17.

## Testing

`tools/run-e2e.sh` runs the real engine against Murmur with the sign-in check and `tools/voicebot.py`, with a
made-up microphone and speaker.
