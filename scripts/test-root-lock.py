#!/usr/bin/env python3
"""take_lock in oneos_root.sh keys the lock by pid + start time (follow-up audit N4).

The real lock_pstart / lock_owner_alive / take_lock are cut out of the script and run
against the real /proc, with stubs for refuse and is_dec.
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'app-android/app/src/full/assets/root/oneos_root.sh').read_text()


def functions():
    a = SCRIPT.index('lock_pstart() {')
    b = SCRIPT.index('# --- Experimental guest root / modules')
    return SCRIPT[a:b]


HEAD = r'''
refuse() { echo "REFUSED: $*"; exit 2; }
is_dec() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; [ ${#1} -le 18 ]; }
PROC=/proc
'''


def pstart(pid):
    return Path('/proc/%d/stat' % pid).read_text().rsplit(') ', 1)[1].split()[19]


class RootLock(unittest.TestCase):
    shell = os.environ.get('TEST_SH', 'sh').split()

    def setUp(self):
        self.stage = tempfile.mkdtemp(prefix='root-lock-')
        os.makedirs(self.stage + '/bin')
        self.lock = self.stage + '/bin/.lock'
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        shutil.rmtree(self.stage, ignore_errors=True)

    def run_sh(self, body='echo ran\n'):
        text = HEAD + 'STAGE=%s\n' % self.stage + functions() + 'take_lock\n' + body
        return subprocess.run(self.shell + ['-c', text], capture_output=True, text=True, timeout=30)

    def spawn(self, *argv):
        p = subprocess.Popen(list(argv), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def wait_named(self, proc):
        for _ in range(100):
            if b'oneos_root' in Path('/proc/%d/cmdline' % proc.pid).read_bytes():
                return
            time.sleep(0.05)
        self.fail('the fake process does not carry "oneos_root" in its command line: the test would prove nothing')

    def test_free_lock_is_taken_and_removed_and_holds_pid_and_start_time(self):
        r = self.run_sh('read -r a b < "$LOCK"; echo "$a $b"; [ "$a" = "$$" ] && echo mine\n')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.split('\n')
        self.assertRegex(lines[0], r'^\d+ \d+$')
        self.assertEqual(lines[1], 'mine')
        self.assertFalse(os.path.exists(self.lock))

    def test_a_running_owner_with_its_start_time_blocks(self):
        live = self.spawn('sleep', '30')
        Path(self.lock).write_text('%d %s\n' % (live.pid, pstart(live.pid)))
        r = self.run_sh()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn('another OneOS step is still running (pid %d)' % live.pid, r.stdout)
        self.assertTrue(os.path.exists(self.lock))

    def test_the_audits_reproduction_a_reused_pid_named_oneos_root_does_not_block(self):
        # an unrelated process whose command line contains "oneos_root" holds the recorded pid now
        d = tempfile.mkdtemp(prefix='oneos_root-')
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        script = d + '/oneos_root'
        Path(script).write_text('#!/bin/sh\nsleep 30\n:\n')
        os.chmod(script, 0o755)
        other = self.spawn('sh', script)
        self.wait_named(other)
        Path(self.lock).write_text('%d 1\n' % other.pid)   # another start time: the pid was recycled
        r = self.run_sh()
        self.assertEqual((r.returncode, r.stdout), (0, 'ran\n'))
        self.assertEqual(other.poll(), None)

    def test_a_dead_owner_is_taken_over(self):
        d = subprocess.Popen(['sleep', '0'])
        d.wait()
        Path(self.lock).write_text('%d 5\n' % d.pid)
        r = self.run_sh()
        self.assertEqual((r.returncode, r.stdout), (0, 'ran\n'))
        self.assertFalse(os.path.exists(self.lock))

    def test_an_old_pid_only_lock_keeps_its_old_test(self):
        d = tempfile.mkdtemp(prefix='oneos_root-')
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        script = d + '/oneos_root.sh'
        Path(script).write_text('#!/bin/sh\nsleep 30\n:\n')
        os.chmod(script, 0o755)
        old = self.spawn('sh', script)
        self.wait_named(old)
        Path(self.lock).write_text('%d\n' % old.pid)
        r = self.run_sh()
        self.assertEqual(r.returncode, 2, r.stdout)
        old.kill()
        old.wait()
        r = self.run_sh()
        self.assertEqual((r.returncode, r.stdout), (0, 'ran\n'))

    def test_a_pid_only_lock_of_an_unrelated_process_is_taken_over(self):
        live = self.spawn('sleep', '30')
        Path(self.lock).write_text('%d\n' % live.pid)
        r = self.run_sh()
        self.assertEqual((r.returncode, r.stdout), (0, 'ran\n'))

    def test_the_audits_one_trap_text_is_unchanged(self):
        self.assertEqual(SCRIPT.count("trap 'rm -f \"$LOCK\"' EXIT"), 1)


if __name__ == '__main__':
    unittest.main()
