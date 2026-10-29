# Publish a marker as a connected phone would (EudHandler: firehose + cot_parser).
#   python3 phone-cot.py SENDER_UID MARKER_UID CALLSIGN [MISSION]   (MISSION adds <dest mission=...>)
import json, sys, datetime, yaml, pika
sender, uid, callsign = sys.argv[1:4]
mission = sys.argv[4] if len(sys.argv) > 4 else None
try:
    cfg = yaml.safe_load(open('/asgard/opentak/data/config.yml')) or {}
except OSError:
    cfg = {}
now = datetime.datetime.now(datetime.timezone.utc)
t = lambda d: (now + d).strftime('%Y-%m-%dT%H:%M:%S.000Z')
dest = '<marti><dest mission="%s"/></marti>' % mission if mission else ''
cot = ('<event version="2.0" uid="%s" type="a-h-G" how="h-g-i-g-o" time="%s" start="%s" stale="%s">'
       '<point lat="30.41" lon="-91.12" hae="10" ce="5" le="5"/><detail><contact callsign="%s"/>'
       '<color argb="-65536"/>%s</detail></event>') % (uid, t(datetime.timedelta()), t(datetime.timedelta()),
                                                  t(datetime.timedelta(days=1)), callsign, dest)
creds = pika.PlainCredentials(cfg.get('OTS_RABBITMQ_USERNAME', 'guest'), cfg.get('OTS_RABBITMQ_PASSWORD', 'guest'))
conn = pika.BlockingConnection(pika.ConnectionParameters(cfg.get('OTS_RABBITMQ_SERVER_ADDRESS', '127.0.0.1'), credentials=creds))
ch = conn.channel()
ch.basic_publish(exchange='firehose', routing_key='', body=json.dumps({'uid': sender, 'cot': cot}))
ch.basic_publish(exchange='cot_parser', routing_key='cot_parser', body=json.dumps({'uid': sender, 'cot': cot, 'user_id': None}))
conn.close()
print('PUBLISHED', uid)
