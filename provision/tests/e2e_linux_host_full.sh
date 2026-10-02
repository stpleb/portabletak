#!/bin/bash
# End-to-end test of the generic Linux host profile (the WSL2 path) inside a
# privileged Ubuntu arm64 container. Never touches real disks: the cartridge
# is a sparse loop image. Mounts: /payload (host installer payload, ro),
# /src (portabletak-provision, ro), /art (clean-base archives, ro).
set -u
say() { printf '\n== %s\n' "$*"; }
fail() { echo "E2E_RESULT=FAIL:$*"; exit 1; }

ARCHIVE=/art/portabletak-clean-arm64-20260930-v3.tar.gz
R=/usr/local/portabletak

say "host tools"
apt-get update -qq >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq e2fsprogs fdisk python3 iproute2 \
    net-tools procps util-linux diffutils bsdutils iputils-ping openssl >/dev/null 2>&1 || fail apt
cp -a /payload/usr/local/portabletak /usr/local/ && chown -R 0:0 $R
IP="$(ip -4 -o addr show dev eth0 | awk '{print $4}' | cut -d/ -f1)"
mkdir -p /etc/portabletak
printf 'PT_PROFILE=linux\nPT_SERVICE_IP=%s\nPT_SERVICE_DEV=eth0\nPT_BOOT_DEV=eth0\n' "$IP" > /etc/portabletak/host-profile
echo "host address $IP"

say "layout v3 cartridge on a loop image"
truncate -s 8G /var/tmp/cartridge.img
LOOP="$(losetup -f --show -P /var/tmp/cartridge.img)" || fail losetup
cleanup() {
    $R/bin/portabletak-stop >/dev/null 2>&1
    $R/bin/portabletak-unprepare >/dev/null 2>&1
    umount /mnt/asgard 2>/dev/null
    losetup -d "$LOOP" 2>/dev/null
}
trap cleanup EXIT
python3 - "$LOOP" "$ARCHIVE" <<'PY' || fail layout
import sys
from pathlib import Path
sys.path.insert(0, '/usr/local/portabletak/lib')
import media_layout
from release import expected_base_seal, expected_base_archive
loop, archive = Path(sys.argv[1]), sys.argv[2]
media_layout.verify_archive(archive, expected_base_archive(), expected_base_seal())
r = media_layout.lay_down(loop, Path('/sys/block') / loop.name, media_layout.partition_name(loop.name),
                          archive, expected_base_seal(), '/var/tmp/layout', log=lambda *_: None)
print('layout', r['metadata']['PORTABLETAK_PROVISIONING_STATE'], 'fsck', r['fsck_rc'])
PY
PART="${LOOP}p1"
mkdir -p /mnt/asgard
e2fsck -fn "$PART" >/dev/null 2>&1 || fail fsck
mount -t ext4 "$PART" /mnt/asgard || fail mount
$R/bin/portabletak-publish-identity /mnt/asgard "$(python3 -c 'import sys;sys.path.insert(0,"/usr/local/portabletak/lib");from release import expected_base_seal;print(expected_base_seal())')" || fail publish

say "provision through the job supervisor (gate opened only for this run)"
mkdir -p $R/release && touch $R/release/first-run-enabled
python3 - <<'PY' || fail launch
import secrets, sys, json
sys.path.insert(0, '/usr/local/portabletak/lib')
from bootstrap_job import BootstrapJobSupervisor
request = {'instance_name': 'Linux Host Test',
           'administrator_password': secrets.token_urlsafe(24),
           'client_password': secrets.token_urlsafe(24)}
print(json.dumps(BootstrapJobSupervisor().launch(request)))
PY
for i in $(seq 1 120); do
    JOB="$(sed -n 's/.*"job": *"\([a-z]*\)".*/\1/p' /tmp/portabletak/bootstrap-job.json)"
    [ "$JOB" = running ] || break
    sleep 10
