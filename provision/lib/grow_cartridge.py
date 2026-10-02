"""Grow the mounted PortableTAK partition to fill the rest of its drive.

PortableTAK Setup writes a compact image (a small "Start here" partition, then
the cartridge partition). The first time the drive runs, this extends the
cartridge partition to the end of the disk and grows its ext4 filesystem while
mounted, so the whole drive is usable for recordings, data and backups.

Only acts when the cartridge partition is the last one on an MBR disk and at
least MIN_GAIN bytes are unused after it. Uses sfdisk to change the table,
BLKPG to tell the kernel, and the cartridge's own resize2fs (hosts such as
OpenWrt do not ship one), run through the cartridge's dynamic loader.

    python3 grow_cartridge.py /mnt/asgard
"""
import ctypes
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_profile import host_arch, rootfs as host_rootfs

MIN_GAIN = 1024 ** 3
BLKPG = 0x1269
BLKPG_RESIZE_PARTITION = 3


def mount_source(mount_point, mounts='/proc/mounts'):
    for line in Path(mounts).read_text().splitlines():
        fields = line.split()
        if len(fields) > 2 and fields[1] == str(mount_point) and fields[2] == 'ext4':
            return fields[0]
    return None


def layout(partition, sys_root='/sys'):
    """(disk_name, number, start_sector, size_sectors, disk_sectors, last) for a partition device."""
    name = os.path.basename(os.path.realpath(partition))
    part_dir = Path(sys_root) / 'class/block' / name
    if not (part_dir / 'partition').is_file():
        raise ValueError('not_a_partition')
    disk_dir = part_dir.resolve().parent
    disk = disk_dir.name
    number = int((part_dir / 'partition').read_text())
    start = int((part_dir / 'start').read_text())
    size = int((part_dir / 'size').read_text())
    disk_sectors = int((disk_dir / 'size').read_text())
    others = [p for p in disk_dir.iterdir() if (p / 'partition').is_file() and p.name != name]
    last = all(int((p / 'start').read_text()) < start for p in others)
    return disk, number, start, size, disk_sectors, last


def plan(partition, sys_root='/sys'):
    disk, number, start, size, disk_sectors, last = layout(partition, sys_root)
    gain = (disk_sectors - (start + size)) * 512
    return {'disk': disk, 'number': number, 'start': start, 'size': size,
            'disk_sectors': disk_sectors, 'last': last, 'gain_bytes': gain,
            'grow': last and gain >= MIN_GAIN}


class _Partition(ctypes.Structure):
    _fields_ = [('start', ctypes.c_longlong), ('length', ctypes.c_longlong), ('pno', ctypes.c_int),
                ('devname', ctypes.c_char * 64), ('volname', ctypes.c_char * 64)]


class _Arg(ctypes.Structure):
    _fields_ = [('op', ctypes.c_int), ('flags', ctypes.c_int), ('datalen', ctypes.c_int),
                ('data', ctypes.c_void_p)]


def blkpg_resize(disk_path, number, start, size):
    """Tell the kernel the partition's new size (works while it is mounted)."""
    part = _Partition(start=start * 512, length=size * 512, pno=number)
    arg = _Arg(op=BLKPG_RESIZE_PARTITION, flags=0, datalen=ctypes.sizeof(part),
               data=ctypes.cast(ctypes.pointer(part), ctypes.c_void_p))
    with open(disk_path, 'rb') as handle:
        fcntl.ioctl(handle.fileno(), BLKPG, arg)


def resize2fs_command(root, machine=None):
    """Run the cartridge's resize2fs through its own loader (host may lack one)."""
    tree = host_rootfs(root, machine)
    if host_arch(machine) == 'x86_64':
        loader, libs = tree / 'lib64/ld-linux-x86-64.so.2', 'x86_64-linux-gnu'
    else:
        loader, libs = tree / 'lib/ld-linux-aarch64.so.1', 'aarch64-linux-gnu'
    library_path = ':'.join(str(tree / p / libs) for p in ('lib', 'usr/lib'))
    binary = next((tree / p for p in ('usr/sbin/resize2fs', 'sbin/resize2fs') if (tree / p).is_file()), None)
    if binary is None or not loader.exists():
        raise FileNotFoundError('cartridge_resize2fs_missing')
    return [str(loader), '--library-path', library_path, str(binary)]


def grow(root='/mnt/asgard', run=subprocess.run, sys_root='/sys', resize=blkpg_resize):
    root = Path(root)
    source = mount_source(root)
    if not source:
        return {'result': 'not_mounted'}
    # Never touch a protected cartridge (ASGARD) or anything that is not a generic PortableTAK drive.
    from media_layout import protected_filesystems, read_ext_superblock
    from portabletak_provision import metadata
    found = read_ext_superblock(source)
    if not found or found[0] in protected_filesystems():
        return {'result': 'protected_or_unknown'}
    try:
        if metadata(root / 'portabletak/cartridge.conf').get('PORTABLETAK_INSTANCE_TYPE') != 'generic':
            return {'result': 'not_generic'}
    except Exception:
        return {'result': 'not_generic'}
    info = plan(source, sys_root)
    if not info['grow']:
        return dict(info, result='not_needed')
    disk_path = '/dev/' + info['disk']
    new_size = info['disk_sectors'] - info['start']
    # Keep 1 MiB alignment of the end, as partitioning tools do.
    new_size -= (info['start'] + new_size) % 2048
    table = run(['sfdisk', '--dump', disk_path], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if table.returncode or 'label: dos' not in table.stdout:
        return dict(info, result='unsupported_partition_table')
    script = ','.join([str(info['start']), str(new_size)])
    changed = run(['sfdisk', '--no-reread', '--no-tell-kernel', '-q', '-N', str(info['number']), disk_path],
                  input=script + '\n', capture_output=True, text=True)
    if changed.returncode:
        return dict(info, result='sfdisk_failed', detail=changed.stderr.strip()[-200:])
    resize(disk_path, info['number'], info['start'], new_size)
    grown = run(resize2fs_command(root) + [source], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if grown.returncode:
        return dict(info, result='resize2fs_failed', detail=grown.stderr.strip()[-200:])
    return dict(info, result='grown', new_size=new_size)


if __name__ == '__main__':
    outcome = grow(sys.argv[1] if len(sys.argv) > 1 else '/mnt/asgard')
    print('PORTABLETAK_GROW=' + outcome['result'].upper())
    if 'gain_bytes' in outcome:
        print('PORTABLETAK_GROW_GAIN_GB=%.1f' % (outcome['gain_bytes'] / 1024 ** 3))
    if outcome.get('detail'):
        print('PORTABLETAK_GROW_DETAIL=' + re.sub(r'\s+', ' ', outcome['detail']))
    sys.exit(0 if outcome['result'] in ('grown', 'not_needed', 'not_mounted', 'protected_or_unknown', 'not_generic') else 1)
