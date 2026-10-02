from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import grow_cartridge as gc


def fake_sys(parts, disk_sectors):
    root = Path(tempfile.mkdtemp())
    disk = root / 'devices/sda'
    disk.mkdir(parents=True)
    (disk / 'size').write_text(str(disk_sectors))
    (root / 'class/block').mkdir(parents=True)
    for n, (start, size) in enumerate(parts, 1):
        p = disk / f'sda{n}'
        p.mkdir()
        (p / 'partition').write_text(str(n)); (p / 'start').write_text(str(start)); (p / 'size').write_text(str(size))
        (root / 'class/block' / f'sda{n}').symlink_to(p)
    return root


class GrowTests(unittest.TestCase):
    def test_last_partition_with_space_grows(self):
        sys_root = fake_sys([(2048, 2097152), (2099200, 16775168)], 240253440)
        info = gc.plan('sda2', sys_root)
        self.assertTrue(info['grow'])
        self.assertEqual(info['number'], 2)
        self.assertGreater(info['gain_bytes'], 100 * 1024 ** 3)

    def test_not_last_or_full_is_left_alone(self):
        sys_root = fake_sys([(2048, 2097152), (2099200, 16775168)], 240253440)
        self.assertFalse(gc.plan('sda1', sys_root)['grow'])
        full = fake_sys([(2048, 240251392)], 240253440)
        self.assertFalse(gc.plan('sda1', full)['grow'])

    def test_resize2fs_uses_cartridge_loader(self):
        root = Path(tempfile.mkdtemp())
        (root / 'rootfs/lib').mkdir(parents=True); (root / 'rootfs/usr/sbin').mkdir(parents=True)
        (root / 'rootfs/lib/ld-linux-aarch64.so.1').write_text('')
        (root / 'rootfs/usr/sbin/resize2fs').write_text('')
        cmd = gc.resize2fs_command(root, 'aarch64')
        self.assertTrue(cmd[0].endswith('rootfs/lib/ld-linux-aarch64.so.1'))
        self.assertTrue(cmd[-1].endswith('rootfs/usr/sbin/resize2fs'))
        with self.assertRaises(FileNotFoundError):
            gc.resize2fs_command(root, 'x86_64')


if __name__ == '__main__':
    unittest.main()
