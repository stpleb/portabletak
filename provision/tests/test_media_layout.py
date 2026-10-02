import hashlib
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import clean_base
import media_layout as layout
from media_layout import LayoutError

ASGARD = '1f501ae2-54f0-460d-a000-bce2fcc78c20'
NIL_CONF = ('PORTABLETAK_FORMAT=1\nPORTABLETAK_ARCH=arm64\n'
            'PORTABLETAK_CARTRIDGE_ID=00000000-0000-0000-0000-000000000000\n'
            'PORTABLETAK_INSTANCE_NAME=Unconfigured\nPORTABLETAK_INSTANCE_TYPE=generic\n'
            'PORTABLETAK_PROVISIONING_STATE=new\n')
PROVENANCE = {'ubuntu_archive': 'upstream.tar.gz', 'ubuntu_sha256': 'a' * 64,
              'apt_packages': {'python3': 'test'}, 'python_packages': {'opentakserver': '1.7.13'}}


def superblock(fs_uuid, label='DATA'):
    data = bytearray(4096)
    data[1024 + 0x38:1024 + 0x3A] = layout.EXT_MAGIC.to_bytes(2, 'little')
    data[1024 + 0x68:1024 + 0x78] = uuid.UUID(fs_uuid).bytes
    data[1024 + 0x78:1024 + 0x78 + len(label)] = label.encode()
    return bytes(data)


class FakeHost:
    """A sysfs/dev/proc tree shaped like Node01's USB layout."""

    def __init__(self, root, usb=True, serial='0000000000000086', sectors=243404800,
                 port='1-1.2', partitions=('sdb1',)):
        self.root = root
        self.sys = root / 'sys'
        self.dev = root / 'dev'
        self.dev.mkdir(parents=True)
        bus = self.sys / 'devices/platform/scb/usb1/1-1' / port if usb else self.sys / 'devices/platform/ahci/ata1'
        disk = bus / f'{port}:1.0/host1/target1:0:0/1:0:0:0/block/sdb'
        (disk / 'device').mkdir(parents=True)
        (disk / 'holders').mkdir()
        if usb:
            for name, value in (('idVendor', '21c4'), ('idProduct', '0809'), ('serial', serial),
                                ('manufacturer', 'Lexar'), ('product', 'USB Flash Drive'),
                                ('speed', '480')):
                (bus / name).write_text(value + '\n')
        (disk / 'size').write_text(f'{sectors}\n')
        (disk / 'dev').write_text('8:16\n')
        (disk / 'removable').write_text('0\n')
        (disk / 'device/vendor').write_text('Lexar   \n')
        (disk / 'device/model').write_text('USB Flash Drive \n')
        for index, name in enumerate(partitions, 1):
            (disk / name).mkdir()
            (disk / name / 'partition').write_text(f'{index}\n')
            (disk / name / 'start').write_text('2048\n')
            (disk / name / 'size').write_text(f'{sectors - 2048}\n')
            (disk / name / 'dev').write_text(f'8:{16 + index}\n')
            (self.dev / name).write_bytes(bytes(4096))
        (self.sys / 'block').mkdir(parents=True)
        (self.sys / 'block/sdb').symlink_to(disk)
        (self.dev / 'sdb').write_bytes(bytes(4096))
        self.disk = disk
        self.mountinfo = root / 'mountinfo'
        self.mountinfo.write_text('22 1 0:20 / / rw - overlay overlayfs:/overlay rw\n'
                                  '23 22 179:2 / /rom ro - squashfs /dev/root ro\n')
        self.swaps = root / 'swaps'
        self.swaps.write_text('Filename Type Size Used Priority\n')

    def kwargs(self, **extra):
        values = dict(dev_root=self.dev, mountinfo=self.mountinfo, swaps=self.swaps)
        values.update(extra)
        return values


