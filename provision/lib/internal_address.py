"""The address PortableTAK's machine-only services listen on: HOST (127.0.0.2).

The drive's Tailscale runs in userspace mode, which hands every tailnet connection
to the same port on 127.0.0.1 (TCP and UDP, IPv4 and IPv6 tailnet addresses alike;
Tailscale's netstack has no setting for this). Anything listening on 127.0.0.1 or
on every address is therefore reachable from the whole tailnet. The services that
only this machine should use listen on HOST instead, which Tailscale never dials:
OpenTAKServer's internal HTTP (18081), the users worker (18090), MediaMTX's API
(9997) and feed inputs (UDP 5600-5615), Murmur's Ice (6502), PostgreSQL (5432) and
RabbitMQ's AMQP (5672). Clients connecting to HOST still arrive from 127.0.0.1, so
loopback checks (nginx's X-Ssl-Cert, OpenTAKServer's IP allowlist) are unchanged.

Still on 127.0.0.1: the dashboard API (8090, which browsers on this computer open
and which requires the dashboard login), and RabbitMQ's port mapper and cluster
port (4369, 25672; they need the drive's Erlang cookie). Cartridges made earlier
are moved at start, before any service launches.

    python3 internal_address.py migrate /mnt/asgard   -> PORTABLETAK_INTERNAL_ADDRESS=127.0.0.2
"""
from pathlib import Path
import json
import os
import re
import sys

HOST = '127.0.0.2'
OLD = '127.0.0.1'
# TCP ports of the services above, as written in URLs and host:port settings.
PORTS = (18081, 18090, 9997, 6502, 5432, 5672)
FEED_UDP = range(5600, 5616)
MEDIAMTX_API = 'http://%s:9997' % HOST
POSTGRES_LINE = "listen_addresses = '%s'   # PortableTAK: not reachable over Tailscale" % HOST
# OpenTAKServer settings holding a bare address (its MediaMTX API default is localhost:9997).
OTS_KEYS = ('OTS_LISTENER_ADDRESS', 'OTS_RABBITMQ_SERVER_ADDRESS')


def _files(root):
    root = Path(root)
    yield root / 'opentak/data/config.yml'
    yield root / 'opentak/data/mediamtx/mediamtx.yml'
    yield root / 'mediamtx/mediamtx.yml'
    yield root / 'mediamtx/ots-webhook.py'
    yield root / 'opentak/voice/murmur.ini'
    for rootfs in (root / 'rootfs', root / 'arch/amd64/rootfs'):
        yield rootfs / 'etc/nginx/sites-available/portabletak'
        yield rootfs / 'etc/rabbitmq/rabbitmq.conf'


def rewrite(text):
    """Point every reference to an internal service in a config at HOST."""
    ports = '|'.join(str(p) for p in PORTS)
    text = re.sub(r'(?:127\.0\.0\.1|localhost):(%s)\b' % ports, HOST + r':\1', text)
    # PostgreSQL URI without a port (postgresql+psycopg://ots:PW@127.0.0.1/ots)
    text = re.sub(r'(postgresql(?:\+\w+)?://[^@\s"\']*@)(?:127\.0\.0\.1|localhost)(?=[/"\'\s]|$)',
                  r'\g<1>' + HOST, text)
    # Ice endpoint (murmur.ini: ice="tcp -h 127.0.0.1 -p 6502")
    text = re.sub(r'tcp -h (?:127\.0\.0\.1|localhost) -p 6502\b', 'tcp -h %s -p 6502' % HOST, text)
    text = re.sub(r'udp://127\.0\.0\.1:(56(?:0\d|1[0-5]))\b', 'udp://%s:\\1' % HOST, text)
    for key in OTS_KEYS:
        text = re.sub(r"""(["']?%s["']?\s*:\s*["']?)(?:127\.0\.0\.1|localhost)\b""" % key,
                      r'\g<1>' + HOST, text)
    return text


def ots_config(text):
    """config.yml with the internal addresses, and MediaMTX's API (OpenTAKServer's
    default is localhost:9997) set explicitly."""
    text = rewrite(text)
    if 'OTS_MEDIAMTX_API_ADDRESS' in text:
        return text
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):                      # PortableTAK writes it as JSON
        data['OTS_MEDIAMTX_API_ADDRESS'] = MEDIAMTX_API
        return json.dumps(data, sort_keys=True, indent=2) + '\n'
    return text.rstrip('\n') + '\nOTS_MEDIAMTX_API_ADDRESS: %s\n' % MEDIAMTX_API


def postgres_config(text):
    """postgresql.conf listening on HOST only (the last listen_addresses wins)."""
    active = [line for line in text.splitlines() if re.match(r'\s*listen_addresses\s*=', line)]
    if active and active[-1].strip() == POSTGRES_LINE.strip():
        return text
    return text.rstrip('\n') + '\n' + POSTGRES_LINE + '\n'


def _write(path, new):
    tmp = path.with_name(path.name + '.pt-internal')
    tmp.write_text(new)
    stat = path.stat()
    tmp.chmod(stat.st_mode & 0o7777)
    try:
        os.chown(tmp, stat.st_uid, stat.st_gid)
    except OSError:
        pass
    tmp.replace(path)


def migrate(root='/mnt/asgard'):
    """Rewrite the cartridge's files in place (atomically). Returns how many changed."""
    root = Path(root)
    changed = 0
    edits = [(path, rewrite) for path in _files(root)]
    edits[0] = (edits[0][0], ots_config)
    edits.append((root / 'postgres/postgresql.conf', postgres_config))
    for path, change in edits:
        try:
            if path.is_symlink():
                continue
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        new = change(text)
        if new != text:
            _write(path, new)
            changed += 1
    return changed


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'migrate':
        count = migrate(sys.argv[2] if len(sys.argv) > 2 else '/mnt/asgard')
        print('PORTABLETAK_INTERNAL_ADDRESS=%s' % HOST)
        print('PORTABLETAK_INTERNAL_ADDRESS_CHANGED=%d' % count)
    else:
        print('usage: internal_address.py migrate [ROOT]', file=sys.stderr)
        sys.exit(2)
