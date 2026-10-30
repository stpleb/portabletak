"""PortableTAK: Data Sync missions with an official TAK Server (ROL).

TAK Server federates missions as ROL on the same ClientROLStream / ServerROLStream
calls that carry its files (see files.py):

* "create mission\\n<MissionMetadata JSON>;" and "delete mission\\n<name, creatorUid>;"
* "update mission\\n<MissionUpdateDetails JSON>;" with changeType ADD_CONTENT or
  REMOVE_CONTENT and content {"hashes": [...], "uids": [...]}. A file goes first as
  "create resource" (with its bytes); a CoT uid refers to a track the other server
  already has from the CoT federation stream.

Inbound, those become missions, mission files and mission tracks here, recorded as
federated mission changes, and the phones subscribed to the mission are told like
for a local change. Outbound, this server's missions are compared every few seconds
with what each ROL peer was last sent (kept on disk, so changes made while the link
was down go when it is back), and the differences go as the same ROL. Only public
missions without a password are shared, and only with a federate whose outbound
groups include one of the mission's groups. A mission that stops being shared but
still exists here is not deleted on the peer. Tracks added by federated devices are
not sent back (their own server sends them).

PortableTAK peers do not use any of this: missions only go to ROL peers.
"""

import datetime
import hashlib
import json
import os
import shutil
import threading
import time
import traceback
import uuid

ANON_GROUP = "__ANON__"
PUBLIC = "public"
POLL_SECONDS = 3
WAIT_SECONDS = 600


def metadata_json(name: str, meta: dict) -> str:
    """TAK Server's MissionMetadata (only its fields: TAK Server refuses unknown ones)."""
    return json.dumps({
        "type": "MissionMetadata",
        "name": name,
        "creatorUid": meta.get("creatorUid") or "",
        "description": meta.get("description") or "",
        "chatRoom": meta.get("chatRoom") or "",
        "tool": meta.get("tool") or PUBLIC,
        "boundingPolygon": "",
        "bbox": meta.get("bbox") or "",
        "passwordHash": "",
        "path": meta.get("path") or "",
        "classification": meta.get("classification") or "",
        "baseLayer": meta.get("baseLayer") or "",
        "parentMissionId": 0,
        "defaultRoleId": 0,
        "expiration": meta.get("expiration") if meta.get("expiration") not in (None, 0) else -1,
        "inviteOnly": bool(meta.get("inviteOnly")),
        "guid": meta.get("guid") or "",
    }, separators=(",", ":"))


def delete_json(name: str, creator_uid: str | None) -> str:
    return json.dumps({"type": "MissionMetadata", "name": name, "creatorUid": creator_uid or ""},
                      separators=(",", ":"))


def update_json(change_type: str, name: str, meta: dict, creator_uid: str | None,
                hashes=(), uids=()) -> str:
    """TAK Server's MissionUpdateDetails for ADD_CONTENT / REMOVE_CONTENT."""
    return json.dumps({
        "content": {"hashes": list(hashes), "uids": list(uids)},
        "creatorUid": creator_uid or meta.get("creatorUid") or "",
        "changeType": change_type,
        "missionName": name,
        "missionCreatorUid": meta.get("creatorUid") or "",
        "missionChatRoom": meta.get("chatRoom") or "",
        "missionTool": meta.get("tool") or PUBLIC,
        "missionDescription": meta.get("description") or "",
        "date": int(time.time() * 1000),
    }, separators=(",", ":"))


def shared_with(groups, federate: dict) -> bool:
    """A mission in these groups goes to this federate (its outbound groups)."""
    return bool(set(groups or [ANON_GROUP]) & set(federate.get("outbound_groups") or []))


