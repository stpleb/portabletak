"""Serve the management dashboard over HTTPS on the TAK network (port 8444).

The dashboard API listens only on 127.0.0.1:8090. On a node with no screen,
operators open it from a phone or laptop instead (the "Start here" QR code
points here), so the cartridge's nginx proxies https://<address>:8444 to it
with the cartridge's own server certificate. The API enforces sign-in, CSRF
and login throttling; nginx passes the real client address so throttling and
the "first-run only from this machine" rule still see who is asking.

    python3 dashboard_site.py /mnt/asgard
"""
import os
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arch_sync
from portabletak_provision import atomic_write

PORT = 8444
SITE = 'etc/nginx/sites-available/portabletak-dashboard'
LINK = 'etc/nginx/sites-enabled/portabletak-dashboard'
CERTS = '/asgard/opentak/data/ca/certs/opentakserver'


def render(port=PORT, certs=CERTS):
    return ('server { listen %d ssl; '
            'ssl_certificate %s/opentakserver.pem; '
            'ssl_certificate_key %s/opentakserver.nopass.key; '
            'client_max_body_size 64m; '
            'location / { proxy_pass http://127.0.0.1:8090; '
            'proxy_set_header Host $http_host; '
            'proxy_set_header X-Forwarded-Proto https; '
            'proxy_set_header X-Forwarded-For $remote_addr; '
            'proxy_read_timeout 900s; } }\n' % (port, certs, certs))


TAK_SITE = 'etc/nginx/sites-available/portabletak'
CA = '/asgard/opentak/data/ca/ca.pem'
# What OpenTAKServer's own nginx setup does on 8443/8446: ask for (not require) a client
# certificate signed by the cartridge CA and pass it on (X-Ssl-Cert), so /Marti endpoints
# (missions, data sync, the enrollment profile) know which phone is asking; and pass
# TAK headers that contain underscores (API_VERSION).
TAK_TLS = ('ssl_client_certificate %s; ssl_verify_client optional; ssl_verify_depth 2; '
           'underscores_in_headers on; ' % CA)
CERT_HEADER = 'proxy_set_header X-Ssl-Cert $ssl_client_escaped_cert; '
HLS = 'location /hls/ { proxy_pass http://127.0.0.1:8888/; proxy_buffering off; } '


def upgrade_tak_site(text):
    """Add client-certificate pass-through to an existing 8443/8446 site (idempotent)."""
    if 'ssl_verify_client' not in text:
        text = re.sub(r'(server \{ listen (?:\S+:)?(?:8443|8446) ssl; )', r'\1' + TAK_TLS, text)
    elif 'underscores_in_headers' not in text:
        text = re.sub(r'(server \{ listen (?:\S+:)?(?:8443|8446) ssl; )', r'\1underscores_in_headers on; ', text)
    if 'X-Ssl-Cert' not in text:
        text = text.replace('proxy_set_header X-Forwarded-Proto https; }',
                            'proxy_set_header X-Forwarded-Proto https; ' + CERT_HEADER + '}')
    # Video feeds that OpenTAKServer lists for TAK clients point at /hls/<feed>/ on 8443.
    if HLS not in text:
        text = re.sub(r'(server \{ listen (?:\S+:)?8443 ssl; .*?)(location / \{)', r'\1' + HLS.replace('\\', '\\\\') + r'\2', text)
    return text


# The drive's Tailscale runs in userspace mode: it hands tailnet connections to the
# server on 127.0.0.1, so those (and connections to the Tailscale address itself)
# get the certificate named for the Tailscale address. Nothing else on the host
# talks to 8443/8446 over loopback.
LOOPBACK = '127.0.0.1'


