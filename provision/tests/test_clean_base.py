import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import clean_base as base

class CleanBaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.archive=self.root/'source.tar.gz'
        self.sums=self.root/'SHA256SUMS'
        self.tree=self.root/'tree'
        self.tree.mkdir()
        (self.tree/'portabletak').mkdir()
        (self.tree/'portabletak/cartridge.conf').write_text('PORTABLETAK_FORMAT=1\n')
        self.provenance={'ubuntu_archive':'upstream.tar.gz','ubuntu_sha256':'a'*64,'apt_packages':{'python3':'test'},'python_packages':{'opentakserver':'1.7.13'}}
    def tearDown(self): self.temp.cleanup()
    def tar(self, entries):
        with tarfile.open(self.archive,'w:gz') as t:
            for name,kind,content in entries:
                member=tarfile.TarInfo(name)
                if kind=='file':
                    member.size=len(content)
                    t.addfile(member,io.BytesIO(content))
                else:
                    member.type=tarfile.SYMTYPE
                    member.linkname=content
                    t.addfile(member)
        self.sums.write_text(hashlib.sha256(self.archive.read_bytes()).hexdigest()+' *source.tar.gz\n')
    def test_checksum_verified(self):
        self.tar([('hello','file',b'hello')])
        self.assertEqual(len(base.verify_download(self.archive,self.sums)),64)
    def test_wrong_checksum_refused(self):
        self.tar([('hello','file',b'hello')])
        self.sums.write_text('0'*64+' *source.tar.gz\n')
        with self.assertRaisesRegex(base.BaseError,'source_checksum_mismatch'):
            base.extract_verified(self.archive,self.sums,self.root/'out')
        self.assertFalse((self.root/'out').exists())
    def test_traversal_refused(self):
        self.tar([('../outside','file',b'bad')])
        with self.assertRaises(base.BaseError): base.inspect_archive(self.archive)
    def test_absolute_path_refused(self):
        self.tar([('/outside','file',b'bad')])
        with self.assertRaises(base.BaseError): base.inspect_archive(self.archive)
    def test_symlink_write_refused(self):
        self.tar([('a','link','b'),('a/payload','file',b'bad')])
        with self.assertRaisesRegex(base.BaseError,'archive_writes_through_link'): base.inspect_archive(self.archive)
    def test_escape_link_refused(self):
        self.tar([('a','link','../outside')])
        with self.assertRaises(base.BaseError): base.inspect_archive(self.archive)
    def test_absolute_rootfs_link_normalized(self):
        self.tar([('usr/bin/a','file',b'code'),('bin/a','link','/usr/bin/a')])
        out=self.root/'out'
        base.extract_verified(self.archive,self.sums,out)
        self.assertEqual((out/'bin/a').resolve(),out/'usr/bin/a')
        self.assertEqual((out/'bin/a').read_bytes(),b'code')
    def test_existing_target_refused(self):
        self.tar([('hello','file',b'hello')])
        with self.assertRaisesRegex(base.BaseError,'extraction_target_must_be_new'): base.extract_verified(self.archive,self.sums,self.tree)
    def test_seal_roundtrip(self):
        (self.tree/'code').write_text('generic software')
        checksum=base.seal(self.tree,self.provenance)
        self.assertTrue(base.verify_seal(self.tree,checksum))
    def test_changed_file_refused(self):
        (self.tree/'code').write_text('generic software')
        checksum=base.seal(self.tree,self.provenance)
        (self.tree/'code').write_text('modified')
        with self.assertRaisesRegex(base.BaseError,'base_content_changed'): base.verify_seal(self.tree,checksum)
    def test_cartridge_identity_can_be_published_after_sealing(self):
        (self.tree/'code').write_text('generic software')
        checksum=base.seal(self.tree,self.provenance)
        (self.tree/'portabletak/cartridge.conf').write_text('PORTABLETAK_FORMAT=1\nPORTABLETAK_CARTRIDGE_ID=fresh\n')
        self.assertTrue(base.verify_seal(self.tree,checksum))
    def test_added_file_refused(self):
        checksum=base.seal(self.tree,self.provenance)
        (self.tree/'unexpected').touch()
        with self.assertRaises(base.BaseError): base.verify_seal(self.tree,checksum)
    def test_private_staging_tree_allowed_by_staged_verifier(self):
        (self.tree/'code').write_text('generic software')
        checksum=base.seal(self.tree,self.provenance)
        (self.tree/'portabletak/provision/attempt').mkdir(parents=True)
        (self.tree/'portabletak/provision/attempt/status.json').write_text('{}')
        self.assertTrue(base.verify_staged_seal(self.tree,checksum))
    def test_staged_verifier_rejects_other_added_files(self):
        checksum=base.seal(self.tree,self.provenance)
        (self.tree/'portabletak/provision').mkdir()
        (self.tree/'rootfs/added-code').parent.mkdir(exist_ok=True)
        (self.tree/'rootfs/added-code').write_text('bad')
        with self.assertRaisesRegex(base.BaseError,'base_content_changed'):
            base.verify_staged_seal(self.tree,checksum)
    def test_seal_tamper_refused(self):
        checksum=base.seal(self.tree,self.provenance)
        (self.tree/'portabletak/base-seal.json').write_text('{}')
        with self.assertRaisesRegex(base.BaseError,'base_seal_mismatch'): base.verify_seal(self.tree,checksum)
    def test_persistent_state_refused(self):
        (self.tree/'postgres').mkdir()
        (self.tree/'postgres/PG_VERSION').write_text('16')
        with self.assertRaises(base.BaseError): base.seal(self.tree,self.provenance)
    def test_private_key_refused(self):
        (self.tree/'key').write_bytes(b'-----BEGIN PRIVATE KEY-----\ndGVzdA==\n-----END PRIVATE KEY-----\n')
        with self.assertRaisesRegex(base.BaseError,'private_key_present'): base.audit_clean_tree(self.tree)
    def test_private_key_parser_string_accepted(self):
        (self.tree/'library').write_bytes(b'\x00-----BEGIN PRIVATE KEY-----\nparser error\x00')
        self.assertTrue(base.audit_clean_tree(self.tree))
    def test_shadow_password_refused(self):
        (self.tree/'rootfs/etc').mkdir(parents=True)
        (self.tree/'rootfs/etc/shadow').write_text('root:$6$example:1:0:99999:7:::\n')
        with self.assertRaisesRegex(base.BaseError,'login_credential_present'): base.audit_clean_tree(self.tree)
    def test_locked_shadow_accepted(self):
        (self.tree/'rootfs/etc').mkdir(parents=True)
        (self.tree/'rootfs/etc/shadow').write_text('root:*:1:0:99999:7:::\n')
        self.assertTrue(base.audit_clean_tree(self.tree))
    def test_identity_cookie_refused(self):
        (self.tree/'.erlang.cookie').write_text('example')
        with self.assertRaisesRegex(base.BaseError,'identity_artifact_present'): base.audit_clean_tree(self.tree)
    def test_mutable_parent_symlink_refused(self):
        (self.tree/'rootfs').symlink_to(self.root)
        with self.assertRaises(base.BaseError): base.audit_clean_tree(self.tree)

