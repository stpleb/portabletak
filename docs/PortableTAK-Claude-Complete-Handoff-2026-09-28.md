---
title: PortableTAK Server - Complete Claude Engineering Handoff
date: 2026-09-28T23:25:00-05:00
status: active-development
primary_goal: Complete a sanitized PortableTAK product for OpenMANET, Windows/WinTAK, and remote WiFi/cellular use.
current_priority: Finish Create New on a separate blank USB without changing ASGARD.
source_of_truth: Inspect current filesystem and live host before trusting older notes.
---

# PortableTAK Server: complete Claude engineering handoff

## 0. Claude operating directive

You are taking over a live, production-like PortableTAK/OpenTAKServer project. Continue the work; do not restart the design from scratch.

Rules:

1. Protect ASGARD TAK. Do not convert, reset, clone, format, or destructively test its original cartridge.
2. Inspect current files before editing. Live files and the current source tree override older notes.
3. Back up live files before meaningful edits. Validate shell syntax, Python compilation, services, and HTTP endpoints after changes.
4. Keep the management UI/API bound to `127.0.0.1` until authentication, HTTPS, CSRF, throttling, revocation, and audit behavior are integrated.
5. Keep OpenMANET networking outside core provisioning. The cartridge owns identity/data. Host adapters own runtime/network/device integration.
6. Never print passwords, tokens, private keys, database credentials, or secret-bearing configuration.
7. Keep the first-run activation gate closed until a controlled physical run on the new blank USB.
8. Separate evidence into unit, native-isolated, and physical acceptance. Never call a simulation or syntax check a physical pass.
9. Do not assume `/dev/sda` or `/dev/sdb` identifies the intended USB. Match model, serial, capacity, and physical topology.
10. Do not assume a raw PostgreSQL directory is safely portable between ARM Linux and Windows/WSL2.

## 1. Product goal and definition of done

Build a sanitized PortableTAK Server carried on removable media.

The cartridge owns:

- cartridge UUID and instance name
- CA, certificates, truststores, and client packages
- users and logical database state
- application configuration and secrets
- backups and provisioning journal

The host owns:

- runtime and architecture-specific dependencies
- networking and service address attachment
- process supervision
- mount, device, and safe-remove integration
- host adapter and optional WAN adapter

The finished product must:

1. Create a new sanitized PortableTAK USB from a factory/setup drive on macOS or Windows.
2. Provision on Node01/Node02 from the same host-agnostic first-run core.
3. Run on a Windows laptop beside WinTAK, preferably through WSL2.
4. Support approved remote client access over WiFi, Ethernet, cellular/hotspot, and Tailscale.
5. Back up, restore to replacement media, retain identity, reboot/autoload, move between nodes, and recover from controlled power loss.
6. Pass ATAK, iTAK, WinTAK, package/QR onboarding, backup/restore, removal, movement, reboot, and power-loss acceptance.

## 2. Current snapshot

Snapshot time: September 28, 2026, 11:25 PM Central.

- ASGARD original USB: unmounted and offline after USB disconnect/ext4 journal I/O faults.
- Node01 management API: running on `127.0.0.1:8090`.
- Live status: `PORTABLETAK_STORAGE=UNMOUNTED`, `PORTABLETAK_RUNTIME=UNAVAILABLE`.
- First-run release marker: absent, so activation is closed.
- Create New implementation: production candidate passes native ARM64 testing through pre-start service configuration.
- Not yet physically accepted: USB layout/write, real service start and health, TAK clients, reboot, movement, safe removal, and power-loss recovery.
- Preferred physical target: new blank USB on Node02.

## 3. Systems and access

