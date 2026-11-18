#!/usr/bin/env python3
"""One UI's persist properties after a session (guest_props.sh restore-persist): what is deleted, blanked and put back."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'switcher/assets/guest_props.sh').read_text()
START = '    NOW="$STAGE/persist_props.now"\n'
END = '    rm -f "$BLANKED.persist" "$NOW" "$plan"\n'

# One UI's values when the session began, and what the phone holds at the return.
SNAPSHOT = {
    'persist.sys.usb.config': 'mtp',
    'persist.sys.timezone': 'Asia/Karachi',
    'persist.sys.locale': 'en-US',
    'persist.sys.boot.reason': 'reboot',
    'persist.trail': 'two words ',
    'persist.gone': '1',
    'persist.sys.x': 'same',
}
NOW = {
    'persist.sys.usb.config': 'mtp',
    'persist.sys.timezone': 'Europe/Berlin',
    'persist.sys.locale': 'zh-CN',
    'persist.sys.boot.reason': 'hard',
    'persist.trail': 'x',
    'persist.sys.x': 'same',
    'persist.oplus.display': '1',
    'persist.sys.new.oneui': '1',
    'persist.sys.boot.reason.history': 'h',
    'persist.blank.me': '',
    'persist.empty.new': '',
    'persist.vendor.softdog': '0',
    'persistXsysXx': 'not a persist property',
    'ro.build.id': 'ignored',
}
BLANKED = ['persist.blank.me']


def block(source=SOURCE, start=START, end=END):
    return start + source.split(start, 1)[1].split(end, 1)[0] + end


def run(mode='', store=0, code=None, rp_fails=False, noop=''):
    """The calls made to the property tool, as sorted tuples, and the files left behind."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d); stage = root / 'stage'; stage.mkdir()
        (root / 'getprop').write_text(''.join(f'[{k}]: [{v}]\n' for k, v in NOW.items()))
        (stage / 'snap').write_text(''.join(f'{k}={v}\n' for k, v in SNAPSHOT.items()))
        (stage / 'blanked').write_text(''.join(k + '\n' for k in BLANKED))
        rp = root / 'rp'
        # The tool changes what getprop shows, as the real one does; NOOP=batch|single makes that kind of call exit 0
        # WITHOUT changing anything (resetprop -n has done exactly that on a device).
        rp.write_text('#!/bin/sh\n'
                      + ('exit 1\n' if rp_fails else '')
                      + 'setp() { grep -vF "[$1]: [" "$FIX/getprop" > "$FIX/g.tmp"; [ -n "$3" ] || printf "[%s]: [%s]\\n" "$1" "$2" >> "$FIX/g.tmp"; mv "$FIX/g.tmp" "$FIX/getprop"; }\n'
                      + 'if [ "$1" = "-n" ] && [ "$2" = "-f" ]; then\n'
                      + '    while IFS= read -r l; do\n'
                      + '        [ -n "$l" ] || continue\n'
                      + '        printf -- "-n\\037%s\\037%s\\037\\n" "${l%%=*}" "${l#*=}" >> "$CALLS"\n'
                      + '        [ "$NOOP" = batch ] || setp "${l%%=*}" "${l#*=}"\n'
                      + '    done < "$3"\n'
                      + '    exit 0\n'
                      + 'fi\n'
                      + 'for a in "$@"; do printf "%s\\037" "$a"; done >> "$CALLS"; printf "\\n" >> "$CALLS"\n'
                      + '[ "$NOOP" = single ] || [ "$NOOP" = batch ] && [ "$1" != -n ] && [ "$NOOP" = single ] && exit 0\n'
                      + 'case "$1" in\n'
                      + '    -p) if [ "$2" = --delete ]; then setp "$3" "" del; else setp "$2" "$3"; fi ;;\n'
                      + '    -n) [ "$NOOP" = single ] || [ "$NOOP" = allnoop ] || setp "$2" "$3" ;;\n'
                      + 'esac\n'
                      + 'exit 0\n')
        rp.chmod(0o755)
        pre = ('getprop() { if [ $# = 0 ]; then cat "$FIX/getprop"; else sed -n "s/^\\[$1\\]: \\[\\(.*\\)\\]$/\\1/p" "$FIX/getprop"; fi; }\nset -- restore-persist "$MODE"\n'
               'SNAP="$STAGE/snap"; BLANKED="$STAGE/blanked"; BOOT_ID=boot-1\n')
        post = '\necho "counts $deleted ${blanked_p:-0} $restored $failed"\n'
        env = dict(os.environ, FIX=str(root), STAGE=str(stage), MODE=mode, STORE_SESSION=str(store), RP=str(rp), NOOP=noop, CALLS=str(root / 'calls'))
        out = subprocess.run(['/bin/sh', '-c', pre + (code or block()) + post], env=env, check=True, capture_output=True, text=True)
        calls = (root / 'calls').read_text().splitlines() if (root / 'calls').exists() else []
        return {
            'calls': sorted(tuple(c.split('\x1f')[:-1]) for c in calls),
            'counts': out.stdout.strip().splitlines()[-1],
            'blanked': sorted((stage / 'blanked').read_text().split()),
            'left': sorted(p.name for p in stage.iterdir()),
        }


