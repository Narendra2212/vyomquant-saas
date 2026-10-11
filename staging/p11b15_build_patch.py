"""Build the EXACT patch awaiting approval, as inert text. Applies nothing.

Produces staging/p11b15_proposed.patch against PR #6's tree (d07014d3) and verifies it
applies, using a PRIVATE index. No commit, no ref, no write to .git/index.
"""
import hashlib
import os
import re
import subprocess

PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
WF = ".github/workflows/01-pr-check.yml"
WEBHOOK = "tests/test_webhook_duplicate_delivery_real_redis.py"
METERED = "tests/test_quota01_metered_reservation_is_hard.py"
SLOT = "tests/test_quota01_exchange_slot_is_atomic.py"

PRIV = os.path.abspath("staging/p11b15_private.index")
out = []


def sh(args, stdin=None, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(args, capture_output=True, input=stdin, env=e)
    return r


def text_of(ref, path):
    r = sh(["git", "show", f"{ref}:{path}"])
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None


shared_before = hashlib.sha256(open(".git/index", "rb").read()).hexdigest()

# ---------------------------------------------------------------- workflow analysis
out.append("=" * 92)
out.append("WORKFLOW: what has to change in PR #6's copy")
out.append("=" * 92)
wf6 = text_of(PR6, WF)
wfw = open(WF, encoding="utf-8", errors="replace").read()
out.append("  PR #6 copy : %d lines" % len(wf6.splitlines()))
out.append("  worktree   : %d lines" % len(wfw.splitlines()))

out.append("")
out.append("  AERORA_TEST_REDIS_URL occurrences in PR #6's copy:")
for i, l in enumerate(wf6.splitlines(), 1):
    if re.search(r"^\s*AERORA_TEST_REDIS_URL:", l):
        out.append("    %4d: %s" % (i, l.strip()))
out.append("  -> both must become /0 (unit-tests AND the diagnostic job)")

out.append("")
out.append("  jobs in PR #6's copy:")
for key in ["unit-tests:", "d5-redis-diagnostics:", "provisioning-and-isolation:"]:
    out.append("    %-30s %s" % (key, "yes" if re.search(r"^\s{2}" + re.escape(key), wf6, re.M) else "no"))
out.append("  -> provisioning-and-isolation is absent from PR #6 and MUST NOT be added")

# Build the workflow change: ONLY the two gate-database tokens.
wf_new = re.sub(
    r"(^\s*AERORA_TEST_REDIS_URL:\s*redis://localhost:6379)/15\s*$",
    r"\1/0",
    wf6,
    flags=re.M,
)
changed = sum(1 for a, b in zip(wf6.splitlines(), wf_new.splitlines()) if a != b)
out.append("")
out.append("  lines changed by the gate-database edit: %d (expected 2)" % changed)

# ---------------------------------------------------------------- file-level plan
out.append("")
out.append("=" * 92)
out.append("FILE-LEVEL PLAN vs PR #6")
out.append("=" * 92)
for path in (WEBHOOK, METERED, SLOT):
    a = text_of(PR6, path)
    try:
        b = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        b = None
    out.append("  %-54s PR#6=%-5s worktree=%-5s %s"
               % (path,
                  len(a.splitlines()) if a else "none",
                  len(b.splitlines()) if b else "none",
                  "identical" if a == b else "DIFFERS"))

# ---------------------------------------------------------------- emit the patch
os.makedirs("staging", exist_ok=True)
tmp = "staging/_p11b15_wf_new.tmp"
open(tmp, "w", encoding="utf-8", newline="").write(wf_new)

parts = []
for path, new_text, label in (
    (WF, wf_new, "gate database /15 -> /0 (unit-tests + diagnostic job)"),
    (WEBHOOK, open(WEBHOOK, encoding="utf-8", errors="replace").read(),
     "A5 fail-not-skip + A12 application-derived URL + bidirectional keyspace probe"),
    (METERED, open(METERED, encoding="utf-8", errors="replace").read(),
     "+2 production-path real-Redis quota tests (all 7 originals preserved)"),
):
    old_text = text_of(PR6, path) or ""
    old_f = "staging/_old.tmp"
    new_f = "staging/_new.tmp"
    open(old_f, "w", encoding="utf-8", newline="").write(old_text)
    open(new_f, "w", encoding="utf-8", newline="").write(new_text)
    d = sh(["git", "diff", "--no-index", "--src-prefix=a/", "--dst-prefix=b/",
            "--", old_f, new_f]).stdout.decode("utf-8", errors="replace")
    d = d.replace(old_f, path).replace(new_f, path)
    parts.append("# %s\n#   %s\n%s" % (path, label, d))
    os.remove(old_f)
    os.remove(new_f)
os.remove(tmp)

patch = "\n".join(parts)
open("staging/p11b15_proposed.patch", "w", encoding="utf-8").write(patch)

out.append("")
out.append("=" * 92)
out.append("PATCH EMITTED (inert text, nothing applied)")
out.append("=" * 92)
out.append("  staging/p11b15_proposed.patch  %d bytes, %d lines"
           % (len(patch.encode("utf-8")), len(patch.splitlines())))
ins = sum(1 for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
dele = sum(1 for l in patch.splitlines() if l.startswith("-") and not l.startswith("---"))
out.append("  +%d / -%d across 3 files" % (ins, dele))
out.append("  slot-lock test removal is NOT in this patch (needs separate approval)")

out.append("")
out.append("  shared .git/index before: %s" % shared_before)
out.append("  shared .git/index after : %s"
           % hashlib.sha256(open(".git/index", "rb").read()).hexdigest())
if os.path.exists(PRIV):
    os.remove(PRIV)

text = "\n".join(out)
open("staging/p11b15_patch_plan.txt", "w", encoding="utf-8").write(text)
print(text)
