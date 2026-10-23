#!/bin/bash
# Run a PortableTAK drive image like a node would (inside portabletak-setup:2,
# privileged, -v /dev:/dev). usage: run-drive.sh /out/cartridge.img
set -u
cp -a /payload/usr/local/portabletak /usr/local/ && chown -R 0:0 /usr/local/portabletak
IP="$(ip -4 -o addr show dev eth0 | awk '{print $4}' | cut -d/ -f1)"
mkdir -p /etc/portabletak
printf 'PT_PROFILE=linux\nPT_SERVICE_IP=%s\nPT_SERVICE_DEV=eth0\nPT_BOOT_DEV=eth0\n' "$IP" > /etc/portabletak/host-profile
NS="$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf)"
dnsmasq --no-resolv --server="$NS" --listen-address=127.0.0.1 --bind-interfaces --port=53 --no-hosts 2>/dev/null
LOOP="$(losetup -f --show -P "$1")"; sleep 2
mkdir -p /mnt/asgard && mount -t ext4 "${LOOP}p2" /mnt/asgard || { echo RUN_DRIVE=MOUNT_FAILED; sleep infinity; }
echo "RUN_DRIVE_IP=$IP LOOP=$LOOP"
/usr/local/portabletak/bin/portabletak-start 2>&1 | grep -E "ERROR|FEDERATION|READY|ADOPT" | tail -12
echo RUN_DRIVE=STARTED
trap '/usr/local/portabletak/bin/portabletak-stop >/dev/null 2>&1; umount /mnt/asgard; losetup -d "$LOOP"; exit 0' TERM INT
sleep infinity & wait
