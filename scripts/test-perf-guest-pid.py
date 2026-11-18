#!/usr/bin/env python3
"""fold8_coloros.sh perf_guest: the cached system_server pid is reused only while it is that very process.

Real processes are used (a copy of sleep named system_server, and a plain sleep) so /proc/<pid>/comm and
/proc/<pid>/stat are the kernel's; stat of the guest /data is stubbed to match for every pid.
"""
import os, shutil, subprocess, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = open(os.path.join(ROOT, "switcher/assets/fold8_coloros.sh")).read()
a = T.index("perf_pstart() {")
b = T.index("perf_snapshot() {")
FUNCS = T[a:b]


class PerfGuest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(self.d + "/bin")
        os.makedirs(self.d + "/stage/data")
        sl = shutil.which("sleep")
        shutil.copy(sl, self.d + "/bin/system_server")
        open(self.d + "/bin/stat", "w").write('#!/bin/sh\necho 1:1\n')
        os.chmod(self.d + "/bin/stat", 0o755)
        self.procs = []
        self.addCleanup(lambda: [p.kill() for p in self.procs])

    def spawn(self, exe):
        p = subprocess.Popen([exe, "60"])
        self.procs.append(p)
        time.sleep(0.1)
        return p

    def start_of(self, pid):
        return open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[19]

    def guest(self, cached_pid, cached_start, pidof_out):
        open(self.d + "/bin/pidof", "w").write("#!/bin/sh\necho %s\n" % pidof_out)
        os.chmod(self.d + "/bin/pidof", 0o755)
        script = ("STAGE=%s/stage\nLAST_PERF_GP=%s\nLAST_PERF_GP_START=%s\n" % (self.d, cached_pid, cached_start)
                  + FUNCS + "\nperf_guest\n")
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                           env=dict(os.environ, PATH=self.d + "/bin:" + os.environ["PATH"]))
        return r.stdout.strip()

    def test_the_same_process_is_kept(self):
        p = self.spawn(self.d + "/bin/system_server")
        self.assertEqual(self.guest(p.pid, self.start_of(p.pid), "999999"), str(p.pid))

    def test_a_pid_recycled_by_another_process_is_not_accepted(self):
        other = self.spawn(shutil.which("sleep"))      # comm "sleep": same pid number, other process
        real = self.spawn(self.d + "/bin/system_server")
        self.assertEqual(self.guest(other.pid, self.start_of(other.pid), str(real.pid)), str(real.pid))

    def test_a_recycled_pid_that_is_a_system_server_with_another_start_time_is_not_accepted(self):
        p = self.spawn(self.d + "/bin/system_server")
        real = self.spawn(self.d + "/bin/system_server")
        self.assertEqual(self.guest(p.pid, "1", str(real.pid)), str(real.pid))

    def test_no_recorded_start_time_means_rediscovery(self):
        p = self.spawn(self.d + "/bin/system_server")
        real = self.spawn(self.d + "/bin/system_server")
        self.assertEqual(self.guest(p.pid, "", str(real.pid)), str(real.pid))


if __name__ == "__main__":
    unittest.main()
