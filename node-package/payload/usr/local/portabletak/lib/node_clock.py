"""Set an OpenMANET node's clock from the administrator's browser at dashboard sign-in, or
from the position reports phones send (from_phones).

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


ROOTFS_QUERY = ("select extract(epoch from timestamp) from cot where sender_uid is not null "
                "order by id desc limit 3")


def newest_report_times(root='/mnt/asgard', run=subprocess.run):
    """Times (epoch s) the phones put on the last 3 reports the server stored (OpenTAKServer keeps
    the phone's own time, not when it arrived)."""
    rootfs = run(['/usr/local/portabletak/lib/pt-rootfs', root], capture_output=True, text=True, timeout=10).stdout.strip()
    if not rootfs:
        return []
    out = run(['/usr/sbin/chroot', rootfs, 'su', 'postgres', '-s', '/bin/sh', '-c',
               'psql -At -d ots -c "%s"' % ROOTFS_QUERY], capture_output=True, text=True, timeout=20).stdout
    times = []
    for line in out.split():
        try:
            times.append(float(line))
        except ValueError:
            pass
    return times


def from_phones(times, profile=None, now=time.time, run=subprocess.run):
    """Move a node's clock forward when the phones' newest reports are dated ahead of it.

    A report that has just arrived cannot be from the future, so three reports more than two
    minutes ahead (and within an hour of each other) mean the node's clock is behind: the usual
    case after a reboot with no internet. Never moves the clock back (an old report says
    nothing). Returns the correction in seconds, or None."""
    if len(times) < 3 or max(times) - min(times) > 3600:
        return None
    if min(times) - now() <= TOLERANCE:
        return None
    return set_from_client(max(times) * 1000, profile, now=now, run=run)
