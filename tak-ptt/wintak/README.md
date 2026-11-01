# TAK PTT for WinTAK

Push-to-talk voice inside WinTAK, on the same Mumble voice server as TAK PTT on ATAK. Nothing to set up:
it uses the TAK server WinTAK is connected to and signs in with the TAK certificate WinTAK enrolled there.

## Install

In WinTAK, open the `.wpk` from `dist/` (double-click it, or use the Plugin Manager). Then press **TAK PTT**
on the Plugins ribbon. Voice starts with WinTAK, so traffic is heard even with the pane closed.

Before a release, build on Windows from a clean tree: `dotnet build -c Release Plugin\TakPtt.WinTak.csproj`.
That also copies the plugin into `%appdata%\wintak\plugins\TakPtt` for testing.

## What it does

The pane has three tabs. **PTT** has the ring and settings, **CHANNELS** the channel list, **HISTORY** the clips.

- **Talk**: hold the ring with the mouse, or hold Space while the pane has focus. Or tap on, tap off.
- **Tones and limit**: talk-permit tone, then a time limit of 20 to 90 s with a warning chirp 5 s before.
- **Channels**: the server's channels. Click a name to move there, make new ones, and per channel use **RX**
  to listen without joining and **TX** to talk on several channels at once. Picks are kept by channel name.
- **History**: every transmission the recorder kept, newest first, with who, channel, when and how long, and a
  dot on unheard ones. Click to play, pause, scrub, or PLAY UNHEARD for oldest first. Live traffic pauses playback
  and it goes on after. Clips are kept in `%localappdata%\TakPtt\clips` for offline replay.
- **Recent and badge**: the last five transmissions sit at the bottom of the PTT tab (click one to play it), and a red
  number on the HISTORY tab counts unheard ones, not counting your own. They update every 20 s and just after traffic.
- **Map**: MAP ON shows clips that have a location as markers on this computer's map only. Click one to play it.
  WinTAK sends its own position to the recorder at key-up, including a location set by hand.
- **Text**: TEXT ON shows what was said under each clip, worked out on this computer with Whisper (`base.en`,
  English, offline, nothing leaves the computer). Clips are done one at a time in the background. If Whisper cannot
  run, Windows' own speech recognizer takes over.
- **Clear**: CLEAR removes the list and saved clips from this computer only. The server keeps them for everyone else.
- **Full duplex**: several people can talk at once.
- **One device per user**: Murmur allows one session per TAK user. If this user signs in on a phone, WinTAK
  stops and says so instead of fighting for the session.

Use a headset: on the desktop there is no echo cancelling, so speakers feed back into the microphone.

## Not in this version

These are in the ATAK plugin but not here yet: mesh failover, OpenMANET talk groups, a global
hotkey (Space only works while the pane has focus), choosing a sound device (it uses the Windows defaults),
Bluetooth PTT hardware, SOS, and sending your position to the recorder (history shows no place for WinTAK users).

Voice runs over TCP through the TLS connection, as the phones do on Tailscale. It needs only port 64738/tcp,
but a poor link adds delay rather than dropping audio.

## Clearing history on the server

The recorder deletes clips after 7 days by itself. There is no button: this deletes every clip for everyone, cannot be
undone, and has to be run on the server, inside the chroot that holds `/asgard`. On a PortableTAK laptop, open
`wsl -d PortableTAK -u root`, run `chroot /proc/$(pgrep -f opentakserver_launch | head -1)/root sh`, then:

python3 -c "import sqlite3,os; d=sqlite3.connect('/asgard/opentak/voice/history.sqlite',timeout=30); f=[r[0] for r in d.execute('select file from clips')]; d.execute('delete from clips'); d.commit(); [os.path.exists(p) and os.remove(p) for p in f]; print(len(f),'clips cleared')"

## How it is built

| Part | Where | What |
|---|---|---|
| Voice | `Voice/` | Mumble client, Opus, mixer, tones, engine. No WinTAK or Windows code, so it also runs in the test |
| Plugin | `Plugin/` | MEF module, ribbon button, dock pane, WPF view (in code, no XAML), Windows audio through NAudio |
| Whisper | `TakPtt.Whisper/` | Speech to text as a separate program the plugin starts per clip, so a crash cannot reach WinTAK and its libraries cannot clash with WinTAK's. In `whisper/` in the plugin folder, with the 148 MB model |
| Test | `TakPtt.E2E/` | The `Voice/` code on .NET 8 against a real Murmur, with a made-up microphone and speaker |

Where the TAK connection comes from: `CoTSettings.ServerConnections` (exported as `INetworkPreference`) gives
the host and certificate paths. The documented `IStreamingPreferences` is internal to WinTAK. An enrolled
certificate's password is one WinTAK made itself: it is held encrypted in a file beside the certificate and read
with `EncryptionUtils.UnprotectPasswordFromFile`. After that it tries TAK's usual `atakatak`, then none.

## Building

The plugin needs WinTAK's DLLs, and nothing else from the SDK.

- **Windows**: install WinTAK, then `dotnet build -c Release Plugin\TakPtt.WinTak.csproj` (or open it in
  Visual Studio). `-p:WinTakDir=...` if WinTAK is not in `C:\Program Files\WinTAK`.
- **Mac or Linux**: unpack the DLLs from the WinTAK `.msi`, then `tools/build-wpk.sh <dll folder>`.

`tools/build-wpk.sh` makes the `.wpk`. Keep `<sdkVersion>` there and `TakSdkVersion` in
`Plugin/AssemblyInfo.cs` matched to your WinTAK (5.8.0.146, the same as the plugins that came with it).

## Testing

`tools/run-e2e.sh` runs the voice code against Murmur with the sign-in check and `tools/voicebot.py` as a
second user. `tools/run-history-e2e.sh` runs the history code against the real recorder. `tools/run-whisper-test.sh` puts real speech through Whisper (Mac). See `../TESTLOG.md` for results and what still needs a Windows computer.

## License

Concentus and NAudio keep their own licenses. Unlike the ATAK plugin this does not use Humla.
