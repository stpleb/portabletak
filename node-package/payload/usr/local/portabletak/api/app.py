from pathlib import Path
import os
import re
import json
import subprocess
import threading

from fastapi import FastAPI, HTTPException, Body, Query, Request
from fastapi.responses import FileResponse, Response
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import backup_catalog
import host_profile

app = FastAPI(
    title="PortableTAK Server",
    version="0.1.0",
)

BIN = Path("/usr/local/portabletak/bin")

COMMANDS = {
    "status": BIN / "portabletak-status",
    "mount": BIN / "portabletak-mount",
    "start": BIN / "portabletak-start",
    "stop": BIN / "portabletak-stop",
    "safe_remove": BIN / "portabletak-safe-remove",
    "network": Path("/usr/local/portabletak/adapters") / host_profile.read_profile()["PT_PROFILE"] / "network",
    "tailscale": BIN / "portabletak-tailscale",
    "health": BIN / "portabletak-health",
    "users": BIN / "portabletak-users",
    "client_info": BIN / "portabletak-client-info",
    "backup": BIN / "portabletak-backup",
    "restore": BIN / "portabletak-restore",
    "factory_reset": BIN / "portabletak-factory-reset",
    "federation": BIN / "portabletak-federation",
}

action_lock = threading.Lock()


def run_command_args(name: str, args, timeout: int = 300):
    command = COMMANDS[name]

    if not command.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"controller_missing:{command}",
        )

    child_env = os.environ.copy()
    child_env.pop("LD_PRELOAD", None)

    try:
        result = subprocess.run(
            [str(command), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"{name}_timeout",
        )

    lines = []

    for source in (result.stdout, result.stderr):
        if source:
            lines.extend(
                line.strip()
                for line in source.splitlines()
                if line.strip()
            )

    return {
        "return_code": result.returncode,
        "output": "\n".join(lines),
    }


def run_command(name: str, timeout: int = 120):
    command = COMMANDS[name]

    if not command.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"controller_missing:{command}",
        )

    child_env = os.environ.copy()
    child_env.pop("LD_PRELOAD", None)

    try:
        result = subprocess.run(
            [str(command)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"{name}_timeout",
        )

    lines = []

    for source in (result.stdout, result.stderr):
        if source:
            lines.extend(
                line.strip()
                for line in source.splitlines()
                if line.strip()
            )

    return {
        "action": name,
        "return_code": result.returncode,
        "output": lines,
    }


def parse_status(lines):
    data = {}

    # run_command_args joins its output into one string; run_command does not.
    if isinstance(lines, str):
        lines = lines.splitlines()

    for line in lines:
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        data[key] = value

    return data


@app.get("/")
def root():
    # Version each script link by its content so browsers never run a stale
    # copy after an update, and never cache the page itself.
    import hashlib
    from fastapi.responses import HTMLResponse
    web = Path("/usr/local/portabletak/web")
    html = (web / "index.html").read_text()
    for name in ("auth.js", "remote-access.js", "video-feeds.js", "channels.js", "federation.js", "factory-reset.js"):
        try:
            tag = hashlib.sha256((web / name).read_bytes()).hexdigest()[:12]
        except OSError:
            continue
        html = html.replace(f'src="/{name}"', f'src="/{name}?v={tag}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/status")
def status():
    result = run_command("status", timeout=15)

    if result["return_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=result,
        )

    return {
        "ok": True,
        "status": parse_status(result["output"]),
        "host_kind": host_kind(),
        "address_gone": address_gone(),
        "opentakserver_version": opentakserver_version(),
        "portabletak_version": portabletak_version(),
    }


def portabletak_version():
    try:
        return open("/usr/local/portabletak/VERSION").read().strip() or None
    except OSError:
        return None


def opentakserver_version():
    """Installed OpenTAKServer version on the drive (x86 computers use the amd64 copy)."""
    import glob
    import platform
    venvs = ["/mnt/asgard/opentak/venv"]
    if platform.machine() in ("x86_64", "AMD64"):
        venvs.insert(0, "/mnt/asgard/arch/amd64/venv")
    for venv in venvs:
        for info in glob.glob(venv + "/lib/python3*/site-packages/opentakserver-*.dist-info"):
            return info.rsplit("/", 1)[1][len("opentakserver-"):-len(".dist-info")]
    return None


def address_gone():
    """True when the address phones use (taken when PortableTAK started on this
    computer) is no longer on any network adapter: the computer changed networks."""
    try:
        import server_addresses
        advertise = server_addresses.current("/mnt/asgard").get("advertise")
        # Only WSL sees the computer's own adapters (mirrored networking); the Mac VM does not.
        if not advertise or host_kind() != "windows":
            return False
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
        return (" " + advertise + "/") not in out
    except Exception:
        return False


def host_kind():
    """"windows" (WSL), "mac" (the PortableTAK VM), else "node". On a computer the
    launcher attached the drive, so only it can start, stop and release the drive."""
    try:
        if "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower():
            return "windows"
        if "pt.advertise=" in Path("/proc/cmdline").read_text():
            return "mac"
    except OSError:
        pass
    return "node"

def run_users(payload):
    command = COMMANDS["users"]

    child_env = os.environ.copy()
    child_env.pop("LD_PRELOAD", None)

    try:
        result = subprocess.run(
            [str(command)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=180,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="users_command_timeout",
        )

    raw = result.stdout.strip()

    try:
        parsed = json.loads(raw)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "invalid_users_response",
                "return_code": result.returncode,
                "output": raw,
                "stderr": result.stderr.strip(),
            },
        )

    if result.returncode != 0 or not parsed.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=parsed,
        )

    return parsed


