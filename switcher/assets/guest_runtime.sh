#!/system/bin/sh
# OneOS full-stack experiment. Host setup and guest lifecycle share this file.
# There are no calls to a host root manager or global injector monitor.
set -eu
S=${STAGE:-/data/local/tmp/oneos}
R=$S/rootfs
STATE=$S/data/oneos_root
verify_private_adb_bind() {
    for suffix in '' /modules; do
        expected=$("$B" stat -L -c %d:%i "$A$suffix") || return 1
        actual=$("$B" stat -L -c %d:%i "$R/data/adb$suffix") || return 1
        [ "$expected" = "$actual" ] || return 1
    done
}

encode_prop_val() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/\r/\\r/g' | awk 'NR > 1 { printf "\\n" } { printf "%s", $0 }'
}

decode_prop_val() {
    awk -v s="$1" 'BEGIN {
        out = ""
        n = length(s)
        for (i = 1; i <= n; i++) {
            c = substr(s, i, 1)
            if (c == "\\" && i < n) {
                nx = substr(s, i + 1, 1)
                if (nx == "n") { out = out "\n"; i++; continue }
                if (nx == "r") { out = out "\r"; i++; continue }
                if (nx == "\\") { out = out "\\"; i++; continue }
            }
            out = out c
        }
        printf "%s", out
    }'
}

check_prop_snapshot_dirs() {
    for _cpd in "$1" "$1/oneos" "$1/oneos/run"; do
        [ ! -L "$_cpd" ] || { echo "Guest runtime: symlink $_cpd" >&2; return 1; }
    done
    return 0
}

restore_module_props() {
    rp_dir=$1
    mode=${2:-all}
    check_prop_snapshot_dirs "$rp_dir" || return 1
    snapshot="$rp_dir/oneos/run/module-props"
    boot_stamp="$rp_dir/oneos/run/module-props.boot"
    [ ! -L "$snapshot" ] || { echo "Guest runtime: symlink $snapshot" >&2; return 1; }
    [ ! -L "$boot_stamp" ] || { echo "Guest runtime: symlink $boot_stamp" >&2; return 1; }
    [ -f "$snapshot" ] || { rm -f "$boot_stamp"; return 0; }
    rp_bin=""
    for c in "$rp_dir/ksu/bin/resetprop" /data/adb/ksu/bin/resetprop /data/adb/ap/bin/resetprop /debug_ramdisk/resetprop /sbin/resetprop; do
        [ ! -L "$c" ] && [ -x "$c" ] && { rp_bin=$c; break; }
    done
    # Validate every record's syntax and metadata before considering early exit or applying writes.
    has_persist=0
    while IFS= read -r raw || [ -n "$raw" ]; do
        case "$raw" in
            *'|'*'|'*) ;;
            *) echo "Guest runtime: malformed module property record" >&2; return 1 ;;
        esac
        key=${raw%%|*}
        rest=${raw#*|}
        present=${rest%%|*}
        case "$key" in ''|*[!A-Za-z0-9._-]*) echo "Guest runtime: invalid property key '$key'" >&2; return 1 ;; esac
        case "$present" in 0|1|0e|1e) ;; *) echo "Guest runtime: invalid present metadata '$present' for $key" >&2; return 1 ;; esac
        case "$key" in persist.*) has_persist=1 ;; esac
    done < "$snapshot"
    if [ "$mode" = persist-only ] && [ "$has_persist" = 0 ]; then
        return 0
    fi
    [ -n "$rp_bin" ] || { echo "Guest runtime: resetprop missing for module property restore" >&2; return 1; }
    # resetprop can exit 0 and leave the property as it was (a full property area), and
    # getprop <key> prints an empty string both when <key> is absent and when <key> is
    # present with an empty value (A8). Verify both presence ([key]: in full getprop)
    # and value before removing the snapshot.
    bad=0
    while IFS= read -r raw || [ -n "$raw" ]; do
        key=${raw%%|*}
        rest=${raw#*|}
        present=${rest%%|*}
        val_raw=${rest#*|}
        if [ "$mode" = persist-only ]; then
            case "$key" in persist.*) ;; *) continue ;; esac
        fi
        case "$present" in
            *e) value=$(decode_prop_val "$val_raw"); is_pres=${present%e} ;;
            *)  value=$val_raw; is_pres=$present ;;
        esac
        if [ "$is_pres" = 1 ]; then
            if [ "$mode" = persist-only ]; then
                "$rp_bin" -p "$key" "$value"
            else
                "$rp_bin" -n "$key" "$value"
            fi
        else
            "$rp_bin" --delete "$key"
        fi
    done < "$snapshot"
    all_after=$(getprop)
    while IFS= read -r raw || [ -n "$raw" ]; do
        key=${raw%%|*}
        rest=${raw#*|}
        present=${rest%%|*}
        val_raw=${rest#*|}
        if [ "$mode" = persist-only ]; then
            case "$key" in persist.*) ;; *) continue ;; esac
        fi
        case "$present" in
            *e) value=$(decode_prop_val "$val_raw"); is_pres=${present%e} ;;
            *)  value=$val_raw; is_pres=$present ;;
        esac
        now_present=0
        case "$all_after" in *"[$key]: ["*) now_present=1 ;; esac
        if [ "$is_pres" = 1 ]; then
            now_val=$(getprop "$key")
            if [ "$now_present" != 1 ] || [ "$now_val" != "$value" ]; then
                echo "Guest runtime: $key was not restored to its earlier state" >&2
                bad=$((bad + 1))
            fi
        else
            if [ "$now_present" != 0 ]; then
                echo "Guest runtime: $key was not removed" >&2
                bad=$((bad + 1))
            fi
        fi
    done < "$snapshot"
    [ "$bad" = 0 ] || { echo "Guest runtime: $bad module properties not restored; keeping $snapshot" >&2; return 1; }
    if [ "$mode" != persist-only ]; then
        check_prop_snapshot_dirs "$rp_dir" || return 1
        [ ! -L "$snapshot" ] && [ ! -L "$boot_stamp" ] || return 1
        rm -f "$snapshot" "$boot_stamp"
    fi
}

