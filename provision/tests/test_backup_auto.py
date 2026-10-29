import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import backup_auto


class BackupAutoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dir = self.root / 'recovery'
        self.dir.mkdir()
        self.sched = self.root / 'schedule.json'
        self.made = []

    def tearDown(self):
        self.tmp.cleanup()

    def backup(self, stamp, note):
        name = 'portabletak-%s.tar.gz' % stamp
        (self.dir / name).write_bytes(b'x')
        local = '%s-%s-%sT%s:%s:%s' % (stamp[0:4], stamp[4:6], stamp[6:8], stamp[9:11], stamp[11:13], stamp[13:15])
        (self.dir / (name + '.json')).write_text(json.dumps({'format': 1, 'note': note, 'created_local': local}))

    def runner(self, cmd, **kw):
        self.made.append(cmd)
        self.backup(time.strftime('%Y%m%d_%H%M%S', self.now), 'Automatic')
        return type('R', (), {'returncode': 0})()

    def go(self, hour_now, enabled=True, hour=3, keep=2):
        self.sched.write_text(json.dumps({'enabled': enabled, 'hour': hour, 'keep': keep}))
        self.now = time.strptime('2026-10-29 %02d:10:00' % hour_now, '%Y-%m-%d %H:%M:%S')
        return backup_auto.run(self.sched, self.dir, 'tool', self.runner, self.now)

    def names(self):
        return sorted(p.name for p in self.dir.glob('*.tar.gz'))

    def test_off_and_before_the_hour(self):
        self.assertEqual(self.go(5, enabled=False), 'off')
        self.assertEqual(self.go(2), 'not_due')
        self.assertEqual(self.made, [])

    def test_once_a_day_and_keeps_only_the_newest_automatic(self):
        self.backup('20261026_031000', 'Automatic')
        self.backup('20261027_031000', 'Automatic')
        self.backup('20261020_120000', 'before the trip')        # made by hand: never removed
        self.backup('20261028_031000', '')                        # also by hand
        self.assertEqual(self.go(4), 'made')
        self.assertEqual(self.go(5), 'not_due')                  # already done today
        self.assertEqual(len(self.made), 1)
        self.assertEqual(self.names(), ['portabletak-20261020_120000.tar.gz', 'portabletak-20261027_031000.tar.gz',
                                        'portabletak-20261028_031000.tar.gz', 'portabletak-20261029_041000.tar.gz'])


if __name__ == '__main__':
    unittest.main()