def plan(desired: dict, sent: dict, existing) -> list[tuple]:
    """What to send a peer: ops turning ``sent`` (what it was last sent) into ``desired``.

    desired: {name: {"meta": {...}, "hashes": {hash: info}, "uids": set}}
    sent: {name: {"creatorUid": str, "hashes": [...], "uids": [...]}}
    existing: names of every mission here (shared or not).
    Ops: ("create", name) ("add"|"remove", name, "hash"|"uid", value) ("delete"|"forget", name).
    """
    ops = []
    for name in sorted(desired):
        want, had = desired[name], sent.get(name)
        if had is None:
            ops.append(("create", name))
            had = {"hashes": [], "uids": []}
        for kind, key in (("hash", "hashes"), ("uid", "uids")):
            for value in sorted(set(want[key]) - set(had[key])):
                ops.append(("add", name, kind, value))
            # A phone on both servers flips between local and federated, which hides its
            # items from "uids": only an item really gone from the mission is removed there.
            for value in sorted(set(had[key]) - set(want[key]) - set(want.get("all_" + key) or [])):
                ops.append(("remove", name, kind, value))
    for name in sorted(set(sent) - set(desired)):
        # Still here but no longer shared (password, group, tool): stop, do not delete there.
        ops.append(("forget" if name in existing else "delete", name))
    return ops


def record(sent: dict, op: tuple, creator_uid: str | None = None):
    """Apply one op to a peer's sent state."""
    kind, name = op[0], op[1]
    if kind == "create":
        sent.setdefault(name, {"creatorUid": creator_uid or "", "hashes": [], "uids": []})
    elif kind in ("delete", "forget"):
        sent.pop(name, None)
    elif name in sent:
        values = sent[name]["hashes" if op[2] == "hash" else "uids"]
        if kind == "add" and op[3] not in values:
            values.append(op[3])
        elif kind == "remove" and op[3] in values:
            values.remove(op[3])