case "${1:-}" in
control)
    A=/data/adb
    id=${3:-}
    valid_id() { case "$1" in ''|*[!A-Za-z0-9._-]*|.*) return 1 ;; esac; }
    safe_dirs() {
        for d in "$A" "$A/oneos" "$A/modules" "$A/modules_update"; do
            [ -d "$d" ] && [ ! -L "$d" ] || exit 1
        done
    }
    safe_dirs
    case "${2:-}" in
    status|list)
        echo '@@RESULT root active'
        selected=$(cat "$A/oneos/framework")
        valid_id "$selected" || exit 1
        provider="$A/modules/$selected"
        kind=none
        [ ! -f "$provider/.oneos-framework" ] || kind=$(cat "$provider/.oneos-framework")
        active=0; zp=$(cat "$A/oneos/run/injection.pid" 2>/dev/null || true)
        case "$zp" in ''|*[!0-9]*) zp=0 ;; esac
        if [ "$zp" -gt 1 ] && kill -0 "$zp" 2>/dev/null &&
           [ "$(stat -Lc %d:%i /proc/$zp/root)" = "$(stat -Lc %d:%i /)" ]; then
            case "$kind" in vector) candidates=$(pidof vectord || true) ;; lsposed) candidates=$(pidof lspd || true) ;; *) candidates= ;; esac
            for pid in $candidates; do
                [ "$(stat -Lc %d:%i /proc/$pid/root 2>/dev/null)" != "$(stat -Lc %d:%i /)" ] || active=1
            done
        fi
        echo "@@RESULT framework-status $selected $kind $active"
        for base in modules modules_update; do
            for m in "$A/$base/"*; do
                [ -d "$m" ] && [ ! -L "$m" ] || continue
                enabled=1; [ ! -f "$m/disable" ] || enabled=0
                kind=module
                if [ -f "$m/.oneos-framework" ]; then
                    { IFS= read -r kind < "$m/.oneos-framework"; } 2>/dev/null || true
                fi
                set -- $(LC_ALL=C awk '
                BEGIN {
                    for (i = 1; i < 256; i++) ord[sprintf("%c", i)] = i
                    lim["version"] = 128; lim["name"] = 256; lim["description"] = 1024
                    lim["author"] = 256; lim["versionCode"] = 32; lim["updateJson"] = 1024
                }
                {
                    sub(/\r$/, "")
                    eq = index($0, "=")
                    if (eq > 1) {
                        k = substr($0, 1, eq - 1)
                        if ((k in lim) && !(k in seen)) {
                            seen[k] = 1
                            v = substr($0, eq + 1, lim[k])
                            h = ""; n = length(v)
                            for (i = 1; i <= n; i++) h = h sprintf("%02x", ord[substr(v, i, 1)])
                            val[k] = (h != "" ? h : "-")
                        }
                    }
                }
                END {
                    printf "%s %s %s %s %s %s\n",
                        (val["version"] ? val["version"] : "-"),
                        (val["name"] ? val["name"] : "-"),
                        (val["description"] ? val["description"] : "-"),
                        (val["author"] ? val["author"] : "-"),
                        (val["versionCode"] ? val["versionCode"] : "-"),
                        (val["updateJson"] ? val["updateJson"] : "-")
                }' "$m/module.prop" 2>/dev/null)
                verhex=${1:--}; namehex=${2:--}; deschex=${3:--}
                authorhex=${4:--}; codehex=${5:--}; updatehex=${6:--}
                caps=-
                [ ! -f "$m/action.sh" ] || caps=action
                [ ! -f "$m/webroot/index.html" ] || caps="$caps,webui"
                removed=0; [ ! -f "$m/remove" ] || removed=1
                echo "@@RESULT module-info ${m##*/} $enabled $base $kind $verhex $namehex $deschex $authorhex $codehex $updatehex $caps $removed"
            done
        done
        ;;
    asset)
        valid_id "$id" || exit 1
        rel=${4:-}
        case "$rel" in ''|/*|*..*|*\\*) exit 1 ;; esac
        root="$A/modules/$id/webroot"
        [ -d "$root" ] && [ ! -L "$root" ] || exit 1
        root=$(realpath "$root") || exit 1
        file=$(realpath "$root/$rel") || exit 1
        case "$file" in "$root"/*) ;; *) exit 1 ;; esac
        [ -f "$file" ] && [ "$(stat -c %s "$file")" -le 16777216 ] || exit 1
        exec cat "$file"
        ;;
    restart)
        # Only the host watcher restarts namespaces; never ask shared init to reboot.
        setprop oneos.guest.powerctl ksud:soft-reboot
        ;;
    remove|restore)
        valid_id "$id" || exit 1
        [ "$id" != "$(cat "$A/oneos/framework")" ] || { echo 'Select another framework before removing this one' >&2; exit 1; }
        found=0
        for m in "$A/modules/$id" "$A/modules_update/$id"; do
            [ -d "$m" ] && [ ! -L "$m" ] || continue
            [ ! -L "$m/remove" ] || exit 1
            if [ "$2" = remove ]; then : > "$m/remove"; else rm -f "$m/remove"; fi
            found=1
        done
        [ "$found" = 1 ] || exit 1
        ;;
    action)
        valid_id "$id" || exit 1
        m="$A/modules/$id"
        [ -d "$m" ] && [ ! -L "$m" ] && [ ! -f "$m/remove" ] && [ -f "$m/action.sh" ] && [ ! -L "$m/action.sh" ] || exit 1
        cd "$m"
        export MODPATH="$m" ASH_STANDALONE=1 PATH=/system/bin:/system/xbin:/data/adb/ksu/bin
        exec /data/oneos_bin/busybox timeout 120 /data/oneos_bin/busybox sh "$m/action.sh"
        ;;
    toggle)
        valid_id "$id" || exit 1
        case "${4:-}" in on|off) ;; *) exit 1 ;; esac
        found=0
        for m in "$A/modules/$id" "$A/modules_update/$id"; do
            [ -d "$m" ] && [ ! -L "$m" ] || continue
            [ ! -L "$m/disable" ] || exit 1
            if [ -f "$m/.oneos-framework" ] && [ "$4" = on ]; then
                [ "$id" = "$(cat "$A/oneos/framework")" ] || { echo 'Select this framework on the pre-boot page first' >&2; exit 1; }
            fi
            if [ "$4" = on ]; then rm -f "$m/disable"; else : > "$m/disable"; fi
            found=1
        done
        [ "$found" = 1 ] || exit 1
        ;;
    import)
        valid_id "$id" || exit 1
        case "$id" in oneos|zygisksu) exit 1 ;; esac
        file=${4:-}
        case "$file" in /data/user/0/com.fahadali.oneos.guest/cache/guest-module-*.bundle) ;; *) exit 1 ;; esac
        [ -f "$file" ] && [ ! -L "$file" ] || exit 1
        target="$A/modules_update/$id"
        [ ! -e "$target" ] && [ ! -L "$target" ] || { echo 'An update for this module is already staged' >&2; exit 1; }
        temp="$A/modules_update/.oneos-import-$$"
        mkdir "$temp"
        trap 'rm -rf "$temp"' EXIT
        "$A/oneos/bin/su" --unpack "$temp" < "$file" || exit 1
        [ "$(sed -n 's/^id=//p' "$temp/module.prop" | tr -d '\r')" = "$id" ] || exit 1
        mv "$temp" "$target"
        trap - EXIT
        ;;
    *) exit 2 ;;
    esac
    ;;
baseline)
    [ -f "$STATE/enabled" ] || exit 0
    B=$S/tmp/guest_runtime_baseline
    mkdir -p "$B"
    rm -f "$B/ready"
    # Keep the host's persisted list intact even if an older kernel prunes its
    # live table after seeing a guest package event. Keep this read-only bind
    # until reboot on an audit failure; remove it only after a matching audit.
    if [ -f /data/adb/ksu/.allowlist ] && [ ! -L /data/adb/ksu/.allowlist ]; then
        cp /data/adb/ksu/.allowlist "$B/allowlist.saved"
        busy=''
        for x in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do [ ! -x "$x" ] || { busy=$x; break; }; done
        [ -n "$busy" ] || exit 1
        "$busy" mount --bind "$B/allowlist.saved" /data/adb/ksu/.allowlist
        # BusyBox requires both operands for a file bind remount; the host
        # namespace's /proc/mounts need not list the bind we just created.
        if ! "$busy" mount -o remount,bind,ro "$B/allowlist.saved" /data/adb/ksu/.allowlist; then
            "$busy" umount /data/adb/ksu/.allowlist
            exit 1
        fi
        : > "$B/allowlist.protected"
    fi
    : > "$B/host-frameworks"
    for proc in /proc/[0-9]*; do
        # Pre-filter via zero-fork /proc/<pid>/comm read before spawning tr on cmdline.
        # Scanning ~1,500 PIDs via subshell+tr while One UI was live previously stalled
        # preflight for ~16s; comm matches both native daemons (vectord/lspd) and
        # app_process-hosted frameworks (main/app_process64/org.lsposed.lspd/org.matrix.*).
        comm=''
        { IFS= read -r comm < "$proc/comm"; } 2>/dev/null || continue
        case "$comm" in
            vectord*|lspd*|org.lsposed*|org.matrix*|main*|app_process*) ;;
            *) continue ;;
        esac
        name=$({ tr '\000' ' ' < "$proc/cmdline"; } 2>/dev/null || true)
        case "$name" in vectord|vectord' '*|lspd|lspd' '*|org.lsposed.lspd|org.lsposed.lspd' '*|org.matrix.vector.daemon|org.matrix.vector.daemon' '*)
            pid=${proc##*/}
            start=$(awk '{print $22}' "$proc/stat")
            kill -STOP "$pid" || exit 1
            echo "$pid $start" >> "$B/host-frameworks"
            ;;
        esac
    done
    "$S/guest_root" --host-audit > "$B/root.unsorted" || exit 0
    sort "$B/root.unsorted" > "$B/root"
    sha256sum /sys/fs/selinux/policy > "$B/selinux" || exit 0
    awk '{print $1, $2, $6}' /proc/modules | sort > "$B/kernel-modules"
    if [ -f /data/adb/ksu/.allowlist ]; then sha256sum /data/adb/ksu/.allowlist > "$B/allowlist"; fi
    cat /proc/sys/kernel/random/boot_id > "$B/boot"
    : > "$B/ready"
    ;;
