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
Plug the drive into an OpenMANET node with the PortableTAK host package
installed. The node sets the server up for itself and starts it; open the
dashboard as usual and sign in as `administrator`.

## Safety
- Only external drives are listed; internal disks are refused.
- The protected ASGARD cartridge is detected and refused.
- A drive that already holds PortableTAK gets an extra warning.
- Passwords are written only to a private temporary file inside
  `~/.portabletak-setup`, which is deleted as soon as setup finishes.
