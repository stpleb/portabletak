"""Root-only Linux integration. Targets only the temporary test chroot."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import emergency_stop
assert os.geteuid() == 0
with tempfile.TemporaryDirectory(prefix='pt-stop-test-') as root:
    emergency_stop.ROOTFS = root
    emergency_stop.EXECUTABLE_PREFIX = root + '/'
    child = subprocess.Popen([sys.executable, '-c', 'import os,signal,sys; os.chroot(sys.argv[1]); os.chdir("/"); print("ready",flush=True); signal.pause()', root], stdout=subprocess.PIPE, text=True)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        assert child.stdout.readline().strip() == 'ready'
        result = emergency_stop.stop(grace=2)
        assert result == {'targeted': 1, 'remaining': 0}, result
        assert child.wait(timeout=3) < 0
        assert unrelated.poll() is None
        assert emergency_stop.stop(grace=0) == {'targeted': 0, 'remaining': 0}
        print('PASS: pidfd shutdown stops the test chroot, preserves unrelated process, repeats safely')
    finally:
        for process in (child, unrelated):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
