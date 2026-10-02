"""Apply reviewed generic patches to a newly built upstream OTS installation."""
from pathlib import Path
import hashlib
import json
import os
import stat
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from build_ca_module import build

COT_QUEUE_OLD = 'SocketIO(message_queue="amqp://" + app.config.get("OTS_RABBITMQ_SERVER_ADDRESS"))'
COT_QUEUE_NEW = ('SocketIO(message_queue="amqp://" + _pt_quote(app.config["OTS_RABBITMQ_USERNAME"], safe="")'
                 ' + ":" + _pt_quote(app.config["OTS_RABBITMQ_PASSWORD"], safe="")'
                 ' + "@" + app.config["OTS_RABBITMQ_SERVER_ADDRESS"])')


def patch_cot_parser(package, expected_sha256):
    """Give the CoT parser's Socket.IO queue the configured RabbitMQ credentials.

    Upstream 1.7.13 omits them here (app.py has the same line, patched above),
    so kombu falls back to RabbitMQ's guest account, which cartridges lack.
    """
    source = Path(package) / 'cot_parser/cot_parser.py'
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('unreviewed_ots_cot_parser_source')
    text = raw.decode()
    if text.count(COT_QUEUE_OLD) != 1:
        raise ValueError('unexpected_cot_parser_message_queue_source')
    text = 'from urllib.parse import quote as _pt_quote\n' + text.replace(COT_QUEUE_OLD, COT_QUEUE_NEW)
    compile(text, str(source), 'exec')
    upstream = source.with_name('cot_parser.py.upstream')
    if upstream.exists():
        raise ValueError('cot_parser_already_patched')
    source.rename(upstream)
    source.write_text(text)
    os.chmod(source, stat.S_IMODE(upstream.stat().st_mode))


def patch(package, source_manifest):
    package=Path(package)
    manifest=json.loads(Path(source_manifest).read_text())
    source=package/'certificate_authority.py'
    target=package/'certificate_authority.py.new'
    app=package/'app.py'
    raw=app.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['app_sha256']:
        raise ValueError('unreviewed_ots_app_source')
    text=raw.decode()
    old='message_queue="amqp://" + app.config.get("OTS_RABBITMQ_SERVER_ADDRESS"),'
    if text.count(old)!=1:
        raise ValueError('unexpected_message_queue_source')
    new='message_queue="amqp://" + _pt_quote(app.config["OTS_RABBITMQ_USERNAME"], safe="") + ":" + _pt_quote(app.config["OTS_RABBITMQ_PASSWORD"], safe="") + "@" + app.config["OTS_RABBITMQ_SERVER_ADDRESS"],'
    text=text.replace(old,new)
    text='from urllib.parse import quote as _pt_quote\n'+text
    compile(text,str(app),'exec')
    build(source,target,manifest['ca_sha256'])
    source.rename(package/'certificate_authority.py.upstream')
    target.replace(source)
    app.rename(package/'app.py.upstream')
    app.write_text(text)
    patch_cot_parser(package, manifest['cot_parser_sha256'])

if __name__=='__main__':
    patch(sys.argv[1],sys.argv[2])
