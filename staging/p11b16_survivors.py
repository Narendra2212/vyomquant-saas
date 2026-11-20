"""Which D5' reports are mine, which are the other session's, and does the patch still verify?"""
import hashlib
import os
import re
import subprocess
from pathlib import Path

PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
PRIV = os.path.abspath("staging/p11b16_private.index")
out = []

# ---- 1. report ownership survey -------------------------------------------------
# A marker only this session used: the content-digest invariant and the 23/39 split.
MINE = [r"7379218b", r"23 introduced", r"39 pre-existing", r"37346853502",
        r"CONTENT digest"]
THEIRS = [r"staged-content fingerprint", r"CF474F1B", r"Owner Coordination"]

out.append("=" * 94)
out.append("D5' REPORT OWNERSHIP (by distinctive markers)")
out.append("=" * 94)
for f in sorted(Path("staging").glob("PHASE11B_A1*.md")):
    t = f.read_text(encoding="utf-8", errors="replace")
    m = sum(1 for p in MINE if re.search(p, t))
    o = sum(1 for p in THEIRS if re.search(p, t))
    verdict = "this session" if m > o else ("other session" if o > m else "ambiguous")
    out.append("  %-50s %7d B  mine=%d other=%d  -> %s"
               % (f.name, f.stat().st_size, m, o, verdict))

# ---- 2. does my A12 report name #7 as the target? --------------------------------
out.append("")
out.append("=" * 94)
out.append("DOES THE WEBHOOK-FIX REPORT NAME A CANONICAL PR?")
out.append("=" * 94)
a12 = Path("staging/PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md")
t12 = a12.read_text(encoding="utf-8", errors="replace")
out.append("  %s  %d bytes" % (a12.name, a12.stat().st_size))
hits = [(t12[:m.start()].count("\n") + 1,
         t12.splitlines()[t12[:m.start()].count("\n")].strip()[:110])
        for m in re.finditer(r"PR\s*#\s*[678]", t12)]
out.append("  references to PR #6/#7/#8: %d" % len(hits))
for ln, s in hits:
    out.append("    L%-5d %s" % (ln, s))
out.append("")
out.append("  canonical/target language present: %s"
           % bool(re.search(r"canonical|target(?:s|ing)?\s+#", t12, re.I)))

# ---- 3. re-verify the prepared patch --------------------------------------------
out.append("")
out.append("=" * 94)
out.append("RE-VERIFY THE PREPARED PATCH AGAINST PR #6 (private index)")
out.append("=" * 94)


def digests():
    raw = hashlib.sha256(open(".git/index", "rb").read()).hexdigest()
    lines = sorted(subprocess.run(["git", "ls-files", "--stage"],
                                  capture_output=True).stdout.decode().splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return raw, h.hexdigest()


raw0, con0 = digests()
for cand in ("staging/p11b15_proposed_fixed.patch", "staging/p11b15_proposed.patch"):
    p = Path(cand)
    if not p.exists():
        out.append("  ABSENT %s" % cand)
        continue
    data = p.read_bytes()
    pre = re.findall(rb"^index ([0-9a-f]+)\.\.([0-9a-f]+)", data, re.M)
    paths = re.findall(rb"^diff --git a/(\S+)", data, re.M)
    out.append("")
    out.append("  %s  (%d bytes)" % (cand, len(data)))
    for pth, (a, b) in zip(paths, pre):
        pth_s = pth.decode()
        real = subprocess.run(["git", "rev-parse", "%s:%s" % (PR6, pth_s)],
                              capture_output=True).stdout.decode().strip()
        out.append("    %-54s pre=%s  PR#6=%s  match=%s"
                   % (pth_s, a.decode(), real[:len(a)], real.startswith(a.decode())))
    if os.path.exists(PRIV):
        os.remove(PRIV)
    env = dict(os.environ, GIT_INDEX_FILE=PRIV)
    subprocess.run(["git", "read-tree", PR6], capture_output=True, env=env)
    r = subprocess.run(["git", "apply", "--cached", "--check", "-"],
                       capture_output=True, input=data, env=env)
    out.append("    git apply --cached --check : rc=%d  %s"
               % (r.returncode, "APPLIES" if r.returncode == 0 else "REJECTED"))
    for line in r.stderr.decode(errors="replace").splitlines():
        out.append("      %s" % line)
    if os.path.exists(PRIV):
        os.remove(PRIV)

raw1, con1 = digests()
out.append("")
out.append("  shared index content digest before/after: %s / %s  %s"
           % (con0[:16], con1[:16], "SAME" if con0 == con1 else "CHANGED"))
out.append("  private index removed: %s" % (not os.path.exists(PRIV)))

text = "\n".join(out)
open("staging/p11b16_survivors.txt", "w", encoding="utf-8").write(text)
print("written")
