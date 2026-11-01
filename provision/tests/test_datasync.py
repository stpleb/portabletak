"""Data Sync card: the feed row and the delete notice."""
import datetime
import importlib.util
from pathlib import Path
import types
import unittest
from xml.etree.ElementTree import Element

PAYLOAD = Path(__file__).resolve().parents[2] / 'host-installer-20260929/payload/usr/local/portabletak'
spec = importlib.util.spec_from_file_location('datasync_actions', PAYLOAD / 'chroot/datasync_actions.py')
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)


def feed(**changes):
    values = dict(name='op1', owner=None, creator_uid='ANDROID-1', create_time=None, contents=[], uids=[],
                  roles=[1], tool='public', password_protected=None)
    values.update(changes)
    return types.SimpleNamespace(**values)


class DataSyncTests(unittest.TestCase):
    def test_row_counts_files_and_map_items(self):
        row = ds._json(feed(contents=[1, 2], uids=[3], owner=types.SimpleNamespace(callsign='301_EOD_1-1'),
                            create_time=datetime.datetime(2026, 10, 30, 12)))
        self.assertEqual((row['items'], row['subscribers'], row['creator']), (3, 1, '301_EOD_1-1'))
        self.assertEqual(row['time'], '2026-10-30T12:00:00+00:00')
        self.assertIs(row['password'], False)

    def test_creator_falls_back_to_the_uid_of_a_phone_the_server_forgot(self):
        self.assertEqual(ds._json(feed())['creator'], 'ANDROID-1')

    def test_notice_is_the_serialized_cot(self):
        self.assertEqual(ds.delete_notice(feed(), lambda m: Element('event', {'type': 't-x-m-d'})),
                         '<event type="t-x-m-d" />')

    def test_feed_that_cannot_be_serialized_is_still_deletable(self):
        self.assertIsNone(ds.delete_notice(feed(tool=None), lambda m: Element('event', {'tool': m.tool})))


if __name__ == '__main__':
    unittest.main()
