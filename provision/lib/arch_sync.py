"""Keep the provisioned configuration identical in both rootfs trees.

A dual-chip cartridge carries an arm64 rootfs (rootfs/) and an x86_64 rootfs
(arch/amd64/rootfs/). Provisioning writes a few configuration files into the
rootfs (RabbitMQ, the nginx site); rootfs/ holds the authoritative copy and
this copies them, with modes and numeric owners, into the tree for the
current host's processor. A no-op on arm64 hosts and on single-chip cartridges.

    python3 arch_sync.py /mnt/asgard
"""
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_profile import rootfs as host_rootfs

CONFIG = ('etc/rabbitmq', 'etc/nginx/sites-available/portabletak',
          'etc/nginx/sites-enabled/portabletak',
          'etc/nginx/sites-available/portabletak-dashboard',
          'etc/nginx/sites-enabled/portabletak-dashboard')
DIRECTORIES = ('var/log/rabbitmq', 'var/log/nginx')


def _same_meta(source, target):
    info = os.lstat(source)
    os.lchown(target, info.st_uid, info.st_gid)
    if not os.path.islink(target):
        os.chmod(target, info.st_mode & 0o7777)


def _copy(source, target):
    if source.is_symlink():
        if target.is_symlink() or target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.symlink_to(os.readlink(source))
    elif source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy(child, target / child.name)
    else:
        if target.is_symlink() or (target.exists() and target.is_dir()):
            raise ValueError('arch_sync_unexpected_target:' + str(target))
        temporary = target.with_name(target.name + '.pt-sync')
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    _same_meta(source, target)


def sync(root='/mnt/asgard', machine=None):
    root = Path(root)
    source_root, target_root = root / 'rootfs', host_rootfs(root, machine)
    if target_root == source_root or not target_root.is_dir():
        return []
    copied = []
    for relative in CONFIG:
        source = source_root / relative
        if source.is_symlink() or source.exists():
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy(source, target)
            copied.append(relative)
    for relative in DIRECTORIES:
        source = source_root / relative
        if source.is_dir() and not source.is_symlink():
            target = target_root / relative
            target.mkdir(parents=True, exist_ok=True)
            _same_meta(source, target)
            copied.append(relative)
    return copied


if __name__ == '__main__':
    try:
        done = sync(sys.argv[1] if len(sys.argv) > 1 else '/mnt/asgard')
    except (OSError, ValueError) as error:
        print('PORTABLETAK_ARCH_SYNC=FAILED')
        print('PORTABLETAK_ARCH_SYNC_ERROR=' + str(error))
        sys.exit(1)
    print('PORTABLETAK_ARCH_SYNC=' + ('COPIED:' + ','.join(done) if done else 'NOT_NEEDED'))
