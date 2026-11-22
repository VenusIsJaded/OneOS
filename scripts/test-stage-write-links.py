#!/usr/bin/env python3
"""Root writes into guest-writable $STAGE/data must not follow a link the guest planted
at the destination (follow-up audit N1 / A1).

no_symlink() in guest_prepare.sh checks directory components only. A guest-root process can
plant a link AT the destination file (e.g. data/takeover.log, data/oneos_host_sf_frozen,
data/oneos_root/enabled, data/oneos_runtime.sh -> a host file), and a host-root redirection,
cp or touch then truncates or overwrites the host file as root. Removing or refusing the
destination first closes it across every host writer.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'switcher/assets'
ROOT_SH = ROOT / 'app-android/app/src/full/assets/root/oneos_root.sh'

FILES = [
    ASSETS / 'guest_prepare.sh',
    ASSETS / 'oneos_switch.sh',
    ASSETS / 'guest_runtime.sh',
    ASSETS / 'boot_status.sh',
    ASSETS / 'return_to_oneui.sh',
    ROOT_SH,
]

DEST_EXPR = (
    r'(?:'
    r'\$(?:STAGE|S)/data/[A-Za-z0-9_./${}-]+'
    r'|/data/local/tmp/oneos/data/[A-Za-z0-9_./-]+'
    r'|\$STATE/[A-Za-z0-9_./${}-]+'
    r'|\$A/(?:oneos|ksu)/[A-Za-z0-9_./${}-]+'
    r'|\$(?:PROF_STAMP|DSDIR|KW|SIGC|BIO_DST|BRQ|KSU_DIR|UFILE|LFILE|TFILE|ICODES|NHOST|VRRDATA|BTD|DAL)(?:/[A-Za-z0-9_./${}-]+|\.tmp)?'
    r'|\\?\$GK(?:\.oneos-tmp)?'
    r'|\$f\.oneos-prev'
    r'|\$FULLLOGS_STATE'
    r'|\$WAVE_STAMP_DEX'
    r'|\$wave_dst/[A-Za-z0-9_.-]+'
    r'|\$ONEOS_BOOT_DIR/[A-Za-z0-9_.-]+'
    r')'
)

REDIR_RE = re.compile(r'(?:>>|>)\s*"?(%s)"?' % DEST_EXPR)
CP_RE = re.compile(
    r'\bcp(?:\s+-[a-zA-Z]+)*\s+[^;|&\n]*?\s+"?(%s)"?(?=\s*(?:[;&|)]|2>|$))' % DEST_EXPR
)
TOUCH_RE = re.compile(
    r'\btouch\s+"?(%s)"?(?=\s|[;&|)]|$)' % DEST_EXPR
)


def extract_host_lines(path: Path):
    """Yield (lineno_0based, line, all_lines) for host-side code (skipping guest-chroot heredocs)."""
    lines = path.read_text().split('\n')
    in_guest_heredoc = None
    guest_tags = {'FIXAPPDIRS', 'ENSUREAPP', 'NETFIX', 'COMPILEEOF', 'APPLOOP', 'PANO', 'ESSOPEN'}
    for i, line in enumerate(lines):
        if in_guest_heredoc:
            if line.strip() == in_guest_heredoc:
                in_guest_heredoc = None
            continue
        m_hd = re.search(r"<<'([A-Z0-9_]+)'", line)
        if m_hd and m_hd.group(1) in guest_tags:
            yield i, line, lines
            in_guest_heredoc = m_hd.group(1)
            continue
        if path.name == 'guest_runtime.sh':
            if re.match(r'^(uninstall|install|customize|post-fs-data|late|restore-props)\)', line.strip()):
                break
        yield i, line, lines


def writes():
    for path in FILES:
        for i, line, lines in extract_host_lines(path):
            stripped = line.lstrip()
            if stripped.startswith('#'):
                continue
            # Strip echo "..." string literals before scanning for > so `echo "foo -> $STAGE/data/bar"` isn't a redir
            clean = re.sub(r'\becho\s+"[^"]*"', 'echo ""', line)
            clean = re.sub(r"\becho\s+'[^']*'", "echo ''", clean)
            for rx in (REDIR_RE, CP_RE, TOUCH_RE):
                for m in rx.finditer(clean):
                    dest = m.group(1)
                    if dest.endswith('.tmp') and 'floating_feature' in dest:
                        continue
                    yield path.name, i, dest, lines


def protected(dest: str, i: int, lines: list[str]) -> bool:
    ctx = '\n'.join(lines[max(0, i - 36):i + 1])
    alts = [re.escape(dest)]
    if dest.startswith('$wave_dst/SystemUI_wave.stock.'):
        alts.append(re.escape('$wave_dst/SystemUI_wave.stock.'))
    for alt in alts:
        if re.search(r'rm\s+-r?f[^\n]*' + alt, ctx):
            return True
        if re.search(r'\[\s*!\s*-L\s+"?' + alt + r'"?\s*\]', ctx):
            return True
    if dest == '$FULLLOGS_STATE' and re.search(r'rm\s+-f[^\n]*\$FULLLOGS_STATE', ctx):
        return True
    return False


class StageWriteLinks(unittest.TestCase):
    def test_every_destination_is_protected_before_it_is_written(self):
        seen, bad = 0, []
        for name, i, dest, lines in writes():
            seen += 1
            if not protected(dest, i, lines):
                bad.append('%s:%d %s' % (name, i + 1, dest))
        self.assertGreaterEqual(seen, 35, 'the pattern no longer finds the known writes: %d' % seen)
        self.assertEqual([], bad)

    def test_known_critical_targets_are_covered(self):
        gp = (ASSETS / 'guest_prepare.sh').read_text()
        sw = (ASSETS / 'oneos_switch.sh').read_text()
        gr = (ASSETS / 'guest_runtime.sh').read_text()
        rt = ROOT_SH.read_text()
        for n in ('fix_app_dirs.sh', 'ensure_app_storage.sh', 'net_fix.sh', 'oneos_compile.sh', 'app_storage_loop.sh'):
            self.assertIn('rm -f "$STAGE/data/local/tmp/%s"' % n, gp)
        self.assertIn('rm -f "$STAGE/data/takeover.log"', sw)
        self.assertIn('rm -f "$STAGE/data/oneos_host_sf_frozen"', sw)
        self.assertIn('rm -f "$FULLLOGS_STATE"', sw)
        self.assertIn('rm -f "$WAVE_STAMP_DEX"', gp)
        self.assertIn('rm -f "$wave_dst/SystemUI_wave.stock.size" "$wave_dst/SystemUI_wave.stock.sha256"', gp)
        self.assertIn('"$S/data/oneos_runtime.sh"', gr)
        self.assertIn('"$A/oneos/bin/su"', gr)
        self.assertIn('rm -f "$STAGE/data/oneos_root/enabled"', rt)
        self.assertIn('rm -f "$STAGE/data/oneos_root/framework"', rt)

    def test_guest_runtime_enable_refuses_final_symlink(self):
        """Reproduces audit A1: pre-planted enabled/framework symlinks must not truncate outside targets."""
        with tempfile.TemporaryDirectory() as d:
            victim = Path(d) / 'victim'
            victim.write_text('KEEP_ME\n')
            stage = Path(d) / 'data/local/tmp/oneos'
            (stage / 'data/oneos_root/adb/modules_update').mkdir(parents=True)
            (stage / '.oneos_stage').write_text(
                '{\n  "schema": 1,\n  "profile": "s25",\n  "image": "pixel9-komodo",\n  "set": "pixel9-sultra",\n  "prepared": 1\n}\n'
            )
            proc = Path(d) / 'proc'
            (proc / '1').mkdir(parents=True)
            (proc / 'self').symlink_to('1')
            (proc / '1/mountinfo').write_text('1 1 0:1 / / rw - ext4 /dev/root rw\n')
            stat_line = '1 (sh) S 0 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 12345 0 0 0\n'
            (proc / '1/stat').write_text(stat_line)
            os.symlink(victim, stage / 'data/oneos_root/enabled')

            env = dict(os.environ, ONEOS_TEST_ROOT=d)
            r = subprocess.run(
                ['dash', str(ROOT_SH), 'guest-runtime', 'pixel9-komodo', 'enable'],
                env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('@@REFUSED', r.stdout)
            self.assertEqual(victim.read_text(), 'KEEP_ME\n')

    def test_the_component_check_alone_lets_a_final_link_through_and_rm_first_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            stage, victim = os.path.join(d, 'stage'), os.path.join(d, 'victim')
            os.makedirs(stage + '/data/local/tmp')
            Path(victim).write_text('HOST FILE\n')
            dest = stage + '/data/local/tmp/oneos_compile.sh'
            os.symlink(victim, dest)
            run = lambda pre: subprocess.run(
                ['sh', '-c', pre + 'cat > "$D" <<\'X\'\npayload\nX\n'],
                env={'D': dest, 'PATH': os.environ['PATH']},
            )
            run('')
            self.assertEqual(Path(victim).read_text(), 'payload\n', 'the reproduction no longer shows the write-through')
            Path(victim).write_text('HOST FILE\n')
            os.remove(dest)
            os.symlink(victim, dest)
            run('rm -f "$D"; ')
            self.assertEqual(Path(victim).read_text(), 'HOST FILE\n')
            self.assertFalse(os.path.islink(dest))

    def test_restore_props_host_refuses_nested_run_symlink(self):
        """Nested run/ symlink under data/oneos_root/adb/oneos must fail closed instead of deleting outside boot stamp."""
        with tempfile.TemporaryDirectory() as d:
            outside = Path(d) / 'outside_run'
            outside.mkdir()
            boot_victim = outside / 'module-props.boot'
            boot_victim.write_text('keep-boot-stamp\n')
            stage = Path(d) / 'stage'
            oneos_dir = stage / 'data/oneos_root/adb/oneos'
            oneos_dir.mkdir(parents=True)
            (oneos_dir / 'run').symlink_to(outside)
            r = subprocess.run(
                ['sh', str(ASSETS / 'guest_runtime.sh'), 'restore-props-host', 'all'],
                env={**os.environ, 'STAGE': str(stage)},
                capture_output=True, text=True,
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('symlink', r.stderr)
            self.assertTrue(boot_victim.exists(), 'outside module-props.boot must not be deleted through a nested symlink')

    def test_bring_watch_status_removes_dotfile_and_rejects_linked_target(self):
        gp = (ASSETS / 'guest_prepare.sh').read_text()
        self.assertIn('rm -f "$BRQ"/*.status "$BRQ"/.[!.]*.status.new "$BRQ"/.watch.pid', gp)
        self.assertIn('cd -P /proc/self/fd/9', gp)
        self.assertIn('rm -f "./.$1.status.new" "./$1.status"', gp)
        self.assertIn('rm -f "$PROF_STAMP"', gp)
        self.assertIn('rm -f "$DSDIR/device_state_configuration.xml"', gp)
        self.assertIn('rm -f "$KW/ksud" "$KW/ksud.real"', gp)
        self.assertIn('rm -f "$SIGC.tmp" "$SIGC"', gp)
        self.assertIn('rm -f "$f.oneos-prev"', gp)

    def test_live_directory_swap_races_are_blocked_by_pinned_fd(self):
        """Regression test for Section 1: BRQ status(), GK libksud wrap, runtime snapshot unlink, and failed find in plain_dir_tree."""
        gp = (ASSETS / 'guest_prepare.sh').read_text()
        sw = (ASSETS / 'oneos_switch.sh').read_text()
        gr = (ASSETS / 'guest_runtime.sh').read_text()

        # 1. plain_dir_tree must fail closed when find exits non-zero even with empty stdout
        pdt_start = gp.index('plain_dir_tree() {')
        pdt_fn = gp[pdt_start:gp.index('\n}\n', pdt_start) + 3]
        with tempfile.TemporaryDirectory() as d:
            real_dir = Path(d) / 'real'
            real_dir.mkdir()
            fake_bin = Path(d) / 'bin'
            fake_bin.mkdir()
            fail_find = fake_bin / 'find'
            fail_find.write_text('#!/bin/sh\nexit 1\n')
            fail_find.chmod(0o755)
            r = subprocess.run(
                ['dash', '-c', pdt_fn + '\nplain_dir_tree "$1"\n', '_', str(real_dir)],
                env={**os.environ, 'PATH': f'{fake_bin}:{os.environ["PATH"]}'},
                capture_output=True, text=True,
            )
            self.assertNotEqual(r.returncode, 0, 'plain_dir_tree must fail closed when find fails')

        # 2. BRQ status() directory swap race between check and write
        bw_body = gp.split("cat > \"$STAGE/bring_watch.sh\" <<'BWEOF'\n", 1)[1].split('\nBWEOF\n', 1)[0]
        st_start = bw_body.index('status() {')
        st_fn = bw_body[st_start:bw_body.index('\n}\n', st_start) + 3]
        # Inject directory swap immediately after fd 9 is opened & verified, proving subsequent writes stay in original inode
        race_hook = (
            'exec 9<&-\n'
            'mv "$BRQ" "$BRQ.moved" && ln -s "$OUTSIDE" "$BRQ"\n'
        )
        raced_st = st_fn.replace('exec 9<&-\n', race_hook, 1)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'stage'
            brq = stage / 'data/oneos_bring_requests'
            brq.mkdir(parents=True)
            outside = root / 'outside'
            outside.mkdir()
            r = subprocess.run(
                ['dash', '-c', f'STAGE="{stage}"\nBRQ="{brq}"\nOUTSIDE="{outside}"\n{raced_st}\nstatus com.example.pkg copying\n'],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((outside / 'com.example.pkg.status').exists(), 'BRQ swap escaped to outside!')
            self.assertEqual((stage / 'data/oneos_bring_requests.moved/com.example.pkg.status').read_text(), 'copying\n')

        # 3. GK libksud wrapper swap race
        gk_start = sw.index('                        if ! cmp -s $STAGE/data/oneos_ksu/ksud "\\$GK" 2>/dev/null; then')
        gk_end = sw.index('                        fi\n                    done', gk_start) + len('                        fi')
        gk_block = sw[gk_start:gk_end]
        # Unescape outer double-quoted activator escapes
        gk_unquoted = re.sub(r'\\([$"\\`])', r'\1', gk_block)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'stage'
            (stage / 'data/oneos_ksu').mkdir(parents=True)
            (stage / 'data/oneos_ksu/ksud').write_text('#!/system/bin/sh\n# wrapper\n')
            arm64 = stage / 'data/app/pkg-1/lib/arm64'
            arm64.mkdir(parents=True)
            (arm64 / 'libksud.so').write_text('old-binary')
            outside = root / 'outside_gk'
            outside.mkdir()
            (outside / 'libksud.so').write_text('HOST_KEEP')
            gk_raced = gk_unquoted.replace(
                'exec 9<&-\n',
                f'exec 9<&-\nmv "{arm64}" "{arm64}.moved" && ln -s "{outside}" "{arm64}"\n',
                1,
            ).replace('$STAGE', str(stage))
            r = subprocess.run(
                ['dash', '-c', f'GK="{arm64}/libksud.so"\ngpkg="pkg"\n{gk_raced}\n'],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((outside / 'libksud.so').read_text(), 'HOST_KEEP', 'GK swap overwrote outside file!')
            self.assertIn('# wrapper', (Path(f'{arm64}.moved') / 'libksud.so').read_text())

        # 4. unlink_prop_snapshots_pinned race when run/ is swapped to outside symlink
        ups_start = gr.index('unlink_prop_snapshots_pinned() {')
        ups_fn = gr[ups_start:gr.index('\n}\n', ups_start) + 3]
        cp_start = gr.index('check_prop_snapshot_dirs() {')
        cp_fn = gr[cp_start:gr.index('\n}\n', cp_start) + 3]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'adb'
            run_dir = adb / 'oneos/run'
            run_dir.mkdir(parents=True)
            (run_dir / 'module-props').write_text('snap')
            (run_dir / 'module-props.boot').write_text('boot')
            outside = root / 'outside_snap'
            outside.mkdir()
            (outside / 'module-props').write_text('KEEP1')
            (outside / 'module-props.boot').write_text('KEEP2')
            ups_raced = ups_fn.replace(
                'exec 9<&-\n',
                f'exec 9<&-\nmv "{run_dir}" "{run_dir}.moved" && ln -s "{outside}" "{run_dir}"\n',
                1,
            )
            r = subprocess.run(
                ['dash', '-c', f'{cp_fn}\n{ups_raced}\nunlink_prop_snapshots_pinned "{adb}" module-props module-props.boot\n'],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((outside / 'module-props').read_text(), 'KEEP1')
            self.assertEqual((outside / 'module-props.boot').read_text(), 'KEEP2')
            self.assertFalse((Path(f'{run_dir}.moved') / 'module-props').exists())
            self.assertFalse((Path(f'{run_dir}.moved') / 'module-props.boot').exists())

    def test_bt_stage_conf_preserves_and_sanitizes_guest_own_config_without_deleting_input_first(self):
        """Regression test for Section 2A: bt_stage_conf called on $BTD/bt_config.conf must not delete its own input before awk reads it."""
        gp = (ASSETS / 'guest_prepare.sh').read_text()
        start = 'BTC=/data/misc/bluedroid/bt_config.conf\n'
        end = '\necho "=== 5d.'
        block = start + gp.split(start, 1)[1].split(end, 1)[0]
        # Replace hardcoded /data/misc/bluedroid with $HOST_BTD (matching directory boundary, not $STAGE/data/misc/bluedroid)
        block = re.sub(r'(?<!\$STAGE)/data/misc/bluedroid', '$HOST_BTD', block)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'stage'
            btd = stage / 'data/misc/bluedroid'
            btd.mkdir(parents=True)
            host_btd = root / 'host_bt'
            host_btd.mkdir(parents=True)
            # Valid AOSP 32-hex link key + One UI leftover 64-hex link key
            valid_key = '0123456789abcdef0123456789abcdef'
            bad_key = valid_key * 2
            (btd / 'bt_config.conf').write_text(
                '[Info]\nFileSource = Empty\n\n'
                '[Adapter]\nAddress = aa:bb:cc:dd:ee:ff\n\n'
                f'[11:22:33:44:55:66]\nDevType = 1\nLinkKey = {valid_key}\n\n'
                f'[aa:bb:cc:11:22:33]\nDevType = 1\nLinkKey = {bad_key}\n'
            )
            script = (
                'chown() { return 0; }\n'
                'chcon() { return 0; }\n'
                'BB="busybox"\n'
                'GUEST_OPLUS=0\n'
                + block
            )
            env = {**os.environ, 'STAGE': str(stage), 'HOST_BTD': str(host_btd), 'ONEOS_GUEST_BT_IMPORT': '0'}
            r = subprocess.run(['dash', '-c', script], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("the guest's own after the checks", r.stdout)
            self.assertTrue((btd / 'bt_config.conf').is_file())
            result = (btd / 'bt_config.conf').read_text()
            self.assertIn('[Adapter]', result)
            self.assertIn('[11:22:33:44:55:66]', result)
            self.assertIn(valid_key, result)
            self.assertNotIn('[aa:bb:cc:11:22:33]', result)
            # Also verify a destination symlink at bt_config.conf.oneos-new or bt_config.conf cannot write through to outside
            outside = root / 'outside_bt'
            outside.write_text('KEEP_HOST_BT\n')
            (btd / 'bt_config.conf.oneos-new').symlink_to(outside)
            r2 = subprocess.run(['dash', '-c', script], env=env, capture_output=True, text=True)
            self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
            self.assertEqual(outside.read_text(), 'KEEP_HOST_BT\n')


if __name__ == '__main__':
    unittest.main()
