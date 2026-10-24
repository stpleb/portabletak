#!/bin/sh
# Build the PortableTAK federation add-on into the node package payload:
#   share/ots-federation/<arch>/  = patched opentakserver package (1.7.13 + PR #343
#   + PortableTAK fixes, branch pt-fed) + grpcio 1.82.1 for that CPU.
# Drives run it by putting this folder first on PYTHONPATH; the sealed base venv
# is untouched.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
OTS="$HERE/ots"
DEST="$HERE/../host-installer-20260929/payload/usr/local/portabletak/share/ots-federation"
REV="$(git -C "$OTS" rev-parse --short pt-fed)"
[ -z "$(git -C "$OTS" status --porcelain -- opentakserver)" ] || { echo "uncommitted changes in ots/" >&2; exit 1; }
rm -rf "$DEST"; mkdir -p "$DEST"
TMP="$(mktemp -d)"
git -C "$OTS" archive pt-fed opentakserver | tar -x -C "$TMP"
find "$TMP" -name '__pycache__' -prune -exec rm -rf {} +
# Same PortableTAK patches as the sealed base (CA passwords, RabbitMQ credentials).
python3 "$HERE/patch-overlay.py" "$TMP/opentakserver" "$OTS"
for ARCH in aarch64 x86_64; do
    mkdir -p "$DEST/$ARCH"
    cp -R "$TMP/opentakserver" "$DEST/$ARCH/"
    (cd "$DEST/$ARCH" && unzip -q "$HERE"/wheels/grpcio-1.82.1-cp312-cp312-manylinux2014_${ARCH}.*.whl)
done
rm -rf "$TMP"
printf 'opentakserver 1.7.13 + federation (PR #343) pt-fed %s + base patches; grpcio 1.82.1\n' "$REV" > "$DEST/VERSION"
du -sh "$DEST"/*; cat "$DEST/VERSION"
