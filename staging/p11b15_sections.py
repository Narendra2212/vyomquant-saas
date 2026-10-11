"""Apply each patch section in isolation to find the malformed one."""
import os
import re
import subprocess

PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
PRIV = os.path.abspath("staging/p11b15_private.index")
patch = open("staging/p11b15_proposed.patch", encoding="utf-8").read()

# Split on the diff --git boundaries.
idxs = [m.start() for m in re.finditer(r"^diff --git ", patch, re.M)] + [len(patch)]
sections = [patch[idxs[i]:idxs[i + 1]] for i in range(len(idxs) - 1)]

out = []
out.append("sections found: %d" % len(sections))


def check(label, data):
    if os.path.exists(PRIV):
        os.remove(PRIV)
    env = dict(os.environ, GIT_INDEX_FILE=PRIV)
    subprocess.run(["git", "read-tree", PR6], capture_output=True, env=env)
    r = subprocess.run(["git", "apply", "--cached", "--check", "-v", "-"],
                       capture_output=True, input=data.encode("utf-8"), env=env)
    out.append("")
    out.append("%-62s rc=%d" % (label, r.returncode))
    for line in r.stderr.decode(errors="replace").splitlines():
        out.append("    %s" % line)
    if os.path.exists(PRIV):
        os.remove(PRIV)
    return r.returncode


for s in sections:
    m = re.match(r"diff --git a/(\S+)", s)
    path = m.group(1) if m else "?"
    # Does the recorded pre-image hash match PR #6's blob?
    mi = re.search(r"^index ([0-9a-f]+)\.\.", s, re.M)
    pre = mi.group(1) if mi else "?"
    real = subprocess.run(["git", "rev-parse", f"{PR6}:{path}"],
                          capture_output=True).stdout.decode().strip()
    ok = real.startswith(pre)
    out.append("")
    out.append("  %s" % path)
    out.append("    patch pre-image %s   PR#6 blob %s   match=%s"
               % (pre, real[:len(pre)] if real else "(absent)", ok))
    out.append("    section ends with newline: %s" % s.endswith("\n"))
    out.append("    hunk headers: %s" % re.findall(r"^@@[^\n]*", s, re.M)[:3])
    check("    isolated apply: %s" % path, s)

# And all three concatenated, each guaranteed to end with a newline.
joined = "".join(s if s.endswith("\n") else s + "\n" for s in sections)
open("staging/p11b15_proposed_fixed.patch", "w", encoding="utf-8", newline="").write(joined)
rc = check("ALL THREE concatenated, newline-normalised", joined)
out.append("")
out.append("CONCLUSION: %s" % ("concatenation now applies cleanly" if rc == 0
                               else "still failing -- see per-section results above"))

text = "\n".join(out)
open("staging/p11b15_sections.txt", "w", encoding="utf-8").write(text)
print(text)