verify-return)
    [ -f "$STATE/enabled" ] || exit 0
    B=$S/tmp/guest_runtime_baseline
    [ -f "$B/ready" ] || { echo 'Guest runtime: no supported live root audit; reboot fallback required'; exit 1; }
    [ "$(cat "$B/boot")" = "$(cat /proc/sys/kernel/random/boot_id)" ] || exit 1
    "$S/guest_root" --host-audit > "$B/return.unsorted" || exit 1
    sort "$B/return.unsorted" > "$B/return.root"
    cmp -s "$B/root" "$B/return.root" || {
        echo "Guest runtime: host root grants or manager changed (baseline=$(wc -l < "$B/root" | tr -d ' ') return=$(wc -l < "$B/return.root" | tr -d ' ') entries; clean reboot will reload protected allowlist)"
        exit 1
    }
    sha256sum -c "$B/selinux" || { echo 'Guest runtime: shared SELinux policy changed'; exit 1; }
    [ ! -f "$B/allowlist" ] || sha256sum -c "$B/allowlist" || exit 1
    awk '{print $1, $2, $6}' /proc/modules | sort > "$B/return.kernel-modules"
    cmp -s "$B/kernel-modules" "$B/return.kernel-modules" || { echo 'Guest runtime: loaded kernel modules changed'; exit 1; }
    if [ -f "$B/host-frameworks" ]; then
        while read -r pid start; do
            [ -r "/proc/$pid/stat" ] || continue
            [ "$(awk '{print $22}' "/proc/$pid/stat")" = "$start" ] || { echo 'Host framework process identity changed'; exit 1; }
            kill -CONT "$pid" || exit 1
        done < "$B/host-frameworks"
    fi
    if [ -f "$B/allowlist.protected" ]; then
        umount /data/adb/ksu/.allowlist || exit 1
        rm -f "$B/allowlist.protected"
    fi
    echo 'Guest runtime: live host grants, manager, SELinux policy and kernel modules match baseline'
    ;;
