"""Data packages for the PortableTAK dashboard, run inside the OpenTAKServer app context
by users_worker.

Packages are OpenTAKServer's own data packages (the ones phones send and share), kept
in its upload folder as <sha256>.zip. The dashboard can list them, add one (a ready
ATAK data package as it is, any other file wrapped in a data package with a manifest),
send one to phones, and delete one. Enrollment packages (they hold certificates) are
never listed.

Sending works like a phone sending a file: each chosen phone gets a file-share CoT on
OpenTAKServer's direct-message exchange and downloads the package from this server.
The download address is a placeholder that PortableTAK's EUD handler fills in per
phone with the address that phone uses (Wi-Fi, mesh or Tailscale).
"""
import datetime
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from xml.sax.saxutils import quoteattr

FILE_HOST = 'PORTABLETAK-FEDERATION-HOST'   # filled in per phone (eud_handler_launch.py)
SERVER_CALLSIGN = 'PortableTAK'
MAX_NAME = 120


def _fail(error):
    return {'ok': False, 'error': error}


def _clean_name(name):
    """A safe file name: letters, digits, space . _ - ( ), at most 120 characters."""
    base = os.path.basename(str(name or '').replace('\\', '/'))
    base = re.sub(r'[^A-Za-z0-9 ._()-]+', '_', base).strip(' .')
    return base[:MAX_NAME] or 'file'


