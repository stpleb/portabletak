"""Stand-in for an OpenMANET talk-group radio (the ManetNode plugin's format), for testing TAK PTT.

Opus 48 kHz mono, 20 ms frames, RTP payload type 111, multicast 239.41.0.1, talk group g on
port 38799 + 2g. Needs libopus (brew install opus); on Apple silicon with an Intel Homebrew run
it as `arch -x86_64 /usr/bin/python3 tools/omtalk.py ...`.

    omtalk.py send <wav, 48 kHz mono 16-bit> [group]   send the file as one transmission
    omtalk.py listen <seconds> <out.wav> [group]         record what is heard (all speakers mixed)
"""
import ctypes
import os
import random
import socket
import struct
import sys
import time
import wave

ADDR = '239.41.0.1'
RATE, FRAME, PT = 48000, 960, 111
for path in ('/opt/homebrew/lib/libopus.0.dylib', '/usr/local/lib/libopus.0.dylib', 'libopus.so.0'):
    try:
        opus = ctypes.CDLL(path)
        break
    except OSError:
        opus = None
if opus is None:
    sys.exit('libopus not found')
opus.opus_encoder_create.restype = ctypes.c_void_p
opus.opus_decoder_create.restype = ctypes.c_void_p


def port(group):
    return 38799 + 2 * group


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('192.0.2.1', 9))
    ip = s.getsockname()[0]
    s.close()
    return ip


def send(path, group):
    w = wave.open(path)
    assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, 'need 48 kHz mono 16-bit'
    pcm = w.readframes(w.getnframes())
    err = ctypes.c_int()
    enc = ctypes.c_void_p(opus.opus_encoder_create(RATE, 1, 2048, ctypes.byref(err)))   # VOIP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip()))
    ssrc, seq, ts = random.getrandbits(32), random.getrandbits(16), random.getrandbits(32)
    out = ctypes.create_string_buffer(1500)
    start, n = time.time(), 0
    for i in range(0, len(pcm) - FRAME * 2 + 1, FRAME * 2):
        frame = pcm[i:i + FRAME * 2]
        size = opus.opus_encode(enc, frame, FRAME, out, 1500)
        hdr = struct.pack('>BBHII', 0x80, (0x80 if n == 0 else 0) | PT, seq, ts, ssrc)
        s.sendto(hdr + out.raw[:size], (ADDR, port(group)))
        seq, ts, n = (seq + 1) & 0xFFFF, (ts + FRAME) & 0xFFFFFFFF, n + 1
        time.sleep(max(0, start + n * 0.02 - time.time()))
    print('sent %d packets (%.1f s) to %s:%d' % (n, n * 0.02, ADDR, port(group)))


def listen(seconds, path, group):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(('', port(group)))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 socket.inet_aton(ADDR) + socket.inet_aton(local_ip()))
    s.settimeout(0.2)
    decoders, pcm = {}, ctypes.create_string_buffer(FRAME * 6 * 2)
    audio, packets, senders = bytearray(), 0, set()
    end = time.time() + seconds
    print('listening on %s:%d for %d s' % (ADDR, port(group), seconds))
    while time.time() < end:
        try:
            data, src = s.recvfrom(2048)
        except socket.timeout:
            continue
        if len(data) < 13 or data[0] & 0xC0 != 0x80 or data[1] & 0x7F != PT:
            continue
        off = 12 + 4 * (data[0] & 0x0F)
        ssrc = struct.unpack('>I', data[8:12])[0]
        if ssrc not in decoders:
            err = ctypes.c_int()
            decoders[ssrc] = ctypes.c_void_p(opus.opus_decoder_create(RATE, 1, ctypes.byref(err)))
            print('hearing ssrc %08x from %s' % (ssrc, src[0]))
        senders.add(src[0])
        payload = data[off:]
        n = opus.opus_decode(decoders[ssrc], payload, len(payload), pcm, FRAME * 6, 0)
        if n > 0:
            audio += pcm.raw[:n * 2]
            packets += 1
    w = wave.open(path, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(bytes(audio))
    w.close()
    print('heard %d packets (%.1f s of audio) from %s -> %s' % (packets, len(audio) / 2 / RATE, sorted(senders), path))


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'send':
        send(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    elif len(sys.argv) >= 4 and sys.argv[1] == 'listen':
        listen(int(sys.argv[2]), sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 1)
    else:
        sys.exit(__doc__)
