"""Data packages card: wrapping files into ATAK data packages and the file-share CoT."""
import datetime
import importlib.util
import io
from pathlib import Path
import types
import unittest
import zipfile

PAYLOAD = Path(__file__).resolve().parents[2] / 'host-installer-20260929/payload/usr/local/portabletak'
spec = importlib.util.spec_from_file_location('datapackages_actions', PAYLOAD / 'chroot/datapackages_actions.py')
dp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp)


class DataPackageTests(unittest.TestCase):
    def test_wraps_a_file_with_a_manifest(self):
        data = dp.wrap(b'<kml/>', 'route.kml')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            manifest = z.read('MANIFEST/manifest.xml').decode()
            entry = [n for n in names if n.endswith('/route.kml')][0]
            self.assertEqual(z.read(entry), b'<kml/>')
        self.assertIn('zipEntry="%s"' % entry, manifest)
        self.assertIn('contentType" value="KML"', manifest)
        self.assertTrue(dp._is_data_package(data))

    def test_recognizes_data_packages_and_plain_files(self):
        self.assertFalse(dp._is_data_package(b'%PDF-1.7 not a zip'))
        plain = io.BytesIO()
        with zipfile.ZipFile(plain, 'w') as z:
            z.writestr('photo.jpg', b'x')
        self.assertFalse(dp._is_data_package(plain.getvalue()))   # a zip without a manifest gets wrapped

    def test_clean_names(self):
        self.assertEqual(dp._clean_name('../../etc/passwd'), 'passwd')
        self.assertEqual(dp._clean_name('Map <1>.pdf'), 'Map _1_.pdf')
        self.assertEqual(dp._clean_name(''), 'file')
        self.assertEqual(len(dp._clean_name('a' * 300)), dp.MAX_NAME)

    def test_fileshare_cot_points_each_phone_at_this_server(self):
        row = types.SimpleNamespace(filename='Site "A" plan.zip', hash='ab' * 32, size=1234)
        cot = dp.fileshare_cot('node-1', row, 'ANDROID-123', now=datetime.datetime(2026, 10, 26, 12, 0, tzinfo=datetime.timezone.utc))
        self.assertIn('type="b-f-t-r"', cot)
        self.assertIn('senderUrl="https://PORTABLETAK-FEDERATION-HOST:8443/Marti/sync/content?hash=' + 'ab' * 32 + '"', cot)
        self.assertIn('sha256="' + 'ab' * 32 + '"', cot)
        self.assertIn('sizeInBytes="1234"', cot)
        self.assertIn('filename=\'Site "A" plan.zip\'', cot)                 # quoted safely
        self.assertIn('<dest uid="ANDROID-123"/>', cot)
        self.assertIn('stale="2026-10-26T12:10:00.000Z"', cot)


if __name__ == '__main__':
    unittest.main()
