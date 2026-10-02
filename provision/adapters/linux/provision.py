"""Generic Linux (and WSL2) provisioning adapter.

The service address is an address the host already owns (for example the
laptop's LAN address or WSL2's forwarded address); nothing is claimed on a
mesh. The claim step only confirms the address is present on the host.
"""

import ipaddress
import json

from host_adapter import HostAdapter
from portabletak_provision import ProvisionError, atomic_write


class LinuxAdapter(HostAdapter):
    profile = 'linux'

    def __init__(self, address, interface, prefix=32,
                 network_tool='/usr/local/portabletak/adapters/linux/network', advertise=None, extra=None):
        super().__init__()
        # Extra addresses the computer is reachable at, e.g. its own Tailscale
        # address on Windows/Mac (remote access uses the computer's Tailscale app).
        try:
            self.extra = [str(ipaddress.IPv4Address(a.strip())) for a in (extra or [])][:4]
        except (ipaddress.AddressValueError, ValueError):
            raise ProvisionError('invalid_extra_address') from None
        # Behind NAT (the Mac VM), phones reach the server at the host computer's
        # address, which QEMU forwards; that address goes first on the certificate.
        try:
            self.advertise = str(ipaddress.IPv4Address(advertise)) if advertise else None
        except (ipaddress.AddressValueError, ValueError):
            raise ProvisionError('invalid_advertise_address') from None
        try:
            self.address = str(ipaddress.IPv4Address(address))
        except (ipaddress.AddressValueError, ValueError):
            raise ProvisionError('invalid_host_service_address') from None
        if not isinstance(interface, str) or not interface or len(interface) > 32:
            raise ProvisionError('invalid_host_service_interface')
        self.interface = interface
        self.prefix = prefix
        self.network_tool = network_tool

    def identity_settings(self, context):
        return {'OTS_SERVER_ADDRESS': self.advertise or self.address}

    def claim_command(self):
        return [self.network_tool, 'claim'] if self.network_tool else None

    def server_addresses(self, context):
        first = self.advertise or self.address
        addresses = list(dict.fromkeys([first, self.address] + self.extra))
        return first, addresses

    def configure_network(self, context):
        atomic_write(
            context.root / 'config/tak-network.conf',
            'PORTABLETAK_SERVICE_NAME=' + json.dumps(context.request['instance_name']) + '\n'
            'PORTABLETAK_SERVICE_IP=' + json.dumps(self.address) + '\n'
            'PORTABLETAK_SERVICE_PREFIX=' + json.dumps(str(self.prefix)) + '\n'
            'PORTABLETAK_SERVICE_DEV=' + json.dumps(self.interface) + '\n',
            0o600,
        )
        metadata = {'format': 1, 'host_mode': 'portable', 'runtime_profile': self.profile,
                    'supported_arches': ['arm64', 'x86_64']}
        atomic_write(context.root / 'config/host.json',
                     json.dumps(metadata, sort_keys=True, indent=2) + '\n', 0o600)
        return {'address': self.address, 'prefix': self.prefix,
                'interface': self.interface, 'profile': self.profile}
