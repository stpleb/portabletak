"""PortableTAK: carry data packages across federation (v2 links).

When a phone sends a data package to someone on another server, ATAK uploads the
file to its own server and sends a file-share CoT (``<fileshare senderUrl=...>``)
pointing at that server, which the other server's phones cannot use. This relay
moves the file itself across the federation link and lets the receiving server
serve it:

* The receiving server holds the file-share CoT until the file has arrived.
* The file travels over the existing mutually authenticated gRPC link (port 9001)
  through a small extra service, ``/portabletak.FederationFiles/{Get,Put}``.
  Whichever server dialed the link moves it: the receiver pulls (Get) or the
  sender pushes (Put).
* The receiving server stores it as one of its own data packages (checked against
  its SHA-256) and releases the CoT with the download URL pointing at itself; the
  host part is a placeholder that PortableTAK's EUD handler fills in per phone
  with the address that phone uses.

Only files offered over a link can be fetched through it, only in the directions
the link shares, and up to ``OTS_FEDERATION_MAX_FILE_BYTES``. v1 links pass the
file-share CoT through unchanged.

An official TAK Server does not know that service. It federates files with ROL
("create resource\n<JSON>;" plus the file bytes), on the ClientROLStream /
ServerROLStream calls, whenever it stores an upload with mission federation on.
It does not send file-share CoT over federation either: it sends "announce package"
ROL (sha256, file name, size, sender, and who it is for), and the receiving server
makes its own file-share CoT pointing at itself. So with a peer that speaks ROL (it
subscribed to our ClientROLStream, or our service is unknown to it): its files are
stored here like the ones above and its announcements become file-share CoT for our
phones, held until the file is here; a file share going to it goes as the file
("create resource") then the announcement, instead of the CoT.
"""

import datetime
import hashlib
import json
import os
import queue
import re
import threading
import time
import traceback

import grpc

PLACEHOLDER_HOST = "PORTABLETAK-FEDERATION-HOST"
SERVICE = "portabletak.FederationFiles"
CHUNK = 256 * 1024
PENDING_SECONDS = 600

FILESHARE = re.compile(r"<fileshare\b[^>]*>", re.S)


