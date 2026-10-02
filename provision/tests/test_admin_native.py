"""Native migration/admin test against an isolated socket-only PostgreSQL cluster.

Run as the postgres OS user in the chroot. No live config is read. Only the
cluster created inside this test's TemporaryDirectory is started/stopped.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from bootstrap_admin import initialize_administrator

PG = Path('/usr/lib/postgresql/16/bin')
assert os.geteuid() != 0, 'Run this test as the postgres OS user'
@contextlib.contextmanager
def test_directory():
    directory = tempfile.mkdtemp(prefix='portabletak-admin-test-')
    try:
        yield directory
    finally:
        # Preserve a cluster if shutdown failed, rather than deleting live files.
        if not (Path(directory) / 'cluster/postmaster.pid').exists():
            shutil.rmtree(directory)

with test_directory() as directory:
    root = Path(directory)
    cluster = root / 'cluster'
    socket = root / 'socket'
    socket.mkdir(mode=0o700)
    data = root / 'ots-data'
    data.mkdir(mode=0o700)
    environment = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    subprocess.run([str(PG / 'initdb'), '-D', str(cluster), '--auth-local=trust', '--auth-host=reject'],
                   env=environment, capture_output=True, check=True, timeout=30)
    started = False
    try:
        subprocess.run([str(PG / 'pg_ctl'), '-D', str(cluster), '-l', str(root / 'postgres.log'),
                        '-o', "-c listen_addresses='' -k " + str(socket) + ' -p 15432',
                        '-w', '-t', '20', 'start'], env=environment, capture_output=True, check=True, timeout=25)
        started = True
        subprocess.run([str(PG / 'createdb'), '-h', str(socket), '-p', '15432', '-U', 'postgres', 'ots_test'],
                       env=environment, capture_output=True, check=True, timeout=20)
        os.environ['OTS_DATA_FOLDER'] = str(data)
        uri = 'postgresql+psycopg://postgres@/ots_test?host=' + str(socket) + '&port=15432'
        config = {'OTS_DATA_FOLDER': str(data), 'SQLALCHEMY_DATABASE_URI': uri,
                  'SECRET_KEY': secrets.token_hex(32), 'SECURITY_PASSWORD_SALT': secrets.token_hex(32),
                  'OTS_CA_FOLDER': str(data / 'ca'), 'OTS_ENABLE_PLUGINS': False,
                  'OTS_ENABLE_LDAP': False, 'OTS_ENABLE_EMAIL': False}
        (data / 'config.yml').write_text(json.dumps(config))
        os.chmod(data / 'config.yml', 0o600)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            from opentakserver.defaultconfig import DefaultConfig
            assert DefaultConfig.OTS_DATA_FOLDER == str(data)
            from opentakserver.app import create_app
            from opentakserver.extensions import db
            import opentakserver
            from flask_migrate import upgrade
            from flask_security import verify_password
            from sqlalchemy import text
            app = create_app(cli=True)
            assert app.config['SQLALCHEMY_DATABASE_URI'] == uri
            with app.app_context():
                assert db.session.execute(text('SHOW data_directory')).scalar() == str(cluster)
                assert db.session.execute(text('SHOW listen_addresses')).scalar() == ''
                upgrade(directory=str(Path(opentakserver.__file__).parent / 'migrations'))
            password = 'Test-only-' + secrets.token_hex(20)
            initialize_administrator(app, password)
            with app.app_context():
                user = app.security.datastore.find_user(username='administrator')
                assert verify_password(password, user.password)
                assert not verify_password('password', user.password)
                assert [role.name for role in user.roles] == ['administrator']
                assert db.session.query(app.security.datastore.user_model).count() == 1
                stored_hash = user.password
            try:
                initialize_administrator(app, 'different-password-12345')
            except ValueError as error:
                assert str(error) == 'database_not_empty'
            else:
                raise AssertionError('existing administrator was not protected')
            with app.app_context():
                assert app.security.datastore.find_user(username='administrator').password == stored_hash
                db.session.remove()
                db.engine.dispose()
        print('PASS: PostgreSQL native migrations, chosen administrator password, default rejected, existing account protected')
    finally:
        if started or (cluster / 'postmaster.pid').exists():
            subprocess.run([str(PG / 'pg_ctl'), '-D', str(cluster), '-m', 'fast', '-w', '-t', '20', 'stop'],
                           env=environment, capture_output=True, check=True, timeout=25)
