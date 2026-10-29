from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import heat


class HeatTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.log = self.root / 'logs' / 'temperature.log'
        heat._last.update(level=None, at=0.0)
        self.t = 1000.0

    def tearDown(self):
        self.dir.cleanup()

    def zones(self, *milli):
        for i, m in enumerate(milli):
            d = self.root / ('thermal_zone%d' % i)
            d.mkdir(exist_ok=True)
            (d / 'temp').write_text(str(m))
        return str(self.root / 'thermal_zone*' / 'temp')

    def status(self, *milli):
        return heat.status(self.zones(*milli), self.log, now=lambda: self.t)

    def test_hottest_zone_and_levels(self):
        self.assertEqual(self.status(61000, 72400), {'c': 72.4, 'level': 'ok'})
        self.assertEqual(self.status(76000)['level'], 'warm')
        self.assertEqual(self.status(81500)['level'], 'hot')

    def test_no_sensors(self):
        self.assertEqual(heat.status(str(self.root / 'none*'), self.log), {'c': None, 'level': None})

    def test_log_when_warm_and_when_it_cools(self):
        self.status(70000)
        self.assertFalse(self.log.exists())                     # nothing to note while cool
        self.status(78000)
        self.t += 60
        self.status(78500)                                        # same level, too soon: no line
        self.t += 700
        self.status(79000)                                        # still warm 10 min later: a line
        self.status(70000)                                        # cooled down: a line
        lines = self.log.read_text().splitlines()
        self.assertEqual([l.split()[-1] for l in lines], ['warm', 'warm', 'ok'])


if __name__ == '__main__':
    unittest.main()
