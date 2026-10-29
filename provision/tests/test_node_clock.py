from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import node_clock

NODE, LINUX = {'PT_PROFILE': 'openmanet'}, {'PT_PROFILE': 'linux'}
NOW = 1793200000.0


class Run:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return type('R', (), {'returncode': self.rc})()


class NodeClockTests(unittest.TestCase):
    def set(self, ms, profile=NODE, run=None):
        return node_clock.set_from_client(ms, profile, now=lambda: NOW, run=run or Run())

    def test_sets_a_node_that_is_off(self):
        run = Run()
        offset = self.set((NOW + 27000) * 1000, run=run)
        self.assertAlmostEqual(offset, 27000)
        self.assertEqual(run.calls, [['date', '-s', '@%d' % (NOW + 27000)]])

    def test_leaves_it_when_close_or_not_a_node(self):
        run = Run()
        self.assertIsNone(self.set((NOW + 60) * 1000, run=run))
        self.assertIsNone(self.set((NOW + 27000) * 1000, LINUX, run=run))
        self.assertEqual(run.calls, [])

    def test_ignores_nonsense(self):
        run = Run()
        for bad in (None, True, 'x', 0, 9e15, (NOW - 3e8) * 1000):
            self.assertIsNone(self.set(bad, run=run))
        self.assertEqual(run.calls, [])

    def test_failed_date_changes_nothing(self):
        self.assertIsNone(self.set((NOW + 27000) * 1000, run=Run(rc=1)))


if __name__ == '__main__':
    unittest.main()