prepare)
    [ -f "$STATE/enabled" ] || exit 0
    [ "$(getprop ro.product.cpu.abi)" = arm64-v8a ] || { echo 'Guest runtime: no native backend for this ABI'; exit 1; }
    B=''
    for b in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do [ ! -x "$b" ] || { B=$b; break; }; done
    [ -n "$B" ] || exit 1
    # Existing prepare validates all staged data ancestors against symlinks.
    [ ! -L "$S/data" ] || { echo "Guest runtime: symlink $S/data"; exit 1; }
    for p in "$STATE" "$STATE/adb" "$STATE/adb/oneos" "$STATE/adb/modules" "$STATE/adb/modules_update"; do
        [ ! -L "$p" ] || { echo "Guest runtime: symlink $p"; exit 1; }
        mkdir -p "$p"
    done
    A=$STATE/adb
    for p in "$A/oneos/bin" "$A/oneos/run" "$A/oneos/run/zygisk" "$A/oneos/run/zygisk/lib64" "$A/ksu" "$A/ksu/bin" "$A/service.d" "$A/post-fs-data.d" "$A/oneos/framework-state" "$A/lspd"; do
        [ ! -L "$p" ] || { echo "Guest runtime: symlink $p"; exit 1; }
        mkdir -p "$p"
    done
    rm -f "$A/oneos/bin/su" "$A/oneos/bin/zygiskd" "$A/oneos/bin/zygisk-ptrace64" "$A/oneos/run/zygisk/lib64/libzygisk.so" "$A/ksu/bin/busybox" "$A/ksu/bin/resetprop" "$A/oneos/run/session.log" "$A/oneos/framework" "$S/data/oneos_runtime.sh"
    cp "$S/guest_root" "$A/oneos/bin/su"
    cp "$S/guest_zygiskd" "$A/oneos/bin/zygiskd"
    cp "$S/guest_zygisk_ptrace" "$A/oneos/bin/zygisk-ptrace64"
    cp "$S/guest_zygisk.so" "$A/oneos/run/zygisk/lib64/libzygisk.so"
    cp "$B" "$A/ksu/bin/busybox"
    for rp in /data/adb/ksu/bin/resetprop /data/adb/ap/bin/resetprop /debug_ramdisk/resetprop /sbin/resetprop; do
        if [ -x "$rp" ]; then cp "$rp" "$A/ksu/bin/resetprop"; chmod 755 "$A/ksu/bin/resetprop"; break; fi
    done
    chmod 755 "$A/oneos/bin/"* "$A/ksu/bin/busybox"
    chmod 711 "$A" "$A/oneos"
    chmod 755 "$A/oneos/run" "$A/oneos/run/zygisk"
    : > "$A/oneos/run/session.log"
    rm -f "$A/oneos/run/injection.pid" "$A/oneos/run/su.sock" "$A/oneos/run/zygisk/cp64.sock"
    if [ -f "$A/oneos/run/module-props" ]; then
        cur_boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
        snap_boot=$(cat "$A/oneos/run/module-props.boot" 2>/dev/null || true)
        if [ -n "$cur_boot" ] && [ -n "$snap_boot" ] && [ "$cur_boot" != "$snap_boot" ]; then
            # A full reboot reset the in-memory property arena; only persist.* keys could survive.
            restore_module_props "$A" persist-only || exit 1
            rm -f "$A/oneos/run/module-props" "$A/oneos/run/module-props.boot"
        else
            # Same kernel boot (or unknown boot_id): restore un-cleaned module properties before new prepare.
            restore_module_props "$A" all || exit 1
        fi
    else
        rm -f "$A/oneos/run/module-props.boot"
    fi
    if [ ! -d "$A/modules/zygisk_vector" ] && [ ! -d "$A/modules_update/zygisk_vector" ]; then
        [ ! -L "$A/modules_update/zygisk_vector" ] || { echo "Guest runtime: symlink $A/modules_update/zygisk_vector"; exit 1; }
        mkdir "$A/modules_update/zygisk_vector"
        "$S/guest_root" --unpack "$A/modules_update/zygisk_vector" < "$S/guest_vector.bundle" || exit 1
    fi
    selected=zygisk_vector
    [ ! -f "$STATE/framework" ] || [ -L "$STATE/framework" ] || selected=$(cat "$STATE/framework")
    case "$selected" in ''|*[!A-Za-z0-9._-]*|.*) exit 1 ;; esac
    for base in modules modules_update; do
        for provider in "$A/$base/"*; do
            [ -d "$provider" ] && [ ! -L "$provider" ] && [ -f "$provider/.oneos-framework" ] || continue
            # Preserve the selected provider's explicit switch; alternatives stay disabled.
            if [ "${provider##*/}" != "$selected" ]; then
                rm -f "$provider/disable"
                touch "$provider/disable"
            fi
        done
    done
    for p in "$A/oneos/framework-state/$selected" "$A/oneos/framework-state/$selected/lspd"; do
        [ ! -L "$p" ] || { echo "Guest runtime: symlink $p"; exit 1; }
        mkdir -p "$p"
    done
    rm -f "$A/oneos/framework" "$S/data/oneos_runtime.sh"
    printf '%s\n' "$selected" > "$A/oneos/framework"
    cp "$S/guest_runtime.sh" "$S/data/oneos_runtime.sh"
    # Bind the complete private root state, including framework and service.d.
    "$B" mount --make-private "$R/data/adb"
    "$B" mount --bind "$A" "$R/data/adb"
    "$B" mount --make-rprivate "$R/data/adb"
    "$B" mount --bind "$A/oneos/framework-state/$selected/lspd" "$R/data/adb/lspd"
    "$B" mount --make-private "$R/data/adb/lspd"
    # /system/xbin/su is the copied passthrough binary; replace only the guest target.
    [ -f "$R/system/xbin/su" ] || { echo 'Guest runtime: guest su target missing'; exit 1; }
    "$B" mount --bind "$S/guest_root" "$R/system/xbin/su"
    "$B" mount --make-private "$R/system/xbin/su"
    # Modules can expect either spelling. Never expose the host provider's su.
    if [ -f "$R/system/bin/su" ]; then
        "$B" mount --bind "$S/guest_root" "$R/system/bin/su"
        "$B" mount --make-private "$R/system/bin/su"
    fi
    # Literal /data/adb paths used by zygiskd and module scripts must resolve
    # to this guest's private tree, including any nested modules mount.
    verify_private_adb_bind || { echo 'Guest runtime: private adb bind identity mismatch' >&2; exit 1; }
    echo 'Guest runtime: separate permissions, module state and explicit guest injection enabled'
    ;;
