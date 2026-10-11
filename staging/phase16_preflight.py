"""Phase 16 preflight. READ-ONLY on index/HEAD/refs.

`git write-tree` is run because the brief requires it. It writes only unreferenced tree
objects to the object database; it does not mutate the index, HEAD, refs or the worktree.
No credential, DSN, database, Redis, exchange or network access.
"""
import datetime
import hashlib
import subprocess
from pathlib import Path

out = []


def g(a):
    r = subprocess.run(["git"] + a, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def hdr(t):
    out.append("")
    out.append("=" * 98)
    out.append(t)
    out.append("=" * 98)


hdr("1. HEAD / UPSTREAM / WORKTREES")
head = g(["rev-parse", "HEAD"]).strip()
out.append(f"  toplevel     : {g(['rev-parse','--show-toplevel']).strip()}")
out.append(f"  branch       : {g(['rev-parse','--abbrev-ref','HEAD']).strip()}")
out.append(f"  HEAD         : {head}")
out.append(f"  parent       : {g(['rev-parse','HEAD^']).strip()}")
out.append(f"  upstream     : {g(['rev-parse','--abbrev-ref','--symbolic-full-name','@{u}']).strip()}")
out.append(f"  origin/main  : {g(['rev-parse','origin/main']).strip()}")
out.append(f"  ahead/behind : {g(['rev-list','--left-right','--count','origin/main...HEAD']).strip()}")
out.append(f"  P15 candidate ecd23a9e is HEAD : "
           f"{head == 'ecd23a9e934ec58a7b1f0e5db638da8e865e8187'}")
out.append("  worktrees:")
for l in g(["worktree", "list"]).splitlines():
    out.append(f"    {l}")
out.append(f"  stash entries : {len([l for l in g(['stash','list']).splitlines() if l])}")
out.append(f"  HEAD subject  : {g(['log','-1','--format=%s']).strip()}")

hdr("2. STATUS / INDEX")
st = [l for l in g(["status", "--short"]).splitlines() if l]
staged = [l for l in st if l[0] not in " ?"]
unstaged = [l for l in st if not l.startswith("??") and len(l) > 1 and l[1] != " "]
untracked = [l for l in st if l.startswith("??")]
unmerged = [l for l in st if l[:2] in ("DD", "AU", "UD", "UA", "DU", "AA", "UU")]
out.append(f"  staged / unstaged / untracked / unmerged : "
           f"{len(staged)} / {len(unstaged)} / {len(untracked)} / {len(unmerged)}")
out.append(f"  tracked index entries : {len([l for l in g(['ls-files']).splitlines() if l])}")
raw = [l for l in g(["ls-files", "--stage"]).splitlines() if l]
h = hashlib.sha256()
for l in sorted(raw):
    h.update(l.encode())
out.append(f"  index digest (A14 method, sorted no-sep) : {h.hexdigest()}")
out.append(f"  git write-tree : {g(['write-tree']).strip()}")
out.append("")
out.append("  PRE-EXISTING MODIFIED FILES (must be preserved untouched):")
for l in unstaged:
    p = l[3:].strip().strip('"')
    blob = g(["hash-object", p]).strip()
    headblob = g(["rev-parse", f"HEAD:{p}"]).strip()
    ins = g(["diff", "--numstat", "--", p]).strip()
    out.append(f"    {l[:2]}  {p}")
    out.append(f"        worktree={blob}")
    out.append(f"        HEAD    ={headblob}")
    out.append(f"        numstat ={ins}")
if unmerged:
    out.append(f"  >> UNMERGED PATHS PRESENT: {unmerged}")

hdr("3. RELEASE-PATH FILE HASHES (current)")
files = [
    ".github/workflows/01-pr-check.yml",
    ".github/workflows/03-deploy.yml",
    ".github/workflows/05-security.yml",
    ".github/workflows/06-frontend-deploy.yml",
    "scripts/require_security_scan.py",
    "tests/test_bandit_sast_gate_is_green.py",
    "backend_app/alembic/versions/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py",
    "backend_app/alembic/versions/b2c3d4e5f6a7_positions_canonical_shape.py",
    "backend_app/alembic/versions/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py",
    "terraform/alb.tf",
    "terraform/redis.tf",
    "terraform/security_groups.tf",
    "backend_app/core/api_key_vault.py",
    "backend_app/backend/exchange_executor.py",
    "backend_app/backend/portfolio_cache_updater.py",
    "backend_app/main.py",
]
P15 = {
    ".github/workflows/01-pr-check.yml": "91d55a379115c88edd2fd6be01871b82a75ef498",
    ".github/workflows/03-deploy.yml": "eb8078eac06268447d60f26ef7a97a537ceed988",
    ".github/workflows/05-security.yml": "e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe",
    ".github/workflows/06-frontend-deploy.yml": "285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9",
    "tests/test_bandit_sast_gate_is_green.py": "a7deff99c9e54beee70c115607d538a638561b87",
    "backend_app/alembic/versions/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py": "5e9f85ce998eef0cf383ec24759e1c6782ac323a",
    "backend_app/alembic/versions/b2c3d4e5f6a7_positions_canonical_shape.py": "ac12c2d980d54d1eeb2dcf9be8921476a7b032a6",
    "backend_app/alembic/versions/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py": "bb9b629835c3eddf1156ea53692bce10b4e6d9b1",
    "backend_app/backend/exchange_executor.py": "df4fd42a151015124ae7eb3743733107106c413e",
}
for p in files:
    if not Path(p).exists():
        out.append(f"  ABSENT                                             {p}")
        continue
    b = g(["hash-object", p]).strip()
    xy = next((l[:2] for l in st if l[3:].strip().strip('"') == p), "clean")
    v = ""
    if p in P15:
        v = "  SAME as P15" if b == P15[p] else "  >> CHANGED since P15"
    out.append(f"  {b}  {xy:<5} {p}{v}")

hdr("4. DESTINATION COLLISION CHECK")
for p in ("staging/PHASE16_FRONTEND_GATE_AND_RELEASE_VERIFICATION.md",):
    out.append(f"  {'EXISTS -- DO NOT OVERWRITE' if Path(p).exists() else 'ABSENT -- safe'}  {p}")
n = sorted(Path("staging").glob("phase16_*"))
out.append(f"  existing staging/phase16_* artifacts : {len(n)}")
for x in n:
    out.append(f"    {x.name}")

hdr("5. REPORTS NEWER THAN PHASE 15")
p15 = Path("staging/PHASE15_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md")
cut = p15.stat().st_mtime if p15.exists() else 0
rows = sorted((f.stat().st_mtime, f.stat().st_size, f.name)
              for f in Path("staging").glob("*.md") if f.stat().st_mtime > cut)
if rows:
    for m, s, nm in rows:
        out.append(f"  {datetime.datetime.fromtimestamp(m).strftime('%m-%d %H:%M:%S')}  {s:>7} B  {nm}")
else:
    out.append("  (none)")

hdr("6. CREDENTIAL HYGIENE (status only -- nothing opened)")
for p in ("exchange_keys.json", ".env", "staging/pg_dsn.txt"):
    ign = subprocess.run(["git", "check-ignore", "-q", p]).returncode == 0
    trk = bool(g(["ls-files", "--error-unmatch", p]).strip())
    out.append(f"  {p:<26} exists={Path(p).exists()!s:<6} tracked={trk!s:<6} gitignored={ign}")

Path("staging/phase16_preflight.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("written")
