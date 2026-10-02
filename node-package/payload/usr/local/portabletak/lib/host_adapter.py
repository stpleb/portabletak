"""Host integration boundary for PortableTAK provisioning.

Core provisioning owns cartridge identity, secrets, data, and durable state.
Adapters own the host runtime profile and network/service attachment.
"""

import ipaddress
import re

from portabletak_provision import ProvisionError


class HostAdapter:
    profile = None

    def __init__(self):
        if not isinstance(self.profile, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}', self.profile):
            raise ValueError('invalid_host_adapter_profile')

    def identity_settings(self, context):
        """Return adapter-owned settings needed before certificates are created."""
        raise NotImplementedError

    def configure_network(self, context):
        """Attach the instance to the host and return its service binding."""
        raise NotImplementedError

    def claim_command(self):
        """Command that attaches the service address to this host, or None.

        It must be idempotent and refuse when another node holds the address.
        Provisioning runs it before validating listeners bound to that address.
        """
        return None

    def server_addresses(self, context):
        """Return (advertised, [all addresses]) the server certificate must cover.

        The advertised address is what client packages and QR codes use; it
        should be one every client can reach without special routing.
        """
        raise NotImplementedError

    def validated_server_addresses(self, context):
        from server_addresses import normalize
        advertised, addresses = self.server_addresses(context)
        entries = normalize(list(addresses))
        if normalize([advertised])[0] not in entries:
            raise ProvisionError('advertised_address_not_listed')
        return normalize([advertised])[0][1], [value for _, value in entries]

    def validated_binding(self, context):
        binding = self.configure_network(context)
        if not isinstance(binding, dict) or set(binding) != {'address', 'prefix', 'interface', 'profile'}:
            raise ProvisionError('invalid_host_adapter_binding')
        try:
            address = str(ipaddress.ip_address(binding['address']))
            prefix = int(binding['prefix'])
        except (ValueError, TypeError):
            raise ProvisionError('invalid_host_adapter_binding') from None
        maximum = 32 if ':' not in address else 128
        if not 0 <= prefix <= maximum or binding['profile'] != self.profile:
            raise ProvisionError('invalid_host_adapter_binding')
        if not isinstance(binding['interface'], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,32}', binding['interface']):
            raise ProvisionError('invalid_host_adapter_binding')
        return dict(binding, address=address, prefix=prefix)
