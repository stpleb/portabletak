# PortableTAK roadmap and current status

Updated September 28, 2026. ASGARD remains the protected reference instance.

**Current storage status:** ASGARD is offline and unmounted after kernel-reported USB disconnects and journal I/O failures. Its verified recovery backup is stored on the Mac. See `portabletak-storage-incident-20260928.md`.

## Current work: Create New provisioning

Implemented and tested first stage:

- Standalone Python controller for status, validation, and private staging.
- Existing/personal cartridge refusal, input checks, exclusive staging lock, atomic journal, fresh independent secrets, artifact hashes, and retry checks.
- Administrator plaintext is never written by staging. The staged client password remains in restricted configuration because client package generation needs it.
- Guarded API status/validation routes installed on Node01. Activation is explicitly unavailable.
- Wizard candidate built with the existing dashboard styling. It has not replaced the live dashboard.
- Generic-only native OTS CA adaptation tested with fresh CA/server/client certificates and ATAK/iTAK packages. It keeps the CA password out of package preferences, handles password punctuation safely, and uses the service IP.
- Administrator helper tested with native OTS migrations in a separate PostgreSQL cluster, on a private Unix socket with TCP disabled. The chosen password worked, the default password failed, and existing accounts were protected.
- Clean ARM64 software base assembled, sanitized, sealed, independently reverified, and archived on the Mac. Its isolated native runtime test passed PostgreSQL, migrations, administrator creation, CA, RabbitMQ, OpenTAKServer, and nginx TLS.
- PortableTAK's management API now carries its own FastAPI/uvicorn dependency bundle and no longer imports from CicadaTAK.
- Host-side emergency Stop fallback installed and tested for unreadable or missing cartridges.

Still to implement before Create New can run end to end:

1. Connect the tested durable bootstrap engine to production Linux operations: publish the verified clean base, initialize PostgreSQL and RabbitMQ, write application configuration, create HTTPS/nginx/MediaMTX state, and activate services.
2. Install the checksum-verified MediaMTX 1.13.0 ARM64 supplement in the final payload and test live OpenTAKServer publish/read behavior. The binary, configuration startup, localhost API, and token-file webhook relay already pass host tests.
3. Integrate the tested shared lifecycle lock into the live shell controllers and add operation-specific state gates. The lock library, wrapper, provisioning path, and autoload candidate are complete in source.
4. Connect the tested background-job supervisor to the production worker and API. It already avoids persisting passwords and detects interrupted workers; backend rollback and health-gated completion remain pending.
5. Wizard deployment and full fresh-cartridge testing on Node02.

Initial administrator name remains `administrator` for this first implementation. Additional users are created from Users after provisioning, as agreed in the approved plan. Custom admin names, wizard-created initial users, and advanced network edits remain later extensions. Default service settings remain 10.41.250.10/32 on br-ahwlan.

## Acceptance evidence

