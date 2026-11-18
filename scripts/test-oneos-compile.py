#!/usr/bin/env python3
"""Runs the guest's oneos_compile.sh (the heredoc in guest_prepare.sh) against stubbed pm/cmd/sleep.

Checks: the order (installed apps, launcher, SystemUI, Settings, Phone first), the auto mode waiting for an
idle phone only for the rest of the system, the heat pause (auto from level 1, now only from level 3), the
stop request, the status/results files the guest's OneOS app reads, and that oneos_dexopt.done is written only
when every app was done.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "switcher/assets/guest_prepare.sh")).read()


def worker_text():
    a = SRC.index("<<'COMPILEEOF'\n") + len("<<'COMPILEEOF'\n")
    b = SRC.index("\nCOMPILEEOF\n", a)
    return SRC[a:b] + "\n"


def takeover_text():
    """stop_auto_compile from the app_storage_loop.sh heredoc."""
    a = SRC.index("stop_auto_compile() {")
    b = SRC.index("\n}\n", a) + 3
    return SRC[a:b] + "\n"


PM = """#!/bin/sh
# the package list: $ROOTD/pkgs when a test wrote one; versionCodes from $ROOTD/vers ("pkg code"), default 1
pk() { if [ -f "$ROOTD/pkgs" ]; then cat "$ROOTD/pkgs"; else printf 'com.sys.x\\ncom.user.a\\ncom.android.systemui\\ncom.user.b\\ncom.android.launcher\\ncom.sys.y\\n'; fi; }
case "$3" in
  -3) printf 'package:com.user.a\\npackage:com.user.b\\n' ;;
  -f)
    # package:<apk path>=<package> versionCode:<n>; the install directory is $ROOTD/tags ("pkg tag"), default t1
    [ -n "$NOVC" ] && exit 1
    [ -n "$NOF" ] && exit 1
    pk | while read -r n; do
      v=$(awk -v p="$n" '$1 == p { print $2 }' "$ROOTD/vers" 2>/dev/null)
      t=$(awk -v p="$n" '$1 == p { print $2 }' "$ROOTD/tags" 2>/dev/null)
      echo "package:/data/app/~~r==/$n-${t:-t1}==/base.apk=$n versionCode:${v:-1}"
    done ;;
  --show-versioncode)
    [ -n "$NOVC" ] && exit 1
    pk | while read -r n; do
      v=$(awk -v p="$n" '$1 == p { print $2 }' "$ROOTD/vers" 2>/dev/null); echo "package:$n installer=x versionCode:${v:-1}" | sed 's/ installer=x//'
    done ;;
  *) pk | sed 's/^/package:/' ;;
