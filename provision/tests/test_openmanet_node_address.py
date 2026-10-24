from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters.openmanet import provision


def runner(output):
    return lambda *a, **k: types.SimpleNamespace(stdout=output)


class NodeAddressTests(unittest.TestCase):
    def test_cicadatak_style_node(self):
        out = '4: br-lan    inet 10.41.254.1/24 scope global br-lan\n'
        self.assertEqual(provision.detect_node_local(runner(out)), '10.41.254.1')

    def test_openmanet_18_flat_network(self):
        out = '5: br-ahwlan    inet 10.41.0.1/16 scope global br-ahwlan\n5: br-ahwlan    inet 10.41.250.10/32 scope global br-ahwlan\n'
        self.assertIsNone(provision.detect_node_local(runner(out)))
        adapter = provision.OpenManetAdapter(network_tool=None, node_local_address=None)
        self.assertEqual(adapter.server_addresses(None), ('10.41.250.10', ['10.41.250.10']))

    def test_drive_from_cicadatak_node_readopts_on_openmanet_18(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import host_adoption as ha
        from test_host_adoption import cartridge
        root = cartridge('openmanet', '10.41.250.10', 'br-ahwlan', ['10.41.254.1', '10.41.250.10'], '10.41.254.1')
        legacy = provision.OpenManetAdapter(network_tool=None, node_local_address='10.41.254.1')
        flat = provision.OpenManetAdapter(network_tool=None, node_local_address=None)
        self.assertFalse(ha.needs_adoption(root, legacy))
        self.assertTrue(ha.needs_adoption(root, flat))

    def test_unknown_keeps_old_behaviour(self):
        def broken(*a, **k):
            raise OSError('no ip')
        self.assertEqual(provision.detect_node_local(broken), '10.41.254.1')


if __name__ == '__main__':
    unittest.main()
