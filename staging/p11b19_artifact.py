"""A19: download and analyse the D5' diagnostics artifact for commit f5b99e57."""
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO = "Narendra2212/vyomquant-saas"
ART = "11673133427"
DEST = Path("staging/p11b19_diag")
out = []


def gh(args):
    return subprocess.run(["gh"] + args, capture_output=True)


r = gh(["api", "repos/%s/actions/artifacts/%s/zip" % (REPO, ART)])
Path("staging/p11b19_diag.zip").write_bytes(r.stdout)
out.append("artifact download: rc=%d  %d bytes" % (r.returncode, len(r.stdout)))

DEST.mkdir(exist_ok=True)
z = zipfile.ZipFile("staging/p11b19_diag.zip")
out.append("contents:")
for n in z.namelist():
    out.append("  %-24s %8d B" % (n, z.getinfo(n).file_size))
z.extractall(DEST)

# ---------------------------------------------------------------- gate state
out.append("")
out.append("=" * 100)
out.append("GATE STATE RECORDED BEFORE ANY TEST RAN")
out.append("=" * 100)
gs = DEST / "gate_state.txt"
if gs.exists():
    for l in gs.read_text(encoding="utf-8", errors="replace").splitlines():
        out.append("  %s" % l)
else:
    out.append("  >> gate_state.txt ABSENT")

# ---------------------------------------------------------------- junit
out.append("")
out.append("=" * 100)
out.append("PER-FILE JUNIT RESULTS")
out.append("=" * 100)
summary = {}
for xml in sorted(DEST.glob("*.xml")):
    root = ET.parse(xml).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    a = suite.attrib
    rows = []
    for tc in suite.findall("testcase"):
        kind, msg = "PASSED", ""
        for ch in tc:
            if ch.tag in ("failure", "error"):
                kind = ch.tag.upper()
                msg = (ch.get("message") or "").strip().replace("\n", " ")[:150]
            elif ch.tag == "skipped":
                kind = "SKIPPED"
                msg = (ch.get("message") or "").strip().replace("\n", " ")[:150]
        rows.append((tc.get("name"), kind, msg))
    summary[xml.name] = (a, rows)
    out.append("")
    out.append("  %s" % xml.name)
    out.append("    tests=%s failures=%s errors=%s skipped=%s time=%s"
               % (a.get("tests"), a.get("failures"), a.get("errors"), a.get("skipped"), a.get("time")))
    for name, kind, msg in rows:
        mark = "   " if kind == "PASSED" else ">> "
        out.append("    %s%-8s %s" % (mark, kind, name))
        if msg:
            out.append("              %s" % msg)

# ---------------------------------------------------------------- redis evidence
out.append("")
out.append("=" * 100)
out.append("REAL-REDIS PROVISIONING / REACHABILITY EVIDENCE IN THE LOGS")
out.append("=" * 100)
PATS = [
    (r"AERORA_TEST_REDIS_URL", "opt-in variable echoed"),
    (r"state=run", "gate classified as RUN"),
    (r"state=skip|state=fail", "gate NOT run"),
    (r"redis service is healthy", "service healthcheck"),
    (r"Ready to accept connections", "server banner"),
    (r"Cache GET failed|Event loop is closed", "cache fault"),
    (r"KEYSPACE SPLIT", "keyspace probe tripped"),
    (r"MockRedisClient", "mock resolved"),
]
for log in sorted(DEST.glob("*.log")):
    text = log.read_text(encoding="utf-8", errors="replace")
    out.append("")
    out.append("  %s  (%d lines)" % (log.name, len(text.splitlines())))
    for pat, label in PATS:
        hits = [l.strip() for l in text.splitlines() if re.search(pat, l)]
        if hits:
            out.append("    %-28s %d hit(s)" % (label, len(hits)))
            for h in hits[:3]:
                out.append("        %s" % h[:118])
    tail = [l for l in text.splitlines() if re.search(r"=+ .*(passed|failed|error).* =+", l)]
    for l in tail[-2:]:
        out.append("    TOTALS: %s" % l.strip()[:118])

# ---------------------------------------------------------------- acceptance focus
out.append("")
out.append("=" * 100)
out.append("THE REQUIRED REAL-REDIS CASES, INDIVIDUALLY")
out.append("=" * 100)
REQUIRED = {
    "webhook.xml": ["test_the_credential_gate_refuses_test_keys",
                    "test_a_redelivered_webhook_is_ignored",
                    "test_the_lock_is_recorded_in_redis_under_the_documented_key",
                    "test_a_different_event_id_is_not_deduplicated",
                    "test_an_in_flight_delivery_blocks_a_concurrent_one",
                    "test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind"],
    "metered.xml": ["test_concurrent_reservations_are_atomic_against_real_redis",
                    "test_production_reserve_quota_admits_exactly_the_limit_against_real_redis",
                    "test_production_zero_allowance_writes_no_meter_against_real_redis"],
    "slot_lock.xml": ["test_the_slot_lock_is_mutually_exclusive_against_real_redis",
                      "test_the_guard_admits_exactly_the_limit_against_real_redis"],
}
for xmlname, names in REQUIRED.items():
    a, rows = summary.get(xmlname, ({}, []))
    by = {n: k for n, k, _ in rows}
    out.append("")
    out.append("  %s" % xmlname)
    for n in names:
        out.append("    %-76s %s" % (n, by.get(n, ">> NOT COLLECTED")))

open("staging/p11b19_artifact.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
