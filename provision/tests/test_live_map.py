"""Live map: snapshot items from stored CoT, offline map area checks, dashboard site."""
import datetime
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

PAYLOAD = Path(__file__).resolve().parents[2] / 'host-installer-20260929/payload/usr/local/portabletak'
spec = importlib.util.spec_from_file_location('map_actions', PAYLOAD / 'chroot/map_actions.py')
map_actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(map_actions)
sys.path.insert(0, str(PAYLOAD / 'lib'))
import map_areas  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import dashboard_site  # noqa: E402

NOW = datetime.datetime(2026, 10, 26, 17, 0, 0)


def row(uid, cot_type, xml, how='m-g'):
    return (uid, cot_type, how, xml, NOW + datetime.timedelta(minutes=5), NOW, None)


class SnapshotItemTests(unittest.TestCase):
    def test_phone_with_team_track_and_battery(self):
        xml = ('<event uid="ANDROID-1" type="a-f-G-U-C"><point lat="32.70" lon="-97.60" hae="200" ce="5" le="5"/>'
               '<detail><contact callsign="TITAN"/><__group name="Cyan" role="Team Lead"/>'
               '<track course="45" speed="1.5"/><status battery="81"/></detail></event>')
        item = map_actions.item(row('ANDROID-1', 'a-f-G-U-C', xml), {'ANDROID-1': {'last_status': 'Connected'}})
        self.assertEqual(item['kind'], 'phone')
        self.assertEqual((item['callsign'], item['team'], item['role']), ('TITAN', 'Cyan', 'Team Lead'))
        self.assertEqual((item['course'], item['speed'], item['battery']), (45.0, 1.5, 81.0))
        self.assertEqual(item['affiliation'], 'friend')

    def test_federated_phone(self):
        xml = '<event><point lat="1" lon="2"/><detail><contact callsign="TITAN"/></detail></event>'
        item = map_actions.item(row('ANDROID-2', 'a-f-G-U-C', xml), {'ANDROID-2': {'last_status': 'Federated (HQ)'}})
        self.assertEqual(item['kind'], 'federated')

    def test_feeds_and_markers(self):
        point = '<point lat="32.7" lon="-97.6"/>'
        cases = [('ICAO-A1B2C3', 'a-n-A-C-F', 'aircraft'), ('MMSI-366999999', 'a-n-S-X-M', 'ship'),
                 ('M-1', 'a-h-G', 'unit'), ('B-1', 'b-m-p-s-m', 'marker'), ('C-1', 'b-r-f-h-c', 'casevac')]
        for uid, cot_type, kind in cases:
            item = map_actions.item(row(uid, cot_type, '<event>%s<detail/></event>' % point), {})
            self.assertEqual(item['kind'], kind, uid)

    def test_no_position_or_bad_xml_is_skipped(self):
        self.assertIsNone(map_actions.item(row('X', 'a-f-G', '<event><detail/></event>'), {}))
        self.assertIsNone(map_actions.item(row('X', 'a-f-G', '<event><point lat="0" lon="0"/></event>'), {}))
        self.assertIsNone(map_actions.item(row('X', 'a-f-G', '<event><point'), {}))

    def test_deleted_markers(self):
        deletes = [('<event type="t-x-d-d"><point lat="0" lon="0"/><detail><link uid="B-1" relation="none"/></detail></event>', NOW)]
        self.assertEqual(map_actions.deleted_uids(deletes), {'B-1': NOW})


class MapAreaTests(unittest.TestCase):
    def test_area_checks(self):
        self.assertEqual(map_areas._check('Fort Worth', [-97.75, 32.6, -97.45, 32.8], 15),
                         ([-97.75, 32.6, -97.45, 32.8], 15))
        for name, bbox, zoom in [('', [0, 0, 1, 1], 15), ('../x', [0, 0, 1, 1], 15), ('ok', [1, 0, 0, 1], 15),
                                 ('ok', [0, 0, 1, 1], 20), ('ok', 'nope', 15)]:
            with self.assertRaises(ValueError):
                map_areas._check(name, bbox, zoom)

    def test_list_marks_interrupted_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            map_areas._save(tmp, {'A': {'file': 'A.pmtiles', 'bbox': [0, 0, 1, 1], 'maxzoom': 15, 'ready': False}})
            self.assertEqual(map_areas.areas(tmp)[0]['error'], 'interrupted')

    def test_delete_removes_file_and_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = map_areas.folder(tmp)
            folder.mkdir(parents=True)
            (folder / 'A.pmtiles').write_bytes(b'PMTiles')
            map_areas._save(tmp, {'A': {'file': 'A.pmtiles', 'ready': True}})
            map_areas.delete(tmp, 'A')
            self.assertFalse((folder / 'A.pmtiles').exists())
            self.assertEqual(map_areas.areas(tmp), [])

    def test_dashboard_site_serves_map_files(self):
        site = dashboard_site.render()
        self.assertIn('location /maps/ { alias /asgard/opentak/maps/;', site)
        self.assertLess(site.index('location /maps/'), site.index('location / {'))


if __name__ == '__main__':
    unittest.main()
