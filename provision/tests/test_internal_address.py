import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import internal_address as ia
import video_feeds

HERE = Path(__file__).resolve().parents[1]
PAYLOAD = HERE.parent / 'host-installer-20260929/payload/usr/local/portabletak'
# A machine-only service reached on 127.0.0.1, where the drive's Tailscale delivers.
LOOPBACK_USE = re.compile(r'(?:127\.0\.0\.1|localhost)["\',:\s]+(?:18081|18090|9997|6502|5432|5672)\b'
                          r'|udp://127\.0\.0\.1:56|tcp -h 127\.0\.0\.1|pg_isready\s*\\?\s*-h 127\.0\.0\.1'
                          r'|@127\.0\.0\.1/')


class RewriteTests(unittest.TestCase):
    def test_internal_services_move(self):
        cases = {
            'authHTTPAddress: http://127.0.0.1:18081/api/external_auth':
                'authHTTPAddress: http://127.0.0.2:18081/api/external_auth',
            'apiAddress: 127.0.0.1:9997': 'apiAddress: 127.0.0.2:9997',
            'location / { proxy_pass http://127.0.0.1:18081; }': 'location / { proxy_pass http://127.0.0.2:18081; }',
            'listeners.tcp.default = 127.0.0.1:5672': 'listeners.tcp.default = 127.0.0.2:5672',
            'ice="tcp -h 127.0.0.1 -p 6502"': 'ice="tcp -h 127.0.0.2 -p 6502"',
            'DEFAULT_BASE_URL = "http://127.0.0.1:18081/api/mediamtx/webhook"':
                'DEFAULT_BASE_URL = "http://127.0.0.2:18081/api/mediamtx/webhook"',
            '"SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://ots:pw@127.0.0.1/ots"':
                '"SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://ots:pw@127.0.0.2/ots"',
            '"OTS_LISTENER_ADDRESS": "127.0.0.1",': '"OTS_LISTENER_ADDRESS": "127.0.0.2",',
            'OTS_RABBITMQ_SERVER_ADDRESS: localhost': 'OTS_RABBITMQ_SERVER_ADDRESS: 127.0.0.2',
            "source: udp://127.0.0.1:5615": "source: udp://127.0.0.2:5615",
        }
        for old, new in cases.items():
            self.assertEqual(ia.rewrite(old), new)
            self.assertEqual(ia.rewrite(new), new)

    def test_public_and_unrelated_addresses_stay(self):
        for text in ('location / { proxy_pass http://127.0.0.1:8090; }',      # dashboard: local browsers
                     "location /hls/ { proxy_pass http://127.0.0.1:8888/; }",
                     'rtspAddress: :8554', 'mqtt.listeners.tcp.default = 1883',
                     'url = rtsp://127.0.0.1:8554/feed', 'udp://127.0.0.1:5616',
                     'listen 8443 ssl; port 18081x', 'OTS_IP_WHITELIST: [127.0.0.1]',
                     'OTS_CA_PASSWORD: "127.0.0.1"', 'http://127.0.0.1:180810'):
            self.assertEqual(ia.rewrite(text), text)

    def test_ots_config_sets_mediamtx_api(self):
        text = json.dumps({'OTS_LISTENER_ADDRESS': '127.0.0.1', 'OTS_RABBITMQ_SERVER_ADDRESS': '127.0.0.1',
                           'SQLALCHEMY_DATABASE_URI': 'postgresql+psycopg://ots:p@127.0.0.1/ots',
                           'OTS_CA_PASSWORD': 'x'}, indent=2)
        data = json.loads(ia.ots_config(text))
        self.assertEqual(data['OTS_MEDIAMTX_API_ADDRESS'], 'http://127.0.0.2:9997')
        self.assertEqual(data['OTS_LISTENER_ADDRESS'], '127.0.0.2')
        self.assertEqual(data['OTS_RABBITMQ_SERVER_ADDRESS'], '127.0.0.2')
        self.assertTrue(data['SQLALCHEMY_DATABASE_URI'].endswith('@127.0.0.2/ots'))
        self.assertEqual(data['OTS_CA_PASSWORD'], 'x')
        once = ia.ots_config(text)
        self.assertEqual(ia.ots_config(once), once)
        yaml = ia.ots_config('OTS_LISTENER_ADDRESS: 127.0.0.1\nOTS_CA_PASSWORD: x\n')
        self.assertEqual(yaml, 'OTS_LISTENER_ADDRESS: 127.0.0.2\nOTS_CA_PASSWORD: x\n'
                               'OTS_MEDIAMTX_API_ADDRESS: http://127.0.0.2:9997\n')
        self.assertEqual(ia.ots_config(yaml), yaml)

    def test_postgres_listens_on_internal_address_only(self):
        text = "#listen_addresses = 'localhost'\nport = 5432\n"
        new = ia.postgres_config(text)
        self.assertTrue(new.startswith(text))
        self.assertEqual(new.splitlines()[-1], ia.POSTGRES_LINE)
        self.assertEqual(ia.postgres_config(new), new)
        # a later setting (someone edited the file) is overridden again
        edited = new + "listen_addresses = '*'\n"
        self.assertEqual(ia.postgres_config(edited).splitlines()[-1], ia.POSTGRES_LINE)

    def test_host_is_never_what_tailscale_dials(self):
        # Tailscale's userspace netstack hands tailnet TCP/UDP to 127.0.0.1 (and ::1).
        self.assertNotIn(ia.HOST, ('127.0.0.1', '0.0.0.0', '::1', 'localhost'))
        self.assertTrue(ia.HOST.startswith('127.'))   # still loopback, so never on the network


