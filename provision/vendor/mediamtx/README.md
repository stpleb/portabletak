# MediaMTX runtime supplement

PortableTAK uses MediaMTX 1.13.0 because OpenTAKServer's current installer and
Docker deployment pin that version. The ARM64 archive comes directly from the
official `bluenviron/mediamtx` GitHub release and is accepted only after its
SHA-256 digest matches the release checksum file.

The API listener remains on `127.0.0.1`. Lifecycle hooks call a Python helper so
the OpenTAKServer token is read from a mode-0600 file and never placed in process
arguments. The first-run backend writes that token file from the staged secrets.

Files copied from the upstream release:

- `mediamtx`
- `LICENSE`
- `checksums.sha256`

PortableTAK files:

- `mediamtx.yml`
- `ots-webhook.py`
