---
title: PortableTAK ⇄ OpenMANET field extension — integration spec
date: 2026-09-30
status: draft for the field-extension author
applies_to: PortableTAK host package 20260929 (installer 6963c10a…), OpenMANET 1.6.x / OpenWrt 24.10, ARM64
---

# PortableTAK ⇄ OpenMANET field extension: integration spec

This document defines what PortableTAK needs from an OpenMANET node, what it
owns, and what a field extension must provide or leave alone. It is written so
the field extension can replace CicadaTAK without changing PortableTAK's
provisioning core. Everything here was observed or exercised on Node02 and Node01
(CicadaTAK 0.12.0 on OpenMANET 1.6.5) during the 2026-09-29/30 session.

**Rule of thumb:** the cartridge owns identity and data; the host owns runtime,
networking, supervision, and device integration. The field extension is part of
"the host". PortableTAK's host-specific code lives only in
`/usr/local/portabletak/adapters/openmanet/`.

---

## 1. Platform requirements

| Requirement | Why | Verified on |
|---|---|---|
| OpenWrt/OpenMANET, `aarch64` | Host package and clean base are ARM64 | Node02, Node01 |
| `/usr/bin/python3` ≥ 3.11 with `tarfile` extraction filters | API, controller, layout tool | 3.11.14 |
| `sfdisk` (util-linux ≥ 2.38 with `--wipe`), `mkfs.ext4`, `e2fsck` (e2fsprogs ≥ 1.46) | Media layout and mount checks | 2.40.2 / 1.47.0 |
| `chroot`, `flock`, `sha256sum`, `tar`, `ip`, `ping`, `cmp`, `netstat` (BusyBox is fine) | Lifecycle scripts | BusyBox 1.36.1 |
| Kernel: ext4, loop not required, IPv4 forwarding on | Cartridge filesystem; phones → service address | 6.6.102 |
| ≥ 4 GB RAM | PostgreSQL + RabbitMQ + OTS + MediaMTX on a Pi 4 | Node02 (4 GB) |
| `tcpdump` (optional) | Field diagnostics | present |

Scripts are POSIX `sh` and are tested under BusyBox `ash`. Do not replace
`/bin/sh` with a shell whose top-level `return` does not exit the script.

## 2. What PortableTAK installs on the host

| Path | Purpose |
|---|---|
| `/usr/local/portabletak/` | Host package: `bin/`, `lib/`, `api/`, `web/`, `adapters/openmanet/`, `chroot/`, `share/mediamtx/`, `python/` (independent API runtime) |
| `/usr/local/portabletak/release/first-run-enabled` | First-run gate. **Absent** except during a controlled provisioning run |
| `/usr/local/portabletak-backups/` | Host package backups made by the installer |
| `/etc/init.d/portabletak-api` (START=97) | Management API, `127.0.0.1:8090` only |
| `/etc/init.d/portabletak-autoload` | Boot-time cartridge discovery/mount/start (installed, **not enabled** yet) |
| `/tmp/portabletak/` | Runtime state: `lifecycle.lock`, `bootstrap-job.json`, users worker pid/log |
| `/mnt/asgard` | Cartridge mount point (compatibility name; not identity) |

The field extension must not write inside these paths. It may **call** the
lifecycle tools listed in §7.

## 3. Ports

PortableTAK binds the following. The field extension must not bind them, and
the host firewall must allow the "phones" column from the phone-facing zone.

| Port / proto | Bound on | Service | Phones need it? |
|---|---|---|---|
| 8089/tcp | `0.0.0.0` | OTS SSL CoT (EUD handler) — **main client port** | **yes** |
| 8443/tcp | service address only (`10.41.250.10`) | nginx HTTPS → OTS API/Marti, data packages | **yes** |
| 8554/tcp, 8000-8001/udp | all | MediaMTX RTSP/RTP | video only |
| 1935/tcp | all | MediaMTX RTMP | video only |
| 8888/tcp | all | MediaMTX HLS | video only |
| 8889/tcp, 8189/udp | all | MediaMTX WebRTC | video only |
| 8890/udp | all | MediaMTX SRT | video only |
| 8081/tcp | `127.0.0.1` | OTS application (behind nginx) | no |
| 5672/tcp | `127.0.0.1` | RabbitMQ (cartridge node `rabbit@localhost`) | no |
| 5432 | unix socket in chroot | PostgreSQL | no |
| 9997/tcp | `127.0.0.1` | MediaMTX API | no |
| 18090/tcp | `127.0.0.1` | PortableTAK users worker | no |
| 8090/tcp | `127.0.0.1` | PortableTAK management API | no (see §8) |
| 4369/tcp, 25672/tcp | epmd / Erlang distribution | RabbitMQ internals | no |

Reserved for future PortableTAK use: **8446/tcp** (certificate enrollment for
QR onboarding) and **8088/tcp** (plain CoT, disabled by default).

