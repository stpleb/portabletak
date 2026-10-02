"""Clean-base source verification and sealing. Never reads a live cartridge.

A seal records content integrity; trust comes from verified upstream inputs and
reviewed build steps. Runtime mutations are intentionally outside this module.
"""
import hashlib
import json
import os
import posixpath
import copy
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile

class BaseError(Exception):
    pass


MUTABLE_PATHS = frozenset({'portabletak/cartridge.conf'})
STAGING_PATHS = frozenset({'portabletak/provision'})
# mke2fs creates this at the filesystem root and e2fsck recreates it after a
# crash. It belongs to the filesystem, not to the sealed software payload.
FILESYSTEM_PATHS = frozenset({'lost+found'})


def filesystem_owned(root, path):
    relative = path.relative_to(root).parts
    return bool(relative) and relative[0] in FILESYSTEM_PATHS


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def verify_download(archive, checksum_file):
    entries = []
    for line in Path(checksum_file).read_text().splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip('*') == Path(archive).name:
            entries.append(fields[0])
    if len(entries) != 1 or not re.fullmatch('[0-9a-f]{64}', entries[0]):
        raise BaseError('invalid_published_checksum')
    if digest(Path(archive)) != entries[0]:
        raise BaseError('source_checksum_mismatch')
    return entries[0]


def inspect_archive(archive):
    """Reject writes through archive links before Linux extraction occurs."""
    paths, links = set(), set()
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise BaseError('unsafe_archive_path')
            name = str(path)
            if name in paths and name != '.':
                raise BaseError('duplicate_archive_path')
            paths.add(name)
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise BaseError('unsupported_archive_entry')
            if member.issym() or member.islnk():
                links.add(name)
                link = PurePosixPath(member.linkname)
                if link.is_absolute() and (not member.issym() or '..' in link.parts):
                    raise BaseError('unsafe_absolute_archive_link')
                parts = [] if link.is_absolute() else list(path.parent.parts if member.issym() else ())
                for part in link.parts:
                    if part == '..':
                        if not parts:
                            raise BaseError('escaping_archive_link')
                        parts.pop()
                    elif part not in ('.', '/'):
                        parts.append(part)
        for member in members:
            path = PurePosixPath(member.name)
            if any(str(parent) in links for parent in path.parents):
                raise BaseError('archive_writes_through_link')
    return len(members)


def extract_verified(archive, checksum_file, destination):
    checksum = verify_download(archive, checksum_file)
    inspect_archive(archive)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or destination.parent.resolve() != destination.parent:
        raise BaseError('extraction_target_must_be_new')
    destination.mkdir(mode=0o700)
    with tarfile.open(archive, 'r:gz') as source:
        def safe_member(member, target):
            item = copy.copy(member)
            if item.issym() and item.linkname.startswith('/'):
                # Rootfs absolute links must refer to this rootfs, even before chroot.
                item.linkname = posixpath.relpath(item.linkname.lstrip('/'), posixpath.dirname(item.name) or '.')
            return tarfile.data_filter(item, target)
        source.extractall(destination, filter=safe_member)
    return checksum


def audit_clean_tree(root):
    root = Path(root)
    if root.is_symlink() or root.resolve() != root.absolute():
        raise BaseError('invalid_base_root')
    # A dual-chip base also carries an x86_64 tree; audit it the same way.
    trees = ['rootfs'] + (['arch/amd64/rootfs'] if (root / 'arch/amd64/rootfs').is_dir() else [])
    per_tree = ('var/lib/postgresql', 'var/lib/rabbitmq', 'root', 'tmp', 'run', 'var/log',
                'asgard/opentak/data', 'asgard/postgres', 'asgard/rabbitmq')
    for relative in ('postgres', 'rabbitmq', 'opentak/data', 'opentak/run',
                     'portabletak/provision', 'portabletak/backups', 'config',
                     *(tree + '/' + item for tree in trees for item in per_tree)):
        path = root / relative
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent) or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
            raise BaseError('persistent_or_personal_data:' + relative)
    for relative in (tree + '/' + item for tree in trees
                     for item in ('etc/machine-id', 'var/lib/dbus/machine-id')):
        path = root / relative
        if path.is_symlink() or (path.exists() and path.read_bytes().strip()):
            raise BaseError('machine_identity_present')
    for tree in trees:
        shadow = root / tree / 'etc/shadow'
        if shadow.is_symlink():
            raise BaseError('invalid_shadow_file')
        if shadow.exists():
            for line in shadow.read_text().splitlines():
                fields = line.split(':')
                if len(fields) != 9 or not fields[1] or set(fields[1]) - {'!', '*'}:
                    raise BaseError('login_credential_present')
    for path in root.rglob('*'):
        if path.is_symlink() or filesystem_owned(root, path):
            continue
        if not path.is_file():
            if not path.is_dir():
                raise BaseError('special_file_in_base')
            continue
        name = path.name
        if name in {'.erlang.cookie', 'authorized_keys', '.bash_history', '.python_history'} or name.endswith(('.p12', '.pfx')):
            raise BaseError('identity_artifact_present')
        if name.startswith('ssh_host_') or '.before-' in name:
            raise BaseError('identity_or_backup_present')
        # Detect PEM private keys without logging or returning their content.
        with path.open('rb') as stream:
            tail = b''
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                chunk = tail + block
                if re.search(
                    rb'(?m)^-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/]',
                    chunk,
                ):
                    raise BaseError('private_key_present:' + path.relative_to(root).as_posix())
                tail = chunk[-128:]
    return True


