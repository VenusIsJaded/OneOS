"""Offline checks on a built framework jar that ART's verifier would reject.

- try/catch ranges that cover no instructions (start == end), which the smali
  patchers produce when they drop the only call inside a try block ("bad
  exception entry: startAddr=14 endAddr=14", run 26 lost system_server to it);
- a move-result that does not directly follow an invoke or filled-new-array,
  which a patcher leaves when it drops a call but not its result (run 40:
  "copyRes1 v4<- result0 type=Undefined", LockSettingsService rejected, and
  system_server died on the missing lock_settings service).

Usage: check-dex.py <jar> [...]   (needs dexdump from the SDK build-tools)
Exits 1 and lists the methods if anything is found.
"""
import glob, os, re, subprocess, sys, tempfile, zipfile

dexdump = sorted(glob.glob(os.path.join(
    os.environ.get("ANDROID_HOME", "/opt/android-sdk"), "build-tools", "*", "dexdump*"
)))[-1]
DEX_NAME = re.compile(r"classes\d*\.dex$")
METHOD_RE = re.compile(r"\|\[[0-9a-f]+\] (\S+)$")
OP_RE = re.compile(r"\|([0-9a-f]{4}): (\S+)")
TRY_RE = re.compile(r"\s+0x([0-9a-f]{4}) - 0x([0-9a-f]{4})\s*$")

bad = []
for jar in sys.argv[1:]:
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            if not DEX_NAME.match(name):
                continue
            z.extract(name, tmp)
            out = subprocess.run([dexdump, "-d", os.path.join(tmp, name)], capture_output=True).stdout.decode("utf-8", "replace")
            method = None
            prev = None
            for line in out.splitlines():
                if "|" in line:
                    m = OP_RE.search(line)
                    if m:
                        op = m.group(2)
                        if op.startswith("move-result") and not (prev or "").startswith(("invoke-", "filled-new-array")):
                            bad.append(f"{os.path.basename(jar)}:{name}: {method} {op} at 0x{m.group(1)} after {prev or 'nothing'}")
                        prev = op
                        continue
                    m = METHOD_RE.search(line)
                    if m:
                        method = m.group(1)
                        prev = None
                        continue
                elif " - 0x" in line:
                    m = TRY_RE.match(line)
                    if m and m.group(1) == m.group(2):
                        bad.append(f"{os.path.basename(jar)}:{name}: {method} empty try range 0x{m.group(1)}")
for b in bad:
    print(b)
print(f"{len(bad)} empty try ranges or orphan move-results", file=sys.stderr)
sys.exit(1 if bad else 0)
