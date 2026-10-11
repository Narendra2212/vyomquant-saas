"""Verify the proposed patch applies onto PR #6's tree, and locate the stale /15 quotes.

Uses a PRIVATE index file. The shared .git/index is never opened for writing; its
checksum and content digest are reported before and after.
"""
import hashlib
import os
import re
import subprocess

PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
PRIV = os.path.abspath("staging/p11b15_private.index")
out = []


def sh(args, stdin=None, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(args, capture_output=True, input=stdin, env=e)


def digests():
    raw = hashlib.sha256(open(".git/index", "rb").read()).hexdigest()
    lines = sorted(sh(["git", "ls-files", "--stage"]).stdout.decode().splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return raw, h.hexdigest()


raw0, con0 = digests()
out.append("shared index BEFORE  raw=%s" % raw0)
out.append("                 content=%s" % con0)

if os.path.exists(PRIV):
    os.remove(PRIV)
env = {"GIT_INDEX_FILE": PRIV}

r = sh(["git", "read-tree", PR6], env=env)
out.append("")
out.append("private index seeded from PR #6 tree: rc=%d" % r.returncode)

patch = open("staging/p11b15_proposed.patch", "rb").read()
r2 = sh(["git", "apply", "--cached", "--check", "--whitespace=error", "-"],
        stdin=patch, env=env)
out.append("")
out.append("git apply --cached --check --whitespace=error   (private index = PR #6)")
out.append("  rc = %d" % r2.returncode)
for line in (r2.stderr.decode(errors="replace") + r2.stdout.decode(errors="replace")).splitlines():
    out.append("  %s" % line)
out.append("  VERDICT: %s" % ("APPLIES CLEANLY onto PR #6, whitespace clean"
                              if r2.returncode == 0 else "DOES NOT APPLY"))

if os.path.exists(PRIV):
    os.remove(PRIV)
out.append("  private index removed: %s" % (not os.path.exists(PRIV)))

raw1, con1 = digests()
out.append("")
out.append("shared index AFTER   raw=%s  %s" % (raw1, "SAME" if raw1 == raw0 else "CHANGED"))
out.append("                 content=%s  %s" % (con1, "SAME" if con1 == con0 else "CHANGED"))

# ------------------------------------------------- stale /15 quotes still pending
out.append("")
out.append("=" * 92)
out.append("STALE /15 REFERENCES THAT THE PATCH DOES *NOT* YET FIX")
out.append("=" * 92)
for path in ("tests/test_webhook_duplicate_delivery_real_redis.py",
             "tests/test_quota01_metered_reservation_is_hard.py"):
    text = open(path, encoding="utf-8", errors="replace").read()
    hits = [(i, l.strip()) for i, l in enumerate(text.splitlines(), 1)
            if re.search(r"6379/15", l)]
    out.append("")
    out.append("  %s : %d reference(s)" % (path, len(hits)))
    for i, l in hits:
        out.append("    %4d: %s" % (i, l[:118]))

out.append("")
out.append("  These are prose, not behaviour: both files derive the client URL from")
out.append("  get_redis_url(REDIS_DB_CACHE) and never read AERORA_TEST_REDIS_URL for the")
out.append("  keyspace. The quotes are accurate for every CURRENT PR head (all carry /15)")
out.append("  and become stale the moment the workflow edit lands. Fixing them requires")
out.append("  editing a test file, which is a repair step -- not read-only preparation --")
out.append("  so it is left pending rather than slipped in.")

text = "\n".join(out)
open("staging/p11b15_verify_patch.txt", "w", encoding="utf-8").write(text)
print(text)
