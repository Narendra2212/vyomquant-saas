"""A19: root-cause detail for the webhook setup errors and the metered quota failure."""
import json
import re
import subprocess
from pathlib import Path

D = Path("staging/p11b19_diag")
REPO = "Narendra2212/vyomquant-saas"
out = []

# ---------------------------------------------------------------- webhook
out.append("=" * 100)
out.append("WEBHOOK: why did the application cache manager REFUSE the write?")
out.append("=" * 100)
wh = (D / "webhook.log").read_text(encoding="utf-8", errors="replace")
lines = wh.splitlines()

out.append("")
out.append("  per-test result lines:")
for l in lines:
    if re.search(r"::test_\w+ (PASSED|FAILED|ERROR|SKIPPED)", l):
        out.append("    %s" % l.strip()[:116])

out.append("")
out.append("  every 'Cache SET failed' / 'Cache GET failed' line, verbatim:")
for i, l in enumerate(lines):
    if "Cache SET failed" in l or "Cache GET failed" in l:
        out.append("    L%-4d %s" % (i, l.strip()[:150]))

out.append("")
out.append("  lines mentioning an event loop or connection state:")
for i, l in enumerate(lines):
    if re.search(r"Event loop|event loop|ConnectionError|Connection closed|RuntimeError", l):
        out.append("    L%-4d %s" % (i, l.strip()[:150]))

out.append("")
out.append("  first setup-error block in full:")
start = next((i for i, l in enumerate(lines) if re.search(r"ERROR at setup of", l)), None)
if start is not None:
    for l in lines[start:start + 34]:
        out.append("    %s" % l.rstrip()[:150])

out.append("")
out.append("  did the keyspace probe ever report a SPLIT?  %s"
           % ("yes" if "KEYSPACE SPLIT" in wh else "NO -- it failed earlier, on the refused write"))

# ---------------------------------------------------------------- metered
out.append("")
out.append("=" * 100)
out.append("METERED: the production reserve_quota failure, in context")
out.append("=" * 100)
mt = (D / "metered.log").read_text(encoding="utf-8", errors="replace")
ml = mt.splitlines()
out.append("")
out.append("  per-test result lines:")
for l in ml:
    if re.search(r"::test_\w+ (PASSED|FAILED|ERROR|SKIPPED)", l):
        out.append("    %s" % l.strip()[:116])

out.append("")
out.append("  failure block in full:")
start = next((i for i, l in enumerate(ml) if re.search(r"_{5,} test_production_reserve", l)), None)
if start is not None:
    for l in ml[start:start + 52]:
        out.append("    %s" % l.rstrip()[:150])

out.append("")
out.append("  WHICH ASSERTIONS PASSED BEFORE THE FAILING ONE?")
for probe, meaning in [
    (r"production reserve_quota admitted", "the hard-limit assertion (len(allowed) == limit)"),
    (r"get_quota_usage reported", "production reader vs limit"),
    (r"the raw meter key holds", "raw key vs limit"),
    (r"a refusal reported usage", "refusal usage bound  <-- THIS ONE FAILED"),
    (r"a retry against an exhausted meter", "post-exhaustion retry"),
]:
    tripped = bool(re.search(probe, mt))
    out.append("    %-52s assertion message present in output: %s" % (meaning, tripped))
out.append("")
out.append("  Reading: an assertion message only appears when that assertion FAILS. Only the")
out.append("  refusal-usage message appears, so the hard-limit check, the get_quota_usage/raw-key")
out.append("  agreement checks all PASSED and the failure is specifically the usage figure a")
out.append("  REFUSED reservation reports back.")

# ---------------------------------------------------------------- blocking lane
out.append("")
out.append("=" * 100)
out.append("BLOCKING LANE STATUS NOW")
out.append("=" * 100)
r = json.loads(subprocess.run(
    ["gh", "api", "repos/%s/actions/runs/38061973739" % REPO], capture_output=True
).stdout.decode() or "{}")
out.append("  run 38061973739 status=%s conclusion=%s updated=%s"
           % (r.get("status"), r.get("conclusion"), r.get("updated_at")))
jobs = json.loads(subprocess.run(
    ["gh", "api", "repos/%s/actions/runs/38061973739/jobs?per_page=50" % REPO, "--jq",
     "[.jobs[] | {id,name,status,conclusion}]"], capture_output=True).stdout.decode() or "[]")
for j in jobs:
    out.append("    %-13s %-12s %s" % (j["id"], j["conclusion"] or j["status"], j["name"]))

open("staging/p11b19_detail.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
