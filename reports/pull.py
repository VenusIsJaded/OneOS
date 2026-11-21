#!/usr/bin/env python3
"""Collect the reports sent from the OneOS app (reports/worker.js) onto the team's server.

    reports/pull.py [--dest DIR] [--keep]

For each record in the service: fetch it, fetch its zip's parts, join them and
check the SHA-256 the app sent, then file it as

    DIR/inbox/<received date>/<report id>/report.json   (the record)
    DIR/inbox/<received date>/<report id>/<zip name>    (the logs or findings, if any)

and add the record to DIR/db/reports.jsonl (every report ever collected, one
line each: docs/compatibility.md is counted from it). Only then are the record
and its parts deleted from the service (--keep leaves them). A report whose
parts are missing or do not match is left in the service and tried again next
time; the service drops anything after 30 days.

Reads the service's address from ~/.oneos-reports-url and its admin key from
~/.oneos-reports-admin-key (mode 600; never printed). Prints one line per report
collected. The zips are untrusted data: unpack each into its own folder and
read them with python -I, as with any upload.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
import zipfile

HOME = os.path.expanduser("~")
DEFAULT_DEST = "/srv/projects/OneOS/out/reports"
ID_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def read_secret(name):
    path = os.path.join(HOME, name)
    with open(path) as f:
        value = f.read().strip()
    if not value:
        sys.exit(f"{path} is empty")
    return value


class Service:
    def __init__(self, base, key):
        self.base = base.rstrip("/")
        self.key = key

    def call(self, method, path, **params):
        url = f"{self.base}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {self.key}", "User-Agent": "oneos-reports-pull/1"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    def keys(self, prefix):
        cursor, out = None, []
        while True:
            params = {"prefix": prefix}
            if cursor:
                params["cursor"] = cursor
            page = json.loads(self.call("GET", "/v1/list", **params))
            out += [k["name"] for k in page["keys"]]
            cursor = page.get("cursor")
            if not cursor:
                return out


def session_app(data):
    """The app of the log bundle in `data` (its own report.json), or None.

    The zip is a tester's upload: read the one member by exact name, bounded,
    and never extract to disk.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
            names = [n for n in z.namelist() if n.endswith("/report.json") or n == "report.json"]
            if len(names) != 1:
                return None
            info = z.getinfo(names[0])
            if info.file_size > 256 * 1024:
                return None
            rep = json.loads(head(z, info, 256 * 1024 + 1).decode("utf-8", "replace"))
            app = rep.get("app")
            ses = rep.get("session")
    except Exception:
        return None
    if not isinstance(app, dict):
        return None
    v, c = app.get("version"), app.get("versionCode")
    # 0.4.70 bundles: the build that launched the session (stage/current.json), when another
    # build collected the logs (0.4.68 sent 0.4.67 sessions as its own).
    s = ses.get("versionCode") if isinstance(ses, dict) else None
    if isinstance(s, int) and not isinstance(s, bool) and 0 < s < 10**9 and s != c:
        return {"version": f"build{s}", "versionCode": s}
    if not isinstance(v, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,32}", v):
        return None
    return {"version": v, "versionCode": c if isinstance(c, int) else None}


TIME = re.compile(rb"\d\d:\d\d:\d\d")


def garbage(b):
    """Random characters, not log text: no time anywhere and hardly a space (log text has words)."""
    if TIME.search(b):
        return False
    return b.count(b" ") + b.count(b"\n") < len(b) * 0.06


MAX_ARCHIVE_ENTRIES = 4096
MAX_LOGS_SAMPLED = 128
SAFE_ZNAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def safe_zip_name(raw_name, fallback="logs.zip"):
    """Sanitize uploaded archive filename and forbid colliding with report.json metadata (audit A10)."""
    base = os.path.basename(raw_name or fallback)
    if not base or base.lower() == "report.json" or not SAFE_ZNAME.fullmatch(base) or base in (".", ".."):
        return fallback
    return base


def head(z, info, n=4096):
    """The first n bytes of a zip member, decompressed from a stream: z.read(info)[:n] would first
    inflate the whole member (a tester's upload: 1.8 MB of zip held 1.8 GB of log)."""
    if info.flag_bits & 0x1:
        raise RuntimeError(f"member {info.filename!r} is encrypted")
    with z.open(info) as f:
        return f.read(n)


def implausible(data, logs):
    """Why a log bundle cannot be a real session, or None (11-18: 622 scripted reports from four throwaway
    accounts, each ~200 log files of random characters). Not the size: real full logs can be small (some
    were cut to under 1 MB), so only what is inside counts."""
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
            infos = z.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                return f"archive has {len(infos)} entries (limit {MAX_ARCHIVE_ENTRIES})"
            for i in infos:
                if i.flag_bits & 0x1:
                    return "archive contains encrypted entries"
            logs_in = [i for i in infos if i.filename.endswith(".log") and i.file_size >= 32]
            if len(logs_in) >= 10:
                sample = logs_in[:MAX_LOGS_SAMPLED]
                bad = sum(1 for i in sample if garbage(head(z, i)))
                if bad * 2 >= len(sample):
                    return f"{bad} of {len(sample)} log files are random characters"
    except zipfile.BadZipFile:
        return None  # judged by the record alone (two old reports of 11-11 were stored that way)
    except (RuntimeError, NotImplementedError, zlib.error, EOFError, OSError) as e:
        return f"unreadable zip member ({type(e).__name__})"
    return None


