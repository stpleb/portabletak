"""How hot the host is: the hottest thermal zone, with a level and a small log.

OpenMANET nodes run warm, and a hot node or USB drive can drop the drive off USB (seen on Node01).
The dashboard shows the temperature and warns before that; the log shows when it ran hot.
"""

import glob
import time
from pathlib import Path

WARM, HOT = 75.0, 80.0          # Raspberry Pi class boards start slowing down at 80-85 C
LOG = Path('/mnt/asgard/opentak/data/logs/temperature.log')
EVERY = 600                     # seconds between log lines while warm or hot

_last = {'level': None, 'at': 0.0}


def read(zones='/sys/class/thermal/thermal_zone*/temp'):
    """Hottest zone in degrees C, or None where there are no sensors (a virtual machine)."""
    best = None
    for path in glob.glob(zones):
        try:
            c = int(Path(path).read_text().strip()) / 1000.0
        except (OSError, ValueError):
            continue
        if -20 < c < 150 and (best is None or c > best):
            best = c
    return best


def level(c):
    if c is None:
        return None
    return 'hot' if c >= HOT else 'warm' if c >= WARM else 'ok'


def status(zones='/sys/class/thermal/thermal_zone*/temp', log=LOG, now=time.time):
    c = read(zones)
    lv = level(c)
    if lv is not None and (lv != _last['level'] or (lv != 'ok' and now() - _last['at'] >= EVERY)):
        if lv != 'ok' or _last['level'] not in (None, 'ok'):
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, 'a') as f:
                    f.write('%s %.1f C %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), c, lv))
            except OSError:
                pass
        _last.update(level=lv, at=now())
    return {'c': None if c is None else round(c, 1), 'level': lv}