@app.get("/api/users")
def users_list():
    return run_users({
        "action": "list",
    })


@app.post("/api/users/create")
def users_create(payload: dict = Body(...)):
    # Usernames are stored in lowercase (phones capitalize the first letter);
    # sign-in and enrollment ignore case as well (opentakserver_launch.py).
    username = str(payload.get("username", "")).strip().lower()
    password = str(payload.get("password", ""))

    if not username or not password:
        raise HTTPException(
            status_code=400,
            detail="username_and_password_required",
        )

    return run_users({
        "action": "create",
        "username": username,
        "password": password,
    })


@app.post("/api/users/reset-password")
def users_reset_password(payload: dict = Body(...)):
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))

    if not username or not password:
        raise HTTPException(
            status_code=400,
            detail="username_and_password_required",
        )

    return run_users({
        "action": "reset_password",
        "username": username,
        "password": password,
    })


@app.post("/api/users/deactivate")
def users_deactivate(payload: dict = Body(...)):
    username = str(payload.get("username", "")).strip()

    if not username:
        raise HTTPException(
            status_code=400,
            detail="username_required",
        )

    return run_users({
        "action": "deactivate",
        "username": username,
    })


@app.post("/api/users/activate")
def users_activate(payload: dict = Body(...)):
    username = str(payload.get("username", "")).strip()

    if not username:
        raise HTTPException(
            status_code=400,
            detail="username_required",
        )

    return run_users({
        "action": "activate",
        "username": username,
    })


@app.post("/api/users/delete")
def users_delete(payload: dict = Body(...)):
    username = str(payload.get("username", "")).strip()

    if not username:
        raise HTTPException(
            status_code=400,
            detail="username_required",
        )

    return run_users({
        "action": "delete",
        "username": username,
    })


def qr_host(address, opened_at=None):
    """None: the normal (local) address. "remote": the Tailscale address, so one
    server entry works on Wi-Fi and on cellular while the phone's Tailscale is on.
    With no choice made, the QR uses the address the dashboard was opened with when
    it is one of this server's addresses (Tailscale, or another network this computer
    is on): the phone can reach the server there, and a certificate is named for it."""
    if address is None:
        host = (opened_at or "").rsplit(":", 1)[0].strip("[]")
        try:
            import client_packages
            import server_addresses
            record = server_addresses.current("/mnt/asgard")
            # Any address of this server other than the default one (the Tailscale
            # address, or this computer's other network): the phone reached it there.
            if host and host != record.get("advertise") and host in (record.get("addresses") or []):
                return host
        except Exception:
            pass
        return None
    import client_packages
    import server_addresses
    record = server_addresses.current("/mnt/asgard")
    if address == "remote":
        host = client_packages.remote_address(record)
        if host is None:
            raise HTTPException(status_code=404, detail="remote_access_not_set_up")
        return host
    # The network the phone is on, picked in the QR window: any of this server's addresses.
    if address in (record.get("addresses") or []):
        return None if address == record.get("advertise") else address
    raise HTTPException(status_code=400, detail="invalid_address")


