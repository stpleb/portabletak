"""ATAK plugin / app update server for the PortableTAK dashboard, run inside the
OpenTAKServer app context by users_worker.

OpenTAKServer already serves the TAK update server ATAK reads (/api/packages/product.infz
on 8443, phones authenticate with their certificate). These actions manage its list: add
an APK (metadata read from the APK itself, as OpenTAKServer does), change "install on
enrollment / connection", delete. Each change rebuilds product.infz.
"""
import base64
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone

SAFE = re.compile(r'[^A-Za-z0-9._-]')


def _fail(error):
    return {'ok': False, 'error': error}


def _folder(app):
    path = os.path.join(app.config.get('OTS_DATA_FOLDER'), 'packages')
    os.makedirs(path, exist_ok=True)
    return path


def _json(package):
    return {
        'package_name': package.package_name, 'name': package.name, 'version': package.version,
        'revision_code': package.revision_code, 'file_name': package.file_name, 'file_size': package.file_size,
        'description': package.description or '', 'platform': package.platform, 'plugin_type': package.plugin_type,
        'install_on_enrollment': bool(package.install_on_enrollment),
        'install_on_connection': bool(package.install_on_connection),
        'tak_prereq': package.tak_prereq, 'atak_version': package.atak_version,
        'publish_time': package.publish_time.isoformat() + 'Z' if package.publish_time else None,
        'icon': 'data:image/png;base64,' + base64.b64encode(package.icon).decode() if package.icon else None,
    }


def _rebuild(app):
    from opentakserver.blueprints.ots_api.package_api import create_product_infz
    create_product_infz(None)


def _read_apk(path):
    """What ATAK's update list needs, read from the APK (androguard, as OpenTAKServer does)."""
    from androguard.core.apk import APK
    apk = APK(path)
    info = {'package_name': apk.get_package(), 'version': apk.get_androidversion_name(),
            'revision_code': apk.get_androidversion_code(), 'os_requirement': apk.get_min_sdk_version(),
            'name': apk.get_app_name() or apk.get_package(), 'tak_prereq': None, 'icon': None}
    if not info['package_name']:
        raise ValueError('not_an_android_app')
    android = '{http://schemas.android.com/apk/res/android}'
    for meta in apk.get_android_manifest_xml().iter('meta-data'):
        if meta.get(android + 'name') == 'plugin-api':
            info['tak_prereq'] = meta.get(android + 'value')
            break
    try:
        icon = apk.get_app_icon()
        if icon and icon.endswith('.png'):
            info['icon'] = apk.get_file(icon)
    except Exception:
        pass
    return info


def _text_prereq_column(db):
    """OpenTAKServer's model says tak_prereq is text (ATAK plugins use e.g.
    'com.atakmap.app@5.7.0.CIV', which ATAK checks), but its migration made it an integer,
    so no real plugin could be added. Make the column text (once; harmless if already)."""
    from sqlalchemy import text
    kind = db.session.execute(text("SELECT data_type FROM information_schema.columns "
                                   "WHERE table_name = 'packages' AND column_name = 'tak_prereq'")).scalar()
    if kind and kind != 'character varying':
        db.session.execute(text('ALTER TABLE packages ALTER COLUMN tak_prereq TYPE varchar(255) USING tak_prereq::varchar'))
        db.session.commit()


def handle(app, db, request):
    from opentakserver.models.Packages import Packages
    action = request.get('action', '')
    if action == 'plugins_add':
        _text_prereq_column(db)

    if action == 'plugins_list':
        rows = db.session.query(Packages).order_by(Packages.name).all()
        return {'ok': True, 'plugins': [_json(p) for p in rows]}

    if action == 'plugins_add':
        staged = str(request.get('staged') or '')
        if not staged.startswith('/asgard/opentak/data/packages-upload/') or not os.path.isfile(staged):
            return _fail('upload_missing')
        try:
            info = _read_apk(staged)
        except Exception:
            os.remove(staged)
            return _fail('not_an_android_app')
        version = re.sub(r'_+', '_', SAFE.sub('_', str(info['version'] or '0'))).strip('_.') or '0'
        file_name = SAFE.sub('_', info['package_name']) + '-' + version[:40] + '.apk'
        target = os.path.join(_folder(app), file_name)
        with open(staged, 'rb') as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        for old in db.session.query(Packages).filter_by(package_name=info['package_name'], atak_version=None).all():
            old_file = os.path.join(_folder(app), old.file_name)
            if old.file_name != file_name and os.path.isfile(old_file):
                os.remove(old_file)
            db.session.delete(old)
        shutil.move(staged, target)
        package = Packages()
        package.platform, package.plugin_type = 'Android', str(request.get('plugin_type') or 'plugin')
        package.package_name, package.name = info['package_name'], info['name']
        package.version, package.revision_code = info['version'], info['revision_code']
        package.os_requirement, package.tak_prereq = info['os_requirement'], None   # set below (model says integer)
        package.file_name, package.file_size, package.apk_hash = file_name, os.path.getsize(target), digest
        package.description = str(request.get('description') or '')[:255] or None
        package.icon = info['icon']
        package.icon_filename = SAFE.sub('_', info['package_name']) + '.png' if info['icon'] else None
        package.install_on_enrollment = bool(request.get('install_on_enrollment'))
        package.install_on_connection = bool(request.get('install_on_connection'))
        package.publish_time = datetime.now(timezone.utc)
        package.atak_version = None
        db.session.add(package)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            if os.path.isfile(target):
                os.remove(target)
            raise
        if info['tak_prereq']:
            from sqlalchemy import text
            db.session.execute(text('UPDATE packages SET tak_prereq = :value WHERE id = :id'),
                               {'value': str(info['tak_prereq'])[:255], 'id': package.id})
            db.session.commit()
            db.session.refresh(package)
        _rebuild(app)
        return {'ok': True, 'plugin': _json(package)}

    if action == 'plugins_update':
        package = db.session.query(Packages).filter_by(package_name=str(request.get('package_name') or ''),
                                                       atak_version=None).first()
        if package is None:
            return _fail('not_found')
        for key in ('install_on_enrollment', 'install_on_connection'):
            if key in request:
                setattr(package, key, bool(request[key]))
        if 'description' in request:
            package.description = str(request.get('description') or '')[:255] or None
        package.publish_time = datetime.now(timezone.utc)
        db.session.commit()
        _rebuild(app)
        return {'ok': True, 'plugin': _json(package)}

    if action == 'plugins_delete':
        package = db.session.query(Packages).filter_by(package_name=str(request.get('package_name') or ''),
                                                       atak_version=None).first()
        if package is None:
            return _fail('not_found')
        path = os.path.join(_folder(app), package.file_name)
        if os.path.isfile(path):
            os.remove(path)
        db.session.delete(package)
        db.session.commit()
        _rebuild(app)
        return {'ok': True}

    return _fail('invalid_action')