| Item | Value |
|---|---|
| Primary node | Node01, `10.41.0.222` |
| SSH | `ssh root@10.41.0.222` |
| Host OS | CicadaTAK 0.12.0, OpenWrt 24.10 / OpenMANET 1.6.5 |
| Architecture | ARM64, Raspberry Pi 4B, 8 GB RAM |
| Kernel | 6.6.102 |
| Secondary node | Node02, 4 GB RAM; preferred isolated test host |
| API | `127.0.0.1:8090` |
| Mac tunnel | `ssh -L 8090:127.0.0.1:8090 root@10.41.0.222` |
| Host package | `/usr/local/portabletak` |
| Cartridge mount | `/mnt/asgard` |
| Chroot compatibility path | `/asgard` |
| OpenTAKServer | 1.7.13 |
| PostgreSQL | 16.15, DB `ots` |
| RabbitMQ | 3.12.1, AMQP `127.0.0.1:5672` |
| OpenMANET service | `10.41.250.10/32` on `br-ahwlan` |

Compatibility paths `/mnt/asgard` and `/asgard` may remain. They are not product identity.

## 4. ASGARD recovery state

The kernel reported USB disconnects, a lost write, and an ext4 journal update failure. All cartridge-rooted processes and mounts were stopped. No later format, repeated repair, or destructive restore was run.

Recovery backup:

- Path: `work/recovery/portabletak-20260927_213104.tar.gz`
- SHA256: `402db10ba6302157e6c112de39b6b7b7f29be026cab94a502df1ba42843249da`
- Contents: configuration, OTS data/certificates, and PostgreSQL logical dump
- Limitation: not a complete bootable rootfs image

A previous controlled restore was reported successful: a temporary user disappeared after baseline restore and services returned to RUNNING. Repeat broader acceptance on replacement media and verify users, config, CA/cert fingerprints, and client reconnection together.

If I/O errors recur, stop services, unmount, and investigate power/cable/drive behavior before more writes.

## 5. Architecture now implemented

```text
PortableTAK core controller + durable journal
        |
        +-- LinuxBootstrapBackend
        |       |
        |       +-- OpenManetAdapter (current)
        |       +-- GenericLinuxAdapter (planned)
        |
        +-- Windows/WSL2 backend + adapter (planned)
```

Core first-run input is exactly:

```text
instance_name
timezone
administrator_password
client_password
```

OpenMANET-specific values now live only in `adapters/openmanet/provision.py`:

```text
address: 10.41.250.10
prefix: 32
interface: br-ahwlan
runtime_profile: openmanet
```

Core staging no longer emits `tak-network.conf` or hardcodes the OpenMANET address. The Linux backend requires an injected host adapter. The adapter adds the service address before certificate generation and creates network/host metadata during service configuration.

Current host metadata is separate from cartridge identity:

```json
{
  "format": 1,
  "host_mode": "portable",
  "runtime_profile": "openmanet",
  "supported_arches": ["arm64", "x86_64"]
}
```

## 6. Completed engineering

### 6.1 Sanitized clean base

- Built from Ubuntu Base 24.04.5 and official Ubuntu/PyPI inputs.
- Excludes ASGARD identity, users, credentials, keys, certs, tokens, logs, RabbitMQ state/cookies, and machine identity.
- Includes archive traversal/link checks, provenance, content manifests, and private-key/identity scans.
- Uses a format-2 software seal. The nil template UUID can be replaced once without invalidating software integrity.
- Provisioning refuses the nil UUID.
- The publisher verifies before and after assignment and refuses a second assignment.

### 6.2 Durable controller and worker

- Strict input and IANA timezone validation.
- Refusal of existing/personal/recovery cartridges for Create New.
- Symlink/path escape and existing-data checks.
- Atomic metadata and JSON writes.
- Durable journal bound to cartridge UUID and attempt ID.
- Staged artifact hashes and retry integrity.
- Interrupted-operation replay and final-publish reconciliation.
- Secret-free phase evidence.
- Administrator password goes through stdin and is not persisted.
- Background job ownership, interruption detection, and concurrency refusal.
- Shared lifecycle lock.
- State-aware autoload and start refusal for incomplete media.

### 6.3 Production candidate Linux backend

Phases:

