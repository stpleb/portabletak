from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import federation_invites as fi

CA = '''-----BEGIN CERTIFICATE-----
MIIBszCCAVmgAwIBAgIUQ1xyFakeButValidBase64AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==
-----END CERTIFICATE-----
'''


class InviteTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        (Path(self.root) / 'portabletak').mkdir()
        self.now = 1000000.0

    def clock(self):
        return self.now

    def make(self):
        return fi.create(self.root, 'Team Alpha', ['100.64.1.2', '192.168.1.5'], CA, 'ab' * 32, clock=self.clock)

    def test_code_round_trip(self):
        code, expires = self.make()
        self.assertTrue(code.startswith('PTFED1.'))
        self.assertLess(len(code), 400)
        data = fi.decode('  ' + code + '\n')
        self.assertEqual(data['n'], 'Team Alpha')
        self.assertEqual(data['a'][0], '100.64.1.2')
        self.assertEqual(data['c'], fi.pem_sha256(CA))
        self.assertEqual(expires, self.now + fi.LIFETIME)

    def test_secret_single_use(self):
        code, _ = self.make()
        token = fi.decode(code)['t']
        self.assertTrue(fi.redeem(self.root, token, clock=self.clock, use=False))   # check only
        self.assertTrue(fi.redeem(self.root, token, clock=self.clock))
        with self.assertRaises(fi.InviteError) as caught:
            fi.redeem(self.root, token, clock=self.clock)
        self.assertEqual(str(caught.exception), 'invite_already_used')

    def test_expiry(self):
        code, _ = self.make()
        self.now += fi.LIFETIME + 1
        with self.assertRaises(fi.InviteError) as caught:
            fi.redeem(self.root, fi.decode(code)['t'], clock=self.clock)
        self.assertEqual(str(caught.exception), 'invite_expired')

    def test_unknown_and_damaged(self):
        self.make()
        with self.assertRaises(fi.InviteError):
            fi.redeem(self.root, 'not-a-real-secret', clock=self.clock)
        for bad in ('hello', 'PTFED1.###', 'PTFED1.eyJ2IjoyfQ'):
            with self.assertRaises(fi.InviteError):
                fi.decode(bad)

    def test_store_keeps_hashes_only(self):
        code, _ = self.make()
        stored = (Path(self.root) / fi.STORE).read_text()
        self.assertNotIn(fi.decode(code)['t'], stored)
        self.assertEqual(oct((Path(self.root) / fi.STORE).stat().st_mode)[-3:], '600')


if __name__ == '__main__':
    unittest.main()
