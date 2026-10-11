"""Can the blocking lane reach zero failures? Inspect PR #6's unit-tests job. Read-only."""
import collections
import re
import subprocess

JOB = "114200293902"  # Unit & Integration Tests, run 38047483583 (PR #6 head d07014d3)
REPO = "Narendra2212/vyomquant-saas"

r = subprocess.run(
    ["gh", "api", "--allow-escape-sequences",
     f"repos/{REPO}/actions/jobs/{JOB}/logs"], capture_output=True)
raw = r.stdout
open("staging/p11b14_pr6_unit.log", "wb").write(raw)

txt = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw.decode("utf-8", errors="replace"))
lines = txt.splitlines()

out = []
out.append("job %s  log lines=%d  bytes=%d" % (JOB, len(lines), len(raw)))

for l in lines:
    if re.search(r"=+ \d+ failed", l):
        out.append("TOTALS: " + l.strip()[-160:])

ids = set()
for l in lines:
    if not re.search(r"\sFAILED\s*(\[|$)", l):
        continue
    m = re.search(r"(tests/\S+?)\s+FAILED", l)
    if m:
        ids.add(m.group(1))

out.append("")
out.append("distinct FAILED node ids parsed: %d" % len(ids))
c = collections.Counter(i.split("::")[0] for i in ids)
D5 = {
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
}
d5_total = unrelated_total = 0
out.append("")
out.append("%-6s %-56s %s" % ("count", "file", "D5'?"))
for f, n in c.most_common():
    tag = "D5'" if f in D5 else "unrelated"
    if f in D5:
        d5_total += n
    else:
        unrelated_total += n
    out.append("%-6d %-56s %s" % (n, f, tag))

out.append("")
out.append("  D5'-attributable failures : %d" % d5_total)
out.append("  UNRELATED failures        : %d" % unrelated_total)
out.append("")
out.append("  => Removing the slot-lock file and fixing the webhook cannot by itself make")
out.append("     the blocking lane green: %d unrelated failures would remain." % unrelated_total)

# Gate state inside the blocking job, if the gate printed anything there.
out.append("")
for l in lines:
    if "AERORA_TEST_REDIS_URL" in l or "redis service is healthy" in l:
        out.append("  " + l.strip()[-120:])

text = "\n".join(out)
open("staging/p11b14_blocking_lane.txt", "w", encoding="utf-8").write(text)
print(text)
