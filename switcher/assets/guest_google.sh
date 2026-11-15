#!/system/bin/sh
# Google Play services and the Play Store in a ColorOS guest, on every phone.
# Pull request #1 added this for the Z Fold8 with the Find N6; since 0.4.16 it
# covers every ColorOS set. ColorOS 17 keeps Play services in a gmssystem APEX
# the phone's apexd never activates: my_product/priv-app/GmsCore holds only its
# modules (m), in the CN and IN builds alike. The guest got One UI's own APEX
# copy instead, without its privileged permissions (Galaxy S25, X9U IN: "You
# either need MANAGE_USERS or CREATE_USERS permission", and the Play Store
# found no gservices provider). The CN builds' Play Store is a 2022 stub
# (GooglePlayServicesUpdater).
#
# snapshot (oneos_switch.sh, while One UI's package manager still runs): copy
# One UI's own Play services and Play Store APK files, never their data, into
# $STAGE/google_apps, with the arm64 libraries from the same APKs. An unchanged
# copy is kept.
# prepare (guest_prepare.sh): check that copy and bind it read-only over
# priv-app/GmsCore in my_product, or else in the guest's product folder (with
# the image's own m), and over the CN stub next to it;
# an image with its own Play Store (the X9U IN) keeps it. The image is not
# changed. The guest's own installed copy of Play services (APK folder only)
# is removed, so the system copy runs. guest_prepare.sh then hides One UI's
# GmsCore in the guest's gmssystem APEX, so the guest finds Play services
# once, in that folder, where the image's own privapp permissions file grants
# it.
#
# Neither step stops a switch. Without a checked copy (old guest app ids, a
# missing guard, an unexpected APK path, no space) the guest starts as before.
STAGE=${STAGE:-/data/local/tmp/oneos}
BB=${BB:-/data/oneos_bin/busybox}
CACHE="$STAGE/google_apps"
say() { echo "Google apps: $*"; }
# ColorOS sets (their guards.txt lists oplus-services_patched.jar) and HyperOS
# sets (miui-services_patched.jar; the Xiaomi 18 Fold CN build has the same
# GmsCore folder with only m and the same Play Store stub, in product). Pixel
# sets present the image's own Play services (guest_prepare.sh GMS_WANT), and
# so does a set with a gmssystem payload (Infinity-X, 0.4.27, which has OPPO's
# jars too). Not when a launch hides Google services (ONEOS_GUEST_HIDE_GMS=1).
# vivo OriginOS (vivo-services_patched.jar) presents Play services from its own
# gmssystem payload (guest_prepare.sh GMS_WANT), but its Play Store is the same
# 2022 CN stub without a launcher entry (F8U run 13: "Play Store missing"):
# STORE_ONLY, One UI's Play Store over the stub and Play services left alone.
STORE_ONLY=0
# LineageOS (0.5.06, set "lineage"): the image has no Google apps at all, no
# GmsCore folder to bind over and no Google permission files. FULL: One UI's
# Play services, Play Store and Google Services Framework (sign-in), with One
# UI's own Google permission, sysconfig and default-permission files, added to
# the guest's product partition through read-only overlays (prepare_full).
FULL=0
selected() {
    [ "${ONEOS_GUEST_GMS:-1}" = 1 ] && [ "${ONEOS_GUEST_HIDE_GMS:-0}" != 1 ] || return 1
    if grep -q -E '"set": *"lineage"' "$STAGE/session/current.json" 2>/dev/null; then
        FULL=1
        return 0
    fi
    if grep -q -E ' vivo-services_patched.jar$' "$STAGE/guards.txt" 2>/dev/null && [ -f "$STAGE/gmssystem_payload.img" ]; then
        STORE_ONLY=1
        return 0
    fi
    grep -q -E ' (oplus|miui)-services_patched.jar$' "$STAGE/guards.txt" 2>/dev/null && [ ! -f "$STAGE/gmssystem_payload.img" ]
}
# No links anywhere below the cache, a guest-planted link to host data included.
plain_tree() {
    [ ! -L "$1" ] && { [ ! -d "$1" ] || [ -z "$(find "$1" -type l -print -quit)" ]; }
}
verify() { # <dir>: every APK and library as the snapshot recorded it
    dir=$1
    [ -d "$dir" ] && plain_tree "$dir" || return 1
    (cd "$dir" && [ -s apk.sha256 ] && [ -f native.sha256 ] || exit 1
     vpids=""
     for w in 0 1 2 3 4 5 6 7; do
         (s=$(awk -v w="$w" 'NR % 8 == w' apk.sha256 native.sha256)
          [ -z "$s" ] || printf '%s\n' "$s" | sha256sum -c - >/dev/null 2>&1) &
         vpids="$vpids $!"
     done
     vrc=0
     for vp in $vpids; do wait "$vp" || vrc=1; done
     exit "$vrc")
}
# Copy <package>'s APKs from One UI into $CACHE/<name>, unless the copy there
# still matches One UI's files (path, size and time).
session_stamp() {
    [ -s "$STAGE/session/current.json" ] || return 1
    stat -c '%d:%i:%s:%Y' "$STAGE/session/current.json" "$1"/*.apk "$1"/lib/*.so "$1"/lib/arm64/*.so 2>/dev/null | sha256sum | cut -d' ' -f1
}
mark_verified() {
    vs=$(session_stamp "$1") && [ -n "$vs" ] && printf '%s\n' "$vs" > "$1/.verified_session" 2>/dev/null || true
}
copy_app() { # <package> <name>
    pkg=$1; name=$2; dir="$CACHE/$name"; new="$CACHE/.$name-$$"
    plain_tree "$dir" || { WHY="a link below $dir"; return 1; }
    pm path "$pkg" 2>/dev/null | sed -n 's/^package://p' > "$CACHE/.$name.paths"
    [ -s "$CACHE/.$name.paths" ] || { WHY="$pkg is not installed in One UI"; return 1; }
    : > "$CACHE/.$name.stat"
    while IFS= read -r source; do
        case "$source" in
            /data/app/*/*.apk|/product/*/*.apk|/system/*/*.apk|/system_ext/*/*.apk) ;;
            /apex/com.google.android.gmssystem/priv-app/*/*.apk) ;;
            *) WHY="unexpected APK path $source"; return 1 ;;
        esac
        [ -f "$source" ] && [ ! -L "$source" ] || { WHY="$source is missing or a link"; return 1; }
        stat -c '%n %s %Y' "$source" >> "$CACHE/.$name.stat" || { WHY="cannot stat $source"; return 1; }
    done < "$CACHE/.$name.paths"
    if verify "$dir" && cmp -s "$dir/source.stat" "$CACHE/.$name.stat"; then
        mark_verified "$dir"
        say "$name: One UI's copy is unchanged"
        return 0
    fi
    rm -rf "$new"
    mkdir "$new" && mkdir -p "$new/lib/arm64" || { WHY="cannot create $new"; return 1; }
    while IFS= read -r source; do
        cp "$source" "$new/${source##*/}" || { WHY="cannot copy $source (space?)"; return 1; }
    done < "$CACHE/.$name.paths"
    for apk in "$new"/*.apk; do
        # BusyBox awk treats the doubled slash escapes as the end of the
        # regex. Match path components instead, and check both commands:
        # errors inside a for-loop substitution otherwise look like no libs.
        "$BB" unzip -l "$apk" > "$new/archive.list" || { WHY='cannot list APK'; return 1; }
        entries=$("$BB" awk 'split($4, p, "/") == 3 && p[1] == "lib" && p[2] == "arm64-v8a" && p[3] ~ /^lib.*[.]so$/ {print $4}' "$new/archive.list") || { WHY='cannot read APK library list'; return 1; }
        for entry in $entries; do
            lib=${entry##*/}
            "$BB" unzip -p "$apk" "$entry" > "$new/lib/$lib" || { WHY='cannot extract native library'; return 1; }
            [ -s "$new/lib/$lib" ] || { WHY='empty native library'; return 1; }
            rm -f "$new/lib/arm64/$lib"
            ln "$new/lib/$lib" "$new/lib/arm64/$lib" || { WHY='cannot link native library'; return 1; }
        done
    done
    rm -f "$new/archive.list"
    # APKs without embedded arm64 libraries are valid too. Keep an explicit
    # empty manifest instead of passing an unmatched glob to sha256sum.
    (cd "$new" && sha256sum ./*.apk > apk.sha256 &&
        : > native.sha256 &&
        for f in lib/*.so lib/arm64/*.so; do
            [ -f "$f" ] || continue
            sha256sum "$f" >> native.sha256 || exit 1
        done) || { WHY='cannot write checksums'; return 1; }
    cp "$CACHE/.$name.stat" "$new/source.stat"
    chown -R 0:0 "$new"; find "$new" -type d -exec chmod 755 {} \;
    find "$new" -type f -exec chmod 644 {} \;
    # Only our own binary copy, replaced; no guest data.
    rm -rf "$dir"
    mv "$new" "$dir" && verify "$dir" || { WHY="the new copy of $name does not verify"; return 1; }
    mark_verified "$dir"
    say "$name: copied from One UI ($(wc -l < "$dir/apk.sha256") APK(s), $(wc -l < "$dir/native.sha256") library file(s))"
}
# The guest's own installed copy of <package> (an earlier session's, or an app
# store's): its APK folder in the guest's /data/app, never its data. Galaxy S25
# with the X9U IN build on 0.4.16: ColorOS kept running Play services from such
# a copy instead of the system copy bound in prepare, without its privileged
# permissions ("You either need MANAGE_USERS"), and it crashed 720 times in
# 7 minutes until the phone was too hot to hold. With the copy gone, the
# package manager takes the system copy (and keeps the package's data).
# Prepare runs before the bridges, with nothing mounted under the staged /data
# (guest_prepare.sh checks that at its start; checked again here).
remove_installed() { # <package>
    app="$STAGE/data/app"
    [ -d "$app" ] && [ ! -L "$app" ] || return 0
    if awk -v p="$app" '$5 == p || index($5, p "/") == 1 { f = 1 } END { exit !f }' /proc/self/mountinfo; then
        say "something is mounted under the guest's /data/app: its installed $1 left alone"
        return 0
    fi
    for d in "$app"/*/"$1"-* "$app"/"$1"-*; do
        [ -d "$d" ] && [ ! -L "$d" ] || continue
        parent=${d%/*}
        if [ "$parent" != "$app" ]; then
            [ -d "$parent" ] && [ ! -L "$parent" ] || continue
        fi
        rm -rf "$d" && say "removed the guest's installed copy ${d#$STAGE/data/} (the system copy runs)"
        [ "$parent" = "$app" ] || rmdir "$parent" 2>/dev/null
    done
    return 0
}
# One UI's Google permission files (FULL): privileged permissions, sysconfig
# (power-save and hidden-API allowances, Google features) and default runtime
# permissions, from One UI's system and product partitions. Only XML files
# whose names say Google; never anything from /data.
CONFIG_DIRS="permissions sysconfig default-permissions"
copy_config() {
    dir="$CACHE/etc"; new="$CACHE/.etc-$$"
    plain_tree "$dir" || { WHY="a link below $dir"; return 1; }
    : > "$CACHE/.etc.stat"
    for sub in $CONFIG_DIRS; do
        for part in /system /product /system_ext /apex/com.google.android.gmssystem; do
            [ -d "$part/etc/$sub" ] || continue
            for f in "$part/etc/$sub"/*.xml; do
                [ -f "$f" ] && [ ! -L "$f" ] || continue
                case "${f##*/}" in
                    *google*|*Google*|*gms*|*GMS*|*vending*|*gsf*|*playstore*) ;;
                    *) continue ;;
                esac
                stat -c '%n %s %Y' "$f" >> "$CACHE/.etc.stat" || { WHY="cannot stat $f"; return 1; }
            done
        done
    done
    [ -s "$CACHE/.etc.stat" ] || { WHY="One UI has no Google permission files"; return 1; }
    if verify_config && [ -f "$dir/permissions/oneos-privapp-permissions-gms.xml" ] && cmp -s "$dir/source.stat" "$CACHE/.etc.stat"; then
        say "Google permission files: One UI's copy is unchanged"
        return 0
    fi
    rm -rf "$new"; mkdir -p "$new" || { WHY="cannot create $new"; return 1; }
    n=0
    for sub in $CONFIG_DIRS; do
        mkdir -p "$new/$sub"
        for part in /system /product /system_ext /apex/com.google.android.gmssystem; do
            [ -d "$part/etc/$sub" ] || continue
            p_tag=$(printf '%s' "${part#/}" | tr '/' '_')
            for f in "$part/etc/$sub"/*.xml; do
                [ -f "$f" ] && [ ! -L "$f" ] || continue
                case "${f##*/}" in
                    *google*|*Google*|*gms*|*GMS*|*vending*|*gsf*|*playstore*) ;;
                    *) continue ;;
                esac
                b="$p_tag-${f##*/}"
                cp "$f" "$new/$sub/$b" || { WHY="cannot copy $f"; return 1; }
                n=$((n + 1))
            done
        done
    done
    [ $n -gt 0 ] || { WHY="One UI has no Google permission files"; return 1; }
    cat > "$new/permissions/oneos-privapp-permissions-gms.xml" <<'GMSPERM' || { WHY="cannot write GMS device_config permissions"; return 1; }
