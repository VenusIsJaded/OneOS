#!/usr/bin/env python3
"""Verified property writes in guest_props.sh when resetprop -n silently exits 0 without mutating getprop."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'switcher/assets/guest_props.sh'


class GuestPropsSilentNoopFallbackTest(unittest.TestCase):
    def run_add(self, n_silent_noop=True, store_isolated=True):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'stage'
            stage.mkdir()
            (stage / '.oneos_stage').write_text('{"image": "coloros17-findx9u-in-1105"}\n')
            (stage / 'host_screenhole').write_text('511,23:569,81\n')
            (stage / 'display_modes_host.log').write_text('DisplayDeviceInfo{"Built-in Screen": brightnessMaximum 5.0, 415.6 x 415.6 dpi}\n')
            rootfs = root / 'rootfs'
            (rootfs / 'system/framework').mkdir(parents=True)
            (rootfs / 'system/system_ext/framework').mkdir(parents=True)
            (rootfs / 'system/framework/oplus-services.jar').write_text('')
            (rootfs / 'system/system_ext/framework/oplus-telephony-common.jar').write_text('')
            (rootfs / 'system/build.prop').write_text(
                'ro.build.version.sdk_full=37.0\n'
                'ro.oplus.display.screenhole.positon=679,40:762,123\n'
                'ro.config.ringtone=ringtone_001.ogg\n'
                'ro.config.notification_sound=notification_004.ogg\n'
                'ro.config.alarm_alert=ringtone_035.ogg\n'
            )

            props = root / 'props'
            props.mkdir()
            for k, v in {
                'ro.build.version.sdk': '37',
                'ro.build.version.sdk_full': '37.1',
                'persist.sys.oplus.anim_level': '',
                'persist.sys.usb.config': 'sec_charging,adb',
                'sys.usb.config': 'adb',
                'persist.sys.oplus.my_carrier.data_utc_backup': '1786738605',
                'ro.config.ringtone': 'ACH_Galaxy_Bells.ogg',
                'ro.config.isolated_compilation_enabled': 'true',
            }.items():
                (props / k).write_text(v)

            bin_dir = root / 'bin'
            bin_dir.mkdir()

            # Mock getprop reading from props/ directory
            gp = bin_dir / 'getprop'
            gp.write_text(
                '#!/bin/sh\n'
                'if [ "$#" -eq 0 ]; then\n'
                '    for f in "$PROPS"/*; do\n'
                '        [ -f "$f" ] || continue\n'
                '        printf "[%s]: [%s]\\n" "${f##*/}" "$(cat "$f")"\n'
                '    done\n'
                'else\n'
                '    [ -f "$PROPS/$1" ] && cat "$PROPS/$1" || true\n'
                'fi\n'
            )
            gp.chmod(0o755)

            # Mock setprop writing mutable properties to props/
            sp = bin_dir / 'setprop'
            sp.write_text(
                '#!/bin/sh\n'
                'case "$1" in ro.*) exit 1 ;; esac\n'
                'printf "%s" "$2" > "$PROPS/$1"\n'
            )
            sp.chmod(0o755)

            # Mock resetprop: when n_silent_noop is True, any call with -n exits 0 without touching PROPS!
            # Without -n, mutable properties are written via property_service (to PROPS).
            rp = bin_dir / 'resetprop'
            rp.write_text(
                '#!/bin/sh\n'
                'skip=0; file=""; del=0\n'
                'while [ "$#" -gt 0 ]; do\n'
                '    case "$1" in\n'
                '        -n) skip=1; shift ;;\n'
                '        -f) file="$2"; shift 2 ;;\n'
                '        --delete|-d) del=1; shift ;;\n'
                '        -p) shift ;;\n'
                '        *) break ;;\n'
                '    esac\n'
                'done\n'
                'if [ "$del" = 1 ]; then rm -f "$PROPS/$1"; exit 0; fi\n'
                + ('if [ "$skip" = 1 ]; then exit 0; fi\n' if n_silent_noop else '') +
                'if [ -n "$file" ]; then\n'
                '    while IFS= read -r l; do\n'
                '        [ -n "$l" ] || continue\n'
                '        printf "%s" "${l#*=}" > "$PROPS/${l%%=*}"\n'
                '    done < "$file"\n'
                '    exit 0\n'
                'fi\n'
                'case "$1" in ro.*) [ "$skip" = 1 ] || exit 1 ;; esac\n'
                'printf "%s" "$2" > "$PROPS/$1"\n'
            )
            rp.chmod(0o755)

            boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            if store_isolated:
                (stage / 'property_session.active').write_text(boot_id + '\n')

            # Prepare copy of guest_props.sh using our mocked RP and pd_mounted
            script_copy = root / 'guest_props.sh'
            txt = SCRIPT.read_text()
            txt = txt.replace('for c in /data/adb/ksu/bin/resetprop', 'for c in "$MOCK_RP" /data/adb/ksu/bin/resetprop', 1)
            if store_isolated:
                txt = txt.replace('pd_mounted() { awk -v d="$PD" \'$5 == d\' /proc/self/mountinfo | grep -q .; }', 'pd_mounted() { return 0; }', 1)
            else:
                txt = txt.replace('pd_mounted() { awk -v d="$PD" \'$5 == d\' /proc/self/mountinfo | grep -q .; }', 'pd_mounted() { return 1; }', 1)
            script_copy.write_text(txt)
            script_copy.chmod(0o755)

            env = {
                **os.environ,
                'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                'PROPS': str(props),
                'STAGE': str(stage),
                'MOCK_RP': str(rp),
                'ONEOS_GUEST_FORM': 'phone',
            }
            res = subprocess.run(['/bin/sh', str(script_copy), 'add', str(rootfs)], env=env, check=True, capture_output=True, text=True)
            state = {p.name: p.read_text() for p in props.iterdir()}
            added = (stage / 'guest_props.added').read_text().splitlines() if (stage / 'guest_props.added').exists() else []
            ovr = (stage / 'guest_props.overridden').read_text().splitlines() if (stage / 'guest_props.overridden').exists() else []
            return res.stdout, state, added, ovr

    def test_normal_batch_resetprop_succeeds_and_cleans_probe(self):
        out, state, added, ovr = self.run_add(n_silent_noop=False, store_isolated=True)
        self.assertNotIn('WARNING: batch resetprop -n -f did not land', out)
        self.assertNotIn('oneos.props.batch_probe', state)
        self.assertNotIn('oneos.props.batch_probe', added)
        self.assertEqual('1', state.get('persist.sys.saupwk_test'))
        self.assertEqual('1', state.get('persist.sys.oplus.anim_level'))
        self.assertEqual('511,23:569,81', state.get('ro.oplus.display.screenhole.positon'))
        self.assertEqual('37.0', state.get('ro.build.version.sdk_full'))
        self.assertEqual('3', state.get('ro.oplus.gaussianlevel'))

    def test_silent_resetprop_n_noop_falls_back_for_mutable_and_isolated_persist_props(self):
        out, state, added, ovr = self.run_add(n_silent_noop=True, store_isolated=True)
        self.assertIn('WARNING: batch resetprop -n -f did not land in getprop', out)
        # Critical runtime & persist props land via fallback when store is isolated!
        self.assertEqual('1', state.get('persist.sys.saupwk_test'))
        self.assertEqual('1', state.get('persist.sys.oplus.anim_level'))
        self.assertEqual('0', state.get('persist.sys.device_first_boot'))
        self.assertEqual('adb', state.get('persist.sys.usb.config'))
        self.assertEqual('', state.get('persist.sys.oplus.my_carrier.data_utc_backup'))
        # Un-applied ro.* overrides are NOT falsely recorded in guest_props.overridden
        self.assertFalse(any(l.startswith('ro.') for l in ovr), ovr)

    def test_silent_resetprop_n_noop_never_writes_persist_without_isolated_store(self):
        out, state, added, ovr = self.run_add(n_silent_noop=True, store_isolated=False)
        self.assertIn('WARNING: batch resetprop -n -f did not land in getprop', out)
        # Without tmpfs /data/property isolation, persist.* fallback is refused so One UI's store is untouched
        self.assertIsNone(state.get('persist.sys.saupwk_test'))
        self.assertEqual('sec_charging,adb', state.get('persist.sys.usb.config'))


if __name__ == '__main__':
    unittest.main()
