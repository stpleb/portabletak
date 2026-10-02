#!/bin/bash
# Assemble PortableTAK Setup for Windows (zip) in outputs/.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
W="$(cd "$HERE/../.." && pwd)"
STAGE="$(mktemp -d)/PortableTAK-Windows-Setup"
mkdir -p "$STAGE/setup/clean-base"
cp "$HERE/PortableTAK Setup (Windows).cmd" "$HERE/README.txt" "$STAGE/"
cp "$HERE/setup/PortableTAK-Setup.ps1" "$STAGE/setup/"
cp "$W/work/mac-setup/container-setup.sh" "$STAGE/setup/"
cp -R "$W/work/start-here" "$STAGE/setup/start-here"
PIN="$(python3 -c "import sys;sys.path.insert(0,'$W/work/host-installer-20260929/payload/usr/local/portabletak/lib');from release import expected_base_archive;print(expected_base_archive())")"
for f in "$W"/outputs/portabletak-clean-base/portabletak-clean-*.tar.gz; do
    [ "$(shasum -a 256 "$f" | cut -d' ' -f1)" = "$PIN" ] && cp "$f" "$STAGE/setup/clean-base/" && break
done
ls "$STAGE"/setup/clean-base/*.tar.gz >/dev/null
( cd "$STAGE" && find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 > SHA256SUMS )
OUT="$W/outputs/PortableTAK-Windows-Setup.zip"
rm -f "$OUT"; ( cd "$(dirname "$STAGE")" && COPYFILE_DISABLE=1 zip -qry "$OUT" PortableTAK-Windows-Setup )
rm -rf "$(dirname "$STAGE")"
shasum -a 256 "$OUT"
