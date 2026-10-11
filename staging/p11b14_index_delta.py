"""What changed in the shared index since the prior phases? Read-only."""
import subprocess

PRIOR = "6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613"

out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


import hashlib
now = hashlib.sha256(open(".git/index", "rb").read()).hexdigest()
out.append("shared .git/index checksum")
out.append("  prior phases (A6..A13): %s" % PRIOR)
out.append("  now                   : %s" % now)
out.append("  CHANGED               : %s" % (now != PRIOR))
out.append("")

staged = [l for l in sh(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
out.append("staged entries: %d" % len(staged))
out.append("")
out.append("staged paths with HEAD->index numstat:")
for line in sh(["git", "diff", "--cached", "--numstat"]).splitlines():
    if line.strip():
        out.append("  " + line)

out.append("")
out.append("full status (XY) for staged paths:")
for line in sh(["git", "status", "--porcelain"]).splitlines():
    if line[:2].strip() and line[0] != "?" and line[0] != " ":
        out.append("  " + line)

text = "\n".join(out)
open("staging/p11b14_index_delta.txt", "w", encoding="utf-8").write(text)
print(text)
