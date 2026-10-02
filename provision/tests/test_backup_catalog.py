import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import backup_catalog as catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_directory_is_empty(self):
        self.assertEqual(catalog.list_backups(self.root / 'none')['backups'], [])

    def test_lists_newest_first_with_labels_notes_and_free_space(self):
        (self.root / 'portabletak-20260928_101500.tar.gz').write_bytes(b'old')
        new = self.root / 'portabletak-20260929_160501.tar.gz'
        new.write_bytes(b'new-backup')
        Path(str(new) + '.json').write_text(json.dumps({
            'format': 1, 'note': 'Before Node02 test', 'sha256': 'a' * 64,
            'created_local': '2026-09-29T16:05:01-0500'}))
        (self.root / '.portabletak-20260929_170000.tar.gz.partial').write_bytes(b'x')
        (self.root / 'notes.txt').write_text('ignored')
        result = catalog.list_backups(self.root)
        names = [b['name'] for b in result['backups']]
        self.assertEqual(names, [new.name, 'portabletak-20260928_101500.tar.gz'])
        first, second = result['backups']
        self.assertEqual((first['note'], first['sha256'], first['checksummed']),
                         ('Before Node02 test', 'a' * 64, True))
        self.assertEqual(first['label'], 'Sep 29, 2026 16:05')
        self.assertEqual(second['created_local'], '2026-09-28T10:15:00')
        self.assertFalse(second['checksummed'])
        self.assertGreater(result['free_bytes'], 0)

    def test_hostile_sidecar_values_dropped(self):
        path = self.root / 'portabletak-20260929_160501.tar.gz'
        path.write_bytes(b'x')
        Path(str(path) + '.json').write_text(json.dumps({
            'format': 1, 'note': '<script>alert(1)</script>', 'sha256': 'nothex'}))
        item = catalog.list_backups(self.root)['backups'][0]
        self.assertEqual((item['note'], item['sha256']), ('', None))

    def test_name_and_note_validation(self):
        self.assertTrue(catalog.valid_name('portabletak-20260929_160501.tar.gz'))
        for bad in ('../portabletak-20260929_160501.tar.gz', 'portabletak-x.tar.gz', None):
            self.assertFalse(catalog.valid_name(bad))
        self.assertTrue(catalog.valid_note('Before Node02 test (1)'))
        self.assertFalse(catalog.valid_note('$(reboot)'))
        self.assertFalse(catalog.valid_note('x' * 121))


if __name__ == '__main__':
    unittest.main()
