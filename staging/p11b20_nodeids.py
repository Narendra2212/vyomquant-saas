"""A20 Phase 3: node-ID comparison of the completed blocking lane vs baseline 37346853502."""
import collections
import re
import subprocess
from pathlib import Path

REPO = "Narendra2212/vyomquant-saas"
JOB = "114242427268"          # Unit & Integration Tests, run 38061973739, commit f5b99e57
BASE_JOB = "111888976615"     # Unit & Integration Tests, baseline run 37346853502
D5_FILES = {
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
}
out = []


def fetch(job, dest):
    p = Path(dest)
    if not p.exists():
        r = subprocess.run(["gh", "api", "--allow-escape-sequences",
                            "repos/%s/actions/jobs/%s/logs" % (REPO, job)],
                           capture_output=True)
        p.write_bytes(r.stdout)
    raw = p.read_bytes()
    txt = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw.decode("utf-8", errors="replace"))
    return txt


def node_ids(txt, token):
    ids, unparsed = set(), 0
    for l in txt.splitlines():
        if not re.search(r"\s%s\s*(\[|$)" % token, l):
            continue
        m = re.search(r"(tests/\S+?)\s+%s" % token, l)
        if m:
            ids.add(m.group(1))
        else:
            unparsed += 1
    return ids, unparsed


def totals(txt):
    for l in txt.splitlines():
        if re.search(r"=+ \d+ (failed|passed)", l):
            return l.strip()[-150:]
    return "(not found)"


cur = fetch(JOB, "staging/p11b20_cur_unit.log")
base = fetch(BASE_JOB, "staging/p11b20_base_unit.log")

cur_f, cur_u = node_ids(cur, "FAILED")
base_f, base_u = node_ids(base, "FAILED")
cur_e, _ = node_ids(cur, "ERROR")
cur_s, _ = node_ids(cur, "SKIPPED")

out.append("=" * 100)
out.append("TOTALS")
out.append("=" * 100)
out.append("  current  (job %s, commit f5b99e57): %s" % (JOB, totals(cur)))
out.append("  baseline (job %s, sha 6c5660a1)   : %s" % (BASE_JOB, totals(base)))
out.append("")
out.append("  parsed FAILED node ids  current=%d (unparsed %d)   baseline=%d (unparsed %d)"
           % (len(cur_f), cur_u, len(base_f), base_u))
out.append("  parsed ERROR node ids   current=%d" % len(cur_e))
out.append("  parsed SKIPPED node ids current=%d" % len(cur_s))

new = sorted(cur_f - base_f)
gone = sorted(base_f - cur_f)
same = cur_f & base_f

out.append("")
out.append("=" * 100)
out.append("NODE-ID SET COMPARISON (FAILED)")
out.append("=" * 100)
out.append("  in BOTH (established baseline failures) : %d" % len(same))
out.append("  NEW in this run                         : %d" % len(new))
out.append("  GONE since baseline                     : %d" % len(gone))

out.append("")
out.append("  BASELINE FAILURES THAT RECURRED, by file:")
for fl, n in collections.Counter(i.split("::")[0] for i in same).most_common():
    out.append("    %-4d %s" % (n, fl))

out.append("")
out.append("  NEW FAILURES, classified:")
for i in new:
    fl = i.split("::")[0]
    cat = "D5' file" if fl in D5_FILES else "NOT a D5' file  <-- INVESTIGATE"
    out.append("    [%s] %s" % (cat, i))
if not new:
    out.append("    (none)")

out.append("")
out.append("  GONE (baseline failures no longer failing):")
for i in gone:
    out.append("    %s" % i)
if not gone:
    out.append("    (none)")

out.append("")
out.append("=" * 100)
out.append("ERRORS AND SKIPS IN THE CURRENT RUN")
out.append("=" * 100)
out.append("  ERROR node ids (%d):" % len(cur_e))
for i in sorted(cur_e):
    out.append("    %s" % i)
out.append("")
out.append("  SKIPPED node ids in the three D5' files:")
d5_sk = sorted(i for i in cur_s if i.split("::")[0] in D5_FILES)
out.append("    %s" % (d5_sk or "NONE -- zero skips in the D5' files"))

out.append("")
out.append("=" * 100)
out.append("PER-FILE BREAKDOWN, CURRENT RUN, D5' FILES ONLY")
out.append("=" * 100)
for fl in sorted(D5_FILES):
    f_ = sorted(i for i in cur_f if i.startswith(fl))
    e_ = sorted(i for i in cur_e if i.startswith(fl))
    out.append("")
    out.append("  %s" % fl)
    out.append("    FAILED=%d  ERROR=%d" % (len(f_), len(e_)))
    for i in f_:
        out.append("      FAILED %s" % i.split("::", 1)[1])
    for i in e_:
        out.append("      ERROR  %s" % i.split("::", 1)[1])

open("staging/p11b20_nodeids.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
