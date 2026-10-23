#!/bin/sh
# send-package.sh SENDER_UID SENDER_CALLSIGN TO_CALLSIGN NAME
# Does what ATAK does to send a data package: the file goes to its own server
# (uploads/<sha256>.zip + data_packages row), then a file-share CoT goes out.
set -e
ROOT="$(/usr/local/portabletak/lib/pt-rootfs /mnt/asgard)"
UP=/mnt/asgard/opentak/data/uploads; mkdir -p "$UP"
python3 - "$4" > /tmp/pkg.out <<'PY'
import hashlib, io, os, sys, zipfile
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as z:
    z.writestr('MANIFEST/manifest.xml', '<MissionPackageManifest version="2"><Configuration><Parameter name="name" value="%s"/></Configuration></MissionPackageManifest>' % sys.argv[1])
    z.writestr('notes/readme.txt', 'Federation test package %s\n' % sys.argv[1])
    z.writestr('notes/blob.bin', os.urandom(300000))
data = buf.getvalue(); h = hashlib.sha256(data).hexdigest()
open('/mnt/asgard/opentak/data/uploads/%s.zip' % h, 'wb').write(data)
print(h, len(data))
PY
read H SIZE < /tmp/pkg.out
cat > "$ROOT/tmp/pkg.sql" <<SQL
insert into data_packages (filename, hash, submission_time, mime_type, size, keywords)
values ('$4.zip', '$H', now(), 'application/zip', $SIZE, 'test upload');
SQL
chmod 644 "$ROOT/tmp/pkg.sql"
chroot "$ROOT" su postgres -s /bin/sh -c "psql -X -q -d ots -f /tmp/pkg.sql"
IP="$(ip -4 -o addr show dev eth0 | awk '{print $4}' | cut -d/ -f1)"
NOW=$(date -u +%Y-%m-%dT%H:%M:%S.000Z); STALE=$(date -u -d '+10 min' +%Y-%m-%dT%H:%M:%S.000Z)
cat > "$ROOT/tmp/fileshare.xml" <<XML
<event version="2.0" uid="$H-share" type="b-f-t-r" how="h-e" time="$NOW" start="$NOW" stale="$STALE"><point lat="0" lon="0" hae="0" ce="9999999" le="9999999"/><detail><fileshare filename="$4.zip" senderUrl="https://$IP:8443/Marti/sync/content?hash=$H" sizeInBytes="$SIZE" sha256="$H" senderUid="$1" senderCallsign="$2" name="$4"/><ackrequest uid="$H-ack" ackrequested="true" tag="$4"/><marti><dest callsign="$3"/></marti></detail></event>
XML
cat > "$ROOT/tmp/pubraw.py" <<'PY'
import json, sys, yaml, pika
try:
    cfg = yaml.safe_load(open('/asgard/opentak/data/config.yml')) or {}
except OSError:
    cfg = {}
creds = pika.PlainCredentials(cfg.get('OTS_RABBITMQ_USERNAME', 'guest'), cfg.get('OTS_RABBITMQ_PASSWORD', 'guest'))
conn = pika.BlockingConnection(pika.ConnectionParameters(cfg.get('OTS_RABBITMQ_SERVER_ADDRESS', '127.0.0.1'), credentials=creds))
conn.channel().basic_publish(exchange='firehose', routing_key='', body=json.dumps({'uid': sys.argv[1], 'cot': open(sys.argv[2]).read()}))
conn.close()
PY
chroot "$ROOT" /asgard/opentak/venv/bin/python /tmp/pubraw.py "$1" /tmp/fileshare.xml
echo "SENT_PACKAGE $H $SIZE"
