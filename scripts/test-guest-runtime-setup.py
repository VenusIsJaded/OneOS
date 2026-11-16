#!/usr/bin/env python3
"""Run the actual guest-su and file-bind setup snippets in isolated fixtures."""
from pathlib import Path
import os, subprocess, tempfile, unittest, sys
ROOT=Path(__file__).resolve().parents[1]
class SetupTest(unittest.TestCase):
 def test_native_broker_is_copied_without_a_host_su_file(self):
  script=(ROOT/'switcher/assets/guest_prepare.sh').read_text()
  snippet=script.split('Setting up /system/xbin/su (KernelSU Bridge) ==="',1)[1].split('echo "=== 7.',1)[0]
  with tempfile.TemporaryDirectory() as d:
   stage=Path(d); (stage/'data/oneos_root').mkdir(parents=True);(stage/'data/oneos_root/enabled').touch();(stage/'rootfs/system/xbin').mkdir(parents=True)
   broker=stage/'guest_root';broker.write_bytes(b'guest broker fixture')
   bb=stage/'busybox';bb.write_text('#!/bin/sh\n[ "$1" = mount ] && exit 0\nexit 1\n');bb.chmod(0o755)
   subprocess.run(['sh','-c',snippet],env={**os.environ,'STAGE':d,'ROOTFS':str(stage/'rootfs'),'BB':str(bb)},check=True)
   actual=stage/'rootfs/system/xbin/su';self.assertEqual(actual.read_bytes(),broker.read_bytes());self.assertTrue(actual.stat().st_mode & 0o111)
 def test_missing_xbin_is_added_only_to_enabled_guests_overlay(self):
  script=(ROOT/'switcher/assets/guest_prepare.sh').read_text()
  helper=script[script.index('guest_root_mountpoint() {'):script.index('if [ -d "$SAMCAM_SRC" ] && [ $SAMCAM_IMAGE = 0 ]; then')]
  with tempfile.TemporaryDirectory() as d:
   stage=Path(d);(stage/'rootfs/system').mkdir(parents=True);(stage/'data/oneos_root').mkdir(parents=True);up=stage/'overlay';up.mkdir()
   env={**os.environ,'STAGE':d,'ROOTFS':str(stage/'rootfs')}
   subprocess.run(['sh','-ec',helper+'\nguest_root_mountpoint "'+str(up)+'"'],env=env,check=True)
   self.assertFalse((up/'xbin').exists())
   (stage/'data/oneos_root/enabled').touch()
   subprocess.run(['sh','-ec',helper+'\nguest_root_mountpoint "'+str(up)+'"'],env=env,check=True)
   self.assertTrue((up/'xbin').is_dir());self.assertFalse((stage/'rootfs/system/xbin').exists())
 def test_bind_remount_has_explicit_source_and_target(self):
  script=(ROOT/'switcher/assets/guest_runtime.sh').read_text()
  snippet=script.split('    if [ -f /data/adb/ksu/.allowlist ]',1)[1].split('\n    fi',1)[0]
  # Run only the real remount/cleanup branch, substituting a fixture target.
  snippet=snippet[snippet.index('        if ! "$busy" mount -o remount,bind,ro'):]
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);(base/'allowlist.saved').write_text('preserved grants')
   busy=base/'busybox';busy.write_text('#!/bin/sh\n[ "$#" = 5 ] && [ "$1" = mount ] && [ "$2" = -o ] && [ "$3" = remount,bind,ro ]\n');busy.chmod(0o755)
   subprocess.run(['sh','-ec',snippet.replace('/data/adb/ksu/.allowlist',str(base/'target'))],env={**os.environ,'B':d,'busy':str(busy)},check=True)
   self.assertTrue((base/'allowlist.protected').exists())
class PrivateBindTest(unittest.TestCase):
 def check_bind(self,kind):
  script=(ROOT/'switcher/assets/guest_runtime.sh').read_text()
  start=script.index('verify_private_adb_bind() {')
  function=script[start:script.index('\n}',start)+2]
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);private=base/'private';(private/'modules').mkdir(parents=True)
   root=base/'rootfs';(root/'data').mkdir(parents=True)
   if kind=='wrong-root':(root/'data/adb').mkdir()
   else:(root/'data/adb').symlink_to(private)
   foreign=base/'foreign';foreign.mkdir()
   busy=base/'busybox'
   busy.write_text('#!'+sys.executable+'\nimport os,sys\nassert sys.argv[1:5]==["stat","-L","-c","%d:%i"]\np=sys.argv[-1]\nif os.getenv("BAD_MODULE_BIND") and p.endswith("/rootfs/data/adb/modules"):p=os.environ["FOREIGN"]\ns=os.stat(p)\nprint(str(s.st_dev)+":"+str(s.st_ino))\n');busy.chmod(0o755)
   env={**os.environ,'B':str(busy),'A':str(private),'R':str(root),'FOREIGN':str(foreign)}
   if kind=='wrong-modules':env['BAD_MODULE_BIND']='1'
   result=subprocess.run(['sh','-ec',function+'\nverify_private_adb_bind'],env=env,capture_output=True,text=True)
   self.assertEqual(kind=='valid',result.returncode==0,result.stderr)
 def test_private_root_and_modules_match(self):self.check_bind('valid')
 def test_host_adb_view_is_rejected(self):self.check_bind('wrong-root')
 def test_nested_modules_overmount_is_rejected(self):self.check_bind('wrong-modules')
