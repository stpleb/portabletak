#!/bin/sh
# Copy a newer node package from the drive's "Start here" partition (FAT, partition 1)
# to its Linux partition (/mnt/asgard/portabletak/node), where nodes install it from.
# A Start Here refreshed on a Mac or PC then updates the next node the drive is
# plugged into. Nodes check the signature before installing; this only copies.
#   node-package-sync.sh [/mnt/asgard]   -> PORTABLETAK_NODE_PACKAGE_SYNCED=<version>
ROOT="${1:-/mnt/asgard}"
DEST="$ROOT/portabletak/node"
MNT=/tmp/portabletak-start-here

version() { sed -n 's/^version=//p' "$1" 2>/dev/null | head -n 1; }

# The partition holding $ROOT, then partition 1 of the same disk (sda2 -> sda1, vdb2 -> vdb1).
dev="$(awk -v r="$ROOT" '$2==r {print $1}' /proc/mounts | tail -n 1)"
case "$dev" in
    /dev/sd[a-z][0-9]*|/dev/vd[a-z][0-9]*) fat="$(echo "$dev" | sed -E 's/[0-9]+$//')1" ;;
    /dev/loop*p[0-9]*|/dev/nvme*p[0-9]*|/dev/mmcblk*p[0-9]*) fat="$(echo "$dev" | sed -E 's/[0-9]+$//')1" ;;
    *) exit 0 ;;
esac
[ -b "$fat" ] && [ "$fat" != "$dev" ] || exit 0
grep -q "^$fat " /proc/mounts && exit 0

mkdir -p "$MNT"
mount -t vfat -o ro "$fat" "$MNT" 2>/dev/null || exit 0
SRC="$MNT/.portabletak/node"
NEW="$(version "$SRC/node-package.manifest")"
OLD="$(version "$DEST/node-package.manifest")"
# Versions are YYYY.MM.DD[.N]: compare as text.
if [ -n "$NEW" ] && [ -f "$SRC/node-package.tar.gz" ] && [ -f "$SRC/node-package.manifest.sig" ] &&
   awk -v a="$NEW" -v b="$OLD" 'BEGIN { exit !((a "") > (b "")) }'; then
    mkdir -p "$DEST"
    # Package first, manifest last: a node never sees a manifest without its package.
    if cp "$SRC/node-package.tar.gz" "$DEST/node-package.tar.gz.new" &&
       cp "$SRC/node-package.manifest.sig" "$DEST/node-package.manifest.sig.new" &&
       cp "$SRC/node-package.manifest" "$DEST/node-package.manifest.new"; then
        mv -f "$DEST/node-package.tar.gz.new" "$DEST/node-package.tar.gz"
        mv -f "$DEST/node-package.manifest.sig.new" "$DEST/node-package.manifest.sig"
        mv -f "$DEST/node-package.manifest.new" "$DEST/node-package.manifest"
        sync
        echo "PORTABLETAK_NODE_PACKAGE_SYNCED=$NEW"
    else
        rm -f "$DEST"/*.new
        echo "PORTABLETAK_NODE_PACKAGE_SYNC=FAILED"
    fi
fi
umount "$MNT" 2>/dev/null
rmdir "$MNT" 2>/dev/null
exit 0
