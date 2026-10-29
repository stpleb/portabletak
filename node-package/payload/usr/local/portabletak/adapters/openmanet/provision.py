"""OpenMANET-specific provisioning adapter."""

import json
import re
import subprocess

from host_adapter import HostAdapter
from portabletak_provision import atomic_write


LEGACY_NODE_ADDRESS = '10.41.254.1'


def detect_node_local(run=subprocess.run):
    """The address a node answers its own WiFi phones on, when it has one.

    CicadaTAK-style nodes give each node's phones their own 10.41.254.0/24 and answer
    on 10.41.254.1 (Android has no route to the service address there). OpenMANET 1.8
    puts every node's phones on the mesh-wide 10.41.0.0/16, where the service address
    itself reaches them on any node: no node-local address then."""
    try:
        out = run(['ip', '-4', '-o', 'addr', 'show'], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return LEGACY_NODE_ADDRESS
    return LEGACY_NODE_ADDRESS if ' 10.41.254.' in out else None


SERVICE_POOL = ['10.41.250.%d' % n for n in range(10, 20)]   # one per PortableTAK server on a mesh


def _held(address, interface, run):
    """'local' (this node has it), 'taken' (another host answers on it) or 'free'."""
    out = run(['ip', '-4', '-o', 'addr', 'show', 'dev', interface], capture_output=True, text=True, timeout=5).stdout
    if (' inet %s/' % address) in out:
        return 'local'
    run(['ip', 'neigh', 'del', address, 'dev', interface], capture_output=True, timeout=5)
    run(['ping', '-c', '1', '-W', '1', address], capture_output=True, timeout=5)
    neigh = run(['ip', 'neigh', 'show', address, 'dev', interface], capture_output=True, text=True, timeout=5).stdout
    return 'taken' if re.search(r'REACHABLE|STALE|DELAY|PROBE|PERMANENT', neigh) else 'free'


def choose_service_address(current, interface='br-ahwlan', run=subprocess.run):
    """The service address for this drive on this mesh: its own (current) when free or already
    here, else the first free one of 10.41.250.10 to .19, so a second PortableTAK server on the
    same mesh starts on its own address (federate the two to share). Keeps `current` when all
    are taken (the claim then reports the collision) or the network cannot be checked."""
    candidates = ([current] if current else []) + [a for a in SERVICE_POOL if a != current]
    try:
        for address in candidates:
            if _held(address, interface, run) in ('local', 'free'):
                return address
    except (OSError, subprocess.SubprocessError):
        pass
    return current or SERVICE_POOL[0]


class OpenManetAdapter(HostAdapter):
    profile = 'openmanet'

    def __init__(self, address='10.41.250.10', prefix=32, interface='br-ahwlan',
                 network_tool='/usr/local/portabletak/adapters/openmanet/network',
                 node_local_address='auto'):
        super().__init__()
        if node_local_address == 'auto':
            node_local_address = detect_node_local()
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
