from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import ots_port


class OtsPortTests(unittest.TestCase):
    def test_rewrite(self):
        self.assertEqual(ots_port.rewrite('OTS_LISTENER_PORT: 8081\nX: 18081\n'), 'OTS_LISTENER_PORT: 18081\nX: 18081\n')
        self.assertIn('http://127.0.0.1:18081/api/external_auth',
                      ots_port.rewrite('authHTTPAddress: http://127.0.0.1:8081/api/external_auth'))
        self.assertIn('proxy_pass http://127.0.0.1:18081;', ots_port.rewrite('proxy_pass http://127.0.0.1:8081;'))
        self.assertEqual(ots_port.rewrite('listen 8443; port 80810'), 'listen 8443; port 80810')
        self.assertIn('"OTS_LISTENER_PORT": 18081,', ots_port.rewrite('{\n  "OTS_LISTENER_PORT": 8081,\n}'))
        self.assertIn("OTS_LISTENER_PORT: '18081'", ots_port.rewrite("OTS_LISTENER_PORT: '8081'"))

    def test_migrate_cartridge_once(self):
        root = Path(tempfile.mkdtemp())
        files = {'opentak/data/config.yml': 'OTS_LISTENER_PORT: 8081\nOTS_CA_PASSWORD: x\n',
                 'mediamtx/ots-webhook.py': 'URL = "http://127.0.0.1:8081/api/mediamtx/webhook"\n',
                 'rootfs/etc/nginx/sites-available/portabletak': 'location / { proxy_pass http://127.0.0.1:8081; }\n'}
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        self.assertEqual(ots_port.migrate(root), 3)
        self.assertEqual(ots_port.migrate(root), 0)
        for name in files:
            self.assertNotIn(':8081', (root / name).read_text().replace(':18081', ''))
        self.assertIn('OTS_CA_PASSWORD: x', (root / 'opentak/data/config.yml').read_text())


if __name__ == '__main__':
    unittest.main()