restore-props)
    restore_module_props /data/adb "${2:-all}"
    ;;
restore-props-host)
    for _hp in "$S/data" "$STATE" "$STATE/adb" "$STATE/adb/oneos" "$STATE/adb/oneos/run" "$STATE/adb/ksu" "$STATE/adb/ksu/bin"; do
        if [ -L "$_hp" ]; then
            echo "Guest runtime: symlink $_hp" >&2
            exit 1
        fi
    done
    [ -d "$STATE/adb" ] || exit 0
    restore_module_props "$STATE/adb" "${2:-all}"
    ;;
uninstall)
    A=/data/adb
    # Installation/cleanup may invoke su before the normal guest daemon starts.
    "$A/oneos/bin/su" --daemon >> "$A/oneos/run/session.log" 2>&1 &
    bootstrap=$!
    # Ended by SIGKILL, never SIGTERM: the guest's shell (mksh) catches SIGTERM itself, and one
    # that reaches the forked child before it has become su is swallowed there; su then never
    # gets it and the wait never returns. With no module to remove this trap runs microseconds
    # after the fork. Galaxy S22 Ultra 0.4.91, OxygenOS 16, 2026-11-11: three switches in a row
    # stood behind the splash for good right after "explicit guest injection enabled" (15 and
    # 27 minutes, forced reboots); the same lines on the Fold's own shell lost 4 of 150 kills.
    # su --daemon has no handler: SIGTERM ended it the same way. The pid file is for the host
    # side (guest_modules.sh), which stops a bootstrap left by a cleanup it had to end.
    echo "$bootstrap" > "$A/oneos/run/bootstrap.pid" || true
    trap 'kill -KILL "$bootstrap" 2>/dev/null || true; wait "$bootstrap" 2>/dev/null || true; rm -f "$A/oneos/run/bootstrap.pid" 2>/dev/null || true' EXIT
    export ASH_STANDALONE=1 PATH=/system/bin:/system/xbin:/data/adb/ksu/bin
    for m in "$A/modules/"*; do
        [ -d "$m" ] && [ ! -L "$m" ] && [ -f "$m/remove" ] || continue
        id=${m##*/}
        # A framework's uninstall.sh (Vector's and LSPosed's: "rm -rf /data/adb/lspd") is not
        # run: /data/adb/lspd is the selected framework's state bound there by prepare, so it
        # emptied that one and then failed on the mount point, and with "|| exit 1" every
        # switch ended at prepare while the remove mark stayed (A36 0.4.73 d2de5297, HyperOS:
        # code 3 right after "explicit guest injection enabled" at 15:53, 16:11 and 16:57,
        # booted only with guest root off; "I can't remove any module (lsposed/hma oss/vector)").
        # Its own state is framework-state/<id>, never bound while another one is selected.
        if [ -f "$m/.oneos-framework" ]; then
            if [ "$id" != "$(cat "$A/oneos/framework" 2>/dev/null)" ] && [ ! -L "$A/oneos/framework-state/$id" ]; then
                rm -rf "$A/oneos/framework-state/$id"
                echo "Guest runtime: $id's framework state removed with it (its uninstall.sh not run)"
            fi
            continue
        fi
        # As KernelSU and Magisk: a failing uninstall.sh is logged and the module still goes.
        if [ -f "$m/uninstall.sh" ] && [ ! -L "$m/uninstall.sh" ]; then
            ( cd "$m"; export MODPATH="$m"; timeout 30 /data/oneos_bin/busybox sh "$m/uninstall.sh" ) >> "$A/oneos/run/session.log" 2>&1 ||
                echo "Guest runtime: $id's uninstall.sh failed (exit $?); removed anyway"
        fi
    done
    ;;
install)
    A=/data/adb
    /data/adb/oneos/bin/su --daemon >> "$A/oneos/run/session.log" 2>&1 &
    bootstrap=$!
    # SIGKILL, as in the uninstall stage above: a SIGTERM can be lost in the forked shell.
    trap 'kill -KILL "$bootstrap" 2>/dev/null || true; wait "$bootstrap" 2>/dev/null || true' EXIT
    A=/data/adb
    export PATH=/system/bin:/system/xbin:/data/adb/ksu/bin
    export ASH_STANDALONE=1
    B=/data/oneos_bin/busybox
    for m in "$A/modules"/*; do
        [ -d "$m" ] && [ ! -f "$m/disable" ] && [ ! -f "$m/remove" ] || continue
        # Installation occurs inside the guest, never as a host-side installer.
        # ZYGISK_ENABLED=1 as Magisk sets it for installers: this runtime's Zygisk always runs
        # with it (HMA-OSS and other Zygisk modules refuse to install without a Zygisk framework).
        if [ -f "$m/.oneos-pending" ]; then
            MODPATH=$m ZIPFILE=$m/.oneos-install.zip BOOTMODE=true KSU=true KSU_VER_CODE=99999 MAGISK_VER_CODE=0 ZYGISK_ENABLED=1 ARCH=arm64 IS64BIT=true API=$(getprop ro.build.version.sdk) \
                "$B" sh /data/oneos_runtime.sh customize "$m" >> "$A/oneos/run/session.log" 2>&1 || {
                    # As a module that asks for shared SELinux changes (pre, below): disabled with
                    # the reason for the modules screen, and the system starts without it. It used
                    # to end the whole switch (S26 Ultra 00655d58, 0.4.74: one module's installer
                    # failed, "Guest module installation failed (exit 1); returning to One UI").
                    # .oneos-pending stays, so turning the module on again runs its installer again.
                    rc=$?
                    touch "$m/disable"
                    echo "Its installer failed (exit $rc); turn it on again to retry, or remove it" > "$m/.oneos-disabled-reason"
                    echo "Guest runtime: installation failed for ${m##*/} (exit $rc); disabled, the system starts without it"
                    continue
                }
            rm -f "$m/.oneos-pending"
            # The host helper reapplies generated mount payloads before zygote.
        fi
    done
    ;;