class FakeOps:
    """Records calls. Its mkfs writes a real superblock into the fake node."""

    def __init__(self, host, fs_uuid='2b0a8a8e-0000-4000-8000-000000000001',
                 fail_at=None, fsck_rc=0):
        self.host, self.fs_uuid, self.fail_at, self.fsck_rc = host, fs_uuid, fail_at, fsck_rc
        self.calls = []
        self.mounted = None
        self.media = host.root / 'media'

    def _step(self, name):
        self.calls.append(name)
        if self.fail_at == name:
            raise LayoutError('injected', name)

    def check_node(self, path, dev):
        self.calls.append('check_node:' + Path(path).name)
        return True

    def partition(self, disk_path):
        self._step('partition')

    def mkfs(self, part_path, label):
        self._step('mkfs')
        Path(part_path).write_bytes(superblock(self.fs_uuid, label))
        if self.media.exists():
            shutil.rmtree(self.media)
        (self.media / 'lost+found').mkdir(parents=True)

    def mount(self, part_path, target, readonly=False):
        self._step('mount_ro' if readonly else 'mount')
        # Model a mount by moving the media tree onto the target directory.
        Path(target).rmdir()
        self.media.rename(target)
        self.mounted = Path(target)
        with self.host.mountinfo.open('a') as stream:
            stream.write(f'40 22 8:17 / {target} rw - ext4 /dev/sdb1 rw\n')

    def umount(self, target):
        self._step('umount')
        self.mounted.rename(self.media)
        Path(target).mkdir()
        lines = self.host.mountinfo.read_text().splitlines(True)
        self.host.mountinfo.write_text(''.join(l for l in lines if ' 8:17 ' not in l))

    def fsck(self, part_path):
        self._step('fsck')
        return self.fsck_rc

    def sync(self):
        pass


class Payload:
    """A small sealed payload with the features that broke naive extraction."""

    def __init__(self, root):
        self.tree = root / 'build/payload'
        rootfs = self.tree / 'rootfs'
        (rootfs / 'usr/bin').mkdir(parents=True)
        (rootfs / 'usr/bin/tool').write_text('tool')
        (rootfs / 'bin').symlink_to('usr/bin')
        (rootfs / 'usr/bin/python').symlink_to('/usr/bin/python3.12')  # absolute
        (rootfs / 'usr/bin/python3.12').write_text('python')
        os.link(rootfs / 'usr/bin/tool', rootfs / 'usr/bin/tool-alias')
        (rootfs / 'usr/bin/su').write_text('su')
        os.chmod(rootfs / 'usr/bin/su', 0o4755)
        self.setuid = bool(os.stat(rootfs / 'usr/bin/su').st_mode & 0o4000)
        (self.tree / 'opentak').mkdir()
        (self.tree / 'portabletak').mkdir()
        (self.tree / 'portabletak/cartridge.conf').write_text(NIL_CONF)
        (self.tree / 'portabletak/base.json').write_text(
            '{"architecture": "arm64", "format": 1, "kind": "portabletak-clean-base"}\n')
        self.seal = clean_base.seal(self.tree, PROVENANCE)
        self.archive = root / 'base.tar.gz'
        self.pack()

    def pack(self):
        with tarfile.open(self.archive, 'w:gz') as archive:
            archive.add(self.tree, arcname='payload')
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.lines = []

    def tearDown(self):
        self.temp.cleanup()


class IdentityTests(Base):
    def test_usb_identity_and_stable_fingerprint(self):
        host = FakeHost(self.root)
        identity = layout.disk_identity('sdb', host.sys)
        self.assertEqual(identity['usb']['usb_port'], '1-1.2')
        self.assertEqual(identity['usb']['id_vendor'], '21c4')
        self.assertEqual(identity['size_bytes'], 243404800 * 512)
        self.assertEqual(identity['model'], 'USB Flash Drive')
        self.assertEqual(identity['partitions'][0]['name'], 'sdb1')
        self.assertEqual(identity['fingerprint'], layout.disk_identity('sdb', host.sys)['fingerprint'])

    def test_fingerprint_distinguishes_identical_drives_by_port(self):
        a = layout.disk_identity('sdb', FakeHost(self.root / 'a', port='1-1.2').sys)
        b = layout.disk_identity('sdb', FakeHost(self.root / 'b', port='1-1.3').sys)
        self.assertNotEqual(a['fingerprint'], b['fingerprint'])

    def test_partition_name_rejected_as_target(self):
        host = FakeHost(self.root)
        (host.sys / 'class/block/sdb1').mkdir(parents=True)
        (host.sys / 'class/block/sdb1/partition').write_text('1\n')
        with self.assertRaisesRegex(LayoutError, 'not_whole_disk'):
            layout.disk_identity('sdb1', host.sys)

    def test_path_like_name_rejected(self):
        with self.assertRaisesRegex(LayoutError, 'invalid_disk_name'):
            layout.disk_identity('../sdb', self.root)

    def test_nvme_and_mmc_partition_names(self):
        self.assertEqual(layout.partition_name('sdb'), 'sdb1')
        self.assertEqual(layout.partition_name('mmcblk0'), 'mmcblk0p1')


