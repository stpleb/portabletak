---
title: PortableTAK on Windows (WSL2) — plan after the generic-Linux proof
date: 2026-09-30
status: plan; nothing Windows-specific built yet
---

# PortableTAK on Windows (WSL2): plan

## What is already proven (2026-09-30, Ubuntu 24.04 arm64 container on the Mac)

The new **`linux` host profile** runs the unmodified provisioning core and host
scripts on ordinary Linux (not OpenWrt), which is what WSL2 is:

- v3 cartridge laid out on a loop disk, identity published, provisioned in one
  run through every phase to `provisioned`; all 7 services RUNNING.
- Server bound to the host's own address (no mesh claim); HTTPS 8443 on that
  address, CoT 8089 on all addresses.
- Two TLS clients with real certificates: one's position reached the other.
- Certificate enrollment on 8446 (the QR onboarding path) signed a CSR.
- Full stop → boot sequence → running again; backup + verify.

Host-side pieces that made this possible: `/etc/portabletak/host-profile`
(`PT_PROFILE=linux`, `PT_SERVICE_IP`, `PT_SERVICE_DEV`), `adapters/linux/`
(address is host-owned; claim = confirm present, release = no-op), a `/proc`
process listing instead of BusyBox `ps`, and scripts that read the cartridge's
recorded service address instead of `10.41.250.10`.

## What Windows adds

| Area | Decision needed | Recommendation |
|---|---|---|
| **CPU architecture** | Most Windows laptops are x86_64; the clean base is arm64 | Build an **x86_64 clean base** with the same builder (Ubuntu Base 24.04 amd64 + same pinned packages); seal and pin separately (`SUPPORTED_BASE_SEALS['x86_64']`). The cartridge format already records `PORTABLETAK_ARCH`. |
| **One cartridge, two architectures?** | A cartridge carries one rootfs | Near term: **arch-specific cartridges**. A cartridge moving ARM ↔ x86 uses a **logical backup/restore** (pg_dump already used) into a cartridge of the other arch. Dual-rootfs cartridges later if worth it. |
| **Getting the USB drive into WSL2** | WSL2 cannot mount ext4 USB by default | Use **`wsl --mount \\.\PHYSICALDRIVEn --partition 1`** (built in, Windows 11) — mounts the ext4 partition inside WSL2. Launcher identifies the disk by model/serial/size (same rules as the layout tool) before mounting. Fallback: `usbipd-win` passthrough. |
| **Networking** | WSL2 is NATed by default | Use **WSL mirrored networking** (`networkingMode=mirrored` in `.wslconfig`, Windows 11 22H2+): WSL2 shares the laptop's addresses, so phones on Wi-Fi/Ethernet reach `laptop-IP:8089/8443/8446` directly. Fallback: `netsh interface portproxy` rules per port. Windows Firewall inbound rules for 8089/8443/8446 (+ video ports if used). |
| **Service address** | Laptop IP changes between networks | `PT_SERVICE_IP` = the interface the operator picks at launch (Wi-Fi, Ethernet, hotspot, Tailscale). Server certificate must cover it: issue a **server cert with multiple IP SANs** or reissue the server cert on address change (client trust is to the CA, so reissuing the server cert does not break clients). |
| **WinTAK on the same laptop** | WinTAK connects locally | WinTAK uses the laptop's own address (mirrored mode) or `127.0.0.1` via a hosts entry; test both. |
| **Launcher** | Operators should not type WSL commands | PowerShell launcher: detect PortableTAK drive (label `PORTABLETAK`), `wsl --mount`, write `/etc/portabletak/host-profile` with the chosen address, run `portabletak-boot`, open the dashboard via `localhost:8090`; Stop/Safe-remove runs `portabletak-safe-remove` then `wsl --unmount`. |
| **Admin rights** | `wsl --mount` needs elevation | Launcher self-elevates once per session; document it. |

## Work plan (in order)

1. **x86_64 clean base** — run the existing builder for amd64 in an x86_64 Linux
   environment; audit, seal, pin; loop-device layout test (can run in a Linux
   VM or GitHub Actions — no Windows needed).
2. **Multi-address server certificate** — issue the server cert with the
   adapter's address plus optional extra SANs; add "reissue server cert for new
   address" as a host action. Needed for laptops and for the Android per-node
   address option (field-extension spec §5 C).
3. **WSL2 bring-up script** (inside WSL): install host package deps, write the
   host profile, mount check, boot. Mostly the generic-Linux path already
   proven.
4. **PowerShell launcher** — drive detection by hardware identity,
   `wsl --mount`, firewall rules, mirrored-networking check, start/stop,
   safe removal.
5. **Acceptance on a real Windows 11 x86 laptop** — provision, WinTAK local,
   ATAK/iTAK over Wi-Fi, stop/relaunch, backup/restore, unplug handling.

## Needs from the owner

- A Windows 11 x86_64 laptop (22H2+ for mirrored networking), admin rights.
- Decision: arch-specific cartridges now (recommended) vs. dual-arch later.
- The Parallels VM on Apple Silicon is Windows-on-ARM; WSL2 inside it needs an
  M3 or newer Mac. It can exercise the launcher logic with the arm64 base, but
  the x86 laptop is the real target.
