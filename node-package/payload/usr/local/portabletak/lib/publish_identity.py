"""Publish a unique identity into a verified generic cartridge payload."""

import os
from pathlib import Path
import re
import uuid

from clean_base import BaseError, verify_seal
from portabletak_provision import ProvisionError, exclusive_lock, metadata, write_metadata


class PublishError(Exception):
    pass


def publish_identity(root, expected_seal, lock='/tmp/portabletak/lifecycle.lock',
                     require_mount=True, identity_factory=uuid.uuid4):
    root = Path(root).absolute()
    if root.is_symlink() or root.resolve() != root or not root.is_dir():
        raise PublishError('invalid_target')
    if require_mount and not os.path.ismount(root):
        raise PublishError('target_not_mounted')
    if not re.fullmatch(r'[0-9a-f]{64}', expected_seal):
        raise PublishError('invalid_expected_seal')

    try:
        with exclusive_lock(Path(lock)):
            verify_seal(root, expected_seal)
            values = metadata(root / 'portabletak/cartridge.conf')
            if values['PORTABLETAK_INSTANCE_TYPE'] != 'generic':
                raise PublishError('generic_payload_required')
            if values['PORTABLETAK_PROVISIONING_STATE'] != 'new':
                raise PublishError('new_payload_required')
            if uuid.UUID(values['PORTABLETAK_CARTRIDGE_ID']).int != 0:
                raise PublishError('identity_already_published')
            cartridge_id = identity_factory()
            if (not isinstance(cartridge_id, uuid.UUID) or cartridge_id.int == 0
                    or cartridge_id.version != 4):
                raise PublishError('invalid_generated_identity')
            values['PORTABLETAK_CARTRIDGE_ID'] = str(cartridge_id)
            write_metadata(root / 'portabletak/cartridge.conf', values)
            verify_seal(root, expected_seal)
            return str(cartridge_id)
    except (BaseError, ProvisionError) as error:
        code = error.code if isinstance(error, ProvisionError) else str(error)
        raise PublishError(code) from None
