"""Put every address the server answers at into one client package.

OpenTAKServer writes packages with a single connection (the advertised
address). This rewrites the `cot_streams` preference of a generated ATAK/WinTAK
or iTAK package so it lists one connection per server address, in the order
recorded in config/server-addresses.json (advertised first). The certificates,
manifest and every other preference are copied unchanged.

The advertised address and the first VPN (Tailscale) address are enabled, so
the same package works on the node's Wi-Fi and over cellular without touching
the phone. Other addresses (mesh service address, DNS names, extra LANs) are
included but disabled; the user switches them on in the client if needed.

The stored package on the cartridge is never modified; callers serve the bytes.
"""
import io
import ipaddress
import re
from xml.sax.saxutils import escape
import zipfile

from server_addresses import normalize

TAILSCALE = ipaddress.IPv4Network('100.64.0.0/10')
MESH = ipaddress.IPv4Network('10.41.250.0/24')
STREAMS = re.compile(r'(<preference\b[^>]*\bname="cot_streams"[^>]*>)(.*?)(</preference>)', re.S)
PORT = 8089


def _kind(kind, value, first=False):
    if kind == 'DNS':
        return 'Tailscale name' if value.endswith('.ts.net') else 'name ' + value
    address = ipaddress.IPv4Address(value)
    if address in TAILSCALE:
        return 'Tailscale'
    if address in MESH:
        return 'mesh'
    # The advertised address is the one phones normally use; other LANs keep theirs.
    return 'Wi-Fi' if first else 'LAN ' + value


def server_name(name):
    """The server's name for client entries (falls back to PortableTAK)."""
    name = (name or '').strip()
    return name if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}', name) else 'PortableTAK'



def connections(record, only=None, name=None):
    """Return [(description, address, enabled)] for a server-addresses record.
    Descriptions are "<server name> (<how it is reached>)"."""
    addresses = record.get('addresses') or []
    advertise = record.get('advertise')
    if advertise and advertise not in addresses:
        addresses = [advertise, *addresses]
    entries = normalize(addresses)
    if advertise:
        first = normalize([advertise])[0]
        entries = [first] + [e for e in entries if e != first]
    if only is not None:
        wanted = normalize([only])[0]
        if wanted not in entries:
            raise ValueError('address_not_served')
        entries = [wanted]
    result, vpn_enabled = [], False
    name = server_name(name)
    for index, (kind, value) in enumerate(entries):
        label = _kind(kind, value, first=(kind, value) == (normalize([advertise])[0] if advertise else None))
        enabled = index == 0
        if not enabled and label == 'Tailscale' and not vpn_enabled:
            enabled = vpn_enabled = True
        result.append((f'{name} ({label})', value, enabled))
    return result


def _streams_body(conns, indent, closing):
    lines = [f'<entry key="count" class="class java.lang.Integer">{len(conns)}</entry>']
    for i, (description, address, enabled) in enumerate(conns):
        lines += [
            f'<entry key="description{i}" class="class java.lang.String">{escape(description)}</entry>',
            f'<entry key="enabled{i}" class="class java.lang.Boolean">{"true" if enabled else "false"}</entry>',
            f'<entry key="connectString{i}" class="class java.lang.String">{escape(address)}:{PORT}:ssl</entry>',
        ]
    return ''.join('\n' + indent + line for line in lines) + closing


def rewrite_pref(text, conns):
    match = STREAMS.search(text)
    if not match or not conns:
        raise ValueError('unexpected_package_preferences')
    body = match.group(2)
    found = re.search(r'\n([ \t]*)<entry', body)
    indent = found.group(1) if found else '    '
    closing = re.search(r'\s*$', body).group(0)
    return text[:match.start(2)] + _streams_body(conns, indent, closing) + text[match.end(2):]


