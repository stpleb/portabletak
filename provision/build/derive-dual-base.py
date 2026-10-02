"""Derive the sealed dual-chip clean base (v4) from v3 plus the x86_64 tree. Root Linux only.

The arm64 software stays where v3 has it (rootfs/, opentak/venv, mediamtx is
installed by provisioning). The x86_64 software is added under arch/amd64/:
rootfs, venv, mediamtx/mediamtx and provenance.json. Every input is proven by
digest first, the result is audited like any clean base, re-sealed with v3's
provenance (the x86_64 provenance is a sealed file), packed, and proven again.

    derive-dual-base.py SRC_ARCHIVE SRC_SHA256 SRC_SEAL \\
        AMD64_TREE AMD64_TREE_SHA256 MEDIAMTX_TGZ MEDIAMTX_SHA256 AMD64_PROVENANCE OUT_ARCHIVE
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import clean_base
import media_layout


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def derive(src, src_sha, src_seal, tree_tgz, tree_sha, mtx_tgz, mtx_sha, provenance_path, out):
    if os.geteuid() != 0:
        raise SystemExit('root required to preserve ownership')
    out = Path(out)
    if out.exists():
        raise SystemExit('output exists')
    if sha256(tree_tgz) != tree_sha:
        raise SystemExit('amd64 tree digest mismatch')
    if sha256(mtx_tgz) != mtx_sha:
        raise SystemExit('mediamtx digest mismatch')
    amd64_provenance = json.loads(Path(provenance_path).read_text())
    if set(amd64_provenance) != {'ubuntu_archive', 'ubuntu_sha256', 'apt_packages', 'python_packages'} \
            or 'amd64' not in amd64_provenance['ubuntu_archive']:
        raise SystemExit('invalid amd64 provenance')
    media_layout.verify_archive(src, src_sha, src_seal)
    with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
        tree = Path(temporary) / 'payload'
        tree.mkdir(mode=0o700)
        media_layout.extract_payload(src, tree)
        clean_base.verify_seal(tree, src_seal)
        provenance = json.loads((tree / 'portabletak/base-seal.json').read_text())['provenance']

        staging = Path(temporary) / 'amd64'
        staging.mkdir()
        with tarfile.open(tree_tgz) as archive:
            archive.extractall(staging, numeric_owner=True, filter='fully_trusted')
        arch = tree / 'arch/amd64'
        arch.mkdir(parents=True)
        os.chmod(tree / 'arch', 0o755)
        os.chmod(arch, 0o755)
        (staging / 'rootfs').rename(arch / 'rootfs')
        (staging / 'opentak/venv').rename(arch / 'venv')
        # Host-side tools follow these links from outside the chroot, so make
        # them relative like the arm64 tree (ubuntu-base amd64 ships /run).
        for name in ('var/run', 'var/lock'):
            link, reference = arch / 'rootfs' / name, tree / 'rootfs' / name
            if link.is_symlink() and reference.is_symlink() and os.readlink(link) != os.readlink(reference):
                link.unlink()
                link.symlink_to(os.readlink(reference))
        media = arch / 'mediamtx'
        media.mkdir(mode=0o755)
        with tarfile.open(mtx_tgz) as archive:
            for name in ('mediamtx', 'LICENSE'):
                member = archive.getmember(name)
                data = archive.extractfile(member).read()
                (media / name).write_bytes(data)
                os.chmod(media / name, 0o755 if name == 'mediamtx' else 0o644)
        (arch / 'provenance.json').write_text(json.dumps(amd64_provenance, sort_keys=True, indent=2) + '\n')
        os.chmod(arch / 'provenance.json', 0o644)
        shutil.rmtree(staging)

        # Same rule as scrub-package-tests.py for the arm64 venv: dependency
        # test suites (which include sample private keys) are not shipped.
        site = arch / 'venv/lib/python3.12/site-packages'
        for path in sorted((p for p in site.rglob('*') if p.is_dir() and not p.is_symlink()
                            and p.name.lower() in {'test', 'tests'}),
                           key=lambda p: len(p.parts), reverse=True):
            if path.exists():
                shutil.rmtree(path)
        clean_base.audit_clean_tree(tree)
        (tree / 'portabletak/base-seal.json').unlink()
        new_seal = clean_base.seal(tree, provenance)
        clean_base.verify_seal(tree, new_seal)
        partial = out.with_name(out.name + '.partial')
        with tarfile.open(partial, 'w:gz') as archive:
            archive.add(tree, arcname='payload')
        partial.rename(out)
    digest = sha256(out)
    summary = media_layout.verify_archive(out, digest, new_seal)
    return {'archive': str(out), 'archive_sha256': digest, 'seal_sha256': new_seal,
            'source_seal_sha256': src_seal, 'members': summary['members'],
            'payload_bytes': summary['payload_bytes']}


if __name__ == '__main__':
    if len(sys.argv) != 10:
        raise SystemExit(__doc__)
    print(json.dumps(derive(*sys.argv[1:]), indent=2, sort_keys=True))
