"""What did concurrent work add to the metered-reservation test file? Read-only."""
import re
import subprocess

PATH = "tests/test_quota01_metered_reservation_is_hard.py"
out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


committed = sh(["git", "show", "49e70da2:" + PATH])
work = open(PATH, encoding="utf-8", errors="replace").read()

out.append("lines: in PR commits = %d   worktree = %d"
           % (len(committed.splitlines()), len(work.splitlines())))
out.append("")


def tests_of(text):
    return re.findall(r"^\s*(?:async\s+)?def (test_\w+)", text, re.M)


old, new = tests_of(committed), tests_of(work)
out.append("tests in PR commits: %d" % len(old))
for t in old:
    out.append("    %s" % t)
out.append("")
out.append("tests in worktree: %d" % len(new))
for t in new:
    mark = "   " if t in old else ">> NEW"
    out.append("  %s %s" % (mark, t))

added = [t for t in new if t not in old]
removed = [t for t in old if t not in new]
out.append("")
out.append("ADDED  : %d" % len(added))
out.append("REMOVED: %d  %s" % (len(removed), removed or "(none -- all preserved)"))

# For each new test: real-Redis gated? production API? keyspace proof?
out.append("")
out.append("=" * 86)
out.append("NEW TESTS - gating and API provenance (comments/docstrings stripped)")
out.append("=" * 86)

TRIPLE = '"""'


def code_only(block):
    kept, in_doc = [], False
    for line in block.splitlines():
        s = line.strip()
        if s.count(TRIPLE) == 1:
            in_doc = not in_doc
            continue
        if in_doc or s.startswith((TRIPLE, "#")):
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept)


lines = work.splitlines()
spans = []
for i, l in enumerate(lines):
    m = re.match(r"\s*(?:async\s+)?def (test_\w+)", l)
    if m:
        spans.append([m.group(1), i, len(lines)])
for i in range(len(spans) - 1):
    spans[i][2] = spans[i + 1][1]

PROD = ["reserve_quota", "get_quota_usage", "enforce_quota", "release_quota",
        "SubscriptionEngine.", "_meter_key"]
RAW = ["client.incrby", "client.decrby", "client.get", "client.set", "client.delete",
       "aioredis.from_url"]

for name, a, b in spans:
    if name not in added:
        continue
    body = code_only("\n".join(lines[a:b]))
    ctx = "\n".join(lines[max(0, a - 6):a])
    out.append("")
    out.append("  %s   (line %d)" % (name, a + 1))
    out.append("    real_redis_required marker : %s" % ("yes" if "real_redis_required" in ctx else "no"))
    out.append("    require_real_redis() call  : %s" % ("yes" if "require_real_redis()" in body else "no"))
    out.append("    production API             : %s"
               % (", ".join(sorted({p for p in PROD if p in body})) or "NONE"))
    out.append("    raw Redis                  : %s"
               % (", ".join(sorted({r for r in RAW if r in body})) or "none"))
    out.append("    keyspace proof             : %s"
               % ("yes" if re.search(r"_prove_one_keyspace|keyspace", body) else "no"))
    out.append("    mock refused (pytest.fail) : %s"
               % ("yes" if "pytest.fail" in body else "no"))

text = "\n".join(out)
open("staging/p11b14_metered_delta.txt", "w", encoding="utf-8").write(text)
print(text)
