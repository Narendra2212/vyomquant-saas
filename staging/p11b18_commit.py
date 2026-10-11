"""A18 D1: build the commit with plumbing against a PRIVATE index. Does not push.

Deliberately does NOT touch:
  * the shared .git/index  (private index file only)
  * the checked-out `main` worktree
  * refs/heads/ci/d5-real-redis-quota-evidence -- that local branch is CHECKED OUT in a
    concurrent session's worktree (C:/Users/aa/AppData/Local/Temp/a18_pr6_wt). Updating it
    would desync their HEAD/index. The commit is pushed by SHA instead.
"""
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

PARENT = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
EXPECT_TREE = "844bf038a3a6fbd48af47638b46f1d4ff257d1fe"
PATHS = [".github/workflows/01-pr-check.yml",
         "tests/test_webhook_duplicate_delivery_real_redis.py",
         "tests/test_quota01_metered_reservation_is_hard.py"]
IDX = os.path.abspath("staging/p11b18_commit.index")
A17_DIGEST = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"

MSG = """D5': align the real-Redis gate database and add production-path quota coverage

Three paths, no production code.

1. .github/workflows/01-pr-check.yml  (+2/-2)
   AERORA_TEST_REDIS_URL: redis://localhost:6379/15 -> /0, in BOTH places that set it
   (the `unit-tests` job and the `d5-redis-diagnostics` job). The application cache is
   pinned to REDIS_DB_CACHE = 0 by backend_app/backend/redis_manager.get_redis_url(), which
   overrides whatever database a URL names, so a gate pointed at /15 PINGed a database the
   application never writes to. The diagnostics job itself is otherwise untouched and the
   provisioning-and-isolation job is not modified.

2. tests/test_webhook_duplicate_delivery_real_redis.py  (383 -> 646 lines)
   The test client now derives its URL from the application's own
   get_redis_url(REDIS_DB_CACHE) instead of reading AERORA_TEST_REDIS_URL, and a
   bidirectional sentinel probe proves the test client and the cache manager observe the
   same keyspace before any assertion runs. A mock or absent cache now FAILS rather than
   skipping. All six existing tests are preserved; there are no executable skips.

   This addresses the two failures in run 38047483583
   (test_the_lock_is_recorded_in_redis_under_the_documented_key and
   test_an_in_flight_delivery_blocks_a_concurrent_one), which were measuring an empty
   database rather than a dedup defect.

3. tests/test_quota01_metered_reservation_is_hard.py  (264 -> 530 lines)
   Adds the first real-Redis coverage of the PRODUCTION quota path; the pre-existing
   real-Redis test re-implemented reserve_quota's algorithm in the test body and so proved
   only that Redis INCR/DECR are atomic.
     * test_production_reserve_quota_admits_exactly_the_limit_against_real_redis
     * test_production_zero_allowance_writes_no_meter_against_real_redis
   All seven existing tests are preserved.

NOT included, deliberately:
  * tests/test_quota01_exchange_slot_is_atomic.py is retained unchanged. Its 21 failures are
    caused by the slot-guard implementation being absent from this branch, not by this
    change. Removal was not authorized.
  * No change to backend_app/core/subscription_dependencies.py or
    backend_app/routers/exchange.py. store_keys still wraps its vault write without the
    guard, so the QUOTA-01 read-then-write race remains live and exchange-slot concurrency
    and release safety remain UNPROVEN, pending a separately authorized workstream.
"""

out = []