class AssessTests(Base):
    def assess(self, host, **extra):
        identity = layout.disk_identity('sdb', host.sys)
        return layout.assess(identity, **host.kwargs(**extra))

    def test_blank_usb_is_eligible_with_weak_serial_warning(self):
        refusals, warnings, _ = self.assess(FakeHost(self.root))
        self.assertEqual(refusals, [])
        self.assertIn('weak_usb_serial_use_port_and_size', warnings)

    def test_strong_serial_has_no_warning(self):
        _, warnings, _ = self.assess(FakeHost(self.root, serial='AA1B2C3D4E5F6071'))
        self.assertEqual(warnings, [])

    def test_non_usb_disk_refused(self):
        refusals, _, _ = self.assess(FakeHost(self.root, usb=False))
        self.assertIn('not_usb_attached', refusals)

    def test_mounted_partition_refused_by_device_number(self):
        host = FakeHost(self.root)
        with host.mountinfo.open('a') as stream:
            stream.write('30 22 8:17 / /mnt/sdb1 rw - vfat /dev/sdb1 rw\n')
        self.assertIn('disk_in_use_mounted', self.assess(host)[0])

    def test_swap_refused(self):
        host = FakeHost(self.root)
        host.swaps.write_text(f'Filename Type\n{host.dev}/sdb1 partition 1 0 -2\n')
        self.assertIn('disk_in_use_swap', self.assess(host)[0])

    def test_holders_refused(self):
        host = FakeHost(self.root)
        (host.disk / 'holders/dm-0').mkdir()
        self.assertIn('disk_has_holders', self.assess(host)[0])

    def test_small_disk_refused(self):
        self.assertIn('disk_too_small', self.assess(FakeHost(self.root, sectors=4 * 1024 ** 2))[0])

    def test_asgard_refused_even_when_named_for_replacement(self):
        host = FakeHost(self.root)
        (host.dev / 'sdb1').write_bytes(superblock(ASGARD, 'asgard'))
        refusals, _, found = self.assess(host, replace_ext_uuid=ASGARD)
        self.assertIn('protected_filesystem:' + ASGARD, refusals)
        self.assertEqual(found[0]['ext_uuid'], ASGARD)

    def test_existing_ext4_refused_unless_named(self):
        host = FakeHost(self.root)
        other = '3d6f0f5e-1111-4222-8333-444455556666'
        (host.dev / 'sdb1').write_bytes(superblock(other))
        self.assertIn('existing_linux_filesystem:' + other, self.assess(host)[0])
        self.assertEqual(self.assess(host, replace_ext_uuid=other)[0], [])

    def test_superfloppy_ext4_without_partition_table_detected(self):
        host = FakeHost(self.root, partitions=())
        (host.dev / 'sdb').write_bytes(superblock(ASGARD))
        self.assertIn('protected_filesystem:' + ASGARD, self.assess(host)[0])

    def test_unreadable_partition_fails_closed(self):
        host = FakeHost(self.root)
        (host.dev / 'sdb1').unlink()
        self.assertIn('unreadable:sdb1', self.assess(host)[0])

    def test_protected_list_file_extends_builtin(self):
        path = self.root / 'protected'
        path.write_text('# lab cartridge\n3D6F0F5E-1111-4222-8333-444455556666\n')
        protected = layout.protected_filesystems(path)
        self.assertIn(ASGARD, protected)
        self.assertIn('3d6f0f5e-1111-4222-8333-444455556666', protected)
        path.write_text('not-a-uuid\n')
        with self.assertRaisesRegex(LayoutError, 'invalid_protected_filesystem_list'):
            layout.protected_filesystems(path)

    def test_plan_only_offers_confirmation_when_eligible(self):
        host = FakeHost(self.root, usb=False)
        result = layout.plan('sdb', host.sys, **host.kwargs())
        self.assertFalse(result['eligible'])
        self.assertIsNone(result['confirmation'])


