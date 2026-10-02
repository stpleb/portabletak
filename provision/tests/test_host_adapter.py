import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.linux import LinuxAdapter
from adapters.openmanet import OpenManetAdapter
import host_profile
from portabletak_provision import ProvisionError
from bootstrap_engine import BootstrapContext


class HostAdapterTests(unittest.TestCase):
    def test_openmanet_owns_network_and_host_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            context = BootstrapContext(root, root / 'stage', 'attempt',
                                       {'instance_name': 'Field Team'}, {})
            adapter = OpenManetAdapter()
            self.assertEqual(adapter.identity_settings(context),
                             {'OTS_SERVER_ADDRESS': '10.41.250.10'})
            binding = adapter.validated_binding(context)
            self.assertEqual(binding['interface'], 'br-ahwlan')
            self.assertIn('PORTABLETAK_SERVICE_DEV="br-ahwlan"',
                          (root / 'config/tak-network.conf').read_text())
            metadata = json.loads((root / 'config/host.json').read_text())
            self.assertEqual(metadata['runtime_profile'], 'openmanet')

    def test_linux_adapter_serves_on_a_host_address(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            context = BootstrapContext(root, root / 'stage', 'attempt',
                                       {'instance_name': 'Laptop'}, {})
            adapter = LinuxAdapter('192.168.1.50', 'eth0')
            self.assertEqual(adapter.identity_settings(context), {'OTS_SERVER_ADDRESS': '192.168.1.50'})
            binding = adapter.validated_binding(context)
            self.assertEqual((binding['address'], binding['interface'], binding['profile']),
                             ('192.168.1.50', 'eth0', 'linux'))
            self.assertIn('PORTABLETAK_SERVICE_IP="192.168.1.50"', (root / 'config/tak-network.conf').read_text())
            self.assertEqual(json.loads((root / 'config/host.json').read_text())['runtime_profile'], 'linux')
            self.assertEqual(adapter.claim_command(), ['/usr/local/portabletak/adapters/linux/network', 'claim'])

    def test_adapters_declare_server_addresses(self):
        context = None
        self.assertEqual(OpenManetAdapter().validated_server_addresses(context),
                         ('10.41.254.1', ['10.41.254.1', '10.41.250.10']))
        self.assertEqual(OpenManetAdapter(node_local_address=None).validated_server_addresses(context),
                         ('10.41.250.10', ['10.41.250.10']))
        self.assertEqual(LinuxAdapter('192.168.1.50', 'eth0').validated_server_addresses(context),
                         ('192.168.1.50', ['192.168.1.50']))

        class Wrong(OpenManetAdapter):
            def server_addresses(self, context):
                return '10.9.9.9', ['10.41.250.10']
        with self.assertRaisesRegex(ProvisionError, 'advertised_address_not_listed'):
            Wrong().validated_server_addresses(context)

    def test_linux_adapter_refuses_bad_address(self):
        for address in ('', 'laptop', '10.0.0.300', '1.2.3.4; rm -rf /'):
            with self.assertRaises(ProvisionError):
                LinuxAdapter(address, 'eth0')

    def test_profile_is_parsed_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'host-profile'
            marker = Path(directory) / 'executed'
            path.write_text('PT_PROFILE=linux\nPT_SERVICE_IP="10.0.0.5"\nPT_SERVICE_DEV=eth0\n'
                            f'$(touch {marker})\nOTHER=ignored\n')
            profile = host_profile.read_profile(path)
            self.assertEqual(profile['PT_PROFILE'], 'linux')
            self.assertEqual(profile['PT_SERVICE_IP'], '10.0.0.5')
            self.assertNotIn('OTHER', profile)
            self.assertFalse(marker.exists())
            self.assertIsInstance(host_profile.select_adapter(profile), LinuxAdapter)
            path.write_text('PT_PROFILE=../../evil\n')
            self.assertEqual(host_profile.read_profile(path)['PT_PROFILE'], 'openmanet')
            self.assertEqual(host_profile.read_profile(Path(directory) / 'missing')['PT_PROFILE'], 'openmanet')
            self.assertIsInstance(host_profile.select_adapter({'PT_PROFILE': 'openmanet'}), OpenManetAdapter)


if __name__ == '__main__':
    unittest.main()
