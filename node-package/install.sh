#!/bin/sh
set -eu

BASE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PAYLOAD="$BASE/payload"
RUNTIME="$BASE/portabletak-python311-arm64-musl.tar.gz"
EXPECTED_RUNTIME="f82710b21eab4ba64ffcb8b51fd64011ee546605227a975e89a6e1b47e8b54ac"

fail()
{
    echo "PORTABLETAK_INSTALL_ERROR=$1" >&2
    exit "$2"
}

[ "$(id -u)" = 0 ] || fail root_required 10
[ -r /etc/openwrt_release ] || fail openwrt_required 11
[ "$(uname -m)" = aarch64 ] || fail unsupported_architecture 12
[ -x /usr/bin/python3 ] || fail python3_missing 13
[ -x /usr/sbin/chroot ] || fail chroot_missing 14
[ -f "$BASE/SHA256SUMS" ] || fail manifest_missing 15
[ -f "$RUNTIME" ] || fail api_runtime_missing 16

(cd "$BASE" && sha256sum -c SHA256SUMS) || fail package_verification_failed 17
[ "$(sha256sum "$RUNTIME" | awk '{print $1}')" = "$EXPECTED_RUNTIME" ] ||
    fail api_runtime_verification_failed 18

if [ "${1:-}" = "--check" ]; then
    echo "PORTABLETAK_INSTALL_CHECK=PASS"
    exit 0
fi
[ "$#" = 0 ] || fail invalid_argument 2

STAMP="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_ROOT="/usr/local/portabletak-backups"
BACKUP="$BACKUP_ROOT/host-$STAMP.tar.gz"
mkdir -p "$BACKUP_ROOT"

if [ -d /usr/local/portabletak ]; then
    tar -C /usr/local -czf "$BACKUP" portabletak || fail backup_failed 20
    echo "PORTABLETAK_INSTALL_BACKUP=$BACKUP"
fi

/etc/init.d/portabletak-api stop >/dev/null 2>&1 || true
# tailscaled runs from the package directory; stop it so its binary can be replaced.
[ -x /usr/local/portabletak/bin/portabletak-tailscale ] &&
    /usr/local/portabletak/bin/portabletak-tailscale stop >/dev/null 2>&1 || true

mkdir -p /usr/local/portabletak
# Installed files belong to root whatever machine built the package, and only
# etc/ and usr/ are copied so the root directory itself is never touched.
chown -R 0:0 "$PAYLOAD" || fail payload_ownership_failed 28
tar -C "$PAYLOAD" -cf - etc usr | tar -C / -xf - || fail payload_install_failed 21

RUNTIME_TEMP="/usr/local/portabletak-runtime-$STAMP"
mkdir -p "$RUNTIME_TEMP"
tar -C "$RUNTIME_TEMP" -xzf "$RUNTIME" || fail api_runtime_extract_failed 22
# The runtime archive was made on a Mac: drop its AppleDouble (._*) metadata files.
find "$RUNTIME_TEMP" -name '._*' -exec rm -f {} + 2>/dev/null
[ -d "$RUNTIME_TEMP/python" ] || fail api_runtime_layout_invalid 23

if [ -d /usr/local/portabletak/python ]; then
    mv /usr/local/portabletak/python "/usr/local/portabletak/python.before-$STAMP"
fi
mv "$RUNTIME_TEMP/python" /usr/local/portabletak/python
chown -R 0:0 /usr/local/portabletak/python || fail api_runtime_ownership_failed 29
# Keep only the two newest backups of the package and of its Python runtime
# (drives can update nodes automatically; old copies must not fill the node).
ls -1t "$BACKUP_ROOT"/host-*.tar.gz 2>/dev/null | tail -n +3 | while read -r old; do rm -f "$old"; done
ls -1dt /usr/local/portabletak/python.before-* 2>/dev/null | tail -n +3 | while read -r old; do rm -rf "$old"; done
# The runtime archive also carries requirements.lock; the temp dir is ours.
rm -rf "$RUNTIME_TEMP"

chmod 700 /usr/local/portabletak/bin/*
chmod 700 /usr/local/portabletak/adapters/*/network
chmod 644 /usr/local/portabletak/lib/host-profile.sh
chmod 755 /usr/local/portabletak/lib/pt-rootfs
chmod 700 /etc/init.d/portabletak-api /etc/init.d/portabletak-autoload
chmod 600 /usr/local/portabletak/lib/*.py /usr/local/portabletak/api/*.py
chmod 600 /usr/local/portabletak/adapters/__init__.py \
    /usr/local/portabletak/adapters/*/*.py
chmod 700 /usr/local/portabletak/share/mediamtx/mediamtx \
    /usr/local/portabletak/share/mediamtx/ots-webhook.py
chmod 600 /usr/local/portabletak/share/mediamtx/mediamtx.yml
chmod 700 /usr/local/portabletak/share/tailscale/tailscale /usr/local/portabletak/share/tailscale/tailscaled
chmod 644 /usr/local/portabletak/share/tailscale/SOURCE

python3 -m py_compile /usr/local/portabletak/lib/*.py /usr/local/portabletak/api/*.py \
    /usr/local/portabletak/adapters/*/*.py ||
    fail python_validation_failed 24
sh -n /usr/local/portabletak/lib/host-profile.sh || fail shell_validation_failed 25
for script in /usr/local/portabletak/bin/* /usr/local/portabletak/adapters/*/network; do
    [ -f "$script" ] || continue
    case "$(head -n 1 "$script")" in
        '#!/bin/sh'*) sh -n "$script" || fail shell_validation_failed 25 ;;
    esac
done

PYTHONPATH=/usr/local/portabletak/python python3 -c \
    'import fastapi,pydantic,starlette,uvicorn' || fail api_import_failed 26

/etc/init.d/portabletak-api enable
/etc/init.d/portabletak-api start
# A 4 GB Pi can take several seconds to import FastAPI; poll up to 30 s.
TRIES=0
until wget -qO- http://127.0.0.1:8090/api/auth/session >/dev/null 2>&1; do
    TRIES=$((TRIES + 1))
    [ "$TRIES" -lt 30 ] || fail api_start_failed 27
    sleep 1
done

# Restart remote access if the mounted cartridge has it enabled (no-op otherwise).
/usr/local/portabletak/bin/portabletak-tailscale start </dev/null 2>/dev/null | sed -n "s/^PORTABLETAK_TAILSCALE=/PORTABLETAK_INSTALL_TAILSCALE=/p" || true
# A phone's USB tethering can serve as the node's internet uplink (like Ethernet).
command -v uci >/dev/null 2>&1 && /usr/local/portabletak/bin/portabletak-phone-uplink setup >/dev/null 2>&1
echo "PORTABLETAK_INSTALL=COMPLETE"
echo "PORTABLETAK_FIRST_RUN_GATE=CLOSED"
