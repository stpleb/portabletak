"""Run with the OTS venv in a scratch directory; no application/DB startup."""
import hashlib
import importlib.util
import io
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from flask import Flask

MODULE = Path(__file__).resolve().parents[1] / 'lib/generic_certificate_authority.py'
spec = importlib.util.spec_from_file_location('opentakserver.portabletak_test_ca', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class NativeCryptoTests(unittest.TestCase):
    def test_fresh_native_ca_and_packages(self):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory(prefix='portabletak-crypto-') as directory:
            root = Path(directory)
            app = Flask('portabletak-isolated-crypto-test')
            ca_password = secrets.token_hex(32)
            # Includes characters that would be dangerous in shell commands or XML.
            client_password = "Test &'<>;$(false) \" sample"
            app.config.update(OTS_CA_FOLDER=str(root / 'ca with spaces'),
                              OTS_CA_SUBJECT='/O=PortableTAK/OU=TAK',
                              OTS_CA_NAME='PortableTAK-Test-CA',
                              OTS_CA_PASSWORD=ca_password,
                              PORTABLETAK_TRUSTSTORE_PASSWORD=client_password,
                              PORTABLETAK_CLIENT_PASSWORD=client_password,
                              OTS_CA_EXPIRATION_TIME=30,
                              OTS_SERVER_ADDRESS='10.41.250.10',
                              OTS_MARTI_HTTPS_PORT=8443,
                              OTS_SSL_STREAMING_PORT=8089)
            log = io.StringIO()
            logger = logging.getLogger('portabletak-test')
            handler = logging.StreamHandler(log)
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
            try:
                ca = module.CertificateAuthority(logger, app)
                for invalid in ('../outside', '/tmp/outside', '..', 'name/child', 'a\\b'):
                    with self.assertRaisesRegex(ValueError, 'invalid_certificate_name'):
                        ca.issue_certificate(invalid)
                ca.create_ca()
                ca_root = Path(app.config['OTS_CA_FOLDER'])
                fingerprint = hashlib.sha256((ca_root / 'ca.pem').read_bytes()).hexdigest()
                ca.create_ca()
                self.assertEqual(fingerprint, hashlib.sha256((ca_root / 'ca.pem').read_bytes()).hexdigest())
                with app.test_request_context(base_url='https://wrong-host.invalid'):
                    names = ca.issue_certificate('test_user')
                self.assertEqual(set(names), {'test_user_CONFIG.zip', 'test_user_CONFIG_iTAK.zip'})
                environment = os.environ.copy()
                environment['PT_TEST_PASSWORD'] = client_password
                for path in [ca_root / 'truststore-root.p12', ca_root / 'certs/test_user/test_user.p12']:
                    subprocess.run(['openssl', 'pkcs12', '-legacy', '-in', str(path), '-passin', 'env:PT_TEST_PASSWORD', '-noout'], env=environment, check=True, capture_output=True)
                cert = ca_root / 'certs/opentakserver/opentakserver.pem'
                subprocess.run(['openssl', 'verify', '-CAfile', str(ca_root / 'ca.pem'), str(cert)], check=True, capture_output=True)
                san = subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout', '-ext', 'subjectAltName'], check=True, capture_output=True, text=True).stdout
                self.assertIn('IP Address:10.41.250.10', san)
                user_root = ca_root / 'certs/test_user'
                with zipfile.ZipFile(user_root / 'test_user_CONFIG_iTAK.zip') as archive:
                    entries = {node.attrib['key']: node.text for node in ET.fromstring(archive.read('config.pref')).iter('entry')}
                    self.assertEqual(entries['clientPassword'], client_password)
                    self.assertEqual(entries['caPassword'], client_password)
                    self.assertEqual(entries['connectString0'], '10.41.250.10:8089:ssl')
                with zipfile.ZipFile(user_root / 'test_user_CONFIG.zip') as archive:
                    inner = [name for name in archive.namelist() if name.endswith('.zip')]
                    self.assertEqual(len(inner), 1)
                    with zipfile.ZipFile(io.BytesIO(archive.read(inner[0]))) as nested:
                        pref = [name for name in nested.namelist() if name.endswith('.pref')][0]
                        entries = {node.attrib['key']: node.text for node in ET.fromstring(nested.read(pref)).iter('entry')}
                        self.assertEqual(entries['clientPassword'], client_password)
                        self.assertEqual(entries['connectString0'], '10.41.250.10:8089:ssl')
                        self.assertNotIn(ca_password.encode(), nested.read(pref))
                self.assertNotIn(ca_password, log.getvalue())
                self.assertNotIn(client_password, log.getvalue())
            finally:
                os.chdir(original_cwd)
                logger.removeHandler(handler)

if __name__ == '__main__':
    unittest.main()