```text
staged
base_verified
storage_initialized
database_initialized
administrator_initialized
certificates_initialized
messaging_initialized
services_configured
services_started
health_verified
complete
```

Backend behavior:

- verifies the full sealed base before runtime mounts/writes
- creates private storage
- initializes PostgreSQL 16 and `ots` DB/role through local administrative access
- runs OTS migrations
- creates chosen administrator and rejects upstream default password
- generates fresh CA and server certificate
- generates independent RabbitMQ credentials
- installs checksum-pinned MediaMTX from host supplement
- writes adapter-owned network config and host metadata
- validates nginx config
- starts services and requires all health states before final metadata publication

Required final health states:

```text
POSTGRESQL
RABBITMQ
MEDIAMTX
OTS
COT
EUD
NGINX
```

### 6.4 Independent API runtime

Node01 uses `/usr/local/portabletak/python`. The API no longer imports FastAPI/uvicorn from `/opt/cicadatak/lib`.

### 6.5 Management auth foundation

`lib/management_auth.py` implements scrypt password verification, separately derived signing keys, bounded signed sessions, constant-time comparisons, tamper checks, and expiry checks.

This is not wired into live routes/UI yet. Keep the API localhost-only.

### 6.6 Host resilience

- Host-side emergency stop can stop cartridge-rooted processes when USB files are missing/unreadable.
- It uses process roots/executables and Linux pidfds to reduce PID reuse races.
- Shared lifecycle lock covers provisioning, autoload, Start, Stop, backup, restore, and safe remove.
- RabbitMQ lifecycle checks avoid repeated expensive `rabbitmqctl` polling.

## 7. Live Node01 deployment

Live installed candidate components:

- independent API runtime
- provisioning route and background worker
- controller, clean-base verifier, publisher, bootstrap engine/job/backend
- host adapter interface and OpenMANET adapter
- bootstrap prepare/unprepare
- state-gated detect/start/autoload candidates
- lifecycle lock and emergency stop
- pinned MediaMTX supplement

Latest live backup before adapter deployment:

```text
/usr/local/portabletak-backups/portabletak-before-host-adapter-20260929_042359.tar.gz
```

Live checks after restart:

```json
GET /api/provision/status
{
  "ok": false,
  "state": "unavailable",
  "error": "cartridge_not_mounted",
  "bootstrap_job": {"ok": true, "job": "idle"}
}
```

```json
GET /api/status
{
  "ok": true,
  "status": {
    "PORTABLETAK_PRODUCT": "PortableTAK Server",
    "PORTABLETAK_STORAGE": "UNMOUNTED",
    "PORTABLETAK_RUNTIME": "UNAVAILABLE"
  }
}
```

Release gate:

```text
/usr/local/portabletak/release/first-run-enabled
```

It is absent. Do not create it until controlled physical acceptance.

Live hashes:

| File | SHA256 |
|---|---|
| `lib/linux_bootstrap_backend.py` | `080bb4a1933e08e772f58c4466e9820da34f4e9c3b71f0a8514b8b35013e8198` |
| `adapters/openmanet/provision.py` | `515cdb49fb9ff075b5b3095f8dde7da6d673cd7d20bc7e7ac8528fa1c4b4d71d` |
| `share/mediamtx/mediamtx` | `07bb8abf4d12cd5c0370c04a72d4b474a9d5f80a13950e83ac12907075b358ef` |

## 8. Source map

Primary source directory:

```text
work/portabletak-provision
```

