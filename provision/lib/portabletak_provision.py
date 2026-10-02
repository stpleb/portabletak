"""PortableTAK first-run state and staging controller. Standard library only.

This module never starts services or modifies a completed cartridge. Runtime
activation requires a separately installed, verified bootstrap backend.
"""
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
import uuid

VERSION = 1
COMPLETE = {'existing', 'provisioned'}
INCOMPLETE = {'new', 'provisioning', 'failed'}
STATES = COMPLETE | INCOMPLETE | {'recovery'}
MAX_INPUT = 16384
DEFAULT_TIMEZONE = 'Etc/UTC'
META_KEYS = {
    'PORTABLETAK_FORMAT', 'PORTABLETAK_ARCH', 'PORTABLETAK_CARTRIDGE_ID',
    'PORTABLETAK_INSTANCE_NAME', 'PORTABLETAK_INSTANCE_TYPE',
    'PORTABLETAK_PROVISIONING_STATE', 'PORTABLETAK_TIMEZONE',
}

class ProvisionError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def read_json(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise ProvisionError('invalid_state_file')
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, ValueError):
        raise ProvisionError('invalid_state_file') from None


def atomic_write(path, content, mode=0o600):
    if path.is_symlink():
        raise ProvisionError('symlink_rejected')
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        binary = isinstance(content, (bytes, bytearray))
        with os.fdopen(fd, 'wb' if binary else 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, data):
    atomic_write(path, json.dumps(data, sort_keys=True, indent=2) + '\n')


def metadata(path):
    if path.is_symlink():
        raise ProvisionError('symlink_rejected')
    try:
        if path.stat().st_size > MAX_INPUT:
            raise ProvisionError('invalid_metadata')
        result = {}
        for line in path.read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if not sep or key in result or key not in META_KEYS or not value:
                raise ProvisionError('invalid_metadata')
            result[key] = value
        for key in META_KEYS - {'PORTABLETAK_TIMEZONE'}:
            if key not in result:
                raise ProvisionError('invalid_metadata')
        if result['PORTABLETAK_FORMAT'] != '1':
            raise ProvisionError('unsupported_format')
        if result['PORTABLETAK_ARCH'] not in {'arm64', 'x86_64'}:
            raise ProvisionError('unsupported_architecture')
        uuid.UUID(result['PORTABLETAK_CARTRIDGE_ID'])
        if result['PORTABLETAK_PROVISIONING_STATE'] not in STATES:
            raise ProvisionError('invalid_provisioning_state')
        return result
    except (OSError, ValueError):
        raise ProvisionError('invalid_metadata') from None


def write_metadata(path, values):
    atomic_write(path, ''.join(f'{k}={v}\n' for k, v in values.items()))


