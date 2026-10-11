"""Phase 11B-A7 Phase 3 — build the D5' commit in a PRIVATE index, verify, then stop.

WHY A PRIVATE INDEX
-------------------
The shared `.git/index` holds 22 staged entries belonging to other workstreams. Any
ordinary `git add` would rewrite it. So every index operation here runs with
`GIT_INDEX_FILE` pointed at a scratch file, and the index is seeded with
`git read-tree HEAD` rather than copied from the shared one — which is what guarantees the
other workstreams' staged entries cannot leak into this commit.

`git commit-tree` then creates the commit OBJECT without moving `HEAD`, without touching
the working tree and without writing the shared index. A branch ref is created separately,
pointing at that object, so nothing is ever checked out.

`git stash`, `git reset`, `git clean` and bare `git add` are never used.

STAGES
------
  prepare  build the private index + tree, verify, print the plan. Creates NO ref.
  commit   additionally create the commit object and the branch ref.

Run `prepare` first and read the output. Nothing is published either way — pushing is a
separate, explicit step.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile

WORKFLOW = ".github/workflows/01-pr-check.yml"
TEST_FILES = [
    "tests/real_redis_gate.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
]
MANIFEST = [WORKFLOW, *TEST_FILES]
BRANCH = "ci/d5-real-redis-switch"
SWITCH = "AERORA_TEST_REDIS_URL"
OTHER_JOB = "provisioning-and-isolation"

COMMIT_MESSAGE = """Let the CI Redis service reach the tests that exist to use it

`tests/conftest.py` blanks REDIS_URL at import so the 11,000-test lane cannot be
re-pointed at a live cache. That is correct and is left alone. Its consequence was
that the healthy `redis:7-alpine` service in the `unit-tests` job could never switch
on the three real-Redis test files: they read REDIS_URL, found '', and skipped on
every PR.

AERORA_TEST_REDIS_URL is the second, deliberate name -- the same pattern
AERORA_TEST_DATABASE_URL already uses for PostgreSQL -- and conftest does not touch
it, so it survives to the shared gate in tests/real_redis_gate.py.

The gate keeps three outcomes apart: opt-in unset SKIPS, configured-but-unreachable
FAILS (a dead Redis in CI must not look like a green run), reachable RUNS. Verified
locally for the first two; the third is what this change exists to exercise.

Scope note: this does NOT close QUOTA-01. The metered concurrency test drives a raw
Redis client and mirrors reserve_quota's increment-first-revert inline; it does not
call SubscriptionEngine.reserve_quota, so the production reservation path remains
unverified against a real server. Recorded in
staging/PHASE11B_A7_D5_CI_VERIFICATION.md.

