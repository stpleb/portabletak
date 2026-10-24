from pathlib import Path
import io
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import client_package_check


def truststore(folder, password):
    key, cert, p12 = folder / 'k.pem', folder / 'c.pem', folder / 't.p12'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert),
                    '-days', '1', '-subj', '/CN=test-ca'], check=True, capture_output=True)
    subprocess.run(['openssl', 'pkcs12', '-export', '-nokeys', '-in', str(cert), '-out', str(p12),
                    '-passout', 'pass:' + password], check=True, capture_output=True)
    return p12.read_bytes()


def package(store, password):
    pref = ('<preferences><preference version="1" name="com.atakmap.app_preferences">'
            '<entry key="caPassword" class="class java.lang.String">%s</entry></preference></preferences>' % password)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as z:
        z.writestr('uid/truststore-root.p12', store)
        z.writestr('uid/preference.pref', pref)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, 'w') as z:
        z.writestr('folder/user.zip', inner.getvalue())
        z.writestr('MANIFEST/manifest.xml', '<x/>')
    return outer.getvalue()


class ClientPackageCheckTests(unittest.TestCase):
    def test_sets_aside_only_mismatched_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = truststore(tmp, 'right-password')
            certs = tmp / 'root/opentak/data/ca/certs'
            for user, password in (('good', 'right-password'), ('bad', 'other-password')):
                (certs / user).mkdir(parents=True)
                (certs / user / (user + '_CONFIG.zip')).write_bytes(package(store, password))
                (certs / user / (user + '_CONFIG_iTAK.zip')).write_bytes(b'itak')
            self.assertEqual(client_package_check.check(tmp / 'root'), ['bad'])
            self.assertTrue((certs / 'good/good_CONFIG.zip').exists())
            self.assertTrue((certs / 'good/good_CONFIG_iTAK.zip').exists())
            self.assertFalse((certs / 'bad/bad_CONFIG.zip').exists())
            self.assertTrue((certs / 'bad/bad_CONFIG.zip.invalid').exists())
            self.assertTrue((certs / 'bad/bad_CONFIG_iTAK.zip.invalid').exists())
            self.assertEqual(client_package_check.check(tmp / 'root'), [])

    def test_unexpected_layout_is_kept(self):
        self.assertFalse(client_package_check.wrong_password(b'not a zip'))


if __name__ == '__main__':
    unittest.main()
