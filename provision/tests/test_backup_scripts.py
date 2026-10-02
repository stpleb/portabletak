"""Runs the real host backup/restore scripts against a fake cartridge.

mount, flock, and the chroot'd pg_dump are replaced by stubs on PATH; the
restore test stops at the host prepare step, after every integrity check.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

BIN = Path(os.environ.get('PORTABLETAK_HOST_BIN', Path(__file__).resolve().parents[2] /
                          'host-installer-20260929/payload/usr/local/portabletak/bin'))


# The host scripts end early with a top-level `return`, which BusyBox ash
# treats as exit. Other /bin/sh implementations keep running, so restore
# behaviour is only meaningful under ash.
ASH = os.path.realpath('/bin/sh').endswith('busybox')


def stub(directory, name, body):
    path = directory / name
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(0o755)


@unittest.skipUnless((BIN / 'portabletak-backup').is_file(), 'host scripts not found')
class BackupScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.mount = root / 'cartridge'
        (self.mount / 'portabletak').mkdir(parents=True)
        (self.mount / 'portabletak/cartridge.conf').write_text(
            'PORTABLETAK_CARTRIDGE_ID=dbaebcba-0000-4000-8000-000000000001\n'
            'PORTABLETAK_INSTANCE_NAME=Field Team\n')
        (self.mount / 'config/secrets').mkdir(parents=True)
        (self.mount / 'config/secrets/token').write_text('secret')
        (self.mount / 'opentak/data/ca').mkdir(parents=True)
        (self.mount / 'opentak/data/ca/ca.pem').write_text('cert')
        (self.mount / 'postgres').mkdir()
        self.stubs = root / 'stubs'
        self.stubs.mkdir()
        stub(self.stubs, 'mount', f'echo "/dev/sdz1 on {self.mount} type ext4 (rw)"')
        stub(self.stubs, 'flock', 'exit 0')
        if not shutil.which('sha256sum'):
            stub(self.stubs, 'sha256sum', 'exec shasum -a 256 "$@"')
        self.chroot = self.stubs / 'fake-chroot'
        stub(self.stubs, 'fake-chroot', 'printf "PGDMP-dump"')
        self.backups = self.mount / 'portabletak/backups/recovery'

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, name, *args, **env):
        environment = dict(os.environ, PATH=f'{self.stubs}:{os.environ["PATH"]}',
                           PORTABLETAK_MOUNT=str(self.mount),
                           PORTABLETAK_CHROOT=str(self.chroot),
                           PORTABLETAK_RUNTIME=str(self.stubs / 'runtime'), **env)
        result = subprocess.run(['sh', str(BIN / name), *args], env=environment,
                                capture_output=True, text=True, timeout=60)
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        return result.returncode, values

    def backup(self, *args):
        code, values = self.run_script('portabletak-backup', *args)
        return code, values

    def only_final_files(self):
        return sorted(p.name for p in self.backups.iterdir())

    def test_complete_backup_is_checksummed_and_atomic(self):
        code, values = self.backup('--note', 'Before Node02 test (run 1)')
        self.assertEqual((code, values['PORTABLETAK_BACKUP']), (0, 'COMPLETE'))
        final = Path(values['BACKUP_FILE'])
        self.assertEqual(self.only_final_files(), [final.name, final.name + '.json'])
        sidecar = json.loads(Path(str(final) + '.json').read_text())
        self.assertEqual(sidecar['sha256'], hashlib.sha256(final.read_bytes()).hexdigest())
        self.assertEqual(sidecar['note'], 'Before Node02 test (run 1)')
        self.assertEqual(sidecar['size'], final.stat().st_size)
        with tarfile.open(final) as archive:
            manifest = archive.extractfile('manifest.txt').read().decode()
            sums = archive.extractfile('files.sha256').read().decode()
            dump = archive.extractfile('files/ots.dump').read()
        self.assertIn('PORTABLETAK_BACKUP_FORMAT=1\n', manifest)
        self.assertIn('INSTANCE_NAME=Field Team\n', manifest)
        self.assertIn('CREATED_LOCAL=', manifest)
        self.assertIn(hashlib.sha256(dump).hexdigest() + '  files/ots.dump', sums)

    def test_unmounted_cartridge_creates_nothing(self):
        stub(self.stubs, 'mount', 'echo "/dev/root on / type ext4 (rw)"')
        code, values = self.backup()
        self.assertEqual((code, values['ERROR']), (10, 'cartridge_not_mounted'))
        self.assertFalse((self.mount / 'portabletak/backups').exists())

    def test_failed_dump_leaves_no_backup(self):
        stub(self.stubs, 'fake-chroot', 'exit 1')
        code, values = self.backup()
        self.assertEqual((code, values['ERROR']), (20, 'database_dump_failed'))
        self.assertEqual(self.only_final_files(), [])

    def test_failed_data_archive_is_not_complete(self):
        shutil.rmtree(self.mount / 'opentak/data')
        code, values = self.backup()
        self.assertEqual(values['ERROR'], 'data_archive_failed')
        self.assertEqual(self.only_final_files(), [])

    def test_note_restricted(self):
        for note in ('$(reboot)', 'quote"s', 'x' * 121):
            code, values = self.backup('--note', note)
            self.assertNotEqual(code, 0)
            self.assertFalse(self.backups.exists())

    def test_insufficient_space_refused(self):
        stub(self.stubs, 'df', 'echo "Filesystem 1024-blocks Used Available Capacity Mounted"; '
                               'echo "/dev/sdz1 100 90 10 90% /x"')
        code, values = self.backup()
        self.assertEqual((code, values['ERROR']), (15, 'insufficient_space'))
        self.assertEqual(self.only_final_files(), [])

    def test_keep_prunes_oldest_with_sidecars(self):
        self.backups.mkdir(parents=True)
        for stamp in ('20200101_000000', '20200102_000000'):
            (self.backups / f'portabletak-{stamp}.tar.gz').write_text('old')
            (self.backups / f'portabletak-{stamp}.tar.gz.json').write_text('{}')
        code, values = self.backup('--keep', '2')
        self.assertEqual(code, 0)
        self.assertEqual(values['BACKUP_PRUNED'], 'portabletak-20200101_000000.tar.gz')
        names = self.only_final_files()
        self.assertNotIn('portabletak-20200101_000000.tar.gz.json', names)
        self.assertIn('portabletak-20200102_000000.tar.gz', names)

    @unittest.skipUnless(ASH, 'restore semantics require BusyBox ash')
    def test_restore_verifies_checksums_before_touching_runtime(self):
        code, values = self.backup()
        final = Path(values['BACKUP_FILE'])
        code, values = self.run_script('portabletak-restore', 'verify', final.name)
        # Integrity passed; the Mac has no host prepare step to run next.
        self.assertEqual(values['ERROR'], 'prepare_failed')
        with final.open('r+b') as stream:
            stream.seek(-10, 2)
            stream.write(b'0000000000')
        code, values = self.run_script('portabletak-restore', 'verify', final.name)
        self.assertEqual(values['ERROR'], 'backup_archive_checksum_mismatch')

    @unittest.skipUnless(ASH, 'restore semantics require BusyBox ash')
    def test_restore_detects_changed_member_without_sidecar(self):
        code, values = self.backup()
        final = Path(values['BACKUP_FILE'])
        Path(str(final) + '.json').unlink()
        work = Path(self.temp.name) / 'repack'
        with tarfile.open(final) as archive:
            archive.extractall(work, filter='data')
        (work / 'files/ots.dump').write_bytes(b'PGDMP-tampered')
        with tarfile.open(final, 'w:gz') as archive:
            for name in ('manifest.txt', 'files.sha256', 'files'):
                archive.add(work / name, arcname=name)
        code, values = self.run_script('portabletak-restore', 'verify', final.name)
        self.assertEqual(values['ERROR'], 'backup_file_checksum_mismatch')


if __name__ == '__main__':
    unittest.main()
