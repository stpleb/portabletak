# PortableTAK OpenWrt host installer candidate

This package installs the host-side PortableTAK controller, management API,
independent API dependencies, OpenMANET adapter, and lifecycle tools on an
ARM64 OpenWrt/OpenMANET node. It backs up an existing installation before
writing files and leaves the physical first-run activation gate closed.

Core provisioning is host-neutral. This package selects the OpenMANET host
adapter, which owns the `br-ahwlan` service address and host runtime metadata.
The verified MediaMTX runtime supplement is carried by the host package so it
does not alter the cartridge's sealed clean base.

Run `./install.sh --check` for read-only platform and checksum validation.
Run `./install.sh` as root to install. The printed backup archive can be passed
to `./rollback.sh` if rollback is required.

The package retains the current `/mnt/asgard` compatibility path. It does not
format a cartridge, alter mesh networking, enable first-run activation, or
claim that physical USB acceptance has passed.
