#!/usr/bin/env python3
"""Verify app_data_labels in guest_prepare.sh skips unchanged kept dirs on warm boots while still relabeling pre-seeded/new/changed dirs."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PREPARE = REPO_ROOT / "switcher" / "assets" / "guest_prepare.sh"


def extract_app_data_labels() -> str:
    text = PREPARE.read_text(encoding="utf-8")
    start = text.index("app_data_labels() {")
    end = text.index("p=$($BB nproc", start)
    return text[start:end]


class AppDataLabelsSkipTest(unittest.TestCase):
    def run_helper(self, skip_kept: str) -> list[str]:
        func = extract_app_data_labels()
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            (stage / "data" / "system").mkdir(parents=True)
            user0 = stage / "data" / "user" / "0"
            user0.mkdir(parents=True)
            (stage / "data" / "system" / "packages.list").write_text(
                "com.example.kept 10100 0 /data/user/0/com.example.kept default:targetSdkVersion=37 none 0 1\n"
                "com.example.fresh 10101 0 /data/user/0/com.example.fresh default:targetSdkVersion=37 none 0 1\n"
                "com.android.systemui 10102 0 /data/user/0/com.android.systemui platform:privapp:targetSdkVersion=37 none 0 1\n",
                encoding="utf-8",
            )
            for pkg in ("com.example.kept", "com.example.fresh", "com.android.systemui"):
                (user0 / pkg).mkdir()

            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            fake_ls = bin_dir / "ls"
            fake_ls.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-dZ\" ]; then\n"
                f"  echo 'u:object_r:app_data_file:s0:c100,c256,c512,c768 {user0}/com.example.kept'\n"
                f"  echo 'u:object_r:shell_data_file:s0 {user0}/com.example.fresh'\n"
                f"  echo 'u:object_r:privapp_data_file:s0:c512,c768 {user0}/com.android.systemui'\n"
                "  exit 0\n"
                "fi\n"
                "exec /bin/ls \"$@\"\n",
                encoding="utf-8",
            )
            fake_ls.chmod(0o755)

            run = Path(tmp) / "run.sh"
            run.write_text(
                f"#!/bin/sh\nset -eu\nSTAGE='{stage}'\nPRE_PKGS='com.android.providers.settings com.android.systemui'\nPATH='{bin_dir}':\"$PATH\"\n"
                + func
                + f"\napp_data_labels '{user0}' '{skip_kept}'\n",
                encoding="utf-8",
            )
            run.chmod(0o755)
            out = subprocess.run(["dash", str(run)], capture_output=True, text=True, check=True)
            return out.stdout.strip().splitlines()

    def test_warm_boot_skips_unchanged_kept_app_but_relabels_pre_pkgs_and_fresh(self) -> None:
        warm = "\n".join(self.run_helper("1"))
        self.assertIn("# 2 1", warm)
        self.assertNotIn("com.example.kept", warm)
        self.assertIn("com.example.fresh", warm)
        self.assertIn("com.android.systemui", warm)

    def test_cold_boot_relabels_all_kept_apps_too(self) -> None:
        cold = "\n".join(self.run_helper("0"))
        self.assertIn("# 2 1", cold)
        self.assertIn("com.example.kept", cold)
        self.assertIn("com.example.fresh", cold)
        self.assertIn("com.android.systemui", cold)


if __name__ == "__main__":
    unittest.main()
