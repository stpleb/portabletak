import base64
import contextlib
import io
import json
import os
import re
import shutil
import socket
import time
import traceback

startup_output = io.StringIO()

with contextlib.redirect_stdout(startup_output), contextlib.redirect_stderr(startup_output):
    from flask_security import admin_change_password, hash_password
    from opentakserver.app import create_app
    from opentakserver.extensions import db, logger
    from opentakserver.UsernameValidator import UsernameValidator
    from opentakserver.certificate_authority import CertificateAuthority
    from opentakserver.models.Token import Token
    import qrcode

with contextlib.redirect_stdout(startup_output), contextlib.redirect_stderr(startup_output):
    app = create_app()


HOST = "127.0.0.1"
PORT = 18090


def fail(message):
    return {
        "ok": False,
        "error": message,
    }


def server_name():
    """The PortableTAK server's name (written by PortableTAK at every start)."""
    try:
        with open("/asgard/opentak/data/portabletak-clients.json") as stream:
            name = str(json.load(stream).get("name") or "")
    except (OSError, ValueError, AttributeError):
        name = ""
    return name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}", name) else "PortableTAK"


def handle_request(request):
    with app.app_context():
        try:
            if app.config.get("OTS_ENABLE_LDAP"):
                return fail("ldap_enabled")

            action = request.get("action", "")

            if action == "list":
                ds = app.security.datastore

                users = (
                    db.session.query(ds.user_model)
                    .order_by(ds.user_model.username)
                    .all()
                )

                result = []

                for user in users:
                    result.append({
                        "username": user.username,
                        "active": bool(user.active),
                        "email": user.email,
                        "roles": [
                            role.name
                            for role in user.roles
                        ],
                        "login_count": user.login_count or 0,
                        "last_login_at": (
                            user.last_login_at.isoformat()
                            if user.last_login_at
                            else None
                        ),
                        "eud_count": len(user.euds),
                    })

                return {
                    "ok": True,
                    "users": result,
                }

            # Drone video feeds (Video Feeds card): the feed is listed for TAK clients
            # like a stream OpenTAKServer created itself when someone published it.
            if action in ("video_feed_add", "video_feed_remove"):
                from opentakserver.models.VideoStream import VideoStream

                name = str(request.get("name", ""))
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", name):
                    return fail("invalid_feed_name")

                video = db.session.query(VideoStream).where(VideoStream.path == name).first()

                if action == "video_feed_remove":
                    if video:
                        db.session.delete(video)
                        db.session.commit()
                    return {"ok": True, "result": "video_feed_removed", "name": name}

                if not video:
                    from opentakserver.forms.MediaMTXPathConfig import MediaMTXPathConfig
                    import uuid

                    with app.test_request_context("/"):
                        settings = MediaMTXPathConfig(None).serialize()
                    settings["sourceOnDemand"] = False
                    video = VideoStream()
                    video.path = name
                    video.alias = name
                    video.uid = str(uuid.uuid4())
                    video.protocol = "rtsp"
                    video.port = 8554
                    video.rover_port = -1
                    video.ignore_embedded_klv = False
                    video.buffer_time = None
                    video.network_timeout = 10000
                    video.rtsp_reliable = 1
                    video.mediamtx_settings = json.dumps(settings)
                    video.generate_xml(str(request.get("host") or "localhost"))
                    db.session.add(video)
                    db.session.commit()
                return {"ok": True, "result": "video_feed_added", "name": name}

            if action.startswith("federation_"):
                import federation_actions
                return federation_actions.handle(app, db, request)

            if action.startswith("channels_"):
                import channels_actions
                return channels_actions.handle(app, db, request)

            if action.startswith("map_"):
                import map_actions
                return map_actions.handle(app, db, request)

            if action.startswith("plugins_"):
                import plugins_actions
                return plugins_actions.handle(app, db, request)

            if action.startswith("dp_"):
                import datapackages_actions
                return datapackages_actions.handle(app, db, request)

            if action.startswith("meshtastic_"):
                import meshtastic_actions
                return meshtastic_actions.handle(app, db, request)

            username = str(
                request.get("username", "")
            ).strip()

            if not username:
                return fail("username_required")

            validated = UsernameValidator(app).validate(
                username
            )

            if validated[0]:
                return fail(str(validated[0]))

            if action == "create":
                password = str(
                    request.get("password", "")
                )

                if not password:
                    return fail("password_required")

                minimum = int(
                    app.config.get(
                        "SECURITY_PASSWORD_LENGTH_MIN",
                        8,
                    )
                )

                if len(password) < minimum:
                    return fail(
                        "password_too_short:"
                        + str(minimum)
                    )

                # OpenTAKServer's certificate enrollment splits "user:password" at
                # every colon and HTML-escapes & < >, so those four cannot work.
                if any(c in password for c in ":&<>"):
                    return fail(
                        "password_contains_disallowed_character"
                    )

                if app.security.datastore.find_user(
                    username=username
                ):
                    return fail("user_already_exists")

                app.security.datastore.create_user(
                    username=username,
                    password=hash_password(password),
                    roles=["user"],
                )

                db.session.commit()

                return {
                    "ok": True,
                    "result": "user_created",
                    "username": username,
                }

            if action == "itak_onboarding_qr":
                user = app.security.datastore.find_user(
                    username=username
                )

                if not user:
                    return fail("user_not_found")

                service_ip = app.config.get("OTS_SERVER_ADDRESS") or "10.41.250.10"
                network_conf = "/asgard/config/tak-network.conf"

                if os.path.isfile(network_conf):
                    with open(network_conf, "r") as f:
                        for line in f:
                            if line.startswith("ASGARD_SERVICE_IP="):
                                service_ip = (
                                    line.split("=", 1)[1]
                                    .strip()
                                    .strip('"')
                                )

                # Remote QR: the API passes the Tailscale address (already validated).
                service_ip = request.get("host") or service_ip

                ssl_port = str(
                    app.config.get(
                        "OTS_SSL_STREAMING_PORT",
                        8089,
                    )
                )

                # iTAK shows the first field as the server's name.
                label = server_name()
                try:
                    import ipaddress
                    remote = ipaddress.ip_address(service_ip) in ipaddress.ip_network("100.64.0.0/10")
                except ValueError:
                    remote = str(service_ip).endswith(".ts.net")
                if remote:
                    label += " (Tailscale)"

                qr_string = (
                    label
                    + ","
                    + service_ip
                    + ","
                    + ssl_port
                    + ",SSL"
                )

                image = qrcode.make(qr_string)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")

                return {
                    "ok": True,
                    "result": "itak_onboarding_qr",
                    "username": username,
                    "qr_string": qr_string,
                    "qr_png_base64": base64.b64encode(
                        buffer.getvalue()
                    ).decode("ascii"),
                }

            if action == "atak_onboarding_qr":
                user = app.security.datastore.find_user(
                    username=username
                )

                if not user:
                    return fail("user_not_found")

                token = (
                    db.session.query(Token)
                    .filter_by(username=username)
                    .first()
                )

                if not token:
                    token = Token()
                    token.username = username

                token.creation = int(time.time())
                token.expiration = int(time.time()) + 900
                token.not_before = None
                # ATAK 5.x enrolls, connects, then enrolls again; a single-use token fails
                # the second call and ATAK drops the connection. Allow three uses in 15 min.
                token.max_uses = 3
                token.total_uses = 0
                token.disabled = False
                token.hash_token()

                db.session.add(token)
                db.session.commit()

                service_ip = app.config.get("OTS_SERVER_ADDRESS") or "10.41.250.10"
                network_conf = "/asgard/config/tak-network.conf"

                if os.path.isfile(network_conf):
                    with open(network_conf, "r") as f:
                        for line in f:
                            if line.startswith("ASGARD_SERVICE_IP="):
                                service_ip = (
                                    line.split("=", 1)[1]
                                    .strip()
                                    .strip('"')
                                )

                # Remote QR: the API passes the Tailscale address (already validated).
                service_ip = request.get("host") or service_ip

                qr_string = (
                    "tak://com.atakmap.app/enroll"
                    "?host=" + service_ip
                    + "&username=" + username
                    + "&token=" + token.generate_token()
                )

                image = qrcode.make(qr_string)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")

                return {
                    "ok": True,
                    "result": "atak_onboarding_qr",
                    "username": username,
                    "expires_in": 900,
                    "max_uses": 3,
                    "qr_string": qr_string,
                    "qr_png_base64": base64.b64encode(
                        buffer.getvalue()
                    ).decode("ascii"),
                }

            if action == "generate_package":
                user = app.security.datastore.find_user(
                    username=username
                )

                if not user:
                    return fail("user_not_found")

                cert_root = app.config.get("OTS_CA_FOLDER")
                cert_dir = os.path.join(
                    cert_root,
                    "certs",
                    username,
                )

                backup_root = os.path.join(
                    "/asgard/portabletak/backups",
                    "certificate-regeneration",
                )

                os.makedirs(
                    backup_root,
                    exist_ok=True,
                )

                backup_dir = None

                if os.path.isdir(cert_dir):
                    stamp = time.strftime(
                        "%Y%m%d-%H%M%S"
                    )

                    backup_dir = os.path.join(
                        backup_root,
                        username + "-" + stamp,
                    )

                    shutil.copytree(
                        cert_dir,
                        backup_dir,
                    )

                service_ip = app.config.get("OTS_SERVER_ADDRESS") or "10.41.250.10"
                network_conf = "/asgard/config/tak-network.conf"

                if os.path.isfile(network_conf):
                    with open(network_conf, "r") as f:
                        for line in f:
                            if line.startswith(
                                "ASGARD_SERVICE_IP="
                            ):
                                service_ip = (
                                    line.split("=", 1)[1]
                                    .strip()
                                    .strip('"')
                                )

                marti_port = int(
                    app.config.get(
                        "OTS_MARTI_HTTPS_PORT",
                        8443,
                    )
                )

                try:
                    with app.test_request_context(
                        "/",
                        base_url=(
                            "https://"
                            + service_ip
                            + ":"
                            + str(marti_port)
                            + "/"
                        ),
                    ):
                        ca = CertificateAuthority(
                            logger,
                            app,
                        )

                        filenames = ca.issue_certificate(
                            username,
                            False,
                        )

                except Exception:
                    if (
                        backup_dir
                        and os.path.isdir(backup_dir)
                    ):
                        if os.path.isdir(cert_dir):
                            shutil.rmtree(cert_dir)

                        shutil.copytree(
                            backup_dir,
                            cert_dir,
                        )

                    raise

                return {
                    "ok": True,
                    "result": "packages_generated",
                    "username": username,
                    "files": filenames,
                    "backup": backup_dir,
                }

            if action == "reset_password":
                password = str(
                    request.get("password", "")
                )

                if not password:
                    return fail("password_required")

                minimum = int(
                    app.config.get(
                        "SECURITY_PASSWORD_LENGTH_MIN",
                        8,
                    )
                )

                if len(password) < minimum:
                    return fail(
                        "password_too_short:"
                        + str(minimum)
                    )

                # OpenTAKServer's certificate enrollment splits "user:password" at
                # every colon and HTML-escapes & < >, so those four cannot work.
                if any(c in password for c in ":&<>"):
                    return fail(
                        "password_contains_disallowed_character"
                    )

                user = app.security.datastore.find_user(
                    username=username
                )

                if not user:
                    return fail("user_not_found")

                admin_change_password(
                    user,
                    password,
                    False,
                )

                db.session.commit()

                return {
                    "ok": True,
                    "result": "password_reset",
                    "username": username,
                }

            if action == "deactivate":
                user = app.security.datastore.find_user(
                    username=username
                )

                if not user:
                    return fail("user_not_found")

                if "administrator" in [
                    role.name
                    for role in user.roles
                ]:
                    return fail(
                        "administrator_protected"
                    )

                changed = (
                    app.security.datastore
                    .deactivate_user(user)
                )

                db.session.commit()

                return {
                    "ok": True,
                    "result": (
                        "user_deactivated"
                        if changed
                        else "user_already_deactivated"
                    ),
                    "username": username,
                }

            if action == "activate":
                user = app.security.datastore.find_user(
                    username=username
                )

                if not user:
                    return fail("user_not_found")

                changed = (
                    app.security.datastore
                    .activate_user(user)
                )

                db.session.commit()

                return {
                    "ok": True,
                    "result": (
                        "user_activated"
                        if changed
                        else "user_already_active"
                    ),
                    "username": username,
                }

            if action == "delete":
                user = app.security.datastore.find_user(
                    username=username
                )

                if not user:
                    return fail("user_not_found")

                if "administrator" in [
                    role.name
                    for role in user.roles
                ]:
                    return fail(
                        "administrator_protected"
                    )

                app.security.datastore.delete_user(user)
                db.session.commit()

                return {
                    "ok": True,
                    "result": "user_deleted",
                    "username": username,
                }

            return fail("invalid_action")

        except Exception:
            db.session.rollback()

            return {
                "ok": False,
                "error": "internal_error",
                "detail": traceback.format_exc(),
            }

        finally:
            db.session.remove()


server = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM,
)

server.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_REUSEADDR,
    1,
)

server.bind((HOST, PORT))
server.listen(8)

while True:
    conn, address = server.accept()

    try:
        chunks = []

        while True:
            data = conn.recv(65536)

            if not data:
                break

            chunks.append(data)

            if b"\n" in data:
                break

        raw = b"".join(chunks).decode(
            "utf-8"
        ).strip()

        request = json.loads(raw)
        response = handle_request(request)

    except Exception:
        response = {
            "ok": False,
            "error": "invalid_request",
        }

    payload = (
        json.dumps(response)
        + "\n"
    ).encode("utf-8")

    conn.sendall(payload)
    conn.close()
