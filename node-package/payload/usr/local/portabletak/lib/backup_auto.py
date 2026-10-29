"""Automatic daily backups (dashboard: Backup / Restore > Automatic backups).

Run every few minutes by the watchdog: after the chosen hour, if there is no automatic backup
from today yet, make one (note "Automatic") and remove the oldest automatic backups beyond
"keep". Backups made by hand or uploaded are never removed here."""

import json
import subprocess
import time
from pathlib import Path

import backup_catalog

NOTE = 'Automatic'


def schedule(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if data.get('enabled') is not True:
        return None
    hour, keep = data.get('hour'), data.get('keep')
    if not (isinstance(hour, int) and 0 <= hour <= 23 and isinstance(keep, int) and 1 <= keep <= 60):
        return None
    return hour, keep


def automatic(backup_dir):
    """Automatic backups, newest first."""
    listed = backup_catalog.list_backups(backup_dir)['backups']
    return [b for b in listed if b['note'] == NOTE]


def due(backup_dir, hour, now=None):
    now = now or time.localtime()
    if now.tm_hour < hour:
        return False
    today = time.strftime('%Y-%m-%d', now)
    return not any(b['created_local'].startswith(today) for b in automatic(backup_dir))


def run(schedule_path='/mnt/asgard/config/backup-schedule.json',
        backup_dir='/mnt/asgard/portabletak/backups/recovery',
        backup_tool='/usr/local/portabletak/bin/portabletak-backup', runner=subprocess.run, now=None):
    """Returns 'off', 'not_due', 'made' or 'failed'."""
    plan = schedule(schedule_path)
    if not plan:
        return 'off'
    hour, keep = plan
    if not due(backup_dir, hour, now):
        return 'not_due'
    if runner([backup_tool, '--note', NOTE], capture_output=True, timeout=600).returncode != 0:
        return 'failed'
    for old in automatic(backup_dir)[keep:]:
        for f in (Path(backup_dir) / old['name'], Path(backup_dir) / (old['name'] + '.json')):
            f.unlink(missing_ok=True)
    return 'made'
