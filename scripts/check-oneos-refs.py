#!/usr/bin/env python3
"""Calls into OneOS helper classes (oneos/*) in a patched jar must reach methods the
jar defines (dexdump -d). Catches a patcher overwriting or renaming another's helper:
the eleventh OriginOS CN run's zygote died on NoSuchMethodError
(OneOSVivoFeatures.listed, replaced by another helper of the same name).
Usage: check-oneos-refs.py <dexdump> <jar> [...]   (exit 1 when one is missing)"""
import re, subprocess, sys, tempfile, zipfile, os

INV = re.compile(r"invoke-\w+(?:/range)? \{[^}]*\}, (L[^;]+;)\.([^:]+):(\([^)]*\)\S+)")
DEX_RE = re.compile(r"classes\d*\.dex")
CLS_RE = re.compile(r"\s*Class descriptor\s*:\s*'(L[^']+;)'")
NAME_RE = re.compile(r"\s*name\s*:\s*'([^']+)'")
TYPE_RE = re.compile(r"\s*type\s*:\s*'(\([^']*\)\S+)'")
dexdump = sys.argv[1]
bad = 0
for jar in sys.argv[2:]:
    defined, calls = {}, set()
    with tempfile.TemporaryDirectory() as td:
        z = zipfile.ZipFile(jar)
        for n in z.namelist():
            if not DEX_RE.fullmatch(n):
                continue
            p = z.extract(n, td)
            out = subprocess.run([dexdump, "-d", p], capture_output=True).stdout.decode("utf-8", "replace")
            cls = None
            name = ""
            for line in out.splitlines():
                if "invoke-" in line:
                    m = INV.search(line)
                    if m:
                        calls.add((m.group(1), m.group(2) + m.group(3)))
                    continue
                if "'" not in line:
                    continue
                m = CLS_RE.match(line)
                if m:
                    cls = m.group(1); defined.setdefault(cls, set()); continue
                if cls:
                    m = NAME_RE.match(line)
                    if m:
                        name = m.group(1); continue
                    m = TYPE_RE.match(line)
                    if m:
                        defined[cls].add(name + m.group(1)); continue
    missing = sorted({(c, s) for c, s in calls if c in defined and s not in defined[c]
                      and (c.startswith("Loneos/") or True)})
    # Only report calls into oneos helpers or into classes a patcher could have changed
    # (a superclass method can satisfy other calls, so limit to oneos/*).
    missing = [(c, s) for c, s in missing if c.startswith("Loneos/")]
    print(f"{os.path.basename(jar)}: {len(calls)} calls, {len(missing)} into oneos/* missing")
    for c, s in missing:
        print("   MISSING", c, s); bad += 1
sys.exit(1 if bad else 0)
