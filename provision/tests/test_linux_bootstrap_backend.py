import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap_engine import BootstrapContext
from clean_base import seal
from linux_bootstrap_backend import LinuxBootstrapBackend, MEDIAMTX_FILES
from portabletak_provision import ProvisionError
from adapters.openmanet import OpenManetAdapter


class LinuxBackendTests(unittest.TestCase):
    def setUp(self):
        self.chown = patch('linux_bootstrap_backend.os.chown')
        self.chown.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'cartridge'
        (self.root / 'portabletak').mkdir(parents=True)
        (self.root / 'portabletak/cartridge.conf').write_text('identity\n')
        (self.root / 'rootfs/etc').mkdir(parents=True)
        (self.root / 'rootfs/etc/passwd').write_text(
            'postgres:x:102:104::/var/lib/postgresql:/bin/sh\n'
            'rabbitmq:x:103:105::/var/lib/rabbitmq:/bin/sh\n')
        (self.root / 'rootfs/usr/share/zoneinfo/Etc').mkdir(parents=True)
        (self.root / 'rootfs/usr/share/zoneinfo/Etc/UTC').write_bytes(b'TZif' + b'\0' * 32)
        provenance = {'ubuntu_archive': 'test.tar.gz', 'ubuntu_sha256': 'a' * 64,
                      'apt_packages': {'postgresql': '16'},
                      'python_packages': {'opentakserver': '1.7.13'}}
        self.seal = seal(self.root, provenance)
        self.attempt = '11111111-1111-4111-8111-111111111111'
        self.stage = self.root / 'portabletak/provision' / self.attempt
        self.stage.mkdir(parents=True)
        (self.stage / 'config.yml').write_text('{}\n')
        (self.stage / 'timezone').write_text('Etc/UTC\n')
        self.secrets = {key: key + '-secret' for key in
                        ('database_password', 'rabbitmq_password', 'rabbitmq_cookie',
                         'mediamtx_token', 'ca_password', 'client_password')}
        self.context = BootstrapContext(self.root, self.stage, self.attempt,
                                        {'instance_name': 'Test'}, self.secrets)
        self.outputs = {}
        self.media_source = Path(self.temp.name) / 'media-source'
        self.media_source.mkdir()
        vendor = Path(__file__).resolve().parents[1] / 'vendor/mediamtx'
        for name in MEDIAMTX_FILES:
            (self.media_source / name).write_bytes((vendor / name).read_bytes())
        self.commands = []
        def runner(command, **kwargs):
            self.commands.append(tuple(command))
            output = self.outputs.get(tuple(command), b'')
            return subprocess.CompletedProcess(command, 0, output, b'')
        self.published = []
        def publisher(root, addresses, advertise):
            self.published.append((Path(root), list(addresses), advertise))
            return {'addresses': list(addresses), 'advertise': advertise}
        self.backend = LinuxBootstrapBackend(self.seal, host_root=self.root,
                                             mediamtx_source=self.media_source,
                                             host_adapter=OpenManetAdapter(), runner=runner,
                                             address_publisher=publisher)

    def tearDown(self):
        self.chown.stop()
        self.temp.cleanup()

    def test_staged_software_seal_verified(self):
        result = self.backend.verify_base(self.context)
        self.assertTrue(result['verified'])
        # Runtime additions are allowed here; the controller's preflight runs
        # the strict no-extra-files check for a new cartridge beforehand.
        (self.root / 'rootfs/unexpected').write_text('runtime')
        self.assertTrue(self.backend.verify_base(self.context)['verified'])
        (self.root / 'rootfs/etc/passwd').write_text('changed\n')
        with self.assertRaisesRegex(ProvisionError, 'clean_base_verification_failed'):
            self.backend.verify_base(self.context)

    def test_storage_layout_is_private_and_retry_safe(self):
        first = self.backend.initialize_storage(self.context)
        second = self.backend.initialize_storage(self.context)
        self.assertEqual(first, second)
        installed = json.loads((self.root / 'opentak/data/config.yml').read_text())
        self.assertEqual(installed['OTS_SERVER_ADDRESS'], '10.41.250.10')
        self.assertEqual((self.root / 'opentak/data/timezone').read_text(), 'Etc/UTC\n')
        self.assertEqual(stat.S_IMODE((self.root / 'postgres').stat().st_mode), 0o700)

    def test_administrator_also_initializes_management_login(self):
        password = 'chosen-admin-password'
        self.backend.initialize_storage(self.context)
        evidence = self.backend.initialize_administrator(self.context, password)
        self.assertEqual(evidence['management_login'], 'initialized')
        self.assertNotIn(password, json.dumps(evidence))
        credential = self.root / 'config/management/credential.json'
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        self.assertNotIn(password, credential.read_text())
        # Retry after an interruption with the same password is accepted.
        self.backend.initialize_administrator(self.context, password)
        with self.assertRaisesRegex(ProvisionError, 'management_auth_already_initialized'):
            self.backend.initialize_administrator(self.context, 'different-password-1')

    def test_messaging_records_host_independent_rabbitmq_node(self):
        self.backend.initialize_storage(self.context)
        evidence = self.backend.initialize_messaging(self.context)
        self.assertEqual(evidence['node'], 'rabbit@localhost')
        self.assertEqual((self.root / 'config/rabbitmq-nodename').read_text(), 'rabbit@localhost\n')

    def test_messaging_credentials_are_idempotent_and_private(self):
        self.backend.initialize_storage(self.context)
        self.backend.initialize_messaging(self.context)
        definitions = self.root / 'rootfs/etc/rabbitmq/portabletak-definitions.json'
        before = definitions.read_bytes()
        self.backend.initialize_messaging(self.context)
        self.assertEqual(before, definitions.read_bytes())
        self.assertNotIn(self.secrets['rabbitmq_password'], definitions.read_text())
        self.assertEqual(stat.S_IMODE((self.root / 'rabbitmq/.erlang.cookie').stat().st_mode), 0o400)

    def test_service_configuration_requires_verified_mediamtx_binary(self):
        self.backend.initialize_storage(self.context)
        (self.root / 'mediamtx/mediamtx').write_text('changed')
        with self.assertRaisesRegex(ProvisionError, 'mediamtx_runtime_missing'):
            self.backend.configure_services(self.context)
        self.backend.initialize_storage(self.context)
        result = self.backend.configure_services(self.context)
        self.assertEqual(result['network'], '10.41.250.10/32')
        self.assertEqual(result['runtime_profile'], 'openmanet')
        host = json.loads((self.root / 'config/host.json').read_text())
        self.assertEqual(host['runtime_profile'], 'openmanet')
        self.assertTrue((self.root / 'rootfs/etc/nginx/sites-enabled/portabletak').is_symlink())
        site = (self.root / 'rootfs/etc/nginx/sites-available/portabletak').read_text()
        self.assertIn('listen 8443 ssl;', site)
        self.assertIn('listen 8446 ssl;', site)
        enrollment = site[site.index('listen 8446'):]
        self.assertIn('location /Marti/api/tls/ { proxy_pass http://127.0.0.1:18081;', enrollment)
        self.assertIn('location / { return 404; }', enrollment)
        self.assertEqual(result['enrollment'], '8446')
        self.assertNotIn('10.41.250.10:84', site)

    def test_certificates_cover_every_adapter_address(self):
        self.backend.initialize_storage(self.context)
        server = self.root / 'opentak/data/ca/certs/opentakserver'
        server.mkdir(parents=True)
        (self.root / 'opentak/data/ca/ca.pem').write_text('ca')
        (server / 'opentakserver.pem').write_text('cert')
        (server / 'opentakserver.nopass.key').write_text('key')
        evidence = self.backend.initialize_certificates(self.context)
        self.assertEqual(self.published, [(self.root, ['10.41.254.1', '10.41.250.10'], '10.41.254.1')])
        self.assertEqual(evidence['advertised_address'], '10.41.254.1')
        self.assertEqual(evidence['server_addresses'], ['10.41.254.1', '10.41.250.10'])

    def test_service_address_claimed_before_nginx_validation(self):
        self.backend.initialize_storage(self.context)
        self.commands.clear()
        result = self.backend.configure_services(self.context)
        claim = ('/usr/local/portabletak/adapters/openmanet/network', 'claim')
        nginx = ('/usr/sbin/chroot', str(self.root / 'rootfs'), '/usr/sbin/nginx', '-t')
        self.assertLess(self.commands.index(claim), self.commands.index(nginx))
        self.assertEqual(result['service_address'], 'claimed')

    def test_failed_claim_stops_before_nginx(self):
        self.backend.initialize_storage(self.context)
        claim = ('/usr/local/portabletak/adapters/openmanet/network', 'claim')
        def refusing(command, **kwargs):
            self.commands.append(tuple(command))
            return subprocess.CompletedProcess(command, 22 if tuple(command) == claim else 0, b'', b'')
        self.backend.runner = refusing
        self.commands.clear()
        with self.assertRaisesRegex(ProvisionError, 'platform_command_failed'):
            self.backend.configure_services(self.context)
        self.assertNotIn('/usr/sbin/nginx', [part for c in self.commands for part in c])

    def test_health_gate_requires_every_service(self):
        command = (self.backend.health,)
        services = ('POSTGRESQL', 'RABBITMQ', 'MEDIAMTX', 'OTS', 'COT', 'EUD', 'NGINX')
        self.outputs[command] = ''.join(
            'PORTABLETAK_' + service + '=RUNNING\n' for service in services).encode()
        self.assertTrue(self.backend.verify_health(self.context)['healthy'])
        self.outputs[command] = b'PORTABLETAK_POSTGRESQL=RUNNING\n'
        with self.assertRaisesRegex(ProvisionError, 'service_health_failed'):
            self.backend.verify_health(self.context)


if __name__ == '__main__':
    unittest.main()