def validate_input(data, zone_root=Path('/usr/share/zoneinfo')):
    if not isinstance(data, dict):
        raise ProvisionError('invalid_input')
    fields = {'instance_name', 'timezone', 'administrator_password', 'client_password'}
    if (not fields - {'timezone'} <= set(data) <= fields
            or any(not isinstance(v, str) for v in data.values())):
        raise ProvisionError('invalid_input_fields')
    # New cartridges run their server in UTC; phones show local time themselves.
    # An explicit timezone is still accepted so earlier attempts can retry.
    data = dict(data, timezone=data.get('timezone', DEFAULT_TIMEZONE))
    name = data['instance_name'].strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}[A-Za-z0-9]|[A-Za-z0-9]', name):
        raise ProvisionError('invalid_instance_name')
    zone = data['timezone']
    if not re.fullmatch(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*', zone):
        raise ProvisionError('invalid_timezone')
    zone_root = zone_root.resolve()
    zone_file = (zone_root / zone).resolve()
    if zone_root not in zone_file.parents or not zone_file.is_file():
        raise ProvisionError('invalid_timezone')
    if zone_file.read_bytes()[:4] != b'TZif':
        raise ProvisionError('invalid_timezone')
    for key in ('administrator_password', 'client_password'):
        value = data[key]
        if not 8 <= len(value) <= 128 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ProvisionError(key + '_invalid_length_or_characters')
    if data['administrator_password'] == data['client_password']:
        raise ProvisionError('passwords_must_differ')
    return dict(data, instance_name=name)


@contextlib.contextmanager
def exclusive_lock(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ProvisionError('invalid_lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProvisionError('provisioning_busy') from None
        yield
    finally:
        os.close(fd)


class Controller:
    def __init__(self, root='/mnt/asgard', lock='/tmp/portabletak/lifecycle.lock',
                 require_mount=True, expected_base_seal=None):
        self.root = Path(root).absolute()
        self.lock = Path(lock)
        self.require_mount = require_mount
        self.expected_base_seal = expected_base_seal
        self.meta = self.root / 'portabletak/cartridge.conf'
        self.state_dir = self.root / 'portabletak/provision'
        self.journal = self.state_dir / 'status.json'

    def check_paths(self):
        if self.root.resolve() != self.root:
            raise ProvisionError('symlink_rejected')
        for relative in ('portabletak', 'portabletak/cartridge.conf',
                         'portabletak/base.json', 'portabletak/provision'):
            path = self.root / relative
            if path.is_symlink():
                raise ProvisionError('symlink_rejected')
        if not self.root.is_dir():
            raise ProvisionError('cartridge_missing')
        if self.require_mount and not os.path.ismount(self.root):
            raise ProvisionError('cartridge_not_mounted')

    def timezone_root(self):
        cartridge_zones = self.root / 'rootfs/usr/share/zoneinfo'
        if cartridge_zones.is_dir():
            return cartridge_zones
        return Path('/usr/share/zoneinfo')

    def status(self):
        try:
            self.check_paths()
            values = metadata(self.meta)
            state = values['PORTABLETAK_PROVISIONING_STATE']
            result = {'ok': True, 'state': state, 'complete': state in COMPLETE,
                      'instance_name': values['PORTABLETAK_INSTANCE_NAME'],
                      'cartridge_id': values['PORTABLETAK_CARTRIDGE_ID'],
                      'can_provision': False, 'can_stage': False,
                      'controller_version': VERSION}
            if state in INCOMPLETE:
                result['can_stage'] = True
                result['blocked_reason'] = 'runtime_backend_not_validated'
            if self.journal.exists():
                journal = self.load_journal(values)
                for key in ('attempt_id', 'phase', 'updated_at', 'error'):
                    if key in journal:
                        result[key] = journal[key]
            return result
        except ProvisionError as error:
            return {'ok': False, 'state': 'unavailable', 'complete': False,
                    'can_provision': False, 'can_stage': False, 'error': error.code}

    def load_journal(self, values):
        data = read_json(self.journal)
        if (data.get('version') != VERSION
                or values['PORTABLETAK_CARTRIDGE_ID'] != data.get('source_cartridge_id')):
            raise ProvisionError('journal_cartridge_mismatch')
        try:
            uuid.UUID(data['attempt_id'])
            uuid.UUID(data['source_cartridge_id'])
        except (KeyError, ValueError, TypeError):
            raise ProvisionError('invalid_journal') from None
        return data

    def preflight(self):
        self.check_paths()
        values = metadata(self.meta)
        if uuid.UUID(values['PORTABLETAK_CARTRIDGE_ID']).int == 0:
            raise ProvisionError('unpublished_cartridge_identity')
        state = values['PORTABLETAK_PROVISIONING_STATE']
        if state not in INCOMPLETE:
            raise ProvisionError('cartridge_already_initialized')
        if values['PORTABLETAK_INSTANCE_TYPE'] != 'generic':
            raise ProvisionError('generic_cartridge_required')
        if state != 'new' and not self.journal.is_file():
            raise ProvisionError('recovery_journal_missing')
        if self.expected_base_seal:
            try:
                from clean_base import verify_seal, verify_sealed_software
                if state == 'new':
                    verify_seal(self.root, self.expected_base_seal)
                else:
                    # A resumed first run already owns its data directories.
                    verify_sealed_software(self.root, self.expected_base_seal)
            except Exception:
                raise ProvisionError('clean_base_verification_failed') from None
        # An explicit base declaration does not certify a golden image. It is
        # sufficient only for staging, which never starts or imports its code.
        base = read_json(self.root / 'portabletak/base.json')
        if base != {'format': 1, 'kind': 'portabletak-clean-base', 'architecture': values['PORTABLETAK_ARCH']}:
            raise ProvisionError('clean_base_manifest_required')
        for name in ('postgres', 'rabbitmq', 'opentak/data', 'config'):
            path = self.root / name
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != self.root.parent):
                raise ProvisionError('symlink_rejected')
            if (state == 'new' and path.exists()
                    and (not path.is_dir() or any(path.iterdir()))):
                raise ProvisionError('existing_instance_data')
        return values

    def stage(self, request, zone_root=None):
        """Prepare private artifacts only; never publishes or starts an instance."""
        data = validate_input(request, zone_root or self.timezone_root())
        with exclusive_lock(self.lock):
            values = self.preflight()
            identity = {'instance_name': data['instance_name'], 'timezone': data['timezone']}
            self.state_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(self.state_dir, 0o700)
            if self.journal.exists():
                journal = self.load_journal(values)
                if journal['input'] != identity:
                    raise ProvisionError('retry_identity_mismatch')
            else:
                journal = {'version': VERSION, 'attempt_id': str(uuid.uuid4()),
                           'source_cartridge_id': values['PORTABLETAK_CARTRIDGE_ID'],
                           'input': identity,
                           'phase': 'validated', 'updated_at': int(time.time())}
                # Save the original metadata before its first change.
                atomic_write(self.state_dir / 'cartridge.conf.before-provision', self.meta.read_text())
                write_json(self.journal, journal)
            stage = self.state_dir / journal['attempt_id']
            if stage.is_symlink():
                raise ProvisionError('symlink_rejected')
            stage.mkdir(mode=0o700, exist_ok=True)
            os.chmod(stage, 0o700)
            values['PORTABLETAK_PROVISIONING_STATE'] = 'provisioning'
            write_metadata(self.meta, values)
            try:
                secret_path = stage / 'secrets.json'
                if (stage / 'artifacts.json').exists():
                    receipt = read_json(stage / 'artifacts.json')
                    expected_files = {'config.yml', 'cartridge.conf', 'timezone', 'secrets.json'}
                    hashes = receipt.get('sha256', {})
                    if receipt.get('format') != 1 or set(hashes) != expected_files:
                        raise ProvisionError('invalid_artifact_receipt')
                    for name, digest in hashes.items():
                        artifact = stage / name
                        if artifact.is_symlink() or not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                            raise ProvisionError('staged_artifact_changed')
                if secret_path.exists():
                    private = read_json(secret_path)
                    expected_keys = {'flask_secret', 'password_salt', 'database_password', 'ca_password', 'mediamtx_token', 'rabbitmq_password', 'rabbitmq_cookie', 'node_id', 'client_password'}
                    if set(private) != expected_keys or any(not isinstance(v, str) for v in private.values()):
                        raise ProvisionError('invalid_staged_secrets')
                    generated = [value for key, value in private.items() if key != 'client_password']
                    if any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in generated) or len(set(generated)) != len(generated):
                        raise ProvisionError('invalid_staged_secrets')
                    if private.get('client_password') != data['client_password']:
                        raise ProvisionError('retry_client_password_mismatch')
                else:
                    private = {key: secrets.token_hex(32) for key in (
                        'flask_secret', 'password_salt', 'database_password',
                        'ca_password', 'mediamtx_token', 'rabbitmq_password',
                        'rabbitmq_cookie', 'node_id')}
                    private['client_password'] = data['client_password']
                    write_json(secret_path, private)
                render_stage(stage, journal, private, values)
                if 'resume_phase' in journal:
                    # The engine already committed phases for this attempt;
                    # keep its resume position rather than starting over.
                    journal.update(updated_at=int(time.time()))
                else:
                    journal.update(phase='staged', updated_at=int(time.time()))
                    journal.pop('error', None)
                write_json(self.journal, journal)
                return {'ok': True, 'phase': 'staged', 'attempt_id': journal['attempt_id'],
                        'activated': False, 'blocked_reason': 'runtime_backend_not_validated'}
            except Exception as error:
                code = error.code if isinstance(error, ProvisionError) else 'staging_failed'
                journal.update(phase='failed', error=code, updated_at=int(time.time()))
                write_json(self.journal, journal)
                values['PORTABLETAK_PROVISIONING_STATE'] = 'failed'
                write_metadata(self.meta, values)
                raise ProvisionError(code) from None


def render_stage(stage, journal, private, original):
    # JSON is also valid YAML and avoids shell/YAML interpolation of passwords.
    instance = journal['input']['instance_name']
    ca_name = 'PortableTAK-' + journal['source_cartridge_id'][:8] + '-CA'
    config = {
        'SECRET_KEY': private['flask_secret'], 'DEBUG': False,
        'SECURITY_PASSWORD_SALT': private['password_salt'],
        'SQLALCHEMY_DATABASE_URI': 'postgresql+psycopg://ots:' + private['database_password'] + '@127.0.0.1/ots',
        'OTS_DATA_FOLDER': '/asgard/opentak/data',
        'OTS_CA_FOLDER': '/asgard/opentak/data/ca',
        'OTS_CA_NAME': ca_name, 'OTS_CA_SUBJECT': '/O=PortableTAK/OU=TAK',
        'OTS_CA_PASSWORD': private['ca_password'],
        'PORTABLETAK_TRUSTSTORE_PASSWORD': private['client_password'],
        'PORTABLETAK_CLIENT_PASSWORD': private['client_password'],
        'OTS_LISTENER_ADDRESS': '127.0.0.1',
        'OTS_LISTENER_PORT': 8081, 'OTS_NODE_ID': private['node_id'],
        'OTS_RABBITMQ_SERVER_ADDRESS': '127.0.0.1',
        'OTS_RABBITMQ_USERNAME': 'portabletak',
        'OTS_RABBITMQ_PASSWORD': private['rabbitmq_password'],
        'OTS_MEDIAMTX_TOKEN': private['mediamtx_token'],
        'OTS_ENABLE_EMAIL': False, 'OTS_ENABLE_LDAP': False,
        'OTS_ENABLE_PLUGINS': False, 'OTS_ENABLE_MESHTASTIC': False,
        'PORTABLETAK_INSTANCE_NAME': instance,
    }
    write_json(stage / 'config.yml', config)
    proposed = dict(original)
    proposed.update(PORTABLETAK_INSTANCE_NAME=instance,
                    PORTABLETAK_PROVISIONING_STATE='provisioning',
                    PORTABLETAK_TIMEZONE=journal['input']['timezone'])
    write_metadata(stage / 'cartridge.conf', proposed)
    atomic_write(stage / 'timezone', journal['input']['timezone'] + '\n')
    hashes = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
              for name in ('config.yml', 'cartridge.conf', 'timezone', 'secrets.json')}
    write_json(stage / 'artifacts.json', {'format': 1, 'sha256': hashes})