No application code, no production path, and no other workstream's changes are
included. Live trading remains OFF / NOT CERTIFIED.
"""


def git(args: list[str], index: str | None = None, text: bool = True):
    env = dict(os.environ)
    if index:
        env["GIT_INDEX_FILE"] = index
    return subprocess.run(
        ["git", *args], capture_output=True, text=text,
        encoding="utf-8" if text else None, errors="replace" if text else None, env=env,
    )


def shared_index_digest() -> str:
    with open(".git/index", "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _parse_hunks(diff_text: str) -> list[dict]:
    """Hunks as `{old_start, lines:[(kind, text)]}` with kind in ' ', '+', '-'."""
    hunks: list[dict] = []
    cur: dict | None = None
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            # @@ -183,10 +183,37 @@ trailing
            spec = line.split("@@")[1].strip()
            old = spec.split()[0]            # -183,10
            old_start = int(old[1:].split(",")[0])
            cur = {"old_start": old_start, "lines": []}
            hunks.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line[:1] in (" ", "+", "-"):
            cur["lines"].append((line[0], line[1:]))
        elif line == "":
            cur["lines"].append((" ", ""))
    return hunks


def isolated_workflow_bytes() -> bytes:
    """HEAD's workflow with ONLY the D5' hunk applied.

    POSITIONAL APPLICATION, WITH EVERY CONTEXT LINE VERIFIED.

    A first version of this function tried to reach the same result by taking the WORKING
    COPY and deleting the other workstream's added lines, matching them by content. That
    was wrong and the verification gate caught it: lines like `runs-on: ubuntu-latest`,
    `env:`, `run: |` and `- name: Checkout code` occur in several jobs, so "delete the
    first occurrence" removed them from PRE-EXISTING jobs and left the new job's copies
    in place. The reconstructed file lost the other job's name line — which made a
    substring check for it pass — while keeping its body.

    So this applies the D5' hunk to HEAD's blob by POSITION instead, and asserts that
    every context and deletion line in the hunk matches HEAD exactly before emitting
    anything. A mismatch means the diff no longer describes HEAD and the plan is stale.
    """
    head_lines = git(["show", f"HEAD:{WORKFLOW}"]).stdout.split("\n")
    diff_text = git(["diff", "-U3", "--", WORKFLOW]).stdout
    hunks = _parse_hunks(diff_text)

    d5 = [h for h in hunks if any(k == "+" and SWITCH in t for k, t in h["lines"])]
    other = [h for h in hunks if any(k == "+" and OTHER_JOB in t for k, t in h["lines"])]

    if len(d5) != 1:
        raise SystemExit(f"expected exactly 1 D5' hunk, found {len(d5)}; stopping")
    if len(other) != 1:
        raise SystemExit(f"expected exactly 1 other-workstream hunk, found {len(other)}")
    if d5[0] is other[0]:
        raise SystemExit("the two changes share a hunk; manual split required. Stopping.")
    if len(hunks) != 2:
        raise SystemExit(f"expected 2 hunks, found {len(hunks)}; plan is stale. Stopping.")

    hunk = d5[0]
    out = list(head_lines[: hunk["old_start"] - 1])
    cursor = hunk["old_start"] - 1

    for kind, text in hunk["lines"]:
        if kind == " ":
            if cursor >= len(head_lines) or head_lines[cursor] != text:
                raise SystemExit(
                    f"context mismatch at HEAD line {cursor + 1}: "
                    f"expected {text!r}, found "
                    f"{head_lines[cursor] if cursor < len(head_lines) else '<EOF>'!r}. "
                    "The diff no longer describes HEAD; stopping."
                )
            out.append(text)
            cursor += 1
        elif kind == "-":
            if head_lines[cursor] != text:
                raise SystemExit(f"deletion mismatch at HEAD line {cursor + 1}; stopping")
            cursor += 1
        else:  # '+'
            out.append(text)

    out.extend(head_lines[cursor:])
    result = "\n".join(out)

    if SWITCH not in result:
        raise SystemExit("the D5' switch is absent from the result; stopping")
    if OTHER_JOB in result:
        raise SystemExit(f"{OTHER_JOB} leaked into the result; stopping")

    return result.encode("utf-8")


def d5_added_lines() -> list[str]:
    """The exact added lines of the D5' hunk, for set-equality verification."""
    diff_text = git(["diff", "-U3", "--", WORKFLOW]).stdout
    for hunk in _parse_hunks(diff_text):
        added = [t for k, t in hunk["lines"] if k == "+"]
        if any(SWITCH in t for t in added):
            return added
    raise SystemExit("no D5' hunk found; stopping")


