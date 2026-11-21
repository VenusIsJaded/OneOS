#!/usr/bin/env python3
"""Execute the actual native bundle parser and nonblocking broker IPC on the build host.
Android privilege, ptrace and seccomp behavior still require a physical device.
"""
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "switcher/native/guest_root.c").read_text()


def function(name, ret="static int "):
    start = SOURCE.index(ret + name + "(")
    pos = SOURCE.index("{", start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (SOURCE[end] == "{") - (SOURCE[end] == "}")
        end += 1
    return SOURCE[start:end]


def bundle(entries):
    data = bytearray(b"ONEOS1\n")
    for path, value in entries:
        name = path.encode()
        data += f"{len(name)} {len(value)} 4755\n".encode() + name + value
    return bytes(data) + b"0 0 0\n"


class NativeBundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="oneos-native-test-")
        cls.binary = Path(cls.temp.name) / "unpack"
        source = Path(cls.temp.name) / "unpack.c"
        source.write_text("""#define _GNU_SOURCE
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
""" + function("write_all") + function("unpack") + "\nint main(int n,char **v){return n==2?unpack(v[1]):1;}\n")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_parser(self, value, setup=None):
        with tempfile.TemporaryDirectory(prefix="oneos-extract-") as temp:
            root = Path(temp) / "module"
            root.mkdir()
            if setup:
                setup(root)
            result = subprocess.run([self.binary, root], input=value, capture_output=True)
            contents = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_symlink()}
            modes = {str(p.relative_to(root)): stat.S_IMODE(p.stat().st_mode) for p in root.rglob("*") if p.is_file() and not p.is_symlink()}
            return result.returncode, contents, modes

    def test_extracts_files_without_executing_payload_or_setuid(self):
        code, files, modes = self.run_parser(bundle([("system/etc/example", b"payload"), ("customize.sh", b"exit 99")]))
        self.assertEqual(code, 0)
        self.assertEqual(files["system/etc/example"], b"payload")
        self.assertFalse(any(mode & 0o7000 for mode in modes.values()))

    def test_rejects_traversal_absolute_nul_and_empty_components(self):
        for path in ("../escape", "/tmp/escape", "system/../../escape", "a//b", "a/./b", "a/\0b"):
            with self.subTest(path=path):
                self.assertNotEqual(self.run_parser(bundle([(path, b"payload")]))[0], 0)

    def test_rejects_duplicate_and_truncated_entries(self):
        self.assertNotEqual(self.run_parser(bundle([("a", b"one"), ("a", b"two")]))[0], 0)
        self.assertNotEqual(self.run_parser(b"ONEOS1\n1 100 644\naonlythree")[0], 0)
        self.assertNotEqual(self.run_parser(bundle([]) + b"unexpected")[0], 0)

    def test_does_not_follow_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as other:
            code, _, _ = self.run_parser(bundle([("link/escape", b"payload")]), lambda root: (root / "link").symlink_to(other))
            self.assertNotEqual(code, 0)
            self.assertFalse((Path(other) / "escape").exists())

    def test_bundled_framework_has_valid_normalized_layout(self):
        code, files, _ = self.run_parser((ROOT / "switcher/assets/guest_vector.bundle").read_bytes())
        self.assertEqual(code, 0)
        self.assertEqual(files[".oneos-framework"], b"vector")
        for name in ("daemon", "daemon.apk", "manager.apk", "framework/vector.dex", "zygisk/arm64-v8a.so", "bin/dex2oat64"):
            self.assertTrue(files[name], name)