pre)
    A=/data/adb
    rm -f "$A/oneos/run/injection.pid" "$A/oneos/run/zygisk/cp64.sock"
    "$A/oneos/bin/su" --daemon >> "$A/oneos/run/session.log" 2>&1 &
    n=0
    while [ ! -S "$A/oneos/run/su.sock" ]; do n=$((n+1)); [ "$n" -lt 50 ] || exit 1; sleep 0.1; done
    export PATH=/system/bin:/system/xbin:/data/adb/ksu/bin
    export ASH_STANDALONE=1
    B=/data/oneos_bin/busybox
    for m in "$A/modules"/*; do
        [ -d "$m" ] && [ ! -f "$m/disable" ] && [ ! -f "$m/remove" ] || continue
        # Shared kernel policy/boot changes cannot be advertised as guest-local: such a module is
        # disabled (with the reason, for the modules screen) and the guest boots without it, rather
        # than the whole session going back to One UI (owner's test: Oh My Keymint's sepolicy.rule).
        if [ -s "$m/sepolicy.rule" ] && [ ! -f "$m/.oneos-framework" ]; then
            echo "Guest runtime: ${m##*/} requests shared SELinux changes; disabled, not loaded"
            : > "$m/disable"
            echo "Needs SELinux policy changes, which would also change One UI's policy (one kernel)" > "$m/.oneos-disabled-reason"
            continue
        fi
        if [ -f "$m/system.prop" ]; then
            # OneOS snapshots/restores module properties on soft reboot, in-place return, and reboot return.
            snapshot="$A/oneos/run/module-props"
            boot_stamp="$A/oneos/run/module-props.boot"
            for _mpd in "$A" "$A/oneos" "$A/oneos/run"; do
                [ ! -L "$_mpd" ] || { echo "Guest runtime: symlink $_mpd" >&2; exit 1; }
            done
            [ ! -L "$snapshot" ] && [ ! -L "$boot_stamp" ] || { echo "Guest runtime: symlink module-props state" >&2; exit 1; }
            if [ ! -f "$boot_stamp" ]; then
                cat /proc/sys/kernel/random/boot_id > "$boot_stamp" 2>/dev/null || true
            fi
            if [ -z "${MOD_ALL_PROPS_SET:-}" ]; then
                MOD_ALL_PROPS=$(getprop)
                MOD_ALL_PROPS_SET=1
                MOD_SEEN_KEYS="|"
                if [ -f "$snapshot" ]; then
                    while IFS='|' read -r sk _rest; do
                        [ -n "$sk" ] && MOD_SEEN_KEYS="${MOD_SEEN_KEYS}${sk}|"
                    done < "$snapshot"
                fi
            fi
            while IFS= read -r line || [ -n "$line" ]; do
                case "$line" in ''|'#'*) continue ;; *=*)
                    key=${line%%=*}
                    case "$key" in ''|*[!A-Za-z0-9._-]*) exit 1 ;; esac
                    [ -x "$A/ksu/bin/resetprop" ] || { echo 'Module properties require reversible resetprop support'; exit 1; }
                    case "$MOD_SEEN_KEYS" in *"|$key|"*) ;; *)
                        MOD_SEEN_KEYS="${MOD_SEEN_KEYS}${key}|"
                        present=0
                        case "$MOD_ALL_PROPS" in *"[$key]: ["*) present=1 ;; esac
                        raw_val=$(getprop "$key")
                        case "$raw_val" in
                            *\\*|*'
