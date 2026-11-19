#!/usr/bin/env python3
"""Run the actual shared boot-flag handoff with and without the guest runtime."""
import os,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=(ROOT/'switcher/assets/guest_start.sh').read_text()
# Only the decision block (the `if [ -f /data/oneos_root/enabled ]; then setprop ...; fi` that follows the comment).
# The old slice ran on to the "Launching guest Zygote" line: 570 lines with the guest's odrefresh, Bring-apps, wave
# APK and sm_diag blocks, which as root ran host odrefresh and wrote host /data/odrefresh.log (follow-up audit N12).
_a=s.index('# Properties are shared: One UI left boot_completed=1.')
_b=s.index('\nfi\n',s.index('if [ -f /data/oneos_root/enabled ]; then',_a))+len('\nfi\n')
RESET=s[_a:_b]
assert RESET.count('\n')<12,'the boot-flag block grew: re-check what this test executes'
for _bad in ('odrefresh','/data/oneos_sm_diag.log','/apex','pm ','mount'):assert _bad not in RESET,_bad
class BootGateTest(unittest.TestCase):
 def run_handoff(self,enabled):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   for key in ['sys.boot_completed','dev.bootcomplete']:(root/key).write_text('1')
   marker=root/'enabled'
   if enabled:marker.touch()
   code='setprop() { printf "%s" "$2" > "$STATE/$1"; }\n'+RESET.replace('/data/oneos_root/enabled',str(marker))
   result=subprocess.run(['sh','-ec',code],env={**os.environ,'STATE':d},capture_output=True,text=True)
   self.assertEqual(0,result.returncode,result.stderr)
   for key in ['sys.boot_completed','dev.bootcomplete']:self.assertEqual('0' if enabled else '1',(root/key).read_text())
 def test_enabled_runtime_clears_stale_host_completion_before_services(self):
  self.run_handoff(True)
  self.assertLess(s.index(RESET),s.index('/data/oneos_runtime.sh late'))
 def test_disabled_runtime_leaves_shared_host_flags_unchanged(self):self.run_handoff(False)
if __name__=='__main__':unittest.main()