class SystemUidListTest(unittest.TestCase):
    def test_native_manager_lists_shell_and_system_packages(self):
        start = SOURCE.index("static int list_apps(")
        end = SOURCE.index("/* Take exactly", start)
        with tempfile.TemporaryDirectory() as temporary:
            w = Path(temporary)
            packages = w / "packages.list"
            packages.write_text("android 1000\ncom.android.shell 2000\ncom.low 9999\ncom.normal 10001\ncom.fahadali.oneos.guest 10002\n")
            code = (
                "#define _GNU_SOURCE\n"
                "#include <stdio.h>\n#include <string.h>\n#include <unistd.h>\n"
                "#include <fcntl.h>\n#include <poll.h>\n#include <time.h>\n"
                "#include <errno.h>\n#include <stdint.h>\n"
            )
            code += '#define PACKAGES "' + str(packages) + '"\n#define MANAGER "com.fahadali.oneos.guest"\n'
            code += "static int safe_package(const char *p) { (void)p; return 1; }\n"
            code += "static int granted(const char *p,unsigned u) { (void)p; return u==2000; }\n"
            code += function("write_client_bounded") + "\n"
            code += SOURCE[start:end] + "\nint main(void) { return list_apps(1) == 0 ? 0 : 1; }\n"
            source = w / "list.c"
            source.write_text(code)
            binary = w / "list"
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary)], check=True)
            rows = subprocess.check_output([str(binary)], text=True).splitlines()
            self.assertEqual(["android 1000 0", "com.android.shell 2000 1", "com.low 9999 0", "com.normal 10001 0"], rows)

    def test_partial_list_flush_failure_does_not_duplicate_and_returns_error(self):
        start = SOURCE.index("static int list_apps(")
        end = SOURCE.index("/* Take exactly", start)
        with tempfile.TemporaryDirectory() as temporary:
            w = Path(temporary)
            packages = w / "packages.list"
            packages.write_text("".join(f"com.pkg.item{i:04d} {10000 + i}\n" for i in range(3000)))
            code = (
                "#define _GNU_SOURCE\n"
                "#include <stdio.h>\n#include <string.h>\n#include <unistd.h>\n"
                "#include <fcntl.h>\n#include <poll.h>\n#include <time.h>\n"
                "#include <errno.h>\n#include <stdint.h>\n"
            )
            code += '#define PACKAGES "' + str(packages) + '"\n#define MANAGER "com.fahadali.oneos.guest"\n'
            code += "static int safe_package(const char *p) { (void)p; return 1; }\n"
            code += "static int granted(const char *p,unsigned u) { (void)p; (void)u; return 0; }\n"
            # Simulate a client pipe that accepts 4096 bytes of the first flush and then stalls/times out
            code += (
                "static int calls = 0;\n"
                "static int write_client_bounded(int fd, const void *buf, size_t n, int timeout_ms) {\n"
                "    (void)timeout_ms;\n"
                "    calls++;\n"
                "    if (calls == 1) { ssize_t w = write(fd, buf, n < 4096 ? n : 4096); (void)w; return -1; }\n"
                "    ssize_t w = write(fd, buf, n); (void)w; return -1;\n"
                "}\n"
            )
            code += SOURCE[start:end] + "\nint main(void) { return list_apps(1) == 0 ? 0 : 1; }\n"
            source = w / "list_partial.c"
            source.write_text(code)
            binary = w / "list_partial"
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary)], check=True)
            res = subprocess.run([str(binary)], capture_output=True)
            self.assertEqual(1, res.returncode, "partial LIST flush must return failure")
            self.assertEqual(4096, len(res.stdout), "failed mid-list flush must not resend buffer from offset 0")
            self.assertEqual(1, res.stdout.count(b"com.pkg.item0000 10000 0\n"))


