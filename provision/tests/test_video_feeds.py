import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import video_feeds as vf
import ts_relay


class FakeResponse:
    def __init__(self, body): self.body = body
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


class VideoFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_names_follow_the_skydio_rule(self):
        for good in ('skydio1', 'isr2', 'skydio-x10', 'x10d', '1skydio'):
            self.assertTrue(vf.NAME.fullmatch(good), good)
        for bad in ('Skydio1', 'sky dio1', 'sky/1', 'sky_1', '-sky', '', 'a' * 40):
            self.assertFalse(vf.NAME.fullmatch(bad), bad)
        with self.assertRaises(vf.FeedError):
            vf.add(self.root, 'Skydio')

    def test_add_remove_and_survive_on_the_drive(self):
        vf.add(self.root, 'skydio1', clock=lambda: 5)
        vf.add(self.root, 'skydio1')                      # twice: no duplicate
        vf.add(self.root, 'isr2')
        self.assertEqual([f['name'] for f in vf.load(self.root)], ['skydio1', 'isr2'])
        vf.remove(self.root, 'skydio1')
        self.assertEqual([f['name'] for f in vf.load(self.root)], ['isr2'])
        self.assertEqual(json.loads((self.root / vf.FEEDS).read_text())['format'], 1)

    def test_exclusions_keep_defaults_and_add_publish_and_read(self):
        rules = vf.exclusions([{'name': 'skydio1'}])
        self.assertEqual(rules[:3], vf.BASE_EXCLUDE)
        self.assertIn({'action': 'publish', 'path': 'skydio1'}, rules)
        self.assertIn({'action': 'read', 'path': 'skydio1'}, rules)
        self.assertEqual(len(rules), 5)

    def test_apply_patches_mediamtx_and_status_marks_live(self):
        vf.add(self.root, 'skydio1')
        sent = []

        def opener(request, timeout=0):
            sent.append((request.get_method(), request.full_url, request.data))
            if request.full_url.endswith('/v3/paths/list'):
                return FakeResponse(json.dumps({'items': [{'name': 'skydio1', 'ready': True}]}).encode())
            return FakeResponse(b'')
        self.assertTrue(vf.apply(self.root, opener=opener))
        method, url, data = sent[0]
        self.assertEqual((method, url), ('PATCH', 'http://127.0.0.1:9997/v3/config/global/patch'))
        self.assertIn({'action': 'publish', 'path': 'skydio1'}, json.loads(data)['authHTTPExclude'])
        self.assertEqual(vf.status(self.root, opener=opener), [{'name': 'skydio1', 'created': vf.load(self.root)[0]['created'], 'live': True}])

    def test_udp_feeds_get_their_own_port_and_can_switch(self):
        vf.add(self.root, 'skydio1')
        vf.add(self.root, 'icu', udp=True)
        vf.add(self.root, 'icu2', udp=True)
        self.assertEqual({f['name']: f.get('udp_port') for f in vf.load(self.root)},
                         {'skydio1': None, 'icu': 5600, 'icu2': 5601})
        vf.add(self.root, 'icu', udp=True)                # unchanged
        vf.add(self.root, 'icu')                          # back to RTSP frees the port
        vf.add(self.root, 'skydio1', udp=True)            # takes the free port
        self.assertEqual({f['name']: f.get('udp_port') for f in vf.load(self.root)},
                         {'skydio1': 5600, 'icu': None, 'icu2': 5601})

    def test_apply_adds_and_removes_udp_paths(self):
        vf.add(self.root, 'icu', udp=True)
        vf.add(self.root, 'skydio1')
        sent = []
        have = [{'name': 'all_others', 'source': 'publisher'}, {'name': 'old', 'source': 'udp://0.0.0.0:5603'},   # older form
                {'name': 'other', 'source': 'rtsp://camera/x'}]

        def opener(request, timeout=0):
            sent.append((request.get_method(), request.full_url.split('9997')[1], request.data))
            if '/v3/config/paths/list' in request.full_url:
                return FakeResponse(json.dumps({'items': have}).encode())
            return FakeResponse(b'')
        vf.apply(self.root, opener=opener)
        calls = [(m, u) for m, u, _ in sent[2:]]
        self.assertEqual(calls, [('DELETE', '/v3/config/paths/delete/old'), ('POST', '/v3/config/paths/add/icu'),
                                 ('POST', '/v3/config/paths/add/_in/icu')])
        self.assertEqual(json.loads(sent[3][2]), {'source': 'udp://127.0.0.1:5600'})   # loopback only
        self.assertEqual(json.loads(sent[0][2])['rtspTransports'], ['tcp'])
        relay = json.loads(sent[4][2])                    # TAK ICU's RTSP input, unwrapped into port 5600
        self.assertEqual(relay['source'], 'publisher')
        self.assertTrue(relay['runOnReady'].endswith('ts_relay.py _in/icu 5600'))
        self.assertEqual(relay['runOnNotReady'], '')      # not announced to OpenTAKServer
        rules = json.loads(sent[0][2])['authHTTPExclude']
        self.assertIn({'action': 'publish', 'path': '_in/icu'}, rules)
        sent.clear(); have[1:] = [{'name': 'icu', 'source': 'udp://127.0.0.1:5600'}, dict(relay, name='_in/icu'),
                                  {'name': '_in/gone', 'source': 'publisher'}]
        vf.apply(self.root, opener=opener)
        self.assertEqual([(m, u) for m, u, _ in sent[2:]], [('DELETE', '/v3/config/paths/delete/_in/gone')])

    def test_camera_feeds_pull_a_url_and_hide_its_password(self):
        for bad in ('ftp://cam/x', 'rtsp://cam/a b', 'rtsp://cam/"x', 'cam:554'):
            with self.assertRaises(vf.FeedError):
                vf.add(self.root, 'cam1', source=bad)
        with self.assertRaises(vf.FeedError):
            vf.add(self.root, 'cam1', source='rtsp://cam/x', udp=True)
        vf.add(self.root, 'cam1', source='rtsp://admin:s3cret@192.168.1.20:554/stream1')
        self.assertEqual(oct((self.root / vf.FEEDS).stat().st_mode & 0o777), '0o600')
        self.assertEqual(vf.public(vf.load(self.root)[0])['source'], 'rtsp://admin:***@192.168.1.20:554/stream1')
        self.assertNotIn('s3cret', json.dumps(vf.status(self.root, opener=lambda *a, **k: FakeResponse(b'{}'))))
        vf.add(self.root, 'cam1', source='rtsp://192.168.1.21/live')       # new URL replaces the old
        self.assertEqual(vf.load(self.root)[0]['source'], 'rtsp://192.168.1.21/live')
        vf.add(self.root, 'cam1')                                          # back to a pushed feed
        self.assertNotIn('source', vf.load(self.root)[0])

    def test_apply_adds_patches_and_forgets_camera_paths(self):
        vf.add(self.root, 'cam1', source='rtsp://cam/new')
        vf.add(self.root, 'cam2', source='rtsp://cam/two')
        vf.add(self.root, 'push1')
        sent = []
        have = [{'name': 'cam1', 'source': 'rtsp://cam/old'}, {'name': 'push1', 'source': 'rtsp://cam/was'},
                {'name': 'gone', 'source': 'rtsp://cam/gone'}, {'name': 'notours', 'source': 'rtsp://elsewhere'}]

        def opener(request, timeout=0):
            sent.append((request.get_method(), request.full_url.split('9997')[1], request.data))
            if '/v3/config/paths/list' in request.full_url:
                return FakeResponse(json.dumps({'items': have}).encode())
            return FakeResponse(b'')
        vf.apply(self.root, opener=opener, forget=['gone'])
        calls = [(m, u) for m, u, _ in sent[2:]]
        self.assertEqual(sorted(calls), sorted([('DELETE', '/v3/config/paths/delete/push1'),
                                                ('DELETE', '/v3/config/paths/delete/gone'),
                                                ('PATCH', '/v3/config/paths/patch/cam1'),
                                                ('POST', '/v3/config/paths/add/cam2')]))

    def test_relay_unwraps_rtp_and_finds_the_track(self):
        packet = bytes([0x80, 33, 0, 1]) + b'\0' * 8 + b'\x47' + b'x' * 187
        self.assertEqual(ts_relay.rtp_payload(packet), b'\x47' + b'x' * 187)
        padded = bytes([0xA0, 33, 0, 1]) + b'\0' * 8 + b'TS' + b'\0\x02'
        self.assertEqual(ts_relay.rtp_payload(padded), b'TS')
        sdp = 'v=0\r\na=control:*\r\nm=video 0 RTP/AVP 33\r\na=control:trackID=0\r\n'
        self.assertEqual(ts_relay.control_url('rtsp://h:8554/_in/icu', sdp), 'rtsp://h:8554/_in/icu/trackID=0')

    def test_corrupt_file_reads_as_no_feeds(self):
        (self.root / vf.FEEDS).write_text('not json')
        self.assertEqual(vf.load(self.root), [])


if __name__ == '__main__':
    unittest.main()
