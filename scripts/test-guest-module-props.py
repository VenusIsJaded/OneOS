#!/usr/bin/env python3
"""Check restoration of module property overrides for a guest-only restart."""
import os,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=(ROOT/'switcher/assets/guest_runtime.sh').read_text()
RESTORE=s[s.index('restore-props)')+len('restore-props)'):s.index('uninstall)')].rsplit(';;',1)[0]
class ModulePropsTest(unittest.TestCase):
 def test_existing_empty_and_new_properties_restore_without_shell_evaluation(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'oneos/run').mkdir(parents=True);(root/'ksu/bin').mkdir(parents=True);(root/'props').mkdir();(root/'bin').mkdir()
   snap=root/'oneos/run/module-props';snap.write_text('ro.test.present|1|original value\nro.test.empty|1|\nro.test.added|0|\nro.test.quoted|1|$(touch /tmp/oneos-must-not-exist)|literal\n')
   # a stateful fake: resetprop logs the call and really sets/deletes, getprop reads it back (the restore verifies)
   rp=root/'ksu/bin/resetprop';rp.write_text('#!/bin/sh\nprintf "[%s][%s][%s]\\n" "$1" "$2" "${3-}" >> "$CALL_LOG"\ncase "$1" in -n) printf "%s" "$3" > "$PROPS/$2" ;; --delete) rm -f "$PROPS/$2" ;; esac\n');rp.chmod(0o755)
   gp=root/'bin/getprop';gp.write_text('#!/bin/sh\n[ -f "$PROPS/$1" ] && cat "$PROPS/$1"\nexit 0\n');gp.chmod(0o755)
   (root/'props/ro.test.added').write_text('module added')
   env={**os.environ,'CALL_LOG':str(root/'calls'),'PROPS':str(root/'props'),'PATH':str(root/'bin')+':'+os.environ['PATH']}
   result=subprocess.run(['sh','-ec',RESTORE.replace('A=/data/adb','A='+d)],env=env,capture_output=True,text=True)
   self.assertEqual(0,result.returncode,result.stderr);calls=(root/'calls').read_text()
   self.assertIn('[-n][ro.test.present][original value]',calls);self.assertIn('[-n][ro.test.empty][]',calls);self.assertIn('[--delete][ro.test.added][]',calls)
   self.assertIn('$(touch /tmp/oneos-must-not-exist)|literal',calls);self.assertFalse(snap.exists())
class RestoreReadBackTest(unittest.TestCase):
 """resetprop exit 0 is not proof the property changed (N14): the restore reads each one back."""
 def run_restore(self,noop=False,snap_text=None):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'oneos/run').mkdir(parents=True);(root/'ksu/bin').mkdir(parents=True);(root/'props').mkdir();(root/'bin').mkdir()
   snap=root/'oneos/run/module-props'
   snap.write_text(snap_text or 'ro.test.present|1|original value\nro.test.empty|1|\nro.test.added|0|\n')
   (root/'props/ro.test.present').write_text('module value');(root/'props/ro.test.added').write_text('module added')
   rp=root/'ksu/bin/resetprop'
   rp.write_text('#!/bin/sh\n[ -n "$NOOP" ] && exit 0\ncase "$1" in -n) printf "%s" "$3" > "$PROPS/$2" ;; --delete) rm -f "$PROPS/$2" ;; esac\n');rp.chmod(0o755)
   gp=root/'bin/getprop';gp.write_text('#!/bin/sh\n[ -f "$PROPS/$1" ] && cat "$PROPS/$1"\nexit 0\n');gp.chmod(0o755)
   env={**os.environ,'PATH':str(root/'bin')+':'+os.environ['PATH'],'PROPS':str(root/'props')}
   if noop:env['NOOP']='1'
   r=subprocess.run(['sh','-c',RESTORE.replace('A=/data/adb','A='+d)],env=env,capture_output=True,text=True)
   return r,snap.exists(),{f.name:f.read_text() for f in (root/'props').iterdir()}
 def test_a_restore_that_landed_removes_the_snapshot(self):
  r,kept,props=self.run_restore()
  self.assertEqual((0,False),(r.returncode,kept),r.stderr)
  self.assertEqual({'ro.test.present':'original value','ro.test.empty':''},props)
 def test_a_restore_that_did_not_land_keeps_the_snapshot_and_fails(self):
  r,kept,props=self.run_restore(noop=True)
  self.assertEqual(1,r.returncode);self.assertTrue(kept,'the only record was deleted although nothing was restored')
  self.assertIn('ro.test.present was not restored',r.stderr);self.assertIn('ro.test.added was not removed',r.stderr)
  self.assertIn('2 module properties not restored',r.stderr)
 def test_a_malformed_key_still_stops_the_restore_before_any_write(self):
  r,kept,props=self.run_restore(snap_text='bad key|1|x\n')
  self.assertEqual(1,r.returncode);self.assertTrue(kept)
if __name__=='__main__':unittest.main()