class MissionStore:
    """The OpenTAKServer database side (missions, files, tracks, change notices)."""

    def __init__(self, app, logger, config):
        self.app = app
        self.logger = logger
        self.config = config
        self.max_bytes = int(config.get("OTS_FEDERATION_MAX_FILE_BYTES", 200 * 1024 * 1024))

    # ---------------------------------------------------------------- outbound
    def snapshot(self):
        """(shareable missions {name: {...}}, names of all missions)."""
        from opentakserver.extensions import db
        from opentakserver.models.EUD import EUD
        from opentakserver.models.Mission import Mission

        out = {}
        with self.app.app_context():
            missions = db.session.execute(db.select(Mission)).scalars().all()
            existing = {m.name for m in missions}
            federated = set(db.session.execute(
                db.select(EUD.uid).where(EUD.last_status.like("Federated (%"))).scalars())
            for m in missions:
                if m.password_protected or (m.tool or PUBLIC) != PUBLIC:
                    continue
                out[m.name] = {
                    "meta": {"creatorUid": m.creator_uid, "description": m.description,
                             "chatRoom": m.chat_room, "tool": m.tool or PUBLIC, "bbox": m.bbox,
                             "path": m.path, "classification": m.classification,
                             "baseLayer": m.base_layer, "expiration": m.expiration,
                             "inviteOnly": m.invite_only, "guid": m.guid},
                    "groups": {g.name for g in m.groups} or {ANON_GROUP},
                    "hashes": {c.hash: {"filename": c.filename, "mime_type": c.mime_type,
                                        "keywords": c.keywords or [], "creator_uid": c.creator_uid}
                               for c in m.contents},
                    "uids": {u.uid for u in m.uids if u.creator_uid not in federated},
                    "all_uids": {u.uid for u in m.uids},
                }
        return out, existing

    def file_bytes(self, sha256: str, info: dict) -> bytes | None:
        """A mission file's bytes (where OpenTAKServer keeps it), checked against its hash."""
        name = os.path.basename(info.get("filename") or "")
        upload = self.config.get("UPLOAD_FOLDER") or ""
        candidates = [os.path.join(upload, sha256 + os.path.splitext(name)[1]),
                      os.path.join(upload, sha256 + ".zip")]
        if name:
            candidates.append(os.path.join(self.config.get("OTS_DATA_FOLDER") or "", "missions", name))
        for path in candidates:
            if os.path.isfile(path) and os.path.getsize(path) <= self.max_bytes:
                with open(path, "rb") as source:
                    data = source.read()
                if hashlib.sha256(data).hexdigest() == sha256:
                    return data
        return None

    # ---------------------------------------------------------------- inbound
    def _notify(self, exchange, routing_key, cot):
        import pika

        credentials = pika.PlainCredentials(self.config.get("OTS_RABBITMQ_USERNAME"),
                                            self.config.get("OTS_RABBITMQ_PASSWORD"))
        connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=self.config.get("OTS_RABBITMQ_SERVER_ADDRESS"), credentials=credentials))
        try:
            connection.channel().basic_publish(
                exchange=exchange, routing_key=routing_key,
                body=json.dumps({"uid": self.config.get("OTS_NODE_ID"), "cot": cot}))
        finally:
            connection.close()

    def _notify_safely(self, exchange, routing_key, element):
        from xml.etree.ElementTree import tostring

        for node in element.iter():                 # a federated creator is often not a device here
            for key, value in list(node.attrib.items()):
                if value is None:
                    node.set(key, "")
        try:
            self._notify(exchange, routing_key, tostring(element).decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Federated mission change saved, phones not told: {e}")

    def _creator(self, uid):
        """A creator uid the missions table accepts (it must be a known EUD), or None."""
        from opentakserver.extensions import db
        from opentakserver.models.EUD import EUD

        if uid and db.session.execute(db.select(EUD).filter_by(uid=uid)).first():
            return uid
        return None

    def ensure_mission(self, name, meta: dict, federate: dict) -> str:
        """Create a federated mission if it is not here. 'created', 'exists' or a refusal reason."""
        import bleach
        from opentakserver.extensions import db
        from opentakserver.models.Group import Group
        from opentakserver.models.GroupMission import GroupMission
        from opentakserver.models.Mission import Mission
        from opentakserver.models.MissionChange import MissionChange
        from opentakserver.models.MissionRole import MissionRole

        if not name or len(name) > 255 or bleach.clean(name) != name:
            return "bad name"
        with self.app.app_context():
            if db.session.get(Mission, name):
                return "exists"
            if meta.get("passwordHash"):
                return "password protected"
            now = datetime.datetime.now(datetime.timezone.utc)
            groups = list(federate.get("inbound_groups") or [])
            mission = Mission()
            mission.name = name
            mission.description = (meta.get("description") or None) and meta["description"][:255]
            mission.chat_room = meta.get("chatRoom") or None
            mission.base_layer = meta.get("baseLayer") or None
            mission.bbox = meta.get("bbox") or None
            mission.path = meta.get("path") or None
            mission.classification = meta.get("classification") or None
            mission.tool = meta.get("tool") or PUBLIC
            mission.group = ",".join(groups)[:255] or ANON_GROUP
            mission.default_role = MissionRole.MISSION_SUBSCRIBER
            mission.creator_uid = self._creator(meta.get("creatorUid"))
            mission.create_time = now
            mission.guid = str(uuid.uuid4())
            mission.invite_only = bool(meta.get("inviteOnly"))
            mission.password_protected = False
            expiration = meta.get("expiration")
            mission.expiration = expiration if isinstance(expiration, int) and expiration > 0 else -1
            db.session.add(mission)
            db.session.flush()
            for group_name in groups:
                group = db.session.execute(db.select(Group).filter_by(name=group_name)).scalar()
                if group:
                    link = GroupMission()
                    link.group_id = group.id
                    link.mission_name = name
                    db.session.add(link)
            change = MissionChange()
            change.isFederatedChange = True
            change.change_type = MissionChange.CREATE_MISSION
            change.mission_name = name
            change.timestamp = now
            change.creator_uid = meta.get("creatorUid") or ""
            change.server_time = now
            db.session.add(change)
            db.session.commit()

            from opentakserver.blueprints.marti_api.mission_marti_api import generate_new_mission_cot

            event = generate_new_mission_cot(mission)
            for group_name in groups or [ANON_GROUP]:
                self._notify_safely("groups", f"{group_name}.OUT", event)
        return "created"

    def delete_mission(self, name) -> bool:
        from sqlalchemy import delete, update
        from opentakserver.extensions import db
        from opentakserver.models.CoT import CoT
        from opentakserver.models.GroupMission import GroupMission
        from opentakserver.models.Mission import Mission
        from opentakserver.models.MissionContentMission import MissionContentMission

        with self.app.app_context():
            mission = db.session.get(Mission, name)
            if not mission:
                return False
            from opentakserver.blueprints.marti_api.mission_marti_api import generate_mission_delete_cot

            event = generate_mission_delete_cot(mission)
            # Links the mission's relationships do not cascade (group links would block the
            # delete; the files stay, as for a local removal, and may be in other missions).
            db.session.execute(delete(GroupMission).where(GroupMission.mission_name == name))
            db.session.execute(delete(MissionContentMission).where(MissionContentMission.mission_name == name))
            db.session.execute(update(CoT).where(CoT.mission_name == name).values(mission_name=None))
            db.session.delete(mission)
            db.session.commit()
        self._notify_safely("missions", "missions", event)
        return True

    def _content_for(self, sha256, resource: dict, federate_name):
        """The MissionContent row for a federated file, made from the file the peer sent."""
        from opentakserver.extensions import db
        from opentakserver.models.DataPackage import DataPackage
        from opentakserver.models.MissionContent import MissionContent

        content = db.session.execute(db.select(MissionContent).filter_by(hash=sha256)).scalar()
        if content:
            return content
        source = os.path.join(self.config.get("UPLOAD_FOLDER") or "", sha256 + ".zip")
        if not os.path.isfile(source):
            return None
        package = db.session.execute(db.select(DataPackage).filter_by(hash=sha256)).scalar()
        name = os.path.basename(str(resource.get("filename") or resource.get("name")
                                    or (package.filename if package else "") or sha256))
        # OpenTAKServer serves mission files from <data>/missions/<file name>.
        folder = os.path.join(self.config.get("OTS_DATA_FOLDER") or "", "missions")
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, name)
        if os.path.exists(target) and self.file_bytes(sha256, {"filename": name}) is None:
            base, ext = os.path.splitext(name)
            name = "%s (%s)%s" % (base, sha256[:6], ext)
            target = os.path.join(folder, name)
        if not os.path.exists(target):
            shutil.copyfile(source, target + ".part")
            os.replace(target + ".part", target)
        content = MissionContent()
        content.uid = str(uuid.uuid4())
        content.hash = sha256
        content.filename = name[:255]
        content.mime_type = resource.get("mimeType") or "application/octet-stream"
        content.keywords = [k for k in (resource.get("keywords") or []) if isinstance(k, str)]
        content.submission_time = datetime.datetime.now(datetime.timezone.utc)
        content.submitter = ("federation:%s" % federate_name)[:255]
        content.creator_uid = resource.get("creatorUid") or None
        content.size = os.path.getsize(target)
        content.expiration = -1
        db.session.add(content)
        db.session.flush()
        return content

    def add_content(self, name, details: dict, resources: dict, federate: dict):
        """ADD_CONTENT from a peer. (hashes added, uids added, hashes whose file is not here yet)."""
        from bs4 import BeautifulSoup
        from opentakserver.extensions import db
        from opentakserver.models.CoT import CoT
        from opentakserver.models.Mission import Mission
        from opentakserver.models.MissionChange import MissionChange, generate_mission_change_cot
        from opentakserver.models.MissionContentMission import MissionContentMission
        from opentakserver.models.MissionUID import MissionUID

        content = details.get("content") or {}
        creator = details.get("creatorUid") or ""
        added_hashes, added_uids, waiting, events = [], [], [], []
        with self.app.app_context():
            mission = db.session.get(Mission, name)
            if mission is None:
                return added_hashes, added_uids, waiting
            now = datetime.datetime.now(datetime.timezone.utc)
            for sha256 in [str(h).lower() for h in content.get("hashes") or []]:
                row = self._content_for(sha256, resources.get(sha256) or {}, federate["name"])
                if row is None:
                    waiting.append(sha256)
                    continue
                if not db.session.execute(db.select(MissionContentMission).filter_by(
                        mission_content_id=row.id, mission_name=name)).first():
                    link = MissionContentMission()
                    link.mission_name = name
                    link.mission_content_id = row.id
                    db.session.add(link)
                change = MissionChange()
                change.isFederatedChange = True
                change.change_type = MissionChange.ADD_CONTENT
                change.content_uid = row.uid
                change.mission_name = name
                change.timestamp = now
                change.creator_uid = creator
                change.server_time = now
                db.session.add(change)
                events.append(generate_mission_change_cot(name, mission, change, content=row))
                added_hashes.append(sha256)
            for uid in [str(u) for u in content.get("uids") or []]:
                track = db.session.get(MissionUID, uid) or MissionUID()
                track.uid = uid
                track.mission_name = name
                track.timestamp = now
                track.creator_uid = creator
                cot = db.session.execute(db.select(CoT).filter_by(uid=uid)).scalar()
                if cot is not None:
                    track.cot_type = cot.type
                    if cot.point is not None:
                        track.latitude, track.longitude = cot.point.latitude, cot.point.longitude
                    event = BeautifulSoup(cot.xml or "", "xml")
                    contact, color, icon = event.find("contact"), event.find("color"), event.find("usericon")
                    if contact is not None and contact.get("callsign"):
                        track.callsign = contact["callsign"]
                    if color is not None and (color.get("argb") or color.get("value")):
                        track.color = color.get("argb") or color.get("value")
                    if icon is not None and (icon.get("iconsetpath") or icon.get("iconsetPath")):
                        track.iconset_path = icon.get("iconsetpath") or icon.get("iconsetPath")
                db.session.add(track)
                db.session.flush()
                change = MissionChange()
                change.isFederatedChange = True
                change.change_type = MissionChange.ADD_CONTENT
                change.mission_name = name
                change.timestamp = now
                change.creator_uid = creator
                change.server_time = now
                change.mission_uid = uid
                db.session.add(change)
                events.append(generate_mission_change_cot(name, mission, change, mission_uid=track))
                added_uids.append(uid)
            db.session.commit()
        for event in events:
            self._notify_safely("missions", f"missions.{name}", event)
        return added_hashes, added_uids, waiting

    def remove_content(self, name, details: dict):
        """REMOVE_CONTENT from a peer. (hashes removed, uids removed)."""
        from opentakserver.extensions import db
        from opentakserver.models.Mission import Mission
        from opentakserver.models.MissionChange import MissionChange, generate_mission_change_cot
        from opentakserver.models.MissionContent import MissionContent
        from opentakserver.models.MissionContentMission import MissionContentMission
        from opentakserver.models.MissionUID import MissionUID

        content = details.get("content") or {}
        removed_hashes, removed_uids, events = [], [], []
        with self.app.app_context():
            mission = db.session.get(Mission, name)
            if mission is None:
                return removed_hashes, removed_uids
            now = datetime.datetime.now(datetime.timezone.utc)

            def change_for(**fields):
                change = MissionChange()
                change.isFederatedChange = True
                change.change_type = MissionChange.REMOVE_CONTENT
                change.mission_name = name
                change.timestamp = now
                change.creator_uid = details.get("creatorUid") or ""
                change.server_time = now
                for key, value in fields.items():
                    setattr(change, key, value)
                db.session.add(change)
                return change

            for sha256 in [str(h).lower() for h in content.get("hashes") or []]:
                row = db.session.execute(db.select(MissionContent).filter_by(hash=sha256)).scalar()
                link = row and db.session.execute(db.select(MissionContentMission).filter_by(
                    mission_content_id=row.id, mission_name=name)).scalar()
                if not link:
                    continue
                db.session.delete(link)    # the file stays, like a local removal
                change = change_for(content_uid=row.uid)
                events.append(generate_mission_change_cot(name, mission, change, content=row))
                removed_hashes.append(sha256)
            for uid in [str(u) for u in content.get("uids") or []]:
                track = db.session.get(MissionUID, uid)
                if track is None or track.mission_name != name:
                    continue
                track.mission_name = None
                change = change_for(mission_uid=uid)
                events.append(generate_mission_change_cot(name, mission, change, mission_uid=track))
                removed_uids.append(uid)
            db.session.commit()
        for event in events:
            self._notify_safely("missions", f"missions.{name}", event)
        return removed_hashes, removed_uids