Observed CicadaTAK conflicts to avoid in the extension: CicadaTAK's gateway held
`0.0.0.0:8080`, its dashboard `10.41.254.1:8081`. Neither collided today, but a
wildcard bind on 8081 or any of the ports above would break PortableTAK.

## 4. Service address (the TAK server's network identity)

- The active cartridge is reachable at **`10.41.250.10/32` on `br-ahwlan`**
  (the mesh bridge). Client packages embed `10.41.250.10:8089:ssl`.
- The address **moves with the cartridge**: the node running it claims it; any
  other node must not hold it.
- Claim/release is done only by
  `/usr/local/portabletak/adapters/openmanet/network {status|check|claim|release}`,
  which probes the mesh for a collision before claiming.
- **The field extension must never assign `10.41.250.10`** itself and must not
  remove it while PortableTAK is running.
- Lesson from Node01: ASGARD's emergency stop left the address assigned after its
  cartridge was gone. If the extension has a node-shutdown or USB-removal hook,
  call `network release` (after the lifecycle stop in §7).
- If the extension renames `br-ahwlan` or changes the mesh subnet
  (`10.41.0.0/16`), tell PortableTAK through the adapter
  (`adapters/openmanet/provision.py`: `address`, `prefix`, `interface`) — not
  by editing core provisioning.

**Current limitation:** one fixed address means **one active PortableTAK server
per mesh**. A per-cartridge address is a possible later change; if the extension
has a view on mesh address allocation, raise it before that work starts.

## 5. Phone (EUD) networking — the decision that matters most

Today's finding: CicadaTAK's phone DHCP hands out **no default gateway**
(`dhcp-option=lan,3` empty) and a **classless static route** (option 121:
`10.41.0.0/16 via 10.41.254.1`). iPhones honour option 121 and reach
`10.41.250.10`. **Android ignores option 121**, so Android phones have no route
to the server ("host unreachable"); it only worked after setting a static IP
with a gateway by hand. Every Android device on that WiFi is affected.

The field extension should pick one of these (all keep PortableTAK unchanged):

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. Hand out a gateway** | DHCP option 3 = node's LAN address (e.g. `10.41.254.1`) | Works on every OS; simplest | Phones route internet via the node; without upstream they may prefer the WiFi and lose cellular internet |
| **B. Gateway + answer Android's connectivity check** | Option 3 as in A, plus serve HTTP 204 for Android's check URLs (`connectivitycheck.gstatic.com/generate_204` etc.) — only when the node has real upstream internet, otherwise let Android see "no internet" | Android keeps WiFi usable for apps | More moving parts; must not fake internet when there is none |
| **C. Per-node local TAK address** | Each node's LAN address (e.g. `10.41.254.1:8089`, `:8443`) forwards (DNAT) to `10.41.250.10`; packages use the local address | Same package works on any node's WiFi, Android and iOS, no routing needed | TLS server certificate must also cover the local address (PortableTAK change: add it as a SAN); DNAT rules follow the active cartridge |

**Recommendation:** A now (it is what made the S22 work), consider C later if
phones roam between nodes' WiFi. Keep option 121 as well — it does no harm.

Also note for Android on a no-internet WiFi: Android may keep app traffic off
that network unless the user chooses "stay connected"; a gateway plus the
network being the only one available avoids most of this. Document the Samsung
"Intelligent Wi-Fi → Switch to mobile data" setting in the field guide.

Phone subnet assumptions: PortableTAK does **not** depend on the phone subnet
(`10.41.254.0/24` today). It only needs phones to reach `10.41.250.10` on the
ports in §3.

## 6. Firewall

- Phone-facing zone (LAN/AP) → **input** to the node: allow 8089/tcp, 8443/tcp,
  and the MediaMTX ports if video is used.
- Mesh zone (`br-ahwlan`) ↔ phone zone: **forward** allowed for
  `10.41.250.10` so phones on another node's WiFi can reach the server over the
  mesh (untested across nodes — part of the Node02→Node01 movement test).
- Deny 8090 (management API) and everything bound to `127.0.0.1` from every zone
  — they are loopback-only today and must stay unreachable (§8).
- Keep IPv4 forwarding on.

## 7. Cartridge detection, mounting, and lifecycle

**Only PortableTAK may mount and manage PortableTAK cartridges.**

- PortableTAK cartridges are ext4 filesystems labelled `PORTABLETAK` with
  `portabletak/cartridge.conf` at the root. The extension's automount/hotplug
  logic must **skip** them (match on label `PORTABLETAK` or on that file).
- Node02 still runs the legacy `asgard-automount` loop
  (`/usr/local/field-extensions/tak/`, `/etc/init.d/asgard-automount`, every 3 s,
  also mounting at `/mnt/asgard`). It only matches one old SanDisk serial, but it
  shares the mount point. **The new extension should retire it**, or at minimum
  never enable it on a node that runs PortableTAK.
