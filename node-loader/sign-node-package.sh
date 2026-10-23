#!/bin/sh
# Sign a PortableTAK node package for drives (run on the release Mac).
#   sign-node-package.sh <node-package.tar.gz> <version> <out-dir>
# Writes <out-dir>/node-package.tar.gz, node-package.manifest and
# node-package.manifest.sig (usign format, checked by portabletak-loader on nodes).
# The private key never leaves ~/.portabletak-signing.
set -eu
PKG="$1"; VERSION="$2"; OUT="$3"
K="${PORTABLETAK_SIGNING_DIR:-$HOME/.portabletak-signing}"
OPENSSL="${OPENSSL:-/opt/homebrew/opt/openssl@3/bin/openssl}"
[ -f "$K/release.key.pem" ] || { echo "signing key missing in $K" >&2; exit 1; }
echo "$VERSION" | grep -Eq '^[0-9]{4}\.[0-9]{2}\.[0-9]{2}(\.[0-9]+)?$' || { echo "version must be YYYY.MM.DD or YYYY.MM.DD.N" >&2; exit 1; }
mkdir -p "$OUT"
cp "$PKG" "$OUT/node-package.tar.gz"
SUM="$(shasum -a 256 "$OUT/node-package.tar.gz" | cut -d' ' -f1)"
printf 'format=1\nversion=%s\narch=aarch64\nsha256=%s\n' "$VERSION" "$SUM" > "$OUT/node-package.manifest"
"$OPENSSL" pkeyutl -sign -inkey "$K/release.key.pem" -rawin -in "$OUT/node-package.manifest" -out "$OUT/sig.raw"
python3 - "$K" "$OUT" <<'PY'
import base64, sys
k, out = sys.argv[1:]
keynum = open(k + '/keynum.raw', 'rb').read()
sig = open(out + '/sig.raw', 'rb').read()
assert len(keynum) == 8 and len(sig) == 64
open(out + '/node-package.manifest.sig', 'w').write(
    'untrusted comment: signed by PortableTAK release key %s\n%s\n' % (keynum.hex(), base64.b64encode(b'Ed' + keynum + sig).decode()))
PY
rm -f "$OUT/sig.raw"
echo "PORTABLETAK_SIGNED version=$VERSION sha256=$SUM"
