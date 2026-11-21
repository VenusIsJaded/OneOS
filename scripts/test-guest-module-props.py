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
    (root / 'ksu/bin').mkdir(parents=True, exist_ok=True)
    (root / 'bin').mkdir(parents=True, exist_ok=True)
    (root / 'props').mkdir(parents=True, exist_ok=True)
    rp = root / 'ksu/bin/resetprop'
    rp.write_text(
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
        '[ -f "$PROPS/$1" ] && cat "$PROPS/$1"\n'
        'exit 0\n'
    )
    gp.chmod(0o755)
    env = {
        **os.environ,
        'CALL_LOG': str(root / 'calls'),
        'PROPS': str(root / 'props'),
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


if __name__ == '__main__':
    unittest.main()
