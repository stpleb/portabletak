# PortableTAK handoff (2026-10-23)

Read this first in a new session, then `outputs/portabletak-node02-runbook-20260929.md` (full log, newest at the end).

## 1. What PortableTAK is
A portable OpenTAKServer on a USB drive. The same drive runs on OpenMANET nodes (Node02, Node01; Raspberry Pi 4),
Windows 11 (WSL2 host + usbipd) and Apple Silicon Macs (QEMU VM). It must stay dummy proof, offline safe, free,
and self-contained on the drive. Main users: Windows laptop with Wi-Fi, ATAK phones on cellular via Tailscale.

## 2. Where everything is
| What | Path |
|---|---|
| Project root | `/Users/STPLEB/Documents/Codex/2026-09-27/files-pasted-by-the-user-paste` |
| Source: provisioning libs + tests | `work/portabletak-provision` (`lib/`, `tests/`, `vendor/mediamtx/`) |
| Node package (payload is the master copy of everything that runs) | `work/host-installer-20260929` (`payload/usr/local/portabletak/...`, `SHA256SUMS`, `install.sh`) |
| Windows host image build | `work/wsl-host` (`build-wsl-distro.sh`, `files/`, `requirements.txt`) |
| Mac VM build | `work/mac-vm` (`build-vm.sh`, `files/`) |
| Setup bundles | `work/win-setup`, `work/mac-setup` (`build-bundle.sh`, `container-setup.sh`) |
| Start Here files on every drive | `work/start-here` (launchers, guides PDFs, `.portabletak/{windows,mac,wsl,node}`) |
| Guides (HTML -> PDF via headless Chrome) | `work/guides` |
| Node loader + signing | `work/node-loader` (`sign-node-package.sh`, `build-loader-installer.sh`, `README.md`) |
| Federation work | `work/federation` (`ots/` = OpenTAKServer clone, branch `pt-fed`; `patches/`; `build-overlay.sh`; `wheels/`; `e2e/` test harness; `takserver/` TAK Server 5.8 test image) |
| Build outputs / releases staging | `outputs/` (Setup zips, node package tarballs, runbook, this handoff) |
| GitHub repo (private) | `stpleb/portabletak`, local clone `/Users/STPLEB/Documents/portabletak`; push with `work/sync-repo.sh "message"` |
| Releases | github.com/stpleb/portabletak/releases (latest **v2026.10.23.3**) |
| Release signing key (PRIVATE, never commit/print) | `~/.portabletak-signing/` (`release.key.pem`, key id `c24087f4f016955e`). Owner should keep a backup. |
| Field extension project (owner's) | `~/Desktop/Field Ext` (GitHub `stpleb/openmanet-field-extension`) |
| Memory notes | `~/.claude/projects/-Users-STPLEB/memory/` (portabletak-project.md etc.) |

## 3. Hardware and hosts right now
| Device | State |
|---|---|
| Node01 (Pi 4, OpenMANET 24.10 / 1.8.0, field-extension SD card, hostname TyrNode01) | SSH `root@10.41.0.1` (Mac on Node01 Wi-Fi gets 10.41.0.x). Has the PortableTAK loader + package 2026.10.23.2 **plus hot-patches** (port 18081, nginx default-site skip, OpenMANET 1.8 address detection, adoption rule, channels card). Running drive: **SanDisk = "Alpha TAK"**, advertises **10.41.250.10**, Tailscale 100.121.108.4. |
| Mac | **Lexar = "Bravo TAK"** running in the Mac VM (dashboard `https://localhost:8444`), built with 2026.10.23.2 (no channels card yet). iPhone (iTAK) connected to Bravo via Tailscale. |
| Federation | Alpha <-> Bravo joined by invite/join, both arrows green. |
| Node02 | Charge port needs repair; last known: OpenWrt/CicadaTAK 0.12, package 2026.10.04 + loader. |
| Docker on Mac | Colima, 4 GB. Test containers may still exist: `fedA`, `fedB` (e2e drives), `taksrv` (TAK Server 5.8). Test drive images (~19 GB) in `work/federation/e2e/A,B/out`. |

## 4. Verified (with what)
- **Windows** (work laptop, x64): Setup blank + ready-to-use, Start/Stop, drive letter restore, network change, Mac-made drive on Windows, ATAK/iTAK QR + package over Wi-Fi and Tailscale, positions on reconnect, Tailscale sign-out/in (2026-10-03).
- **Node loader** (signed with usign): tampered package refused, install from nothing, update, up-to-date no-op (Node02, containers); **real hardware: Node01 updated itself 2026.10.04 -> 2026.10.23.2 from the drive** (2026-10-23).
- **Federation** (OpenTAKServer 1.7.13 + PR #343 + PortableTAK patches, add-on on PYTHONPATH):
  - Containers: PortableTAK <-> PortableTAK and PortableTAK <-> official TAK Server 5.8, CoT both ways; invite/join one paste; invite reuse refused; pinning refuses impostors; data packages both ways (receiver pull and sender push); one-way links; per-channel federation.
  - **Real hardware (Alpha on Node01 <-> Bravo on Mac): positions, chat, points, data packages (S22 -> iPhone) all work.**
- **Channels**: create/rename/delete, per-user send/receive, built-ins protected (__ANON__, ADS-B, AIS, Meshtastic) - in containers via the real API.
- **Release 2026.10.23.3**: fresh drive built from the new Mac Setup in a container -> 7/7 services, all OTS references on port 18081.
- Tests: `cd work/portabletak-provision && python3 -m unittest discover -s tests -p 'test_*.py'` -> all pass except the usual **6 container-only errors** (test_*_native, test_api). OTS add-on tests: run in image `pt-ots-test` (see 6).

## 5. Open items (in priority order)
1. ~~Confirm v2026.10.23.3 finished uploading~~ - done, all 7 assets uploaded. (`gh release view v2026.10.23.3 --repo stpleb/portabletak`). If any asset is missing, re-upload from `outputs/` (zips, `portabletak-host-installer-arm64-openwrt-20261023-3.tar.gz`, manifest+sig from `work/start-here/.portabletak/node/`, `portabletak-loader-install.sh`, SHA256SUMS).
2. **Update Bravo (Lexar) to 2026.10.23.3**: stop Bravo (Return in its window / Stop PortableTAK), then refresh the drive's Start Here files in place (copy `work/start-here/` contents incl. hidden `.portabletak/` onto the Lexar's `PORTABLETAK` FAT volume; keeps users/data on the Linux partition) or remake it with Mac Setup (wipes it). Then start Bravo; it converts itself (port 18081 etc.).
3. **Update Node01 cleanly**: unplug/replug Alpha after a drive carries 2026.10.23.3 (loader updates Node01 and replaces the hot-patches). The SanDisk gets 2026.10.23.3 node package only when its Start Here/node folder is refreshed or the drive is remade.
4. **Channels phone test** (iPhone): Bravo after its update, or add the iPhone to Alpha over Tailscale (`https://100.121.108.4:8444`, Alpha's iPhone Trust Profile + full trust, iTAK QR "Tailscale"); create a channel, assign the user, reconnect, check iTAK "TAK Channels".
5. ~~Field extension repo~~ - committed and pushed (stpleb/openmanet-field-extension main, 7462f4b "SD image: optional PortableTAK loader").
6. ~~Contribute to OpenTAKServer~~ - owner decided not to (2026-10-23). Do not raise it again.
7. **Skydio X10D field test** (Device TLS Package, Video Feeds, phone hotspot bridge) - waiting on the drone.
8. Later / on hold: licensing (on hold), Node02 charge-port repair then update, Windows on ARM untested, phone USB-tether uplink untested, Mac QR one-network limit, OpenTAKServer weekly watch (scheduled task "opentakserver-upstream-watch", Mondays 9 AM; press Run now once).

## 6. How to do things
**Full release build** (after any payload change):
1. Bump `payload/usr/local/portabletak/VERSION` (YYYY.MM.DD or YYYY.MM.DD.N; nodes only update to a higher version).
2. Rehash the node package (names with spaces exist - never use awk '{print $2}'):
   `cd work/host-installer-20260929 && find payload -name __pycache__ -type d -prune -exec rm -rf {} + ; { cut -c67- SHA256SUMS | grep -v '^payload/'; find payload -type f; } | sort -u | while IFS= read -r f; do [ -f "$f" ] && shasum -a 256 "$f"; done > SHA256SUMS.new && mv SHA256SUMS.new SHA256SUMS && shasum -a 256 -c SHA256SUMS | grep -vc ': OK$'` (must print 0).
3. If OpenTAKServer add-on changed: commit in `work/federation/ots` (branch pt-fed), run `work/federation/build-overlay.sh`, then step 2.
4. Run the build chain (exact command is in the runbook, "Full rebuild of release"): tar node package -> `node-loader/sign-node-package.sh <tgz> <version> start-here/.portabletak/node` -> WSL distros (amd64+arm64 via docker) -> Mac VM (`portabletak-setup:2` image + qemu-img) -> `win-setup/build-bundle.sh` -> `mac-setup/build-bundle.sh`.
5. **Regression before publishing**: build a fresh drive with the Mac Setup container into `work/federation/e2e/C/out` (needs `secrets/request.json`) and require `PORTABLETAK_SETUP_SERVICES=7/7`.
6. Publish with `gh release create ...` (see runbook) and `bash work/sync-repo.sh "message"`.
- **MediaMTX source pins**: `lib/linux_bootstrap_backend.py` pins sha256 of `share/mediamtx/mediamtx.yml` and `ots-webhook.py`; changing those files needs new pins (both lib copies) and the copy in `portabletak-provision/vendor/mediamtx/`.
- **PowerShell syntax check** (Windows scripts): `docker run --rm --platform linux/amd64 -v "$PWD":/w:ro mcr.microsoft.com/powershell:latest pwsh -c '<Parser::ParseFile loop>'` (see runbook).
- **OTS add-on tests**: `cd work/federation/ots && docker run --rm -v "$PWD":/src:ro pt-ots-test sh -c 'cp -r /src /w && cd /w && PYTHONPATH=/w python -m pytest -q -p no:cacheprovider -o addopts="" tests/test_federation.py tests/test_portabletak_files.py'` (24 passed, 2 skipped).
- **E2E federation harness**: `work/federation/e2e` (run-drive.sh, start-api.sh, api.py browser-like client, users.sh, publish.py, send-package.sh, find-cot.sh, find-package.sh; TAK Server: `work/federation/takserver/tak-entry.sh`, image `pt-takserver:5.8`).
- On nodes: run long jobs detached: `setsid sh -c "... > /tmp/x.log 2>&1" </dev/null >/dev/null 2>&1 &` (plain `&` dies with the SSH session). BusyBox lacks `base64`, `grep --line-buffered`, `dmesg -w`.

## 7. Key design facts (don't re-derive)
- OpenTAKServer internal port **18081** (127.0.0.1); `lib/ots_port.py` migrates old drives at start (YAML and JSON-style config).
- nginx `default` site (OTS web UI, ports 80/443) dropped at start when those ports are in use (OpenMANET 1.8 uhttpd).
- OpenMANET adapter: `10.41.254.x` present -> CicadaTAK style, advertise 10.41.254.1; else advertise service IP **10.41.250.10**; `host_adoption.needs_adoption` re-signs when advertise differs.
- Federation: add-on `share/ots-federation/<arch>` copied to `/mnt/asgard/opentak/federation-overlay` and first on PYTHONPATH for all OTS processes; `bin/portabletak-federation` runs `federation_server` when `portabletak/federation.conf` says enabled; ports 9000 (v1) / 9001 (v2); stable federation cert `opentak/data/ca/certs/federation/` (10 years); invites `portabletak/federation-invites.json` (hashes, single use, 24 h); public endpoints `/api/federation/public-ca` and `/api/federation/accept`.
- Data packages over federation: `opentakserver/federation/files.py` (gRPC `portabletak.FederationFiles` Get/Put on 9001), receiving side holds the file-share CoT until the file is stored, rewrites senderUrl to placeholder `PORTABLETAK-FEDERATION-HOST`, filled per phone by `chroot/eud_handler_launch.py` (local socket address; 127.0.0.1 -> drive Tailscale address).
- Channels: `chroot/channels_actions.py`, API `/api/channels*`, `/api/users/{u}/channels`, card `web/channels.js`; federation per-link channels = inbound/outbound groups.
- Rules: never print passwords/secrets; downloads and outward-facing actions need owner approval; remote changes to Node01/Node02 are OK when the owner asks.