done
rm -f $R/release/first-run-enabled
python3 -c 'import json;d=json.load(open("/mnt/asgard/portabletak/provision/status.json"));print("phases:",[h["phase"] for h in d["history"]],"error:",d.get("error"))'
[ "$JOB" = complete ] || fail "provisioning_$JOB"
grep -E "^PORTABLETAK_PROVISIONING_STATE" /mnt/asgard/portabletak/cartridge.conf

say "health"
$R/bin/portabletak-health | grep -E "^PORTABLETAK_(POSTGRESQL|RABBITMQ|MEDIAMTX|OTS|COT|EUD|NGINX)="
[ "$($R/bin/portabletak-health | grep -cE '^PORTABLETAK_(POSTGRESQL|RABBITMQ|MEDIAMTX|OTS|COT|EUD|NGINX)=RUNNING')" = 7 ] || fail health
netstat -ltn | grep -E ":8089 |$IP:8443 "

say "two TLS TAK clients: A's position must reach B"
ALPHA_PW="$(openssl rand -hex 12)"; BRAVO_PW="$(openssl rand -hex 12)"
printf '{"action":"create","username":"alpha","password":"%s"}' "$ALPHA_PW" | $R/bin/portabletak-users | head -c 200; echo
printf '{"action":"create","username":"bravo","password":"%s"}' "$BRAVO_PW" | $R/bin/portabletak-users | head -c 200; echo
CHARLIE_PW="$(openssl rand -hex 12)"
printf '{"action":"create","username":"charlie","password":"%s"}' "$CHARLIE_PW" | $R/bin/portabletak-users | head -c 200; echo
for U in alpha bravo; do
    printf '{"action":"generate_package","username":"%s"}' "$U" | $R/bin/portabletak-users | head -c 120; echo
done
ls /mnt/asgard/opentak/data/ca/certs/alpha/ | tr "\n" " "; echo
relay_check() {
python3 - "$IP" <<'PY' 
import socket, ssl, sys, time, uuid
ip = sys.argv[1]
ca = '/mnt/asgard/opentak/data/ca/ca.pem'
def client(user):
    ctx = ssl.create_default_context(cafile=ca)
    ctx.check_hostname = False
    d = f'/mnt/asgard/opentak/data/ca/certs/{user}'
    ctx.load_cert_chain(f'{d}/{user}.pem', f'{d}/{user}.nopass.key')
    s = ctx.wrap_socket(socket.create_connection((ip, 8089), timeout=15))
    return s
uid = 'E2E-' + uuid.uuid4().hex[:8]
def event(u, callsign):
    now = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    stale = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(time.time() + 300))
    return (f'<event version="2.0" uid="{u}" type="a-f-G-U-C" how="m-g" time="{now}" start="{now}" stale="{stale}">'
            f'<point lat="30.0" lon="-97.0" hae="100" ce="10" le="10"/>'
            f'<detail><contact callsign="{callsign}"/><takv device="e2e" platform="E2E" os="linux" version="1"/></detail></event>').encode()
a, b = client('alpha'), client('bravo')
b.sendall(event('E2E-BRAVO', 'BRAVO'))
time.sleep(3)
a.sendall(event(uid, uid))
b.settimeout(2)
deadline, seen = time.time() + 45, b''
while time.time() < deadline and uid.encode() not in seen:
    try:
        chunk = b.recv(65536)
        if chunk:
            seen += chunk
    except (socket.timeout, TimeoutError):
        a.sendall(event(uid, uid))
print('B received A:', uid.encode() in seen)
sys.exit(0 if uid.encode() in seen else 1)
PY
}
relay_check || fail relay