class FullPipeNonblockingBrokerTest(unittest.TestCase):
    """Audit A5: a supplied full stderr/stdout pipe must never wedge the single-threaded broker."""

    def test_full_stderr_pipe_does_not_block_daemon_or_subsequent_calls(self):
        with tempfile.TemporaryDirectory(prefix="oneos-broker-") as tmp:
            w = Path(tmp)
            sock_path = w / "su.sock"
            grants_path = w / "grants"
            pkgs_path = w / "packages.list"
            my_uid = os.getuid()
            # Give manager a different UID unless invoked with --admin
            pkgs_path.write_text(f"com.fahadali.oneos.guest {my_uid + 1}\n")
            patched = (
                SOURCE.replace('#define SOCK BASE "/run/su.sock"', f'#define SOCK "{sock_path}"')
                .replace('#define GRANTS BASE "/grants"', f'#define GRANTS "{grants_path}"')
                .replace('#define PACKAGES "/data/system/packages.list"', f'#define PACKAGES "{pkgs_path}"')
                .replace("if(getuid()!=0)return 1;", "/* test allow non-root daemon */")
                .replace("if(uid==0)return 1;", "if(uid==999999)return 1;")
                .replace("int admin=cred.uid==0||", "int admin=cred.uid==999999||")
            )
            src = w / "broker.c"
            src.write_text(patched)
            bin_path = w / "broker"
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", str(src), "-o", str(bin_path)], check=True)
            daemon = subprocess.Popen([str(bin_path), "--daemon"])
            try:
                deadline = time.monotonic() + 3.0
                while not sock_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(sock_path.exists(), "broker socket did not appear")

                # Fill a pipe completely so any blocking write on w_fd hangs forever
                r_fd, w_fd = os.pipe()
                os.set_blocking(w_fd, False)
                chunk = b"x" * 65536
                try:
                    while True:
                        os.write(w_fd, chunk)
                except BlockingIOError:
                    pass
                os.set_blocking(w_fd, True)
                devnull = os.open("/dev/null", os.O_RDWR)
                try:
                    c1 = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                    c1.settimeout(2.0)
                    c1.connect(str(sock_path))
                    # Send RUN ("id") as unprivileged non-granted caller -> triggers denial write to fds[2] (full pipe!)
                    cmd = b"id"
                    hdr = struct.pack("<III", 0x4F535255, 1, len(cmd))
                    fds = struct.pack("iii", devnull, devnull, w_fd)
                    t0 = time.monotonic()
                    c1.sendmsg([hdr, cmd], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds)])
                    reply = c1.recv(4)
                    elapsed = time.monotonic() - t0
                    self.assertEqual(4, len(reply))
                    self.assertEqual(126, struct.unpack("<i", reply)[0])
                    self.assertLess(elapsed, 1.5, f"denial path blocked too long on full pipe: {elapsed:.2f}s")
                    c1.close()

                    # Prove subsequent management call (INFO as manager) is immediately served!
                    pkgs_path.write_text(f"com.fahadali.oneos.guest {my_uid}\n")
                    out_r, out_w = os.pipe()
                    try:
                        c2 = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                        c2.settimeout(2.0)
                        c2.connect(str(sock_path))
                        hdr2 = struct.pack("<III", 0x4F535255, 5, 0)  # INFO
                        fds2 = struct.pack("iii", devnull, out_w, devnull)
                        c2.sendmsg([hdr2], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds2)])
                        os.close(out_w)
                        out_w = -1
                        reply2 = c2.recv(4)
                        info_text = os.read(out_r, 256).decode()
                        self.assertEqual(0, struct.unpack("<i", reply2)[0])
                        self.assertIn("OneOS guest root API 1", info_text)
                        c2.close()
                    finally:
                        if out_w >= 0:
                            os.close(out_w)
                        os.close(out_r)
                finally:
                    os.close(devnull)
                    os.close(r_fd)
                    os.close(w_fd)
            finally:
                daemon.terminate()
                daemon.wait(timeout=3)

    def test_native_build_script_includes_guest_root_and_pinned_ndk_a19_n13(self):
        build_sh = (ROOT / "switcher/native/build.sh").read_text()
        self.assertIn("guest_root.c", build_sh)
        self.assertIn("../assets/guest_root", build_sh)
        self.assertIn("28.2.13676358", build_sh)


if __name__ == "__main__":
    unittest.main()
