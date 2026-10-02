"""Sanitize a freshly assembled, stopped development rootfs before sealing.

Only use on a marked build tree. This tool must never run on a cartridge.
"""
import json
import os
from pathlib import Path
import shutil
import sys
root=Path(sys.argv[1]).absolute()
assert root.resolve()==root and str(root).startswith('/tmp/portabletak-cleanbase-')
assert (root/'.portabletak-development').is_file()
fs=root/'rootfs'
assert fs.is_dir() and not fs.is_symlink()
for line in Path('/proc/mounts').read_text().splitlines():
    assert not line.split()[1].startswith(str(fs)+'/'), 'Build mounts still active'
# Capture software provenance before removing installation records/caches.
apt={}
for line in (fs/'build-apt-packages.txt').read_text().splitlines():
    name,version,architecture=line.split('\t')
    apt[name]={'version':version,'architecture':architecture}
python={}
for line in (fs/'build-python-packages.txt').read_text().splitlines():
    name,version=line.split('==',1); python[name]=version
provenance={'ubuntu_archive':'ubuntu-base-24.04.5-base-arm64.tar.gz','ubuntu_sha256':'a91d5a93010193712d346d761372b7c9db6dfcf093893161c64ca107f05914f2','apt_packages':apt,'python_packages':python}
(root/'provenance.json').write_text(json.dumps(provenance,sort_keys=True,indent=2)+'\n')
# These paths belong exclusively to the new build and contain only generated
# package-install state. The audit still refuses any unexpected private key.
for relative in ('root','tmp','run','var/log','var/cache/apt/archives','var/lib/apt/lists','var/lib/postgresql','var/lib/rabbitmq'):
    path=fs/relative
    assert not path.is_symlink()
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True,mode=0o755)
(fs/'tmp').chmod(0o1777)
for relative in ('etc/machine-id','var/lib/dbus/machine-id','var/lib/systemd/random-seed','etc/ssl/private/ssl-cert-snakeoil.key','etc/ssl/certs/ssl-cert-snakeoil.pem','install-clean.sh','build-apt-packages.txt','build-python-packages.txt','build-python-report.json'):
    path=fs/relative
    if path.is_file() or path.is_symlink(): path.unlink()
(fs/'etc/machine-id').touch()
(fs/'etc/hostname').write_text('portabletak-unconfigured\n')
(fs/'etc/hosts').write_text('127.0.0.1 localhost\n::1 localhost\n')
for path in fs.rglob('__pycache__'):
    if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
print('Sanitized clean build; audit and seal are still required')
