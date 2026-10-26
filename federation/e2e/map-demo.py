"""Feed demo map items into a running test drive (inside its chroot python):
   python3 map-demo.py [step]   - phones, an aircraft, a ship, markers, a CASEVAC around Fort Worth.
   Published like OpenTAKServer's own ADS-B job: exchange cot_parser, uid = OTS_NODE_ID.
   'step' moves the moving items a little each run."""
import datetime
import json
import math
import sys

import pika
import yaml

cfg = yaml.safe_load(open('/asgard/opentak/data/config.yml')) or {}
step = int(sys.argv[1]) if len(sys.argv) > 1 else 0
now = datetime.datetime.now(datetime.timezone.utc)
t = lambda minutes=0: (now + datetime.timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def event(uid, cot_type, lat, lon, detail, how='m-g', stale=10):
    return ('<event version="2.0" uid="%s" type="%s" how="%s" time="%s" start="%s" stale="%s">'
            '<point lat="%.6f" lon="%.6f" hae="200" ce="5" le="5"/><detail>%s</detail></event>'
            % (uid, cot_type, how, t(), t(), t(stale), lat, lon, detail))


a = step * 0.002
items = [
    event('DEMO-PHONE-1', 'a-f-G-U-C', 32.7040 + a, -97.6058,
          '<contact callsign="RANGER-1"/><__group name="Cyan" role="Team Lead"/><track course="45" speed="1.4"/><status battery="81"/>'),
    event('DEMO-PHONE-2', 'a-f-G-U-C', 32.7012, -97.6090 + a,
          '<contact callsign="RANGER-2"/><__group name="Cyan" role="Team Member"/><track course="90" speed="0.8"/>'),
    event('DEMO-PHONE-3', 'a-f-G-U-C', 32.6990 - a, -97.6010,
          '<contact callsign="MEDIC"/><__group name="Red" role="Medic"/>'),
    event('ICAO-A1B2C3', 'a-n-A-C-F', 32.72 + 0.01 * math.sin(step / 3), -97.62 + 0.03 * step % 0.2,
          '<contact callsign="AAL1234"/><track course="110" speed="120"/><remarks>ADS-B demo</remarks>', how='m-g', stale=2),
    event('MMSI-366999999', 'a-n-S-X-M', 32.735, -97.585, '<contact callsign="DEMO VESSEL"/><track course="200" speed="3"/>'),
    event('DEMO-MARKER-1', 'a-h-G', 32.7031, -97.6040,
          '<contact callsign="Hostile vehicle"/><remarks>Seen at the gate</remarks><link parent_callsign="RANGER-1" relation="p-p"/>',
          how='h-g-i-g-o', stale=60),
    event('DEMO-MARKER-2', 'b-m-p-s-m', 32.7065, -97.6100,
          '<contact callsign="Rally point"/><color argb="-256"/><usericon iconsetpath="COT_MAPPING_SPOTMAP/b-m-p-s-m/-256"/>',
          how='h-g-i-g-o', stale=60),
    event('DEMO-CASEVAC', 'b-r-f-h-c', 32.7002, -97.6030,
          '<contact callsign="CASEVAC-1"/><remarks>2 urgent</remarks>', how='h-g-i-g-o', stale=60),
]
creds = pika.PlainCredentials(cfg.get('OTS_RABBITMQ_USERNAME', 'guest'), cfg.get('OTS_RABBITMQ_PASSWORD', 'guest'))
conn = pika.BlockingConnection(pika.ConnectionParameters(cfg.get('OTS_RABBITMQ_SERVER_ADDRESS', '127.0.0.1'), credentials=creds))
ch = conn.channel()
for cot in items:
    ch.basic_publish(exchange='cot_parser', routing_key='cot_parser',
                     body=json.dumps({'cot': cot, 'uid': cfg.get('OTS_NODE_ID')}))
conn.close()
print('DEMO_PUBLISHED', len(items))
