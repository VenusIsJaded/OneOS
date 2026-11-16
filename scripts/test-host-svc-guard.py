#!/usr/bin/env python3
"""Verify stop_host_svcs in oneos_switch.sh queries targeted init.svc.* properties and stops resurrected host services."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SWITCH = REPO_ROOT / "switcher" / "assets" / "oneos_switch.sh"


def extract_guard_func() -> str:
    text = SWITCH.read_text(encoding="utf-8")
    start = text.index("    stop_host_svcs() {")
    end = text.index("    ticks=0; fast_until=600", start)
    return text[start:end]


class HostSvcGuardTest(unittest.TestCase):
    def test_targeted_getprop_and_conditional_stops(self) -> None:
        snippet = extract_guard_func()
        self.assertIn(': "ctl.stop zygote"', snippet)
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            (stage / "data").mkdir(parents=True)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            calls = Path(tmp) / "calls.log"

            # Fake getprop fails if invoked with 0 args (proving no full-table dump)
            getprop = bin_dir / "getprop"
            getprop.write_text(
                "#!/bin/sh\n"
                f"if [ $# -eq 0 ]; then echo 'FULL_DUMP_FORBIDDEN' >> '{calls}'; exit 2; fi\n"
                "case \"$1\" in\n"
                "  init.svc.zygote|init.svc.surfaceflinger|init.svc.audioserver|init.svc.dumpstate) echo running ;;\n"
                "  *) echo stopped ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            getprop.chmod(0o755)

            setprop = bin_dir / "setprop"
            setprop.write_text(
                f"#!/bin/sh\necho \"$1=$2\" >> '{calls}'\n",
                encoding="utf-8",
            )
            setprop.chmod(0o755)

            # Case 1: surfaceflinger frozen, guest_audio off -> stops zygote & dumpstate, NOT surfaceflinger or audioserver
            (stage / "data" / "oneos_host_sf_frozen").write_text("2051\n", encoding="utf-8")
            run = Path(tmp) / "run.sh"
            run.write_text(
                f"#!/bin/sh\nset -eu\nSTAGE='{stage}'\nPATH='{bin_dir}':\"$PATH\"\n"
                + snippet
                + "\nstop_host_svcs\necho \"acted=$acted\"\n",
                encoding="utf-8",
            )
            run.chmod(0o755)

            res1 = subprocess.run(["dash", str(run)], capture_output=True, text=True, check=True)
            self.assertIn("acted=1", res1.stdout)
            logged1 = calls.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("FULL_DUMP_FORBIDDEN", logged1)
            self.assertIn("ctl.stop=zygote", logged1)
            self.assertIn("ctl.stop=dumpstate", logged1)
            self.assertNotIn("ctl.stop=surfaceflinger", logged1)
            self.assertNotIn("ctl.stop=audioserver", logged1)

            # Case 2: surfaceflinger unfrozen, guest_audio on -> stops surfaceflinger & audioserver too
            calls.unlink()
            (stage / "data" / "oneos_host_sf_frozen").unlink()
            (stage / "data" / "guest_audio.flag").touch()
            res2 = subprocess.run(["dash", str(run)], capture_output=True, text=True, check=True)
            self.assertIn("acted=1", res2.stdout)
            logged2 = calls.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("FULL_DUMP_FORBIDDEN", logged2)
            self.assertIn("ctl.stop=surfaceflinger", logged2)
            self.assertIn("ctl.stop=audioserver", logged2)


if __name__ == "__main__":
    unittest.main()