class ArchiveTests(Base):
    def test_valid_archive_matches_seal(self):
        payload = Payload(self.root)
        summary = layout.verify_archive(payload.archive, payload.sha, payload.seal)
        self.assertEqual(summary['architecture'], 'arm64')

    def test_archive_checksum_pinned(self):
        payload = Payload(self.root)
        with self.assertRaisesRegex(LayoutError, 'archive_checksum_mismatch'):
            layout.verify_archive(payload.archive, '0' * 64, payload.seal)

    def test_seal_pinned(self):
        payload = Payload(self.root)
        with self.assertRaisesRegex(LayoutError, 'embedded_seal_mismatch'):
            layout.verify_archive(payload.archive, payload.sha, '0' * 64)

    def test_changed_file_detected(self):
        payload = Payload(self.root)
        (payload.tree / 'rootfs/usr/bin/python3.12').write_text('modified')
        payload.pack()
        with self.assertRaisesRegex(LayoutError, 'archive_differs_from_seal'):
            layout.verify_archive(payload.archive, payload.sha, payload.seal)

    def test_added_file_detected(self):
        payload = Payload(self.root)
        (payload.tree / 'opentak/extra').write_text('x')
        payload.pack()
        with self.assertRaisesRegex(LayoutError, 'archive_differs_from_seal'):
            layout.verify_archive(payload.archive, payload.sha, payload.seal)

    def test_published_identity_in_archive_refused(self):
        payload = Payload(self.root)
        conf = payload.tree / 'portabletak/cartridge.conf'
        conf.write_text(NIL_CONF.replace('00000000-0000-0000-0000-000000000000', str(uuid.uuid4())))
        payload.pack()
        with self.assertRaisesRegex(LayoutError, 'template_metadata_not_blank'):
            layout.verify_archive(payload.archive, payload.sha, payload.seal)

    def test_member_outside_payload_refused(self):
        payload = Payload(self.root)
        with tarfile.open(payload.archive, 'w:gz') as archive:
            archive.add(payload.tree, arcname='payload')
            archive.add(payload.tree / 'opentak', arcname='other')
        sha = hashlib.sha256(payload.archive.read_bytes()).hexdigest()
        with self.assertRaisesRegex(LayoutError, 'archive_outside_payload'):
            layout.verify_archive(payload.archive, sha, payload.seal)

    def test_faithful_extraction_verifies_beside_lost_found(self):
        payload = Payload(self.root)
        target = self.root / 'mnt'
        (target / 'lost+found').mkdir(parents=True)
        layout.extract_payload(payload.archive, target)
        self.assertEqual(os.readlink(target / 'rootfs/usr/bin/python'), '/usr/bin/python3.12')
        self.assertEqual(os.stat(target / 'rootfs/usr/bin/tool').st_ino,
                         os.stat(target / 'rootfs/usr/bin/tool-alias').st_ino)
        if payload.setuid:
            self.assertTrue(os.stat(target / 'rootfs/usr/bin/su').st_mode & 0o4000)
        self.assertTrue(clean_base.verify_seal(target, payload.seal))
        (target / 'lost+found/#12').write_text('recovered by e2fsck')
        self.assertTrue(clean_base.verify_seal(target, payload.seal))

    def test_extraction_refuses_used_destination(self):
        payload = Payload(self.root)
        target = self.root / 'mnt'
        target.mkdir()
        (target / 'old').write_text('x')
        with self.assertRaisesRegex(LayoutError, 'destination_not_empty'):
            layout.extract_payload(payload.archive, target)

    def test_nested_lost_found_is_still_sealed(self):
        payload = Payload(self.root)
        target = self.root / 'mnt'
        target.mkdir()
        layout.extract_payload(payload.archive, target)
        (target / 'opentak/lost+found').mkdir()
        with self.assertRaisesRegex(clean_base.BaseError, 'base_content_changed'):
            clean_base.verify_seal(target, payload.seal)


