#!/usr/bin/env python3
"""Root writes into $STAGE/data must not follow a link the guest planted at the destination
(follow-up audit N1).

no_symlink() in guest_prepare.sh checks directory components only. A guest-root process can
plant a link AT the destination file (data/local/tmp/oneos_compile.sh -> a host file), and the
host's `cat >` then truncates the host file as root. Removing the destination first closes it.
This test requires an `rm -f` of the very destination before every heredoc / cp write the two
scripts make into $STAGE/data, and demonstrates the write-through with a real link.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'switcher/assets'
DEST = r'\$STAGE/data/[A-Za-z0-9_./-]+'
WRITE = re.compile(r'(?:cat\s*>\s*"(%s)"\s*<<)|(?:\bcp\s+(?:-f\s+)?"[^"]+"\s+"(%s)")' % (DEST, DEST))


def writes():
    for name in ('guest_prepare.sh', 'oneos_switch.sh'):
        lines = (ASSETS / name).read_text().split('\n')
        for i, line in enumerate(lines):
            if line.lstrip().startswith('#'):
                continue
            m = WRITE.search(line)
            if m:
                yield name, i, m.group(1) or m.group(2), lines


class StageWriteLinks(unittest.TestCase):
    def test_every_destination_is_removed_before_it_is_written(self):
        seen, bad = 0, []
        for name, i, dest, lines in writes():
            seen += 1
            ctx = '\n'.join(lines[max(0, i - 8):i + 1])
            if not re.search(r'rm -f[^\n]*"?' + re.escape(dest) + r'"?', ctx):
                bad.append('%s:%d %s' % (name, i + 1, dest))
        self.assertGreaterEqual(seen, 14, 'the pattern no longer finds the known writes: %d' % seen)
        self.assertEqual([], bad)

    def test_the_known_five_scripts_are_covered(self):
        text = (ASSETS / 'guest_prepare.sh').read_text()
        for n in ('fix_app_dirs.sh', 'ensure_app_storage.sh', 'net_fix.sh', 'oneos_compile.sh', 'app_storage_loop.sh'):
            self.assertIn('rm -f "$STAGE/data/local/tmp/%s"' % n, text)
        self.assertIn('rm -f "$STAGE/data/oneos_essential_open.sh"', (ASSETS / 'oneos_switch.sh').read_text())

    def test_the_component_check_alone_lets_a_final_link_through_and_rm_first_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            stage, victim = os.path.join(d, 'stage'), os.path.join(d, 'victim')
            os.makedirs(stage + '/data/local/tmp')
            Path(victim).write_text('HOST FILE\n')
            dest = stage + '/data/local/tmp/oneos_compile.sh'
            os.symlink(victim, dest)
            run = lambda pre: subprocess.run(['sh', '-c', pre + 'cat > "$D" <<\'X\'\npayload\nX\n'], env={'D': dest, 'PATH': os.environ['PATH']})
            run('')
            self.assertEqual(Path(victim).read_text(), 'payload\n', 'the reproduction no longer shows the write-through')
            Path(victim).write_text('HOST FILE\n')
            os.remove(dest)
            os.symlink(victim, dest)
            run('rm -f "$D"; ')
            self.assertEqual(Path(victim).read_text(), 'HOST FILE\n')
            self.assertFalse(os.path.islink(dest))


if __name__ == '__main__':
    unittest.main()