- Do not create automatic mounts at `/mnt/asgard` or bind mounts under
  `/mnt/asgard/rootfs`.
- Serialise with PortableTAK through the lifecycle lock if the extension ever
  triggers lifecycle actions:
  `/usr/local/portabletak/bin/portabletak-with-lifecycle-lock` (flock on
  `/tmp/portabletak/lifecycle.lock`).

Lifecycle tools the extension may call (all print `KEY=VALUE` lines, exit 0 on
success):

| Tool | Use from the extension |
|---|---|
| `portabletak-status` | UI/status: storage, runtime, cartridge state |
| `portabletak-health` | Per-service RUNNING/STOPPED (7 required services) |
| `portabletak-autoload` | Boot: discover, mount, start a completed cartridge; never starts unfinished ones |
| `portabletak-stop` | Stop services (before shutdown or removal) |
| `portabletak-safe-remove` | Stop, unmount, mark safe to unplug |
| `portabletak-emergency-stop` | Kill cartridge processes when the USB has vanished |
| `adapters/openmanet/network release` | Release the service address after stop |

Recommended extension hooks: on **clean shutdown** run `portabletak-stop` then
`network release`; on **USB removal event** for the cartridge device run
`portabletak-emergency-stop` then `network release`; on **boot** let
`portabletak-autoload` run (after networking is up and `br-ahwlan` exists).

## 8. Management UI / API

- The PortableTAK management API listens on **`127.0.0.1:8090` only** and must
  not be exposed on LAN, mesh, WiFi, cellular or VPN. Operators reach it by SSH
  tunnel (`ssh -L 8090:127.0.0.1:8090 root@<node>`).
- If the extension has its own web UI, **link to** PortableTAK rather than
  proxying it, until PortableTAK's HTTPS, session login (built, off by default),
  CSRF and audit are enabled together. Proxying it publicly before then would
  expose an unauthenticated admin surface.
- Session login is keyed to the cartridge's administrator password
  (`config/management/` on the cartridge); the extension should not store or
  handle that password.

## 9. Time, DNS, and USB power

- **Time:** new cartridges run their server in **UTC** (decision 2026-09-29).
  Nodes should keep correct UTC (GPS/NTP); phones show local time themselves.
  Certificates are issued at provisioning, so a node clock far in the past can
  make fresh certificates "not yet valid".
- **DNS:** PortableTAK needs no DNS; packages use IP addresses. If the extension
  runs DNS for phones, a name for the server (e.g. `tak.lan → 10.41.250.10`) is
  a nice extra but not required.
- **USB power:** Node01's USB 3 port logged 10 over-current events and hundreds of
  "cannot enable" errors; ASGARD's corruption started there. The extension's
  health view should surface `dmesg` USB over-current/disconnect counts and
  `vcgencmd get_throttled`, and warn before PortableTAK writes to media.

## 10. Coexistence checklist for the extension

- [ ] Does not bind any port in §3 (including wildcard binds).
- [ ] Never assigns or removes `10.41.250.10`; calls `network release` only after PortableTAK stops.
- [ ] Phone DHCP gives Android a usable route (§5 option A, B or C).
- [ ] Firewall allows 8089/8443 from phones, blocks 8090 and loopback services.
- [ ] Automount skips ext4 label `PORTABLETAK`; legacy `asgard-automount` disabled.
- [ ] Shutdown and USB-removal hooks call the PortableTAK lifecycle tools.
- [ ] Nothing written under `/usr/local/portabletak`, `/mnt/asgard`, `/tmp/portabletak`.
- [ ] Autoload runs after `br-ahwlan` is up.
- [ ] Node keeps UTC time.

## 11. Joint acceptance tests (extension + PortableTAK)

1. Fresh node with the extension, no CicadaTAK: install PortableTAK host package; `install.sh --check` passes; API on `127.0.0.1:8090` only.
2. Insert a provisioned cartridge, reboot: autoload mounts and starts it; all 7 services RUNNING.
3. iPhone **and Android on DHCP** (no static IP) join the node's WiFi and connect to `10.41.250.10:8089`; they see each other.
4. Phone on node B's WiFi reaches the server running on node A over the mesh.
5. Safe remove from the extension's UI/hook: services stop, address released, filesystem clean.
6. Pull the USB while running: emergency stop + release fire; no stale `10.41.250.10` remains on any node.
7. Port scan from the phone subnet: only the §3 "phones" ports answer; 8090 does not.

## 12. Open items that may change this spec

- Clean-base rebuild will remove the host-side CoT parser launcher (no effect on the extension).
- Per-cartridge service address (would change §4).
- Enabling management login/HTTPS (would change §8).
- QR onboarding on 8446 (would add a phone-facing port in §3).
