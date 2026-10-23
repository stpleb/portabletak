# Publish one position report as if from a connected phone (into the firehose).
import json, sys, datetime, yaml, pika
uid, callsign = sys.argv[1], sys.argv[2]
try:
    cfg = yaml.safe_load(open('/asgard/opentak/data/config.yml')) or {}
except OSError:
    cfg = {}
now = datetime.datetime.now(datetime.timezone.utc)
t = lambda d: (now + d).strftime('%Y-%m-%dT%H:%M:%S.000Z')
cot = ('<event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" time="%s" start="%s" stale="%s">'
       '<point lat="30.4" lon="-91.1" hae="10" ce="5" le="5"/><detail><contact callsign="%s"/>'
       '<__group name="Cyan" role="Team Member"/></detail></event>') % (uid, t(datetime.timedelta()), t(datetime.timedelta()), t(datetime.timedelta(minutes=10)), callsign)
creds = pika.PlainCredentials(cfg.get('OTS_RABBITMQ_USERNAME', 'guest'), cfg.get('OTS_RABBITMQ_PASSWORD', 'guest'))
conn = pika.BlockingConnection(pika.ConnectionParameters(cfg.get('OTS_RABBITMQ_SERVER_ADDRESS', '127.0.0.1'), credentials=creds))
ch = conn.channel()
ch.basic_publish(exchange='firehose', routing_key='', body=json.dumps({'uid': uid, 'cot': cot}))
conn.close()
print('PUBLISHED', uid)