| Path | Purpose |
|---|---|
| `lib/portabletak_provision.py` | validation, metadata, staging, secrets, atomic writes |
| `lib/bootstrap_engine.py` | durable state machine and final publication |
| `lib/linux_bootstrap_backend.py` | Linux DB/admin/CA/messaging/nginx/MediaMTX/start/health |
| `lib/host_adapter.py` | adapter boundary and binding validation |
| `adapters/openmanet/provision.py` | OpenMANET service address/interface and host metadata |
| `lib/clean_base.py` | software seal and staged verification |
| `lib/publish_identity.py` | one-time cartridge UUID publication |
| `lib/bootstrap_job.py` | worker ownership and status |
| `lib/management_auth.py` | password/session primitives |
| `lib/lifecycle_lock.py` | cross-operation serialization |
| `bin/portabletak-bootstrap-worker` | stdin request, release seal, OpenMANET adapter selection |
| `bin/portabletak-bootstrap-prepare` | safe runtime mounts for incomplete generic media |
| `api/provision_routes.py` | status, validation, guarded activation |
| `web/index.html` | candidate dashboard with preserved styling |
| `build/host-isolate.py` | private namespace native-test harness |
| `tests/test_linux_backend_native.py` | production backend native pre-start test |
| `vendor/mediamtx` | pinned ARM64 binary/config/token-file webhook |

Host installer source:

```text
work/host-installer-20260928
```

Installer behavior:

- validates OpenWrt ARM64 platform
- verifies every file checksum
- backs up `/usr/local/portabletak`
- installs independent API runtime and host components
- compiles Python and checks shell syntax
- restarts API and keeps first-run gate closed
- includes `rollback.sh`

## 9. Cartridge state contract

| State | Behavior |
|---|---|
| `new` | recognized but normal service start blocked; publish identity and stage Create New |
| `provisioning` | resume from journal; normal start blocked |
| `failed` | retry from last verified phase; preserve artifacts |
| `provisioned` | completed generic instance; normal/autoload start allowed |
| `existing` | legacy ASGARD-compatible completed instance; Create New forbidden |
| `recovery` | recovery workflow only; normal start blocked until reconciled |

Current staged receipt contains:

```text
config.yml
cartridge.conf
timezone
secrets.json
```

`tak-network.conf` is adapter-owned and is no longer a core staged artifact.

## 10. Test evidence

| Check | Result |
|---|---|
| Controller | 25 tests pass |
| Bootstrap engine | 8 tests pass |
| Clean base | 22 tests pass |
| Linux backend unit | 5 tests pass |
| Host adapter | 1 test passes |
| Identity publisher | 3 tests pass |
| Job supervisor | 4 tests pass |
| API | 12 tests previously passed on Node01; live endpoints respond |
| Wizard | 5 JavaScript behavior tests previously passed |
| Native crypto | CA/server/client certs and ATAK/iTAK packages passed |
| Native DB/admin | PostgreSQL 16 migrations and chosen administrator passed; default rejected |
| Native production backend | ARM64 pre-start pass through nginx, MediaMTX, and OpenMANET config |
| Installer | all file checksums and `install.sh --check` passed on Node01 |

Latest native result:

```text
PASS: production backend verified clean base, storage, PostgreSQL,
migrations, administrator, CA, RabbitMQ, nginx and MediaMTX configuration
NOT TESTED: service-start phase, physical health gate, USB behavior, TAK clients
```

Final native-harness fixes:

- bring loopback up in private network namespace
- add OpenMANET service IP only inside that namespace
- create nginx log directory before `nginx -t`
- call runtime prepare from certificate and service-config phases for retry after a new process/namespace

## 11. Current release artifacts

Artifact root:

```text
/Users/STPLEB/Documents/Codex/2026-09-27/files-pasted-by-the-user-paste/outputs
```

| Artifact | SHA256 |
|---|---|
| `portabletak-clean-base/portabletak-clean-arm64-20260928-v2.tar.gz` | `42904660fa1efca94db9ab17558339098023311d9277a146916944159170a56b` |
| `portabletak-provisioning-stage2-source.zip` | `c4d27497d705e7ce942a4c078469eda582d7a2ee096d1be3dca6143395b41819` |
| `portabletak-host-installer-arm64-openwrt-20260928.tar.gz` | `330fa5cb1916c805876d0d253f2da7299f8227e2b7fee17191de6446a6181896` |
| `portabletak-independent-runtime/portabletak-python311-arm64-musl.tar.gz` | `f82710b21eab4ba64ffcb8b51fd64011ee546605227a975e89a6e1b47e8b54ac` |
| `portabletak-mediamtx-arm64/portabletak-mediamtx-1.13.0-linux-arm64.tar.gz` | `8fc6fad1acd075218f65f0908009f3791f1a7b50ad6af1b0467cfdd2a8512964` |
| Clean-base software seal | `df9a6ffb775712f540a1afab28fc369f441c193096bfab846c862e9de51ff775` |
| ASGARD recovery backup | `402db10ba6302157e6c112de39b6b7b7f29be026cab94a502df1ba42843249da` |

