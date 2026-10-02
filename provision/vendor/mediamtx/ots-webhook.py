#!/usr/bin/python3
"""Forward MediaMTX lifecycle events to the local OpenTAKServer API."""

import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8081/api/mediamtx/webhook"
DEFAULT_TOKEN_FILE = "/mnt/asgard/config/secrets/ots-mediamtx-token"


def env(name):
    return os.environ.get(name, "")


def main():
    if len(sys.argv) != 2:
        print("WEBHOOK_ERROR=missing_event", file=sys.stderr)
        return 2

    event = sys.argv[1]
    allowed = {"init", "connect", "disconnect", "ready", "notready", "read", "unread"}
    if event not in allowed:
        print("WEBHOOK_ERROR=unsupported_event", file=sys.stderr)
        return 3

    token_file = Path(env("PORTABLETAK_MEDIAMTX_TOKEN_FILE") or DEFAULT_TOKEN_FILE)
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        print("WEBHOOK_ERROR={}".format(type(error).__name__), file=sys.stderr)
        return 5
    if not token:
        print("WEBHOOK_ERROR=empty_token", file=sys.stderr)
        return 5

    params = {"token": token, "event": event, "rtsp_port": env("RTSP_PORT")}
    if event == "init":
        params["path"] = env("MTX_PATH")
    elif event in ("connect", "disconnect"):
        params["connection_type"] = env("MTX_CONN_TYPE")
        params["connection_id"] = env("MTX_CONN_ID")
    elif event in ("ready", "notready"):
        params.update(path=env("MTX_PATH"), query=env("MTX_QUERY"),
                      source_type=env("MTX_SOURCE_TYPE"), source_id=env("MTX_SOURCE_ID"))
    else:
        params.update(path=env("MTX_PATH"), query=env("MTX_QUERY"),
                      reader_type=env("MTX_READER_TYPE"), reader_id=env("MTX_READER_ID"))

    base_url = env("PORTABLETAK_OTS_WEBHOOK_URL") or DEFAULT_BASE_URL
    request_url = base_url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(request_url, timeout=5) as response:
            if 200 <= response.getcode() < 300:
                return 0
            print("WEBHOOK_HTTP_ERROR={}".format(response.getcode()), file=sys.stderr)
            return 4
    except Exception as error:
        print("WEBHOOK_ERROR={}".format(type(error).__name__), file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
