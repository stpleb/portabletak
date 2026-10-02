"""Start the upstream OpenTAKServer web app with case-insensitive usernames and
an enrollment profile that also carries the Tailscale connection (one QR code).

Phone keyboards capitalize the first letter, so "Qrtest" and "qrtest" must be
the same user when enrolling a certificate (QR setup) or signing in. Flask-
Security supports case-insensitive lookups; OpenTAKServer just never asks for
them. This turns them on for every username lookup. The sealed package is not
modified.

Usage (inside the cartridge chroot):
    python opentakserver_launch.py /asgard/opentak/venv/bin/opentakserver
"""
import sys


def install(datastore_class):
    original = datastore_class.find_user
    if getattr(original, '_portabletak_case_insensitive', False):
        return False

    def find_user(self, case_insensitive=False, **kwargs):
        if kwargs.get('username'):
            case_insensitive = True
        return original(self, case_insensitive=case_insensitive, **kwargs)

    find_user._portabletak_case_insensitive = True
    datastore_class.find_user = find_user
    return True


# ---------------------------------------------------------------- one QR, two connections
# ATAK's QR code carries a single address (the local one). Right after enrolling,
# ATAK downloads its enrollment profile from the server. When remote access is set
# up, PortableTAK adds this user's ATAK package with only the Tailscale connection
# to that profile, so one QR scan leaves the phone with both connections: local
# Wi-Fi/mesh and Tailscale (cellular), the same as importing the ATAK Package.
import io
import ipaddress
import json
import os
import re
import zipfile

TAILSCALE = ipaddress.IPv4Network('100.64.0.0/10')
STREAMS = re.compile(r'(<preference\b[^>]*\bname="cot_streams"[^>]*>)(.*?)(</preference>)', re.S)
FOLDER = '5c2bfcae3d98c9f4d262172df99ebac5'


CLIENT_INFO = '/asgard/opentak/data/portabletak-clients.json'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}')


def client_info(path=CLIENT_INFO):
    """Written by PortableTAK at every start: server name and Tailscale address."""
    try:
        with open(path) as stream:
            info = json.load(stream)
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


def remote_address(path=CLIENT_INFO):
    value = client_info(path).get('remote')
    try:
        return value if value and ipaddress.IPv4Address(value) in TAILSCALE else None
    except ValueError:
        return None


def server_name(path=CLIENT_INFO):
    """The PortableTAK server's name (set at first setup), for client entries."""
    name = str(client_info(path).get('name') or '')
    return name if NAME.fullmatch(name) else 'PortableTAK'


def _rezip(data, change):
    """Copy a zip, passing each member through change(name, bytes) -> bytes or None."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            content = source.read(info)
            updated = change(info.filename, content)
            target.writestr(info, content if updated is None else updated)
    return out.getvalue()


def tailscale_only(package, address, name='PortableTAK'):
    """The user's ATAK package (<user>_CONFIG.zip) with one connection: Tailscale."""
    body = ('<entry key="count" class="class java.lang.Integer">1</entry>'
            '<entry key="description0" class="class java.lang.String">%s (Tailscale)</entry>'
            '<entry key="enabled0" class="class java.lang.Boolean">true</entry>'
            '<entry key="connectString0" class="class java.lang.String">%s:8089:ssl</entry>' % (name, address))
    changed = []

    def pref(name, content):
        if name.rsplit('/', 1)[-1] != 'preference.pref':
            return None
        text, count = STREAMS.subn(lambda m: m.group(1) + body + m.group(3), content.decode('utf-8'))
        if count != 1:
            raise ValueError('unexpected_package_preferences')
        changed.append(name)
        return text.encode('utf-8')

    result = _rezip(package, lambda n, c: _rezip(c, pref) if n.endswith('.zip') else None)
    if not changed:
        raise ValueError('unexpected_package_layout')
    return result


def add_to_profile(profile, name, data):
    """Add a data package to an enrollment profile (a mission package) and list it."""
    entry = FOLDER + '/' + name

    def manifest(member, content):
        if member != 'MANIFEST/manifest.xml':
            return None
        text = content.decode('utf-8')
        line = '<Content ignore="false" zipEntry="%s" />' % entry
        return text.replace('</Contents>', line + '</Contents>', 1).encode('utf-8')

    result = _rezip(profile, manifest)
    out = io.BytesIO(result)
    with zipfile.ZipFile(out, 'a', zipfile.ZIP_DEFLATED) as target:
        target.writestr(entry, data)
    return out.getvalue()