class WriteTests(Base):
    def setUp(self):
        super().setUp()
        self.host = FakeHost(self.root / 'host')
        self.payload = Payload(self.root)
        self.confirm = layout.plan('sdb', self.host.sys, **self.host.kwargs())['confirmation']

    def write(self, ops, confirm=None, **extra):
        return layout.write('sdb', confirm or self.confirm, self.payload.archive,
                            self.payload.sha, self.payload.seal, sys_root=self.host.sys,
                            work_root=self.root / 'work', ops=ops, log=self.lines.append,
                            **self.host.kwargs(**extra))

    def test_complete_layout_order_and_receipt(self):
        ops = FakeOps(self.host)
        receipt = self.write(ops)
        self.assertEqual(ops.calls, ['check_node:sdb', 'partition', 'check_node:sdb1', 'mkfs',
                                     'mount', 'umount', 'fsck', 'mount_ro', 'umount'])
        self.assertEqual(receipt['result'], 'laid_out_state_new')
        self.assertEqual(receipt['label'], 'PORTABLETAK')
        self.assertEqual(receipt['metadata']['PORTABLETAK_PROVISIONING_STATE'], 'new')
        self.assertEqual(receipt['metadata']['PORTABLETAK_CARTRIDGE_ID'],
                         '00000000-0000-0000-0000-000000000000')
        self.assertTrue(clean_base.verify_seal(ops.media, self.payload.seal))
        self.assertNotIn('password', repr(receipt).lower())

    def test_wrong_or_missing_confirmation_touches_nothing(self):
        for confirm in ('ERASE 0000000000000000', 'yes', ''):
            ops = FakeOps(self.host)
            with self.assertRaises(LayoutError):
                layout.write('sdb', confirm, self.payload.archive, self.payload.sha,
                             self.payload.seal, sys_root=self.host.sys, ops=ops,
                             work_root=self.root / 'work', log=self.lines.append,
                             **self.host.kwargs())
            self.assertEqual(ops.calls, [])

    def test_refused_disk_touches_nothing(self):
        with self.host.mountinfo.open('a') as stream:
            stream.write('30 22 8:17 / /mnt/sdb1 rw - vfat /dev/sdb1 rw\n')
        ops = FakeOps(self.host)
        with self.assertRaisesRegex(LayoutError, 'disk_in_use_mounted'):
            self.write(ops)
        self.assertEqual(ops.calls, [])

    def test_bad_archive_touches_nothing(self):
        ops = FakeOps(self.host)
        with self.assertRaisesRegex(LayoutError, 'archive_checksum_mismatch'):
            layout.write('sdb', self.confirm, self.payload.archive, '1' * 64,
                         self.payload.seal, sys_root=self.host.sys, ops=ops,
                         work_root=self.root / 'work', log=self.lines.append,
                         **self.host.kwargs())
        self.assertEqual(ops.calls, [])

    def test_disk_swapped_during_archive_check_refused(self):
        ops = FakeOps(self.host)
        real = layout.verify_archive

        def swap_then_verify(*args, **kwargs):
            (self.host.disk / 'size').write_text('300000000\n')
            return real(*args, **kwargs)
        layout.verify_archive = swap_then_verify
        try:
            with self.assertRaisesRegex(LayoutError, 'disk_changed_during_verification'):
                self.write(ops)
        finally:
            layout.verify_archive = real
        self.assertEqual(ops.calls, [])

    def test_failed_fsck_is_not_complete(self):
        ops = FakeOps(self.host, fsck_rc=4)
        with self.assertRaisesRegex(LayoutError, 'fsck:filesystem_check_failed'):
            self.write(ops)
        self.assertNotIn('PORTABLETAK_LAYOUT_STEP=complete', self.lines)

    def test_interrupted_extract_leaves_no_metadata_and_unmounts(self):
        ops = FakeOps(self.host)
        real = layout.extract_payload

        def partial(archive, target, progress=None):
            (Path(target) / 'rootfs').mkdir()
            raise OSError('usb disconnect')
        layout.extract_payload = partial
        try:
            with self.assertRaisesRegex(LayoutError, 'extract:OSError'):
                self.write(ops)
        finally:
            layout.extract_payload = real
        self.assertEqual(ops.calls[-1], 'umount')
        self.assertFalse((ops.media / 'portabletak/cartridge.conf').exists())

    def test_filesystem_mounted_twice_refused(self):
        ops = FakeOps(self.host)
        original = ops.mount

        def double(part_path, target, readonly=False):
            original(part_path, target, readonly)
            with self.host.mountinfo.open('a') as stream:
                stream.write('41 22 8:17 / /mnt/auto rw - ext4 /dev/sdb1 rw\n')
        ops.mount = double
        with self.assertRaisesRegex(LayoutError, 'mount:filesystem_mounted_elsewhere'):
            self.write(ops)

    def test_partition_start_checked(self):
        ops = FakeOps(self.host)
        (self.host.disk / 'sdb1/start').write_text('63\n')
        with self.assertRaisesRegex(LayoutError, 'unexpected_partition_start'):
            self.write(ops)


if __name__ == '__main__':
    unittest.main()
