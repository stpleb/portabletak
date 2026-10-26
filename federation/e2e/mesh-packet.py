"""Make encrypted Meshtastic MQTT messages (ServiceEnvelope) like a radio uplinks on
LongFast with the default key: one NODEINFO and one POSITION from node !a1b2c3d4.
   python mesh-packet.py OUTDIR [lat lon]   (inside the cartridge chroot python)"""
import base64
import os
import sys

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic import mesh_pb2, mqtt_pb2, portnums_pb2

out = sys.argv[1]
lat, lon = (float(sys.argv[2]), float(sys.argv[3])) if len(sys.argv) > 3 else (32.7050, -97.6070)
NODE = 0xA1B2C3D4
KEY = base64.b64decode('1PG7OiApB1nwvP+rz05pAQ==')   # Meshtastic default key (AQ==)


def envelope(packet_id, portnum, payload):
    data = mesh_pb2.Data(portnum=portnum, payload=payload)
    nonce = packet_id.to_bytes(8, 'little') + NODE.to_bytes(8, 'little')
    enc = Cipher(algorithms.AES(KEY), modes.CTR(nonce), backend=default_backend()).encryptor()
    packet = mesh_pb2.MeshPacket(to=0xFFFFFFFF, id=packet_id, channel=8, hop_limit=3, hop_start=3)
    setattr(packet, 'from', NODE)
    packet.encrypted = enc.update(data.SerializeToString()) + enc.finalize()
    return mqtt_pb2.ServiceEnvelope(packet=packet, channel_id='LongFast', gateway_id='!a1b2c3d4').SerializeToString()


user = mesh_pb2.User(id='!a1b2c3d4', long_name='Test Radio', short_name='TR')
position = mesh_pb2.Position(latitude_i=int(lat * 1e7), longitude_i=int(lon * 1e7), altitude=200, time=1)
for name, pid, port, payload in (('nodeinfo', 1001, portnums_pb2.NODEINFO_APP, user.SerializeToString()),
                                 ('position', 1002, portnums_pb2.POSITION_APP, position.SerializeToString())):
    with open(os.path.join(out, name + '.bin'), 'wb') as f:
        f.write(envelope(pid, port, payload))
print('MESH_PACKETS_WRITTEN')