class MigrateTests(unittest.TestCase):
    def test_migrate_cartridge_once(self):
        root = Path(tempfile.mkdtemp())
        files = {
            'opentak/data/config.yml': json.dumps({'OTS_LISTENER_ADDRESS': '127.0.0.1',
                                                   'OTS_RABBITMQ_SERVER_ADDRESS': '127.0.0.1',
                                                   'SQLALCHEMY_DATABASE_URI': 'postgresql+psycopg://ots:p@127.0.0.1/ots'},
                                                  sort_keys=True, indent=2) + '\n',
            'opentak/data/mediamtx/mediamtx.yml': 'authHTTPAddress: http://127.0.0.1:18081/api/external_auth\n'
                                                  'apiAddress: 127.0.0.1:9997\nhlsAddress: :8888\n',
            'mediamtx/ots-webhook.py': 'URL = "http://127.0.0.1:18081/api/mediamtx/webhook"\n',
            'opentak/voice/murmur.ini': 'port=64738\nice="tcp -h 127.0.0.1 -p 6502"\n',
            'rootfs/etc/nginx/sites-available/portabletak': 'location / { proxy_pass http://127.0.0.1:18081; }\n',
            'rootfs/etc/rabbitmq/rabbitmq.conf': 'listeners.tcp.default = 127.0.0.1:5672\n'
                                                 'mqtt.listeners.tcp.default = 1883\n',
            'arch/amd64/rootfs/etc/rabbitmq/rabbitmq.conf': 'listeners.tcp.default = 127.0.0.1:5672\n',
            'postgres/postgresql.conf': "#listen_addresses = 'localhost'\n",
        }
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        (root / 'postgres/postgresql.conf').chmod(0o600)
        self.assertEqual(ia.migrate(root), len(files))
        self.assertEqual(ia.migrate(root), 0)
        for name in files:
            text = (root / name).read_text()
            self.assertIsNone(LOOPBACK_USE.search(text), (name, text))
        self.assertIn("listen_addresses = '127.0.0.2'", (root / 'postgres/postgresql.conf').read_text())
        self.assertEqual((root / 'postgres/postgresql.conf').stat().st_mode & 0o777, 0o600)
        self.assertIn('mqtt.listeners.tcp.default = 1883', (root / 'rootfs/etc/rabbitmq/rabbitmq.conf').read_text())
        self.assertIn('hlsAddress: :8888', (root / 'opentak/data/mediamtx/mediamtx.yml').read_text())
        self.assertEqual(json.loads((root / 'opentak/data/config.yml').read_text())['OTS_MEDIAMTX_API_ADDRESS'],
                         'http://127.0.0.2:9997')
        self.assertEqual(list(root.rglob('*.pt-internal')), [])

    def test_missing_files_and_symlinks_are_left_alone(self):
        root = Path(tempfile.mkdtemp())
        target = root / 'elsewhere.conf'
        target.write_text('listeners.tcp.default = 127.0.0.1:5672\n')
        (root / 'rootfs/etc/rabbitmq').mkdir(parents=True)
        (root / 'rootfs/etc/rabbitmq/rabbitmq.conf').symlink_to(target)
        self.assertEqual(ia.migrate(root), 0)
        self.assertIn('127.0.0.1:5672', target.read_text())


class FeedTests(unittest.TestCase):
    def test_feed_inputs_on_internal_address(self):
        self.assertEqual(video_feeds.udp_source(5600), 'udp://127.0.0.2:5600')
        self.assertTrue(video_feeds._ours('udp://127.0.0.1:5600'))      # made by an earlier version
        self.assertTrue(video_feeds._ours('udp://127.0.0.2:5615'))
        self.assertFalse(video_feeds._ours('udp://127.0.0.1:5616'))


class SourceTreeTests(unittest.TestCase):
    """No runtime source reaches a machine-only service on 127.0.0.1 (where tailnet
    connections arrive). share/mediamtx is sealed by hash and moved at start."""

    def sources(self, base):
        for sub in ('bin', 'lib', 'api', 'chroot', 'adapters'):
            for path in (base / sub).rglob('*'):
                if (path.is_file() and '.before' not in path.name and '__pycache__' not in path.parts
                        and path.name not in ('internal_address.py', 'ots_port.py')   # the migrations
                        and 'root' not in path.parts and path.suffix not in ('.pyc', '.pbf', '.png')):
                    yield path

    def test_no_internal_service_on_tailscale_loopback(self):
        bases = [HERE] + ([PAYLOAD] if PAYLOAD.is_dir() else [])
        found = []
        for base in bases:
            for path in self.sources(base):
                try:
                    text = path.read_text()
                except (UnicodeDecodeError, OSError):
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if LOOPBACK_USE.search(line):
                        found.append('%s:%d: %s' % (path, number, line.strip()[:120]))
        self.assertEqual(found, [])

    @unittest.skipUnless(PAYLOAD.is_dir(), 'payload tree not beside the provision sources')
    def test_start_migrates_before_services(self):
        start = (PAYLOAD / 'bin/portabletak-start').read_text()
        migrate = start.index('internal_address.py migrate')
        self.assertLess(migrate, start.index('starting_postgresql'))
        self.assertIn('on 127\\.0\\.0\\.2:9997', start)

    @unittest.skipUnless(PAYLOAD.is_dir(), 'payload tree not beside the provision sources')
    def test_payload_and_provision_libraries_match(self):
        for name in ('internal_address.py', 'video_feeds.py', 'ts_relay.py',
                     'linux_bootstrap_backend.py', 'portabletak_provision.py'):
            self.assertEqual((PAYLOAD / 'lib' / name).read_bytes(), (HERE / 'lib' / name).read_bytes(), name)


if __name__ == '__main__':
    unittest.main()
