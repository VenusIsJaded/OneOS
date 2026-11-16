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
    def run_add(self, n_silent_noop=True, store_isolated=True, setprop_ro=False):
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
            (rootfs / 'system/system_ext/etc').mkdir(parents=True)
            (rootfs / 'system/system_ext/etc/build.prop').write_text(
                'ro.build.version.oplus.api=40\n'
                'ro.build.version.oplus.sub_api=25\n'
            )
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
            # init's property service takes a NEW ro.* name and refuses an existing one
            # (AOSP property_service.cpp: "Read-only property was already set").
            sp.write_text(
                '#!/bin/sh\n'
                + ('case "$1" in ro.*) [ -e "$PROPS/$1" ] && exit 1 ;; esac\n' if setprop_ro else
                   'case "$1" in ro.*) exit 1 ;; esac\n') +
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

    def test_normal_batch_lands_every_prop_and_records_them(self):
        out, state, added, ovr = self.run_add(n_silent_noop=False, store_isolated=True)
        self.assertNotIn('WARNING', out)
        self.assertNotIn('oneos.props.batch_probe', state)
        self.assertEqual('1', state.get('persist.sys.saupwk_test'))
        self.assertEqual('1', state.get('persist.sys.oplus.anim_level'))
        self.assertEqual('511,23:569,81', state.get('ro.oplus.display.screenhole.positon'))
        self.assertEqual('37.0', state.get('ro.build.version.sdk_full'))
        # The ro.* visual tiers were read in and tested on the Find N6 image only;
        # the Find X9 Ultra image must not get them (Control Center regression).
        for k in ('ro.oplus.gaussianlevel', 'ro.oplus.animationlevel', 'ro.launcher.blur.appLaunch',
                  'ro.surface_flinger.set_display_power_timer_ms'):
            self.assertIsNone(state.get(k), k)
        self.assertIn('ro.oplus.display.screenhole.positon', added)
        # sdk_full is defined by One UI too, so it is a recorded override, not an added name
        self.assertTrue(any(l.startswith('ro.build.version.sdk_full=') for l in ovr), ovr)

    def test_ro_props_are_never_dropped_when_batch_lands(self):
        # 58bc1b9 declared a landed batch failed (probe name missing from the
        # snapshot) and its fallback could not set ro.*: the whole ro.* set was lost.
        out, state, added, ovr = self.run_add(n_silent_noop=False, store_isolated=True)
        self.assertNotIn('falling back', out)
        self.assertNotIn('missed the batch', out)
        self.assertTrue(any(k.startswith('ro.') for k in added), added)

    def test_silent_resetprop_n_noop_retries_mutable_props_only_for_missing_keys(self):
        out, state, added, ovr = self.run_add(n_silent_noop=True, store_isolated=True)
        self.assertIn('still not in getprop after the batch and a per-property retry', out)
        self.assertEqual('1', state.get('persist.sys.saupwk_test'))
        self.assertEqual('1', state.get('persist.sys.oplus.anim_level'))
        self.assertEqual('0', state.get('persist.sys.device_first_boot'))
        self.assertEqual('adb', state.get('persist.sys.usb.config'))
        self.assertEqual('', state.get('persist.sys.oplus.my_carrier.data_utc_backup'))
        # ro.* that did not land are neither recorded as overridden nor as added
        self.assertFalse(any(l.startswith('ro.') for l in ovr), ovr)
        self.assertFalse(any(l.startswith('ro.oplus.display.screenhole') for l in added), added)
        self.assertIn('persist.sys.saupwk_test', added)

    def test_missing_oplus_api_is_reported_loudly(self):
        # com.oplus.blur crash-looped (49 FATAL EXCEPTIONs) when
        # ro.build.version.oplus.api never reached getprop.
        out, state, added, ovr = self.run_add(n_silent_noop=True, store_isolated=True)
        self.assertIsNone(state.get('ro.build.version.oplus.api'))
        self.assertIn('!!! ro.build.version.oplus.api is not set although the image defines it', out)

    def test_ro_props_that_resetprop_n_missed_are_added_through_the_property_service(self):
        # resetprop -n silently did nothing: absent ro.* names are retried with
        # setprop (init accepts a new ro.* name), existing ones are never touched.
        out, state, added, ovr = self.run_add(n_silent_noop=True, store_isolated=True, setprop_ro=True)
        self.assertEqual('40', state.get('ro.build.version.oplus.api'))
        self.assertEqual('25', state.get('ro.build.version.oplus.sub_api'))
        self.assertEqual('511,23:569,81', state.get('ro.oplus.display.screenhole.positon'))
        self.assertIn('ro.build.version.oplus.api', added)
        self.assertNotIn('!!! ro.build.version.oplus.api', out)
        # One UI's own ro.* value stays (write-once)
        self.assertEqual('ACH_Galaxy_Bells.ogg', state.get('ro.config.ringtone'))

    def test_silent_resetprop_n_noop_never_writes_persist_without_isolated_store(self):
        out, state, added, ovr = self.run_add(n_silent_noop=True, store_isolated=False)
        self.assertIn('still not in getprop after the batch', out)
        self.assertIsNone(state.get('persist.sys.saupwk_test'))
        self.assertEqual('sec_charging,adb', state.get('persist.sys.usb.config'))


if __name__ == '__main__':
    unittest.main()
