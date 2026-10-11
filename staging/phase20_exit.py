"""Phase 20 exit preservation audit. READ-ONLY on index/HEAD/refs."""
import subprocess
from pathlib import Path

out = []


def g(a):
    return subprocess.run(["git"] + a, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


out.append("=" * 90)
out.append("PHASE 20 EXIT MANIFEST")
out.append("=" * 90)
out.append(f"  HEAD        : {g(['rev-parse','HEAD']).strip()}")
out.append(f"  origin/main : {g(['rev-parse','origin/main']).strip()}")
out.append(f"  divergence  : {g(['rev-list','--left-right','--count','origin/main...HEAD']).strip()}")
out.append(f"  write-tree  : {g(['write-tree']).strip()}")
st = [l for l in g(["status", "--short"]).splitlines() if l]
out.append(f"  staged      : {len([l for l in st if l[0] not in ' ?'])}")
out.append(f"  unstaged    : {len([l for l in st if not l.startswith('??') and len(l) > 1 and l[1] != ' '])}")
out.append(f"  untracked   : {len([l for l in st if l.startswith('??')])}")
out.append(f"  unmerged    : {len([l for l in st if l[:2] in ('DD','AU','UD','UA','DU','AA','UU')])}")
out.append(f"  stash       : {len([l for l in g(['stash','list']).splitlines() if l])}")
out.append(f"  worktrees   : {len([l for l in g(['worktree','list']).splitlines() if l])}")
out.append("")
out.append("  pre-existing modifications (must be byte-identical):")
for l in st:
    if l.startswith("??"):
        continue
    p = l[3:].strip().strip('"')
    out.append(f"    {l[:2]}  {g(['hash-object', p]).strip()}  {p}")

out.append("")
out.append("  lane targets -- all must be clean:")
targets = [
    ".github/workflows/06-frontend-deploy.yml",
    ".github/workflows/01-pr-check.yml",
    ".github/workflows/03-deploy.yml",
    ".github/workflows/05-security.yml",
    "scripts/require_security_scan.py",
    "tests/test_nightly_audit_workflow.py",
    "tests/test_bandit_sast_gate_is_green.py",
    "backend_app/test_perf.py",
    "backend_app/routers/market.py",
    "backend_app/routers/orders.py",
    "backend_app/routers/exchange.py",
    "backend_app/backend/portfolio_cache_updater.py",
    "backend_app/backend/exchange_executor.py",
    "backend_app/backend/api_key_vault.py",
    "terraform/security_groups.tf",
    "terraform/alb.tf",
    "terraform/redis-only/main.tf",
    "staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md",
]
for p in targets:
    if not Path(p).exists():
        out.append(f"    ABSENT                                     {p}")
        continue
    xy = next((l[:2] for l in st if l[3:].strip().strip('"') == p), "clean")
    out.append(f"    {g(['hash-object', p]).strip()}  {xy:<5} {p}")

Path("staging/phase20_exit.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