say "certificate enrollment on 8446 (QR onboarding server side)"
CHARLIE_PW="$CHARLIE_PW" python3 - "$IP" <<'PY' || fail enrollment
import base64, os, ssl, subprocess, sys, tempfile, urllib.request, urllib.error
ip = sys.argv[1]
ctx = ssl.create_default_context(cafile='/mnt/asgard/opentak/data/ca/ca.pem'); ctx.check_hostname = False
auth = 'Basic ' + base64.b64encode(b'charlie:' + os.environ['CHARLIE_PW'].encode()).decode()
def req(path, data=None, headers=None):
    r = urllib.request.Request(f'https://{ip}:8446{path}', data=data, headers=headers or {}, method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(r, context=ctx, timeout=30) as x: return x.status, x.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
status, body = req('/Marti/api/tls/config', headers={'Authorization': auth})
print('tls/config', status, b'nameEntry' in body or b'certificateConfig' in body)
assert status == 200
d = tempfile.mkdtemp()
subprocess.run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', f'{d}/k.pem',
                '-subj', '/O=PortableTAK/OU=TAK/CN=charlie', '-out', f'{d}/c.csr'], check=True, capture_output=True)
csr = open(f'{d}/c.csr').read()
csr_body = ''.join(l for l in csr.splitlines() if not l.startswith('-----')).encode()
status, body = req('/Marti/api/tls/signClient/v2?clientUid=E2E-ENROLL&version=5.2', data=csr_body,
                   headers={'Authorization': auth, 'Accept': 'application/json', 'Content-Type': 'text/plain'})
print('signClient/v2', status, len(body), 'bytes', 'signedCert' in body.decode(errors='replace'))
assert status == 200 and b'signedCert' in body
# Upstream serves tls/config (only O/OU name fields) without auth; signing must refuse.
status, _ = req('/Marti/api/tls/signClient/v2?clientUid=E2E-BAD&version=5.2', data=csr_body,
                headers={'Authorization': 'Basic ' + base64.b64encode(b'charlie:wrong-password').decode(),
                         'Accept': 'application/json', 'Content-Type': 'text/plain'})
print('signClient wrong password', status); assert status in (401, 403)
status, _ = req('/Marti/api/tls/signClient/v2?clientUid=E2E-NOAUTH&version=5.2', data=csr_body,
                headers={'Accept': 'application/json', 'Content-Type': 'text/plain'})
print('signClient no auth', status); assert status in (401, 403)
for path in ('/', '/api/users', '/Marti/api/version'):
    status, _ = req(path, headers={'Authorization': auth}); print('non-enrollment', path, status); assert status == 404
PY

say "full stop, then the boot sequence (autoload) on generic Linux"
$R/bin/portabletak-stop >/dev/null 2>&1
echo "after stop: $($R/bin/portabletak-health | grep '=RUNNING' | tr '\n' ' ')"
. $R/lib/host-profile.sh; pt_ps | grep -E "[p]ostgres|[b]eam|[m]ediamtx|[n]ginx|[o]pentakserver|[c]ot_parser|[e]ud_handler" | cut -c1-120
$R/bin/portabletak-unprepare >/dev/null 2>&1; umount /mnt/asgard || fail umount_before_boot
$R/bin/portabletak-boot; echo "boot rc=$?"
grep -E "AUTOLOAD|PREEN|RECOVERED|ERROR" /tmp/portabletak/boot-autoload.log | tr "\n" " "; echo
[ "$($R/bin/portabletak-health | grep -cE '^PORTABLETAK_(POSTGRESQL|RABBITMQ|MEDIAMTX|OTS|COT|EUD|NGINX)=RUNNING')" = 7 ] || fail health_after_boot
relay_check || fail relay_after_boot

say "backup and verify on generic Linux"
$R/bin/portabletak-backup --note "E2E linux host" | tee /tmp/e2e-backup.log | grep -E "COMPLETE|ERROR|BACKUP_FILE"
grep -q "PORTABLETAK_BACKUP=COMPLETE" /tmp/e2e-backup.log || fail backup
NAME="$(ls /mnt/asgard/portabletak/backups/recovery/ | grep -E '^portabletak-.*\.tar\.gz$' | tail -1)"
$R/bin/portabletak-restore verify "$NAME" | tee /tmp/e2e-verify.log | grep -E "RESTORE=|ERROR"
grep -q "PORTABLETAK_RESTORE=VERIFIED" /tmp/e2e-verify.log || fail verify

say "host-owned address tool"
$R/adapters/linux/network status | tail -1
echo "E2E_RESULT=PASS"
