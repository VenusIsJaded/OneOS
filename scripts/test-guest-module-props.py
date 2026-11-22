#!/usr/bin/env python3
"""Check restoration of module property overrides for soft reboot, in-place return, and cold prepare (A3, A8)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUEST_RUNTIME = ROOT / 'switcher/assets/guest_runtime.sh'
RETURN_SH = ROOT / 'switcher/assets/return_to_oneui.sh'


def make_fake_prop_tools(root: Path, noop: bool = False, ignore_empty_set: bool = False, leave_empty_on_delete: bool = False):
    (root / 'host_ksu/bin').mkdir(parents=True, exist_ok=True)
    (root / 'ksu/bin').mkdir(parents=True, exist_ok=True)
    (root / 'bin').mkdir(parents=True, exist_ok=True)
    (root / 'props').mkdir(parents=True, exist_ok=True)
    rp_body = (
        '#!/bin/sh\n'
        'printf "[%s][%s][%s]\\n" "$1" "$2" "${3-}" >> "$CALL_LOG"\n'
        '[ -n "${NOOP:-}" ] && exit 0\n'
        'case "$1" in\n'
        '  -n|-p)\n'
        '    if [ -n "${IGNORE_EMPTY_SET:-}" ] && [ -z "${3-}" ]; then exit 0; fi\n'
        '    printf "%s" "${3-}" > "$PROPS/$2"\n'
        '    ;;\n'
        '  --delete)\n'
        '    if [ -n "${LEAVE_EMPTY_ON_DELETE:-}" ]; then : > "$PROPS/$2"; exit 0; fi\n'
        '    rm -f "$PROPS/$2"\n'
        '    ;;\n'
        'esac\n'
    )
    rp_host = root / 'host_ksu/bin/resetprop'
    rp_host.write_text(rp_body)
    rp_host.chmod(0o755)
    rp = root / 'ksu/bin/resetprop'
    rp.write_text(rp_body)
    rp.chmod(0o755)
    gp = root / 'bin/getprop'
    gp.write_text(
        '#!/bin/sh\n'
        'if [ $# -eq 0 ]; then\n'
        '  for f in "$PROPS"/*; do\n'
        '    [ -f "$f" ] || continue\n'
        '    printf "[%s]: [%s]\\n" "${f##*/}" "$(cat "$f")"\n'
        '  done\n'
        '  exit 0\n'
        'fi\n'
        'if [ -f "$PROPS/$1" ]; then\n'
        '  cat "$PROPS/$1"\n'
        '  printf "\\n"\n'
        'else\n'
        '  printf "\\n"\n'
        'fi\n'
        'exit 0\n'
    )
    gp.chmod(0o755)
    env = {
        **os.environ,
        'CALL_LOG': str(root / 'calls'),
        'PROPS': str(root / 'props'),
        'ONEOS_HOST_RESETPROP': str(rp_host),
        'PATH': str(root / 'bin') + ':' + os.environ['PATH'],
    }
    if noop:
        env['NOOP'] = '1'
    if ignore_empty_set:
        env['IGNORE_EMPTY_SET'] = '1'
    if leave_empty_on_delete:
        env['LEAVE_EMPTY_ON_DELETE'] = '1'
    return env


class ModulePropsTest(unittest.TestCase):
    def run_host_restore(self, root: Path, env: dict, mode: str = 'all'):
        stage = root / 'stage'
        adb = stage / 'data/oneos_root/adb'
        adb.mkdir(parents=True, exist_ok=True)
        env2 = {**env, 'STAGE': str(stage)}
        return subprocess.run(
            ['sh', str(GUEST_RUNTIME), 'restore-props-host', mode],
            env=env2,
            capture_output=True,
            text=True,
        )

    def test_existing_empty_and_new_properties_restore_without_shell_evaluation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            (adb / 'ksu/bin').mkdir(parents=True)
            env = make_fake_prop_tools(adb)
            snap = adb / 'oneos/run/module-props'
            snap.write_text(
                'ro.test.present|1|original value\n'
                'ro.test.empty|1|\n'
                'ro.test.added|0|\n'
                'ro.test.quoted|1|$(touch /tmp/oneos-must-not-exist)|literal\n'
            )
            (adb / 'props/ro.test.added').write_text('module added')
            result = self.run_host_restore(root, env)
            self.assertEqual(0, result.returncode, result.stderr)
            calls = (adb / 'calls').read_text()
            self.assertIn('[-n][ro.test.present][original value]', calls)
            self.assertIn('[-n][ro.test.empty][]', calls)
            self.assertIn('[--delete][ro.test.added][]', calls)
            self.assertIn('$(touch /tmp/oneos-must-not-exist)|literal', calls)
            self.assertTrue((adb / 'props/ro.test.empty').exists(), 'present-empty property must exist after restore')
            self.assertFalse((adb / 'props/ro.test.added').exists(), 'absent property must be deleted')
            self.assertFalse(snap.exists())

    def test_present_empty_left_absent_fails_and_retains_snapshot_a8(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            env = make_fake_prop_tools(adb, ignore_empty_set=True)
            snap = adb / 'oneos/run/module-props'
            snap.write_text('ro.test.empty|1|\n')
            r = self.run_host_restore(root, env)
            self.assertEqual(1, r.returncode)
            self.assertTrue(snap.exists(), 'snapshot must be retained when present-empty key stays absent')
            self.assertIn('ro.test.empty was not restored', r.stderr)

    def test_absent_key_left_present_empty_fails_and_retains_snapshot_a8(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            env = make_fake_prop_tools(adb, leave_empty_on_delete=True)
            snap = adb / 'oneos/run/module-props'
            snap.write_text('ro.test.added|0|\n')
            (adb / 'props/ro.test.added').write_text('val')
            r = self.run_host_restore(root, env)
            self.assertEqual(1, r.returncode)
            self.assertTrue(snap.exists(), 'snapshot must be retained when deleted key remains present-empty')
            self.assertIn('ro.test.added was not removed', r.stderr)

    def test_noop_resetprop_keeps_snapshot_and_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            env = make_fake_prop_tools(adb, noop=True)
            snap = adb / 'oneos/run/module-props'
            snap.write_text('ro.test.present|1|original value\nro.test.added|0|\n')
            (adb / 'props/ro.test.present').write_text('module value')
            (adb / 'props/ro.test.added').write_text('module added')
            r = self.run_host_restore(root, env)
            self.assertEqual(1, r.returncode)
            self.assertTrue(snap.exists())
            self.assertIn('ro.test.present was not restored', r.stderr)
            self.assertIn('ro.test.added was not removed', r.stderr)
            self.assertIn('2 module properties not restored', r.stderr)

    def test_malformed_key_stops_before_write(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            env = make_fake_prop_tools(adb)
            snap = adb / 'oneos/run/module-props'
            snap.write_text('bad key|1|x\n')
            r = self.run_host_restore(root, env)
            self.assertEqual(1, r.returncode)
            self.assertTrue(snap.exists())

    def test_persist_only_mode_restores_persist_keys_and_retains_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            env = make_fake_prop_tools(adb)
            snap = adb / 'oneos/run/module-props'
            snap.write_text('ro.nonpersist|1|orig\npersist.mod.flag|1|off\npersist.mod.new|0|\n')
            (adb / 'props/ro.nonpersist').write_text('guest')
            (adb / 'props/persist.mod.flag').write_text('on')
            (adb / 'props/persist.mod.new').write_text('1')
            r = self.run_host_restore(root, env, mode='persist-only')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('off', (adb / 'props/persist.mod.flag').read_text())
            self.assertFalse((adb / 'props/persist.mod.new').exists())
            self.assertEqual('guest', (adb / 'props/ro.nonpersist').read_text())
            self.assertTrue(snap.exists(), 'persist-only keeps snapshot until full restore or new boot_id')

    def test_return_to_oneui_restores_module_props_on_inplace_and_reboot_paths_a3(self):
        ret = RETURN_SH.read_text()
        self.assertIn(
            'guest_runtime.sh restore-props-host all >> "$IPLOG" 2>&1 || failsafe',
            ret,
            'in-place return must restore module system.prop overrides or failsafe to reboot',
        )
        self.assertIn(
            'guest_runtime.sh restore-props-host persist-only',
            ret,
            'reboot return must restore persist.* module overrides',
        )
        gr = GUEST_RUNTIME.read_text()
        self.assertNotIn(
            'rm -f "$A/oneos/run/injection.pid" "$A/oneos/run/su.sock" "$A/oneos/run/zygisk/cp64.sock" "$A/oneos/run/module-props"',
            gr,
            'cold prepare must not blindly delete un-restored module-props on the same boot',
        )
        self.assertNotIn('restore-props-host persist-only 2>/dev/null || true', ret)
        self.assertNotIn('restore-props-host persist-only >> "$IPLOG" 2>&1 || true', ret)

    def test_multiline_and_backslash_values_round_trip_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            env = make_fake_prop_tools(adb)
            # Simulate pre-existing multiline/backslash property captured by encode_prop_val
            snap = adb / 'oneos/run/module-props'
            snap.write_text('persist.test.multi|1e|line1\\nline2\\\\tail|pipe\n')
            (adb / 'props/persist.test.multi').write_text('overridden')
            r = self.run_host_restore(root, env)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('line1\nline2\\tail|pipe', (adb / 'props/persist.test.multi').read_text())
            self.assertFalse(snap.exists())

    def test_invalid_present_metadata_fails_and_retains_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            env = make_fake_prop_tools(adb)
            snap = adb / 'oneos/run/module-props'
            snap.write_text('ro.test.key|2|orig\n')
            (adb / 'props/ro.test.key').write_text('orig')
            r = self.run_host_restore(root, env)
            self.assertEqual(1, r.returncode)
            self.assertIn('invalid present metadata', r.stderr)
            self.assertTrue(snap.exists())
            self.assertEqual('orig', (adb / 'props/ro.test.key').read_text())

    def test_end_to_end_capture_and_restore_preserves_backslashes_trailing_newlines_and_pipes(self):
        """Regression test for Section 2: literal \\n, consecutive backslashes, pipes, CR, interior/trailing newlines, and empty values."""
        gr = GUEST_RUNTIME.read_text()
        helpers = gr[gr.index('hex_encode_file() {'):gr.index('check_prop_snapshot_dirs() {')]
        pre_idx = gr.index('pre)\n')
        loop_start = gr.index('    B=/data/oneos_bin/busybox\n', pre_idx)
        loop_end = gr.index('    selected=$(cat "$A/oneos/framework")', loop_start)
        capture_loop = helpers + '\n' + gr[loop_start:loop_end]

        cases = {
            'persist.test.lit_nl': 'a\\nb',
            'persist.test.two_bs': 'x\\\\y\\\\',
            'persist.test.pipes': 'left|mid|right',
            'persist.test.crlf': 'r1\r\nr2\\r\\n',
            'persist.test.trailing_nl': 'hello\nworld\n\n',
            'persist.test.empty': '',
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            (adb / 'modules/mod1').mkdir(parents=True)
            env = make_fake_prop_tools(adb)
            for k, v in cases.items():
                (adb / 'props' / k).write_bytes(v.encode('utf-8'))
            (adb / 'modules/mod1/system.prop').write_text(
                ''.join(f'{k}=guest_override\n' for k in cases) + 'persist.test.added=guest_new\n'
            )
            bb = adb / 'bin/bb'
            bb.write_text('#!/bin/sh\nexec "$@"\n')
            bb.chmod(0o755)
            script = (
                f'A="{adb}"\n'
                + capture_loop
                .replace('B=/data/oneos_bin/busybox', f'B="{bb}"')
                .replace('export PATH=/system/bin:/system/xbin:/data/adb/ksu/bin', f'export PATH="{adb}/bin:$PATH"')
            )
            r_cap = subprocess.run(['dash', '-c', script], env=env, capture_output=True, text=True)
            self.assertEqual(0, r_cap.returncode, r_cap.stderr + r_cap.stdout)
            for k in cases:
                self.assertEqual('guest_override', (adb / 'props' / k).read_text())
            self.assertEqual('guest_new', (adb / 'props/persist.test.added').read_text())

            r_res = self.run_host_restore(root, env, mode='all')
            self.assertEqual(0, r_res.returncode, r_res.stderr + r_res.stdout)
            for k, expected in cases.items():
                self.assertEqual(expected.encode('utf-8'), (adb / 'props' / k).read_bytes(), f'mismatch for {k}')
            self.assertFalse((adb / 'props/persist.test.added').exists())
            self.assertFalse((adb / 'oneos/run/module-props').exists())

    def test_length_mismatch_in_hex_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'stage/data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            env = make_fake_prop_tools(adb)
            snap = adb / 'oneos/run/module-props'
            snap.write_text('persist.test.tampered|1h:5|4142\n')
            (adb / 'props/persist.test.tampered').write_text('keep')
            r = self.run_host_restore(root, env)
            self.assertEqual(1, r.returncode)
            self.assertTrue(snap.exists())
            self.assertEqual('keep', (adb / 'props/persist.test.tampered').read_text())

    def test_host_restore_ignores_guest_staged_resetprop_and_uses_host_baseline_provenance(self):
        """Regression test for Section 1/2: host restore must not execute guest-staged resetprop or trust forged snapshot values over host baselines."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'stage'
            adb = stage / 'data/oneos_root/adb'
            (adb / 'oneos/run').mkdir(parents=True)
            (stage / 'data/oneos_root/enabled').touch()
            env = make_fake_prop_tools(adb)
            # Replace guest-staged resetprop with a trojan regular executable
            marker = root / 'trojan_ran'
            trojan = adb / 'ksu/bin/resetprop'
            trojan.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
            trojan.chmod(0o755)
            # Host baseline says persist.test.host was 'real-host-val', not 'guest-forged-val'
            (stage / 'persist_props.snapshot').write_text('persist.test.host=real-host-val\n')
            (stage / 'persist_session.active').write_text('format=2\n')
            (adb / 'props/persist.test.host').write_text('guest-live-val')
            (adb / 'oneos/run/module-props').write_text('persist.test.host|1|guest-forged-val\n')

            r = self.run_host_restore(root, env)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertFalse(marker.exists(), 'host restore must never execute guest-staged resetprop')
            self.assertEqual('real-host-val', (adb / 'props/persist.test.host').read_text())

    def test_host_restore_refuses_deleted_adb_dir_when_guest_root_enabled_and_directory_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'stage'
            (stage / 'data/oneos_root').mkdir(parents=True)
            (stage / 'data/oneos_root/enabled').touch()
            # STATE/adb deleted by guest to suppress restore
            r = subprocess.run(
                ['sh', str(GUEST_RUNTIME), 'restore-props-host', 'all'],
                env={**os.environ, 'STAGE': str(stage)},
                capture_output=True, text=True,
            )
            self.assertNotEqual(0, r.returncode)

            # Directory named module-props instead of regular file must fail closed
            adb = stage / 'data/oneos_root/adb'
            (adb / 'oneos/run/module-props').mkdir(parents=True)
            env = make_fake_prop_tools(adb)
            r2 = self.run_host_restore(root, env)
            self.assertNotEqual(0, r2.returncode)


if __name__ == '__main__':
    unittest.main()
