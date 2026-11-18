#!/usr/bin/env python3
"""Verify batched ART profile directory creation in guest_prepare.sh step 5.9."""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PREPARE = REPO_ROOT / "switcher" / "assets" / "guest_prepare.sh"


def extract_profile_snippet() -> str:
    text = PREPARE.read_text(encoding="utf-8")
    start = text.index("prof_made=0")
    end = text.index('echo "=== 5.10 $(date +%T) realign ==="')
    return text[start:end]


class ArtProfileBatchTest(unittest.TestCase):
    def test_creates_missing_dirs_preserves_existing_and_fast(self) -> None:
        snippet = extract_profile_snippet()
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            cur_dir = stage / "data" / "misc" / "profiles" / "cur" / "0"
            ref_dir = stage / "data" / "misc" / "profiles" / "ref"
            sys_dir = stage / "data" / "system"
            cur_dir.mkdir(parents=True)
            ref_dir.mkdir(parents=True)
            sys_dir.mkdir(parents=True)

            # Pre-populate 100 existing packages with profile files to ensure they stay untouched
            pkg_lines = []
            for i in range(500):
                pkg = f"com.example.pkg{i:03d}"
                uid = 10000 + (i % 80)
                pkg_lines.append(f"{pkg} {uid} 0 /data/user/0/{pkg} default:targetSdkVersion=37 none 0 1")
                if i < 100:
                    (cur_dir / pkg).mkdir()
                    (cur_dir / pkg / "primary.prof").write_bytes(b"PROF")
                    (ref_dir / pkg).mkdir()
                    (ref_dir / pkg / "primary.prof").write_bytes(b"RPROF")
            # Invalid/malformed lines must be ignored safely
            pkg_lines.append("bad/pkg 10999 0 /data/user/0/bad default none 0 1")
            pkg_lines.append("com.example.baduid notanumber 0 /data/user/0/x default none 0 1")
            (sys_dir / "packages.list").write_text("\n".join(pkg_lines) + "\n", encoding="utf-8")

            # Fake busybox wrapper delegating find/awk/xargs to system tools
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            bb = bin_dir / "busybox"
            bb.write_text(
                "#!/bin/sh\n"
                "applet=$1; shift\n"
                "exec \"$applet\" \"$@\"\n",
                encoding="utf-8",
            )
            bb.chmod(0o755)
            # Fake chown to capture ownership assignments without requiring root
            chown_log = Path(tmp) / "chown.log"
            fake_chown = bin_dir / "chown"
            fake_chown.write_text(
                f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{chown_log}'\nexit 0\n",
                encoding="utf-8",
            )
            fake_chown.chmod(0o755)

            script = Path(tmp) / "run.sh"
            script.write_text(
                f"#!/bin/sh\nset -eu\nSTAGE='{stage}'\nBB='{bb}'\nPATH='{bin_dir}':\"$PATH\"\n"
                + snippet,
                encoding="utf-8",
            )
            script.chmod(0o755)

            t0 = time.monotonic()
            res = subprocess.run(["dash", str(script)], capture_output=True, text=True, check=True)
            elapsed = time.monotonic() - t0

            # 400 missing packages * 2 dirs (cur + ref) = 800 dirs created
            self.assertIn("ART profile dirs pre-created before Zygote: 800", res.stdout)
            # Wall-clock is machine-dependent (2.2-2.4 s on a slow CI box, 0.4 s here): this only catches a return to a
            # per-package process loop (about 1,500 forks, tens of seconds). The batching itself is asserted by
            # test_fresh_tree_gets_every_directory_in_batches below, which counts mkdir invocations.
            self.assertLess(elapsed, 15.0, f"Batched profile creation took too long: {elapsed:.2f}s")

            # Existing profiles preserved
            self.assertEqual((cur_dir / "com.example.pkg000" / "primary.prof").read_bytes(), b"PROF")
            self.assertEqual((ref_dir / "com.example.pkg000" / "primary.prof").read_bytes(), b"RPROF")

            # Newly created dirs have exact permissions (0700 cur, 0771 ref)
            cur_mode = stat.S_IMODE((cur_dir / "com.example.pkg250").stat().st_mode)
            ref_mode = stat.S_IMODE((ref_dir / "com.example.pkg250").stat().st_mode)
            self.assertEqual(cur_mode, 0o700)
            self.assertEqual(ref_mode, 0o771)

            # Malformed lines rejected
            self.assertFalse((cur_dir / "com.example.baduid").exists())

            # Warm second run creates 0 dirs
            res2 = subprocess.run(["dash", str(script)], capture_output=True, text=True, check=True)
            self.assertEqual(res2.stdout.strip(), "")

    def run_snippet(self, tmp, stage, packages, with_dirs=()):
        """Runs the step 5.9 snippet in a temp tree; returns (stdout, mkdir invocation count)."""
        cur_dir = stage / "data" / "misc" / "profiles" / "cur" / "0"
        ref_dir = stage / "data" / "misc" / "profiles" / "ref"
        sys_dir = stage / "data" / "system"
        for d in (cur_dir, ref_dir, sys_dir):
            d.mkdir(parents=True, exist_ok=True)
        for pkg in with_dirs:
            (cur_dir / pkg).mkdir(exist_ok=True)
            (ref_dir / pkg).mkdir(exist_ok=True)
        (sys_dir / "packages.list").write_text(
            "".join(f"{pkg} {uid} 0 /data/user/0/{pkg} default none 0 1\n" for pkg, uid in packages), encoding="utf-8")
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir(exist_ok=True)
        bb = bin_dir / "busybox"
        bb.write_text('#!/bin/sh\napplet=$1; shift\nexec "$applet" "$@"\n', encoding="utf-8")
        bb.chmod(0o755)
        mk_log = Path(tmp) / "mkdir.log"
        real_mkdir = subprocess.run(["which", "mkdir"], capture_output=True, text=True).stdout.strip()
        for name, body in (
            ("chown", "#!/bin/sh\nexit 0\n"),
            ("mkdir", f"#!/bin/sh\necho x >> '{mk_log}'\nexec {real_mkdir} \"$@\"\n"),
        ):
            f = bin_dir / name
            f.write_text(body, encoding="utf-8")
            f.chmod(0o755)
        script = Path(tmp) / "run.sh"
        script.write_text(
            f"#!/bin/sh\nset -eu\nSTAGE='{stage}'\nBB='{bb}'\nPATH='{bin_dir}':\"$PATH\"\n" + extract_profile_snippet(),
            encoding="utf-8")
        res = subprocess.run(["dash", str(script)], capture_output=True, text=True, check=True)
        return res.stdout, len(mk_log.read_text().splitlines()) if mk_log.exists() else 0

    def test_fresh_tree_gets_every_directory_in_batches(self) -> None:
        # a fresh or cleared profile tree: prof.have is EMPTY. The package list used to be taken for the "have" file
        # (NR == FNR is true for all of the second file then) and nothing was created.
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            pkgs = [(f"com.example.p{i:03d}", 10000 + i) for i in range(60)]
            out, mkdirs = self.run_snippet(tmp, stage, pkgs)
            self.assertIn("ART profile dirs pre-created before Zygote: 120", out)
            cur = stage / "data/misc/profiles/cur/0"
            ref = stage / "data/misc/profiles/ref"
            for pkg, _ in pkgs:
                self.assertTrue((cur / pkg).is_dir(), pkg)
                self.assertTrue((ref / pkg).is_dir(), pkg)
            self.assertEqual(stat.S_IMODE((cur / pkgs[7][0]).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((ref / pkgs[7][0]).stat().st_mode), 0o771)
            # batched: a handful of mkdir invocations (the tree itself plus xargs), not one per directory
            self.assertLess(mkdirs, 10, f"{mkdirs} mkdir invocations for 120 directories")

    def test_empty_have_with_one_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            out, _ = self.run_snippet(tmp, stage, [("com.only.one", 10123)])
            self.assertIn("pre-created before Zygote: 2", out)

    def test_only_the_missing_ones_are_created_when_some_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            pkgs = [("com.a", 10001), ("com.b", 10002), ("com.c", 10003)]
            out, _ = self.run_snippet(tmp, stage, pkgs, with_dirs=["com.a"])
            self.assertIn("pre-created before Zygote: 4", out)


if __name__ == "__main__":
    unittest.main()
