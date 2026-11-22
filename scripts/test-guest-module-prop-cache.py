#!/usr/bin/env python3
"""guest_runtime.sh pre: module system.prop snapshots must not use a property listing that a module hook made stale.

Module A's post-fs-data hook creates ro.x.hooked=fromhook; module B's system.prop overrides it. The snapshot taken for
B must record it as present with the hook's value (so the restore puts that back), not as absent (which deletes it).
"""
import os, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S = (ROOT / 'switcher/assets/guest_runtime.sh').read_text()
HELPERS = S[S.index('hex_encode_file() {'):S.index('check_prop_snapshot_dirs() {')]
a = S.index('pre)\n')
a = S.index('    B=/data/oneos_bin/busybox\n', a)
b = S.index('    selected=$(cat "$A/oneos/framework")', a)
LOOP = HELPERS + '\n' + S[a:b]


class Cache(unittest.TestCase):
    def run_loop(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            (r / 'oneos/run').mkdir(parents=True)
            (r / 'ksu/bin').mkdir(parents=True)
            (r / 'bin').mkdir()
            props = r / 'props'
            props.write_text('[ro.x.orig]: [o]\n')
            gp = r / 'bin/getprop'
            gp.write_text('#!/bin/sh\nif [ $# = 0 ]; then cat "%s"; else sed -n "s/^\\[$1\\]: \\[\\(.*\\)\\]$/\\1/p" "%s"; fi\n' % (props, props))
            rp = r / 'ksu/bin/resetprop'
            rp.write_text('#!/bin/sh\n[ "$1" = -n ] && { grep -vF "[$2]: [" "%s" > "%s.t"; printf "[%%s]: [%%s]\\n" "$2" "$3" >> "%s.t"; mv "%s.t" "%s"; }\n' % ((props,) * 5))
            tm = r / 'bin/timeout'
            tm.write_text('#!/bin/sh\nshift\nexec "$@"\n')
            bb = r / 'bin/bb'
            bb.write_text('#!/bin/sh\nexec "$@"\n')
            for f in (gp, rp, tm, bb):
                f.chmod(0o755)
            ma = r / 'modules/a'; mb = r / 'modules/b'
            ma.mkdir(parents=True); mb.mkdir(parents=True)
            (ma / 'system.prop').write_text('ro.x.orig=changedbya\n')
            (ma / 'post-fs-data.sh').write_text('printf "[ro.x.hooked]: [fromhook]\\n" >> "%s"\n' % props)
            (mb / 'system.prop').write_text('ro.x.hooked=overridden\n')
            script = LOOP.replace('B=/data/oneos_bin/busybox', 'B=%s/bin/bb' % d).replace('export PATH=/system/bin:/system/xbin:/data/adb/ksu/bin', 'export PATH=%s/bin:$PATH' % d)
            script = 'A=%s\n' % d + script.replace('"$A/ksu/bin/resetprop"', '"%s/ksu/bin/resetprop"' % d)
            script = script.replace('[ -x "%s/ksu/bin/resetprop" ]' % d, 'true')
            subprocess.run(['sh', '-c', script], env=dict(os.environ, PATH=str(r / 'bin') + ':' + os.environ['PATH']), capture_output=True, text=True, timeout=30)
            return (r / 'oneos/run/module-props').read_text().splitlines()

    def test_hook_created_property_is_snapshotted_as_present(self):
        snap = self.run_loop()
        self.assertIn(f'ro.x.hooked|1h:{len("fromhook")}|{"fromhook".encode().hex()}', snap)
        self.assertIn(f'ro.x.orig|1h:{len("o")}|{"o".encode().hex()}', snap)
        self.assertNotIn('ro.x.hooked|0h:0|', snap)
        self.assertNotIn('ro.x.hooked|0|', snap)

    def test_each_key_is_snapshotted_once(self):
        snap = self.run_loop()
        keys = [l.split('|')[0] for l in snap]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == '__main__':
    unittest.main()
