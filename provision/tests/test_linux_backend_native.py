"""Native pre-start bootstrap test. Run only in host-isolate.py."""

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(sys.argv[1]).resolve()
SOURCE = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(SOURCE / 'lib'))
sys.path.insert(0, str(SOURCE))
from bootstrap_engine import BootstrapEngine
from linux_bootstrap_backend import LinuxBootstrapBackend
from portabletak_provision import Controller, metadata, write_json
from release import expected_base_seal
from adapters.openmanet import OpenManetAdapter

assert os.getpid() == 1, 'must be PID 1 in the isolated namespace'
assert (ROOT.parent / '.portabletak-development').is_file()
seal = expected_base_seal()
request = json.load(sys.stdin)
controller = Controller(ROOT, lock='/tmp/portabletak-native-lifecycle.lock', require_mount=False,
                        expected_base_seal=seal)

class PrestartBackend(LinuxBootstrapBackend):
    def start_services(self, context):
        return {'test_boundary': 'service_start_not_run'}
    def verify_health(self, context):
        return {'test_boundary': 'physical_health_not_run'}

def diagnostic_runner(command, **kwargs):
    result = subprocess.run(command, **kwargs)
    if result.returncode and result.stderr:
        diagnostic = result.stderr.decode(errors='replace')
        payload = kwargs.get('input')
        if payload:
            try:
                for value in json.loads(payload).values():
                    if isinstance(value, str) and value:
                        diagnostic = diagnostic.replace(value, '[REDACTED]')
            except (ValueError, AttributeError):
                pass
        print('NATIVE COMMAND FAILED:', command[0], command[-1] if len(command) > 1 else '', file=sys.stderr)
        print(diagnostic[-4000:], file=sys.stderr)
    return result

backend = PrestartBackend(seal, host_root=ROOT,
                          prepare=(sys.executable, str(SOURCE/'tests/mount_linux_backend_native.py'), str(ROOT)),
                          mediamtx_source=SOURCE / 'vendor/mediamtx',
                          host_adapter=OpenManetAdapter(), runner=diagnostic_runner)
try:
    result = BootstrapEngine(controller, backend).run(request)
    assert result['complete']
    assert metadata(controller.meta)['PORTABLETAK_PROVISIONING_STATE'] == 'provisioned'
    assert (ROOT / 'postgres/PG_VERSION').read_text().strip() == '16'
    assert (ROOT / 'opentak/data/ca/ca.pem').is_file()
    assert (ROOT / 'rootfs/etc/rabbitmq/portabletak-definitions.json').is_file()
    assert (ROOT / 'rootfs/etc/nginx/sites-enabled/portabletak').is_symlink()
    assert json.loads((ROOT / 'config/host.json').read_text())['runtime_profile'] == 'openmanet'
    print('PASS: production backend verified clean base, storage, PostgreSQL, migrations, administrator, CA, RabbitMQ, nginx and MediaMTX configuration')
    print('NOT TESTED: service-start phase, physical health gate, USB behavior, TAK clients')
finally:
    pgctl = ROOT / 'rootfs/usr/lib/postgresql/16/bin/pg_ctl'
    if (ROOT / 'postgres/postmaster.pid').exists():
        subprocess.run(['/usr/sbin/chroot', str(ROOT / 'rootfs'),
                        '/usr/sbin/runuser', '-u', 'postgres', '--', str(pgctl).replace(str(ROOT / 'rootfs'), ''),
                        '-D', '/asgard/postgres', '-m', 'fast', '-w', '-t', '20', 'stop'], check=False)
