# PortableTAK

A portable OpenTAKServer on a USB drive. The same drive runs on OpenMANET nodes, Windows 11 computers
(WSL2) and Apple Silicon Macs (QEMU), adapts to whichever host it is plugged into, and works offline.

## Download (Releases)

Ready-to-use files are attached to the newest release (Releases, on the right):

| File | Use |
|---|---|
| `PortableTAK-Windows-Setup.zip` | Make PortableTAK drives on a Windows 11 computer |
| `PortableTAK-Mac-Setup.zip` | Make PortableTAK drives on an Apple Silicon Mac |
| `portabletak-host-installer-arm64-openwrt-*.tar.gz` | Install PortableTAK support on an OpenMANET node |
| `build-inputs-*.tar` | Pinned third-party inputs needed to rebuild everything from source |

Guides (also on every drive's Start-here partition): `guides/` (Quick Start, Quick Reference,
Setup and Troubleshooting Guide, Skydio X10D Guide).

## Layout

| Folder | Contents |
|---|---|
| `provision/` | Provisioning library, API, tests (`python3 -m unittest discover -s tests`) |
| `node-package/` | OpenMANET node package source (payload, install.sh, SHA256SUMS) |
| `windows-host/` | Builds the WSL2 host image the Windows launcher imports |
| `mac-vm/` | Builds the Mac VM (kernel, initrd, qcow2) |
| `windows-setup/`, `mac-setup/` | Drive-making tools |
| `start-here/` | Files on the drive's visible partition (launchers; large images come from the builds) |
| `guides/` | Guide sources (HTML) and PDFs |
| `docs/` | Runbook, plans and design notes |
| `third-party/` | Provenance of bundled third-party binaries |

Third-party software keeps its own license: OpenTAKServer (GPL-3.0), Ubuntu base, MediaMTX (MIT),
Tailscale (BSD-3-Clause), usbipd-win (MIT).
