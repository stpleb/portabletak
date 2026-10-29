"""Set an OpenMANET node's clock from the administrator's browser at dashboard sign-in.

A node has no clock that keeps time while off and usually no internet for NTP, so after a
reboot its time can be hours off (voice history, logs and certificates then carry wrong times).
Only on OpenMANET nodes, only after the password checked out, and only when more than two
minutes off.
"""

import subprocess
import time

from host_profile import read_profile

TOLERANCE = 120                       # seconds
EARLIEST, LATEST = 1767225600, 4102444800    # 2026-01-01 .. 2100-01-01: anything else is not a real clock


def set_from_client(client_ms, profile=None, now=time.time, run=subprocess.run):
    """Returns the correction made in seconds, or None when nothing was changed."""
    if isinstance(client_ms, bool) or not isinstance(client_ms, (int, float)):
        return None
    if (profile or read_profile())['PT_PROFILE'] != 'openmanet':
        return None
    target = client_ms / 1000.0
    if not EARLIEST <= target <= LATEST:
        return None
    offset = target - now()
    if abs(offset) <= TOLERANCE:
        return None
    if run(['date', '-s', '@%d' % int(target)], capture_output=True).returncode != 0:
        return None
    return offset
