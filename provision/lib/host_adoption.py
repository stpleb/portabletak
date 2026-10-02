"""Adopt a provisioned cartridge on the host it is plugged into.

A cartridge can be set up on one host (a Mac or Windows computer) and then run
on another (an OpenMANET node, a Linux laptop). What ties it to a host is small
and recorded on the cartridge: the service address/interface
(config/tak-network.conf), the host profile it was bound for
(config/host.json) and the addresses on the server certificate
(config/server-addresses.json). When the current host's adapter differs, this
rewrites those for the current host before services start. It needs no
internet and keeps VPN (Tailscale) addresses already on the certificate.

The CA, client certificates and data are unchanged, so enrolled clients keep
working; only the server certificate is re-signed (same key).
"""
import ipaddress
import json
from pathlib import Path
from types import SimpleNamespace

from portabletak_provision import ProvisionError, read_json
import server_addresses

TAILSCALE = ipaddress.IPv4Network('100.64.0.0/10')


def _read_conf(path):
    values = {}
    try:
        lines = Path(path).read_text().splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        key, sep, value = line.partition('=')
        if sep:
            try:
                values[key.strip()] = json.loads(value)
            except ValueError:
                values[key.strip()] = value.strip().strip('"')
    return values


def _is_vpn(address):
    if address.endswith('.ts.net'):
        return True
    try:
        return ipaddress.IPv4Address(address) in TAILSCALE
    except ValueError:
        return False


def own_vpn(root, address):
    """True if a VPN address belongs to the cartridge's own Tailscale (it moves with
    the drive), not to a Windows/Mac computer's Tailscale app (it stays behind)."""
    state = Path(root) / 'config/tailscale'
    listed = state / 'addresses'
    if listed.is_file():
        return address in listed.read_text().split()
    # Drives that joined Tailscale before the address list existed: keep as before.
    return (state / 'tailscaled.state').is_file()


def own_addresses(root):
    """The drive's own Tailscale addresses (written when remote access was set up)."""
    listed = Path(root) / 'config/tailscale/addresses'
    try:
        return [a for a in listed.read_text().split() if _is_vpn(a)]
    except OSError:
        return []


def binding(root):
    root = Path(root)
    host = {}
    if (root / 'config/host.json').is_file():
        host = read_json(root / 'config/host.json')
    network = _read_conf(root / 'config/tak-network.conf')
    return {'profile': host.get('runtime_profile'),
            'address': network.get('PORTABLETAK_SERVICE_IP'),
            'interface': network.get('PORTABLETAK_SERVICE_DEV'),
            'name': network.get('PORTABLETAK_SERVICE_NAME')}


def instance_name(root):
    name = binding(root).get('name')
    if name:
        return name
    conf = _read_conf(Path(root) / 'portabletak/cartridge.conf')
    return conf.get('PORTABLETAK_INSTANCE_NAME') or 'PortableTAK'


def needs_adoption(root, adapter):
    current = binding(root)
    if (current['profile'] != adapter.profile or current['address'] != adapter.address
            or current['interface'] != adapter.interface):
        return True
    # A NAT'd host (the Mac VM) advertises the computer's address; re-sign if it changed.
    advertise = getattr(adapter, 'advertise', None)
    record = server_addresses.current(root)
    if advertise and record.get('advertise') != advertise:
        return True
    listed = set(record.get('addresses') or [])
    # A VPN address that is not the drive's own (e.g. a computer's Tailscale app,
    # used by older versions) must go; the drive's own addresses must be present.
    if any(_is_vpn(a) and not own_vpn(root, a) for a in listed):
        return True
    if not set(own_addresses(root)) <= listed:
        return True
    return not set(getattr(adapter, 'extra', []) or []) <= listed


def adopt(root, adapter, reissue=server_addresses.reissue, listen=server_addresses.listen_everywhere):
    """Bind the cartridge to `adapter`'s host. Returns a summary dict."""
    root = Path(root)
    context = SimpleNamespace(root=root, request={'instance_name': instance_name(root)})
    advertise, addresses = adapter.validated_server_addresses(context)
    kept = [a for a in server_addresses.current(root)['addresses'] if a and _is_vpn(a) and own_vpn(root, a)]
    kept += [a for a in own_addresses(root) if a not in kept]
    wanted = list(dict.fromkeys(addresses + kept))[:server_addresses.MAX_ADDRESSES]
    previous = binding(root)
    # Certificate first: if it fails nothing else has changed.
    record = reissue(root, wanted, advertise)
    listen(root)
    result = adapter.validated_binding(context)
    if result.get('address') != adapter.address:
        raise ProvisionError('adoption_binding_mismatch')
    return {'from_profile': previous['profile'], 'to_profile': adapter.profile,
            'advertise': record['advertise'], 'addresses': record['addresses']}
