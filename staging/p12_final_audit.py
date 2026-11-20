"""Phase 12 closing preservation audit.

Read-only. Confirms that Phase 12 changed no source or test file, that none of the 13 files
tracked across phases 11B-D3..D11 differ from HEAD, and records the real-Redis gate state in
the form the gate itself reports it (no endpoint echoed).

METHOD NOTE -- a correction to this script's own first draft.
------------------------------------------------------------
The first draft of this file compared the 13 files against hash strings carried forward in
prose from earlier phases, using a hash function written fresh here. It reported
``drifted=13/13``, which was an artefact of the comparison and not a finding: a different
hash function cannot be compared against values a different function produced, and one of
the 13 paths was wrong as well (``core/event_router.py``; the file is at
``backend/event_router.py``, and the script read the typo as MISSING).

Two changes follow from that. Non-drift is established by ``git diff HEAD --name-only``,
which is authoritative and needs no hash at all. The per-file digests are emitted with
``git hash-object``, i.e. the SHA-1 blob id git itself stores, so that any later phase can
reproduce them with one command rather than trusting a number copied out of a report.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The files touched, inspected or depended upon across 11B-D3..D11 and Phase 12.
TRACKED = [
    "backend_app/backend/api_key_vault.py",
    "backend_app/backend/exchange_executor.py",
    "backend_app/backend/order_watchdog.py",
    "backend_app/main.py",
    "backend_app/core/state.py",
    "backend_app/core/models/execution_record.py",
    "tests/test_d5_watchdog_adjudication_repair.py",
    "backend_app/routers/orders.py",
    "backend_app/routers/market.py",
    "backend_app/backend/portfolio_cache_updater.py",
    "backend_app/backend/event_router.py",
    "backend_app/core/dependencies.py",
    "tests/test_market_none_keys_fix.py",
    "backend_app/core/subscription_dependencies.py",
    "backend_app/routers/exchange.py",
    "backend_app/core/tenant_db_context.py",
]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.rstrip("\n")


def main() -> int:
    print("=== PHASE 12 CLOSING PRESERVATION AUDIT ===")
    print(f"HEAD                 : {git('rev-parse', 'HEAD')}")
    print(f"branch               : {git('rev-parse', '--abbrev-ref', 'HEAD')}")
    print(
        "vs origin/main       : "
        + git("rev-list", "--left-right", "--count", "HEAD...origin/main").replace("\t", " / ")
        + "  (ahead / behind)"
    )
    old = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "074b772a", "HEAD"], cwd=ROOT
    ).returncode
    print(f"074b772a is ancestor : {old == 0}")
    print(f"worktrees            : {len(git('worktree', 'list').splitlines())}")
    print(f"unmerged paths       : {len([x for x in git('diff', '--name-only', '--diff-filter=U').splitlines() if x])}")

    staged = [x for x in git("diff", "--cached", "--name-only").splitlines() if x]
    print(f"staged entries       : {len(staged)}")

    porcelain = [x for x in git("status", "--porcelain=v1").splitlines() if x]
    print(f"working-tree entries : {len(porcelain)}")

    print("\n--- files differing from HEAD (authoritative non-drift check) ---")
    diffed = [x for x in git("diff", "HEAD", "--name-only").splitlines() if x]
    for x in diffed:
        print(f"  MODIFIED  {x}")
    print(f"  total modified vs HEAD = {len(diffed)}")
    overlap = sorted(set(diffed) & set(TRACKED))
    print(f"  of which in the tracked set = {len(overlap)} {overlap}")

    print("\n--- non-staging working-tree entries ---")
    nonstaging = [x for x in porcelain if not x[3:].startswith("staging/")]
    for x in nonstaging:
        print(f"  {x}")
    print(f"  total = {len(nonstaging)}")

    print("\n--- git blob ids of the tracked files (reproducible: git hash-object <path>) ---")
    missing = 0
    for rel in TRACKED:
        p = ROOT / rel
        if not p.exists():
            print(f"  MISSING  {rel}")
            missing += 1
            continue
        worktree = git("hash-object", rel)
        committed = git("rev-parse", f"HEAD:{rel}")
        flag = "ok   " if worktree == committed else "DRIFT"
        print(f"  {flag} {worktree}  {rel}")
    print(f"  missing={missing}/{len(TRACKED)}")

    print("\n--- real-Redis gate, as the gate itself reports it ---")
    sys.path.insert(0, str(ROOT))
    from tests.real_redis_gate import GATE_REASON, GATE_STATE, gate_summary  # noqa: E402

    print(f"  {gate_summary()}")
    print(f"  state={GATE_STATE}")
    print("  reason (verbatim):")
    print(f"    {GATE_REASON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
