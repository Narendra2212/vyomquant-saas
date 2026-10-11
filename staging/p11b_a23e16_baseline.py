"""A23E16 Phase 1 baseline. STRICTLY READ-ONLY. No credential/DSN/DB/Redis/network access.

Nothing is reused from prior phases without re-verification: HEAD moved at A29
(074b772a -> ecd23a9e), so all prior index digests and counts are obsolete by construction.
"""
import datetime
import hashlib
import subprocess
from pathlib import Path

A29_HEAD = "ecd23a9e934ec58a7b1f0e5db638da8e865e8187"
out = []


def g(a):
    return subprocess.run(["git"] + a, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def hdr(t):
    out.append("")
    out.append("=" * 96)
    out.append(t)
    out.append("=" * 96)


hdr("1. ROOT / BRANCH / HEAD / REMOTE")
out.append(f"  toplevel     : {g(['rev-parse','--show-toplevel']).strip()}")
head = g(["rev-parse", "HEAD"]).strip()
origin = g(["rev-parse", "origin/main"]).strip()
out.append(f"  branch       : {g(['rev-parse','--abbrev-ref','HEAD']).strip()}")
out.append(f"  HEAD         : {head}")
out.append(f"  origin/main  : {origin}")
out.append(f"  HEAD==origin : {head == origin}")
out.append(f"  ahead/behind : {g(['rev-list','--left-right','--count','origin/main...HEAD']).strip()}")
out.append(f"  A29 baseline ecd23a9e still HEAD : {head == A29_HEAD}")
out.append("")
out.append("  last 3 commits:")
for l in g(["log", "-3", "--format=    %h %ad %an :: %s", "--date=format:%m-%d %H:%M"]).splitlines():
    out.append(l)

hdr("2. WORKTREE COUNTS")
st = [l for l in g(["status", "--porcelain"]).splitlines() if l]
staged = [l for l in st if l[0] not in " ?"]
unstaged = [l for l in st if not l.startswith("??") and len(l) > 1 and l[1] != " "]
untracked = [l for l in st if l.startswith("??")]
unmerged = [l for l in st if l[:2] in ("DD", "AU", "UD", "UA", "DU", "AA", "UU")]
out.append(f"  staged / unstaged / untracked / unmerged : "
           f"{len(staged)} / {len(unstaged)} / {len(untracked)} / {len(unmerged)}"
           "    (A29 exit: 0 / 2 / 6 / 0)")
out.append(f"  tracked index entries : {len([l for l in g(['ls-files']).splitlines() if l])}"
           "   (A29 exit: 5705)")
out.append(f"  worktrees / stash     : "
           f"{len([l for l in g(['worktree','list']).splitlines() if l])} / "
           f"{len([l for l in g(['stash','list']).splitlines() if l])}")
out.append("")
out.append("  full porcelain:")
for l in st:
    out.append(f"    {l}")

hdr("3. A29 TEST-6 SURVIVAL CHECK")
t = "tests/test_drift_classification.py"
work = g(["hash-object", t]).strip()
idxb = g(["rev-parse", f":{t}"]).strip()
headb = g(["rev-parse", f"HEAD:{t}"]).strip()
txt = Path(t).read_text(encoding="utf-8")
out.append(f"  worktree blob : {work}   (A29 exit recorded 99a2917ac0a65761ef3dfe9299ed1df87647552a)")
out.append(f"  index blob    : {idxb}")
out.append(f"  HEAD blob     : {headb}")
out.append(f"  status code   : {next((l[:2] for l in st if l[3:].strip().strip(chr(34)) == t), 'not listed')}")
out.append(f"  lines         : {len(txt.splitlines())}   (A29: 808)")
for marker in ("def test_counts_reduce_to_the_legacy_output",
               "def test_the_legacy_reconstruction_is_membership_not_multiset",
               "def test_the_legacy_arithmetic_cases_cover_every_classification_category",
               "def _legacy_membership_diff",
               "LEGACY_ARITHMETIC_CASES = ["):
    out.append(f"  present: {marker in txt}   {marker}")
out.append(f"  'whole-tuple **membership**' semantics anchor cited : "
           f"{'membership' in txt}")
out.append(f"  LEGACY_ARITHMETIC_CASES entries : "
           f"{txt.count(chr(34)) and sum(1 for l in txt.splitlines() if l.strip().startswith('(') and l.strip().endswith('),') and 'policies' in l or (l.strip().startswith('(') and 'privileges' in l) or (l.strip().startswith('(') and 'columns' in l) or (l.strip().startswith('(') and 'recorded_migrations' in l))}")

hdr("4. FINGERPRINTS (worktree) -- all re-measured, none reused")
paths = [
    "scripts/verify_provisioning.py",
    "scripts/verify_schema_drift.py",
    "scripts/verify_production_schema.py",
    "tests/test_snapshot_dimension_invariants.py",
    "tests/test_drift_classification.py",
    ".github/workflows/01-pr-check.yml",
    "backend_app/backend/redis_manager.py",
    "backend_app/core/cache/redis_manager.py",
    "backend_app/core/subscription_engine.py",
    "backend_app/main.py",
    "staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md",
    "staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md",
    "staging/PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md",
    "staging/provisioning_snapshot.json",
    "staging/PHASE11A_READINESS_SCORECARD.json",
]
for p in paths:
    if not Path(p).exists():
        out.append(f"  ABSENT                                             {p}")
        continue
    b = g(["hash-object", p]).strip()
    xy = next((l[:2] for l in st if l[3:].strip().strip('"') == p), "clean")
    out.append(f"  {b}  {xy:<5} {p}")

hdr("5. LEDGER LINES 800-804 (verbatim status cells)")
led = Path("staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md").read_text(encoding="utf-8").splitlines()
for n in (800, 801, 802, 803, 804):
    cells = [c.strip() for c in led[n - 1].split("|")]
    out.append(f"  L{n}: id={cells[1][:16]!r:<20} owner={cells[3]!r:<26} status={cells[4]!r}")
out.append(f"  L802 reconciled to 'GRANTED (W1D-12)' : {'GRANTED (W1D-12)' in led[801]}")

hdr("6. ORIGINAL 22 STAGED PATHS -- still tracked after the launch commit?")
orig22 = [
    ".github/workflows/05-security.yml", ".kiro/security-template.md",
    "algo22-terminal/src/pages/ExchangeManager.jsx",
    "backend_app/core/dependencies.py", "backend_app/core/tenant_db_context.py",
    "backend_app/routers/auth.py", "backend_app/routers/exchange.py",
    "docs/adr/ADR-0006-bridge-tenancy-with-institutional-silo-tier.md",
    "docs/adr/ADR-0007-per-tenant-credential-encryption-keys.md",
    "docs/saas-architecture/tenant-isolation-matrix.md",
    "docs/security-reviews/ALGO22_Quant_Platform_Review_10032026.md",
    "scripts/verify_rls.py", "scripts/verify_rls_enforcement.py",
    "terraform/alb.tf", "terraform/cloudtrail.tf", "terraform/providers.tf",
    "terraform/variables.tf", "tests/test_sc3_aal_claim_propagation.py",
    "tests/test_tenant_db_context.py", "tests/test_tenant_db_context_postgres.py",
    "tests/test_verify_rls_script.py", "tests/test_worker_deployment_coverage.py",
]
ok = 0
for p in orig22:
    r = g(["rev-parse", f"HEAD:{p}"]).strip()
    good = bool(r) and "fatal" not in r.lower()
    ok += 1 if good else 0
    if not good:
        out.append(f"  >> NOT IN HEAD: {p}")
out.append(f"  present in HEAD commit : {ok} / {len(orig22)}")

hdr("7. PG LANE TESTS -- presence and tracked state")
pg = [p for p in sorted(Path("tests").glob("*postgres*.py"))]
out.append(f"  tests/*postgres*.py files : {len(pg)}")
for p in pg:
    rel = p.as_posix()
    tracked = bool(g(["ls-files", "--error-unmatch", rel]).strip())
    out.append(f"    tracked={tracked!s:<6} {rel}")

hdr("8. CREDENTIAL HYGIENE (status only -- nothing opened)")
for p in ("exchange_keys.json", ".env", "staging/pg_dsn.txt", ".env.corrupt.backup"):
    ign = subprocess.run(["git", "check-ignore", "-q", p]).returncode == 0
    tracked = bool(g(["ls-files", "--error-unmatch", p]).strip())
    out.append(f"  {p:<28} exists={Path(p).exists()!s:<6} tracked={tracked!s:<6} gitignored={ign}")

Path("staging/p11b_a23e16_baseline.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("written")
