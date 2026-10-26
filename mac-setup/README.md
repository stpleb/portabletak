# PortableTAK Setup for Mac (Apple Silicon)

Sets up a PortableTAK server on a USB drive from a Mac. The node you plug the
drive into later finishes the node-specific part by itself on first start
(addresses, server certificate), with or without internet.

## One-time preparation
1. Install Homebrew (https://brew.sh) if you don't have it.
2. In Terminal: `brew install colima docker`

## Set up a drive
1. Plug in the USB drive (16 GB or larger). **It will be erased.**
2. Double-click **PortableTAK Setup.command** (first time: right-click → Open).
3. Answer the questions: server name, administrator password (dashboard
   login), client password (used by phones when they enroll). Typos can be
   retried.
4. Pick the drive from the list and type `ERASE diskN` to confirm. macOS asks
   for your Mac password to write the drive.
5. Wait about 10–15 minutes. The drive is verified and ejected at the end.
   If macOS says the disk is not readable, click **Ignore**.

## Use it
- **OpenMANET node:** plug the drive into a blue USB 3 port. The node installs or
  updates PortableTAK from the drive by itself (with the PortableTAK loader),
  starts it, and the dashboard is at `https://10.41.250.10:8444`.
- **This Mac or a Windows 11 computer:** open the PORTABLETAK volume and
  double-click **Start PortableTAK (Mac)** or **(Windows)**.

Sign in as `administrator`. The guides on the drive (Quick Start, Quick
Reference, Setup and Troubleshooting Guide) cover the dashboard: phones,
remote access, Live Map, live aircraft and ships, video feeds, ATAK plugins,
Meshtastic and federation.

## Safety
- Only external drives are listed; internal disks are refused.
- The protected ASGARD cartridge is detected and refused.
- A drive that already holds PortableTAK gets an extra warning.
- Passwords are written only to a private temporary file inside
  `~/.portabletak-setup`, which is deleted as soon as setup finishes.
