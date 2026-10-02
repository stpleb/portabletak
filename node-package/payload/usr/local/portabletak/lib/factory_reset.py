"""Return a PortableTAK cartridge to its first-run state ("factory reset").

Everything that is not sealed software is deleted: the database, RabbitMQ
state, certificates and users, configuration, remote-access state, logs and
the backups kept on the drive. Sealed software is checked file by file and its
modes restored; a fresh cartridge identity is written; then the strict seal
check proves the drive is exactly a blank PortableTAK drive again.

Callers must stop the services and remove the runtime mounts first. Refuses
anything that is not a sealed generic PortableTAK cartridge (never ASGARD).
"""
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

import clean_base
from portabletak_provision import ProvisionError, metadata as read_metadata, write_metadata

KEEP = {'portabletak/base-seal.json', 'portabletak/cartridge.conf'}


def _sealed(root):
    path = root / 'portabletak/base-seal.json'
    if path.is_symlink() or not path.is_file():
        raise ProvisionError('reset_not_a_sealed_cartridge')
    data = json.loads(path.read_text())
    if data.get('kind') != 'portabletak-clean-base-seal' or data.get('format') != 2:
        raise ProvisionError('reset_unsupported_seal')
    return data['files'], clean_base.digest(path)


def _active_mounts(root):
    prefix = str(root).rstrip('/') + '/'
    with open('/proc/mounts') as mounts:
        return [line.split()[1] for line in mounts if line.split()[1].startswith(prefix)]


def plan(root):
    """Paths to delete (deepest first) for a reset; validates the cartridge."""
    root = Path(root)
    metadata = read_metadata(root / 'portabletak/cartridge.conf')
    if metadata.get('PORTABLETAK_INSTANCE_TYPE') != 'generic':
        raise ProvisionError('reset_refused_not_generic')
    files, _ = _sealed(root)
    sealed_dirs = {rel for rel, entry in files.items() if entry['kind'] == 'directory'}
    doomed = []
    for path in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        relative = path.relative_to(root).as_posix()
        if relative in files or relative in KEEP or clean_base.filesystem_owned(root, path):
            continue
        # Keep parents of sealed paths (they are sealed directories themselves).
        if relative in sealed_dirs:
            continue
        doomed.append(path)
    return doomed


def reset(root, mounts=_active_mounts):
    root = Path(root)
    if mounts(root):
        raise ProvisionError('reset_requires_stopped_cartridge')
    files, seal_digest = _sealed(root)
    metadata = read_metadata(root / 'portabletak/cartridge.conf')
    # Check the software before deleting anything, so a damaged drive is reported, not half-reset.
    for relative, entry in files.items():
        path = root / relative
        kind = entry['kind']
        if kind == 'link':
            ok = path.is_symlink() and os.readlink(path) == entry['target']
        elif kind == 'file':
            ok = path.is_file() and not path.is_symlink() and clean_base.digest(path) == entry['sha256']
        else:
            ok = path.is_dir() and not path.is_symlink()
        if not ok:
            raise ProvisionError('reset_software_damaged:' + relative[:120])
    removed = 0
    for path in plan(root):
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path)
        removed += 1
    for relative, entry in files.items():
        path = root / relative
        if entry['kind'] != 'link' and stat.S_IMODE(path.lstat().st_mode) != entry['mode']:
            os.chmod(path, entry['mode'])
    write_metadata(root / 'portabletak/cartridge.conf', {
        'PORTABLETAK_FORMAT': metadata.get('PORTABLETAK_FORMAT', '1'),
        'PORTABLETAK_ARCH': metadata.get('PORTABLETAK_ARCH', 'arm64'),
        'PORTABLETAK_CARTRIDGE_ID': str(uuid.uuid4()),
        'PORTABLETAK_INSTANCE_NAME': 'Unconfigured',
        'PORTABLETAK_INSTANCE_TYPE': 'generic',
        'PORTABLETAK_PROVISIONING_STATE': 'new'})
    clean_base.verify_seal(root, seal_digest)
    return {'removed': removed, 'seal_sha256': seal_digest}
