"""Supervise one bootstrap worker without persisting provisioning passwords."""

import json
import os
from pathlib import Path
import subprocess
import time

from portabletak_provision import ProvisionError, read_json, write_json


class BootstrapJobSupervisor:
    def __init__(self, state_path='/tmp/portabletak/bootstrap-job.json',
                 worker_command=('/usr/local/portabletak/bin/portabletak-bootstrap-worker',),
                 clock=None, process_probe=None, popen=None):
        self.state_path = Path(state_path)
        self.worker_command = tuple(worker_command)
        self.clock = clock or (lambda: int(time.time()))
        self.process_probe = process_probe or self._probe
        self.popen = popen or subprocess.Popen

    @staticmethod
    def _probe(pid):
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def status(self):
        if not self.state_path.exists():
            return {'ok': True, 'job': 'idle'}
        data = read_json(self.state_path)
        required = {'format', 'job', 'pid', 'started_at', 'updated_at'}
        if not required.issubset(data) or data.get('format') != 1:
            raise ProvisionError('invalid_bootstrap_job')
        if data['job'] == 'running' and not self.process_probe(data['pid']):
            data.update(job='interrupted', updated_at=self.clock(), error='worker_interrupted')
            write_json(self.state_path, data)
        return dict(data, ok=True)

    def launch(self, request):
        current = self.status()
        if current.get('job') == 'running':
            raise ProvisionError('bootstrap_job_busy')
        if not self.worker_command or not all(isinstance(value, str) and value for value in self.worker_command):
            raise ProvisionError('invalid_bootstrap_worker')
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        process = self.popen(self.worker_command, stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, start_new_session=True)
        state = {'format': 1, 'job': 'running', 'pid': process.pid,
                 'started_at': self.clock(), 'updated_at': self.clock()}
        write_json(self.state_path, state)
        payload = bytearray(json.dumps(request, separators=(',', ':')).encode('utf-8'))
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except Exception:
            state.update(job='interrupted', updated_at=self.clock(), error='worker_input_failed')
            write_json(self.state_path, state)
            raise ProvisionError('bootstrap_worker_input_failed') from None
        finally:
            for index in range(len(payload)):
                payload[index] = 0
        return {'ok': True, 'job': 'running', 'pid': process.pid,
                'started_at': state['started_at']}

    def finish(self, pid, success, error=None):
        state = read_json(self.state_path)
        if state.get('job') != 'running' or state.get('pid') != pid:
            raise ProvisionError('bootstrap_job_owner_mismatch')
        if success:
            state.update(job='complete', updated_at=self.clock())
            state.pop('error', None)
        else:
            state.update(job='failed', updated_at=self.clock(),
                         error=error or 'bootstrap_failed')
        write_json(self.state_path, state)
        return dict(state, ok=bool(success))
