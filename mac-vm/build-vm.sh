#!/bin/bash
# Build the PortableTAK Mac VM disk (arm64) from the PortableTAK WSL arm64 host
# rootfs + Ubuntu's virtual kernel. Run in a privileged arm64 container.
# Mounts: /wsl (WSL host tarballs, ro), /work (this folder, ro), /out (rw).
set -eu
D=/tmp/vm; rm -rf $D; mkdir -p $D
tar -xpzf /wsl/portabletak-wsl-arm64.tar.gz -C $D --numeric-owner
cp /etc/resolv.conf $D/etc/resolv.conf
mount -t proc proc $D/proc; mount --bind /dev $D/dev; mount -t sysfs sysfs $D/sys
trap 'umount $D/proc $D/dev $D/sys 2>/dev/null || true' EXIT
printf '#!/bin/sh\nexit 101\n' > $D/usr/sbin/policy-rc.d; chmod 755 $D/usr/sbin/policy-rc.d
chroot $D /bin/sh -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq &&
    apt-get install -y -qq --no-install-recommends linux-image-virtual initramfs-tools kmod >/dev/null && apt-get clean'
install -m 700 /work/files/portabletak-vm-init $D/usr/local/portabletak/bin/portabletak-vm-init
install -m 700 /work/files/portabletak-vm-control $D/usr/local/portabletak/bin/portabletak-vm-control
rm -f $D/etc/wsl.conf $D/usr/sbin/policy-rc.d $D/etc/resolv.conf
echo "/dev/vda / ext4 defaults 0 1" > $D/etc/fstab
KVER="$(ls $D/lib/modules | head -n 1)"
cp "$D/boot/vmlinuz-$KVER" /out/vmlinuz; cp "$D/boot/initrd.img-$KVER" /out/initrd.img
rm -rf $D/var/lib/apt/lists/* $D/var/cache/apt/*
umount $D/proc $D/dev $D/sys; trap - EXIT
echo "PORTABLETAK_VM=$(date -u +%Y%m%d)-arm64 kernel $KVER" > $D/etc/portabletak/vm-version
rm -f /out/vm.raw; truncate -s 2G /out/vm.raw
mkfs.ext4 -q -F -L PORTABLETAK-VM -d $D /out/vm.raw
echo "PT_VM_BUILT kernel=$KVER rootfs=$(du -sh $D | cut -f1)"