@app.get("/api/client/qr-addresses")
def qr_addresses(request: Request):
    """The networks a phone can enroll on, for the QR window's "Phone is on" picker.
    Default: the address the dashboard was opened with, if it is one of them."""
    import client_packages
    import server_addresses
    record = server_addresses.current("/mnt/asgard")
    items = []
    for description, address, _ in client_packages.connections(record):
        if address.startswith("10.0.2.") or address.endswith(".ts.net"):
            continue
        label = description[description.rfind("(") + 1:].rstrip(")") if "(" in description else address
        if label.startswith("LAN "):
            label = "LAN"
        items.append({"address": address, "label": label})
    opened = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    default = opened if any(i["address"] == opened for i in items) else record.get("advertise")
    return {"ok": True, "addresses": items, "default": default}


@app.post("/api/client/itak-qr/{username}")
def itak_qr(username: str, request: Request, address: str | None = Query(default=None)):
    if not username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="invalid_username",
        )

    result = run_users({
        "action": "itak_onboarding_qr",
        "username": username,
        "host": qr_host(address, request.headers.get("host")),
    })

    if not result.get("ok"):
        raise HTTPException(
            status_code=500,
            detail=result,
        )

    return result


@app.post("/api/client/atak-qr/{username}")
def atak_qr(username: str, request: Request, address: str | None = Query(default=None)):
    if not username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="invalid_username",
        )

    result = run_users({
        "action": "atak_onboarding_qr",
        "username": username,
        "host": qr_host(address, request.headers.get("host")),
    })

    if not result.get("ok"):
        raise HTTPException(
            status_code=500,
            detail=result,
        )

    return result


@app.get("/api/client/package/{username}/{client_type}")
def client_package(username: str, client_type: str, address: str | None = Query(default=None)):
    if not username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="invalid_username",
        )

    cert_dir = os.path.join(
        "/mnt/asgard/opentak/data/ca/certs",
        username,
    )

    if client_type == "atak":
        filename = f"{username}_CONFIG.zip"
    elif client_type == "itak":
        filename = f"{username}_CONFIG_iTAK.zip"
    elif client_type == "wintak":
        filename = f"{username}_CONFIG.zip"
    elif client_type == "tls":
        # Device TLS package (drones, radios): built from the user's ATAK package.
        filename = f"{username}_CONFIG.zip"
    else:
        raise HTTPException(
            status_code=400,
            detail="unsupported_client_type",
        )

    path = os.path.join(cert_dir, filename)

    if not os.path.isfile(path):
        generated = run_users({
            "action": "generate_package",
            "username": username,
        })

        if not generated.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=generated,
            )

    if not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail="client_package_not_available",
        )

    # One package, one connection per server address (Wi-Fi, mesh, Tailscale).
    # ?address=X gives a package with only that connection.
    try:
        import client_packages
        import server_addresses
        record = server_addresses.current("/mnt/asgard")
        if address == "remote":
            address = client_packages.remote_address(record)
            if address is None:
                raise HTTPException(status_code=404, detail="remote_access_not_set_up")
            filename = filename.replace(".zip", "_Tailscale.zip")
        with open(path, "rb") as stream:
            import host_adoption
            if client_type == "tls":
                data = client_packages.tls_package(stream.read(), record,
                                                   host_adoption.instance_name("/mnt/asgard"), username)
                filename = f"{username}_TLS.zip"
            else:
                data = client_packages.build(stream.read(), client_type, record, address,
                                             host_adoption.instance_name("/mnt/asgard"))
    except HTTPException:
        raise
    except ValueError as error:
        if address is not None or client_type == "tls":
            raise HTTPException(status_code=400, detail=str(error))
        data = None
    except Exception:
        if client_type == "tls":
            raise HTTPException(status_code=500, detail="tls_package_failed")
        data = None

    if data is None:
        return FileResponse(
            path,
            media_type="application/zip",
            filename=filename,
        )

    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/client/ios-trust-profile")
