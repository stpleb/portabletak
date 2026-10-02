from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import clean_base
import portabletak_provision as pt
from publish_identity import PublishError, publish_identity


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'payload'
        (self.root / 'portabletak').mkdir(parents=True)
        (self.root / 'software').write_text('verified')
        pt.write_metadata(self.root / 'portabletak/cartridge.conf', {
            'PORTABLETAK_FORMAT': '1', 'PORTABLETAK_ARCH': 'arm64',
            'PORTABLETAK_CARTRIDGE_ID': '00000000-0000-0000-0000-000000000000',
            'PORTABLETAK_INSTANCE_NAME': 'Unconfigured',
            'PORTABLETAK_INSTANCE_TYPE': 'generic',
            'PORTABLETAK_PROVISIONING_STATE': 'new',
        })
        provenance = {'ubuntu_archive': 'base.tar.gz', 'ubuntu_sha256': 'a' * 64,
                      'apt_packages': {'python3': 'test'},
                      'python_packages': {'opentakserver': '1.7.13'}}
        self.seal = clean_base.seal(self.root, provenance)
        self.lock = Path(self.temp.name) / 'publish.lock'

    def tearDown(self):
        self.temp.cleanup()

    def publish(self, value):
        return publish_identity(self.root, self.seal, lock=self.lock,
                                require_mount=False, identity_factory=lambda: value)

    def test_publishes_once_and_preserves_software_seal(self):
        expected = uuid.UUID('11111111-1111-4111-8111-111111111111')
        self.assertEqual(self.publish(expected), str(expected))
        self.assertEqual(pt.metadata(self.root / 'portabletak/cartridge.conf')['PORTABLETAK_CARTRIDGE_ID'], str(expected))
        self.assertTrue(clean_base.verify_seal(self.root, self.seal))
        with self.assertRaisesRegex(PublishError, 'identity_already_published'):
            self.publish(uuid.uuid4())

    def test_software_tamper_refused_before_identity_write(self):
        (self.root / 'software').write_text('changed')
        with self.assertRaisesRegex(PublishError, 'base_content_changed'):
            self.publish(uuid.uuid4())
        values = pt.metadata(self.root / 'portabletak/cartridge.conf')
        self.assertEqual(uuid.UUID(values['PORTABLETAK_CARTRIDGE_ID']).int, 0)

    def test_controller_refuses_unpublished_template(self):
        (self.root / 'rootfs/usr/share/zoneinfo/Etc').mkdir(parents=True)
        (self.root / 'rootfs/usr/share/zoneinfo/Etc/UTC').write_bytes(b'TZif' + b'\0' * 20)
        pt.write_json(self.root / 'portabletak/base.json',
                      {'format': 1, 'kind': 'portabletak-clean-base', 'architecture': 'arm64'})
        controller = pt.Controller(self.root, self.lock, require_mount=False)
        with self.assertRaisesRegex(pt.ProvisionError, 'unpublished_cartridge_identity'):
            controller.stage({'instance_name': 'Test', 'timezone': 'Etc/UTC',
                              'administrator_password': 'Administrator-123',
                              'client_password': 'Client-password-456'})


if __name__ == '__main__':
    unittest.main()