'*|*$(printf '\r')*)
                                enc_val=$(printf '%s' "$raw_val" | sed -e 's/\\/\\\\/g' -e 's/\r/\\r/g' | awk 'NR > 1 { printf "\\n" } { printf "%s", $0 }')
                                printf '%s|%se|%s\n' "$key" "$present" "$enc_val" >> "$snapshot" ;;
                            *)
                                printf '%s|%s|%s\n' "$key" "$present" "$raw_val" >> "$snapshot" ;;
                        esac
                    ;; esac
                    "$A/ksu/bin/resetprop" -n "$key" "${line#*=}"
                    ;; *) exit 1 ;; esac
            done < "$m/system.prop"
        fi
        [ ! -f "$m/.oneos-framework" ] || continue
        for hook in post-fs-data.sh post-mount.sh; do
            if [ -f "$m/$hook" ]; then
                timeout 30 "$B" sh "$m/$hook" >> "$A/oneos/run/session.log" 2>&1
                # A hook may have created or changed properties: the cached listing is stale for the next module's
                # system.prop, which would take such a property for one that never existed (and delete it on restore
                # instead of putting the earlier value back). Read again, when one is next needed.
                MOD_ALL_PROPS_SET=
            fi
        done
    done
    selected=$(cat "$A/oneos/framework")
    framework="$A/modules/$selected"
    [ -f "$framework/.oneos-framework" ] || { echo 'Guest runtime: selected framework is missing'; exit 1; }
    if [ ! -f "$framework/disable" ]; then
        [ -f "$framework/zygisk/arm64-v8a.so" ] || { echo 'Guest runtime: selected framework layout is not supported'; exit 1; }
        ( cd "$framework"; exec "$A/oneos/bin/su" --guard-exec /system/bin/sh "$framework/daemon" ) >> "$A/oneos/run/session.log" 2>&1 &
    fi
    "$A/oneos/bin/zygiskd" --workdir "$A/oneos/run/zygisk" >> "$A/oneos/run/session.log" 2>&1 &
    n=0
    while [ ! -S "$A/oneos/run/zygisk/cp64.sock" ]; do
        n=$((n+1)); [ "$n" -lt 100 ] || { echo 'Guest runtime: injector daemon did not start'; exit 1; }; sleep 0.1
    done
    ;;