<?xml version="1.0" encoding="utf-8"?>
<permissions>
    <privapp-permissions package="com.google.android.gms">
        <permission name="android.permission.READ_DEVICE_CONFIG"/>
        <permission name="android.permission.WRITE_DEVICE_CONFIG"/>
        <permission name="android.permission.WRITE_ALLOWLISTED_DEVICE_CONFIG"/>
    </privapp-permissions>
</permissions>
GMSPERM
    n=$((n + 1))
    (cd "$new" && find . -type f -name '*.xml' -exec sha256sum {} + > config.sha256) || { WHY="cannot write checksums"; return 1; }
    cp "$CACHE/.etc.stat" "$new/source.stat"
    chown -R 0:0 "$new"; find "$new" -type d -exec chmod 755 {} \;; find "$new" -type f -exec chmod 644 {} \;
    rm -rf "$dir"; mv "$new" "$dir" || { WHY="cannot keep the config copy"; return 1; }
    say "Google permission files: $n copied from One UI"
}
verify_config() {
    [ -d "$CACHE/etc" ] && plain_tree "$CACHE/etc" && (cd "$CACHE/etc" && [ -s config.sha256 ] && sha256sum -c config.sha256) >/dev/null 2>&1
}
# FULL prepare: the three apps into product/priv-app and the files into
# product/etc/<dir>. No overlays (this kernel refuses an overlay on an overlay,
# and an overlay layer on /data: F8U 11-14): each folder's top level is copied
# on a small tmpfs, the image's entries bound back into it, and the apps'
# folders and the XML files added as mount points with the checked copies bound
# read-only on them (farm_dir, as guest_prepare.sh does for product/apex). Any
# failure takes back every mount made here.
farm_dir() { # <target> <work dir> <entry>... (a name ending in / is a folder)
    fd_t=$1; fd_w=$2; shift 2
    fd_f="$fd_w/farm"; fd_o="$fd_w/orig"
    "$BB" umount -l "$fd_f" 2>/dev/null; "$BB" umount -l "$fd_o" 2>/dev/null
    mkdir -p "$fd_f" "$fd_o" && "$BB" mount -t tmpfs -o size=256k,mode=755 tmpfs "$fd_f" || return 1
    for fd_e in "$fd_t"/* "$fd_t"/.[!.]*; do
        [ -e "$fd_e" ] || [ -L "$fd_e" ] || continue
        fd_n=${fd_e##*/}
        if [ -L "$fd_e" ]; then cp -P "$fd_e" "$fd_f/$fd_n"
        elif [ -d "$fd_e" ]; then mkdir "$fd_f/$fd_n"
        else : > "$fd_f/$fd_n"; fi || { "$BB" umount -l "$fd_f"; return 1; }
    done
    for fd_e in "$@"; do
        case "$fd_e" in */) mkdir -p "$fd_f/${fd_e%/}" ;; *) : > "$fd_f/$fd_e" ;; esac || { "$BB" umount -l "$fd_f"; return 1; }
    done
    chmod 755 "$fd_f"; chcon -R u:object_r:system_file:s0 "$fd_f" 2>/dev/null
    "$BB" mount --bind "$fd_t" "$fd_o" || { "$BB" umount -l "$fd_f"; return 1; }
    # Private before the farm goes over the target: a shared bind would show the farm here too.
    "$BB" mount --make-private "$fd_o" || { "$BB" umount -l "$fd_o"; "$BB" umount -l "$fd_f"; return 1; }
    "$BB" mount --bind "$fd_f" "$fd_t" || { "$BB" umount -l "$fd_o"; "$BB" umount -l "$fd_f"; return 1; }
    for fd_e in "$fd_o"/* "$fd_o"/.[!.]*; do
        [ -e "$fd_e" ] && [ ! -L "$fd_e" ] || continue
        "$BB" mount --bind "$fd_e" "$fd_t/${fd_e##*/}" || { unfarm "$fd_t" "$fd_w"; return 1; }
    done
    "$BB" mount -o remount,ro "$fd_f" 2>/dev/null
    return 0
}
unfarm() { # <target> <work dir>: everything farm_dir made there
    awk -v r="$1/" 'index($2, r) == 1 {print $2}' /proc/mounts | sort -r | while read -r m; do "$BB" umount -l "$m" 2>/dev/null; done
    grep -q " $1 tmpfs " /proc/mounts && "$BB" umount -l "$1"
    "$BB" umount -l "$2/orig" 2>/dev/null; "$BB" umount -l "$2/farm" 2>/dev/null
}
prepare_full() {
    verify_config || { say "the copy of Google's permission files does not match its checksums: not used"; return 0; }
    PRODUCT="$ROOTFS/system/product"
    [ -L "$PRODUCT" ] && PRODUCT="$ROOTFS/product"
    [ -d "$PRODUCT/priv-app" ] && [ ! -L "$PRODUCT/priv-app" ] || { say "the image has no product/priv-app folder: not used"; return 0; }
    for name in GmsCore Phonesky GoogleServicesFramework; do
        [ ! -e "$PRODUCT/priv-app/$name" ] || { say "the image has its own priv-app/$name: not used"; return 0; }
    done
    W="$STAGE/google_farm"
    farmed=
    undo_all() { for f in $farmed; do unfarm "${f%%|*}" "${f#*|}"; done; }
    farm_dir "$PRODUCT/priv-app" "$W/priv-app" GmsCore/ Phonesky/ GoogleServicesFramework/ ||
        { say "could not add mount points to ${PRODUCT#$ROOTFS/}/priv-app: not used"; return 0; }
    farmed="$PRODUCT/priv-app|$W/priv-app"
    for name in GmsCore Phonesky GoogleServicesFramework; do
        target="$PRODUCT/priv-app/$name"
        chcon -R u:object_r:system_file:s0 "$CACHE/$name" &&
            "$BB" mount --bind "$CACHE/$name" "$target" &&
            "$BB" mount -o remount,bind,ro "$CACHE/$name" "$target" || {
            say "could not bind $name on ${target#$ROOTFS/}: not used"; undo_all; return 0
        }
    done
    nfiles=0
    for sub in $CONFIG_DIRS; do
        files=$(cd "$CACHE/etc/$sub" 2>/dev/null && ls *.xml 2>/dev/null)
        [ -n "$files" ] || continue
        tgt="$PRODUCT/etc/$sub"
        [ -d "$tgt" ] && [ ! -L "$tgt" ] || { say "${tgt#$ROOTFS/} is missing or a link: not used"; undo_all; return 0; }
        # Populate a single read-only tmpfs with our verified Google XMLs first,
        # then bind the image's own XMLs back over it so any same-named image file
        # wins — zero per-XML bind mounts in /proc/mounts (saves 70 mount syscalls
        # and avoids installd rescanning 35 extra quota-mount entries at boot).
        ef_w="$W/etc-$sub"; ef_f="$ef_w/farm"; ef_o="$ef_w/orig"
        "$BB" umount -l "$ef_f" 2>/dev/null; "$BB" umount -l "$ef_o" 2>/dev/null
        mkdir -p "$ef_f" "$ef_o" && "$BB" mount -t tmpfs -o size=2m,mode=755 tmpfs "$ef_f" ||
            { say "could not mount tmpfs for ${tgt#$ROOTFS/}: not used"; undo_all; return 0; }
        sub_n=0
        for f in $files; do
            cp "$CACHE/etc/$sub/$f" "$ef_f/$f" && chmod 644 "$ef_f/$f" ||
                { "$BB" umount -l "$ef_f" 2>/dev/null; say "could not copy $sub/$f: not used"; undo_all; return 0; }
            sub_n=$((sub_n + 1))
        done
        for fd_e in "$tgt"/* "$tgt"/.[!.]*; do
            [ -e "$fd_e" ] || [ -L "$fd_e" ] || continue
            fd_n=${fd_e##*/}
            if [ -L "$fd_e" ]; then rm -f "$ef_f/$fd_n" && cp -P "$fd_e" "$ef_f/$fd_n"
            elif [ -d "$fd_e" ]; then rm -rf "$ef_f/$fd_n" && mkdir "$ef_f/$fd_n"
            else : > "$ef_f/$fd_n"; fi ||
                { "$BB" umount -l "$ef_f" 2>/dev/null; say "could not stage ${tgt#$ROOTFS/}/$fd_n: not used"; undo_all; return 0; }
        done
        chmod 755 "$ef_f"; chcon -R u:object_r:system_file:s0 "$ef_f" 2>/dev/null
        if ! "$BB" mount --bind "$tgt" "$ef_o" ||
           ! "$BB" mount --make-private "$ef_o" ||
           ! "$BB" mount --bind "$ef_f" "$tgt"; then
            "$BB" umount -l "$ef_o" 2>/dev/null; "$BB" umount -l "$ef_f" 2>/dev/null
            say "could not bind ${tgt#$ROOTFS/}: not used"; undo_all; return 0
        fi
        farmed="$tgt|$ef_w $farmed"
        for fd_e in "$ef_o"/* "$ef_o"/.[!.]*; do
            [ -e "$fd_e" ] && [ ! -L "$fd_e" ] || continue
            "$BB" mount --bind "$fd_e" "$tgt/${fd_e##*/}" ||
                { say "could not bind ${tgt#$ROOTFS/}/${fd_e##*/}: not used"; undo_all; return 0; }
        done
        "$BB" mount -o remount,ro "$ef_f" 2>/dev/null
        nfiles=$((nfiles + sub_n))
    done
    say "Play services, Play Store and Google Services Framework from One UI -> ${PRODUCT#$ROOTFS/}/priv-app (read-only), with $nfiles Google permission file(s) in ${PRODUCT#$ROOTFS/}/etc"
    remove_installed com.google.android.gms
    remove_installed com.android.vending
    remove_installed com.google.android.gsf
    : > "$CACHE/active"
}
case "$1" in
snapshot)
    selected || exit 0
    # 0.4.14-0.4.15 kept the Fold8's copy here; ours alone, no guest data.
    rm -rf "$STAGE/fold8_google"
    skip() {
        rm -rf "$CACHE"
        say "not added ($*); the guest starts without One UI's Play services and Play Store"
        exit 0
    }
    for t in gatekeeper pdb apexd appid15000; do
        grep '^#' "$STAGE/guards.txt" 2>/dev/null | grep -qw "$t" || skip "guards.txt lacks the $t guard"
    done
    awk -v ksu="me.weishu.kernelsu com.rifsxd.ksunext com.sukisu.ultra app.kitsusu.manager" 'BEGIN { split(ksu, a, " "); for (i in a) m[a[i]] = 1 } $2 ~ /^[0-9]+$/ && $2 >= 10000 && $2 <= 14999 && !($1 in m) { f = 1 } END { exit !f }' "$STAGE/data/system/packages.list" 2>/dev/null &&
        skip "the guest has app ids 10000-14999 from before the app-id guard; set up again with fresh guest data for Google apps"
    [ ! -e "$CACHE" ] || { [ -d "$CACHE" ] && [ ! -L "$CACHE" ]; } || skip "$CACHE is not a directory"
    mkdir -p "$CACHE" || skip "cannot create $CACHE"
    rm -rf "$CACHE"/.[!.]*
    WHY=
    run_snap() { # <tag> <cmd>...
        s_tag=$1; shift
        "$@" > "$CACHE/.$s_tag.out" || { printf '%s\n' "$WHY" > "$CACHE/.$s_tag.why"; return 1; }
    }
    spids=""; stags=""
    if [ $STORE_ONLY != 1 ]; then
        run_snap GmsCore copy_app com.google.android.gms GmsCore &
        spids="$spids $!"; stags="$stags GmsCore"
    fi
    run_snap Phonesky copy_app com.android.vending Phonesky &
    spids="$spids $!"; stags="$stags Phonesky"
    if [ $FULL = 1 ]; then
        run_snap GoogleServicesFramework copy_app com.google.android.gsf GoogleServicesFramework &
        spids="$spids $!"; stags="$stags GoogleServicesFramework"
        run_snap etc copy_config &
        spids="$spids $!"; stags="$stags etc"
    fi
    sfail=""
    set -- $stags
    for sp in $spids; do
        st=$1; shift
        if wait "$sp"; then
            [ -s "$CACHE/.$st.out" ] && [ -z "$sfail" ] && cat "$CACHE/.$st.out"
        elif [ -z "$sfail" ]; then
            sfail=$(cat "$CACHE/.$st.why" 2>/dev/null)
            [ -n "$sfail" ] || sfail="$st snapshot failed"
        fi
    done
    rm -f "$CACHE"/.*.paths "$CACHE"/.*.stat "$CACHE"/.*.out "$CACHE"/.*.why
    [ -z "$sfail" ] || skip "$sfail"
    ;;