| Item | Status |
| --- | --- |
| Existing ASGARD restore | Newer handoff reports a controlled restore passed, including removal of a temporary user and return to RUNNING. Not repeated during this work. |
| Broader restore acceptance | Still explicitly verify users, certificate fingerprints, config, and client reconnection together. |
| Current recovery backup | `portabletak-20260927_213104.tar.gz`, copied to the Mac and rechecked at SHA256 `402db10...43249da`. The original USB is unmounted after I/O failures. |
| Controller/API tests | 25 controller tests and 10 ASGI route tests pass. The API tests use PortableTAK's independent FastAPI bundle on Node01. |
| Clean base | Sanitized and resealed with per-cartridge identity support; v2 seal `df9a6ffb...ff775`. The 491 MB archive passed gzip/tar checks and has SHA256 `42904660...a56b`. The old fixed-identity archive is retained only as a build record. |
| Cartridge identity publication | One-time UUID publisher verifies the software seal before and after its atomic metadata write. Provisioning refuses the nil template UUID. Three tests pass; physical mounted-media use is pending. |
| Clean native runtime | Passed fresh PostgreSQL, migrations, administrator, CA, RabbitMQ authentication, OTS, nginx TLS, CA chain, and service-IP identity in private namespaces. MediaMTX was not tested. |
| Wizard behavior tests | 5 tests passed locally. Visual browser and physical client acceptance are pending. |
| Native crypto | Passed in isolated temporary files using the installed OTS Python environment. |
| Native migrations/admin | Passed in an isolated temporary PostgreSQL 16 cluster. The temporary cluster was stopped afterward. |
| Durable bootstrap journal | Eight tests pass, including phase retry, interrupted replay, artifact/secret refusal, and recovery from the final metadata/journal crash window. Production platform operations are pending. |
| MediaMTX | Official v1.13.0 ARM64 archive checksum verified. Configuration and localhost API started on Node01; temporary process stopped. Live OTS/TAK streaming remains pending. |
| Management authentication | Password verifier and signed-session core has five passing tests. HTTPS routes, CSRF, rate limits, revocation, and live integration remain pending. |
| Lifecycle/job control | Shared lock has four passing tests; bootstrap supervisor has four passing tests; BusyBox autoload test passes on Node01. Live lifecycle script integration remains pending. |
| Reboot/autoload | Physical test pending. The installed script still mounts only; a tested candidate starts completed cartridges and leaves incomplete/recovery media stopped. It is withheld while ASGARD storage is unstable. |
| Existing cartridge movement | Handoff reports Node01/Node02 movement passed. Generic movement remains pending. |

A blank USB cartridge is being ordered. Node02 can be the test host when it arrives. No ASGARD clone will be used as the generic image.

## Remaining product work

- **Sanitize generic build: completed for the clean software base.** It excludes ASGARD identity, users, credentials, keys, certificates, tokens, logs, RabbitMQ state/cookies, and machine identity. Reverify the saved content seal before publishing it to physical media.
- **Management authentication:** login/session protection before any LAN/mesh management exposure. The current API remains bound to localhost.
- **Independent Python dependencies: completed on Node01.** API uses `/usr/local/portabletak/python`, with pinned wheel hashes and a CPython 3.11 ARM64 musl bundle. Ten ASGI tests and live HTTP checks passed. Other host architectures need their own matching bundle.
- **Recovery / replacement media:** prepare replacement cartridge, upload/import backup, restore identity/database/certificates, and reconnect existing clients.
- **Backup improvements:** readable timestamps, notes/names, free space, configurable retention, optional scheduled backups, and upload/import. No automatic deletion or schedules enabled by this plan.
- **Installer:** package the host tools and explicit runtime dependencies.
- **OpenMANET adapter cleanup:** keep networking integration outside the portable core.
- **Generic Linux:** support normal Linux hosts independently of CicadaTAK/OpenMANET.
- **Windows/WinTAK host adapter:** later phase.
- **WAN/BLOS/overlay/federation:** later phase.

## Final acceptance matrix

Run on fresh generic media: ATAK, iTAK, WinTAK; package onboarding; QR onboarding with its documented certificate limitations; users and password changes; backup/restore; safe removal; Node01/Node02 movement; reboot/autoload; and controlled unexpected-power-loss recovery. Record actual outcomes separately from syntax checks, unit tests, and simulations.

## Host resilience work completed September 28

- Host-side emergency process shutdown is installed as the Stop fallback for missing/invalid cartridges. It uses Linux pidfds and actual process roots/executables, avoiding dependence on readable USB metadata. Four selection tests and a native process-isolation test passed.
- No additional filesystem repair was attempted after the USB disconnected. All cartridge processes were stopped and all cartridge mounts unmounted.
- Management still binds to localhost and the existing dashboard remains unchanged.
- An autoload candidate now starts only completed cartridges and passed its
  state tests. It remains undeployed until the USB connection is stable.
