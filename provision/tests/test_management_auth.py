import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from management_auth import ManagementAuth
from portabletak_provision import ProvisionError


class ManagementAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.credentials = root / 'config/management-auth.json'
        self.key = root / 'config/secrets/management-session-key.json'
        self.now = 1000
        self.auth = ManagementAuth(self.credentials, self.key, clock=lambda: self.now)
        self.password = 'Management-password-123'
        self.secret = 'a' * 64

    def tearDown(self):
        self.temp.cleanup()

    def initialize(self):
        return self.auth.initialize(self.password, self.secret)

    def test_initialization_stores_hash_and_repeats_safely(self):
        result = self.initialize()
        self.assertFalse(result['existing'])
        combined = self.credentials.read_text() + self.key.read_text()
        self.assertNotIn(self.password, combined)
        self.assertEqual(os.stat(self.credentials).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.key).st_mode & 0o777, 0o600)
        self.assertTrue(self.auth.verify_password('administrator', self.password))
        self.assertFalse(self.auth.verify_password('administrator', 'wrong-password-value'))
        self.assertTrue(self.auth.initialize(self.password, self.secret)['existing'])

    def test_different_password_cannot_replace_credentials(self):
        self.initialize()
        with self.assertRaisesRegex(ProvisionError, 'management_auth_already_initialized'):
            self.auth.initialize('Different-password-456', self.secret)

    def test_signed_session_and_expiry(self):
        self.initialize()
        token = self.auth.create_session('administrator', ttl=60)
        self.assertEqual(self.auth.verify_session(token)['expires_at'], 1060)
        self.now = 1060
        with self.assertRaisesRegex(ProvisionError, 'invalid_management_session'):
            self.auth.verify_session(token)

    def test_tampered_session_refused(self):
        self.initialize()
        token = self.auth.create_session('administrator', ttl=60)
        changed = ('A' if token[0] != 'A' else 'B') + token[1:]
        with self.assertRaisesRegex(ProvisionError, 'invalid_management_session'):
            self.auth.verify_session(changed)

    def test_tampered_credential_parameters_refused(self):
        self.initialize()
        data = json.loads(self.credentials.read_text())
        data['n'] = 2 ** 20
        self.credentials.write_text(json.dumps(data))
        with self.assertRaisesRegex(ProvisionError, 'invalid_management_credentials'):
            self.auth.verify_password('administrator', self.password)


if __name__ == '__main__':
    unittest.main()