def load_seen_ids(db):
    if not os.path.exists(db):
        return set()
    with open(db) as f:
        return {json.loads(line).get("reportId") for line in f if line.strip()}


def collect(svc, name, dest, keep, seen=None):
    record = json.loads(svc.call("GET", "/v1/get", key=name))
    rid = record.get("reportId", "")
    if not rid or set(rid) - ID_OK:
        return f"skipped {name}: bad report id"
    day = (record.get("receivedAt") or "unknown")[:10]
    # Held (the account's first two days, reports/worker.js) and implausible reports go beside the inbox.
    box = "held" if record.get("held") else "inbox"
    folder = os.path.join(dest, box, day, rid)
    logs = record.get("logs")
    parts = []
    if logs:
        zname = safe_zip_name(logs.get("name"), "logs.zip")
        data = bytearray()
        for i in range(int(logs["parts"])):
            key = f"part:{rid}:{i}"
            try:
                data += svc.call("GET", "/v1/get", key=key)
            except urllib.error.HTTPError as e:
                return f"waiting {rid}: part {i} not there ({e.code})"
            parts.append(key)
        if len(data) != logs["bytes"] or hashlib.sha256(data).hexdigest() != logs["sha256"]:
            return f"waiting {rid}: the parts do not match the record (size or SHA-256)"
        why = implausible(data, logs)
        if why:
            record["suspect"] = why
            folder = os.path.join(dest, "suspect", day, rid)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, zname), "wb") as f:
            f.write(data)
        # Findings: what the probe says about the phone, to Phone reports (reports/notify.py).
        # Never in the way of collecting: any failure is only logged.
        if logs.get("kind") == "probe" and box == "inbox" and not record.get("suspect"):
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import notify
                msg = notify.findings_message(os.path.join(folder, zname), record)
                if msg and not notify.send(msg):
                    print(f"note {rid}: the findings message was not accepted", flush=True)
            except Exception as e:  # noqa: BLE001 (the message is optional)
                print(f"note {rid}: no findings message ({type(e).__name__})", flush=True)
        # The version the SESSION ran on, which is not always the version that
        # sent it: a Z Fold7 report of 2026-10-30 was sent from 0.4.31 and held a
        # 0.4.19 ColorOS session the phone still had stored, so the compatibility
        # table credited an old run to the new release. The log bundle records its
        # own app in report.json; keep it beside the record under sessionApp.
        ran = session_app(data)
        if ran:
            record["sessionApp"] = ran
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report.json"), "w") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    db = os.path.join(dest, "db", "reports.jsonl")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    if seen is None:
        seen = load_seen_ids(db)
    if rid not in seen:
        with open(db, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        seen.add(rid)
    if not keep:
        svc.call("POST", "/v1/delete", key=name)
        for key in parts:
            svc.call("POST", "/v1/delete", key=key)
    phone = (record.get("phone") or {}).get("label") or (record.get("phone") or {}).get("model")
    start = (record.get("checklist") or {}).get("start")
    kind = "findings" if logs and logs.get("kind") == "probe" else "full log" if logs and logs.get("full") else "short log"
    size = f"{logs['bytes'] / 1048576:.1f} MB {kind}" if logs else "no logs"
    # Say loudly when the session predates the app that sent it: "Send to the
    # OneOS team" sends the session the phone has stored, which can be many
    # releases old (Z Fold7, 2026-10-30: 0.4.31 sent a 0.4.19 session). Those are
    # not evidence about the build we just shipped and must not be read as such.
    sent = record["app"]["version"]
    ran = (record.get("sessionApp") or {}).get("version")
    ver = f"OneOS {sent}"
    if ran and ran.split("-")[0] != sent.split("-")[0]:
        ver = f"OneOS {sent} !! STALE SESSION: it ran on {ran}, not {sent} !!"
    if record.get("suspect"):
        return f"suspect {rid} ({record['suspect']}): {phone}, from {(record.get('tg') or {}).get('username')} -> {folder}"
    if box == "held":
        return f"held {rid} (new account): {phone}, from {(record.get('tg') or {}).get('username')} -> {folder}"
    return f"new {rid}: {phone}, {record.get('imageName') or record.get('image')}, {ver}, start {start}, {size} -> {folder}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dest", default=DEFAULT_DEST)
    ap.add_argument("--keep", action="store_true", help="leave the reports in the service")
    a = ap.parse_args()
    svc = Service(read_secret(".oneos-reports-url"), read_secret(".oneos-reports-admin-key"))
    seen = load_seen_ids(os.path.join(a.dest, "db", "reports.jsonl"))
    for name in svc.keys("report:"):
        try:
            print(collect(svc, name, a.dest, a.keep, seen=seen), flush=True)
        except (urllib.error.URLError, ValueError, KeyError, RuntimeError, NotImplementedError, zlib.error, OSError) as e:
            print(f"error {name}: {e}", flush=True)


if __name__ == "__main__":
    main()
