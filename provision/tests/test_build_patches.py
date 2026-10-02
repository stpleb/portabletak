import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
from build_ca_module import build
spec=importlib.util.spec_from_file_location('patch_ots',ROOT/'build/patch-ots.py')
patcher=importlib.util.module_from_spec(spec); spec.loader.exec_module(patcher)
class BuildPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        for name in ('app.py','certificate_authority.py'):
            shutil.copyfile(ROOT/'tests/fixtures/ots'/name,self.root/name)
        (self.root/'cot_parser').mkdir()
        shutil.copyfile(ROOT/'tests/fixtures/ots/cot_parser.py',self.root/'cot_parser/cot_parser.py')
        self.manifest=ROOT/'templates/ots-source.json'
    def tearDown(self): self.temp.cleanup()
    def test_reviewed_upstream_patches(self):
        patcher.patch(self.root,self.manifest)
        ca=(self.root/'certificate_authority.py').read_text()
        app=(self.root/'app.py').read_text()
        self.assertNotIn('shell=True',ca)
        self.assertNotIn('ASGARD',ca)
        self.assertIn('invalid_certificate_name',ca)
        self.assertIn('_pt_quote(app.config["OTS_RABBITMQ_PASSWORD"], safe="")',app)
        compile(ca,'ca','exec'); compile(app,'app','exec')
        for name in ('app.py','certificate_authority.py'):
            self.assertEqual((self.root/(name+'.upstream')).read_bytes(),(ROOT/'tests/fixtures/ots'/name).read_bytes())
    def test_cot_parser_queue_gets_credentials(self):
        patcher.patch(self.root,self.manifest)
        cot=(self.root/'cot_parser/cot_parser.py').read_text()
        self.assertNotIn(patcher.COT_QUEUE_OLD,cot)
        self.assertIn('_pt_quote(app.config["OTS_RABBITMQ_PASSWORD"], safe="")',cot)
        self.assertTrue(cot.startswith('from urllib.parse import quote as _pt_quote\n'))
        compile(cot,'cot','exec')
        self.assertEqual((self.root/'cot_parser/cot_parser.py.upstream').read_bytes(),
                         (ROOT/'tests/fixtures/ots/cot_parser.py').read_bytes())
    def test_modified_cot_parser_refused(self):
        with (self.root/'cot_parser/cot_parser.py').open('a') as f: f.write('\n# changed\n')
        with self.assertRaisesRegex(ValueError,'unreviewed_ots_cot_parser_source'):
            patcher.patch_cot_parser(self.root,json.loads(self.manifest.read_text())['cot_parser_sha256'])
        self.assertFalse((self.root/'cot_parser/cot_parser.py.upstream').exists())
    def test_modified_app_refuses_all_changes(self):
        with (self.root/'app.py').open('a') as f: f.write('\n# changed\n')
        original=(self.root/'certificate_authority.py').read_bytes()
        with self.assertRaisesRegex(ValueError,'unreviewed_ots_app_source'):
            patcher.patch(self.root,self.manifest)
        self.assertEqual((self.root/'certificate_authority.py').read_bytes(),original)
        self.assertFalse((self.root/'certificate_authority.py.new').exists())
    def test_modified_ca_refuses(self):
        (self.root/'certificate_authority.py').write_text('# changed')
        with self.assertRaisesRegex(ValueError,'reviewed source'):
            patcher.patch(self.root,self.manifest)
        self.assertFalse((self.root/'app.py.upstream').exists())
    def test_second_patch_refuses(self):
        patcher.patch(self.root,self.manifest)
        original=(self.root/'app.py').read_bytes()
        with self.assertRaises(ValueError): patcher.patch(self.root,self.manifest)
        self.assertEqual((self.root/'app.py').read_bytes(),original)
if __name__=='__main__': unittest.main()
