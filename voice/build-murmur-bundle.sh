#!/bin/sh
# Build the PortableTAK voice server bundle (Murmur 1.5 + Python Ice) for one architecture,
# from official Ubuntu 24.04 packages. Run in ubuntu:24.04 for that architecture:
#   docker run --rm --platform linux/arm64 -v "$PWD:/w" ubuntu:24.04 /w/build-murmur-bundle.sh /w/out aarch64
# Writes <out>/murmur-<arch>.tar.gz (root/, PACKAGES, licenses/).
# Only the packages the PortableTAK base system does not already have are bundled (the base
# list matches portabletak-provision/build/install-clean.sh). The result runs inside the
# drive's chroot with LD_LIBRARY_PATH / PYTHONPATH pointing at it.
set -eu
OUT="$1"; ARCH="$2"; S=/tmp/murmur
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates build-essential python3-dev python3 python3-venv \
    postgresql-16 postgresql-16-postgis-3 rabbitmq-server nginx openssl tzdata iproute2 curl libmagic1 >/dev/null
apt-get install -y -qq --no-install-recommends --download-only mumble-server python3-zeroc-ice >/dev/null
rm -rf "$S" && mkdir -p "$S/root" "$S/licenses" "$OUT"
for deb in /var/cache/apt/archives/*.deb; do
    pkg="$(dpkg-deb -f "$deb" Package)"
    echo "$pkg $(dpkg-deb -f "$deb" Version)" >> "$S/PACKAGES"
    dpkg-deb -x "$deb" "$S/root"
    cp "$S/root/usr/share/doc/$pkg/copyright" "$S/licenses/$pkg.copyright" 2>/dev/null || true
done
# Keep only what runs: the server, shared libraries, Qt plugins and the Python Ice module.
cd "$S/root"
rm -rf etc lib usr/share var 2>/dev/null || true
find . -type d -empty -delete
sort -o "$S/PACKAGES" "$S/PACKAGES"
tar -C "$S" --owner=0 --group=0 -czf "$OUT/murmur-$ARCH.tar.gz" root PACKAGES licenses
du -sh "$S" "$OUT/murmur-$ARCH.tar.gz"
