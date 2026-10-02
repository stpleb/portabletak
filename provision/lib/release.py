"""Trusted release inputs for the current PortableTAK payload."""

SUPPORTED_BASE_SEALS = {
    # v4: dual-chip (v3 arm64 + arch/amd64 x86_64 software); v3 was 6b1f3209…
    'arm64': '8c1a1c36c5389939ff7c586ff5ea3e29b0b265effea44664a0f002a402d5e2e6',
}

# Compressed clean-base archives that may be laid onto new media.
SUPPORTED_BASE_ARCHIVES = {
    'arm64': '987589e72cd17b40127d79f9f11b8029075e32c5ba144ae6b8f97b3cf2c08a5b',  # portabletak-clean-dual-20261001-v4
}


def expected_base_seal(architecture='arm64'):
    try:
        return SUPPORTED_BASE_SEALS[architecture]
    except KeyError:
        raise ValueError('unsupported_release_architecture') from None


def expected_base_archive(architecture='arm64'):
    try:
        return SUPPORTED_BASE_ARCHIVES[architecture]
    except KeyError:
        raise ValueError('unsupported_release_architecture') from None
