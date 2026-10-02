"""Arrange and seal a sanitized development build; produces no runtime identity."""
import json
from pathlib import Path
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from clean_base import seal,verify_seal
from portabletak_provision import write_json,write_metadata
root=Path(sys.argv[1]).absolute()
assert root.resolve()==root and str(root).startswith('/tmp/portabletak-cleanbase-')
assert (root/'.portabletak-development').is_file()
payload=root/'payload'; payload.mkdir(mode=0o700)
(root/'rootfs').rename(payload/'rootfs')
(payload/'opentak').mkdir()
(payload/'rootfs/asgard/opentak/venv').rename(payload/'opentak/venv')
(payload/'portabletak').mkdir()
write_json(payload/'portabletak/base.json',{'format':1,'kind':'portabletak-clean-base','architecture':'arm64'})
write_metadata(payload/'portabletak/cartridge.conf',{'PORTABLETAK_FORMAT':'1','PORTABLETAK_ARCH':'arm64','PORTABLETAK_CARTRIDGE_ID':str(uuid.uuid4()),'PORTABLETAK_INSTANCE_NAME':'Unconfigured','PORTABLETAK_INSTANCE_TYPE':'generic','PORTABLETAK_PROVISIONING_STATE':'new'})
provenance=json.loads((root/'provenance.json').read_text())
checksum=seal(payload,provenance)
assert verify_seal(payload,checksum)
(root/'base-seal.sha256').write_text(checksum+'\n')
print('Clean software payload sealed and verified:',checksum)