def inventory(root, excluded=frozenset()):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if (relative == 'portabletak/base-seal.json' or relative in excluded
                or filesystem_owned(root, path)):
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            result[relative] = {'kind': 'link', 'target': os.readlink(path), 'mode': mode}
        elif path.is_file():
            result[relative] = {'kind': 'file', 'sha256': digest(path), 'mode': mode}
        elif path.is_dir():
            result[relative] = {'kind': 'directory', 'mode': mode}
        else:
            raise BaseError('special_file_in_base')
    return result


def seal(root, provenance):
    root = Path(root)
    seal_path = root / 'portabletak/base-seal.json'
    if seal_path.exists() or seal_path.is_symlink():
        raise BaseError('base_already_sealed')
    if not isinstance(provenance, dict) or set(provenance) != {'ubuntu_archive', 'ubuntu_sha256', 'apt_packages', 'python_packages'}:
        raise BaseError('incomplete_provenance')
    if not re.fullmatch('[0-9a-f]{64}', provenance['ubuntu_sha256']):
        raise BaseError('invalid_source_digest')
    if not provenance['apt_packages'] or not provenance['python_packages']:
        raise BaseError('package_inventory_required')
    identity = root / 'portabletak/cartridge.conf'
    if identity.is_symlink() or not identity.is_file():
        raise BaseError('cartridge_identity_template_required')
    audit_clean_tree(root)
    (root / 'portabletak').mkdir(exist_ok=True)
    data = {'format': 2, 'kind': 'portabletak-clean-base-seal',
            'mutable': sorted(MUTABLE_PATHS), 'provenance': provenance,
            'files': inventory(root, MUTABLE_PATHS)}
    with seal_path.open('x') as stream:
        json.dump(data, stream, sort_keys=True, separators=(',', ':'))
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    return digest(seal_path)


def verify_seal(root, expected_digest):
    root = Path(root)
    path = root / 'portabletak/base-seal.json'
    if path.is_symlink() or digest(path) != expected_digest:
        raise BaseError('base_seal_mismatch')
    data = json.loads(path.read_text())
    if data.get('kind') != 'portabletak-clean-base-seal':
        raise BaseError('unsupported_base_seal')
    if data.get('format') == 1:
        excluded = frozenset()
    elif data.get('format') == 2 and data.get('mutable') == sorted(MUTABLE_PATHS):
        identity = root / 'portabletak/cartridge.conf'
        if identity.is_symlink() or not identity.is_file():
            raise BaseError('cartridge_identity_missing')
        excluded = MUTABLE_PATHS
    else:
        raise BaseError('unsupported_base_seal')
    audit_clean_tree(root)
    if data['files'] != inventory(root, excluded):
        raise BaseError('base_content_changed')
    return True


def verify_staged_seal(root, expected_digest):
    """Verify sealed software after the private provisioning journal exists."""
    root = Path(root)
    path = root / 'portabletak/base-seal.json'
    if path.is_symlink() or digest(path) != expected_digest:
        raise BaseError('base_seal_mismatch')
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise BaseError('invalid_base_seal') from None
    if (data.get('kind') != 'portabletak-clean-base-seal'
            or data.get('format') != 2
            or data.get('mutable') != sorted(MUTABLE_PATHS)
            or not isinstance(data.get('files'), dict)):
        raise BaseError('unsupported_base_seal')
    identity = root / 'portabletak/cartridge.conf'
    if identity.is_symlink() or not identity.is_file():
        raise BaseError('cartridge_identity_missing')
    current = inventory(root, MUTABLE_PATHS)
    for relative in list(current):
        if any(relative == prefix or relative.startswith(prefix + '/')
               for prefix in STAGING_PATHS):
            current.pop(relative)
    if data['files'] != current:
        raise BaseError('base_content_changed')
    return True


def verify_sealed_software(root, expected_digest):
    """Verify every sealed file and link after runtime data exists.

    Used when resuming an interrupted first run: data directories now hold this
    cartridge's database and certificates, and runtime mounts change directory
    modes, so only software content, file modes, and link targets are compared.
    Directories must still exist; extra runtime paths are allowed.
    """
    root = Path(root)
    path = root / 'portabletak/base-seal.json'
    if path.is_symlink() or digest(path) != expected_digest:
        raise BaseError('base_seal_mismatch')
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise BaseError('invalid_base_seal') from None
    if (data.get('kind') != 'portabletak-clean-base-seal' or data.get('format') != 2
            or data.get('mutable') != sorted(MUTABLE_PATHS)
            or not isinstance(data.get('files'), dict)):
        raise BaseError('unsupported_base_seal')
    for relative, entry in data['files'].items():
        target = root / relative
        kind = entry.get('kind')
        if kind == 'link':
            if not target.is_symlink() or os.readlink(target) != entry.get('target'):
                raise BaseError('base_content_changed:' + relative)
        elif kind == 'directory':
            if target.is_symlink() or not target.is_dir():
                raise BaseError('base_content_changed:' + relative)
        elif kind == 'file':
            if (target.is_symlink() or not target.is_file()
                    or stat.S_IMODE(target.lstat().st_mode) != entry.get('mode')
                    or digest(target) != entry.get('sha256')):
                raise BaseError('base_content_changed:' + relative)
        else:
            raise BaseError('unsupported_base_seal')
    return True
