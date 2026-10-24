"""Set aside user packages whose certificate passwords do not match their settings.

2026.10.23 - 2026.10.23.3 ran the federation add-on with OpenTAKServer's stock
certificate authority, which encrypts the .p12 files with one password and writes
another into preference.pref ("Invalid Truststore" in ATAK). Such packages are renamed
to *.invalid at start; the dashboard and QR enrollment make new ones when next needed.

    python3 client_package_check.py /mnt/asgard   -> PORTABLETAK_CLIENT_PACKAGES_RESET=<users>
"""
from pathlib import Path
import io
import os
import re
import subprocess
import sys
import tempfile
import zipfile


def _password(pref, key):
    found = re.search(r'key="%s"[^>]*>([^<]*)<' % key, pref)
    return found.group(1) if found else None


def wrong_password(data):
    """True when the package's trust store does not open with its own caPassword.
    Anything unexpected (layout, no openssl) counts as fine: never discard on doubt."""
    try:
        outer = zipfile.ZipFile(io.BytesIO(data))
        inner_name = next((n for n in outer.namelist() if n.endswith('.zip')), None)
        if inner_name is None:
            return False
        inner = zipfile.ZipFile(io.BytesIO(outer.read(inner_name)))
        pref_name = next((n for n in inner.namelist() if n.endswith('preference.pref')), None)
        trust_name = next((n for n in inner.namelist() if n.endswith('.p12') and 'truststore' in n), None)
        if not (pref_name and trust_name):
            return False
        password = _password(inner.read(pref_name).decode('utf-8', 'replace'), 'caPassword')
        if password is None:
            return False
        with tempfile.NamedTemporaryFile(suffix='.p12') as store:
            store.write(inner.read(trust_name))
            store.flush()
            env = dict(os.environ, PT_CHECK_PASSWORD=password.replace('&amp;', '&')
                       .replace('&lt;', '<').replace('&gt;', '>'))
            result = subprocess.run(['openssl', 'pkcs12', '-info', '-noout', '-in', store.name,
                                     '-passin', 'env:PT_CHECK_PASSWORD'],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        # The MAC check runs before any decryption, so this message means a wrong password
        # (and not a missing legacy cipher).
        return result.returncode != 0 and b'invalid password' in (result.stdout + result.stderr)
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError):
        return False


def check(root='/mnt/asgard'):
    reset = []
    certs = Path(root) / 'opentak/data/ca/certs'
    for package in sorted(certs.glob('*/*_CONFIG.zip')):
        user = package.name[:-len('_CONFIG.zip')]
        if package.parent.name != user or not wrong_password(package.read_bytes()):
            continue
        for name in (user + '_CONFIG.zip', user + '_CONFIG_iTAK.zip'):
            path = package.parent / name
            if path.exists():
                path.replace(path.with_name(name + '.invalid'))
        reset.append(user)
    return reset


if __name__ == '__main__':
    users = check(sys.argv[1] if len(sys.argv) > 1 else '/mnt/asgard')
    if users:
        print('PORTABLETAK_CLIENT_PACKAGES_RESET=' + ','.join(users))
