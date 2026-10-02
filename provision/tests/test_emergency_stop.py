import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from emergency_stop import belongs_to_cartridge
class EmergencyStopTests(unittest.TestCase):
    def test_chroot_process(self):
        self.assertTrue(belongs_to_cartridge('/mnt/asgard/rootfs', '/usr/bin/python3'))
    def test_deleted_root(self):
        self.assertTrue(belongs_to_cartridge('/mnt/asgard/rootfs (deleted)', '/usr/bin/python3'))
    def test_host_mediamtx(self):
        self.assertTrue(belongs_to_cartridge('/', '/mnt/asgard/mediamtx/mediamtx'))
    def test_host_and_similar_paths_protected(self):
        for root,exe in [('/', '/usr/bin/python3'),('/mnt/asgard/rootfs-copy','/usr/bin/python3'),('/', '/mnt/asgard-other/bin/python'),('/tmp/portabletak-cleanbase/rootfs','/tmp/portabletak-cleanbase/rootfs/bin/sh')]:
            self.assertFalse(belongs_to_cartridge(root,exe))
if __name__ == '__main__': unittest.main()
