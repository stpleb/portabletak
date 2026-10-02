"""One host-side lock shared by PortableTAK lifecycle operations."""

import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from portabletak_provision import ProvisionError, write_json


OPERATIONS = frozenset({
    'autoload', 'backup', 'factory-reset', 'layout', 'provision', 'restore',
    'safe-remove', 'start', 'stop'
})


class LifecycleLock:
    def __init__(self, lock_path='/tmp/portabletak/lifecycle.lock',
                 state_path='/tmp/portabletak/lifecycle.json', clock=None):
        self.lock_path = Path(lock_path)
        self.state_path = Path(state_path)
        self.clock = clock or (lambda: int(time.time()))
        self.fd = None
        self.owner_pid = None

    def acquire(self, operation, cartridge_id=None):
        if operation not in OPERATIONS:
            raise ProvisionError('invalid_lifecycle_operation')
        if self.fd is not None:
            raise ProvisionError('lifecycle_lock_already_held')
        self.lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ProvisionError('lifecycle_busy') from None
        self.fd = fd
        self.owner_pid = os.getpid()
        state = {'format': 1, 'operation': operation, 'pid': self.owner_pid,
                 'started_at': self.clock()}
        if cartridge_id is not None:
            state['cartridge_id'] = cartridge_id
        write_json(self.state_path, state)
        return state

    def release(self):
        if self.fd is None:
            return
        try:
            try:
                data = json.loads(self.state_path.read_text())
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict) and data.get('pid') == self.owner_pid:
                try:
                    self.state_path.unlink()
                except FileNotFoundError:
                    pass
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None
            self.owner_pid = None

    def __enter__(self):
        if self.fd is None:
            raise ProvisionError('lifecycle_lock_not_acquired')
        return self

    def __exit__(self, *args):
        self.release()


def run_locked(operation, command, cartridge_id=None, lock=None):
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ProvisionError('invalid_lifecycle_command')
    guard = lock or LifecycleLock()
    guard.acquire(operation, cartridge_id)
    try:
        return subprocess.run(command, check=False).returncode
    finally:
        guard.release()
