"""Dashboard logo: what is accepted, where it goes, and how it falls back."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

PAYLOAD = Path(__file__).resolve().parents[2] / 'host-installer-20260929/payload/usr/local/portabletak'
spec = importlib.util.spec_from_file_location('site_logo', PAYLOAD / 'lib/site_logo.py')
logo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logo)

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 32


class SiteLogoTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)

    def test_kinds_are_read_from_the_bytes(self):
        self.assertEqual(logo.media_type(PNG), 'image/png')
        self.assertEqual(logo.media_type(b'\xff\xd8\xff\xe0'), 'image/jpeg')
        self.assertEqual(logo.media_type(b'RIFF\0\0\0\0WEBPVP8 '), 'image/webp')
        self.assertIsNone(logo.media_type(b'<svg onload="x()">'))
        self.assertIsNone(logo.media_type(b'<html>'))

    def test_built_in_logo_without_a_custom_one(self):
        self.assertEqual(logo.current(self.data), (logo.BUILT_IN, 'image/png'))
        self.assertFalse(logo.is_custom(self.data))

    def test_save_replace_and_reset(self):
        self.assertIsNone(logo.save(PNG, self.data))
        self.assertEqual(logo.current(self.data)[1], 'image/png')
        self.assertTrue(logo.is_custom(self.data))
        self.assertIsNone(logo.save(b'\xff\xd8\xff' + b'1' * 32, self.data))
        self.assertEqual(logo.current(self.data)[1], 'image/jpeg')
        self.assertEqual(len(list(logo.custom_path(self.data).parent.iterdir())), 1)
        logo.clear(self.data)
        logo.clear(self.data)
        self.assertFalse(logo.is_custom(self.data))

    def test_refused_files_leave_the_old_logo(self):
        logo.save(PNG, self.data)
        self.assertEqual(logo.save(b'<svg/>', self.data), 'not_an_image')
        self.assertEqual(logo.save(b'', self.data), 'empty_file')
        self.assertEqual(logo.save(PNG + b'0' * logo.MAX_BYTES, self.data), 'logo_too_large')
        self.assertEqual(logo.custom_path(self.data).read_bytes(), PNG)

    def test_no_drive_writes_nothing(self):
        self.assertEqual(logo.save(PNG, self.data / 'missing'), 'no_drive')
        self.assertFalse((self.data / 'missing').exists())

    def test_a_damaged_custom_file_falls_back(self):
        logo.save(PNG, self.data)
        logo.custom_path(self.data).write_bytes(b'junk')
        self.assertEqual(logo.current(self.data)[0], logo.BUILT_IN)


if __name__ == '__main__':
    unittest.main()