The older `portabletak-clean-arm64-20260928.tar.gz` has a fixed template identity and is not suitable for copying to multiple cartridges.

## 12. Immediate next sequence

### 12.1 Read-only takeover

1. Read this file and `work/portabletak-provision/README.md`.
2. Verify the checksums above.
3. SSH to Node01.
4. Confirm API, release gate, ASGARD unmounted state, kernel storage messages, and free space.
5. Re-run focused tests and installer `--check`.
6. Do not mount or repair ASGARD as a routine first step.

### 12.2 Blank USB Create New acceptance

1. Use Node02 or another isolated host.
2. Identify blank USB by model, serial, capacity, topology, and current device path.
3. Write/extract the v2 clean payload with a reviewed layout procedure.
4. Verify archive hash and full cartridge software seal.
5. Run one-time UUID publisher. Confirm non-nil UUID and second-run refusal.
6. Install/check host package while activation remains closed.
7. Verify OpenMANET adapter and MediaMTX supplement hashes.
8. Create a test-target recovery/rollback snapshot.
9. Open release gate only for the controlled run.
10. Submit the four-field request through localhost.
11. Observe journal transitions and real service logs.
12. Require all health states before `provisioned` is published.
13. Verify chosen administrator and reject `administrator/password`.
14. Create a normal user.
15. Generate ATAK, iTAK, and WinTAK packages.
16. Test real clients and record certificate fingerprints.
17. Test backup, verify, restore, and client reconnection.
18. Test safe removal.
19. Test cold reboot/autoload.
20. Move Node02 to Node01 and confirm clients reconnect without changing server identity.
21. Only after backup/restore passes, test controlled power loss on disposable media.

### 12.3 Failure handling

If a phase fails:

- leave state `provisioning` or `failed`
- preserve `status.json`, `artifacts.json`, safe logs, and diagnostics
- never publish completion manually
- fix and retry the uncommitted phase
- keep UUID, secrets, CA, name, timezone, and client password stable
- re-enter the administrator password because it is intentionally not stored
- do not generate a replacement identity unless explicitly running Reset New Team

## 13. Physical acceptance matrix

| Area | Pass criteria |
|---|---|
| Media | stable target identity recorded; system/internal/self media refused |
| Integrity | source hash and cartridge seal match |
| Identity | fresh UUID, CA, certs, secrets; no ASGARD material |
| Retry | interruption at every phase preserves verified work |
| Administrator | chosen password succeeds; upstream default fails |
| Services | PostgreSQL, RabbitMQ, MediaMTX, OTS, CoT, EUD, nginx RUNNING |
| Clients | ATAK, iTAK, WinTAK connect with packages; QR limitations documented |
| Recovery | backup restores users/config/fingerprints and client connectivity |
| Portability | Node02 to Node01 movement keeps client configuration |
| Autoload | cold boot mounts, prepares, and starts automatically |
| Safe remove | processes stop, mounts release, filesystem cleanly ejects |
| Power loss | journal never claims false completion; disposable media recovers |

## 14. Windows and WinTAK implementation

Preferred runtime: WSL2 unless testing demonstrates a safer/simpler design.

Reuse:

- OpenTAKServer
- PostgreSQL
- RabbitMQ
- nginx
- MediaMTX
- PortableTAK core controller/journal/identity contracts

Add:

