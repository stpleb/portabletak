# PortableTAK node package (OpenWrt / OpenMANET, arm64)

Installs PortableTAK's host side on an ARM64 OpenWrt/OpenMANET node: the
dashboard API, the controller that finds, checks, mounts and starts a
PortableTAK drive (the server itself runs from the drive), the OpenMANET
adapter (service address `10.41.250.10` on `br-ahwlan`), live feeds (ADS-B,
AIS), offline map tools, the TAK ICU video relay, and lifecycle tools.

Nodes normally install and update this package by themselves from a drive's
Start Here partition, through the PortableTAK loader (`portabletak-loader`,
installed once with `portabletak-loader-install.sh`); only packages signed with
the PortableTAK release key are accepted.

Manual install (administrators):
- `./install.sh --check`: read-only platform and checksum check.
- `./install.sh` as root: installs. It backs up the previous installation first;
  the printed backup archive can be passed to `./rollback.sh`.

The package never formats a drive or changes mesh networking. Drive data stays
under `/mnt/asgard` on the drive.
