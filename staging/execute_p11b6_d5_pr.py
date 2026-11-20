"""PHASE 11B-A6 — build, verify and push the isolated D5' commit, then open the PR.

AUTHORIZED by the Phase 11B-A5/A6 brief. Aborts rather than proceeding on any mismatch.

WHY PLUMBING INSTEAD OF add/checkout/commit
-------------------------------------------
Two constraints rule out the ordinary porcelain route:

1. `.git/index` already holds 22 staged files from concurrent workstreams, so `git add`
   would fold them into the commit. Everything here uses a PRIVATE index via
   `GIT_INDEX_FILE`, and the shared index is sha256-checked before and after.
2. `git checkout -b` would move `HEAD` and interact with the working tree, which carries
   120 modified tracked files belonging to other workstreams.

So the commit is assembled with `write-tree` / `commit-tree` / `update-ref`: the branch ref
is created pointing at a new commit without ever switching to it. The checked-out branch
stays `main`, the working tree is not written, and nothing of anyone else's moves.

GUARDS (each aborts)
  * parent must be the approved baseline 074b772a
  * the commit must contain EXACTLY the five approved paths
  * the workflow diff must contain AERORA_TEST_REDIS_URL and must NOT contain
    provisioning-and-isolation
  * whitespace/conflict check must be clean
  * the shared index sha256 must be unchanged

Does not merge. Does not touch production, infrastructure, exchanges or live trading.
"""
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/01-pr-check.yml"
BRANCH = "ci/d5-prime-real-redis"
APPROVED_PARENT = "074b772ab16cece62cbfca0f2b78497be2ae56d5"

WHOLE_FILES = [
    "tests/real_redis_gate.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
]
EXPECTED = sorted(WHOLE_FILES + [WORKFLOW])

MESSAGE = """D5': point the real-Redis tests at the CI redis service

The three real-Redis test files could never run in CI. They gated on
`AERORA_TEST_REDIS_URL or REDIS_URL` and tested only the shape of the string, and
`tests/conftest.py` blanks `REDIS_URL` unconditionally at import so the 11,000+ test
lane cannot be re-pointed at a live cache. The `redis:7-alpine` service in the
`unit-tests` job was therefore healthy, mapped and health-checked, and exported under a
name the gate could never read: the tests skipped on every PR, one of them since the day
it was written.

The workflow now also exports `AERORA_TEST_REDIS_URL` (database index 15), the second
deliberate name -- the same pattern `AERORA_TEST_DATABASE_URL` already uses for
PostgreSQL. `REDIS_URL` is retained unchanged, because other tests assert on how the
application behaves when it is present.

`tests/real_redis_gate.py` now owns the decision for every real-Redis test, and keeps
three states apart that were previously two:

    opt-in unset            SKIP   the only legitimate skip
    opt-in set, no server   FAIL   you asked for a real Redis; there isn't one
    opt-in set, reachable   RUN

A non-loopback host is refused unless `AERORA_TEST_REDIS_ALLOW_REMOTE=1`, and no URL,
host or credential appears in any message the gate produces.

The webhook file additionally stopped converting a cache-manager fallback into a skip.
Once the gate has confirmed the endpoint answers PING, a `MockRedisClient` or `None` from
`_get_active_cache()` is a wiring fault rather than an absent server, and it now fails --
a skip there would have shown six tests green against a broken service while the three
quota tests failed on the same one.

No application code changes. The reachable-Redis path has never executed anywhere, which
is what this PR exists to establish.
"""