def client_ios_trust_profile():
    """iPhone: install once (Safari opens it as a profile), then turn on full trust.
    iTAK's QR sign-in only accepts a server iOS trusts."""
    import client_packages
    import host_adoption
    try:
        with open("/mnt/asgard/opentak/data/ca/ca.pem") as stream:
            data = client_packages.ios_trust_profile(stream.read(), host_adoption.instance_name("/mnt/asgard"))
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="ca_not_available")
    return Response(content=data, media_type="application/x-apple-aspen-config",
                    headers={"Content-Disposition": 'attachment; filename="PortableTAK-Trust.mobileconfig"'})


@app.get("/api/client/trust-package")
def client_trust_package():
    """ATAK package that only trusts this server's CA (import once, then QR enrollment works)."""
    import client_packages
    store = "/mnt/asgard/opentak/data/ca/truststore-root.p12"
    if not os.path.isfile(store):
        raise HTTPException(status_code=404, detail="truststore_not_available")
    try:
        with open("/mnt/asgard/opentak/data/config.yml") as stream:
            config = json.load(stream)
        password = config["PORTABLETAK_TRUSTSTORE_PASSWORD"]
        name = config.get("PORTABLETAK_INSTANCE_NAME") or "PortableTAK"
    except (OSError, ValueError, KeyError):
        raise HTTPException(status_code=500, detail="truststore_password_unavailable")
    with open(store, "rb") as stream:
        data = client_packages.trust_package(stream.read(), password, name)
    filename = "".join(c if c.isalnum() else "_" for c in name)[:40] + "_TRUST.zip"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/client/truststore")
def client_truststore():
    path = "/mnt/asgard/opentak/data/ca/truststore-root.p12"

    if not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail="truststore_not_available",
        )

    return FileResponse(
        path,
        media_type="application/x-pkcs12",
        filename="truststore-root.p12",
    )


@app.post("/api/backup")
def create_backup(payload: dict = Body(default=None)):
    note = (payload or {}).get("note") or None
    if not backup_catalog.valid_note(note):
        raise HTTPException(
            status_code=400,
            detail="invalid_backup_note",
        )

    result = run_command_args("backup", ["--note", note] if note else [], timeout=120)

    if result["return_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=result,
        )

    values = parse_status(result["output"])

    return {
        "ok": True,
        "backup": values,
    }


@app.post("/api/backup/verify/{name}")
def verify_backup(name: str):
    if (
        "/" in name
        or "\\" in name
        or not name.startswith("portabletak-")
        or not name.endswith(".tar.gz")
    ):
        raise HTTPException(
            status_code=400,
            detail="invalid_backup_name",
        )

    result = run_command_args(
        "restore",
        ["verify", name],
        timeout=120,
    )

    if result["return_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=result,
        )

    return {
        "ok": True,
        "result": parse_status(result["output"]),
    }


@app.post("/api/backup/restore/{name}")
def restore_backup(name: str):
    if (
        "/" in name
        or "\\" in name
        or not name.startswith("portabletak-")
        or not name.endswith(".tar.gz")
    ):
        raise HTTPException(
            status_code=400,
            detail="invalid_backup_name",
        )

    with action_lock:
        result = run_command_args(
            "restore",
            ["restore", name],
            timeout=600,
        )

    if result["return_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=result,
        )

    return {
        "ok": True,
        "result": parse_status(result["output"]),
    }


@app.get("/api/backups")
def list_backups():
    return backup_catalog.list_backups("/mnt/asgard/portabletak/backups/recovery")


@app.delete("/api/backup/{name}")
def delete_backup(name: str):
    if (
        "/" in name
        or "\\" in name
        or not name.startswith("portabletak-")
        or not name.endswith(".tar.gz")
    ):
        raise HTTPException(
            status_code=400,
            detail="invalid_backup_name",
        )

    path = os.path.join(
        "/mnt/asgard/portabletak/backups/recovery",
        name,
    )

    if not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail="backup_not_found",
        )

    try:
        os.unlink(path)
        if os.path.isfile(path + ".json"):
            os.unlink(path + ".json")
    except OSError as err:
        raise HTTPException(
            status_code=500,
            detail=f"backup_delete_failed:{err}",
        )

    return {
        "ok": True,
        "deleted": name,
    }


