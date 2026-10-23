"""Federation invites: join two PortableTAK servers with one code.

Server A makes an invite code (its name, addresses, federation port, the SHA-256 of
its CA certificate, its federation certificate fingerprint and a one-time secret).
Server B's admin pastes it: B fetches A's CA from A's dashboard address, checks it
against the hash in the code, then over a connection verified with that CA sends
the secret plus its own CA and federation certificate. A checks the secret (single
use, 24 hours), trusts B and allows sharing; B trusts A, pins A's certificate and
connects. Invites are stored on the drive as hashes only.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import ssl
import time
import urllib.error
import urllib.request

PREFIX = 'PTFED1.'
LIFETIME = 24 * 3600
STORE = 'portabletak/federation-invites.json'
DASHBOARD_PORT = 8444


class InviteError(ValueError):
    pass


def pem_sha256(pem):
    """SHA-256 of the first certificate in a PEM (DER bytes), hex."""
    body = pem.split('-----BEGIN CERTIFICATE-----', 1)[1].split('-----END CERTIFICATE-----', 1)[0]
    return hashlib.sha256(base64.b64decode(''.join(body.split()))).hexdigest()


def _load(root):
    try:
        data = json.loads((Path(root) / STORE).read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(root, invites):
    path = Path(root) / STORE
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(invites))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def create(root, name, addresses, ca_pem, fingerprint, port=9001, clock=time.time):
    token = secrets.token_urlsafe(18)
    now = clock()
    invites = [i for i in _load(root) if i.get('expires', 0) > now and not i.get('used')]
    invites.append({'hash': hashlib.sha256(token.encode()).hexdigest(), 'expires': now + LIFETIME, 'used': False})
    _save(root, invites[-20:])
    payload = {'v': 1, 'n': name, 'a': list(addresses)[:4], 'p': port, 'c': pem_sha256(ca_pem),
               'f': fingerprint, 't': token}
    code = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    return PREFIX + code, now + LIFETIME


def decode(code):
    code = ''.join(str(code or '').split())
    if not code.startswith(PREFIX):
        raise InviteError('not_an_invite')
    raw = code[len(PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    except (ValueError, TypeError):
        raise InviteError('damaged_invite') from None
    if not isinstance(data, dict) or data.get('v') != 1 or not data.get('t') or not data.get('a'):
        raise InviteError('damaged_invite')
    return data


def redeem(root, token, clock=time.time, use=True):
    """Check (and with use=True, use up) an invite secret. Raises InviteError unless it is
    valid, unused and unexpired."""
    wanted = hashlib.sha256(str(token or '').encode()).hexdigest()
    now = clock()
    invites = _load(root)
    for invite in invites:
        if hmac.compare_digest(invite.get('hash', ''), wanted):
            if invite.get('used'):
                raise InviteError('invite_already_used')
            if invite.get('expires', 0) < now:
                raise InviteError('invite_expired')
            if use:
                invite['used'] = True
                _save(root, invites)
            return True
    raise InviteError('invite_not_found')


def _get(url, context, timeout=8):
    with urllib.request.urlopen(url, context=context, timeout=timeout) as response:
        return response.read().decode()


def _post(url, body, context, timeout=20):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode()).get('detail')
        except ValueError:
            detail = None
        raise InviteError(str(detail or 'invite_refused_%s' % error.code)) from None


def contact(invite, our_name, our_ca, our_cert):
    """B side: reach A, verify it, redeem the invite. Returns (address, A's CA PEM, A's reply)."""
    last = 'server_unreachable'
    for address in invite['a']:
        host = '[%s]' % address if ':' in address else address
        base = 'https://%s:%d/api/federation' % (host, DASHBOARD_PORT)
        try:
            probe = ssl.create_default_context()
            probe.check_hostname = False
            probe.verify_mode = ssl.CERT_NONE        # only to read the CA; checked against the code
            ca_pem = _get(base + '/public-ca', probe)
            if pem_sha256(ca_pem) != invite['c']:
                last = 'certificate_mismatch'        # not the server that made the invite
                continue
            trusted = ssl.create_default_context(cadata=ca_pem)
            trusted.check_hostname = False           # addresses vary; the CA is pinned by the code
            reply = _post(base + '/accept', {'t': invite['t'], 'name': our_name, 'ca': our_ca,
                                             'cert': our_cert}, trusted)
            if not reply.get('ok'):
                raise InviteError(str(reply.get('error') or 'invite_refused'))
            return address, ca_pem, reply
        except InviteError:
            raise
        except (OSError, ValueError, ssl.SSLError, IndexError):
            continue                                 # try the next address
    raise InviteError(last)
