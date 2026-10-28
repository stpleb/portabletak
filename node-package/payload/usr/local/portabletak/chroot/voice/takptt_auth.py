#!/usr/bin/env python3
"""Sign-in check for the PortableTAK voice server (Murmur).

Murmur asks this script about every user who connects (over Ice, on localhost). A user is let in when:

- they connect with a client certificate issued by the PortableTAK certificate authority (the
  certificate ATAK enrolled with the TAK server). Their voice ID is the certificate's user name, and
  the name others see is the callsign their phone sends.
- or, for apps that cannot send the TAK certificate (such as Vx), they give a PortableTAK user name
  and password, checked with the TAK server's own login (only with --login-url).

The voice history recorder (takptt_recorder.py) signs in with its own certificate, whose SHA-1
fingerprint it writes to the --recorder file.
The OpenMANET talk group bridge (takptt_bridge.py) does the same with --bridge, and may only use the
names "OpenMANET CH 1" to "OpenMANET CH 5" (nobody else may).

With --db-config (OpenTAKServer's config.yml), the user must also still have an active PortableTAK
account, so deleting or turning off a user also locks them out of voice.

Everyone else is turned away. At start it also lets every signed-in user make channels in Root.

    takptt_auth.py --ca ca.pem --ids ids.json [--crl ca.crl] [--ice 127.0.0.2:6502]
                   [--secret-file secret] [--db-config config.yml]
                   [--login-url http://127.0.0.2:18081/api/login]
"""
import argparse
import base64
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gen'))
import Ice  # noqa: E402
import MumbleServer  # noqa: E402

log = logging.getLogger('takptt-auth')

FIRST_ID = 100000          # well above Murmur's own user IDs
CACHE_SECONDS = 60         # a certificate checked this recently is not checked again
RESERVED = {'superuser', 'portabletak recorder'} | {'openmanet ch %d' % g for g in range(1, 6)}
RECORDER_ID = 99999
RECORDER_NAME = 'PortableTAK Recorder'
BRIDGE_ID = 99900                 # + talk group: the OpenMANET bridge's users (99901 to 99905)
GROUP = 'tak'              # every signed-in user is in this group on Root
CHANNEL_RIGHTS = MumbleServer.PermissionMakeChannel | MumbleServer.PermissionMakeTempChannel


def der_to_pem(der):
    b = base64.encodebytes(der).decode().replace('\n', '')
    lines = [b[i:i + 64] for i in range(0, len(b), 64)]
    return '-----BEGIN CERTIFICATE-----\n' + '\n'.join(lines) + '\n-----END CERTIFICATE-----\n'


def common_name(pem_path):
    out = subprocess.run(['openssl', 'x509', '-noout', '-subject', '-nameopt', 'multiline,utf8,-esc_msb',
                          '-in', pem_path], capture_output=True, text=True, timeout=10).stdout
    for line in out.splitlines():
        key, _, value = line.strip().partition('=')
        if key.strip() == 'commonName':
            return value.strip()
    return None


def check_certificate(certificates, ca, crl=None):
    """The user name in a certificate chain issued by the CA, or None."""
    if not certificates:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        leaf = os.path.join(tmp, 'leaf.pem')
        with open(leaf, 'w') as f:
            f.write(der_to_pem(bytes(certificates[0])))
        cmd = ['openssl', 'verify', '-CAfile', ca, '-purpose', 'sslclient']
        if len(certificates) > 1:
            chain = os.path.join(tmp, 'chain.pem')
            with open(chain, 'w') as f:
                f.write(''.join(der_to_pem(bytes(c)) for c in certificates[1:]))
            cmd += ['-untrusted', chain]
        if crl and os.path.exists(crl):
            cmd += ['-crl_check', '-CRLfile', crl]
        result = subprocess.run(cmd + [leaf], capture_output=True, text=True, timeout=10)
        if result.returncode != 0 or not result.stdout.strip().endswith(': OK'):
            log.info('certificate refused: %s', (result.stdout + result.stderr).strip().splitlines()[-1:])
            return None
        return common_name(leaf)