def sh(args, stdin=None, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(args, capture_output=True, input=stdin, env=e)


def t(args, env=None):
    return sh(args, env=env).stdout.decode("utf-8", errors="replace")


def digest():
    lines = sorted(t(["git", "ls-files", "--stage"]).splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return h.hexdigest()


shutil.rmtree("staging/p11b18_objdir", ignore_errors=True)

# ---- immediate pre-commit rechecks (brief §4) -----------------------------------
out.append("=" * 98)
out.append("PRE-COMMIT RECHECKS (immediately before building)")
out.append("=" * 98)
remote = {}
for line in t(["git", "ls-remote", "--heads", "origin"]).splitlines():
    s, r = line.split("\t")
    remote[r] = s
pr6_now = remote.get("refs/heads/ci/d5-real-redis-quota-evidence", "")
out.append("  remote PR #6 head : %s  %s" % (pr6_now, "== expected parent" if pr6_now == PARENT else ">> MOVED"))
out.append("  checked-out branch: %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
d0 = digest()
out.append("  shared index digest: %s  %s" % (d0, "SAME" if d0 == A17_DIGEST else ">> CHANGED"))
out.append("  staged paths: %d" % len([l for l in t(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]))
for p in PATHS:
    out.append("  pre-image %-52s %s" % (p, t(["git", "rev-parse", "%s:%s" % (PARENT, p)]).strip()))

if pr6_now != PARENT or d0 != A17_DIGEST:
    out.append("")
    out.append("  >> ABORTING: a precondition moved. No commit built.")
    open("staging/p11b18_commit.txt", "w", encoding="utf-8").write("\n".join(out))
    raise SystemExit(2)

# ---- build the commit ------------------------------------------------------------
if os.path.exists(IDX):
    os.remove(IDX)
env = {"GIT_INDEX_FILE": IDX}

r = sh(["git", "read-tree", PARENT], env=env)
out.append("")
out.append("  read-tree %s -> rc=%d" % (PARENT[:12], r.returncode))

patch = Path("staging/p11b15_proposed_fixed.patch").read_bytes()
r = sh(["git", "apply", "--cached", "-"], stdin=patch, env=env)
out.append("  apply --cached (private index) -> rc=%d" % r.returncode)
for l in r.stderr.decode(errors="replace").splitlines():
    out.append("    %s" % l)
if r.returncode != 0:
    open("staging/p11b18_commit.txt", "w", encoding="utf-8").write("\n".join(out))
    raise SystemExit(3)

tree = t(["git", "write-tree"], env=env).strip()
out.append("  write-tree -> %s  %s" % (tree, "== preflight candidate" if tree == EXPECT_TREE else ">> UNEXPECTED"))
if tree != EXPECT_TREE:
    open("staging/p11b18_commit.txt", "w", encoding="utf-8").write("\n".join(out))
    raise SystemExit(4)

changed = sorted(l for l in t(["git", "diff", "--cached", "--name-only", PARENT], env=env).splitlines() if l.strip())
out.append("  paths changed vs parent (%d): %s" % (len(changed), changed))
if changed != sorted(PATHS):
    out.append("  >> ABORTING: candidate contains unexpected paths.")
    open("staging/p11b18_commit.txt", "w", encoding="utf-8").write("\n".join(out))
    raise SystemExit(5)

msg_file = os.path.abspath("staging/p11b18_msg.txt")
Path(msg_file).write_text(MSG, encoding="utf-8", newline="\n")
r = sh(["git", "commit-tree", tree, "-p", PARENT, "-F", msg_file])
commit = r.stdout.decode().strip()
out.append("")
out.append("  commit-tree -> %s (rc=%d)" % (commit, r.returncode))
for l in r.stderr.decode(errors="replace").splitlines():
    out.append("    %s" % l)

# ---- verify the commit object ----------------------------------------------------
out.append("")
out.append("=" * 98)
out.append("COMMIT VERIFICATION")
out.append("=" * 98)
out.append("  commit : %s" % commit)
out.append("  parent : %s" % t(["git", "rev-parse", commit + "^"]).strip())
out.append("  tree   : %s" % t(["git", "rev-parse", commit + "^{tree}"]).strip())
out.append("  author/date: %s" % t(["git", "log", "-1", "--format=%an <%ae>  %ad", commit]).strip())
out.append("  subject: %s" % t(["git", "log", "-1", "--format=%s", commit]).strip())
out.append("")
out.append("  numstat vs parent:")
for l in t(["git", "diff", "--numstat", PARENT, commit]).splitlines():
    out.append("    %s" % l)
out.append("  shortstat: %s" % t(["git", "diff", "--shortstat", PARENT, commit]).strip())
out.append("")
out.append("  workflow diff in the commit:")
for l in t(["git", "diff", PARENT, commit, "--", PATHS[0]]).splitlines():
    if l.startswith(("@@", "+", "-")) and not l.startswith(("+++", "---")):
        out.append("    %s" % l.strip()[:94])
out.append("")
out.append("  exchange-slot test in the commit vs parent: %s"
           % (t(["git", "rev-parse", "%s:tests/test_quota01_exchange_slot_is_atomic.py" % commit]).strip()
              == t(["git", "rev-parse", "%s:tests/test_quota01_exchange_slot_is_atomic.py" % PARENT]).strip()))

d1 = digest()
out.append("")
out.append("  shared index digest after build: %s  %s" % (d1, "SAME" if d1 == d0 else ">> CHANGED"))
out.append("  checked-out branch after build : %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
out.append("  local PR#6 branch ref untouched: %s"
           % (t(["git", "rev-parse", "refs/heads/ci/d5-real-redis-quota-evidence"]).strip() == PARENT))
if os.path.exists(IDX):
    os.remove(IDX)
out.append("  private index removed: %s" % (not os.path.exists(IDX)))

open("staging/p11b18_commit.txt", "w", encoding="utf-8").write("\n".join(out))
print("COMMIT=%s" % commit)
