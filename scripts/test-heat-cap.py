#!/usr/bin/env python3
"""Runs the heat-cap / guest-thermal worker of oneos_switch.sh (the `$BB sh -c "..."` block) against a fake sysfs.

The block sits in double quotes in the switcher, so the outer shell expands it: this test does the same with the
same variables, points /sys at a temp tree, stubs sleep/pidof/timeout/nsenter and drives the battery temperature.
Checks: no telemetry means no release of limits, power-saving ceilings, atomic heat-level file, thermal override retry.
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "switcher/assets/oneos_switch.sh")).read()
RET = open(os.path.join(ROOT, "switcher/assets/return_to_oneui.sh")).read()


def block():
    m = SRC.index("    : oneos_heat_cap\n")
    a = SRC.rindex('$BB sh -c "\n', 0, m) + len('$BB sh -c "\n')
    b = SRC.index('\n" </dev/null >/dev/null 2>&1 &', m)
    return SRC[a:b]


def pre_block():
    """The part that runs before the worker starts: the stale file clean-up and the power-saving record."""
    a = SRC.index('rm -f "$STAGE/heat_cap.active"\n# A level file')
    b = SRC.index("    $BB sh -c \"\n    : oneos_heat_cap")
    return SRC[a:b]


STUB_SLEEP = """#!/bin/sh
echo "$1" >> "$T/sleeps"
n=$(wc -l < "$T/sleeps")
# temperatures, one per loop turn: "$T/temps" lines, the last repeats
t=$(sed -n "${n}p" "$T/temps"); [ -n "$t" ] || t=$(tail -n 1 "$T/temps")
[ -n "$t" ] && echo "$t" > "$T/sys/class/power_supply/battery/temp"
[ -n "$HOOK" ] && sh -c "$HOOK" "$n"
[ "$n" -ge "${TURNS:-6}" ] && : > "$T/stage/data/oneos_rec_stop"
exit 0
"""
STUB_TIMEOUT = """#!/bin/sh
shift
exec "$@"
"""
# nsenter -t P -m sh genv cmd thermalservice <args>: succeeds or fails as the test says
STUB_BB = """#!/bin/sh
case "$1" in
  nsenter)
    echo "$*" >> "$T/overrides"
    [ -f "$T/override_fail" ] && { echo "Service thermalservice not found"; exit 1; }
    exit 0 ;;
  sh) shift; exec sh "$@" ;;
esac
"""
STUB_PIDOF = """#!/bin/sh
echo 1234
"""


def sysroot(text, t):
    """Points the script's bare /sys/ paths at the fake tree (paths that already are inside it stay)."""
    return re.sub(r"(?<![\w/.-])/sys/", t + "/sys/", text)


