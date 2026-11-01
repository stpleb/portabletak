#!/bin/bash
# End-to-end voice test with no TAKX and no sound hardware: the real engine (Java) against Murmur with
# the TAK PTT sign-in check, and tools/voicebot.py signed in as a second user. Needs Docker, a JDK 17+,
# the images takptt-murmur-test and takptt-voicebot, and the test CA in .test-ca.
#   tools/run-e2e.sh [work dir]
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$here/.." && pwd)
work=${1:-$(mktemp -d)}
port=64738
mkdir -p "$work/classes" "$work/certs" "$work/out" "$work/lib"
JAVAC=${JAVAC:-javac}
JAVA=${JAVA:-java}

for jar in io/github/jaredmdobson/concentus/1.0.2/concentus-1.0.2 org/bouncycastle/bcprov-jdk18on/1.80/bcprov-jdk18on-1.80 \
    org/bouncycastle/bcpkix-jdk18on/1.80/bcpkix-jdk18on-1.80 org/bouncycastle/bcutil-jdk18on/1.80/bcutil-jdk18on-1.80; do
  f="$work/lib/$(basename "$jar").jar"
  [ -f "$f" ] || curl -sSfL -o "$f" "https://repo1.maven.org/maven2/$jar.jar"
done

ca="$repo/.test-ca"
store() { openssl pkcs12 -export -in "$ca/$1.pem" -inkey "$ca/$1.key" -name "$1" -passout pass:atakatak -out "$work/certs/$1.p12"; }
trust() { openssl pkcs12 -export -nokeys -in "$ca/$1.pem" -name ca -passout pass:atakatak -out "$work/certs/$2.p12"; }
store alice
store rogue
trust ca trust
trust rogue rogue-trust

"$JAVAC" --release 17 -nowarn -d "$work/classes" -cp "$work/lib/*" \
  $(find "$here/src/test/java" -name '*.java') \
  "$here"/src/main/java/com/portabletak/takptt/takx/{Audio,Mixer,MumbleClient,Pb,Tones,VoiceEngine,VoiceLogin,VoicePacket}.java

cleanup() { docker rm -f takptt-e2e-murmur takptt-e2e-bot >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker run -d --name takptt-e2e-murmur -p 127.0.0.1:$port:64738/tcp -v "$ca":/certs:ro takptt-murmur-test >/dev/null
for _ in $(seq 1 40); do nc -z 127.0.0.1 $port && break; sleep 0.5; done
sleep 4                                     # the sign-in check registers after Murmur is up
docker run -d --name takptt-e2e-bot --add-host=host.docker.internal:host-gateway -v "$ca":/certs:ro -v /out \
  -e NAME=BOT -e CERT=/certs/bob.pem -e KEY=/certs/bob.key takptt-voicebot host.docker.internal 48 >/dev/null
sleep 4

status=0
"$JAVA" -cp "$work/classes:$work/lib/*" com.portabletak.takptt.takx.VoiceE2E 127.0.0.1 $port "$work/certs" || status=$?
echo "--- server log (sign-ins)"
docker logs takptt-e2e-murmur 2>&1 | grep -iE 'authenticated|rejected|Connection closed|certificate' | tail -12 || true
echo "--- bot (it saves what it heard when it exits)"
docker wait takptt-e2e-bot >/dev/null
rm -rf "$work/out"
docker cp takptt-e2e-bot:/out "$work/out"
docker logs takptt-e2e-bot 2>&1 | grep -E 'CONNECTED|RECORDED' | tail -8 || true
# The other direction: the bot's recording of us must be the made-up microphone's 440 Hz tone.
python3 -I - "$work/out" <<'PY' || status=1
import glob, struct, sys, wave
best = None
for path in glob.glob(sys.argv[1] + '/TAKXTEST*.wav'):
    w = wave.open(path)
    n = w.getnframes()
    if best is None or n > best[1]:
        best = (path, n, w.getframerate(), w.readframes(n))
if best is None:
    print('FAIL the bot recorded nothing from us')
    sys.exit(1)
path, n, rate, raw = best
pcm = struct.unpack('<%dh' % n, raw)
loud = [s for s in pcm if abs(s) > 0] or [0]
crossings = sum(1 for a, b in zip(pcm, pcm[1:]) if a < 0 <= b)
hz, seconds, peak = crossings / (n / rate), n / rate, max(abs(s) for s in pcm)
ok = abs(hz - 440) < 25 and seconds > 3 and peak > 3000
print('%s the bot heard us: %.1f s, peak %d, %.0f Hz' % ('PASS' if ok else 'FAIL', seconds, peak, hz))
sys.exit(0 if ok else 1)
PY
echo "work dir: $work"
exit $status
