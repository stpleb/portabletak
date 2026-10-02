"""Lay a sealed clean base onto a blank removable disk. Standard library only.

Block names such as /dev/sdb are never trusted on their own because they change
when a device re-enumerates. Every destructive step requires the operator to
repeat a fingerprint of the disk's hardware identity, which is recomputed and
compared immediately before the partition table is replaced.

The archive is proven against its embedded seal before any disk is touched, and
the written filesystem is verified again after a clean unmount and read-only
remount. The cartridge metadata file is written last, so an interrupted layout
is never discovered as a cartridge.
"""
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import time
import uuid

import clean_base
from portabletak_provision import ProvisionError, metadata

ARCHIVE_ROOT = 'payload'
METADATA_PATH = 'portabletak/cartridge.conf'
SEAL_PATH = 'portabletak/base-seal.json'
FILESYSTEM_LABEL = 'PORTABLETAK'
PARTITION_START_SECTOR = 2048
MIN_DISK_BYTES = 8 * 1024 ** 3
# ASGARD's cartridge filesystem. Refused unconditionally, even if named by the
# operator. Hosts may add more in PROTECTED_FILE, one UUID per line.
PROTECTED_FILESYSTEMS = frozenset({'1f501ae2-54f0-460d-a000-bce2fcc78c20'})
PROTECTED_FILE = '/usr/local/portabletak/release/protected-filesystems'
EXT_MAGIC = 0xEF53


class LayoutError(Exception):
    def __init__(self, code, step=None):
        self.code = code
        self.step = step
        super().__init__(code if step is None else f'{step}:{code}')


def sha256_file(path):
    return clean_base.digest(Path(path))


