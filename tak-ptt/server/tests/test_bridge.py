"""Tests for the OpenMANET bridge's packet code (no server or network needed).

    python3 -m unittest discover -s server/tests
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bridge'))
import takptt_bridge as b  # noqa: E402
import takptt_recorder as r  # noqa: E402


class VoicePacketTests(unittest.TestCase):
    def test_round_trip_through_mumble_varints(self):
        for seq in (1, 200, 70000, 1 << 22, 1 << 30):
            opus = bytes([(31 << 3)]) + b'x' * 60
            p = b.voice_packet(seq, opus, seq == 200)
            self.assertEqual(p[0] >> 5, 4)                       # Opus
            got, i = r.mumble_varint(p, 1)
            self.assertEqual(got, seq)
            header, i = r.mumble_varint(p, i)
            self.assertEqual(header & 0x1FFF, len(opus))
            self.assertEqual(bool(header & 0x2000), seq == 200)
            self.assertEqual(p[i:], opus)


class RtpTests(unittest.TestCase):
    def test_payload_offset(self):
        hdr = struct.pack('>BBHII', 0x80, 0x80 | 111, 1, 2, 3)
        self.assertEqual(b.rtp_payload(hdr + b'opus'), 12)
        self.assertEqual(b.rtp_payload(struct.pack('>BBHII', 0x80, 96, 1, 2, 3) + b'opus'), -1)  # not Opus
        csrc = struct.pack('>BBHII', 0x81, 111, 1, 2, 3) + b'\0' * 4
        self.assertEqual(b.rtp_payload(csrc + b'opus'), 16)
        self.assertEqual(b.rtp_payload(hdr), -1)                 # no payload


class LinkTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(b.parse_links('LOBBY=1, Ops=2'), [('LOBBY', 1), ('Ops', 2)])
        self.assertEqual(b.parse_links('Team=A=3'), [('Team=A', 3)])
        self.assertEqual(b.parse_links(''), [])
        with self.assertRaises(ValueError):
            b.parse_links('LOBBY=6')

    def test_ports_and_names(self):
        self.assertEqual(b.talk_group_port(1), 38801)
        self.assertEqual(b.talk_group_port(5), 38809)
        self.assertEqual(b.name_for(2), 'OpenMANET CH 2')


class DuplicateTests(unittest.TestCase):
    def test_same_packet_on_both_addresses_counts_once(self):
        tg = b.TalkGroup.__new__(b.TalkGroup)
        tg.heard = {}
        self.assertTrue(tg.fresh(7, 100, 1.0))
        self.assertFalse(tg.fresh(7, 100, 1.01))       # the copy from the other address
        self.assertTrue(tg.fresh(7, 101, 1.02))
        self.assertTrue(tg.fresh(8, 100, 1.05))        # another speaker
        self.assertTrue(tg.fresh(7, 50, 9.0))          # long after: a new transmission
        self.assertTrue(tg.fresh(9, 65535, 10.0))
        self.assertTrue(tg.fresh(9, 0, 10.02))         # sequence wraps


class InterfaceTests(unittest.TestCase):
    IP = '''1: lo    inet 127.0.0.1/8 scope host lo
5: br-ahwlan    inet 10.41.0.1/16 brd 10.41.255.255 scope global br-ahwlan
5: br-ahwlan    inet 10.41.250.10/32 scope global br-ahwlan
9: tailscale0    inet 100.64.0.4/32 scope global tailscale0
'''

    def addresses(self, only=None):
        real = b.subprocess.run
        b.subprocess.run = lambda *a, **k: type('R', (), {'stdout': self.IP})()
        try:
            return b.interface_addresses(only)
        finally:
            b.subprocess.run = real

    def test_one_address_per_interface(self):
        # A node's br-ahwlan has two addresses; sending from both doubles every packet.
        self.assertEqual(self.addresses(), [('br-ahwlan', '10.41.0.1')])

    def test_named_interfaces(self):
        self.assertEqual(self.addresses({'tailscale0'}), [('tailscale0', '100.64.0.4')])


if __name__ == '__main__':
    unittest.main()
