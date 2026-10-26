"""Live feeds: ADS-B (internet / USB radio) and AIS records to CoT; one service pass."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

PAYLOAD = Path(__file__).resolve().parents[2] / 'host-installer-20260929/payload/usr/local/portabletak'
spec = importlib.util.spec_from_file_location('feeds_service', PAYLOAD / 'chroot/feeds_service.py')
feeds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feeds)
NOW = datetime.datetime(2026, 10, 26, 17, 0, 0, tzinfo=datetime.timezone.utc)


class AircraftTests(unittest.TestCase):
    def test_airliner(self):
        cot = feeds.aircraft_to_cot({'hex': 'a1b2c3', 'flight': 'AAL1234 ', 'r': 'N150NN', 't': 'A321', 'lat': 32.88,
                                     'lon': -97.03, 'alt_baro': 3000, 'alt_geom': 3100, 'gs': 180, 'track': 175,
                                     'squawk': '2424'}, now=NOW, stale_seconds=60, source='ADS-B (radio)')
        event = ET.fromstring(cot)
        self.assertEqual((event.get('uid'), event.get('type')), ('ICAO-A1B2C3', 'a-n-A-C-F'))
        self.assertEqual(event.find('detail/contact').get('callsign'), 'AAL1234')
        self.assertAlmostEqual(float(event.find('point').get('hae')), 3100 * 0.3048, places=1)
        self.assertAlmostEqual(float(event.find('detail/track').get('speed')), 180 * 0.514444, places=2)
        self.assertIn('ADS-B (radio)', event.find('detail/remarks').text)
        self.assertEqual(event.get('stale'), '2026-10-26T17:01:00.000Z')

    def test_military_helicopter_on_ground(self):
        cot = feeds.aircraft_to_cot({'hex': '~ae1234', 'lat': 1, 'lon': 2, 'alt_baro': 'ground', 'category': 'A7',
                                     'dbFlags': 1}, now=NOW)
        event = ET.fromstring(cot)
        self.assertEqual(event.get('type'), 'a-n-A-M-H')
        self.assertEqual(event.find('point').get('hae'), '0.0')
        self.assertEqual(event.find('detail/contact').get('callsign'), 'AE1234')

    def test_no_position(self):
        self.assertIsNone(feeds.aircraft_to_cot({'hex': 'abc'}, now=NOW))
        self.assertIsNone(feeds.aircraft_to_cot({'lat': 1, 'lon': 2}, now=NOW))

    def test_odd_text_is_escaped(self):
        cot = feeds.aircraft_to_cot({'hex': 'abc', 'flight': 'A"<&', 'lat': 1, 'lon': 2, 't': '<x>'}, now=NOW)
        self.assertEqual(ET.fromstring(cot).find('detail/contact').get('callsign'), 'A"<&')


class VesselTests(unittest.TestCase):
    def test_vessel(self):
        cot = feeds.vessel_to_cot({'MMSI': 366999999, 'NAME': 'DEMO VESSEL', 'LATITUDE': 29.7, 'LONGITUDE': -95.0,
                                   'COG': 200, 'SOG': 10, 'CALLSIGN': 'WDE1234', 'DEST': 'HOUSTON'}, now=NOW)
        event = ET.fromstring(cot)
        self.assertEqual((event.get('uid'), event.get('type')), ('MMSI-366999999', 'a-n-S-X'))
        self.assertEqual(event.find('detail/contact').get('callsign'), 'DEMO VESSEL')
        self.assertIn('HOUSTON', event.find('detail/remarks').text)

    def test_bad_position(self):
        self.assertIsNone(feeds.vessel_to_cot({'MMSI': 1, 'LATITUDE': 91, 'LONGITUDE': 0}, now=NOW))


class AisStreamTests(unittest.TestCase):
    def stream(self):
        import threading
        stream = object.__new__(feeds.AisStream)   # no network thread
        stream.key, stream.bbox, stream.vessels, stream.error = 'k', None, {}, None
        stream.lock, stream.stop = threading.Lock(), threading.Event()
        return stream

    def test_position_and_static_data_merge(self):
        stream = self.stream()
        stream.handle({'MessageType': 'PositionReport', 'MetaData': {'MMSI': 366999999, 'ShipName': 'DEMO  '},
                       'Message': {'PositionReport': {'Latitude': 29.7, 'Longitude': -95.0, 'Cog': 200, 'Sog': 10}}})
        stream.handle({'MessageType': 'ShipStaticData', 'MetaData': {'MMSI': 366999999},
                       'Message': {'ShipStaticData': {'Name': 'DEMO VESSEL', 'CallSign': 'WDE1234', 'Destination': 'HOUSTON'}}})
        vessels = stream.current()
        self.assertEqual(len(vessels), 1)
        cot = ET.fromstring(feeds.vessel_to_cot(vessels[0], now=NOW))
        self.assertEqual(cot.find('detail/contact').get('callsign'), 'DEMO VESSEL')
        self.assertIn('HOUSTON', cot.find('detail/remarks').text)

    def test_static_only_is_not_on_the_map_and_errors_are_kept(self):
        stream = self.stream()
        stream.handle({'MessageType': 'ShipStaticData', 'MetaData': {'MMSI': 1}, 'Message': {'ShipStaticData': {'Name': 'X'}}})
        self.assertEqual(stream.current(), [])
        stream.handle({'error': 'Api Key Is Not Valid'})
        self.assertEqual(stream.error, 'Api Key Is Not Valid')


class ServiceTests(unittest.TestCase):
    def test_config_defaults_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'feeds.json')
            with open(path, 'w') as f:
                json.dump({'adsb': {'internet': True, 'lat': 1, 'evil': 'x'}}, f)
            cfg = feeds.load_config(path)
            self.assertTrue(cfg['adsb']['internet'])
            self.assertNotIn('evil', cfg['adsb'])
            self.assertEqual(cfg['adsb']['api_url'], 'https://api.adsb.lol/v2/point')
            self.assertFalse(cfg['ais']['internet'])

    def test_radio_file_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'aircraft.json')
            with open(path, 'w') as f:
                json.dump({'aircraft': [{'hex': 'a', 'lat': 1, 'lon': 2, 'seen_pos': 3}, {'hex': 'b', 'seen_pos': 300}]}, f)
            self.assertEqual([a['hex'] for a in feeds.read_radio_aircraft(path)], ['a'])
            old = time.time() - 600
            os.utime(path, (old, old))
            with self.assertRaises(ValueError):
                feeds.read_radio_aircraft(path)

    def test_one_pass_publishes_radio_aircraft_into_the_channel(self):
        published = []

        class Publisher:
            def publish(self, events, group):
                published.append((group, events))

        cfg = feeds.load_config('/nonexistent')
        cfg['adsb']['radio'] = True
        original = feeds.read_radio_aircraft
        feeds.read_radio_aircraft = lambda: [{'hex': 'abc', 'lat': 1, 'lon': 2}]
        try:
            status, due = {}, {}
            feeds.run_once(cfg, Publisher(), status, due, 1000.0)
        finally:
            feeds.read_radio_aircraft = original
        self.assertEqual(published[0][0], 'ADS-B')
        self.assertEqual(len(published[0][1]), 1)
        self.assertEqual(status['adsb']['sources'], ['radio'])
        self.assertEqual(status['ais'], {'off': True})
        self.assertEqual(due['adsb'], 1005.0)


if __name__ == '__main__':
    unittest.main()
