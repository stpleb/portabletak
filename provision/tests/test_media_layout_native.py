"""Loop-device layout test. Root Linux only; never points at a real disk.

Run with PORTABLETAK_LAYOUT_ARCHIVE set to the pinned clean-base archive. The
image is sparse and the loop device is detached afterwards. The hardware
identity policy is covered by test_media_layout; this exercises the real
sfdisk, mkfs.ext4, extraction, e2fsck, seal verification, and publisher.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import media_layout as layout
from portabletak_provision import Controller, metadata
from publish_identity import PublishError, publish_identity
from release import expected_base_archive, expected_base_seal

ARCHIVE = os.environ.get('PORTABLETAK_LAYOUT_ARCHIVE')


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid() == 0 and ARCHIVE,
                     'root Linux with PORTABLETAK_LAYOUT_ARCHIVE required')
class LoopLayoutTest(unittest.TestCase):
    def test_layout_publish_and_preflight(self):
        seal = expected_base_seal()
        summary = layout.verify_archive(ARCHIVE, expected_base_archive(), seal)
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            work = Path(temporary)
            image = work / 'disk.img'
            with image.open('wb') as stream:
                stream.truncate(4 * 1024 ** 3)
            loop = subprocess.run(['losetup', '-f', '--show', '-P', str(image)],
                                  check=True, capture_output=True, text=True).stdout.strip()
            name = Path(loop).name
            mount = work / 'asgard'
            try:
                receipt = layout.lay_down(
                    Path(loop), Path('/sys/block') / name, layout.partition_name(name),
                    ARCHIVE, seal, work / 'layout', log=print)
                self.assertEqual(receipt['fsck_rc'], 0)
                self.assertEqual(receipt['metadata']['PORTABLETAK_PROVISIONING_STATE'], 'new')
                part = Path('/dev') / layout.partition_name(name)
                mount.mkdir()
                subprocess.run(['mount', '-t', 'ext4', str(part), str(mount)], check=True)
                try:
                    lock = work / 'lifecycle.lock'
                    sealed = json.loads((mount / 'portabletak/base-seal.json').read_text())['files']
                    special = [k for k, v in sealed.items() if v['kind'] == 'file' and v['mode'] & 0o6000]
                    self.assertTrue(special)
                    for relative in special:
                        self.assertEqual(os.stat(mount / relative).st_mode & 0o7777,
                                         sealed[relative]['mode'], relative)
                    self.assertTrue(os.path.islink(mount / 'rootfs/bin'))
                    cartridge = publish_identity(mount, seal, lock=lock)
                    self.assertNotEqual(cartridge, '00000000-0000-0000-0000-000000000000')
                    with self.assertRaisesRegex(PublishError, 'identity_already_published'):
                        publish_identity(mount, seal, lock=lock)
                    values = metadata(mount / 'portabletak/cartridge.conf')
                    self.assertEqual(values['PORTABLETAK_CARTRIDGE_ID'], cartridge)
                    controller = Controller(mount, lock=lock, expected_base_seal=seal)
                    self.assertEqual(controller.preflight()['PORTABLETAK_CARTRIDGE_ID'], cartridge)
                    status = controller.status()
                    self.assertEqual((status['state'], status['can_stage']), ('new', True))
                    print('PORTABLETAK_NATIVE_LAYOUT=PASS', summary['members'], cartridge[:8])
                finally:
                    subprocess.run(['umount', str(mount)], check=True)
                self.assertEqual(subprocess.run(['e2fsck', '-fn', str(part)],
                                                capture_output=True).returncode, 0)
            finally:
                subprocess.run(['losetup', '-d', loop], check=False)


if __name__ == '__main__':
    unittest.main()
