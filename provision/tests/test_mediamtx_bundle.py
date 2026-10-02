import hashlib
import json
from pathlib import Path
import unittest


BUNDLE = Path(__file__).resolve().parents[1] / 'vendor/mediamtx'


class BundleTests(unittest.TestCase):
    def test_binary_matches_pinned_provenance(self):
        source = json.loads((BUNDLE / 'source.json').read_text())
        digest = hashlib.sha256((BUNDLE / 'mediamtx').read_bytes()).hexdigest()
        self.assertEqual(source['version'], '1.13.0')
        self.assertEqual(source['architecture'], 'arm64')
        self.assertEqual(digest, source['binary_sha256'])

    def test_api_is_local_and_hooks_do_not_contain_a_token(self):
        config = (BUNDLE / 'mediamtx.yml').read_text()
        self.assertIn('apiAddress: 127.0.0.1:9997', config)
        self.assertIn('authMethod: http', config)
        self.assertIn('ots-webhook.py connect', config)
        self.assertNotIn('token=', config)


if __name__ == '__main__':
    unittest.main()
