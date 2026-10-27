# PortableTAK

A portable OpenTAKServer on a USB drive. The same drive runs on OpenMANET nodes, Windows 11 computers
(WSL2), Apple Silicon Macs (QEMU) and 64-bit Linux computers including Raspberry Pi OS (a contained host system,
`start-here/.portabletak/linux`), adapts to whichever host it is plugged into, and works offline.

## What it does

- **TAK server** (OpenTAKServer) for ATAK, iTAK and WinTAK, with its own certificate authority; phones set up by
  package or QR code; channels (TAK groups).
- **Dashboard** (sign-in, HTTPS 8444): users and phones, server status, backups, remote access over Tailscale.
- **Live Map**: phones, linked servers, aircraft, ships, Meshtastic radios and markers; online maps, or offline
  maps saved by area on the drive.
- **Live Feeds**: ADS-B aircraft from the internet or a USB RTL-SDR radio (no internet), AIS ships from aisstream.io.
- **Video feeds**: drones (RTSP push), the TAK ICU app and RTSP/RTMP/SRT/HLS cameras; Watch in the browser
  (WebRTC, under a second behind) and in ATAK.
- **ATAK plugin update server**: phones install uploaded plugins from ATAK → Plugins → Sync, offline.
- **Data packages**: upload any file or ATAK data package and send it to phones; packages phones share are listed.
- **Desktop layout**: on a computer screen the dashboard is a sidebar of pages; on phones it is one scrolling page.
- **Meshtastic**: radios over MQTT on the map and in ATAK.
- **Federation** with other PortableTAK servers, OpenTAKServer and the official TAK Server.

## Download (Releases)

Ready-to-use files are attached to the newest release (Releases, on the right):

| File | Use |
|---|---|
| `PortableTAK-Windows-Setup.zip` | Make PortableTAK drives on a Windows 11 computer |
| `PortableTAK-Mac-Setup.zip` | Make PortableTAK drives on an Apple Silicon Mac |
| `portabletak-host-installer-arm64-openwrt-*.tar.gz` | The OpenMANET node package (nodes normally install it from the drive by themselves) |
| `portabletak-loader-install.sh` | Installs the node loader once, so nodes install and update PortableTAK from a drive |
| `portabletak-node-package-*.manifest` (+ `.sig`) | Signed version and checksum of the node package |
| `SHA256SUMS.txt` | Checksums of all release files |

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
Tailscale (BSD-3-Clause), usbipd-win (MIT), MapLibre GL JS (BSD-3-Clause), PMTiles and Protomaps basemaps
(BSD-3-Clause; map data © OpenStreetMap contributors, ODbL), hls.js (Apache-2.0), readsb (GPL-3.0),
rtl-sdr (GPL-2.0), libusb (LGPL-2.1).
