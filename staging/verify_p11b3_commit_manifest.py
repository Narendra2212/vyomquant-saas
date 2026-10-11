"""PHASE 11B-A3 — prove the D5' commit scope can be isolated. DRY RUN, no commit.

WHY A PRIVATE INDEX
-------------------
The brief's suggested check is `git add` the D5' paths then `git diff --cached`. That is
UNSAFE in this working tree: `.git/index` ALREADY HOLDS 22 STAGED FILES from concurrent
workstreams (tenant isolation, the security review, terraform, ADRs). Staging into it would
make `git diff --cached` show those 22 as well, and any later `git commit` would capture
them.

So this script builds the prospective commit in a PRIVATE index via `GIT_INDEX_FILE`,
pointed at a scratch file under TEMP. `.git/index` is never written, and the 22 staged files
stay exactly as they are -- asserted before and after by hashing `.git/index`.

It also proves the one hard part: `.github/workflows/01-pr-check.yml` carries TWO hunks, one
of which is another workstream's `provisioning-and-isolation` job. Only hunk 1 (D5') is
applied, and hunk 2 is shown to be absent from the result.

NOTHING IS COMMITTED. No branch is created. No remote is contacted.
"""
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/01-pr-check.yml"

WHOLE_FILES = [
    "tests/real_redis_gate.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
]


def git(*args, index=None, check=True, text=True):
    env = dict(os.environ)
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    else:
        env.pop("GIT_INDEX_FILE", None)
    r = subprocess.run(["git", *args], cwd=ROOT, env=env, capture_output=True, text=text)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r


def shared_index_digest():
    p = ROOT / ".git" / "index"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(absent)"


print("=" * 78)
print("PHASE 11B-A3 - D5' commit manifest isolation (DRY RUN, private index)")
print("=" * 78)

before_digest = shared_index_digest()
before_staged = sorted(
    l for l in git("diff", "--cached", "--name-only").stdout.splitlines() if l
)
print(f"shared .git/index sha256 BEFORE : {before_digest[:16]}...")
print(f"files already staged by others   : {len(before_staged)}")

# ── split the workflow diff into its hunks ──────────────────────────────────
full = git("diff", "-U3", "--", WORKFLOW).stdout
lines = full.splitlines(keepends=True)
hunk_starts = [i for i, l in enumerate(lines) if l.startswith("@@")]
if len(hunk_starts) != 2:
    raise SystemExit(f"expected exactly 2 hunks in {WORKFLOW}, found {len(hunk_starts)}")

header = lines[: hunk_starts[0]]
hunk1 = lines[hunk_starts[0] : hunk_starts[1]]
hunk2 = lines[hunk_starts[1] :]

print("-" * 78)
print("workflow diff hunks:")
print(f"   hunk 1 {lines[hunk_starts[0]].strip()[:60]}  ({len(hunk1)} diff lines)")
print(f"   hunk 2 {lines[hunk_starts[1]].strip()[:60]}  ({len(hunk2)} diff lines)")

h1_added = sum(1 for l in hunk1 if l.startswith("+") and not l.startswith("+++"))
h2_added = sum(1 for l in hunk2 if l.startswith("+") and not l.startswith("+++"))
h1_removed = sum(1 for l in hunk1 if l.startswith("-") and not l.startswith("---"))
h2_removed = sum(1 for l in hunk2 if l.startswith("-") and not l.startswith("---"))
print(f"   hunk 1: +{h1_added} -{h1_removed}   <- D5'")
print(f"   hunk 2: +{h2_added} -{h2_removed}   <- provisioning-and-isolation (OTHER WORKSTREAM)")

# Identify each hunk by content so the classification is evidence, not assumption.
h1_text = "".join(hunk1)
h2_text = "".join(hunk2)
print("-" * 78)
print("hunk identification by content:")
print(f"   hunk 1 mentions AERORA_TEST_REDIS_URL        : {'AERORA_TEST_REDIS_URL' in h1_text}")
print(f"   hunk 1 mentions provisioning-and-isolation   : {'provisioning-and-isolation' in h1_text}")
print(f"   hunk 2 mentions provisioning-and-isolation   : {'provisioning-and-isolation' in h2_text}")
print(f"   hunk 2 mentions AERORA_TEST_REDIS_URL        : {'AERORA_TEST_REDIS_URL' in h2_text}")

