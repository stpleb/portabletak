PortableTAK 1.0: a TAK server on a USB drive. Plug it into an OpenMANET node, a Windows computer, a Mac (Apple Silicon), a Linux computer or a Raspberry Pi.

**What it does**
- Runs a TAK server (OpenTAKServer) from the drive, with a web dashboard for users, phone setup packages and QR codes for ATAK, iTAK and WinTAK.
- On an OpenMANET node it runs headless; on Windows, Mac and Linux it starts from the drive with one double-click (or one command on Linux).
- Remote access over Tailscale, so phones can reach the server over cellular.
- Voice: a Mumble server for the TAK PTT plugin, bridged to OpenMANET talk groups, with recordings.
- Federation with other PortableTAK servers and with an official TAK Server: positions, chat, data packages and Data Sync missions.
- Backups (manual, uploaded and automatic daily), drive checks and repair after a power loss, node heat warnings, and the node clock set from phones when it has no internet.

**Getting started**
- Download the setup for your computer (`PortableTAK-Mac-Setup.zip` or `PortableTAK-Windows-Setup.zip`) to prepare a drive, then follow the Quick Start and the Setup and Troubleshooting Guide on the drive.
- OpenMANET nodes: `portabletak-loader-install.sh` installs the loader; `portabletak-host-installer-arm64-openwrt-*.tar.gz` is the signed node package (checksums in `SHA256SUMS.txt`).

**Build:** 2026.10.28.7. Fresh-drive build test: 7 of 7 services.

**Not yet tested:** real phones on both sides of a TAK Server federation, an official TAK Server connecting in to PortableTAK, Raspberry Pi and Linux PC hosts on real hardware, and field range tests.