def resource_json(sha256: str, filename: str, size: int, sender_uid: str | None,
                  keywords=None, mime_type=None) -> str:
    """TAK Server's enterprise-sync Resource, as its ROL "create resource" carries it.
    toMetadata() on the other side needs keywords, size and submissionTime. Defaults are
    a data package's; a mission file passes its own keywords and type."""
    now = datetime.datetime.now(datetime.timezone.utc)
    resource = {
        "uid": sha256,
        "hash": sha256,
        "name": filename,
        "filename": filename,
        "keywords": ["missionpackage"] if keywords is None else list(keywords),
        "mimeType": mime_type or "application/x-zip-compressed",
        "submissionTime": now.strftime("%Y-%m-%dT%H:%M:%S.") + str(now.microsecond // 100000) + "Z",
        "submitter": "federation",
        "size": size,
        "tool": "public",
    }
    if sender_uid:
        resource["creatorUid"] = sender_uid
    return json.dumps(resource, separators=(",", ":"))


def destinations(cot: str) -> dict:
    """Who a file share is for: <marti><dest callsign=... uid=...>."""
    callsigns, uids = [], []
    for tag in re.findall(r"<dest\b[^>]*>", cot or ""):
        if _attr(tag, "callsign"):
            callsigns.append(_attr(tag, "callsign"))
        if _attr(tag, "uid"):
            uids.append(_attr(tag, "uid"))
    return {"callsigns": callsigns, "uids": uids}


def announce_json(info: dict, sender_callsign: str | None, dests: dict) -> str:
    """TAK Server's ResourceDetails for "announce package" (senderUid carries the destinations)."""
    return json.dumps({
        "sha256": info["sha256"],
        "senderUid": json.dumps(dests, separators=(",", ":")),
        "name": info.get("name") or info["filename"],
        "filename": info["filename"],
        "senderUrl": "",
        "tsStored": int(time.time() * 1000),
        "sizeInBytes": info.get("size") or 0,
        "senderCallsign": sender_callsign or "",
    }, separators=(",", ":"))


def announce_cot(details: dict, port: int) -> str | None:
    """A file-share CoT for our phones from a TAK Server "announce package" (as TAK Server
    builds it, pointing at this server)."""
    sha256 = str(details.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return None
    try:
        dests = json.loads(details.get("senderUid") or "{}")
    except ValueError:
        dests = {}
    esc = lambda v: str(v or "").replace("&", "&amp;").replace("'", "&apos;").replace("<", "&lt;")
    now = datetime.datetime.now(datetime.timezone.utc)
    t = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    filename = os.path.basename(str(details.get("filename") or sha256 + ".zip"))
    marti = "".join("<dest uid='%s'/>" % esc(u) for u in (dests.get("uids") or []))
    marti += "".join("<dest callsign='%s'/>" % esc(c) for c in (dests.get("callsigns") or []))
    return ("<?xml version='1.0' encoding='UTF-8' standalone='yes'?><event version='2.0' uid='%s' type='b-f-t-r' "
            "time='%s' start='%s' stale='%s' how='h-e'><point lat='0.0' lon='0.0' hae='9999999.0' ce='9999999' "
            "le='9999999'/><detail><fileshare sha256='%s' senderUid='%s' name='%s' filename='%s' senderUrl='%s' "
            "sizeInBytes='%s' senderCallsign='%s'/>%s</detail></event>") % (
        sha256, t(now), t(now), t(now + datetime.timedelta(minutes=10)), sha256, sha256,
        esc(details.get("name") or filename), esc(filename),
        "https://%s:%d/Marti/sync/content?hash=%s" % (PLACEHOLDER_HOST, port, sha256),
        int(details.get("sizeInBytes") or 0), esc(details.get("senderCallsign")),
        "<marti>%s</marti>" % marti if marti else "")


def parse_rol(program: str):
    """(operation, resource, parameters dict) of a ROL program, or None."""
    match = re.match(r"\s*(create|update|delete|assign|announce)\s+(\w+)\s*\n?(.*?);?\s*$", program or "", re.S)
    if not match:
        return None
    try:
        params = json.loads(match.group(3)) if match.group(3).strip() else {}
    except ValueError:
        return None
    return match.group(1), match.group(2), params if isinstance(params, dict) else {}


def rewrite_sender_url_to(cot: str, url: str) -> str:
    return re.sub(r"""(<fileshare\b[^>]*\bsenderUrl=)(?:"[^"]*"|'[^']*')""", lambda m: m.group(1) + '"' + url + '"', cot, count=1)


def _attr(tag: str, name: str) -> str | None:
    """An attribute in double or single quotes (TAK Server writes single quotes)."""
    match = re.search(r"""\b%s=(?:"([^"]*)"|'([^']*)')""" % name, tag)
    return (match.group(1) if match.group(1) is not None else match.group(2)) if match else None


def fileshare_info(cot: str) -> dict | None:
    """The file-share fields of a CoT, or None when it is not a file share."""
    if not cot or "<fileshare" not in cot:
        return None
    tag = FILESHARE.search(cot)
    if not tag:
        return None
    tag = tag.group(0)
    sha256 = (_attr(tag, "sha256") or "").lower()
    url = _attr(tag, "senderUrl") or ""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        match = re.search(r"hash=([0-9a-fA-F]{64})", url)
        sha256 = match.group(1).lower() if match else ""
    if not sha256:
        return None
    try:
        size = int(_attr(tag, "sizeInBytes") or 0)
    except ValueError:
        size = 0
    return {
        "sha256": sha256,
        "filename": _attr(tag, "filename") or (sha256 + ".zip"),
        "size": size,
        "sender_uid": _attr(tag, "senderUid"),
        "url": url,
    }


def rewrite_sender_url(cot: str, sha256: str, port: int) -> str:
    """Point the file share at this server (host filled in per phone by the EUD handler)."""
    url = "https://%s:%d/Marti/sync/content?hash=%s" % (PLACEHOLDER_HOST, port, sha256)
    return rewrite_sender_url_to(cot, url)


def _identity(data):
    return data


class FileRelay:
    """Moves file-share data packages across v2 federation links (one per server)."""

    def __init__(self, app, logger, bridge_factory, config=None):
        self.app = app
        self.logger = logger
        self.bridge_factory = bridge_factory
        self.config = config or app.config
        self.max_bytes = int(self.config.get("OTS_FEDERATION_MAX_FILE_BYTES", 200 * 1024 * 1024))
        self.port = int(self.config.get("OTS_MARTI_HTTPS_PORT", 8443))
        self.channels = {}          # federate id -> gRPC channel we dialed
        self.offered = {}           # federate id -> {sha256}: files we offered over that link
        self.pending = {}           # sha256 -> [(envelope, federate dict, time)]
        self.rol_peers = set()      # federate ids that speak ROL (official TAK Server)
        self.rol_out = {}           # federate id -> queue of ROL messages to send
        self.rol_streaming = {}     # federate id -> ROL senders running (the peer is connected)
        self.missions = None        # missions.MissionSync once the manager attaches it
        self._lock = threading.Lock()
        self._work = queue.Queue()
        self._bridge = None
        threading.Thread(target=self._worker, name="fed-files", daemon=True).start()

    # ---------------------------------------------------------------- storage
    def _upload_path(self, sha256):
        return os.path.join(self.app.config.get("UPLOAD_FOLDER"), sha256 + ".zip")

    def have(self, sha256) -> bool:
        from opentakserver.extensions import db
        from opentakserver.models.DataPackage import DataPackage

        with self.app.app_context():
            row = db.session.execute(db.select(DataPackage).filter_by(hash=sha256)).first()
        return bool(row) and os.path.exists(self._upload_path(sha256))

    def store(self, sha256, filename, chunks, sender_uid, federate_name) -> bool:
        """Write a received file (iterable of bytes) as a data package. True when stored."""
        from opentakserver.extensions import db
        from opentakserver.models.DataPackage import DataPackage
        from opentakserver.models.EUD import EUD

        folder = self.app.config.get("UPLOAD_FOLDER")
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, ".%s.part" % sha256)
        digest, size = hashlib.sha256(), 0
        try:
            with open(tmp, "wb") as out:
                for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("file larger than OTS_FEDERATION_MAX_FILE_BYTES")
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != sha256:
                raise ValueError("SHA-256 does not match")
            os.replace(tmp, self._upload_path(sha256))
        except Exception as e:  # noqa: BLE001
            if os.path.exists(tmp):
                os.unlink(tmp)
            self.logger.warning(f"Federated data package {sha256[:12]} from '{federate_name}' refused: {e}")
            return False

        name = os.path.basename(filename or "") or sha256 + ".zip"
        with self.app.app_context():
            if not db.session.execute(db.select(DataPackage).filter_by(hash=sha256)).first():
                base, ext = os.path.splitext(name)
                if db.session.execute(db.select(DataPackage).filter_by(filename=name)).first():
                    name = "%s (%s)%s" % (base, sha256[:6], ext or ".zip")
                creator = None
                if sender_uid and db.session.execute(db.select(EUD).filter_by(uid=sender_uid)).first():
                    creator = sender_uid
                package = DataPackage()
                package.filename = name[:255]
                package.hash = sha256
                package.creator_uid = creator
                package.submission_time = datetime.datetime.now(datetime.timezone.utc)
                package.keywords = ("federation:%s" % federate_name)[:255]
                package.mime_type = "application/zip"
                package.size = size
                db.session.add(package)
                db.session.commit()
        self.logger.info(f"Federated data package '{name}' ({size} bytes) received from '{federate_name}'")
        return True

    # ---------------------------------------------------------------- ROL (TAK Server)
    def mark_rol_peer(self, federate: dict):
        with self._lock:
            new = federate["id"] not in self.rol_peers
            self.rol_peers.add(federate["id"])
        if new:
            self.logger.info(f"Federation peer '{federate['name']}' exchanges files as ROL (TAK Server)")

    def rol_queue(self, federate_id) -> queue.Queue:
        with self._lock:
            return self.rol_out.setdefault(federate_id, queue.Queue(maxsize=64))

    def rol_messages(self, federate_id, active):
        """ROL messages queued for a federate, while active() (one sender at a time)."""
        q = self.rol_queue(federate_id)
        with self._lock:
            self.rol_streaming[federate_id] = self.rol_streaming.get(federate_id, 0) + 1
        try:
            while active():
                try:
                    yield q.get(timeout=1.0)
                except queue.Empty:
                    continue
        finally:
            with self._lock:
                self.rol_streaming[federate_id] -= 1

    def streaming_rol_peers(self) -> list:
        with self._lock:
            return [fid for fid, count in self.rol_streaming.items() if count > 0]

    def attach_missions(self, lookup):
        """Data Sync missions with ROL peers (missions.py); lookup: federate id -> federate."""
        if self.missions is None and self.config.get("OTS_FEDERATION_MISSIONS", True):
            from opentakserver.federation.missions import MissionSync

            self.missions = MissionSync(self, self.app, self.logger, self.config, lookup)

    def before_outbound(self, federate: dict, cot: str) -> str | None:
        """A file-share CoT is about to go to a ROL peer: send the file and its announcement as
        ROL instead, and None (the CoT itself is not sent). Other CoT (and other peers) unchanged."""
        if federate["id"] not in self.rol_peers:
            return cot
        info = fileshare_info(cot)
        if not info:
            return cot
        path = self._upload_path(info["sha256"])
        if not os.path.exists(path) or os.path.getsize(path) > self.max_bytes:
            return cot
        from opentakserver.federation.proto import fig_pb2

        with open(path, "rb") as source:
            data = source.read()
        program = "create resource\n" + resource_json(info["sha256"], info["filename"], len(data), info["sender_uid"]) + ";"
        tag = FILESHARE.search(cot).group(0)
        info = dict(info, size=len(data), name=_attr(tag, "name"))
        announce = "announce package\n" + announce_json(info, _attr(tag, "senderCallsign"), destinations(cot)) + ";"
        groups = list(federate.get("outbound_groups") or [])
        try:
            q = self.rol_queue(federate["id"])
            q.put(fig_pb2.ROL(program=program, payload=[fig_pb2.BinaryBlob(data=data)], federateGroups=groups), timeout=5)
            q.put(fig_pb2.ROL(program=announce, federateGroups=groups), timeout=5)
        except queue.Full:
            self.logger.warning(f"ROL queue to '{federate['name']}' full: file {info['sha256'][:12]} not sent")
            return cot
        self.logger.info(f"Data package {info['sha256'][:12]} sent to '{federate['name']}' as ROL")
        return None

    def rol_inbound(self, rol, federate: dict):
        """A ROL message from a peer: files ("create resource" with content) become data packages."""
        parsed = parse_rol(rol.program)
        if not parsed:
            self.logger.warning(f"PortableTAK: ROL from '{federate['name']}' not understood: {(rol.program or '')[:120]!r}")
            return
        operation, resource, params = parsed
        self.logger.info(f"PortableTAK: ROL from '{federate['name']}': {operation} {resource} "
                         f"{params.get('changeType') or ''} {params.get('missionName') or params.get('name') or ''}")
        if self.missions is not None and self.missions.inbound(operation, resource, params, federate):
            return
        if operation == "announce" and resource == "package":
            cot = announce_cot(params, self.port)
            if cot and federate.get("enabled") and federate.get("inbound_groups"):
                # The parser stores CoT "from this server" (no sender device) for our node id.
                self.hold({"uid": self.config.get("OTS_NODE_ID"), "cot": cot, "user_id": None,
                           "groups": federate.get("inbound_groups"), "federate": federate["name"]}, federate)
            return
        if operation != "create" or resource != "resource":
            self.logger.debug(f"ROL '{operation} {resource}' from '{federate['name']}' not handled")
            return
        if not federate.get("enabled") or not federate.get("inbound_groups"):
            return
        if not rol.payload:
            return
        keywords = next((v for k, v in params.items() if k.lower() == "keywords"), None) or []
        if "ARCHIVED_MISSION" in keywords:
            return                                  # TAK Server's copy of a deleted mission, not a package
        data = rol.payload[0].data
        sha256 = hashlib.sha256(data).hexdigest()
        stated = str(params.get("hash") or "").lower()
        if stated and stated != sha256:
            self.logger.warning(f"ROL file from '{federate['name']}' refused: SHA-256 does not match")
            return
        name = params.get("filename") or params.get("name") or sha256 + ".zip"
        if self.have(sha256) or self.store(sha256, name, [data], params.get("creatorUid"), federate["name"]):
            self._work.put(("release", federate, {"sha256": sha256}))
            if self.missions is not None:
                self.missions.resource_arrived(sha256, params)

    # ---------------------------------------------------------------- outbound
    def outbound(self, federate: dict, cot: str):
        """A file-share CoT just went to a federate: remember the offer, push if we dialed."""
        info = fileshare_info(cot)
        if not info or int(federate.get("protocol_version") or 1) != 2:
            return
        if federate["id"] in self.rol_peers:
            return                                  # sent as ROL before the CoT
        with self._lock:
            self.offered.setdefault(federate["id"], set()).add(info["sha256"])
            dialed = federate["id"] in self.channels
        if dialed:
            self._work.put(("push", federate, info))

    # ---------------------------------------------------------------- inbound
    def hold(self, envelope: dict, federate: dict) -> bool:
        """Called for every inbound CoT. True when this relay took it (file share on v2)."""
        info = fileshare_info(envelope.get("cot") or "")
        if not info or int(federate.get("protocol_version") or 1) != 2:
            return False
        with self._lock:
            self.pending.setdefault(info["sha256"], []).append((envelope, federate, time.monotonic()))
            dialed = federate["id"] in self.channels
        self.logger.info(f"File share {info['sha256'][:12]} from '{federate['name']}' held until its file is here")
        self._work.put(("check", federate, info))
        if dialed:
            self._work.put(("pull", federate, info))
        return True

    def _release(self, sha256):
        with self._lock:
            waiting = self.pending.pop(sha256, [])
        if not waiting:
            return
        if self._bridge is None:
            self._bridge = self.bridge_factory()
        for envelope, federate, _ in waiting:
            envelope = dict(envelope, cot=rewrite_sender_url(envelope["cot"], sha256, self.port))
            self._bridge.publish_inbound(envelope)
            self.logger.info(f"Released file share {sha256[:12]} from '{federate['name']}'")

    # ---------------------------------------------------------------- worker
    def _worker(self):
        while True:
            try:
                job, federate, info = self._work.get(timeout=30)
            except queue.Empty:
                self._expire()
                continue
            try:
                if job == "release":
                    self._release(info["sha256"])
                elif job == "check" and self.have(info["sha256"]):
                    self._release(info["sha256"])
                elif job == "pull" and not self.have(info["sha256"]):
                    self._pull(federate, info)
                elif job == "push":
                    self._push(federate, info, info.get("attempt", 1))
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"Federation file {job} for {info['sha256'][:12]} failed: {e}")
                self.logger.debug(traceback.format_exc())
                try:
                    self._bridge = None
                except Exception:  # noqa: BLE001
                    pass

    def _expire(self):
        now = time.monotonic()
        with self._lock:
            for sha256 in list(self.pending):
                kept = [p for p in self.pending[sha256] if now - p[2] < PENDING_SECONDS]
                if kept:
                    self.pending[sha256] = kept
                else:
                    del self.pending[sha256]
                    self.logger.warning(f"Federated file share {sha256[:12]} dropped: the file never arrived")

    def _pull(self, federate, info):
        channel = self.channels.get(federate["id"])
        if channel is None:
            return
        if federate["id"] in self.rol_peers:
            return                                  # a ROL peer sends its files itself
        call = channel.unary_stream("/%s/Get" % SERVICE, request_serializer=_identity, response_deserializer=_identity)
        stream = call(json.dumps({"sha256": info["sha256"]}).encode(), timeout=600)
        try:
            meta = json.loads(next(stream))
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                self.mark_rol_peer(federate)        # an official TAK Server: files come as ROL
                return
            raise
        if not meta.get("ok"):
            raise RuntimeError(meta.get("error") or "refused")
        if self.store(info["sha256"], info["filename"], stream, info["sender_uid"], federate["name"]):
            self._release(info["sha256"])

    def _push(self, federate, info, attempt=1):
        channel = self.channels.get(federate["id"])
        path = self._upload_path(info["sha256"])
        if channel is None or not os.path.exists(path):
            return

        def chunks():
            yield json.dumps({"sha256": info["sha256"], "filename": info["filename"],
                              "sender_uid": info["sender_uid"]}).encode()
            with open(path, "rb") as source:
                while True:
                    data = source.read(CHUNK)
                    if not data:
                        return
                    yield data

        call = channel.stream_unary("/%s/Put" % SERVICE, request_serializer=_identity, response_deserializer=_identity)
        try:
            reply = json.loads(call(chunks(), timeout=600))
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                self.mark_rol_peer(federate)        # later file shares go as ROL
                return
            raise
        if reply.get("ok"):
            self.logger.info(f"Data package {info['sha256'][:12]} sent to '{federate['name']}'")
        elif reply.get("error") == "not_expected" and attempt < 4:
            threading.Timer(5 * attempt, lambda: self._work.put(("push", federate, info))).start()
            info["attempt"] = attempt + 1
        else:
            self.logger.warning(f"'{federate['name']}' did not take file {info['sha256'][:12]}: {reply.get('error')}")

    # ---------------------------------------------------------------- service
    def generic_handler(self, directory):
        """gRPC handler for the listener: peers pull offered files and push file shares."""
        from opentakserver.federation import truststore

        def peer_federate(context):
            certs = (context.auth_context() or {}).get("x509_pem_cert") or []
            if not certs:
                return None
            fingerprint = truststore.fingerprint_from_pem(certs[0])
            return directory.federate_by_fingerprint(fingerprint)

        def get(request, context):
            federate = peer_federate(context)
            try:
                sha256 = json.loads(request).get("sha256", "")
            except ValueError:
                sha256 = ""
            with self._lock:
                offered = federate is not None and sha256 in self.offered.get(federate["id"], set())
            if not federate or not federate.get("enabled") or not federate.get("outbound_groups") or not offered:
                yield json.dumps({"ok": False, "error": "not_offered"}).encode()
                return
            path = self._upload_path(sha256)
            if not os.path.exists(path):
                yield json.dumps({"ok": False, "error": "missing"}).encode()
                return
            yield json.dumps({"ok": True, "size": os.path.getsize(path)}).encode()
            with open(path, "rb") as source:
                while True:
                    data = source.read(CHUNK)
                    if not data:
                        return
                    yield data

        def put(request_iterator, context):
            federate = peer_federate(context)
            try:
                meta = json.loads(next(request_iterator))
            except (StopIteration, ValueError):
                return json.dumps({"ok": False, "error": "bad_request"}).encode()
            sha256 = str(meta.get("sha256") or "").lower()
            # The file can overtake its file-share CoT on the other stream: wait briefly.
            expected = False
            for _ in range(30):
                with self._lock:
                    expected = any(p[1]["id"] == (federate or {}).get("id") for p in self.pending.get(sha256, []))
                if expected or not federate:
                    break
                time.sleep(0.5)
            if not federate or not federate.get("enabled") or not federate.get("inbound_groups") or not expected:
                return json.dumps({"ok": False, "error": "not_expected"}).encode()
            if self.have(sha256) or self.store(sha256, meta.get("filename"), request_iterator,
                                               meta.get("sender_uid"), federate["name"]):
                self._work.put(("release", federate, {"sha256": sha256}))   # publish on the worker thread
                return json.dumps({"ok": True}).encode()
            return json.dumps({"ok": False, "error": "refused"}).encode()

        return grpc.method_handlers_generic_handler(SERVICE, {
            "Get": grpc.unary_stream_rpc_method_handler(get, request_deserializer=_identity, response_serializer=_identity),
            "Put": grpc.stream_unary_rpc_method_handler(put, request_deserializer=_identity, response_serializer=_identity),
        })


# Set by the federation manager at startup; None when the relay is not running.
RELAY: FileRelay | None = None
