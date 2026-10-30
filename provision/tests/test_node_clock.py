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


class FromPhonesTests(unittest.TestCase):
    def go(self, times, profile=NODE):
        run = Run()
        return node_clock.from_phones(times, profile, now=lambda: NOW, run=run), run.calls

    def test_node_behind_moves_forward(self):
        offset, calls = self.go([NOW + 27000, NOW + 26990, NOW + 26800])
        self.assertAlmostEqual(offset, 27000)
        self.assertEqual(calls, [['date', '-s', '@%d' % (NOW + 27000)]])

    def test_never_back_and_needs_three_close_reports(self):
        self.assertEqual(self.go([NOW - 27000] * 3), (None, []))           # old reports: nothing
        self.assertEqual(self.go([NOW + 30] * 3), (None, []))              # close enough
        self.assertEqual(self.go([NOW + 27000, NOW + 27000]), (None, []))  # too few
        self.assertEqual(self.go([NOW + 27000, NOW + 27000, NOW - 9000]), (None, []))
        self.assertEqual(self.go([NOW + 27000] * 3, LINUX), (None, []))    # not a node



class FromGpsTests(unittest.TestCase):
    class Gps(Run):
        def __init__(self, out):
            super().__init__()
            self.out = out

        def __call__(self, cmd, **kw):
            if cmd[0] == 'gpspipe':
                return type('R', (), {'stdout': self.out, 'returncode': 0})()
            return super().__call__(cmd, **kw)

    FIX = '{"class":"TPV","mode":3,"time":"2026-10-30T12:00:00.000Z"}\n'
    TARGET = 1793361600.0

    def test_fix_sets_the_clock_either_way(self):
        for now in (self.TARGET - 3000, self.TARGET + 3000):
            run = self.Gps(self.FIX)
            self.assertEqual(node_clock.from_gps(NODE, now=lambda: now, run=run), self.TARGET - now)
            self.assertEqual(run.calls[-1][-1], '@%d' % self.TARGET)

    def test_no_fix_or_no_gps_changes_nothing(self):
        run = self.Gps('{"class":"TPV","mode":1}\n{"class":"SKY"}\nnot json\n')
        self.assertIsNone(node_clock.from_gps(NODE, now=lambda: NOW, run=run))
        self.assertEqual(run.calls, [])

        def missing(cmd, **kw):
            raise FileNotFoundError(cmd[0])
        self.assertIsNone(node_clock.from_gps(NODE, now=lambda: NOW, run=missing))

    def test_only_on_nodes(self):
        self.assertIsNone(node_clock.from_gps(LINUX, now=lambda: NOW, run=self.Gps(self.FIX)))


if __name__ == '__main__':
    unittest.main()