def _is_data_package(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return any(n.lower().endswith('manifest.xml') and 'manifest' in n.lower() for n in z.namelist())
    except zipfile.BadZipFile:
        return False


def wrap(data, name):
    """Any file as an ATAK data package: the file in a folder plus MANIFEST/manifest.xml
    (KML/KMZ marked as map overlays, as OpenTAKServer and ATAK do)."""
    folder = hashlib.md5(data).hexdigest()
    entry = '%s/%s' % (folder, name)
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    params = ''
    if ext in ('kml', 'kmz'):
        params = ('<Parameter name="name" value=%s/><Parameter name="contentType" value="KML"/>'
                  '<Parameter name="visible" value="true"/>') % quoteattr(name)
    manifest = ('<MissionPackageManifest version="2"><Configuration>'
                '<Parameter name="uid" value="%s"/><Parameter name="name" value=%s/></Configuration>'
                '<Contents><Content ignore="false" zipEntry=%s>%s</Content></Contents>'
                '</MissionPackageManifest>') % (uuid.uuid4(), quoteattr(name), quoteattr(entry), params)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(entry, data)
        z.writestr('MANIFEST/manifest.xml', manifest)
    return out.getvalue()


def fileshare_cot(node_uid, row, recipient, now=None):
    """The file-share CoT one phone receives for a package (as ATAK sends files)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = lambda t: t.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (t.microsecond // 1000)
    name = row.filename[:-4] if row.filename.lower().endswith('.zip') else row.filename
    url = 'https://%s:8443/Marti/sync/content?hash=%s' % (FILE_HOST, row.hash)
    return ('<event version="2.0" uid=%s type="b-f-t-r" how="h-e" time="%s" start="%s" stale="%s">'
            '<point lat="0.0" lon="0.0" hae="9999999.0" ce="9999999.0" le="9999999.0"/><detail>'
            '<fileshare filename=%s senderUrl=%s sizeInBytes="%d" sha256="%s" senderUid=%s '
            'senderCallsign="%s" name=%s/>'
            '<ackrequest uid=%s ackrequested="true" tag=%s/>'
            '<marti><dest uid=%s/></marti></detail></event>') % (
        quoteattr(str(uuid.uuid4())), stamp(now), stamp(now), stamp(now + datetime.timedelta(minutes=10)),
        quoteattr(row.filename), quoteattr(url), int(row.size or 0), row.hash, quoteattr(node_uid),
        SERVER_CALLSIGN, quoteattr(name), quoteattr(str(uuid.uuid4())), quoteattr(name), quoteattr(recipient))


def _upload_path(app, sha256):
    return os.path.join(app.config.get('UPLOAD_FOLDER'), sha256 + '.zip')


def _rows(db, DataPackage):
    return [r for r in db.session.query(DataPackage).order_by(DataPackage.submission_time.desc()).all()
            if r.certificate is None]


def _json(row):
    sent = row.submission_time
    return {'hash': row.hash, 'filename': row.filename, 'size': int(row.size or 0),
            'time': sent.replace(tzinfo=datetime.timezone.utc).isoformat() if sent else None,
            'from': (row.eud.callsign if row.eud else None) or (row.user.username if row.user else None)}


def _publish(app, bodies):
    import pika
    credentials = pika.PlainCredentials(app.config.get('OTS_RABBITMQ_USERNAME', 'guest'),
                                        app.config.get('OTS_RABBITMQ_PASSWORD', 'guest'))
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host=app.config.get('OTS_RABBITMQ_SERVER_ADDRESS', '127.0.0.2'), credentials=credentials))
    try:
        channel = connection.channel()
        for routing_key, body in bodies:
            channel.basic_publish(exchange='dms', routing_key=routing_key, body=body,
                                  properties=pika.BasicProperties(expiration=app.config.get('OTS_RABBITMQ_TTL')))
    finally:
        connection.close()


def handle(app, db, request):
    from opentakserver.models.DataPackage import DataPackage
    from opentakserver.models.EUD import EUD
    action = request.get('action', '')

    if action == 'dp_list':
        phones = [{'uid': e.uid, 'callsign': e.callsign, 'online': e.last_status == 'Connected',
                   'user': e.user.username if e.user else None}
                  for e in db.session.query(EUD).order_by(EUD.callsign).all()
                  if e.callsign and (e.platform or '') != 'Meshtastic']
        return {'ok': True, 'packages': [_json(r) for r in _rows(db, DataPackage)], 'phones': phones}

    if action == 'dp_add':
        staged = str(request.get('staged') or '')
        if not re.fullmatch(r'/asgard/opentak/data/packages-upload/[0-9a-f]{16}\.bin', staged):
            return _fail('invalid_upload')
        with open(staged, 'rb') as stream:
            data = stream.read()
        if not data:
            return _fail('empty_file')
        name = _clean_name(request.get('filename'))
        if not _is_data_package(data):
            data = wrap(data, name)
        stored_name = name if name.lower().endswith('.zip') else (name.rsplit('.', 1)[0] if '.' in name else name) + '.zip'
        sha256 = hashlib.sha256(data).hexdigest()
        row = db.session.query(DataPackage).filter_by(hash=sha256).first()
        if row:
            return {'ok': True, 'package': _json(row), 'existing': True}
        if db.session.query(DataPackage).filter_by(filename=stored_name).first():
            stored_name = stored_name[:-4] + '-' + sha256[:6] + '.zip'   # file names are unique
        path = _upload_path(app, sha256)
        with open(path, 'wb') as stream:
            stream.write(data)
        row = DataPackage()
        row.filename, row.hash, row.mime_type, row.size = stored_name, sha256, 'application/zip', len(data)
        row.submission_time = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        admin = app.security.datastore.find_user(username='administrator')
        row.submission_user = admin.id if admin else None
        db.session.add(row)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            os.remove(path)
            raise
        return {'ok': True, 'package': _json(row)}

    if action == 'dp_send':
        row = db.session.query(DataPackage).filter_by(hash=str(request.get('hash') or '')).first()
        if row is None or row.certificate is not None:
            return _fail('not_found')
        uids = [str(u) for u in (request.get('uids') or []) if isinstance(u, str)]
        phones = {e.uid: e for e in db.session.query(EUD).filter(EUD.uid.in_(uids)).all()} if uids else {}
        if not phones:
            return _fail('choose_phones')
        node = app.config.get('OTS_NODE_ID') or 'PortableTAK'
        bodies = [(uid, json.dumps({'uid': node, 'cot': fileshare_cot(node, row, uid)})) for uid in phones]
        _publish(app, bodies)
        return {'ok': True, 'sent': [{'callsign': e.callsign, 'online': e.last_status == 'Connected'} for e in phones.values()]}

    if action == 'dp_delete':
        row = db.session.query(DataPackage).filter_by(hash=str(request.get('hash') or '')).first()
        if row is None or row.certificate is not None:
            return _fail('not_found')
        sha256 = row.hash
        db.session.delete(row)
        db.session.commit()
        try:
            os.remove(_upload_path(app, sha256))
        except OSError:
            pass
        return {'ok': True}

    if action == 'dp_file':
        # Where the API reads a package for download (the host sees /asgard as /mnt/asgard).
        row = db.session.query(DataPackage).filter_by(hash=str(request.get('hash') or '')).first()
        if row is None or row.certificate is not None:
            return _fail('not_found')
        return {'ok': True, 'path': _upload_path(app, row.hash), 'filename': row.filename}

    return _fail('invalid_action')
