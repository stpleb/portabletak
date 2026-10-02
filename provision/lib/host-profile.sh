# Host profile for PortableTAK host scripts. Source with ". lib/host-profile.sh".
#
# /etc/portabletak/host-profile (root-owned, optional) selects the adapter:
#   PT_PROFILE=openmanet          # default: OpenMANET mesh, service address claimed on br-ahwlan
#   PT_PROFILE=linux              # generic Linux / WSL2: service address is an existing host address
#   PT_BOOT_DEV=br-ahwlan         # interface to wait for at boot
#   PT_SERVICE_IP=, PT_SERVICE_DEV=   # linux profile: address/interface to serve on
# The active cartridge records its service address in config/tak-network.conf;
# that value wins so scripts always match what clients were given.

PT_PROFILE="openmanet"
PT_BOOT_DEV="br-ahwlan"
PT_SERVICE_IP=""
PT_SERVICE_DEV=""
PT_TAILSCALE_PROXY=""   # optional http://host:port CONNECT proxy for tailscaled
[ -r /etc/portabletak/host-profile ] && . /etc/portabletak/host-profile

case "$PT_PROFILE" in
    ''|*[!a-z0-9_-]*) PT_PROFILE="openmanet" ;;
esac
PT_NETWORK="/usr/local/portabletak/adapters/$PT_PROFILE/network"

pt_valid_ip()
{
    case "$1" in
        *[!0-9.]*|'') return 1 ;;
    esac
    OLDIFS="$IFS"; IFS=.; set -- $1; IFS="$OLDIFS"
    [ "$#" -eq 4 ] || return 1
    for OCTET in "$@"; do
        [ -n "$OCTET" ] && [ "$OCTET" -le 255 ] 2>/dev/null || return 1
    done
}

PT_CARTRIDGE_IP="$(sed -n 's/^PORTABLETAK_SERVICE_IP=//p' /mnt/asgard/config/tak-network.conf 2>/dev/null | tr -d '"' | head -n 1)"
if pt_valid_ip "$PT_CARTRIDGE_IP"; then
    PT_SERVICE_IP="$PT_CARTRIDGE_IP"
elif ! pt_valid_ip "$PT_SERVICE_IP"; then
    PT_SERVICE_IP="10.41.250.10"
fi

# /proc/net/tcp shows IPv4 addresses as little-endian hex (10.41.250.10 -> 0AFA290A).
pt_ip_hex()
{
    OLDIFS="$IFS"; IFS=.; set -- $1; IFS="$OLDIFS"
    printf '%02X%02X%02X%02X' "$4" "$3" "$2" "$1"
}
PT_SERVICE_HEX="$(pt_ip_hex "$PT_SERVICE_IP")"

# List "PID command line" for every process. BusyBox ps lists all processes but
# procps ps (Ubuntu, WSL2) only the current terminal's, so read /proc directly.
pt_ps()
{
    for PT_P in /proc/[0-9]*; do
        PT_C="$({ tr '\000' ' ' < "$PT_P/cmdline"; } 2>/dev/null)"
        [ -n "$PT_C" ] && printf '%s %s\n' "${PT_P#/proc/}" "$PT_C"
    done
}
