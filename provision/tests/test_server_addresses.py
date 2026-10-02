import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import server_addresses as sa
from portabletak_provision import ProvisionError

CFG = '''[ server ]
basicConstraints=critical,CA:FALSE
keyUsage=critical, digitalSignature, keyEncipherment
extendedKeyUsage = critical, clientAuth, serverAuth

subjectAltName = @alt_names
[alt_names]
IP.1 = 10.41.250.10'''


def run(*argv):
    result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise AssertionError(' '.join(a.rsplit('/',1)[-1] for a in argv) + ': ' + result.stderr[-400:])


@unittest.skipUnless(shutil.which('openssl'), 'openssl required')
class ServerAddressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.ca = self.root / 'opentak/data/ca'
        self.cert = self.ca / 'certs/opentakserver'
        self.cert.mkdir(parents=True)
        (self.root / 'config').mkdir()
        run('openssl', 'genrsa', '-aes256', '-passout', 'pass:ca-secret', '-out', str(self.ca / 'ca-do-not-share.key'), '2048')
        run('openssl', 'req', '-new', '-x509', '-key', str(self.ca / 'ca-do-not-share.key'), '-passin', 'pass:ca-secret',
            '-subj', '/O=PortableTAK/CN=Test-CA', '-days', '30', '-batch', '-out', str(self.ca / 'ca.pem'))
        run('openssl', 'genrsa', '-out', str(self.cert / 'opentakserver.nopass.key'), '2048')
        run('openssl', 'req', '-new', '-key', str(self.cert / 'opentakserver.nopass.key'),
            '-subj', '/O=PortableTAK/OU=TAK/CN=opentakserver', '-batch', '-passin', 'pass:unused',
            '-out', str(self.cert / 'opentakserver.csr'))
        (self.cert / 'opentakserver_config.cfg').write_text(CFG)
        (self.cert / 'opentakserver.pem').write_text('ORIGINAL CERTIFICATE\n')
        self.config = self.root / 'opentak/data/config.yml'
        self.config.write_text(json.dumps({'OTS_CA_PASSWORD': 'ca-secret', 'OTS_SERVER_ADDRESS': '10.41.250.10'}))

    def tearDown(self):
        self.temp.cleanup()

    def reissue(self, addresses, advertise):
        return sa.reissue(self.root, addresses, advertise, openssl_prefix=(), ca_dir=self.ca, clock=lambda: 1790000000)

    def text(self):
        return subprocess.run(['openssl', 'x509', '-noout', '-text', '-in', str(self.cert / 'opentakserver.pem')],
                              capture_output=True, text=True, check=True).stdout

    def test_certificate_lists_every_address_and_advertises_one(self):
        record = self.reissue(['10.41.250.10', '10.41.254.1', '100.101.102.103', 'tak.example.net'], '10.41.254.1')
        details = self.text()
        for expected in ('IP Address:10.41.250.10', 'IP Address:10.41.254.1',
                         'IP Address:100.101.102.103', 'DNS:tak.example.net'):
            self.assertIn(expected, details)
        self.assertIn('CN=10.41.254.1', details.replace(' = ', '='))  # CN = advertised address (ATAK checks it)
        subprocess.run(['openssl', 'verify', '-CAfile', str(self.ca / 'ca.pem'), str(self.cert / 'opentakserver.pem')],
                       check=True, capture_output=True)
        self.assertEqual(record['advertise'], '10.41.254.1')
        self.assertEqual(json.loads(self.config.read_text())['OTS_SERVER_ADDRESS'], '10.41.254.1')
        self.assertEqual(sa.current(self.root)['addresses'], record['addresses'])
        previous = list(self.cert.glob('opentakserver.pem.previous-*'))
        self.assertEqual(previous[0].read_text(), 'ORIGINAL CERTIFICATE\n')
        self.assertFalse(list(self.cert.glob('*.new')))
        self.assertNotIn('ca-secret', (self.root / 'config/server-addresses.json').read_text())

    def test_wrong_ca_password_changes_nothing(self):
        self.config.write_text(json.dumps({'OTS_CA_PASSWORD': 'wrong', 'OTS_SERVER_ADDRESS': '10.41.250.10'}))
        with self.assertRaisesRegex(ProvisionError, 'server_certificate_openssl_failed'):
            self.reissue(['10.41.250.10', '10.41.254.1'], '10.41.254.1')
        self.assertEqual((self.cert / 'opentakserver.pem').read_text(), 'ORIGINAL CERTIFICATE\n')
        self.assertEqual((self.cert / 'opentakserver_config.cfg').read_text(), CFG)
        self.assertFalse(list(self.cert.glob('*.new')))
        self.assertFalse((self.root / 'config/server-addresses.json').exists())

    def test_advertised_address_must_be_listed(self):
        with self.assertRaisesRegex(ProvisionError, 'advertised_address_not_listed'):
            self.reissue(['10.41.250.10'], '10.41.254.1')

    def test_addresses_validated(self):
        for bad in (['10.0.0.300'], ['a b'], ['1.2.3.4; rm -rf /'], [], ['x'] * 9, 'not-a-list', [5]):
            with self.assertRaises(ProvisionError):
                sa.normalize(bad)
        self.assertEqual(sa.normalize(['10.41.254.1', 'TAK.Example.net', '10.41.254.1']),
                         [('IP', '10.41.254.1'), ('DNS', 'tak.example.net')])
        self.assertEqual(sa.alt_names([('IP', '1.1.1.1'), ('DNS', 'a.b'), ('IP', '2.2.2.2')]),
                         '[alt_names]\nIP.1 = 1.1.1.1\nDNS.1 = a.b\nIP.2 = 2.2.2.2\n')

    def test_remote_certificate_named_for_tailscale_address(self):
        self.reissue(['192.168.1.189', '10.0.2.15', '100.83.127.3'], '192.168.1.189')
        ensure = lambda: sa.ensure_remote(self.root, openssl_prefix=(), ca_dir=self.ca)
        self.assertEqual(ensure(), '100.83.127.3')
        remote = self.cert / sa.REMOTE_CERT
        details = subprocess.run(['openssl', 'x509', '-noout', '-text', '-in', str(remote)],
                                 capture_output=True, text=True, check=True).stdout
        self.assertIn('CN=100.83.127.3', details.replace(' = ', '='))
        self.assertIn('IP Address:192.168.1.189', details)
        subprocess.run(['openssl', 'verify', '-CAfile', str(self.ca / 'ca.pem'), str(remote)], check=True, capture_output=True)
        key = lambda *a: subprocess.run(['openssl', *a], capture_output=True, text=True, check=True).stdout
        self.assertEqual(key('x509', '-noout', '-pubkey', '-in', str(remote)),
                         key('x509', '-noout', '-pubkey', '-in', str(self.cert / 'opentakserver.pem')))
        before = remote.read_bytes()
        self.assertEqual(ensure(), '100.83.127.3')
        self.assertEqual(remote.read_bytes(), before)        # current: not re-signed
        self.reissue(['192.168.1.189', '10.0.2.15'], '192.168.1.189')
        self.assertIsNone(ensure())
        self.assertFalse(remote.exists())
        self.assertFalse(list(self.cert.glob('*.new')))

    def test_listen_everywhere_keeps_remote_blocks(self):
        site = self.root / 'rootfs/etc/nginx/sites-available/portabletak'
        site.parent.mkdir(parents=True)
        keep = 'server { listen 100.83.127.3:8446 ssl; z; } ' + sa.REMOTE_MARK
        site.write_text('server { listen 10.41.250.10:8443 ssl; x; }\n' + keep + '\n')
        sa.listen_everywhere(self.root)
        self.assertEqual(site.read_text(), 'server { listen 8443 ssl; x; }\n' + keep + '\n')

    def test_nginx_site_listens_on_every_address(self):
        site = self.root / 'rootfs/etc/nginx/sites-available/portabletak'
        site.parent.mkdir(parents=True)
        site.write_text('server { listen 10.41.250.10:8443 ssl; x; }\nserver { listen 10.41.250.10:8446 ssl; y; }\n')
        self.assertTrue(sa.listen_everywhere(self.root))
        self.assertEqual(site.read_text(), 'server { listen 8443 ssl; x; }\nserver { listen 8446 ssl; y; }\n')
        self.assertFalse(sa.listen_everywhere(self.root))


if __name__ == '__main__':
    unittest.main()
