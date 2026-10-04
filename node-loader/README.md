# PortableTAK loader for OpenMANET nodes

With the loader on a node, plugging in a PortableTAK drive:

1. installs PortableTAK on the node if it does not have it (then turns on autoload),
2. updates PortableTAK if the drive carries a newer signed node package,
3. starts PortableTAK from the drive (as before).

It never downgrades, never changes anything while a cartridge is running, and only
accepts a node package whose manifest is signed with the PortableTAK release key
(checked with OpenWrt's built-in `usign`). Every PortableTAK drive made with Setup
2026.10.04 or later carries the signed package on its Linux partition
(`/portabletak/node/`).

## Files (all plain text)

| Node path | Mode | Purpose |
|---|---|---|
| `/usr/sbin/portabletak-loader` | 755 | does the check, install and start |
| `/etc/hotplug.d/block/10-portabletak-loader` | 644 | runs it when a USB drive is plugged in |
| `/etc/portabletak/loader.pub` | 644 | PortableTAK release public key (usign format) |

## Field extension

Ship the three files above at those paths (copy them from
`node-package/payload/` in this repo), and list them in `/etc/sysupgrade.conf`
so they survive firmware upgrades. The node needs `usign` (standard on OpenWrt),
`python3` and `chroot` (needed by the PortableTAK node package itself), aarch64.

## Stock node, one time

Copy `portabletak-loader-install.sh` (release asset) to the node and run it as root:

    scp portabletak-loader-install.sh root@<node>:/tmp/
    ssh root@<node> sh /tmp/portabletak-loader-install.sh

## Logs

`portabletak-loader status` on the node, or `/tmp/portabletak/loader.log`
(install output: `/tmp/portabletak/loader-install.log`).

## Release signing (owner's Mac)

`sign-node-package.sh <node package .tar.gz> <YYYY.MM.DD> <out dir>` signs with the
private key in `~/.portabletak-signing/` (never in this repo). Keep a backup of that
folder: without the key, nodes cannot be updated from drives until each node gets a
loader with a new public key.