def git(*args, index=None, check=True):
    env = dict(os.environ)
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    else:
        env.pop("GIT_INDEX_FILE", None)
    r = subprocess.run(["git", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        abort(f"git {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r


def shared_index_digest():
    p = ROOT / ".git" / "index"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(absent)"


def abort(msg):
    print("\n" + "!" * 78)
    print("ABORTED — nothing was pushed.")
    print(msg)
    print("!" * 78)
    sys.exit(1)


print("=" * 78)
print("PHASE 11B-A6 — isolated D5' commit, push and PR")
print("=" * 78)

idx_before = shared_index_digest()
staged_before = sorted(l for l in git("diff", "--cached", "--name-only").stdout.split() if l)
head = git("rev-parse", "HEAD").stdout.strip()
print(f"HEAD                  : {head}")
print(f"approved parent       : {APPROVED_PARENT}")
if head != APPROVED_PARENT:
    abort(f"HEAD is not the approved baseline.")
print(f"shared index sha256   : {idx_before[:16]}...  ({len(staged_before)} staged)")

if git("rev-parse", "--verify", f"refs/heads/{BRANCH}", check=False).returncode == 0:
    abort(f"local branch {BRANCH} already exists; refusing to overwrite it.")
if git("ls-remote", "--heads", "origin", BRANCH).stdout.strip():
    abort(f"remote branch {BRANCH} already exists; refusing to overwrite it.")
print(f"branch {BRANCH} : absent locally and remotely (clean)")

# ── split the workflow diff; keep hunk 1 only ───────────────────────────────
full = git("diff", "-U3", "--", WORKFLOW).stdout
lines = full.splitlines(keepends=True)
hs = [i for i, l in enumerate(lines) if l.startswith("@@")]
if len(hs) != 2:
    abort(f"expected 2 hunks in {WORKFLOW}, found {len(hs)}")
h1 = "".join(lines[hs[0]:hs[1]])
h2 = "".join(lines[hs[1]:])
if "AERORA_TEST_REDIS_URL" not in h1 or "provisioning-and-isolation" in h1:
    abort("hunk 1 is not cleanly D5'")
if "provisioning-and-isolation" not in h2:
    abort("hunk 2 is not the other workstream's job; classification wrong")
print("workflow hunks        : 1 = D5' (include), 2 = provisioning-and-isolation (exclude)")

with tempfile.TemporaryDirectory() as td:
    priv = pathlib.Path(td) / "d5_index"
    patch = pathlib.Path(td) / "h1.patch"
    patch.write_text("".join(lines[:hs[0]]) + h1, encoding="utf-8", newline="")

    git("read-tree", "HEAD", index=priv)
    git("add", "--", *WHOLE_FILES, index=priv)
    if git("apply", "--cached", "--whitespace=nowarn", str(patch),
           index=priv, check=False).returncode != 0:
        abort("the D5' workflow hunk did not apply to the private index")

    staged = sorted(l for l in git("diff", "--cached", "--name-only",
                                   index=priv).stdout.split() if l)
    print("-" * 78)
    print(f"prospective commit    : {len(staged)} files")
    for f in staged:
        print(f"   {f}")
    if staged != EXPECTED:
        abort(f"contents differ from the approved manifest.\n  got      {staged}\n"
              f"  expected {EXPECTED}")

    if git("diff", "--cached", "--check", index=priv, check=False).returncode != 0:
        abort("whitespace/conflict check failed")
    print("whitespace check      : CLEAN")

    wf = git("diff", "--cached", "--", WORKFLOW, index=priv).stdout
    if "AERORA_TEST_REDIS_URL" not in wf:
        abort("the staged workflow diff lacks AERORA_TEST_REDIS_URL")
    if "provisioning-and-isolation" in wf:
        abort("the staged workflow diff CONTAINS the other workstream's job")
    print("workflow isolation    : D5' present, other workstream absent")

    tree = git("write-tree", index=priv).stdout.strip()

print("-" * 78)
commit = git("commit-tree", tree, "-p", head, "-m", MESSAGE).stdout.strip()
print(f"tree                  : {tree}")
print(f"commit                : {commit}")

parent = git("rev-parse", f"{commit}^").stdout.strip()
print(f"commit parent         : {parent}")
if parent != APPROVED_PARENT:
    abort("the new commit's parent is not the approved baseline")

changed = sorted(l for l in git("diff", "--name-only", head, commit).stdout.split() if l)
print(f"commit vs HEAD        : {len(changed)} files")
if changed != EXPECTED:
    abort(f"the commit changes unexpected paths: {changed}")
print("   exactly the approved five paths")

idx_mid = shared_index_digest()
if idx_mid != idx_before:
    abort("the shared index changed while building the commit")
print(f"shared index          : unchanged ({idx_mid[:16]}...)")

# ── create the branch ref WITHOUT switching to it ───────────────────────────
git("update-ref", f"refs/heads/{BRANCH}", commit)
print(f"branch ref created    : refs/heads/{BRANCH} -> {commit[:12]}")
print(f"checked-out branch    : {git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()} "
      f"(unchanged — never switched)")

# ── push ────────────────────────────────────────────────────────────────────
print("-" * 78)
print("pushing ...")
r = git("push", "-u", "origin", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}", check=False)
print(r.stdout + r.stderr)
if r.returncode != 0:
    abort("push failed")
print("pushed.")

# ── open the PR ─────────────────────────────────────────────────────────────
print("-" * 78)
print("opening the pull request ...")
pr = subprocess.run(
    ["gh", "pr", "create", "--base", "main", "--head", BRANCH,
     "--title", "D5': point the real-Redis tests at the CI redis service",
     "--body", MESSAGE],
    cwd=ROOT, capture_output=True, text=True,
)
print(pr.stdout + pr.stderr)

idx_after = shared_index_digest()
staged_after = sorted(l for l in git("diff", "--cached", "--name-only").stdout.split() if l)
print("-" * 78)
print(f"shared index sha256 after : {idx_after[:16]}...  "
      f"unchanged={idx_after == idx_before}")
print(f"staged by others          : {len(staged_after)} (was {len(staged_before)})")
print(f"checked-out branch        : {git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()}")
print("=" * 78)
print(f"COMMIT {commit}")
print(f"PARENT {parent}")
sys.exit(0 if pr.returncode == 0 else 2)
