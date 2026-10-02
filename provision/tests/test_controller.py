import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import portabletak_provision as pt

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'cartridge'
        (self.root / 'portabletak').mkdir(parents=True)
        self.values = {
            'PORTABLETAK_FORMAT': '1', 'PORTABLETAK_ARCH': 'arm64',
            'PORTABLETAK_CARTRIDGE_ID': str(uuid.uuid4()),
            'PORTABLETAK_INSTANCE_NAME': 'PortableTAK Server',
            'PORTABLETAK_INSTANCE_TYPE': 'generic',
            'PORTABLETAK_PROVISIONING_STATE': 'new',
        }
        self.controller = pt.Controller(self.root, self.root.parent / 'operation.lock', require_mount=False)
        pt.write_metadata(self.controller.meta, self.values)
        pt.write_json(self.root / 'portabletak/base.json', {'format': 1, 'kind': 'portabletak-clean-base', 'architecture': 'arm64'})
        self.request = {'instance_name': 'Test Team', 'timezone': 'Etc/UTC',
                        'administrator_password': 'Admin-test-password-123!',
                        'client_password': "Client-test-&<>;password123"}
        self.zones = self.root.parent / 'zones'
        (self.zones / 'Etc').mkdir(parents=True)
        (self.zones / 'Etc/UTC').write_bytes(b'TZif' + b'\x00' * 40)

    def tearDown(self):
        self.temp.cleanup()

    def stage(self, request=None):
        return self.controller.stage(request or self.request, self.zones)

    def test_existing_refused_without_writes(self):
        for state in ('existing', 'provisioned', 'recovery'):
            self.values['PORTABLETAK_PROVISIONING_STATE'] = state
            pt.write_metadata(self.controller.meta, self.values)
            before = self.controller.meta.read_bytes()
            with self.assertRaisesRegex(pt.ProvisionError, 'cartridge_already_initialized'):
                self.stage()
            self.assertEqual(before, self.controller.meta.read_bytes())
            self.assertFalse(self.controller.state_dir.exists())

    def test_personal_new_refused(self):
        self.values['PORTABLETAK_INSTANCE_TYPE'] = 'personal'
        pt.write_metadata(self.controller.meta, self.values)
        with self.assertRaisesRegex(pt.ProvisionError, 'generic_cartridge_required'):
            self.stage()

    def test_fresh_secrets_and_no_admin_plaintext(self):
        result = self.stage()
        stage = self.controller.state_dir / result['attempt_id']
        private = pt.read_json(stage / 'secrets.json')
        generated = [v for k, v in private.items() if k != 'client_password']
        self.assertEqual(len(set(generated)), len(generated))
        for path in self.controller.state_dir.rglob('*'):
            if path.is_file():
                self.assertNotIn(self.request['administrator_password'].encode(), path.read_bytes())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertFalse(result['activated'])
        self.assertEqual(pt.metadata(self.controller.meta)['PORTABLETAK_PROVISIONING_STATE'], 'provisioning')
        self.assertEqual(pt.metadata(stage / 'cartridge.conf')['PORTABLETAK_CARTRIDGE_ID'], self.values['PORTABLETAK_CARTRIDGE_ID'])

    def test_retry_preserves_secrets_uuid_and_attempt(self):
        first = self.stage()
        stage = self.controller.state_dir / first['attempt_id']
        before = {p.name: p.read_bytes() for p in stage.iterdir()}
        second = self.stage()
        self.assertEqual(first, second)
        self.assertEqual(before, {p.name: p.read_bytes() for p in stage.iterdir()})

    def test_retry_different_identity_refused(self):
        self.stage()
        with self.assertRaisesRegex(pt.ProvisionError, 'retry_identity_mismatch'):
            self.stage(dict(self.request, instance_name='Other Team'))

    def test_retry_different_client_password_fails_closed(self):
        result = self.stage()
        secret_path = self.controller.state_dir / result['attempt_id'] / 'secrets.json'
        before = secret_path.read_bytes()
        with self.assertRaisesRegex(pt.ProvisionError, 'retry_client_password_mismatch'):
            self.stage(dict(self.request, client_password='Another-password-1234'))
        self.assertEqual(before, secret_path.read_bytes())
        self.assertEqual(self.controller.status()['state'], 'failed')
        self.assertEqual(self.stage()['phase'], 'staged')

    def test_failure_retries_with_same_secrets(self):
        with patch.object(pt, 'render_stage', side_effect=RuntimeError('secret must not be logged')):
            with self.assertRaisesRegex(pt.ProvisionError, 'staging_failed'):
                self.stage()
        state = self.controller.status()
        self.assertEqual(state['state'], 'failed')
        self.assertNotIn('secret must not be logged', json.dumps(state))
        before = (self.controller.state_dir / state['attempt_id'] / 'secrets.json').read_bytes()
        self.stage()
        self.assertEqual(before, (self.controller.state_dir / state['attempt_id'] / 'secrets.json').read_bytes())

    def test_journal_cartridge_mismatch_refused(self):
        self.stage()
        self.values['PORTABLETAK_CARTRIDGE_ID'] = str(uuid.uuid4())
        pt.write_metadata(self.controller.meta, self.values)
        with self.assertRaisesRegex(pt.ProvisionError, 'journal_cartridge_mismatch'):
            self.stage()

    def test_missing_journal_refused_for_failed(self):
        self.values['PORTABLETAK_PROVISIONING_STATE'] = 'failed'
        pt.write_metadata(self.controller.meta, self.values)
        with self.assertRaisesRegex(pt.ProvisionError, 'recovery_journal_missing'):
            self.stage()

    def test_symlink_targets_rejected(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (self.root / 'config').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(pt.ProvisionError, 'symlink_rejected'):
            self.stage()
        self.assertEqual(list(outside.iterdir()), [])

    def test_staging_symlink_rejected(self):
        self.controller.state_dir.symlink_to(self.root.parent)
        with self.assertRaisesRegex(pt.ProvisionError, 'symlink_rejected'):
            self.stage()

    def test_secret_symlink_rejected(self):
        result = self.stage()
        path = self.controller.state_dir / result['attempt_id'] / 'secrets.json'
        path.unlink()
        outside = self.root.parent / 'outside.json'
        outside.write_text('{}')
        path.symlink_to(outside)
        with self.assertRaises(pt.ProvisionError):
            self.stage()
        self.assertEqual(outside.read_text(), '{}')

    def test_existing_data_refused(self):
        for name in ('postgres', 'rabbitmq', 'config', 'opentak/data'):
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            (path / 'existing').touch()
            with self.assertRaisesRegex(pt.ProvisionError, 'existing_instance_data'):
                self.stage()
            (path / 'existing').unlink()

    def test_timezone_optional_defaults_to_utc(self):
        request = {k: v for k, v in self.request.items() if k != 'timezone'}
        data = pt.validate_input(request, self.zones)
        self.assertEqual(data['timezone'], 'Etc/UTC')
        result = self.stage(request)
        journal = pt.read_json(self.controller.journal)
        self.assertEqual(journal['input']['timezone'], 'Etc/UTC')
        stage = self.controller.state_dir / result['attempt_id']
        self.assertEqual((stage / 'timezone').read_text(), 'Etc/UTC\n')

    def test_explicit_timezone_still_validated(self):
        self.assertEqual(pt.validate_input(self.request, self.zones)['timezone'], 'Etc/UTC')
        with self.assertRaisesRegex(pt.ProvisionError, 'invalid_timezone'):
            pt.validate_input(dict(self.request, timezone='Central'), self.zones)

    def test_missing_or_extra_fields_refused(self):
        for bad in ({k: v for k, v in self.request.items() if k != 'client_password'},
                    dict(self.request, extra='x')):
            with self.assertRaisesRegex(pt.ProvisionError, 'invalid_input_fields'):
                pt.validate_input(bad, self.zones)

    def test_retry_keeps_data_written_by_the_same_attempt(self):
        first = self.stage()
        for name in ('postgres', 'rabbitmq', 'config', 'opentak/data'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
            (self.root / name / 'runtime').write_text('attempt data')
        self.values = pt.metadata(self.controller.meta)
        self.values['PORTABLETAK_PROVISIONING_STATE'] = 'failed'
        pt.write_metadata(self.controller.meta, self.values)
        again = self.stage()
        self.assertEqual(first['attempt_id'], again['attempt_id'])

    def test_retry_keeps_engine_resume_position(self):
        self.stage()
        journal = pt.read_json(self.controller.journal)
        journal.update(phase='failed', resume_phase='messaging_initialized',
                       active_phase='services_configured', error='platform_command_failed')
        pt.write_json(self.controller.journal, journal)
        self.values = pt.metadata(self.controller.meta)
        self.values['PORTABLETAK_PROVISIONING_STATE'] = 'failed'
        pt.write_metadata(self.controller.meta, self.values)
        self.stage()
        after = pt.read_json(self.controller.journal)
        self.assertEqual((after['phase'], after['resume_phase']), ('failed', 'messaging_initialized'))

    def test_retry_seal_check_uses_software_verifier(self):
        self.stage()
        self.values = pt.metadata(self.controller.meta)
        self.values['PORTABLETAK_PROVISIONING_STATE'] = 'failed'
        pt.write_metadata(self.controller.meta, self.values)
        controller = pt.Controller(self.root, self.root.parent / 'operation.lock',
                                   require_mount=False, expected_base_seal='a' * 64)
        with patch('clean_base.verify_seal') as full, \
                patch('clean_base.verify_sealed_software') as software:
            controller.stage(self.request, self.zones)
        full.assert_not_called()
        software.assert_called_once()

    def test_duplicate_metadata_refused(self):
        with self.controller.meta.open('a') as stream:
            stream.write('PORTABLETAK_PROVISIONING_STATE=new\n')
        self.assertFalse(self.controller.status()['ok'])
        with self.assertRaisesRegex(pt.ProvisionError, 'invalid_metadata'):
            self.stage()

    def test_unknown_state_refused(self):
        self.values['PORTABLETAK_PROVISIONING_STATE'] = 'something'
        pt.write_metadata(self.controller.meta, self.values)
        with self.assertRaises(pt.ProvisionError):
            self.stage()

    def test_input_errors_do_not_write(self):
        cases = [dict(self.request, instance_name='bad\nNAME=x'),
                 dict(self.request, timezone='../UTC'),
                 dict(self.request, timezone='Missing'),
                 dict(self.request, administrator_password='short'),
                 dict(self.request, client_password=self.request['administrator_password']),
                 dict(self.request, extra='bad'),
                 dict(self.request, instance_name=23)]
        for request in cases:
            with self.assertRaises(pt.ProvisionError):
                self.stage(request)
            self.assertFalse(self.controller.state_dir.exists())

    def test_invalid_zone_file(self):
        (self.zones / 'Etc/UTC').write_bytes(b'not a zone')
        with self.assertRaisesRegex(pt.ProvisionError, 'invalid_timezone'):
            self.stage()

    def test_missing_base_refused(self):
        (self.root / 'portabletak/base.json').unlink()
        with self.assertRaises(pt.ProvisionError):
            self.stage()

    def test_lock_prevents_concurrent_stage(self):
        with pt.exclusive_lock(self.controller.lock):
            with self.assertRaisesRegex(pt.ProvisionError, 'provisioning_busy'):
                self.stage()
        self.assertFalse(self.controller.state_dir.exists())

    def test_mount_required_by_default(self):
        controller = pt.Controller(self.root, self.controller.lock)
        self.assertEqual(controller.status()['error'], 'cartridge_not_mounted')

    def test_modified_artifact_blocks_retry(self):
        result = self.stage()
        path = self.controller.state_dir / result['attempt_id'] / 'config.yml'
        path.write_text('{}')
        with self.assertRaisesRegex(pt.ProvisionError, 'staged_artifact_changed'):
            self.stage()
        self.assertEqual(self.controller.status()['state'], 'failed')

    def test_missing_secret_never_generates_replacement_identity(self):
        result = self.stage()
        path = self.controller.state_dir / result['attempt_id'] / 'secrets.json'
        path.unlink()
        with self.assertRaisesRegex(pt.ProvisionError, 'staged_artifact_changed'):
            self.stage()
        self.assertFalse(path.exists())

    def test_receipt_path_escape_rejected(self):
        result = self.stage()
        path = self.controller.state_dir / result['attempt_id'] / 'artifacts.json'
        pt.write_json(path, {'format': 1, 'sha256': {'../../escape': 'digest'}})
        with self.assertRaisesRegex(pt.ProvisionError, 'invalid_artifact_receipt'):
            self.stage()

    def test_cartridge_timezone_database_used_on_host_without_tzdata(self):
        target = self.root / 'rootfs/usr/share/zoneinfo/Etc'
        target.mkdir(parents=True)
        (target / 'UTC').write_bytes(b'TZif' + b'\x00' * 40)
        self.assertEqual(self.controller.stage(self.request)['phase'], 'staged')

    def test_status_never_returns_secrets(self):
        result = self.stage()
        private = pt.read_json(self.controller.state_dir / result['attempt_id'] / 'secrets.json')
        status = json.dumps(self.controller.status())
        for secret in private.values():
            self.assertNotIn(secret, status)
        self.assertFalse(self.controller.status()['can_provision'])

if __name__ == '__main__':
    unittest.main()
