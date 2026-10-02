"""Publish identity and stage a native test request before runtime mounts exist."""

import json
from pathlib import Path
import secrets
import sys

ROOT = Path(sys.argv[1]).resolve()
SOURCE = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(SOURCE / 'lib'))
from portabletak_provision import Controller, metadata
from publish_identity import publish_identity
from release import expected_base_seal

seal = expected_base_seal()
values = metadata(ROOT / 'portabletak/cartridge.conf')
if values['PORTABLETAK_CARTRIDGE_ID'] == '00000000-0000-0000-0000-000000000000':
    publish_identity(ROOT, seal, lock='/tmp/portabletak-native-identity.lock', require_mount=False)
request = {'instance_name': 'PortableTAK Native Test', 'timezone': 'Etc/UTC',
           'administrator_password': 'Admin-' + secrets.token_hex(24),
           'client_password': 'Client-' + secrets.token_hex(24)}
controller = Controller(ROOT, lock='/tmp/portabletak-native-lifecycle.lock',
                        require_mount=False, expected_base_seal=seal)
controller.stage(request)
json.dump(request, sys.stdout, separators=(',', ':'))
