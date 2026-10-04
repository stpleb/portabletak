#!/bin/sh
# Build portabletak-loader-install.sh: one self-contained file that puts the
# PortableTAK loader on an OpenWrt/OpenMANET node (or into a field extension).
# The three files are plain text, embedded as-is (nodes may lack base64).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$HERE/../host-installer-20260929/payload"
OUT="${1:-$HERE/../../outputs/portabletak-loader-install.sh}"
emit() {   # emit <path on node> <mode> <source file>
    grep -q '^PORTABLETAK_EOF$' "$3" && { echo "terminator clash in $3" >&2; exit 1; }
    printf "mkdir -p '%s'\ncat > '%s' <<'PORTABLETAK_EOF'\n" "$(dirname "$1")" "$1"
    cat "$3"
    printf "PORTABLETAK_EOF\nchmod %s '%s'\n" "$2" "$1"
}
{
cat <<'HEAD'
#!/bin/sh
# PortableTAK loader installer for OpenWrt / OpenMANET nodes (aarch64).
# After this, plugging in a PortableTAK drive installs or updates PortableTAK on
# the node and starts it; only packages signed with the PortableTAK release key
# are accepted. Run once as root:   sh portabletak-loader-install.sh
# Files: /usr/sbin/portabletak-loader, /etc/hotplug.d/block/10-portabletak-loader,
#        /etc/portabletak/loader.pub   (remove them to uninstall)
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -r /etc/openwrt_release ] || { echo "this is not an OpenWrt node" >&2; exit 1; }
command -v usign >/dev/null || { echo "usign is missing on this node" >&2; exit 1; }
HEAD
emit /usr/sbin/portabletak-loader 755 "$P/usr/sbin/portabletak-loader"
emit /etc/hotplug.d/block/10-portabletak-loader 644 "$P/etc/hotplug.d/block/10-portabletak-loader"
emit /etc/portabletak/loader.pub 644 "$P/etc/portabletak/loader.pub"
cat <<'TAIL'
# Keep the loader across firmware upgrades (it reinstalls PortableTAK from the drive).
grep -qx /usr/sbin/portabletak-loader /etc/sysupgrade.conf 2>/dev/null || cat >> /etc/sysupgrade.conf <<'KEEP'
/usr/sbin/portabletak-loader
/etc/hotplug.d/block/10-portabletak-loader
/etc/portabletak/
KEEP
echo "PortableTAK loader installed. Plug in a PortableTAK drive (or unplug and plug it in again)."
TAIL
} > "$OUT"
chmod 755 "$OUT"
echo "$OUT"
