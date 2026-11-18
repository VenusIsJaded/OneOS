#!/usr/bin/env python3
"""oneos_switch.sh holds one lock for the whole switch (audit item 23).

The block between the "switch lock begin/end" markers is cut out of the real
script and run in a shell with a stub `refuse`, against a temporary stage.
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
BEGIN, END = '# --- switch lock begin\n', '# --- switch lock end\n'


def block():
    assert SWITCH.count(BEGIN) == 1 and SWITCH.count(END) == 1
    return SWITCH.split(BEGIN, 1)[1].split(END, 1)[0]


HEAD = 'refuse() { echo "REFUSED: $*"; exit 2; }\n'


class Base(unittest.TestCase):
    shell = os.environ.get('TEST_SH', 'sh').split()

    def setUp(self):
        self.stage = tempfile.mkdtemp(prefix='switch-lock-')
        self.lock = os.path.join(self.stage, '.switch.lock')

    def tearDown(self):
        shutil.rmtree(self.stage, ignore_errors=True)

    def script(self, body):
        return HEAD + 'STAGE=%s\n' % self.stage + block() + body

    def run_sh(self, body, timeout=20):
        return subprocess.run(self.shell + ['-c', self.script(body)], capture_output=True, text=True, timeout=timeout)

    def popen(self, body):
        return subprocess.Popen(self.shell + ['-c', self.script(body)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def pstart(pid):
    s = Path('/proc/%d/stat' % pid).read_text()
    return s.rsplit(') ', 1)[1].split()[19]


class SwitchLockTest(Base):
    def test_a_free_stage_runs_and_the_lock_is_gone_afterwards(self):
        r = self.run_sh('echo ran; [ -f "$SW_LOCK" ] && echo held\n')
        self.assertEqual((r.returncode, r.stdout), (0, 'ran\nheld\n'))
        self.assertFalse(os.path.exists(self.lock))

    def test_the_lock_is_released_on_a_refusal_exit_too(self):
        r = self.run_sh('refuse "later check"\n')
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.lock))

    def test_a_second_switch_is_refused_while_the_first_runs(self):
        first = self.popen('echo up; sleep 3\n')
        self.assertEqual(first.stdout.readline(), 'up\n')
        r = self.run_sh('echo second ran\n')
        self.assertEqual(r.returncode, 2)
        self.assertIn('REFUSED: another OneOS switch is starting or running (pid %d)' % first.pid, r.stdout)
        self.assertNotIn('second ran', r.stdout)
        self.assertTrue(os.path.exists(self.lock), 'the refused one must not remove the first one\'s lock')
        first.wait()
        self.assertFalse(os.path.exists(self.lock))
        self.assertEqual(self.run_sh('echo again\n').stdout, 'again\n')

    def test_simultaneous_starts_let_exactly_one_through(self):
        ps = [self.popen('echo won; sleep 1.5\n') for _ in range(8)]
        outs = [p.communicate() for p in ps]
        won = sum(1 for o, _ in outs if o == 'won\n')
        refused = sum(1 for o, _ in outs if o.startswith('REFUSED: another OneOS switch'))
        self.assertEqual((won, refused), (1, 7), outs)

    def test_a_dead_owners_lock_is_taken_over(self):
        d = subprocess.Popen(['sleep', '0'])
        d.wait()
        Path(self.lock).write_text('%d 12345\n' % d.pid)
        r = self.run_sh('echo ran\n')
        self.assertEqual((r.returncode, r.stdout), (0, 'ran\n'))
        self.assertFalse(os.path.exists(self.lock))
        self.assertEqual([f for f in os.listdir(self.stage)], [])

    def test_a_reused_pid_with_another_start_time_is_not_the_owner(self):
        live = subprocess.Popen(['sleep', '30'])
        try:
            Path(self.lock).write_text('%d %d\n' % (live.pid, int(pstart(live.pid)) + 7))
            r = self.run_sh('echo ran\n')
            self.assertEqual((r.returncode, r.stdout), (0, 'ran\n'))
            self.assertEqual(live.poll(), None, 'the unrelated process must be left alone')
        finally:
            live.kill()
            live.wait()

    def test_the_real_owner_with_its_start_time_is_respected(self):
        live = subprocess.Popen(['sleep', '30'])
        try:
            Path(self.lock).write_text('%d %s\n' % (live.pid, pstart(live.pid)))
            r = self.run_sh('echo ran\n')
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn('pid %d' % live.pid, r.stdout)
        finally:
            live.kill()
            live.wait()

    def test_a_zombie_owner_counts_as_gone(self):
        z = subprocess.Popen(['sleep', '0'])
        for _ in range(50):
            try:
                if Path('/proc/%d/stat' % z.pid).read_text().rsplit(') ', 1)[1][0] == 'Z':
                    break
            except OSError:
                break
            time.sleep(0.1)
        try:
            Path(self.lock).write_text('%d %s\n' % (z.pid, pstart(z.pid)))
        except OSError:
            self.skipTest('no zombie to test with')
        r = self.run_sh('echo ran\n')
        self.assertEqual((r.returncode, r.stdout), (0, 'ran\n'))
        z.wait()

    def test_another_switchs_lock_is_not_removed_by_a_stale_exit(self):
        # A subshell/child that inherited the EXIT trap must not delete a lock it does not own.
        r = self.run_sh('( : ); x=$(echo hi); [ -f "$SW_LOCK" ] && echo held\n')
        self.assertEqual(r.stdout, 'held\n')

    def test_the_switch_takes_it_before_it_changes_anything(self):
        lines = SWITCH.split('\n')
        lock_line = next(n for n, l in enumerate(lines) if l == BEGIN.strip())
        for later in ('guest_props.sh" snapshot-persist', 'setenforce 0', '"$RP" -p persist.oneos.probe 1'):
            code = [n for n, l in enumerate(lines) if later in l and not l.lstrip().startswith('#')]
            self.assertTrue(code, later + ' not found as code')
            self.assertGreater(min(code), lock_line, later + ' comes before the lock')
        self.assertEqual(SWITCH.count('trap sw_unlock EXIT'), 1)

if __name__ == '__main__':
    unittest.main()
