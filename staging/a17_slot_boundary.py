"""Phase 11B-A17. READ-ONLY. Phase 4 -- exchange-slot boundary and ownership.

Questions the brief sets:
  * are the guard AND its call site both present on the canonical branch (PR #6)?
  * why can the existing integration not be copied or cherry-picked?
  * which unrelated tenant-isolation/RLS changes share exchange.py?
  * is the implementation owner identifiable from repository evidence?

Nothing is removed, rewritten, or excluded. Apply tests use a disposable private index and
--check only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

REPO = r"c:\aerora_quant_backend_updated_final1"
HEAD = "074b772ab16cece62cbfca0f2b78497be2ae56d5"
PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"

GUARD_SYMS = [
    "def _slot_lock_key",
    "async def _acquire_slot_lock",
    "async def _release_slot_lock",
    "def exchange_connection_slot_guard",
]


def run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=REPO, capture_output=True, text=True, errors="replace", env=e, timeout=180
    )


def blob(rev: str, path: str) -> str:
    r = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"], cwd=REPO, capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""


def main() -> int:
    print("=== 4a. GUARD SYMBOLS: definition presence per revision ===")
    dep = "backend_app/core/subscription_dependencies.py"
    disk_dep = open(os.path.join(REPO, dep.replace("/", os.sep)), encoding="utf-8", errors="replace").read()
    for sym in GUARD_SYMS:
        row = []
        for label, rev in (("HEAD", HEAD), ("PR#6", PR6)):
            row.append(f"{label}={'yes' if sym in blob(rev, dep) else 'NO'}")
        row.append(f"worktree={'yes' if sym in disk_dep else 'NO'}")
        print(f"  {sym:<38} {'  '.join(row)}")
    print()

    print("=== 4b. CALL SITE: does store_keys use the guard? ===")
    exch = "backend_app/routers/exchange.py"
    disk_exch = open(os.path.join(REPO, exch.replace("/", os.sep)), encoding="utf-8", errors="replace").read()
    idx_r = run(["git", "show", f":{exch}"])
    versions = {
        "HEAD": blob(HEAD, exch),
        "PR#6": blob(PR6, exch),
        "INDEX(staged)": idx_r.stdout if idx_r.returncode == 0 else "",
        "worktree": disk_exch,
    }
    for label, text in versions.items():
        guard = text.count("exchange_connection_slot_guard")
        legacy = text.count("check_exchange_connection_slot")
        has_store = "def store_keys" in text or "async def store_keys" in text
        print(f"  {label:<14} lines={text.count(chr(10)):>5}  guard_refs={guard}  "
              f"legacy_refs={legacy}  store_keys_def={has_store}")
    print()

    print("=== 4c. store_keys: what guards the vault write on HEAD / PR#6 ===")
    for label in ("HEAD", "PR#6"):
        text = versions[label]
        lines = text.splitlines()
        start = next((i for i, l in enumerate(lines) if re.search(r"def store_keys", l)), None)
        print(f"  --- {label} ---")
        if start is None:
            print("      store_keys not found")
            continue
        seg = lines[start : start + 90]
        for i, l in enumerate(seg, start + 1):
            if re.search(r"check_exchange_connection_slot|slot_guard|async with|vault|store_exchange", l):
                print(f"      {i:>5}: {l.strip()[:110]}")
    print()

    print("=== 4d. exchange.py staged layer: whose change is it? ===")
    ns = run(["git", "diff", "--cached", "--numstat", "--", exch])
    print(f"  staged (index vs HEAD) numstat : {ns.stdout.strip()}")
    ns2 = run(["git", "diff", "--numstat", "--", exch])
    print(f"  unstaged (worktree vs index)   : {ns2.stdout.strip()}")
    d = run(["git", "diff", "--cached", "--", exch])
    added = [l[1:] for l in d.stdout.splitlines() if l.startswith("+") and not l.startswith("+++")]
    themes = {
        "tenant / RLS": r"tenant|rls|row.level|set_config|app\.current",
        "auth / AAL": r"\baal\b|aal2|claim|jwt|require_auth|mfa",
        "credential vault": r"vault|encrypt|kms|cipher|credential",
        "connection / CONN": r"connection|ccxt|venue|exchange_id",
        "slot guard (F1)": r"slot_guard|_slot_lock|exchange_connection_slot_guard",
        "error handling": r"HTTPException|try:|except|raise",
    }
    print(f"  staged added lines sampled: {len(added)}")
    for name, pat in themes.items():
        c = sum(1 for l in added if re.search(pat, l, re.I))
        print(f"      {name:<20} {c:>4} added lines match")
    print()

    print("=== 4e. APPLY TESTS onto HEAD (private index, --check only) ===")
    tmp = tempfile.mkdtemp(prefix="a17_slot_")
    try:
        for path, role in (
            (dep, "guard definitions - positive control"),
            (exch, "call site - subject"),
        ):
            diff = run(["git", "diff", HEAD, "--", path])
            pf = os.path.join(tmp, os.path.basename(path) + ".patch")
            with open(pf, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(diff.stdout)
            idx = os.path.join(tmp, os.path.basename(path) + ".index")
            env = {"GIT_INDEX_FILE": idx}
            run(["git", "read-tree", HEAD], env=env)
            chk = run(["git", "apply", "--cached", "--recount", "--check", pf], env=env)
            print(f"  {path}")
            print(f"      role  : {role}")
            print(f"      patch : {len(diff.stdout)} B")
            print(f"      check : exit={chk.returncode} -> {'APPLIES' if chk.returncode == 0 else 'REFUSES'}")
            for l in (chk.stderr or "").splitlines()[:3]:
                print(f"      stderr: {l[:130]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"  temp dir removed: {not os.path.exists(tmp)}")
    print()

    print("=== 4f. OWNER IDENTIFIABILITY from repository evidence ===")
    staged = run(["git", "diff", "--cached", "--name-only"])
    paths = [l for l in staged.stdout.splitlines() if l.strip()]
    print(f"  22 staged paths co-located with exchange.py:")
    for p in paths:
        print(f"      {p}")
    print()
    log = run(["git", "log", "-3", "--format=%h %an <%ae> %s", "--", exch])
    print("  last 3 commits touching exchange.py:")
    for l in log.stdout.splitlines():
        print(f"      {l[:120]}")
    print()
    print("  Note: git authorship identifies who COMMITTED history, not who holds the")
    print("  uncommitted staged layer. The staged set is attributable to a workstream by")
    print("  content (tenant-isolation / RLS / credential-vault), NOT to a named person.")

    print()
    print("=== 4g. SLOT TEST: untouched? ===")
    slot = "tests/test_quota01_exchange_slot_is_atomic.py"
    r = run(["git", "hash-object", slot])
    st = run(["git", "status", "--porcelain", "--", slot])
    print(f"  {slot}")
    print(f"      hash   {r.stdout.strip()}  (expect 2d7ccbbb04b3af04675b365809d89b4429db4888)")
    print(f"      status {st.stdout.strip() or '(untracked/none)'}")
    print(f"      exists {os.path.exists(os.path.join(REPO, slot.replace('/', os.sep)))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
