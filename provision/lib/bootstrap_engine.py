"""Durable, retry-safe orchestration for first-run provisioning.

This module owns state transitions only. Platform-specific work is supplied by
an audited backend whose operations must be idempotent and verify their result.
Passwords stay in process memory and restricted staged files; journal evidence
is checked to ensure that no staged secret is written into status history.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import time

from portabletak_provision import (
    COMPLETE,
    INCOMPLETE,
    Controller,
    ProvisionError,
    exclusive_lock,
    metadata,
    read_json,
    validate_input,
    write_json,
    write_metadata,
)

PHASES = (
    'staged',
    'base_verified',
    'storage_initialized',
    'database_initialized',
    'administrator_initialized',
    'certificates_initialized',
    'messaging_initialized',
    'services_configured',
    'services_started',
    'health_verified',
    'complete',
)

OPERATIONS = {
    'base_verified': 'verify_base',
    'storage_initialized': 'initialize_storage',
    'database_initialized': 'initialize_database',
    'administrator_initialized': 'initialize_administrator',
    'certificates_initialized': 'initialize_certificates',
    'messaging_initialized': 'initialize_messaging',
    'services_configured': 'configure_services',
    'services_started': 'start_services',
    'health_verified': 'verify_health',
}


@dataclass(frozen=True)
class BootstrapContext:
    root: Path
    stage: Path
    attempt_id: str
    request: dict
    secrets: dict


def _safe_evidence(value, secrets):
    """Return bounded JSON evidence after rejecting embedded secrets."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        raise ProvisionError('invalid_phase_evidence') from None
    if len(encoded) > 4096:
        raise ProvisionError('phase_evidence_too_large')
    for secret in secrets.values():
        if isinstance(secret, str) and secret and secret in encoded:
            raise ProvisionError('secret_in_phase_evidence')
    return json.loads(encoded)