if "AERORA_TEST_REDIS_URL" not in h1_text or "provisioning-and-isolation" in h1_text:
    raise SystemExit("hunk 1 is not cleanly D5'; STOPPING rather than staging a mixed hunk")
if "provisioning-and-isolation" not in h2_text:
    raise SystemExit("hunk 2 is not the provisioning job; classification is wrong, STOPPING")

# ── build the prospective commit in a PRIVATE index ─────────────────────────
with tempfile.TemporaryDirectory() as td:
    priv = pathlib.Path(td) / "d5_index"
    patch = pathlib.Path(td) / "d5_workflow_hunk1.patch"
    patch.write_text("".join(header + hunk1), encoding="utf-8", newline="")

    print("-" * 78)
    print("building the prospective commit in a PRIVATE index (GIT_INDEX_FILE):")
    git("read-tree", "HEAD", index=priv)
    print("   private index seeded from HEAD")

    git("add", "--", *WHOLE_FILES, index=priv)
    print(f"   added {len(WHOLE_FILES)} whole new files")

    r = git("apply", "--cached", "--whitespace=nowarn", str(patch), index=priv, check=False)
    if r.returncode != 0:
        print("   HUNK 1 DID NOT APPLY:")
        print(r.stderr)
        raise SystemExit("STOP: the D5' workflow hunk could not be isolated safely")
    print("   applied workflow hunk 1 ONLY")

    staged = sorted(
        l for l in git("diff", "--cached", "--name-only", index=priv).stdout.splitlines() if l
    )
    print("-" * 78)
    print(f"PROSPECTIVE COMMIT CONTENTS ({len(staged)} files):")
    for f in staged:
        print(f"   {f}")

    print("-" * 78)
    print("diffstat:")
    for line in git("diff", "--cached", "--stat", index=priv).stdout.splitlines():
        print("   " + line)

    chk = git("diff", "--cached", "--check", index=priv, check=False)
    print("-" * 78)
    print(f"whitespace/conflict check: "
          f"{'CLEAN' if chk.returncode == 0 else 'ISSUES'}")
    if chk.stdout.strip():
        print(chk.stdout)

    # The decisive assertion: the other workstream's job must NOT be in the commit.
    staged_workflow = git("diff", "--cached", "--", WORKFLOW, index=priv).stdout
    print("-" * 78)
    print("OTHER WORKSTREAM PRESERVED?")
    has_d5 = "AERORA_TEST_REDIS_URL" in staged_workflow
    has_other = "provisioning-and-isolation" in staged_workflow
    print(f"   staged workflow diff contains AERORA_TEST_REDIS_URL      : {has_d5}")
    print(f"   staged workflow diff contains provisioning-and-isolation : {has_other}")
    unexpected = [f for f in staged if f not in WHOLE_FILES + [WORKFLOW]]
    print(f"   files outside the allowlist in the commit                : "
          f"{unexpected or 'none'}")

ok = has_d5 and not has_other and not unexpected and chk.returncode == 0

# ── the shared index must be untouched ──────────────────────────────────────
after_digest = shared_index_digest()
after_staged = sorted(
    l for l in git("diff", "--cached", "--name-only").stdout.splitlines() if l
)
print("-" * 78)
print(f"shared .git/index sha256 AFTER  : {after_digest[:16]}...")
print(f"unchanged                        : {before_digest == after_digest}")
print(f"files staged by others still     : {len(after_staged)} "
      f"(was {len(before_staged)})")

print("=" * 78)
if ok and before_digest == after_digest and before_staged == after_staged:
    print("MANIFEST VERIFIED: D5' isolates cleanly; the other workstream's hunk is")
    print("excluded; the shared index was not touched. NOTHING WAS COMMITTED.")
    sys.exit(0)
print("MANIFEST NOT VERIFIED - see above. NOTHING WAS COMMITTED.")
sys.exit(1)