- Windows launcher that detects USB state, starts backend, and opens wizard/dashboard
- Windows adapter for Local WiFi, Ethernet, cellular/hotspot, Tailscale, and advanced binding
- PowerShell attach/start/status/stop/safe-remove tools
- clear WSL2 and privilege diagnostics
- safe storage/migration contract
- WinTAK same-laptop acceptance

Storage warning:

Do not move current raw ARM PostgreSQL storage into Windows/WSL2 and assume safety. Initial recommended path is a versioned logical export/import on the cartridge and host-local PostgreSQL runtime storage. Preserve CA/config/client identity on the cartridge.

Windows acceptance:

1. Insert USB and launch PortableTAK Setup.
2. Detect new/completed state.
3. Run same four-field core wizard, then choose Windows network mode.
4. Start backend and WinTAK together.
5. Connect WinTAK locally and remote TAK clients over chosen networks.
6. Back up, stop, relaunch, and restore with same identity.

Never deploy Windows experiments on the live ASGARD cartridge.

## 15. Factory/setup drive

Desired workflow:

```text
Factory drive + blank USB + macOS/Windows
    -> Create PortableTAK USB
    -> PORTABLETAK_PROVISIONING_STATE=new
    -> supported host
    -> first-run wizard
```

Factory contents:

- sanitized master image
- version manifest and checksums
- macOS creation tool
- Windows creation tool
- user/recovery documentation

Writer safety:

1. Enumerate removable drives.
2. Show model, capacity, serial, and device ID.
3. Refuse internal/system disks.
4. Refuse factory drive itself.
5. Require explicit destructive confirmation bound to stable identity.
6. Verify source checksum.
7. Write image.
8. Read back/verify written image.
9. Confirm `new` unprovisioned metadata.
10. Safely eject.

Current status: clean image, seal, publisher, and installer exist. Polished cross-platform destructive writers do not.

## 16. Remote WiFi/cellular path

Recommended first release: optional Tailscale adapter outside core and OpenMANET adapter.

Behavior:

- advertise only PortableTAK route or required client service ports
- advertise only while host owns mounted cartridge/service identity
- deny by default
- keep client traffic and management traffic separate
- preserve mesh-only operation without WAN

Before exposing management:

- integrate login/session auth
- HTTPS
- CSRF protection
- login throttling
- logout/revocation
- audit events

Test home WiFi, Ethernet, phone hotspot, cellular NAT, relay fallback, network address changes, reconnect, and internet loss.

## 17. Remaining work and estimate

| Milestone | Estimate |
|---|---:|
| Generic OpenMANET cartridge physical acceptance | 3-7 focused sessions |
| Recovery/replace workflow | 2-4 sessions |
| Management auth live integration | 2-4 sessions |
| Backup improvements | 2-4 sessions |
| Generic Linux adapter | 3-6 sessions |
| Windows/WinTAK adapter and storage contract | 5-10 sessions |
| Remote WiFi/cellular/Tailscale | 2-5 sessions |
| Factory drive tools | 3-6 sessions |
| Final release repeat acceptance | 2-4 sessions |

Largest uncertainties: USB hardware stability and cross-platform database/storage design.

## 18. Common traps

- PostgreSQL restore/admin: do not force `-h 127.0.0.1`; use Unix socket as postgres OS user.
- OTS default admin: ordinary startup may create `administrator/password`. Provision before public startup and verify default rejection.
- Certificate issuance: OTS deletes existing cert directory for a common name before regeneration. Do not casually reissue ASGARD users.
- Device paths: `/dev/sdX` changes. Match stable identity.
- Chroot isolation: chroot alone does not isolate network, processes, mounts, or ports.
- Core/adapters: do not put `br-ahwlan` or `10.41.250.10` back into core staging.
- Clean base: it is a verified input, not a complete cartridge.
- Evidence: staging/unit/native results do not equal physical acceptance.
- RabbitMQ: avoid repeated expensive `rabbitmqctl` startup polling.
- BusyBox: `mountpoint`, GNU grep options, and desktop wget options may be unavailable.