def named_blocks(text, mapping):
    """Copies of the 8443/8446 servers that present, to connections arriving at an
    address, the certificate named for that address (ATAK checks the name). mapping:
    {listen address: certificate file}. Listening on one address next to the wildcard
    listen needs no extra socket and works even while that address is absent."""
    from server_addresses import REMOTE_MARK
    lines = [line for line in text.split('\n') if not line.endswith(REMOTE_MARK)]
    if mapping:
        extra = []
        for line in lines:
            for port in ('8443', '8446'):
                if line.startswith('server { listen %s ssl; ' % port) and '/opentakserver.pem;' in line:
                    for where, cert in mapping.items():
                        extra.append(line.replace('listen %s ssl;' % port, 'listen %s:%s ssl;' % (where, port), 1)
                                     .replace('/opentakserver.pem;', '/' + cert + ';') + ' ' + REMOTE_MARK)
        while lines and lines[-1] == '':
            lines.pop()
        lines += extra + ['']
    return '\n'.join(lines)


def remote_blocks(text, address):
    """The Tailscale-named certificate on the Tailscale address and on loopback (the
    drive's Tailscale hands tailnet connections to 127.0.0.1). None removes them."""
    from server_addresses import REMOTE_CERT
    return named_blocks(text, {LOOPBACK: REMOTE_CERT, address: REMOTE_CERT} if address else {})


def named_certs(root):
    """Make sure every address-named certificate is current: {listen address: file}."""
    try:
        import server_addresses
        server_addresses.renew_if_needed(root)
        mapping = server_addresses.ensure_named(root)
    except Exception:
        return {}
    from server_addresses import REMOTE_CERT
    if REMOTE_CERT in mapping.values():
        mapping = {LOOPBACK: REMOTE_CERT, **mapping}
    return mapping


def ensure_tak_site(root='/mnt/asgard', mapping=None):
    site = Path(root) / 'rootfs' / TAK_SITE
    if site.is_symlink() or not site.is_file():
        return False
    text = site.read_text()
    updated = named_blocks(upgrade_tak_site(text), mapping or {})
    if updated != text:
        atomic_write(site, updated, 0o644)
        return True
    return False


CLIENT_INFO = 'opentak/data/portabletak-clients.json'


def write_client_info(root):
    """What OpenTAKServer's add-ons need from PortableTAK's own config (which the
    server's chroot cannot see): the server's name and its Tailscale address."""
    import json
    import host_adoption
    import server_addresses
    record = server_addresses.current(root)
    info = {'name': host_adoption.instance_name(root), 'advertise': record.get('advertise'),
            'remote': server_addresses.remote_address(record)}
    path = Path(root) / CLIENT_INFO
    text = json.dumps(info, sort_keys=True) + '\n'
    if not path.is_file() or path.read_text() != text:
        atomic_write(path, text, 0o644)
        return True
    return False


def ensure(root='/mnt/asgard'):
    """Write the dashboard site into the cartridge (idempotent). True if changed."""
    root = Path(root)
    rootfs = root / 'rootfs'
    site, link = rootfs / SITE, rootfs / LINK
    if not (rootfs / 'etc/nginx/sites-available').is_dir():
        raise FileNotFoundError('nginx_sites_missing')
    changed = False
    text = render()
    if site.is_symlink() or not site.is_file() or site.read_text() != text:
        atomic_write(site, text, 0o644)
        changed = True
    target = '/etc/nginx/sites-available/portabletak-dashboard'
    if not link.is_symlink() or os.readlink(link) != target:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)
        changed = True
    changed = ensure_tak_site(root, named_certs(root)) or changed
    try:
        changed = write_client_info(root) or changed
    except Exception:
        pass
    arch_sync.sync(root)
    return changed


if __name__ == '__main__':
    try:
        print('PORTABLETAK_DASHBOARD_SITE=' + ('UPDATED' if ensure(sys.argv[1] if len(sys.argv) > 1 else '/mnt/asgard') else 'CURRENT'))
    except OSError as error:
        print('PORTABLETAK_DASHBOARD_SITE=FAILED')
        print('PORTABLETAK_DASHBOARD_SITE_ERROR=' + str(error))
        sys.exit(1)
