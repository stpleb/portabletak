"""Tests for the recorder's own protocol and file code (no server needed).

    python3 -m unittest discover -s server/tests
"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'recorder'))
import takptt_recorder as r  # noqa: E402


class ProtobufTests(unittest.TestCase):
    def test_round_trip(self):
        data = r.pb([(1, 300), (3, 'PortableTAK Recorder'), (5, True), (21, 7), (21, 9)])
        m = r.unpb(data)
        self.assertEqual(m[1], [300])
        self.assertEqual(m[3], [b'PortableTAK Recorder'])
        self.assertEqual(m[5], [1])
        self.assertEqual(m[21], [7, 9])


class MumbleVarintTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(r.mumble_varint(bytes([0x05]), 0), (5, 1))
        self.assertEqual(r.mumble_varint(bytes([0x81, 0x00]), 0), (256, 2))
        self.assertEqual(r.mumble_varint(bytes([0xC1, 0x00, 0x00]), 0), (65536, 3))
        self.assertEqual(r.mumble_varint(bytes([0xE1, 0, 0, 0]), 0), (1 << 24, 4))
        self.assertEqual(r.mumble_varint(bytes([0xF0]) + struct.pack('>I', 123456789), 0), (123456789, 5))
        self.assertEqual(r.mumble_varint(bytes([0xFC]), 0), (-1, 1))


class OpusTests(unittest.TestCase):
    def test_samples_per_packet(self):
        self.assertEqual(r.opus_samples(bytes([(31 << 3) | 0])), 960)        # CELT 20 ms, one frame
        self.assertEqual(r.opus_samples(bytes([(31 << 3) | 1])), 1920)       # two frames
        self.assertEqual(r.opus_samples(bytes([(31 << 3) | 3, 3])), 2880)    # three frames (code 3)
        self.assertEqual(r.opus_samples(bytes([(1 << 3) | 0])), 960)         # SILK 20 ms
        self.assertEqual(r.opus_samples(b''), 0)


class OggTests(unittest.TestCase):
    def test_pages_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'clip.opus')
            ogg = r.OggOpus(path)
            for _ in range(50):
                ogg.add(bytes([(31 << 3) | 0]) + os.urandom(60))
            seconds = ogg.close()
            self.assertAlmostEqual(seconds, 1.0)
            data = open(path, 'rb').read()
        pages, i = [], 0
        while i < len(data):
            self.assertEqual(data[i:i + 4], b'OggS')
            nseg = data[i + 26]
            size = 27 + nseg + sum(data[i + 27:i + 27 + nseg])
            page = bytearray(data[i:i + size])
            crc = struct.unpack_from('<I', page, 22)[0]
            struct.pack_into('<I', page, 22, 0)
            self.assertEqual(r.ogg_crc(page), crc)
            pages.append(page)
            i += size
        self.assertEqual(len(pages), 2 + 50 + 1)            # head, tags, packets, end
        self.assertEqual(pages[0][5], 0x02)                  # first page
        self.assertEqual(pages[-1][5], 0x04)                 # last page
        self.assertIn(b'OpusHead', bytes(pages[0]))


class StoreTests(unittest.TestCase):
    def test_add_list_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = r.Store(tmp, days=7, max_mb=1)
            import time
            old = s.path(time.time() - 8 * 86400)
            open(old, 'wb').write(b'x' * 10)
            s.add(old, 1, 'Ops', 'ALPHA', 'alpha', time.time() - 8 * 86400, 2.0, None, None)
            new = s.path(time.time())
            open(new, 'wb').write(b'y' * 10)
            cid = s.add(new, 1, 'Ops', 'BRAVO', 'bravo', time.time(), 3.0, 33.5, -101.9)
            clips = s.list()                         # the 8-day-old clip went when BRAVO's was added
            self.assertEqual([c['sender'] for c in clips], ['BRAVO'])
            self.assertEqual(clips[0]['lat'], 33.5)
            self.assertFalse(os.path.exists(old))
            self.assertEqual(s.file(cid), new)


class MeshKeyTests(unittest.TestCase):
    def test_weekly_keys_differ_by_week_and_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = 1793174400.0
            a = r.mesh_epochs(tmp, ['Ops', 'LOBBY'], now)
            b = r.mesh_epochs(tmp, ['Ops', 'LOBBY'], now + r.WEEK)
            self.assertEqual(len(a), 2)
            self.assertEqual(a[1]['keys'], b[0]['keys'])           # next week is the same key
            self.assertNotEqual(a[0]['keys']['Ops'], a[1]['keys']['Ops'])
            self.assertNotEqual(a[0]['keys']['Ops'], a[0]['keys']['LOBBY'])
            self.assertTrue(a[0]['from'] <= now < a[0]['until'])
            self.assertEqual(oct(os.stat(os.path.join(tmp, 'mesh.secret')).st_mode & 0o777), '0o600')


if __name__ == '__main__':
    unittest.main()
