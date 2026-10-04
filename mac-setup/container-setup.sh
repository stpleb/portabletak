#!/bin/bash
# Runs inside the setup container. Lays out a PortableTAK cartridge on a disk
# image and provisions it (first setup), including anything that needs the
# internet, so the node it is plugged into later needs none.
#   /payload  host installer payload (ro)     /art  clean base archive (ro)
#   /secrets/request.json  setup answers (ro) /out  image output (rw)
set -u
# Folder locations (the Mac container mounts them here; the Windows host passes its own).
PAYLOAD_DIR="${PT_PAYLOAD_DIR:-/payload}"; ART_DIR="${PT_ART_DIR:-/art}"; SECRETS_DIR="${PT_SECRETS_DIR:-/secrets}"
OUT_DIR="${PT_OUT_DIR:-/out}"; STARTHERE_DIR="${PT_STARTHERE_DIR:-/starthere}"
say() { echo "PORTABLETAK_SETUP_STEP=$*"; }
fail() { echo "PORTABLETAK_SETUP=FAILED"; echo "PORTABLETAK_SETUP_ERROR=$*"; exit 1; }
R=/usr/local/portabletak
PIN="$(PT_LIB="$PAYLOAD_DIR/usr/local/portabletak/lib" python3 -c 'import os,sys;sys.path.insert(0,os.environ["PT_LIB"]);from release import expected_base_archive;print(expected_base_archive())')"
ARCHIVE=""
for f in $ART_DIR/portabletak-clean-*.tar.gz; do
    [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$PIN" ] && ARCHIVE="$f" && break
done
[ -n "$ARCHIVE" ] || { echo "PORTABLETAK_SETUP=FAILED"; echo "PORTABLETAK_SETUP_ERROR=clean_base_archive_missing"; exit 1; }
IMAGE=$OUT_DIR/cartridge.img
SIZE_GIB="${PT_IMAGE_GIB:-8}"

say host
# In the Windows (WSL) host the host software is already installed.
if [ "$(readlink -f $PAYLOAD_DIR/usr/local/portabletak)" != "$R" ]; then
    cp -a $PAYLOAD_DIR/usr/local/portabletak /usr/local/ && chown -R 0:0 $R
fi
DEV=eth0
IP="$(ip -4 -o addr show dev eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
if [ -z "$IP" ]; then
    # WSL mirrored networking: use the first non-loopback IPv4 interface.
    set -- $(ip -4 -o addr show scope global | awk '{split($4,a,"/"); print $2, a[1]; exit}')
    DEV="$1"; IP="$2"
fi
[ -n "$IP" ] || fail no_network_address
mkdir -p /etc/portabletak
printf 'PT_PROFILE=linux\nPT_SERVICE_IP=%s\nPT_SERVICE_DEV=%s\nPT_BOOT_DEV=%s\n' "$IP" "$DEV" "$DEV" > /etc/portabletak/host-profile

# The cartridge resolves names via 127.0.0.1 (as on a node, where dnsmasq
# answers); forward that to the container's DNS so setup can use the internet.
NS="$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf)"
dnsmasq --no-resolv --server="$NS" --listen-address=127.0.0.1 --bind-interfaces \
    --port=53 --no-hosts --pid-file=/run/pt-dnsmasq.pid 2>/dev/null ||
    echo "PORTABLETAK_SETUP_WARNING=dns_forwarder_unavailable"

say layout
rm -f "$IMAGE"; truncate -s "${SIZE_GIB}G" "$IMAGE"
LOOP="$(losetup -f --show -P "$IMAGE")" || fail loop_device
cleanup() {
    $R/bin/portabletak-stop >/dev/null 2>&1
    $R/bin/portabletak-unprepare >/dev/null 2>&1
    umount /mnt/asgard 2>/dev/null
    losetup -d "$LOOP" 2>/dev/null
}
trap cleanup EXIT
python3 - "$LOOP" "$ARCHIVE" <<'PY' || fail layout
import sys
from pathlib import Path
sys.path.insert(0, '/usr/local/portabletak/lib')
import media_layout
from release import expected_base_seal, expected_base_archive
loop, archive = Path(sys.argv[1]), sys.argv[2]
media_layout.verify_archive(archive, expected_base_archive(), expected_base_seal())
media_layout.lay_down(loop, Path('/sys/block') / loop.name, media_layout.partition_name(loop.name),
                      archive, expected_base_seal(), '/var/tmp/layout', log=lambda *_: None)
PY
mkdir -p /mnt/asgard
mount -t ext4 "${LOOP}p1" /mnt/asgard || fail mount
SEAL="$(python3 -c 'import sys;sys.path.insert(0,"/usr/local/portabletak/lib");from release import expected_base_seal;print(expected_base_seal())')"
$R/bin/portabletak-publish-identity /mnt/asgard "$SEAL" >/dev/null || fail publish_identity

if [ "${PT_BLANK:-0}" = 1 ]; then
    echo "PORTABLETAK_SETUP_BLANK=1"
else
say provisioning
mkdir -p $R/release && touch $R/release/first-run-enabled
PT_REQUEST="$SECRETS_DIR/request.json" python3 - <<'PY' || fail launch
import json, os, sys
sys.path.insert(0, '/usr/local/portabletak/lib')
from bootstrap_job import BootstrapJobSupervisor
request = json.load(open(os.environ['PT_REQUEST']))
result = BootstrapJobSupervisor().launch(request)
print('PORTABLETAK_SETUP_JOB=' + str(result.get('job', result)))
PY
JOB=running
for i in $(seq 1 180); do
    JOB="$(sed -n 's/.*"job": *"\([a-z]*\)".*/\1/p' /tmp/portabletak/bootstrap-job.json 2>/dev/null)"
    [ "$JOB" = running ] || [ -z "$JOB" ] || break
    sleep 5
done
rm -f $R/release/first-run-enabled
python3 -c 'import json;d=json.load(open("/mnt/asgard/portabletak/provision/status.json"));print("PORTABLETAK_SETUP_PHASES="+",".join(h["phase"] for h in d["history"]));e=d.get("error");print("PORTABLETAK_SETUP_PROVISION_ERROR="+str(e)) if e else None'
[ "$JOB" = complete ] || fail "provisioning_$JOB"

say verify
RUNNING="$($R/bin/portabletak-health | grep -cE '^PORTABLETAK_(POSTGRESQL|RABBITMQ|MEDIAMTX|OTS|COT|EUD|NGINX)=RUNNING')"
echo "PORTABLETAK_SETUP_SERVICES=$RUNNING/7"
[ "$RUNNING" = 7 ] || fail services
count_icons() {
    chroot "$($R/lib/pt-rootfs /mnt/asgard)" su postgres -s /bin/sh -c "psql -X -A -t -d ots -c 'select count(*) from icons'" 2>/dev/null </dev/null | tr -d ' '
}
# OpenTAKServer downloads its icon sets on first start; let a download in progress finish.
for i in $(seq 1 60); do
    ICONS="$(count_icons)"
    [ "${ICONS:-0}" -gt 0 ] 2>/dev/null && break
    tail -n 1 /mnt/asgard/opentak/data/logs/opentakserver.log 2>/dev/null | grep -q "Downloading icons" || break
    sleep 5
done
echo "PORTABLETAK_SETUP_ICONS=${ICONS:-0}"

fi

say finishing
if [ "${PT_BLANK:-0}" != 1 ]; then
    $R/bin/portabletak-stop >/dev/null 2>&1 || fail stop
fi
$R/bin/portabletak-unprepare >/dev/null 2>&1
# The signed node package: an OpenMANET node with the PortableTAK loader installs
# or updates PortableTAK from it when the drive is plugged in.
if [ -f "$STARTHERE_DIR/.portabletak/node/node-package.manifest.sig" ]; then
    mkdir -p /mnt/asgard/portabletak/node
    cp "$STARTHERE_DIR"/.portabletak/node/node-package.* /mnt/asgard/portabletak/node/ || fail node_package_copy
    echo "PORTABLETAK_SETUP_NODE_PACKAGE=$(sed -n 's/^version=//p' /mnt/asgard/portabletak/node/node-package.manifest)"
fi
sync; umount /mnt/asgard || fail unmount
e2fsck -fn "${LOOP}p1" >/dev/null 2>&1 || fail filesystem_check
losetup -d "$LOOP"; trap - EXIT

say start_here
# Final drive: partition 1 = FAT32 "Start here" (readable on Windows and Mac:
# launchers, guides, dashboard QR; support files in a hidden folder),
# partition 2 = the PortableTAK cartridge (nodes find it on any partition).
START_MIB="${PT_START_HERE_MIB:-1024}"
P1_SECTORS=$((START_MIB * 2048))
P2_START=$((2048 + P1_SECTORS))
EXT_BYTES=$(( $(stat -c %s "$IMAGE") - 1048576 ))
DRIVE=$OUT_DIR/drive.img
rm -f "$DRIVE"; truncate -s $(( P2_START * 512 + EXT_BYTES )) "$DRIVE"
printf 'label: dos\nstart=2048, size=%s, type=c\nstart=%s, type=83\n' "$P1_SECTORS" "$P2_START" |
    sfdisk -q "$DRIVE" || fail partition_table
START=/tmp/start-here.img
rm -f "$START"; truncate -s $((START_MIB * 1048576)) "$START"
mkfs.vfat -F 32 -n PORTABLETAK "$START" >/dev/null || fail start_here_format
if [ -d $STARTHERE_DIR ]; then
    # Never copy Windows download markers (names with ':' would make mcopy prompt).
    find $STARTHERE_DIR -name '*:*' -delete 2>/dev/null || true
    for item in $STARTHERE_DIR/* $STARTHERE_DIR/.portabletak; do
        [ -e "$item" ] && { mcopy -s -Q -D o -i "$START" "$item" ::/ </dev/null || fail start_here_copy; }
    done
    mdeltree -i "$START" ::/.portabletak/node 2>/dev/null || true   # lives on the Linux partition
    mattrib -i "$START" +h ::/.portabletak 2>/dev/null || true
fi
# Keep macOS from indexing / journaling the volume (its folders would show on Windows).
: > /tmp/empty
mcopy -Q -i "$START" /tmp/empty ::/.metadata_never_index && mattrib -i "$START" +h ::/.metadata_never_index
mmd -i "$START" ::/.fseventsd && mcopy -Q -i "$START" /tmp/empty ::/.fseventsd/no_log && mattrib -i "$START" +h ::/.fseventsd
python3 - "$START" <<'PY2' || fail start_here_qr
import subprocess, sys, qrcode
img = qrcode.make('https://10.41.254.1:8444/', box_size=12, border=4)
img.save('/tmp/qr.png')
subprocess.run(['mcopy', '-Q', '-i', sys.argv[1], '/tmp/qr.png',
                '::/Scan to open the dashboard (node).png'], check=True)
PY2
dd if="$START" of="$DRIVE" bs=1M seek=1 conv=notrunc status=none || fail start_here_write
dd if="$IMAGE" of="$DRIVE" bs=1M skip=1 seek=$((P2_START / 2048)) conv=notrunc status=none || fail cartridge_copy
rm -f "$START" "$IMAGE"; mv "$DRIVE" "$IMAGE"
echo "PORTABLETAK_SETUP_START_HERE=$(mdir -i "$IMAGE@@1M" -b ::/ 2>/dev/null | wc -l | tr -d ' ') items"
echo "PORTABLETAK_SETUP_IMAGE_BYTES=$(stat -c %s "$IMAGE")"
echo "PORTABLETAK_SETUP=COMPLETE"
