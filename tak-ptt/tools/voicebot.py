"""Test Mumble client for TAK PTT: joins the server as MacBot, records what other users
transmit (one WAV per transmission in /out) and plays a short tone burst every few
seconds so phones have something to receive.
    python voicebot.py HOST [seconds]"""
import math
import os
import struct
import sys
import time
import wave

import pymumble_py3 as pymumble
from pymumble_py3.constants import PYMUMBLE_CLBK_SOUNDRECEIVED

# Optional: PORT, and CERT / KEY (PEM files) to sign in with a client certificate.
host = sys.argv[1]
duration = int(sys.argv[2]) if len(sys.argv) > 2 else 60
RATE = 48000
recording = {}   # user -> [chunks, last time]


def on_sound(user, chunk):
    name = user['name']
    rec = recording.setdefault(name, {'pcm': bytearray(), 'last': 0.0, 'start': time.time()})
    if time.time() - rec['last'] > 1.0 and rec['pcm']:
        save(name, rec)
        rec.update(pcm=bytearray(), start=time.time())
    rec['pcm'] += chunk.pcm
    rec['last'] = time.time()


def save(name, rec):
    path = '/out/%s-%d.wav' % (name, int(rec['start']))
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(bytes(rec['pcm']))
    samples = struct.unpack('<%dh' % (len(rec['pcm']) // 2), rec['pcm'])
    peak = max((abs(s) for s in samples), default=0)
    print('RECORDED %s %.1fs peak=%d -> %s' % (name, len(samples) / RATE, peak, path), flush=True)


def tone(seconds=1.5, freq=660):
    n = int(RATE * seconds)
    return b''.join(struct.pack('<h', int(9000 * math.sin(2 * math.pi * freq * i / RATE))) for i in range(n))


m = pymumble.Mumble(host, os.environ.get('NAME', 'MacBot'), reconnect=False, port=int(os.environ.get('PORT', '64738')), password=os.environ.get('PASSWORD', ''), certfile=os.environ.get('CERT') or None, keyfile=os.environ.get('KEY') or None)
m.callbacks.set_callback(PYMUMBLE_CLBK_SOUNDRECEIVED, on_sound)
m.set_receive_sound(True)
m.start(); m.is_ready()
print('CONNECTED users=%s' % [u['name'] for u in m.users.values()], flush=True)
want = os.environ.get('CHANNEL')               # optional: join this channel first
if want:
    for c in m.channels.values():
        if c['name'] == want or (want == 'LOBBY' and c['channel_id'] == 0):
            c.move_in()
            time.sleep(1)
            print('IN CHANNEL', want, flush=True)
beep = tone()
end = time.time() + duration
quiet = os.environ.get('QUIET') == '1'     # record only, play no tones
while time.time() < end:
    if quiet:
        time.sleep(1)
        continue
    m.sound_output.add_sound(beep)
    print('SENT tone 1.5s', flush=True)
    time.sleep(6)
for name, rec in recording.items():
    if rec['pcm']:
        save(name, rec)
print('USERS %s' % [u['name'] for u in m.users.values()], flush=True)