prepare)
    rm -f "$CACHE/active"
    selected || exit 0
    ROOTFS="$STAGE/rootfs"
    [ -d "$CACHE" ] || { say "no copy from One UI (see the snapshot line before One UI stopped)"; exit 0; }
    names="GmsCore Phonesky"; [ $STORE_ONLY = 1 ] && names=Phonesky
    [ $FULL = 1 ] && names="GmsCore Phonesky GoogleServicesFramework"
    for name in $names; do
        vdir="$CACHE/$name"
        if [ -d "$vdir" ] && plain_tree "$vdir" && [ -s "$vdir/.verified_session" ] &&
           [ "$(cat "$vdir/.verified_session" 2>/dev/null)" = "$(session_stamp "$vdir")" ]; then
            continue
        fi
        verify "$vdir" || { say "the copy of $name does not match its checksums: not used"; exit 0; }
    done
    if [ $FULL = 1 ]; then
        prepare_full
        exit 0
    fi
    # The image's Play services folder: in my_product (ColorOS) or in the
    # guest's product folder, found inside the rootfs (system/product can be an
    # absolute link to /product, which in this namespace is One UI's).
    PRODUCT="$ROOTFS/system/product"
    [ -L "$PRODUCT" ] && PRODUCT="$ROOTFS/product"
    gms=""
    for d in "$ROOTFS/my_product/priv-app" "$PRODUCT/priv-app"; do
        [ $STORE_ONLY = 1 ] || "$BB" umount -l "$d/GmsCore" 2>/dev/null
        [ -d "$d/GmsCore" ] && [ ! -L "$d/GmsCore" ] && [ ! -L "$d" ] && { gms="$d/GmsCore"; break; }
    done
    [ -n "$gms" ] || { say "the image has no priv-app/GmsCore folder: not used"; exit 0; }
    # The CN builds' Play Store stub next to it; an image with its own Play
    # Store keeps it.
    store="${gms%/GmsCore}/GooglePlayServicesUpdater"
    "$BB" umount -l "$store" 2>/dev/null
    [ -d "$store" ] && [ ! -L "$store" ] || store=
    if [ $STORE_ONLY = 1 ]; then
        [ -n "$store" ] || { say "the image has no Play Store stub next to GmsCore: its own Play Store stays"; exit 0; }
        chcon -R u:object_r:system_file:s0 "$CACHE/Phonesky" &&
            "$BB" mount --bind "$CACHE/Phonesky" "$store" &&
            "$BB" mount --make-private "$store" &&
            "$BB" mount -o remount,bind,ro "$CACHE/Phonesky" "$store" || {
            "$BB" umount -l "$store" 2>/dev/null
            say "could not bind Phonesky over ${store#$ROOTFS/}: not used"
            exit 0
        }
        say "Phonesky from One UI -> ${store#$ROOTFS/} (read-only; Play services from the image's payload)"
        remove_installed com.android.vending
        exit 0
    fi
    # The image's own signed Dynamite modules stay with Play services.
    rm -rf "$CACHE/GmsCore/m"
    if [ -d "$gms/m" ]; then
        cp -a "$gms/m" "$CACHE/GmsCore/" || { rm -rf "$CACHE/GmsCore/m"; say "cannot copy the image's GmsCore/m: not used"; exit 0; }
    fi
    bound=
    for spec in "GmsCore:$gms" ${store:+"Phonesky:$store"}; do
        name=${spec%%:*}; target=${spec#*:}
        chcon -R u:object_r:system_file:s0 "$CACHE/$name" &&
            "$BB" mount --bind "$CACHE/$name" "$target" && bound="$bound $target" &&
            "$BB" mount --make-private "$target" &&
            "$BB" mount -o remount,bind,ro "$CACHE/$name" "$target" || {
            for b in $bound; do "$BB" umount -l "$b" 2>/dev/null; done
            say "could not bind $name over ${target#$ROOTFS/}: not used"
            exit 0
        }
        say "$name from One UI -> ${target#$ROOTFS/} (read-only)"
    done
    [ -n "$store" ] || say "the image keeps its own Play Store"
    remove_installed com.google.android.gms
    : > "$CACHE/active"
    ;;
*) echo "guest_google.sh: expected snapshot or prepare" >&2; exit 3 ;;
esac