## 19. Useful commands

Read-only Node01 checks:

```sh
ssh root@10.41.0.222
/usr/local/portabletak/bin/portabletak-status
wget -qO- http://127.0.0.1:8090/api/provision/status
wget -qO- http://127.0.0.1:8090/api/status
test ! -e /usr/local/portabletak/release/first-run-enabled && echo CLOSED
mount | grep -E "asgard|sda|sdb"
dmesg | tail -n 120
```

Focused local tests:

```sh
cd /Users/STPLEB/Documents/Codex/2026-09-27/files-pasted-by-the-user-paste
python3 -m unittest discover -s work/portabletak-provision/tests -p 'test_controller.py'
python3 -m unittest discover -s work/portabletak-provision/tests -p 'test_bootstrap_engine.py'
python3 -m unittest discover -s work/portabletak-provision/tests -p 'test_linux_bootstrap_backend.py'
python3 -m unittest discover -s work/portabletak-provision/tests -p 'test_host_adapter.py'
python3 -m unittest discover -s work/portabletak-provision/tests -p 'test_clean_base.py'
python3 -m unittest discover -s work/portabletak-provision/tests -p 'test_publish_identity.py'
python3 -m unittest discover -s work/portabletak-provision/tests -p 'test_bootstrap_job.py'
```

Installer validation:

```sh
tar -xzf portabletak-host-installer-arm64-openwrt-20260928.tar.gz
cd host-installer-20260928
./install.sh --check
```

Review README, checksum manifest, backup behavior, and rollback before running installer without `--check`.

## 20. Suggested first message to Claude

```text
You are taking over PortableTAK Server. Read the attached handoff completely.
Treat ASGARD as protected production-like reference data. Inspect current files
before editing. Keep the management API localhost-only and keep the first-run
release gate closed until a controlled blank-USB acceptance run.

Start by validating artifact checksums, reviewing work/portabletak-provision,
and inspecting Node01 at root@10.41.0.222. Confirm ASGARD remains unmounted and
the API is healthy. Continue the Create New path from the tested native
pre-start boundary. Use the OpenMANET adapter; do not add OpenMANET assumptions
back into the core controller. Back up live files before changes and report
unit/native/physical evidence separately.

When the blank USB is present, identify it by model, serial, capacity, and
physical topology. Never infer the target from /dev/sdX alone. Complete physical
provisioning, client, backup/restore, reboot, Node01/Node02 movement, and power-loss
acceptance before opening a release. Preserve all secrets and never print them.
```

Expected first response:

- read-only drift report
- exact next test target and safety boundary
- files to change and checks to run
- no destructive media command until target identity is reviewed

## 21. Handoff inventory

| File/package | Purpose |
|---|---|
| `PortableTAK-Claude-Complete-Handoff-2026-09-28.pdf` | human-readable complete handoff |
| `PortableTAK-Claude-Complete-Handoff-2026-09-28.md` | machine-friendly Claude handoff |
| `portabletak-provisioning-stage2-source.zip` | latest controller/backend/adapter/tests source |
| `portabletak-host-installer-arm64-openwrt-20260928.tar.gz` | verified OpenWrt/OpenMANET host installer |
| `portabletak-clean-arm64-20260928-v2.tar.gz` | sanitized ARM64 clean payload |
| `portabletak-python311-arm64-musl.tar.gz` | independent OpenWrt API runtime |
| `portabletak-mediamtx-1.13.0-linux-arm64.tar.gz` | pinned MediaMTX supplement |
| `portabletak-storage-incident-20260928.md` | USB incident boundary |
| `portabletak-next-usb-tests.md` | short physical acceptance checklist |

When this file conflicts with older Markdown status notes, inspect current source and live state. This handoff includes the host-adapter split, final native pre-start pass, rebuilt packages, and Node01 deployment through 11:25 PM Central on September 28, 2026.
