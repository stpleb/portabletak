"""Meshtastic link for a PortableTAK cartridge.

OpenTAKServer reads Meshtastic traffic from MQTT: radios (with Wi-Fi) or the Meshtastic
phone app ("MQTT client proxy") publish to this server's RabbitMQ through its MQTT plugin
on port 1883, root topic "opentakserver". This module turns that on or off:

  - RabbitMQ: the rabbitmq_mqtt plugin, an MQTT listener on 1883 (no anonymous access),
    and a radio-only user "meshtastic" (MQTT topics only; never the server's own account);
  - OpenTAKServer: OTS_ENABLE_MESHTASTIC in config.yml.

Settings live in opentak/meshtastic.json ({"enabled": bool, "password": radio password}).
Applied at every start before RabbitMQ and OpenTAKServer start:

    python3 meshtastic_setup.py apply /mnt/asgard
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys

USER = 'meshtastic'
TOPIC = 'opentakserver'
PORT = 1883
CONF = 'opentak/meshtastic.json'
MQTT_LINES = ['mqtt.listeners.tcp.default = %d' % PORT, 'mqtt.allow_anonymous = false',
              'mqtt.vhost = /', 'mqtt.exchange = amq.topic']
PERMISSION = {'user': USER, 'vhost': '/', 'configure': '^mqtt-subscription-.*$',
              'write': '^(amq\\.topic|mqtt-subscription-.*)$', 'read': '^(amq\\.topic|mqtt-subscription-.*)$'}


def _write(path, text, mode=0o644):
    """Atomic write; an existing file keeps its owner and permissions (RabbitMQ reads
    its files as its own user)."""
    path = Path(path)
    try:
        old = path.stat()
    except OSError:
        old = None
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    os.chmod(tmp, old.st_mode & 0o777 if old else mode)
    if old:
        try:
            os.chown(tmp, old.st_uid, old.st_gid)
        except OSError:
            pass
    os.replace(tmp, path)


def load(root):
    try:
        data = json.loads((Path(root) / CONF).read_text())
    except (OSError, ValueError):
        data = {}
    return {'enabled': bool(data.get('enabled')), 'password': str(data.get('password') or '')}


def save(root, enabled):
    """Turn the link on/off; the radio password is made once and kept."""
    current = load(root)
    password = current['password'] or secrets.token_urlsafe(12).replace('-', 'x').replace('_', 'y')
    _write(Path(root) / CONF, json.dumps({'enabled': bool(enabled), 'password': password}, indent=1), 0o600)
    return {'enabled': bool(enabled), 'password': password}


def password_hash(password, salt=None):
    """RabbitMQ rabbit_password_hashing_sha256: base64(salt + sha256(salt + password))."""
    salt = salt if salt is not None else os.urandom(4)
    return base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()


def _rabbit(rootfs, settings):
    etc = Path(rootfs) / 'etc/rabbitmq'
    enabled = settings['enabled']
    plugins = etc / 'enabled_plugins'
    if enabled:
        _write(plugins, '[rabbitmq_mqtt].\n')
    elif plugins.exists():
        plugins.unlink()
    conf = etc / 'rabbitmq.conf'
    lines = [line for line in conf.read_text().splitlines() if not line.startswith('mqtt.')]
    if enabled:
        lines += MQTT_LINES
    _write(conf, '\n'.join(lines) + '\n', 0o640)
    defs_path = etc / 'portabletak-definitions.json'
    defs = json.loads(defs_path.read_text())
    users = [u for u in defs.get('users', []) if u.get('name') != USER]
    perms = [p for p in defs.get('permissions', []) if p.get('user') != USER]
    if enabled:
        users.append({'name': USER, 'password_hash': password_hash(settings['password']),
                      'hashing_algorithm': 'rabbit_password_hashing_sha256', 'tags': []})
        perms.append(dict(PERMISSION))
    defs['users'], defs['permissions'] = users, perms
    _write(defs_path, json.dumps(defs, indent=1), 0o640)


def _ots(root, enabled):
    path = Path(root) / 'opentak/data/config.yml'
    try:
        text = path.read_text()
    except OSError:
        return
    value = 'true' if enabled else 'false'
    pattern = re.compile(r'(["\']?OTS_ENABLE_MESHTASTIC["\']?\s*:\s*)(true|false|True|False)')
    if pattern.search(text):
        new = pattern.sub(lambda m: m.group(1) + value, text)
    elif text.lstrip().startswith('{'):
        new = text.rstrip().rstrip('}').rstrip().rstrip(',') + ',\n  "OTS_ENABLE_MESHTASTIC": %s\n}\n' % value
    else:
        new = text.rstrip('\n') + '\nOTS_ENABLE_MESHTASTIC: %s\n' % value
    if new != text:
        _write(path, new, os.stat(path).st_mode & 0o777)


def apply(root='/mnt/asgard'):
    settings = load(root)
    if settings['enabled'] and not settings['password']:
        settings = save(root, True)
    rootfs = Path(root) / 'rootfs'
    for candidate in (rootfs, Path(root) / 'arch/amd64/rootfs'):
        if (candidate / 'etc/rabbitmq/portabletak-definitions.json').is_file():
            _rabbit(candidate, settings)
    _ots(root, settings['enabled'])
    return settings['enabled']


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'apply':
        on = apply(sys.argv[2] if len(sys.argv) > 2 else '/mnt/asgard')
        print('PORTABLETAK_MESHTASTIC=%s' % ('ON' if on else 'OFF'))
    else:
        print('usage: meshtastic_setup.py apply [ROOT]', file=sys.stderr)
        sys.exit(2)
