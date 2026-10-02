"""Exercise a fresh OTS stack inside a private PID/network/mount namespace.

No physical cartridge access. This is runtime integration evidence, not the
production provisioning/rollback implementation or physical acceptance.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import ssl

assert os.getpid() == 1, 'Must run as PID 1 under the isolated development launcher'
assert Path('/.portabletak-runtime-test').is_file()
os.umask(0o077)
ROOT=Path('/asgard')
DATA=ROOT/'opentak/data'
PG=ROOT/'postgres'
assert not DATA.exists(), 'Never reuse populated application data'
assert PG.is_dir() and not any(PG.iterdir()), 'Never reuse populated database data'
BASE=Path('/portabletak-test')
sys.path.insert(0,str(BASE/'lib'))
from portabletak_provision import Controller, write_metadata, write_json, metadata

processes=[]
logs=[]
def run(args, **kw):
    try:
        return subprocess.run(args,check=True,capture_output=True,timeout=90,**kw)
    except subprocess.CalledProcessError as error:
        output=((error.stdout or b'')+(error.stderr or b'')).decode(errors='replace')
        hidden=list(globals().get('private',{}).values())+[v for k,v in globals().get('request',{}).items() if 'password' in k]
        for value in hidden:
            output=output.replace(value,'[redacted]')
        print(output[-5000:],flush=True)
        raise
def spawn(args, name, env=None):
    log=open('/tmp/'+name+'.log','wb'); logs.append(log)
    process=subprocess.Popen(args,stdout=log,stderr=log,env=env)
    processes.append(process)
    return process

def wait_port(port, process=None):
    for _ in range(120):
        if process is not None and process.poll() is not None:
            raise AssertionError('process_exited_before_port_'+str(port))
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=1): return
        except OSError: time.sleep(0.5)
    raise AssertionError('listener_timeout_'+str(port))

try:
    run(['ip','link','set','lo','up'])
    run(['ip','address','add','10.41.250.10/32','dev','lo'])
    Path('/etc/hosts').write_text('127.0.0.1 localhost portabletak-build\n')
    request={'instance_name':'PortableTAK Integration','timezone':'Etc/UTC',
             'administrator_password':'Integration-'+secrets.token_hex(16),
             'client_password':'Client-'+secrets.token_hex(16)}
    cartridge=Path('/tmp/test-cartridge'); (cartridge/'portabletak').mkdir(parents=True)
    import uuid
    write_metadata(cartridge/'portabletak/cartridge.conf',{
        'PORTABLETAK_FORMAT':'1','PORTABLETAK_ARCH':'arm64','PORTABLETAK_CARTRIDGE_ID':str(uuid.uuid4()),
        'PORTABLETAK_INSTANCE_NAME':'Unconfigured','PORTABLETAK_INSTANCE_TYPE':'generic','PORTABLETAK_PROVISIONING_STATE':'new'})
    write_json(cartridge/'portabletak/base.json',{'format':1,'kind':'portabletak-clean-base','architecture':'arm64'})
    controller=Controller(cartridge,lock='/tmp/test-provision.lock',require_mount=False)
    result=controller.stage(request)
    stage=controller.state_dir/result['attempt_id']
    config=json.loads((stage/'config.yml').read_text())
    # MediaMTX binary is a separate build item; do not pretend it was tested.
    config['OTS_MEDIAMTX_ENABLE']=False
    DATA.mkdir(parents=True,mode=0o700)
    write_json(DATA/'config.yml',config)
    private=json.loads((stage/'secrets.json').read_text())
    postgres=pwd.getpwnam('postgres')
    PG.chmod(0o700); os.chown(PG,postgres.pw_uid,postgres.pw_gid)
    Path('/run/postgresql').mkdir(mode=0o755,exist_ok=True)
    os.chown('/run/postgresql',postgres.pw_uid,postgres.pw_gid)
    run(['runuser','-u','postgres','--','/usr/lib/postgresql/16/bin/initdb','-D',str(PG),'--auth-local=peer','--auth-host=scram-sha-256'])
    pg=spawn(['runuser','-u','postgres','--','/usr/lib/postgresql/16/bin/postgres','-D',str(PG),'-h','127.0.0.1'],'postgres')
    wait_port(5432,pg)
    db_script='''import sys,json,psycopg
from psycopg import sql
password=json.load(sys.stdin)['password']
with psycopg.connect('dbname=postgres user=postgres host=/run/postgresql',autocommit=True) as conn:
 assert conn.execute('SHOW data_directory').fetchone()[0]=='/asgard/postgres'
 conn.execute(sql.SQL('CREATE ROLE ots LOGIN PASSWORD {}').format(sql.Literal(password)))
 conn.execute('CREATE DATABASE ots OWNER ots')
'''
    run(['runuser','-u','postgres','--',sys.executable,'-c',db_script],input=json.dumps({'password':private['database_password']}).encode())
    environment=dict(os.environ,OTS_DATA_FOLDER=str(DATA))
    # Each application import happens in its own process to contain gevent patches.
    admin_script='''import sys,json
from pathlib import Path
sys.path.insert(0,'/portabletak-test/lib')
from opentakserver.app import create_app
from opentakserver.extensions import db
from flask_migrate import upgrade
from flask_security import verify_password
from sqlalchemy import text
import opentakserver
from bootstrap_admin import initialize_administrator
from opentakserver.certificate_authority import CertificateAuthority
app=create_app(cli=True)
with app.app_context():
 assert db.session.execute(text('SELECT current_database()')).scalar()=='ots'
 assert db.session.execute(text('SELECT current_user')).scalar()=='ots'
 upgrade(directory=str(Path(opentakserver.__file__).parent/'migrations'))
password=json.load(sys.stdin)['password']
initialize_administrator(app,password)
with app.app_context():
 user=app.security.datastore.find_user(username='administrator')
 assert verify_password(password,user.password)
 assert not verify_password('password',user.password)
 ca=CertificateAuthority(app.logger,app)
 ca.create_ca()
print('administrator_and_ca_ready')
'''
    run([sys.executable,'-c',admin_script],input=json.dumps({'password':request['administrator_password']}).encode(),env=environment)
    print('PASS: staged secrets, fresh PostgreSQL, native migrations, chosen administrator, fresh CA',flush=True)
    rabbit=pwd.getpwnam('rabbitmq')
    rabbit_dir=Path('/var/lib/rabbitmq'); rabbit_dir.mkdir(exist_ok=True)
    os.chown(rabbit_dir,rabbit.pw_uid,rabbit.pw_gid)
    Path('/var/log/rabbitmq').mkdir(parents=True,exist_ok=True)
    os.chown('/var/log/rabbitmq',rabbit.pw_uid,rabbit.pw_gid)
    cookie=rabbit_dir/'.erlang.cookie'; cookie.write_text(private['rabbitmq_cookie']); cookie.chmod(0o400)
    os.chown(cookie,rabbit.pw_uid,rabbit.pw_gid)
    salt=secrets.token_bytes(4)
    password_hash=base64.b64encode(salt+hashlib.sha256(salt+private['rabbitmq_password'].encode()).digest()).decode()
    definition=Path('/etc/rabbitmq/portabletak-definitions.json')
    write_json(definition,{'users':[{'name':'portabletak','password_hash':password_hash,'hashing_algorithm':'rabbit_password_hashing_sha256','tags':[]}],
                         'vhosts':[{'name':'/'}], 'permissions':[{'user':'portabletak','vhost':'/','configure':'.*','write':'.*','read':'.*'}]})
    os.chown(definition,rabbit.pw_uid,rabbit.pw_gid)
    rabbit_config=Path('/etc/rabbitmq/rabbitmq.conf')
    rabbit_config.write_text(
        'listeners.tcp.default = 127.0.0.1:5672\n'
        'definitions.import_backend = local_filesystem\n'
        'definitions.local.path = /etc/rabbitmq/portabletak-definitions.json\n'
    )
    rabbit_config.chmod(0o640)
    os.chown(rabbit_config,rabbit.pw_uid,rabbit.pw_gid)
    rabbitenv=dict(os.environ,RABBITMQ_NODENAME='rabbit@portabletak-build',RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS='+S 2:2')
    mq=spawn(['runuser','-u','rabbitmq','--','rabbitmq-server'],'rabbitmq',rabbitenv)
    wait_port(5672,mq)
    # Assert credentials, rather than accepting just an open listener.
    import pika
    credentials=pika.PlainCredentials('portabletak',private['rabbitmq_password'])
    connection=pika.BlockingConnection(pika.ConnectionParameters('127.0.0.1',credentials=credentials)); connection.close()
    ots=spawn(['/asgard/opentak/venv/bin/opentakserver'],'ots',environment)
    wait_port(8081,ots)
    cert=DATA/'ca/certs/opentakserver/opentakserver.pem'
    key=DATA/'ca/certs/opentakserver/opentakserver.nopass.key'
    assert cert.is_file() and key.is_file()
    nginx=Path('/tmp/nginx.conf')
    nginx.write_text('pid /run/portabletak-nginx.pid;\nerror_log /tmp/nginx-error.log;\nevents {}\nhttp { access_log off; server { listen 127.0.0.1:8443 ssl; ssl_certificate '+str(cert)+'; ssl_certificate_key '+str(key)+'; location / { proxy_pass http://127.0.0.1:8081; proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto https; } } }\n')
    run(['nginx','-t','-c',str(nginx)])
    web=spawn(['nginx','-c',str(nginx),'-g','daemon off;'],'nginx')
    wait_port(8443,web)
    # Verify CA trust and IP SAN while connecting to the private listener.
    context=ssl.create_default_context(cafile=str(DATA/'ca/ca.pem'))
    with socket.create_connection(('127.0.0.1',8443),timeout=10) as raw:
        with context.wrap_socket(raw,server_hostname='10.41.250.10') as connection:
            connection.sendall(b'GET / HTTP/1.1\r\nHost: 10.41.250.10\r\nConnection: close\r\n\r\n')
            response=connection.recv(4096)
            # OTS 1.7.13 has no root route, so a proxied 404 is the expected
            # application response. TLS validation and the IP SAN have already
            # succeeded before this assertion.
            assert response.startswith((b'HTTP/1.1 200',b'HTTP/1.1 302',b'HTTP/1.1 404')), response[:60]
            assert b'Server: nginx/' in response
    print('PASS: independent RabbitMQ credentials, OTS listener, nginx TLS, CA chain and service IP',flush=True)
    print('NOT TESTED: MediaMTX, client devices, production activation/retry, physical USB',flush=True)
except Exception:
    hidden=list(globals().get('private',{}).values())+[v for k,v in globals().get('request',{}).items() if 'password' in k]
    for log in logs:
        log.flush()
        output=Path(log.name).read_text(errors='replace')[-3000:]
        for value in hidden:
            output=output.replace(value,'[redacted]')
        print(Path(log.name).name+': '+output,flush=True)
    raise
finally:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
    for log in logs: log.close()