def check_password(url, name, password):
    """True when OpenTAKServer's login (POST /api/login) accepts this user name and password."""
    if not url or not name or not password:
        return False
    body = json.dumps({'username': name, 'password': password}).encode()
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status == 200 and 'user' in json.loads(r.read()).get('response', {})
    except Exception:
        return False


class Accounts:
    """Checks a user still has an active account in OpenTAKServer's database."""

    def __init__(self, config_path):
        self.config_path = config_path

    def active(self, user):
        """True or False, or None when the database could not be asked."""
        if not self.config_path:
            return True
        try:
            import psycopg
            import yaml
            with open(self.config_path) as f:
                uri = yaml.safe_load(f)['SQLALCHEMY_DATABASE_URI'].replace('postgresql+psycopg://', 'postgresql://')
            with psycopg.connect(uri, connect_timeout=3) as db:
                row = db.execute('SELECT active FROM "user" WHERE lower(username) = lower(%s)', (user,)).fetchone()
            return bool(row and row[0])
        except Exception as e:
            log.warning('could not check account %r: %s', user, e)
            return None


class Ids:
    """A lasting voice ID for each TAK user name (case-insensitive), saved in a JSON file."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {'ids': {}, 'names': {}}

    def get(self, user, shown_name):
        key = user.lower()
        with self.lock:
            ids = self.data['ids']
            if key not in ids:
                ids[key] = max(ids.values(), default=FIRST_ID - 1) + 1
            uid = ids[key]
            if self.data['names'].get(str(uid)) != shown_name:
                self.data['names'][str(uid)] = shown_name
                tmp = self.path + '.tmp'
                with open(tmp, 'w') as f:
                    json.dump(self.data, f)
                os.replace(tmp, self.path)
            return uid

    def ids_for(self, user):
        """This TAK user's voice id, or None if they have not signed in before."""
        return self.data['ids'].get(user.lower())

    def name(self, uid):
        return self.data['names'].get(str(uid), '')

    def owner(self, shown_name):
        """The voice id that last used this name, or None."""
        for uid, n in self.data['names'].items():
            if n.lower() == shown_name.lower():
                return int(uid)
        return None

    def by_name(self, shown_name):
        for uid, n in self.data['names'].items():
            if n.lower() == shown_name.lower():
                return int(uid)
        return -2


class Authenticator(MumbleServer.ServerAuthenticator):
    def __init__(self, args, ids, accounts):
        self.args = args
        self.ids = ids
        self.accounts = accounts
        self.cache = {}            # certificate hash -> (user, checked at)

    def authenticate(self, name, pw, certificates, certhash, certstrong, current=None):
        if name == 'SuperUser':
            return -2, None, None
        if self.args.recorder and certhash:
            try:
                with open(self.args.recorder) as f:
                    if f.read().strip().lower() == certhash.lower():
                        log.info('voice history recorder signed in')
                        return RECORDER_ID, RECORDER_NAME, ['recorder']
            except OSError:
                pass
        if self.args.bridge and certhash:
            try:
                with open(self.args.bridge) as f:
                    if f.read().strip().lower() == certhash.lower():
                        m = re.fullmatch(r'OpenMANET CH ([1-5])', name.strip())
                        if not m:
                            log.info('refused the bridge as %r', name)
                            return -1, None, None
                        log.info('OpenMANET bridge signed in for talk group %s', m.group(1))
                        return BRIDGE_ID + int(m.group(1)), 'OpenMANET CH ' + m.group(1), ['bridge']
            except OSError:
                pass
        shown = ''.join(ch for ch in name.strip() if ch.isprintable())[:64]
        if shown.lower() in RESERVED:
            log.info('refused the reserved name %r', shown)
            return -1, None, None
        hit = self.cache.get(certhash) if certhash else None
        if hit and time.time() - hit[1] < CACHE_SECONDS:
            user = hit[0]
        else:
            user = check_certificate(certificates, self.args.ca, self.args.crl)
            if user and certhash:
                if len(self.cache) > 1000:
                    self.cache.clear()
                self.cache[certhash] = (user, time.time())
        how = 'certificate'
        if user is None and check_password(self.args.login_url, shown, pw):
            user, how = shown, 'password'
        if user is None:
            log.info('refused %r', shown)
            return -1, None, None
        active = self.accounts.active(user)
        if active is None:
            return -3, None, None
        if not active:
            log.info('refused %r: no active PortableTAK account %r', shown, user)
            return -1, None, None
        if not shown:
            shown = user
        # A callsign another TAK user already uses gets this user's name added, so nobody can
        # take over (or bump off) someone else's name.
        mine = self.ids.ids_for(user)
        owner = self.ids.owner(shown)
        if owner is not None and owner != mine:
            shown = '%s (%s)' % (shown, user)
        uid = self.ids.get(user, shown)
        log.info('signed in %r as %r (%s, id %d)', user, shown, how, uid)
        return uid, shown, [GROUP]

    def getInfo(self, id, current=None):
        return False, {}

    def nameToId(self, name, current=None):
        return self.ids.by_name(name)

    def idToName(self, id, current=None):
        return self.ids.name(id)

    def idToTexture(self, id, current=None):
        return []


