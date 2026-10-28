# TAK PTT: what got done overnight (2026-10-28)

Code: `~/Developer/tak-ptt` (committed; nothing pushed anywhere). Test results per phase: `TESTLOG.md` there.
PortableTAK changes: in the work folder (node package payload, provision sources, guides); not released, not synced to GitHub.

## Built and tested on the S22 (against the PortableTAK test copy in Docker)

- **Voice history (phase 5).**
  - Every transmission is saved for 7 days, with who, channel, when, length and where from.
  - HISTORY tab:
    - list with unheard dots;
    - play, pause and scrub;
    - PLAY UNHEARD, in order;
    - playback pauses while someone is live and goes on after;
    - clips are kept on the phone for offline replay;
    - MAP shows markers on your own map.
- **Settings (phase 6).** Three sections:
  - TX and RX: hold or tap, time limit, tones, audio out (automatic, loudspeaker, earpiece);
  - Channels: go back to your last channel, clear RX/TX picks;
  - Preferences: floating button and size, volume key, history on the map, mesh failover.
- **Mesh failover (phase 7).**
  - If the voice server is unreachable for 6 s, TAK PTT talks over the local network (OpenMANET or Wi-Fi): encrypted per channel, with weekly keys from the server.
  - It goes back to the server by itself.
  - Tested on one phone, which hears its own packets. A second phone is still needed.
- **SOS.**
  - Hold 3 s under the PTT ring to send ATAK's 911 alert; tap to cancel.
  - The server logged both.
- **Fixes found while testing:**
  - reconnecting while connected;
  - a transmission lost while receiving;
  - keys wiped by a server restart;
  - the reconnect storm.

## Security review

An independent review found 9 problems. I fixed and retested 8 of them:
- The recorder API is on a Unix socket only nginx can reach.
- Callsigns can't be hijacked, and the recorder's name can't be taken.
- Murmur has its own certificate, so the TAK server key is no longer shared with it.
- Ice reads need the secret.
- Mesh keys change weekly, and replays are blocked.
- Clips are capped at 2 minutes and the recorder stops when the disk is low.
- Sign-in is cached so it can't be stalled.

Two bigger PortableTAK issues are waiting for your OK as session cards:
1. **OpenTAKServer enrollment signs a certificate for any name.** One user can get a certificate in another user's name, for TAK itself, not just voice.
2. **Tailscale exposes internal ports.** The drive's Tailscale (userspace mode) lets tailnet devices reach internal-only ports, including OpenTAKServer's 18081 and the users worker.

## Docs

- **Guide:** new "13. Voice (push to talk)" chapter (SOS and mesh included), voice troubleshooting rows, and a quick reference line. PDFs are rebuilt and copied to Start Here.
- **READMEs:**
  - PortableTAK README edited, not committed.
  - New TAK PTT README.

## Needs you

- **Physical tests:**
  - music pausing during traffic;
  - a phone call to the S22 while connected;
  - your Bluetooth headset, paired with the S22.
- **Second phone:** for mesh between two devices and real two-person voice.
- **Release:** a PortableTAK test release with the voice server. Node01 and Node02 need it too.
  - Node01 was offline overnight.
  - The Node02 update job saw Node02 come up at 2:44 but couldn't reach it because Node01 dropped.
- **PTT hardware:** none on hand, so none is claimed.

## The S22 right now

- **ATAK:** developer ATAK. Callsign BOMBER, test user pttalpha, connected to the Docker test copy over USB (adb reverse).
- **TAK PTT setup:** the testing override is set to `,18443`, a history port for this USB setup only. Clear it (long-press the pill) when the phone goes back to a real server.
- **Permissions:** microphone, Appear on top, and the TAK PTT buttons accessibility service are on. The volume-down key works as PTT.
- **To put back later:** callsign TITAN, your real server, production ATAK.
