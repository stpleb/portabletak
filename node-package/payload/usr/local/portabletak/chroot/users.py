import contextlib
import io
import json
import sys

startup_output = io.StringIO()

with contextlib.redirect_stdout(startup_output), contextlib.redirect_stderr(startup_output):
    from flask_security import admin_change_password, hash_password
    from opentakserver.app import create_app
    from opentakserver.extensions import db
    from opentakserver.UsernameValidator import UsernameValidator


def fail(message, code=1):
    print(json.dumps({
        "ok": False,
        "error": message,
    }))
    raise SystemExit(code)


def read_request():
    raw = sys.stdin.read()

    if not raw.strip():
        return {}

    try:
        return json.loads(raw)
    except Exception:
        fail("invalid_json", 2)


with contextlib.redirect_stdout(startup_output), contextlib.redirect_stderr(startup_output):
    app = create_app()

with app.app_context():
    if app.config.get("OTS_ENABLE_LDAP"):
        fail("ldap_enabled", 3)

    request = read_request()
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
                "roles": [role.name for role in user.roles],
                "login_count": user.login_count or 0,
                "last_login_at": (
                    user.last_login_at.isoformat()
                    if user.last_login_at
                    else None
                ),
                "eud_count": len(user.euds),
            })

        print(json.dumps({
            "ok": True,
            "users": result,
        }))

        raise SystemExit(0)

    username = str(request.get("username", "")).strip()

    if not username:
        fail("username_required", 4)

    validated = UsernameValidator(app).validate(username)

    if validated[0]:
        fail(str(validated[0]), 5)

    if action == "create":
        password = str(request.get("password", ""))

        if not password:
            fail("password_required", 6)

        minimum = int(
            app.config.get(
                "SECURITY_PASSWORD_LENGTH_MIN",
                8,
            )
        )

        if len(password) < minimum:
            fail(
                "password_too_short:" + str(minimum),
                7,
            )

        if "@" in password or ":" in password:
            fail(
                "password_contains_disallowed_character",
                8,
            )

        if app.security.datastore.find_user(
            username=username
        ):
            fail("user_already_exists", 9)

        role = app.security.datastore.find_role("user")

        if not role:
            fail("user_role_missing", 10)

        app.security.datastore.create_user(
            username=username,
            password=hash_password(password),
            roles=["user"],
        )

        db.session.commit()

        print(json.dumps({
            "ok": True,
            "result": "user_created",
            "username": username,
        }))

        raise SystemExit(0)

    if action == "deactivate":
        user = app.security.datastore.find_user(
            username=username
        )

        if not user:
            fail("user_not_found", 16)

        if "administrator" in [
            role.name for role in user.roles
        ]:
            fail("administrator_protected", 17)

        changed = app.security.datastore.deactivate_user(
            user
        )

        db.session.commit()

        print(json.dumps({
            "ok": True,
            "result": (
                "user_deactivated"
                if changed
                else "user_already_deactivated"
            ),
            "username": username,
        }))

        raise SystemExit(0)

    if action == "activate":
        user = app.security.datastore.find_user(
            username=username
        )

        if not user:
            fail("user_not_found", 18)

        changed = app.security.datastore.activate_user(
            user
        )

        db.session.commit()

        print(json.dumps({
            "ok": True,
            "result": (
                "user_activated"
                if changed
                else "user_already_active"
            ),
            "username": username,
        }))

        raise SystemExit(0)

    if action == "delete":
        user = app.security.datastore.find_user(
            username=username
        )

        if not user:
            fail("user_not_found", 19)

        if "administrator" in [
            role.name for role in user.roles
        ]:
            fail("administrator_protected", 20)

        app.security.datastore.delete_user(user)
        db.session.commit()

        print(json.dumps({
            "ok": True,
            "result": "user_deleted",
            "username": username,
        }))

        raise SystemExit(0)

    if action == "reset_password":
        password = str(request.get("password", ""))

        if not password:
            fail("password_required", 11)

        minimum = int(
            app.config.get(
                "SECURITY_PASSWORD_LENGTH_MIN",
                8,
            )
        )

        if len(password) < minimum:
            fail(
                "password_too_short:" + str(minimum),
                12,
            )

        if "@" in password or ":" in password:
            fail(
                "password_contains_disallowed_character",
                13,
            )

        user = app.security.datastore.find_user(
            username=username
        )

        if not user:
            fail("user_not_found", 14)

        admin_change_password(
            user,
            password,
            False,
        )

        db.session.commit()

        print(json.dumps({
            "ok": True,
            "result": "password_reset",
            "username": username,
        }))

        raise SystemExit(0)

    fail("invalid_action", 15)
