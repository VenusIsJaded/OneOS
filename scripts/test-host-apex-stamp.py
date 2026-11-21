#!/usr/bin/env python3
"""check_host_apex.sh: fail-closed manifest/cache-drop validation and NUL-safe file reads (N11, A6, A18)."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'switcher/assets/check_host_apex.sh').read_text()
LIST = ('<apex-info-list>\n'
        '<apex-info moduleName="com.x" modulePath="/data/apex/active/com.x@2.apex" isActive="true" />\n'
        '<apex-info moduleName="com.sys" modulePath="/system/apex/com.sys.apex" isActive="true" />\n'
        '<apex-info moduleName="com.old" modulePath="/data/apex/active/com.old@1.apex" isActive="false" />\n'
        '</apex-info-list>\n')


class Phone:
    def __init__(self, root):
        self.root = Path(root)
        for m in ('com.x', 'com.sys', 'com.old'):
            (self.root / 'apex' / m / 'lib').mkdir(parents=True)
            (self.root / 'apex' / m / 'lib/a.so').write_text('a')
        (self.root / 'apex/com.x/manifest').write_text('m')
        (self.root / 'apex/apex-info-list.xml').write_text(LIST)
        self.stamp = self.root / 'stamp'
        self.calls = self.root / 'calls'
        self.drop_target = str(self.root / 'dropped')

    def look(self, label):
        """(line, exit code, whether the files were read from disk, whether a stamp is left)"""
        self.calls.unlink(missing_ok=True)
        script = ('sync() { echo sync >> "$CALLS"; }\n' +
                  SOURCE.replace('/proc/sys/vm/drop_caches', self.drop_target))
        out = subprocess.run(
            ['/bin/sh', '-c', script, 'check', label],
            capture_output=True,
            text=True,
            env=dict(
                os.environ,
                ONEOS_APEX_STAMP=str(self.stamp),
                ONEOS_APEX_INFO_LIST=str(self.root / 'apex/apex-info-list.xml'),
                ONEOS_APEX_ROOT=str(self.root / 'apex'),
                TMPDIR=str(self.root),
                CALLS=str(self.calls),
            ),
        )
        return out.stdout.strip(), out.returncode, self.calls.exists(), self.stamp.exists()


class HostApexStamp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.phone = Phone(self.tmp.name)

    def test_the_line_counts_the_active_modules_on_data(self):
        line, code, disk, stamp = self.phone.look('before-setup')
        self.assertEqual('[before-setup] host APEXes on /data: 1 modules, 2 files, 0 unreadable (read from disk)', line)
        self.assertEqual((0, True, False), (code, disk, stamp))

    def test_only_the_final_look_of_a_return_leaves_a_stamp(self):
        for label, left in (('before-setup', False), ('after-setup', False), ('after-return', False), ('after-inplace', True)):
            with self.subTest(label):
                self.phone.stamp.unlink(missing_ok=True)
                _, code, disk, stamp = self.phone.look(label)
                self.assertEqual((0, True, left), (code, disk, stamp))

    def test_the_next_sessions_first_look_keeps_the_cache_once(self):
        self.phone.look('after-inplace')
        line, code, disk, stamp = self.phone.look('before-setup')
        self.assertIn('page cache kept', line)
        self.assertEqual((0, False, False), (code, disk, stamp))
        for label in ('after-setup', 'before-setup'):
            self.assertEqual((True, False), self.phone.look(label)[2:], label)

    def test_a_look_after_the_setup_never_trusts_the_stamp(self):
        for label in ('after-setup', 'after-return', 'after-inplace'):
            with self.subTest(label):
                self.phone.look('after-inplace')
                line, _, disk, _ = self.phone.look(label)
                self.assertTrue(disk)
                self.assertIn('(read from disk)', line)

    def test_a_stamp_of_another_boot_or_another_module_list_is_not_trusted(self):
        self.phone.look('after-inplace')
        boot, sig = self.phone.stamp.read_text().split()
        self.phone.stamp.write_text(f'00000000-0000-0000-0000-000000000000 {sig}\n')
        self.assertTrue(self.phone.look('before-setup')[2])
        self.phone.look('after-inplace')
        (self.phone.root / 'apex/apex-info-list.xml').write_text(LIST + '\n')
        self.assertTrue(self.phone.look('before-setup')[2])

    @unittest.skipIf(os.geteuid() == 0, 'root reads every file')
    def test_an_unreadable_file_fails_the_look_and_leaves_no_stamp(self):
        bad = self.phone.root / 'apex/com.x/lib/a.so'
        bad.chmod(0)
        line, code, disk, stamp = self.phone.look('after-inplace')
        bad.chmod(0o644)
        self.assertIn(f'1 unreadable: {bad}', line)
        self.assertEqual((1, True, False), (code, disk, stamp))


class HostApexIncompleteWalk(unittest.TestCase):
    """A look that could not read the files or manifest must fail closed and leave no stamp (N11, A6, A18)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.phone = Phone(self.tmp.name)

    def test_missing_manifest_fails_closed_a6(self):
        (self.phone.root / 'apex/apex-info-list.xml').unlink()
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertIn('manifest unreadable', line)
        self.assertEqual((1, False), (code, stamp))

    @unittest.skipIf(os.geteuid() == 0, 'root reads mode 000 manifest')
    def test_unreadable_manifest_fails_closed_and_leaves_no_stamp_a6(self):
        xml = self.phone.root / 'apex/apex-info-list.xml'
        xml.chmod(0)
        self.addCleanup(xml.chmod, 0o644)
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertIn('manifest unreadable', line)
        self.assertEqual((1, False), (code, stamp))

    def test_manifest_without_active_entries_fails_closed_a6(self):
        (self.phone.root / 'apex/apex-info-list.xml').write_text('<apex-info-list></apex-info-list>\n')
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertIn('no active APEX entries', line)
        self.assertEqual((1, False), (code, stamp))

    def test_system_only_active_apex_manifest_succeeds_with_zero_data_modules(self):
        (self.phone.root / 'apex/apex-info-list.xml').write_text(
            '<apex-info-list>\n'
            '<apex-info moduleName="com.sys" modulePath="/system/apex/com.sys.apex" isActive="true" />\n'
            '</apex-info-list>\n'
        )
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertIn('0 modules, 0 files, 0 unreadable', line)
        self.assertEqual((0, True), (code, stamp))

    def test_a_missing_module_directory_is_unreadable_and_leaves_no_stamp(self):
        shutil.rmtree(self.phone.root / 'apex/com.x')
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertIn('1 unreadable', line)
        self.assertIn('(missing)', line)
        self.assertEqual((1, False), (code, stamp))

    def test_a_module_with_no_files_is_unreadable(self):
        shutil.rmtree(self.phone.root / 'apex/com.x/lib')
        (self.phone.root / 'apex/com.x/manifest').unlink()
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertIn('(no files)', line)
        self.assertEqual((1, False), (code, stamp))

    @unittest.skipIf(os.geteuid() == 0, 'root can list a mode 000 directory')
    def test_a_directory_that_cannot_be_listed_fails_the_look(self):
        sub = self.phone.root / 'apex/com.x/lib'
        os.chmod(sub, 0)
        self.addCleanup(os.chmod, sub, 0o755)
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertIn('(not listable)', line)
        self.assertEqual((1, False), (code, stamp))

    def test_a_failed_drop_of_the_page_cache_fails_closed_and_leaves_no_stamp_a6(self):
        self.phone.drop_target = '/proc/nonexistent/drop'
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertIn('page cache NOT dropped', line)
        self.assertNotIn('(read from disk)', line)
        self.assertEqual((1, False), (code, stamp), 'failed drop_caches is not a disk read and must return non-zero')

    def test_newline_filename_in_apex_is_preserved_nul_safely_a18(self):
        weird = self.phone.root / 'apex/com.x/lib/line\nbreak.so'
        weird.write_text('ok')
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertEqual('[after-inplace] host APEXes on /data: 1 modules, 3 files, 0 unreadable (read from disk)', line)
        self.assertEqual((0, True), (code, stamp))

    def test_empty_active_module_with_nonexistent_tmpdir_still_fails_closed(self):
        shutil.rmtree(self.phone.root / 'apex/com.x/lib')
        (self.phone.root / 'apex/com.x/manifest').unlink()
        script = ('sync() { echo sync >> "$CALLS"; }\n' +
                  (ROOT / 'switcher/assets/check_host_apex.sh').read_text().replace('/proc/sys/vm/drop_caches', self.phone.drop_target))
        out = subprocess.run(
            ['/bin/sh', '-c', script, 'check', 'after-inplace'],
            capture_output=True,
            text=True,
            env=dict(
                os.environ,
                ONEOS_APEX_STAMP=str(self.phone.stamp),
                ONEOS_APEX_INFO_LIST=str(self.phone.root / 'apex/apex-info-list.xml'),
                ONEOS_APEX_ROOT=str(self.phone.root / 'apex'),
                TMPDIR=str(self.phone.root / 'nonexistent-tmp'),
                CALLS=str(self.phone.calls),
            ),
        )
        self.assertEqual(1, out.returncode, out.stdout)
        self.assertFalse(self.phone.stamp.exists(), 'empty module with bad TMPDIR must never leave a stamp')
        self.assertTrue('(no files)' in out.stdout or '(mktemp failed)' in out.stdout, out.stdout)

    def test_mktemp_failure_fails_closed_without_stamp(self):
        script = ('sync() { echo sync >> "$CALLS"; }\n'
                  'mktemp() { return 1; }\n' +
                  (ROOT / 'switcher/assets/check_host_apex.sh').read_text().replace('/proc/sys/vm/drop_caches', self.phone.drop_target))
        out = subprocess.run(
            ['/bin/sh', '-c', script, 'check', 'after-inplace'],
            capture_output=True,
            text=True,
            env=dict(
                os.environ,
                ONEOS_APEX_STAMP=str(self.phone.stamp),
                ONEOS_APEX_INFO_LIST=str(self.phone.root / 'apex/apex-info-list.xml'),
                ONEOS_APEX_ROOT=str(self.phone.root / 'apex'),
                CALLS=str(self.phone.calls),
            ),
        )
        self.assertEqual(1, out.returncode, out.stdout)
        self.assertIn('(mktemp failed)', out.stdout)
        self.assertFalse(self.phone.stamp.exists())

    def test_a_working_look_is_unchanged(self):
        line, code, _, stamp = self.phone.look('after-inplace')
        self.assertEqual('[after-inplace] host APEXes on /data: 1 modules, 2 files, 0 unreadable (read from disk)', line)
        self.assertEqual((0, True), (code, stamp))


if __name__ == '__main__':
    unittest.main()
