#!/usr/bin/env python3
"""Runs ensure_app_storage.sh (the ENSUREAPP heredoc in guest_prepare.sh) against a temp /data.

Covers the empty-listing case (a tree with no app directories yet): the join used NR == FNR, which is also true for
the whole package list when the listing before it is empty, so no package got its CE/DE, Android/data, media or obb
directory.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "switcher/assets/guest_prepare.sh")).read()


def script_text():
    a = SRC.index("<<'ENSUREAPP'\n") + len("<<'ENSUREAPP'\n")
    b = SRC.index("\nENSUREAPP\n", a)
    return SRC[a:b] + "\n"


class Ensure(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        os.makedirs(self.t + "/bin")
        os.makedirs(self.t + "/data/system")
        os.makedirs(self.t + "/data/local/tmp")
        # chown/chmod need root; stat reports the uid a test sets per dir through a "<dir>.uid" side file
        for n, body in (
            ("chown", '#!/bin/sh\necho "$*" >> "%s/chown.log"\nfor last; do :; done\n'
                      '[ "$1" = -h ] || echo "$1" > "$last.uid"\nexit 0\n' % self.t),
            ("stat", '#!/bin/sh\n# stat -c "%%u %%n" PATH...: uid from PATH.uid (default 0)\nshift 2\n'
                     'for p; do u=0; [ -f "$p.uid" ] && u=$(cat "$p.uid"); echo "$u $p"; done\n'),
        ):
            p = self.t + "/bin/" + n
            open(p, "w").write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        txt = script_text().replace("/data/", self.t + "/data/").replace(" /data ", " " + self.t + "/data ")
        # the find ... -exec stat {} + must call the stub: keep find, replace only the binary through PATH
        open(self.t + "/ensure.sh", "w").write(txt)

    def packages(self, pk):
        open(self.t + "/data/system/packages.list", "w").write(
            "".join("%s %d 0 /data/user/0/%s default none 0 1\n" % (n, u, n) for n, u in pk))

    def run_it(self):
        e = dict(os.environ, PATH=self.t + "/bin:" + os.environ["PATH"])
        # find -exec stat: GNU find execs "stat" from PATH, which is the stub
        return subprocess.run(["sh", self.t + "/ensure.sh"], env=e, capture_output=True, text=True, timeout=30)

    def d(self, *p):
        return os.path.join(self.t, "data", *p)

    def test_empty_tree_gets_every_directory(self):
        pk = [("com.a.one", 10001), ("com.b.two", 10002), ("com.c.three", 10003)]
        self.packages(pk)
        r = self.run_it()
        for n, _ in pk:
            for base in ("user/0", "user_de/0", "media/0/Android/data", "media/0/Android/media", "media/0/Android/obb"):
                self.assertTrue(os.path.isdir(self.d(base, n)), "%s/%s missing\n%s" % (base, n, r.stdout + r.stderr))
        self.assertIn("created", r.stdout)

    def test_empty_tree_one_package(self):
        self.packages([("com.only.one", 10050)])
        self.run_it()
        self.assertTrue(os.path.isdir(self.d("user/0/com.only.one")))
        self.assertTrue(os.path.isdir(self.d("media/0/Android/obb/com.only.one")))

    def test_existing_directories_are_left_and_only_missing_ones_made(self):
        self.packages([("com.have.it", 10001), ("com.need.it", 10002)])
        for base in ("user/0", "user_de/0", "media/0/Android/data", "media/0/Android/media", "media/0/Android/obb"):
            os.makedirs(self.d(base, "com.have.it"))
        open(self.d("user/0/com.have.it/keep"), "w").close()
        for base in ("user/0", "user_de/0"):
            open(self.d(base, "com.have.it.uid"), "w").write("10001\n")
        for base in ("media/0/Android/data", "media/0/Android/media", "media/0/Android/obb"):
            open(self.d(base, "com.have.it.uid"), "w").write("10001\n")
        self.run_it()
        self.assertTrue(os.path.exists(self.d("user/0/com.have.it/keep")))
        self.assertTrue(os.path.isdir(self.d("user/0/com.need.it")))
        self.assertTrue(os.path.isdir(self.d("media/0/Android/data/com.need.it")))

    def test_wrong_owner_is_realigned(self):
        self.packages([("com.moved.uid", 10077)])
        for base in ("user/0", "user_de/0", "media/0/Android/data", "media/0/Android/media", "media/0/Android/obb"):
            os.makedirs(self.d(base, "com.moved.uid"))
            open(self.d(base, "com.moved.uid.uid"), "w").write("1000\n")
        r = self.run_it()
        self.assertIn("realigned 5", r.stdout)
        self.assertIn("10077:10077", open(self.t + "/chown.log").read())

    def test_malformed_package_lines_are_ignored(self):
        open(self.d("system/packages.list"), "w").write(
            "bad/pkg 10999 0 x\ncom.bad.uid notanumber 0 x\ncom.good.one 10001 0 x\n")
        self.run_it()
        self.assertTrue(os.path.isdir(self.d("user/0/com.good.one")))
        self.assertFalse(os.path.exists(self.d("user/0/com.bad.uid")))
        self.assertFalse(os.path.exists(self.d("user/0/bad")))


if __name__ == "__main__":
    unittest.main()
