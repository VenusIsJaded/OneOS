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


PM = """#!/bin/sh
# the package list: $ROOTD/pkgs when a test wrote one; versionCodes from $ROOTD/vers ("pkg code"), default 1
pk() { if [ -f "$ROOTD/pkgs" ]; then cat "$ROOTD/pkgs"; else printf 'com.sys.x\\ncom.user.a\\ncom.android.systemui\\ncom.user.b\\ncom.android.launcher\\ncom.sys.y\\n'; fi; }
case "$3" in
  -3) printf 'package:com.user.a\\npackage:com.user.b\\n' ;;
  --show-versioncode)
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
        for name, body in (("pm", PM), ("cmd", CMD), ("sleep", SLEEP)):
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


if __name__ == "__main__":
    unittest.main()