def read_attr(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return ''


# ---------------------------------------------------------------- identity

def usb_attributes(device_path):
    """Return the nearest USB device's attributes above a sysfs block path."""
    for directory in (device_path, *device_path.parents):
        if (directory / 'idVendor').is_file() and (directory / 'idProduct').is_file():
            return {
                'usb_port': directory.name,
                'id_vendor': read_attr(directory / 'idVendor'),
                'id_product': read_attr(directory / 'idProduct'),
                'serial': read_attr(directory / 'serial'),
                'manufacturer': read_attr(directory / 'manufacturer'),
                'product': read_attr(directory / 'product'),
                'speed_mbps': read_attr(directory / 'speed'),
            }
    return None


def partition_name(disk, number=1):
    return f'{disk}p{number}' if disk[-1:].isdigit() else f'{disk}{number}'


def disk_identity(name, sys_root='/sys'):
    if not re.fullmatch(r'[A-Za-z0-9]+', name or ''):
        raise LayoutError('invalid_disk_name')
    block = Path(sys_root) / 'block' / name
    if not block.exists():
        if (Path(sys_root) / 'class/block' / name / 'partition').exists():
            raise LayoutError('not_whole_disk')
        raise LayoutError('disk_not_found')
    real = block.resolve()
    sectors = read_attr(real / 'size')
    partitions = []
    for child in sorted(real.iterdir()):
        if (child / 'partition').is_file():
            partitions.append({'name': child.name, 'dev': read_attr(child / 'dev'),
                               'start': int(read_attr(child / 'start') or 0),
                               'sectors': int(read_attr(child / 'size') or 0)})
    usb = usb_attributes(real)
    holders = real / 'holders'
    identity = {
        'name': name,
        'dev': read_attr(real / 'dev'),
        'size_bytes': int(sectors) * 512 if sectors.isdigit() else 0,
        'vendor': read_attr(real / 'device/vendor'),
        'model': read_attr(real / 'device/model'),
        'removable_flag': read_attr(real / 'removable'),
        'usb': usb,
        'sysfs_path': str(real)[len(str(Path(sys_root).resolve())):],
        'partitions': partitions,
        'holders': sorted(p.name for p in holders.iterdir()) if holders.is_dir() else [],
    }
    identity['fingerprint'] = fingerprint(identity)
    return identity


def fingerprint(identity):
    usb = identity.get('usb') or {}
    stable = {key: usb.get(key, '') for key in ('usb_port', 'id_vendor', 'id_product', 'serial')}
    stable.update(vendor=identity['vendor'], model=identity['model'],
                  size_bytes=identity['size_bytes'])
    encoded = json.dumps(stable, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def confirmation_phrase(identity):
    return 'ERASE ' + identity['fingerprint']


def weak_serial(serial):
    return (not serial or len(serial) < 8 or len(set(serial)) <= 3
            or bool(re.fullmatch(r'0+[0-9A-Fa-f]{0,4}', serial)))


def read_ext_superblock(device):
    """Return (uuid, label) of an ext2/3/4 superblock, or None. Read-only."""
    try:
        with open(device, 'rb') as stream:
            stream.seek(1024)
            block = stream.read(1024)
    except OSError:
        raise LayoutError('unreadable_block_device') from None
    if len(block) < 0x88 or int.from_bytes(block[0x38:0x3A], 'little') != EXT_MAGIC:
        return None
    label = block[0x78:0x88].split(b'\0', 1)[0].decode('utf-8', 'replace')
    return str(uuid.UUID(bytes=bytes(block[0x68:0x78]))), label


def mounted_devices(mountinfo='/proc/self/mountinfo'):
    result = set()
    for line in Path(mountinfo).read_text().splitlines():
        fields = line.split()
        if len(fields) > 2:
            result.add(fields[2])
    return result


def swap_devices(swaps='/proc/swaps'):
    try:
        lines = Path(swaps).read_text().splitlines()[1:]
    except OSError:
        return set()
    return {line.split()[0] for line in lines if line.split()}


def protected_filesystems(path=PROTECTED_FILE):
    extra = set()
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip().lower()
            if line and not line.startswith('#'):
                extra.add(str(uuid.UUID(line)))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        raise LayoutError('invalid_protected_filesystem_list') from None
    return PROTECTED_FILESYSTEMS | extra


def assess(identity, dev_root='/dev', mountinfo='/proc/self/mountinfo',
           swaps='/proc/swaps', protected=None, replace_ext_uuid=None,
           min_bytes=MIN_DISK_BYTES):
    """Return (refusals, warnings, filesystems). Never writes."""
    protected = PROTECTED_FILESYSTEMS if protected is None else protected
    refusals, warnings, filesystems = [], [], []
    if not identity['usb']:
        refusals.append('not_usb_attached')
    elif weak_serial(identity['usb']['serial']):
        warnings.append('weak_usb_serial_use_port_and_size')
    if identity['size_bytes'] < min_bytes:
        refusals.append('disk_too_small')
    if identity['holders']:
        refusals.append('disk_has_holders')
    in_use = mounted_devices(mountinfo)
    devs = {identity['dev']} | {p['dev'] for p in identity['partitions']}
    if devs & in_use:
        refusals.append('disk_in_use_mounted')
    names = {identity['name']} | {p['name'] for p in identity['partitions']}
    if {str(Path(dev_root) / n) for n in names} & swap_devices(swaps):
        refusals.append('disk_in_use_swap')
    for name in [identity['name']] + [p['name'] for p in identity['partitions']]:
        try:
            found = read_ext_superblock(Path(dev_root) / name)
        except LayoutError:
            refusals.append('unreadable:' + name)
            continue
        if not found:
            continue
        fs_uuid, label = found
        filesystems.append({'device': name, 'ext_uuid': fs_uuid, 'label': label})
        if fs_uuid in protected:
            refusals.append('protected_filesystem:' + fs_uuid)
        elif fs_uuid != replace_ext_uuid:
            refusals.append('existing_linux_filesystem:' + fs_uuid)
    return refusals, warnings, filesystems


def plan(name, sys_root='/sys', **kwargs):
    identity = disk_identity(name, sys_root)
    refusals, warnings, filesystems = assess(identity, **kwargs)
    return {'identity': identity, 'filesystems': filesystems, 'refusals': refusals,
            'warnings': warnings, 'eligible': not refusals,
            'confirmation': confirmation_phrase(identity) if not refusals else None}


def list_disks(sys_root='/sys', **kwargs):
    result = []
    for entry in sorted((Path(sys_root) / 'block').iterdir()):
        if entry.name.startswith(('loop', 'ram', 'zram', 'mtdblock', 'dm-', 'md')):
            continue
        try:
            result.append(plan(entry.name, sys_root, **kwargs))
        except LayoutError as error:
            result.append({'identity': {'name': entry.name}, 'refusals': [error.code],
                           'eligible': False})
    return result


# ---------------------------------------------------------------- archive

def split_member(name):
    path = PurePosixPath(name)
    if path.parts[:1] != (ARCHIVE_ROOT,):
        raise LayoutError('archive_outside_payload')
    return '/'.join(path.parts[1:])


def verify_archive(archive, expected_archive_sha256, expected_seal, progress=None):
    """Prove every archive entry matches the embedded seal. Never writes."""
    if not re.fullmatch(r'[0-9a-f]{64}', expected_archive_sha256 or ''):
        raise LayoutError('invalid_expected_archive_digest')
    if not re.fullmatch(r'[0-9a-f]{64}', expected_seal or ''):
        raise LayoutError('invalid_expected_seal')
    if sha256_file(archive) != expected_archive_sha256:
        raise LayoutError('archive_checksum_mismatch')
    try:
        clean_base.inspect_archive(archive)
    except clean_base.BaseError as error:
        raise LayoutError(str(error)) from None
    seal = conf = None
    found, links, hardlinks, total = {}, set(), [], 0
    with tarfile.open(archive, 'r:gz') as source:
        for count, member in enumerate(source, 1):
            if member.name.rstrip('/') == ARCHIVE_ROOT:
                continue
            relative = split_member(member.name)
            mode = member.mode & 0o7777
            if relative == SEAL_PATH:
                raw = source.extractfile(member).read()
                if hashlib.sha256(raw).hexdigest() != expected_seal:
                    raise LayoutError('embedded_seal_mismatch')
                seal = json.loads(raw)
                continue
            if relative == METADATA_PATH:
                conf = source.extractfile(member).read()
                continue
            if member.issym():
                links.add(relative)
                found[relative] = {'kind': 'link', 'target': member.linkname, 'mode': mode}
            elif member.islnk():
                hardlinks.append((relative, split_member(member.linkname), mode))
            elif member.isdir():
                found[relative] = {'kind': 'directory', 'mode': mode}
            elif member.isfile():
                digest = hashlib.sha256()
                stream = source.extractfile(member)
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
                found[relative] = {'kind': 'file', 'sha256': digest.hexdigest(), 'mode': mode}
                total += member.size
            if progress and count % 5000 == 0:
                progress(count)
    for relative, target, mode in hardlinks:
        if any(str(parent) in links for parent in PurePosixPath(target).parents):
            raise LayoutError('hardlink_through_symlink')
        if found.get(target, {}).get('kind') != 'file':
            raise LayoutError('hardlink_target_missing')
        found[relative] = {'kind': 'file', 'sha256': found[target]['sha256'], 'mode': mode}
    if seal is None or conf is None:
        raise LayoutError('archive_missing_seal_or_metadata')
    if (seal.get('kind') != 'portabletak-clean-base-seal' or seal.get('format') != 2
            or seal.get('mutable') != sorted(clean_base.MUTABLE_PATHS)):
        raise LayoutError('unsupported_base_seal')
    if seal['files'] != found:
        raise LayoutError('archive_differs_from_seal')
    values = parse_template_metadata(conf)
    return {'members': len(found) + 2, 'payload_bytes': total,
            'architecture': values['PORTABLETAK_ARCH'],
            'archive_sha256': expected_archive_sha256, 'seal_sha256': expected_seal}


def parse_template_metadata(raw):
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'cartridge.conf'
        path.write_bytes(raw)
        return check_template_metadata(path)


def check_template_metadata(path):
    try:
        values = metadata(Path(path))
    except ProvisionError as error:
        raise LayoutError(error.code) from None
    if (uuid.UUID(values['PORTABLETAK_CARTRIDGE_ID']).int != 0
            or values['PORTABLETAK_PROVISIONING_STATE'] != 'new'
            or values['PORTABLETAK_INSTANCE_TYPE'] != 'generic'):
        raise LayoutError('template_metadata_not_blank')
    return values


def extract_payload(archive, destination, progress=None):
    """Extract faithfully: absolute link targets, set-id bits, numeric owners.

    The caller must have run verify_archive, whose inspection refuses paths that
    escape, duplicate, or write through links. Metadata is written last.
    """
    destination = Path(destination)
    present = {p.name for p in destination.iterdir()}
    if present - clean_base.FILESYSTEM_PATHS:
        raise LayoutError('destination_not_empty')
    trusted = {'filter': 'fully_trusted'} if hasattr(tarfile, 'fully_trusted_filter') else {}
    deferred = None
    with tarfile.open(archive, 'r:gz') as source:
        def members():
            nonlocal deferred
            for count, member in enumerate(source, 1):
                if member.name.rstrip('/') == ARCHIVE_ROOT:
                    continue
                item = copy.copy(member)
                item.name = split_member(member.name)
                if item.islnk():
                    item.linkname = split_member(member.linkname)
                if item.name == METADATA_PATH:
                    deferred = item
                    continue
                if progress and count % 5000 == 0:
                    progress(count)
                yield item
        source.extractall(destination, members=members(), numeric_owner=True, **trusted)
        if deferred is None:
            raise LayoutError('archive_missing_metadata')
        os.sync()
        source.extract(deferred, destination, numeric_owner=True, **trusted)
    os.sync()


# ---------------------------------------------------------------- block ops

class LinuxOps:
    """Real block-device operations. Each failure names its step."""

    def run(self, step, argv, stdin=None):
        result = subprocess.run(argv, input=stdin, capture_output=True, text=True)
        if result.returncode != 0:
            raise LayoutError(f'exit_{result.returncode}', step)
        return result.returncode

    def check_node(self, path, dev):
        try:
            info = os.stat(path)
        except OSError:
            return False
        return (stat.S_ISBLK(info.st_mode)
                and f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' == dev)

    def partition(self, disk_path):
        table = f'label: dos\nstart={PARTITION_START_SECTOR}, type=83\n'
        self.run('partition', ['sfdisk', '--wipe', 'always', '--wipe-partitions',
                               'always', str(disk_path)], table)

    def mkfs(self, part_path, label):
        self.run('mkfs', ['mkfs.ext4', '-F', '-q', '-L', label, '-m', '1', str(part_path)])

    def mount(self, part_path, target, readonly=False):
        options = 'ro,nodev' if readonly else 'nodev'
        self.run('mount', ['mount', '-t', 'ext4', '-o', options, str(part_path), str(target)])

    def umount(self, target):
        self.run('umount', ['umount', str(target)])

    def fsck(self, part_path):
        return subprocess.run(['e2fsck', '-fn', str(part_path)],
                              capture_output=True, text=True).returncode

    def sync(self):
        os.sync()


def wait_for_partition(disk_sysfs, part_name, timeout=20, sleep=time.sleep):
    part = Path(disk_sysfs) / part_name
    for _ in range(timeout * 4):
        if (part / 'dev').is_file():
            start = int(read_attr(part / 'start') or -1)
            if start != PARTITION_START_SECTOR:
                raise LayoutError('unexpected_partition_start', 'partition')
            return read_attr(part / 'dev')
        sleep(0.25)
    raise LayoutError('partition_did_not_appear', 'partition')


def lay_down(disk_path, disk_sysfs, part_name, archive, expected_seal, work_dir,
             ops=None, dev_root='/dev', mountinfo='/proc/self/mountinfo', log=print):
    """Partition, format, extract, and verify. Destroys everything on disk_path."""
    ops = ops or LinuxOps()
    receipt = {'steps': []}
    target = Path(work_dir)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(target.iterdir()) or os.path.ismount(target):
        raise LayoutError('work_dir_not_empty')
    mounted = False

    def step(name):
        receipt['steps'].append({'step': name, 'at': int(time.time())})
        log('PORTABLETAK_LAYOUT_STEP=' + name)

    try:
        step('partition')
        ops.partition(disk_path)
        part_dev = wait_for_partition(disk_sysfs, part_name)
        part_path = Path(dev_root) / part_name
        if not ops.check_node(part_path, part_dev):
            raise LayoutError('partition_node_mismatch', 'partition')
        step('format')
        ops.mkfs(part_path, FILESYSTEM_LABEL)
        found = read_ext_superblock(part_path)
        if not found or found[1] != FILESYSTEM_LABEL:
            raise LayoutError('filesystem_not_created', 'format')
        receipt.update(partition=part_name, ext_uuid=found[0], label=found[1])
        step('mount')
        ops.mount(part_path, target)
        mounted = True
        if sum(1 for line in Path(mountinfo).read_text().splitlines()
               if line.split()[2:3] == [part_dev]) != 1:
            raise LayoutError('filesystem_mounted_elsewhere', 'mount')
        step('extract')
        extract_payload(archive, target,
                        progress=lambda n: log(f'PORTABLETAK_LAYOUT_EXTRACTED={n}'))
        step('verify_written')
        verify_cartridge(target, expected_seal)
        step('unmount')
        ops.sync()
        ops.umount(target)
        mounted = False
        step('fsck')
        receipt['fsck_rc'] = ops.fsck(part_path)
        if receipt['fsck_rc'] != 0:
            raise LayoutError('filesystem_check_failed', 'fsck')
        step('verify_readback')
        ops.mount(part_path, target, readonly=True)
        mounted = True
        receipt['metadata'] = verify_cartridge(target, expected_seal)
        ops.umount(target)
        mounted = False
        step('complete')
        return receipt
    except LayoutError:
        raise
    except Exception as error:
        raise LayoutError(type(error).__name__, receipt['steps'][-1]['step']) from None
    finally:
        if mounted:
            try:
                ops.umount(target)
            except LayoutError:
                log('PORTABLETAK_LAYOUT_WARNING=unmount_failed_after_error')


def verify_cartridge(root, expected_seal):
    try:
        clean_base.verify_seal(Path(root), expected_seal)
    except clean_base.BaseError as error:
        raise LayoutError(str(error), 'verify') from None
    values = check_template_metadata(Path(root) / METADATA_PATH)
    return {key: values[key] for key in (
        'PORTABLETAK_ARCH', 'PORTABLETAK_CARTRIDGE_ID',
        'PORTABLETAK_INSTANCE_TYPE', 'PORTABLETAK_PROVISIONING_STATE')}


def write(name, confirm, archive, expected_archive_sha256, expected_seal,
          sys_root='/sys', dev_root='/dev', mountinfo='/proc/self/mountinfo',
          swaps='/proc/swaps', protected=None, replace_ext_uuid=None,
          work_root='/tmp/portabletak-layout', ops=None, log=print,
          min_bytes=MIN_DISK_BYTES, archive_summary=None):
    """Checked end-to-end layout. Nothing destructive happens before the
    confirmation matches a freshly recomputed fingerprint."""
    if not isinstance(confirm, str) or not re.fullmatch(r'ERASE [0-9a-f]{16}', confirm):
        raise LayoutError('confirmation_required')
    ops = ops or LinuxOps()
    kwargs = dict(dev_root=dev_root, mountinfo=mountinfo, swaps=swaps,
                  protected=protected, replace_ext_uuid=replace_ext_uuid,
                  min_bytes=min_bytes)
    before = plan(name, sys_root, **kwargs)
    if not before['eligible']:
        raise LayoutError('refused:' + ','.join(before['refusals']))
    if confirm != before['confirmation']:
        raise LayoutError('confirmation_does_not_match_disk')
    log('PORTABLETAK_LAYOUT_STEP=verify_archive')
    summary = archive_summary or verify_archive(
        archive, expected_archive_sha256, expected_seal,
        progress=lambda n: log(f'PORTABLETAK_LAYOUT_VERIFIED={n}'))
    # Archive verification takes minutes; the disk may have been swapped.
    after = plan(name, sys_root, **kwargs)
    if not after['eligible'] or after['confirmation'] != confirm:
        raise LayoutError('disk_changed_during_verification')
    identity = after['identity']
    disk_path = Path(dev_root) / name
    if not ops.check_node(disk_path, identity['dev']):
        raise LayoutError('disk_node_mismatch')
    receipt = {'format': 1, 'kind': 'portabletak-media-layout',
               'started_at': int(time.time()), 'host': os.uname().nodename,
               'identity': identity, 'warnings': after['warnings'],
               'replaced_filesystems': after['filesystems'], 'archive': summary}
    result = lay_down(disk_path, Path(sys_root) / 'block' / name, partition_name(name),
                      archive, expected_seal, Path(work_root) / identity['fingerprint'],
                      ops=ops, dev_root=dev_root, mountinfo=mountinfo, log=log)
    receipt.update(result, finished_at=int(time.time()), result='laid_out_state_new')
    return receipt