def user_package(app, username):
    """This user's server-generated ATAK package, generating it on first use."""
    folder = os.path.join(app.config.get('OTS_CA_FOLDER'), 'certs', username)
    path = os.path.join(folder, username + '_CONFIG.zip')
    if not os.path.isfile(path):
        from opentakserver.certificate_authority import CertificateAuthority
        from opentakserver.extensions import logger
        CertificateAuthority(logger, app).issue_certificate(username, False)
    with open(path, 'rb') as stream:
        return stream.read()


def profile_user(module, app):
    """Who is asking for the enrollment profile: the client certificate if ATAK sent
    one, otherwise ATAK's sign-in (user name + password, or the QR code's token, which
    must belong to that same user). A token is checked without using one of its uses."""
    cert = module.verify_client_cert()
    if cert:
        return app.security.datastore.find_user(username=cert.get_subject().commonName)
    header = module.request.headers.get('Authorization') or ''
    if not header.lower().startswith('basic '):
        return None
    import base64
    try:
        username, password = base64.b64decode(header.split(' ', 1)[1]).decode('utf-8').split(':', 1)
    except (ValueError, UnicodeDecodeError):
        return None
    user = app.security.datastore.find_user(username=username)
    if not user:
        return None
    from flask_security import verify_password
    if verify_password(password, user.password):
        return user
    import hashlib
    import jwt
    from opentakserver.models.Token import Token
    with open(os.path.join(app.config.get('OTS_CA_FOLDER'), 'certs', 'opentakserver', 'opentakserver.pub')) as key:
        try:
            claims = jwt.decode(password, key.read(), algorithms=['RS256'], audience='OpenTAKServer')
        except jwt.exceptions.InvalidTokenError:
            return None
    if str(claims.get('sub', '')).lower() != user.username.lower():
        return None
    digest = hashlib.sha256(json.dumps(claims).encode()).hexdigest()
    stored = module.db.session.query(Token).filter_by(token_hash=digest).first()
    if not stored or stored.disabled:
        return None
    return user


def install_profile(module):
    original = module.create_profile_zip
    if getattr(original, '_portabletak_remote', False):
        return False

    def create_profile_zip(enrollment=True, *args, **kwargs):
        buffer = original(enrollment, *args, **kwargs)
        if not enrollment:
            return buffer
        try:
            header = module.request.headers.get('Authorization') or ''
            module.logger.info('PortableTAK: enrollment profile requested (%s, client cert %s, auth %s)',
                               module.request.path,
                               'yes' if module.request.headers.get(module.app.config.get('OTS_SSL_CERT_HEADER', 'X-Ssl-Cert')) else 'no',
                               header.split(' ', 1)[0] or 'none')
            address = remote_address()
            if not address:
                module.logger.info('PortableTAK: no Tailscale address on the server; profile unchanged')
                return buffer
            app = module.app
            user = profile_user(module, app)
            if not user:
                module.logger.warning('PortableTAK: enrollment profile without a known user; no Tailscale connection added')
                return buffer
            package = tailscale_only(user_package(app, user.username), address, server_name())
            profile = add_to_profile(buffer.getvalue(), user.username + '_Tailscale.zip', package)
            module.logger.info('PortableTAK: enrollment profile includes the Tailscale connection')
            return io.BytesIO(profile)
        except Exception as error:   # never break enrollment over the extra connection
            module.logger.warning('PortableTAK: Tailscale connection not added to profile: %s', error)
            return buffer

    create_profile_zip._portabletak_remote = True
    module.create_profile_zip = create_profile_zip
    return True


def main():
    if len(sys.argv) < 2:
        print('usage: opentakserver_launch.py ENTRY_POINT [args...]', file=sys.stderr)
        return 2
    from flask_security import SQLAlchemyUserDatastore
    install(SQLAlchemyUserDatastore)
    sys.argv = sys.argv[1:]
    # opentakserver.app monkey-patches with gevent on import, so it must be imported
    # before any other OpenTAKServer module; the profile hook goes in after create_app.
    import opentakserver.app as ots_app
    original_create_app = ots_app.create_app

    def create_app(*args, **kwargs):
        app = original_create_app(*args, **kwargs)
        # OpenTAKServer lets 127.0.0.1 skip the video sign-in (for its YouTube re-streaming,
        # unused here). The drive's Tailscale delivers every remote connection from
        # 127.0.0.1, so keep the sign-in for everyone; drone feeds are opened explicitly.
        app.config['OTS_IP_WHITELIST'] = []
        try:
            from opentakserver.blueprints.marti_api import device_profile_marti_api
            install_profile(device_profile_marti_api)
            device_profile_marti_api.logger.info('PortableTAK: enrollment profile hook installed')
        except Exception as error:   # the server must start even if the hook cannot
            print('PortableTAK: enrollment profile hook not installed: %s' % error, file=sys.stderr)
        return app

    ots_app.create_app = create_app
    return ots_app.start()


if __name__ == '__main__':
    sys.exit(main())