@app.get("/api/backup/download/{name}")
def download_backup(name: str):
    if (
        "/" in name
        or "\\" in name
        or not name.startswith("portabletak-")
        or not name.endswith(".tar.gz")
    ):
        raise HTTPException(
            status_code=400,
            detail="invalid_backup_name",
        )

    path = os.path.join(
        "/mnt/asgard/portabletak/backups/recovery",
        name,
    )

    if not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail="backup_not_found",
        )

    return FileResponse(
        path,
        media_type="application/gzip",
        filename=name,
    )


@app.get("/api/client-info")
def client_info():
    result = run_command("client_info", timeout=10)

    if result["return_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=result,
        )

    return {
        "ok": True,
        "client_info": parse_status(result["output"]),
    }


@app.get("/api/health")
def health():
    result = run_command("health", timeout=15)

    if result["return_code"] != 0:
        raise HTTPException(
            status_code=500,
            detail=result,
        )

    return {
        "ok": True,
        "health": parse_status(result["output"]),
    }


@app.get("/api/network")
def network():
    command = COMMANDS["network"]

    child_env = os.environ.copy()
    child_env.pop("LD_PRELOAD", None)

    try:
        result = subprocess.run(
            [str(command), "status"],
            capture_output=True,
            text=True,
            timeout=10,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="network_status_timeout",
        )

    lines = []

    for source in (result.stdout, result.stderr):
        if source:
            lines.extend(
                line.strip()
                for line in source.splitlines()
                if line.strip()
            )

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "return_code": result.returncode,
                "output": lines,
            },
        )

    return {
        "ok": True,
        "network": parse_status(lines),
    }


def perform_action(name: str):
    if not action_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="lifecycle_action_in_progress",
        )

    try:
        result = run_command(name)

        if result["return_code"] != 0:
            raise HTTPException(
                status_code=500,
                detail=result,
            )

        return {
            "ok": True,
            **result,
        }
    finally:
        action_lock.release()


@app.post("/api/mount")
def mount():
    return perform_action("mount")


@app.post("/api/start")
def start():
    return perform_action("start")


@app.post("/api/stop")
def stop():
    return perform_action("stop")


@app.post("/api/safe-remove")
def safe_remove():
    return perform_action("safe_remove")


@app.post("/api/factory-reset")
def factory_reset_route(payload: dict = Body(...)):
    """Erase the drive back to first run. Needs the administrator password again
    and the word ERASE, on top of the signed-in session."""
    if payload.get("confirm") != "ERASE":
        raise HTTPException(status_code=400, detail="type_ERASE_to_confirm")
    password = payload.get("password")
    from management_guard import guard_settings
    if not isinstance(password, str) or not guard_settings()["auth"].verify_password("administrator", password):
        import time
        time.sleep(2)
        raise HTTPException(status_code=403, detail="incorrect_password")
    if not action_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="lifecycle_action_in_progress")
    try:
        result = run_command_args("factory_reset", ["--confirm", "ERASE"], timeout=900)
    finally:
        action_lock.release()
    if result["return_code"] != 0:
        raise HTTPException(status_code=500, detail=result)
    return {"ok": True, **result}


@app.get("/factory-reset.js")
def factory_reset_script():
    return FileResponse("/usr/local/portabletak/web/factory-reset.js", media_type="text/javascript")


# First-run status and validation; activation remains fail-closed.
from provision_routes import router as provision_router
app.include_router(provision_router)


@app.get("/auth.js")
def auth_script():
    return FileResponse(
        "/usr/local/portabletak/web/auth.js",
        media_type="text/javascript",
    )




@app.get("/remote-access.js")
def remote_access_script():
    return FileResponse("/usr/local/portabletak/web/remote-access.js", media_type="text/javascript")


@app.get("/video-feeds.js")
def video_feeds_script():
    return FileResponse("/usr/local/portabletak/web/video-feeds.js", media_type="text/javascript")


@app.get("/channels.js")
def channels_script():
    return FileResponse("/usr/local/portabletak/web/channels.js", media_type="text/javascript")


@app.get("/federation.js")
def federation_script():
    return FileResponse("/usr/local/portabletak/web/federation.js", media_type="text/javascript")