class BootstrapEngine:
    def __init__(self, controller, backend, clock=None):
        if not isinstance(controller, Controller):
            raise TypeError('controller_required')
        self.controller = controller
        self.backend = backend
        self.clock = clock or (lambda: int(time.time()))

    def _staged_context(self, request, values):
        journal = self.controller.load_journal(values)
        phase = journal.get('phase')
        if phase not in PHASES and phase not in {'running', 'failed'}:
            raise ProvisionError('invalid_bootstrap_phase')
        attempt_id = journal['attempt_id']
        stage = self.controller.state_dir / attempt_id
        if stage.is_symlink() or not stage.is_dir():
            raise ProvisionError('staged_artifacts_missing')

        receipt = read_json(stage / 'artifacts.json')
        hashes = receipt.get('sha256', {})
        required = {'config.yml', 'cartridge.conf', 'timezone', 'secrets.json'}
        if receipt.get('format') != 1 or set(hashes) != required:
            raise ProvisionError('invalid_artifact_receipt')
        import hashlib
        for name, expected in hashes.items():
            path = stage / name
            if path.is_symlink() or not path.is_file():
                raise ProvisionError('staged_artifact_changed')
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ProvisionError('staged_artifact_changed')

        secrets = read_json(stage / 'secrets.json')
        identity = {'instance_name': request['instance_name'], 'timezone': request['timezone']}
        if journal.get('input') != identity:
            raise ProvisionError('retry_identity_mismatch')
        if secrets.get('client_password') != request['client_password']:
            raise ProvisionError('retry_client_password_mismatch')
        return journal, BootstrapContext(self.controller.root, stage, attempt_id, request, secrets)

    def _save(self, journal, **updates):
        journal.update(updates, updated_at=self.clock())
        write_json(self.controller.journal, journal)

    def _completed_metadata(self, context):
        proposed = metadata(context.stage / 'cartridge.conf')
        proposed['PORTABLETAK_PROVISIONING_STATE'] = 'provisioned'
        return proposed

    def _reconcile_completed_publish(self, request, values):
        """Finish the journal after metadata was published before a crash."""
        if values['PORTABLETAK_PROVISIONING_STATE'] != 'provisioned' or not self.controller.journal.exists():
            return
        journal = self.controller.load_journal(values)
        identity = {'instance_name': request['instance_name'], 'timezone': request['timezone']}
        if journal.get('input') != identity:
            raise ProvisionError('retry_identity_mismatch')
        if journal.get('phase') == 'complete':
            return
        if (journal.get('phase') == 'running'
                and journal.get('active_phase') == 'complete'
                and journal.get('resume_phase') == 'health_verified'):
            self._save(journal, phase='complete', resume_phase='complete',
                       active_phase=None, error=None)
            return
        raise ProvisionError('invalid_completed_journal')

    def run(self, request):
        request = validate_input(request, self.controller.timezone_root())
        with exclusive_lock(self.controller.lock):
            self.controller.check_paths()
            values = metadata(self.controller.meta)
            if values['PORTABLETAK_PROVISIONING_STATE'] in COMPLETE:
                self._reconcile_completed_publish(request, values)
                return {'ok': True, 'complete': True, 'phase': 'complete', 'already_complete': True}
            if values['PORTABLETAK_PROVISIONING_STATE'] not in INCOMPLETE:
                raise ProvisionError('invalid_activation_state')
            if values['PORTABLETAK_INSTANCE_TYPE'] != 'generic':
                raise ProvisionError('generic_cartridge_required')

            journal, context = self._staged_context(request, values)
            phase = journal['phase']
            if phase in {'running', 'failed'}:
                phase = journal.get('resume_phase')
            if phase not in PHASES:
                raise ProvisionError('invalid_resume_phase')
            # Never repeat a phase this attempt already committed, even if the
            # resume pointer was lost; history holds only committed phases.
            committed = [entry.get('phase') for entry in journal.get('history', [])
                         if isinstance(entry, dict) and entry.get('phase') in PHASES]
            if committed:
                latest = max(committed, key=PHASES.index)
                if PHASES.index(latest) > PHASES.index(phase):
                    phase = latest
            if phase == 'complete':
                return {'ok': True, 'complete': True, 'phase': 'complete', 'already_complete': True}

            start = PHASES.index(phase) + 1
            for next_phase in PHASES[start:]:
                if next_phase == 'complete':
                    proposed = self._completed_metadata(context)
                    self._save(journal, phase='running', resume_phase='health_verified',
                               active_phase='complete', error=None)
                    write_metadata(self.controller.meta, proposed)
                    self._save(journal, phase='complete', resume_phase='complete',
                               active_phase=None, error=None)
                    return {'ok': True, 'complete': True, 'phase': 'complete',
                            'attempt_id': context.attempt_id}

                operation = getattr(self.backend, OPERATIONS[next_phase], None)
                if operation is None:
                    raise ProvisionError('bootstrap_backend_incomplete')
                self._save(journal, phase='running', resume_phase=phase,
                           active_phase=next_phase, error=None)
                try:
                    if next_phase == 'administrator_initialized':
                        evidence = operation(context, request['administrator_password'])
                    else:
                        evidence = operation(context)
                    evidence = _safe_evidence(evidence, context.secrets)
                except Exception as error:
                    code = error.code if isinstance(error, ProvisionError) else next_phase + '_failed'
                    self._save(journal, phase='failed', resume_phase=phase,
                               active_phase=next_phase, error=code)
                    failed = dict(values)
                    failed['PORTABLETAK_PROVISIONING_STATE'] = 'failed'
                    write_metadata(self.controller.meta, failed)
                    raise ProvisionError(code) from None
                phase = next_phase
                history = list(journal.get('history', []))[-15:]
                history.append({'phase': phase, 'at': self.clock(), 'evidence': evidence})
                self._save(journal, phase=phase, resume_phase=phase,
                           active_phase=None, error=None, history=history)

        raise ProvisionError('bootstrap_did_not_complete')
