#!/bin/sh
set -eu

SOURCE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/bin/portabletak-autoload"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT

prepare()
{
    rm -rf "$TEMP/bin" "$TEMP/runtime"
    mkdir -p "$TEMP/bin" "$TEMP/runtime"
    sed "s#/usr/local/portabletak/bin#$TEMP/bin#g; s#/tmp/portabletak#$TEMP/runtime#g" "$SOURCE" > "$TEMP/autoload"
    chmod 755 "$TEMP/autoload"
    cat > "$TEMP/bin/portabletak-discover" <<'EOF'
#!/bin/sh
printf 'PORTABLETAK_DEVICE=/dev/test1\n'
EOF
    cat > "$TEMP/bin/portabletak-mount" <<'EOF'
#!/bin/sh
touch "$(dirname "$0")/mounted"
EOF
    cat > "$TEMP/bin/portabletak-start" <<'EOF'
#!/bin/sh
touch "$(dirname "$0")/started"
EOF
    chmod 755 "$TEMP/bin/"*
}

set_detect()
{
    state="$1"
    mounted="$2"
    cat > "$TEMP/bin/portabletak-detect" <<EOF
#!/bin/sh
if [ "$mounted" = 0 ] && [ ! -f "$TEMP/bin/mounted" ]; then exit 1; fi
printf 'PORTABLETAK_PROVISIONING_STATE=$state\\n'
EOF
    chmod 755 "$TEMP/bin/portabletak-detect"
}

prepare
set_detect existing 1
output="$(sh "$TEMP/autoload")"
test -f "$TEMP/bin/started"
printf '%s\n' "$output" | grep -q '^PORTABLETAK_AUTOLOAD=RUNNING$'

prepare
set_detect provisioned 0
output="$(sh "$TEMP/autoload")"
test -f "$TEMP/bin/mounted"
test -f "$TEMP/bin/started"
printf '%s\n' "$output" | grep -q '^PORTABLETAK_AUTOLOAD=CARTRIDGE_FOUND$'

for state in new provisioning failed recovery; do
    prepare
    set_detect "$state" 1
    output="$(sh "$TEMP/autoload")"
    test ! -f "$TEMP/bin/started"
    printf '%s\n' "$output" | grep -q '^PORTABLETAK_AUTOLOAD=AWAITING_ACTION$'
    printf '%s\n' "$output" | grep -q "^PORTABLETAK_PROVISIONING_STATE=$state$"
done

prepare
set_detect invalid 1
if sh "$TEMP/autoload" > "$TEMP/output" 2>&1; then
    echo "invalid state was accepted" >&2
    exit 1
fi
grep -q '^PORTABLETAK_AUTOLOAD_ERROR=invalid_provisioning_state$' "$TEMP/output"

echo "PASS: autoload starts only completed cartridges"
