"""OpenMANET-specific provisioning adapter."""

import json

from host_adapter import HostAdapter
from portabletak_provision import atomic_write


class OpenManetAdapter(HostAdapter):
    profile = 'openmanet'

    def __init__(self, address='10.41.250.10', prefix=32, interface='br-ahwlan',
                 network_tool='/usr/local/portabletak/adapters/openmanet/network',
                 node_local_address='10.41.254.1'):
        super().__init__()
        # Every OpenMANET node answers its own WiFi clients on this address, so
        # phones can reach it without a gateway or static route (Android needs that).
        self.node_local_address = node_local_address
        self.address = address
        self.prefix = prefix
        self.interface = interface
        self.network_tool = network_tool

    def claim_command(self):
        # The host tool probes the mesh for a collision before adding the address.
        return [self.network_tool, 'claim'] if self.network_tool else None

    def identity_settings(self, context):
        return {'OTS_SERVER_ADDRESS': self.address}

    def server_addresses(self, context):
        if self.node_local_address:
            return self.node_local_address, [self.node_local_address, self.address]
        return self.address, [self.address]

    def configure_network(self, context):
        config = context.root / 'config/tak-network.conf'
        atomic_write(
            config,
            'PORTABLETAK_SERVICE_NAME=' + json.dumps(context.request['instance_name']) + '\n'
            'PORTABLETAK_SERVICE_IP=' + json.dumps(self.address) + '\n'
            'PORTABLETAK_SERVICE_PREFIX=' + json.dumps(str(self.prefix)) + '\n'
            'PORTABLETAK_SERVICE_DEV=' + json.dumps(self.interface) + '\n',
            0o600,
        )
        metadata = {
            'format': 1,
            'host_mode': 'portable',
            'runtime_profile': self.profile,
            'supported_arches': ['arm64', 'x86_64'],
        }
        atomic_write(context.root / 'config/host.json', json.dumps(metadata, sort_keys=True, indent=2) + '\n', 0o600)
        return {'address': self.address, 'prefix': self.prefix,
                'interface': self.interface, 'profile': self.profile}
