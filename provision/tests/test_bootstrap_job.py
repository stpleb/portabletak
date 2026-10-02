import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from bootstrap_job import BootstrapJobSupervisor
from portabletak_provision import ProvisionError


class CaptureStream(io.BytesIO):
    def close(self):
        self.close_requested = True


class Process:
    pid = 4321
    def __init__(self):
        self.stdin = CaptureStream()


class BootstrapJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / 'job.json'
        self.processes = []
        self.calls = []
        def popen(command, **kwargs):
            self.calls.append((command, kwargs))
            process = Process()
            self.processes.append(process)
            return process
        self.alive = True
        self.supervisor = BootstrapJobSupervisor(
            self.state, ('worker',), clock=lambda: 100,
            process_probe=lambda pid: self.alive and pid == 4321, popen=popen)
        self.request = {'instance_name': 'Test', 'timezone': 'Etc/UTC',
                        'administrator_password': 'Administrator-secret-123',
                        'client_password': 'Client-secret-456'}

    def tearDown(self):
        self.temp.cleanup()

    def test_passwords_travel_only_through_stdin(self):
        result = self.supervisor.launch(self.request)
        self.assertEqual(result['job'], 'running')
        state_text = self.state.read_text()
        arguments = json.dumps(self.calls[0][0]) + json.dumps({
            key: str(value) for key, value in self.calls[0][1].items() if key != 'stdin'})
        for password in (self.request['administrator_password'], self.request['client_password']):
            self.assertNotIn(password, state_text)
            self.assertNotIn(password, arguments)
        self.assertEqual(json.loads(self.processes[0].stdin.getvalue()), self.request)

    def test_live_job_refuses_second_launch(self):
        self.supervisor.launch(self.request)
        with self.assertRaisesRegex(ProvisionError, 'bootstrap_job_busy'):
            self.supervisor.launch(self.request)

    def test_dead_worker_becomes_interrupted_and_can_restart(self):
        self.supervisor.launch(self.request)
        self.alive = False
        self.assertEqual(self.supervisor.status()['job'], 'interrupted')
        self.alive = True
        self.supervisor.launch(self.request)
        self.assertEqual(len(self.calls), 2)

    def test_only_owner_can_finish(self):
        self.supervisor.launch(self.request)
        with self.assertRaisesRegex(ProvisionError, 'bootstrap_job_owner_mismatch'):
            self.supervisor.finish(9999, True)
        result = self.supervisor.finish(4321, True)
        self.assertEqual(result['job'], 'complete')


if __name__ == '__main__':
    unittest.main()
