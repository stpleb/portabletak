#!/bin/sh
# Read-only pre-flight for a PortableTAK test host. Run from the Mac:
#
#   tools/portabletak-preflight.sh root@<host> [outputs-dir]
#
# Verifies local release artifacts, then reports the host's platform, tools,
# installed package, gate, mounts, block devices (by hardware identity), USB
# faults, throttling, and free space. Nothing is written on either machine
# except the report printed to stdout.

TARGET="$1"
OUTPUTS="${2:-$(cd "$(dirname "$0")/../../../outputs" 2>/dev/null && pwd)}"
[ -n "$TARGET" ] || { echo "usage: $0 root@host [outputs-dir]" >&2; exit 2; }

FAIL=0
check_local()
{
    if [ ! -f "$OUTPUTS/$1" ]; then
        echo "LOCAL_MISSING $1"; FAIL=1; return
    fi
    ACTUAL="$(shasum -a 256 "$OUTPUTS/$1" | awk '{print $1}')"
    if [ "$ACTUAL" = "$2" ]; then echo "LOCAL_OK $1"
    else echo "LOCAL_MISMATCH $1 $ACTUAL"; FAIL=1; fi
}

echo "== local artifacts ($OUTPUTS)"
check_local portabletak-clean-base/portabletak-clean-arm64-20260930-v3.tar.gz \
    670e7b15eaebf0d02469ee13b8c7a0cd233316e3bab5dd1585f57c1cef521861
check_local portabletak-host-installer-arm64-openwrt-20260929.tar.gz \
    "${PORTABLETAK_INSTALLER_SHA256:-37fc39803f0a68d2a5c9736fa0663c786f3e7700dc66f59590f11019ca86d5c2}"

echo "== remote $TARGET"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$TARGET" sh -s 2>&1 <<'REMOTE' | grep -v -e 'post-quantum' -e 'store now' -e 'pq.html'
section() { echo "== $1"; }
section platform
date; uname -a; cat /etc/openwrt_release 2>/dev/null | grep -E 'DISTRIB_(ID|RELEASE|ARCH)'
grep -m1 -E 'MemTotal' /proc/meminfo; uptime
section tools
for c in python3 sfdisk mkfs.ext4 e2fsck mount umount flock sha256sum tar; do
    printf '%s ' "$c"; command -v "$c" >/dev/null 2>&1 && echo ok || echo MISSING
done
python3 -c 'import sys,tarfile;print("python",sys.version.split()[0],"tar_filter",hasattr(tarfile,"fully_trusted_filter"))' 2>&1
section package
if [ -d /usr/local/portabletak ]; then
    echo "installed yes"
    grep -q FILESYSTEM_PATHS /usr/local/portabletak/lib/clean_base.py 2>/dev/null &&
        echo "lost_found_fix present" || echo "lost_found_fix ABSENT (install 20260929 package)"
    [ -x /usr/local/portabletak/bin/portabletak-media-layout ] &&
        echo "media_layout present" || echo "media_layout ABSENT"
    ls -1t /usr/local/portabletak-backups 2>/dev/null | head -3 | sed 's/^/backup /'
else
    echo "installed NO"
fi
test -e /usr/local/portabletak/release/first-run-enabled && echo "gate OPEN" || echo "gate CLOSED"
section api
netstat -ltn 2>/dev/null | grep ':8090 ' || echo "api not listening"
wget -qO- http://127.0.0.1:8090/api/provision/status 2>/dev/null; echo
section mounts
mount | grep -E '/mnt/|/dev/sd' || echo "no removable mounts"
section block_devices
for d in /sys/block/sd* /sys/block/mmcblk* /sys/block/nvme*; do
    [ -e "$d" ] || continue
    n="${d##*/}"; real="$(readlink -f "$d")"; usb=""
    p="$real"
    while [ "$p" != "/" ] && [ -n "$p" ]; do
        if [ -f "$p/idVendor" ]; then usb="$p"; break; fi
        p="$(dirname "$p")"
    done
    size=$(( $(cat "$d/size") / 2097152 ))
    printf '%s size=%sGiB model=%s' "$n" "$size" "$(cat "$d/device/model" 2>/dev/null | tr -s ' ')"
    if [ -n "$usb" ]; then
        printf ' usb=%s id=%s:%s serial=%s speed=%s' "${usb##*/}" "$(cat "$usb/idVendor")" \
            "$(cat "$usb/idProduct")" "$(cat "$usb/serial" 2>/dev/null)" "$(cat "$usb/speed")"
    else
        printf ' usb=no'
    fi
    for part in "$real"/"$n"*; do [ -f "$part/partition" ] && printf ' part=%s' "${part##*/}"; done
    echo
done
section usb_faults
echo "over_current $(dmesg | grep -c 'over-current') cannot_enable $(dmesg | grep -c 'Cannot enable') disconnect $(dmesg | grep -ci 'disconnect') io_error $(dmesg | grep -ciE 'I/O error|error -5')"
dmesg | grep -iE 'over-current|Cannot enable|I/O error|EXT4-fs (error|warning)' | tail -n 5
section throttle
vcgencmd get_throttled 2>/dev/null || echo "throttle n/a"
section space
df -h / /tmp 2>/dev/null
REMOTE
[ "$FAIL" -eq 0 ] && echo "PORTABLETAK_PREFLIGHT_LOCAL=OK" || echo "PORTABLETAK_PREFLIGHT_LOCAL=FAILED"
exit "$FAIL"
