import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import video_feeds as vf


class FakeResponse:
    def __init__(self, body): self.body = body
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


class VideoFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_names_follow_the_skydio_rule(self):
        for good in ('skydio1', 'isr2', 'skydio-x10', 'x10d', '1skydio'):
            self.assertTrue(vf.NAME.fullmatch(good), good)
        for bad in ('Skydio1', 'sky dio1', 'sky/1', 'sky_1', '-sky', '', 'a' * 40):
            self.assertFalse(vf.NAME.fullmatch(bad), bad)
        with self.assertRaises(vf.FeedError):
            vf.add(self.root, 'Skydio')

    def test_add_remove_and_survive_on_the_drive(self):
        vf.add(self.root, 'skydio1', clock=lambda: 5)
        vf.add(self.root, 'skydio1')                      # twice: no duplicate
        vf.add(self.root, 'isr2')
        self.assertEqual([f['name'] for f in vf.load(self.root)], ['skydio1', 'isr2'])
        vf.remove(self.root, 'skydio1')
        self.assertEqual([f['name'] for f in vf.load(self.root)], ['isr2'])
        self.assertEqual(json.loads((self.root / vf.FEEDS).read_text())['format'], 1)

    def test_exclusions_keep_defaults_and_add_publish_and_read(self):
        rules = vf.exclusions([{'name': 'skydio1'}])
        self.assertEqual(rules[:3], vf.BASE_EXCLUDE)
        self.assertIn({'action': 'publish', 'path': 'skydio1'}, rules)
        self.assertIn({'action': 'read', 'path': 'skydio1'}, rules)
        self.assertEqual(len(rules), 5)

    def test_apply_patches_mediamtx_and_status_marks_live(self):
        vf.add(self.root, 'skydio1')
        sent = []

        def opener(request, timeout=0):
            sent.append((request.get_method(), request.full_url, request.data))
            if request.full_url.endswith('/v3/paths/list'):
                return FakeResponse(json.dumps({'items': [{'name': 'skydio1', 'ready': True}]}).encode())
            return FakeResponse(b'')
        self.assertTrue(vf.apply(self.root, opener=opener))
        method, url, data = sent[0]
        self.assertEqual((method, url), ('PATCH', 'http://127.0.0.1:9997/v3/config/global/patch'))
        self.assertIn({'action': 'publish', 'path': 'skydio1'}, json.loads(data)['authHTTPExclude'])
        self.assertEqual(vf.status(self.root, opener=opener), [{'name': 'skydio1', 'created': vf.load(self.root)[0]['created'], 'live': True}])

    def test_corrupt_file_reads_as_no_feeds(self):
        (self.root / vf.FEEDS).write_text('not json')
        self.assertEqual(vf.load(self.root), [])


if __name__ == '__main__':
    unittest.main()
