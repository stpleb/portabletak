import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from lifecycle_lock import LifecycleLock, run_locked
from portabletak_provision import ProvisionError, read_json


def competing(lock_path, state_path, queue):
    guard = LifecycleLock(lock_path, state_path)
    try:
        guard.acquire('start')
    except ProvisionError as error:
        queue.put(error.code)
    else:
        guard.release()
        queue.put('acquired')


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.lock_path = root / 'lifecycle.lock'
        self.state_path = root / 'lifecycle.json'

    def tearDown(self):
        self.temp.cleanup()

    def guard(self):
        return LifecycleLock(self.lock_path, self.state_path, clock=lambda: 123)

    def test_records_operation_and_cleans_state(self):
        guard = self.guard()
        state = guard.acquire('backup', '11111111-1111-4111-8111-111111111111')
        self.assertEqual(state['operation'], 'backup')
        self.assertEqual(read_json(self.state_path)['pid'], os.getpid())
        guard.release()
        self.assertFalse(self.state_path.exists())

    def test_competing_process_is_refused(self):
        guard = self.guard()
        guard.acquire('restore')
        queue = multiprocessing.Queue()
        process = multiprocessing.Process(target=competing,
                                          args=(self.lock_path, self.state_path, queue))
        process.start()
        process.join(10)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(queue.get(timeout=1), 'lifecycle_busy')
        guard.release()

    def test_command_result_propagates_and_lock_releases(self):
        code = run_locked('stop', [sys.executable, '-c', 'raise SystemExit(7)'],
                          lock=self.guard())
        self.assertEqual(code, 7)
        self.assertFalse(self.state_path.exists())
        second = self.guard()
        second.acquire('start')
        second.release()

    def test_invalid_operation_refused(self):
        with self.assertRaisesRegex(ProvisionError, 'invalid_lifecycle_operation'):
            self.guard().acquire('format')


if __name__ == '__main__':
    unittest.main()