# --- Video Feeds: drones push RTSP video without a sign-in (see lib/video_feeds.py) ---
def _feed_targets():
    """Where a drone sends its video: the address phones use, and Tailscale if set up."""
    try:
        import client_packages
        import server_addresses
        record = server_addresses.current("/mnt/asgard")
        return {"local": record.get("advertise"), "remote": client_packages.remote_address(record)}
    except Exception:
        return {"local": None, "remote": None}


@app.get("/api/video-feeds")
def video_feeds_list():
    import video_feeds
    return {"ok": True, "feeds": video_feeds.status("/mnt/asgard"), "port": 8554, **_feed_targets()}


@app.post("/api/video-feeds")
def video_feeds_add(payload: dict = Body(...)):
    import video_feeds
    name = str(payload.get("name", "")).strip().lower()
    try:
        video_feeds.add("/mnt/asgard", name)
    except video_feeds.FeedError as error:
        raise HTTPException(status_code=400, detail=str(error))
    result = run_users({"action": "video_feed_add", "name": name, "host": _feed_targets()["local"]})
    if not result.get("ok"):
        video_feeds.remove("/mnt/asgard", name)
        raise HTTPException(status_code=500, detail=result.get("error", "video_feed_not_registered"))
    try:
        video_feeds.apply("/mnt/asgard")
    except Exception:
        raise HTTPException(status_code=503, detail="video_server_not_running")
    return {"ok": True, "name": name}


@app.delete("/api/video-feeds/{name}")
def video_feeds_remove(name: str):
    import video_feeds
    if not video_feeds.NAME.fullmatch(name):
        raise HTTPException(status_code=400, detail="invalid_feed_name")
    video_feeds.remove("/mnt/asgard", name)
    run_users({"action": "video_feed_remove", "name": name})
    try:
        video_feeds.apply("/mnt/asgard")
    except Exception:
        pass
    return {"ok": True, "name": name}


def _tailscale(action, timeout):
    result = run_command_args("tailscale", [action], timeout=timeout)
    values = parse_status(result["output"])
    values["ok"] = result["return_code"] == 0
    return values


@app.get("/api/remote-access")
def remote_access_status():
    try:
        values = _tailscale("status", 20)
    except HTTPException:
        values = {"PORTABLETAK_TAILSCALE": "NOT_INSTALLED", "ok": False}
    # The drive's own Tailscale is used on every host (node, Windows, Mac).
    values["PORTABLETAK_HOST_PROFILE"] = host_profile.read_profile()["PT_PROFILE"]
    try:
        import client_packages
        import server_addresses
        remote = client_packages.remote_address(server_addresses.current("/mnt/asgard"))
        if remote:
            values["PORTABLETAK_REMOTE_ADDRESS"] = remote
    except Exception:
        pass
    return values


@app.post("/api/remote-access/enable")
def remote_access_enable():
    return _tailscale("enable", 60)


@app.post("/api/remote-access/apply")
def remote_access_apply():
    return _tailscale("apply-address", 400)


@app.post("/api/remote-access/logout")
def remote_access_logout():
    return _tailscale("logout", 400)


# ---- TAK channels (OpenTAKServer groups) ----
@app.get("/api/channels")
def channels_list():
    return run_users({"action": "channels_list"})


@app.post("/api/channels")
def channels_create(payload: dict = Body(...)):
    return run_users({"action": "channels_create", "name": str(payload.get("name") or ""),
                      "description": str(payload.get("description") or "")})


@app.post("/api/channels/rename")
def channels_rename(payload: dict = Body(...)):
    return run_users({"action": "channels_rename", "name": str(payload.get("name") or ""),
                      "new_name": str(payload.get("new_name") or "")})


@app.delete("/api/channels/{name}")
def channels_delete(name: str):
    return run_users({"action": "channels_delete", "name": name})


@app.get("/api/users/{username}/channels")
def channels_user_get(username: str):
    return run_users({"action": "channels_user_get", "username": username})


@app.post("/api/users/{username}/channels")
def channels_user_set(username: str, payload: dict = Body(...)):
    return run_users({"action": "channels_user_set", "username": username,
                      "channels": payload.get("channels")})


