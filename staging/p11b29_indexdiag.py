"""A29 index-change diagnostic. STRICTLY READ-ONLY -- no git write command is issued.

Something staged a large number of paths during this phase. This establishes WHAT changed,
WHETHER the original 22 staged paths survived, and WHETHER any content was lost -- without
attempting any repair, because unstaging would itself be a destructive write against
another session's deliberate work.
"""
import datetime
import subprocess
from pathlib import Path

out = []


def g(a):
    return subprocess.run(["git"] + a, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def hdr(t):
    out.append("")
    out.append("=" * 92)
    out.append(t)
    out.append("=" * 92)


hdr("1. WHAT THE INDEX LOOKS LIKE NOW")
st = [l for l in g(["status", "--porcelain"]).splitlines() if l]
out.append(f"  porcelain lines      : {len(st)}")
out.append(f"  staged (XY[0] set)   : {len([l for l in st if l[0] not in ' ?'])}")
out.append(f"  unstaged             : {len([l for l in st if not l.startswith('??') and len(l)>1 and l[1]!=' '])}")
out.append(f"  untracked            : {len([l for l in st if l.startswith('??')])}")
out.append(f"  index entries        : {len([l for l in g(['ls-files']).splitlines() if l])}")
out.append(f"  HEAD                 : {g(['rev-parse','HEAD']).strip()}")
out.append(f"  ahead/behind         : {g(['rev-list','--left-right','--count','origin/main...HEAD']).strip()}")

idx = Path(".git/index")
if idx.exists():
    out.append(f"  .git/index mtime     : "
               f"{datetime.datetime.fromtimestamp(idx.stat().st_mtime)}")
    out.append(f"  .git/index size      : {idx.stat().st_size} bytes")

hdr("2. STAGED-PATH BREAKDOWN BY STATUS CODE")
codes = {}
for l in st:
    codes[l[:2]] = codes.get(l[:2], 0) + 1
for c, n in sorted(codes.items(), key=lambda kv: -kv[1]):
    out.append(f"  {c!r:>6} : {n}")

hdr("3. WHAT GOT NEWLY ADDED -- top-level prefix of 'A ' entries")
added = [l[3:].strip().strip('"') for l in st if l[0] == "A"]
pref = {}
for p in added:
    top = p.split("/")[0]
    pref[top] = pref.get(top, 0) + 1
out.append(f"  total 'A' (added to index) : {len(added)}")
for t, n in sorted(pref.items(), key=lambda kv: -kv[1])[:20]:
    out.append(f"    {t:<40} {n}")

hdr("4. DID THE ORIGINAL 22 STAGED PATHS SURVIVE?")
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
present = 0
for p in orig22:
    staged_blob = g(["rev-parse", f":{p}"]).strip()
    ok = bool(staged_blob) and "fatal" not in staged_blob
    present += 1 if ok else 0
    if not ok:
        out.append(f"  >> MISSING FROM INDEX: {p}")
out.append(f"  original staged paths still in index : {present} / {len(orig22)}")

hdr("5. IS MY AUTHORIZED TARGET NOW STAGED?")
t = "tests/test_drift_classification.py"
staged_blob = g(["rev-parse", f":{t}"]).strip()
work_blob = g(["hash-object", t]).strip()
out.append(f"  worktree blob : {work_blob}")
out.append(f"  index blob    : {staged_blob}")
out.append(f"  staged        : {bool(staged_blob) and 'fatal' not in staged_blob}")
out.append(f"  identical     : {staged_blob == work_blob}")
out.append(f"  status code   : "
           f"{next((l[:2] for l in st if l[3:].strip().strip(chr(34)) == t), 'not listed')}")

hdr("6. CONTENT-LOSS CHECK -- nothing committed, nothing removed from the worktree")
out.append(f"  commits ahead of origin/main : "
           f"{g(['rev-list','--count','origin/main..HEAD']).strip()}")
out.append(f"  deleted-in-worktree entries  : "
           f"{len([l for l in st if 'D' in l[:2]])}")
out.append(f"  stash entries                : "
           f"{len([l for l in g(['stash','list']).splitlines() if l])}")
out.append("  NOTE: staging changes which files git TRACKS; it does not alter or delete")
out.append("        worktree content. All file contents remain as they were.")

text = "\n".join(out) + "\n"
Path("staging/p11b29_indexdiag.txt").write_text(text, encoding="utf-8")
print("written")
