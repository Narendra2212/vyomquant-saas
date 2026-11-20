"""A17 §3 + §4: slot-guard integration trace, and three-path patch verification.

Read-only. Private index only; no objects written to the shared ODB; no shared-index writes.
"""
import hashlib
import os
import re
import subprocess
from pathlib import Path

PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
PRIV = os.path.abspath("staging/p11b17_private.index")
WF = ".github/workflows/01-pr-check.yml"
WEBHOOK = "tests/test_webhook_duplicate_delivery_real_redis.py"
METERED = "tests/test_quota01_metered_reservation_is_hard.py"
SLOT = "tests/test_quota01_exchange_slot_is_atomic.py"
SD = "backend_app/core/subscription_dependencies.py"
EX = "backend_app/routers/exchange.py"

out = []


def sh(args, stdin=None, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(args, capture_output=True, input=stdin, env=e)


def txt(args):
    return sh(args).stdout.decode("utf-8", errors="replace")


def digest():
    lines = sorted(txt(["git", "ls-files", "--stage"]).splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return h.hexdigest()


con0 = digest()

# =====================================================================  claim check
out.append("=" * 96)
out.append("0. ARE THE QUOTED WORKTREE BLOB IDS STORED OBJECTS OR COMPUTED DIGESTS?")
out.append("=" * 96)
out.append("  (claim raised by the concurrent A17 report; checking it directly)")
for p in (WF, WEBHOOK, METERED):
    computed = txt(["git", "hash-object", p]).strip()
    exists = sh(["git", "cat-file", "-e", computed]).returncode == 0
    out.append("    %-52s %s  in-ODB=%s" % (p, computed[:12], exists))
pre = txt(["git", "rev-parse", "%s:%s" % (PR6, WF)]).strip()
out.append("    %-52s %s  in-ODB=%s   <- patch PRE-image"
           % ("PR#6:" + WF, pre[:12], sh(["git", "cat-file", "-e", pre]).returncode == 0))
out.append("")
out.append("  Reading: TRUE but not consequential. `git hash-object` computes without writing,")
out.append("  which is the correct non-mutating way to fingerprint a worktree file for drift")
out.append("  detection. Patch applicability depends on the PRE-image, which IS a stored object.")

# =====================================================================  section 3
out.append("")
out.append("=" * 96)
out.append("3. SLOT-GUARD INTEGRATION TRACE")
out.append("=" * 96)

sd_head = txt(["git", "show", "HEAD:" + SD])
sd_work = Path(SD).read_text(encoding="utf-8", errors="replace")
ex_head = txt(["git", "show", "HEAD:" + EX])
ex_index = sh(["git", "show", ":" + EX]).stdout.decode("utf-8", errors="replace")
ex_work = Path(EX).read_text(encoding="utf-8", errors="replace")

SYMS = ["_slot_lock_key", "_acquire_slot_lock", "_release_slot_lock",
        "exchange_connection_slot_guard", "check_exchange_connection_slot"]
out.append("")
out.append("  symbol presence  (HEAD = what CI runs today)")
out.append("    %-34s %-7s %-9s | %-7s %-7s %-9s" %
           ("symbol", "SD@HEAD", "SD@work", "EX@HEAD", "EX@index", "EX@work"))
for s in SYMS:
    pat = r"\b" + re.escape(s) + r"\b"
    out.append("    %-34s %-7s %-9s | %-7s %-7s %-9s" % (
        s,
        "yes" if re.search(pat, sd_head) else "-",
        "yes" if re.search(pat, sd_work) else "-",
        "yes" if re.search(pat, ex_head) else "-",
        "yes" if re.search(pat, ex_index) else "-",
        "yes" if re.search(pat, ex_work) else "-"))

# the store_keys body at each of the three states
out.append("")
out.append("  store_keys: which limit call does each state use?")


def store_keys_calls(src, label):
    m = re.search(r"async def store_keys\(.*?(?=\n@router\.|\nasync def |\Z)", src, re.S)
    if not m:
        out.append("    %-10s store_keys NOT FOUND" % label)
        return
    body = m.group(0)
    out.append("    %-10s lines=%-4d  check_exchange_connection_slot=%s  guard=%s  vault.store_exchange_keys=%s"
               % (label, len(body.splitlines()),
                  "yes" if "check_exchange_connection_slot" in body else "no",
                  "yes" if "exchange_connection_slot_guard" in body else "no",
                  "yes" if "vault.store_exchange_keys" in body else "no"))
    if "exchange_connection_slot_guard" in body:
        for i, l in enumerate(body.splitlines()):
            if "exchange_connection_slot_guard" in l or "vault.store_exchange_keys" in l:
                out.append("        %s" % l.strip()[:100])


for src, label in ((ex_head, "HEAD"), (ex_index, "index"), (ex_work, "worktree")):
    store_keys_calls(src, label)

# which staged (other-workstream) markers share exchange.py
out.append("")
out.append("  other workstreams sharing exchange.py (markers in the STAGED version):")
for tag in sorted(set(re.findall(r"\b(?:AUTH|CONN|QUOTA|TENANT|BUG|API)-\d+", ex_index))):
    out.append("    %-10s x%d" % (tag, len(re.findall(re.escape(tag), ex_index))))
out.append("  markers added only in the WORKTREE (unstaged, i.e. the guard workstream):")
only_work = set(re.findall(r"\b(?:AUTH|CONN|QUOTA|TENANT|BUG|API)-\d+", ex_work)) - \
    set(re.findall(r"\b(?:AUTH|CONN|QUOTA|TENANT|BUG|API)-\d+", ex_index))
out.append("    %s" % (sorted(only_work) or "none"))

# apply tests for the two production files, onto HEAD
out.append("")
out.append("  does each production file's unstaged patch apply onto HEAD?  (private index)")
for path in (SD, EX):
    if os.path.exists(PRIV):
        os.remove(PRIV)
    env = {"GIT_INDEX_FILE": PRIV}
    sh(["git", "read-tree", "HEAD"], env=env)
    patch = sh(["git", "diff", "--", path]).stdout
    r = sh(["git", "apply", "--cached", "--recount", "--check", "-"], stdin=patch, env=env)
    out.append("    %-52s rc=%d  %s" % (path, r.returncode,
                                        "APPLIES" if r.returncode == 0 else "REJECTED"))
    for l in r.stderr.decode(errors="replace").splitlines():
        out.append("        %s" % l)
    if os.path.exists(PRIV):
        os.remove(PRIV)

# slot test expectations
out.append("")
slot = Path(SLOT).read_text(encoding="utf-8", errors="replace")
out.append("  %s: %d tests; symbols it requires:" % (SLOT, len(re.findall(r"^\s*(?:async\s+)?def test_", slot, re.M))))
for s in SYMS:
    n = len(re.findall(r"sd\." + re.escape(s) + r"\b", slot)) + len(re.findall(re.escape(s), slot))
    out.append("    %-34s referenced %d time(s)" % (s, n))

# =====================================================================  section 4
out.append("")
out.append("=" * 96)
out.append("4. THREE-PATH PATCH MANIFEST, RECOMPUTED AND VERIFIED")
out.append("=" * 96)
patch_bytes = Path("staging/p11b15_proposed_fixed.patch").read_bytes()
pairs = re.findall(rb"^diff --git a/(\S+)\n(?:.*\n)*?index ([0-9a-f]+)\.\.([0-9a-f]+)",
                   patch_bytes, re.M)
out.append("")
out.append("  %-52s %-10s %-10s %s" % ("path", "patch-pre", "PR#6", "match"))
for pth, a, b in pairs:
    p = pth.decode()
    real = txt(["git", "rev-parse", "%s:%s" % (PR6, p)]).strip()
    out.append("  %-52s %-10s %-10s %s"
               % (p, a.decode(), real[:8], real.startswith(a.decode())))

if os.path.exists(PRIV):
    os.remove(PRIV)
env = {"GIT_INDEX_FILE": PRIV}
sh(["git", "read-tree", PR6], env=env)
r = sh(["git", "apply", "--cached", "--check", "-"], stdin=patch_bytes, env=env)
out.append("")
out.append("  git apply --cached --check (private index = current PR #6 head): rc=%d  %s"
           % (r.returncode, "APPLIES CLEANLY" if r.returncode == 0 else "REJECTED"))
for l in r.stderr.decode(errors="replace").splitlines():
    out.append("    %s" % l)
if os.path.exists(PRIV):
    os.remove(PRIV)

# workflow: exactly the two DB selections, diagnostics preserved
out.append("")
out.append("  WORKFLOW: what the patch actually changes")
wf_sec = patch_bytes.decode("utf-8", errors="replace")
start = wf_sec.index("diff --git a/%s" % WF)
nxt = wf_sec.find("diff --git a/tests", start)
wf_sec = wf_sec[start:nxt if nxt > 0 else len(wf_sec)]
adds = [l for l in wf_sec.splitlines() if l.startswith("+") and not l.startswith("+++")]
dels = [l for l in wf_sec.splitlines() if l.startswith("-") and not l.startswith("---")]
out.append("    hunks: %s" % re.findall(r"^@@[^\n]*", wf_sec, re.M))
out.append("    +%d / -%d" % (len(adds), len(dels)))
for l in dels + adds:
    out.append("      %s" % l.strip()[:92])
wf6 = txt(["git", "show", "%s:%s" % (PR6, WF)])
out.append("    diagnostics job in PR#6 base      : %s"
           % bool(re.search(r"^\s{2}d5-redis-diagnostics:", wf6, re.M)))
out.append("    patch touches the diagnostics job?: %s"
           % any("d5-redis-diagnostics" in l for l in adds + dels))
out.append("    patch touches provisioning job?   : %s"
           % any("provisioning-and-isolation" in l for l in adds + dels))

# tests preserved, and no skip conversions
out.append("")
out.append("  TESTS: preservation and skip/fail disposition")
for path, base_ref in ((WEBHOOK, PR6), (METERED, PR6)):
    base = txt(["git", "show", "%s:%s" % (base_ref, path)])
    new = Path(path).read_text(encoding="utf-8", errors="replace")
    tb = re.findall(r"^\s*(?:async\s+)?def (test_\w+)", base, re.M)
    tn = re.findall(r"^\s*(?:async\s+)?def (test_\w+)", new, re.M)
    out.append("")
    out.append("    %s" % path)
    out.append("      tests: base=%d  new=%d  removed=%s  added=%s"
               % (len(tb), len(tn), [t for t in tb if t not in tn] or "none",
                  [t for t in tn if t not in tb] or "none"))
    out.append("      pytest.skip( : base=%d  new=%d" % (base.count("pytest.skip("), new.count("pytest.skip(")))
    out.append("      pytest.fail( : base=%d  new=%d" % (base.count("pytest.fail("), new.count("pytest.fail(")))
    out.append("      require_real_redis() calls: base=%d new=%d"
               % (base.count("require_real_redis()"), new.count("require_real_redis()")))

con1 = digest()
out.append("")
out.append("=" * 96)
out.append("PRESERVATION")
out.append("=" * 96)
out.append("  index content digest before/after: %s / %s  %s"
           % (con0[:16], con1[:16], "SAME" if con0 == con1 else "CHANGED"))
out.append("  private index file removed: %s" % (not os.path.exists(PRIV)))

open("staging/p11b17_verify.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
