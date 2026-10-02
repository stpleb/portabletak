#!/bin/bash
# Assemble the distributable Mac setup folder (zip) in outputs/.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
W="$(cd "$HERE/../.." && pwd)"
STAGE="$(mktemp -d)/PortableTAK-Mac-Setup"
mkdir -p "$STAGE/clean-base"
cp "$HERE/portabletak-mac-setup" "$HERE/PortableTAK Setup.command" "$HERE/container-setup.sh" "$HERE/Dockerfile" "$HERE/README.md" "$STAGE/"
cp -R "$W/work/host-installer-20260929/payload" "$STAGE/payload"
cp -R "$W/work/start-here" "$STAGE/start-here"
find "$STAGE/payload" -name __pycache__ -type d -prune -exec rm -rf {} +
PIN="$(python3 -c "import sys;sys.path.insert(0,'$STAGE/payload/usr/local/portabletak/lib');from release import expected_base_archive;print(expected_base_archive())")"
for f in "$W"/outputs/portabletak-clean-base/portabletak-clean-*.tar.gz; do
    [ "$(shasum -a 256 "$f" | cut -d' ' -f1)" = "$PIN" ] && cp "$f" "$STAGE/clean-base/" && break
done
ls "$STAGE"/clean-base/*.tar.gz >/dev/null
( cd "$STAGE" && find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 > SHA256SUMS )
OUT="$W/outputs/PortableTAK-Mac-Setup.zip"
rm -f "$OUT"; ( cd "$(dirname "$STAGE")" && COPYFILE_DISABLE=1 zip -qry "$OUT" PortableTAK-Mac-Setup )
rm -rf "$(dirname "$STAGE")"
shasum -a 256 "$OUT"
