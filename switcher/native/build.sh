#!/bin/sh
# Builds the aarch64 helpers in this directory (static, except the Bluetooth
# audio bridge) into ../assets/ and bin/.
#
# Pin ANDROID_NDK_VERSION (default 28.2.13676358, matching third_party/guest-runtime)
# unless ANDROID_NDK_HOME is explicitly supplied or ALLOW_UNPINNED_NDK=1 is set.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
ANDROID_HOME="${ANDROID_HOME:-/opt/android-sdk}"
PINNED_NDK_VERSION="${ANDROID_NDK_VERSION:-28.2.13676358}"
if [ -n "${ANDROID_NDK_HOME:-}" ]; then
    NDK="$ANDROID_NDK_HOME"
elif [ -d "$ANDROID_HOME/ndk/$PINNED_NDK_VERSION" ]; then
    NDK="$ANDROID_HOME/ndk/$PINNED_NDK_VERSION"
elif [ "${ALLOW_UNPINNED_NDK:-0}" = 1 ]; then
    NDK="$(ls -d "$ANDROID_HOME"/ndk/* | sort -V | tail -1)"
else
    echo "error: pinned Android NDK $PINNED_NDK_VERSION not found under $ANDROID_HOME/ndk (set ANDROID_NDK_HOME or ALLOW_UNPINNED_NDK=1 to override)" >&2
    exit 1
fi
CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang"
STRIP="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip"
echo "toolchain: NDK=$NDK"
"$CC" --version | head -n 1

build_guest_root() {
    "$CC" --target=aarch64-linux-android29 -static -O2 -s -Wall -Wextra \
        -o "$DIR/../assets/guest_root" "$DIR/guest_root.c"
    "$STRIP" --strip-all "$DIR/../assets/guest_root"
    ls -la "$DIR/../assets/guest_root"
    sha256sum "$DIR/../assets/guest_root"
}

if [ "${1:-all}" = "guest_root" ]; then
    build_guest_root
    exit 0
fi

build_guest_root

"$CC" --target=aarch64-linux-android29 -static -O2 -Wall -Wextra \
    -o "$DIR/../assets/zygote_exec" "$DIR/zygote_launcher.c"
"$STRIP" "$DIR/../assets/zygote_exec"
ls -la "$DIR/../assets/zygote_exec"
sha256sum "$DIR/../assets/zygote_exec"

mkdir -p "$DIR/bin"
"$CC" --target=aarch64-linux-android29 -static -O2 -Wall -Wextra \
    -o "$DIR/bin/socket_exec" "$DIR/socket_exec.c"
"$STRIP" "$DIR/bin/socket_exec"
ls -la "$DIR/bin/socket_exec"
sha256sum "$DIR/bin/socket_exec"

"$CC" --target=aarch64-linux-android29 -static -O2 -Wall -Wextra \
    -o "$DIR/../assets/oneos_essential_key" "$DIR/oneos_essential_key.c"
"$STRIP" "$DIR/../assets/oneos_essential_key"
ls -la "$DIR/../assets/oneos_essential_key"
sha256sum "$DIR/../assets/oneos_essential_key"

# Bluetooth audio bridge (oneos_btaudio.cpp): dynamic, it needs the phone's
# libbinder_ndk.so; API 31 for AParcel_appendFrom.
"${CC}++" --target=aarch64-linux-android31 -O2 -Wall -Wextra -std=c++17 -static-libstdc++ \
    -o "$DIR/../assets/oneos_btaudio" "$DIR/oneos_btaudio.cpp" -lbinder_ndk -llog -ldl
"$STRIP" "$DIR/../assets/oneos_btaudio"
ls -la "$DIR/../assets/oneos_btaudio"
sha256sum "$DIR/../assets/oneos_btaudio"
