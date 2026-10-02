import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_adoption as ha
from adapters.linux import LinuxAdapter
from adapters.openmanet import OpenManetAdapter


def cartridge(profile, address, dev, addresses, advertise):
    root = Path(tempfile.mkdtemp())
    (root / 'config').mkdir()
    (root / 'portabletak').mkdir()
    (root / 'config/host.json').write_text(json.dumps({'runtime_profile': profile}))
    (root / 'config/tak-network.conf').write_text(
        f'PORTABLETAK_SERVICE_NAME="Field Kit"\nPORTABLETAK_SERVICE_IP="{address}"\n'
        f'PORTABLETAK_SERVICE_PREFIX="32"\nPORTABLETAK_SERVICE_DEV="{dev}"\n')
    (root / 'config/server-addresses.json').write_text(json.dumps({'advertise': advertise, 'addresses': addresses}))
    (root / 'opentak/data').mkdir(parents=True)
    (root / 'opentak/data/config.yml').write_text('{}')
    return root


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def reissue(self, root, addresses, advertise):
        self.calls.append((addresses, advertise))
        return {'advertise': advertise, 'addresses': addresses}

    def test_mac_setup_adopted_by_openmanet_node_keeps_tailscale(self):
        root = cartridge('linux', '192.168.64.5', 'eth0',
                         ['192.168.64.5', '100.93.136.26', 'kit.tail1.ts.net'], '192.168.64.5')
        (root / 'config/tailscale').mkdir(parents=True)
        (root / 'config/tailscale/addresses').write_text('100.93.136.26\nkit.tail1.ts.net\n')
        node = OpenManetAdapter(network_tool=None)
        self.assertTrue(ha.needs_adoption(root, node))
        result = ha.adopt(root, node, reissue=self.reissue, listen=lambda r: None)
        self.assertEqual(self.calls, [(['10.41.254.1', '10.41.250.10', '100.93.136.26', 'kit.tail1.ts.net'],
                                       '10.41.254.1')])
        self.assertEqual(result['from_profile'], 'linux')
        self.assertEqual(ha.binding(root), {'profile': 'openmanet', 'address': '10.41.250.10',
                                            'interface': 'br-ahwlan', 'name': 'Field Kit'})
        self.assertFalse(ha.needs_adoption(root, node))

    def test_bound_cartridge_is_current(self):
        root = cartridge('openmanet', '10.41.250.10', 'br-ahwlan', ['10.41.254.1'], '10.41.254.1')
        self.assertFalse(ha.needs_adoption(root, OpenManetAdapter(network_tool=None)))

    def test_linux_address_change_needs_adoption(self):
        root = cartridge('linux', '192.168.1.20', 'eth0', ['192.168.1.20'], '192.168.1.20')
        self.assertFalse(ha.needs_adoption(root, LinuxAdapter('192.168.1.20', 'eth0')))
        laptop = LinuxAdapter('10.0.0.7', 'wlan0')
        self.assertTrue(ha.needs_adoption(root, laptop))
        ha.adopt(root, laptop, reissue=self.reissue, listen=lambda r: None)
        self.assertEqual(self.calls, [(['10.0.0.7'], '10.0.0.7')])

    def test_certificate_failure_changes_nothing(self):
        root = cartridge('linux', '192.168.64.5', 'eth0', ['192.168.64.5'], '192.168.64.5')
        before = (root / 'config/tak-network.conf').read_text()

        def broken(*a):
            raise ha.ProvisionError('server_certificate_openssl_failed')
        with self.assertRaises(ha.ProvisionError):
            ha.adopt(root, OpenManetAdapter(network_tool=None), reissue=broken, listen=lambda r: None)
        self.assertEqual((root / 'config/tak-network.conf').read_text(), before)




