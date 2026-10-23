#!/bin/bash
# tak-send.sh UID CALLSIGN : send one position to TAK Server as a client on 8089
NOW=$(date -u +%Y-%m-%dT%H:%M:%S.000Z); STALE=$(date -u -d '+10 min' +%Y-%m-%dT%H:%M:%S.000Z)
EV="<event version=\"2.0\" uid=\"$1\" type=\"a-f-G-U-C\" how=\"m-g\" time=\"$NOW\" start=\"$NOW\" stale=\"$STALE\"><point lat=\"30.5\" lon=\"-91.2\" hae=\"10\" ce=\"5\" le=\"5\"/><detail><contact callsign=\"$2\"/><__group name=\"Cyan\" role=\"Team Member\"/></detail></event>"
cd /opt/tak/certs/files
( echo "$EV"; sleep 4 ) | openssl s_client -quiet -connect 127.0.0.1:8089 -cert admin.pem -key admin.key -pass pass:atakatak 2>&1 | head -3
echo SENT