class MissionSync:
    """Keeps ROL peers' copies of this server's missions, and applies theirs here."""

    def __init__(self, relay, app, logger, config, lookup, store=None, start=True):
        self.relay = relay
        self.logger = logger
        self.config = config
        self.lookup = lookup                    # federate id -> current federate dict
        self.store = store or MissionStore(app, logger, config)
        self.folder = os.path.join(config.get("OTS_DATA_FOLDER") or ".", "federation_missions")
        self.poll = float(config.get("OTS_FEDERATION_MISSION_POLL_SECONDS", POLL_SECONDS))
        self.resources = {}                     # sha256 -> "create resource" metadata from a peer
        self.waiting = []                       # (time, mission name, details, federate) for files not here yet
        self._sent = {}                         # federate id -> {name: {...}}
        self._lock = threading.RLock()
        if start:
            threading.Thread(target=self._loop, name="fed-missions", daemon=True).start()

    # ---------------------------------------------------------------- sent state
    def sent(self, federate_id) -> dict:
        if federate_id not in self._sent:
            try:
                with open(os.path.join(self.folder, "%d.json" % int(federate_id))) as source:
                    self._sent[federate_id] = json.load(source)
            except (OSError, ValueError):
                self._sent[federate_id] = {}
        return self._sent[federate_id]

    def _save(self, federate_id):
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, "%d.json" % int(federate_id))
        with open(path + ".part", "w") as out:
            json.dump(self._sent.get(federate_id, {}), out)
        os.replace(path + ".part", path)

    # ---------------------------------------------------------------- outbound
    def _loop(self):
        while True:
            time.sleep(self.poll)
            try:
                self.sync_all()
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"Federation mission sync failed: {e}")
                self.logger.debug(traceback.format_exc())

    def sync_all(self):
        peers = self.relay.streaming_rol_peers()
        if not peers:
            self._expire()
            return
        with self._lock:
            desired, existing = self.store.snapshot()
            for federate_id in peers:
                federate = self.lookup(federate_id)
                if federate and federate.get("enabled"):
                    self.sync_peer(federate, desired, existing)
            self._retry_waiting()

    def sync_peer(self, federate, desired, existing):
        mine = {n: m for n, m in desired.items() if shared_with(m["groups"], federate)}
        sent = self.sent(federate["id"])
        ops = plan(mine, sent, existing)
        if not ops:
            return
        from opentakserver.federation.proto import fig_pb2

        groups = list(federate.get("outbound_groups") or [])
        q = self.relay.rol_queue(federate["id"])
        done = 0
        try:
            for op in ops:
                kind, name = op[0], op[1]
                meta = (mine.get(name) or {}).get("meta") or {}
                messages = []
                if kind == "create":
                    messages.append("create mission\n" + metadata_json(name, meta) + ";")
                elif kind == "delete":
                    messages.append("delete mission\n" + delete_json(name, sent[name].get("creatorUid")) + ";")
                elif kind in ("add", "remove"):
                    if op[2] == "hash" and kind == "add":
                        info = mine[name]["hashes"][op[3]]
                        data = self.store.file_bytes(op[3], info)
                        if data is None:
                            self.logger.warning(f"Mission '{name}' file {op[3][:12]} not sent to "
                                                f"'{federate['name']}': file missing or too large")
                        else:
                            from opentakserver.federation.files import resource_json

                            q.put(fig_pb2.ROL(program="create resource\n" + resource_json(
                                op[3], info.get("filename") or op[3], len(data), info.get("creator_uid"),
                                keywords=info.get("keywords") or [], mime_type=info.get("mime_type")) + ";",
                                payload=[fig_pb2.BinaryBlob(data=data)], federateGroups=groups), timeout=5)
                    change = "ADD_CONTENT" if kind == "add" else "REMOVE_CONTENT"
                    hashes = [op[3]] if op[2] == "hash" else []
                    uids = [op[3]] if op[2] == "uid" else []
                    creator = (mine[name]["hashes"].get(op[3]) or {}).get("creator_uid") if hashes else None
                    messages.append("update mission\n" + update_json(change, name, meta, creator, hashes, uids) + ";")
                for program in messages:
                    q.put(fig_pb2.ROL(program=program, federateGroups=groups), timeout=5)
                record(sent, op, meta.get("creatorUid"))
                done += 1
        except Exception as e:  # noqa: BLE001  (queue full: the rest goes next time)
            self.logger.info(f"Mission changes to '{federate['name']}' paused: {e!r}")
        finally:
            if done:
                self._save(federate["id"])
                self.logger.info(f"Sent {done} mission change(s) to '{federate['name']}' as ROL")

    # ---------------------------------------------------------------- inbound
    def resource_arrived(self, sha256, params: dict):
        """A peer's "create resource" was stored: keep its metadata for the mission change after it."""
        with self._lock:
            self.resources[sha256] = {k: params.get(k) for k in
                                      ("filename", "name", "mimeType", "keywords", "creatorUid")}
            while len(self.resources) > 512:
                self.resources.pop(next(iter(self.resources)))
            if any(sha256 in ((w[2].get("content") or {}).get("hashes") or []) for w in self.waiting):
                self._retry_waiting()

    def inbound(self, operation, resource, params: dict, federate: dict) -> bool:
        """A ROL program about missions from a peer. True when it was one."""
        if resource != "mission":
            return False
        if not federate.get("enabled") or not federate.get("inbound_groups"):
            return True
        if not self.config.get("OTS_FEDERATION_MISSIONS", True):
            return True
        try:
            with self._lock:
                self._inbound(operation, params, federate)
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"PortableTAK: Mission ROL '{operation} mission' from '{federate['name']}' failed: {e}")
            self.logger.debug(traceback.format_exc())
        return True

    def _shared_back(self, federate):
        """Missions made from this peer's ROL are in its inbound groups: do they go back to it?"""
        return shared_with(federate.get("inbound_groups") or [], federate)

    def _inbound(self, operation, params, federate):
        sent = self.sent(federate["id"])
        if operation == "create":
            name = params.get("name") or ""
            result = self.store.ensure_mission(name, params, federate)
            if result == "created":
                self.logger.info(f"Mission '{name}' created from '{federate['name']}'")
            elif result != "exists":
                self.logger.info(f"Mission '{name}' from '{federate['name']}' not created: {result}")
            if result in ("created", "exists") and self._shared_back(federate):
                record(sent, ("create", name), params.get("creatorUid"))
                self._save(federate["id"])
        elif operation == "delete":
            name = params.get("name") or ""
            if self.store.delete_mission(name):
                self.logger.info(f"Mission '{name}' deleted by '{federate['name']}'")
            if sent.pop(name, None) is not None:
                self._save(federate["id"])
        elif operation == "update":
            change = params.get("changeType")
            name = params.get("missionName") or ""
            if change == "ADD_CONTENT":
                meta = {"creatorUid": params.get("missionCreatorUid"), "description": params.get("missionDescription"),
                        "chatRoom": params.get("missionChatRoom"), "tool": params.get("missionTool")}
                result = self.store.ensure_mission(name, meta, federate)
                if result not in ("created", "exists"):
                    self.logger.info(f"Mission '{name}' content from '{federate['name']}' ignored: {result}")
                    return
                if self._shared_back(federate):
                    record(sent, ("create", name), meta["creatorUid"])
                hashes, uids, waiting = self.store.add_content(name, params, self.resources, federate)
                self._recorded(sent, federate, name, "add", hashes, uids)
                if waiting:
                    held = dict(params, content={"hashes": waiting, "uids": []})
                    self.waiting.append((time.monotonic(), name, held, federate))
                    self.logger.info(f"Mission '{name}' file(s) from '{federate['name']}' wait for their data")
            elif change == "REMOVE_CONTENT":
                hashes, uids = self.store.remove_content(name, params)
                self._recorded(sent, federate, name, "remove", hashes, uids)
            else:
                self.logger.debug(f"Mission update '{change}' from '{federate['name']}' not handled")
        else:
            self.logger.debug(f"ROL '{operation} mission' from '{federate['name']}' not handled")

    def _recorded(self, sent, federate, name, kind, hashes, uids):
        for value in hashes:
            record(sent, (kind, name, "hash", value))
        for value in uids:
            record(sent, (kind, name, "uid", value))
        if hashes or uids:
            self._save(federate["id"])
            self.logger.info(f"PortableTAK: Mission '{name}': {len(hashes)} file(s) and {len(uids)} item(s) "
                             f"{'added' if kind == 'add' else 'removed'} by '{federate['name']}'")

    def _retry_waiting(self):
        waiting, self.waiting = self.waiting, []
        for since, name, details, federate in waiting:
            hashes, uids, still = self.store.add_content(name, details, self.resources, federate)
            self._recorded(self.sent(federate["id"]), federate, name, "add", hashes, uids)
            if still and time.monotonic() - since < WAIT_SECONDS:
                self.waiting.append((since, name, dict(details, content={"hashes": still, "uids": []}), federate))
            elif still:
                self.logger.warning(f"Mission '{name}' file(s) from '{federate['name']}' dropped: never arrived")

    def _expire(self):
        with self._lock:
            if self.waiting:
                self._retry_waiting()