if __name__=='__main__': unittest.main()


class SealedSoftwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'tree'
        (self.root / 'portabletak').mkdir(parents=True)
        (self.root / 'portabletak/cartridge.conf').write_text('PORTABLETAK_FORMAT=1\n')
        (self.root / 'rootfs/usr/bin').mkdir(parents=True)
        (self.root / 'rootfs/usr/bin/tool').write_text('tool')
        (self.root / 'rootfs/run').mkdir()
        (self.root / 'rootfs/bin').symlink_to('usr/bin')
        provenance = {'ubuntu_archive': 'u.tar.gz', 'ubuntu_sha256': 'a' * 64,
                      'apt_packages': {'x': '1'}, 'python_packages': {'y': '1'}}
        self.seal = base.seal(self.root, provenance)

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_data_and_mount_modes_allowed(self):
        (self.root / 'postgres').mkdir()
        (self.root / 'postgres/PG_VERSION').write_text('16')
        os.chmod(self.root / 'rootfs/run', 0o700)
        (self.root / 'rootfs/run/pid').write_text('1')
        self.assertTrue(base.verify_sealed_software(self.root, self.seal))

    def test_changed_software_refused(self):
        (self.root / 'rootfs/usr/bin/tool').write_text('trojan')
        with self.assertRaisesRegex(base.BaseError, 'base_content_changed:rootfs/usr/bin/tool'):
            base.verify_sealed_software(self.root, self.seal)

    def test_changed_mode_and_link_refused(self):
        os.chmod(self.root / 'rootfs/usr/bin/tool', 0o4755)
        with self.assertRaisesRegex(base.BaseError, 'base_content_changed'):
            base.verify_sealed_software(self.root, self.seal)
        os.chmod(self.root / 'rootfs/usr/bin/tool', 0o644)
        (self.root / 'rootfs/bin').unlink()
        (self.root / 'rootfs/bin').symlink_to('/tmp')
        with self.assertRaisesRegex(base.BaseError, 'base_content_changed:rootfs/bin'):
            base.verify_sealed_software(self.root, self.seal)

    def test_wrong_seal_refused(self):
        with self.assertRaisesRegex(base.BaseError, 'base_seal_mismatch'):
            base.verify_sealed_software(self.root, '0' * 64)