def allow_channels(server):
    """Let every signed-in user make channels in Root (Murmur's default is registered users only)."""
    acls, groups, inherit = server.getACL(0)
    for a in acls:
        if a.group == 'all' and a.applyHere and not a.inherited and a.allow & CHANNEL_RIGHTS == CHANNEL_RIGHTS:
            return
    acls = [a for a in acls if not a.inherited]
    acls.append(MumbleServer.ACL(applyHere=True, applySubs=False, inherited=False, userid=-1, group='all',
                                 allow=CHANNEL_RIGHTS, deny=0))
    server.setACL(0, acls, [g for g in groups if not g.inherited], inherit)
    log.info('every user may now make channels in Root')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ca', required=True)
    p.add_argument('--crl')
    p.add_argument('--ids', required=True)
    p.add_argument('--ice', default='127.0.0.2:6502')
    p.add_argument('--secret-file')
    p.add_argument('--login-url')
    p.add_argument('--db-config')
    p.add_argument('--recorder', help="file with the voice history recorder's certificate SHA-1")
    p.add_argument('--bridge', help="file with the OpenMANET bridge's certificate SHA-1")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')

    props = Ice.createProperties()
    props.setProperty('Ice.ImplicitContext', 'Shared')
    props.setProperty('Ice.Default.EncodingVersion', '1.0')
    init = Ice.InitializationData()
    init.properties = props
    ice = Ice.initialize(init)
    if args.secret_file:
        with open(args.secret_file) as f:
            ice.getImplicitContext().put('secret', f.read().strip())
    host, port = args.ice.rsplit(':', 1)
    adapter = ice.createObjectAdapterWithEndpoints('Auth', 'tcp -h 127.0.0.2')
    adapter.activate()
    proxy = MumbleServer.ServerAuthenticatorPrx.uncheckedCast(
        adapter.addWithUUID(Authenticator(args, Ids(args.ids), Accounts(args.db_config))))

    attached = None
    uptime = -1
    while True:
        # Attach again whenever Murmur has restarted (its uptime went back).
        try:
            meta = MumbleServer.MetaPrx.checkedCast(ice.stringToProxy('Meta:tcp -h %s -p %s -t 5000' % (host, port)))
            now = meta.getUptime()
            if not attached or now < uptime:
                server = meta.getServer(1)
                server.setAuthenticator(proxy)
                allow_channels(server)
                log.info('attached to Murmur')
            uptime = now
            attached = True
        except Exception as e:
            if attached is not False:
                log.warning('Murmur not reachable yet: %s', e)
            attached = False
        time.sleep(10 if attached else 3)

if __name__ == '__main__':
    main()
