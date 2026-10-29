"""Is another host using this server's service address? (Two teams' meshes linked up, both
PortableTAK servers on 10.41.250.10.) An ARP duplicate-address probe on the mesh interface:
any answer from a MAC that is not ours is the other server. Linux only, needs root."""

import socket
import struct
import time
from pathlib import Path

_cache = {'at': 0.0, 'key': None, 'value': None}


def _mac(interface):
    return bytes.fromhex(Path('/sys/class/net/%s/address' % interface).read_text().strip().replace(':', ''))


def other_macs(address, interface, wait=1.5):
    """MACs (other than ours) that answer ARP for `address` on `interface`."""
    ours = _mac(interface)
    target = socket.inet_aton(address)
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
    try:
        s.bind((interface, 0))
        # Duplicate-address probe (RFC 5227): sender IP 0.0.0.0, so nobody learns from it.
        arp = struct.pack('!HHBBH6s4s6s4s', 1, 0x0800, 6, 4, 1, ours, b'\0' * 4, b'\0' * 6, target)
        s.send(b'\xff' * 6 + ours + b'\x08\x06' + arp)
        s.settimeout(0.3)
        found, end = set(), time.time() + wait
        while time.time() < end:
            try:
                frame = s.recv(2048)
            except socket.timeout:
                continue
            if len(frame) < 42 or frame[12:14] != b'\x08\x06':
                continue
            op, sha, spa = struct.unpack('!H6s4s', frame[20:32])
            if op == 2 and spa == target and sha != ours:
                found.add(sha.hex(':'))
        return sorted(found)
    finally:
        s.close()


def check(address, interface, every=60, now=time.time):
    """Cached for `every` seconds: list of other MACs, or None when it cannot be checked."""
    key = (address, interface)
    if _cache['key'] == key and now() - _cache['at'] < every:
        return _cache['value']
    try:
        value = other_macs(address, interface)
    except (OSError, ValueError):
        value = None
    _cache.update(at=now(), key=key, value=value)
    return value
