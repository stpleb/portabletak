"""Give the federation add-on's opentakserver copy the same PortableTAK patches the
sealed base gets from portabletak-provision/build/patch-ots.py.

The add-on is first on PYTHONPATH, so every module in it replaces the base's. Without
this the stock certificate authority runs: it encrypts the .p12 files with the CA
password but writes the client password into the package settings, and ATAK reports
"Invalid Truststore".

usage: patch-overlay.py <overlay>/opentakserver <ots git checkout>
"""
import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROVISION = HERE.parent / 'portabletak-provision'
sys.path.insert(0, str(PROVISION / 'lib'))
from build_ca_module import build  # noqa: E402

# Reviewed OTS 1.7.13 wheel source of certificate_authority.py (templates/ots-source.json).
REVIEWED_CA = HERE.parent / 'clean-base-downloads' / 'upstream-certificate_authority.py'
REVIEWED_CA_SHA256 = 'f407352f3bb16037d01e188ae83ac3a17023566561fc83c4c87c01ab57d26112'

QUOTE = 'from urllib.parse import quote as _pt_quote\n'
CREDENTIALS = ('_pt_quote(app.config["OTS_RABBITMQ_USERNAME"], safe="") + ":" + '
               '_pt_quote(app.config["OTS_RABBITMQ_PASSWORD"], safe="") + "@" + '
               'app.config["OTS_RABBITMQ_SERVER_ADDRESS"]')
QUEUE_OLD = '"amqp://" + app.config.get("OTS_RABBITMQ_SERVER_ADDRESS")'


def _git_show(checkout, ref, path):
    return subprocess.run(['git', '-C', str(checkout), 'show', f'{ref}:{path}'],
                          check=True, capture_output=True).stdout


def patch_queue(path):
    """Socket.IO message queue with the cartridge's RabbitMQ credentials (no guest account)."""
    text = path.read_text()
    if text.count(QUEUE_OLD) != 1:
        raise ValueError(f'unexpected message queue source in {path.name}')
    text = QUOTE + text.replace(QUEUE_OLD, '"amqp://" + ' + CREDENTIALS)
    compile(text, str(path), 'exec')
    path.write_text(text)


def patch_ca(package, checkout):
    """Swap in the base's generic CA module. Only safe while the fork leaves the CA
    exactly as upstream 1.7.13 has it."""
    ca = 'opentakserver/certificate_authority.py'
    if _git_show(checkout, 'pt-fed', ca) != _git_show(checkout, '1.7.13', ca):
        raise ValueError('pt-fed changed certificate_authority.py; review before patching')
    target = package / 'certificate_authority.py'
    target.unlink()
    build(REVIEWED_CA, target, REVIEWED_CA_SHA256)
    if '_pt_openssl' not in target.read_text():
        raise ValueError('generic CA module not written')


def main(package, checkout):
    package = Path(package)
    patch_ca(package, checkout)
    patch_queue(package / 'app.py')
    patch_queue(package / 'cot_parser' / 'cot_parser.py')
    for name in ('certificate_authority.py', 'app.py', 'cot_parser/cot_parser.py'):
        print('patched', name, hashlib.sha256((package / name).read_bytes()).hexdigest()[:16])


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