# ---- Federation (links to other TAK servers; needs the federation add-on) ----
FEDERATION_CONF = Path("/mnt/asgard/portabletak/federation.conf")


def _federation_process(action):
    result = run_command_args("federation", [action], timeout=60)
    return parse_status(result["output"]).get("PORTABLETAK_FEDERATION", "UNKNOWN")


def _federation_restart():
    """New certificates and edited links take effect when the federation server restarts."""
    if FEDERATION_CONF.exists() and "enabled" in FEDERATION_CONF.read_text():
        _federation_process("stop")
        _federation_process("start")


@app.get("/api/federation")
def federation_status():
    data = run_users({"action": "federation_status"})
    enabled = FEDERATION_CONF.exists() and "PORTABLETAK_FEDERATION=enabled" in FEDERATION_CONF.read_text()
    try:
        import server_addresses
        record = server_addresses.current("/mnt/asgard")
    except Exception:
        record = {}
    return {
        "ok": True,
        "enabled": enabled,
        "process": _federation_process("status"),
        "federates": data.get("federates", []),
        "cas": data.get("cas", []),
        "addresses": [a for a in record.get("addresses", []) if a],
        "ports": {"v1": 9000, "v2": 9001},
        "name": _our_server_name(),
    }


@app.post("/api/federation/enable")
def federation_enable(payload: dict = Body(...)):
    on = bool(payload.get("enabled"))
    FEDERATION_CONF.parent.mkdir(parents=True, exist_ok=True)
    FEDERATION_CONF.write_text("PORTABLETAK_FEDERATION=%s\n" % ("enabled" if on else "disabled"))
    return {"ok": True, "process": _federation_process("start" if on else "stop")}


@app.post("/api/federation/federates")
def federation_save(payload: dict = Body(...)):
    allowed = ("name", "address", "port", "protocol_version", "outbound", "enabled",
               "inbound_groups", "outbound_groups", "reset_pin", "direction")
    result = run_users({"action": "federation_save", **{k: payload[k] for k in allowed if k in payload}})
    _federation_restart()
    return result


@app.delete("/api/federation/federates/{name}")
def federation_delete(name: str):
    result = run_users({"action": "federation_delete", "name": name})
    _federation_restart()
    return result


@app.post("/api/federation/ca")
def federation_ca_add(payload: dict = Body(...)):
    pem = str(payload.get("pem") or "")
    if len(pem) > 65536:
        raise HTTPException(status_code=400, detail="certificate_too_large")
    result = run_users({"action": "federation_ca_add", "pem": pem, "filename": str(payload.get("filename") or "")})
    _federation_restart()
    return result


@app.delete("/api/federation/ca/{filename}")
def federation_ca_delete(filename: str):
    result = run_users({"action": "federation_ca_delete", "filename": filename})
    _federation_restart()
    return result


def _federation_on():
    """Turn federation on (writes the setting, starts it; makes the federation certificate)."""
    FEDERATION_CONF.parent.mkdir(parents=True, exist_ok=True)
    FEDERATION_CONF.write_text("PORTABLETAK_FEDERATION=enabled\n")
    state = _federation_process("start")
    if state != "RUNNING":
        raise HTTPException(status_code=500, detail="federation_not_running")


def _our_server_name():
    """This server's name (set at first setup), as shown to other servers."""
    try:
        import client_packages
        text = Path("/mnt/asgard/portabletak/cartridge.conf").read_text()
        found = re.search(r'^PORTABLETAK_INSTANCE_NAME="?([^"\n]*)', text, re.M)
        return client_packages.server_name(found.group(1) if found else "")
    except Exception:
        return "PortableTAK"


def _our_addresses():
    """Addresses another server can use to reach this one: remote (Tailscale) first."""
    import server_addresses
    import client_packages
    record = server_addresses.current("/mnt/asgard")
    found = []
    remote = client_packages.remote_address(record)
    for address in [remote, record.get("advertise")] + list(record.get("addresses") or []):
        if address and address not in found and not str(address).startswith("10.0.2."):
            found.append(address)
    return found[:4]


@app.post("/api/federation/invite")
def federation_invite():
    import federation_invites
    _federation_on()
    me = run_users({"action": "federation_status"})
    code, expires = federation_invites.create("/mnt/asgard", _our_server_name(), _our_addresses(),
                                              me["our_ca"], me["our_fingerprint"])
    return {"ok": True, "code": code, "expires": int(expires), "name": _our_server_name()}