customize)
    m=$2
    TMPDIR="$m/.oneos-tmp"; export TMPDIR; mkdir -p "$TMPDIR"; cp "$m/module.prop" "$TMPDIR/module.prop"
    grep_prop() { sed -n "s/^$1=//p" "${2:-/system/build.prop}" | head -n 1; }
    ui_print() { echo "$*"; }
    abort() { echo "$*" >&2; exit 1; }
    # Standard Magisk/KernelSU order: target, uid, gid, mode(s), optional context.
    set_perm() {
        chown "$2:$3" "$1" || return 1
        chmod "$4" "$1" || return 1
        chcon "${5:-u:object_r:system_file:s0}" "$1" || return 1
    }
    set_perm_recursive() {
        chown -hR "$2:$3" "$1" || return 1
        find "$1" -type d -exec chmod "$4" '{}' \; || return 1
        find "$1" -type f -exec chmod "$5" '{}' \; || return 1
        chcon -hR "${6:-u:object_r:system_file:s0}" "$1" || return 1
    }
    # The guest package service is absent before zygote. Fail promptly so a
    # module can use its own boot-time retry instead of waiting for minutes.
    pm() {
        /system/bin/service check package 2>/dev/null | grep -q ': found' || { echo 'Guest package manager is not ready; defer this operation to service.sh' >&2; return 1; }
        /data/oneos_bin/busybox timeout 10 /system/bin/pm "$@"
    }
    extract() { unzip -o "$1" "$2" -d "$3"; }
    # Root-manager customization scripts do not inherit this lifecycle shell's
    # errexit/nounset flags. Explicit abort and a failing final command still fail.
    set +eu
    [ ! -f "$m/customize.sh" ] || . "$m/customize.sh"
    ;;
late)
    A=/data/adb
    # The same PATH as the pre stage, so service.sh / boot-completed.sh find resetprop
    # and the other root-manager tools (owner's F8U: Play Integrity Fix's service.sh
    # "resetprop: not found" on every call).
    export PATH=/system/bin:/system/xbin:/data/adb/ksu/bin
    # Wait for this guest's package/activity services before starting app_process
    # based framework daemons; they must not bind to stale host services.
    n=0
    while [ "$(getprop sys.boot_completed)" != 1 ]; do n=$((n+1)); [ "$n" -lt 300 ] || exit 1; sleep 1; done
    if [ -f /data/system/oneos_guest_lock ]; then
        n=0
        while [ "$(/system/bin/locksettings get-disabled --user 0 2>/dev/null)" != false ]; do
            /system/bin/locksettings set-disabled false --user 0 >/dev/null 2>&1
            n=$((n+1))
            [ "$n" -lt 10 ] || break
            sleep 1
        done
        if [ "$(/system/bin/locksettings get-disabled --user 0 2>/dev/null)" = false ]; then
            echo 'Guest runtime: guest keyguard enabled'
        else
            echo 'Guest runtime: could not enable the guest keyguard'
        fi
    fi
    /data/adb/oneos/bin/su --oneos-info || { echo 'Guest runtime: root broker identity check failed'; exit 1; }
    # guest_start.sh clears the shared boot flag before launching zygote;
    # only the new framework's completed boot releases this gate. Also require
    # this guest's integrated manager to have been discovered by package scan.
    n=0
    until pm path com.fahadali.oneos.guest 2>/dev/null | grep '^package:'; do
        n=$((n+1)); [ "$n" -lt 300 ] || {
            echo 'Guest runtime: Guest root manager was not discovered by the guest package manager'; exit 1;
        }
        sleep 1
    done
    # Each script's start in the shared log as well (session.log stays in the guest's /data and is
    # not in a report): S24 FE 0.4.73 b94e54fe, Pixel, after a module install and a soft reboot,
    # SystemUI got signal 15 2 s after boot_completed (boot.log 17:50:24.829), a display transition
    # snapshot then covered the screen and every touch was dropped as untrusted (17:50:29); only
    # pixel_5g_toggle_fix's service.sh ran then, and nothing named it.
    started() { /system/bin/log -t OneOSGuestRoot "$1 started" 2>/dev/null || true; }
    for m in "$A/modules"/*; do
        [ -d "$m" ] && [ ! -f "$m/disable" ] && [ ! -f "$m/remove" ] || continue
        if [ -f "$m/.oneos-framework" ]; then
            # Prevent its dex2oat helper's /proc/1/ns/mnt setns from reaching One UI.
            : # framework started before guest zygote
        elif [ -f "$m/service.sh" ]; then
            started "${m##*/}/service.sh"
            /data/oneos_bin/busybox sh "$m/service.sh" >> "$A/oneos/run/session.log" 2>&1 &
        fi
        [ ! -f "$m/boot-completed.sh" ] || { started "${m##*/}/boot-completed.sh"; /data/oneos_bin/busybox sh "$m/boot-completed.sh" >> "$A/oneos/run/session.log" 2>&1 & }
    done
    for script in "$A/service.d/"*.sh; do
        [ ! -f "$script" ] || { started "service.d/${script##*/}"; /data/oneos_bin/busybox sh "$script" >> "$A/oneos/run/session.log" 2>&1 & }
    done
    ;;
*) echo 'guest_runtime.sh: prepare | pre | customize | late' >&2; exit 2 ;;
esac
