"""Production Linux operations for the durable PortableTAK bootstrap engine.

The backend is intentionally narrow: it only accepts the controller-owned
context, uses stdin for secrets, and makes each operation verify the state it
creates. Host lifecycle scripts retain ownership of mounts and service PIDs.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess

from clean_base import BaseError, verify_sealed_software
from host_profile import rootfs as host_rootfs
import arch_sync
import dashboard_site
from management_auth import ManagementAuth
import server_addresses
from portabletak_provision import ProvisionError, atomic_write, read_json

MEDIAMTX_FILES = {
    'mediamtx': '07bb8abf4d12cd5c0370c04a72d4b474a9d5f80a13950e83ac12907075b358ef',
    'mediamtx.yml': 'b2f651be78d7f2df0de3611328dc96000e6894ed80d0759c85429ccf2c325460',
    'ots-webhook.py': 'c8a65ad47c9177572087a5540f3eaafd965a030c3dc3502e392c5db85849d177',
}


ADMIN_SCRIPT = r'''import json,sys
from pathlib import Path
from flask_migrate import upgrade
from flask_security import verify_password
from opentakserver.app import create_app
from opentakserver.extensions import db
import opentakserver
request=json.load(sys.stdin)
app=create_app(cli=True)
with app.app_context():
 upgrade(directory=str(Path(opentakserver.__file__).parent/'migrations'))
 datastore=app.security.datastore
 users=db.session.query(datastore.user_model).all()
 if users:
  if len(users)!=1 or users[0].username!='administrator' or not verify_password(request['password'],users[0].password):
   raise SystemExit('administrator_state_mismatch')
 else:
  from flask_security import hash_password
  datastore.find_or_create_role(name='user',permissions={'user-read','user-write'})
  datastore.find_or_create_role(name='administrator',permissions={'administrator'})
  user=datastore.create_user(username='administrator',password=hash_password(request['password']),active=True,roles=['administrator'])
  if not verify_password(request['password'],user.password) or verify_password('password',user.password):
   db.session.rollback(); raise SystemExit('administrator_password_verification_failed')
  db.session.commit()
'''

CA_SCRIPT = r'''from opentakserver.app import create_app
from opentakserver.certificate_authority import CertificateAuthority
app=create_app(cli=True)
CertificateAuthority(app.logger,app).create_ca()
'''

DATABASE_SCRIPT = r'''import json,sys,psycopg
from psycopg import sql
request=json.load(sys.stdin)
with psycopg.connect('dbname=postgres user=postgres host=/run/postgresql',autocommit=True) as conn:
 data=conn.execute('SHOW data_directory').fetchone()[0]
 if data!='/asgard/postgres': raise SystemExit('unexpected_postgres_cluster')
 role=conn.execute("SELECT 1 FROM pg_roles WHERE rolname='ots'").fetchone()
 if role:
  conn.execute(sql.SQL('ALTER ROLE ots LOGIN PASSWORD {}').format(sql.Literal(request['password'])))
 else:
  conn.execute(sql.SQL('CREATE ROLE ots LOGIN PASSWORD {}').format(sql.Literal(request['password'])))
 database=conn.execute("SELECT datdba::regrole::text FROM pg_database WHERE datname='ots'").fetchone()
 if database and database[0]!='ots': raise SystemExit('ots_database_owner_mismatch')
 if not database: conn.execute('CREATE DATABASE ots OWNER ots')
'''


RABBITMQ_NODE = 'rabbit@localhost'


class LinuxBootstrapBackend:
    def __init__(self, expected_seal, host_root='/mnt/asgard',
                 prepare='/usr/local/portabletak/bin/portabletak-bootstrap-prepare',
                 start='/usr/local/portabletak/bin/portabletak-start',
                 health='/usr/local/portabletak/bin/portabletak-health',
                 mediamtx_source='/usr/local/portabletak/share/mediamtx', host_adapter=None,
                 runner=None, address_publisher=None):
        if not re.fullmatch(r'[0-9a-f]{64}', expected_seal or ''):
            raise ValueError('invalid_expected_seal')
        self.expected_seal = expected_seal
        self.host_root = Path(host_root).absolute()
        self.prepare = (prepare,) if isinstance(prepare, str) else tuple(prepare)
        self.start = start
        self.health = health
        self.mediamtx_source = Path(mediamtx_source).absolute()
        if host_adapter is None:
            raise ValueError('host_adapter_required')
        self.host_adapter = host_adapter
        self.runner = runner or subprocess.run
        self.address_publisher = address_publisher or server_addresses.reissue

    def _check_context(self, context):
        if context.root.absolute() != self.host_root or context.root.resolve() != self.host_root:
            raise ProvisionError('bootstrap_root_mismatch')
        expected = context.root / 'portabletak/provision' / context.attempt_id
        if context.stage.absolute() != expected or context.stage.resolve() != expected:
            raise ProvisionError('bootstrap_stage_mismatch')

    def _run(self, command, *, payload=None, timeout=120):
        encoded = None if payload is None else json.dumps(payload, separators=(',', ':')).encode()
        try:
            result = self.runner(command, input=encoded, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=timeout, check=False,
                                 env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
        except (OSError, subprocess.SubprocessError):
            raise ProvisionError('platform_command_failed') from None
        if result.returncode:
            raise ProvisionError('platform_command_failed')
        return result.stdout.decode(errors='replace')

    def _chroot(self, arguments, **kwargs):
        return self._run(['/usr/sbin/chroot', str(host_rootfs(self.host_root)), *arguments], **kwargs)

    def _account(self, name):
        passwd = host_rootfs(self.host_root) / 'etc/passwd'
        for line in passwd.read_text().splitlines():
            fields = line.split(':')
            if len(fields) >= 4 and fields[0] == name:
                return int(fields[2]), int(fields[3])
        raise ProvisionError('runtime_account_missing')

    @staticmethod
    def _mkdir(path, mode, owner=None):
        if path.is_symlink():
            raise ProvisionError('symlink_rejected')
        path.mkdir(mode=mode, parents=True, exist_ok=True)
        os.chmod(path, mode)
        if owner:
            os.chown(path, *owner)

    def _prepare(self):
        self._run(list(self.prepare))

    def _postgres_ready(self):
        result = self.runner(
            ['/usr/sbin/chroot', str(host_rootfs(self.host_root)), '/usr/bin/pg_isready',
             '-h', '/run/postgresql', '-p', '5432'],
            input=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
            check=False, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
        return result.returncode == 0

    def _start_postgres(self):
        self._prepare()
        socket_dir = host_rootfs(self.host_root) / 'run/postgresql'
        self._mkdir(socket_dir, 0o2775, self._account('postgres'))
        if not self._postgres_ready():
            self._chroot(['/usr/sbin/runuser', '-u', 'postgres', '--',
                          '/usr/lib/postgresql/16/bin/pg_ctl', '-D', '/asgard/postgres',
                          '-l', '/asgard/postgres/postgresql.log', '-w', '-t', '30', 'start'])
        if not self._postgres_ready():
            raise ProvisionError('postgres_not_ready')

    def verify_base(self, context):
        self._check_context(context)
        try:
            # The controller's preflight ran the full clean-tree check for a new
            # cartridge; here only software is compared so a resumed run with
            # runtime data and mounts (/proc, /sys) still verifies.
            verify_sealed_software(context.root, self.expected_seal)
        except (BaseError, OSError):
            raise ProvisionError('clean_base_verification_failed') from None
        return {'seal': self.expected_seal, 'verified': True}

    def initialize_storage(self, context):
        self._check_context(context)
        postgres = self._account('postgres')
        rabbitmq = self._account('rabbitmq')
        for relative, mode, owner in (
                ('postgres', 0o700, postgres), ('rabbitmq', 0o700, rabbitmq),
                ('opentak/data', 0o700, None), ('opentak/run', 0o700, None),
                ('config', 0o700, None), ('mediamtx', 0o700, None)):
            self._mkdir(context.root / relative, mode, owner)
        config = context.root / 'opentak/data/config.yml'
        try:
            settings = json.loads((context.stage / 'config.yml').read_text())
        except (OSError, ValueError):
            raise ProvisionError('invalid_staged_configuration') from None
        adapter_settings = self.host_adapter.identity_settings(context)
        if (not isinstance(settings, dict) or not isinstance(adapter_settings, dict)
                or any(key in settings for key in adapter_settings)):
            raise ProvisionError('invalid_host_adapter_settings')
        settings.update(adapter_settings)
        atomic_write(config, json.dumps(settings, sort_keys=True, indent=2) + '\n', 0o600)
        timezone = (context.stage / 'timezone').read_text().strip()
        zone = context.root / 'rootfs/usr/share/zoneinfo' / timezone
        if not zone.is_file() or zone.read_bytes()[:4] != b'TZif':
            raise ProvisionError('invalid_timezone')
        atomic_write(context.root / 'opentak/data/timezone', timezone + '\n', 0o600)
        for name, expected in MEDIAMTX_FILES.items():
            source = self.mediamtx_source / name
            if (source.is_symlink() or not source.is_file()
                    or hashlib.sha256(source.read_bytes()).hexdigest() != expected):
                raise ProvisionError('mediamtx_source_verification_failed')
            mode = 0o700 if name in {'mediamtx', 'ots-webhook.py'} else 0o600
            atomic_write(context.root / 'mediamtx' / name, source.read_bytes(), mode)
        return {'directories': 6, 'config': 'installed'}

    def initialize_database(self, context):
        self._check_context(context)
        self._prepare()
        pgdata = context.root / 'postgres'
        if not (pgdata / 'PG_VERSION').exists():
            if any(pgdata.iterdir()):
                raise ProvisionError('postgres_storage_not_empty')
            self._chroot(['/usr/sbin/runuser', '-u', 'postgres', '--',
                          '/usr/lib/postgresql/16/bin/initdb', '-D', '/asgard/postgres',
                          '--auth-local=peer', '--auth-host=scram-sha-256'])
        elif (pgdata / 'PG_VERSION').read_text().strip() != '16':
            raise ProvisionError('unsupported_postgres_cluster')
        self._start_postgres()
        self._chroot(['/usr/sbin/runuser', '-u', 'postgres', '--',
                      '/asgard/opentak/venv/bin/python', '-c', DATABASE_SCRIPT],
                     payload={'password': context.secrets['database_password']})
        return {'postgresql': '16', 'database': 'ots'}

    def initialize_administrator(self, context, password):
        self._check_context(context)
        self._start_postgres()
        self._chroot(['/usr/bin/env', 'OTS_DATA_FOLDER=/asgard/opentak/data',
                      '/asgard/opentak/venv/bin/python', '-c', ADMIN_SCRIPT],
                     payload={'password': password}, timeout=240)
        # The dashboard login is the same administrator, stored only as a
        # scrypt hash on the cartridge. A retry must present the same password.
        management = context.root / 'config/management'
        auth = ManagementAuth(management / 'credential.json', management / 'session-key.json')
        auth.initialize(password, secrets.token_hex(32))
        if not auth.verify_password('administrator', password):
            raise ProvisionError('management_login_verification_failed')
        return {'administrator': 'verified', 'default_password': 'rejected',
                'management_login': 'initialized'}

    def initialize_certificates(self, context):
        self._check_context(context)
        self._prepare()
        self._chroot(['/usr/bin/env', 'OTS_DATA_FOLDER=/asgard/opentak/data',
                      '/asgard/opentak/venv/bin/python', '-c', CA_SCRIPT], timeout=240)
        ca = context.root / 'opentak/data/ca/ca.pem'
        cert = context.root / 'opentak/data/ca/certs/opentakserver/opentakserver.pem'
        key = context.root / 'opentak/data/ca/certs/opentakserver/opentakserver.nopass.key'
        if not all(path.is_file() and not path.is_symlink() for path in (ca, cert, key)):
            raise ProvisionError('certificate_artifacts_missing')
        # The CA module issues the server certificate for one address; re-sign it
        # (same key) for every address the adapter says clients may use.
        advertised, addresses = self.host_adapter.validated_server_addresses(context)
        record = self.address_publisher(context.root, addresses, advertised)
        return {'ca_sha256': hashlib.sha256(ca.read_bytes()).hexdigest(), 'server_certificate': True,
                'server_addresses': record['addresses'], 'advertised_address': record['advertise']}

    def initialize_messaging(self, context):
        self._check_context(context)
        rabbit = self._account('rabbitmq')
        root = context.root / 'rabbitmq'
        cookie = root / '.erlang.cookie'
        atomic_write(cookie, context.secrets['rabbitmq_cookie'], 0o400)
        os.chown(cookie, *rabbit)
        salt = hashlib.sha256(b'portabletak-rabbitmq-hash\0' +
                              context.secrets['rabbitmq_password'].encode()).digest()[:4]
        digest = hashlib.sha256(salt + context.secrets['rabbitmq_password'].encode()).digest()
        encoded = base64.b64encode(salt + digest).decode()
        definitions = {'users': [{'name': 'portabletak', 'password_hash': encoded,
                                  'hashing_algorithm': 'rabbit_password_hashing_sha256', 'tags': []}],
                       'vhosts': [{'name': '/'}],
                       'permissions': [{'user': 'portabletak', 'vhost': '/',
                                        'configure': '.*', 'write': '.*', 'read': '.*'}]}
        etc = context.root / 'rootfs/etc/rabbitmq'
        self._mkdir(etc, 0o750, rabbit)
        self._mkdir(context.root / 'rootfs/var/log/rabbitmq', 0o750, rabbit)
        definition_path = etc / 'portabletak-definitions.json'
        atomic_write(definition_path, json.dumps(definitions, sort_keys=True) + '\n', 0o640)
        os.chown(definition_path, *rabbit)
        config_path = etc / 'rabbitmq.conf'
        atomic_write(config_path,
                     'listeners.tcp.default = 127.0.0.1:5672\n'
                     'definitions.import_backend = local_filesystem\n'
                     'definitions.local.path = /etc/rabbitmq/portabletak-definitions.json\n', 0o640)
        os.chown(config_path, *rabbit)
        arch_sync.sync(context.root)
        # RabbitMQ keeps its data under mnesia/<node>; a host-derived node name
        # (rabbit@Node02) would strand the data when the cartridge moves hosts.
        atomic_write(context.root / 'config/rabbitmq-nodename', RABBITMQ_NODE + '\n', 0o644)
        return {'rabbitmq': 'configured', 'listener': '127.0.0.1:5672',
                'node': RABBITMQ_NODE}

    def configure_services(self, context):
        self._check_context(context)
        self._prepare()
        binding = self.host_adapter.validated_binding(context)
        media = context.root / 'mediamtx/mediamtx'
        if (not media.is_file() or media.is_symlink() or not os.access(media, os.X_OK)
                or hashlib.sha256(media.read_bytes()).hexdigest() != MEDIAMTX_FILES['mediamtx']):
            raise ProvisionError('mediamtx_runtime_missing')
        template = context.root / 'mediamtx/mediamtx.yml'
        webhook = context.root / 'mediamtx/ots-webhook.py'
        if (template.is_symlink() or not template.is_file() or webhook.is_symlink()
                or not webhook.is_file() or not os.access(webhook, os.X_OK)):
            raise ProvisionError('mediamtx_supplement_incomplete')
        media_config = context.root / 'opentak/data/mediamtx/mediamtx.yml'
        self._mkdir(media_config.parent, 0o700)
        atomic_write(media_config, template.read_text(), 0o600)
        token = context.root / 'config/secrets/ots-mediamtx-token'
        self._mkdir(token.parent, 0o700)
        atomic_write(token, context.secrets['mediamtx_token'] + '\n', 0o600)
        ca_root = '/asgard/opentak/data/ca/certs/opentakserver'
        nginx = context.root / 'rootfs/etc/nginx/sites-available/portabletak'
        self._mkdir(nginx.parent, 0o755)
        self._mkdir(context.root / 'rootfs/var/log/nginx', 0o755)
        atomic_write(nginx,
            'server { listen 8443 ssl; ' + dashboard_site.TAK_TLS +
            'ssl_certificate ' + ca_root + '/opentakserver.pem; '
            'ssl_certificate_key ' + ca_root + '/opentakserver.nopass.key; '
            'location / { proxy_pass http://127.0.0.1:18081; '
            'proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto https; ' + dashboard_site.CERT_HEADER + '} }\n'
            # Certificate enrollment for QR onboarding (ATAK/iTAK use 8446).
            # Only the Marti TLS enrollment API is reachable on this port.
            'server { listen 8446 ssl; ' + dashboard_site.TAK_TLS +
            'ssl_certificate ' + ca_root + '/opentakserver.pem; '
            'ssl_certificate_key ' + ca_root + '/opentakserver.nopass.key; '
            'location /Marti/api/tls/ { proxy_pass http://127.0.0.1:18081; '
            'proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto https; ' + dashboard_site.CERT_HEADER + '} '
            'location / { return 404; } }\n', 0o644)
        enabled = context.root / 'rootfs/etc/nginx/sites-enabled/portabletak'
        self._mkdir(enabled.parent, 0o755)
        if enabled.is_symlink():
            if os.readlink(enabled) != '/etc/nginx/sites-available/portabletak':
                raise ProvisionError('nginx_site_link_mismatch')
        elif enabled.exists():
            raise ProvisionError('nginx_site_link_mismatch')
        else:
            enabled.symlink_to('/etc/nginx/sites-available/portabletak')
        arch_sync.sync(context.root)
        # nginx -t binds the listener, so the service address must be on this
        # host first. The same adapter claim runs again, as a no-op, at start.
        claim = self.host_adapter.claim_command()
        if claim:
            self._run(list(claim), timeout=30)
        self._chroot(['/usr/sbin/nginx', '-t'])
        return {'network': binding['address'] + '/' + str(binding['prefix']),
                'runtime_profile': binding['profile'],
                'service_address': 'claimed' if claim else 'host_managed',
                'nginx': 'verified', 'https': '8443', 'enrollment': '8446',
                'mediamtx': 'present'}

    def start_services(self, context):
        self._check_context(context)
        self._run([self.start], timeout=240)
        return {'lifecycle': 'started'}

    def verify_health(self, context):
        self._check_context(context)
        output = self._run([self.health], timeout=30)
        states = {}
        for line in output.splitlines():
            key, separator, value = line.partition('=')
            if separator:
                states[key] = value
        required = ('PORTABLETAK_POSTGRESQL', 'PORTABLETAK_RABBITMQ',
                    'PORTABLETAK_MEDIAMTX', 'PORTABLETAK_OTS',
                    'PORTABLETAK_COT', 'PORTABLETAK_EUD', 'PORTABLETAK_NGINX')
        if any(states.get(name) != 'RUNNING' for name in required):
            raise ProvisionError('service_health_failed')
        return {'required_services': len(required), 'healthy': True}