def _rebuild(data, change):
    """Copy a zip, passing each member's bytes through change(name, bytes)."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    touched = 0
    with zipfile.ZipFile(out, 'w') as target:
        for info in source.infolist():
            content = source.read(info)
            updated = change(info.filename, content)
            if updated is not None:
                content, touched = updated, touched + 1
            target.writestr(info, content)
    if not touched:
        raise ValueError('unexpected_package_layout')
    return out.getvalue()


def _pref(conns):
    return lambda content: rewrite_pref(content.decode('utf-8'), conns).encode('utf-8')


def rewrite_atak(data, conns):
    """ATAK/WinTAK: <user>_CONFIG.zip holding <uid>/<user>.zip holding preference.pref."""
    pref = _pref(conns)

    def inner(name, content):
        if name.endswith('.zip'):
            return _rebuild(content, lambda n, c: pref(c) if n.rsplit('/', 1)[-1] == 'preference.pref' else None)
        return None
    return _rebuild(data, inner)


def rewrite_itak(data, conns):
    """iTAK: <user>_CONFIG_iTAK.zip holding config.pref."""
    pref = _pref(conns)
    return _rebuild(data, lambda n, c: pref(c) if n.rsplit('/', 1)[-1] == 'config.pref' else None)


def build(data, client_type, record, only=None, name=None):
    conns = connections(record, only, name)
    if client_type == 'itak':
        return rewrite_itak(data, conns)
    return rewrite_atak(data, conns)


def remote_address(record):
    """The first VPN (Tailscale) address the server is reachable at, or None."""
    for kind, value in normalize(record.get('addresses') or [record.get('advertise')]):
        if kind == 'DNS' and value.endswith('.ts.net'):
            continue
        if ipaddress.IPv4Address(value) in TAILSCALE:
            return value
    return None


def trust_package(truststore, password, name='PortableTAK'):
    """ATAK data package that only makes ATAK trust this server's CA (no user
    certificate, no connection). Import it once; then QR enrollment works.

    Flat layout, the one Sit(x) and TAK Server use for certificate packages: the
    manifest lists the trust store and a preference file at the top level. (An
    earlier nested layout had no inner manifest, so ATAK could file it away
    without applying the trust.)"""
    import uuid
    from xml.sax.saxutils import escape as _escape
    pref = ("<?xml version='1.0' standalone='yes'?>\n<preferences>\n"
            '<preference version="1" name="com.atakmap.app_preferences">'
            '<entry key="caLocation" class="class java.lang.String">cert/truststore-root.p12</entry>'
            f'<entry key="caPassword" class="class java.lang.String">{_escape(password)}</entry>'
            '</preference>\n</preferences>\n')
    label = re.sub(r'[^A-Za-z0-9_.-]', '_', name)[:40] + '_TRUST'
    manifest = ('<MissionPackageManifest version="2"><Configuration>'
                f'<Parameter name="uid" value="{uuid.uuid4()}"/>'
                f'<Parameter name="name" value="{_escape(label)}"/></Configuration><Contents>'
                '<Content ignore="false" zipEntry="truststore-root.p12"/>'
                '<Content ignore="false" zipEntry="trust.pref"/></Contents></MissionPackageManifest>\n')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('truststore-root.p12', truststore)
        z.writestr('trust.pref', pref)
        z.writestr('MANIFEST/manifest.xml', manifest)
    return out.getvalue()


def tls_package(data, record, name, username, uid=None):
    """A device TLS package in the layout Sit(x) uses for drones and radios (e.g. the
    Skydio X10D Flight Deck): one flat ATAK data package holding the trust store, the
    device's client certificate, secure.pref (connections, certificate locations and
    passwords) and the manifest, plus a README with the values to type in.

    Built from the user's server-generated ATAK package (<user>_CONFIG.zip)."""
    import uuid
    outer = zipfile.ZipFile(io.BytesIO(data))
    inner_name = next((n for n in outer.namelist() if n.endswith('.zip')), None)
    if inner_name is None:
        raise ValueError('unexpected_package_layout')
    inner = zipfile.ZipFile(io.BytesIO(outer.read(inner_name)))
    files = {n.rsplit('/', 1)[-1]: n for n in inner.namelist()}
    pref_name = files.get('preference.pref')
    trust_name = next((files[n] for n in files if n.endswith('.p12') and 'truststore' in n), None)
    client_name = files.get(username + '.p12') or next(
        (files[n] for n in files if n.endswith('.p12') and 'truststore' not in n), None)
    if not (pref_name and trust_name and client_name):
        raise ValueError('unexpected_package_layout')
    pref = inner.read(pref_name).decode('utf-8')

    def value(key):
        found = re.search(r'key="%s"[^>]*>([^<]*)<' % key, pref)
        return found.group(1) if found else ''
    ca_password, client_password = value('caPassword'), value('clientPassword')
    name = server_name(name)
    conns = [c for c in connections(record, name=name) if c[2]]
    trust_file, client_file = 'truststore-root.p12', username + '.p12'
    streams = ''.join(
        f'<entry key="description{i}" class="class java.lang.String">{escape(d)}</entry>'
        f'<entry key="enabled{i}" class="class java.lang.Boolean">true</entry>'
        f'<entry key="connectString{i}" class="class java.lang.String">{escape(a)}:{PORT}:ssl</entry>'
        for i, (d, a, _) in enumerate(conns))
    secure = ("<?xml version='1.0' standalone='yes'?>\n<preferences>\n"
              '<preference version="1" name="cot_streams">'
              f'<entry key="count" class="class java.lang.Integer">{len(conns)}</entry>{streams}</preference>\n'
              '<preference version="1" name="com.atakmap.app_preferences">'
              f'<entry key="clientPassword" class="class java.lang.String">{client_password}</entry>'
              f'<entry key="caPassword" class="class java.lang.String">{ca_password}</entry>'
              f'<entry key="caLocation" class="class java.lang.String">cert/{trust_file}</entry>'
              f'<entry key="certificateLocation" class="class java.lang.String">cert/{client_file}</entry>'
              '<entry key="displayServerConnectionWidget" class="class java.lang.Boolean">true</entry>'
              '</preference>\n</preferences>\n')
    manifest = ('<MissionPackageManifest version="2"><Configuration>'
                f'<Parameter name="uid" value="{uid or uuid.uuid4()}"/>'
                f'<Parameter name="name" value="{escape(username)} ({escape(name)})"/></Configuration><Contents>'
                f'<Content ignore="false" zipEntry="{trust_file}"/>'
                f'<Content ignore="false" zipEntry="{escape(client_file)}"/>'
                '<Content ignore="false" zipEntry="secure.pref"/></Contents></MissionPackageManifest>\n')
    lines = [f'PortableTAK device TLS package for "{username}" ({name})',
             'Keep this file private: it contains the certificate passwords.', '',
             'Skydio X10D: Global Settings > Sharing > TAK/CoT > TAK Connections > Add a Connection',
             f'  Server name:  {re.sub(r"[^A-Za-z0-9]", "", name) or "PortableTAK"}  (one word)']
    for d, a, _ in conns:
        lines.append(f'  Server IP:    {a}   ({d})')
    lines += ['  Drone on a phone hotspot (server elsewhere): use the address printed by the phone',
              '  bridge instead (dashboard > Video Feeds > Drone far from this server).',
              f'  Server port:  {PORT}', '  Advanced Options > SSL/TLS:',
              f'    Trust Store:        {trust_file}   password: {ca_password}',
              f'    Client certificate: {client_file}   password: {client_password}', '',
              'Other ATAK devices: import this zip in ATAK (Import > Local SD).']
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        target.writestr(trust_file, inner.read(trust_name))
        target.writestr(client_file, inner.read(client_name))
        target.writestr('secure.pref', secure)
        target.writestr('MANIFEST/manifest.xml', manifest)
        target.writestr('README.txt', '\n'.join(lines) + '\n')
    return out.getvalue()