def main() -> int:
    stage = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    if stage not in {"prepare", "commit"}:
        print("usage: a7_prepare_commit.py [prepare|commit]")
        return 2

    before = shared_index_digest()
    head = git(["rev-parse", "HEAD"]).stdout.strip()
    print(f"shared .git/index SHA256 (before) : {before}")
    print(f"parent (HEAD)                     : {head}\n")

    scratch = os.path.join(tempfile.gettempdir(), "a7_private_index")
    if os.path.exists(scratch):
        os.remove(scratch)

    # Seed from HEAD, NOT from the shared index — this is the isolation guarantee.
    res = git(["read-tree", head], index=scratch)
    if res.returncode != 0:
        print("read-tree failed:", res.stderr)
        return 1

    for path in TEST_FILES:
        res = git(["add", "--", path], index=scratch)
        if res.returncode != 0:
            print(f"add failed for {path}:", res.stderr)
            return 1

    # The workflow goes in as a hand-built blob so only the D5' hunk is included.
    blob_bytes = isolated_workflow_bytes()
    proc = subprocess.run(
        ["git", "hash-object", "-w", "--stdin", "--path", WORKFLOW],
        input=blob_bytes, capture_output=True,
    )
    if proc.returncode != 0:
        print("hash-object failed:", proc.stderr.decode(errors="replace"))
        return 1
    blob = proc.stdout.decode().strip()
    res = git(
        ["update-index", "--cacheinfo", f"100644,{blob},{WORKFLOW}"], index=scratch
    )
    if res.returncode != 0:
        print("update-index failed:", res.stderr)
        return 1

    tree = git(["write-tree"], index=scratch).stdout.strip()
    print(f"prospective tree                  : {tree}\n")

    # ── verification gates ────────────────────────────────────────────────
    changed = [
        l.split("\t", 1)[1]
        for l in git(["diff-tree", "-r", "--name-status", head, tree]).stdout.splitlines()
        if "\t" in l
    ]
    print("paths changed vs parent:")
    for path in changed:
        print(f"  {path}")
    print()

    failures = []
    if sorted(changed) != sorted(MANIFEST):
        failures.append(f"tree changes {sorted(changed)} != manifest {sorted(MANIFEST)}")

    wf_diff = git(["diff-tree", "-p", head, tree, "--", WORKFLOW]).stdout
    added = [
        l[1:] for l in wf_diff.splitlines()
        if l.startswith("+") and not l.startswith("+++")
    ]
    functional = [l for l in added if l.strip() and not l.strip().startswith("#")]
    removed = [
        l[1:] for l in wf_diff.splitlines()
        if l.startswith("-") and not l.startswith("---")
    ]

    # THE DECISIVE GATE: the added lines must equal the D5' hunk's added lines EXACTLY.
    #
    # A count-only check is not enough and a substring check for the other job's NAME is
    # actively misleading — the first attempt at this function produced a tree whose name
    # line was absent while its whole body was present, so `OTHER_JOB in wf_diff` was
    # False and the change was still wrong. Set equality against the reviewed hunk is the
    # only check that cannot be satisfied by the wrong content.
    expected_added = d5_added_lines()
    if added != expected_added:
        failures.append(
            f"workflow added lines ({len(added)}) != D5' hunk added lines "
            f"({len(expected_added)}) — the tree does not match the reviewed hunk"
        )
    if len(functional) != 1 or SWITCH not in functional[0]:
        failures.append(f"workflow functional added lines: {functional}")
    if removed:
        failures.append(f"workflow removed {len(removed)} line(s); D5' is additive only")
    if OTHER_JOB in wf_diff:
        failures.append(f"{OTHER_JOB} present in the staged workflow diff")
    # Distinctive body lines of the other job, independent of its name.
    for marker in ("postgres:17-alpine", "provision_database.py", "verify_schema_drift"):
        if marker in wf_diff:
            failures.append(f"other-workstream marker {marker!r} present in staged diff")

    print(f"workflow added lines              : {len(added)} ({len(functional)} functional)")
    print(f"workflow removed lines            : {len(removed)} (expected 0)")
    print(f"matches reviewed D5' hunk exactly : {added == expected_added}")
    for line in functional:
        print(f"  FUNCTIONAL > {line}")
    print(f"'{OTHER_JOB}' in staged diff       : {OTHER_JOB in wf_diff}")
    print()

    after = shared_index_digest()
    print(f"shared .git/index SHA256 (after)  : {after}")
    if after != before:
        failures.append("THE SHARED INDEX CHANGED")
    staged_now = [
        l for l in git(["diff", "--cached", "--name-only"]).stdout.splitlines() if l.strip()
    ]
    print(f"shared staged entries             : {len(staged_now)} (expected 22)")
    if len(staged_now) != 22:
        failures.append(f"shared staged entry count changed to {len(staged_now)}")
    print()

    if failures:
        print("=== ISOLATION NOT PROVEN — STOPPING ===")
        for f in failures:
            print(f"  FAIL  {f}")
        return 1

    print("=== ISOLATION PROVEN ===")
    print("  exactly the 5 manifest paths; 1 functional workflow line; other job absent;")
    print("  shared index byte-identical; 22 staged entries untouched.")

    if stage == "prepare":
        print("\n(prepare only — no commit object, no ref created)")
        return 0

    commit = subprocess.run(
        ["git", "commit-tree", tree, "-p", head],
        input=COMMIT_MESSAGE, capture_output=True, text=True, encoding="utf-8",
    )
    if commit.returncode != 0:
        print("commit-tree failed:", commit.stderr)
        return 1
    sha = commit.stdout.strip()
    print(f"\ncommit object created             : {sha}")

    res = git(["branch", BRANCH, sha])
    if res.returncode != 0:
        print(f"branch creation failed: {res.stderr}")
        return 1
    print(f"branch created                    : {BRANCH} -> {sha}")

    final = shared_index_digest()
    print(f"shared .git/index SHA256 (final)  : {final}")
    if final != before:
        print("  FAIL  the shared index changed during commit")
        return 1
    print(f"HEAD still                        : {git(['rev-parse','HEAD']).stdout.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