class VerifyReturnRescanTest(unittest.TestCase):
 def run_verify(self, rescans):
  script=(ROOT/'switcher/assets/guest_runtime.sh').read_text()
  snippet=script[script.index('verify-return)\n')+len('verify-return)\n'):script.index('\n    ;;\nprepare)')]
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);stage=root/'stage';base=stage/'tmp/guest_runtime_baseline';sysd=root/'data_system'
   (stage/'data/oneos_root').mkdir(parents=True);(stage/'data/oneos_root/enabled').touch()
   base.mkdir(parents=True);sysd.mkdir(parents=True)
   pl=sysd/'packages.list';pl.write_text('kqvdie.zeykvw.hpbcyd 10348 0 /data/user/0/kqvdie.zeykvw.hpbcyd default none 0 1\n')
   orig_ino=pl.stat().st_ino
   (base/'ready').touch();(base/'boot').write_text('boot-123\n')
   (root/'boot_id').write_text('boot-123\n')
   (root/'selinux_policy').write_text('policy-bytes')
   import hashlib
   h=hashlib.sha256(b'policy-bytes').hexdigest()
   (base/'selinux').write_text(f'{h}  {root/"selinux_policy"}\n')
   (root/'modules').write_text('sec_Blocking 16384 0 - Live 0x0\n')
   (base/'kernel-modules').write_text('sec_Blocking 16384 0x0\n')
   (base/'root').write_text('allow 10383 unmount=0\nmanager 10348\n')
   marker=root/'rescan_seen'
   audit=stage/'guest_root'
   audit.write_text(
    '#!/bin/sh\n'
    f'if [ "{int(rescans)}" = 1 ] && [ -f "{marker}" ]; then\n'
    '  printf "manager 10348\\nallow 10383 unmount=0\\n"\n'
    'else\n'
    '  printf "manager 65535\\nallow 10348 unmount=0\\nallow 10383 unmount=0\\n"\n'
    'fi\n'
   );audit.chmod(0o755)
   # Wrap mv so moving .oneos_ksu_rescan.* -> packages.list simulates KernelSU pkg_observer re-crowning
   bin_dir=root/'bin';bin_dir.mkdir()
   real_mv=subprocess.check_output(['sh','-c','command -v mv'],text=True).strip()
   mv_wrap=bin_dir/'mv'
   mv_wrap.write_text(
    '#!/bin/sh\n'
    f'case "$*" in *packages.list*) : > "{marker}" ;; esac\n'
    f'exec "{real_mv}" "$@"\n'
   );mv_wrap.chmod(0o755)
   patched=(snippet
    .replace('/data/system',str(sysd))
    .replace('/proc/sys/kernel/random/boot_id',str(root/'boot_id'))
    .replace('/sys/fs/selinux/policy',str(root/'selinux_policy'))
    .replace('/proc/modules',str(root/'modules')))
   runner=root/'run.sh'
   runner.write_text(f'#!/bin/sh\nset -eu\nS="{stage}"\nSTATE="$S/data/oneos_root"\nPATH="{bin_dir}:$PATH"\n'+patched+'\n')
   runner.chmod(0o755)
   res=subprocess.run(['dash',str(runner)],capture_output=True,text=True)
   return res, pl.read_text(), pl.stat().st_ino, orig_ino
 def test_recrowns_host_manager_after_data_app_unbridge_preserving_bytes_and_mode(self):
  res, content, _, _ = self.run_verify(rescans=True)
  self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
  self.assertIn('live host grants, manager, SELinux policy and kernel modules match baseline', res.stdout)
  self.assertEqual(content, 'kqvdie.zeykvw.hpbcyd 10348 0 /data/user/0/kqvdie.zeykvw.hpbcyd default none 0 1\n')
 def test_still_fails_with_manager_diagnostic_when_grants_genuinely_differ(self):
  res, content, _, _ = self.run_verify(rescans=False)
  self.assertNotEqual(res.returncode, 0)
  self.assertIn('mgr=10348->65535', res.stdout)
  self.assertEqual(content, 'kqvdie.zeykvw.hpbcyd 10348 0 /data/user/0/kqvdie.zeykvw.hpbcyd default none 0 1\n')
if __name__=='__main__': unittest.main()