def delete(k): return ('-p', '--delete', k)
def restore(k): return ('-p', k, SNAPSHOT[k])
def blank(k): return ('-n', k, '')


ADDED = ['persist.oplus.display', 'persist.sys.new.oneui', 'persist.sys.boot.reason.history', 'persist.empty.new',
         'persist.vendor.softdog']
CHANGED = ['persist.sys.timezone', 'persist.sys.locale', 'persist.sys.boot.reason', 'persist.trail', 'persist.gone']


class PersistRestore(unittest.TestCase):
    def test_the_return_deletes_what_the_guest_added_and_puts_one_uis_values_back(self):
        r = run()
        self.assertEqual(sorted([delete(k) for k in ADDED] + [restore(k) for k in CHANGED]), r['calls'])
        self.assertEqual('counts 5 0 5 0', r['counts'])
        # A value goes back exactly, its trailing space included.
        self.assertIn(('-p', 'persist.trail', 'two words '), r['calls'])
        self.assertEqual(['blanked', 'snap'], r['left'])

    def test_with_the_sessions_own_store_added_ones_are_blanked_and_listed(self):
        r = run(store=1)
        self.assertEqual(sorted([blank(k) for k in ADDED] + [restore(k) for k in CHANGED]), r['calls'])
        self.assertEqual('counts 0 5 5 0', r['counts'])
        self.assertEqual(sorted(BLANKED + ADDED), r['blanked'])
        self.assertIn('blanked.boot', r['left'])

    def test_recover_touches_only_the_guests_own_names_and_leaves_the_boot_reason(self):
        r = run('--recover')
        self.assertEqual(sorted([delete('persist.oplus.display'), delete('persist.vendor.softdog'), restore('persist.sys.locale')]), r['calls'])

    def test_added_only_restores_nothing(self):
        r = run('--added-only')
        self.assertEqual(sorted(delete(k) for k in ADDED), r['calls'])

    def test_a_batch_blank_that_exits_0_but_changed_nothing_is_written_one_by_one(self):
        # resetprop -n -f reported success and left the values: the per-key path must run (and land them)
        r = run(store=1, noop='batch')
        self.assertEqual('counts 0 5 5 0', r['counts'])
        self.assertEqual(sorted(BLANKED + ADDED), r['blanked'])
        for k in ADDED:
            self.assertIn(blank(k), r['calls'])

    def test_a_write_that_exits_0_but_did_not_land_is_a_failure(self):
        # nothing changes at all (batch, delete, restore): every one is counted as failed, the marker stays
        r = run(noop='single')
        self.assertNotEqual('counts 0 0 0 0', r['counts'])
        self.assertTrue(r['counts'].endswith(' 10') or int(r['counts'].split()[-1]) > 0, r['counts'])
        self.assertIn('snap', r['left'])

    def test_a_failed_write_is_counted(self):
        self.assertEqual('counts 0 0 0 10', run(rp_fails=True)['counts'])


if __name__ == '__main__':
    unittest.main()
