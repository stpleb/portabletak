#!/bin/bash
# Build the PortableTAK WSL2 host distribution for one CPU (run in a container
# of that CPU). Mounts: /base (ubuntu-base tarball dir, ro), /payload (host
# installer payload, ro). Base tarballs at /base-amd64 and /base-arm64., /work (this folder, ro), /out (rw),
# /tailscale (Tailscale builds, ro; amd64 only).
#   build-wsl-distro.sh amd64|arm64
set -eu
ARCH="$1"
BASE="$(ls /base-$ARCH/ubuntu-base-24.04.5-base-$ARCH.tar.gz)"
D=/tmp/distro; rm -rf $D; mkdir -p $D
tar -xpzf "$BASE" -C $D --numeric-owner
cp /etc/resolv.conf $D/etc/resolv.conf
mount -t proc proc $D/proc; mount --bind /dev $D/dev
trap 'umount $D/proc $D/dev 2>/dev/null || true' EXIT
printf '#!/bin/sh\nexit 101\n' > $D/usr/sbin/policy-rc.d; chmod 755 $D/usr/sbin/policy-rc.d
chroot $D /bin/sh -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    kmod python3 python3-pip python3-qrcode python3-pil dnsmasq-base dosfstools mtools e2fsprogs fdisk util-linux mount iproute2 procps iputils-ping ca-certificates openssl bsdutils coreutils tar gzip \
    >/dev/null && apt-get clean'
mkdir -p $D/usr/local/portabletak
cp -a /payload/usr/local/portabletak/. $D/usr/local/portabletak/
# Gateway mode is node-only. Tailscale stays: remote access uses the drive's own
# Tailscale on computers too. The payload has the arm64 build; amd64 needs the amd64
# build of the same version (mounted at /tailscale, checksum-verified when fetched).
rm -f $D/usr/local/portabletak/bin/portabletak-gateway
if [ "$ARCH" = amd64 ]; then
    TS=/tailscale/tailscale_1.102.4_amd64
    [ -x "$TS/tailscale" ] && [ -x "$TS/tailscaled" ] || { echo "PT_WSL_ERROR=tailscale_amd64_missing" >&2; exit 1; }
    install -m 700 "$TS/tailscale" "$TS/tailscaled" $D/usr/local/portabletak/share/tailscale/
    printf 'Tailscale 1.102.4, official static build for Linux amd64 (BSD-3-Clause).\nSource archive: https://pkgs.tailscale.com/stable/tailscale_1.102.4_amd64.tgz\n' \
        > $D/usr/local/portabletak/share/tailscale/SOURCE
fi
cp /work/requirements.txt $D/tmp/requirements.txt
chroot $D /usr/bin/python3 -m pip install -q --no-cache-dir --disable-pip-version-check \
    --target /usr/local/portabletak/python -r /tmp/requirements.txt
rm -f $D/tmp/requirements.txt
install -m 644 /work/files/wsl.conf $D/etc/wsl.conf
for f in portabletak-wsl-boot portabletak-wsl-api portabletak-wsl-start portabletak-wsl-stop portabletak-wsl-provision portabletak-wsl-make-drive; do
    install -m 755 /work/files/$f $D/usr/local/portabletak/bin/$f
done
chown -R 0:0 $D/usr/local/portabletak
chmod 700 $D/usr/local/portabletak/bin/* ; chmod 755 $D/usr/local/portabletak/lib/pt-rootfs
mkdir -p $D/mnt/asgard $D/etc/portabletak
rm -f $D/usr/sbin/policy-rc.d $D/etc/resolv.conf
rm -rf $D/var/lib/apt/lists/* $D/var/cache/apt/* $D/root/.cache
find $D/usr/local/portabletak -name __pycache__ -type d -prune -exec rm -rf {} +
umount $D/proc $D/dev; trap - EXIT
echo "PORTABLETAK_WSL_DISTRO=$(date -u +%Y%m%d)-$ARCH" > $D/etc/portabletak/wsl-distro-version
OUT=/out/portabletak-wsl-$ARCH.tar.gz
tar --numeric-owner -C $D -czf "$OUT" .
sha256sum "$OUT" | cut -d' ' -f1 > "$OUT.sha256"
echo "PT_WSL_DISTRO=$OUT $(du -h "$OUT" | cut -f1) $(cat "$OUT.sha256")"
