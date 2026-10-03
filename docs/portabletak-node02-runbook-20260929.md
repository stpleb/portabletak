# Node02 blank-USB session runbook (2026-09-29)

Goal: turn the new blank USB into a verified `new` cartridge on Node02, publish its
identity, then run one release-gated Create New with the real health gate.
ASGARD's Lexar stays on Node01, untouched. The gate on Node01 stays closed.

## What changed overnight (software only; no live host was modified)

- `bin/portabletak-media-layout` + `lib/media_layout.py`: identity-gated disk layout.
  - Refuses: non-USB disks, mounted/swap/held disks, disks under 8 GiB, ASGARD's
    filesystem UUID (`1f501ae2-…`, never overridable), and any existing ext2/3/4
    filesystem unless named with `--replace-ext-uuid`.
  - Requires `--confirm "ERASE <fingerprint>"`. The fingerprint hashes USB port,
    vendor/product ID, serial, model and size, and is recomputed right before the
    partition table is replaced. Weak serials (like ASGARD's `0000000000000086`)
    produce a warning; port + size still distinguish drives.
  - Proves the archive against the pinned digest and its embedded seal before
    touching the disk. Extracts faithfully (absolute links, set-id modes, numeric
    owners), writes `cartridge.conf` last, unmounts, requires `e2fsck -fn` = 0,
    then re-verifies the seal from a read-only remount.
- `clean_base.py`: the seal ignores the filesystem's top-level `lost+found`.
  Without this, every freshly formatted cartridge failed seal verification.
  **Node02 must run the 20260929 host package, or publish/provision will refuse.**
- Backups: fixed creating the work folder before the mount check (it could
  write to the host), unchecked config/data archive failures, and non-atomic
  publishing. Added per-file checksums, an archive checksum sidecar that restore
  checks before unpacking, notes, readable labels, free-space check, and opt-in
  `--keep N` retention. Format stays 1, so older backups still restore.
- Management login (decision: same administrator password as first-run).
  The administrator phase now also writes a scrypt hash to the cartridge at
  `config/management/`; a retry must use the same password. `app.py` is wrapped
  by `management_guard.py` (sessions, CSRF, throttle, logout/revoke-all, audit
  log) and the dashboard loads `auth.js` for the sign-in panel. Enforcement is
  **off** unless the API runs with `PORTABLETAK_MANAGEMENT_AUTH=enforce`; the
  API stays on 127.0.0.1 either way.

Evidence (keep separate from physical acceptance):

| Check | Result |
|---|---|
| Unit: media layout | 37 pass |
| Unit: management guard | 16 pass |
| Unit: backend creates management login in administrator phase | pass |
| Real FastAPI 0.14x on Python 3.11 (Linux VM), guard in enforce mode | setup 503, no session 401, CSRF 403, login/logout OK, wizard routes intact |
| Existing suites (controller, engine, clean base, backend, adapter, publisher, job, lock, auth, MediaMTX, patches, emergency stop) | all pass |
| Real v2 archive vs seal | 42,460 entries match |
| Native loop device (Linux arm64 VM, real sfdisk/mkfs/e2fsck, real v2 archive) | layout → seal → publish → second publish refused → controller preflight `new`/`can_stage` → fsck clean: PASS |
| Backup/restore scripts under BusyBox ash (Alpine): atomic publish, per-file + archive checksums, notes, free-space check, retention, restore refuses tampering | 9 pass |
| Backup catalog (API listing) | 4 pass |
| Physical USB, Node02, services, clients | NOT TESTED |

## Artifacts

| File (outputs/) | SHA256 |
|---|---|
| `portabletak-host-installer-arm64-openwrt-20260929.tar.gz` | `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2` |
| `portabletak-clean-base/portabletak-clean-arm64-20260928-v2.tar.gz` | `42904660fa1efca94db9ab17558339098023311d9277a146916944159170a56b` |
| Clean-base seal | `df9a6ffb775712f540a1afab28fc369f441c193096bfab846c862e9de51ff775` |

## Session steps

Stop at any unexpected result. Record host, time, and output for each step.

1. **Node02 baseline (read-only).** Run from the Mac:
   `work/portabletak-provision/tools/portabletak-preflight.sh root@<Node02>`.
   It checks the local artifact hashes and prints the items below. Then: Address, OS/kernel, `dmesg | tail`, `/sys/block`,
   `mount`, free space in `/tmp`, whether `/usr/local/portabletak` exists and at
   what version. Confirm no ASGARD media is attached to Node02.
2. **Copy and verify.** `scp` the installer and v2 archive to Node02 `/tmp`, then
   `sha256sum` both on Node02 against the table above.
3. **Host package.** Extract the installer, run `./install.sh --check`, review,
   then install (it backs up `/usr/local/portabletak` and keeps the gate closed).
   Confirm `lib/clean_base.py` contains `FILESYSTEM_PATHS`.
4. **Insert the blank USB.** Then `portabletak-media-layout list`. Review the
   identity record together: vendor/product ID, serial, model, size, USB port,
   `/dev` name. If OpenWrt auto-mounted it, `umount` it and re-run `list`.
5. **Plan.** `portabletak-media-layout plan sdX` must say `eligible: true`.
   Note the fingerprint and confirmation phrase.
6. **Write** (destructive, only after step 4 review):
   `portabletak-media-layout write sdX /tmp/portabletak-clean-arm64-20260928-v2.tar.gz --confirm "ERASE <fp>" --receipt /root/portabletak-layout-<fp>.json`
   Expect `PORTABLETAK_LAYOUT=COMPLETE`. Keep only this one PortableTAK drive
   attached: `portabletak-discover` mounts the first cartridge it finds.
7. **Mount and publish.** `portabletak-mount` (runs discovery + `e2fsck -fn`),
   then `portabletak-publish-identity /mnt/asgard df9a6ffb…ff775`. Record the UUID.
   Run it again and confirm `identity_already_published`.
8. **Provision (gate open only for this run).** Create
   `/usr/local/portabletak/release/first-run-enabled`, open the wizard over
   `ssh -L 8090:127.0.0.1:8090 root@<Node02>`, submit the four fields, and watch
   `portabletak/provision/status.json`. Remove the gate file afterwards,
   whatever the result.
9. **Management login check** (after provisioning completes): set
   `PORTABLETAK_MANAGEMENT_AUTH=enforce` in `/etc/init.d/portabletak-api`'s env,
   restart the API, confirm the dashboard asks for the password, the chosen
   administrator password signs in, a wrong one is refused and audited in
   `/mnt/asgard/config/management/audit.log`, and Sign out works.
10. **Then** handoff steps 6–10: health gate, chosen admin works / default fails,
   test user and client packages, backup/restore, safe removal, reboot/autoload,
   Node02 → Node01 move (Node01 only after its USB 3 port issue is dealt with).

If a phase fails: leave the state as `provisioning`/`failed`, keep `status.json`
and logs, fix, and retry with the same four inputs. Re-running the layout is
always possible for this disposable drive, since it has no data yet.

## Follow-ups decided during the Node02 session

- **Timezone (decided 2026-09-29):** first-run sets the server to UTC and no
  longer asks for a timezone (three setup fields). Add a separate "change server
  timezone" feature later. Phones display their own local time regardless. The
  Node02 test cartridge keeps `America/Chicago` because it was provisioned before
  this change.
- **Service address:** every cartridge uses `10.41.250.10`, so only one PortableTAK
  server can be active on a mesh. Consider a per-cartridge address before
  running two cartridges at once.
- **Emergency stop** should release the service address; ASGARD's stop left
  `10.41.250.10` on Node01 until it was removed by hand.

## Physical acceptance results — Node02, 2026-09-29/30 (UTC)

Host Node02 (10.41.254.1 / mesh 10.41.0.89), Lexar 21c4:0809 serial 0000000000000362,
port 2-2, fingerprint acfd6e1106911d92. Installer at end of session: `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`.

| Check | Result |
|---|---|
| Layout (run 1 and run 2) | PASS: seal verified, e2fsck 0, read-back verified (~11 min) |
| Identity publish / second publish | PASS / refused |
| Test cartridge | a21f50bc-0dbb-4be7-9716-18d27d539bea, "PortableTAK Test", Etc/UTC |
| Provisioning, all phases to health_verified | PASS (after fixes below) |
| Required services | PostgreSQL, RabbitMQ, MediaMTX, OTS, CoT, EUD, nginx all RUNNING |
| First-run gate | closed after the run |
| Chosen admin login / upstream default / wrong password | 200 / 400 / 400 |
| HTTPS 10.41.250.10:8443 verified against new CA | PASS |
| New CA | CN=PortableTAK-a21f50bc-CA, SHA-256 36:D3:12:A6:...:A5:60, to 2036 |
| Test user tester1 + ATAK and iTAK packages | PASS (packages point to 10.41.250.10:8089:ssl) |
| iTAK (iPhone) connect, cert auth | PASS |
| ATAK (Samsung S22, Android 15) connect, cert auth | PASS only with static IP + gateway (see Android below) |
| iTAK <-> ATAK see each other | PASS (after CoT parser credential fix) |
| ASGARD-certificate client (WinTAK) against new CA | correctly refused |
| QR onboarding, backup/restore, reboot/autoload, Node02->Node01 move, power loss | NOT TESTED |

Test credentials: work/test-credentials/node02-test-cartridge.json (0600, never printed).

### Fixes made on hardware (all in the installer above)
1. Installer: root ownership, runtime temp cleanup, 30 s API start poll.
2. Service address claimed through the adapter (collision check) before nginx -t.
3. Retry: software-only seal check; engine resumes after last committed phase;
   controller keeps the resume position.
4. Start allowed for an unfinished cartridge only from its running bootstrap worker.
5. MediaMTX 1.13 readiness wording.
6. Cartridge-owned RabbitMQ node name rabbit@localhost (was host-derived).
7. HTTPS readiness/health accept 8443 (generic) and 443 (ASGARD).
8. Users helpers shipped by the host package (clean base has none).
9. CoT parser launcher adds RabbitMQ credentials to Socket.IO (upstream gap).

### Follow-ups from the session
- **Clean base rebuild:** extend build/patch-ots.py to cot_parser.py line
  `SocketIO(message_queue="amqp://" + ...)`, re-seal, re-pin; then drop the launcher.
- **Android on CicadaTAK WiFi:** DHCP gives no gateway (option 3 empty) and a
  classless route (option 121) that Android ignores, so Android cannot reach
  10.41.250.10. Decide: give a gateway, answer Android's connectivity check, or a
  per-node local TAK address. Needs owner decision (CicadaTAK networking).
- Users client waits 5 s and API 30 s; certificate/package generation needs longer.
- Unknown client on Node01 (10.41.0.222) connecting to 10.41.250.10:8089 — identify.
- Revert the S22 to DHCP (static .209 is inside the DHCP pool).
- Node01 host package is still the 0928 build; update before any Node02->Node01 move,
  and fix Node01's USB 3 port faults first.

## Overnight session 2026-09-30 (UTC 03:00–) — unattended, Node02 only

Installer after these changes: `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`.

| Check | Result |
|---|---|
| Backup via API with note | PASS: portabletak-20260930_032839.tar.gz, 79 KB, checksummed, labelled |
| Verify via API | PASS: checksums + dump valid; details returned |
| Restore via API (after creating tempuser) | PASS: tempuser and its certs gone; tester1 cert, CA fingerprint, config hash identical; 7/7 RUNNING; both phones reconnected without action |
| Start from fully stopped foundation | PASS: all steps READY in 72 s |

Fixes deployed to Node02 tonight (all in the installer above):
10. Users client waits 170 s for the worker (was 5 s); API allows 180 s (was 30 s).
11. Emergency stop releases the service address; network tool can release/status with adapter defaults when the cartridge config is gone.
12. API parse_status handled string output as characters (backup/verify/restore returned {"": ""}).
13. Restore and start hard-coded ASGARD's postgres uid:gid 102:104 (clean base: 101:103). Now read from the cartridge's /etc/passwd. Would have broken the first cold boot.

Operator note: when restarting the CoT parser by hand, stop its worker too; an orphaned
worker (ppid 1) blocks stop/restore (the stop script's safety check caught it).
14. **First-run timezone now defaults to UTC** (decision 2026-09-29). The setup
    wizard and helper no longer ask for it; `timezone` stays optional in the API so
    older attempts (e.g. the America/Chicago cartridge) can still retry. Wizard JS
    5/5 (Node), API 12/12 (FastAPI), controller/engine suites pass. Deployed to Node02.
    A "change server timezone" feature remains a follow-up.

Next hands-on steps (need the operator): reboot/autoload on Node02, safe removal and
reinsertion, revert the S22 to DHCP, then decide the Android phone-network option
(see the field-extension integration spec, §5).

## Overnight session 2, 2026-09-30 (UTC 04:00–05:25)

Installer at end: `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`.
Node02 has received the individual fixes marked (Node02) below, not yet this full package.

### Power loss / reboot (answers "does it come back by itself?")
| Check | Result |
|---|---|
| Before tonight | Autoload installed but **not enabled**; start/restore hard-coded ASGARD's postgres uid (cold start would fail) |
| Reboot #1 (services killed, like a power cut) | Autoload found cartridge but **refused to mount**: `e2fsck -fn` rc=4 (free-inode count, orphan flag — routine journal bookkeeping) |
| Fix | Mount script preens (`e2fsck -p`) only if check-only fails, **refuses on device I/O errors or preen rc≥4**, re-checks before mounting (Node02) |
| Manual boot sequence after fix | PREEN_RC=1 → FILESYSTEM=RECOVERED → 7/7 RUNNING in ~90 s |
| Clean-shutdown hook | `/etc/init.d/portabletak-autoload stop` runs safe-remove + address release at K10 (Node02) |
| **Reboot #2, hands-off** | **PASS**: clean shutdown (no preen needed), autoload → 7/7 RUNNING, phone reconnected on its own |
| Other boot fixes | Stale `postmaster.pid` removed only when no cartridge postgres runs (Node02); boot runs in background and waits for `br-ahwlan` (Node02); `LD_PRELOAD` from procd unset (package) |
| Simulated power cuts (VM, 5×, heavy metadata) | check-only rc 0 each time; real drive differed (rc 4) — hence the preen path |

Autoload is **enabled on Node02 only**. Do **not** enable it on Node01 while ASGARD's drive is attached (it would start ASGARD).

### Clean base v3 (release)
`portabletak-clean-arm64-20260930-v3.tar.gz`, archive `670e7b15…21861`, seal `6b1f3209…53ae5`.
v2 + reviewed `cot_parser.py` credential patch (build patch + derivation script + tests).
Seal diff vs v2: exactly cot_parser.py changed + .upstream added. Loop-device layout/publish/preflight PASS.
Hosts on this package accept only v3-laid cartridges for publish/provision.

### Generic Linux host profile (Windows/WSL2 path) — Ubuntu 24.04 container on the Mac
`/etc/portabletak/host-profile` (`PT_PROFILE=linux`), `adapters/linux/`, `lib/host-profile.sh`,
`/proc`-based process listing (procps `ps` only shows the terminal's processes).
| Check | Result |
|---|---|
| Layout v3 → publish → provision (all phases, one run) | PASS |
| 7/7 RUNNING, HTTPS on host address, CoT 8089 | PASS |
| Two TLS clients, A's position reaches B | PASS |
| Enrollment 8446: config, sign CSR, wrong/no password 401, non-enrollment paths 404 | PASS |
| Stop → boot sequence → 7/7 RUNNING | PASS |
| Relay after that restart | **FAIL — open item**: B did not receive A after restart in the container (needs investigation; on Node02 the phones reconnected after reboot/restore but cross-visibility after restart was not re-checked) |
| Health false "PostgreSQL RUNNING" after stop | found & fixed: the grep pattern matched itself (package) |

### QR onboarding (ATAK, S22)
- 8446 enrollment listener added to provisioning (package) and to Node02's running cartridge (Node02).
- ATAK 5.7 scanned the QR, got a certificate signed (`signClient/v2` 200) and connected:
  **`qrtest is ID'ed by cert`** at 05:05 and 05:11.
- ATAK enrolls twice; single-use tokens made the second call fail → token now allows 3 uses in 15 min (Node02 + package).
- **PASS 05:30 UTC:** after re-establishing trust in the CA, ATAK scanned, enrolled twice (both 200 with the
  3-use token) and connected as **`qrtest`**, staying connected (>1 min, no disconnect).
- Root cause of the intermediate "identity could not be verified": ATAK had no trusted CA after its
  certificates were cleared, so it aborted the TLS handshake on 8446 (no HTTP request reached nginx).
  **Product follow-up:** provide a CA-only "trust this server" package/QR (no user certificate) that devices
  import once; QR enrollment then works for any user. `/api/client/truststore` already serves the CA p12.
- Upstream note: `/Marti/api/tls/config` answers without auth (returns only O/OU); signing requires auth.

### Also noticed
- Something polls `POST /ubus/` on nginx every 5 s (404) — likely a browser tab pointed at the service
  address expecting LuCI; harmless, identify later.

### Next session
1. Install this package on Node02 (brings host profile, pt_ps, health fix, boot/LD_PRELOAD) and run one stop→boot cycle under BusyBox.
2. Investigate relay-after-restart (container), then re-check phones see each other after a Node02 reboot.
3. QR retest (clean ATAK or iTAK). 4. Android networking decision (spec §5). 5. x86_64 clean base for Windows.

### Relay after restart — investigation state (2026-09-30 05:45 UTC)
- Synthetic TLS clients `relay1`/`relay2` (created on Node02; certs under ca/certs/relay*/; test script
  `/tmp/relay_check.py` on Node02 — A sends a new `RELAY-<rand>` uid, B must receive it within 45 s).
- Restart #1 (boot after an interrupted script) → relay check ~2 min later: **PASS**.
- Restart #2 (clean stop → unprepare → umount → portabletak-boot, all 7 RUNNING) → relay **FAIL**,
  repeatedly (15 s and several minutes later; a 3 s pause after connecting did not help).
- CoT parser log: `ForeignKeyViolation ... Key (sender_uid)=(RELAY-…) is not present in table "euds"`
  — the sender device is never registered, so its CoT is dropped. EUD handler: authenticates both by
  cert, logs no errors. Same symptom as the container run (relay FAIL after stop→boot).
- Hypotheses to test next: (1) EUD registration happens in a component/worker that is not restarted or
  not reconnected after the stack restart (e.g. a RabbitMQ queue/exchange bound only on first start, or
  the users worker / a background task that inserts `euds`); (2) the `euds` insert is done by the CoT
  parser from a different message path that now fails first; (3) stale RabbitMQ state after restart.
  Compare `euds` rows, RabbitMQ queues/bindings (`rabbitmqctl -n rabbit@Node02 list_queues/list_bindings`
  — Node02's v2 cartridge node is rabbit@Node02), and which process inserts into `euds` in OTS source
  (`grep -rn "EUD(" opentakserver/`).
- Real phones after Node02 reboots/restore showed as Connected, but cross-visibility after a restart was
  not re-verified — treat as **unverified** until this is resolved.
- **Lead (source read 05:50 UTC):** in OTS `eud_handler/EudHandler.py::parse_device_info` (~l.485–700) the
  per-device queue declare/bind **and the `euds` insert** run only inside
  `if self.rabbit_channel and self.rabbit_channel.is_open ...`. If a connection's RabbitMQ channel is not
  open (or never opens after a restart) the device is silently never registered → CoT parser FK
  violation → no relay. Next: on Node02 after stop→boot, check `rabbitmqctl -n rabbit@Node02 list_connections
  user state` while relay1/relay2 are connected, and EudHandler log lines around `on_connection_open` /
  `on_channel_open` / `Connection closed`; compare with a working run. Likely host-side causes: RabbitMQ
  started/ready before definitions import finished, or the EUD handler started before RabbitMQ accepted
  the `portabletak` user (start script only waits for the 5672 listener).

## 3am continuation, 2026-09-30 (UTC 08:07–08:25)

Installer at end: `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2` (Node02 runs this package, installed via install.sh, except the last
host-profile.sh noise fix which was copied in directly and is identical to the package copy).

### Relay after restart — RESOLVED: it was a test artifact, not a server bug
- `euds` has `UNIQUE (callsign)`. The relay test gave client A a **new uid every run but the same
  callsign "RA"**; after the first run every new registration failed on that constraint (silently in the
  EUD handler), so the CoT parser then hit the FK error. The restart was coincidental. The container test
  had the same flaw (callsign "ALPHA").
- With a unique callsign per uid: **relay PASS twice immediately after a full stop → boot** on Node02.
- **Operator note (real):** two devices with the **same callsign** cannot both register — the second will
  neither send nor receive. Every device needs its own callsign. Add to the user guide.
- Also found by reading upstream EudHandler: device registration is attempted only on the first message
  and the uid is recorded before checking the RabbitMQ channel is open, so an early first message can skip
  registration permanently. Added a **defensive** launcher (`chroot/eud_handler_launch.py`, generic
  cartridges only) that defers registration until the channel is open; simulation shows upstream never
  registers in that case and the patched handler registers exactly once. It was **not** the cause above.
  Candidate for the clean base v4 alongside the cot_parser patch.
- e2e scripts fixed to use uid-unique callsigns (re-run of the container test is pending).

### Full package on Node02
| Check | Result |
|---|---|
| sha256, install.sh --check, install | PASS / COMPLETE; backup host-20260930_081740; gate CLOSED; autoload still enabled |
| Health after stop | 0 running (self-matching postgres pattern fixed) |
| stop → unprepare → umount → portabletak-boot | rc 0; COT + EUD launchers active; 7/7 RUNNING |
| Relay after that restart (unique callsigns) | PASS, PASS |
| pt_ps "can't open /proc/N/cmdline" noise under BusyBox | fixed (redirect grouped); 5 health runs clean |

### Needs the operator at 4pm
1. Phones after a Node02 reboot: confirm iTAK and ATAK see each other (use distinct callsigns).
2. S22: switch back to DHCP after testing; decide the Android route option (field-extension spec §5).
3. Optional: iTAK QR onboarding; CA-only "trust this server" package design.
4. Windows: an x86_64 Windows 11 laptop when ready (see portabletak-windows-wsl2-plan.md).

### Lifecycle lock held by services after boot — found and fixed (08:40 UTC)
- Container rerun (uid-unique callsigns): provisioning, relay before and after stop→boot, enrollment
  checks all PASS; but **backup refused: lifecycle_busy**.
- Cause: `portabletak-autoload` holds the lock on fd 9 and ran the start tool with fd 9 inherited, so
  PostgreSQL, RabbitMQ, OTS etc. held the lock for their whole life (Node02: 22 holders). After any boot,
  backup/restore/layout/provision would be refused until shutdown. Earlier backup tests passed only
  because services had been started by hand.
- Fix: autoload runs detect/discover/mount/start with `9>&-`. Node02 after stop→boot: **0 holders**,
  backup COMPLETE, relay PASS. e2e test now fails if backup or verify fails.
- Installer at end: `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2` (Node02 has this autoload; otherwise identical to what install.sh installed).

### Operator check after Node02 restart (2026-09-30 ~15:51 UTC)
- Node02 restarted (uptime 524 s at check) and the server came back **without any manual action**.
- iPhone (iTAK, SL) and S22 (ATAK, TITAN, still on static IP) both reconnected; operator confirms
  **they see each other**. CoT parser: no errors. → "phones see each other after a restart": **PASS**.
- Cosmetic: `euds.last_status` for SL read "Disconnected" while its connection was live (status lag).
- Still open: S22 back to DHCP + Android route decision (spec §5); iTAK QR; CA-only trust package;
  Node02→Node01 move; replacement-media restore; real power-pull test; x86_64 base for Windows.

## Multi-address server (requirement: work independent of the field extension, and off-mesh) — 2026-09-30 16:15 UTC

Owner requirements: Android must work without relying on the network's DHCP/routes; the server must also
be reachable on ordinary WiFi/Ethernet and over cellular. Design: **one server, several addresses**, one
certificate listing them all, packages/QR advertise a chosen address. VPN for cellular: **Tailscale**.

Step 1 (done, Node02, installer `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`):
- `lib/server_addresses.py` + `bin/portabletak-server-addresses {show|set --advertise A [--also B…]}`:
  re-signs the existing server CSR (same key → QR token key unchanged, clients keep trusting the CA) with
  all addresses as SANs, verifies chain/key/SANs before swapping, sets OTS_SERVER_ADDRESS (packages/QR),
  records config/server-addresses.json, and makes nginx listen on every address. 5 tests (real openssl).
- Start script accepts a wildcard nginx listener.
- Node02: advertise 10.41.254.1, also 10.41.250.10 → strict TLS verification OK on 10.41.254.1:8443/8446/8089
  and 10.41.250.10:8443; 7/7 RUNNING after restart.
- **S22 on plain DHCP (no static IP, no gateway) connects to 10.41.254.1:8089** with package droid1;
  iPhone on 10.41.250.10; operator confirms it works. → Android independent of host DHCP: **PASS** (single node).

Remaining steps:
2. Provisioning should do this automatically for new cartridges (adapter supplies the address list; nginx
   wildcard from the start). 3. **Relay role** for non-hosting mesh nodes (forward 8089/8443/8446 from the
   node's 10.41.254.1 to the server over the mesh) — needs the host package on each node.
4. **Tailscale adapter** + add the VPN address to the certificate; test from a phone on cellular.
5. Multi-connection packages (node-local + LAN + VPN in one package); check what iTAK accepts.
6. CA-only "trust this server" package for QR onboarding.

Step 2 (done, installer `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`): **new cartridges are multi-address automatically.**
- `HostAdapter.server_addresses()` + validation. OpenMANET: advertise 10.41.254.1, also 10.41.250.10
  (`node_local_address` configurable). Linux profile: its host address.
- Certificates phase re-signs the server certificate for the adapter's list right after the CA is created
  (evidence records the addresses); nginx listens on every address from the start.
- All openssl calls non-interactive (stdin closed, batch, explicit passin) — fixes an intermittent
  passphrase prompt seen once in tests.
- Tests: 183 local pass (new: certificates phase publishes adapter addresses; adapters declare addresses).
- Container (generic Linux, real openssl in the cartridge): full e2e PASS incl. relay before/after restart,
  enrollment checks, backup COMPLETE, verify VERIFIED.
- Not yet on Node02 (Node02's cartridge was converted with the step-1 tool; this affects new cartridges only).

## Step 4 — Tailscale, cellular access: **PASS** (2026-09-30 ~17:55 UTC)
- Tailscale 1.102.4 official static arm64 (sha256 9dd1e6a5…c4c3f, verified vs pkgs.tailscale.com) at
  /usr/local/portabletak/share/tailscale on Node02. `tailscaled --tun=userspace-networking` (Node02 has no
  /dev/net/tun; no firewall/route changes needed; inbound tailnet TCP is handed to local ports).
- **State on the cartridge**: /mnt/asgard/config/tailscale → the server's tailnet identity moves with the
  cartridge. Node `portabletak-a21f50bc`, 100.93.136.26, portabletak-a21f50bc.tailc13345.ts.net
  (owner approved the login link themselves).
- Node02 has no internet: for this test Tailscale reached its servers via a CONNECT proxy on the Mac
  restricted to *.tailscale.com/*.tailscale.io, reverse-tunnelled to Node02's loopback (127.0.0.1:18888).
  Field use needs Node02's own internet (USB WiFi adapter in client mode, or Ethernet) — no free radio today.
- Server certificate now: 10.41.254.1, 10.41.250.10, 100.93.136.26, portabletak-a21f50bc.tailc13345.ts.net.
- **S22 on cellular only (WiFi off), Tailscale app connected, ATAK server 100.93.136.26:8089 SSL → green.**
- Not yet productised (manual today): start/stop tailscaled from the host package with the cartridge
  (start after mount, stop before safe-remove/unmount — it holds files on the cartridge), auto-add the
  tailnet addresses to the certificate, and a proxy-free internet path on Node02.

Tailscale now follows the cartridge (installer `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`, deployed to Node02):
- `bin/portabletak-tailscale start|stop|status`: no-op unless the host has the binaries under
  share/tailscale **and** the cartridge has joined a tailnet (config/tailscale exists) — opt-in per cartridge.
  Userspace networking; optional `PT_TAILSCALE_PROXY` in /etc/portabletak/host-profile (Node02: the Mac proxy).
- Hooks: `portabletak-start` starts it after the TAK server is READY (never fails the start);
  `portabletak-stop` (and so safe-remove) stops it first so unmount works; emergency stop stops it too.
- Node02: stop → Tailscale STOPPED; start → 7/7 RUNNING and Tailscale RUNNING at 100.93.136.26 again.
- Binaries are not yet in the installer payload (35 MB; kept separate under work/tailscale/).
- Next: auto-add the tailnet address to the server certificate on first join; Node02's own internet.

"Enable remote access" (installer `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`, deployed to Node02):
- `portabletak-tailscale enable` (opt the cartridge in, start tailscaled, print the login link) and
  `apply-address` (after approval: add the tailnet IP + MagicDNS name to the server certificate, keep
  existing addresses and the advertised one, restart the TAK services).
- API: GET /api/remote-access, POST /api/remote-access/enable, POST /api/remote-access/apply.
- Dashboard: `web/remote-access.js` card (Enable remote access → login link → Finish setup /
  Update server certificate); script allowed before sign-in by the guard.
- Node02: status RUNNING 100.93.136.26; enable → LOGGED_IN; page serves the card. Browser click-through
  and apply-address via the button: pending operator check.
- Remote Access card is collapsible (same markup as Backup / Restore) and sits directly after Client Setup. Installer: `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`.

Tailscale shipped with the host installer (installer `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`, 58.6 MB, installed on Node02):
- share/tailscale/{tailscale,tailscaled} = verified 1.102.4 arm64 build (+ SOURCE note with upstream URL
  and archive SHA-256); covered by the installer manifest; install.sh sets modes.
- install.sh stops tailscaled before replacing files (a running binary cannot be overwritten) and starts it
  again afterwards if the mounted cartridge has remote access enabled (no-op otherwise).
- Node02: install rc 0, backup made, gate CLOSED; Tailscale RUNNING at 100.93.136.26 before and after;
  TAK services 7/7 untouched; binary hashes match the verified download.

## Multi-connection client packages (2026-09-30)

- New `lib/client_packages.py`: rewrites the `cot_streams` preference of the
  generated ATAK/WinTAK (`<user>_CONFIG.zip` → inner `<uid>/<user>.zip` →
  `preference.pref`) and iTAK (`config.pref`) packages so they carry one
  connection per address in `config/server-addresses.json`, advertised first.
  Certificates, manifest and other preferences are copied byte-for-byte; the
  package stored on the cartridge is not modified (bytes are rewritten when served).
- Enabled by default: the advertised address and the first Tailscale IP (so one
  package works on node Wi-Fi and over cellular). Included but disabled: mesh
  service address and MagicDNS name.
- `GET /api/client/package/{user}/{atak|itak|wintak}?address=X` returns a
  single-connection package for X (400 if X is not a served address) — fallback
  for clients that only honour one connection.
- If rewriting fails (unexpected layout), the original single-connection package
  is served.
- Tests: `tests/test_client_packages.py` (7) pass; suite otherwise unchanged.
- Installer `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2`
  installed on Node02: rc 0, services 7/7, Tailscale RUNNING 100.93.136.26.
- Verified via the Node02 API: droid1 ATAK and iTAK packages list 4 connections
  (Wi-Fi 10.41.254.1 on, mesh off, Tailscale 100.93.136.26 on, Tailscale name
  off); `?address=100.93.136.26` gives a 1-connection package; bad address → 400.
- Pending: import on the S22 (ATAK) and iPhone (iTAK); confirm connected on
  Node02 Wi-Fi and on cellular without editing the server. Watch whether iTAK
  honours more than one connection; if not, use `?address=`.
- 2026-09-30: Node02 given its own internet via Ethernet (eth0 DHCP 192.168.1.173,
  default via 192.168.1.254; ping and DNS OK). Removed `PT_TAILSCALE_PROXY` from
  `/etc/portabletak/host-profile` (backup `host-profile.before-direct-internet`);
  Tailscale reconnected directly: 100.93.136.26 online, no Mac proxy needed.

## Phone results for multi-connection packages + two fixes (2026-09-30 evening)

- ATAK (S22): one package connects on Node02 Wi-Fi and on cellular via Tailscale. PASS.
- iTAK (iPhone): only honours the first connection in a package (shows 1 server). With
  a second, Tailscale-only package (`?address=100.93.136.26`) it works on Wi-Fi and
  cellular. TODO: add an "iTAK – Tailscale" download next to the iTAK package on the dashboard.
- **Duplicate connections (bug found):** with both connections reachable (phone on Node02
  Wi-Fi with Tailscale on) each phone connects twice with one uid. Upstream OTS names the
  device's RabbitMQ queue after its uid; when either connection closed it unbound that
  shared queue and broadcast t-x-d-d, so the other side greyed the device out (iTAK saw
  TITAN grey) and the remaining connection stopped receiving.
  Fix in `chroot/eud_handler_launch.py` (sealed package untouched): each forked connection
  records itself in `/tmp/portabletak-eud-connections/<uid>.<pid>`; a closing connection
  with a live sibling closes only its own socket/channel. Tests
  `tests/test_eud_duplicate_guard.py` (6) pass. On Node02: closing one of two connections
  with the same uid → no disconnect broadcast, the other connection still sends and
  receives; closing the last runs the upstream disconnect (cot_parser logged it). PASS.
- **Icon download on first start with internet (bug found):** OTS downloads and imports
  its icon sets when the icons table is empty; with Node02 now online this outlasted the 40 s
  readiness wait → start rc 51 + rollback. `portabletak-start` now extends the wait to
  600 s while the last OTS log line is "Downloading icons". Node02: DOWNLOADING_ICONS →
  READY (~30 s), 7/7. Offline starts unchanged (download fails instantly).
- Installer `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2` installed on Node02.

## Mac setup app + node adoption (2026-09-30 night)

Owner requirement: first setup on a Mac/Windows computer; the node finishes by itself on
first plug-in, with or without internet.

- **Node adoption** (`lib/host_adoption.py`, `bin/portabletak-adopt check|apply`, hooked into
  `portabletak-start` after the cartridge checks, before the service claim). If the
  cartridge's recorded binding (`config/host.json` profile, `tak-network.conf` address/dev)
  differs from this host's adapter: run `portabletak-prepare` (idempotent), re-sign the server
  cert for the adapter's addresses **keeping Tailscale addresses**, nginx wildcard, rewrite
  tak-network.conf/host.json. Certificate first: on failure nothing changes (verified).
  Offline-safe. Tests `tests/test_host_adoption.py` (4) pass.
- **Mac setup app** (`work/mac-setup/`, bundle `outputs/PortableTAK-Mac-Setup.zip`,
  sha256 9bb30c460a331b8f0b822e3abe5d02675fe4ec24c813fae889183779c671b2ab, 565 MB):
  `PortableTAK Setup.command` → `portabletak-mac-setup`: preflight (Apple Silicon, colima +
  docker, starts colima, builds `portabletak-setup:1`), questions with retry (name, admin pw,
  client pw; 12–128 printable, must differ), drive picker (external physical only; 16 GB–2 TB;
  refuses ASGARD by ext4 UUID read from the superblock; extra warning if label PORTABLETAK;
  type `ERASE diskN`), container setup on an 8 GiB image, `dd` to `/dev/rdiskN`, `cmp`
  verify, eject, image + request file deleted.
  `container-setup.sh`: selects the clean base by the pinned hash, lays out, publishes
  identity, provisions via the job supervisor (gate open only in the container), checks 7/7,
  counts icons, stops, fsck. Runs a dnsmasq forwarder on 127.0.0.1 because the cartridge
  resolves via 127.0.0.1 (nodes have dnsmasq there) — so the icon sets download on the Mac.
- Container results: setup COMPLETE in 2 m 44 s, phases all green, 7/7, **icons 3743**.
  Adoption test on a new Linux host (172.30.0.50): adopted at start, 7/7, TLS 8443 verifies
  the new IP SAN; then as OpenMANET: SANs 10.41.254.1 + 10.41.250.10, dev br-ahwlan, fsck clean.
  Bundle dry run: checksums OK; name/password retries work; stops safely with no drive.
- Dashboard: "iTAK Remote Package" button (`?address=remote` → first Tailscale address,
  filename `_Tailscale.zip`; 404 `remote_access_not_set_up` if none).
- Installer `37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2` built;
  **not yet on Node02** (Mac lost the Node02 network). Deploy script checks adopt=CURRENT and that
  the server cert and tak-network.conf are unchanged across stop/start.
- Pending with owner: real drive write on the Mac (needs a spare USB drive — the Node02 test
  drive would be erased — and the Mac password for sudo), then plug into Node02: expect
  adoption + start offline. Follow-ups: grow the 8 GiB partition to the whole drive on the
  node (node lacks resize2fs; use the cartridge's own via its loader), Windows (WSL2) version.

### Hardware results (2026-10-01 01:00–01:30 UTC)
- **Real power loss (Node02 battery ran flat):** on recharge Node02 booted, the kernel ran orphan
  cleanup on the cartridge, autoload → RUNNING, 7/7, Tailscale 100.93.136.26. PASS.
- Installer 3dadc25a… on Node02: adopt check CURRENT, no adoption during stop/start, server cert
  and tak-network.conf byte-identical, 7/7, Tailscale RUNNING. PASS.
- **Mac setup app on a real drive** (SanDisk 3.2Gen1 123 GB, disk6): setup 7/7, icons 3743,
  write 8.6 GB in 71 s (121 MB/s), verified, ejected. (macOS "not readable" dialog: owner
  clicked Initialize → Disk Utility opened; nothing erased.) `cmp` verify on the raw device was
  very slow (~10 min) → replaced with a 4 MiB-block `dd | shasum` comparison.
- **Mac-built drive in Node02, no internet involved:** old cartridge safe-removed, SanDisk
  inserted, `portabletak-boot`: ADOPTION APPLIED linux → openmanet, SANs 10.41.254.1 +
  10.41.250.10, service IP 10.41.250.10 on br-ahwlan, 7/7, instance "TestTAK". PASS.
- Old Node02 test cartridge (a21f50bc) is out of Node02; keep it.

## Dual-chip cartridge — step 1 (2026-10-01)

- **x86_64 software tree built** on the Mac (linux/amd64 container under Rosetta) from the
  official ubuntu-base-24.04.5-base-amd64 (SHA256SUMS OK; arm64 entry in the same file matches
  our pinned arm64 base), Ubuntu archive packages, PyPI pinned to the arm64 freeze
  (`-c constraints.txt`), patch-ots (reviewed source hashes identical), finalize.
  `work/amd64-build/out/amd64-tree.tar.gz` (550 MB) + provenance-amd64.json.
  MediaMTX v1.13.0 linux_amd64 checksum OK.
- Compatibility: postgres 101:103, rabbitmq 102:104, www-data 33:33 — all passwd/group IDs
  identical; all 195 Python packages identical; PostgreSQL 16.15, RabbitMQ 3.12.1, Erlang 25.3.2.8,
  nginx 1.24.0, glibc 2.39 identical (openssl one patch newer on amd64).
- Layout: arm64 stays in `rootfs/`, `opentak/venv`, `mediamtx/`; x86_64 in `arch/amd64/{rootfs,
  venv,mediamtx}`. Data shared. Host picks by CPU: `lib/pt-rootfs`, `host_profile.rootfs()`;
  prepare binds `arch/amd64/venv` over `/asgard/opentak/venv` inside the x86 chroot;
  `arch_sync.py` mirrors provisioned rootfs config (rabbitmq, nginx site, log dirs) from rootfs/
  to the x86 tree (at prepare on x86 hosts, and during provisioning); MediaMTX binary by CPU in
  start/stop/health; clean audit covers both trees. Tests `test_arch_sync.py` (3).
- Bugs found and fixed: start/restore used `$ROOT/var/run/postgresql`; amd64 ubuntu-base has
  `var/run -> /run` (absolute), so the host's /run was used → now `$ROOT/run/postgresql`.
  nginx under Rosetta keeps title `/usr/sbin/nginx` → pid checks accept that for the master
  (PID comes from nginx.pid).
- **Cross-chip test PASS:** cartridge provisioned on arm64 (Mac container), then started with the
  x86_64 software on an x86_64 container: rootfs arch/amd64 chosen, 7/7 RUNNING, icons 3743 and
  the administrator account read from arm64-written PostgreSQL data, users created, client
  packages generated, TLS relay B received A, stop clean, fsck clean.
- Next: derive sealed dual-chip clean base v4 (v3 + arch/amd64, var/run → ../run), pin it,
  switch the Mac setup to v4, then Windows (WSL2) launcher.

## Owner decisions + features (2026-10-01 early UTC)

- **Password minimum = 8** (OpenTAKServer's default; owner chose it over 12 and 6) for administrator
  and client passwords everywhere: provisioning validation, management login, bootstrap admin,
  wizard, provision-request tool, Mac setup (warns when < 12, recommends 12+). Test updated.
- **Erase Drive (factory reset):** `lib/factory_reset.py`, `bin/portabletak-factory-reset --confirm
  ERASE`, `POST /api/factory-reset` (session + administrator password again + "ERASE"),
  dashboard card `web/factory-reset.js` (collapsible, last on the page). Checks every sealed file
  first (damaged software → refuse before deleting), deletes everything not sealed (database,
  RabbitMQ, CA/users, config incl. Tailscale, logs, on-drive backups), restores sealed modes,
  writes a fresh cartridge ID with state `new`, then **strict** seal verification. Refuses
  non-generic cartridges (never ASGARD) and mounted runtimes. Tests `test_factory_reset.py` (4).
  Container test: running provisioned cartridge → reset → strict seal OK vs pinned v3 → re-provisioned
  "Reprovisioned" 7/7 → fsck clean. PASS.
- Master folder policy: drives are made from the setup folder (sealed clean base), each with its own
  CA and secrets; never clone a provisioned drive. Planned: "make blank drives" option.
- Mac setup final message no longer assumes a node.

## Dual-chip clean base v4 — sealed and pinned (2026-10-01)

- `build/derive-dual-base.py`: v3 (proven by archive digest + seal) + x86_64 tree (digest) +
  MediaMTX linux_amd64 (digest) + amd64 provenance → `arch/amd64/{rootfs,venv,mediamtx,provenance.json}`;
  var/run & var/lock made relative like arm64; dependency test suites scrubbed (the audit caught
  gevent's sample private keys on the first attempt); clean audit; re-sealed; packed; proven again.
- **v4:** `outputs/portabletak-clean-base/portabletak-clean-dual-20261001-v4.tar.gz`
  archive 987589e72cd17b40127d79f9f11b8029075e32c5ba144ae6b8f97b3cf2c08a5b,
  seal 8c1a1c36c5389939ff7c586ff5ea3e29b0b265effea44664a0f002a402d5e2e6, 84,915 members, 3.19 GB payload.
  Pinned in `lib/release.py` (v3 kept in the comment).
- Mac setup with v4: COMPLETE in 5 m 39 s, 7/7, icons 3743. The same image on x86_64, **no manual
  steps**: arch/amd64 chosen, ADOPTION APPLIED, 7/7, icons + admin read, users + packages created,
  TLS relay PASS, stop clean, fsck clean.
- Node02 regression with the chip-aware host package (single-chip v3 SanDisk): adopt CURRENT,
  stop/start 7/7, cert + network config unchanged, pt-rootfs → rootfs / arm64. PASS.
- Bundle `outputs/PortableTAK-Mac-Setup.zip` (now with v4, 1.12 GB) sha256
  bfa61d4bf884cc22e4c79e6a0b49c94e4638b7f0a8aa3d9483e4cb8b4c6e12b2.
  Installer 37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2 (not yet on Node02;
  Node02 runs 66bd3fe5…, identical except the v4 pin).
- Existing v3 drives keep working (adoption/start do not check the base pin; Erase Drive uses the
  drive's own seal). Re-provisioning an erased v3 drive with a v4-pinned host would be refused by
  the seal pin — recreate it with the setup app instead.
- Next: Windows (WSL2) host package + launcher, "Start here" readable partition, auto-start helper,
  dashboard auto-open; Mac run; node QR (needs owner decision on exposing the dashboard); guides.

## v4 drive on Node02, HTTPS dashboard, gateway mode (2026-10-01 ~03:30 UTC)

- Mac setup with the v4 bundle onto the SanDisk ("MacTAK"; owner paused internet → ICONS=0;
  new block-checksum verify "Verified."; existing-PortableTAK warning shown). In Node02:
  ADOPTION APPLIED linux → openmanet, OTS DOWNLOADING_ICONS → READY via Node02's Ethernet,
  7/7, arch/amd64 present, listening 8089/8443/8444/8446. PASS.
- **Dashboard sign-in now enforced** (`PORTABLETAK_MANAGEMENT_AUTH=enforce` in the API service;
  it was `off` before — safe only because it was loopback-only). uvicorn `--proxy-headers
  --forwarded-allow-ips 127.0.0.1`.
- **HTTPS dashboard on 8444** (`lib/dashboard_site.py`, written at start before nginx; proxies to
  127.0.0.1:8090 with the cartridge's server cert; X-Forwarded-For set, not appended). Verified
  from the Mac over Wi-Fi: page 200 with the drive CA validating 10.41.254.1; API without login
  401; wrong password 401 + audit log shows real client 10.41.254.120; cross-site Origin 403;
  Erase without login 401. First-run setup stays loopback-only (guard rule). Tests
  `test_dashboard_site.py` (2). Node QR target: https://10.41.254.1:8444/
- **Gateway mode** (`bin/portabletak-gateway auto|on|off|status|disable`, hotplug
  `/etc/hotplug.d/iface/95-portabletak-gateway` on wan up/down): with uplink internet, LAN DHCP
  gives router/DNS 10.41.254.1 and option 121 incl. 0.0.0.0/0; without, the node's original
  mesh-only options are restored (saved once in /etc/portabletak/dhcp-lan-options.original).
  Node already NATs lan→wan. Node02: SHARING (Ethernet up). Owner asked for this to save hotspot data.
- Installer 37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2 on Node02.

## Overnight 2026-10-01: Windows host, drive layout, guides, growth

**Drive layout (setup app):** partition 1 = FAT32 "PORTABLETAK" Start-here (1 GiB; exFAT not
possible: the Colima VM kernel has no exFAT), partition 2 = ext4 cartridge. Nodes find the cartridge
on any partition (discover scans all). Visible on Windows/Mac: Start PortableTAK (Windows).cmd,
Scan to open the dashboard (node).png (https://10.41.254.1:8444/), PortableTAK Quick Start.pdf,
PortableTAK Quick Reference.pdf, PortableTAK Setup and Troubleshooting Guide.pdf. Hidden (FAT H
attribute) `.portabletak/` = windows launcher + WSL host tarballs. Mac setup checks all partitions
for ASGARD/PortableTAK labels. Tool image now `portabletak-setup:2`, built --platform linux/arm64.
**Blank drive option** (Mac setup choice 2 / PT_BLANK=1): software + Start-here, first setup later on Windows.

**Windows (WSL2) host:** `work/wsl-host/` builds `portabletak-wsl-{amd64,arm64}.tar.gz` (81 MB each)
from ubuntu-base 24.04.5 + python3, e2fsprogs, fdisk, util-linux, iproute2 + FastAPI/uvicorn pinned to
Node02's versions in /usr/local/portabletak/python + the host payload (no OpenWrt Tailscale, no gateway
script). wsl.conf boots the API (enforced auth, proxy headers); helpers portabletak-wsl-{boot,api,
start,stop,provision}. Launcher `start-here/.portabletak/windows/PortableTAK.ps1` (+ .cmd): copies
itself to %LOCALAPPDATA%\PortableTAK (the drive vanishes from Windows when attached), elevates,
checks Windows ≥ 22H2 / WSL (offers `wsl --install`), sets `.wslconfig` networkingMode=mirrored,
Hyper-V + Windows firewall rules (TCP 8089 8443 8444 8446 8554 1935 8888 8889 8890; UDP 8000 8001
8189 8890), imports the distro for the CPU, finds the disk by the PORTABLETAK volume (never boot/
system disk), `wsl --mount \\.\PHYSICALDRIVEn --bare`, picks the Wi-Fi/Ethernet address, starts,
opens http://localhost:8090; desktop shortcuts Stop / Status / Dashboard; blank drive → asks name
and passwords in the console (retries) → portabletak-wsl-provision via stdin; helper
`install-helper` = logon scheduled task watching volume arrival.
- Container tests (the exact WSL rootfs, drive attached as a whole disk like --bare):
  amd64 and arm64: drive found on p2, AUTOLOAD RUNNING, 7/7, correct rootfs per CPU, HTTPS
  dashboard 401 before login → login → API OK, user created via dashboard, safe stop. PASS.
- Parallels Windows 11 VM (22621 ARM64, PowerShell 5.1): launcher parses with 0 errors; functions
  run: build 22621, no drive found (none attached), address chosen, admin. The VM pauses itself when
  idle (Parallels) — resume before prlctl exec; left suspended as found. WSL2 cannot run in this VM
  (M2 has no nested virtualization) → real Windows laptop needed for the WSL part.

**Use the whole drive:** `lib/grow_cartridge.py`, run by portabletak-start (1c) before services:
if the cartridge is the last MBR partition with ≥1 GiB after it → sfdisk -N (no reread) + BLKPG
resize + online resize2fs from the cartridge's own rootfs via its loader. Refuses protected
filesystems (ASGARD UUID list) and non-generic cartridges. Container tests arm64 + amd64:
7.8 → 19 GB, fsck clean, idempotent, Start-here intact. **Node02 real hardware (MacTAK SanDisk):
7.8 G → 112.6 G online, 7/7. PASS.** Tests `test_grow_cartridge.py` (3).

**Guides:** `work/guides/*.html` → PDF via headless Chrome: Quick Start (2 pp), Quick Reference
(2 pp), Setup and Troubleshooting Guide (6 pp).

**Fixes found tonight:** installer readiness poll used /api/status (now 401 with enforced auth) →
`/api/auth/session` (install rc 27 earlier was this; API was fine). bootstrap-prepare (first setup)
did not bind the x86_64 venv → x86 first setup failed at administrator_initialized
(platform_command_failed) → fixed. BrokenPipe tracebacks in Node02's log come from CicadaTAK's
cicada-field-dashboard, not PortableTAK.

Node02 installer 37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2 (gateway SHARING, 7/7).
- **Blank drive → first setup on Windows (x86 WSL host, container): PASS.** AWAITING_ACTION /
  state new → answers via stdin → portabletak-wsl-provision → COMPLETE → provisioned, 7/7 on
  arch/amd64, x86 tree has rabbitmq + nginx + dashboard config (arch_sync during provisioning),
  stop + restart RUNNING. Two fixes: bootstrap-prepare now binds the x86 venv; the helper polls
  setup progress from local files (the API status route correctly requires sign-in once the
  administrator exists).
- **Two PortableTAK drives attached → refuse:** discover counts all cartridges; >1 →
  PORTABLETAK_STATE=MULTIPLE / multiple_cartridges (rc 41); autoload →
  PORTABLETAK_AUTOLOAD=MULTIPLE_CARTRIDGES and starts neither; Windows launcher explains. Tested
  with two loop drives. (Found because stale loop devices from a killed test container were picked.)
- Installer 37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2 on Node02 (7/7).
  Bundle bf7a288bf7e3ed5d15cac86b57b766a72a42b685ca17b726cd152c6d4188c1cc (outputs/PortableTAK-Mac-Setup.zip + unpacked folder).

### PortableTAK Setup for Windows (make drives on Windows 11)
- `work/win-setup/` → `outputs/PortableTAK-Windows-Setup.zip` (1.23 GB):
  "PortableTAK Setup (Windows).cmd" → `setup/PortableTAK-Setup.ps1` (loads the launcher's functions
  via PORTABLETAK_LIBRARY=1; elevates; Windows 11 ≥ 22000; WSL + PortableTAK host; asks ready vs
  blank + name/passwords with retries; lists USB disks only (never boot/system), 16 GB–2 TB,
  confirm "ERASE DISK n"; copies clean base + start-here + container-setup.sh into the distro via
  \\wsl.localhost; answers written UTF-8 without BOM; `wsl --mount --bare`; runs
  `portabletak-wsl-make-drive SIZE [blank]` (finds the unmounted whole disk of that exact size,
  refuses ASGARD by ext4 UUID, builds with the same container-setup.sh as the Mac, dd, read-back
  sha256 verify, removes image + answers); always `wsl --unmount`.
- container-setup.sh now takes its folders from PT_PAYLOAD_DIR/PT_ART_DIR/PT_SECRETS_DIR/
  PT_OUT_DIR/PT_STARTHERE_DIR (Mac defaults unchanged), picks a non-eth0 interface under WSL
  mirrored networking, counts icons in the CPU's rootfs, and waits ≤5 min for an in-progress icon
  download.
- Container test in the x86_64 WSL host: built "Windows Made Drive" → wrote a 16 GB fake USB →
  verified → started it like Start PortableTAK: RUNNING 7/7, grown to 15 G. PASS. Mac path
  regression after the change: 7/7, icons 3743, 5 Start-here items. PASS.
- Both PowerShell scripts parse with 0 errors in the Windows 11 VM; library mode loads 4/4 helpers.
- Mac bundle 10cc982572557863dd94ffa41417d68c9b80729bef5c029ef98e3ef2c2079a7e,
  Windows bundle 36a90061b589e6f3bf45c598fc366b827c85ee17d8650ef43b259d0b3d231219.

### Waiting on the owner (morning)
1. Phone test of the node dashboard: Wi-Fi to Node02, scan the QR / https://10.41.254.1:8444, sign
   in with the MacTAK administrator password; create a user; import packages.
2. Re-make the SanDisk with the newest Mac bundle (two partitions + Start-here) and plug into Node02:
   expect adoption, growth to ~114 GB, 7/7; check the PORTABLETAK volume on the Mac shows the
   5 items only.
3. Parallels Windows VM: pass the SanDisk through and run Start PortableTAK (Windows) — tests drive
   detection, local copy, shortcuts, firewall and .wslconfig; WSL itself cannot run on an M2 VM.
4. Real Windows 11 laptop (Intel/AMD): Start PortableTAK end to end, WinTAK + phone over Wi-Fi;
   PortableTAK Setup for Windows to make a drive.
5. Decide: install QEMU (Homebrew download) to try running the server on a Mac.
6. Node01 items (relay role on a non-hosting node, Node02→Node01 move).

### ATAK trust package (for QR enrollment)
- `client_packages.trust_package()` + `GET /api/client/trust-package` (sign-in required) + Client
  Setup button "ATAK Trust Package (for QR)": same layout as server-generated ATAK packages (outer
  mission package → inner zip) with only truststore-root.p12 + preference (caLocation/caPassword);
  no user certificate, no connection. Built on Node02 from the real MacTAK truststore (2 files);
  route refuses without sign-in. **Phone test pending** (import on the S22, then ATAK QR Setup).
- Guides updated to use it. Final artifacts: installer 1e9be2c4… (on Node02, 7/7);
  Windows setup bundle 0a7b80f3…; Mac setup bundle 8ff8767d… (also unpacked in
  outputs/PortableTAK-Mac-Setup). Tests: 220 run, only the 6 container-only modules error as before.

## Running the server on a Mac (QEMU) — 2026-10-01 morning
- Owner approved installing QEMU: native Homebrew `/opt/homebrew/bin/brew install qemu` → QEMU 11.1.2
  (the `/usr/local` Intel Homebrew refused: outdated CLT).
- **Mac VM** (`work/mac-vm/`): build-vm.sh = PortableTAK WSL arm64 rootfs + Ubuntu linux-image-virtual
  6.8.0-146 + `portabletak-vm-init` (PID 1: modules, static 10.0.2.15/24 via QEMU user net, clock from
  `pt.time=` — the pl031 RTC is a module and hwclock is not installed; a first run with a 1970 clock
  left zero-dtime inodes on the test drive) + `portabletak-vm-control` (:8091, forwarded only to Mac
  127.0.0.1:18091: GET /status, GET /log, POST /stop = safe-remove + poweroff, POST /setup = blank
  first setup with the gate opened only during the call). Disk vm.qcow2 179 MB (compressed),
  vmlinuz + initrd beside it, in the drive's hidden `.portabletak/mac/`.
- **NAT-aware certificates:** host-profile `PT_ADVERTISE_IP`; LinuxAdapter(advertise=…) puts the Mac's
  address first (cert SANs, packages, QR); adoption re-signs if the advertised address changes.
  Test `AdvertiseTests` added (221 tests). API listen address configurable (`PT_API_HOST`); the VM
  listens on 0.0.0.0 (reachable only via QEMU forwards; 8090 forwarded only to Mac loopback).
- **Launcher** `Start PortableTAK (Mac).command` → `.portabletak/mac/portabletak-mac-run`: copies
  itself + VM files to ~/Library/Application Support/PortableTAK, finds the drive's disk from the
  PORTABLETAK volume (external only), sudo unmountDisk + chown /dev/rdiskN (then QEMU runs as the
  user), Mac address from the default route, QEMU hvf, VM disk snapshot=on, USB raw cache=none,
  TCP 8089 8443 8444 8446 8554 1935 8888 8889 8890 + UDP 8000 8001 8189 8890 forwarded on all
  interfaces; blank drive → questions → /setup; Desktop "Stop PortableTAK.command"; Return in the
  window stops. Test hook PT_TEST_IMAGE (image instead of a disk; no browser).
- Tests on this Mac (drive image instead of a USB disk): VM boot < 1 s; set-up drive: ADOPTION
  APPLIED [10.41.254.120, 10.0.2.15], RUNNING 7/7; ports 8444 and 8089 present SANs
  10.41.254.120 + 10.0.2.15; dashboard login via the Mac's address 200; local dashboard 8090 OK;
  Desktop Stop → safe stop, VM off, drive fsck clean; blank drive → questions → setup COMPLETE →
  RUNNING → stop. PASS. Not yet: a real USB drive (needs the owner's Mac password) and phones.
- Guides updated (Quick Start section C, Quick Reference row, full guide "Using the drive on a Mac").
  Start-here now shows: Start PortableTAK (Windows), Start PortableTAK (Mac), the QR, 3 guides (363 MB
  total incl. hidden files).
- Installer 551b4bda… on Node02: adopt CURRENT, 7/7. Bundles: Windows 4c9ffe6c…, Mac 594f18e6….

## To do (owner-approved): anti-duplication licensing — after the core work
- Each drive gets a license file signed with an owner-only key (never on drives or in bundles), bound to
  the USB stick's hardware serial; node/Windows/Mac hosts verify signature + serial before starting;
  setup apps need the key to make drives. Limits explained to owner (bit copies of the same drive,
  patched hosts, fake serials; OpenTAKServer is GPL v3 → only PortableTAK's own parts/branding can be
  restricted; license agreement + trademark; lawyer).
- Also pending from this morning: remote access on computers uses the computer's Tailscale app
  (PT_EXTRA_IPS; tests 222); stale-session cleanup in both launchers; dashboard unplug note
  (needs the next VM/WSL/installer rebuild); remote-access walkthrough in all guides.

### Remote access on computers + dashboard caching (2026-10-01 ~11:00)
- Owner test on the Mac with the SanDisk: ATAK connected over Tailscale through the Mac's own
  Tailscale app (100.83.127.3). PASS. Dashboard initially showed the old Remote Access card: Safari
  kept a cached copy of remote-access.js across the update (Option-Cmd-R showed the new card:
  "On (this computer's Tailscale)"). Fix: GET / now returns index.html with content-hashed script
  links (?v=) and Cache-Control: no-store.
- Computer remote access: launchers detect the computer's Tailscale (100.64/10) address → VM
  pt.extra / wsl-start arg → PT_EXTRA_IPS → cert SANs + packages; adoption re-signs on change.
  Remote Access card explains node vs computer steps; Enable hidden on computers.
- Dashboard Server section note: Safe Remove before unplugging; Stop Server only pauses services.
  Launchers clean up a stale session whose drive was unplugged without stopping.
- Installer 37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2 (not yet on Node02). Bundles: Windows 1ee86e92…, Mac 03a96ff3….
- SanDisk updated in place (VM + launchers + WSL + guides). Owner restarted on the Mac with a normal page
  load: Remote Access shows "On" with the Mac's Tailscale address. Cache fix PASS.

### Node plug/unplug automation (2026-10-01 ~16:20 UTC, Node02)
- Found Node02 with stale mounts + postgres/rabbit still running after the SanDisk was pulled without
  Safe Remove (to remake it); cleaned with portabletak-emergency-stop (25 processes) + lazy unmounts.
- New `/etc/hotplug.d/block/90-portabletak` (only when autoload is enabled, so never on Node01 with
  ASGARD): **add** of a partition with nothing mounted → portabletak-boot (lock prevents doubles);
  **remove** of the running cartridge's device → Tailscale stop + emergency stop + lazy unmount
  (once per unplug). Node02 tests: plugged the SanDisk into running Node02 → started by itself in ~90 s,
  ADOPTION linux→openmanet, 7/7, 111.7 G. Pulled without Safe Remove → cleanup in 22 s, 0 mounts,
  0 services/processes, address released. PASS.
- Adoption now keeps only the cartridge's own Tailscale addresses (config/tailscale/addresses,
  written by apply-address; legacy drives with tailscaled.state keep all as before). The Mac's
  Tailscale address (100.83.127.3) had followed the drive onto Node02. Tests 224.
- Owner approved (with licensing, later): drive-carried, signed node host-package updates; suggest
  bundling the host package in the owner's OpenMANET field extension.
- Installer 37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2 on Node02.

### QR enrollment on the S22, usernames and password symbols (2026-10-01 ~17:00 UTC, Node02)
- Owner: ATAK QR → "registration failed / TAK Server profile request failed", then red after editing
  the server. nginx log: enrollment worked as "Qrtest" (200, cert issued, connected); the edit used
  "qrtest" → signClient 401. Usernames were case-sensitive (phone keyboard capitalized at creation).
- Fixes: `chroot/opentakserver_launch.py` (generic cartridges; started by portabletak-start) makes
  Flask-Security username lookups case-insensitive (sealed package unchanged); new usernames stored
  lowercase (API); username fields autocapitalize/autocorrect off; password rule now forbids only
  `: & < >` (OTS enrollment splits on ':' and bleach-escapes & < >) — '@' '$' '!' allowed.
- Node02 test (real signClient/v2 CSR): user symtest / password p@$$w0rd!Sym9 → 200 as symtest,
  SYMTEST and Symtest; wrong password 401; password with ':' refused at creation. OTS runs via the
  launcher, 7/7. PASS.
- Dashboard: Safe Remove note spans the row centered; ATAK/iTAK QR pop-ups say to scan inside the
  app (not the camera), import the ATAK Trust Package first, or tap Open in ATAK on the same phone;
  administrator listed first under Users.
- Installer c5ff270b… on Node02. WSL/Mac VM/bundles need a rebuild to carry these changes.
- **Root cause of the QR trouble (nginx):** our 8443/8446 sites never asked for or forwarded the
  client certificate. OTS authenticates /Marti endpoints by the client cert forwarded in X-Ssl-Cert
  (/Marti/api/missions was 401) and nginx dropped ATAK's API_VERSION header (underscore). Capture
  (tcpdump on Node02) showed ATAK enroll OK, then open a 3rd 8446 TLS connection and close it without a
  request ("TAK Server profile request failed"), and never connect on 8089.
- Fix (`dashboard_site.TAK_TLS/CERT_HEADER`, used by the provisioning template and applied at every
  start to existing sites, idempotent): `ssl_client_certificate <cartridge CA>; ssl_verify_client
  optional; ssl_verify_depth 2; underscores_in_headers on;` + `proxy_set_header X-Ssl-Cert
  $ssl_client_escaped_cert;`. After the fix the S22 QR flow turns green on its own; /Marti/api/missions
  200; missions/invitations 200. ATAK still shows "registration failed" (its extra profile connection
  is still closed client-side without a request) — cosmetic; documented in the guides. Test 225.
- Dashboard: Remote Access now sits above Client Setup (workflow order).
- **"registration failed" solved:** the server certificate's CN was "opentakserver"; ATAK's profile
  step checks the CN against the address it connected to and aborts after the TLS handshake.
  Experiment on Node02: re-signed with the same key, CN=10.41.254.1, same SANs → owner: "registration
  succeeded". Fix: `server_addresses.reissue` now makes a fresh CSR from the same key with
  CN = advertised address (subject O/OU from OTS_CA_SUBJECT) for every issue/re-issue (provisioning,
  adoption, remote access). Test updated (CN=10.41.254.1). 225 tests.
- Installer d85218ef… built (Node02 unreachable when deploying; Node02 currently runs the hand-made
  CN=10.41.254.1 cert, equivalent).

### 2026-10-01 ~19:40 UTC — phone USB-tether uplink + a firewall mistake (FIX PENDING ON Node02)
- Reached Node02 over a direct Ethernet cable (Node02 eth_direct 10.41.253.1; Mac 10.41.253.2 manual).
  SSH needed the portabletak key re-loaded into the agent: `ssh-add --apple-load-keychain`.
- Installed installer d85218ef… then re-issued the cert properly: CN=10.41.254.1, SANs .254.1/.250.10,
  7/7, nginx client-cert blocks present.
- Node02 has USB tether drivers (rndis_host, cdc_ncm, cdc_ether). Added `bin/portabletak-phone-uplink`
  (interface phonewan, dhcp, metric 20, in the wan zone), `/etc/hotplug.d/net/30-portabletak-phone`
  (attach on a tether device), gateway hotplug also on phonewan, installer runs setup.
- **Mistake:** the first setup did `uci add_list` on the wan zone whose network option was the single
  value 'wan eth_direct' → list ['wan eth_direct', 'phonewan'] → fw4 sees a network literally named
  "wan eth_direct" → wan and eth_direct are in no zone → Node02 rejects all input on eth0 (SSH/ping on
  the direct link refused) and lan→wan forwarding/NAT is broken. Wi-Fi (lan zone) and TAK unaffected.
  Installer 427aa33e… has the bad version — superseded.
- Fixed code (not yet on Node02): rebuild the zone's network list one name per entry when it is missing
  phonewan or any entry holds merged names (words > entries). Repair on Node02 via its Wi-Fi (lan):
    uci delete firewall.wan_zone.network; for n in wan eth_direct phonewan; do uci add_list firewall.wan_zone.network=$n; done; uci commit firewall; /etc/init.d/firewall reload
  then install the fixed installer and test S22 USB tethering.
- 2026-10-01 evening (owner at home; Mac on home Ethernet + Node02 Wi-Fi): firewall repaired on Node02
  via Wi-Fi (backup /etc/config/firewall.before-zone-repair): wan_zone networks 'wan' 'eth_direct'
  'phonewan', wan_devices {eth0, usb0}; Node02 internet OK. Installer f305b337… installed: firewall
  file unchanged by the corrected setup (idempotent), gateway SHARING, 7/7.
- Owner clarified the S22 question was about tethering the Mac (macOS has no RNDIS driver → use
  Bluetooth tethering or the S22 Wi-Fi hotspot). Owner wants the node phone-uplink kept — useful for
  the OpenMANET field extension. **Test later:** S22 USB tethering into Node02 → phonewan attaches;
  pull Node02's Ethernet → uplink fails over to the phone; Wi-Fi clients keep internet.
- **Phone USB-tether uplink test (Node02, S22) PASS:** LoRa unplugged to free a port; S22 USB tethering →
  hotplug "phone tethering detected on usb0 (rndis_host)" → phonewan 192.168.134.107, default metric 20
  (Ethernet still preferred while connected). Ethernet unplugged → traffic via usb0, DNS OK, gateway
  SHARING, the Mac on Node02 Wi-Fi reached the internet (HTTP 200). Ethernet back → eth0 route, phone
  NOT_CONNECTED, LoRa back as /dev/ttyACM0, 7/7.
- Found: simultaneous uplink events applied the gateway DHCP options twice (duplicates). Fix:
  portabletak-gateway serializes with flock and share_on compares the exact option list. Deployed;
  options clean. Installer 37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2.

## To do (owner): Skydio X10D integration test
- Verify the X10D connects to PortableTAK (CoT: likely SSL 8089 with a client certificate package; note
  PortableTAK accepts SSL only — find out if the drone needs plain TCP) and shares live video into
  MediaMTX (RTSP 8554 / RTMP 1935 / SRT 8890; publishing needs credentials — confirm which drone
  option carries them), visible in ATAK's video list with the drone on the map.
- Need from owner when the drone is available: controller/app + version handling TAK, its TAK server
  fields (address/port/protocol/cert format), and its video output options.

## 2026-10-01 rebuild for the Windows laptop test
- WSL distros rebuilt with the CN fix, nginx client-cert pass-through and case-insensitive usernames: amd64 5d8e218b…, arm64 5d0a5062….
- Mac VM rebuilt (kernel 6.8.0-146).
- Bundles: PortableTAK-Windows-Setup.zip 9d71e541…, PortableTAK-Mac-Setup.zip a38615a1….
- Next: refresh the SanDisk's Start-here .portabletak files from the Mac, then do the real Windows laptop test.
- SanDisk Start-here refreshed in place on the Mac (WSL distros, Mac VM, 2 guide PDFs); every file verified byte-for-byte; ejected. Cartridge (p2) untouched.
- Windows test laptop: Windows 10 1909 x64, VT enabled → below the launcher minimum (Win11 22H2 for mirrored networking). User to upgrade to Windows 11 (or Win10 22H2 → would need a NAT/portproxy fallback, TCP only).

### Remote QR (2026-10-01 evening)
- Mac test with the rebuilt VM: adoption Node02→Mac, CN=192.168.1.189, SAN incl. Mac Tailscale 100.83.127.3, 8443 asks for client certs. ATAK QR: "registration succeeded", green on Wi-Fi. PASS.
- On cellular it went red ("IO error"): the ATAK QR enrolls one host (local address). Fix: `?address=remote` on /api/client/{atak,itak}-qr (qr_host → client_packages.remote_address; 404 remote_access_not_set_up), users_worker accepts "host". Dashboard: "ATAK Remote QR" / "iTAK Remote QR" buttons + explanation in the QR window. Guides updated (Quick Start F, full guide §5/§6, Quick Reference checklist).
- Rebuilt: WSL arm64 640ac44a…, amd64 cd957c92…, Mac VM; bundles Windows c126fe00…, Mac 87083b1b…. SanDisk Start-here refreshed and verified.
- Pending: owner test of ATAK Remote QR on cellular (watch for a "registration failed" pop-up: CN is the LAN address; Tailscale is only in SAN).
- Owner test of ATAK Remote QR (Mac, cellular): "registration failed", then green after untick/retick — as predicted: CN is the LAN address, ATAK's profile step checks CN vs. the Tailscale address.
- Fix (built, not yet owner-tested): `server_addresses.ensure_remote` issues opentakserver-remote.pem (same key, same SANs, CN = Tailscale address; stamp file keyed on main cert sha256; removed when no Tailscale). `dashboard_site.remote_blocks` adds marked copies of the 8443/8446 servers listening on <tailscale>:port and 10.0.2.16:port with the remote cert (no extra sockets; nginx dispatches by local address next to the wildcard listen). listen_everywhere leaves marked lines alone. Mac VM gets 10.0.2.16/32; mac-run forwards <Mac Tailscale>:8443/8446 → 10.0.2.16 (macOS: specific bind beats wildcard, verified). Tests 228 (+3), container-only 6 errors as before.
- One-QR idea (to try): OTS /Marti/api/tls/profile/enrollment builds a profile zip (create_profile_zip); a patch could add a per-user mission package with the Tailscale connection, so one normal QR yields both connections.
- One QR (built, not yet owner-tested): `chroot/opentakserver_launch.py` wraps OTS `device_profile_marti_api.create_profile_zip` (enrollment only): if server-addresses.json has a Tailscale address (≠ advertised) and the request carries a valid client cert (X-Ssl-Cert via nginx), the user's server-generated ATAK package (generated on first use via OTS CertificateAuthority.issue_certificate — note: that clears certs/<user>/ incl. the enrolled cert record) is rewritten to a single Tailscale connection and added to the enrollment profile as `<user>_Tailscale.zip` (manifest Content entry). Any error → the unmodified profile. Helpers unit-checked on synthetic packages.
- Mac start FAILED (opentakserver_not_ready, safe stop): the launcher imported device_profile_marti_api before opentakserver.app, whose first lines run gevent monkey.patch_all(). Fix: import opentakserver.app first, wrap create_app and install the profile hook after the app exists (hook errors only logged). VM rebuilt (arm64 only) and copied to the SanDisk; amd64/bundles still need a rebuild with this fix.
- Owner test (Mac, normal ATAK QR): "registration succeeded" (CN fix PASS: 100.83.127.3:8443/8446 present CN=100.83.127.3, LAN CN=192.168.1.189) but only one server: ATAK's profile request authenticates with Basic auth (user + QR token), not the client cert, and OTS does not check auth there. Hook now identifies the user by client cert, else Basic auth: password via flask_security.verify_password, or a QR JWT whose sub matches the user and whose hash is in the Token table (not disabled) — checked without consuming a use. vm-control /log now appends the last 20 "PortableTAK:" lines from opentakserver.log.
- Owner request: server names instead of addresses in client entries. Packages: "<server name> (Wi-Fi)", "(mesh)", "(Tailscale)", other LANs "(LAN x.x.x.x)" (client_packages.server_name/connections name=, API passes host_adoption.instance_name). iTAK QR first field = server name (+ " (Tailscale)" for remote). QR-added Tailscale entry "<name> (Tailscale)". The ATAK QR-enrolled entry is named by ATAK (address) — no name field in the enroll URI; rename in ATAK.
- Diagnostics (owner scan, Mac): hook installed; ATAK GET /Marti/api/tls/profile/enrollment arrives with client cert AND Basic auth; hook said "no Tailscale address": the OTS chroot only has /asgard/opentak and /asgard/postgres bound — /asgard/config is not visible (also users_worker's /asgard/config/tak-network.conf read never worked; it falls back to OTS_SERVER_ADDRESS). Fix: dashboard_site.write_client_info (every start) writes opentak/data/portabletak-clients.json {name, advertise, remote}; launcher + users_worker read it. Note: ATAK repeats signClient/v2 several times per scan (5+ in this capture).

### Drive's own Tailscale on every host (owner decision 2026-10-01 evening)
- Owner: computer Tailscale addresses change per computer → use the drive's own Tailscale everywhere (reverses "computers use their own Tailscale app").
- Downloaded (owner approved) tailscale_1.102.4_amd64.tgz from pkgs.tailscale.com, SHA-256 50748df1045e60b5b695f19f4c56b0da36c019948b440fb456b6584a50f0d8b9 = published .sha256. Static x86-64.
- WSL build keeps share/tailscale (arm64 from payload; amd64 replaced from work/tailscale, mounted at /tailscale). Launchers no longer detect the computer's Tailscale or pass pt.extra/PT_EXTRA_IPS; 10.0.2.16 alias and Tailscale hostfwd removed.
- Userspace tailscaled hands tailnet TCP to 127.0.0.1 → remote_blocks now listen on 127.0.0.1:8443/8446 (+ the Tailscale address) with the Tailscale-named certificate.
- host_adoption: own_addresses() from config/tailscale/addresses always kept; needs_adoption when a foreign VPN address is listed or an own one is missing (the SanDisk still listed the Mac's 100.83.127.3 and lacked its own 100.93.136.26).
- Remote Access card: same Enable → approve → Finish flow on all hosts. Guides rewritten (one flow; ATAK QR Setup/Package add the Tailscale server — one-QR still to be confirmed on the phone). Tests: 230 + container-only 6.
- **Owner test PASS (Mac, SanDisk, 2026-10-01 ~21:30):** Remote Access on the Mac with the drive's own Tailscale: Enable → approve → Finish → "Ready. Remote clients: 100.102.110.115 or portabletak-6baed64f.tailc13345.ts.net" (the SanDisk is cartridge 6baed64f; 100.93.136.26/a21f50bc was another drive). From the Mac over the tailnet: 8089 open; 8443/8446 present CN=100.102.110.115; LAN presents CN=192.168.1.189.
- **One QR PASS:** normal ATAK QR Setup → "registration succeeded", two servers, both green on Wi-Fi; log "enrollment profile includes the Tailscale connection". Wi-Fi off: Tailscale server stays green, local one red (expected).
- Cleanup: dashboard "ATAK Remote QR" button removed (iTAK Remote QR stays: iTAK has no enrollment profile); ATAK QR window explains the two servers; guides updated (stale "registration failed is harmless" text replaced).
- Node installer rebuilt with all of today's changes: outputs/portabletak-host-installer-arm64-openwrt-20261001.tar.gz SHA-256 65b071ff5e7999109f30cd4a6eb63266b34c21947ee16f20343b1f1157e2a54b (SHA256SUMS refreshed: only the 9 changed files differed). Not yet installed on Node02/Node01.
- Owner report: "Connected CoT Clients" kept climbing on every reconnect. Cause: health counted ESTABLISHED sockets on 8089, and upstream EudHandler recv() has no timeout, so phones that vanish (Wi-Fi off, cellular switch) leave half-open connections for hours (also behind slirp/userspace Tailscale, where TCP keepalive cannot see the far end). Fix: eud_handler_launch.install_idle_timeout (socket timeout 120 s + TCP keepalive 60/15/4; TAK apps send SA or t-x-c-t pings far more often), and portabletak-health counts distinct device uids with a live pid in the launcher registry ($ROOT/tmp/portabletak-eud-connections), falling back to the socket count. Dashboard label "Connected TAK Devices". Tests +2 (container check of the counter: 2 devices from 4 entries incl. a dead pid).
- Node installer repackaged: SHA-256 eb8e2afb5960f19b58225a0f78ea96f70e4a2de5aa289dd8d3cb60e48e2f47a4.
- Final rebuild (all 2026-10-01 changes incl. idle timeout/device count): WSL arm64 1dc52b38…, amd64 29d3c70f… (both carry the drive's Tailscale), Mac VM rebuilt; PortableTAK-Windows-Setup.zip 6be1e516…, PortableTAK-Mac-Setup.zip 7294d88c…. To copy to the SanDisk once PortableTAK on the Mac is stopped.
- **Owner test PASS (Mac, SanDisk, final build):** Connected TAK Devices showed 1 for the S22 with both servers (Wi-Fi + Tailscale) green, and dropped to 0 within ~2 minutes after the phone's Wi-Fi and Tailscale were switched off.
- State at end of 2026-10-01: SanDisk Start-here has the final build; Windows/Mac setup bundles 6be1e516…/7294d88c…; OpenMANET node package eb8e2afb… not yet installed on Node02/Node01.

### Node02 updated to the 2026-10-01 OpenMANET node package (2026-10-02 01:48 UTC)
- Over Node02 Wi-Fi (Mac 10.41.254.120). 192.168.1.173 is Node02 too (same ed25519 host key; just not in known_hosts under that address).
- No cartridge mounted during install. --check PASS; install rc 0, backup /usr/local/portabletak-backups/host-20261002_014838.tar.gz, gate CLOSED. Installed launcher/lib hashes match the build; API running; wan zone list intact (3 entries).
- Next: plug the SanDisk (6baed64f) into Node02 → expect adoption Mac→node, drive's own Tailscale 100.102.110.115 up on Node02, S22's two servers (set up on the Mac) green without new setup (local server will be red: it points at the Mac's 192.168.1.189; Tailscale server should be green).
- SanDisk moved Mac → Node02: adoption linux→openmanet at 02:00:08, addresses [10.41.254.1, 10.41.250.10, 100.102.110.115, portabletak-6baed64f.tailc13345.ts.net] (Mac's 100.83.127.3 gone, drive's own kept), AUTOLOAD=RUNNING, Tailscale RUNNING 100.102.110.115, 1 device connected right away (S22, set up on the Mac). Certs: Tailscale 8443/8446 CN=100.102.110.115; Wi-Fi CN=10.41.254.1. PASS (pending owner confirmation in ATAK).
- **Owner confirmed:** the S22's "(Tailscale)" server (set up by QR on the Mac) turned green on Node02 with no new setup. Drive-own Tailscale portability PASS.
- Guides: dashboard over Tailscale (https://<Tailscale address>:8444) added to Quick Start F.6, full guide §6 Step 3 + security note, Quick Reference address table. PDFs staged in start-here (not yet on the SanDisk, which is in Node02).

### Node01 (2026-10-02 ~02:25 UTC, read-only checks)
- Node01 on the mesh (10.41.0.222, default via Node02 10.41.0.89). **Autoload ENABLED on Node01** (S96portabletak-autoload) and **ASGARD attached** (sda 116 GiB "USB Flash Drive", superblock UUID 1f501ae2…, label ASGARD_TAK). Hotplug autoload ran at boot (21:22 local) and on 2026-09-30: only `e2fsck -fn` (read-only; journal not replayed → rc 4) → mount_failed; nothing mounted or written.
- Asked the owner to unplug ASGARD and run `/etc/init.d/portabletak-autoload disable` on Node01 (remote writes to Node01 are the owner's). Then install the 20261001 OpenMANET node package (commands given).
- Owner unplugged ASGARD (Node01: 0 USB disks, nothing mounted). Owner's disable command printed 0 but Node01 still has S96/K10 autoload links (dated Sep 26) and `enabled` → the command did not reach Node01; Node02 (checked over mesh 10.41.0.89 with Node02's saved key) unchanged: autoload on, SanDisk mounted. Asked the owner to re-run against 10.41.0.222 and paste output.
- Host key note: 10.41.254.1 is each node's own Wi-Fi address; the Mac roamed to Node01's Wi-Fi (10.41.254.215), so 10.41.254.1 presented Node01's key (U0td09…, same as 10.41.0.222). Node02's key is aPU9z7…? (scanned at 10.41.0.89) — use HostKeyAlias or the mesh address to reach a specific node.
- Remote changes to Node01 now go through (owner asked me to run them): autoload disabled on Node01 (links 0). Installed the 20261001 OpenMANET node package on Node01: --check PASS, rc 0, backup /usr/local/portabletak-backups/host-20261002_023206.tar.gz, gate CLOSED, autoload still off, API running, wan zone 'wan' 'eth_direct' 'phonewan' (Node01 already had eth_direct 10.41.253.1), launcher hash matches the build.
- Move test Node02 → Node01 started: Node02 portabletak-safe-remove → SAFE_TO_REMOVE=1, unmounted. Node01 autoload enabled for the test (no USB disks attached). Node01 dmesg since boot: 14 over-current changes on usb2-port1..4 (all USB3 bus ports) within ~140 s of boot while Node01 was low on charge with ASGARD attached → likely supply, not just port 2. Asked owner to keep Node01 on full power; watching dmesg during the test.
- Node01 with the SanDisk: Node01 uptime 6 min at 21:34 (rebooted around the plug-in — owner asked whether it was a power cycle); autoload at boot: CARTRIDGE_FOUND → READY → RUNNING, OTS/nginx RUNNING, mounted /dev/sda2 r/w (bus 1-1.4, high-speed), 0 over-current this boot. Drive's Tailscale state loaded (100.102.110.115) but Node01 has NO internet (network unreachable; earlier boot had default via Node02 10.41.0.89) → "logged out / fetch control key" → S22 Tailscale server cannot connect. Expected (remote access needs internet on the server side). Options: Node01 Ethernet to the home router, or stop the test (Safe Remove + autoload off).
- Node02 had been off (owner restarted it); Node01's internet comes via the mesh (default via Node02 10.41.0.89). With Node02 back: Node01 internet after ~30 s, drive's Tailscale online after ~1–2 min (no restart needed), S22 "active; direct". Node01 health: 1 TAK device connected; over Tailscale 8443/8446 present CN=100.102.110.115. **Move test Node02 → Node01 PASS** (S22 unchanged since the Mac QR setup).
- Cleanup: Node01 portabletak-safe-remove → SAFE_TO_REMOVE=1, unmounted. Node01 autoload left ON at the owner's request (ASGARD unplugged; owner does not plan to reattach it to Node01).
- Guides with remote-dashboard steps copied to the SanDisk (verified, ejected); setup bundles rebuilt with them.
- 2026-10-02 ~03:10 UTC: Node02 and Node01 shut down cleanly for the night (poweroff; no drives attached). Next: work site tomorrow — Windows laptop (Windows 11 required), Skydio X10D, Setup for Windows on a spare stick, iPhone. Licensing design doc: https://claude.ai/code/artifact/79a1d226-1025-4b3c-afab-738efe8d4f8b (awaiting owner decisions).

### Real Windows test (2026-10-02, owner's work laptop: Windows 11 25H2 x64, admin, VT on)
- Bug: Start window closed instantly. Windows PowerShell 5.1 turns native stderr (wsl --status: "not installed") into a terminating error under ErrorActionPreference=Stop (container tests used pwsh 7). Fix: native wsl calls run with Continue and stringify output; Setup's make-drive call too; global trap keeps any unexpected error on screen. Parse-checked. Copied to the SanDisk.
- Then: WSL installed, mirrored networking + firewall + distro import OK, address 192.168.1.47, drive found. **wsl --mount fails: ERROR_INVALID_DRIVE — Windows marks the SanDisk as removable media ("Removable media cannot be set to offline"); WSL cannot attach removable disks.** Design change needed: usbipd-win (owner installed via winget; SanDisk busid 2-14, 0781:5583 UAS). usbipd attach needs `modprobe` (kmod) in the distro — missing from our image → add kmod to the WSL build.
- usbipd path built into the launcher + Setup (Attach-Disk/Detach-Disk: install via bundled MSI or winget, PnP parent walk to the usbipd BusId with a single-mass-storage fallback, bind, hidden `wsl sleep infinity` keep-alive, modprobe vhci_hcd/usb-storage/uas, attach, wait for a usb disk). kmod added to the WSL image.
- Bug: wsl.exe runs `-- cmd` through a second Linux shell, which expanded $(...) early ("Syntax error: word unexpected"). Fix: Invoke-Distro sends the command base64-encoded (`echo B64 | base64 -d | /bin/sh`). Also fixes Setup's `sed "s/\r$//"` which could have stripped trailing 'r' characters.
- With usbipd: drive attached as /dev/sdX (sde, later sdf), mounted, adopted openmanet→linux (192.168.1.47 + drive Tailscale kept), postgres/rabbit/mediamtx up, then **opentakserver_not_ready**: x86 OTS first start over USB/IP is slow (runtime log stopped mid alembic; nothing in opentakserver.log). Fix in portabletak-start: OTS wait up to 300 s while the process is alive (fail after 15 s gone) + runtime-log tail on failure; CoT parser wait 15→120 s; EUD wait 20→180 s. Hot-patched on the laptop (sed) for today's test.
- Cleanup failure chain: rollback stopped RabbitMQ, then safe-remove's rabbitmqctl stop failed ("not running") → server_stop_failed → launcher detached the drive while mounted; next start: PORTABLETAK_ERROR=mountpoint_in_use (stale /mnt/asgard in the still-running distro). Fixes: portabletak-stop tolerates an already-stopped RabbitMQ; launcher runs `wsl --terminate PortableTAK` before attaching and after detaching, and on an unconfirmed stop runs emergency-stop + unmounts before detaching.
- Unclean detaches left the journal needing replay and stale "I/O error … sde" lines in the WSL VM kernel log (survive `wsl --terminate`); portabletak-mount's preen path refused (REFUSED_DEVICE_IO_ERRORS heuristic). Manual: mount rw + umount replayed the journal; e2fsck -fn clean. Fix: portabletak-mount only counts I/O errors logged since the disk's last "[sdX] Attached SCSI" line (tested both ways).
- **Windows PASS (first real run):** "PortableTAK is running" on the work laptop (Win 11 25H2 x64) via usbipd, 192.168.1.47. Drive's own Tailscale up on Windows: from the Mac over the tailnet 8089 open, 8446 CN=100.102.110.115. LAN from the Mac (192.168.1.88) to 192.168.1.47: unreachable (work Wi-Fi client isolation or IT-managed firewall) → use Node02's Wi-Fi / S22 hotspot for local clients.
- Windows on Node01 Wi-Fi: phones could not reach the laptop although Windows firewall + Hyper-V port rules existed; Hyper-V VM setting DefaultInboundAction=Block and the port rules were not honoured. `Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow` fixed it (Mac reached 8444/8089 at once). Launcher now sets it.
- Wrong address: on Node01 Wi-Fi (no internet → no gateway from the node) the launcher's Get-ServiceAddress (gateway required) picked the laptop's Windows Tailscale app adapter (wintak-301eod 100.74.238.23) → cert CN=100.74.238.23. Fix: exclude VPN/virtual/link-local/100.64/10 adapters, prefer Wi-Fi then Ethernet, gateway preferred not required. After the fix: 10.41.254.181, CN=10.41.254.181 on 8089/8443/8446, chain verifies (Mac, verify_ip OK).
- **Windows PASS on Node01:** S22 via ATAK Package → "TestaTAK (Wi-Fi)" and "(Tailscale)" both green (laptop also on home Ethernet for internet).
- Open: ATAK QR on the S22 → "The TAK Server's identity could not be verified" (QR content correct: 10.41.254.181:8089, Qrtest). No /Marti/api/tls requests logged. Server cert verifies; ATAK lost trust in the CA after the owner deleted servers, and re-importing the ATAK Trust Package did not restore it. Investigate trust_package vs ATAK 5.7 cert storage. ATAK Package works.
- Also to do: dashboard Stop/Start on Windows (Start cannot re-attach the USB drive → point to "Start PortableTAK (Windows)"); guides: "Changed Wi-Fi? Stop and start PortableTAK"; restrict epmd/rabbit dist (4369/25672) to localhost now that WSL inbound is open; rebuild WSL/VM/node package with today's fixes; bundle usbipd MSI on Start-here (needs download approval).

### Drone video feeds (Skydio X10D) — built 2026-10-02
- Skydio X10D GCS: Menu → Sharing → RTSP → External (stream name lowercase ending in a digit, VMS IP, port) → Start Stream: the controller PUSHES RTSP, with no user/password fields (owner's Sit(x) one-slide + Sit(x) "Video Alias" havocair01 @ 15.200.23.30:1935). OTS/MediaMTX authenticate every publish/read via /api/external_auth → an unauthenticated push gets 401.
- Owner chose (my recommendation): drone feeds need no sign-in to send or watch.
- Built: lib/video_feeds.py (config/video-feeds.json on the drive; MediaMTX PATCH /v3/config/global/patch authHTTPExclude = api/metrics/pprof + publish/read per feed; status from /v3/paths/list); users_worker video_feed_add/remove (VideoStream row so OTS lists it for TAK clients; Marti /api/video builds URLs from the request host → follows the drive); API GET/POST/DELETE /api/video-feeds; dashboard "Video Feeds" card (web/video-feeds.js: add/delete, Live dot, Skydio settings with the server address, port 8554); portabletak-start applies feeds after MediaMTX sync; nginx 8443 `location /hls/ → 127.0.0.1:8888` (OTS's feed URLs are https://host:8443/hls/<path>/index.m3u8).
- Verified with real MediaMTX 1.13 (arm64 container, auth server refusing everything): ffmpeg push to skydio1 accepted + ffprobe read without sign-in (h264); push to other1 → 401. Unit tests: test_video_feeds (5), HLS location test. Guides: full guide §7 Drone video, troubleshooting rows; Quick Reference table.
- Node package rebuilt: outputs/portabletak-host-installer-arm64-openwrt-20261002.tar.gz (SHA256SUMS now includes video_feeds.py, video-feeds.js). Full WSL/VM/bundle rebuild running.
- Pending owner test at work: X10D push → Live in the card → S22/WinTAK Video.
- Rebuild done: node package 62a8207a…; WSL amd64 f4990a83…, arm64 dbfe8661…; Mac VM; bundles Windows 234124a3…, Mac 99c413a2…. SanDisk not yet refreshed (drive in the Windows laptop).
- Skydio docs (support.skydio.com, read in the browser; WebFetch 403): Flight Deck TAK connection = Global Settings → Sharing → TAK/CoT → TAK Connections → Add (name one word, IP, port) → Advanced → SSL/TLS → Trust Store (+password). RTSP: internal server (controller hosts rtsp://<ctrl>:8554/<name>, name letters/digits/dash, case-sensitive) or External server push. Feed name rule loosened to [a-z0-9][a-z0-9-]{0,31}.
- Owner's Sit(x) TLS zip (structure only; passwords not printed): flat ATAK data package: truststore-<CA>.p12, <device>.p12, secure.pref (cot_streams host:8088:ssl + app prefs clientPassword/caPassword/caLocation cert/…/certificateLocation cert/…), MANIFEST/manifest.xml.
- Built "Device TLS Package" (dashboard per-user button; API /api/client/package/<user>/tls; client_packages.tls_package): same flat layout from the user's OTS ATAK package (truststore-root.p12, <user>.p12, secure.pref with every enabled connection incl. Tailscale, manifest, README.txt with Skydio values; trust-store and client passwords differ). Tests added.
- Owner's end goal: Skydio controller on the S22 hotspot → TAK (CoT) + RTSP push to the server over cellular. Android hotspot clients don't use the phone's VPN → the controller needs its own Tailscale app; then IP = drive Tailscale 100.102.110.115 for both. Guides updated (full §7 + Quick Reference).
- Risks to test: (1) RTSP publish over Tailscale userspace if the Skydio uses UDP transport (RTP 8000/8001) — TCP-interleaved works; (2) userspace Tailscale delivers from 127.0.0.1, which OTS whitelists for MediaMTX auth (OTS_IP_WHITELIST) — any tailnet member can publish/read any stream (tailnet = owner's devices only; note for the security section).
- New standalone guide: guides/skydio-x10d.html → start-here/'PortableTAK Skydio X10D Guide.pdf' (setups A same network / B phone hotspot + Tailscale on controller; Device TLS Package; TAK connection; Video Feeds; RTSP External; watching; troubleshooting). Every PDF in start-here goes onto drives (Windows launcher also copies *.pdf locally).
- Rebuild (Device TLS Package + feed-name rule + Skydio guide): node package 0f2e543f…; WSL amd64 da133b0f…, arm64 779f7924…; Mac VM; bundles Windows f30f9514…, Mac 2cbf5e4b… (bundles include the Skydio guide). Owner: Skydio controller doesn't allow installing apps → proposed public cloud relay (VPS, like Sit(x)) vs S22 Termux bridge; awaiting decision.
- Owner: users run PortableTAK on a Windows laptop; EUDs on cellular; Skydio controller is locked (no app installs) and everything must be free (no subscription). Case 2: laptop far from the drone team.
- Built the **phone hotspot bridge**: Video Feeds card → "Drone far from this server" gives a one-line Termux command (termux-wake-lock; pkg install socat; socat TCP-LISTEN 8089/8554 fork → drive Tailscale address; pid file so re-running replaces it; prints the hotspot interface address). Controller joins the ATAK phone's hotspot and uses that address for TAK (8089) and RTSP External (8554); the phone's own Tailscale carries it. Tested in two containers (both ports forwarded, rerun replaces). Not tested on Android: Termux bind reachable from hotspot clients, RTSP over TCP from the Skydio (UDP RTP not forwarded), Skydio TAK hostname check vs cert (hotspot IP not in SAN).
- Skydio guide Setup B rewritten around the bridge; full guide/Quick Reference updated; TLS README notes the bridge address. Rebuild running.
- Rebuild with phone bridge done (see task output): f6d546569a7b649da7763d795d9ad2b4d65a5d82767c6ac6aa4fee59c3578044  ../outputs/portabletak-host-installer-arm64-openwrt-20261002.tar.gz PT_WSL_DISTRO=/out/portabletak-wsl-amd64.tar.gz 129M 90c4fa10f5a34fef7957940179adc4c3dd851b4d86285c6d35f684a02176e648 PT_WSL_DISTRO=/out/portabletak-wsl-arm64.tar.gz 

### 2026-10-02 evening: items 7–11, GitHub
- 7 ATAK QR trust: trust_package was nested (outer manifest → inner zip WITHOUT its own manifest), so ATAK may file it without applying caLocation. Rebuilt as a flat package (MANIFEST + truststore-root.p12 + trust.pref with caLocation cert/truststore-root.p12, caPassword) like Sit(x)/TAK Server cert packages. Needs a phone test (delete servers → import Trust Package → QR).
- 8 Dashboard on Windows/Mac: /api/status returns host_kind (WSL osrelease "microsoft" → windows; VM cmdline pt.advertise → mac); dashboard hides Mount/Start/Stop/Safe Remove there and explains Stop/Start PortableTAK.
- 9 RabbitMQ: epmd started first with -address 127.0.0.1; rabbitmq-server with ERL_EPMD_ADDRESS=127.0.0.1 and -kernel inet_dist_use_interface {127,0,0,1}. Container test (Ubuntu rabbitmq): 4369 and 25672 on 127.0.0.1/::1 only, status/stop OK.
- 10 OTS_IP_WHITELIST emptied in opentakserver_launch (only used for YouTube re-streaming; Tailscale userspace makes every remote client 127.0.0.1).
- 11 usbipd-win 5.3.0 MSIs (x64 1c984914…, arm64 efd7c4eb…, = GitHub release digests) on Start-here (.portabletak/windows); launcher installs the matching one with msiexec /qn, winget only as fallback.
- GitHub: private repo https://github.com/stpleb/portabletak (gh 2.102 via Homebrew; owner signed in). Source only (no test credentials, keys, Sit(x) TLS zip); secret scan clean (only a fake key in a test). work/sync-repo.sh copies sources and pushes. Release assets (setup bundles, node package, build inputs) after the current rebuild.
- Owner: Node01 power likely fixed (PiSugar replaced). Two Lexars: ASGARD (protected) + a spare usable for tests.
- Final rebuild of 2026-10-02 (trust package flat, items 8–11): bundles Windows dbff0e47…, Mac b5cc2a84…; node package 8e395128…; build inputs d196a485… (172 MB). SanDisk Start-here refreshed (incl. usbipd MSIs; 47% used) and ejected.
- GitHub Release v2026.10.02 (private repo): https://github.com/stpleb/portabletak/releases/tag/v2026.10.02 — Windows/Mac setup zips, node package, build inputs, SHA256SUMS.txt (all uploaded).
- Node01 updated to node package 8e395128… (check PASS, rc 0, backup host-20261002_224659, autoload still on, API running). Node02 was off (not updated).
- Owner tests 2026-10-02 night (laptop on Node01 Wi-Fi + home Ethernet): Test 1 PASS (Video Feeds card, computer stop/start note). Test 2 PASS on the node network (Trust Package fix: one QR → both servers green; Tailscale green with Wi-Fi off). Fails: enrolling over cellular only (QR carried the local address) and phone on home Wi-Fi with laptop on Ethernet only (server still bound to the Node01 Wi-Fi address after the network change). "Update server certificate" → "not logged in" (drive Tailscale reconnecting after the network change).
- Fixes (rebuild running): QR host = Tailscale address when the dashboard was opened through it (qr_host uses the Host header); enrollment profile then adds the local (Wi-Fi) server instead of a second Tailscale one; Windows dashboard warns "This computer changed networks… stop and start" (/api/status address_gone: advertised IP not on any adapter, WSL only); apply-address waits up to 30 s for the drive's Tailscale; Video Feeds card shortened (no Sit(x)), drone settings/bridge in expanders, placed after Users; Sign out moved to the bottom (after Erase Drive).
- iTAK QR from home Wi-Fi failed ("error connecting to the server" after the password): the iPhone opened the dashboard at the laptop's Ethernet address 192.168.1.194, but the QR carried the advertised Node01 Wi-Fi address; no /Marti/api/tls requests reached nginx. (iTAK QR had never been tested before.)
- Fix (rebuild running): Windows launcher passes every local address (Wi-Fi first, then Ethernet; up to 3 extra) → PT_EXTRA_IPS → certificate SANs; server_addresses.ensure_named issues a certificate named for each non-advertised address (CN = address; Tailscale keeps opentakserver-remote.pem; stale ones removed; 10.0.2.x skipped); dashboard_site.named_blocks gives each address its own 8443/8446 block (+127.0.0.1 → Tailscale cert); qr_host uses the address the dashboard was opened with whenever it is one of the server's addresses. Mac VM not covered (slirp hides the destination address). Tests added (test_named_certificates_for_every_other_address).
- Setup for Windows with the spare Lexar: usbipd attach → "Device busy (exported) … used by Windows". Fix: Attach-Disk dismounts the disk's Windows volumes (Win32_Volume Dismount Force), retries with `usbipd bind --force` on busy, records the forced flag and unbinds on detach/failure so Windows sees the drive again. Workaround given: `usbipd bind --force --busid 1-2`, later `usbipd unbind --busid 1-2`.
- QR from the laptop's own dashboard (localhost) cannot know the phone's network → "Phone is on:" picker in both QR windows (/api/client/qr-addresses; qr endpoints accept ?address=<any server address>).
- iTAK QR failed on every network (home Wi-Fi, Tailscale, Node02); nginx shows NO iTAK /Marti requests (only ANDROID) → TLS refused by iOS. Cause (most likely): Apple requires TLS server certificates ≤ 825 days validity even from private CAs; ours were 3650 days. Fix: server and address-named certificates 820 days; server_addresses.needs_renewal/renew_if_needed (validity > 825 d or < 120 d left) runs in portabletak-start right after adoption (before services) and in dashboard_site. Test added. Rebuild running.
- iTAK Remote QR button removed (the QR window's network picker covers Tailscale); guides updated. Full rebuild (825-day certs, Lexar fix, picker) done; button removal + guides go into the next build.
- iPhone trust profile (unsigned .mobileconfig with the drive CA, AirDropped from the Mac + full trust in Certificate Trust Settings) → iTAK QR enrollment now succeeds; both servers created. 8089 then fails: eud-ssl-runtime.log shows repeated pika ChannelWrongStateError in close_connection. Cause: the EUD handler process lacks the case-insensitive find_user patch (only the OTS web process had it) — iOS capitalizes the typed username ("Qrtest"), cert CN "Qrtest" → "User does not exist" → close_connection runs twice (handle_auth + end of handle) → second publish on a closed channel. Fix: eud_handler_launch installs case-insensitive find_user and makes close_connection idempotent/safe. Tests added. Windows image rebuilt (amd64).
- To build next: dashboard "iPhone Trust Profile (for QR)" download (.mobileconfig) + guide steps.
- **iTAK QR PASS** (Windows laptop server): Node01 Wi-Fi and Tailscale both green after the trust profile + case-insensitive EUD lookup.
- Owner: iPhone saw the S22's chat but not its marker (grey dot) until a little later. Cause: OTS sends no cached positions on connect; a stationary ATAK reports rarely. Fix: eud_handler_launch.install_latest_positions sends each other device's newest unexpired a-* CoT (from the cot table, stale > now) once, right after the new device registers. Test added (DB query untested on real data; errors only logged).
- Also added: dashboard "iPhone Trust Profile (for QR)" link (/api/client/ios-trust-profile → .mobileconfig, deterministic UUIDs), guides (iPhone once-per-phone step). Full rebuild running.
- Release v2026.10.03 published (Windows/Mac setup, node package 20261003, SHA256SUMS). Owner to rerun Setup for Windows with it (Lexar busy fix).
- 2026-10-03: owner quick-formatted a Lexar in Windows before Setup; the other Lexar (read-only check on the Mac, superblock label) is PORTABLETAK → the formatted one was ASGARD. Owner: nothing needed on it. Both Lexars are now spares.
- 2026-10-03 Windows Setup + Start on Lexar (work laptop) PASSED after fixes (all in release v2026.10.03 Windows zip, repo up to 9b27d3a):
  - Setup: Clear-Disk before handover; Zone.Identifier files removed; mcopy non-interactive (hung on 'name:Zone.Identifier').
  - Start: "Device busy" root cause = WSL Plan9FileSystem dllhost.exe {DFB65C4C-...} holding the FAT volume (WSL#7972; System event 225). Start closes that helper, dismounts all volumes, removes letter (mountvol /p), forced bind + pnputil restart; letter restored at start/stop/failure.
  - Single-address bug (@() around Get-ServiceAddress); first-setup answers via private file (pipe into wsl.exe delivered nothing → invalid_input).
  - Live progress bar (Write-Progress) for Setup build and first setup. PS syntax not machine-checked on Mac (no pwsh); user runs Parser check on Windows.
