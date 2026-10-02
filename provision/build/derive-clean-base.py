"""Derive a new sealed clean base from a verified earlier one. Root Linux only.

Used for v3: v2 plus the reviewed cot_parser credential patch. The source
archive is proven against its pinned digest and embedded seal, extracted
faithfully (links, set-id modes, numeric owners), patched, re-sealed with the
source seal's provenance, packed under payload/, and the result is proven
again the same way before anything is reported.

    derive-clean-base.py SRC_ARCHIVE SRC_ARCHIVE_SHA256 SRC_SEAL MANIFEST OUT_ARCHIVE
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import clean_base
import media_layout

OTS_PACKAGE = 'opentak/venv/lib/python3.12/site-packages/opentakserver'


def load_patcher():
    spec = importlib.util.spec_from_file_location('patch_ots', ROOT / 'build/patch-ots.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def derive(src, src_sha, src_seal, manifest, out):
    if os.geteuid() != 0:
        raise SystemExit('root required to preserve ownership')
    out = Path(out)
    if out.exists():
        raise SystemExit('output exists')
    media_layout.verify_archive(src, src_sha, src_seal)
    with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
        tree = Path(temporary) / 'payload'
        tree.mkdir(mode=0o700)
        media_layout.extract_payload(src, tree)
        clean_base.verify_seal(tree, src_seal)
        seal_path = tree / 'portabletak/base-seal.json'
        provenance = json.loads(seal_path.read_text())['provenance']
        expected = json.loads(Path(manifest).read_text())['cot_parser_sha256']
        load_patcher().patch_cot_parser(tree / OTS_PACKAGE, expected)
        seal_path.unlink()
        new_seal = clean_base.seal(tree, provenance)
        clean_base.verify_seal(tree, new_seal)
        partial = out.with_name(out.name + '.partial')
        with tarfile.open(partial, 'w:gz') as archive:
            archive.add(tree, arcname='payload')
        partial.rename(out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    summary = media_layout.verify_archive(out, digest, new_seal)
    return {'archive': str(out), 'archive_sha256': digest, 'seal_sha256': new_seal,
            'source_seal_sha256': src_seal, 'members': summary['members'],
            'payload_bytes': summary['payload_bytes']}


if __name__ == '__main__':
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    print(json.dumps(derive(*sys.argv[1:]), indent=2, sort_keys=True))
