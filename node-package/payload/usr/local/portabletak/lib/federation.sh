# PortableTAK federation add-on (sourced by start, users worker and federation).
# share/ots-federation/<arch> holds OpenTAKServer 1.7.13 with federation (PR #343
# + PortableTAK fixes) and grpcio. It is copied onto the cartridge so the chroot's
# Python can see it, and every OpenTAKServer process runs with it first on
# PYTHONPATH; the sealed base venv is never changed.

PT_FED_SRC=/usr/local/portabletak/share/ots-federation
PT_FED_IN_ROOT=/asgard/opentak/federation-overlay

# pt_federation_overlay: bring the cartridge's copy up to date (for this CPU) and
# print the PYTHONPATH to use inside the chroot (empty when not available).
# /mnt/asgard is the cartridge; the chroot sees it as /asgard.
pt_federation_overlay()
{
    FED_ARCH="$(uname -m)"
    FED_DST="/mnt$PT_FED_IN_ROOT"
    [ -d "$PT_FED_SRC/$FED_ARCH" ] || return 0
    [ "$(sed -n 's/^PORTABLETAK_INSTANCE_TYPE=//p' /mnt/asgard/portabletak/cartridge.conf 2>/dev/null | tr -d '"' | head -n 1)" = generic ] || return 0
    FED_STAMP="$(cat "$PT_FED_SRC/VERSION") $FED_ARCH"
    if [ "$(cat "$FED_DST/STAMP" 2>/dev/null)" != "$FED_STAMP" ]; then
        rm -rf "$FED_DST.new"
        mkdir -p "$FED_DST.new" &&
            cp -R "$PT_FED_SRC/$FED_ARCH/." "$FED_DST.new/" &&
            echo "$FED_STAMP" > "$FED_DST.new/STAMP" &&
            rm -rf "$FED_DST" && mv "$FED_DST.new" "$FED_DST" || { rm -rf "$FED_DST.new"; return 0; }
    fi
    echo "$PT_FED_IN_ROOT"
}

# Federation runs only when the owner turned it on in the dashboard.
pt_federation_enabled()
{
    grep -qx 'PORTABLETAK_FEDERATION=enabled' /mnt/asgard/portabletak/federation.conf 2>/dev/null
}
