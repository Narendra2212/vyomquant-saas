"""PHASE 11B-A2 -- read-only CI history: has the lane D5' depends on ever been green?

Uses `gh` for READ operations only (`run list`, `run view`). It triggers nothing, pushes
nothing and changes no remote state.
"""

from __future__ import annotations

import json
import subprocess


def gh_json(*args: str):
    r = subprocess.run(["gh", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  gh {' '.join(args[:3])} failed: {(r.stderr or '').strip()[:140]}")
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def main():
    banner("A. LAST 10 RUNS OF 01-pr-check.yml (read-only)")
    runs = gh_json("run", "list", "--workflow", "01-pr-check.yml", "--limit", "10",
                   "--json", "databaseId,headSha,conclusion,event,createdAt") or []
    for r in runs:
        print(f"  {r['createdAt'][:19]}  {r['conclusion'] or 'n/a':10s} "
              f"{r['headSha'][:8]}  id={r['databaseId']}  ({r['event']})")
    greens = [r for r in runs if r.get("conclusion") == "success"]
    print(f"\n  successful runs in this window: {len(greens)} of {len(runs)}")

    banner("B. JOB BREAKDOWN OF THE MOST RECENT RUN")
    if not runs:
        print("  no runs to inspect")
        return
    latest = runs[0]
    detail = gh_json("run", "view", str(latest["databaseId"]),
                     "--json", "headSha,conclusion,jobs")
    if not detail:
        print("  could not read run detail")
    else:
        print(f"  run id   : {latest['databaseId']}")
        print(f"  head SHA : {detail.get('headSha')}")
        print(f"  outcome  : {detail.get('conclusion')}")
        print("  jobs:")
        for j in detail.get("jobs", []):
            name = j.get("name", "?")
            print(f"    {str(j.get('conclusion')):10s} {name}")
        unit = [j for j in detail.get("jobs", [])
                if "unit" in (j.get("name") or "").lower()]
        print("\n  the job D5' modifies is the one named for unit tests:")
        for j in unit:
            print(f"    -> {j.get('name')}: {j.get('conclusion')}")
        if not unit:
            print("    -> no job whose name contains 'unit' appears in this run")

    banner("C. DOES ANY RUN CONTAIN THE D5' CHANGE?")
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()
    shas = {r["headSha"] for r in runs}
    print(f"  local HEAD                      : {head}")
    print(f"  local HEAD appears in these runs : {head in shas}")
    print("  D5' is UNCOMMITTED, so by construction it cannot appear in ANY run,")
    print("  including a run on local HEAD. Confirmed by the working-tree state in")
    print("  staging/p11b_a2_baseline.txt.")


if __name__ == "__main__":
    main()
