"""Let one cartridge's TAK server be reached at several addresses.

The server certificate is re-signed from its existing request, so its private
key (also the QR-token signing key) is unchanged and clients, which trust the
CA, keep working. The new certificate lists every address the server may be
reached at: the mesh service address, a node's own LAN address (reachable by
Android without a route), a plain LAN address, a VPN address, or a DNS name.
One of them is "advertised": it is what new client packages and QR codes use.

Nothing here talks to the network. Services read the certificate at start, so
callers restart the stack afterwards.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

from host_profile import rootfs as host_rootfs
from portabletak_provision import ProvisionError, atomic_write, read_json

MAX_ADDRESSES = 8
HOSTNAME = re.compile(r'(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
                      r'(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*')


def normalize(addresses):
    """Return unique addresses as (kind, value) with kind 'IP' or 'DNS'."""
    if not isinstance(addresses, (list, tuple)) or not 1 <= len(addresses) <= MAX_ADDRESSES:
        raise ProvisionError('invalid_server_addresses')
    result = []
    for item in addresses:
        if not isinstance(item, str):
            raise ProvisionError('invalid_server_addresses')
        try:
            entry = ('IP', str(ipaddress.IPv4Address(item)))
        except ValueError:
            if not HOSTNAME.fullmatch(item) or item.replace('.', '').isdigit():
                raise ProvisionError('invalid_server_address:' + item[:40]) from None
            entry = ('DNS', item.lower())
        if entry not in result:
            result.append(entry)
    return result


def alt_names(entries):
    counters = {'IP': 0, 'DNS': 0}
    lines = ['[alt_names]']
    for kind, value in entries:
        counters[kind] += 1
        lines.append(f'{kind}.{counters[kind]} = {value}')
    return '\n'.join(lines) + '\n'


def current(root='/mnt/asgard'):
    root = Path(root)
    record = root / 'config/server-addresses.json'
    config = read_json(root / 'opentak/data/config.yml')
    if record.is_file():
        data = read_json(record)
        return {'advertise': data.get('advertise'), 'addresses': data.get('addresses', [])}
    address = config.get('OTS_SERVER_ADDRESS')
    return {'advertise': address, 'addresses': [address] if address else []}


def reissue(root, addresses, advertise, openssl_prefix=None, ca_dir=None,
            runner=subprocess.run, days=3650, clock=time.time):
    """Re-sign the server certificate for `addresses` and advertise one of them."""
    root = Path(root)
    entries = normalize(addresses)
    advertised = normalize([advertise])[0]
    if advertised not in entries:
        raise ProvisionError('advertised_address_not_listed')
    host_ca = root / 'opentak/data/ca'
    cert_dir = host_ca / 'certs/opentakserver'
    config_path = root / 'opentak/data/config.yml'
    config = read_json(config_path)
    password = config.get('OTS_CA_PASSWORD')
    if not isinstance(password, str) or not password:
        raise ProvisionError('ca_password_unavailable')
    template = cert_dir / 'opentakserver_config.cfg'
    for needed in (host_ca / 'ca.pem', host_ca / 'ca-do-not-share.key', template,
                   cert_dir / 'opentakserver.csr', cert_dir / 'opentakserver.nopass.key'):
        if needed.is_symlink() or not needed.is_file():
            raise ProvisionError('server_certificate_inputs_missing')
    text = template.read_text()
    if '[alt_names]' not in text:
        raise ProvisionError('unexpected_certificate_config')
    new_config = cert_dir / 'opentakserver_config.cfg.new'
    new_cert = cert_dir / 'opentakserver.pem.new'
    atomic_write(new_config, text[:text.index('[alt_names]')] + alt_names(entries), 0o644)

    # Paths as the openssl process sees them (inside the cartridge chroot by default).
    prefix = list(openssl_prefix if openssl_prefix is not None
                  else ('/usr/sbin/chroot', str(host_rootfs(root))))
    seen = Path(ca_dir or '/asgard/opentak/data/ca')
    seen_cert = seen / 'certs/opentakserver'
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'PT_CA_PASSWORD': password}

    def openssl(*arguments):
        result = runner([*prefix, 'openssl', *arguments], capture_output=True, text=True, env=env,
                        stdin=subprocess.DEVNULL)
        if result.returncode:
            raise ProvisionError('server_certificate_openssl_failed')
        return result.stdout

    # ATAK checks the certificate's common name against the address it connects to
    # (QR enrollment's profile step fails on CN=opentakserver), so each re-issue
    # gets a fresh request from the same key with CN = the advertised address.
    subject = config.get('OTS_CA_SUBJECT') or '/O=PortableTAK/OU=TAK'
    if not re.fullmatch(r'(/[A-Za-z]+=[A-Za-z0-9 ._-]{1,64}){1,6}', subject):
        subject = '/O=PortableTAK/OU=TAK'
    new_csr = cert_dir / 'opentakserver.csr.new'
    try:
        openssl('req', '-new', '-sha256', '-key', str(seen_cert / 'opentakserver.nopass.key'),
                '-subj', subject + '/CN=' + advertised[1], '-out', str(seen_cert / 'opentakserver.csr.new'))
        openssl('x509', '-sha256', '-req', '-days', str(days),
                '-in', str(seen_cert / 'opentakserver.csr.new'), '-CA', str(seen / 'ca.pem'),
                '-CAkey', str(seen / 'ca-do-not-share.key'), '-passin', 'env:PT_CA_PASSWORD',
                '-set_serial', str(secrets.randbits(62) + 1), '-extensions', 'server',
                '-extfile', str(seen_cert / 'opentakserver_config.cfg.new'),
                '-out', str(seen_cert / 'opentakserver.pem.new'))
        with new_cert.open('a') as stream:
            stream.write((host_ca / 'ca.pem').read_text())
        openssl('verify', '-CAfile', str(seen / 'ca.pem'), str(seen_cert / 'opentakserver.pem.new'))
        cert_key = openssl('x509', '-noout', '-modulus', '-in', str(seen_cert / 'opentakserver.pem.new'))
        private_key = openssl('rsa', '-noout', '-modulus', '-passin', 'pass:unused',
                              '-in', str(seen_cert / 'opentakserver.nopass.key'))
        if cert_key.strip() != private_key.strip():
            raise ProvisionError('server_certificate_key_mismatch')
        details = openssl('x509', '-noout', '-text', '-in', str(seen_cert / 'opentakserver.pem.new'))
        for kind, value in entries:
            label = 'IP Address:' if kind == 'IP' else 'DNS:'
            if label + value not in details:
                raise ProvisionError('server_certificate_address_missing')
    except Exception:
        for leftover in (new_config, new_cert, new_csr):
            if leftover.exists():
                leftover.unlink()
        raise

    stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime(clock()))
    current_cert = cert_dir / 'opentakserver.pem'
    if current_cert.is_file():
        atomic_write(cert_dir / f'opentakserver.pem.previous-{stamp}', current_cert.read_text(), 0o644)
    os.replace(new_cert, current_cert)
    os.replace(new_config, template)
    os.replace(new_csr, cert_dir / 'opentakserver.csr')
    config['OTS_SERVER_ADDRESS'] = advertised[1]
    atomic_write(config_path, json.dumps(config, sort_keys=True, indent=2) + '\n', 0o600)
    record = {'format': 1, 'advertise': advertised[1], 'addresses': [value for _, value in entries],
              'updated_at': int(clock())}
    atomic_write(root / 'config/server-addresses.json', json.dumps(record, sort_keys=True, indent=2) + '\n', 0o600)
    return record


REMOTE_CERT = 'opentakserver-remote.pem'
REMOTE_MARK = '# portabletak-remote'
REMOTE_STAMP = 'opentakserver-remote.json'
TAILSCALE = ipaddress.IPv4Network('100.64.0.0/10')


def remote_address(record):
    """The first Tailscale address on the certificate unless it is the advertised one."""
    for value in record.get('addresses') or []:
        try:
            if ipaddress.IPv4Address(value) in TAILSCALE and value != record.get('advertise'):
                return value
        except ValueError:
            continue
    return None


def ensure_remote(root, openssl_prefix=None, ca_dir=None, runner=subprocess.run):
    """Keep a second server certificate whose common name is the Tailscale address.

    ATAK checks the certificate's common name against the address it connected to,
    so phones set up with the Remote QR (Tailscale address) need a certificate named
    for it. Same key, same addresses, signed by the same CA; nginx presents it on
    connections that arrive at the Tailscale address. Returns that address or None."""
    import hashlib
    root = Path(root)
    host_ca = root / 'opentak/data/ca'
    cert_dir = host_ca / 'certs/opentakserver'
    remote_cert, stamp = cert_dir / REMOTE_CERT, cert_dir / REMOTE_STAMP
    address = remote_address(current(root))
    main = cert_dir / 'opentakserver.pem'
    if address is None or not main.is_file():
        for stale in (remote_cert, stamp):
            if stale.is_file():
                stale.unlink()
        return None
    wanted = {'address': address, 'main': hashlib.sha256(main.read_bytes()).hexdigest()}
    try:
        if remote_cert.is_file() and json.loads(stamp.read_text()) == wanted:
            return address
    except (OSError, ValueError):
        pass
    config = read_json(root / 'opentak/data/config.yml')
    password = config.get('OTS_CA_PASSWORD')
    if not isinstance(password, str) or not password:
        raise ProvisionError('ca_password_unavailable')
    prefix = list(openssl_prefix if openssl_prefix is not None
                  else ('/usr/sbin/chroot', str(host_rootfs(root))))
    seen = Path(ca_dir or '/asgard/opentak/data/ca')
    seen_cert = seen / 'certs/opentakserver'
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'PT_CA_PASSWORD': password}

    def openssl(*arguments):
        result = runner([*prefix, 'openssl', *arguments], capture_output=True, text=True, env=env,
                        stdin=subprocess.DEVNULL)
        if result.returncode:
            raise ProvisionError('remote_certificate_openssl_failed')
        return result.stdout

    subject = config.get('OTS_CA_SUBJECT') or '/O=PortableTAK/OU=TAK'
    if not re.fullmatch(r'(/[A-Za-z]+=[A-Za-z0-9 ._-]{1,64}){1,6}', subject):
        subject = '/O=PortableTAK/OU=TAK'
    new_cert, new_csr = cert_dir / (REMOTE_CERT + '.new'), cert_dir / 'opentakserver-remote.csr.new'
    try:
        openssl('req', '-new', '-sha256', '-key', str(seen_cert / 'opentakserver.nopass.key'),
                '-subj', subject + '/CN=' + address, '-out', str(seen_cert / new_csr.name))
        openssl('x509', '-sha256', '-req', '-days', '3650', '-in', str(seen_cert / new_csr.name),
                '-CA', str(seen / 'ca.pem'), '-CAkey', str(seen / 'ca-do-not-share.key'),
                '-passin', 'env:PT_CA_PASSWORD', '-set_serial', str(secrets.randbits(62) + 1),
                '-extensions', 'server', '-extfile', str(seen_cert / 'opentakserver_config.cfg'),
                '-out', str(seen_cert / new_cert.name))
        with new_cert.open('a') as stream:
            stream.write((host_ca / 'ca.pem').read_text())
        openssl('verify', '-CAfile', str(seen / 'ca.pem'), str(seen_cert / new_cert.name))
        details = openssl('x509', '-noout', '-text', '-in', str(seen_cert / new_cert.name))
        if 'IP Address:' + address not in details:
            raise ProvisionError('remote_certificate_address_missing')
    except Exception:
        for leftover in (new_cert, new_csr):
            if leftover.exists():
                leftover.unlink()
        raise
    os.chmod(new_cert, 0o644)
    os.replace(new_cert, remote_cert)
    new_csr.unlink()
    atomic_write(stamp, json.dumps(wanted) + '\n', 0o644)
    return address


def listen_everywhere(root):
    """Make the cartridge's nginx site listen on every address for 8443/8446."""
    site = Path(root) / 'rootfs/etc/nginx/sites-available/portabletak'
    if site.is_symlink() or not site.is_file():
        raise ProvisionError('nginx_site_missing')
    text = site.read_text()
    # Lines marked portabletak-remote listen on one address on purpose (dashboard_site).
    updated = '\n'.join(line if line.endswith(REMOTE_MARK) else
                        re.sub(r'listen (?:\d{1,3}(?:\.\d{1,3}){3}:)?(8443|8446) ssl;', r'listen \1 ssl;', line)
                        for line in text.split('\n'))
    if 'listen 8443 ssl;' not in updated:
        raise ProvisionError('unexpected_nginx_site')
    if updated != text:
        atomic_write(site, updated, 0o644)
    import arch_sync
    arch_sync.sync(root)
    return updated != text
