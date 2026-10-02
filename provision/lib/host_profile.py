"""Read /etc/portabletak/host-profile without executing it (PT_KEY=value lines)."""

from pathlib import Path
import platform
import re

KEYS = {'PT_PROFILE', 'PT_BOOT_DEV', 'PT_SERVICE_IP', 'PT_SERVICE_DEV', 'PT_ADVERTISE_IP', 'PT_EXTRA_IPS'}


def read_profile(path='/etc/portabletak/host-profile'):
    values = {'PT_PROFILE': 'openmanet'}
    try:
        lines = Path(path).read_text().splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        key, sep, value = line.strip().partition('=')
        if sep and key in KEYS:
            values[key] = value.strip().strip('"').strip("'")
    if not re.fullmatch(r'[a-z0-9_-]+', values['PT_PROFILE']):
        values['PT_PROFILE'] = 'openmanet'
    return values


def select_adapter(profile):
    if profile['PT_PROFILE'] == 'linux':
        from adapters.linux import LinuxAdapter
        return LinuxAdapter(profile.get('PT_SERVICE_IP', ''), profile.get('PT_SERVICE_DEV', ''),
                            advertise=profile.get('PT_ADVERTISE_IP') or None,
                            extra=[a for a in profile.get('PT_EXTRA_IPS', '').split(',') if a.strip()])
    from adapters.openmanet import OpenManetAdapter
    return OpenManetAdapter()


def host_arch(machine=None):
    """'x86_64' on Intel/AMD hosts, otherwise 'arm64'."""
    machine = (machine or platform.machine()).lower()
    return 'x86_64' if machine in ('x86_64', 'amd64') else 'arm64'


def rootfs(root='/mnt/asgard', machine=None):
    """The cartridge rootfs for this host's processor (dual-chip cartridges keep
    the x86_64 software under arch/amd64/; data is shared)."""
    if host_arch(machine) == 'x86_64':
        return Path(root) / 'arch/amd64/rootfs'
    return Path(root) / 'rootfs'
