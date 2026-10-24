"""OpenTAKServer's internal port on a PortableTAK cartridge.

OpenTAKServer listens on 127.0.0.1 only (nginx and MediaMTX reach it there). Its
upstream default, 8081, is taken by openmanetd on OpenMANET 1.8 nodes (8080 and 8081
on all addresses), so PortableTAK uses PORT instead. Cartridges made earlier are
moved at start, before any service launches.

    python3 ots_port.py migrate /mnt/asgard   -> PORTABLETAK_OTS_PORT=18081 (changed=N)
"""
from pathlib import Path
import re
import sys

PORT = 18081
OLD = 8081


def _files(root):
    root = Path(root)
    yield root / 'opentak/data/config.yml'
    yield root / 'opentak/data/mediamtx/mediamtx.yml'
    yield root / 'mediamtx/mediamtx.yml'
    yield root / 'mediamtx/ots-webhook.py'
    for rootfs in (root / 'rootfs', root / 'arch/amd64/rootfs'):
        yield rootfs / 'etc/nginx/sites-available/portabletak'


def rewrite(text):
    """Point every OpenTAKServer reference in a config at PORT."""
    # YAML (OTS_LISTENER_PORT: 8081) or JSON-style ("OTS_LISTENER_PORT": 8081,), value quoted or not
    text = re.sub(r"""(["']?OTS_LISTENER_PORT["']?\s*:\s*["']?)%d\b""" % OLD, r'\g<1>%d' % PORT, text)
    return re.sub(r'(127\.0\.0\.1|localhost):%d\b' % OLD, r'\g<1>:%d' % PORT, text)


def migrate(root='/mnt/asgard'):
    """Rewrite the cartridge's files in place (atomically). Returns how many changed."""
    changed = 0
    for path in _files(root):
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        new = rewrite(text)
        if new != text:
            tmp = path.with_name(path.name + '.pt-port')
            tmp.write_text(new)
            tmp.chmod(path.stat().st_mode & 0o7777)
            tmp.replace(path)
            changed += 1
    return changed


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'migrate':
        count = migrate(sys.argv[2] if len(sys.argv) > 2 else '/mnt/asgard')
        print('PORTABLETAK_OTS_PORT=%d' % PORT)
        print('PORTABLETAK_OTS_PORT_CHANGED=%d' % count)
    else:
        print('usage: ots_port.py migrate [ROOT]', file=sys.stderr)
        sys.exit(2)
