"""Write the runnable subset of the affected test files, one per line."""
import pathlib
import subprocess
import sys

raw = subprocess.run(
    [sys.executable, "staging/p10c_find_affected.py"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
).stdout.splitlines()[1:]

# Excluded, with reasons:
#   conftest / capture_baseline  -- not test modules
#   tests/perf/                  -- excluded by pytest.ini norecursedirs (long experiments)
#   marketplace_paper_schema_contract -- the documented 17-minute Hypothesis migration replay
#     (staging/full_audit_results.json, TO-01). Not skipped as a product concern; it is a
#     known cost and is out of this workstream's scope.
SKIP = ("conftest", "capture_baseline", "tests/perf/", "marketplace_paper_schema_contract")

out = []
for line in raw:
    path = line.split("  <- ")[0].strip()
    if path and not any(s in path for s in SKIP):
        out.append(path)

pathlib.Path("staging/p10c_affected_list.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print(len(out))
