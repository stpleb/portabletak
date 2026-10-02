import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import clean_base as base
import factory_reset
from portabletak_provision import ProvisionError, metadata, write_metadata

BLANK = {'PORTABLETAK_FORMAT': '1', 'PORTABLETAK_ARCH': 'arm64',
         'PORTABLETAK_CARTRIDGE_ID': '00000000-0000-0000-0000-000000000000',
         'PORTABLETAK_INSTANCE_NAME': 'Unconfigured', 'PORTABLETAK_INSTANCE_TYPE': 'generic',
         'PORTABLETAK_PROVISIONING_STATE': 'new'}
NO_MOUNTS = lambda root: []


class FactoryResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'tree'
        r = self.root
        (r / 'portabletak').mkdir(parents=True)
        write_metadata(r / 'portabletak/cartridge.conf', BLANK)
        (r / 'rootfs/usr/bin').mkdir(parents=True)
        (r / 'rootfs/usr/bin/tool').write_text('tool')
        (r / 'rootfs/etc/nginx/sites-available').mkdir(parents=True)
        (r / 'rootfs/run').mkdir()
        (r / 'rootfs/bin').symlink_to('usr/bin')
        for d in ('postgres', 'rabbitmq', 'opentak/data', 'config', 'arch/amd64/rootfs/etc'):
            (r / d).mkdir(parents=True)
        self.seal = base.seal(r, {'ubuntu_archive': 'u.tar.gz', 'ubuntu_sha256': 'a' * 64,
                                  'apt_packages': {'x': '1'}, 'python_packages': {'y': '1'}})
        # Provision it: data everywhere, config in the rootfs, changed modes, new identity.
        (r / 'postgres/PG_VERSION').write_text('16')
        (r / 'opentak/data/ca').mkdir()
        (r / 'opentak/data/ca/ca-do-not-share.key').write_text('KEY')
        (r / 'config/tailscale').mkdir()
        (r / 'config/server-addresses.json').write_text('{}')
        (r / 'rootfs/etc/nginx/sites-available/portabletak').write_text('site')
        (r / 'arch/amd64/rootfs/etc/rabbitmq').mkdir()
        (r / 'rootfs/run/postgresql').mkdir()
        os.chmod(r / 'postgres', 0o700)
        (r / 'portabletak/backups').mkdir()
        (r / 'portabletak/backups/b.tar.gz').write_text('backup')
        write_metadata(r / 'portabletak/cartridge.conf', dict(
            BLANK, PORTABLETAK_CARTRIDGE_ID='69afec99-44fb-4631-9872-496534c3ce0b',
            PORTABLETAK_INSTANCE_NAME='TestTAK', PORTABLETAK_PROVISIONING_STATE='provisioned'))

    def tearDown(self):
        self.temp.cleanup()

    def test_provisioned_drive_returns_to_blank(self):
        result = factory_reset.reset(self.root, mounts=NO_MOUNTS)
        self.assertTrue(base.verify_seal(self.root, self.seal))
        self.assertEqual(result['seal_sha256'], self.seal)
        values = metadata(self.root / 'portabletak/cartridge.conf')
        self.assertEqual(values['PORTABLETAK_PROVISIONING_STATE'], 'new')
        self.assertEqual(values['PORTABLETAK_INSTANCE_NAME'], 'Unconfigured')
        self.assertNotEqual(values['PORTABLETAK_CARTRIDGE_ID'], '69afec99-44fb-4631-9872-496534c3ce0b')
        for gone in ('postgres/PG_VERSION', 'opentak/data/ca', 'config/tailscale',
                     'rootfs/etc/nginx/sites-available/portabletak', 'portabletak/backups',
                     'arch/amd64/rootfs/etc/rabbitmq', 'rootfs/run/postgresql'):
            self.assertFalse((self.root / gone).exists(), gone)
        self.assertEqual((self.root / 'rootfs/usr/bin/tool').read_text(), 'tool')

    def test_damaged_software_refused_before_deleting(self):
        (self.root / 'rootfs/usr/bin/tool').write_text('changed')
        with self.assertRaisesRegex(ProvisionError, 'reset_software_damaged:rootfs/usr/bin/tool'):
            factory_reset.reset(self.root, mounts=NO_MOUNTS)
        self.assertTrue((self.root / 'postgres/PG_VERSION').exists())

    def test_running_cartridge_refused(self):
        with self.assertRaisesRegex(ProvisionError, 'reset_requires_stopped_cartridge'):
            factory_reset.reset(self.root, mounts=lambda root: [str(root) + '/rootfs/dev'])

    def test_non_generic_refused(self):
        write_metadata(self.root / 'portabletak/cartridge.conf', dict(BLANK, PORTABLETAK_INSTANCE_TYPE='asgard'))
        with self.assertRaisesRegex(ProvisionError, 'reset_refused_not_generic'):
            factory_reset.reset(self.root, mounts=NO_MOUNTS)
        self.assertTrue((self.root / 'postgres/PG_VERSION').exists())


if __name__ == '__main__':
    unittest.main()
