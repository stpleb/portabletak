import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import dashboard_site


class DashboardSiteTests(unittest.TestCase):
    def test_site_written_once_and_proxies_to_local_api(self):
        root = Path(tempfile.mkdtemp())
        (root / 'rootfs/etc/nginx/sites-available').mkdir(parents=True)
        (root / 'rootfs/etc/nginx/sites-enabled').mkdir(parents=True)
        self.assertTrue(dashboard_site.ensure(root))
        self.assertFalse(dashboard_site.ensure(root))
        text = (root / 'rootfs' / dashboard_site.SITE).read_text()
        self.assertIn('listen 8444 ssl;', text)
        self.assertIn('proxy_pass http://127.0.0.1:8090;', text)
        self.assertIn('X-Forwarded-For $remote_addr;', text)  # set, never appended (no spoofing)
        self.assertEqual(os.readlink(root / 'rootfs' / dashboard_site.LINK),
                         '/etc/nginx/sites-available/portabletak-dashboard')

    def test_dual_chip_tree_gets_copy(self):
        root = Path(tempfile.mkdtemp())
        for tree in ('rootfs', 'arch/amd64/rootfs'):
            (root / tree / 'etc/nginx/sites-available').mkdir(parents=True)
            (root / tree / 'etc/nginx/sites-enabled').mkdir(parents=True)
        import arch_sync
        dashboard_site.ensure(root)
        arch_sync.sync(root, 'x86_64')
        self.assertTrue((root / 'arch/amd64/rootfs' / dashboard_site.SITE).is_file())



class TakSiteTests(unittest.TestCase):
    OLD = ('server { listen 8443 ssl; ssl_certificate a; location / { proxy_pass http://127.0.0.1:8081; '
           'proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto https; } }\n'
           'server { listen 8446 ssl; ssl_certificate a; location /Marti/api/tls/ { proxy_pass http://127.0.0.1:8081; '
           'proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto https; } location / { return 404; } }\n')

    def test_upgrade_adds_client_cert_passthrough_once(self):
        new = dashboard_site.upgrade_tak_site(self.OLD)
        self.assertEqual(new.count('ssl_verify_client optional;'), 2)
        # One per TAK server block, plus the voice history location on 8443.
        self.assertEqual(new.count('X-Ssl-Cert $ssl_client_escaped_cert;'), 2 + new.count(dashboard_site.VOICE))
        self.assertEqual(new.count('underscores_in_headers on;'), 2)
        self.assertEqual(dashboard_site.upgrade_tak_site(new), new)


class RemoteBlockTests(unittest.TestCase):
    SITE = ('server { listen 8443 ssl; ssl_certificate /c/opentakserver.pem; a; }\n'
            'server { listen 8446 ssl; ssl_certificate /c/opentakserver.pem; b; }\n')

    def test_remote_blocks_added_idempotent_and_removed(self):
        import dashboard_site
        text = dashboard_site.remote_blocks(self.SITE, '100.83.127.3')
        self.assertIn('server { listen 100.83.127.3:8446 ssl; ssl_certificate /c/opentakserver-remote.pem; b; }', text)
        self.assertIn('server { listen 127.0.0.1:8443 ssl; ssl_certificate /c/opentakserver-remote.pem; a; }', text)
        self.assertTrue(text.startswith(self.SITE))
        self.assertEqual(dashboard_site.remote_blocks(text, '100.83.127.3'), text)
        self.assertEqual(dashboard_site.remote_blocks(text, None), self.SITE)
        moved = dashboard_site.remote_blocks(text, '100.64.0.9')
        self.assertNotIn('100.83.127.3', moved)
        self.assertIn('listen 100.64.0.9:8443 ssl;', moved)


class ClientInfoTests(unittest.TestCase):
    def test_client_info_written_for_the_server_chroot(self):
        import json
        import dashboard_site
        with tempfile.TemporaryDirectory() as root:
            for sub in ('config', 'opentak/data'):
                os.makedirs(os.path.join(root, sub))
            with open(os.path.join(root, 'config/server-addresses.json'), 'w') as f:
                json.dump({'advertise': '192.168.1.189', 'addresses': ['192.168.1.189', '100.83.127.3']}, f)
            with open(os.path.join(root, 'config/tak-network.conf'), 'w') as f:
                f.write('PORTABLETAK_SERVICE_NAME="Node02 TAK"\n')
            with open(os.path.join(root, 'opentak/data/config.yml'), 'w') as f:
                f.write('{}')
            self.assertTrue(dashboard_site.write_client_info(root))
            with open(os.path.join(root, dashboard_site.CLIENT_INFO)) as f:
                self.assertEqual(json.load(f), {'name': 'Node02 TAK', 'advertise': '192.168.1.189', 'remote': '100.83.127.3'})
            self.assertFalse(dashboard_site.write_client_info(root))


class HlsTests(unittest.TestCase):
    def test_hls_location_added_to_8443_only_once(self):
        import dashboard_site
        site = ('server { listen 8443 ssl; location / { proxy_pass http://127.0.0.1:8081; } }\n'
                'server { listen 8446 ssl; location / { return 404; } }\n')
        text = dashboard_site.upgrade_tak_site(site)
        self.assertEqual(text.count(dashboard_site.HLS), 1)
        self.assertLess(text.index(dashboard_site.HLS), text.index('listen 8446'))
        self.assertEqual(dashboard_site.upgrade_tak_site(text), text)


class VoiceTests(unittest.TestCase):
    def test_voice_location_added_to_8443_only_once_and_passes_the_certificate(self):
        import dashboard_site
        site = ('server { listen 8443 ssl; location / { proxy_pass http://127.0.0.1:8081; } }\n'
                'server { listen 8446 ssl; location / { return 404; } }\n')
        text = dashboard_site.upgrade_tak_site(site)
        self.assertEqual(text.count(dashboard_site.VOICE), 1)
        self.assertLess(text.index(dashboard_site.VOICE), text.index('listen 8446'))
        self.assertIn('X-Ssl-Verify $ssl_client_verify', dashboard_site.VOICE)
        self.assertIn('unix:/asgard/opentak/voice/api.sock', dashboard_site.VOICE)
        self.assertEqual(dashboard_site.upgrade_tak_site(text), text)

    def test_older_voice_location_is_replaced_not_duplicated(self):
        import dashboard_site
        old = ('server { listen 8443 ssl; location /voice/ { proxy_pass http://127.0.0.1:18096/; proxy_buffering off; } '
               'location / { proxy_pass http://127.0.0.1:8081; } }\n')
        text = dashboard_site.upgrade_tak_site(old)
        self.assertEqual(text.count('location /voice/'), 1)
        self.assertNotIn('18096', text)


if __name__ == '__main__':
    unittest.main()
