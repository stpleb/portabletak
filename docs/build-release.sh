#!/bin/sh
# Full PortableTAK release build + regression drive (run on the release Mac).
#   build-release.sh <version, e.g. 2026.10.23.4>
# Bumps the node package VERSION, rehashes, tars + signs the node package, builds the
# WSL distros (amd64, arm64), the Mac VM, both Setup bundles, then builds a fresh drive
# with the Mac Setup in a container (needs federation/e2e/C/secrets/request.json).
# Publish only if it prints PORTABLETAK_SETUP_SERVICES=7/7.
set -eu
V="$1"; TAG="$(echo "$V" | sed -E 's/^([0-9]{4})\.([0-9]{2})\.([0-9]{2})(\.([0-9]+))?$/\1\2\3\4/; s/\./-/')"
W="$(cd "$(dirname "$0")" && pwd)"; O="$W/../outputs"
NODE="$O/portabletak-host-installer-arm64-openwrt-$TAG.tar.gz"
cd "$W/host-installer-20260929"
printf '%s\n' "$V" > payload/usr/local/portabletak/VERSION
find payload -name __pycache__ -type d -prune -exec rm -rf {} +
find payload -name '._*' -exec rm -f {} +
{ cut -c67- SHA256SUMS | grep -v '^payload/'; find payload -type f; } | sort -u | while IFS= read -r f; do [ -f "$f" ] && shasum -a 256 "$f"; done > SHA256SUMS.new
mv SHA256SUMS.new SHA256SUMS
test "$(shasum -a 256 -c SHA256SUMS 2>&1 | grep -vc ': OK$')" = 0 && echo "sums ok"
cd "$W"
COPYFILE_DISABLE=1 tar --uid 0 --gid 0 --uname root --gname root --exclude='__pycache__' --exclude='.DS_Store' --exclude='._*' -czf "$NODE" host-installer-20260929
rm -rf start-here/.portabletak/node
node-loader/sign-node-package.sh "$NODE" "$V" start-here/.portabletak/node
# Every image must really be rebuilt: a failed step must stop the release, never reuse an old image.
(cd wsl-host && for A in amd64 arm64; do
    rm -f out/portabletak-wsl-$A.tar.gz out/portabletak-wsl-$A.tar.gz.sha256
    for TRY in 1 2; do
        docker run --rm --privileged --platform linux/$A -v "$W/clean-base-downloads:/base-arm64:ro" -v "$W/clean-base-downloads-amd64:/base-amd64:ro" -v "$W/host-installer-20260929/payload:/payload:ro" -v "$W/tailscale:/tailscale:ro" -v "$PWD:/work:ro" -v "$PWD/out:/out" ubuntu:24.04 /work/build-wsl-distro.sh $A > out/build-$A.log 2>&1
        grep "PT_WSL_DISTRO=" out/build-$A.log && [ -f out/portabletak-wsl-$A.tar.gz ] && break
        echo "WSL $A build attempt $TRY failed (see wsl-host/out/build-$A.log)"
        [ "$TRY" = 2 ] && exit 1
    done
done)
cp wsl-host/out/portabletak-wsl-*.tar.gz* start-here/.portabletak/wsl/
(cd mac-vm && rm -f out/vm.raw out/vm.qcow2 && docker run --rm --privileged --platform linux/arm64 -v "$W/wsl-host/out:/wsl:ro" -v "$PWD:/work:ro" -v "$PWD/out:/out" portabletak-setup:2 /work/build-vm.sh > out/build.log 2>&1
 grep "PT_VM_BUILT" out/build.log || { echo "Mac VM build failed (see mac-vm/out/build.log)"; exit 1; }
 cd out && /opt/homebrew/bin/qemu-img convert -c -O qcow2 vm.raw vm.qcow2)
cp mac-vm/out/vm.qcow2 mac-vm/out/vmlinuz mac-vm/out/initrd.img start-here/.portabletak/mac/
shasum -a 256 start-here/.portabletak/mac/vm.qcow2 | cut -d' ' -f1 > start-here/.portabletak/mac/vm.sha256
(cd win-setup && ./build-bundle.sh 2>&1 | tail -1 | cut -c1-16)
(cd mac-setup && ./build-bundle.sh 2>&1 | tail -1 | cut -c1-16)
rm -rf "$O/PortableTAK-Mac-Setup" && (cd "$O" && unzip -q PortableTAK-Mac-Setup.zip)
echo ALL-REBUILT
E="$W/federation/e2e"; M="$(cd "$O" && pwd)/PortableTAK-Mac-Setup"
rm -rf "$E/C/out" && mkdir -p "$E/C/out"
docker run --rm --privileged -v /dev:/dev -v "$M/payload:/payload:ro" -v "$M/clean-base:/art:ro" -v "$E/C/secrets:/secrets:ro" -v "$E/C/out:/out" -v "$M/start-here:/starthere:ro" -v "$M/container-setup.sh:/setup.sh:ro" --platform linux/arm64 portabletak-setup:2 /setup.sh 2>&1 | grep -E "SERVICES|NODE_PACKAGE|PORTABLETAK_SETUP=|ERROR"
