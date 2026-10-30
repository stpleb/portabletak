"""TAK channels (OpenTAKServer groups) for the PortableTAK dashboard, run inside the
OpenTAKServer app context by users_worker.

A user can send into a channel (direction IN) and/or receive from it (OUT). Users in
no channel use "__ANON__", the built-in TAK name for "no channel": it cannot be
renamed or deleted. Phones pick up channel changes when they reconnect.
"""
import re

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}")
ANON = "__ANON__"


def builtin(app):
    """Channels OpenTAKServer itself creates and uses by name: name -> what it carries."""
    return {ANON: 'everyone not in a channel',
            app.config.get("OTS_ADSB_GROUP", "ADS-B"): 'aircraft (ADS-B) feed',
            app.config.get("OTS_AIS_GROUP", "AIS"): 'ships (AIS) feed',
            app.config.get("OTS_MESHTASTIC_GROUP", "Meshtastic"): 'Meshtastic feed'}


def receive_only(app):
    """Feed channels: phones cannot post into them, and ATAK would pick them as its default."""
    return {app.config.get("OTS_ADSB_GROUP", "ADS-B"), app.config.get("OTS_AIS_GROUP", "AIS")}


def _fail(error):
    return {"ok": False, "error": error}


def _models():
    from opentakserver.models.Group import Group
    from opentakserver.models.GroupUser import GroupUser
    return Group, GroupUser


def _user(app, db, username):
    model = app.security.datastore.user_model
    return (db.session.query(model).filter(model.username.ilike(str(username or "").strip())).first()
            if username else None)


def _counts(db, GroupUser, group_id):
    rows = db.session.query(GroupUser).filter_by(group_id=group_id).all()
    send = {r.user_id for r in rows if r.direction == "IN"}
    receive = {r.user_id for r in rows if r.direction == "OUT"}
    return len(send | receive), len(send), len(receive)


def handle(app, db, request):
    if app.config.get("OTS_ENABLE_LDAP"):
        return _fail("ldap_enabled")
    Group, GroupUser = _models()
    action = request.get("action", "")

    if action == "channels_list":
        groups = db.session.query(Group).order_by(Group.name).all()
        fixed = builtin(app)
        out = []
        for g in groups:
            members, send, receive = _counts(db, GroupUser, g.id)
            out.append({"name": g.name, "description": g.description or "", "members": members,
                        "send": send, "receive": receive, "builtin": g.name in fixed,
                        "builtin_note": fixed.get(g.name, "")})
        out.sort(key=lambda c: (c["name"] != ANON, c["builtin"], c["name"].lower()))
        return {"ok": True, "channels": out}

    if action == "channels_create":
        name = str(request.get("name") or "").strip()
        if not NAME.fullmatch(name) or name in builtin(app):
            return _fail("invalid_channel_name")
        if db.session.query(Group).filter(Group.name.ilike(name)).first():
            return _fail("channel_exists")
        group = Group()
        group.name = name
        group.type = Group.SYSTEM
        group.description = str(request.get("description") or "")[:255] or None
        db.session.add(group)
        db.session.commit()
        return {"ok": True, "name": name}

    if action == "channels_rename":
        old, new = str(request.get("name") or ""), str(request.get("new_name") or "").strip()
        if old in builtin(app):
            return _fail("builtin_channel")
        if not NAME.fullmatch(new) or new in builtin(app):
            return _fail("invalid_channel_name")
        group = db.session.query(Group).filter_by(name=old).first()
        if group is None:
            return _fail("not_found")
        clash = db.session.query(Group).filter(Group.name.ilike(new)).first()
        if clash is not None and clash.id != group.id:
            return _fail("channel_exists")
        group.name = new
        if "description" in request:
            group.description = str(request.get("description") or "")[:255] or None
        db.session.commit()
        return {"ok": True, "name": new}

    if action == "channels_delete":
        name = str(request.get("name") or "")
        if name in builtin(app):
            return _fail("builtin_channel")
        group = db.session.query(Group).filter_by(name=name).first()
        if group is None:
            return _fail("not_found")
        db.session.query(GroupUser).filter_by(group_id=group.id).delete()
        db.session.delete(group)
        db.session.commit()
        return {"ok": True}

    if action == "channels_user_get":
        user = _user(app, db, request.get("username"))
        if user is None:
            return _fail("user_not_found")
        rows = db.session.query(GroupUser).filter_by(user_id=user.id).all()
        names = {g.id: g.name for g in db.session.query(Group).all()}
        result = {}
        for r in rows:
            entry = result.setdefault(names.get(r.group_id, "?"), {"send": False, "receive": False})
            entry["send" if r.direction == "IN" else "receive"] = True
        return {"ok": True, "username": user.username,
                "channels": [{"name": n, **v} for n, v in sorted(result.items())]}

    if action == "channels_user_set":
        # channels: [{name, send, receive}] - the user's complete set (others removed)
        user = _user(app, db, request.get("username"))
        if user is None:
            return _fail("user_not_found")
        wanted = request.get("channels")
        if not isinstance(wanted, list):
            return _fail("invalid_channels")
        groups = {g.name: g for g in db.session.query(Group).all()}
        rows = []
        feeds = receive_only(app)
        for item in wanted:
            if not isinstance(item, dict) or item.get("name") not in groups:
                return _fail("unknown_channel")
            for direction, key in (("IN", "send"), ("OUT", "receive")):
                if item.get(key) and not (direction == "IN" and item["name"] in feeds):
                    rows.append((groups[item["name"]].id, direction))
        db.session.query(GroupUser).filter_by(user_id=user.id).delete()
        for group_id, direction in rows:
            membership = GroupUser()
            membership.user_id = user.id
            membership.group_id = group_id
            membership.direction = direction
            membership.enabled = True
            db.session.add(membership)
        db.session.commit()
        return {"ok": True, "username": user.username, "memberships": len(rows)}

    return _fail("invalid_action")
