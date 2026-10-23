"""The drive's federation certificate: one stable identity for links to other TAK servers.

The server certificate is reissued whenever the drive's addresses change (moving
the drive to another network), which would change its fingerprint and break every
federation link, since federation pins the peer's certificate. This certificate is
named after the drive, signed by the drive's CA, valid 10 years, and never reissued
while it is valid, so a moved drive keeps its federation links.

    python3 federation_cert.py ensure /mnt/asgard   -> prints the fingerprint
"""
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portabletak_provision import ProvisionError, read_json
from host_profile import rootfs as host_rootfs

DAYS = 3650
FOLDER = 'opentak/data/ca/certs/federation'


def cartridge_id(root):
    try:
        text = (Path(root) / 'portabletak/cartridge.conf').read_text()
    except OSError:
        return 'drive'
    match = re.search(r'^PORTABLETAK_CARTRIDGE_ID="?([A-Za-z0-9-]+)', text, re.M)
    return match.group(1)[:8] if match else 'drive'


def common_name(root):
    return 'PortableTAK-%s-federation' % cartridge_id(root)


def paths(root):
    folder = Path(root) / FOLDER
    return folder / 'federation.pem', folder / 'federation.nopass.key'


def ensure(root, openssl_prefix=None, ca_dir=None, runner=subprocess.run):
    """Create the federation certificate if it is missing; return its SHA-256 fingerprint."""
    root = Path(root)
    cert, key = paths(root)
    prefix = list(openssl_prefix if openssl_prefix is not None
                  else ('/usr/sbin/chroot', str(host_rootfs(root))))
    seen = Path(ca_dir or '/asgard/opentak/data/ca')
    seen_folder = seen / 'certs/federation'
    config = read_json(root / 'opentak/data/config.yml')
    password = config.get('OTS_CA_PASSWORD')
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'PT_CA_PASSWORD': str(password or '')}

    def openssl(*arguments):
        result = runner([*prefix, 'openssl', *arguments], capture_output=True, text=True, env=env,
                        stdin=subprocess.DEVNULL)
        if result.returncode:
            raise ProvisionError('federation_certificate_openssl_failed')
        return result.stdout

    if not (cert.is_file() and key.is_file()):
        if not isinstance(password, str) or not password:
            raise ProvisionError('ca_password_unavailable')
        cert.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(cert.parent, 0o755)
        name = common_name(root)
        ext = cert.parent / 'federation.ext'
        ext.write_text('[federation]\nbasicConstraints=critical,CA:FALSE\n'
                       'keyUsage=critical,digitalSignature,keyEncipherment\n'
                       'extendedKeyUsage=critical,clientAuth,serverAuth\n'
                       'subjectAltName=DNS:%s\n' % name)
        try:
            openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-sha256',
                    '-keyout', str(seen_folder / 'federation.nopass.key.new'),
                    '-subj', '/O=PortableTAK/OU=TAK/CN=' + name, '-out', str(seen_folder / 'federation.csr'))
            openssl('x509', '-sha256', '-req', '-days', str(DAYS), '-in', str(seen_folder / 'federation.csr'),
                    '-CA', str(seen / 'ca.pem'), '-CAkey', str(seen / 'ca-do-not-share.key'),
                    '-passin', 'env:PT_CA_PASSWORD', '-set_serial', str(secrets.randbits(62) + 1),
                    '-extensions', 'federation', '-extfile', str(seen_folder / 'federation.ext'),
                    '-out', str(seen_folder / 'federation.pem.new'))
            openssl('verify', '-CAfile', str(seen / 'ca.pem'), str(seen_folder / 'federation.pem.new'))
        except Exception:
            for leftover in cert.parent.glob('*.new'):
                leftover.unlink()
            raise
        new_cert = cert.parent / 'federation.pem.new'
        with new_cert.open('a') as stream:          # chain: leaf + CA, like the server certificate
            stream.write((root / 'opentak/data/ca/ca.pem').read_text())
        os.chmod(new_cert, 0o644)
        os.chmod(cert.parent / 'federation.nopass.key.new', 0o600)
        os.replace(cert.parent / 'federation.nopass.key.new', key)
        os.replace(new_cert, cert)
        for leftover in ('federation.csr', 'federation.ext'):
            (cert.parent / leftover).unlink(missing_ok=True)
    out = openssl('x509', '-noout', '-fingerprint', '-sha256', '-in', str(seen_folder / 'federation.pem'))
    return out.strip().split('=', 1)[-1].replace(':', '').lower()


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'ensure':
        try:
            print('PORTABLETAK_FEDERATION_CERT=' + ensure(sys.argv[2] if len(sys.argv) > 2 else '/mnt/asgard'))
        except ProvisionError as error:
            print('PORTABLETAK_FEDERATION_CERT_ERROR=%s' % error)
            sys.exit(1)
    else:
        print('usage: federation_cert.py ensure [ROOT]', file=sys.stderr)
        sys.exit(2)
