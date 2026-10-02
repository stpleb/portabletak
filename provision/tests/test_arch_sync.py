import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import arch_sync
import host_profile


class ArchTests(unittest.TestCase):
    def test_rootfs_selection(self):
        self.assertEqual(host_profile.host_arch('aarch64'), 'arm64')
        self.assertEqual(host_profile.host_arch('x86_64'), 'x86_64')
        self.assertEqual(host_profile.host_arch('AMD64'), 'x86_64')
        self.assertEqual(host_profile.rootfs('/m', 'aarch64'), Path('/m/rootfs'))
        self.assertEqual(host_profile.rootfs('/m', 'x86_64'), Path('/m/arch/amd64/rootfs'))

    def cartridge(self):
        root = Path(tempfile.mkdtemp())
        a = root / 'rootfs'
        (a / 'etc/rabbitmq').mkdir(parents=True)
        (a / 'etc/rabbitmq/rabbitmq.conf').write_text('listeners.tcp.default = 127.0.0.1:5672\n')
        os.chmod(a / 'etc/rabbitmq/rabbitmq.conf', 0o640)
        (a / 'etc/nginx/sites-available').mkdir(parents=True)
        (a / 'etc/nginx/sites-enabled').mkdir(parents=True)
        (a / 'etc/nginx/sites-available/portabletak').write_text('listen 8443 ssl;\n')
        (a / 'etc/nginx/sites-enabled/portabletak').symlink_to('/etc/nginx/sites-available/portabletak')
        (a / 'var/log/rabbitmq').mkdir(parents=True, mode=0o750)
        (a / 'var/log/nginx').mkdir(parents=True)
        b = root / 'arch/amd64/rootfs'
        (b / 'etc/nginx/sites-enabled').mkdir(parents=True)
        (b / 'etc/nginx/sites-available').mkdir(parents=True)
        (b / 'etc/nginx/sites-available/portabletak').write_text('old\n')
        return root, a, b

    def test_x86_host_gets_config_copy(self):
        root, a, b = self.cartridge()
        done = arch_sync.sync(root, 'x86_64')
        self.assertIn('etc/rabbitmq', done)
        self.assertEqual((b / 'etc/rabbitmq/rabbitmq.conf').read_text(), (a / 'etc/rabbitmq/rabbitmq.conf').read_text())
        self.assertEqual(os.stat(b / 'etc/rabbitmq/rabbitmq.conf').st_mode & 0o777, 0o640)
        self.assertEqual((b / 'etc/nginx/sites-available/portabletak').read_text(), 'listen 8443 ssl;\n')
        self.assertEqual(os.readlink(b / 'etc/nginx/sites-enabled/portabletak'), '/etc/nginx/sites-available/portabletak')
        self.assertTrue((b / 'var/log/rabbitmq').is_dir())
        self.assertEqual(arch_sync.sync(root, 'x86_64'), done)  # idempotent

    def test_arm_host_and_single_chip_are_noops(self):
        root, a, b = self.cartridge()
        self.assertEqual(arch_sync.sync(root, 'aarch64'), [])
        single = Path(tempfile.mkdtemp())
        (single / 'rootfs').mkdir()
        self.assertEqual(arch_sync.sync(single, 'x86_64'), [])


if __name__ == '__main__':
    unittest.main()