class AdvertiseTests(unittest.TestCase):
    def test_mac_vm_advertises_mac_address_first(self):
        root = cartridge('linux', '10.0.2.15', 'eth0', ['10.0.2.15'], '10.0.2.15')
        calls = []
        vm = LinuxAdapter('10.0.2.15', 'eth0', advertise='192.168.1.50')
        self.assertTrue(ha.needs_adoption(root, vm))
        ha.adopt(root, vm, reissue=lambda r, a, adv: calls.append((a, adv)) or {'advertise': adv, 'addresses': a},
                 listen=lambda r: None)
        self.assertEqual(calls, [(['192.168.1.50', '10.0.2.15'], '192.168.1.50')])



class ComputerTailscaleTests(unittest.TestCase):
    def test_computer_tailscale_address_added_and_triggers_adoption(self):
        root = cartridge('linux', '10.0.2.15', 'eth0', ['192.168.1.50', '10.0.2.15'], '192.168.1.50')
        vm = LinuxAdapter('10.0.2.15', 'eth0', advertise='192.168.1.50', extra=['100.101.102.103'])
        self.assertTrue(ha.needs_adoption(root, vm))
        calls = []
        ha.adopt(root, vm, reissue=lambda r, a, adv: calls.append((a, adv)) or {'advertise': adv, 'addresses': a},
                 listen=lambda r: None)
        self.assertEqual(calls[0], (['192.168.1.50', '10.0.2.15', '100.101.102.103'], '192.168.1.50'))



class VpnOwnershipTests(unittest.TestCase):
    def reissue(self, calls):
        return lambda r, a, adv: calls.append(a) or {'advertise': adv, 'addresses': a}

    def test_computer_tailscale_address_left_behind_on_node(self):
        root = cartridge('linux', '10.0.2.15', 'eth0', ['192.168.1.50', '10.0.2.15', '100.83.127.3'], '192.168.1.50')
        calls = []
        ha.adopt(root, OpenManetAdapter(network_tool=None), reissue=self.reissue(calls), listen=lambda r: None)
        self.assertEqual(calls[0], ['10.41.254.1', '10.41.250.10'])

    def test_cartridge_own_tailscale_moves_with_drive(self):
        root = cartridge('openmanet', '10.41.250.10', 'br-ahwlan',
                         ['10.41.254.1', '10.41.250.10', '100.93.136.26', 'kit.tail1.ts.net'], '10.41.254.1')
        (root / 'config/tailscale').mkdir(parents=True)
        (root / 'config/tailscale/addresses').write_text('100.93.136.26\nkit.tail1.ts.net\n')
        calls = []
        ha.adopt(root, LinuxAdapter('192.168.1.20', 'eth0'), reissue=self.reissue(calls), listen=lambda r: None)
        self.assertEqual(calls[0], ['192.168.1.20', '100.93.136.26', 'kit.tail1.ts.net'])

    def test_same_mac_drops_its_tailscale_and_adds_the_drives_own(self):
        # Older versions put the Mac's Tailscale address on the certificate; the drive's
        # own address (joined on a node) was missing. Same host binding otherwise.
        root = cartridge('linux', '10.0.2.15', 'eth0', ['192.168.1.189', '10.0.2.15', '100.83.127.3'], '192.168.1.189')
        (root / 'config/tailscale').mkdir(parents=True)
        (root / 'config/tailscale/addresses').write_text('100.93.136.26\nkit.tail1.ts.net\n')
        vm = LinuxAdapter('10.0.2.15', 'eth0', advertise='192.168.1.189')
        self.assertTrue(ha.needs_adoption(root, vm))
        calls = []
        ha.adopt(root, vm, reissue=self.reissue(calls), listen=lambda r: None)
        self.assertEqual(calls[0], ['192.168.1.189', '10.0.2.15', '100.93.136.26', 'kit.tail1.ts.net'])

    def test_current_when_own_addresses_present(self):
        root = cartridge('linux', '10.0.2.15', 'eth0', ['192.168.1.189', '10.0.2.15', '100.93.136.26'], '192.168.1.189')
        (root / 'config/tailscale').mkdir(parents=True)
        (root / 'config/tailscale/addresses').write_text('100.93.136.26\n')
        self.assertFalse(ha.needs_adoption(root, LinuxAdapter('10.0.2.15', 'eth0', advertise='192.168.1.189')))


if __name__ == '__main__':
    unittest.main()