@app.get("/api/federation/public-ca")
def federation_public_ca():
    """This server's CA certificate (public): lets a server holding an invite check it is
    talking to the server that made the invite."""
    data = run_users({"action": "federation_status"})
    return Response(data.get("our_ca", ""), media_type="application/x-pem-file")


@app.post("/api/federation/accept")
def federation_accept(payload: dict = Body(...)):
    """Called by a server redeeming one of our invites (no sign-in; the invite secret
    is the credential, single use, 24 hours)."""
    import federation_invites
    if len(json.dumps(payload)) > 32768:
        raise HTTPException(status_code=413, detail="too_large")
    try:
        federation_invites.redeem("/mnt/asgard", payload.get("t"), use=False)
    except federation_invites.InviteError as error:
        raise HTTPException(status_code=403, detail=str(error))
    result = run_users({"action": "federation_accept", "name": str(payload.get("name") or ""),
                        "ca": str(payload.get("ca") or ""), "cert": str(payload.get("cert") or "")})
    federation_invites.redeem("/mnt/asgard", payload.get("t"))
    _federation_on()
    _federation_restart()
    return {"ok": True, "name": _our_server_name(), "fingerprint": result.get("our_fingerprint")}


@app.post("/api/federation/join")
def federation_join(payload: dict = Body(...)):
    import federation_invites
    try:
        invite = federation_invites.decode(payload.get("code"))
    except federation_invites.InviteError as error:
        raise HTTPException(status_code=400, detail=str(error))
    _federation_on()
    me = run_users({"action": "federation_status"})
    try:
        address, ca_pem, reply = federation_invites.contact(invite, _our_server_name(), me["our_ca"], me["our_cert"])
    except federation_invites.InviteError as error:
        raise HTTPException(status_code=400, detail=str(error))
    name = re.sub(r"[^A-Za-z0-9 ._-]", "", str(invite.get("n") or reply.get("name") or "TAK server"))[:60] or "TAK server"
    run_users({"action": "federation_ca_add", "pem": ca_pem, "filename": name + "-CA.pem"})
    run_users({"action": "federation_save", "name": name, "address": address, "port": int(invite.get("p") or 9001),
               "protocol_version": 2, "outbound": True, "enabled": True, "pin": invite.get("f"), "pin_name": name,
               "direction": payload.get("direction") or "both"})
    _federation_restart()
    return {"ok": True, "name": name, "address": address}


@app.post("/api/federation/tak/check")
def federation_tak_check(payload: dict = Body(...)):
    return run_users({"action": "federation_fetch_ca", "address": str(payload.get("address") or ""),
                      "port": payload.get("port") or 9001})


@app.post("/api/federation/tak/connect")
def federation_tak_connect(payload: dict = Body(...)):
    name = str(payload.get("name") or "TAK Server").strip()[:60]
    _federation_on()
    run_users({"action": "federation_ca_add", "pem": str(payload.get("ca") or ""),
               "filename": re.sub(r"[^A-Za-z0-9._-]", "_", name) + "-CA.pem"})
    result = run_users({"action": "federation_save", "name": name, "address": str(payload.get("address") or ""),
                        "port": payload.get("port") or 9001, "protocol_version": 2, "outbound": True,
                        "enabled": True, "pin": payload.get("server_fingerprint"), "pin_name": name,
                        "direction": payload.get("direction") or "both"})
    _federation_restart()
    return result


@app.get("/api/federation/our-ca")
def federation_our_ca():
    """This server's CA certificate, for the other server's admin (federation truststore)."""
    data = run_users({"action": "federation_status"})
    name = re.sub(r"[^A-Za-z0-9_-]", "_", _our_server_name())
    return Response(data.get("our_ca", ""), media_type="application/x-pem-file",
                    headers={"Content-Disposition": 'attachment; filename="%s-federation-CA.pem"' % name})


# Sessions are enforced only when PORTABLETAK_MANAGEMENT_AUTH=enforce.
from management_guard import ManagementGuard, guard_settings
app = ManagementGuard(app, **guard_settings())
