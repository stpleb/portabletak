import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import portabletak_provision as pt
from bootstrap_engine import BootstrapEngine, OPERATIONS, PHASES


class Backend:
    def __init__(self, fail=None, evidence=None):
        self.calls = []
        self.fail = fail
        self.evidence = evidence or {}

    def __getattr__(self, name):
        if name not in OPERATIONS.values():
            raise AttributeError(name)
        def operation(context, *args):
            self.calls.append(name)
            if name == self.fail:
                raise RuntimeError('test failure')
            return self.evidence.get(name, {'verified': True})
        return operation


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'cartridge'
        (self.root / 'portabletak').mkdir(parents=True)
        (self.root / 'rootfs/usr/share/zoneinfo/Etc').mkdir(parents=True)
        (self.root / 'rootfs/usr/share/zoneinfo/Etc/UTC').write_bytes(b'TZif' + b'\0' * 20)
        self.values = {
            'PORTABLETAK_FORMAT': '1', 'PORTABLETAK_ARCH': 'arm64',
            'PORTABLETAK_CARTRIDGE_ID': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'PORTABLETAK_INSTANCE_NAME': 'Unconfigured',
            'PORTABLETAK_INSTANCE_TYPE': 'generic',
            'PORTABLETAK_PROVISIONING_STATE': 'new',
        }
        self.controller = pt.Controller(self.root, self.root.parent / 'lock', require_mount=False)
        pt.write_metadata(self.controller.meta, self.values)
        pt.write_json(self.root / 'portabletak/base.json',
                      {'format': 1, 'kind': 'portabletak-clean-base', 'architecture': 'arm64'})
        self.request = {
            'instance_name': 'Test Team', 'timezone': 'Etc/UTC',
            'administrator_password': 'Administrator-password-123',
            'client_password': 'Client-password-456',
        }
        self.controller.stage(self.request)

    def tearDown(self):
        self.temp.cleanup()

    def run_engine(self, backend):
        return BootstrapEngine(self.controller, backend, clock=lambda: 123).run(self.request)

    def test_all_phases_complete_and_publish_identity(self):
        backend = Backend()
        result = self.run_engine(backend)
        self.assertTrue(result['complete'])
        self.assertEqual(backend.calls, list(OPERATIONS.values()))
        values = pt.metadata(self.controller.meta)
        self.assertEqual(values['PORTABLETAK_PROVISIONING_STATE'], 'provisioned')
        journal = pt.read_json(self.controller.journal)
        self.assertEqual(journal['phase'], 'complete')
        self.assertEqual(len(journal['history']), len(OPERATIONS))
        self.assertNotIn(self.request['administrator_password'], self.controller.journal.read_text())

    def test_failure_records_resume_and_retry_skips_verified_phases(self):
        first = Backend(fail='initialize_messaging')
        with self.assertRaisesRegex(pt.ProvisionError, 'messaging_initialized_failed'):
            self.run_engine(first)
        self.assertEqual(pt.metadata(self.controller.meta)['PORTABLETAK_PROVISIONING_STATE'], 'failed')
        journal = pt.read_json(self.controller.journal)
        self.assertEqual(journal['phase'], 'failed')
        self.assertEqual(journal['resume_phase'], 'certificates_initialized')
        second = Backend()
        self.run_engine(second)
        self.assertEqual(second.calls[0], 'initialize_messaging')
        self.assertNotIn('initialize_database', second.calls)

    def test_worker_order_retry_resumes_after_last_committed_phase(self):
        # The worker always stages before running the engine, including retries.
        with self.assertRaises(pt.ProvisionError):
            self.run_engine(Backend(fail='configure_services'))
        self.controller.stage(self.request)
        second = Backend()
        self.run_engine(second)
        self.assertEqual(second.calls[0], 'configure_services')
        self.assertNotIn('initialize_certificates', second.calls)

    def test_lost_resume_pointer_recovered_from_history(self):
        with self.assertRaises(pt.ProvisionError):
            self.run_engine(Backend(fail='configure_services'))
        journal = pt.read_json(self.controller.journal)
        journal.update(phase='failed', resume_phase='staged', active_phase='base_verified')
        pt.write_json(self.controller.journal, journal)
        second = Backend()
        self.run_engine(second)
        self.assertEqual(second.calls[0], 'configure_services')
        self.assertNotIn('verify_base', second.calls)
        self.assertNotIn('initialize_certificates', second.calls)

    def test_running_phase_replays_only_uncommitted_operation(self):
        journal = pt.read_json(self.controller.journal)
        journal.update(phase='running', resume_phase='database_initialized',
                       active_phase='administrator_initialized')
        pt.write_json(self.controller.journal, journal)
        backend = Backend()
        self.run_engine(backend)
        self.assertEqual(backend.calls[0], 'initialize_administrator')

    def test_secret_in_evidence_is_refused(self):
        secret = pt.read_json(next((self.controller.state_dir).glob('*/secrets.json')))['ca_password']
        backend = Backend(evidence={'verify_base': {'detail': secret}})
        with self.assertRaisesRegex(pt.ProvisionError, 'secret_in_phase_evidence'):
            self.run_engine(backend)

    def test_changed_staged_artifact_refused(self):
        journal = pt.read_json(self.controller.journal)
        (self.controller.state_dir / journal['attempt_id'] / 'config.yml').write_text('{}')
        with self.assertRaisesRegex(pt.ProvisionError, 'staged_artifact_changed'):
            self.run_engine(Backend())

    def test_existing_personal_instance_refused(self):
        values = pt.metadata(self.controller.meta)
        values['PORTABLETAK_INSTANCE_TYPE'] = 'personal'
        pt.write_metadata(self.controller.meta, values)
        with self.assertRaisesRegex(pt.ProvisionError, 'generic_cartridge_required'):
            self.run_engine(Backend())

    def test_completed_retry_is_noop(self):
        self.run_engine(Backend())
        backend = Backend()
        result = self.run_engine(backend)
        self.assertTrue(result['already_complete'])
        self.assertEqual(backend.calls, [])

    def test_retry_finalizes_journal_after_metadata_publish_crash(self):
        journal = pt.read_json(self.controller.journal)
        journal.update(phase='running', resume_phase='health_verified',
                       active_phase='complete', error=None)
        pt.write_json(self.controller.journal, journal)
        proposed = pt.metadata(self.controller.state_dir / journal['attempt_id'] / 'cartridge.conf')
        proposed['PORTABLETAK_PROVISIONING_STATE'] = 'provisioned'
        pt.write_metadata(self.controller.meta, proposed)

        backend = Backend()
        result = self.run_engine(backend)

        self.assertTrue(result['already_complete'])
        self.assertEqual(backend.calls, [])
        reconciled = pt.read_json(self.controller.journal)
        self.assertEqual(reconciled['phase'], 'complete')
        self.assertEqual(reconciled['resume_phase'], 'complete')
        self.assertIsNone(reconciled['active_phase'])


if __name__ == '__main__':
    unittest.main()