esac
"""
# cmd package compile -m speed <pkg>; "cmd notification ..." is ignored. FAILPKG makes one app fail.
CMD = """#!/bin/sh
[ "$1" = package ] || exit 0
echo "$5" >> "$ROOTD/order"
[ -n "$HOOK" ] && sh -c "$HOOK" "$5"
if [ "$5" = "$FAILPKG" ]; then echo Failure; else echo Success; fi
"""
# timeout [-k N] SECS cmd...: TMOFAIL=<pkg> makes that package's compile time out (124), the rest run
TIMEOUT = """#!/bin/sh
echo "$*" >> "$ROOTD/timeouts"
[ "$1" = -k ] && shift 2
shift
for last; do :; done
[ -n "$TMOFAIL" ] && [ "$last" = "$TMOFAIL" ] && exit 124
exec "$@"
"""
# sleep: record, and let a test change the world while "waiting"
SLEEP = """#!/bin/sh
echo "$1" >> "$ROOTD/sleeps"
n=$(wc -l < "$ROOTD/sleeps")
[ -n "$SLEEPHOOK" ] && sh -c "$SLEEPHOOK" "$n"
[ "$n" -gt 400 ] && exit 1
exit 0
"""


class Compile(unittest.TestCase):
    def setUp(self):
        self.r = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.r, True)
        for d in ("data", "bin", "fw", "sys/class/power_supply/battery", "sys/class/drm/card0-DSI-1"):
            os.makedirs(os.path.join(self.r, d))
        for name, body in (("pm", PM), ("cmd", CMD), ("sleep", SLEEP), ("timeout", TIMEOUT)):
            p = os.path.join(self.r, "bin", name)
            open(p, "w").write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        open(os.path.join(self.r, "fw/framework.jar"), "w").write("a")
        open(os.path.join(self.r, "fw/services.jar"), "w").write("b")
        open(os.path.join(self.r, "worker.sh"), "w").write(worker_text())
        self.battery("Discharging")
        self.dpms("On")

    def p(self, *a):
        return os.path.join(self.r, *a)

    def battery(self, s):
        open(self.p("sys/class/power_supply/battery/status"), "w").write(s + "\n")

    def dpms(self, s):
        open(self.p("sys/class/drm/card0-DSI-1/dpms"), "w").write(s + "\n")

    def heat(self, n):
        open(self.p("data/oneos_heat_level"), "w").write("%s\n" % n)

    def run_worker(self, mode, **env):
        e = dict(os.environ)
        e.update(PATH=self.p("bin") + ":" + e["PATH"], ROOTD=self.r,
                 ONEOS_COMPILE_DATA=self.p("data"), ONEOS_COMPILE_SYS=self.p("sys"), ONEOS_COMPILE_FW=self.p("fw"))
        e.update(env)
        return subprocess.run(["sh", self.p("worker.sh"), mode], env=e, capture_output=True, text=True, timeout=20)

    def lines(self, name):
        try:
            return open(self.p(name)).read().split()
        except FileNotFoundError:
            return []

    def order(self):
        return self.lines("order")

    def status(self):
        return open(self.p("data/oneos_compile/status")).read().split()

    FIRST = ["com.user.a", "com.user.b", "com.android.launcher", "com.android.systemui"]

    def test_now_order_and_done(self):
        r = self.run_worker("now")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.order(), self.FIRST + ["com.sys.x", "com.sys.y"])
        self.assertEqual(self.status()[0], "done")
        res = [l.split() for l in open(self.p("data/oneos_compile/results")).read().splitlines()]
        self.assertEqual([x[1] for x in res], ["ok"] * 6)
        self.assertTrue(os.path.exists(self.p("data/oneos_dexopt.done")))
        self.assertFalse(os.path.exists(self.p("data/oneos_compile/lock")))

    def test_now_has_no_pacing(self):
        self.run_worker("now")
        self.assertEqual(self.lines("sleeps"), [])

    def test_auto_waits_for_idle_only_after_the_first_apps(self):
        self.run_worker("auto", SLEEPHOOK='[ "$0" -ge 3 ] && echo Off > "$ROOTD/sys/class/drm/card0-DSI-1/dpms"')
        order = self.order()
        self.assertEqual(order, self.FIRST + ["com.sys.x", "com.sys.y"])
        # the first four were compiled before any 30 s idle wait (waits are the sleeps of 30)
        self.assertIn("30", self.lines("sleeps"))
        self.assertTrue(os.path.exists(self.p("data/oneos_dexopt.done")))

    def test_auto_charging_counts_as_idle(self):
        self.battery("Charging")
        self.run_worker("auto")
        self.assertNotIn("30", self.lines("sleeps"))

    def test_auto_paused_while_heat_cap_is_on_then_goes_on(self):
        self.heat(2)
        self.battery("Charging")
        self.run_worker("auto", SLEEPHOOK='[ "$0" -ge 3 ] && echo 0 > "$ROOTD/data/oneos_heat_level"',
                        )
        self.assertIn("10", self.lines("sleeps"))
        self.assertEqual(len(self.order()), 6)

    def test_now_ignores_light_heat_but_waits_at_level_3(self):
        self.heat(2)
        self.run_worker("now")
        self.assertNotIn("10", self.lines("sleeps"))
        self.assertEqual(len(self.order()), 6)
        shutil.rmtree(self.p("data/oneos_compile"))
        os.remove(self.p("data/oneos_dexopt.done"))
        os.remove(self.p("order"))
        self.heat(3)
        self.run_worker("now", SLEEPHOOK='[ "$0" -ge 2 ] && echo 0 > "$ROOTD/data/oneos_heat_level"')
        self.assertIn("10", self.lines("sleeps"))
        self.assertEqual(len(self.order()), 6)

    def test_stop_request_leaves_no_done_mark(self):
        r = self.run_worker("now", HOOK='[ "$0" = com.user.b ] && : > "$ROOTD/data/oneos_compile/stop"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.order(), ["com.user.a", "com.user.b"])
        self.assertEqual(self.status()[0], "stopped")
        self.assertFalse(os.path.exists(self.p("data/oneos_dexopt.done")))
        self.assertFalse(os.path.exists(self.p("data/oneos_compile/lock")))

    def test_return_stops_it(self):
        self.run_worker("now", HOOK='[ "$0" = com.user.a ] && : > "$ROOTD/data/oneos_rec_stop"')
        self.assertEqual(self.order(), ["com.user.a"])
        self.assertFalse(os.path.exists(self.p("data/oneos_dexopt.done")))

    def test_failed_app_is_reported_and_the_rest_go_on(self):
        self.run_worker("now", FAILPKG="com.android.launcher")
        res = dict((l.split()[0], l.split()[1]) for l in open(self.p("data/oneos_compile/results")).read().splitlines())
        self.assertEqual(res["com.android.launcher"], "fail")
        self.assertEqual(res["com.sys.y"], "ok")
        self.assertEqual(len(self.order()), 6)

    def test_auto_does_nothing_when_already_done_for_this_framework(self):
        self.run_worker("now")
        os.remove(self.p("order"))
        self.run_worker("auto")
        self.assertEqual(self.order(), [])

    def test_second_instance_exits_while_one_is_running(self):
        os.makedirs(self.p("data/oneos_compile"))
        open(self.p("data/oneos_compile/lock"), "w").write("%d\n" % os.getpid())
        self.run_worker("now")
        self.assertEqual(self.order(), [])

    def test_after_done_auto_compiles_only_new_or_updated_versions_quietly(self):
        self.battery("Charging")
        self.run_worker("auto")
        self.assertEqual(len(self.order()), 6)
        os.remove(self.p("order"))
        # nothing changed: no compile, no lock left, status untouched
        self.run_worker("auto")
        self.assertEqual(self.order(), [])
        self.assertFalse(os.path.exists(self.p("data/oneos_compile/lock")))
        # an update (com.user.a v2) and an install (com.new.z)
        open(self.p("vers"), "w").write("com.user.a 2\ncom.new.z 7\n")
        open(self.p("pkgs"), "w").write("com.sys.x\ncom.user.a\ncom.user.b\ncom.new.z\n")
        self.battery("Discharging")
        self.dpms("On")
        r = self.run_worker("auto")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(self.order()), ["com.new.z", "com.user.a"])
        # no idle wait, and no pacing after the last one
        self.assertNotIn("30", self.lines("sleeps"))
        self.assertEqual(self.status()[0], "done")
        # tried versions are not tried again, even though one of them failed before
        os.remove(self.p("order"))
        self.run_worker("auto", FAILPKG="com.new.z")
        self.assertEqual(self.order(), [])
        # a further update of the same package is compiled again
        open(self.p("vers"), "w").write("com.user.a 3\ncom.new.z 7\n")
        self.run_worker("auto")
        self.assertEqual(self.order(), ["com.user.a"])

    def test_incremental_pass_keeps_the_full_pass_mark_and_log(self):
        self.battery("Charging")
        self.run_worker("auto")
        done = open(self.p("data/oneos_dexopt.done")).read()
        log = open(self.p("data/oneos_dexopt.log")).read()
        open(self.p("vers"), "w").write("com.sys.y 5\n")
        self.run_worker("auto")
        self.assertEqual(open(self.p("data/oneos_dexopt.done")).read(), done)
        self.assertTrue(open(self.p("data/oneos_dexopt.log")).read().startswith(log))

    def test_framework_change_runs_a_full_pass_again(self):
        self.battery("Charging")
        self.run_worker("auto")
        open(self.p("fw/services.jar"), "w").write("changed")
        os.remove(self.p("order"))
        self.run_worker("auto")
        self.assertEqual(len(self.order()), 6)

    def test_failing_app_is_not_retried_by_incremental_passes(self):
        self.battery("Charging")
        self.run_worker("auto", FAILPKG="com.sys.y")
        os.remove(self.p("order"))
        self.run_worker("auto")
        self.assertEqual(self.order(), [])

    # ---- audit fixes (2026-11-18) -------------------------------------------------------------------------

    def start_of(self, pid):
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[19]

    def test_pm_answering_nothing_is_not_a_finished_pass(self):
        self.battery("Charging")  # auto compiles the rest of the system only on an idle phone
        open(self.p("pkgs"), "w").close()
        r = self.run_worker("auto")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.order(), [])
        self.assertFalse(os.path.exists(self.p("data/oneos_dexopt.done")))
        self.assertFalse(os.path.exists(self.p("data/oneos_compile/lock")))
        # a later start, with pm answering, does the whole pass
        os.remove(self.p("pkgs"))
        self.run_worker("auto")
        self.assertEqual(len(self.order()), 6)
        self.assertTrue(os.path.exists(self.p("data/oneos_dexopt.done")))

    def test_plain_package_list_is_used_when_versioncode_listing_is_unavailable(self):
        # pm without --show-versioncode: the ver file is empty, the plain list has six packages
        self.battery("Charging")
        r = self.run_worker("auto", NOVC="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(self.order()), sorted(self.FIRST + ["com.sys.x", "com.sys.y"]))
        self.assertTrue(os.path.exists(self.p("data/oneos_dexopt.done")))

    def test_auto_is_idle_only_when_every_panel_is_off(self):
        os.makedirs(self.p("sys/class/drm/card0-DSI-2"))
        self.dpms("Off")
        open(self.p("sys/class/drm/card0-DSI-2/dpms"), "w").write("On\n")
        self.run_worker("auto", SLEEPHOOK='[ "$0" -ge 4 ] && echo Off > "$ROOTD/sys/class/drm/card0-DSI-2/dpms"')
        sl = self.lines("sleeps")
        # one panel Off is not idle: it waited (30 s) until the second panel went off too
        self.assertGreaterEqual(sl.count("30"), 3)
        self.assertEqual(len(self.order()), 6)

    def test_auto_is_idle_when_the_only_readable_panel_is_off(self):
        self.dpms("Off")
        self.run_worker("auto")
        self.assertNotIn("30", self.lines("sleeps"))

    def test_heat_pause_does_not_give_up_after_ten_minutes(self):
        self.heat(3)
        self.battery("Charging")
        # 60 waits of 10 s used to end the pause; it is still on after 70 and ends when the heat does
        self.run_worker("auto", HOOK='wc -l < "$ROOTD/sleeps" >> "$ROOTD/at"',
                        SLEEPHOOK='[ "$0" -ge 70 ] && echo 0 > "$ROOTD/data/oneos_heat_level"')
        first = int(self.lines("at")[0])
        self.assertGreaterEqual(first, 70)
        self.assertEqual(len(self.order()), 6)

    def test_heat_pause_still_ends_with_a_stop_request(self):
        self.heat(3)
        r = self.run_worker("auto", SLEEPHOOK='[ "$0" -ge 80 ] && : > "$ROOTD/data/oneos_compile/stop"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.order(), [])
        self.assertEqual(self.status()[0], "stopped")
        self.assertFalse(os.path.exists(self.p("data/oneos_compile/lock")))

    def test_a_hung_compile_is_given_up_on_and_the_rest_go_on(self):
        self.run_worker("now", TMOFAIL="com.android.launcher")
        res = dict((l.split()[0], l.split()[1]) for l in open(self.p("data/oneos_compile/results")).read().splitlines())
        self.assertEqual(res["com.android.launcher"], "fail")
        self.assertEqual(res["com.sys.y"], "ok")
        self.assertIn("timed out", open(self.p("data/oneos_dexopt.log")).read())
        self.assertEqual(self.status()[0], "done")
        self.assertFalse(os.path.exists(self.p("data/oneos_compile/lock")))
        # every compile ran under timeout, with the configured limit
        self.assertTrue(all(l.split()[:3] == ["-k", "5", "600"] for l in open(self.p("timeouts")).read().splitlines()))

    def test_lock_of_a_reused_pid_is_not_a_running_worker(self):
        os.makedirs(self.p("data/oneos_compile"))
        # this test's own pid is alive, but its start time is not the one the lock recorded: pid reuse
        open(self.p("data/oneos_compile/lock"), "w").write("%d 1\n" % os.getpid())
        self.run_worker("now")
        self.assertEqual(len(self.order()), 6)

    def test_lock_of_a_running_worker_with_its_start_time_is_respected(self):
        os.makedirs(self.p("data/oneos_compile"))
        open(self.p("data/oneos_compile/lock"), "w").write("%d %s\n" % (os.getpid(), self.start_of(os.getpid())))
        self.run_worker("now")
        self.assertEqual(self.order(), [])
        self.assertTrue(os.path.exists(self.p("data/oneos_compile/lock")))

    def test_lock_of_a_dead_worker_is_taken_over(self):
        os.makedirs(self.p("data/oneos_compile"))
        open(self.p("data/oneos_compile/lock"), "w").write("999999 5\n")
        self.run_worker("now")
        self.assertEqual(len(self.order()), 6)
        self.assertFalse(os.path.exists(self.p("data/oneos_compile/lock")))

    def test_worker_never_removes_a_lock_that_is_not_its_own(self):
        # another worker took over meanwhile (the old one was stopped for it): its lock stays
        self.run_worker("now", HOOK='[ "$0" = com.user.a ] && echo "4242 9" > "$ROOTD/data/oneos_compile/lock"')
        self.assertEqual(open(self.p("data/oneos_compile/lock")).read().split(), ["4242", "9"])

    def test_simultaneous_starts_run_one_worker(self):
        e = dict(os.environ)
        e.update(PATH=self.p("bin") + ":" + e["PATH"], ROOTD=self.r, HOOK="/bin/sleep 0.2",
                 ONEOS_COMPILE_DATA=self.p("data"), ONEOS_COMPILE_SYS=self.p("sys"), ONEOS_COMPILE_FW=self.p("fw"))
        procs = [subprocess.Popen(["sh", self.p("worker.sh"), "now"], env=e, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) for _ in range(6)]
        for pr in procs:
            pr.wait(timeout=30)
        order = self.order()
        self.assertEqual(sorted(order), sorted(set(order)), "an app was compiled twice: two workers ran")
        self.assertEqual(len(order), 6)

    def takeover(self, lock_line, mode="auto"):
        """Runs stop_auto_compile against a live fake worker (a shell with a long-running child)."""
        d = self.p("data/oneos_compile")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "mode"), "w").write(mode + "\n")
        w = subprocess.Popen(["sh", "-c", "sleep 60 & echo $! > %s; wait" % self.p("child.pid")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if os.path.exists(self.p("child.pid")) and open(self.p("child.pid")).read().strip():
                break
            subprocess.run(["sleep", "0.05"])
        child = int(open(self.p("child.pid")).read())
        open(os.path.join(d, "lock"), "w").write(lock_line.replace("{pid}", str(w.pid))
                                                 .replace("{start}", self.start_of(w.pid)) + "\n")
        fn = takeover_text().replace("/data/oneos_compile", d)
        open(self.p("take.sh"), "w").write(fn + "stop_auto_compile\n")
        e = dict(os.environ, PATH=self.p("bin") + ":" + os.environ["PATH"], ROOTD=self.r)
        subprocess.run(["sh", self.p("take.sh")], env=e, timeout=30)

        def alive(pid):
            try:
                os.kill(pid, 0)
                return "Z" not in open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[0]
            except (ProcessLookupError, FileNotFoundError):
                return False
        res = (alive(w.pid), alive(child), os.path.exists(os.path.join(d, "lock")))
        for pid in (w.pid, child):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        w.wait()
        return res

    def test_takeover_stops_the_worker_and_the_compile_it_waits_for(self):
        worker, child, lock = self.takeover("{pid} {start}")
        self.assertFalse(worker)
        self.assertFalse(child, "the foreground compile of the old worker survived the takeover")
        self.assertTrue(lock, "the takeover must leave the lock to the new worker (it takes a dead owner's over)")

    def test_takeover_leaves_a_reused_pid_alone(self):
        worker, child, _ = self.takeover("{pid} 1")
        self.assertTrue(worker)
        self.assertTrue(child)

    def test_takeover_leaves_a_running_now_job_alone(self):
        worker, child, _ = self.takeover("{pid} {start}", mode="now")
        self.assertTrue(worker)
        self.assertTrue(child)

    def test_new_worker_runs_after_the_takeover(self):
        # the lock the takeover left belongs to a dead worker: a new one takes it over and runs
        self.takeover("{pid} {start}")
        self.run_worker("now")
        self.assertEqual(len(self.order()), 6)

    def test_reinstall_of_the_same_version_is_compiled_again(self):
        self.battery("Charging")
        self.run_worker("auto")
        os.remove(self.p("order"))
        open(self.p("tags"), "w").write("com.user.a t2\n")   # reinstalled: same versionCode, new install directory
        self.run_worker("auto")
        self.assertEqual(self.order(), ["com.user.a"])
        os.remove(self.p("order"))
        self.run_worker("auto")
        self.assertEqual(self.order(), [], "an unchanged install was compiled again")

    def test_old_attempted_lines_without_an_install_tag_still_count(self):
        self.battery("Charging")
        self.run_worker("auto")
        d = self.p("data/oneos_compile/attempted")
        old = open(d).read().split("\n")
        self.assertTrue(any("#" in l for l in old))
        open(d, "w").write("".join(l.split("#")[0] + "\n" for l in old if l))
        os.remove(self.p("order"))
        self.run_worker("auto")
        self.assertEqual(self.order(), [])

    def test_pm_without_the_path_listing_falls_back_to_version_only(self):
        self.battery("Charging")
        self.run_worker("auto", NOF="1")
        os.remove(self.p("order"))
        open(self.p("vers"), "w").write("com.user.b 7\n")
        self.run_worker("auto", NOF="1")
        self.assertEqual(self.order(), ["com.user.b"])

    def test_a_timed_out_compile_is_tried_again_by_the_next_pass(self):
        self.battery("Charging")
        self.run_worker("auto", TMOFAIL="com.sys.y")
        self.assertNotIn("com.sys.y", [l.split()[0] for l in open(self.p("data/oneos_compile/attempted"))])

    def test_an_app_whose_version_could_not_be_read_is_not_marked_as_tried(self):
        self.battery("Charging")
        self.run_worker("auto", NOVC="1")
        self.assertEqual(self.lines("data/oneos_compile/attempted"), [])


if __name__ == "__main__":
    unittest.main()