class Heat(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        for d in ("bin", "stage/data", "sys/class/power_supply/battery", "sys/kernel/gpu"):
            os.makedirs(os.path.join(self.t, d))
        for n, body in (("sleep", STUB_SLEEP), ("timeout", STUB_TIMEOUT), ("bb", STUB_BB), ("pidof", STUB_PIDOF)):
            p = os.path.join(self.t, "bin", n)
            open(p, "w").write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        self.w("stage/guest_mntns", "4026532600\n")
        self.policies = {"policy0": (3628800, 3628800), "policy6": (4742400, 4742400)}  # hw max, current
        self.gpu_max = 1300
        self.gpu_cur = 1300
        self.batt_node = True
        self.temps = ["300"]

    def w(self, name, text):
        p = os.path.join(self.t, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(text)

    def r(self, name):
        try:
            return open(os.path.join(self.t, name)).read().strip()
        except FileNotFoundError:
            return None

    def sysfs(self):
        for p, (hw, cur) in self.policies.items():
            self.w("sys/devices/system/cpu/cpufreq/%s/cpuinfo_max_freq" % p, "%d\n" % hw)
            self.w("sys/devices/system/cpu/cpufreq/%s/scaling_max_freq" % p, "%d\n" % cur)
        self.w("sys/kernel/gpu/gpu_max_clock", "%d\n" % self.gpu_cur)
        self.w("sys/class/kgsl/kgsl-3d0/gpu_available_frequencies",
               "1300000000 1050000000 826000000 646000000 160000000\n")
        self.w("sys/devices/system/cpu/cpufreq_limit/cpufreq_max_limit", "-1\n")
        self.w("temps", "\n".join(self.temps) + "\n")
        if self.batt_node:
            self.w("sys/class/power_supply/battery/temp", self.temps[0] + "\n")

    def env(self, **kw):
        e = dict(os.environ, T=self.t, PATH=os.path.join(self.t, "bin") + ":" + os.environ["PATH"])
        e.update(kw)
        return e

    def run_pre(self, low_power):
        """The session-start part (stale file removal, power-saving record) in the same fake tree."""
        script = sysroot('STAGE=%s/stage\nHEAT_CAP=1\nHOST_LOW_POWER=%s\n' % (self.t, low_power) + pre_block() + "\nfi\n", self.t)  # pre_block ends inside the switcher's outer if
        subprocess.run(["sh", "-c", script], env=self.env(), capture_output=True, timeout=20)

    def run_worker(self, low_power="0", heat_cap=1, guest_map=1, turns=6, **kw):
        self.sysfs()
        body = block()
        # what the outer shell expands: $STAGE $BB $HEAT_CAP $GUEST_THERMAL_MAP $HOST_LOW_POWER $BATT_NODE $AP_NODES
        bn = os.path.join(self.t, "sys/class/power_supply/battery/temp") if self.batt_node else ""
        # the body is double-quote text: let a real shell expand it the way `sh -c "..."` does
        gen = ('STAGE=%s/stage\nBB=%s/bin/bb\nHEAT_CAP=%d\nGUEST_THERMAL_MAP=%d\nHOST_LOW_POWER=%s\n'
               "BATT_NODE='%s'\nAP_NODES=''\n" % (self.t, self.t, heat_cap, guest_map, low_power, bn)
               + 'printf "%s\\n" "' + body + '" > ' + self.t + "/worker.sh\n")
        subprocess.run(["sh", "-c", gen], env=self.env(), check=True, timeout=20)
        expanded = sysroot(open(self.t + "/worker.sh").read(), self.t)  # read first: open("w") truncates
        open(self.t + "/worker.sh", "w").write(expanded)
        return subprocess.run(["sh", self.t + "/worker.sh"], env=self.env(TURNS=str(turns), **kw),
                              capture_output=True, text=True, timeout=30)

    def cur(self, p):
        return int(self.r("sys/devices/system/cpu/cpufreq/%s/scaling_max_freq" % p))

    def log(self):
        return self.r("stage/heat_cap.log") or ""

    # ---- item 5 ----------------------------------------------------------------------------------------------

    def test_no_battery_node_changes_no_clock(self):
        self.batt_node = False
        self.policies = {"policy0": (3628800, 1800000), "policy6": (4742400, 2000000)}
        self.gpu_cur = 646
        self.run_worker()
        self.assertEqual(self.cur("policy0"), 1800000)
        self.assertEqual(self.cur("policy6"), 2000000)
        self.assertEqual(self.r("sys/kernel/gpu/gpu_max_clock"), "646")

    def test_a_reading_that_turns_unreadable_keeps_the_cap_in_place(self):
        self.temps = ["470", "470", "garbage", "garbage", "garbage"]
        self.run_worker()
        before_stop = self.log().split("stopped")[0]
        self.assertIn("level 0 to 3", before_stop)
        # the only full-clock write is the one at the stop (the return started), never one for the lost reading
        self.assertEqual(before_stop.count("full clock"), 0)
        self.assertIn("stopped: full clock", self.log())

    def test_early_uncap_needs_a_cool_reading(self):
        self.run_pre("0")  # (this part only records; the early uncap itself is checked by its condition)
        self.assertIn('[ -n "$pre_sk" ] && awk', SRC)
        self.assertNotIn('[ -z "$pre_sk" ] || awk', SRC)

    # ---- item 6 ----------------------------------------------------------------------------------------------

    def power_saving_case(self, temps):
        self.policies = {"policy0": (3628800, 1228800), "policy6": (4742400, 1267200)}
        self.gpu_cur = 160
        self.temps = temps
        self.sysfs()
        self.run_pre("1")
        return self.run_worker(low_power="1")

    def test_with_power_saving_the_cap_never_raises_the_hosts_limits(self):
        self.power_saving_case(["440", "440", "440", "300", "300", "300"])
        for ln in self.log().splitlines():
            for tok in ln.split():
                if tok.startswith("policy0="):
                    self.assertLessEqual(int(tok.split("=")[1].split("/")[0]), 1228800, ln)
                if tok.startswith("policy6="):
                    self.assertLessEqual(int(tok.split("=")[1].split("/")[0]), 1267200, ln)
        self.assertLessEqual(self.cur("policy0"), 1228800)
        self.assertLessEqual(self.cur("policy6"), 1267200)

    def test_with_power_saving_the_gpu_is_back_to_its_own_limit_when_cool(self):
        self.power_saving_case(["440", "440", "440", "300", "300", "300"])
        self.assertEqual(self.r("sys/kernel/gpu/gpu_max_clock"), "160")
        self.assertFalse(os.path.exists(self.t + "/stage/gpu_max_clock.capped"))

    def test_without_power_saving_cool_means_the_hardware_maximum(self):
        self.temps = ["450", "450", "450", "300", "300", "300"]
        self.run_worker(low_power="0")
        self.assertEqual(self.cur("policy0"), 3628800)
        self.assertEqual(self.cur("policy6"), 4742400)
        self.assertEqual(self.r("sys/kernel/gpu/gpu_max_clock"), "1300")

    def test_return_restores_the_pre_session_limits_when_power_saving_was_on(self):
        a = RET.index('for p in /sys/devices/system/cpu/cpufreq/policy*; do\n        hw=$(cat "$p/cpuinfo_max_freq"')
        b = RET.index("    GCLK=/sys/kernel/gpu/gpu_max_clock", a)
        piece = RET[a:b]
        self.policies = {"policy0": (3628800, 3000000)}
        self.sysfs()
        self.w("stage/heat_cap.pre/policy0", "1228800\n")
        script = 'S=%s/stage\nhc=""\n%s' % (self.t, sysroot(piece, self.t))
        subprocess.run(["sh", "-c", script], env=self.env(), check=True, timeout=10)
        self.assertEqual(self.cur("policy0"), 1228800)
        os.remove(self.t + "/stage/heat_cap.pre/policy0")
        subprocess.run(["sh", "-c", script], env=self.env(), check=True, timeout=10)
        self.assertEqual(self.cur("policy0"), 3628800)

    # ---- item 9 ----------------------------------------------------------------------------------------------

    def test_heat_level_is_published_by_rename(self):
        self.assertIn("oneos_heat_level.tmp", block())
        self.temps = ["440", "440", "300"]
        seen = []
        hook = ('f="$T/stage/data/oneos_heat_level"; [ -f "$f" ] && { v=$(cat "$f"); echo "[$v]" >> "$T/seen"; }; true')
        self.run_worker(HOOK=hook)
        for v in (self.r("seen") or "").split():
            seen.append(v)
        self.assertTrue(seen)
        self.assertNotIn("[]", seen, "the level file was observed empty")

    def test_worker_leaves_no_level_file_behind(self):
        self.temps = ["470", "470"]
        self.run_worker()
        self.assertIsNone(self.r("stage/data/oneos_heat_level"))
        self.assertFalse(os.path.exists(self.t + "/stage/data/.oneos_heat_level.tmp"))

    def test_session_start_removes_a_stale_level_file(self):
        self.w("stage/data/oneos_heat_level", "4\n")
        self.run_pre("0")
        self.assertIsNone(self.r("stage/data/oneos_heat_level"))

    def test_return_removes_the_level_file(self):
        self.assertIn("rm -f $S/data/oneos_heat_level", RET)
        self.assertLess(RET.index("rm -f $S/data/oneos_heat_level"), RET.index("if [ -f $S/heat_cap.active ]"))

    # ---- item 24 ---------------------------------------------------------------------------------------------

    def test_failed_thermal_override_is_retried(self):
        self.temps = ["470"] * 12
        open(self.t + "/override_fail", "w").close()
        self.sysfs()
        self.run_worker(turns=3, guest_map=1, heat_cap=0)
        first = len((self.r("overrides") or "").splitlines())
        self.assertEqual(first, 1, "retried every turn instead of backing off")
        self.assertIn("FAILED", self.log())
        os.remove(self.t + "/override_fail")
        os.remove(self.t + "/stage/data/oneos_rec_stop")
        os.remove(self.t + "/sleeps")
        self.run_worker(turns=9, guest_map=1, heat_cap=0)
        self.assertGreaterEqual(len((self.r("overrides") or "").splitlines()), 2)
        self.assertIn("guest thermal status 1 (cmd thermalservice override-status 1", self.log())

    def test_successful_override_is_recorded_once(self):
        self.temps = ["470"] * 8
        self.run_worker(turns=6, guest_map=1, heat_cap=0)
        self.assertEqual(len((self.r("overrides") or "").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
