#!/system/bin/sh
# Read back every file of the host's APEXes that live on /data (decompressed
# or updated APEXes). After some switch sessions these returned dm-verity I/O
# errors and every One UI process that mapped them died with SIGBUS.
#
# Prints a summary; exits 1 if the manifest cannot be validated, if drop_caches
# failed when a disk read was required, or if any file/directory read failed.
# Usage: check_host_apex.sh [label]
LABEL="${1:-check}"
INFO_XML=${ONEOS_APEX_INFO_LIST:-/apex/apex-info-list.xml}
APEX_ROOT=${ONEOS_APEX_ROOT:-/apex}
STAMP=${ONEOS_APEX_STAMP:-/data/local/tmp/oneos/host_apex.checked}
BOOT=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
SIG=$(stat -c '%s:%Y' "$INFO_XML" 2>/dev/null)
# Reading from disk means dropping the page cache first, and everything after
# that starts cold. Only the look before the setup may skip it, and only when
# the last look of the session before (after-inplace, read from disk, nothing
# unreadable) left its stamp in this boot with the same APEX list: nothing of
# ours has run since. Every other look reads from disk: the one after the setup
# would otherwise be answered from the cache the look before it just filled.
FROM_DISK=1
[ "$LABEL" = before-setup ] && [ -n "$BOOT" ] && [ -n "$SIG" ] &&
    [ "$(cat "$STAMP" 2>/dev/null)" = "$BOOT $SIG" ] && FROM_DISK=0
# A session starts here or is still running: the stamp ends with this look.
rm -f "$STAMP" 2>/dev/null
DROPPED=1
if [ "$FROM_DISK" = 1 ]; then
    sync
    # A failed drop leaves the cache in place: the look is then not a read from disk,
    # cannot leave a stamp, and fails closed so callers do not trust cached pages (A6).
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || DROPPED=0
fi

bad=0
badlist=""
files=0
mod_count=0
modules=""

if [ ! -f "$INFO_XML" ] || [ ! -r "$INFO_XML" ]; then
    bad=$((bad + 1))
    badlist="$badlist $INFO_XML(manifest unreadable)"
else
    raw_info=$(cat "$INFO_XML" 2>/dev/null) || raw_info=""
    all_active=$(printf '%s\n' "$raw_info" | grep -o '<apex-info [^>]*>' | grep -c 'isActive="true"')
    if [ -z "$raw_info" ] || [ "${all_active:-0}" -eq 0 ]; then
        bad=$((bad + 1))
        badlist="$badlist $INFO_XML(no active APEX entries)"
    else
        modules=$(printf '%s\n' "$raw_info" | grep -o '<apex-info [^>]*>' |
            grep 'isActive="true"' | grep 'modulePath="/data/apex/' |
            sed -n 's/.*moduleName="\([^"]*\)".*/\1/p' | sort -u)
    fi
fi

tmpz=$(mktemp "${TMPDIR:-/data/local/tmp}/oneos_apex.XXXXXX" 2>/dev/null) || tmpz=""
[ -z "$tmpz" ] || trap 'rm -f "$tmpz"' EXIT HUP INT TERM

for m in $modules; do
    case "$m" in ''|*[!A-Za-z0-9._-]*)
        bad=$((bad + 1))
        badlist="$badlist $APEX_ROOT/$m(invalid-name)"
        continue
        ;;
    esac
    mod_count=$((mod_count + 1))
    # An active module of /data that is not mounted, cannot be listed, or has no file cannot have been read.
    if [ ! -d "$APEX_ROOT/$m" ]; then bad=$((bad + 1)); badlist="$badlist $APEX_ROOT/$m(missing)"; continue; fi
    if [ -n "$tmpz" ]; then
        if ! find "$APEX_ROOT/$m" -type f -print0 > "$tmpz" 2>/dev/null; then
            bad=$((bad + 1))
            badlist="$badlist $APEX_ROOT/$m(not listable)"
            continue
        fi
        n=$(tr -cd '\0' < "$tmpz" | wc -c | tr -d ' ')
        if [ "${n:-0}" -eq 0 ]; then bad=$((bad + 1)); badlist="$badlist $APEX_ROOT/$m(no files)"; continue; fi
        files=$((files + n))
        # Preserve NUL-delimited filenames end-to-end so even newline-containing paths are never split (A18).
        if ! xargs -0 cat < "$tmpz" > /dev/null 2>&1; then
            mod_bad=$(xargs -0 -n 1 sh -c 'cat -- "$1" > /dev/null 2>&1 || printf " %s" "$1" | tr "\n" "?"' sh < "$tmpz")
            if [ -n "$mod_bad" ]; then
                bad_n=$(xargs -0 -n 1 sh -c 'cat -- "$1" > /dev/null 2>&1 || echo x' sh < "$tmpz" | wc -l | tr -d ' ')
                bad=$((bad + bad_n))
                badlist="$badlist$mod_bad"
            else
                bad=$((bad + 1))
                badlist="$badlist $APEX_ROOT/$m(read-failed)"
            fi
        fi
    else
        # Fallback if mktemp could not write anywhere
        if ! find "$APEX_ROOT/$m" -type f -print0 2>/dev/null | xargs -0 -r cat > /dev/null 2>&1; then
            bad=$((bad + 1))
            badlist="$badlist $APEX_ROOT/$m(unreadable)"
        fi
    fi
done

src="read from disk"; [ "$FROM_DISK" = 1 ] || src="page cache kept: the last session's final look read them from disk"
if [ "$FROM_DISK" = 1 ] && [ "$DROPPED" != 1 ]; then
    src="page cache NOT dropped: drop_caches failed, so this is not a read from disk"
fi
echo "[$LABEL] host APEXes on /data: $mod_count modules, $files files, $bad unreadable${badlist:+:$badlist} ($src)"
if [ "$LABEL" = after-inplace ] && [ "$bad" -eq 0 ] && [ "$DROPPED" = 1 ] && [ -n "$BOOT" ] && [ -n "$SIG" ]; then
    printf '%s %s\n' "$BOOT" "$SIG" > "$STAMP" 2>/dev/null
fi
[ "$bad" -eq 0 ] && { [ "$FROM_DISK" = 0 ] || [ "$DROPPED" = 1 ]; }
