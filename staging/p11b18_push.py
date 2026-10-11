"""A18 D2: push ONLY the built commit to PR #6's branch. Normal fast-forward, never forced."""
import hashlib
import os
import subprocess

COMMIT = "f5b99e57e1f7644bdbb10712e21f85e2ff236920"
PARENT = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
BRANCH = "ci/d5-real-redis-quota-evidence"
REF = "refs/heads/" + BRANCH
A17_DIGEST = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
PATHS = sorted([".github/workflows/01-pr-check.yml",
                "tests/test_webhook_duplicate_delivery_real_redis.py",
                "tests/test_quota01_metered_reservation_is_hard.py"])

out = []


def sh(args):
    return subprocess.run(args, capture_output=True)


def t(args):
    return sh(args).stdout.decode("utf-8", errors="replace")


def digest():
    lines = sorted(t(["git", "ls-files", "--stage"]).splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return h.hexdigest()


out.append("=" * 98)
out.append("PRE-PUSH RECHECKS")
out.append("=" * 98)
remote = {}
for line in t(["git", "ls-remote", "--heads", "origin"]).splitlines():
    s, r = line.split("\t")
    remote[r] = s
now = remote.get(REF, "")
out.append("  remote %s" % REF)
out.append("    now      : %s" % now)
out.append("    expected : %s  %s" % (PARENT, "MATCH" if now == PARENT else ">> MOVED"))

anc = sh(["git", "merge-base", "--is-ancestor", PARENT, COMMIT]).returncode == 0
out.append("  commit descends from the expected head : %s" % anc)
out.append("  fast-forward (remote head is ancestor) : %s"
           % (sh(["git", "merge-base", "--is-ancestor", now, COMMIT]).returncode == 0 if now else False))
changed = sorted(l for l in t(["git", "diff", "--name-only", PARENT, COMMIT]).splitlines() if l.strip())
out.append("  commit changes exactly the 3 approved paths : %s" % (changed == PATHS))
out.append("    %s" % changed)
d0 = digest()
out.append("  shared index digest : %s  %s" % (d0, "SAME" if d0 == A17_DIGEST else ">> CHANGED"))
out.append("  checked-out branch  : %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())

pr_json = t(["gh", "pr", "view", "6", "--json", "number,headRefName,headRefOid"])
out.append("  PR #6 association   : %s" % pr_json.strip())

ok = (now == PARENT and anc and changed == PATHS and d0 == A17_DIGEST
      and BRANCH in pr_json)
out.append("")
out.append("  ALL PRE-PUSH CONDITIONS PASS: %s" % ok)

if not ok:
    out.append("  >> ABORTING. No push attempted.")
    open("staging/p11b18_push.txt", "w", encoding="utf-8").write("\n".join(out))
    raise SystemExit(2)

out.append("")
out.append("=" * 98)
out.append("PUSH  (no --force, no --force-with-lease, single refspec)")
out.append("=" * 98)
cmd = ["git", "push", "origin", "%s:%s" % (COMMIT, REF)]
out.append("  $ %s" % " ".join(cmd))
r = sh(cmd)
out.append("  rc = %d" % r.returncode)
for stream, label in ((r.stdout, "stdout"), (r.stderr, "stderr")):
    for l in stream.decode("utf-8", errors="replace").splitlines():
        out.append("  %s: %s" % (label, l))

out.append("")
out.append("=" * 98)
out.append("POST-PUSH VERIFICATION FROM THE GIT HOST")
out.append("=" * 98)
remote2 = {}
for line in t(["git", "ls-remote", "--heads", "origin"]).splitlines():
    s, rf = line.split("\t")
    remote2[rf] = s
out.append("  ls-remote %s -> %s  %s" % (REF, remote2.get(REF), "== our commit"
                                         if remote2.get(REF) == COMMIT else ">> MISMATCH"))
out.append("  other D5 branches unchanged:")
for rf in ("refs/heads/ci/d5-prime-real-redis", "refs/heads/ci/d5-real-redis-switch",
           "refs/heads/main"):
    out.append("    %-48s %s  %s" % (rf, remote2.get(rf),
                                     "SAME" if remote2.get(rf) == remote.get(rf) else ">> CHANGED"))

out.append("")
out.append("  gh pr view 6 (host-side truth):")
out.append("    %s" % t(["gh", "pr", "view", "6", "--json",
                         "number,state,headRefName,headRefOid,mergedAt,mergeStateStatus"]).strip())
for n in (7, 8):
    out.append("  gh pr view %d: %s" % (n, t(["gh", "pr", "view", str(n), "--json",
                                              "number,state,headRefOid,mergedAt"]).strip()))

d1 = digest()
out.append("")
out.append("  shared index digest after push : %s  %s" % (d1, "SAME" if d1 == d0 else ">> CHANGED"))
out.append("  checked-out branch after push  : %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
out.append("  local main HEAD after push     : %s" % t(["git", "rev-parse", "HEAD"]).strip())
out.append("  local %s ref : %s" % (REF, t(["git", "rev-parse", REF]).strip()))

open("staging/p11b18_push.txt", "w", encoding="utf-8").write("\n".join(out))
print("push rc=%d ; remote head now %s" % (r.returncode, remote2.get(REF)))
